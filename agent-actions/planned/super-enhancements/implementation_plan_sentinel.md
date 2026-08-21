# Goal: Iteration 1.1 (Sentinel) - The Regime Engine

Transform the trading system from a static, rigid ruleset into a dynamic, regime-aware algorithmic engine. The system will categorize the market into specific states (Regimes) and dynamically adjust both **Entry** and **Exit** behaviors to match the current market conditions.

## Super-Enhancements

### Codename Logic: "Sentinel"
A **Sentinel** is a guard whose primary purpose is to stand watch, observe the environment, and dictate who is allowed to pass and who is turned away. 

This codename was chosen because Iteration 1.1 fundamentally shifts the system from blindly executing instructions (like a soldier) to observing the environment first (like a guard). The `RegimeClassifier` acts as the Sentinel for your portfolio:
- It **observes** the market weather (ADX, ATR, ORB).
- It **dictates** which strategies are allowed to enter the market (e.g., turning away Sideways strategies when the market is Directional).
- It **protects** active trades by preemptively exiting them if the environment turns hostile, rather than blindly waiting for a static trailing stop to be hit.

## User Review Required

> [!IMPORTANT]  
> This is a major architectural shift. The system will no longer blindly execute a strategy just because a time window opens or an indicator crosses. It will only execute if the *Market Regime* supports that specific strategy. Please review the proposed regimes and exit adjustments carefully to ensure they align with your trading philosophy.

## Open Questions

> [!WARNING]  
> 1. **Data Feeds**: Calculating ADX and ATR requires live 1-minute OHLCV data for the index. We currently bootstrap this via Tradejini's historical API. Are you comfortable with the system pulling the last 3 days of 1-minute data on startup to prime these indicators?
> 2. **Fallback Behavior**: If the broker API fails to deliver index data and the Regime Classifier goes "blind", should the Sentinel default to a specific gate (e.g., `STATE_SIDEWAYS` as the most conservative), or halt all entries entirely to protect capital?

## Proposed Changes

We will introduce a central `RegimeClassifier` that runs continuously and acts as the master gatekeeper for the entire system.

### 1. The Regime Classifier (The Brain)
We will build `backend/regime_classifier.py` which computes a live state based on Index price action:
- **STATE_SIDEWAYS**: ADX is low (< 20). Price is chopping in a range.
- **STATE_DIRECTIONAL**: ADX is high (> 25). Price is breaking the Opening Range (ORB) or establishing a clear trend.
- **STATE_VOLATILE**: VIX is spiking, ATR (Average True Range) is expanding rapidly.

### 2. Regime-Based Entry Gates
We will refactor `backend/entry_signals.py` to use the Regime Classifier:
- Instead of parallel racing, the `RegimeClassifier` dictates the active gate.
- If a Program is configured as a "Directional Strategy" (e.g., Long Straddle Breakout), it is physically blocked from entering if the Classifier outputs `STATE_SIDEWAYS`.
- If a Program is a "Sideways Strategy" (e.g., Short Iron Condor), it is blocked if the Classifier outputs `STATE_DIRECTIONAL` or `STATE_VOLATILE`.

### 3. Regime-Based Smart Exits
To answer your question: *Yes, exits can and should be regime-aware.* We will upgrade `backend/order_manager.py` to support dynamic, regime-driven exits.

**A. Volatility-Adjusted Trailing Stops**
Static trailing stops (e.g., "trail by 10 points") fail because 10 points in a quiet market is huge, but 10 points in a volatile market is nothing (whipsaw).
- The trailing logic will be updated to read the current ATR from the Regime Classifier.
- In `STATE_VOLATILE`, trailing stops automatically widen (e.g., 2x ATR) to avoid premature stop-outs.
- In `STATE_SIDEWAYS`, trailing stops tighten because mean-reversion happens quickly.

**B. The "Smart Exit" (Preemptive)**
We will introduce an exit condition that fires *before* your stop loss is hit, based purely on regime changes.
- **Trend Exhaustion**: If you are in a Directional trade and the Regime Classifier suddenly downshifts from `STATE_DIRECTIONAL` to `STATE_SIDEWAYS`, the system will preemptively exit the trade to protect profits, rather than waiting for the price to fall back and hit your trailing stop.
- **Theta Bleed Exit**: If you are holding Long options and the market enters `STATE_SIDEWAYS` for more than 45 minutes, the system auto-exits. Time decay (Theta) will destroy long premium in sideways markets, even if the price hasn't hit your stop loss.

---

### Backend Component Breakdown

#### [NEW] `backend/regime_classifier.py`
- Houses the math for calculating ADX, ATR, and ORB status.
- Exposes a `get_current_regime(symbol_id)` function that returns the active `STATE`.

#### [MODIFY] `backend/entry_signals.py`
- Removes static, isolated gates.
- Wraps entry logic into `evaluate_directional_gate`, `evaluate_sideways_gate`, etc.
- Only evaluates the gate that matches the current regime.

#### [MODIFY] `backend/order_manager.py`
- Updates `handle_l1_tick()` to check for preemptive "Smart Exits" (Regime changes).
- Updates trailing stop calculation to ingest ATR/Volatility multipliers.

## Verification Plan

### Automated / Paper Verification
- We will deploy a Paper Trading program that runs all day.
- We will monitor the logs to verify that the `RegimeClassifier` correctly transitions states as the market moves from morning volatility -> midday chop -> afternoon trend.
- We will verify that trailing stops dynamically adjust their distance based on the live ATR values.

### Manual Verification
- You will be able to view the live, active Regime (e.g., "Current Market: 🌪️ VOLATILE") directly on the frontend dashboard to confirm it matches your own read of the market chart.
