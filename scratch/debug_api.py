import asyncio
import httpx
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend.auth import create_session
from backend import config

async def run():
    token = create_session()
    cookies = {config.SESSION_COOKIE_NAME: token}
    
    async with httpx.AsyncClient(timeout=300.0, cookies=cookies) as client:
        # Get groups
        resp = await client.get("http://localhost:8000/api/programs")
        data = resp.json()
        groups = data.get("groups", [])
        if not groups:
            print("No groups found.")
            return
            
        group = groups[0]
        group_id = group["sentinel_group_id"]
        print(f"Running backtest for group {group['name']} ({group_id})")
        
        # Run backtest
        resp = await client.post(f"http://localhost:8000/api/backtest/run/{group_id}")
        if resp.status_code != 200:
            print(f"Failed to run backtest: {resp.text}")
            return
            
        res = resp.json()
        print(f"Trades Taken: {res['total_trades']}")
        print(f"Net PnL: {res['net_pnl']}")
        print(f"Final Capital: {res['final_capital']}")
        
        import csv
        with open("scratch/backtest_trades.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["EntryTime", "Type", "Regime", "Entry", "Exit", "Qty", "PnL"])
            for t in res.get("trades", []):
                writer.writerow([
                    t.get('entry_dt'),
                    t.get('trade_type'),
                    t.get('regime'),
                    t.get('entry_price'),
                    t.get('exit_price'),
                    t.get('qty'),
                    t.get('pnl')
                ])
                
        with open("scratch/backtest_events.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Time", "Message", "PnL"])
            for e in res.get("events", []):
                writer.writerow([
                    e.get('datetime'),
                    e.get('message'),
                    e.get('pnl', '')
                ])
                
        print("Logs saved to scratch/backtest_trades.csv and scratch/backtest_events.csv")

if __name__ == "__main__":
    asyncio.run(run())
