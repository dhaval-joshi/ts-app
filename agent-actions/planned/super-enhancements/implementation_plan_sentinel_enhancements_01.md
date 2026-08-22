# Implementation Plan: Iteration 1.3 (Sentinel Orchestrator)

Provide a brief description of the problem, any background context, and what the change accomplishes.
During a market regime shift, if decentralized Programs are configured to use 100% of available capital (`sizing_mode="capital"`), they will suffer a "Margin Race Condition." The program attempting to enter the new regime will be rejected for insufficient funds if the previous program has not yet finished settling its exit orders. 
To solve this without bloating existing core logic, and to perfectly preserve the future "Super Program" vision for multi-strike and multi-broker scaling, we will introduce a dedicated Sentinel Orchestrator.

## Open Questions / Design Feedback

1. **Multi-Strike Capital Spreading (Your Question)**
   *My Take:* You should **not** force a single Program to manage multiple strike pairs (e.g., ATM, OTM1, OTM2) internally. If a single Program tracks 6 different option legs simultaneously, the Stop Loss and Trailing logic becomes an absolute nightmare of complexity. 
   *The Solution:* This is exactly where the **Super Program** comes in! In the future, the Super Program will hold a massive capital pool (e.g., 20L). It will dynamically spawn **multiple standard Programs** (e.g., 5L to Broker A on ATM, 5L to Broker A on OTM1, 10L to Broker B on ATM). The core `program_manager.py` stays incredibly simple and lightning fast because it still only manages 1 CE/PE pair per Program. The Super Program just acts as a master allocator, distributing capital across multiple vanilla Programs. Does this microservice approach to multi-strike sound right to you?

2. **Is `program_manager.py` doing too much? (Your Question)**
   *My Take:* You are 100% correct. `program_manager.py` is already heavy. Shoving state-machine logic (waiting for funds to settle) into it violates the Single Responsibility Principle. 
   *The Solution:* We will create a completely new service: `backend/sentinel_orchestrator.py`. This service will sit *above* `program_manager.py`. It will subscribe to regime shifts, and when a shift happens, it will command `program_manager` to flatten the old program, wait patiently for the broker margin to update, and then command `program_manager` to force-start the new program. 

## Proposed Changes

---

### Backend Data Models

#### [MODIFY] [models.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/models.py)
- Add `sentinel_group_id: Optional[str] = None` to `ProgramConfig`.
- This simple string allows the Orchestrator to logically group mutually exclusive programs (e.g., Sideways, Directional, Volatile) that share the same capital pool lock.

---

### Backend Core Engine

#### [NEW] [sentinel_orchestrator.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/sentinel_orchestrator.py)
- Create `SentinelOrchestrator` class running its own asynchronous background loop.
- **State Machine**: It tracks the state of each `sentinel_group_id` (`IDLE`, `FLATTENING`, `AWAITING_MARGIN`, `DEPLOYING`).
- **Logic**: 
  - Listens to `RegimeClassifier` updates.
  - If regime shifts to `DIRECTIONAL`, it looks up the active `sentinel_group_id`.
  - Commands `program_manager.flatten_program(sideways_program)`.
  - Enters a rapid polling loop calling `tradejini_client.get_margins()` until the capital is released.
  - Commands `program_manager.force_start_cycle(directional_program)` immediately once funds are available.

#### [MODIFY] [program_manager.py](file:///c:/Users/dhava/OneDrive/___Personal/___trading_zone/vibe-with-claude/tradejini-trading-station/ts-app/backend/program_manager.py)
- Expose a `force_start_cycle(program_id)` method that bypasses the strict `program_schedule.py` time clocks, allowing the Orchestrator to forcefully enter a trade the exact millisecond the margin is released.

## Verification Plan

### Automated Tests
- Mock a regime shift during a running backtest in `chronos.py` to ensure the Orchestrator intercepts the shift, halts the first trade, simulates a 2-second margin delay, and fires the second trade successfully.

### Manual Verification
- Deploy to paper trading mode. Manually override the Sentinel regime via the UI and watch the logs to confirm the Orchestrator cleanly squashes the active program and successfully rolls the dynamic capital into the new program without triggering a margin error.
