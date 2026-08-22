# Autonomous Sentinel Implementation

- **Plan**: `agent-actions/planned/plan_20260822_autonomous_sentinel.md`
- **Objective**: Overhaul Sentinel from static configuration to fully autonomous, Greek-based, regime-aware execution with "Smart Exit" and an "Isolated Capital Sandbox".

## Files Changed
- `frontend/programs.js`: Removed the static strategy configurations from the Sentinel Group creation UI.
- `backend/models.py`: Removed the `strategies` field from the `SentinelGroupConfig` model.
- `backend/program_manager.py`: Removed static strategy extraction when auto-generating child programs. Tagged children with `execution_mode: autonomous_sentinel`.
- `backend/chronos.py`: Updated backtest engine to properly handle `trade_type: sell`. Specifically: tracking capital offset from margin for short selling, correctly inverting the PnL calculation for short trades (`entry - exit`), and ignoring raw entry cost limits if selling against margin.
- `backend/sentinel_orchestrator.py`: Complete overhaul of the orchestrator to dynamically compute entry conditions, Greeks-based target/stop configurations (Smart Exit), and isolated capital (using program cycles) before deploying a child program.

## Behavior Changed
Sentinel Groups now completely lack static configurations (except for the underlying index and total base capital). The `SentinelOrchestrator` now evaluates real-time market data (IV/IVR via the `OrderManager` Greeks stream) and injects highly optimized, quantitative rules dynamically into child programs before they are deployed. 

The orchestrator now also calculates the **Isolated Capital Sandbox** before entering a trade by checking `base_capital + cumulative_realized_pnl` from all children's cycles. If the capital is below the required margin (e.g. ₹130,000 for a Nifty Short Straddle leg), the entry is safely skipped and a warning is logged.

## Important Implementation Decisions
- **PnL Tracking for Sandbox**: Rather than executing complex DB queries that join against non-existent or differently-structured tables, the sandbox calculates cumulative PnL by extracting `p["cycles"]` from the `ProgramManager`'s in-memory state. This guarantees real-time accuracy and prevents race conditions with DB syncs.
- **Backtest Shorting**: Chronos needed to be updated to support shorting. `trade_type` is now respected, meaning margin logic dictates sizing rather than cash balance alone, and PnL correctly computes inverted values.

## Tests Actually Run
- `test_sentinel_e2e.py` (Local integration test script): Verified the end-to-end flow of creating an Autonomous Sentinel Group, successfully auto-generating its 3 children, and running the Orchestrator loop. 
- The test successfully caught the **Isolated Capital Sandbox** in action, correctly blocking a `SIDEWAYS` short entry when only ₹100,000 capital was provided (₹130,000 required).
- Tested again with ₹200,000 capital, and the Orchestrator successfully deployed the child program with the injected dynamic configurations.

## What Was Not Verified
- Multi-day historical simulations in Chronos for the new autonomous logic (Chronos can run it, but the dynamic IV mock injection into historical data may need refinement for perfect accuracy).

## Known Risks/Follow-ups
- The `iv` and `ivr` fallback values inside the orchestrator are hardcoded if the Greeks stream is unavailable (e.g., paper trading with no active index feed). This could cause the strategy to always default to specific regimes if not handled carefully.
- The `required_margin_per_lot` is currently approximated. In production, this should ideally poll a live broker margin calculator endpoint.

Another agent can safely continue work.
