"""
Durable, OUTSIDE-the-live-record snapshots -- the answer to "I deleted a
Program I didn't like (or edited one mid-flight) and lost everything it
ever did." `program["cycles"]` and an order's own JSON file already record
history, but both live and die with the entity that owns them: deleting a
Program removes its `cycles` rollup entirely (order-manager-caution.md's
`store.py` notes this was never a design goal), and an edited Program's
`config` no longer reflects what was actually in effect for a cycle that
already ran.

A factsheet is written EXACTLY ONCE per cycle/order, at the moment its
outcome is known (an order reaches a terminal status; a Program cycle
closes -- see order_manager.py's `_on_terminal` and program_manager.py's
`_close_cycle`), and is never overwritten afterward. The one thing that
CAN change later -- a correction from broker_reconcile.py -- is recorded
as an appended `amendments` entry, never as a rewrite of the original
snapshot. This is deliberate: a trading journal or any future ML use is
only trustworthy if "what actually happened" and "what we later found out
was wrong about our own records" stay visibly separate, not silently
merged into one mutable blob.

Every function here swallows its own exceptions and logs to failure_log --
a factsheet write must NEVER be able to break the real trading operation
it's attached to, same non-blocking-side-effect philosophy as _log()/
failure_log.log_failure() everywhere else in this codebase. All actual
disk I/O stays in store.py; this module only shapes content and policy.
"""
import logging

from . import clock, store, failure_log

log = logging.getLogger("tradejini.factsheet")

FACTSHEET_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return clock.now_iso()


def write_order_factsheet(order: dict) -> str | None:
    """Called once, from order_manager.py's _on_terminal -- for EVERY
    order that reaches a terminal status, Regular OMS or Advanced OMS leg
    alike, live or paper. Exists-guarded: a factsheet is created once and
    never rewritten, so calling this again for the same order_id (which
    shouldn't happen -- an order only goes terminal once -- but costs
    nothing to guard against) is a safe no-op. Returns the order_id on
    success, None on any failure (never raises)."""
    try:
        order_id = order["order_id"]
        path = store.factsheet_order_path(order_id)
        if path.exists():
            return order_id  # already written -- creation happens exactly once
        factsheet = {
            "factsheet_schema_version": FACTSHEET_SCHEMA_VERSION,
            "factsheet_type": "order",
            "created_at": _now_iso(),
            "order_id": order_id,
            "owner": order.get("owner"),
            "broker_id": order.get("broker_id"),
            "program_id": order.get("program_id"),
            "cycle_id": order.get("cycle_id"),
            "program_leg": order.get("program_leg"),
            "strategy_name": order.get("strategy_name"),
            "strategy_snapshot": order.get("strategy_snapshot") or {},
            "order": _deep_copy(order),
            "amendments": [],
        }
        store.save_factsheet(path, factsheet)
        return order_id
    except Exception as e:
        log.exception("Failed to write order factsheet for %s", order.get("order_id"))
        failure_log.log_failure(category="factsheet_write_failed", order_id=order.get("order_id"),
                                 program_id=order.get("program_id"), message=f"order factsheet: {e}")
        return None


def write_cycle_factsheet(*, program: dict, cycle_record: dict, legs: list[dict],
                           snapshot: dict | None) -> str | None:
    """Called once, from program_manager.py's _close_cycle -- for EVERY
    cycle that closes, regardless of why (SL/target/time-exit, Stop &
    Flatten, or the manual Close Cycle action) or whether it's live or
    paper. `snapshot` is the program["active_cycle_snapshot"] captured at
    _start_new_cycle time (program config / spot / expiry / strikes /
    widen offset as they stood when the cycle STARTED, since the live
    config can be edited mid-cycle and would no longer reflect that by
    the time this runs) -- may legitimately be None for a cycle that
    somehow closed without ever going through that capture (defensive,
    shouldn't happen in practice). Exists-guarded like write_order_factsheet."""
    try:
        cfg = program["config"]
        cycle_id = cycle_record["cycle_id"]
        path = store.factsheet_cycle_path(cfg["program_id"], cycle_id)
        if path.exists():
            return cycle_id
        snapshot = snapshot or {}
        factsheet = {
            "factsheet_schema_version": FACTSHEET_SCHEMA_VERSION,
            "factsheet_type": "cycle",
            "created_at": _now_iso(),
            "program_id": cfg["program_id"],
            "program_name": cfg["name"],  # the Program may later be deleted -- the id alone is
                                            # unreadable by itself once that happens
            "cycle_id": cycle_id,
            "cycle_number": cycle_record.get("cycle_number"),
            "mode": cfg.get("mode"),
            "broker_id": cfg.get("broker_id"),
            "risk_group_id": cfg.get("risk_group_id"),
            "super_program_id": cfg.get("super_program_id"),
            "program_config_at_cycle_start": snapshot.get("program_config"),
            "cycle_selection": {
                "spot": snapshot.get("spot"),
                "expiry": snapshot.get("expiry"),
                "ce_id": snapshot.get("ce_id"), "pe_id": snapshot.get("pe_id"),
                "ce_strike": snapshot.get("ce_strike"), "pe_strike": snapshot.get("pe_strike"),
                "leg_qty": snapshot.get("leg_qty"),
                "widen_offset": snapshot.get("widen_offset"),
                "widened": bool(snapshot.get("widen_offset")),
            },
            "cycle_summary": _deep_copy(cycle_record),
            "legs": [_deep_copy(leg) for leg in legs],  # full embedded copies, deliberately duplicating
                                                          # each leg's own order factsheet -- see this
                                                          # module's docstring for why
            "amendments": [],
        }
        store.save_factsheet(path, factsheet)
        return cycle_id
    except Exception as e:
        log.exception("Failed to write cycle factsheet for program %s", program.get("config", {}).get("program_id"))
        failure_log.log_failure(category="factsheet_write_failed", order_id=None,
                                 program_id=program.get("config", {}).get("program_id"),
                                 message=f"cycle factsheet: {e}")
        return None


def append_amendment(path, *, run_id: str, source: str, broker_id: str | None,
                      reason: str, changes: list[dict]) -> bool:
    """The ONLY write path into an already-existing factsheet -- appends
    to `amendments`, never touches any other field. This is what makes
    the "original snapshot is immutable" guarantee structural rather than
    just a rule someone has to remember: there is no other function that
    can modify a factsheet once written. De-duplicates on `change_key` (a
    stable hash of the sorted (path, to) pairs) against the newest
    existing amendment, so re-running the same reconciliation finding
    twice doesn't append the same correction twice."""
    try:
        factsheet = store.load_factsheet(path)
        if factsheet is None:
            return False
        change_key = _change_key(changes)
        existing = factsheet.setdefault("amendments", [])
        if existing and existing[-1].get("change_key") == change_key:
            return True  # identical to the most recent amendment already -- nothing new to record
        existing.append({
            "amendment_id": f"{run_id}-{len(existing) + 1}",
            "run_id": run_id,
            "at": _now_iso(),
            "source": source,  # e.g. "broker_reconciliation"
            "broker_id": broker_id,
            "reason": reason,
            "changes": changes,  # [{"path": "order.entry.avg_price", "from": ..., "to": ...}, ...]
            "change_key": change_key,
        })
        store.save_factsheet(path, factsheet)
        return True
    except Exception as e:
        log.exception("Failed to append amendment to factsheet %s", path)
        failure_log.log_failure(category="factsheet_write_failed", order_id=None, message=f"amendment: {e}")
        return False


def apply_amendments(factsheet: dict) -> dict:
    """PURE -- never writes anything. Folds every amendment's `changes`
    over a deep copy of the original snapshot, in order, and returns the
    resulting "corrected view." The original `factsheet` argument (and
    the file on disk) is never touched -- this is a read-time projection,
    not a migration. Dotted `path` strings address the copy via simple
    attribute/key traversal (e.g. "order.entry.avg_price")."""
    result = _deep_copy(factsheet)
    for amendment in factsheet.get("amendments", []):
        for change in amendment.get("changes", []):
            _set_by_path(result, change["path"], change["to"])
    return result


def load_order_factsheet(order_id: str) -> dict | None:
    return store.load_factsheet(store.factsheet_order_path(order_id))


def load_cycle_factsheet(program_id: str, cycle_id: str) -> dict | None:
    return store.load_factsheet(store.factsheet_cycle_path(program_id, cycle_id))


# ------------------------------------------------------------------ journal --
#
# A basic Trading Journal reads through here rather than the raw store
# listers directly, so the (potentially large) embedded order/leg data
# doesn't need to be parsed just to render a summary list -- these return
# small, flat summary dicts; the full factsheet is only loaded on demand
# when a specific entry is opened.

def list_journal_entries(*, program_id: str | None = None, limit: int = 200) -> list[dict]:
    """Combines cycle factsheets (Advanced OMS -- one entry per cycle,
    both legs already embedded) and order factsheets that do NOT belong
    to a Program (Regular OMS -- a Program leg's own order factsheet is
    intentionally excluded here since it's already represented inside its
    cycle's entry; see this module's docstring on deliberate duplication
    for why the order factsheet still exists on its own, just not
    double-listed in the journal). Sorted newest-first by when each
    actually closed."""
    entries = []
    for p in store.list_cycle_factsheet_paths(program_id):
        fs = store.load_factsheet(p)
        if not fs:
            continue
        summary = fs.get("cycle_summary") or {}
        entries.append({
            "type": "cycle",
            "id": f"{fs['program_id']}:{fs['cycle_id']}",
            "program_id": fs.get("program_id"),
            "program_name": fs.get("program_name"),
            "cycle_number": summary.get("cycle_number"),
            "mode": fs.get("mode"),
            "broker_id": fs.get("broker_id"),
            "closed_at": summary.get("closed_at"),
            "pnl": summary.get("pnl"),
            "pnl_unknown": summary.get("pnl_unknown", False),
            "widened": (fs.get("cycle_selection") or {}).get("widened", False),
            "amended": bool(fs.get("amendments")),
        })
    if not program_id:  # Regular OMS orders never belong to a specific Program's listing
        for p in store.list_order_factsheet_paths():
            fs = store.load_factsheet(p)
            if not fs or fs.get("program_id"):
                continue  # Program legs are represented via their cycle entry above, not listed twice
            o = fs.get("order") or {}
            entries.append({
                "type": "order",
                "id": fs.get("order_id"),
                "sym_id": o.get("sym_id"),
                "strategy_name": fs.get("strategy_name"),
                "mode": fs.get("owner"),
                "broker_id": fs.get("broker_id"),
                "closed_at": o.get("updated_at"),
                "pnl": (o.get("pnl") or {}).get("realized"),
                "pnl_unknown": (o.get("pnl") or {}).get("realized") is None,
                "amended": bool(fs.get("amendments")),
            })
    entries.sort(key=lambda e: e.get("closed_at") or "", reverse=True)
    return entries[:limit]


# --------------------------------------------------------------- internals --

def _deep_copy(d: dict) -> dict:
    import copy
    return copy.deepcopy(d)


def _change_key(changes: list[dict]) -> str:
    import hashlib, json
    material = json.dumps(sorted((c["path"], c.get("to")) for c in changes), sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _set_by_path(obj, path: str, value) -> None:
    """Dotted path traversal that supports BOTH dict keys and list indices
    (e.g. "legs.0.entry.avg_price" -- a cycle factsheet's `legs` is a
    list, addressed by position, since there's no other stable per-leg
    key available at the factsheet-amendment layer). Never fabricates
    structure -- a path that doesn't resolve on this particular snapshot
    is silently a no-op rather than a crash, since a malformed/unexpected
    path here must never be allowed to break a reconciliation run."""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                return
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return
    last = parts[-1]
    if isinstance(cur, list):
        if last.isdigit() and int(last) < len(cur):
            cur[int(last)] = value
    elif isinstance(cur, dict):
        cur[last] = value
