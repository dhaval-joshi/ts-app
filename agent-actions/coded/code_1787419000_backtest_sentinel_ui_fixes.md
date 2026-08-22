- **Associated Plan**: `agent-actions/planned/plan_1787419000_backtest_sentinel_ui_fixes.md`
- **Objective**: Fix major Chronos backtest mathematical compounding bugs, fix Sentinel UI constraints and execution modes, and reconstruct the missing Backtest Detailed Trades & CSV export UI.

# Files Changed
- `backend/chronos.py`:
  - Removed `capital += cost` for sell trades to fix massive infinite capital generation.
  - Implemented exact `next_expiry` matching on simulated `dt.date()` instead of using `datetime.now().date()`.
  - Added a strict safety cutoff: `if (expiry - dt.date()).days > min_days + 10: continue` to strictly bypass trading highly illiquid weeklies if Tradejini API cannot provide the accurate past expiry contract.
- `frontend/programs.js`:
  - Sentinel Form: Added Min DTE input constraint and Execution Mode (Paper/Live) selector.
  - Sentinel UI: Automatically injected the amber Paper styling layout matching standard programs.
  - Backtest UI: Reconstructed the entire results table to parse `res.trades` into a structured HTML grid showing Script(s), Capital Deployed, and Net PnL.
  - Backtest UI: Added `Max Draw-down` logic evaluated directly against the continuous curve peak of trades.
  - CSV Export: Built `exportBacktestTradesCsv` to allow users to directly save structured simulation trades to disk.
  - Websocket bugfix: Addressed the `[object Object]` error by explicitly pulling `.text` from the progress JSON payload.
- `backend/models.py`: Added `min_working_days_to_expiry` configuration parameter support for the DB Sentinel objects.
- `backend/main.py`: Fixed a rogue `get_programs` variable that previously crashed the app; successfully changed to `list_programs`.
- `backend/program_manager.py`: Properly propagated the `min_working_days_to_expiry` from orchestrator down to spawned children.

# Verification
1. Attempted running backtests natively in the UI.
2. Verified infinite compounding was resolved. Simulated run scaled safely from 5L to 5.11L instead of 600 Billions.
3. Verified the Backtest cleanly skips initial August dates where the 25-Aug expiry was unrealistically far away.
4. Verified Sentinel accurately reflects PAPER styling dynamically.

# Next Steps
None required; ready for next iteration or structural changes.
