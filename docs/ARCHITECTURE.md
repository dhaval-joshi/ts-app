# Architecture

## Product

Tradejini Trading Station is a self-hosted NSE F&O trading application with:

- Regular OMS for manual single orders.
- Advanced OMS for automated Programs.
- Paper and Live execution modes.
- Local persistence under `data/`.
- A plain HTML/JavaScript frontend with no frontend build step.

The detailed behavior and current feature inventory remain in `README.md`.

## Major backend boundaries

```text
Frontend
   |
   v
backend/main.py
   |
   +--> ProgramManager ---------> OrderManager
   |                                  |
   |                                  +--> BrokerClient
   |                                  |       |
   |                                  |       +--> TradejiniClient (Live)
   |                                  |       +--> PaperBrokerClient (Paper)
   |                                  |
   |                                  +--> Store / factsheets / failure log
   |
   +--> Regular OMS ------------> OrderManager
   |
   +--> StreamManager ----------> Tradejini streaming SDK
   |
   +--> Heartbeat / reconciliation / script master
```

## Critical ownership rules

- `backend/order_manager.py` owns order lifecycle behavior.
- `backend/program_manager.py` owns Advanced OMS cycle orchestration.
- `backend/models.py` is the schema/validation authority for persisted request/config shapes.
- `backend/store.py` owns disk persistence.
- `backend/broker_interface.py` defines the broker abstraction.
- `backend/tradejini_client.py` is the Live broker implementation.
- `backend/paper_broker.py` is the Paper broker implementation.
- `backend/clock.py` is the backend time authority.
- `backend/entry_signals.py` contains pure entry-gate logic.
- `backend/program_safeguards.py` contains pure safeguard logic.

## Order exit architecture

Broker-side OCO/conditional exits are retired. The application watches market
data locally and sends a plain market close when a configured trigger is crossed.
Trailing and exit-trigger evaluation are local application behavior.

Do not introduce a second exit mechanism without explicit architectural approval.

## Persistence

The application uses JSON files under `data/`, including orders, Programs,
Strategies, Risk Groups, factsheets, reconciliation reports, failures, market
holidays, and script-master data. Writes are designed to be atomic.

Never treat the `data/` directory as disposable source code. It is runtime state
and is excluded from Git.

## Live vs Paper

Both modes intentionally share the same `OrderManager` path. Differences belong
behind the broker abstraction rather than in duplicated order-management logic.

## Frontend

The frontend is vanilla HTML/JavaScript/CSS with scripts sharing a global page
scope. A change to shared helpers can therefore affect multiple pages. See
`.claude/rules/frontend-conventions.md` for Claude-specific frontend guidance
and preserve the same architectural constraints when working with Codex.
