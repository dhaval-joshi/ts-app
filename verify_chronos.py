import asyncio
import os
import sqlite3
import json

from backend.chronos import ChronosEngine
from backend.tradejini_client import TradejiniClient
from backend.script_master import ScriptMaster
from backend.config import DATA_DIR
import backend.store as store

async def test():
    print("Initializing DB...")
    conn = sqlite3.connect(DATA_DIR / "store.db")
    conn.row_factory = sqlite3.Row
    program_id = "test_prog_1"
    program = {
        "program_id": program_id,
        "name": "Test Strategy",
        "index_id": "IDX_NIFTY_NSE",
        "sizing_mode": "capital",
        "execution_mode": "manual_config",
        "entry_signals": {"enabled": True},
        "stop": {"trig_offset": 10.0}
    }
    
    # We must patch load_program because ChronosEngine calls it!
    import backend.store as store
    store.load_program = lambda pid: {"config": program}
    print(f"Testing backtest for program_id: {program_id}")
    
    class FakeClient:
        async def get_historical_data(self, sym_id, start_ts, end_ts, interval):
            return {"status": "ok", "chartData": []}
            
        async def get_interval_chart_data(self, sym_id, interval, start_ts, end_ts):
            bars = []
            for i in range(100):
                bars.append({
                    "timestamp": start_ts + i * 60, # 1 min bars
                    "open": 10000,
                    "high": 10010,
                    "low": 9990,
                    "close": 10000,
                    "volume": 1000
                })
            return {"status": "ok", "chartData": bars}
            
    client = FakeClient()
    script_master = ScriptMaster(client)
    
    # Give ScriptMaster some dummy data to avoid hitting network
    script_master._indices = {
        "IDX_NIFTY_NSE": type("IndexRow", (), {"id": "IDX_NIFTY_NSE", "avail_flag": True, "disp_name": "NIFTY"})()
    }
    
    import datetime
    today = datetime.date.today()
    exp = today + datetime.timedelta(days=2)
    script_master._options_by_und = {
        "IDX_NIFTY_NSE": [
            type("OptionRow", (), {"id": f"OPTIDX_NIFTY_NFO_{exp}_10000_CE", "expiry": exp, "strike": 10000.0, "opt_type": "CE", "lot": 25, "tick": 0.05})(),
            type("OptionRow", (), {"id": f"OPTIDX_NIFTY_NFO_{exp}_10000_PE", "expiry": exp, "strike": 10000.0, "opt_type": "PE", "lot": 25, "tick": 0.05})()
        ]
    }
    
    engine = ChronosEngine(client, script_master)
    print("Running backtest...")
    try:
        res = await engine.run_backtest(program_id, 2, 100000.0)
        print("Backtest Successful!")
        print(json.dumps(res, indent=2))
    except Exception as e:
        print("Backtest Failed with Error:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
