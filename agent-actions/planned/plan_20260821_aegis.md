# Implementation Plan: Iteration 1 (Aegis)

## Objective
Implement Iteration 1 (Aegis) to establish a robust, disaster-resilient foundation for the trading platform. This iteration focuses on extreme state safety and loss prevention.

## Proposed Changes

### 1. SQLite Migration (The Robust Core)
- **Problem**: Atomic JSON writes are safe from corruption but suffer from file-locking latency during high-frequency volatility spikes.
- **Solution**: Completely refactor `backend/store.py` to use a local `sqlite3` database (`store.db`). 
- **Implementation**:
  - Keep all existing function signatures (`save_order`, `list_orders`, `archive_order`, etc.) exactly the same so that the rest of the application remains completely unaware of the change.
  - Create a migration script `scripts/migrate_json_to_sqlite.py` to port existing JSON files into the DB so the user doesn't lose current state.
  - Tables: `orders`, `strategies`, `programs`, `risk_groups`, `factsheets`, `reconcile_reports`, `signal_history`. Each table will have a string `id` (or `date`) and a JSON `data` text column.

### 2. Global Kill Switch
- **Problem**: In a flash-crash, the trader must manually close multiple positions and programs, which takes precious seconds.
- **Solution**: Implement a Global Kill Switch.
- **Implementation**:
  - Backend (`app.py`): New `POST /api/kill-switch` endpoint.
  - Logic: Calls `program_manager.halt_all_programs()`, loops through all active orders and triggers their square-off, and immediately archives them.
  - Frontend (`admin.js` / `index.html`): Add a prominent red `[KILL SWITCH]` button at the top of the dashboard.

### 3. Historical Data Pipeline (EOD)
- **Problem**: We are currently losing all the valuable intra-day Option Chain and OHLCV data when the market closes, preventing future ML training.
- **Solution**: Add a background pipeline.
- **Implementation**: 
  - Add `backend/data_archiver.py` which runs continuously. Every day at 15:35, it dumps the current state of `script_master.instruments` (which contains LTP and Greeks for all tracked tokens) into a new `data/historical/` folder as a compressed JSON or Parquet file.

### 4. Real-time IVR and VWAP
- **Implementation**: Update `script_master.py` to continuously calculate basic VWAP based on `volume` and `average_price` fields from the incoming tick data, appending it to the instrument state so it can be queried by the UI or strategy engine.

## Verification
- Run the migration script to ensure active orders move to SQLite.
- Ensure the platform boots and loads orders seamlessly.
- Trigger the Kill Switch in Paper Mode and ensure all programs halt and orders close.
