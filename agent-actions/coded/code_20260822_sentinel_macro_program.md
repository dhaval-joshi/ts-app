# Sentinel Macro-Program Implementation Handover

- **Associated Plan**: `agent-actions/planned/plan_20260822_sentinel_macro_program.md`
- **Objective**: Implement the new "Sentinel Macro-Program" architecture where a parent Sentinel Group automatically generates and orchestrates 3 distinct child programs (Sideways, Directional, Volatile) to seamlessly rotate capital based on the live market regime.

## Files Changed

1. **Backend**:
   - `backend/models.py`: Added `SentinelGroupConfig` dataclass to serialize the macro-program properties and added `is_auto_generated` flag to the core `ProgramConfig`.
   - `backend/store.py`: Added the `sentinel_groups` SQLite table along with `save_sentinel_group`, `load_sentinel_group`, `list_sentinel_groups`, and `delete_sentinel_group`.
   - `backend/program_manager.py`: Integrated Sentinel CRUD methods. Added `_sync_sentinel_children()` to automatically create/update the 3 respective child `ProgramConfigs` whenever a Sentinel Group is saved.
   - `backend/main.py`: Updated `api_list_programs()` to filter out `is_auto_generated` children. Created REST endpoints for Sentinel Groups CRUD and orchestration actions (`/start`, `/stop`, `/flatten`). The `/api/sentinel-groups` GET endpoint directly embeds the respective child configurations for ease of frontend rendering.
   - `backend/sentinel_orchestrator.py`: Modified the main tick loop to iterate over the active sentinel groups (instead of just looping children indiscriminately), evaluating market regime and performing the appropriate transitions.

2. **Frontend**:
   - `frontend/index.html`: Added a new "Sentinel Groups" tab under Advanced OMS alongside "Programs" and "Risk Groups".
   - `frontend/programs.js`: Added the corresponding UI logic, API hooks, form handlers, and macro-program modal builder (`sentinelGroupFormHtml`), which supports editing shared parameters and sub-strategy specific properties across the 3 tabs.

## Verification

### Automated Tests/Simulations Run
- Playwright UI tests failed due to environment limitations (driver download 404).
- Built and ran a standalone internal Python E2E script (`scratch/test_sentinel_e2e.py`) to bypass HTTP/Browser layers.
- **Results**: Verified that creating a Sentinel Group correctly cascades to the store and successfully produces exactly 3 auto-generated children (Sideways, Directional, Volatile) in the main program dictionary. Verified the orchestrator correctly reads the `is_active` flag. Verified that deleting the parent Sentinel Group properly cleans up the children and leaves no orphaned configurations in the `program_manager`.

### Known Risks/Follow-ups
- The backend relies on exactly 3 regimes (SIDEWAYS, DIRECTIONAL, VOLATILE). Future additions of new regimes in the `regime_classifier.py` will require extending the `_sync_sentinel_children` block in `program_manager.py`.
- Ensure capital isn't over-allocated. Sizing is currently configured on the parent macro-program and passed down to each child identically, assuming only 1 child runs at a time.

Another agent can safely continue from this repository state.
