import time
import logging
from typing import Dict, List, Optional
from datetime import datetime
import math

from . import store, clock
from .tradejini_client import TradejiniClient

log = logging.getLogger("tradejini.regime")

STATE_SIDEWAYS = "SIDEWAYS"
STATE_DIRECTIONAL = "DIRECTIONAL"
STATE_VOLATILE = "VOLATILE"
STATE_UNKNOWN = "UNKNOWN"

class RegimeClassifier:
    """
    Acts as the Sentinel. Continuously monitors the market environment
    and outputs the current regime state.
    """
    def __init__(self, client: TradejiniClient):
        self.client = client
        self.regimes: Dict[str, dict] = {} # symbol_id -> {"state": STATE, "adx": float, "atr": float, "timestamp": int}
        
    async def bootstrap_symbol(self, symbol_id: str, days_back: int = 3):
        """
        Fetches the missing historical 1-minute bars from the broker to prime the indicators.
        Uses SQLite to cache historical bars, minimizing broker API calls.
        """
        latest_ts = store.get_latest_historical_bar_timestamp(symbol_id)
        current_ts = int(time.time())
        
        # If we have no data, fetch the last N days
        if latest_ts == 0:
            from_ts = current_ts - (days_back * 24 * 3600)
            log.info("Sentinel: No historical data found for %s. Bootstrapping last %d days.", symbol_id, days_back)
        else:
            from_ts = latest_ts
            # If the gap is less than a minute, we're up to date
            if current_ts - from_ts < 60:
                log.info("Sentinel: Historical data for %s is up to date.", symbol_id)
                self._compute_regime(symbol_id)
                return
            log.info("Sentinel: Fetching delta for %s from %s to %s", symbol_id, from_ts, current_ts)

        try:
            resp = await self.client.get_interval_chart_data(symbol_id, "1", from_ts, current_ts)
            raw_bars = resp.get("bars", []) if isinstance(resp, dict) else resp
            if not raw_bars and isinstance(resp, dict):
                raw_bars = resp.get("chartData", [])
                
            new_bars = []
            for row in raw_bars:
                if isinstance(row, list) and len(row) >= 5:
                    ts = int(row[0] / 1000) if row[0] > 1e11 else int(row[0])
                    if ts > latest_ts:
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
                    if ts > latest_ts:
                        new_bars.append({
                            "timestamp": ts,
                            "open": float(row.get("o") or row.get("open", 0)),
                            "high": float(row.get("h") or row.get("high", 0)),
                            "low": float(row.get("l") or row.get("low", 0)),
                            "close": float(row.get("c") or row.get("close", 0)),
                            "volume": float(row.get("v") or row.get("volume", 0)),
                        })
            
            if new_bars:
                store.save_historical_bars(symbol_id, new_bars)
                log.info("Sentinel: Saved %d new historical bars for %s.", len(new_bars), symbol_id)
                
            self._compute_regime(symbol_id)
            
        except Exception as e:
            log.error("Sentinel: Failed to fetch historical data for %s: %s", symbol_id, e)
            self.regimes[symbol_id] = {"state": STATE_UNKNOWN, "adx": 0.0, "atr": 0.0, "timestamp": current_ts}
            from .notifier import send_telegram_alert
            import asyncio
            asyncio.create_task(send_telegram_alert(
                f"⚠️ <b>SENTINEL BLIND</b> ⚠️\n\nFailed to fetch historical data for {symbol_id}. "
                f"Sentinel entry gates will be halted until data is restored."
            ))

    def update_with_live_tick(self, symbol_id: str, ltp: float):
        """
        Takes a live tick, updates the current minute bar, and re-computes the regime if the minute rolled over.
        """
        current_ts = int(time.time())
        current_minute = current_ts - (current_ts % 60)
        
        latest_ts = store.get_latest_historical_bar_timestamp(symbol_id)
        if latest_ts == 0:
            return  # Not bootstrapped yet
            
        if current_minute > latest_ts:
            # We crossed into a new minute. We should ideally fetch the final closed bar from the broker,
            # but for now, we can synthesize it from ticks.
            # Synthesizing is complex without keeping all ticks, so we will trigger an async fetch
            # for the delta to keep the DB perfectly accurate.
            # To avoid spamming, we just rely on `bootstrap_symbol` being called periodically, OR
            # we can append a dummy live bar.
            pass
            
        # For true live regime computation, we need the live ADX. We can compute it on the fly by taking the
        # historical bars + the current live price.
        self._compute_regime(symbol_id, live_ltp=ltp)

    def _compute_regime(self, symbol_id: str, live_ltp: Optional[float] = None):
        """Computes ADX and ATR using Wilder's Smoothing (RMA) to determine the regime."""
        bars = store.load_historical_bars(symbol_id)
        if len(bars) < 30:
            self.regimes[symbol_id] = {"state": STATE_UNKNOWN, "adx": 0.0, "atr": 0.0, "timestamp": int(time.time())}
            return
            
        # Append live ltp as the current bar if provided
        if live_ltp is not None:
            last_bar = bars[-1]
            current_ts = int(time.time())
            current_minute = current_ts - (current_ts % 60)
            if current_minute > last_bar["timestamp"]:
                # Create a temporary new bar
                bars.append({
                    "timestamp": current_minute,
                    "open": live_ltp,
                    "high": live_ltp,
                    "low": live_ltp,
                    "close": live_ltp,
                    "volume": 0.0
                })
            else:
                # Update the last bar's high/low/close
                bars[-1] = last_bar.copy()
                bars[-1]["high"] = max(last_bar["high"], live_ltp)
                bars[-1]["low"] = min(last_bar["low"], live_ltp)
                bars[-1]["close"] = live_ltp

        period = 14
        
        # Calculate TR, +DM, -DM
        tr_list, plus_dm_list, minus_dm_list = [], [], []
        for i in range(1, len(bars)):
            high = bars[i]["high"]
            low = bars[i]["low"]
            prev_close = bars[i-1]["close"]
            prev_high = bars[i-1]["high"]
            prev_low = bars[i-1]["low"]
            
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            up_move = high - prev_high
            down_move = prev_low - low
            
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
            
            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)
            
        # Wilder's Smoothing (RMA)
        def rma(series, period):
            if len(series) < period: return []
            res = [sum(series[:period]) / period]
            for val in series[period:]:
                res.append((res[-1] * (period - 1) + val) / period)
            return res
            
        tr_rma = rma(tr_list, period)
        plus_dm_rma = rma(plus_dm_list, period)
        minus_dm_rma = rma(minus_dm_list, period)
        
        if not tr_rma:
            return
            
        # Calculate +DI, -DI, DX, ADX
        dx_list = []
        for tr, pdm, mdm in zip(tr_rma, plus_dm_rma, minus_dm_rma):
            if tr == 0:
                dx_list.append(0.0)
                continue
            plus_di = 100 * (pdm / tr)
            minus_di = 100 * (mdm / tr)
            di_sum = plus_di + minus_di
            if di_sum == 0:
                dx_list.append(0.0)
            else:
                dx_list.append(100 * abs(plus_di - minus_di) / di_sum)
                
        adx_list = rma(dx_list, period)
        if not adx_list:
            return
            
        current_adx = adx_list[-1]
        current_atr = tr_rma[-1]
        
        # We also need VIX or ATR relative expansion to detect VOLATILE state.
        # Simple heuristic: if ATR is significantly higher than its 14-period average
        atr_sma = sum(tr_rma[-14:]) / min(14, len(tr_rma))
        is_volatile = current_atr > (atr_sma * 1.5)
        
        old_state = self.regimes.get(symbol_id, {}).get("state", STATE_UNKNOWN)
        
        if is_volatile:
            new_state = STATE_VOLATILE
        elif current_adx > 25:
            new_state = STATE_DIRECTIONAL
        else:
            new_state = STATE_SIDEWAYS
            
        self.regimes[symbol_id] = {
            "state": new_state,
            "adx": current_adx,
            "atr": current_atr,
            "timestamp": int(time.time())
        }
        
        if new_state != old_state and old_state != STATE_UNKNOWN:
            msg = f"Sentinel shifted {symbol_id} regime: {old_state} -> {new_state} (ADX: {current_adx:.2f}, ATR: {current_atr:.2f})"
            log.info(msg)
            from .failure_log import log_failure
            log_failure(category="regime_change", order_id=None, program_id=None, message=msg)

    def get_current_regime(self, symbol_id: str) -> dict:
        return self.regimes.get(symbol_id, {"state": STATE_UNKNOWN, "adx": 0.0, "atr": 0.0, "timestamp": 0})
