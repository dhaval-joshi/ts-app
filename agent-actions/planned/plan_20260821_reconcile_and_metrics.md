# Plan: Reconcile Storage, Trail Metrics, Capital Sizing, and Cache Fixes

## Objective
Address several outstanding bug reports and feature requests:
1. Admin reconciliation Preview is crashing/doing nothing.
2. The reconciliation UI text is confusing regarding local vs broker positions.
3. Stuck square-offs are unconditionally wiping their `close_reason`.
4. `trail_update_count`, `initial_trig_price`, and `slippage` metrics are missing from the `order` schema.
5. The Script Master cache logic (`is_loaded()`) isn't properly checking the date, causing it to incorrectly load yesterday's data on restart.
6. The manual single leg feature strictly locks to the ATM strike, failing if the capital constraint is tighter than what the ATM premium costs.

## Verified Current Behavior
- `store.save_reconcile_report` is missing from `backend/store.py`, causing silent `AttributeError` failures when hitting the Preview button.
- `_revert_stuck_square_off` sets `order["close_reason"] = None` every time.
- `start_manual_single_leg_cycle` in `backend/program_manager.py` only tries `offset_steps=0` (ATM).
- `is_loaded()` in `backend/script_master.py` just returns `bool(self._indices)`.

## Proposed Design
- Add the three missing reconciliation storage functions to `store.py`.
- Update the Admin UI text to explicitly separate "Checked X local positions" from "spanning ~Y broker orders".
- Update `_revert_stuck_square_off` to leave `close_reason` intact if it is one of `"manual"`, `"program_cycle_manual_close"`, or `"program_flatten"`.
- Inject `trail_update_count` and `slippage` fields into `order_manager.py`'s lifecycle. `slippage` will be calculated during `_finalize_realized_pnl` for target/stop hits. `initial_trig_price` will be captured on entry fill in `_finalize_exit_leg`.
- Update `create_and_place_order` to pass the application `order_id` in the `remarks` to the broker, and update `_do_close` to pass the entry `broker_order_id` in the square-off `remarks`.
- Update `backend/script_master.py` to capture `self._loaded_on_date = clock.today()` and verify it in `is_loaded()`.
- Update `start_manual_single_leg_cycle` to search up to `ATM±3` by iterating `offset` from `0` to `3` checking against `capital_per_leg`.

## Safety Implications
- None. These are primarily bug fixes and conservative metric additions.
- The single leg widening loop is explicitly bounded to `ATM±3` to prevent traversing deep OTM.

## Verification Plan
- Syntax check using `py_compile`.
- Manual verification requested from the user to verify Admin Preview, UI text, and manual single-leg bounds.
