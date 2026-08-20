# Plan: Intraday Momentum Indicator Dashboard
**Epoch**: 1787225000

## Objective
Replace static, potentially lagging entry indicators (RSI/EMA on options) with a real-time Intraday Momentum Slope (linear regression against moving average) visualized through UI color borders, trend arrows, and momentum-shift dots on active trading legs.

## Verified Current Behavior
Currently, the advanced active legs dashboard displays static text for RSI and EMA values, fetched via the `/api/programs/{program_id}/indicators` route. The user noted that traditional moving averages and oscillators fail on options due to Theta decay and Vega crush, leading to false signals.

## Proposed Design
1. **Backend Momentum Engine**: 
   - Add an asynchronous `_backfill_order_momentum` task in `OrderManager` to pull the last 5 or 9 minutes (Equities vs. Options) of historical 1-minute closes from `TradejiniClient` right as the leg enters `watching` state, preventing a cold start.
   - Maintain a sliding window of live tick closes in `handle_l1_tick`.
   - Calculate a linear regression slope against the moving average of the window throttled at 1 check per second. 
   - Inject `momentum_state` and `momentum_prev` directly into the live tracking dictionary.
2. **Frontend UI Refresh**:
   - Update `activeLegHtml` in `frontend/programs.js` to render the dynamic CSS border blocks, trend arrows, and momentum dots.
   - Replicate the new momentum UI in `renderOrderCard` within `frontend/app.js` to bring standard OMS orders up to parity.
   - Remove legacy `/indicators` fetch logic.

## Files/Components Likely Affected
- `backend/order_manager.py`
- `backend/main.py`
- `frontend/programs.js`
- `frontend/app.js`

## Safety Implications
- The historical data backfill must gracefully skip on Paper mode (which lacks the history API) without blocking execution.
- Throttling the linear regression math is required to prevent a high-velocity tick stream from spiking CPU usage and delaying trailing stop calculations.

## Verification Plan
- Code will be structurally verified.
- Handover and walkthrough documents will be written upon completion.
