# Plan: Iteration 1.1 (Sentinel) - Regime-Aware Engine

## Objective
Enhance the existing trading system by introducing a regime-aware entry and exit system. The "Sentinel" classifier will continuously assess market conditions (Side-ways, Directional, Volatile) and physically block programs from entering if the market conditions do not support the program's designated strategy. It also acts defensively, liquidating positions preemptively if the regime suddenly shifts against them.

## Current Behavior
The system blindly opens programs if time schedules and standard safeties pass, operating independently of the overarching market environment (e.g. attempting to run a sideways straddle in a high-volatility breakout).

## Proposed Design

### 1. Data Model & Cache
- Add `execution_mode` (manual_config vs sentinel) and `target_regime` to `ProgramConfig`.
- Implement a `historical_bars` table in SQLite (`store.py`) to cache 1-minute interval data to avoid fetching 3 days of historical data from Tradejini on every restart.

### 2. Regime Classifier (Sentinel)
- Create `backend/regime_classifier.py`.
- Computes ADX, ATR, and True Range using Wilder's Smoothing.
- Emits states: `STATE_SIDEWAYS`, `STATE_DIRECTIONAL`, `STATE_VOLATILE`, `STATE_UNKNOWN`.

### 3. Entry Gates
- Modify `evaluate_entry` in `backend/entry_signals.py`.
- Programs targeting specific regimes (e.g., `SIDEWAYS`) are gated from entering if Sentinel output differs.

### 4. Smart Exits
- Modify `order_manager.py`.
- Continuous streaming monitoring: If Sentinel shifts regimes away from a program's target (e.g., Directional -> Volatile), Sentinel force-closes the position.
- If Sentinel loses data (broker downtime), safely halts new entries and force-closes active Sentinel-managed positions.
- Plumb ATR into trailing logic (`_maybe_trail`) for wider dynamic stops in highly volatile environments.

## Verification Plan
1. Validate syntactic correctness and build logic.
2. Ensure Sentinel correctly fetches and caches delta data to SQLite instead of full pulls on restart.
3. Simulate `order_manager.py` using `verify-trading-app` skill with fake ticks to ensure regime shift successfully forces a market-close.

## Scope
- Focus exclusively on Regime awareness and Sentinel entry/exit gating. No changes to the actual option buying strategies themselves.
