# Implementation Plan: Telegram Notifications (Iteration 1)

## Objective
Implement a Telegram Bot integration to send real-time alerts to the trader without them needing to monitor the dashboard constantly, fulfilling the requirement for an "alert/notification based decision system" between Iterations 1 and 3.

## Verified Current Behavior
- Iteration 1's core Aegis features (SQLite Migration, Global Kill Switch, EOD Historical Data Pipeline, IVR/VWAP indicators) are already fully implemented and integrated in the current codebase.
- There is currently no external notification system; alerts are only visible if the trader is actively watching the UI.

## Proposed Design
1. **Telegram Configuration**:
   - Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `backend/config.py` (read from `.env`).

2. **Notifier Module (`backend/notifier.py`)**:
   - Create an async helper `send_telegram_alert(message: str)` that issues a `POST` request to `https://api.telegram.org/bot<TOKEN>/sendMessage`.
   - The function will fail gracefully (log a warning) if Telegram keys are not configured, so it doesn't crash the trading engine.

3. **Event Hooks**:
   - **Kill Switch**: In `api_kill_switch` (`main.py`), send an alert: *"🚨 KILL SWITCH ACTIVATED 🚨 Halted X programs and closed Y orders."*
   - **Program Safeguards**: In `program_manager.py` (during `_evaluate_safeguards`), if a program halts due to daily loss or consecutive loss limits, send an alert.
   - **Manual Entry Alerts**: In `indicators.py` or where manual alerts are raised (e.g. TTM momentum change or IVR threshold), send an alert so the trader knows to check the app.

## Safety Implications
- Network latency for Telegram API calls must not block the main trading execution loop. We will use `asyncio.create_task()` to fire-and-forget the notification.

## Verification Plan
1. Add mock Telegram keys to `.env`.
2. Trigger the Kill Switch locally and ensure a log entry confirms the Telegram API was hit.
3. Configure a tight daily loss limit on a paper program, let it halt, and verify the safeguard notification is triggered.

## Open Questions
- Do you want notifications for *every* individual order execution (entry/exit), or should we strictly limit it to high-priority events (Kill Switch, Safeguards, Manual Entry Alerts) to avoid spam?
