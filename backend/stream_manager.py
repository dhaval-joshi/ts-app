"""
NxtradStream (the vendored SDK) runs its own background thread and calls
callbacks from that thread. This wraps it so the rest of the app -- which is
asyncio-based -- can just `await stream_queue.get()` for events, regardless
of which thread they originated on.

We subscribe to:
  - the 'events' channel (orders/positions/trades) -- always on, this is how
    the order manager knows an entry filled, a leg got hit, etc.
  - L1 price ticks, and only for symbols that currently have trailing
    enabled on an open order (or an on-demand price/market-snapshot fetch
    in flight -- see OrderManager.fetch_live_price/fetch_market_snapshot).
  - Greeks, but only as one-shot snapshots (subscribe_greeks_snapshot),
    right before a candidate Advanced OMS cycle starts, for
    entry_signals.py's optional IV-rank gate -- never a persistent
    subscription. Still no charts, no depth (L5), no streaming OHLC.
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

    def subscribe_greeks_snapshot(self, symbols: set[str]):
        """One-shot: fires a 'greeks-snapshot' request for these symbols and
        lets the broker reply on its own -- unlike set_trailing_symbols
        above, this is NOT a persistent subscription this object tracks or
        re-sends; the SDK's "-snapshot" request types are single-pull by
        design (see nxtradstream.subscribeGreeksSnapShot). Used only by
        entry_signals' IV-rank gate, right before a candidate cycle starts
        -- see OrderManager.fetch_greeks_snapshot. Same string-symbol
        format as set_trailing_symbols (e.g. "12345_NSE") -- the wire
        protocol treats that whole string as the subscription token, same
        as every existing L1 call in this module.

        No-ops silently if not connected -- the caller
        (fetch_greeks_snapshot) already has its own timeout, so a
        temporarily-down stream just times out that call rather than
        needing a special case here."""
        if self._nx and self._connected and symbols:
            log.info("Requesting Greeks snapshot for %d symbol(s): %s", len(symbols), sorted(symbols))
            self._nx.subscribeGreeksSnapShot(list(symbols))

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
