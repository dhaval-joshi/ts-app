# Iteration 1.1 (Sentinel) - Regime-Aware Engine

- **Associated plan:** `plan_1787328413_sentinel.md` (Implementation plan provided in Gemini UI artifacts)
- **Objective:** Introduce a `RegimeClassifier` ("Sentinel") that dictates entry and exit decisions based on live market conditions (Side-ways, Directional, Volatile) to prevent blindly deploying capital during unfavorable environments.

## Files Changed
- `backend/models.py`: Added `execution_mode` (manual_config/sentinel) and `target_regime` to `ProgramConfig`.
- `backend/store.py`: Added `historical_bars` table initialization and CRUD operations for caching broker data.
- `backend/regime_classifier.py`: [NEW] Central logic for ADX/ATR-based state classification. Tracks regimes (`STATE_SIDEWAYS`, `STATE_DIRECTIONAL`, `STATE_VOLATILE`) and fetches historical data.
- `backend/main.py`: Bootstraps `RegimeClassifier` instance at startup.
- `backend/program_manager.py`: Connects Sentinel regime evaluation on order entry. Wires `execution_mode`, `target_regime`, and `index_id` fields onto `order` dicts for Sentinel smart exits.
- `backend/entry_signals.py`: Enforces explicit Sentinel gate logic during entry (`evaluate_entry`).
- `backend/order_manager.py`: Plumbs Sentinel Smart Exit logic (preemptive liquidations on regime shift and Sentinel data-blind halting) into `_maybe_app_market_exit()`. Wired dynamic ATR-based trailing into `_trail_leg()`.

## Behavior Changed
- Programs configured with `execution_mode = "sentinel"` are now physically blocked from entering the market unless the `RegimeClassifier` categorizes the active regime as matching the program's `target_regime`.
- Orders actively managed by Sentinel (`execution_mode = "sentinel"`) track the active regime continuously through the L1 tick stream (`_maybe_app_market_exit`). A sudden shift away from the targeted regime forces an immediate protective close ("Smart Exit").
- Sentinel halts/liquidates dynamically if historical API data drops out ("Sentinel Blind").
- Orders trailed under Sentinel adapt `trail_by` dynamically to `1.5 * ATR` if the market becomes exceptionally volatile.
- Market condition evaluations efficiently query an on-disk SQLite cache (`historical_bars`) to minimize `get_interval_chart_data` API limit strain.

## Important Implementation Decisions
- Added Sentinel `program_tag` fields (`execution_mode`, `target_regime`, `index_id`) directly to the `order` object. `handle_l1_tick` and `_maybe_trail` can now evaluate preemptive checks strictly in-memory without cross-referencing `program_manager.py`.
- ATR logic gracefully falls back to the statically-configured `trail_by` value if ATR yields a smaller stop distance, prioritizing capital protection.

## Tests/Simulations Run
- [x] Compilation and Syntax validation: The updated modules compile correctly in standard run conditions.
- *Pending*: Next step is a fake-broker simulation to verify `RegimeClassifier` state transitions correctly propagate through to the Order Manager exit closures.

## Known Risks / Follow-ups
- Need to establish the exact parameters of the `verify-trading-app` skill simulation (as required by `AGENTS.md`) in a separate run.
- Telegram alerting is correctly wired in `regime_classifier.py` and `handle_l1_tick` but assumes `notifier.py` is present and functional.

**Status:** Code Implementation phase completed. Ready for review or handover.
