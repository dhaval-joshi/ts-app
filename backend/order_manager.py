"""
The brain of the app. Owns the lifecycle of every order:

  1. place entry
  2. poll broker truth (REST) to notice when it fills
  3. once filled, start WATCHING live ticks for the SL/Target trigger --
     no broker order is placed for the exit at all (OCO is fully retired;
     see the "Historical" section in the README for why)
  4. on every L1 tick: ratchet the trigger price if trailing is on, then
     check if price has crossed the current trigger -- by default this
     evaluates on every raw tick, but a Strategy/Program can configure
     trail_check_interval_seconds to only re-evaluate every N seconds
     using the window's MEDIAN tick (smooths single-tick noise in a
     choppy market), and a Program can additionally require
     exit_confirmation_windows consecutive crossed evaluations before
     actually firing -- see handle_l1_tick
  5. trigger crossed (or a time-based exit is due): fire a plain market
     order to close, the same reliable mechanism everywhere else in this
     app uses (Stop & Flatten, manual close)
  6. persist every state change to disk immediately (see store.py) so a
     crash can be recovered from on next boot

Design choice: order/position status transitions are driven primarily by
POLLING the REST truth (GET /api/oms/orders, /api/oms/positions) on a short
timer, not by trying to parse the (sparsely documented) websocket event
payload field-by-field. The websocket 'events' subscription is still used,
but only as a "something changed, go reconcile now" nudge for snappier
updates -- the REST poll is what actually decides state. This keeps the
logic correct even where the event schema is ambiguous, at the cost of a
few seconds of latency on state transitions (EXIT_CHECK_INTERVAL_SECONDS).

Live L1 ticks (used only for trailing) are handled directly, no polling
alternative -- see the clarifying-question answers this app was built from.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
import uuid
from datetime import time as dtime

from . import clock, config, store, failure_log, factsheet
from .broker_interface import BrokerClient
from .tradejini_client import TradejiniApiError

log = logging.getLogger("tradejini.order_manager")

TERMINAL_STATUSES = {"closed", "cancelled", "entry_rejected"}

# Tradejini requires a market-protection percentage on every market order
# placed via the API (a price collar so a market order can't fill
# arbitrarily far from the last traded price) -- orders without one get
# rejected outright with "Market protection mandatory for all market order."
# A strategy can set its own value; if it doesn't, this is the fallback.
DEFAULT_MARKET_PROTECTION_PERCENT = 5.0

# Fallback tick size (min price increment) for strategies saved before this
# field existed. 0.05 covers most NSE equity/F&O contracts, but isn't
# universal -- see the tick_size field on the Strategies page.
DEFAULT_TICK_SIZE = 0.05
SQUARE_OFF_STUCK_TIMEOUT_SECONDS = 90  # a square-off that's neither confirmed filled nor confirmed
    # rejected/cancelled within this long is treated as stuck (most likely: placed at/after market close,
    # accepted by the broker but never resolving within the session) -- see _reconcile_square_off


def _round_to_tick(price: float, tick_size: float) -> float:
    """Rounds to the nearest valid multiple of the instrument's tick size.
    Rounding SL/Target prices to a flat 2 decimals (instead of to the actual
    tick) is what caused the broker's "price is not a multiple of tick size"
    rejection -- every computed price (initial and every trailing update)
    goes through this now."""
    if not tick_size or tick_size <= 0:
        tick_size = DEFAULT_TICK_SIZE
    ticks = round(price / tick_size)
    return round(ticks * tick_size, 4)


def _round_down_to_tick(price: float, tick_size: float) -> float:
    """Like _round_to_tick but always rounds DOWN to the nearest valid tick
    multiple -- used for a user-specified close price, per an explicit
    request that it round down rather than to the nearest tick. A tiny
    epsilon guards against float error nudging an exact multiple down a tick
    (e.g. 13.95 / 0.05 landing on 278.999999... instead of 279)."""
    if not tick_size or tick_size <= 0:
        tick_size = DEFAULT_TICK_SIZE
    ticks = math.floor(price / tick_size + 1e-9)
    return round(ticks * tick_size, 4)


def _round_up_to_tick(price: float, tick_size: float) -> float:
    """Like _round_down_to_tick but rounds UP -- used for a user-specified
    entry price (New Order page), per an explicit request that it round up
    rather than to the nearest tick. The epsilon here guards the opposite
    direction: an exact multiple shouldn't get bumped up a tick by float
    error (e.g. 13.95 / 0.05 landing on 279.00000001 instead of 279)."""
    if not tick_size or tick_size <= 0:
        tick_size = DEFAULT_TICK_SIZE
    ticks = math.ceil(price / tick_size - 1e-9)
    return round(ticks * tick_size, 4)


def now_iso() -> str:
    return clock.now_iso()


class OrderManager:
    def __init__(self, client: BrokerClient, stream, subscribe_all_active: bool = False, owner: str = "live"):
        self.client = client
        self.stream = stream
        self.owner = owner  # "live" | "paper" -- passed to stream.set_trailing_symbols() so two OrderManager
                              # instances sharing the same StreamManager don't stomp on each other's
                              # subscriptions; see StreamManager.set_trailing_symbols' docstring
        self.subscribe_all_active = subscribe_all_active  # False (default) preserves EXACT existing live
                                                             # behavior (only watching orders get subscribed);
                                                             # True is used ONLY by the paper-dedicated
                                                             # OrderManager instance, which needs continuous
                                                             # price monitoring for PENDING entries too, since
                                                             # PaperBrokerClient can't fill an order it never
                                                             # receives a tick for -- see main.py's wiring
        self._orders: dict[str, dict] = {}          # order_id -> state
        self._archived_orders: dict[str, dict] = {}  # order_id -> state (moved out of the active dashboard)
        self._trail_state: dict[str, dict] = {}  # order_id -> in-memory-only state for timeframe-aggregated
                                                       # trailing/exit checks (trail_check_interval_seconds)
                                                       # and exit confirmation (exit_confirmation_windows) --
                                                       # {"window_start": monotonic float | None,
                                                       #  "window_ticks": [float, ...],
                                                       #  "stop_confirm_streak": int, "target_confirm_streak": int}.
                                                       # Deliberately NOT persisted (same reasoning as
                                                       # _latest_prices -- this is display/decision-cadence
                                                       # state, not something that needs to survive a restart;
                                                       # losing it mid-window just means the next window starts
                                                       # fresh, which is harmless). See handle_l1_tick and
                                                       # _maybe_app_market_exit.
        self._symbol_momentum: dict[str, dict] = {} # stream_symbol -> {"window": [], "last_update": 0, "state": "Steady", "prev": None}
        self._reconcile_event = asyncio.Event()
        self._lock = asyncio.Lock()
        # on-demand "fetch current price" support (New Order page) -- these
        # are temporary subscriptions for a symbol that may not belong to
        # any order yet, layered on top of the real per-order subscriptions
        self._adhoc_price_symbols: set[str] = set()
        self._price_fetch_waiters: dict[str, list[asyncio.Future]] = {}
        self._greeks_fetch_waiters: dict[str, list[asyncio.Future]] = {}  # separate from the price waiters
                                                       # above -- different payload shape (a dict, not a
                                                       # float), and no reason to put new risk anywhere
                                                       # near the proven price-fetch path
        self._latest_prices: dict[str, float] = {}  # stream_symbol -> most recent tick, ANY symbol that's
                                                       # ever ticked (not just tracked watching orders) --
                                                       # used by PaperBrokerClient for synchronous, non-blocking
                                                       # fill checks; see get_cached_price() below
        self._background_close_tasks: set[asyncio.Task] = set()  # strong references for the fire-and-forget
                                                       # _do_close() tasks spawned by _spawn_close() -- asyncio
                                                       # only holds a create_task() result weakly, so without
                                                       # this the task could be garbage-collected mid-flight,
                                                       # silently abandoning a close the order was already
                                                       # committed to (status already flipped to "closing")

    # ------------------------------------------------------------ boot ---

    def _backfill_order_fields(self, o: dict) -> dict:
        """Fields that didn't exist in orders created before various features
        were added, so old order files keep working without needing to be
        edited by hand. Defensive against a genuinely malformed/very-old
        file too -- one bad order file should never take down the whole
        orders list (and therefore the whole dashboard) for every order."""
        o.setdefault("tick_size", DEFAULT_TICK_SIZE)
        o.setdefault("last_ltp", None)
        o.setdefault("last_ltt", None)  # exchange's own last-traded-time for the most recent tick --
                                          # diagnosis only (fresh vs. stale tick, clock drift), never
                                          # compared for equality or used to drive any decision
        o.setdefault("pnl", {})
        o["pnl"].setdefault("unrealized", None)
        o["pnl"].setdefault("unrealized_pct", None)
        o["pnl"].setdefault("realized", None)
        o["pnl"].setdefault("exit_avg_price", None)
        o["pnl"].setdefault("source", None)
        o.setdefault("warning", None)
        o.setdefault("trail_update_count", 0)
        o.setdefault("trail_failure_count", 0)
        o["pnl"].setdefault("slippage", None)
        o.setdefault("entry", {})
        o["entry"].setdefault("filled_at", None)
        o.setdefault("lot_size", 1)
        o.setdefault("exit_mode", "both")  # the only mode that existed before this field was added
        o.setdefault("program_id", None)
        o.setdefault("cycle_id", None)
        o.setdefault("program_leg", None)
        o.setdefault("trail_check_interval_seconds", 0)  # 0 = no aggregation, today's exact behavior
        o.setdefault("exit_confirmation_windows", 1)      # 1 = fire on first crossing, today's exact behavior
        o.setdefault("stop_breach_force_close_count", 0)  # 0 = off, today's exact behavior
        o.setdefault("stop_breach_count", 0)  # lifetime counter -- deliberately a real, persisted order
                                                # field (like trail_update_count/trail_failure_count),
                                                # NOT ephemeral in-memory state -- a restart must not
                                                # silently reset "this stop has been tested N times"
        o.setdefault("broker_id", None)  # which broker this order actually went to; None for paper
                                           # (paper isn't a real broker) and for any order saved before
                                           # this field existed (safe default -- reconciliation only
                                           # ever considers owner=="live" orders in the first place)
        o.setdefault("strategy_snapshot", {})  # the resolved strategy/leg_strategy dict exactly as it
                                                 # stood at order-creation time -- independent of whatever
                                                 # the named Strategy or Program config later becomes
                                                 # (renamed, edited, deleted). Empty for orders saved
                                                 # before this field existed -- there's no way to
                                                 # reconstruct history that was never captured.
        return o

    def _load_orders_safely(self, loader) -> list[dict]:
        """Wraps a store list_* call so a single corrupt/unexpected order
        file logs a warning and gets skipped, rather than a KeyError from
        backfilling it taking out every other order along with it."""
        loaded = []
        for o in loader():
            try:
                loaded.append(self._backfill_order_fields(o))
            except Exception as e:
                log.error("Skipping unreadable order file (id=%s): %s", o.get("order_id", "?"), e)
        return loaded

    async def load_from_disk(self):
        for o in self._load_orders_safely(store.list_orders):
            if o.get("owner", "live") != self.owner:
                continue  # this order belongs to the OTHER OrderManager instance -- see the "owner"
                           # field's comment at order-creation time for why this matters; a pre-existing
                           # order saved before this field existed defaults to "live" (correct for the
                           # overwhelming majority -- Regular OMS orders are always live, and this is the
                           # safe assumption for any Program order saved before Paper mode was in heavy use)
            self._orders[o["order_id"]] = o
        for o in self._load_orders_safely(store.list_archived_orders):
            if o.get("owner", "live") != self.owner:
                continue
            self._archived_orders[o["order_id"]] = o
        active = [o for o in self._orders.values() if o["status"] not in TERMINAL_STATUSES]
        log.info("[%s] Loaded %d orders from disk, %d still active", self.owner, len(self._orders), len(active))
        self._resubscribe_live_price_symbols()

    def _resubscribe_live_price_symbols(self):
        """Subscribes L1 ticks for any open (watching) order that has a
        stream symbol -- needed for live P&L display, and for trailing when
        a leg has it enabled. Deliberately NOT subscribed for orders without
        a stream_symbol (no live P&L for those, but nothing else needs it).
        Also includes any symbol currently being on-demand price-fetched
        from the New Order page (see fetch_live_price).

        When self.subscribe_all_active is True (the paper-dedicated
        instance only -- see __init__), ALSO subscribes PENDING entries,
        not just watching ones: PaperBrokerClient needs a live price to
        simulate an entry fill in the first place, which a real broker
        never needs (it does its own matching independent of whether we're
        watching the tick feed)."""
        symbols = set(self._adhoc_price_symbols)
        for o in self._orders.values():
            if o["status"] == "watching" and o.get("stream_symbol"):
                symbols.add(o["stream_symbol"])
            elif self.subscribe_all_active and o["status"] not in TERMINAL_STATUSES and o.get("stream_symbol"):
                symbols.add(o["stream_symbol"])
        self.stream.set_trailing_symbols(symbols, owner=self.owner)

    async def fetch_market_snapshot(self, stream_symbol: str, timeout: float = 8.0) -> dict | None:
        """Temporarily subscribes to that symbol's live ticks (reusing the
        same subscription mechanism trailing/P&L already use), waits for
        the next tick, then drops the temporary subscription again unless
        a real order still needs it. Returns the FULL merged tick dict
        (ltp, OI, session open/high/low, vwap, etc. -- whatever the SDK
        has accumulated for this symbol so far) or None on timeout (e.g.
        market closed, bad symbol, feed not connected). fetch_live_price
        below is a thin wrapper over this for callers that only want ltp."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._price_fetch_waiters.setdefault(stream_symbol, []).append(future)
        self._adhoc_price_symbols.add(stream_symbol)
        self._resubscribe_live_price_symbols()
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._price_fetch_waiters.get(stream_symbol, [])
            if future in waiters:
                waiters.remove(future)
            if not waiters:
                self._price_fetch_waiters.pop(stream_symbol, None)
                self._adhoc_price_symbols.discard(stream_symbol)
                self._resubscribe_live_price_symbols()

    async def fetch_live_price(self, stream_symbol: str, timeout: float = 8.0) -> float | None:
        """One-off "what's the current price" for the New Order page's Fetch
        Price button, and every other existing ltp-only caller -- unchanged
        behavior, now just extracting ltp from fetch_market_snapshot."""
        snapshot = await self.fetch_market_snapshot(stream_symbol, timeout=timeout)
        return snapshot.get("ltp") if snapshot else None

    async def fetch_greeks_snapshot(self, stream_symbol: str, timeout: float = 8.0) -> dict | None:
        """One-shot live Greeks/IV fetch for entry_signals' IV-rank gate --
        deliberately separate from the price-waiter mechanism above (see
        _greeks_fetch_waiters). Returns None on timeout, which IS the live
        entitlement signal: if this account isn't provisioned for the
        Greeks channel, this is how that's discovered, naturally, the
        first time a Program actually tries to use it -- see
        entry_signals.evaluate_entry's greeks_unverifiable handling."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._greeks_fetch_waiters.setdefault(stream_symbol, []).append(future)
        self.stream.subscribe_greeks_snapshot({stream_symbol})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._greeks_fetch_waiters.get(stream_symbol, [])
            if future in waiters:
                waiters.remove(future)
            if not waiters:
                self._greeks_fetch_waiters.pop(stream_symbol, None)

    # --------------------------------------------------------- public API

    def list_orders(self) -> list[dict]:
        return sorted(self._orders.values(), key=lambda o: o["created_at"], reverse=True)

    def list_archived_orders(self) -> list[dict]:
        return sorted(self._archived_orders.values(), key=lambda o: o["created_at"], reverse=True)

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id) or self._archived_orders.get(order_id)

    def get_orders_by_cycle(self, cycle_id: str) -> list[dict]:
        """Both legs of an Advanced OMS cycle, active or archived -- the
        Program orchestrator uses this to check whether a cycle's legs
        have both closed yet."""
        return [o for o in list(self._orders.values()) + list(self._archived_orders.values())
                if o.get("cycle_id") == cycle_id]

    def archive_order(self, order_id: str) -> dict:
        order = self._orders.get(order_id)
        if not order:
            raise ValueError("order not found (already archived, or doesn't exist)")
        if order["status"] not in TERMINAL_STATUSES:
            raise ValueError(f"can't archive an order that's still active (status: {order['status']})")
        if not store.archive_order(order_id):
            raise ValueError("order file not found on disk")
        del self._orders[order_id]
        self._archived_orders[order_id] = order
        return order

    def unarchive_order(self, order_id: str) -> dict:
        order = self._archived_orders.get(order_id)
        if not order:
            raise ValueError("archived order not found")
        if not store.unarchive_order(order_id):
            raise ValueError("order file not found in archive")
        del self._archived_orders[order_id]
        self._orders[order_id] = order
        return order

    def archive_orders_bulk(self, order_ids: list[str]) -> dict:
        """Archives as many of the given orders as are eligible, and reports
        which ones weren't (still active, or not found) rather than failing
        the whole batch over one bad id."""
        archived, failed = [], {}
        for oid in order_ids:
            try:
                self.archive_order(oid)
                archived.append(oid)
            except ValueError as e:
                failed[oid] = str(e)
        return {"archived": archived, "failed": failed}

    async def create_and_place_order(self, req) -> dict:
        strategy = store.load_strategy_by_name(req.strategy_name)
        if not strategy:
            raise ValueError(f"Strategy '{req.strategy_name}' not found")
        return await self.create_and_place_order_with_strategy(req, strategy)

    async def create_and_place_order_with_strategy(self, req, strategy: dict, program_tag: dict | None = None) -> dict:
        """The actual order-creation logic, taking an already-resolved
        strategy dict directly instead of looking one up by name. Exists
        so the Advanced OMS's Program orchestrator can place a leg using
        its own in-memory stop/target config (never saved as a named
        Strategy a person would pick from a dropdown) while still going
        through the exact same entry/exit/trailing/reconciliation machinery
        as every other order in this app -- see program_manager.py's
        module docstring for why that reuse matters.

        program_tag, when given, is {"program_id": ..., "cycle_id": ...,
        "leg": "CE"|"PE"} -- stored on the order so the orchestrator can
        later ask "which orders make up this cycle" and so the order
        card's own UI can show which Program/cycle it belongs to.
        """
        order_id = uuid.uuid4().hex[:12]
        exit_side = "sell" if req.side == "buy" else "buy"
        entry_mkt_prot = _resolve_mkt_prot(None, req.entry_type)
        tick_size = strategy.get("tick_size") or DEFAULT_TICK_SIZE

        # a user-specified entry price is rounded UP to the tick size, per
        # an explicit request -- applies to both the limit price and the
        # trigger price if given (stop-type entries need both to be valid ticks)
        entry_limit_price = _round_up_to_tick(req.entry_limit_price, tick_size) if req.entry_limit_price is not None else None
        entry_trig_price = _round_up_to_tick(req.entry_trig_price, tick_size) if req.entry_trig_price is not None else None

        order = {
            "order_id": order_id,
            "label": req.label or req.sym_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "sym_id": req.sym_id,
            "stream_symbol": req.stream_symbol,
            "side": req.side,
            "exit_side": exit_side,
            "qty": req.qty,
            "lot_size": req.lot_size,  # informational, but needed so Re-enter can reproduce the same lots/size
            "product": strategy["product"],
            "strategy_name": req.strategy_name,
            "tick_size": tick_size,
            "owner": self.owner,  # "live" | "paper" -- set here, at creation time, from the OWNING
                # OrderManager instance itself (never guessed later). This is the fix for a real, severe
                # bug: load_from_disk() used to read EVERY order file indiscriminately, with no way to
                # tell which of the two OrderManager instances (live/paper) should actually own a given
                # order -- so BOTH instances loaded EVERY order into their own memory, and GET /api/orders
                # (which concatenates both) returned every single order, live or paper, TWICE. See
                # load_from_disk()'s filter below, which this field now makes possible.
            "exit_mode": req.exit_mode,  # "both" | "sl_only" | "target_only" | "none" -- WHICH leg(s) get
                # watched. Every exit closes via a plain market order the app fires itself the instant a
                # trigger crosses -- no broker-side conditional order is ever placed, for any exit_mode.
            "entry": {
                "type": req.entry_type,
                "limit_price": entry_limit_price,
                "trig_price": entry_trig_price,
                "validity": req.entry_validity,
                "mkt_prot": entry_mkt_prot,
                "broker_order_id": None,
                "status": "pending",
                "avg_price": None,
                "fill_qty": 0,
                "filled_at": None,
            },
            # stored as strategy-relative offsets until the entry fills, at
            # which point _finalize_exit_leg() converts them to concrete
            # price levels (see that function's docstring)
            "stop": _pending_leg_state(strategy["stop"]),
            "target": _pending_leg_state(strategy["target"]),
            "square_off": None,
            "time_exit": strategy["time_exit"],
            "status": "entry_pending",
            "close_reason": None,
            "last_ltp": None,
            "last_ltt": None,
            "pnl": {"unrealized": None, "unrealized_pct": None, "realized": None, "exit_avg_price": None, "source": None},
            "warning": None,  # {"message": str, "since": iso timestamp} when a protective order needs attention
            "trail_update_count": 0,    # successful trailing modifies, lifetime
            "trail_failure_count": 0,   # failed trailing modifies, lifetime
            "program_id": (program_tag or {}).get("program_id"),  # non-None only for an Advanced OMS leg
            "cycle_id": (program_tag or {}).get("cycle_id"),
            "program_leg": (program_tag or {}).get("leg"),         # "CE" | "PE" | None
            "execution_mode": (program_tag or {}).get("execution_mode", "manual_config"),
            "target_regime": (program_tag or {}).get("target_regime", "ANY"),
            "index_id": (program_tag or {}).get("index_id"),
            "trail_check_interval_seconds": strategy.get("trail_check_interval_seconds", 0) or 0,  # copied
                # from the resolved Strategy (Regular OMS) or the Program's own leg_strategy dict, same
                # pattern as tick_size/time_exit above -- 0 (absent for a plain Strategy dict, or an
                # explicit 0) means no aggregation, today's exact per-tick behavior
            "exit_confirmation_windows": strategy.get("exit_confirmation_windows", 1) or 1,  # on both
                # StrategyConfig and ProgramConfig -- full parity; 1 (fire on first crossing) if the
                # resolved strategy dict doesn't set it
            "stop_breach_force_close_count": strategy.get("stop_breach_force_close_count", 0) or 0,
            "stop_breach_count": 0,  # lifetime counter, starts fresh for every new order
            "broker_id": getattr(self.client, "broker_id", None),  # None for paper (PaperBrokerClient
                # has no broker_id at all -- it isn't a real broker); the live client's own broker_id
                # otherwise (see TradejiniClient.broker_id)
            "strategy_snapshot": dict(strategy),  # the resolved strategy/leg_strategy dict exactly as
                # used for THIS order -- survives independently of whatever the named Strategy or
                # Program config later becomes (renamed, edited, deleted)
            "logs": [],
        }

        exit_needs_stream = req.exit_mode != "none" and (
            (req.exit_mode in ("both", "sl_only") and strategy["stop"]["trailing"]["enabled"])
            or (req.exit_mode in ("both", "target_only") and strategy["target"]["trailing"]["enabled"])
        )
        trailing_no_stream = not req.stream_symbol and exit_needs_stream
        origin = f"Program {program_tag['program_id']} ({program_tag.get('leg', '?')} leg)" if program_tag else f"strategy '{req.strategy_name}'"
        self._log(order, f"Order created from {origin}. Placing {req.side} entry for qty {req.qty} on {req.sym_id}.")
        if req.exit_mode != "both":
            self._log(order, f"Exit mode: {req.exit_mode} (default is 'both' -- SL and Target).")
        if req.entry_limit_price is not None and entry_limit_price != req.entry_limit_price:
            self._log(order, f"Requested entry limit price {req.entry_limit_price} rounded UP to tick size -> {entry_limit_price}.")
        if req.entry_trig_price is not None and entry_trig_price != req.entry_trig_price:
            self._log(order, f"Requested entry trigger price {req.entry_trig_price} rounded UP to tick size -> {entry_trig_price}.")
        if trailing_no_stream:
            self._log(order, "WARNING: this strategy has trailing enabled but no stream symbol was given -- "
                              "SL/Target will be watched but will NOT trail.")
        if req.exit_mode == "none":
            self._log(order, "WARNING: exit_mode is 'none' -- NO stop-loss or target will be watched. "
                              "This position is only ever closed manually or by a time-based rule.")
        self._orders[order_id] = order
        store.save_order(order)

        # ONLY meaningful for PaperBrokerClient (guarded so the real
        # Tradejini path is never touched by this at all) -- see that
        # module's docstring for why this registration step exists instead
        # of threading stream_symbol through place_order's actual payload
        if hasattr(self.client, "register_symbol"):
            self.client.register_symbol(req.sym_id, req.stream_symbol)

        entry_payload = dict(
            symId=req.sym_id,
            qty=req.qty,
            side=req.side,
            type=req.entry_type,
            product=strategy["product"],
            limitPrice=entry_limit_price,
            trigPrice=entry_trig_price,
            validity=req.entry_validity,
            mktProt=entry_mkt_prot,
            remarks=order_id,
        )
        try:
            resp = await self.client.place_order(**entry_payload)
            order["entry"]["broker_order_id"] = resp["d"]["orderId"]
            self._log(order, f"Entry order placed at broker, id={resp['d']['orderId']}.")
        except TradejiniApiError as e:
            order["status"] = "entry_rejected"
            self._log(order, f"Entry order REJECTED by broker: {e}")
            failure_log.log_failure(category="entry_rejected", order_id=order_id, program_id=order.get("program_id"),
                                     message=str(e), request=entry_payload, response=e.payload)
            self._on_terminal(order)  # this order never reaches "watching" at all -- this is its
                                        # ONLY terminal transition, so its factsheet has to be written
                                        # here or never (the caution rule's caught gap -- see _on_terminal)

        store.save_order(order)
        self._resubscribe_live_price_symbols()  # harmless no-op for live (subscribe_all_active=False means
                                                   # this just recomputes the same set as before); necessary
                                                   # for the paper-dedicated instance, whose freshly-created
                                                   # pending entry needs its stream_symbol subscribed
                                                   # immediately so PaperBrokerClient can start seeing ticks

        # Fire off backfill for the symbol if we don't have it already
        if req.stream_symbol and req.sym_id:
            asyncio.create_task(self._backfill_symbol_momentum(req.stream_symbol, req.sym_id))
            
        return order

    async def request_close(self, order_id: str, reason: str = "manual", price: float | None = None) -> dict:
        order = self._orders.get(order_id)
        if not order:
            raise ValueError("order not found")
        if order["status"] in TERMINAL_STATUSES or order["status"] == "closing":
            return order
        order["close_reason"] = reason
        order["status"] = "closing"
        order["closing_since"] = now_iso()
        close_kind = f"at limit price ~{price}" if price is not None else "at market"
        self._log(order, f"Close requested ({reason}), {close_kind}. Cancelling protective orders and squaring off.")
        store.save_order(order)
        await self._do_close(order, price=price)
        return order

    # ------------------------------------------------------ closing flow

    async def _do_close(self, order: dict, price: float | None = None):
        # cancel the still-pending entry order if it never filled
        if order["entry"]["status"] == "pending" and order["entry"]["broker_order_id"]:
            try:
                await self.client.cancel_order(order["entry"]["broker_order_id"])
                self._log(order, "Pending entry order cancelled.")
            except TradejiniApiError as e:
                self._log(order, f"Cancel entry failed (may have already filled): {e}")

        # every exit is watched, not placed at the broker -- there's no
        # conditional order to cancel here at all; just square off whatever's
        # still filled below

        # square off any remaining filled quantity, at the requested limit
        # price if one was given (rounded DOWN to the instrument's tick
        # size, as requested -- see _round_down_to_tick), otherwise at market
        filled = order["entry"]["fill_qty"] or 0
        already_exited = 0  # tracked via reconciliation of positions; kept simple here
        remaining = filled - already_exited
        if remaining and remaining > 0 and not order.get("square_off"):
            sq_payload = None
            try:
                if price is not None:
                    tick_size = order.get("tick_size") or DEFAULT_TICK_SIZE
                    limit_price = _round_down_to_tick(price, tick_size)
                    sq_payload = dict(
                        symId=order["sym_id"], qty=remaining, side=order["exit_side"], type="limit",
                        product=order["product"], limitPrice=limit_price, validity="day", remarks=order["entry"]["broker_order_id"],
                    )
                    resp = await self.client.place_order(**sq_payload)
                    self._log(order, f"Square-off LIMIT order placed for qty {remaining} @ {limit_price} "
                                      f"(requested {price}, rounded down to tick size {tick_size}).")
                else:
                    sq_payload = dict(
                        symId=order["sym_id"], qty=remaining, side=order["exit_side"], type="market",
                        product=order["product"], validity="day", mktProt=DEFAULT_MARKET_PROTECTION_PERCENT,
                        remarks=order["entry"]["broker_order_id"],
                    )
                    resp = await self.client.place_order(**sq_payload)
                    self._log(order, f"Square-off MARKET order placed for qty {remaining}.")
                order["square_off"] = {"broker_order_id": resp["d"]["orderId"], "status": "pending"}
                self._clear_warning(order)
            except TradejiniApiError as e:
                self._log(order, f"Square-off order FAILED: {e}. Manual intervention may be required.")
                self._set_warning(order, f"Square-off order FAILED — you tried to close this position and "
                                          f"it didn't go through. It may still be open at the broker. {e}")
                failure_log.log_failure(category="square_off_failed", order_id=order["order_id"], program_id=order.get("program_id"),
                                         message=str(e), request=sq_payload, response=e.payload)
        elif not remaining:
            # nothing was ever filled (entry cancelled before any fill) vs. a
            # position that genuinely got flattened -- record the right terminal state
            order["status"] = "closed" if filled else "cancelled"
            self._on_terminal(order)
            self._log(order, "Nothing left to square off; marking " + order["status"] + ".")

        store.save_order(order)
        self._resubscribe_live_price_symbols()

    def _spawn_close(self, order: dict):
        """Fires _do_close() as a background task instead of awaiting it
        inline -- used ONLY by _maybe_app_market_exit (the tick-triggered
        exit path), which runs on the single shared stream-consumer task
        that EVERY order/symbol's live ticks funnel through (see
        main.py's _stream_consumer_loop). Awaiting _do_close() there
        inline would block that one shared task on this order's real
        broker round-trip (cancel/place, each subject to the REST
        client's own timeout) -- with more than one order/Program
        active, that stalls trailing AND exit-trigger checks for EVERY
        OTHER order until this one's close resolves. That was the root
        cause of the "multiple Programs eventually freeze" bug.

        Safe to background: order["status"] is already flipped to
        "closing" synchronously, before this is called, with no `await`
        in between -- so a later tick for this same order is filtered
        out by the "status != watching" guard in handle_l1_tick's loop
        regardless of whether this background task has even started
        running yet, so it can't double-fire a second close.

        request_close() (manual close, REST-driven) and
        _check_time_exits() (runs on the separate reconcile_loop task)
        both still AWAIT _do_close() directly -- neither of them runs on
        the shared stream-consumer task, and request_close()'s REST
        caller needs the synchronous result to report success/failure."""
        task = asyncio.create_task(self._do_close(order))
        self._background_close_tasks.add(task)
        task.add_done_callback(lambda t, o=order: self._on_close_task_done(t, o))

    def _on_close_task_done(self, task: asyncio.Task, order: dict):
        self._background_close_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # _do_close() already catches TradejiniApiError internally (logs it
            # on the order, sets a warning) -- reaching here means a genuine
            # bug, not an expected broker failure, so it needs to be loud
            # rather than vanishing as an "exception was never retrieved"
            # warning nobody sees
            log.exception("Background close task failed unexpectedly for order %s", order.get("order_id"), exc_info=exc)
            failure_log.log_failure(category="app_market_exit_background_failed", order_id=order.get("order_id"),
                                     program_id=order.get("program_id"), message=f"Background _do_close task raised: {exc}")

    # -------------------------------------------------------- reconcile

    def nudge_reconcile(self):
        self._reconcile_event.set()

    def lock(self) -> asyncio.Lock:
        """Exposes the SAME lock reconcile_loop() already uses around
        _reconcile_once()/_check_time_exits(), for broker_reconcile.py's
        correction-writing phase to hold too -- a small public accessor
        rather than that module reaching into a private attribute
        directly. The two never touch the same order (the live loop only
        ever processes non-terminal orders; reconciliation only ever
        processes terminal ones), but archiving an order (a separate,
        concurrent path) touches the container dicts these iterate over,
        so sharing the lock during a correction write is a cheap defensive
        measure against that narrow overlap."""
        return self._lock

    async def reconcile_loop(self):
        while True:
            try:
                await asyncio.wait_for(self._reconcile_event.wait(), timeout=config.EXIT_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._reconcile_event.clear()
            async with self._lock:
                await self._reconcile_once()
                await self._check_time_exits()

    async def _reconcile_once(self):
        active = [o for o in self._orders.values() if o["status"] not in TERMINAL_STATUSES]
        if not active:
            return
        try:
            broker_orders = await self.client.get_orders()
        except TradejiniApiError as e:
            log.warning("Reconcile: could not fetch orders: %s", e)
            return
        by_id = {bo.get("orderId"): bo for bo in broker_orders}

        changed = False
        for order in active:
            if order["status"] == "entry_pending":
                changed |= await self._reconcile_entry(order, by_id)
            if order["status"] == "closing" and order.get("square_off"):
                changed |= self._reconcile_square_off(order, by_id)

        if changed:
            self._resubscribe_live_price_symbols()

    async def _reconcile_entry(self, order: dict, by_id: dict) -> bool:
        bo = by_id.get(order["entry"]["broker_order_id"])
        if not bo:
            return False
        order["entry"]["status"] = bo.get("status", order["entry"]["status"])
        order["entry"]["fill_qty"] = bo.get("fillQty", order["entry"]["fill_qty"])
        order["entry"]["avg_price"] = bo.get("avgPrice", order["entry"]["avg_price"])

        if bo.get("status") == "completed":
            self._log(order, f"Entry FILLED at avg price {bo.get('avgPrice')}.")
            order["entry"]["filled_at"] = now_iso()
            entry_avg = bo.get("avgPrice")
            sign = 1 if order["side"] == "buy" else -1
            if entry_avg:
                _finalize_exit_leg(order["stop"], entry_avg, direction=-sign, tick_size=order.get("tick_size") or DEFAULT_TICK_SIZE)
                _finalize_exit_leg(order["target"], entry_avg, direction=sign, tick_size=order.get("tick_size") or DEFAULT_TICK_SIZE)
                self._log(
                    order,
                    f"Exit levels set from strategy: stop {order['stop']['current_trig_price']}, "
                    f"target {order['target']['current_trig_price']}.",
                )
            order["status"] = "position_open"
            store.save_order(order)
            await self._place_exit_orders(order)
            return True
        elif bo.get("status") in ("rejected", "cancelled"):
            order["status"] = "entry_rejected" if bo.get("status") == "rejected" else "cancelled"
            self._on_terminal(order)
            self._log(order, f"Entry order ended: {bo.get('status')} ({bo.get('reason', '')}).")
            store.save_order(order)
            return True
        return False

    async def _place_exit_orders(self, order: dict):
        """Every exit watches live price and fires a plain market order the
        instant a trigger crosses -- no broker-side conditional order is
        ever placed, for any exit_mode. exit_mode only decides WHICH leg(s)
        get watched (default "both" means stop and target both; "none"
        means neither, only manual/time-based close applies)."""
        order["status"] = "watching"
        mode = order.get("exit_mode", "both")
        watching = {"both": "stop and target", "sl_only": "stop", "target_only": "target", "none": "nothing"}[mode]
        self._log(order, f"Watching live price for {watching} -- a plain market order fires the instant "
                          f"the trigger is crossed.")
        store.save_order(order)

    async def _backfill_symbol_momentum(self, stream_symbol: str, sym_id: str):
        state = self._symbol_momentum.setdefault(stream_symbol, {"window": [], "last_update": 0, "state": "Steady", "prev": None})
        if state["window"]: 
            return # Already backfilled or has live data
            
        try:
            # Import real client dynamically for potential cross-module circularity
            from .main import client as real_client
            api_client = self.client if hasattr(self.client, "get_interval_chart_data") else real_client
            
            is_option = len(sym_id.split('_')) >= 3 and sym_id.split('_')[1] in ("OPTIDX", "OPTSTK")
            window_minutes = 9 if is_option else 5
            
            to_ts = int(time.time())
            from_ts = to_ts - (window_minutes * 60 * 5) # fetch a bit extra
            
            resp = await api_client.get_interval_chart_data(sym_id, "1", from_ts, to_ts)
            bars = resp.get("bars", []) if isinstance(resp, dict) else resp
            if not bars and isinstance(resp, dict):
                bars = resp.get("chartData", [])
                
            closes = []
            for row in bars:
                if isinstance(row, list) and len(row) >= 5:
                    closes.append(float(row[4]))
                elif isinstance(row, dict) and "close" in row:
                    closes.append(float(row["close"]))
                    
            if closes and not state["window"]:
                state["window"] = closes[-window_minutes:-1]
                self._update_symbol_momentum(stream_symbol, closes[-1])
                
                # Apply the newly backfilled state to all watching orders immediately
                for order in self._orders.values():
                    if order["status"] == "watching" and order.get("stream_symbol") == stream_symbol:
                        order["momentum_state"] = state.get("state", "Steady")
                        order["momentum_prev"] = state.get("prev")
                        
        except Exception as e:
            log.warning("Failed to backfill momentum history for %s: %s", stream_symbol, e)

    def _update_symbol_momentum(self, symbol: str, ltp: float):
        state = self._symbol_momentum.setdefault(symbol, {"window": [], "last_update": 0, "state": "Steady", "prev": None})
        window = state["window"]
        
        # throttle momentum updates to ~1 per second max
        now = time.monotonic()
        if now - state["last_update"] < 1.0:
            return
        state["last_update"] = now
        
        is_option = "CE_" in symbol.upper() or "PE_" in symbol.upper()
        max_len = 9 if is_option else 5
        
        window.append(ltp)
        if len(window) > max_len:
            window.pop(0)
            
        if len(window) > 1:
            n = len(window)
            x_mean = (n - 1) / 2.0
            y_mean = sum(window) / n
            numerator = sum((i - x_mean) * (window[i] - y_mean) for i in range(n))
            denominator = sum((i - x_mean)**2 for i in range(n))
            slope = numerator / denominator if denominator != 0 else 0
            
            is_positive = ltp >= y_mean
            if is_positive and slope > 0:
                new_state = "Dark Green"
            elif is_positive and slope <= 0:
                new_state = "Light Green"
            elif not is_positive and slope > 0:
                new_state = "Amber"
            elif not is_positive and slope <= 0:
                new_state = "Red"
            else:
                new_state = "Steady"
                
            if state["state"] != new_state:
                state["prev"] = state["state"]
                state["state"] = new_state

    def _reconcile_square_off(self, order: dict, by_id: dict) -> bool:
        bo = by_id.get(order["square_off"]["broker_order_id"])
        if bo and bo.get("status") == "completed":
            order["square_off"]["status"] = "completed"
            order["status"] = "closed"
            order["pnl"]["source"] = "broker order record (exact, square-off)"
            self._finalize_realized_pnl(order, bo.get("avgPrice"))
            # _on_terminal (and the factsheet write inside it) runs AFTER _finalize_realized_pnl,
            # deliberately -- the factsheet must capture the FINAL realized P&L, not whatever was
            # there (typically still None) before this order's exit price was actually known
            self._on_terminal(order)
            self._log(order, f"Square-off filled at {bo.get('avgPrice')} -- position closed. "
                              f"P&L: {order['pnl']['realized']}.")
            store.save_order(order)
            return True
        if bo and bo.get("status") in ("rejected", "cancelled"):
            reason = bo.get("reason") or bo.get("status")
            self._revert_stuck_square_off(order, f"was {bo.get('status')} by the broker ({reason})",
                                           category="square_off_rejected", response=bo)
            return True
        # THIRD case, distinct from both above: the square-off was accepted
        # (bo exists) or hasn't even matched yet (bo is None), but has
        # neither confirmed filled NOR confirmed rejected/cancelled for a
        # long time. This used to be a silent dead end just like the
        # rejection case originally was: an order placed at or after
        # market close can be accepted by the broker but never resolve to
        # any terminal state within the session, and with no timeout check
        # at all, this order would sit in "closing" -- misleadingly still
        # showing its last-known "(live)" P&L, frozen at whatever it was
        # the instant the close fired, since ticks stop updating it the
        # moment status leaves "watching" -- indefinitely, invisible to
        # the trigger-monitoring loop, with no warning at all.
        closing_since = order.get("closing_since")
        if closing_since:
            try:
                elapsed = (clock.now() - clock.parse_iso(closing_since)).total_seconds()
            except ValueError:
                elapsed = 0
            if elapsed > SQUARE_OFF_STUCK_TIMEOUT_SECONDS:
                self._revert_stuck_square_off(
                    order, f"has not resolved after {int(elapsed)}s (likely placed at/after market close)",
                    category="square_off_stuck", response=bo)
                return True
        return False

    def _revert_stuck_square_off(self, order: dict, reason_text: str, *, category: str, response):
        """Shared recovery path for a square-off that will never confirm a
        close on its own -- whether explicitly rejected/cancelled, or just
        stuck unresolved past the timeout. Reverts to "watching" so the
        order resumes being watched and gets a genuine retry the next time
        a live tick arrives and its trigger re-evaluates (which, notably,
        naturally can't happen again and again in a tight loop if the
        market is genuinely closed -- no new ticks means no new retry
        attempts until the next session's first tick, so this can't hammer
        the broker with repeated attempts while stuck). Clears the failed
        square_off so a fresh attempt isn't blocked by _do_close's own
        "already has a square_off" guard, and raises a prominent, persistent
        warning so this is never silently invisible."""
        self._log(order, f"Square-off {reason_text} -- this position is STILL OPEN. Reverting to watched "
                          f"state for an automatic retry the next time a live tick arrives.")
        self._set_warning(order, f"A close attempt {reason_text} -- this position is still open. The app "
                                  f"will automatically retry closing it the next time a live tick arrives "
                                  f"and its trigger re-evaluates, but check it manually if this keeps "
                                  f"happening or if the market is closed for an extended period.")
        failure_log.log_failure(category=category, order_id=order["order_id"], program_id=order.get("program_id"),
                                 message=f"Square-off {reason_text}", response=response)
        order["square_off"] = None
        order["status"] = "watching"
        if order.get("close_reason") not in ("manual", "program_cycle_manual_close", "program_flatten"):
            order["close_reason"] = None
        order["closing_since"] = None
        store.save_order(order)

    def _finalize_realized_pnl(self, order: dict, exit_avg_price):
        order["pnl"]["exit_avg_price"] = exit_avg_price
        order["pnl"]["unrealized"] = None
        order["pnl"]["unrealized_pct"] = None
        
        if order.get("close_reason") in ("stop_hit", "target_hit") and exit_avg_price is not None:
            leg = order["stop"] if order["close_reason"] == "stop_hit" else order["target"]
            expected = leg.get("current_trig_price")
            if expected is not None:
                # Slippage > 0 means we lost money compared to expected price
                order["pnl"]["slippage"] = round((expected - exit_avg_price) * (1 if order["side"] == "buy" else -1), 4)

        entry_avg = order["entry"]["avg_price"]
        if exit_avg_price is None or entry_avg is None:
            return
        sign = 1 if order["side"] == "buy" else -1
        # entry.fill_qty (what actually filled), NOT order["qty"] (what was requested) -- on a partial
        # fill these differ, and _do_close's own square-off already squares off fill_qty specifically
        # (see "filled = order["entry"]["fill_qty"]"), so P&L must be computed on that same quantity
        realized = (exit_avg_price - entry_avg) * order["entry"]["fill_qty"] * sign
        order["pnl"]["realized"] = round(realized, 2)

    # ----------------------------------------------------------- timers

    async def _check_time_exits(self):
        now = clock.now()
        for order in list(self._orders.values()):
            if order["status"] not in ("entry_pending", "position_open", "watching"):
                continue
            te = order.get("time_exit") or {}
            due = False
            if te.get("mode") == "intraday_window" and te.get("window_start"):
                start = _parse_hhmm(te["window_start"])
                end = _parse_hhmm(te.get("window_end") or te["window_start"])
                if start and now.time() >= start:
                    due = True
                    reason = f"intraday window ({te['window_start']}-{te.get('window_end','')})"
            elif te.get("mode") == "datetime" and te.get("at"):
                try:
                    at_dt = clock.parse_iso(te["at"])
                    if now >= at_dt:
                        due = True
                        reason = f"scheduled close time {te['at']}"
                except ValueError:
                    pass
            else:
                reason = ""

            if due:
                self._log(order, f"Time-based exit triggered: {reason}.")
                order["status"] = "closing"
                order["close_reason"] = "time_exit"
                order["closing_since"] = now_iso()
                store.save_order(order)
                await self._do_close(order)

    # ---------------------------------------------------------- trailing

    async def handle_l1_tick(self, data: dict):
        symbol = data.get("symbol")
        ltp = data.get("ltp")
        ltt = data.get("ltt")  # exchange's own last-traded-time, not present on every tick spec
        if symbol is None or ltp is None:
            return

        self._latest_prices[symbol] = ltp

        waiters = self._price_fetch_waiters.get(symbol)
        if waiters:
            for future in waiters:
                if not future.done():
                    # the FULL merged tick dict (not just ltp) -- fetch_live_price extracts .get("ltp"),
                    # fetch_market_snapshot returns it whole (OI, session O/H/L, vwap, etc.)
                    future.set_result(dict(data))

        self._update_symbol_momentum(symbol, ltp)

        for order in self._orders.values():
            if order["status"] != "watching" or order.get("stream_symbol") != symbol:
                continue
                
            m = self._symbol_momentum.get(symbol, {})
            new_state = m.get("state", "Steady")
            old_state = order.get("momentum_state", "Steady")
            
            if old_state != new_state and new_state in ("Dark Green", "Red"):
                import asyncio
                from .notifier import send_telegram_alert
                signal_type = "LONG" if new_state == "Dark Green" else "SHORT"
                icon = "🟢" if signal_type == "LONG" else "🔴"
                asyncio.create_task(send_telegram_alert(
                    f"{icon} <b>{signal_type} ENTRY SIGNAL</b> {icon}\n\n"
                    f"Symbol: {symbol}\n"
                    f"Momentum just flipped to {new_state}!\n"
                    f"Check dashboard for manual entry."
                ))
            
            order["momentum_state"] = new_state
            order["momentum_prev"] = m.get("prev")
            
            self._update_live_pnl(order, ltp, ltt)  # unconditional, every raw tick -- display accuracy is a

            interval = order.get("trail_check_interval_seconds") or 0
            if interval <= 0:
                # today's exact behavior when aggregation isn't configured -- react to every raw tick,
                # no window state touched at all (zero new state/cost for the common, unconfigured case)
                eval_price, run_eval = ltp, True
            else:
                state = self._trail_state.setdefault(order["order_id"], {"window_start": None, "window_ticks": []})
                now = time.monotonic()
                if state["window_start"] is None:
                    state["window_start"] = now
                state["window_ticks"].append(ltp)
                if now - state["window_start"] >= interval:
                    # window closes on THIS tick -- decide using the MEDIAN of every tick seen during
                    # the window (not just this closing tick), which is what actually smooths out a
                    # single noisy/spike tick rather than merely sampling less often. Then reset for
                    # the next window, which starts fresh on the next tick that arrives.
                    eval_price = statistics.median(state["window_ticks"])
                    run_eval = True
                    state["window_start"] = None
                    state["window_ticks"] = []
                else:
                    eval_price, run_eval = None, False

            if run_eval:
                if order["stop"]["trailing"]["enabled"] or order["target"]["trailing"]["enabled"]:
                    await self._maybe_trail(order, eval_price)
                
                # check global safeguards BEFORE evaluating standard stop/target exits
                await self._maybe_app_market_exit(order, eval_price)

    async def handle_greeks_tick(self, data: dict):
        """Routed here from main.py's stream consumer for any packet whose
        msgType is "greeks" -- resolves fetch_greeks_snapshot's waiters.
        Deliberately doesn't touch order state or P&L at all; Greeks are
        entry-decision input only in this round (see entry_signals.py),
        not part of the trailing/exit engine above."""
        symbol = data.get("symbol")
        if symbol is None:
            return
        waiters = self._greeks_fetch_waiters.get(symbol)
        if waiters:
            for future in waiters:
                if not future.done():
                    future.set_result(dict(data))

    async def _maybe_app_market_exit(self, order: dict, ltp: float):
        """Watches live price directly -- no broker-side conditional order
        exists for any order at all -- and, once the stop or target trigger
        is crossed (and confirmed, if exit_confirmation_windows > 1 -- see
        below), fires the exact same reliable plain-market-order close
        already used by Stop & Flatten, manual close, and time-based exits
        (_do_close). `ltp` here is whatever handle_l1_tick decided to
        evaluate against the trigger -- the raw tick when no aggregation is
        configured, or the current window's MEDIAN tick otherwise; either
        way this function doesn't care which, it just compares a price to a
        trigger. Called once per evaluation (see handle_l1_tick); guarded
        by order["status"] != "watching" in that loop once this fires
        (status flips to "closing" synchronously, before _do_close is even
        spawned below), so it can't double-fire on a later evaluation that
        arrives while _do_close is still running -- see _spawn_close's
        docstring for why the close is backgrounded rather than awaited
        directly here."""
        # Smart Exit (Sentinel): Preemptive regime shifts
        execution_mode = order.get("execution_mode")
        target_regime = order.get("target_regime")
        index_id = order.get("index_id")
        
        if execution_mode == "sentinel" and target_regime and index_id:
            from . import main as main_app
            if main_app.regime_classifier:
                reg_data = main_app.regime_classifier.get_current_regime(index_id)
                current_regime = reg_data.get("state", "UNKNOWN")
                
                # Check for blind state or opposing state
                if current_regime == "UNKNOWN":
                    self._log(order, "Preemptive Smart Exit: Sentinel is BLIND (data failure). Liquidating to protect capital.")
                    order["status"] = "closing"
                    order["close_reason"] = "sentinel_blind"
                    order["closing_since"] = now_iso()
                    store.save_order(order)
                    self._spawn_close(order)
                    return
                elif current_regime != target_regime and target_regime != "ANY":
                    self._log(order, f"Preemptive Smart Exit: Regime shifted from {target_regime} to {current_regime}. Liquidating immediately.")
                    order["status"] = "closing"
                    order["close_reason"] = "regime_shift"
                    order["closing_since"] = now_iso()
                    store.save_order(order)
                    self._spawn_close(order)
                    return

        mode = order.get("exit_mode", "both")
        if mode == "none":
            return

        exit_side = order["exit_side"]
        stop_trig = order["stop"]["current_trig_price"] if mode in ("both", "sl_only") else None
        target_trig = order["target"]["current_trig_price"] if mode in ("both", "target_only") else None
        if exit_side == "sell":  # exiting a long -- stop below entry, target above
            stop_hit = stop_trig is not None and ltp <= stop_trig
            target_hit = target_trig is not None and ltp >= target_trig
        else:  # exit_side == "buy" -- exiting a short -- stop above entry, target below
            stop_hit = stop_trig is not None and ltp >= stop_trig
            target_hit = target_trig is not None and ltp <= target_trig

        # exit_confirmation_windows (Program-only; defaults to 1 for every Regular OMS order and any
        # Program that doesn't set it) -- a crossed trigger must stay crossed for this many CONSECUTIVE
        # evaluations (one evaluation = one raw tick if trail_check_interval_seconds is 0, or one
        # aggregation window close otherwise) before actually firing, instead of firing the instant a
        # single evaluation crosses it.
        #
        # stop_breach_force_close_count (stop side only) is the deliberate complement: a leg that keeps
        # testing its stop and recovering before confirmation -- exactly the case exit_confirmation_windows
        # can prolong rather than prevent -- gets counted. Once it's been tested this many times, the
        # NEXT hit force-closes immediately regardless of confirmation state: repeated testing is itself
        # the signal at that point, no reason to keep waiting for one more confirmed test.
        #
        # Both guarded behind a single "needs_state" check so the default case (confirm_n=1, breach
        # tracking off) never touches self._trail_state at all.
        confirm_n = order.get("exit_confirmation_windows") or 1
        breach_limit = order.get("stop_breach_force_close_count") or 0
        breach_fired = False
        if confirm_n > 1 or breach_limit > 0:
            state = self._trail_state.setdefault(order["order_id"], {})
            prev_stop_streak = state.get("stop_confirm_streak", 0)
            state["stop_confirm_streak"] = prev_stop_streak + 1 if stop_hit else 0
            state["target_confirm_streak"] = state.get("target_confirm_streak", 0) + 1 if target_hit else 0

            # A completed "near-miss episode": the stop was hit for at least one evaluation
            # (prev_stop_streak > 0) and this evaluation shows it's recovered (streak reset to
            # 0) -- without the order having actually closed in between. Persisted on the order
            # itself (not this ephemeral state dict) so it survives a restart -- see the field's
            # own comment in _backfill_order_fields for why an in-memory counter wouldn't do.
            if breach_limit > 0 and prev_stop_streak > 0 and state["stop_confirm_streak"] == 0:
                order["stop_breach_count"] = order.get("stop_breach_count", 0) + 1
                store.save_order(order)
                self._log(order, f"Stop tested and recovered before confirming "
                                  f"(breach {order['stop_breach_count']}/{breach_limit}).")

            stop_confirmed = state["stop_confirm_streak"] >= confirm_n
            breach_fired = (breach_limit > 0 and stop_hit
                             and order.get("stop_breach_count", 0) >= breach_limit)
            stop_hit = stop_hit and (stop_confirmed or breach_fired)
            target_hit = target_hit and state["target_confirm_streak"] >= confirm_n

        if not (stop_hit or target_hit):
            return
        # if a large gap somehow crosses both in the same tick, protecting
        # capital wins over capturing extra profit
        reason = "stop" if stop_hit else "target"
        trig_value = stop_trig if stop_hit else target_trig
        if breach_fired:
            confirm_note = f" (stop breached {breach_limit}+ times previously -- closing without waiting for confirmation)"
        elif confirm_n > 1:
            confirm_note = f" (confirmed over {confirm_n} consecutive evaluations)"
        else:
            confirm_note = ""
        self._log(order, f"Price {ltp} crossed the {reason} trigger ({trig_value}){confirm_note} -- firing a "
                          f"market order to close now.")
        order["status"] = "closing"
        order["close_reason"] = f"{reason}_hit"
        order["closing_since"] = now_iso()
        store.save_order(order)
        self._spawn_close(order)  # backgrounded, not awaited -- see _spawn_close's docstring for why
                                    # this specific call site must never block the shared stream-consumer task

    def get_cached_price(self, stream_symbol: str) -> float | None:
        """The most recent tick seen for this symbol, if any -- synchronous
        and non-blocking, unlike fetch_live_price() which subscribes and
        waits (up to several seconds) for a fresh one. Used by
        PaperBrokerClient, which needs to check many pending paper orders
        on every reconcile pass without paying that wait cost each time.
        Returns None if this symbol has never ticked since this process
        started (e.g. nothing has subscribed to it yet)."""
        return self._latest_prices.get(stream_symbol)

    def _update_live_pnl(self, order: dict, ltp: float, ltt: str | None = None):
        """Updates in-memory only (not written to disk on every tick -- the
        2s dashboard websocket push already picks this up from memory, and
        writing a JSON file on every price tick would be needless I/O).

        `ltt` is the EXCHANGE's own last-traded-time for this tick (decoded
        by nxtradstream.py, always IST regardless of host timezone) -- kept
        purely for diagnosis (is this tick actually fresh, or a stale
        repeat? did our clock drift from the exchange's?), never compared
        for equality or used to drive any decision, same rule
        broker_reconcile.py already follows for broker timestamps."""
        order["last_ltp"] = ltp
        if ltt is not None:
            order["last_ltt"] = ltt
        entry_avg = order["entry"]["avg_price"]
        if entry_avg is None:
            return
        sign = 1 if order["side"] == "buy" else -1
        unrealized = (ltp - entry_avg) * order["qty"] * sign
        order["pnl"]["unrealized"] = round(unrealized, 2)
        order["pnl"]["unrealized_pct"] = round((ltp - entry_avg) / entry_avg * 100 * sign, 2)

    async def _maybe_trail(self, order: dict, ltp: float):
        """Trailing is entirely local: recomputes the trigger price(s) in
        memory and persists them -- there is no broker call involved at
        all, since no broker-side order exists to notify. The updated
        values are exactly what _maybe_app_market_exit compares against on
        the next tick."""
        mode = order.get("exit_mode", "both")
        if mode == "none":
            return  # nothing being watched to trail

        sign = 1 if order["side"] == "buy" else -1
        entry_price = order["entry"]["avg_price"]
        if entry_price is None:
            return
        profit = (ltp - entry_price) * sign

        stop_changed = self._trail_leg(order, "stop", ltp, sign, profit) if mode in ("both", "sl_only") else False
        target_changed = self._trail_leg(order, "target", ltp, sign, profit) if mode in ("both", "target_only") else False

        if not (stop_changed or target_changed):
            return

        order["trail_update_count"] += 1
        store.save_order(order)
        self._log(order, f"Trailing: stop -> {order['stop']['current_trig_price']}, "
                          f"target -> {order['target']['current_trig_price']} (ltp {ltp}).")

    def _trail_leg(self, order: dict, leg_name: str, ltp: float, sign: int, profit: float) -> bool:
        leg = order[leg_name]
        trailing = leg["trailing"]
        if not trailing["enabled"] or profit < trailing["activation_offset"]:
            return False

        trail_by = trailing["trail_by"]
        
        # Smart Exits: ATR-based trailing
        execution_mode = order.get("execution_mode")
        index_id = order.get("index_id")
        if execution_mode == "sentinel" and index_id:
            from . import main as main_app
            if main_app.regime_classifier:
                reg_data = main_app.regime_classifier.get_current_regime(index_id)
                atr = reg_data.get("atr", 0.0)
                if atr > 0:
                    # Dynamically widen the stop based on ATR if it's larger than static trail_by
                    trail_by = max(trail_by, round(atr * 1.5, 2))

        offset = leg["trig_limit_offset"]
        tick_size = order.get("tick_size") or DEFAULT_TICK_SIZE

        if leg_name == "stop":
            candidate = _round_to_tick(ltp - sign * trail_by, tick_size)
            improves = (candidate - leg["current_trig_price"]) * sign > 0
        else:  # target -- extend further away in the favourable direction
            candidate = _round_to_tick(ltp + sign * trail_by, tick_size)
            improves = (candidate - leg["current_trig_price"]) * sign > 0

        if improves:
            leg["current_trig_price"] = candidate
            leg["current_limit_price"] = _round_to_tick(candidate + offset, tick_size)
            return True
        return False

    # -------------------------------------------------------------- log

    def _log(self, order: dict, msg: str):
        order["updated_at"] = now_iso()
        order["logs"].append({"ts": now_iso(), "msg": msg})
        order["logs"] = order["logs"][-200:]
        log.info("[%s] %s", order["order_id"], msg)

    def _set_warning(self, order: dict, message: str):
        """Surfaces a persistent, visible warning on the order's dashboard
        card -- not just a log line -- for anything that leaves a position
        potentially unprotected: a square-off that failed to place, got
        rejected after acceptance, or got stuck unresolved past the
        timeout. Cleared by _clear_warning once resolved, or automatically
        once the order reaches a terminal status (nothing left to protect
        by then)."""
        order["warning"] = {"message": message, "since": now_iso()}
        store.save_order(order)

    def _clear_warning(self, order: dict):
        if order.get("warning") is not None:
            order["warning"] = None
            store.save_order(order)

    def _on_terminal(self, order: dict):
        """The single place every terminal-status transition converges on
        -- called from all FOUR sites an order can reach closed/cancelled/
        entry_rejected (TERMINAL_STATUSES): the place-time rejection
        branch in create_and_place_order_with_strategy, _do_close's
        immediate-terminal branch, _reconcile_entry's rejected/cancelled
        branch, and _reconcile_square_off's completed branch. Previously
        each of the latter three duplicated `_trail_state.pop(...)`
        inline and the place-time-rejection branch didn't clean it up at
        all -- a real gap, since a leg rejected at placement never
        reaches "watching" but could still theoretically have had
        adhoc/trailing state if this ever changes. Also writes this
        order's factsheet -- the durable, outside-the-live-record
        snapshot -- exactly once, here, since this is the one place that
        reliably fires for every terminal order regardless of which of
        the four paths got it there."""
        self._trail_state.pop(order["order_id"], None)
        order["warning"] = None
        factsheet.write_order_factsheet(order)  # never raises -- see that function's own docstring



def _resolve_mkt_prot(strategy_value, entry_type) -> float | None:
    """Falls back to DEFAULT_MARKET_PROTECTION_PERCENT for market-type entry
    orders that don't specify their own -- see the constant's comment.
    Non-market entries (plain limit) don't need it, so leave those as None."""
    if strategy_value is not None:
        return strategy_value
    if entry_type in ("market", "stopmarket"):
        return DEFAULT_MARKET_PROTECTION_PERCENT
    return None


def _pending_leg_state(leg_cfg: dict) -> dict:
    """Order-creation-time shape: strategy-relative offsets, no concrete
    price yet (that needs the entry fill price, which we don't have yet)."""
    trailing = leg_cfg["trailing"]
    mode = leg_cfg["offset_mode"]
    return {
        "offset_mode": mode,
        "trig_offset": leg_cfg["trig_offset"],
        "limit_offset": leg_cfg["limit_offset"],
        "initial_trig_price": None,
        "current_trig_price": None,
        "current_limit_price": None,
        "trig_limit_offset": None,
        "trailing": {
            "enabled": trailing["enabled"],
            "trail_by": trailing["trail_by"],                    # still % or points, not yet converted
            "activation_offset": trailing["activation_offset"],  # ditto
            "trail_by_display": f"{trailing['trail_by']}{'%' if mode == 'percent' else ' pts'}",
        },
    }


def _offset_to_points(value: float, mode: str, reference_price: float) -> float:
    if mode == "percent":
        return abs(reference_price) * value / 100.0
    return value


def _finalize_exit_leg(leg: dict, entry_avg_price: float, direction: int, tick_size: float) -> None:
    """Called once, the moment the entry order fills. Converts the leg's
    strategy-relative offsets (points or %) into concrete trigger/limit
    prices anchored to the actual fill price, and converts trailing's
    trail_by/activation_offset into fixed points (computed once, relative to
    the fill price) so all the trailing math elsewhere in this file can stay
    simple plain-points arithmetic regardless of how the strategy expressed it.

    direction: +1 if this leg sits above entry, -1 if below. Stop = -sign(side),
    Target = +sign(side) -- see the two call sites in _reconcile_entry.

    Every computed price is rounded to the instrument's tick_size (not just
    2 decimals) -- the broker rejects prices that aren't a multiple of it.
    """
    mode = leg["offset_mode"]
    trig_dist = _offset_to_points(leg["trig_offset"], mode, entry_avg_price)
    limit_dist = _offset_to_points(leg["limit_offset"], mode, entry_avg_price)

    trig_price = _round_to_tick(entry_avg_price + direction * trig_dist, tick_size)
    limit_price = _round_to_tick(trig_price + direction * limit_dist, tick_size)

    leg["initial_trig_price"] = trig_price
    leg["current_trig_price"] = trig_price
    leg["current_limit_price"] = limit_price
    leg["trig_limit_offset"] = round(limit_price - trig_price, 4)

    trailing = leg["trailing"]
    trailing["trail_by"] = round(_offset_to_points(trailing["trail_by"], mode, entry_avg_price), 4)
    trailing["activation_offset"] = round(_offset_to_points(trailing["activation_offset"], mode, entry_avg_price), 4)
    # trail_by_display was already set at order-creation time and is left
    # untouched -- it still shows the original "1%" / "5 pts" the user chose


def _parse_hhmm(s: str) -> dtime | None:
    try:
        h, m = s.split(":")
        return dtime(int(h), int(m))
    except (ValueError, AttributeError):
        return None
