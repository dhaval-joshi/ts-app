# Autonomous Sentinel Architecture

The current implementation of Sentinel Groups acts as a router that switches between three statically-configured child programs based on the market regime. The new goal is to make the Sentinel **fully autonomous**—meaning the user only provides Capital and the Underlying Index, and the Sentinel mathematically determines the optimal trade type, entry triggers, stop losses, and targets using real-time Greeks and market volatility.

## Proposed Autonomous Logic

When the Orchestrator evaluates the market, it will dynamically build and deploy the child program configuration based on the active regime. Along with Dynamic Stops, the Sentinel will deploy **Smart Exit** mechanisms (e.g., dynamic trailing stops based on realized IV crush or momentum exhaustion) to ensure minimal loss of funds.

### 1. SIDEWAYS Regime (Theta Collection)
- **Trade Type**: Short Straddle (Sell ATM Call & Sell ATM Put).
- **Entry Logic**: Enters when Implied Volatility Rank (IVR) is above a certain threshold (e.g., > 30%), ensuring sufficient premium exists to be collected.
- **Dynamic Stop Loss**: Instead of a static percentage, the stop loss is dynamically set to `1.5 * Implied Volatility (IV) implied daily move`.
- **Dynamic Target**: Set to capture 50% of the initial premium collected.

### 2. DIRECTIONAL Regime (Trend Following)
- **Trade Type**: Long Single Leg (Buy ATM Call if bullish, Buy ATM Put if bearish).
- **Entry Logic**: Uses an automated ORB (Opening Range Breakout) or moving average crossover signal to determine the direction.
- **Dynamic Stop Loss**: Set to the low of the breakout candle or a static 15% trailing stop to protect capital while letting the trend run.
- **Dynamic Target**: Open-ended with an aggressive trailing stop (`trail_by: 5%`) to capture maximum directional momentum.

### 3. VOLATILE Regime (Vega/Delta Expansion)
- **Trade Type**: Long Straddle (Buy ATM Call & Buy ATM Put).
- **Entry Logic**: Enters when IVR is low (e.g., < 30%) and historical volatility is expanding, anticipating a violent price expansion.
- **Dynamic Stop Loss**: Tight 15% stop loss to cut theta bleed quickly if the anticipated expansion doesn't materialize.
- **Dynamic Target**: 40% initial target with trailing enabled to ride massive gaps.

## Required Changes

### [MODIFY] `frontend/programs.js` & `index.html`
- Remove the complex strategy configuration tabs (Sideways, Directional, Volatile) from the New Sentinel Group modal.
- The modal will now only ask for: Name, Underlying Index, and Capital Allocation.

### [MODIFY] `backend/models.py`
- Remove the `strategies` dictionary from `SentinelGroupConfig`.

### [MODIFY] `backend/program_manager.py`
- Update `_sync_sentinel_children()` to strip out static configurations. The children will be initialized with a "Dynamic/Autonomous" flag.

### [MODIFY] `backend/sentinel_orchestrator.py`
- Overhaul the tick loop to fetch live Greeks (`om.fetch_greeks_snapshot()`) and calculate the dynamic stops/targets.
- When shifting regimes, the Orchestrator will programmatically build the execution parameters (Buy vs. Sell, specific entry mode, dynamic offsets) and inject them directly into the active child program before it trades.

> [!IMPORTANT]
> **Margin Implications & Capital Safeguards**
> Switching to autonomous logic means the Sentinel will execute Short options (selling) during Sideways regimes. Shorting options requires significantly higher margin (typically ₹1.2L - ₹1.5L per lot for Nifty) than buying.
> **Safeguard**: The Sentinel Orchestrator will calculate the required margin before entry. It will strictly operate within an **Isolated Capital Sandbox** (`Base Capital Allocation + Net Realized PnL` from all its historical trades). Even if the broker account has excess free margin, the Sentinel will not touch it. If its internal available capital is insufficient to deploy the optimal strategy, the Sentinel will **skip the entry**, display a warning message on the Sentinel card in the UI, and wait for the next viable opportunity.

## User Review Required

Does this autonomous mathematical framework align with your vision for the Sentinel? Once you approve, I will strip out the manual UI configurations and bake this quantitative logic directly into the Orchestrator.
