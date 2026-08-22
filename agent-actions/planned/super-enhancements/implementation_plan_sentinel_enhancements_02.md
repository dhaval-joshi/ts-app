# Sentinel Groups UI Implementation

## Feedback & Design Philosophy
Your feedback is excellent, and I completely agree: **The Sentinel Group should be a "Macro-Program" that acts as the primary interface for the user, fully automating the creation and management of its 3 child programs.** 

If we rely on the user to manually create 3 separate programs and perfectly align their `sentinel_group_id`, capital allocations, and target regimes, we are inviting human error. 

**My Opinion / Proposed Architecture:**
1. **Creation Flow**: The user creates a "Sentinel Group" via a new dialog. They define the shared constraints once (Index, Capital Allocation, Risk Group, Broker). Then, they are presented with 3 tabs to configure the distinct strategy parameters (Stops, Targets, Entry Modes) for **SIDEWAYS**, **DIRECTIONAL**, and **VOLATILE**. 
2. **Auto-Generation**: Upon saving, the backend automatically generates exactly 3 `ProgramConfig` children hidden from the normal "Programs" list. They share the same `sentinel_group_id` and capital pool.
3. **UI Dashboard**: The UI displays a "Sentinel Group" card. The Start, Stop, Flatten, and Backtest buttons exist only on this parent card. The card can expand to show the 3 child programs, displaying their individual P&L, open legs, and logs, but the user cannot manually start/stop a child individually.
4. **Multiple Sentinels**: Yes, we will allow multiple independent Sentinel Groups to run concurrently. Each Sentinel Group will have its own capital pool and will independently rotate its own children based on the global market regime.

---

## Proposed Changes

### Backend Models & Store

#### [MODIFY] [models.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/models.py)
- **Add `SentinelGroupConfig` Model**: A new dataclass that holds the shared program settings (`name`, `index_id`, `capital_per_leg`, `sizing_mode`, `risk_group_id`, etc.) and a dictionary of the 3 regime-specific configurations.
- **Update `ProgramConfig`**: Add `is_auto_generated: bool` to explicitly flag programs managed by a Sentinel Parent so they can be filtered out of standard API responses.

#### [MODIFY] [store.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/store.py)
- **Initialize Table**: Add `CREATE TABLE IF NOT EXISTS sentinel_groups (id TEXT PRIMARY KEY, name TEXT, data TEXT)`.
- **Add CRUD Methods**: `save_sentinel_group`, `load_sentinel_group`, `list_sentinel_groups`, and `delete_sentinel_group`.

#### [MODIFY] [main.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/main.py)
- **Hide Children**: Update `api_list_programs` to exclude programs where `sentinel_group_id` is set (or `is_auto_generated` is True) to prevent dashboard clutter.
- **Sentinel CRUD Endpoints**: Add `/api/sentinel-groups` to handle creation. When a `POST` creates a Sentinel Group, the backend logic will automatically dynamically map the settings into 3 distinct `ProgramConfig` objects and save them to the `programs` table.
- **Sentinel Action Endpoints**: Add `/api/sentinel-groups/{id}/start`, `/stop`, and `/flatten`. These endpoints will iterate over the child programs to apply the action, and engage the `sentinel_orchestrator` state machine.

### Frontend UI

#### [MODIFY] [index.html](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/frontend/index.html)
- **Sentinel Groups Tab**: Add a dedicated `advtab-sentinelgroups` tab alongside "Risk Groups".
- **Sentinel Card Template**: Add HTML templates for a Macro-Card that hosts the parent-level controls and a nested accordion for the 3 child programs.
- **Sentinel Configuration Dialog**: Create a new modal for creating Sentinel Groups. It will have a unified "General Settings" tab, and 3 specific tabs for "Sideways Strategy", "Directional Strategy", and "Volatile Strategy".

#### [MODIFY] [programs.js](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/frontend/programs.js)
- Implement rendering logic for `renderSentinelGroups()`.
- Add UI handlers for the parent-level `startSentinelGroup`, `stopSentinelGroup`, `flattenSentinelGroup`, and `backtestSentinelGroup`.

### Engine / Orchestration

#### [MODIFY] [sentinel_orchestrator.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/sentinel_orchestrator.py)
- Ensure the orchestrator is equipped to handle multiple active Sentinel Groups concurrently. It will iterate over all active `SentinelGroupConfig`s, check the global regime for their respective `index_id`, and manage their children independently.

## Verification Plan
1. Launch UI, navigate to "Sentinel Groups", and create a new group.
2. Verify exactly 1 Sentinel Group is visible, and the 3 children are *not* visible in the standard "Programs" list.
3. Click "Start" on the Sentinel Group. Verify the orchestrator engages and activates the child program matching the current regime.
4. Click "Backtest". Verify Chronos processes the group ID and successfully backtests across the 3 children via regime shifting.
