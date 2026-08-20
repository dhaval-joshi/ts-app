# Architect's Target Architecture & Progressive Roadmap (v2)

## The Foundation: Building on the Current System
The current Tradejini Trading Station (FastAPI, Python Asyncio, JSON store) serves as the exact starting point. These iterations **improve and evolve** the existing system. Over time, the monolithic script will be gradually refactored into modular components, eventually migrating to a distributed cloud architecture.

## Phase 1: Local Foundation & Hybrid Augmentation

### Iteration 1: Robust Core (SQLite Migration)
- **Base Architecture**: Migrate from atomic JSON writes (`store.py`) to a local SQLite database (e.g., using `SQLAlchemy` or `aiosqlite`).
- **Development Goal**: Eliminate file-locking bottlenecks, ensuring the system can handle higher frequency updates without corrupting order state.

### Iteration 2: Async Decoupling (The Augmentation Engine)
- **Base Architecture**: Decouple the Strategy evaluation loop from the Order Execution loop. Introduce `asyncio.Queue` channels to pass signals internally. Integrate the `python-telegram-bot` library to push alerts and listen for one-click execution commands via webhooks.
- **Development Goal**: Ensure that UI API calls or strategy processing never block the execution thread, laying the groundwork for true co-pilot execution remotely via Telegram.

### Iteration 3: Local Event-Driven Pub/Sub
- **Base Architecture**: Integrate a local Redis instance. Migrate the WebSocket data feed to publish directly to Redis topics (e.g., `ticks:NIFTY`).
- **Development Goal**: The execution engine, UI backend, and strategy engine become distinct local subscribers. This guarantees zero-latency UI updates during violent market moves.

## Phase 2: Data Dominance & Autonomous Scaling

### Iteration 4: The Time-Series Database
- **Base Architecture**: Introduce InfluxDB or QuestDB running locally via Docker.
- **Development Goal**: Replace rudimentary CSV archiving with a high-performance TSDB capable of ingesting and querying massive tick data for local backtesting.

### Iteration 5: Multi-Process Isolation
- **Base Architecture**: Refactor the backend into a multi-process architecture (using Python's `multiprocessing`). 
- **Development Goal**: Isolate specific strategy engines so a critical failure in one algorithm does not crash the entire platform or impact other running positions.

### Iteration 6: Shared Memory Micro-Structure
- **Base Architecture**: Implement Memory-Mapped Files (`mmap`) or ZeroMQ for ultra-fast Inter-Process Communication (IPC).
- **Development Goal**: Share the live L1/L2 order book across isolated processes in microseconds without serialized network overhead.

## Phase 3: The Cloud Transition

### Iteration 7: Hybrid Cloud Data Pipeline
- **Base Architecture**: Connect the local TSDB to an AWS S3 / GCP Cloud Storage data lake. 
- **Development Goal**: Offload heavy machine learning training to cloud compute while keeping live trading execution purely on the local machine.

### Iteration 8: Cloud Execution Staging (Kubernetes)
- **Base Architecture**: Containerize the Python execution engine. Deploy to a cloud Kubernetes cluster. Implement a FIX API connection to replace the retail REST/WebSocket broker API.
- **Development Goal**: Run the cloud instance in "shadow mode" (paper trading) to compare its latency and execution quality against the local live machine.

### Iteration 9: Full Cloud Transition (Microservices)
- **Base Architecture**: Transition the execution engine to gRPC-based microservices. The local machine becomes purely a thin client (React UI), alongside a **dedicated React Native / Flutter Mobile App** communicating via a unified API Gateway.
- **Development Goal**: Achieve 100% cloud-based autonomous execution, fully monitorable and controllable from a mobile device anywhere in the world.

### Iteration 10: The HFT Powerhouse
- **Base Architecture**: Rewrite the critical Execution and Order Routing engines in C++ or Rust. Deploy on servers co-located at the NSE exchange.
- **Development Goal**: Achieve institutional-grade sub-millisecond latency.
