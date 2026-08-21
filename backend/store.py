"""
Thread-safe persistence using SQLite.
Replaces the old JSON file-per-entity store.
Provides the exact same function signatures as the JSON store to remain 
a drop-in replacement.
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from . import config

DB_PATH = config.DATA_DIR / "store.db"

_local = threading.local()

def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        # check_same_thread=False allows FastAPI worker threads to use the DB
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn

# Ensure DB path exists
config.DATA_DIR.mkdir(parents=True, exist_ok=True)
_get_db().execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, archived INTEGER, data TEXT, created_at TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS strategies (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS programs (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS risk_groups (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS factsheets (id TEXT PRIMARY KEY, type TEXT, program_id TEXT, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS reconcile_reports (id TEXT PRIMARY KEY, date TEXT, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS signal_history (id TEXT PRIMARY KEY, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS singletons (id TEXT PRIMARY KEY, data TEXT)")
_get_db().execute("CREATE TABLE IF NOT EXISTS historical_bars (symbol_id TEXT, timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol_id, timestamp))")


# ---------------------------------------------------------------- orders --

def order_path(order_id: str) -> Path:
    # Deprecated: returns a dummy path for compatibility
    return config.ORDERS_DIR / f"{order_id}.json"

def save_order(order: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO orders (id, archived, data, created_at) VALUES (?, ?, ?, ?)",
                 (order["order_id"], 0, json.dumps(order, default=str), order.get("created_at", "")))

def save_order_in_place(order: dict) -> bool:
    conn = _get_db()
    order_id = order["order_id"]
    row = conn.execute("SELECT archived FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(order, default=str), order_id))
    return True

def load_order(order_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM orders WHERE id=?", (order_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_orders() -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM orders WHERE archived=0 ORDER BY created_at DESC").fetchall()
    return [json.loads(row[0]) for row in rows]

# ------------------------------------------------------------ archiving --

def archive_order_path(order_id: str) -> Path:
    return config.ARCHIVE_DIR / f"{order_id}.json"

def archive_order(order_id: str) -> bool:
    conn = _get_db()
    cursor = conn.execute("UPDATE orders SET archived=1 WHERE id=? AND archived=0", (order_id,))
    return cursor.rowcount > 0

def unarchive_order(order_id: str) -> bool:
    conn = _get_db()
    cursor = conn.execute("UPDATE orders SET archived=0 WHERE id=? AND archived=1", (order_id,))
    return cursor.rowcount > 0

def list_archived_orders() -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM orders WHERE archived=1 ORDER BY created_at DESC").fetchall()
    return [json.loads(row[0]) for row in rows]

# ------------------------------------------------------------ strategies --

def strategy_path(strategy_id: str) -> Path:
    return config.STRATEGIES_DIR / f"{strategy_id}.json"

def save_strategy(strategy_id: str, data: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO strategies (id, name, data) VALUES (?, ?, ?)",
                 (strategy_id, data.get("name", ""), json.dumps(data, default=str)))

def load_strategy_by_id(strategy_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM strategies WHERE id=?", (strategy_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_strategies() -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM strategies ORDER BY name ASC").fetchall()
    out = []
    for row in rows:
        s = json.loads(row[0])
        out.append(s)
    return out

def load_strategy_by_name(name: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM strategies WHERE name=?", (name,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def delete_strategy_by_id(strategy_id: str) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM strategies WHERE id=?", (strategy_id,))

# -------------------------------------------------------------- programs --

def program_path(program_id: str) -> Path:
    return config.PROGRAMS_DIR / f"{program_id}.json"

def save_program(program: dict) -> None:
    conn = _get_db()
    prog_id = program["config"]["program_id"]
    name = program.get("config", {}).get("name", "")
    conn.execute("INSERT OR REPLACE INTO programs (id, name, data) VALUES (?, ?, ?)",
                 (prog_id, name, json.dumps(program, default=str)))

def load_program(program_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM programs WHERE id=?", (program_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_programs() -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM programs ORDER BY name ASC").fetchall()
    return [json.loads(row[0]) for row in rows]

def delete_program(program_id: str) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM programs WHERE id=?", (program_id,))

# ------------------------------------------------------------ risk groups --

def risk_group_path(risk_group_id: str) -> Path:
    return config.RISK_GROUPS_DIR / f"{risk_group_id}.json"

def save_risk_group(risk_group: dict) -> None:
    conn = _get_db()
    rg_id = risk_group["risk_group_id"]
    name = risk_group.get("name", "")
    conn.execute("INSERT OR REPLACE INTO risk_groups (id, name, data) VALUES (?, ?, ?)",
                 (rg_id, name, json.dumps(risk_group, default=str)))

def load_risk_group(risk_group_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM risk_groups WHERE id=?", (risk_group_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_risk_groups() -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM risk_groups ORDER BY name ASC").fetchall()
    return [json.loads(row[0]) for row in rows]

def delete_risk_group(risk_group_id: str) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM risk_groups WHERE id=?", (risk_group_id,))

# ------------------------------------------------------ portfolio safeguards --

def load_portfolio_safeguards() -> dict:
    conn = _get_db()
    row = conn.execute("SELECT data FROM singletons WHERE id='portfolio_safeguards'").fetchone()
    if row:
        return json.loads(row[0])
    return {}

def save_portfolio_safeguards(data: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO singletons (id, data) VALUES ('portfolio_safeguards', ?)",
                 (json.dumps(data, default=str),))

# ---------------------------------------------------------------- app settings --

def load_app_settings() -> dict:
    conn = _get_db()
    row = conn.execute("SELECT data FROM singletons WHERE id='app_settings'").fetchone()
    if row:
        return json.loads(row[0])
    return {}

def save_app_settings(data: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO singletons (id, data) VALUES ('app_settings', ?)",
                 (json.dumps(data, default=str),))

# ------------------------------------------------------------- factsheets --

def factsheet_cycle_path(program_id: str, cycle_id: str) -> Path:
    return config.FACTSHEETS_PROGRAMS_DIR / program_id / f"{cycle_id}.json"

def factsheet_order_path(order_id: str) -> Path:
    return config.FACTSHEETS_ORDERS_DIR / f"{order_id}.json"

def save_factsheet(path: Path, data: dict) -> None:
    conn = _get_db()
    p_str = str(path)
    if "programs" in p_str or "FACTSHEETS_PROGRAMS_DIR" in p_str or path.parent.parent.name == "programs":
        fs_id = f"{path.parent.name}_{path.stem}"
        conn.execute("INSERT OR REPLACE INTO factsheets (id, type, program_id, data) VALUES (?, ?, ?, ?)",
                     (fs_id, "program", path.parent.name, json.dumps(data, default=str)))
    else:
        conn.execute("INSERT OR REPLACE INTO factsheets (id, type, program_id, data) VALUES (?, ?, ?, ?)",
                     (path.stem, "order", "", json.dumps(data, default=str)))

def load_factsheet(path: Path) -> Optional[dict]:
    conn = _get_db()
    p_str = str(path)
    if "programs" in p_str or "FACTSHEETS_PROGRAMS_DIR" in p_str or path.parent.parent.name == "programs":
        fs_id = f"{path.parent.name}_{path.stem}"
    else:
        fs_id = path.stem
    row = conn.execute("SELECT data FROM factsheets WHERE id=?", (fs_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_cycle_factsheet_paths(program_id: Optional[str] = None) -> list[Path]:
    conn = _get_db()
    if program_id:
        rows = conn.execute("SELECT id FROM factsheets WHERE type='program' AND program_id=?", (program_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM factsheets WHERE type='program'").fetchall()
    paths = []
    for row in rows:
        fs_id = row[0]
        parts = fs_id.split("_", 1)
        if len(parts) == 2:
            paths.append(factsheet_cycle_path(parts[0], parts[1]))
    return paths

def list_order_factsheet_paths() -> list[Path]:
    conn = _get_db()
    rows = conn.execute("SELECT id FROM factsheets WHERE type='order'").fetchall()
    return [factsheet_order_path(row[0]) for row in rows]

# --------------------------------------------------------- reconcile reports --

def _reconcile_report_path(run_id: str) -> Path:
    return config.RECONCILE_REPORTS_DIR / f"{run_id}.json"

def save_reconcile_report(report: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO reconcile_reports (id, date, data) VALUES (?, ?, ?)",
                 (report["run_id"], report.get("started_at", ""), json.dumps(report, default=str)))

def load_reconcile_report(run_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM reconcile_reports WHERE id=?", (run_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_reconcile_reports(limit: int = 50) -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM reconcile_reports ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(row[0]) for row in rows]

# --------------------------------------------------------- signal history --

def _signal_history_path(date_str: str) -> Path:
    return config.SIGNAL_HISTORY_DIR / f"{date_str}.json"

def save_signal_snapshot(date_str: str, data: dict) -> None:
    conn = _get_db()
    conn.execute("INSERT OR REPLACE INTO signal_history (id, data) VALUES (?, ?)",
                 (date_str, json.dumps(data, default=str)))

def load_signal_snapshot(date_str: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT data FROM signal_history WHERE id=?", (date_str,)).fetchone()
    if row:
        return json.loads(row[0])
    return None

def list_recent_signal_snapshots(days: int = 20) -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT data FROM signal_history ORDER BY id DESC LIMIT ?", (days,)).fetchall()
    return [json.loads(row[0]) for row in rows]

# --------------------------------------------------------- historical bars --

def save_historical_bars(symbol_id: str, bars: list[dict]) -> None:
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("BEGIN TRANSACTION")
    try:
        for bar in bars:
            cursor.execute(
                "INSERT OR REPLACE INTO historical_bars (symbol_id, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol_id, bar["timestamp"], bar["open"], bar["high"], bar["low"], bar["close"], bar.get("volume", 0.0))
            )
        cursor.execute("COMMIT")
    except Exception:
        cursor.execute("ROLLBACK")
        raise

def load_historical_bars(symbol_id: str) -> list[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT timestamp, open, high, low, close, volume FROM historical_bars WHERE symbol_id=? ORDER BY timestamp ASC", (symbol_id,)).fetchall()
    return [{"timestamp": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]

def get_latest_historical_bar_timestamp(symbol_id: str) -> int:
    conn = _get_db()
    row = conn.execute("SELECT MAX(timestamp) FROM historical_bars WHERE symbol_id=?", (symbol_id,)).fetchone()
    return row[0] if row and row[0] is not None else 0
