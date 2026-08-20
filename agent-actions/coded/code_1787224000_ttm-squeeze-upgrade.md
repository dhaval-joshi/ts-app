# True TTM Squeeze Upgrade Implementation

**Associated Plan**: `plan_1787224000_ttm-squeeze-upgrade.md`

## Objective
Upgrade the Squeeze gate from using Bollinger Bandwidth Percentile to using the True TTM Squeeze math (Bollinger Bands nested inside Keltner Channels).

## Files Changed
- `backend/indicators.py`: Updated `backfill_daily_signals` to scrape and store OHLCV components instead of just `close`.
- `backend/program_manager.py`: Renamed `_index_close_history` to `_index_history`. Coerces legacy scalar close values into `{open: c, high: c, low: c, close: c, volume: 0.0}` to prevent breaks. Passes `index_history` (list of dicts) into `evaluate_entry`.
- `backend/entry_signals.py`: Removed Bollinger bandwidth percentile logic. Implemented True Range (ATR) calculation for Keltner Channels. A true squeeze is evaluated by comparing Bollinger band half-width vs Keltner Channel half-width.
- `backend/models.py`: Removed `max_squeeze_bandwidth_percentile` and added `require_ttm_squeeze: bool` and `squeeze_keltner_mult: float`.
- `frontend/programs.js`: Changed input from percentile number to a checkbox for `require_ttm_squeeze`.

## Behavior Changed
The Squeeze gate is now binary (is there a squeeze, yes or no). It strictly requires OHLC data to compute Average True Range (ATR). If the data is missing (or using legacy scalar floats), the ATR simplifies but continues to function without throwing exceptions. The front-end now exposes a simple "Require TTM Squeeze" checkbox instead of a confusing percentile input.

## Tests Run
- Ran tests in `scratch_verify.py` successfully proving a flat price action triggers `squeeze_gate = True`, and a strongly trending market evaluates to `squeeze_gate = False`.

## Known Risks
Existing saved configurations using `max_squeeze_bandwidth_percentile` will simply drop that requirement upon next load due to the schema evolution. They will require manual intervention to opt-in to the new True TTM Squeeze if they wish to re-enable it.
