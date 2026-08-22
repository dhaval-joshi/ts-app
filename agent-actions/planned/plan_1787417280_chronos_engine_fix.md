# Plan: Fix Chronos Backtest Engine & Add Transparency

## Objective / Problem
The Chronos backtest engine currently has two major flaws that cause massive artificial losses for Directional (trend-following) strategies:
1. **Ignored Single-Leg Entries**: `chronos.py` forces all entries into a Straddle (buying both CE and PE), even when `evaluate_entry` returns a specific `signal_leg` (e.g. `CE` for bullish).
2. **Missing Target & Trailing Logic**: The simulator does not evaluate targets or trailing stops. It only evaluates a static stop loss, meaning winning trades eventually revert and hit the stop or close at End of Day for a loss.
3. **No Transparency**: The backtester is a black box, making it impossible to see when regime shifts, trailing stop updates, or specific exits occur.

## Verified Current Behavior
- `sentinel_orchestrator.py` correctly assigns `signal_single_leg` to the Directional regime.
- `chronos.py` (around line 239) calls `evaluate_entry` and explicitly discards the `signal_leg` return value.
- `chronos.py` (around line 125) checks stops using a hardcoded `sl_pct` against the combined entry price (`comb_entry = ce_entry + pe_entry`), ignoring targets entirely.

## Proposed Design
1. **Engine Core Fixes (`backend/chronos.py`)**:
   - Capture `signal_leg` ("CE", "PE", or `None`) from `evaluate_entry`.
   - Calculate `qty`, `cost`, and `margin_req` using only the price of the active leg. (If `None`, use both).
   - Evaluate Target percentage (`target.trig_offset`). Close trade if target hit.
   - Implement Trailing Stop logic using `trailing.enabled`, tracking the highest/lowest price to raise the stop loss based on `trail_by` rules.
2. **Transparency & Logging**:
   - Add an `event_log` list to the engine.
   - Append structured events during the tick loop for regime shifts, entries, trailing updates, and exits.
   - Return the log in the JSON response.
3. **Frontend UI (`frontend/programs.js`)**:
   - Add a "View Detailed Logs" button to the results dialog.
   - Display a tabular log of events.
   - Add an "Export to CSV" button to download the event log.

## Files/Components Affected
- `backend/chronos.py` (Engine Entry & Exit Logic)
- `frontend/programs.js` (Backtest Results UI & CSV Export)

## Safety Implications
- Changes are strictly confined to the `chronos.py` backtester and UI. No changes to live order management or broker integration.
- The `AGENTS.md` constraint "Never execute a real broker order" is upheld because Chronos strictly uses mocked local historical data.

## Verification Plan
1. Run a Chronos backtest via the UI.
2. Verify the "View Detailed Logs" table appears.
3. Verify Directional trades only enter a single leg (CE or PE).
4. Verify exits occur for Target Hit, Trailing Stop Hit, or Regime Shift.
5. Verify CSV export downloads correctly.


---

# Phase 2: Backtest Sizing, Trailing Stop Fixes, and Data Caching
## Objective / Problem
1. **Capital Under-utilization**: The backtester was incorrectly allocating exactly 10% of total capital per trade, leading to an inability to enter short straddles (which require ~1.3L margin per lot) on 5L accounts.
2. **Profit Evaporation**: The backtester's Trailing Stop logic was flawed. When a trade moved into profit, the trailing stop formula pushed the stop into negative territory (e.g. at 15% profit, the stop became a 3% loss) instead of locking in the profit. This caused trades to reverse and exit for pennies instead of capturing the run.
3. **Missing Target Trailing**: The backtester did not implement Target Trailing, making it inconsistent with the live `order_manager.py`.
4. **API Rate Limiting / Slow Startups**: The application hit Tradejini API rate limits because it downloaded 1-minute historical data over and over during backtests, and re-downloaded the script master CSV on every startup.

## Verified Current Behavior
- `chronos.py` uses `capital * 0.10` for allocation.
- `chronos.py` calculates `new_sl = original_sl - pnl_pct + trail_by`.
- `tradejini_client.py` fetches data live from the API for every call.
- `script_master.py` checks the API for updates every startup, but Tradejini's API incorrectly returns `isUpdated=True` every time, forcing a daily CSV redownload.

## Proposed Design
1. **Sizing**: Revert backtester sizing allocation to `capital * 0.90` (preserving a 10% safety buffer).
2. **Trailing Stop Fix**: Change `chronos.py` trailing stop calculation to correctly track the high watermark: `new_sl = -(pnl_pct - trail_by)`.
3. **Target Trailing**: Port the target trailing logic into `chronos.py` so the target pushes further away dynamically when the trade is in profit.
4. **Accounting Fix**: Stop double-crediting short option premium when closing a sell trade in `chronos.py`.
5. **Data Caching**: 
   - Add a file-based disk cache (`.cache/mkt_data/*.json`) to `TradejiniClient.get_interval_chart_data`.
   - Add a date-based check in `ScriptMaster.refresh()` to only download the symbol master CSVs once per day, bypassing the broken `isUpdated` API response.

## Files / Components Affected
- `backend/chronos.py`
- `backend/tradejini_client.py`
- `backend/script_master.py`
- `.gitignore`

## Safety Implications
- Modifying trailing stop logic in the backtester could lead to overly optimistic results if implemented incorrectly, but this change aligns it mathematically with the live `order_manager.py`.
- Disk caching could cause stale data if not keyed correctly. The keying mechanism for interval chart data must include the exact timestamp bounds.

## Alternatives / Trade-offs
- Instead of file caching for historical data, a SQLite timeseries database could be used. We decided against this for now (deferring to Iteration 3) because file caching provides instantaneous lookup matching the backtester's exact query patterns without the overhead of row insertion.

## Verification Plan
- Run `debug_chronos.py` with 5L capital over a 20-day period.
- Verify short straddles are entered.
- Verify trailing stops trigger *in profit* (negative loss percentages in logs).
- Verify `.cache/mkt_data` populates with JSON files.

## Open Questions / Assumptions
- Assume Tradejini does not retroactively change 1-minute historical bars for past days, making them safe to cache indefinitely.

## Explicit Scope and Non-Scope
- **In Scope**: Backtester fixes, historical data caching, script master caching.
- **Out of Scope**: Altering the live `order_manager.py` trailing behavior or tightening the default configuration parameters (which was proposed and subsequently rejected by the user).
