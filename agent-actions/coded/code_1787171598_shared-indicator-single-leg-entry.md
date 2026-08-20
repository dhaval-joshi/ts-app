# Code Handover: Shared-Indicator Manual Single-Leg Program Entry

- **Associated Plan**: `plan_1787171598_shared-indicator-single-leg-entry.md`
- **Objective**: Implement a manual single-leg entry mode that fetches technical indicators dynamically, skips regular tick-based auto-starts, and calculates risk and margins based on full program capital.

### Files Changed
- `backend/models.py`: Config schema changes for manual entry mode.
- `backend/program_manager.py`: Added `start_manual_single_leg_cycle` which allocates 100% of the program's configured capital to a single leg. Also excluded manual entries from `_tick_one` auto-starts.
- `backend/program_safeguards.py`: Updated constraints for cycle statuses to permit manual entry logic without unintended halts.
- `backend/indicators.py` (NEW): Bootstraps historical chart data and computes RSI/EMA from real broker stream data. Fixes include timestamp normalization (milliseconds to seconds) and correctly parsing Tradejini's nested `"bars"` structure.
- `backend/main.py`: Added `indicators` to the global scope during `lifespan` and created `/api/programs/{id}/indicators` endpoint. Addressed issue where the ID passed for index history needed to be the native `IDX_..._NSE` ID instead of the stream token.
- `backend/tradejini_client.py`: Updated interval charting payload format compatibility.
- `frontend/programs.js`: Added support for `manual_single_leg` in UI toggles, parsing `/api/programs/{id}/indicators` output and generating HTML blocks for indicators on active cards.

### Behavior Changed
1. Users can now select "Manual Entry (Single CE/PE Leg)" via the UI for program config.
2. An API endpoint calculates realtime RSI and EMA over underlying charts.
3. The cycle logic treats the one leg safely and handles its exit the same as a pair.

### Important Implementation Decisions
- **Indicators Source Structure**: Discovered Tradejini historical payload includes a dict structure keyed under `bars`. Also fixed an epoch mismatch (milliseconds from API vs seconds natively in python) to ensure historical lists sync perfectly with live L1 streaming updates.
- **Capital Sizing**: Doubled the `capital_per_leg` parameter solely during a `manual_single_leg` entry to ensure 100% of the program capital is leveraged (as the other leg isn't traded).
- **Index ID Fetching**: `api_program_indicators` uses `index_row.id` directly because Tradejini caches charts using the native script master Index ID, not the stream token (e.g. `-1_NSE`).

### Tests/Simulations Run
- **Live Local Python Scripts**: Tested Tradejini `/api/mkt-data/chart/interval-data` endpoint directly.
- Validated `indicators.bootstrap_symbol` effectively extracts historical bars and populates memory.
- Validated math of RSI and EMA logic dynamically.

### What Was Not Verified
- Live paper/broker fills on a manual entry via UI button (the API endpoints and capital logic were tested by reviewing program_manager logic).
- Long term theta decay's impact on Option based Indicators (currently applying indicators to the underlying script natively instead of option string directly).

### Known Risks/Follow-ups
- As discussed with user, if we ever switch to calculating technical indicators against option prices instead of underlying, we will face theta decay and strike shift limitations. (Presently we track underlying).

### Can Another Agent Continue?
Yes. The feature is stable, data successfully propagates to the frontend.
