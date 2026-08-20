import asyncio
import logging
from datetime import timedelta
from typing import Dict

from . import clock

log = logging.getLogger("tradejini.signal_engine")

class SignalEngine:
    """
    Subscribes to live L1 ticks to track the Opening Range Breakout (ORB) High/Low
    for each program configured with signal_single_leg. Emits a trigger to ProgramManager
    when the breakout happens.
    """
    def __init__(self, manager, script_master):
        self.manager = manager
        self.script_master = script_master
        # state: program_id -> {"date": "YYYY-MM-DD", "orb_high": float, "orb_low": float, "triggered": bool}
        self.state: Dict[str, dict] = {}
        
    def _get_state(self, program_id: str, date_str: str) -> dict:
        state = self.state.setdefault(program_id, {})
        if state.get("date") != date_str:
            state.clear()
            state["date"] = date_str
            state["orb_high"] = 0.0
            state["orb_low"] = float('inf')
            state["triggered"] = False
        return state

    def handle_l1_tick(self, data: dict):
        symbol_id = data.get("symbol")
        ltp = float(data.get("ltp", 0.0))
        if not symbol_id or ltp <= 0:
            return

        now = clock.now()
        date_str = now.date().isoformat()
        
        # Determine market open. NSE equity/F&O opens at 09:15
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        
        if now < market_open or now.hour >= 15:
            return

        for program_id, p in list(self.manager._programs.items()):
            cfg = p.config
            if getattr(cfg, "entry_mode", "auto_pair") != "signal_single_leg":
                continue
                
            # Signal Engine tracks the underlying index of the program for ORB
            index_id = getattr(cfg, "index_id", None) if not isinstance(cfg, dict) else cfg.get("index_id")
            if not index_id:
                continue
            idx = self.script_master.get_index(index_id)
            if not idx or f"{idx.exc_token}_NSE" != symbol_id:
                continue
                
            state = self._get_state(program_id, date_str)
            if state.get("triggered"):
                continue
                
            orb_minutes = getattr(cfg, "orb_duration_minutes", 15)
            orb_end_time = market_open + timedelta(minutes=orb_minutes)
            
            if now <= orb_end_time:
                # We are in the ORB forming window
                state["orb_high"] = max(state["orb_high"], ltp)
                state["orb_low"] = min(state["orb_low"], ltp)
            else:
                # ORB window is over, check for breakout
                if state["orb_high"] == 0.0 or state["orb_low"] == float('inf'):
                    continue
                
                # Check breakout
                leg_direction = None
                if ltp > state["orb_high"]:
                    leg_direction = "CE"
                elif ltp < state["orb_low"]:
                    leg_direction = "PE"
                    
                if leg_direction:
                    log.info("SignalEngine: Program %s triggered %s breakout! (LTP %.2f crossed ORB High/Low %.2f/%.2f)",
                             getattr(cfg, "name", program_id), leg_direction, ltp, state["orb_high"], state["orb_low"])
                    state["triggered"] = True
                    
                    # Fire the trigger asynchronously to not block the stream loop
                    asyncio.create_task(self.manager.start_signal_single_leg_cycle(program_id, leg_direction))
