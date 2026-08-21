import asyncio
import sqlite3
import logging
from datetime import datetime, date, time
from typing import Optional

from . import config
from .script_master import ScriptMaster
from .tradejini_client import TradejiniClient

log = logging.getLogger("tradejini.data_archiver")

DB_PATH = config.DATA_DIR / "historical.db"

def _get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS ohlcv (id TEXT, symbol TEXT, timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER, PRIMARY KEY (id))")
    conn.execute("CREATE TABLE IF NOT EXISTS option_chain (id TEXT, date TEXT, underlying TEXT, expiry TEXT, strike REAL, opt_type TEXT, PRIMARY KEY (id))")
    return conn

async def archive_eod_data(client: TradejiniClient, script_master: ScriptMaster):
    """
    Downloads the day's Option Chain for active indices (NIFTY, BANKNIFTY) 
    and saves OHLCV to SQLite.
    """
    log.info("Starting EOD data archive...")
    conn = _get_db()
    today_str = date.today().isoformat()
    
    # 1. Archive Option Chain
    await script_master.refresh()
    count = 0
    for sym_id, row in script_master._options_by_und.items():
        # Just loop through lists of OptionRow inside the dict
        # wait, script_master._options_by_und is dict[str, list[OptionRow]]
        for opt in row:
            if opt.underlying in ("NIFTY", "BANKNIFTY"):
                row_id = f"{opt.id}_{today_str}"
                conn.execute(
                    "INSERT OR REPLACE INTO option_chain (id, date, underlying, expiry, strike, opt_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (row_id, today_str, opt.underlying, opt.expiry.isoformat(), opt.strike, opt.opt_type)
                )
                count += 1
    log.info(f"Archived {count} option chain entries for today.")

    # 2. Archive OHLCV for indices
    # We use timestamps for today's market hours (09:15 to 15:30 IST)
    today = date.today()
    start_dt = datetime.combine(today, time(9, 15))
    end_dt = datetime.combine(today, time(15, 30))
    from_ts = int(start_dt.timestamp())
    to_ts = int(end_dt.timestamp())

    for idx_name in ["NIFTY", "BANKNIFTY"]:
        try:
            # First find the symbol ID from script master
            idx_sym = None
            for idx in script_master._indices.values():
                if idx.asset == idx_name:
                    idx_sym = idx.id
                    break
            
            if not idx_sym:
                continue

            chart_data = await client.get_interval_chart_data(idx_sym, "1", from_ts, to_ts)
            c = 0
            for bar in chart_data:
                bar_id = f"{idx_sym}_{bar[0]}"
                conn.execute(
                    "INSERT OR REPLACE INTO ohlcv (id, symbol, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (bar_id, idx_sym, bar[0], bar[1], bar[2], bar[3], bar[4], bar[5])
                )
                c += 1
            log.info(f"Archived {c} OHLCV bars for {idx_name}.")
        except Exception as e:
            log.error(f"Failed to fetch OHLCV for {idx_name}: {e}")

    log.info("EOD data archive completed.")
    conn.close()

if __name__ == "__main__":
    # Test script standalone
    async def _test():
        c = TradejiniClient()
        sm = ScriptMaster(c)
        await archive_eod_data(c, sm)
        await c.close()
    asyncio.run(_test())
