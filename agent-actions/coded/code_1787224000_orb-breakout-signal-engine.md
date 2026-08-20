# Implementation of ORB Breakout Signal Engine

## Associated Plan
`plan_1787224000_orb-breakout-signal-engine.md`

## Objective
Implement an automated entry logic that calculates an N-minute Opening Range Breakout (ORB) on the underlying index, triggers entry unconditionally when price breaks High (CE) or Low (PE), applies Squeeze/VIX entry condition gates, and allocates program capital entirely to that single breakout direction without modifying the Live Order exits logic.

## Files Changed

- `backend/indicators.py`
  - Added `backfill_daily_signals` to aggregate 1-minute historical intraday data into daily snapshots (fetching up to 30 days) on startup so Squeeze and VIX metrics are primed immediately.
- `backend/signal_engine.py` (NEW)
  - Created a new live stream processor that subscribes to the underlying index.
  - Computes the dynamic ORB `High`/`Low` over the configured `orb_duration_minutes`.
  - Triggers a single leg (CE or PE) cycle upon breakout via `ProgramManager.start_signal_single_leg_cycle`.
- `backend/main.py`
  - Instantiated `SignalEngine` alongside `IndicatorService`.
  - Registered `signal_engine` into the L1 tick broadcast loop.
  - Initialized `backfill_daily_signals` in the scrip master refresh loop.
- `backend/models.py`
  - Updated `ProgramConfig` schema with `orb_duration_minutes` (default 15).
  - Added `signal_single_leg` to the `entry_mode` enum literal.
- `backend/program_manager.py`
  - Implemented `start_signal_single_leg_cycle(program_id, leg)` to initiate the order while evaluating existing safety nets and entry gates (e.g. Squeeze, VIX thresholds).
  - Adjusted `_tick_one` so time-based generic auto-cycle starts ignore programs configured as `signal_single_leg`.
  - Allowed both `lots` sizing and 100% `capital` sizing options to pass through cleanly for a single leg.
- `frontend/programs.js`
  - Updated the Program UI to include `Signal Single-Leg (Breakout)` in the Entry Mode dropdown.
  - Added an input for `ORB Tracking Duration (mins)`.
  - Added logic to automatically populate Prop-desk standards (Bollinger Period 20, Squeeze bandwidth <= 30, Max VIX 80) and toggle entry signals ON when the `signal_single_leg` mode is selected.
  - Added visual toggles to hide less relevant gates (Max OI change, Max session range) for ORB mode to reduce UI clutter.

## Behavior Changed
- The app can now fully automate single-leg entries based on live streaming ORB parameters of the index. 
- It actively avoids standard time-based scheduling entries. Instead, it delegates entirely to the `SignalEngine` for breakout logic.
- A programmatic API verifies signal states asynchronously, blocking bad entries while preserving the established generic `OrderManager` exit handling untouched.

## Tests & Simulations Run
- Executed `scratch_verify.py` locally patching clock increments and using `FakeProgramManager` and `FakeScriptMaster`.
- Verified that 1-minute ticks occurring before 09:15 + ORB duration dynamically expanded `orb_high` and `orb_low` but did NOT trigger an entry.
- Verified that ticks after ORB completion crossing below `orb_low` correctly triggered a `PE` start cycle on the `ProgramManager`.
- Verified that a subsequent simulated cross above `orb_high` successfully dispatched a `CE` start cycle on the `ProgramManager`.

## What Was Not Verified
- Live Tradejini execution with full capital margins as real money accounts were avoided. Tested strictly using mocks for time and the `FakeProgramManager`.

## Known Risks / Follow-ups
- Ensure users don't configure overly tight ORB durations (e.g., 1 minute) resulting in rapid noise-based triggers.
- The `backfill_daily_signals` fetches 30 days of 1-minute candles across indexes, which can add a few seconds of startup delay. Wait logic handles this safely in the background.

## Can Another Agent Safely Continue?
Yes, all configuration, state logic, and UI bindings are fully integrated and isolated. The implementation does not alter how the core `OrderManager` or trailing stops function.
