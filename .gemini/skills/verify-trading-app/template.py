# Copy this file, adapt the strategy/req/assertions to the scenario you're
# verifying, run it, read the output, then delete your copy. This project
# doesn't keep these permanently -- see the skill's SKILL.md for why, and
# AGENTS.md for the (separate, bigger) question of a real test suite.
#
# Run from the project root: python3 your_copy_of_this_file.py

import sys, asyncio, tempfile, shutil, types
sys.path.insert(0, '.')

# Stub the real client's dependencies so this runs with no network access
# and no real credentials.
for name in ['httpx', 'pyotp']:
    sys.modules[name] = types.ModuleType(name)
sys.modules['httpx'].AsyncClient = type('AsyncClient', (), {})
sys.modules['pyotp'].TOTP = type('TOTP', (), {})

import backend.config as config
tmp = tempfile.mkdtemp()
config.DATA_DIR = __import__('pathlib').Path(tmp)
config.ORDERS_DIR = config.DATA_DIR / 'orders'
config.ARCHIVE_DIR = config.ORDERS_DIR / 'archive'
config.ORDERS_DIR.mkdir(parents=True, exist_ok=True)
config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
import backend.failure_log as failure_log
failure_log.FAILURES_PATH = config.DATA_DIR / 'failures.jsonl'

from backend.order_manager import OrderManager
from types import SimpleNamespace


class RecordingClient:
    """Records every call it receives so you can assert on exactly what
    did and didn't happen -- proving something did NOT happen (e.g. no
    OCO order was placed) is often the actual point of the test."""
    def __init__(self):
        self.calls = []
        self._orders = {}
        self._next_id = 1

    async def place_order(self, **fields):
        self.calls.append(('place_order', dict(fields)))
        oid = f"B{self._next_id}"; self._next_id += 1
        self._orders[oid] = {
            'orderId': oid, 'status': 'completed',
            'fillQty': fields.get('qty'), 'avgPrice': fields.get('limitPrice') or 100.0,
        }
        return {'d': {'orderId': oid}}

    async def cancel_order(self, order_id):
        self.calls.append(('cancel_order', order_id))
        return {'d': {}}

    async def get_orders(self):
        return list(self._orders.values())

    # Add get_positions / get_trades here only if the specific code path
    # you're testing actually calls them -- most current code doesn't.


class FakeStream:
    def set_trailing_symbols(self, symbols, owner='live'):
        pass


client = RecordingClient()
om = OrderManager(client=client, stream=FakeStream())


async def main():
    strategy = {
        "product": "intraday", "tick_size": 0.05,
        "stop": {"offset_mode": "points", "trig_offset": 10, "limit_offset": 0,
                 "trailing": {"enabled": True, "trail_by": 3, "activation_offset": 0}},
        "target": {"offset_mode": "points", "trig_offset": 20, "limit_offset": 0,
                   "trailing": {"enabled": False, "trail_by": 0, "activation_offset": 0}},
        "time_exit": {"mode": "none", "window_start": None, "window_end": None, "at": None},
    }
    req = SimpleNamespace(
        sym_id="TESTSYM", side="buy", qty=100, lot_size=100,
        strategy_name=None, label="Test", stream_symbol="TESTSYM_NSE",
        entry_type="market", entry_validity="day", entry_limit_price=None, entry_trig_price=None,
        exit_mode="both",
    )

    order = await om.create_and_place_order_with_strategy(req, strategy)
    order_id = order["order_id"]

    # Entry-fill detection is a SEPARATE reconcile step -- forgetting this
    # is the most common mistake writing one of these tests.
    await om._reconcile_once()
    o = om.get_order(order_id)
    assert o["status"] == "watching", f"expected watching, got {o['status']}"
    print("1. Entry fill detected -- PASS")

    # Feed a tick to drive trailing / trigger-crossing.
    await om.handle_l1_tick({"symbol": "TESTSYM_NSE", "ltp": 85.0})
    o = om.get_order(order_id)
    print(f"   status after tick: {o['status']}")

    # --- add your scenario-specific assertions here ---

    print()
    print("ALL TESTS PASSED")


asyncio.run(main())
shutil.rmtree(tmp)
