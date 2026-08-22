# Plan: Iteration 1.4 (Sentinel Macro-Program Architecture)

## Objective/Problem
The current Iteration 1.3 architecture for Sentinel orchestrates individual child programs using a shared `sentinel_group_id`. However, relying on the user to manually create 3 separate programs (one for each regime) and perfectly align their capital settings and group IDs invites significant human error. The UI currently lacks a cohesive "Sentinel Parent" view, making it impossible to orchestrate or configure a Sentinel group holistically.

## Verified Current Behavior
- `sentinel_group_id` exists on `ProgramConfig` but is not exposed in the frontend creation form.
- The user cannot easily manage a unified Sentinel group from the UI.
- No `SentinelGroupConfig` model exists to act as the single source of truth for the macro-program settings.

## Proposed Design
1. **Creation Flow**: Create a "Sentinel Group" UI dialog where the user defines shared constraints once (Index, Capital Allocation, Risk Group, Broker), and then configures the distinct strategy parameters (Stops, Targets, Entry Modes) for **SIDEWAYS**, **DIRECTIONAL**, and **VOLATILE** across 3 internal tabs.
2. **Auto-Generation**: Upon saving the macro-group, the backend generates exactly 3 distinct `ProgramConfig` children automatically. They share the same `sentinel_group_id` and are marked with `is_auto_generated=True` to hide them from the normal "Programs" tab.
3. **Macro-Card UI**: The Sentinel Group appears as a single parent card in a new "Sentinel Groups" UI tab. Parent-level actions (Start, Stop, Flatten, Backtest) command the `sentinel_orchestrator`. The card can expand to show the 3 child programs and their active states, P&L, orders, and logs.
4. **Multiple Sentinels**: The orchestrator will support multiple independent Sentinel Groups running concurrently, each managing its own capital rotation.

## Files/Components Likely Affected
- `backend/models.py` (Add `SentinelGroupConfig`, modify `ProgramConfig`)
- `backend/store.py` (Add `sentinel_groups` table and CRUD methods)
- `backend/main.py` (Add Sentinel API endpoints, filter auto-generated programs from standard list)
- `backend/sentinel_orchestrator.py` (Iterate across multiple Sentinel Groups)
- `frontend/index.html` (Add Sentinel Groups tab and macro-program dialog)
- `frontend/programs.js` (Render Sentinel Groups, manage parent-level UI actions)

## Safety Implications
- Sentinel orchestrates automated capital shifting. Enforcing the generation of the 3 children programmatically via the backend API ensures they are strictly mutually exclusive and configured safely without human typing errors.
- The `is_auto_generated` flag prevents users from accidentally deleting or misconfiguring an active child program from the standard Programs tab.

## Alternatives/Trade-offs
- **Alternative**: Keep programs decoupled and just add `sentinel_group_id` to the existing Program Form.
- **Trade-off**: This avoids creating a new UI tab, but leaves the user with the burden of configuring 3 complex programs manually and managing them as separate entities, breaking the mental model of a single "regime-aware" strategy. The Macro-Program approach is substantially safer and more user-friendly.

## Verification Plan
1. Launch UI, navigate to "Sentinel Groups", and create a new group.
2. Verify exactly 1 Sentinel Group is visible, and the 3 children are *not* visible in the standard "Programs" list.
3. Click "Start" on the Sentinel Group. Verify the orchestrator engages and activates the child program matching the current regime.
4. Click "Backtest". Verify Chronos processes the group ID and successfully backtests across the 3 children via regime shifting.

## Open Questions/Assumptions
- Will `Chronos` support the `sentinel_group_id` parameter natively without architectural overhauls? (Assumption: Yes, it just needs to load the 3 children and tick through them).

## Explicit Scope and Non-Scope
- **In Scope**: `SentinelGroupConfig` data model, backend API CRUD for Sentinels, UI tab for Sentinel Groups, auto-generation of 3 children programs, orchestrator support for multiple Sentinels.
- **Non-Scope**: Editing a *single* auto-generated child program manually. Edits must happen at the parent Macro-Program level.
