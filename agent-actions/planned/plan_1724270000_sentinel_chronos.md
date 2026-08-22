# Plan: Iteration 1.1 (Sentinel Config Overrides) & Iteration 1.2 (Chronos Backtesting)

## Objective/Problem
1. **Iteration 1.1**: The Sentinel Regime Classifier currently uses hardcoded ADX and ATR threshold variables to dictate market conditions. The objective is to bring these thresholds to the application layer so they can be modified dynamically via the UI without code changes. The UI should display a clear warning if the system is running a modified configuration compared to the default.
2. **Iteration 1.2**: Project Chronos requires a native backtesting engine that simulates real-world entry conditions against Tradejini's historical data. Since Tradejini's historical data only provides OHLCV (and no historical Options Greeks/IV), we need to programmatically derive Implied Volatility and the Options Greeks to accurately mimic the live gating logic (such as the `iv_session_rank_gate`) during the simulation. This requires implementing the Black-Scholes formula using the Newton-Raphson approximation.

## Verified Current Behavior
- Sentinel limits (`adx_period=14`, `adx_directional_threshold=25`, `atr_volatile_multiplier=1.5`) are hardcoded in `backend/regime_classifier.py`.
- No native backtesting exists. Entry gates operate purely on live WebSockets / snapshot APIs.

## Proposed Design
### Iteration 1.1
- **Data Model**: Introduce `SentinelConfig` dataclass in `backend/models.py`.
- **Storage**: Store the overridden config in the `singletons` table via `backend/store.py` (`load_sentinel_config`, `save_sentinel_config`).
- **Engine Logic**: `RegimeClassifier._compute_regime` reads from the configured `SentinelConfig` instead of static variables.
- **API & UI**: Expose `/api/sentinel/config` in `main.py`. The UI (`frontend/index.html` + `frontend/app.js`) features a modal to edit ADX/ATR parameters and highlights any differences from defaults via a diff view.

### Iteration 1.2
- **Black-Scholes Engine**: Create `backend/bsm_calculator.py` utilizing `scipy.stats.norm` and Newton-Raphson to reverse-engineer IV from historical minute-bar closing prices and derive Delta, Gamma, Theta, Vega.
- **Chronos Engine**: Create `backend/chronos.py` containing `run_backtest(program_id, days_back, capital)`. It:
  - Fetches index history.
  - Feeds minute bars to `RegimeClassifier` sequentially.
  - At valid entry windows, identifies ATM strikes via `ScriptMaster` and fetches specific option history.
  - Calculates mock Option Greeks using `bsm_calculator`.
  - Evaluates entry gates via `evaluate_entry` (including IV session rank gates based on the day's high/low IV range).
  - Simulates SL and Smart Exits (regime shifts) with infinite margin.
- **API & UI**: Expose `/api/backtest/run/{program_id}` and add a "Backtest (Chronos)" button to the Program card in `frontend/programs.js`.

## Files/Components Likely Affected
- `backend/models.py`
- `backend/store.py`
- `backend/regime_classifier.py`
- `backend/bsm_calculator.py` (New)
- `backend/chronos.py` (New)
- `backend/main.py`
- `frontend/index.html`
- `frontend/app.js`
- `frontend/programs.js`

## Safety Implications
- Sentinel is critical for system safety. If the stored config is invalid or corrupted, it should gracefully fall back to the defaults.
- Backtesting assumes infinite margin, therefore it is only for simulation. It should strictly use mock objects (e.g. `chronos_mock_<index_id>` for historical bars) so it does not pollute live data stores or accidentally place live orders.

## Alternatives/Trade-offs
- **Historical Greeks**: We could theoretically pull historical IV from external sources (e.g., NSE archives), but dynamically calculating it using BSM gives us complete self-sufficiency within the Tradejini ecosystem, solving the lack of data provided by the broker API. Accuracy diminishes slightly for deep OTM strikes, but since programs primarily trade ATM strikes, accuracy will be high.

## Verification Plan
1. Ensure Sentinel Config UI accurately updates the backend storage and visually displays the diff.
2. Verify `bsm_calculator.py` produces mathematically sound IV values matching manual calculations.
3. Test Chronos Backtesting on an existing program and verify trades simulate realistically across regime shifts and stop-loss logic.

## Open Questions/Assumptions
- Backtesting does not currently model slippage or precise fill limits. Fills occur at exactly the historical minute's close price.
- `scipy` is assumed to be an acceptable dependency for statistical operations.

## Explicit Scope and Non-Scope
- **In Scope**: Application-layer Sentinel config overrides, Black-Scholes IV derivation, pure OHLCV native backtesting.
- **Non-Scope**: Sophisticated slippage modeling or multi-leg margin requirement modeling in the backtester.

---

# Plan Extension: Iteration 1.3 (Sentinel Orchestrator)

## Objective/Problem
When utilizing `sizing_mode="capital"` (trading on 100% of available margin), a decentralized architecture creates a Margin Race Condition during regime shifts. If the active program exits, but the newly eligible program evaluates its entry *before* the broker officially releases the funds, the new entry will fail and miss the breakout. 

## Proposed Design
- **Data Model**: Add `sentinel_group_id: Optional[str] = None` to `ProgramConfig`. Programs sharing this ID are mutually exclusive and share a single capital lock.
- **Sentinel Orchestrator**: Create a new service (`backend/sentinel_orchestrator.py`) that subscribes to regime shifts. 
- **State Machine**: When the regime shifts, the Orchestrator commands `program_manager.flatten_program()` on the currently active program, enters a rapid polling loop on `tradejini_client.get_margins()` until the capital clears, and immediately calls `program_manager.force_start_cycle()` on the newly eligible program (bypassing its internal clock).
- **Chronos Upgrade**: `chronos.py` will be upgraded to accept a `sentinel_group_id` instead of a `program_id`. It will load all child programs, evaluate the historical regime tick-by-tick, actively swap between strategies, and dynamically roll the unified mock capital across the historical transitions.

## Safety Implications
- The Orchestrator must handle edge cases where margin is *partially* released or never released (e.g., a frozen broker API). It should have a fallback timeout to prevent infinite loops.
- `program_manager` must ensure `force_start_cycle` strictly obeys all other standard entry gates (OI buildup, VIX, etc.) and only bypasses the time clock.

## Explicit Scope
- **In Scope**: `sentinel_group_id` abstraction, `SentinelOrchestrator` service, `force_start_cycle` override, Chronos Group Backtesting upgrades.
- **Non-Scope**: Cross-broker capital distribution (reserved for Phase 2 "Super Program").
