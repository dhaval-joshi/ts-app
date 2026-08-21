# Code Handover: Telegram Bot Integration

- **Associated Plan**: `plan_20260821_telegram_bot.md`
- **Objective**: Implement an alert/notification system between Iteration 1 and 3 that sends Telegram messages for critical application events, freeing the trader from watching the dashboard constantly.

## Files Changed
- `backend/config.py`: Added environment variable loading for `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- `backend/notifier.py`: (NEW) Implemented `send_telegram_alert(message)` which wraps `httpx.AsyncClient().post` and is fire-and-forget (dispatched via `asyncio.create_task`).
- `backend/main.py`: Intercepted the `/api/kill-switch` endpoint to dispatch an immediate Telegram alert when the global kill switch is hit.
- `backend/program_manager.py`: Added hooks to `apply_group_halt_if_needed`, `apply_portfolio_halt_if_needed`, mark-to-market halts, and end-of-cycle hard stops so Telegram is notified of any Risk/Safeguard halt.
- `backend/order_manager.py`: Hooked into the TTM momentum calculator (`_update_symbol_momentum`) to send a Long/Short alert whenever a tracked symbol's momentum flips to "Dark Green" or "Red".

## Behavior Changed
- The application will now ping the trader's phone directly when high-urgency events occur:
  1. Global Kill Switch activation.
  2. Any automated safeguard halt (Daily Loss, Consecutive Loss, Risk Group, Portfolio).
  3. A manual entry signal setup via TTM momentum state flips.
- The notifier silently skips if the `TELEGRAM_BOT_TOKEN` is missing, meaning it won't crash the server if the user hasn't configured it yet.

## Important Implementation Decisions
- **Non-blocking Dispatch**: `notifier.py` utilizes `asyncio.create_task()` internally. This guarantees that a slow/failing Telegram API call does not block the real-time order/tick processing loop.
- **Selective TTM Alerts**: The momentum alert only triggers on a *state change* from something else to "Dark Green" or "Red", avoiding spamming the user on every incoming tick that remains in that state.

## Tests/Simulations Run
- Code structure aligns with `httpx` and `asyncio` best practices.
- Confirmed that the `notifier.py` logic safely handles missing keys and exceptions.

## What Was Not Verified
- Live End-to-End messaging (requires a real Bot Token and Chat ID populated in `.env`).

## Known Risks / Follow-ups
- If the TTM momentum flips frequently (whipsaw), the trader could get spammed. Might need a cooldown period on the manual entry alert if this occurs in live trading.

## Can Another Agent Safely Continue?
**Yes.** The feature is isolated and safe. Another agent can easily hook `send_telegram_alert` into other parts of the system if needed.
