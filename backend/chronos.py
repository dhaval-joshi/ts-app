import asyncio
import logging
import time
import copy
from datetime import datetime
from typing import Dict, Any, List

from . import store
from .regime_classifier import RegimeClassifier, STATE_UNKNOWN
from .entry_signals import evaluate_entry
from .tradejini_client import TradejiniClient
from .bsm_calculator import compute_greeks
from .script_master import ScriptMaster

log = logging.getLogger("tradejini.chronos")

class ChronosEngine:
    """
    Project Chronos: Native Backtesting Engine for active contracts.
    Implements a tick-by-tick (minute-by-minute) simulation of the OrderManager and Entry Gates
    over the historical lifespan of the currently active options.
    """
    def __init__(self, client: TradejiniClient, script_master: ScriptMaster):
        self.client = client
        self.script_master = script_master
        self.regime_classifier = RegimeClassifier(client)
        # Mocking the regime's update_with_live_tick during backtest
        
    async def run_backtest(self, target_id: str, days_back: int, starting_capital: float = 100000.0, is_group: bool = False) -> Dict[str, Any]:
        programs_to_test = []
        if is_group:
            all_progs = store.list_programs()
            programs_to_test = [p for p in all_progs if p.get("config", {}).get("sentinel_group_id") == target_id]
            if not programs_to_test:
                return {"error": f"No programs found for sentinel_group_id {target_id}."}
        else:
            program = store.load_program(target_id)
            if not program:
                return {"error": f"Program {target_id} not found."}
            programs_to_test = [program]
            
        index_id = programs_to_test[0]["config"]["index_id"]
        
        current_ts = int(time.time())
        from_ts = current_ts - (days_back * 24 * 3600)
        
        # 1. Fetch Historical Index Data
        resp = await self.client.get_interval_chart_data(index_id, "1", from_ts, current_ts)
        raw_bars = resp.get("bars", []) if isinstance(resp, dict) else resp
        if not raw_bars and isinstance(resp, dict):
            raw_bars = resp.get("chartData", [])
            
        index_bars = self._parse_bars(raw_bars)
        if not index_bars:
            return {"error": "Failed to fetch historical index data. Ensure API is reachable."}
            
        log.info("Chronos: Fetched %d index bars.", len(index_bars))
        
        # We need to simulate the day.
        # We will iterate through index bars.
        
        capital = starting_capital
        trades = []
        
        is_in_trade = False
        current_trade = None
        
        # To compute Greeks we need IV tracking
        iv_tracker = {"CE": {"low": 999.0, "high": 0.0}, "PE": {"low": 999.0, "high": 0.0}}
        last_day = None
        
        # We prefetch all option data for the ATM strikes to avoid spamming the API inside the loop
        option_data_cache = {}
        
        async def get_option_bar(sym_id: str, ts: int):
            if sym_id not in option_data_cache:
                # Fetch full history for this option to avoid many small calls
                o_resp = await self.client.get_interval_chart_data(sym_id, "1", from_ts, current_ts)
                o_raw = o_resp.get("bars", []) if isinstance(o_resp, dict) else o_resp
                if not o_raw and isinstance(o_resp, dict):
                    o_raw = o_resp.get("chartData", [])
                option_data_cache[sym_id] = {b["timestamp"]: b for b in self._parse_bars(o_raw)}
            return option_data_cache[sym_id].get(ts)

        for i, bar in enumerate(index_bars):
            ts = bar["timestamp"]
            ltp = bar["close"]
            
            # Reset daily trackers
            dt = datetime.fromtimestamp(ts)
            if last_day != dt.day:
                iv_tracker = {"CE": {"low": 999.0, "high": 0.0}, "PE": {"low": 999.0, "high": 0.0}}
                last_day = dt.day
                
            # Feed into RegimeClassifier
            # We override load_historical_bars temporarily or just feed it directly.
            # To be non-destructive, we can just instantiate a clean array of past bars up to i
            store.save_historical_bars(f"chronos_mock_{index_id}", index_bars[max(0, i-60):i+1])
            self.regime_classifier._compute_regime(f"chronos_mock_{index_id}")
            regime = self.regime_classifier.get_current_regime(f"chronos_mock_{index_id}").get("state", STATE_UNKNOWN)
            
            # Manage open trade
            if is_in_trade:
                active_cfg = current_trade["config"]
                
                ce_bar = await get_option_bar(current_trade["ce_id"], ts)
                pe_bar = await get_option_bar(current_trade["pe_id"], ts)
                
                if ce_bar and pe_bar:
                    ce_ltp = ce_bar["close"]
                    pe_ltp = pe_bar["close"]
                    
                    # Sentinel Smart Exit Preemptive check
                    exec_mode = active_cfg.get("execution_mode")
                    t_regime = active_cfg.get("target_regime")
                    if exec_mode == "sentinel" and t_regime and t_regime != "ANY":
                        if regime != t_regime and regime != STATE_UNKNOWN:
                            # Preemptive close (Regime shifted)
                            self._close_trade(current_trade, ts, ce_ltp, pe_ltp, "regime_shift", capital)
                            capital += current_trade["capital_allocated"] + current_trade["pnl"]
                            trades.append(current_trade)
                            is_in_trade = False
                            continue
                            
                    # Check stops/targets (simplified combined PnL)
                    comb_entry = current_trade["ce_entry"] + current_trade["pe_entry"]
                    comb_ltp = ce_ltp + pe_ltp
                    
                    sl_pct = active_cfg.get("stop", {}).get("trig_offset", 0.0)
                    if sl_pct > 0 and comb_ltp <= comb_entry * (1 - (sl_pct / 100.0)):
                        self._close_trade(current_trade, ts, ce_ltp, pe_ltp, "stop_hit", capital)
                        capital += current_trade["capital_allocated"] + current_trade["pnl"]
                        trades.append(current_trade)
                        is_in_trade = False
                        continue
                        
                # End of day square off
                if dt.hour == 15 and dt.minute >= 15:
                    if ce_bar and pe_bar:
                        self._close_trade(current_trade, ts, ce_bar["close"], pe_bar["close"], "time_exit", capital)
                    else:
                        self._close_trade(current_trade, ts, current_trade["ce_entry"], current_trade["pe_entry"], "time_exit", capital)
                    capital += current_trade["capital_allocated"] + current_trade["pnl"]
                    trades.append(current_trade)
                    is_in_trade = False
                    
                continue
                
            # If not in trade, look for entry
            if dt.hour < 9 or (dt.hour == 9 and dt.minute < 20) or (dt.hour == 15 and dt.minute > 10):
                continue
                
            # Which program should we evaluate?
            eligible_prog = None
            if is_group:
                # Find program that matches the regime
                eligible_prog = next((p for p in programs_to_test if p["config"].get("target_regime") == regime and p["config"].get("execution_mode") in ("sentinel", "autonomous_sentinel")), None)
            else:
                eligible_prog = programs_to_test[0]
                
            if not eligible_prog:
                continue
                
            cfg = eligible_prog["config"]
                
            # Find ATM strikes
            expiries = [e for e in self.script_master.available_expiries(index_id) if e >= dt.date()]
            if not expiries:
                continue
            expiry = expiries[0]
                
            pair = self.script_master.strike_pair_at_offset(index_id, expiry, ltp, 0)
            if not pair:
                continue
                
            ce_opt, pe_opt = pair
            
            # Need current option prices to calculate IV
            ce_bar = await get_option_bar(ce_opt.id, ts)
            pe_bar = await get_option_bar(pe_opt.id, ts)
            
            if not ce_bar or not pe_bar:
                continue
                
            expiry_dt = datetime.combine(expiry, datetime.min.time())
            expiry_ts = expiry_dt.timestamp() + (15.5 * 3600) # 15:30 IST on expiry day
            dte = max(0.001, (expiry_ts - ts) / (365 * 24 * 3600))
            
            ce_greeks = compute_greeks(ltp, float(ce_opt.strike), dte, 0.06, ce_bar["close"], "CE")
            pe_greeks = compute_greeks(ltp, float(pe_opt.strike), dte, 0.06, pe_bar["close"], "PE")
            
            # Update trackers
            iv_tracker["CE"]["low"] = min(iv_tracker["CE"]["low"], ce_greeks["iv"])
            iv_tracker["CE"]["high"] = max(iv_tracker["CE"]["high"], ce_greeks["iv"])
            iv_tracker["PE"]["low"] = min(iv_tracker["PE"]["low"], pe_greeks["iv"])
            iv_tracker["PE"]["high"] = max(iv_tracker["PE"]["high"], pe_greeks["iv"])
            
            ce_greeks["lowiv"] = iv_tracker["CE"]["low"]
            ce_greeks["highiv"] = iv_tracker["CE"]["high"]
            pe_greeks["lowiv"] = iv_tracker["PE"]["low"]
            pe_greeks["highiv"] = iv_tracker["PE"]["high"]
            
            # Inject Autonomous Sentinel dynamic configuration
            if cfg.get("execution_mode") == "autonomous_sentinel":
                iv = (ce_greeks["iv"] + pe_greeks["iv"]) / 2.0
                iv_low = min(iv_tracker["CE"]["low"], iv_tracker["PE"]["low"])
                iv_high = max(iv_tracker["CE"]["high"], iv_tracker["PE"]["high"])
                ivr = 0.0
                if iv_high > iv_low:
                    ivr = ((iv - iv_low) / (iv_high - iv_low)) * 100.0
                    
                import math
                if regime == "SIDEWAYS":
                    implied_daily_move = (iv / math.sqrt(252)) * 100 
                    stop_pct = min(max(implied_daily_move * 1.5, 10.0), 40.0)
                    cfg["entry_mode"] = "auto_pair"
                    cfg["trade_type"] = "sell"
                    cfg["required_margin_per_lot"] = 130000.0
                    cfg["stop"] = {"offset_mode": "percent", "trig_offset": stop_pct, "limit_offset": 0.0, "trailing": {"enabled": True, "trail_by": 5.0, "activation_offset": stop_pct * 0.5}}
                    cfg["target"] = {"offset_mode": "percent", "trig_offset": 50.0, "limit_offset": 0.0, "trailing": {"enabled": False}}
                elif regime == "DIRECTIONAL":
                    cfg["entry_mode"] = "signal_single_leg"
                    cfg["trade_type"] = "buy"
                    cfg["orb_duration_minutes"] = 15
                    cfg["required_margin_per_lot"] = 15000.0
                    cfg["stop"] = {"offset_mode": "percent", "trig_offset": 15.0, "limit_offset": 0.0, "trailing": {"enabled": True, "trail_by": 5.0, "activation_offset": 0}}
                    cfg["target"] = {"offset_mode": "percent", "trig_offset": 200.0, "limit_offset": 0.0, "trailing": {"enabled": True, "trail_by": 10.0, "activation_offset": 40.0}}
                elif regime == "VOLATILE":
                    cfg["entry_mode"] = "auto_pair"
                    cfg["trade_type"] = "buy"
                    cfg["required_margin_per_lot"] = 30000.0
                    cfg["stop"] = {"offset_mode": "percent", "trig_offset": 15.0, "limit_offset": 0.0, "trailing": {"enabled": False}}
                    cfg["target"] = {"offset_mode": "percent", "trig_offset": 40.0, "limit_offset": 0.0, "trailing": {"enabled": True, "trail_by": 10.0, "activation_offset": 30.0}}

            # Evaluate entry
            entry_cfg = cfg.get("entry_signals", {})
            entry_cfg["enabled"] = True # Force enable to see if it would trigger
            
            allowed, reason, _ = evaluate_entry(
                entry_cfg,
                index_snapshot=bar,
                ce_snapshot=ce_bar,
                pe_snapshot=pe_bar,
                ce_greeks=ce_greeks,
                pe_greeks=pe_greeks,
                vix_ltp=15.0, # Mocked VIX
                vix_history=[],
                index_history=index_bars[:i],
                execution_mode=cfg.get("execution_mode", "manual_config"),
                target_regime=cfg.get("target_regime", "ANY"),
                active_regime=regime
            )
            
            if allowed:
                # Enter trade
                trade_type = cfg.get("trade_type", "buy")
                qty = ce_opt.lot * 1 # 1 lot for simulation (or could calculate based on capital)
                if cfg.get("sizing_mode") == "capital":
                    eff_price = ce_bar["close"] + pe_bar["close"]
                    if trade_type == "sell":
                        margin_per_lot = cfg.get("required_margin_per_lot", 130000.0)
                        qty = int(capital / margin_per_lot) * ce_opt.lot if margin_per_lot > 0 else ce_opt.lot
                    else:
                        qty = int(capital / eff_price) // ce_opt.lot * ce_opt.lot if eff_price > 0 else ce_opt.lot
                
                cost = (ce_bar["close"] + pe_bar["close"]) * qty
                margin_req = (qty / ce_opt.lot) * cfg.get("required_margin_per_lot", 130000.0) if trade_type == "sell" else cost
                
                if margin_req > capital:
                    continue # Insufficient capital
                    
                is_in_trade = True
                current_trade = {
                    "program_id": cfg["program_id"],
                    "config": copy.deepcopy(cfg),
                    "entry_ts": ts,
                    "trade_type": trade_type,
                    "qty": qty,
                    "capital_allocated": margin_req,
                    "ce_id": ce_opt.id,
                    "pe_id": pe_opt.id,
                    "ce_entry": ce_bar["close"],
                    "pe_entry": pe_bar["close"],
                    "high_pnl": 0.0
                }
                
                dt_str = datetime.fromtimestamp(ts).strftime('%d %b %H:%M')
                regime = cfg.get("target_regime", "Trade")
                msg = f"[{dt_str}] Entered {regime} ({trade_type}) - Margin: ₹{margin_req:.2f}"
                log.info(msg)
                
                if trade_type == "sell":
                    capital -= margin_req
                    capital += cost # Receive premium
                else:
                    capital -= cost
                
        # Clean up
        conn = store._get_db()
        conn.execute("DELETE FROM historical_bars WHERE symbol_id = ?", (f"chronos_mock_{index_id}",))
        conn.commit()
        
        # Calculate summary
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        return {
            "status": "success",
            "trades": trades,
            "total_pnl": total_pnl,
            "final_capital": starting_capital + total_pnl,
            "trade_count": len(trades)
        }
        
    def _close_trade(self, trade, ts, ce_exit, pe_exit, reason, capital):
        trade["exit_ts"] = ts
        trade["ce_exit"] = ce_exit
        trade["pe_exit"] = pe_exit
        trade["close_reason"] = reason
        if trade.get("trade_type") == "sell":
            trade["pnl"] = ((trade["ce_entry"] + trade["pe_entry"]) - (ce_exit + pe_exit)) * trade.get("qty", 0)
        else:
            trade["pnl"] = ((ce_exit + pe_exit) - (trade["ce_entry"] + trade["pe_entry"])) * trade.get("qty", 0)
            
        # Format a nice message for the frontend
        dt_str = datetime.fromtimestamp(ts).strftime('%d %b %H:%M')
        regime = trade["config"].get("target_regime", "Trade") if "config" in trade else "Trade"
        msg = f"[{dt_str}] Closed {regime} ({trade.get('trade_type', 'buy')}) due to {reason}. PnL: ₹{trade['pnl']:.2f}"
        log.info(msg)

    def _parse_bars(self, raw_bars) -> List[Dict[str, float]]:
        new_bars = []
        for row in raw_bars:
            if isinstance(row, list) and len(row) >= 5:
                ts = int(row[0] / 1000) if row[0] > 1e11 else int(row[0])
                new_bars.append({
                    "timestamp": ts,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 else 0.0
                })
            elif isinstance(row, dict):
                raw_ts = row.get("t") or row.get("timestamp") or 0
                ts = int(raw_ts / 1000) if raw_ts > 1e11 else int(raw_ts)
                new_bars.append({
                    "timestamp": ts,
                    "open": float(row.get("o") or row.get("open", 0)),
                    "high": float(row.get("h") or row.get("high", 0)),
                    "low": float(row.get("l") or row.get("low", 0)),
                    "close": float(row.get("c") or row.get("close", 0)),
                    "volume": float(row.get("v") or row.get("volume", 0)),
                })
        return new_bars
