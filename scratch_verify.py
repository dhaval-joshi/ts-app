import asyncio
import tempfile
import os
import shutil
import datetime

# Stub out things so we don't hit the real network or data dir
import backend.config as config
tmp_data = tempfile.mkdtemp()
config.DATA_DIR = tmp_data
config.SIGNAL_HISTORY_DIR = os.path.join(tmp_data, "signal_history")

from types import SimpleNamespace
class FakeScriptMaster:
    def get_index(self, name):
        return SimpleNamespace(disp_name=name, exc_token="111")
    
    def next_expiry(self, *args, **kwargs):
        return "2026-08-25"
        
    def atm_pair(self, *args, **kwargs):
        return (SimpleNamespace(id="CE_1", exc_token="222", strike=10000, lot=15, tick=0.05),
                SimpleNamespace(id="PE_1", exc_token="333", strike=10000, lot=15, tick=0.05))

# Stub ProgramManager
class FakeProgramManager:
    def __init__(self):
        self.triggered_cycles = []
        self._programs = {}
        
    async def start_signal_single_leg_cycle(self, program_id, leg):
        print(f"TRIGGERED: Program {program_id} -> Leg {leg}")
        self.triggered_cycles.append((program_id, leg))

from backend.indicators import IndicatorService
from backend.signal_engine import SignalEngine
from unittest.mock import patch
import backend.clock as clock

async def test_signal_engine():
    start_time = datetime.datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
    now = start_time
    
    def mock_now():
        from backend.clock import IST
        return now.replace(tzinfo=IST)
        
    with patch("backend.clock.now", side_effect=mock_now):
        pm = FakeProgramManager()
        indicators = IndicatorService(None)
        sm = FakeScriptMaster()
        engine = SignalEngine(pm, sm)
        
        cfg = SimpleNamespace(
            program_id="test_prog_1",
            entry_mode="signal_single_leg",
            index_id="IDX_NIFTY_NSE",
            orb_duration_minutes=15
        )
        
        idx_token = sm.get_index(cfg.index_id).exc_token
        stream_symbol = f"{idx_token}_NSE"
        pm._programs = {
            cfg.program_id: SimpleNamespace(
                config=cfg,
                index_exc_token=idx_token
            )
        }
        
        indicators.handle_l1_tick({"symbol": stream_symbol, "ltp": 10000})
        engine.handle_l1_tick({"symbol": stream_symbol, "ltp": 10000})
        await asyncio.sleep(0.01)
        
        now = start_time + datetime.timedelta(minutes=10)
        indicators.handle_l1_tick({"symbol": stream_symbol, "ltp": 10100})
        engine.handle_l1_tick({"symbol": stream_symbol, "ltp": 10100})
        await asyncio.sleep(0.01)
        
        now = start_time + datetime.timedelta(minutes=14)
        indicators.handle_l1_tick({"symbol": stream_symbol, "ltp": 9900})
        engine.handle_l1_tick({"symbol": stream_symbol, "ltp": 9900})
        await asyncio.sleep(0.01)
        
        print(f"Triggered cycles before ORB completion: {pm.triggered_cycles}")
        
        now = start_time + datetime.timedelta(minutes=15)
        indicators.handle_l1_tick({"symbol": stream_symbol, "ltp": 10000})
        engine.handle_l1_tick({"symbol": stream_symbol, "ltp": 10000})
        await asyncio.sleep(0.01)
        
        print("ORB tracking finished.")
        
        now = start_time + datetime.timedelta(minutes=16)
        indicators.handle_l1_tick({"symbol": stream_symbol, "ltp": 10150})
        engine.handle_l1_tick({"symbol": stream_symbol, "ltp": 10150})
        await asyncio.sleep(0.01)
        
        print(f"Triggered cycles after breaking high: {pm.triggered_cycles}")
        
    shutil.rmtree(tmp_data)

from backend.entry_signals import squeeze_gate, _ttm_squeeze_active

def test_squeeze_gate():
    print("\n--- Testing TTM Squeeze Gate ---")
    # Generate mock history
    # Let's say price is flat (close = 1000) but True Range is 10 (High=1005, Low=995)
    # BB width will be 0 (std=0). KC width will be 15. BB is inside KC!
    history = []
    for i in range(30):
        history.append({
            "open": 1000,
            "high": 1005,
            "low": 995,
            "close": 1000,
            "volume": 0
        })
    
    # Should be squeezed
    is_squeezed, reason = squeeze_gate(history, period=20, num_std=2.0, min_days=5, kc_mult=1.5, require_ttm_squeeze=True)
    print(f"Flat market (BB inside KC) -> allowed (is_squeezed=True)? {is_squeezed}")
    assert is_squeezed is True

    # Now let's make price volatile so BB expands outside KC
    # Alternating closes: 900, 1100, 900, 1100 -> std is 100. BB half width = 200.
    # High/Low are same as close, so True range is 200. ATR = 200. KC half width = 1.5 * 200 = 300.
    # Wait, if BB half width (200) < KC half width (300), it's STILL inside KC.
    # To make BB > KC, we need TR to be smaller than StdDev. But TR is always >= absolute change in Close!
    # If TR = absolute change in Close, TR = 200. So KC = 300. 
    # BB half width is 2 * StdDev.
    # Can BB half width be > KC half width? Yes, if BB std mult is 2, and KC mult is 1.5. 2*StdDev vs 1.5*ATR.
    # StdDev is usually <= ATR. So 2*StdDev can be > 1.5*ATR.
    # In the alternating 900, 1100 case: mean=1000, std=100. BB=200. TR=200, ATR=200, KC=300. BB(200) < KC(300). Squeeze is ON.
    # What if price is a straight trend? 10, 20, 30, 40...
    history_trending = []
    for i in range(30):
        c = 1000 + i * 20
        history_trending.append({
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 0
        })
    is_squeezed, reason = squeeze_gate(history_trending, period=20, num_std=2.0, min_days=5, kc_mult=1.5, require_ttm_squeeze=True)
    print(f"Trending market (BB outside KC) -> allowed? {is_squeezed}")
    assert is_squeezed is False

if __name__ == "__main__":
    test_squeeze_gate()
    asyncio.run(test_signal_engine())
