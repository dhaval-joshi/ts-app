import asyncio
import logging
import httpx

from . import config

log = logging.getLogger("tradejini.notifier")

async def send_telegram_alert(message: str):
    """
    Fire-and-forget helper to send a Telegram message.
    Silently fails (with a log) if token/chat_id are not set,
    so it never crashes the main execution loop.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.debug("Telegram alert skipped (keys not configured): %s", message)
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    async def _dispatch():
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=5.0)
                resp.raise_for_status()
                log.info("Telegram alert sent successfully.")
        except Exception as e:
            log.warning("Failed to send Telegram alert: %s", e)

    # Dispatch to background to prevent blocking the caller
    asyncio.create_task(_dispatch())
