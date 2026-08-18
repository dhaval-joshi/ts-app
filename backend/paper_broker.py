"""
A drop-in BrokerClient implementation that simulates fills against LIVE
prices instead of placing real orders -- built specifically so a Program
in Paper mode reuses the EXACT SAME order/exit/trailing/reconciliation
machinery as a real one, with zero special-casing anywhere in
order_manager.py. That reuse is the entire point: paper and live
Programs should behave identically in every way except where the order
actually goes, so a paper run is a genuine preview of live behavior, not
a separate, simplified approximation of it.

Deliberately a SIMPLIFIED simulation, not a market-microstructure engine:
market orders fill immediately at the current live price; limit/stop
orders fill the moment the live price crosses their trigger (checked on
each get_orders() poll, reusing order_manager's own existing reconcile
cadence rather than needing a separate loop). No partial fills, no
broker-side rejections (paper mode explicitly assumes capital/margin is
always available, per the person's own framing when this was designed),
no slippage beyond whatever the live price naturally reflects. That's
enough to validate the thing paper mode actually exists to validate --
Program orchestration logic (expiry/ATM selection, safeguard triggers,
cycle timing) -- without needing to replicate Tradejini's actual matching
engine.

The one real friction point: Tradejini's real place_order payload
(which this class's place_order also accepts, for drop-in compatibility)
carries a symId but NOT a stream_symbol -- that's an app-level concept
for the live-price feed, absent from their actual order API. This class
needs live prices to simulate fills, so order_manager.py calls
register_symbol() once per order, ONLY when the client is a
PaperBrokerClient (checked via hasattr, never touching the real
Tradejini call signature at all) -- see order_manager.py's
create_and_place_order_with_strategy for the exact call site.
"""
import logging
from datetime import datetime

log = logging.getLogger("tradejini.paper_broker")


class PaperBrokerClient:
    def __init__(self, stream_manager):
        self.stream = stream_manager
        self.order_manager = None  # set AFTER construction, once the OrderManager that owns this client
                                     # exists -- genuine circular dependency (this client needs cached
                                     # prices from the order manager, which needs this client to be
                                     # constructed first) resolved by two-step wiring; see main.py
        self._orders: dict[str, dict] = {}   # synthetic orderId -> broker-order-shaped dict
        self._symbol_map: dict[str, str] = {}  # symId -> stream_symbol, via register_symbol()
        self._next_id = 1

    # ---------------------------------------------------- BrokerClient protocol --

    async def login(self) -> None:
        pass  # nothing to authenticate -- paper mode never talks to a real broker

    @property
    def auth_token(self) -> str:
        return "paper:paper"

    @property
    def is_logged_in(self) -> bool:
        return True  # paper mode is always "logged in" -- there's no real session to lose

    async def place_order(self, **fields) -> dict:
        order_id = self._new_id()
        record = self._new_record(order_id, fields)
        self._orders[order_id] = record
        self._maybe_fill_entry(record)
        return {"d": {"orderId": order_id}}

    async def cancel_order(self, order_id: str) -> dict:
        record = self._orders.get(order_id)
        if record and record.get("status") == "open":
            record["status"] = "cancelled"
        return {"d": {}}

    async def get_orders(self) -> list[dict]:
        """Called on every reconcile pass -- this is where pending paper
        orders actually get checked against the current live price and
        filled, reusing order_manager's own existing polling cadence
        rather than needing a separate simulation loop."""
        for record in self._orders.values():
            if record.get("status") == "open":
                self._maybe_fill_entry(record)
        return list(self._orders.values())

    async def close(self) -> None:
        pass

    # ------------------------------------------------------------- paper-only --

    def register_symbol(self, sym_id: str, stream_symbol: str | None):
        """NOT part of the BrokerClient protocol -- called by order_manager.py
        only when the client is a PaperBrokerClient (via hasattr), so live
        fills can be simulated. See this module's docstring for why this
        exists instead of threading stream_symbol through place_order's
        actual payload."""
        if stream_symbol:
            self._symbol_map[sym_id] = stream_symbol

    # ------------------------------------------------------------------ internals --

    def _new_id(self) -> str:
        order_id = f"PAPER{self._next_id}"
        self._next_id += 1
        return order_id

    def _new_record(self, order_id: str, fields: dict) -> dict:
        return {
            "orderId": order_id, "symId": fields.get("symId"), "side": fields.get("side"),
            "status": "open", "type": fields.get("type"),
            "limitPrice": fields.get("limitPrice"), "trigPrice": fields.get("trigPrice"),
            "qty": fields.get("qty"), "fillQty": 0, "avgPrice": None,
        }

    def _ltp(self, sym_id: str) -> float | None:
        stream_symbol = self._symbol_map.get(sym_id)
        if not stream_symbol or not self.order_manager:
            return None
        return self.order_manager.get_cached_price(stream_symbol)

    def _fill(self, record: dict, price: float, qty):
        record["status"] = "completed"
        record["avgPrice"] = price
        record["fillQty"] = qty
        record["filled_at"] = datetime.now().isoformat()

    def _maybe_fill_entry(self, record: dict):
        """Despite the name, this fills ANY plain order this class ever
        places -- an entry, or a square-off closing a position -- since
        both go through the exact same place_order() / record shape.
        There's no OCO-kind record anymore (nothing ever creates one), so
        this is the only fill path that exists."""
        order_type = record.get("type")
        ltp = self._ltp(record["symId"])
        if order_type == "market":
            # a market order has no live price to wait on if the symbol
            # was never registered (shouldn't normally happen) -- fill at
            # the limit/trigger if one happens to be set, else stay
            # pending rather than fabricate a price out of nowhere
            if ltp is not None:
                self._fill(record, ltp, record.get("qty"))
            return
        if ltp is None:
            return
        side = record.get("side")
        trig = record.get("trigPrice")
        limit = record.get("limitPrice")
        if order_type == "limit":
            crossed = (side == "buy" and ltp <= limit) or (side == "sell" and ltp >= limit)
            if crossed:
                self._fill(record, limit, record.get("qty"))
        elif order_type in ("stoplimit", "stopmarket"):
            crossed = (side == "buy" and ltp >= trig) or (side == "sell" and ltp <= trig)
            if crossed:
                fill_price = limit if order_type == "stoplimit" and limit else ltp
                self._fill(record, fill_price, record.get("qty"))
