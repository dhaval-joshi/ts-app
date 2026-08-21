# Walkthrough: Iteration 1.1 (Sentinel) - Regime-Aware Engine

I've successfully implemented the core features for **Iteration 1.1 (Sentinel)**! The system is now regime-aware and uses historical and live market data to protect capital. 

Here is what was accomplished:

## 1. Data Model & Storage Updates
- Updated `ProgramConfig` in `backend/models.py` to support `execution_mode` (with `"sentinel"` and `"manual_config"` options) and `target_regime`.
- Added the `historical_bars` table to `store.py` to cache historical 1-minute chart data locally. This avoids slamming the Tradejini API to rebuild the history from scratch on every restart. 

## 2. Regime Classifier (Sentinel)
- Created `backend/regime_classifier.py` which actively computes the ADX, True Range, and ATR using Wilder's Smoothing. 
- Sentinel categorizes the market into `STATE_SIDEWAYS`, `STATE_DIRECTIONAL`, and `STATE_VOLATILE`.
- Sentinel handles fetching and caching missed data, automatically recovering when it starts up. If Sentinel loses data (broker issue), it emits a Telegram alert (`SENTINEL BLIND`) to notify you.

## 3. Entry Gates Integration
- Refactored `evaluate_entry` in `backend/entry_signals.py` to ingest the Sentinel parameters. 
- If a Program is configured as `sentinel`, it is **physically blocked** from placing an entry if the current market regime does not match the Program's `target_regime`. 
- If Sentinel is "blind" (UNKNOWN regime due to data failure), the entry gate safely halts the program to protect capital.

## 4. Smart Exits
- Plumbed preemptive regime shift tracking into `_maybe_app_market_exit` inside `backend/order_manager.py`. If Sentinel determines that the market regime has suddenly shifted away from the program's target while an order is open, it will trigger an immediate **Smart Exit** liquidation to close the trade.
- Updated `_trail_leg` logic to integrate the live **ATR** from Sentinel. If the market becomes volatile and the ATR requires a wider stop, Sentinel dynamically widens `trail_by` to `1.5 * ATR` to prevent the trailing stop from being prematurely chopped out in a volatile environment.

## 5. Multi-Agent Handover 
- Generated the mandatory `plan_1787328413_sentinel.md` and `code_1787328413_sentinel.md` artifacts in the repository's `agent-actions/` directory, adhering to the Codex agent handover requirements in `AGENTS.md`.

> [!NOTE]
> Next up, you may want to test this logic in Paper Mode or run the Fake Broker simulation (`verify-trading-app`) to ensure the state transitions perform as expected under synthetic conditions.
