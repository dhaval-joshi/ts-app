"""
Dead-simple persistence: one JSON file per order, one JSON file per saved
strategy template. Writes are atomic (write to temp file, then rename) so a
crash mid-write can never leave a half-written, corrupt order file behind --
this is what makes crash recovery possible.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from . import config


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------- orders --

def order_path(order_id: str) -> Path:
    return config.ORDERS_DIR / f"{order_id}.json"


def save_order(order: dict) -> None:
    _atomic_write(order_path(order["order_id"]), order)


def save_order_in_place(order: dict) -> bool:
    """Like save_order, but writes to WHICHEVER of the active or archive
    folder the order's file currently lives in, instead of always the
    active one. save_order alone would silently resurrect a duplicate:
    correcting an already-archived order with plain save_order() writes a
    SECOND copy into the active folder while the archived one stays put,
    and on next boot list_orders()/list_archived_orders() both return it
    -- exactly the duplicate-orders bug class AGENTS.md documents. Used
    by broker_reconcile.py, which operates on already-terminal orders
    that may well be archived by the time a reconciliation pass reaches
    them. Returns False if the order isn't found in either location."""
    order_id = order["order_id"]
    if archive_order_path(order_id).exists():
        _atomic_write(archive_order_path(order_id), order)
        return True
    if order_path(order_id).exists():
        _atomic_write(order_path(order_id), order)
        return True
    return False


def load_order(order_id: str) -> Optional[dict]:
    p = order_path(order_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_orders() -> list[dict]:
    orders = []
    for p in sorted(config.ORDERS_DIR.glob("*.json")):
        try:
            with open(p) as f:
                orders.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            # skip a corrupt/partial file rather than crash the whole app
            continue
    orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orders


# ------------------------------------------------------------ archiving --
# Archived orders live in a subfolder of ORDERS_DIR (data/orders/archive/),
# so list_orders()'s non-recursive glob above naturally excludes them --
# no separate filtering needed to keep the active dashboard clean.

def archive_order_path(order_id: str) -> Path:
    return config.ARCHIVE_DIR / f"{order_id}.json"


def archive_order(order_id: str) -> bool:
    """Moves an order's JSON file from the active orders folder into the
    archive subfolder. Returns False if the order file doesn't exist."""
    src = order_path(order_id)
    if not src.exists():
        return False
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(src, archive_order_path(order_id))
    return True


def unarchive_order(order_id: str) -> bool:
    """Reverses archive_order -- moves the file back to the active folder."""
    src = archive_order_path(order_id)
    if not src.exists():
        return False
    os.replace(src, order_path(order_id))
    return True


def list_archived_orders() -> list[dict]:
    orders = []
    for p in sorted(config.ARCHIVE_DIR.glob("*.json")):
        try:
            with open(p) as f:
                orders.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orders


# ------------------------------------------------------------ strategies --
#
# Filenames are the strategy's stable generated `strategy_id` -- same
# pattern as order_path() -- NOT the display name. This means renaming a
# strategy (editing its `name` field and saving) never moves or recreates
# the file; it's a real in-place rename. Everywhere OUTSIDE of file storage
# (order placement, the API routes, the UI) strategies are still addressed
# by name, since that's what's actually meaningful to look up by.

def strategy_path(strategy_id: str) -> Path:
    return config.STRATEGIES_DIR / f"{strategy_id}.json"


def save_strategy(strategy_id: str, data: dict) -> None:
    _atomic_write(strategy_path(strategy_id), data)


def load_strategy_by_id(strategy_id: str) -> Optional[dict]:
    p = strategy_path(strategy_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_strategies() -> list[dict]:
    out = []
    for p in sorted(config.STRATEGIES_DIR.glob("*.json")):
        try:
            with open(p) as f:
                s = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not s.get("strategy_id"):
            # a strategy file saved before strategy_id existed -- the
            # filename itself (the old sanitized-name-based one) is already
            # a perfectly good stable id, so reuse it rather than minting a
            # new one. Without this, editing a legacy strategy for the
            # first time would generate a fresh id, save under a NEW
            # filename, and leave this file behind as an orphaned stray --
            # exactly the bug this whole feature exists to prevent.
            s["strategy_id"] = p.stem
        out.append(s)
    out.sort(key=lambda s: (s.get("name") or "").lower())
    return out


def load_strategy_by_name(name: str) -> Optional[dict]:
    """Names are expected to be unique in practice; if two strategies
    somehow share a name, the first (alphabetically, per list_strategies)
    match wins."""
    for s in list_strategies():
        if s.get("name") == name:
            return s
    return None


def delete_strategy_by_id(strategy_id: str) -> None:
    p = strategy_path(strategy_id)
    if p.exists():
        p.unlink()


# -------------------------------------------------------------- programs --
#
# Same filename-is-a-stable-id pattern as strategies above. Each file holds
# BOTH the static ProgramConfig (models.py) and its runtime state
# (program_safeguards.ProgramRuntimeState) merged into one dict, under
# "config" and "runtime" keys respectively -- see program_manager.py.

def program_path(program_id: str) -> Path:
    return config.PROGRAMS_DIR / f"{program_id}.json"


def save_program(program: dict) -> None:
    _atomic_write(program_path(program["config"]["program_id"]), program)


def load_program(program_id: str) -> Optional[dict]:
    p = program_path(program_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_programs() -> list[dict]:
    out = []
    for p in sorted(config.PROGRAMS_DIR.glob("*.json")):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda pr: (pr.get("config", {}).get("name") or "").lower())
    return out


def delete_program(program_id: str) -> None:
    p = program_path(program_id)
    if p.exists():
        p.unlink()


# ------------------------------------------------------------ risk groups --
#
# Same filename-is-a-stable-id pattern as strategies/programs above.

def risk_group_path(risk_group_id: str) -> Path:
    return config.RISK_GROUPS_DIR / f"{risk_group_id}.json"


def save_risk_group(risk_group: dict) -> None:
    _atomic_write(risk_group_path(risk_group["risk_group_id"]), risk_group)


def load_risk_group(risk_group_id: str) -> Optional[dict]:
    p = risk_group_path(risk_group_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_risk_groups() -> list[dict]:
    out = []
    for p in sorted(config.RISK_GROUPS_DIR.glob("*.json")):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda g: (g.get("name") or "").lower())
    return out


def delete_risk_group(risk_group_id: str) -> None:
    p = risk_group_path(risk_group_id)
    if p.exists():
        p.unlink()


# ------------------------------------------------------ portfolio safeguards --
#
# A singleton, not per-program -- one file, always the same path.

def load_portfolio_safeguards() -> dict:
    p = config.DATA_DIR / "portfolio_safeguards.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save_portfolio_safeguards(data: dict) -> None:
    _atomic_write(config.DATA_DIR / "portfolio_safeguards.json", data)


# ---------------------------------------------------------------- app settings --
#
# A singleton, same pattern as portfolio_safeguards.json above. Small and
# general on purpose -- currently just the Advanced OMS accent color, but
# meant to be the one place any future simple app-level preference lives,
# rather than spinning up a new single-purpose file per setting.

def load_app_settings() -> dict:
    p = config.DATA_DIR / "app_settings.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save_app_settings(data: dict) -> None:
    _atomic_write(config.DATA_DIR / "app_settings.json", data)


# ------------------------------------------------------------- factsheets --
#
# Durable, outside-the-live-record snapshots -- see factsheet.py for the
# actual content/amendment logic; this is just the disk I/O, same
# separation of concerns as every other entity in this file (all disk
# access stays in store.py, factsheet.py never touches a Path directly).

def factsheet_cycle_path(program_id: str, cycle_id: str) -> Path:
    return config.FACTSHEETS_PROGRAMS_DIR / program_id / f"{cycle_id}.json"


def factsheet_order_path(order_id: str) -> Path:
    return config.FACTSHEETS_ORDERS_DIR / f"{order_id}.json"


def save_factsheet(path: Path, data: dict) -> None:
    _atomic_write(path, data)


def load_factsheet(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def list_cycle_factsheet_paths(program_id: Optional[str] = None) -> list[Path]:
    """Lists factsheet file PATHS only (never parses every file -- the
    caller decides which ones, if any, are actually worth loading)."""
    if program_id:
        program_dir = config.FACTSHEETS_PROGRAMS_DIR / program_id
        return sorted(program_dir.glob("*.json")) if program_dir.exists() else []
    return sorted(config.FACTSHEETS_PROGRAMS_DIR.glob("*/*.json"))


def list_order_factsheet_paths() -> list[Path]:
    """Same "paths only" contract as list_cycle_factsheet_paths -- used by
    factsheet.list_journal_entries() for the Regular OMS side of the
    journal."""
    return sorted(config.FACTSHEETS_ORDERS_DIR.glob("*.json"))


# --------------------------------------------------------- reconcile reports --

def _reconcile_report_path(run_id: str) -> Path:
    return config.RECONCILE_REPORTS_DIR / f"{run_id}.json"


def save_reconcile_report(report: dict) -> None:
    _atomic_write(_reconcile_report_path(report["run_id"]), report)


def load_reconcile_report(run_id: str) -> Optional[dict]:
    p = _reconcile_report_path(run_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_reconcile_reports(limit: int = 50) -> list[dict]:
    reports = []
    for p in config.RECONCILE_REPORTS_DIR.glob("*.json"):
        try:
            with open(p) as f:
                reports.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    reports.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return reports[:limit]


# --------------------------------------------------------- signal history --
# One small file per trading day -- see config.SIGNAL_HISTORY_DIR's own
# comment for why this is deliberately NOT a historical-data pipeline.

def _signal_history_path(date_str: str) -> Path:
    return config.SIGNAL_HISTORY_DIR / f"{date_str}.json"


def save_signal_snapshot(date_str: str, data: dict) -> None:
    _atomic_write(_signal_history_path(date_str), data)


def load_signal_snapshot(date_str: str) -> Optional[dict]:
    p = _signal_history_path(date_str)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_recent_signal_snapshots(days: int = 20) -> list[dict]:
    """Most recent snapshots first, newest-`days`-worth -- entry_signals'
    percentile gate reads this to build its own recent-history window."""
    snapshots = []
    for p in config.SIGNAL_HISTORY_DIR.glob("*.json"):
        try:
            with open(p) as f:
                snapshots.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    snapshots.sort(key=lambda s: s.get("date", ""), reverse=True)
    return snapshots[:days]

# ------------------------------------------------ reconcile reports --

def _reconcile_report_path(run_id: str) -> Path:
    return config.RECONCILE_REPORTS_DIR / f"{run_id}.json"

def save_reconcile_report(report: dict) -> None:
    _atomic_write(_reconcile_report_path(report["run_id"]), report)

def load_reconcile_report(run_id: str) -> Optional[dict]:
    p = _reconcile_report_path(run_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)

def list_reconcile_reports(limit: int = 50) -> list[dict]:
    reports = []
    for p in config.RECONCILE_REPORTS_DIR.glob("*.json"):
        try:
            with open(p) as f:
                reports.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    reports.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return reports[:limit]
