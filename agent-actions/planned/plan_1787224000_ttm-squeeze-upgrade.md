# True TTM Squeeze Upgrade

## Objective
Upgrade the existing Bollinger Bandwidth Squeeze gate (which just checked if bandwidth was below a static percentile of its own history) to a definitive John Carter TTM Squeeze check. This evaluates whether Bollinger Bands are strictly contained within Keltner Channels.

## Proposed Design
1. Upgrade `backend/indicators.py` to persist OHLCV historical data instead of just Closes.
2. Update `program_manager.py` to inject `index_history` containing OHLCV dictionary blobs instead of a float array.
3. Update `entry_signals.py` to calculate True Range and Keltner Channels (using ATR).
4. Update `models.py` and `programs.js` to replace `max_squeeze_bandwidth_percentile` with a boolean `require_ttm_squeeze`.

## Safety Implications
Changes how historical signal snapshots are stored (moving from flat floats to dicts). We must gracefully handle legacy single-float values on load.

## Verification
- Unit test in `scratch_verify.py` asserting that Flat markets trigger a Squeeze and Volatile markets don't.
- Manual test of signal persistence structure.
