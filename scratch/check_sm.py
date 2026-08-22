import asyncio
from backend.tradejini_client import TradejiniClient
from backend.script_master import ScriptMaster

async def f():
    client = TradejiniClient()
    sm = ScriptMaster(client)
    await sm.refresh(client)
    print("IDX_-1_NSE:", sm.get_index("IDX_-1_NSE"))
    print("IDX_NIFTY_NSE:", sm.get_index("IDX_NIFTY_NSE"))

asyncio.run(f())
