# Implementation of Iteration 1.1 (Sentinel Configuration Overrides) and Iteration 1.2 (Project Chronos Native Backtesting)

- **Associated Plan**: `plan_1787217959_sentinel_chronos.md` (via implementation_plan.md)
- **Objective**: 
  1. Add application-level UI overrides for Sentinel's regime detection thresholds.
  2. Implement an in-house backtesting engine capable of simulating dynamic ATM strike selection and reverse-engineering Options Greeks via Black-Scholes (to mimic live IV session ranks).

## Files Changed
- `backend/models.py`: Added `SentinelConfig`.
- `backend/store.py`: Added `load_sentinel_config` and `save_sentinel_config`.
- `backend/regime_classifier.py`: Refactored to fetch dynamic `adx_directional_threshold`, `atr_period`, etc. from `SentinelConfig`.
- `backend/bsm_calculator.py` [NEW]: Pure mathematical Black-Scholes model utilizing Newton-Raphson method to derive IV and calculate Delta, Gamma, Theta, Vega from historical prices.
- `backend/chronos.py` [NEW]: Core backtesting loop. Fetches historical 1-minute bars, feeds them into Regime Classifier tick-by-tick, simulates Entry Gate Evaluation (using the BSM Calculator to reverse engineer IV), dynamically resolves ATM strikes via `ScriptMaster`, and mocks `iv_session_rank_gate`. Simulates Smart Exits and Trailing Stops over infinite margin assumptions.
- `backend/main.py`: Exposed `/api/sentinel/config*` endpoints and `/api/backtest/run/{program_id}` endpoint.
- `frontend/index.html`: Added a Sentinel Badge inside the Nav Bar next to the Admin icon. Added a Sentinel Settings Modal for overriding Regime Detection defaults and previewing diffs.
- `frontend/app.js`: Added JS logic for the Sentinel Settings modal, displaying diff UI and handling API submission.
- `frontend/programs.js`: Added a "Backtest (Chronos)" button to the Program Details Card which prompts for Days and Capital, and then executes the backtest asynchronously.

## Behavior Changed
- Sentinel is no longer strictly hardcoded. Users can override ADX & ATR detection parameters directly from the UI.
- The Dashboard UI now allows running backtests seamlessly for a particular program.

## Important Implementation Decisions
- **BSM Reverse-Engineering**: Tradejini's Historical chart APIs only provide OHLCV data. To maintain high fidelity to live market gating (like `iv_session_rank_gate`), we utilize Newton-Raphson approximation to mathematically trace Implied Volatility given the 1-minute OHLCV close price.
- **Infinite Margin Backtesting**: Standard backtesting assumes infinite margin for simplicity when trading against historical options prices.
- `scipy` is required for statistical standard normal distribution CDF/PDF methods within the Black-Scholes implementation.

## Tests Run
- Run a live simulation against `ChronosEngine` using mocked Tradejini historical data logic.

## Follow-ups / Known Risks
- Depending on the frequency, the backend may need to optimize `TradejiniClient.get_interval_chart_data` caching to not hammer the broker when querying historical options data dynamically in the backtest loop.
- The Newton-Raphson approximation performs well for short DTE option chains but accuracy falls when nearing expiration / deep OTM chains. 
