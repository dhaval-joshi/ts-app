# Fix Chronos Backtest Engine & Add Transparency

I investigated why the Chronos backtests are generating such massive losses for the Directional regime. I found two critical flaws in the core engine (`chronos.py`), and per your request, we will also open the "black box" by exposing a detailed event log and CSV export capability.

*(Yes, the Smart Exit / Preemptive Close logic will remain completely active and will apply to single-leg trades perfectly!)*

## Proposed Changes

### 1. Engine Core Fixes (`backend/chronos.py`)
- **Single Leg Entries**: Update `evaluate_entry` call to capture the `signal_leg` ("CE", "PE", or `None`). Modify the `qty`, `cost`, and `margin_req` calculations to only use the price of the active leg. If `signal_leg` is "CE", it will only buy/sell the CE. If `None`, it trades the Straddle.
- **Target Logic**: If the active price reaches the target (`target.trig_offset`), close the trade with reason `target_hit`.
- **Trailing Stop Logic**: Track the highest/lowest price reached by the active leg(s). If `trailing.enabled` is true and the price hits the activation offset, dynamically raise the stop loss based on the `trail_by` rules.

### 2. Transparency & Logging (`backend/chronos.py`)
- Implement a `simulation_events` list inside the engine.
- Append structured events during the tick loop:
  - **Regime Shifts**: Log when the underlying regime changes (e.g., `VOLATILE -> DIRECTIONAL`).
  - **Trade Entries**: Log position details (e.g., `Entered Long CE (Directional)`, `Entered Short Straddle`).
  - **Trailing Updates**: Log when the stop loss is trailed (e.g., `Stop trailed to ₹150`).
  - **Exits**: Log exactly why the trade closed (`Smart Exit (Regime Shift)`, `Stop Hit`, `Target Hit`, `EOD Exit`).
- Return these `events` as part of the JSON response payload.

### 3. Frontend UI (`frontend/programs.js`)
- **Detailed Logs View**: Update the Chronos Backtest Results dialog to include a "View Detailed Logs" button. Clicking this will reveal a chronological, tabular log of all simulation events.
- **CSV Export**: Add an "Export to CSV" button that converts the detailed event logs (timestamps, event types, messages, PnL) into a downloadable CSV file.

## User Review Required
> [!IMPORTANT]
> This will fundamentally upgrade Chronos from a simple PnL calculator into a fully transparent, highly accurate simulator of your live system. Please review the updated plan and click **Proceed** if you're ready!
