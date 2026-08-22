# Autonomous Sentinel Implementation

The Sentinel Macro-Program architecture has been completely overhauled to run entirely autonomously based on quantitative market data, as detailed in our implementation plan.

## Completed Work

### 1. Stripped Static UI & Data Models
- **Frontend**: Removed the intricate strategy sub-tabs (Sideways, Directional, Volatile) from the Sentinel Group creation modal. The user now only defines the Name, Underlying Index, and Total Capital Allocation.
- **Backend Models**: Purged the `strategies` dictionary from the `SentinelGroupConfig` data model, enforcing strict autonomy.

### 2. Upgraded Sentinel Orchestrator
- **Dynamic Configuration Injection**: The orchestrator now actively calculates IV/IVR from the OrderManager's real-time Greeks stream.
  - **SIDEWAYS**: Deploys a Short Straddle (`trade_type: sell`). Dynamic stops are calculated as 1.5x the implied daily move. Target at 50%.
  - **DIRECTIONAL**: Deploys an ORB breakout Single Leg. Uses tight Smart Exits trailing stops.
  - **VOLATILE**: Deploys a Long Straddle with a very tight stop and large trailing target.
- **Strict Entry Validation**: If the current Greeks contradict the expected regime (e.g. IVR < 30 in a Sideways market), the orchestrator blocks the entry and waits for optimal conditions.

### 3. Isolated Capital Sandbox
- **Cumulative PnL Tracking**: Before any trade is executed, the Orchestrator aggregates the net realized PnL across all historical cycles of the Sentinel Group's active children.
- **Dynamic Margin Validation**: Base Capital + Realized PnL is checked against the required margin for the trade (e.g. ₹130,000 per lot for Short Straddles). If there is insufficient capital, the trade is blocked with a clear warning, preventing catastrophic margin calls.

### 4. Backtest Engine (Chronos) Updates
- Upgraded the `chronos.py` engine to seamlessly handle `sell` positions. It now calculates sizing based on margin limits rather than cash debits, and correctly inverses the PnL calculation (`(entry - exit) * qty`) for accurate historical simulations of our shorting strategies.

## Verification

An internal end-to-end simulation was built and executed. The results verified:
1. Sentinel Groups auto-generate 3 clean child programs upon creation.
2. The Orchestrator correctly calculated the sandbox (₹100k capital), noticed it was short of the ₹130k required for a Short Straddle, and safely blocked the entry.
3. Upon increasing capital to ₹200k, the Orchestrator successfully bypassed the safeguard, generated the dynamic Greeks-based rules, and injected them into the child program before launching the trade cycle.

> [!TIP]
> The Autonomous Sentinel is now live and will make decisions purely based on the index's regime and Greeks snapshots, keeping strictly to its allocated sandbox capital!
