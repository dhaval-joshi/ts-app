"""
NxtradStream (the vendored SDK) runs its own background thread and calls
callbacks from that thread. This wraps it so the rest of the app -- which is
asyncio-based -- can just `await stream_queue.get()` for events, regardless
of which thread they originated on.

We ONLY ever subscribe to:
  - the 'events' channel (orders/positions/trades) -- always on, this is how
    the order manager knows an entry filled, a leg got hit, etc.
  - L1 price ticks, and only for symbols that currently have trailing
    enabled on an open order. Nothing else. No charts, no depth, no greeks.
"""
import asyncio
import logging

from .nxtradstream import NxtradStream
from . import config

log = logging.getLogger("tradejini.stream")


class StreamManager:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self._nx: NxtradStream | None = None
        self._trailing_symbols: set[str] = set()
        self._trailing_symbols_by_owner: dict[str, set[str]] = {}  # owner -> symbols, unioned into the
                                                                     # actual subscription below -- needed
                                                                     # because subscription lists are NOT
                                                                     # additive at the SDK level (every call
                                                                     # replaces the previous one), so with
                                                                     # two independent OrderManager instances
                                                                     # (live + paper) sharing this SAME
                                                                     # StreamManager, each just sending its
                                                                     # own subset would silently wipe out the
                                                                     # other's subscriptions on every call
        self._connected = False

    def start(self, auth_token: str):
        self._nx = NxtradStream(
            config.TRADEJINI_HOST, stream_cb=self._stream_cb, connect_cb=self._connect_cb
        )
        self._nx.connect(auth_token)

    def _put(self, item: dict):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, item)

    # -- callbacks, called from the SDK's own background thread -----------

    def _connect_cb(self, nx, ev):
        if ev.get("s") == "connected":
            self._connected = True
            nx.subscribeEvents(["orders", "positions", "trades"])
            if self._trailing_symbols:
                nx.subscribeL1(list(self._trailing_symbols))
            self._put({"kind": "connected"})
        elif ev.get("s") == "closed":
            self._connected = False
            self._put({"kind": "closed", "reason": ev.get("reason"), "code": ev.get("code")})
            if ev.get("reason") != "Unauthorized Access":
                import time
                time.sleep(5)
                nx.reconnect()
        elif ev.get("s") == "error":
            self._put({"kind": "error", "reason": ev.get("reason")})

    def _stream_cb(self, nx, data: dict):
        self._put({"kind": "tick", "data": data})

    # -- called from asyncio side ------------------------------------------

    def set_trailing_symbols(self, symbols: set[str], owner: str = "default"):
        """Subscription lists are NOT additive at the SDK level (every
        call replaces the previous list) -- so this tracks each caller's
        own desired set separately by `owner` and always sends the UNION
        of all of them, rather than trusting a single caller's set to be
        the complete picture. Two OrderManager instances (live + paper)
        share this same StreamManager and each call this independently
        with their own owner key ("live" / "paper") -- without this
        unioning, whichever called last would silently wipe out the
        other's subscriptions.

        Deliberately does NOT skip calling subscribeL1/unsubscribeL1 when
        the computed set happens to match what this object already
        believes is subscribed -- a "skip if unchanged" optimization used
        to live here, and it's a real risk: this object's own belief about
        what's subscribed can silently drift from what the broker actually
        has (e.g. around a reconnect, or any other edge case not fully
        understood), and a skipped call in that state means a symbol is
        genuinely not receiving ticks with nothing here to notice. Given
        what's riding on price monitoring actually working, a little
        redundant API traffic is a trivial cost next to a silent gap in
        it -- see also _periodic_resubscribe_loop below, a second,
        independent safety net for the same reason."""
        self._trailing_symbols_by_owner[owner] = set(symbols)
        combined = set()
        for s in self._trailing_symbols_by_owner.values():
            combined |= s
        self._trailing_symbols = combined
        if self._nx and self._connected:
            if self._trailing_symbols:
                log.info("Subscribing L1 for %d symbol(s): %s", len(self._trailing_symbols), sorted(self._trailing_symbols))
                self._nx.subscribeL1(list(self._trailing_symbols))
            else:
                log.info("Unsubscribing L1 -- no symbols currently need it.")
                self._nx.unsubscribeL1()

    async def periodic_resubscribe_loop(self):
        """A second, independent safety net on top of set_trailing_symbols
        always re-sending on every call (see its docstring): every 60s,
        UNCONDITIONALLY re-sends the current subscription list to the
        broker, even if nothing has changed from this object's point of
        view. Self-healing against any kind of silent drift between what
        this app believes is subscribed and what the broker actually has
        -- cheap insurance for something that must not silently fail."""
        while True:
            await asyncio.sleep(60)
            if self._nx and self._connected and self._trailing_symbols:
                log.info("Periodic resubscribe safety net: re-sending %d symbol(s).", len(self._trailing_symbols))
                self._nx.subscribeL1(list(self._trailing_symbols))

    def is_connected(self) -> bool:
        return self._connected
