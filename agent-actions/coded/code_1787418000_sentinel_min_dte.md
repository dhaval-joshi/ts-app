# Sentinel Min DTE Implementation

**Associated Plan:** `plan_1787418000_sentinel_min_dte.md`
**Objective:** Allow configuration of `min_working_days_to_expiry` at the Sentinel Group level to avoid 0DTE/1DTE options.

## Files Changed
1. `backend/models.py`
2. `backend/program_manager.py`
3. `frontend/programs.js`

## Behavior Changed
- Sentinel Groups now support a configurable `min_working_days_to_expiry` parameter (default 2).
- When a Sentinel Group is created or updated, the orchestrator automatically injects this configuration downward into all three child programs (SIDEWAYS, DIRECTIONAL, VOLATILE).
- The Program Manager will inherently use this injected parameter during its expiry selection (`self.script_master.next_expiry()`), smoothly shifting automated trading away from 0DTE gamma risks.

## Verification
- **Verified:** The UI form successfully captures and transmits the integer payload. The `SentinelGroupConfig.from_dict` correctly defaults, parses, and persists it to `config.RISK_GROUPS_DIR`. `_sync_sentinel_children` successfully injects it into `child_dict`.
- **Not Verified:** Live execution in the broker (as this is prohibited per `AGENTS.md` and requires the passing of time).

## Known Risks / Follow-ups
- By setting this to 2, the user explicitly opts out of massive theta decay on expiry day for the Short Straddle (Sideways) program. This is an accepted strategic trade-off. No further follow-ups required.
