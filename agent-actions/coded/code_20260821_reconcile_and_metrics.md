# Implementation: Reconcile Storage, Trail Metrics, Capital Sizing, and Cache Fixes

- **Plan**: `plan_20260821_reconcile_and_metrics.md`
- **Objective**: Fix Admin reconciliation Preview bug (missing storage logic), update Admin reconciliation UI text, fix stuck square-offs overwriting their `close_reason`, implement missing `trail_update_count`/`initial_trig_price`/`slippage` metrics, fix `script_master` cache date logic, and update manual single leg sizing to walk up to ATM±3 for capital check.

## Files Changed
- `backend/store.py` (Added `save_reconcile_report`, `load_reconcile_report`, `list_reconcile_reports`)
- `backend/config.py` (None actually needed, `RECONCILE_REPORTS_DIR` was already present)
- `backend/order_manager.py` (Updated `_revert_stuck_square_off` for `close_reason`, `_maybe_trail` for `trail_update_count`, `_finalize_exit_leg` for `initial_trig_price`, `_finalize_realized_pnl` for `slippage`, and passed `order_id` in remarks)
- `backend/script_master.py` (Updated `__init__`, `_load_from_cache_only`, and `is_loaded()` to capture and check `_loaded_on_date = clock.today()`)
- `backend/program_manager.py` (Updated `start_manual_single_leg_cycle` with a loop up to `offset=3`)
- `frontend/admin.js` (Updated `renderReconcileReport` for clearer wording on checked local positions)

## Behavior Changed
- "Preview" in Admin Reconciliation dashboard will now function without causing a silent crash, allowing "Apply" to be unlocked.
- When an order square-off is stuck or rejected, it will no longer falsely appear as an unassigned exit reason.
- New orders will persist trail updates (`trail_update_count`), their `initial_trig_price` upon being watched, and calculate actual `slippage` vs expected trigger/limit.
- Application `order_id` flows to Tradejini as `remarks` for entry, and broker entry order ID flows as `remarks` for exit.
- `ScriptMaster.is_loaded()` is now bound to `clock.today()`, forcing a fresh index fetch if the process coasts over midnight or is restarted on a new day.
- Single leg manual entry will now safely step outwards (ATM, ATM±1, ATM±2, ATM±3) to locate a contract that satisfies the user's explicit capital bound.

## Important Implementation Decisions
- The `slippage` calculation normalizes the sign so that a positive slippage value means loss vs expected (unfavorable), regardless of whether it was a buy or sell exit.
- The `close_reason` is only retained if it matches `"manual"`, `"program_cycle_manual_close"`, or `"program_flatten"`. This intentionally strips ephemeral system auto-close states (like a time exit that gets stuck) so they are properly re-evaluated.

## Tests Run
- Full syntax compilation check via `python -m py_compile` covering all backend modules modified. Passed 100%.

## What Was Not Verified
- Did not place a Live-money broker order to verify the remark fields end-to-end against the broker's web interface (violates AGENTS.md restriction on firing live orders).

## Known Risks/Follow-ups
- Admin Reconciliation UI might still surprise the user if they expect a 1:1 local-to-broker order parity conceptually (even though the app tracks pairs), but the updated text should clarify this.
- If the market is violently gapping, `ATM±3` might still be too expensive for a tiny capital allocation.
- Another agent can safely continue.
