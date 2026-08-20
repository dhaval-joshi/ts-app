import asyncio
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime

from .tradejini_client import TradejiniClient

log = logging.getLogger("tradejini.indicators")


class IndicatorService:
    """
    Manages fetching historical data, tracking live ticks to form 1-min bars,
    and computing basic indicators like EMA and RSI.
    Designed to be lightweight and stateless across restarts, bootstrapping
    from the broker's historical data API.
    """
    def __init__(self, client: TradejiniClient):
        self.client = client
        self.bars_by_symbol: Dict[str, List[Dict]] = {}  # symbol -> list of dicts with open, high, low, close, volume, timestamp
        self._lock = asyncio.Lock()
        
    async def bootstrap_symbol(self, symbol_id: str, days_back: int = 5):
        """Fetches historical 1-minute data for the symbol to prime indicators."""
        async with self._lock:
            if symbol_id in self.bars_by_symbol and len(self.bars_by_symbol[symbol_id]) > 0:
                return # Already bootstrapped
            
            # Fetch data from broker
            to_ts = int(time.time())
            from_ts = to_ts - (days_back * 24 * 3600)
            
            try:
                # Interval is "1" for 1-minute bars
                resp = await self.client.get_interval_chart_data(symbol_id, "1", from_ts, to_ts)
                
                # Tradejini returns a dict with 'bars' key containing the list of bars
                raw_bars = resp.get("bars", []) if isinstance(resp, dict) else resp
                
                # Fallback to chartData in case the structure changes
                if not raw_bars and isinstance(resp, dict):
                    raw_bars = resp.get("chartData", [])
                
                bars = []
                for row in raw_bars:
                    # typical tradejini response structure might be [timestamp, open, high, low, close, volume]
                    # Since I don't know the exact structure, assuming typical array format, if it's a dict I handle it too.
                    if isinstance(row, list) and len(row) >= 5:
                        ts = int(row[0] / 1000) if row[0] > 1e11 else int(row[0])
                        bars.append({
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
                        bars.append({
                            "timestamp": ts,
                            "open": float(row.get("o") or row.get("open", 0)),
                            "high": float(row.get("h") or row.get("high", 0)),
                            "low": float(row.get("l") or row.get("low", 0)),
                            "close": float(row.get("c") or row.get("close", 0)),
                            "volume": float(row.get("v") or row.get("volume", 0)),
                        })
                
                # Sort by timestamp just in case
                bars.sort(key=lambda x: x["timestamp"])
                self.bars_by_symbol[symbol_id] = bars
                log.info("Bootstrapped %d historical bars for %s", len(bars), symbol_id)
            except Exception as e:
                log.error("Failed to bootstrap historical data for %s: %s", symbol_id, e)

    async def backfill_daily_signals(self, symbol_ids: List[str], days_back: int = 30):
        """Fetches 1-minute historical data for symbols (e.g. VIX, NIFTY) over the last N days,
        aggregates them to daily OHLCV, and writes them into store.signal_history so that
        the Squeeze and VIX percentile gates are primed instantly without waiting days."""
        from . import clock, store

        for symbol_id in symbol_ids:
            try:
                to_ts = int(time.time())
                from_ts = to_ts - (days_back * 24 * 3600)
                
                resp = await self.client.get_interval_chart_data(symbol_id, "1", from_ts, to_ts)
                raw_bars = resp.get("bars", []) if isinstance(resp, dict) else resp
                if not raw_bars and isinstance(resp, dict):
                    raw_bars = resp.get("chartData", [])

                daily_bars = {}  # date_str -> {"open", "high", "low", "close", "volume"}
                for row in raw_bars:
                    # extract timestamp and OHLCV
                    ts = 0
                    o, h, l, c, v = 0.0, 0.0, 0.0, 0.0, 0.0
                    if isinstance(row, list) and len(row) >= 5:
                        ts = int(row[0] / 1000) if row[0] > 1e11 else int(row[0])
                        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
                        v = float(row[5]) if len(row) > 5 else 0.0
                    elif isinstance(row, dict):
                        raw_ts = row.get("t") or row.get("timestamp") or 0
                        ts = int(raw_ts / 1000) if raw_ts > 1e11 else int(raw_ts)
                        o = float(row.get("o") or row.get("open", 0))
                        h = float(row.get("h") or row.get("high", 0))
                        l = float(row.get("l") or row.get("low", 0))
                        c = float(row.get("c") or row.get("close", 0))
                        v = float(row.get("v") or row.get("volume", 0))
                    
                    if ts > 0:
                        dt = datetime.fromtimestamp(ts, tz=clock.IST)
                        date_str = dt.date().isoformat()
                        
                        if date_str not in daily_bars:
                            daily_bars[date_str] = {"open": o, "high": h, "low": l, "close": c, "volume": v}
                        else:
                            daily_bars[date_str]["high"] = max(daily_bars[date_str]["high"], h)
                            daily_bars[date_str]["low"] = min(daily_bars[date_str]["low"], l)
                            daily_bars[date_str]["close"] = c
                            daily_bars[date_str]["volume"] += v

                # Now merge into the store
                is_vix = symbol_id == "IDX_-15_NSE"
                for date_str, bar in daily_bars.items():
                    snap = store.load_signal_snapshot(date_str) or {"date": date_str, "vix_close_seen": None, "index_closes": {}}
                    snap.setdefault("index_closes", {})
                    changed = False
                    if is_vix:
                        # VIX percentiles only need the close
                        if snap.get("vix_close_seen") != bar["close"]:
                            snap["vix_close_seen"] = bar["close"]
                            changed = True
                    else:
                        if snap["index_closes"].get(symbol_id) != bar:
                            snap["index_closes"][symbol_id] = bar
                            changed = True
                    if changed:
                        store.save_signal_snapshot(date_str, snap)

                log.info("Backfilled %d daily bars for %s", len(daily_bars), symbol_id)
            except Exception as e:
                log.error("Failed to backfill daily signals for %s: %s", symbol_id, e)

    def handle_l1_tick(self, data: dict):
        """Process a live tick, updating the current 1-min bar or opening a new one."""
        symbol_id = data.get("symbol") # trading symbol token, formatted like 26000_NSE
        if not symbol_id:
            return
            
        ltp = float(data.get("ltp", 0.0))
        if ltp <= 0:
            return
            
        # NxtradStream ticks typically include timestamp, if not use current time
        # 'time' is available for OHLC but for L1 'ltt' or current time is used.
        # OrderManager handles ticks with 'ltt'. Wait, NxtradStream returns ltt as a string or timestamp?
        # NxtradStream decodes ltt via datefmt which returns a string!
        # But `tt` or `time` might not be present. Let's just use time.time() for the bar timestamp to be safe.
        ts = int(time.time())
        minute_ts = (ts // 60) * 60
        
        bars = self.bars_by_symbol.setdefault(symbol_id, [])
        
        if not bars:
            bars.append({
                "timestamp": minute_ts,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": 0.0
            })
        else:
            last_bar = bars[-1]
            if minute_ts > last_bar["timestamp"]:
                # New minute bar
                bars.append({
                    "timestamp": minute_ts,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": 0.0
                })
                # Trim memory if it gets too large (e.g. > 5000 bars)
                if len(bars) > 5000:
                    self.bars_by_symbol[symbol_id] = bars[-5000:]
            elif minute_ts == last_bar["timestamp"]:
                # Update current bar
                last_bar["high"] = max(last_bar["high"], ltp)
                last_bar["low"] = min(last_bar["low"], ltp)
                last_bar["close"] = ltp

    def get_rsi(self, symbol_id: str, periods: int = 14) -> Optional[float]:
        """Calculates standard RSI."""
        bars = self.bars_by_symbol.get(symbol_id, [])
        if len(bars) < periods + 1:
            return None
            
        changes = [bars[i]["close"] - bars[i-1]["close"] for i in range(1, len(bars))]
        
        gains = [c if c > 0 else 0.0 for c in changes]
        losses = [-c if c < 0 else 0.0 for c in changes]
        
        avg_gain = sum(gains[:periods]) / periods
        avg_loss = sum(losses[:periods]) / periods
        
        # Smoothed RS
        for i in range(periods, len(changes)):
            avg_gain = (avg_gain * (periods - 1) + gains[i]) / periods
            avg_loss = (avg_loss * (periods - 1) + losses[i]) / periods
            
        if avg_loss == 0:
            return 100.0
            
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def get_ema(self, symbol_id: str, periods: int = 14) -> Optional[float]:
        """Calculates standard EMA."""
        bars = self.bars_by_symbol.get(symbol_id, [])
        if len(bars) < periods:
            return None
            
        multiplier = 2.0 / (periods + 1.0)
        
        # SMA for first EMA point
        closes = [b["close"] for b in bars]
        ema = sum(closes[:periods]) / periods
        
        for price in closes[periods:]:
            ema = (price - ema) * multiplier + ema
            
        return ema
