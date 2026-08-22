import asyncio
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend.program_manager import ProgramManager

async def run():
    pm = ProgramManager(None, None, None)
    await pm.load_from_disk()
    groups = pm.list_sentinel_groups()
    if not groups:
        print("No groups found")
        return
        
    group = groups[0]
    print(f"Old group: {group}")
    group['index_id'] = "IDX_-1_NSE"
    pm.update_sentinel_group(group['sentinel_group_id'], group)
    print(f"Updated group: {group}")

if __name__ == "__main__":
    asyncio.run(run())
