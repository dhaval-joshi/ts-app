"""
A separate, on-demand pass over already-CLOSED, historical LIVE orders --
NOT to be confused with OrderManager.reconcile_loop(), which is the
always-on loop deciding CURRENT/open order state. That name collision
risk is exactly why this module exists under its own name: "reconcile"
already means the live loop in this codebase.

The problem this solves: nothing today periodically checks whether this
app's own records of a CLOSED order still match what the broker's own
history says actually happened. If anything ever silently drifted (a
missed tick, an edge case not yet found), there is no self-healing check
that would catch and correct it after the fact -- this is that check.

Scope, deliberately narrow:
  - LIVE orders only (owner == "live"). Paper never talks to a real
    broker, so there's nothing external to reconcile it against.
  - Already-TERMINAL orders only (closed/cancelled/entry_rejected). A
    currently-open position is the live reconcile loop's job, continuously
    -- this is specifically about catching drift in the HISTORICAL record,
    not replacing that loop.
  - Comparison spine is get_orders() (order-level status/fillQty/avgPrice)
    -- the only broker response shape this codebase has real production
    evidence for (the live loop already trusts these exact fields daily).
    get_trades() is consumed defensively, as enrichment/orphan-detection
    only, never assumed to have a specific shape beyond .get() lookups.
    get_positions() is out of scope entirely -- it's netted by symbol,
    with no orderId to join against a local order.
  - pnl.realized is NEVER copied from the broker -- it's recomputed
    locally (via the SAME OrderManager._finalize_realized_pnl this app
    already uses everywhere else) from the corrected fill data, since a
    broker P&L figure is almost certainly net-of-brokerage/STT/GST while
    this app's own figure is gross -- comparing them would disagree on
    nearly every order for reasons that have nothing to do with a real
    discrepancy.
  - daily_realized_pnl and every other safeguard counter are NEVER
    auto-corrected -- report-only. Silently rewriting a counter that
    already drove a real halt/resume decision this session, or
    double-counting it on a re-run, is a materially worse failure mode
    than the drift it would be fixing.
  - Corrections are CONVERGENT (local := broker's value), never
    incremental -- this is what makes re-running safe: a second run
    against the same broker state finds zero remaining diffs and writes
    nothing.
  - dry_run defaults to True. The first call always previews (produces
    the full report, corrects nothing); a second, explicit dry_run=False
    call actually writes. This is what makes "never a silent correction"
    real on live-money records, not just a docstring promise.

Deliberately out of scope for this module (name, don't build):
  - A client-order-id echoed to the broker (e.g. this app's own order_id
    embedded in `remarks`) for a real bidirectional join key -- would
    make orphan detection far more reliable, but changes LIVE ORDER
    PLACEMENT, not just this read/correct path. Propose separately.
  - Auto-creating a synthetic local order for a broker-side trade with no
    local match at all (e.g. options expiry auto-settlement). Flagged
    loudly as an orphan; full handling deferred.
"""
import asyncio
import logging
import uuid
from datetime import date, datetime

from . import clock, store, failure_log, factsheet
from .order_manager import TERMINAL_STATUSES

log = logging.getLogger("tradejini.broker_reconcile")

SCHEMA_VERSION = 1
PRICE_TOLERANCE = 0.05   # one standard NSE tick -- see order_manager.DEFAULT_TICK_SIZE
MAX_REPORTS_RETAINED = 50

_lock = asyncio.Lock()  # guards against two overlapping reconciliation runs (a double-click, or an
                          # automated trigger firing while a manual one is already in progress) --
                          # see run_reconciliation, which raises ReconciliationAlreadyRunning rather
                          # than letting a second run start while one is in flight

VERDICT_MATCH = "match"
VERDICT_CORRECTED = "corrected"
VERDICT_DIFFERS = "differs"
VERDICT_UNVERIFIABLE = "unverifiable"      # broker's order book is day-scoped; an order from a
                                             # previous session legitimately has no broker record anymore
VERDICT_NOT_APPLICABLE = "not_applicable"  # place-time rejection -- never reached the broker at all

# Which diff paths this module actually knows how to correct -- entry.broker_order_id/
# square_off.broker_order_id diffs (broker has no record at all) are real findings but
# there's nothing to copy FROM, so they can never move past VERDICT_DIFFERS.
_CORRECTABLE_PATHS = {"entry.status", "entry.fill_qty", "entry.avg_price",
                       "square_off.status", "pnl.exit_avg_price"}


class ReconciliationAlreadyRunning(Exception):
    pass


def _now_iso() -> str:
    return clock.now_iso()


def _diff(path: str, local, broker) -> dict:
    return {"path": path, "local": local, "broker": broker}


def _prices_differ(a, b) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(a - b) > PRICE_TOLERANCE


def compare_order(local: dict, broker_orders_by_id: dict, broker_trades: list[dict]) -> tuple[str, list[dict]]:
    """PURE -- no I/O, no network. Compares one local TERMINAL order
    against the broker's own get_orders() records, keyed by orderId.

    Deliberately takes the FULL broker_orders_by_id map, not a single
    resolved `broker_order` -- a local order can have TWO separate
    broker-side legs (the entry order and, once closed, the square-off/
    exit order), each with its own broker_order_id. Comparing only the
    entry leg would silently never check the close side at all, which is
    exactly where a wrong exit price would actually show up.

    broker_trades is accepted for future enrichment but not used in the
    comparison itself -- see this module's docstring on why get_orders()
    is the comparison spine.

    Returns (verdict, diffs). diffs: [{"path", "local", "broker"}, ...]."""
    entry_broker_id = (local.get("entry") or {}).get("broker_order_id")
    if entry_broker_id is None:
        return VERDICT_NOT_APPLICABLE, []  # never reached the broker (place-time rejection)

    diffs = []
    entry_bo = broker_orders_by_id.get(entry_broker_id)
    if entry_bo is None:
        diffs.append(_diff("entry.broker_order_id", entry_broker_id, None))
    else:
        local_status = local["entry"].get("status")
        broker_status = entry_bo.get("status")
        if local_status != broker_status:
            diffs.append(_diff("entry.status", local_status, broker_status))
        local_fill_qty = local["entry"].get("fill_qty") or 0
        broker_fill_qty = entry_bo.get("fillQty") or 0
        if local_fill_qty != broker_fill_qty:
            diffs.append(_diff("entry.fill_qty", local_fill_qty, broker_fill_qty))
        if _prices_differ(local["entry"].get("avg_price"), entry_bo.get("avgPrice")):
            diffs.append(_diff("entry.avg_price", local["entry"].get("avg_price"), entry_bo.get("avgPrice")))

    square_off = local.get("square_off") or {}
    sq_broker_id = square_off.get("broker_order_id")
    if sq_broker_id:
        sq_bo = broker_orders_by_id.get(sq_broker_id)
        if sq_bo is None:
            diffs.append(_diff("square_off.broker_order_id", sq_broker_id, None))
        else:
            local_sq_status = square_off.get("status")
            broker_sq_status = sq_bo.get("status")
            if local_sq_status != broker_sq_status:
                diffs.append(_diff("square_off.status", local_sq_status, broker_sq_status))
            local_exit = (local.get("pnl") or {}).get("exit_avg_price")
            if _prices_differ(local_exit, sq_bo.get("avgPrice")):
                diffs.append(_diff("pnl.exit_avg_price", local_exit, sq_bo.get("avgPrice")))

    return (VERDICT_MATCH if not diffs else VERDICT_DIFFERS), diffs


def find_orphans(known_broker_order_ids: set, broker_orders: list[dict], broker_trades: list[dict]) -> list[dict]:
    """PURE. A broker-side order or trade with NO matching local
    broker_order_id at all -- e.g. options expiry auto-settlement, or an
    order placed by hand directly in the broker's own terminal (expected
    and not actually a problem in that case). Flagged for visibility;
    auto-creating a synthetic local record for these is explicitly out of
    scope (see module docstring).

    Every field access here is defensive (.get(), multiple key-name
    fallbacks) -- get_trades()'s actual response shape has no confirmed
    documentation or production evidence in this codebase, unlike
    get_orders()'s, so this must never assume a specific shape and crash
    the whole reconciliation run over a malformed/unexpected entry."""
    orphans = []
    for bo in broker_orders or []:
        oid = bo.get("orderId")
        if oid and oid not in known_broker_order_ids:
            orphans.append({"source": "get_orders", "broker_order_id": oid,
                             "status": bo.get("status"), "sym_id": bo.get("symId")})
    for t in broker_trades or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("orderId") or t.get("tradeId") or t.get("id")
        if tid and tid not in known_broker_order_ids:
            orphans.append({"source": "get_trades", "broker_order_id": tid, "sym_id": t.get("symId")})
    return orphans


def find_stale_open_orders(local_open_orders: list[dict], today: date) -> list[dict]:
    """PURE -- report-only flag for a LIVE order still in a NON-terminal
    status locally whose instrument's expiry date has already passed.
    Aimed at the one class of drift get_orders()/get_trades() may not
    reliably surface at all: NSE index-options expiry auto-settlement/
    exercise, which can leave this app still watching a position the
    exchange has already settled. Never corrects anything -- there's no
    safe automatic action here, only a loud flag for a human to check.

    Deliberately parses the option id string directly (same encoding
    script_master._parse_option_id already decodes) rather than taking a
    live ScriptMaster instance as a parameter -- keeps this function
    genuinely pure/stateless rather than depending on another module's
    mutable, separately-refreshed cache."""
    stale = []
    for order in local_open_orders:
        sym_id = order.get("sym_id") or ""
        parts = sym_id.split("_")
        if len(parts) < 6 or not parts[0].startswith("OPT"):
            continue  # not an option symbol (or unparseable) -- nothing to check
        try:
            expiry = datetime.strptime(parts[-3], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue
        if expiry < today:
            stale.append({"order_id": order.get("order_id"), "sym_id": sym_id,
                           "expiry": str(expiry), "status": order.get("status")})
    return stale


def _order_date(order: dict) -> date | None:
    created = order.get("created_at")
    if not created:
        return None
    try:
        return clock.parse_iso(created).date()
    except ValueError:
        return None


def _apply_corrections(order: dict, diffs: list[dict]) -> None:
    """Convergent assignment only -- local := broker's value. Mutates the
    order dict in place; the caller is responsible for persisting it."""
    for d in diffs:
        path, value = d["path"], d["broker"]
        if path == "entry.status":
            order["entry"]["status"] = value
        elif path == "entry.fill_qty":
            order["entry"]["fill_qty"] = value
        elif path == "entry.avg_price":
            order["entry"]["avg_price"] = value
        elif path == "square_off.status":
            order["square_off"]["status"] = value
        elif path == "pnl.exit_avg_price":
            order["pnl"]["exit_avg_price"] = value


def _amend_factsheets(order: dict, *, run_id: str, broker_id: str, changes: list[dict], reason: str) -> None:
    """Appends the SAME correction to the order's own factsheet, and --
    for a Program leg -- to the owning cycle's factsheet too, addressing
    this specific leg within the cycle's `legs` list by position (matched
    by order_id, since that's the only stable per-leg identifier available
    at the factsheet-amendment layer). One correction, two records kept in
    sync, sharing one run_id so both sides are traceable together."""
    order_changes = [{"path": f"order.{d['path']}", "from": d["local"], "to": d["broker"]} for d in changes]
    factsheet.append_amendment(store.factsheet_order_path(order["order_id"]), run_id=run_id,
                                source="broker_reconciliation", broker_id=broker_id,
                                reason=reason, changes=order_changes)

    program_id, cycle_id = order.get("program_id"), order.get("cycle_id")
    if not (program_id and cycle_id):
        return
    cycle_path = store.factsheet_cycle_path(program_id, cycle_id)
    cycle_fs = store.load_factsheet(cycle_path)
    if not cycle_fs:
        return
    leg_index = next((i for i, leg in enumerate(cycle_fs.get("legs", []))
                       if leg.get("order_id") == order["order_id"]), None)
    if leg_index is None:
        return
    leg_changes = [{"path": f"legs.{leg_index}.{d['path']}", "from": d["local"], "to": d["broker"]} for d in changes]
    factsheet.append_amendment(cycle_path, run_id=run_id, source="broker_reconciliation", broker_id=broker_id,
                                reason=f"{reason} (leg {order.get('program_leg')})", changes=leg_changes)


async def _reconcile_one_broker(*, broker_id: str, client, order_manager, dry_run: bool,
                                 run_id: str, today: date) -> dict:
    counts = {"checked": 0, "match": 0, "corrected": 0, "differs": 0, "unverifiable": 0, "not_applicable": 0}
    findings = []

    all_orders = order_manager.list_orders() + order_manager.list_archived_orders()
    live_terminal = [o for o in all_orders if o.get("owner") == "live" and o.get("broker_id") == broker_id
                      and o.get("status") in TERMINAL_STATUSES]
    live_open = [o for o in order_manager.list_orders()
                 if o.get("owner") == "live" and o.get("broker_id") == broker_id
                 and o.get("status") not in TERMINAL_STATUSES]

    try:
        broker_orders = await client.get_orders()
    except Exception as e:
        log.warning("Reconciliation: get_orders() failed for broker %s: %s", broker_id, e)
        return {"error": f"get_orders() failed: {e}", "checked": 0, "counts": counts,
                "findings": [], "orphans": [], "stale_open_orders": []}

    broker_orders_by_id = {bo.get("orderId"): bo for bo in broker_orders if bo.get("orderId")}

    broker_trades = []
    if hasattr(client, "get_trades"):
        try:
            broker_trades = await client.get_trades()
        except Exception as e:
            log.warning("Reconciliation: get_trades() failed for broker %s (enrichment only, "
                        "continuing without it): %s", broker_id, e)

    known_broker_order_ids = set()
    for order in live_terminal:
        counts["checked"] += 1
        eid = (order.get("entry") or {}).get("broker_order_id")
        if eid:
            known_broker_order_ids.add(eid)
        sqid = (order.get("square_off") or {}).get("broker_order_id")
        if sqid:
            known_broker_order_ids.add(sqid)

        order_date = _order_date(order)
        if order_date != today:
            counts["unverifiable"] += 1
            findings.append({"order_id": order["order_id"], "verdict": VERDICT_UNVERIFIABLE, "diffs": [],
                              "note": "order predates today's broker-side order book"})
            continue

        verdict, diffs = compare_order(order, broker_orders_by_id, broker_trades)

        if verdict == VERDICT_NOT_APPLICABLE:
            counts["not_applicable"] += 1
            findings.append({"order_id": order["order_id"], "verdict": verdict, "diffs": [],
                              "note": "never reached the broker (place-time rejection)"})
            continue
        if verdict == VERDICT_MATCH:
            counts["match"] += 1
            findings.append({"order_id": order["order_id"], "verdict": verdict, "diffs": []})
            continue

        # VERDICT_DIFFERS -- separate what's actually correctable from what isn't (a missing
        # broker record has nothing to copy FROM, so it can never become "corrected")
        correctable = [d for d in diffs if d["path"] in _CORRECTABLE_PATHS]
        uncorrectable = [d for d in diffs if d["path"] not in _CORRECTABLE_PATHS]

        if not dry_run and correctable:
            async with order_manager.lock():
                _apply_corrections(order, correctable)
                # recompute P&L deterministically from the corrected inputs via the SAME function
                # every other close in this app uses -- never copy pnl.realized from the broker
                order_manager._finalize_realized_pnl(order, order["pnl"].get("exit_avg_price"))
                change_summary = "; ".join(f"{d['path']}: {d['local']!r} -> {d['broker']!r}" for d in correctable)
                order.setdefault("logs", []).append({
                    "ts": _now_iso(),
                    "msg": f"Corrected by broker reconciliation (run {run_id}): {change_summary}",
                })
                order["logs"] = order["logs"][-200:]
                store.save_order_in_place(order)
            failure_log.log_failure(category="reconcile_discrepancy", order_id=order["order_id"],
                                     program_id=order.get("program_id"),
                                     message=f"Corrected {len(correctable)} field(s) via broker reconciliation: {change_summary}")
            _amend_factsheets(order, run_id=run_id, broker_id=broker_id, changes=correctable,
                              reason="Broker reconciliation correction")

        final_verdict = VERDICT_CORRECTED if (not dry_run and correctable and not uncorrectable) else VERDICT_DIFFERS
        counts["corrected" if final_verdict == VERDICT_CORRECTED else "differs"] += 1
        findings.append({"order_id": order["order_id"], "verdict": final_verdict, "diffs": diffs})

    stale = find_stale_open_orders(live_open, today)
    orphans = find_orphans(known_broker_order_ids, broker_orders, broker_trades)

    return {"checked": counts["checked"], "counts": counts, "findings": findings,
            "orphans": orphans, "stale_open_orders": stale}


async def run_reconciliation(*, order_manager, brokers: dict, dry_run: bool = True,
                              today: date | None = None) -> dict:
    """Top-level entry point. `brokers` maps broker_id -> BrokerClient
    (today: always exactly {"tradejini": client} -- the loop underneath is
    already broker-keyed for whenever a second broker exists). Raises
    ReconciliationAlreadyRunning if a run is already in progress -- the
    caller (main.py's route handler) turns that into an HTTP 409 rather
    than letting two runs interleave."""
    if _lock.locked():
        raise ReconciliationAlreadyRunning()
    async with _lock:
        run_id = uuid.uuid4().hex[:12]
        started_at = _now_iso()
        today = today or clock.today()
        broker_reports = {}
        for broker_id, client in brokers.items():
            broker_reports[broker_id] = await _reconcile_one_broker(
                broker_id=broker_id, client=client, order_manager=order_manager,
                dry_run=dry_run, run_id=run_id, today=today,
            )
        report = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "dry_run": dry_run,
            "brokers": broker_reports,
        }
        store.save_reconcile_report(report)
        return report

