# Implementation Plan: Iteration 1.1 & 1.2

You are absolutely right. My apologies—I was thinking about deep historical backtesting (e.g., years in the past). If we restrict the backtest window to the **lifespan of the currently active contracts** (e.g., the current weekly expiry for Nifty/BankNifty), your logic holds up perfectly! 

Here is the updated architectural plan for both iterations.

---

## Iteration 1.1: Sentinel Configuration Overrides

### Proposed Design
- **Backend Configuration:** Introduce a global `SentinelConfig` model (stored in `backend/config.py` or SQLite) that houses the parameters driving the `RegimeClassifier` (e.g., ADX period, ADX threshold for Directional, ATR multipliers for Volatile, etc.). We will store the "Factory Default" settings immutably, and allow a "Modified" override state.
- **Frontend Dashboard:** Add a "Sentinel Settings" panel to the UI. If the active configuration diverges from the factory defaults, the UI will display a prominent visual indicator (e.g., a "Sentinel (Modified)" badge). 
- **Diff View:** Clicking the badge or panel will reveal a side-by-side diff of the Default vs. Modified values, allowing you to easily reset to default or tweak parameters.

---

## Iteration 1.2: Native Backtesting Engine
**Codename:** **Project Chronos** 

### How it will work:
1. **Entry Simulation:** We fetch historical 1-minute chart data for the Index (e.g., Nifty) for the past few days. The system runs the `RegimeClassifier` and `evaluate_entry` logic over this historical index data tick-by-tick.
2. **Dynamic Strike Selection:** When a simulated entry triggers (e.g., yesterday at 10:15 AM, Nifty was at 23500), we query `script_master` to dynamically find the ATM strikes for the current active expiry (23500 CE & PE).
3. **Exit Simulation:** We fetch the historical 1-minute chart data specifically for the chosen CE and PE strikes, starting from the entry timestamp. We then feed this historical option data into `handle_l1_tick` and `_maybe_app_market_exit` to simulate Stop Losses, Targets, and dynamic ATR Trailing.
4. **Fixed Capital Constraint:** The simulation bypasses the live `get_basket_margin` check and instead assumes a fixed, user-defined starting capital pool.

### User Review Required (Caveats for Chronos)
> [!WARNING]
> **Data Availability Limit:** We can only backtest as far back as the current contracts existed (typically a few days for weeklies, up to a month for monthlies).

### Overcoming the Greeks Limitation
You are completely right again. Greeks and Implied Volatility are just derivatives of the Black-Scholes-Merton model. Since we have:
1. Spot Price (Index historical data)
2. Option Premium (Option historical data)
3. Strike Price (Known from selection)
4. Time to Expiry (Calculated from timestamp to expiry date)
5. Risk-free rate (Assumed fixed, e.g., 6%)

We will implement a localized **Black-Scholes / Newton-Raphson calculator** (or use a library like `py_vollib`) during backtesting to mathematically derive the exact IV and Greeks for any given historical minute. This perfectly bridges the gap and ensures entry gates relying on IV ranks function identically to live trading.

## Next Steps

If you are aligned with this refined approach:
1. I will execute **Iteration 1.1** right now (Sentinel overrides and UI diffing).
2. We can tackle **Project Chronos (Iteration 1.2)** in the immediate following step.

Click **Proceed** to begin Iteration 1.1!
