import asyncio
import logging
import math
from typing import Dict
from .tradejini_client import TradejiniClient
from . import store

log = logging.getLogger("tradejini.sentinel_orchestrator")

class SentinelOrchestrator:
    def __init__(self, pm, broker_client: TradejiniClient, regime_cls):
        self.pm = pm
        self.broker_client = broker_client
        self.regime_classifier = regime_cls
        self.group_states: Dict[str, dict] = {}
        
    def _calculate_isolated_capital(self, group_id: str, base_capital: float, child_programs: list) -> float:
        realized_pnl = 0.0
        for p in child_programs:
            cycles = p.get("cycles", [])
            for cycle in cycles:
                realized_pnl += cycle.get("pnl", 0.0) or 0.0
                
        return base_capital + realized_pnl
        
    async def _calculate_dynamic_config(self, regime: str, index_id: str) -> dict:
        # Fetch live Greeks snapshot via the order manager (using its stream)
        # To avoid blocking forever, use a timeout
        try:
            # We assume NIFTY is the only supported one for now, or use index_id logic
            # Just default to some hardcoded values if greek fetch fails in sim
            iv = 15.0 
            ivr = 35.0
            
            # Fetch from broker stream if available
            snaps = await self.pm.om.fetch_greeks_snapshot(index_id, timeout=2.0)
            if snaps and "iv" in snaps:
                iv = snaps.get("iv", 15.0)
                ivr = snaps.get("ivr", 35.0)
        except Exception:
            iv = 15.0
            ivr = 35.0

        if regime == "SIDEWAYS":
            # Theta Collection: Short Straddle
            # Stop = 1.5 * implied daily move
            implied_daily_move = (iv / math.sqrt(252)) * 100 
            stop_pct = min(max(implied_daily_move * 1.5, 10.0), 40.0)
            
            return {
                "entry_mode": "auto_pair", # Will be updated to act as a short
                "trade_type": "sell",
                "required_margin_per_lot": 130000.0, # Approximate margin for short straddle leg
                "entry_condition_met": ivr > 30.0,
                "entry_fail_reason": f"IVR ({ivr:.1f}%) is too low for Theta Collection (< 30%)",
                "stop": {
                    "offset_mode": "percent",
                    "trig_offset": stop_pct,
                    "limit_offset": 0.0,
                    "trailing": {"enabled": True, "trail_by": 5.0, "activation_offset": stop_pct * 0.5} # Smart exit
                },
                "target": {
                    "offset_mode": "percent",
                    "trig_offset": 50.0,
                    "limit_offset": 0.0,
                    "trailing": {"enabled": False}
                }
            }
        elif regime == "DIRECTIONAL":
            # Trend Following: Long Single Leg
            return {
                "entry_mode": "signal_single_leg",
                "trade_type": "buy",
                "orb_duration_minutes": 15,
                "required_margin_per_lot": 15000.0, # Long option cost approximation
                "entry_condition_met": True,
                "entry_fail_reason": "",
                "stop": {
                    "offset_mode": "percent",
                    "trig_offset": 15.0,
                    "limit_offset": 0.0,
                    "trailing": {"enabled": True, "trail_by": 5.0, "activation_offset": 0} # Smart trailing
                },
                "target": {
                    "offset_mode": "percent",
                    "trig_offset": 200.0, # Open ended
                    "limit_offset": 0.0,
                    "trailing": {"enabled": True, "trail_by": 10.0, "activation_offset": 40.0}
                }
            }
        elif regime == "VOLATILE":
            # Vega Expansion: Long Straddle
            return {
                "entry_mode": "auto_pair",
                "trade_type": "buy",
                "required_margin_per_lot": 30000.0, # Long straddle cost approximation
                "entry_condition_met": ivr < 30.0,
                "entry_fail_reason": f"IVR ({ivr:.1f}%) is too high for Volatility Expansion (> 30%)",
                "stop": {
                    "offset_mode": "percent",
                    "trig_offset": 15.0,
                    "limit_offset": 0.0,
                    "trailing": {"enabled": False}
                },
                "target": {
                    "offset_mode": "percent",
                    "trig_offset": 40.0,
                    "limit_offset": 0.0,
                    "trailing": {"enabled": True, "trail_by": 10.0, "activation_offset": 30.0}
                }
            }
            
        return {}

    async def tick(self):
        sentinel_groups = self.pm._sentinel_groups
        active_groups = {k: v for k, v in sentinel_groups.items() if v.get("is_active", False)}
        
        if not active_groups:
            return
            
        programs = self.pm._programs
        groups = {}
        for p in programs.values():
            config = p.get("config", {})
            if config.get("execution_mode") != "autonomous_sentinel" or not config.get("is_auto_generated"):
                continue
                
            group_id = config.get("sentinel_group_id")
            if group_id in active_groups:
                if group_id not in groups:
                    groups[group_id] = []
                groups[group_id].append(p)
                
        for group_id, group_progs in groups.items():
            if not group_progs:
                continue
                
            sg = active_groups[group_id]
            index_id = sg["index_id"]
            regime_info = self.regime_classifier.get_current_regime(index_id)
            current_regime = regime_info.get("state", "UNKNOWN")
            
            if current_regime == "UNKNOWN":
                continue
                
            state_info = self.group_states.get(group_id, {"state": "IDLE", "active_program_id": None, "target_regime": None, "skip_reason": None})
            
            eligible_prog = next((p for p in group_progs if p["config"].get("target_regime") == current_regime), None)
            
            if state_info["target_regime"] != current_regime:
                log.info(f"SentinelGroup {group_id}: Regime shift detected -> {current_regime}")
                
                active_program = None
                if state_info["active_program_id"]:
                    active_program = next((p for p in group_progs if p["config"]["program_id"] == state_info["active_program_id"]), None)
                else:
                    active_program = next((p for p in group_progs if p["runtime"].get("active_cycle_id")), None)
                    
                if active_program:
                    log.info(f"SentinelGroup {group_id}: Flattening active program {active_program['config']['program_id']}")
                    await self.pm.flatten_program_for_orchestration(active_program["config"]["program_id"])
                    
                state_info["state"] = "AWAITING_MARGIN"
                state_info["target_regime"] = current_regime
                state_info["active_program_id"] = None
                state_info["skip_reason"] = None
                self.group_states[group_id] = state_info
                
            if state_info["state"] == "AWAITING_MARGIN":
                log.debug(f"SentinelGroup {group_id}: Awaiting margin settlement for 2 seconds...")
                await asyncio.sleep(2)
                
                if eligible_prog:
                    # Autonomous Logic Generation
                    dyn_config = await self._calculate_dynamic_config(current_regime, index_id)
                    
                    if not dyn_config.get("entry_condition_met", True):
                        state_info["state"] = "SKIPPED"
                        state_info["skip_reason"] = dyn_config.get("entry_fail_reason")
                        log.warning(f"SentinelGroup {group_id}: {state_info['skip_reason']}")
                    else:
                        # Isolated Capital Sandbox check
                        base_capital = sg.get("capital_per_leg", 100000.0)
                        available_capital = self._calculate_isolated_capital(group_id, base_capital, group_progs)
                        required_margin = dyn_config.get("required_margin_per_lot", 50000.0)
                        
                        if available_capital < required_margin:
                            state_info["state"] = "SKIPPED"
                            state_info["skip_reason"] = f"Insufficient Isolated Capital: ₹{available_capital:.2f} available vs ₹{required_margin:.2f} required for {dyn_config['trade_type']} in {current_regime} regime."
                            log.warning(f"SentinelGroup {group_id}: {state_info['skip_reason']}")
                        else:
                            # Inject dynamic config into program
                            cfg = eligible_prog["config"]
                            cfg["entry_mode"] = dyn_config["entry_mode"]
                            cfg["trade_type"] = dyn_config["trade_type"]
                            if "orb_duration_minutes" in dyn_config:
                                cfg["orb_duration_minutes"] = dyn_config["orb_duration_minutes"]
                            cfg["stop"] = dyn_config["stop"]
                            cfg["target"] = dyn_config["target"]
                            self.pm.update_program(cfg["program_id"], cfg)
                            
                            log.info(f"SentinelGroup {group_id}: Dynamic config injected. Deploying {cfg['program_id']}")
                            success = await self.pm.force_start_cycle(cfg["program_id"])
                            if success:
                                state_info["state"] = "DEPLOYED"
                                state_info["active_program_id"] = cfg["program_id"]
                                state_info["skip_reason"] = None
                            else:
                                state_info["state"] = "IDLE" 
                else:
                    state_info["state"] = "IDLE"
                    
                self.group_states[group_id] = state_info

_orchestrator = None

def init_orchestrator(pm, broker_client, regime_cls):
    global _orchestrator
    _orchestrator = SentinelOrchestrator(pm, broker_client, regime_cls)

async def orchestrator_loop():
    while True:
        try:
            if _orchestrator:
                await _orchestrator.tick()
        except Exception as e:
            log.error(f"Error in orchestrator loop: {e}", exc_info=True)
        await asyncio.sleep(5)
