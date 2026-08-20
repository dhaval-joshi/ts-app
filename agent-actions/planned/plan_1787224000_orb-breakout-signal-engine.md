# Plan: ORB Breakout Signal Engine

- **Objective/Problem**: Currently, the system executes trades using time-based passive scheduling with negative filters (`auto_pair`). To generate directional alpha, the system needs an active event-driven signal engine to trigger trades at statistically advantageous moments, specifically using an Opening Range Breakout (ORB) combined with volatility squeeze confirmation. Additionally, daily signals like VIX and Squeeze Bandwidth currently wait 20 days to become active; they need instant historical backfilling.
- **Verified Current Behavior**:
  - `entry_signals.py` relies on `store.load_signal_snapshot` which only populates a daily data point if the app is left running.
  - *Tradejini API Verification:* The `/api/mkt-data/chart/interval-data` endpoint successfully returns 30 days of 1-minute bars (~7,000 bars), but rejects `1D` or `1440` intervals. We will fetch 1-minute bars and aggregate the daily closes locally.
  - `ProgramManager._tick_one` triggers cycles strictly based on time polling (e.g. 9:15).
  - "Entry Mode" in UI supports `auto_pair` and `manual_single_leg`.
- **Proposed Design**:
  1. **Historical Backfill**: Add a startup routine (via `IndicatorService` or a separate script) that fetches the last 30 days of daily data for the underlying index and India VIX using Tradejini's historical APIs, instantly priming the squeeze and VIX gates.
  2. **Signal Engine (ORB + Squeeze)**: A new daemon (`backend/signal_engine.py`) that subscribes to 1-minute bars. It records the first `X` minutes' absolute high and low. If price crosses above ORB High (CE) or below ORB Low (PE), it passes through existing `entry_signals.py` gates. If allowed, it emits a trigger.
  3. **Program Manager Integration**: `start_signal_single_leg_cycle(program_id, leg, indicators)` handles sizing based on the configured mode:
     - *Capital Based Entry:* Allocates the entire program capital directly to the single breakout direction (using the fixed slippage points buffer), rather than splitting it as it would for a dual-leg setup.
     - *Lot Based Entry:* Uses the configured lot size for the breakout direction, explicitly subjected to the 10% margin safety buffer before entry.
  4. **Smart Frontend UI**: Add `signal_single_leg` to the UI. When selected, show `orb_duration_minutes`, auto-populate prop-desk standards (e.g. Squeeze Bollinger Period 20, Max Bandwidth 30, Max VIX 80), and hide non-applicable gates like `Session Range Gate`.
  5. **Exit/Trailing Verification**: *No changes required.* The `OrderManager` already applies the `StrategyConfig` (Trailing Stop Loss, Exit Confirmation Windows, Targets, etc.) directly to the individual *order object*, not the cycle. If our ORB signal enters a single CE leg, that leg inherits all the rigorous timeframe aggregation, trailing ratchets, and safeguards exactly as an `auto_pair` leg would.
- **Files/Components Likely Affected**:
  - `backend/signal_engine.py` (NEW)
  - `backend/indicators.py` (Historical data fetching for daily closes)
  - `backend/program_manager.py` (Adding `start_signal_single_leg_cycle` and signal listener)
  - `backend/models.py` (ProgramConfig schema additions for ORB)
  - `frontend/programs.js` & `frontend/index.html` (Dynamic UI and defaults)
- **Safety Implications**: 
  - Signals must perfectly respect existing max positions, daily loss caps, and consecutive loss limits.
  - The new trigger will reuse the existing strict logic of `evaluate_entry` in `entry_signals.py` to ensure all structural safety nets (VIX ceilings, etc.) are still honored.
- **Alternatives/Trade-offs**: 
  - *Alternative:* VWAP bounces or EMA Crossovers. *Trade-off:* ORB is selected because option premiums require violent immediate moves to outpace theta decay.
  - *Alternative:* Waiting 20 days for daily snapshots. *Trade-off:* Backfilling introduces API limits on startup but avoids an unacceptable 20-day dry period.
- **Verification Plan**:
  - Verify backfill logic correctly populates `data/signal_history` with 30 days of valid Index/VIX closes, properly aggregated from the 1-minute API response.
  - Verify the ORB high/low calculation tracks the exact bounds of the first N minutes.
  - Verify that a mocked tick crossing the ORB threshold successfully triggers `start_signal_single_leg_cycle` and obeys sizing rules.
- **Explicit Scope and Non-Scope**:
  - *In-Scope:* Building the ORB engine, backfilling daily data, and dynamic UI defaults.
  - *Non-Scope:* Building a local backtesting engine. (Deferred for a future task).
