import asyncio
import copy
import logging
import os
import sys
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# Load env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.tradejini_client import TradejiniClient
from backend.script_master import ScriptMaster
from backend.chronos import ChronosEngine
from backend.models import SentinelGroupConfig
import backend.store as db

async def run():
    client = TradejiniClient()
    script_master = ScriptMaster(client)
    if not script_master.get_index("IDX_NIFTY_NSE"):
        await script_master.refresh(client)
        
    engine = ChronosEngine(client, script_master)
    
    from backend.program_manager import ProgramManager
    pm = ProgramManager(None, None, None)
    await pm.load_from_disk()
    groups = pm.list_sentinel_groups()
    if not groups:
        print("No sentinel groups found!")
        return
        
    group_cfg = next((g for g in groups if g["index_id"] == "IDX_-1_NSE"), None)
    if not group_cfg:
        print("No group with IDX_-1_NSE found!")
        return
    print(f"Running backtest for group: {group_cfg}")
    
    res = await engine.run_backtest(
        group_cfg['sentinel_group_id'],
        20,
        500000.0,
        True,
        None
    )
    
    print("BACKTEST RESULT KEYS:", res.keys())
    if "error" in res:
        print("ERROR:", res["error"])
    print(f"Trades Taken: {len(res.get('trades', []))}")
    print(f"Net PnL: {res.get('net_pnl', 0)}")
    print(f"Final Capital: {res.get('final_capital', 0)}")
    
    # Save the CSV to scratch
    import csv
    with open("scratch/backtest_trades.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["EntryTime", "ExitTime", "Type", "Regime", "SignalLeg", "CE_Entry", "PE_Entry", "CE_Exit", "PE_Exit", "Qty", "Reason", "PnL"])
        for t in res.get("trades", []):
            entry_dt = datetime.fromtimestamp(t.get('entry_ts', 0)).strftime('%d %b %H:%M') if 'entry_ts' in t else ''
            exit_dt = datetime.fromtimestamp(t.get('exit_ts', 0)).strftime('%d %b %H:%M') if 'exit_ts' in t else ''
            writer.writerow([
                entry_dt,
                exit_dt,
                t.get('trade_type', ''),
                t.get('config', {}).get('target_regime', ''),
                t.get('signal_leg', 'Straddle'),
                t.get('ce_entry', ''),
                t.get('pe_entry', ''),
                t.get('ce_exit', ''),
                t.get('pe_exit', ''),
                t.get('qty', ''),
                t.get('close_reason', ''),
                t.get('pnl', '')
            ])
            
    with open("scratch/backtest_events.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Message", "PnL"])
        for e in res.get("events", []):
            writer.writerow([
                e.get('datetime'),
                e.get('message'),
                e.get('pnl', '')
            ])

if __name__ == "__main__":
    asyncio.run(run())
