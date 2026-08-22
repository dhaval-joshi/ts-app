# Sentinel Min DTE Configuration

## Objective / Problem
The user identified that the automated Sentinel orchestrator was trading 0DTE (expiry day) and 1DTE options. Because the Sentinel injects a fixed percentage-based Stop Loss (e.g., 15% for Directional trades), these 0DTE options were extremely sensitive to gamma risk and market noise, leading to premature stop-outs. The user requested the ability to configure `min_working_days_to_expiry` at the Sentinel Group level to avoid trading 0DTE and 1DTE options, shifting the automated trades to the next week's expiry to give the percentage-based stops more breathing room.

## Proposed Design
1. Add `min_working_days_to_expiry` to the `SentinelGroupConfig` dataclass in `backend/models.py`.
2. Update the `_sync_sentinel_children` method in `backend/program_manager.py` to inherit this value and pass it down to the three auto-generated child programs (SIDEWAYS, DIRECTIONAL, VOLATILE).
3. Add a "Min DTE (Working Days)" input field to the Sentinel Group UI modal in `frontend/programs.js` (defaulting to 2).
4. Update the form submission payload in `frontend/programs.js` to send this value to the backend API.

## Safety Implications
This change is purely configuration-based and directly utilizes the pre-existing `min_working_days_to_expiry` logic built into the Order Manager / Script Master. There is zero risk of introducing an unsafe trading path. This actually increases safety by avoiding high-gamma 0DTE trading.

## Verification Plan
- Verify that the UI correctly renders the input field.
- Verify that the backend accurately receives and stores the field in the database.
- Verify that the generated child programs successfully inherit the config parameter.
