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
        
    async def run_backtest(self, target_id: str, days_back: int, starting_capital: float = 100000.0, is_group: bool = False, progress_cb=None) -> Dict[str, Any]:
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
        idx_info = self.script_master.get_index(index_id)
        if not idx_info:
            return {"error": f"Index {index_id} not found in ScriptMaster."}
        broker_index_id = idx_info.id
        
        current_ts = int(time.time())
        from_ts = current_ts - (days_back * 24 * 3600)
        
        # 1. Fetch Historical Index Data
        try:
            resp = await self.client.get_interval_chart_data(broker_index_id, "1", from_ts, current_ts)
            raw_bars = resp.get("bars", []) if isinstance(resp, dict) else resp
            if not raw_bars and isinstance(resp, dict):
                raw_bars = resp.get("chartData", [])
                
            index_bars = self._parse_bars(raw_bars)
        except Exception as e:
            log.error(f"Failed to fetch index bars for backtest: {e}", exc_info=True)
            return {"error": f"Failed to fetch historical index data. Ensure API is reachable. {e}"}
            
        if not index_bars:
            return {"error": "Failed to fetch historical index data. Ensure API is reachable."}
            
        log.info("Chronos: Fetched %d index bars.", len(index_bars))
        
        # We need to simulate the day.
        # We will iterate through index bars.
        
        capital = starting_capital
        trades = []
        events = []
        
        is_in_trade = False
        current_trade = None
        
        iv_tracker = {"CE": {"low": 999.0, "high": 0.0}, "PE": {"low": 999.0, "high": 0.0}}
        last_day = None
        option_data_cache = {}
        
        def log_event(ts, msg, pnl=None):
            dt_str = datetime.fromtimestamp(ts).strftime('%d %b %H:%M')
            log.info(f"[{dt_str}] {msg}")
            events.append({"timestamp": ts, "datetime": dt_str, "message": msg, "pnl": pnl})
        
        async def get_option_bar(sym_id: str, ts: int, current_dt: datetime):
            cache_key = f"{sym_id}_{current_dt.date()}"
            if cache_key not in option_data_cache:
                day_start = int(current_dt.replace(hour=9, minute=0, second=0, microsecond=0).timestamp())
                day_end = int(current_dt.replace(hour=15, minute=35, second=0, microsecond=0).timestamp())
                o_resp = await self.client.get_interval_chart_data(sym_id, "1", day_start, day_end)
                o_raw = o_resp.get("bars", []) if isinstance(o_resp, dict) else o_resp
                if not o_raw and isinstance(o_resp, dict):
                    o_raw = o_resp.get("chartData", [])
                option_data_cache[cache_key] = {b["timestamp"]: b for b in self._parse_bars(o_raw)}
            return option_data_cache[cache_key].get(ts)

        for i, bar in enumerate(index_bars):
            ts = bar["timestamp"]
            ltp = bar["close"]
            
            dt = datetime.fromtimestamp(ts)
            if last_day != dt.day:
                iv_tracker = {"CE": {"low": 999.0, "high": 0.0}, "PE": {"low": 999.0, "high": 0.0}}
                orb_state = {"high": 0.0, "low": float('inf'), "triggered": False, "leg": None}
                last_day = dt.day
                if progress_cb:
                    asyncio.create_task(progress_cb("backtest_event", {
                        "text": f"Simulating day {dt.strftime('%Y-%m-%d')}...",
                        "progress": int((i / max(1, len(index_bars))) * 100)
                    }))
                    await asyncio.sleep(0)
                
            store.save_historical_bars(f"chronos_mock_{index_id}", index_bars[max(0, i-60):i+1])
            self.regime_classifier._compute_regime(f"chronos_mock_{index_id}")
            regime = self.regime_classifier.get_current_regime(f"chronos_mock_{index_id}").get("state", STATE_UNKNOWN)
            
            # Detect regime shift
            if i > 0 and 'last_regime' in locals() and last_regime != regime and regime != STATE_UNKNOWN:
                log_event(ts, f"Regime shifted to {regime}")
            last_regime = regime
            
            if is_in_trade:
                active_cfg = current_trade["config"]
                signal_leg = current_trade.get("signal_leg")
                
                try:
                    ce_bar = await get_option_bar(current_trade["ce_id"], ts, dt)
                    pe_bar = await get_option_bar(current_trade["pe_id"], ts, dt)
                except Exception as e:
                    log.warning(f"Failed to fetch option bars for exit check at {dt}: {e}")
                    continue
                
                if ce_bar and pe_bar:
                    ce_ltp = ce_bar["close"]
                    pe_ltp = pe_bar["close"]
                    
                    # Calculate active price based on signal_leg
                    active_entry = 0.0
                    active_ltp = 0.0
                    if signal_leg == "CE":
                        active_entry = current_trade["ce_entry"]
                        active_ltp = ce_ltp
                    elif signal_leg == "PE":
                        active_entry = current_trade["pe_entry"]
                        active_ltp = pe_ltp
                    else:
                        active_entry = current_trade["ce_entry"] + current_trade["pe_entry"]
                        active_ltp = ce_ltp + pe_ltp
                        
                    # Track high for trailing logic
                    if trade_type == "sell":
                        current_trade["high_pnl"] = max(current_trade["high_pnl"], active_entry - active_ltp)
                    else:
                        current_trade["high_pnl"] = max(current_trade["high_pnl"], active_ltp - active_entry)
                    
                    # Check Smart Exit
                    exec_mode = active_cfg.get("execution_mode")
                    t_regime = active_cfg.get("target_regime")
                    if exec_mode in ("sentinel", "autonomous_sentinel") and t_regime and t_regime != "ANY":
                        if regime != t_regime and regime != STATE_UNKNOWN:
                            self._close_trade(current_trade, ts, ce_ltp, pe_ltp, "Smart Exit (Regime Shift)", capital, log_event)
                            capital += current_trade["capital_allocated"] + current_trade["pnl"]
                            trades.append(current_trade)
                            is_in_trade = False
                            continue
                            
                    # Evaluate Stops and Targets
                    original_sl = active_cfg.get("stop", {}).get("trig_offset", 0.0)
                    sl_pct = current_trade.get("current_stop_pct", original_sl)
                    has_sl = "current_stop_pct" in current_trade or original_sl > 0
                    
                    original_tgt = active_cfg.get("target", {}).get("trig_offset", 0.0)
                    tgt_pct = current_trade.get("current_tgt_pct", original_tgt)
                    has_tgt = "current_tgt_pct" in current_trade or original_tgt > 0
                    
                    stop_hit = False
                    target_hit = False
                    
                    if trade_type == "sell":
                        # Short trade: stop hit if price goes UP, target hit if price goes DOWN
                        if has_sl and active_ltp >= active_entry * (1 + (sl_pct / 100.0)):
                            stop_hit = True
                        if has_tgt and active_ltp <= active_entry * (1 - (tgt_pct / 100.0)):
                            target_hit = True
                    else:
                        # Long trade: stop hit if price goes DOWN, target hit if price goes UP
                        if has_sl and active_ltp <= active_entry * (1 - (sl_pct / 100.0)):
                            stop_hit = True
                        if has_tgt and active_ltp >= active_entry * (1 + (tgt_pct / 100.0)):
                            target_hit = True
                            
                    if stop_hit:
                        reason = "Trailing Stop Hit" if "current_stop_pct" in current_trade else "Stop Loss Hit"
                        self._close_trade(current_trade, ts, ce_ltp, pe_ltp, reason, capital, log_event)
                        capital += current_trade["capital_allocated"] + current_trade["pnl"]
                        trades.append(current_trade)
                        is_in_trade = False
                        continue
                        
                    if target_hit:
                        self._close_trade(current_trade, ts, ce_ltp, pe_ltp, "Target Hit", capital, log_event)
                        capital += current_trade["capital_allocated"] + current_trade["pnl"]
                        trades.append(current_trade)
                        is_in_trade = False
                        continue
                        
                    # Evaluate Trailing
                    pnl_pct = (current_trade["high_pnl"] / active_entry) * 100.0
                    
                    # Trailing Stop
                    trailing_cfg = active_cfg.get("stop", {}).get("trailing", {})
                    if trailing_cfg.get("enabled"):
                        trail_by = trailing_cfg.get("trail_by", 5.0)
                        act_offset = trailing_cfg.get("activation_offset", 0.0)
                        
                        if pnl_pct >= act_offset:
                            new_sl = -(pnl_pct - trail_by)
                            if "current_stop_pct" not in current_trade or new_sl < current_trade["current_stop_pct"]:
                                # Ensure we don't widen the stop beyond the original
                                if new_sl < original_sl:
                                    current_trade["current_stop_pct"] = new_sl
                                    log_event(ts, f"Trailing Stop moved to {new_sl:.2f}% (High PnL: {pnl_pct:.2f}%)")
                                    
                    # Trailing Target
                    tgt_trailing_cfg = active_cfg.get("target", {}).get("trailing", {})
                    if tgt_trailing_cfg.get("enabled"):
                        tgt_trail_by = tgt_trailing_cfg.get("trail_by", 5.0)
                        tgt_act_offset = tgt_trailing_cfg.get("activation_offset", 0.0)
                        
                        if pnl_pct >= tgt_act_offset:
                            # Push target further away
                            new_tgt = pnl_pct + tgt_trail_by
                            if "current_tgt_pct" not in current_trade or new_tgt > current_trade["current_tgt_pct"]:
                                current_trade["current_tgt_pct"] = new_tgt
                                log_event(ts, f"Trailing Target pushed to {new_tgt:.2f}% (High PnL: {pnl_pct:.2f}%)")
                        
                # End of day square off
                if dt.hour == 15 and dt.minute >= 15:
                    if ce_bar and pe_bar:
                        self._close_trade(current_trade, ts, ce_bar["close"], pe_bar["close"], "EOD Exit", capital, log_event)
                    else:
                        self._close_trade(current_trade, ts, current_trade["ce_entry"], current_trade["pe_entry"], "EOD Exit (No Data)", capital, log_event)
                    capital += current_trade["capital_allocated"] + current_trade["pnl"]
                    trades.append(current_trade)
                    is_in_trade = False
                    continue
                
            if not is_in_trade:
                if dt.hour < 9 or (dt.hour == 9 and dt.minute < 20) or (dt.hour == 15 and dt.minute > 10):
                    continue
                    
                eligible_prog = None
                if is_group:
                    eligible_prog = next((p for p in programs_to_test if p["config"].get("target_regime") == regime and p["config"].get("execution_mode") in ("sentinel", "autonomous_sentinel")), None)
                else:
                    eligible_prog = programs_to_test[0]
                    
                if not eligible_prog:
                    continue
                    
                cfg = eligible_prog["config"]
                    
                min_days = cfg.get("min_working_days_to_expiry", 0)
                # In backtesting, we only have current live expiries in script_master.
                # So we must manually use next_expiry against the simulated date.
                expiry = self.script_master.next_expiry(index_id, min_working_days=min_days, holidays=set(), today=dt.date())
                
                if not expiry:
                    continue
                    
                # Strict check: If the Tradejini API only gave us a far-out expiry (because past ones are gone), 
                # do NOT trade it if it's more than 12 calendar days away (assuming weekly cycle + min_dte buffer).
                # This prevents the backtest from buying 25-Aug options on 03-Aug, but allows starting on 13-Aug.
                if (expiry - dt.date()).days > min_days + 10:
                    continue
                    
                pair = self.script_master.strike_pair_at_offset(index_id, expiry, ltp, 0)
                if not pair:
                    continue
                    
                ce_opt, pe_opt = pair
                try:
                    ce_bar = await get_option_bar(ce_opt.id, ts, dt)
                    pe_bar = await get_option_bar(pe_opt.id, ts, dt)
                except Exception as e:
                    log.warning(f"Failed to fetch option bars for {dt}: {e}")
                    continue
                    
                if not ce_bar or not pe_bar:
                    continue
                    
                expiry_dt = datetime.combine(expiry, datetime.min.time())
                expiry_ts = expiry_dt.timestamp() + (15.5 * 3600)
                dte = max(0.001, (expiry_ts - ts) / (365 * 24 * 3600))
                
                ce_greeks = compute_greeks(ltp, float(ce_opt.strike), dte, 0.06, ce_bar["close"], "CE")
                pe_greeks = compute_greeks(ltp, float(pe_opt.strike), dte, 0.06, pe_bar["close"], "PE")
                
                iv_tracker["CE"]["low"] = min(iv_tracker["CE"]["low"], ce_greeks["iv"])
                iv_tracker["CE"]["high"] = max(iv_tracker["CE"]["high"], ce_greeks["iv"])
                iv_tracker["PE"]["low"] = min(iv_tracker["PE"]["low"], pe_greeks["iv"])
                iv_tracker["PE"]["high"] = max(iv_tracker["PE"]["high"], pe_greeks["iv"])
                
                ce_greeks["lowiv"] = iv_tracker["CE"]["low"]
                ce_greeks["highiv"] = iv_tracker["CE"]["high"]
                pe_greeks["lowiv"] = iv_tracker["PE"]["low"]
                pe_greeks["highiv"] = iv_tracker["PE"]["high"]
                
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
                        cfg["trade_type"] = "sell"
                        cfg["required_margin_per_lot"] = 130000.0
                        cfg["stop"] = {"offset_mode": "percent", "trig_offset": 15.0, "limit_offset": 0.0, "trailing": {"enabled": False}}
                        cfg["target"] = {"offset_mode": "percent", "trig_offset": 40.0, "limit_offset": 0.0, "trailing": {"enabled": True, "trail_by": 10.0, "activation_offset": 30.0}}

                entry_cfg = cfg.get("entry_signals", {})
                entry_cfg["enabled"] = True
                
                # Check ORB if entry_mode is signal_single_leg
                signal_leg = None
                if cfg.get("entry_mode") == "signal_single_leg":
                    orb_minutes = cfg.get("orb_duration_minutes", 15)
                    # Market opens at 9:15, ORB ends at 9:15 + orb_minutes
                    orb_end_hour = 9 + (15 + orb_minutes) // 60
                    orb_end_minute = (15 + orb_minutes) % 60
                    
                    if dt.hour < orb_end_hour or (dt.hour == orb_end_hour and dt.minute <= orb_end_minute):
                        orb_state["high"] = max(orb_state["high"], ltp)
                        orb_state["low"] = min(orb_state["low"], ltp)
                        continue  # Wait for ORB to form
                    else:
                        if not orb_state["triggered"]:
                            if ltp > orb_state["high"]:
                                orb_state["triggered"] = True
                                orb_state["leg"] = "CE"
                            elif ltp < orb_state["low"]:
                                orb_state["triggered"] = True
                                orb_state["leg"] = "PE"
                                
                        if not orb_state["triggered"]:
                            continue  # Wait for breakout
                            
                        signal_leg = orb_state["leg"]
                
                allowed, reason, greeks_unverifiable = evaluate_entry(
                    entry_cfg,
                    index_snapshot=bar,
                    ce_snapshot=ce_bar,
                    pe_snapshot=pe_bar,
                    ce_greeks=ce_greeks,
                    pe_greeks=pe_greeks,
                    vix_ltp=15.0,
                    vix_history=[],
                    index_history=index_bars[:i],
                    execution_mode=cfg.get("execution_mode", "manual_config"),
                    target_regime=cfg.get("target_regime", "ANY"),
                    active_regime=regime
                )
                
                if allowed:
                    trade_type = cfg.get("trade_type", "buy")
                    
                    # Determine effective price and qty logic based on entry_mode
                    eff_price = 0.0
                    entry_mode = cfg.get("entry_mode", "auto_pair")
                    
                    if entry_mode == "signal_single_leg":
                        if signal_leg == "CE":
                            eff_price = ce_bar["close"]
                        elif signal_leg == "PE":
                            eff_price = pe_bar["close"]
                        else:
                            continue  # No Entry: signal_leg not determined
                    elif entry_mode == "auto_pair":
                        eff_price = ce_bar["close"] + pe_bar["close"]
                    else:
                        continue  # No Entry: unknown entry_mode
                        
                    qty = ce_opt.lot * 1
                    if cfg.get("sizing_mode") == "capital":
                        allocated = capital * 0.90
                        if trade_type == "sell":
                            margin_per_lot = cfg.get("required_margin_per_lot", 130000.0)
                            qty = int(allocated / margin_per_lot) * ce_opt.lot if margin_per_lot > 0 else ce_opt.lot
                        else:
                            qty = int(allocated / eff_price) // ce_opt.lot * ce_opt.lot if eff_price > 0 else ce_opt.lot
                    
                    if qty == 0:
                        continue

                    cost = eff_price * qty
                    margin_req = (qty / ce_opt.lot) * cfg.get("required_margin_per_lot", 130000.0) if trade_type == "sell" else cost
                    
                    if margin_req > capital:
                        continue
                        
                    is_in_trade = True
                    trade_type = cfg.get("trade_type", "buy")
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
                        "signal_leg": signal_leg,
                        "high_pnl": 0.0
                    }
                    
                    t_regime = cfg.get("target_regime", "Trade")
                    leg_str = signal_leg if signal_leg else "Straddle"
                    log_event(ts, f"Entered {t_regime} ({trade_type} {leg_str}) - Capital Used: ₹{margin_req:.2f}")
                    
                    if trade_type == "sell":
                        capital -= margin_req
                    else:
                        capital -= cost
                
        conn = store._get_db()
        conn.execute("DELETE FROM historical_bars WHERE symbol_id = ?", (f"chronos_mock_{index_id}",))
        conn.commit()
        
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        return {
            "status": "success",
            "trades": trades,
            "events": events,
            "total_pnl": total_pnl,
            "final_capital": starting_capital + total_pnl,
            "trade_count": len(trades)
        }
        
    def _close_trade(self, trade, ts, ce_exit, pe_exit, reason, capital, log_event):
        trade["exit_ts"] = ts
        trade["ce_exit"] = ce_exit
        trade["pe_exit"] = pe_exit
        trade["close_reason"] = reason
        
        signal_leg = trade.get("signal_leg")
        ce_diff = ce_exit - trade["ce_entry"]
        pe_diff = pe_exit - trade["pe_entry"]
        
        if signal_leg == "CE":
            diff = ce_diff
        elif signal_leg == "PE":
            diff = pe_diff
        else:
            diff = ce_diff + pe_diff
            
        if trade.get("trade_type") == "sell":
            trade["pnl"] = -diff * trade.get("qty", 0)
        else:
            trade["pnl"] = diff * trade.get("qty", 0)
            
        t_regime = trade["config"].get("target_regime", "Trade") if "config" in trade else "Trade"
        leg_str = signal_leg if signal_leg else "Straddle"
        log_event(ts, f"Closed {t_regime} ({trade.get('trade_type', 'buy')} {leg_str}) due to {reason}", pnl=trade["pnl"])


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
