import os
import json
import sqlite3
from pathlib import Path
import sys

# Add parent to path to import backend
sys.path.append(str(Path(__file__).resolve().parent.parent))
from backend import config

DB_PATH = config.DATA_DIR / "store.db"

def migrate():
    print(f"Migrating JSON files to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    conn.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, archived INTEGER, data TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS strategies (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS programs (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS risk_groups (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS factsheets (id TEXT PRIMARY KEY, type TEXT, program_id TEXT, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS reconcile_reports (id TEXT PRIMARY KEY, date TEXT, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS signal_history (id TEXT PRIMARY KEY, data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS singletons (id TEXT PRIMARY KEY, data TEXT)")
    
    # 1. Orders
    active_count = 0
    if config.ORDERS_DIR.exists():
        for p in config.ORDERS_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO orders (id, archived, data, created_at) VALUES (?, ?, ?, ?)",
                                 (data["order_id"], 0, json.dumps(data), data.get("created_at", "")))
                    active_count += 1
            except Exception as e:
                print(f"Error reading {p}: {e}")
    print(f"Migrated {active_count} active orders.")

    # 2. Archived Orders
    archive_count = 0
    if config.ARCHIVE_DIR.exists():
        for p in config.ARCHIVE_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO orders (id, archived, data, created_at) VALUES (?, ?, ?, ?)",
                                 (data["order_id"], 1, json.dumps(data), data.get("created_at", "")))
                    archive_count += 1
            except Exception as e:
                print(f"Error reading {p}: {e}")
    print(f"Migrated {archive_count} archived orders.")

    # 3. Strategies
    strat_count = 0
    if config.STRATEGIES_DIR.exists():
        for p in config.STRATEGIES_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    strat_id = data.get("strategy_id", p.stem)
                    data["strategy_id"] = strat_id
                    conn.execute("INSERT OR REPLACE INTO strategies (id, name, data) VALUES (?, ?, ?)",
                                 (strat_id, data.get("name", ""), json.dumps(data)))
                    strat_count += 1
            except Exception as e:
                print(f"Error reading {p}: {e}")
    print(f"Migrated {strat_count} strategies.")

    # 4. Programs
    prog_count = 0
    if config.PROGRAMS_DIR.exists():
        for p in config.PROGRAMS_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    prog_id = data.get("config", {}).get("program_id", p.stem)
                    conn.execute("INSERT OR REPLACE INTO programs (id, name, data) VALUES (?, ?, ?)",
                                 (prog_id, data.get("config", {}).get("name", ""), json.dumps(data)))
                    prog_count += 1
            except Exception as e:
                print(f"Error reading {p}: {e}")
    print(f"Migrated {prog_count} programs.")

    # 5. Risk Groups
    rg_count = 0
    if config.RISK_GROUPS_DIR.exists():
        for p in config.RISK_GROUPS_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO risk_groups (id, name, data) VALUES (?, ?, ?)",
                                 (data.get("risk_group_id", p.stem), data.get("name", ""), json.dumps(data)))
                    rg_count += 1
            except Exception as e:
                pass
    print(f"Migrated {rg_count} risk groups.")

    # 6. Factsheets - Programs
    fs_prog_count = 0
    if config.FACTSHEETS_PROGRAMS_DIR.exists():
        for p in config.FACTSHEETS_PROGRAMS_DIR.glob("*/*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    fs_id = f"{p.parent.name}_{p.stem}"
                    conn.execute("INSERT OR REPLACE INTO factsheets (id, type, program_id, data) VALUES (?, ?, ?, ?)",
                                 (fs_id, "program", p.parent.name, json.dumps(data)))
                    fs_prog_count += 1
            except Exception as e:
                pass
    
    # 7. Factsheets - Orders
    fs_ord_count = 0
    if config.FACTSHEETS_ORDERS_DIR.exists():
        for p in config.FACTSHEETS_ORDERS_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO factsheets (id, type, program_id, data) VALUES (?, ?, ?, ?)",
                                 (p.stem, "order", "", json.dumps(data)))
                    fs_ord_count += 1
            except Exception as e:
                pass
    print(f"Migrated {fs_prog_count} program factsheets and {fs_ord_count} order factsheets.")

    # 8. Reconcile Reports
    rec_count = 0
    if config.RECONCILE_REPORTS_DIR.exists():
        for p in config.RECONCILE_REPORTS_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO reconcile_reports (id, date, data) VALUES (?, ?, ?)",
                                 (data.get("run_id", p.stem), data.get("started_at", ""), json.dumps(data)))
                    rec_count += 1
            except Exception as e:
                pass
    print(f"Migrated {rec_count} reconcile reports.")

    # 9. Signal History
    sig_count = 0
    if config.SIGNAL_HISTORY_DIR.exists():
        for p in config.SIGNAL_HISTORY_DIR.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO signal_history (id, data) VALUES (?, ?)",
                                 (p.stem, json.dumps(data)))
                    sig_count += 1
            except Exception as e:
                pass
    print(f"Migrated {sig_count} signal histories.")

    # 10. Singletons
    for s_name in ["portfolio_safeguards", "app_settings", "market_holidays"]:
        p = config.DATA_DIR / f"{s_name}.json"
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                    conn.execute("INSERT OR REPLACE INTO singletons (id, data) VALUES (?, ?)",
                                 (s_name, json.dumps(data)))
            except Exception as e:
                pass

    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
