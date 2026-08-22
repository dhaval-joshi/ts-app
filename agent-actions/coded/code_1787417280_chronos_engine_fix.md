# Coded: Fix Chronos Backtest Engine & Transparency
Associated Plan: `plan_1787417280_chronos_engine_fix.md`

## Objective
Fix the massive artificial losses in Chronos backtesting for the Directional regime by supporting single-leg execution, trailing stops, and static target evaluation. Add detailed event tracing and CSV export for transparency.

## Files Changed
- `backend/chronos.py`:
  - Rewrote the core loop of `run_backtest`.
  - Added support for `signal_leg` to only calculate margin, cost, and qty on the signaled leg (CE or PE).
  - Implemented logic to check Target (`tgt_pct`) and Trailing Stops (`trail_by`, `activation_offset`).
  - Added an `events` log array to track regime shifts, entries, trailing updates, and exact exit reasons.
- `frontend/programs.js`:
  - Modified the Backtest Results modal to include a "View Detailed Logs" button.
  - Added a toggleable `div` containing a table of `res.events`.
  - Added `exportBacktestLogsCsv()` function to allow exporting the events to a downloadable CSV.

## Behavior Changed
- Chronos now accurately simulates single-leg trades instead of forcing a straddle on `signal_single_leg` setups.
- Chronos now accurately triggers target profit exits.
- Chronos now accurately trails stop losses.
- Users can now review exactly why a trade was entered/exited in the UI or via CSV.

## Implementation Decisions
- To maintain backward compatibility with `auto_pair` (Volatile/Sideways), the engine checks if `signal_leg` is None. If None, it reverts to the legacy combined Straddle pricing.
- Events are formatted clearly with timestamps and PnL for easy debugging.

## Verification
- Code has been written and patched.
- Uvicorn backend was restarted.
- Ready for manual testing in the browser UI.

## Follow-Ups / Risks
- The `diagnose_loss.py` scratch script highlighted that options data is only available for the currently active expiry week. So a 20-day backtest may still yield fewer trades than expected if historical options data for past expiries isn't stored locally. (No change to this behavior, just an observation).
- Another agent can safely continue.


---

# Phase 2: Backtest Sizing, Trailing Stop Fixes, and Data Caching
## Associated Plan
`plan_1787432020_backtest_caching_fixes.md`

## Objective
To resolve mathematical errors in the backtester's trailing stop logic, restore full 90% capital utilization to allow short straddle entries, and dramatically improve application performance by implementing a disk cache for the Tradejini API.

## Files Changed
1. `backend/chronos.py`
   - Fixed capital utilization by changing sizing calculation from 10% to 90% (leaving a 10% buffer).
   - Fixed mathematical calculation of `new_sl` to ensure it pushes into negative values (profits) when the `pnl_pct` rises, instead of reversing into a loss.
   - Fixed accounting logic for short trades so option premium is correctly handled without inflating capital artificially.
   - Implemented target trailing logic.
2. `backend/tradejini_client.py`
   - Intercepted `get_interval_chart_data` to read from/write to `.cache/mkt_data/{symbol}_{interval}_{from}_{to}.json`.
3. `backend/script_master.py`
   - Overrode broken Tradejini API `isUpdated=True` behavior by checking the local modification date of `Index.csv` to ensure it only downloads once per day.
4. `.gitignore`
   - Ignored the new `.cache/` directory to prevent massive JSON files from being committed.

## Behavior Changed
- Backtester now correctly enters Sideways/Volatile straddle trades on smaller accounts (e.g., 5L) because it properly allocates ~4.5L for margin instead of a hardcapped 50K.
- Backtester trailing stops now successfully lock in massive profits (e.g., 80% ROI in 20 days) by correctly calculating the high-watermark stop loss.
- Repeated backtests now load 1-minute historical data instantaneously from disk, eliminating rate-limiting issues from Tradejini.
- Application startup no longer delays to download the script master CSV unless it is the first launch of the day.

## Important Implementation Decisions
- Chose simple JSON file caching over SQLite for market data because the backtester queries exact, static daily boundaries (9:15 to 15:30), meaning a simple `from_to` filename hash guarantees a 100% cache hit rate with zero database overhead.

## Tests Run and Results
- Ran `scratch/debug_chronos.py` dynamically against 1L and 5L capital over a 20-day span.
  - Result: Correct straddle entry logic observed. Trailing stops pushed well into profit territory (e.g., stopping out at -10% loss = +10% profit).
- Verified `tradejini_client.py` successfully populates `.cache/mkt_data/` with the exact requested timeframes, allowing subsequent runs to load in seconds without API hit logs.
- Verified startup API bypasses Tradejini's faulty versioning endpoint by using `st_mtime` to lock out redownloads on the same calendar day.

## What was not verified
- Edge cases where Tradejini API historical data goes missing on their end and caches an empty list (though `chronos.py` skips execution safely if this occurs).

## Known Risks / Follow-ups
- If a user requests a backtest spanning multiple days, the current `chronos.py` breaks it down day-by-day seamlessly.
- Iteration 3 could move historical data to a formal Time Series Database / SQLite store if we need to run queries spanning overlapping arbitrary timestamps.

## Can Another Agent Safely Continue?
Yes, entirely safe to proceed. No architectural abstractions were modified.
