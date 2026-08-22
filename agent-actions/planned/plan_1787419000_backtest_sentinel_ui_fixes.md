# Objective
Enhance the Sentinel UI to support configuration of "Min Working Days to Expiry" and apply paper-mode styling. Simultaneously, drastically improve the Chronos backtest engine to fix infinite compounding bugs, accurately handle trading invalid/far expiries, and expose a robust trades report/export UI.

# Current Behavior
- Sentinel groups cannot configure minimum DTE logic.
- Sentinel groups in paper mode look identical to live mode.
- Chronos backtesting has a massive mathematical flaw in `capital` mutation where it explicitly adds raw premium back to capital at entry, causing exponential 600-billion-rupee sizing blowouts on `SELL` trades.
- Chronos backtesting blindly trades the currently active 25-Aug expiry on 03-Aug, resulting in terrible performance because it's trading weeklies 22 days out.
- The UI renders `[object Object]` when streaming websocket progress.

# Proposed Design
1. **Sentinel UI**:
   - Add `min_working_days_to_expiry` and `mode` (Execution Mode) fields.
   - Inject amber `PAPER` styling if `mode === "paper"`.
2. **Chronos Backend**:
   - Fix capital mutation logic: remove `capital += cost` on sell trades at entry. The PnL calculation correctly evaluates the Net Profit.
   - Force backtester to run `next_expiry` dynamically based on the simulated day rather than blindly trading whatever is actively available today.
   - Enforce a strict `max_working_days_to_expiry` limit for the backtester: Skip trading entirely if the only available expiry is more than 10 days away. This mathematically drops 22-day out trades.
3. **Backtest Results UI**:
   - Calculate mathematical max drawdown dynamically.
   - Build a "Detailed Trades Log" HTML table containing Entry, Exit, Trade Type, Script(s), Capital Deployed, and PnL.
   - Provide an "Export CSV" functionality that exports this detailed breakdown.

# Safety Implications
- Sentinel mode and DTE constraints are persisted in `models.py` and correctly synchronized by `program_manager.py` to child programs.
- Chronos backtesting does not impact live systems.
- Safe `e.detail.message.text` access in JS prevents object-to-string coercion crashes.

# Verification
Run a native Chronos simulation and verify max drawdown calculations, realistic exponential compounding behavior, trades log rendering, and correct skipping of early August dates.
