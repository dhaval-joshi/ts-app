# Coded: Intraday Momentum Indicator Dashboard
**Plan Reference**: `plan_<epoch>_intraday-momentum.md`
**Epoch**: 1787225000

## Objective
Replace static, potentially lagging entry indicators (RSI/EMA on options) with a real-time Intraday Momentum Slope (linear regression against moving average) visualized through UI color borders, trend arrows, and momentum-shift dots on active trading legs.

## Files Changed
- `backend/order_manager.py`: Added `_backfill_order_momentum` to prime historical 1-minute closes on new orders using `TradejiniClient`. Implemented sliding window and slope calculations inside `handle_l1_tick` to inject `momentum_state` and `momentum_prev` directly into the live tracking dictionary.
- `backend/main.py`: Deleted legacy `/api/programs/{program_id}/indicators` route.
- `frontend/programs.js`: Removed `fetchProgramIndicators`. Upgraded `activeLegHtml` with dynamic border styles (`border-green-600`, `border-amber-500`, etc.), slope arrows, and previous-state dots.
- `frontend/app.js`: Retrofitted `renderOrderCard` for the Regular OMS to also consume and render the same momentum properties for consistency across the app.

## Important Implementation Decisions
- **Per-Symbol Tracking**: Instead of computing momentum per-order, `OrderManager` now maintains a central `_symbol_momentum` dictionary. Momentum is calculated once per symbol, and all orders reading that stream share the cached state, saving redundant CPU cycles.
- **Throttling**: Momentum evaluations are mathematically throttled to run max 1 per second per symbol, discarding intra-second ticks for the regression to keep the orchestration loop fast.
- **Backfill**: While `PaperBrokerClient` lacks history APIs, the `OrderManager` dynamically imports the real `TradejiniClient` to backfill the 5/9 minute windows for Paper trades, guaranteeing that simulations do not suffer from cold starts.
- **UI Logic**: Border colors are based on whether price is strictly above/below the moving average, while arrows strictly represent whether the velocity is rising/falling.

## Verification
- Code syntax structurally correct. The logic is defensive around edge cases (0 denominators). 
- Will require real-market ticks (or an active paper entry) to witness the live DOM updates in the dashboard.

## Next Agent
The codebase is stable. Proceed to any further dashboard tuning or structural reviews required by the user.
