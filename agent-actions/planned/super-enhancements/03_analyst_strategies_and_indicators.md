# Trading Desk Analyst: Best Indicators & Strategy Combinations

## Core Philosophy
We are not aiming for equal distribution across asset classes; we are aiming for **maximum profitability and capital velocity**. 
In the Indian market, this means the overwhelming majority of alpha is currently found in **Index Options (FNO)** due to massive liquidity, weekly expiries, and structural inefficiencies (volatility skews). Stocks and Commodities will serve as supplementary diversifiers.

## 1. Index FNO (The Core Profit Driver)
*Target: 70-80% of Capital Allocation*

**The Strategy: Adaptive Volatility Harvesting (Short Options)**
- **Concept**: Selling premium (Straddles, Strangles, Iron Condors) when implied volatility is rich, and dynamically hedging (Gamma Scalping) when the market moves.
- **Indicators**:
  - **IVR (Implied Volatility Rank) & IVP (Implied Volatility Percentile)**: The ultimate filter. If IVR > 50, we execute mean-reverting short premium strategies.
  - **VIX / India VIX**: Broad market fear gauge. Used to size positions (higher VIX = smaller position size, wider strikes).
  - **VWAP (Volume Weighted Average Price)**: Used for intraday directional bias.
  - **Max Pain**: Open interest clustering to identify likely expiry zones.

**The Setup**:
- *Moderate Risk (Hybrid)*: System detects high IVR and sets up a 1 standard deviation Iron Condor. Sends to UI for manual execution.
- *Low Risk (Autonomous)*: Delta-neutral ATM Straddle executed at 9:20 AM, with an automated combined premium stop-loss of 20%, trailing every 5%.

## 2. Equities (Stocks)
*Target: 15-20% of Capital Allocation*

**The Strategy: Relative Strength Momentum & Gap-and-Go**
- **Concept**: Capitalizing on the first 90 minutes of market open where institutional volume drives massive liquidity imbalances.
- **Indicators**:
  - **Relative Volume (RVOL)**: Identifies stocks trading significantly above their 10-day average volume at the open.
  - **RS (Relative Strength vs Nifty)**: Is the stock outperforming the broader market?
  - **Volume Profile (VPVR)**: Identifying high-volume nodes for entry pullbacks and low-volume nodes for breakout targets.
  - **TTM Squeeze**: Identifying prolonged consolidation before explosive moves.

**The Setup**:
- *Highest Risk (Discretionary)*: System alerts on RVOL > 300% and TTM Squeeze firing on a 5-minute chart. Trader manually assesses the daily chart context and executes a breakout momentum trade via DOM.

## 3. Commodities (MCX - Crude Oil / Gold)
*Target: 0-10% of Capital Allocation (Diversification)*

**The Strategy: Macro Trend Following**
- **Concept**: Commodities trend longer and harder than equities. We avoid intraday noise and focus on capturing large macro moves.
- **Indicators**:
  - **SuperTrend (10, 3)**: Core directional filter.
  - **ADX (Average Directional Index)**: Only take trades when ADX > 25 (confirming a strong trend exists).
  - **EMA Crossovers (20 EMA / 50 EMA)**: Used for entry triggers on pullbacks.

**The Setup**:
- *Low Risk (Autonomous)*: Fully algorithmic trend-following system on Crude Oil. Enters on 15m 20/50 EMA crossover ONLY if ADX > 25. Trails stop behind the 50 EMA.

## Prioritization Matrix (Value vs. Cost)
1. **Immediate Value (Low Cost)**: Implement IVR/IVP calculations on the backend utilizing the current `script_master.py` data to drive options premium screening.
2. **Medium Value (Medium Cost)**: Build the automated 9:20 AM Straddle engine (combining existing Advanced OMS leg logic into a unified time-triggered flow).
3. **High Value (High Cost)**: Integrating real-time Relative Volume (RVOL) scanners across the entire NSE500 stock universe.
