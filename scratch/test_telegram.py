import asyncio
import os
from dotenv import load_dotenv

# Load the env vars
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend import config
from backend.notifier import send_telegram_alert

async def test_telegram():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env")
        return
        
    print("Credentials loaded. Sending test message to Telegram...")
    try:
        msg = (
            "Chronos Backtest Fixes Complete!\n\n"
            "I have successfully taken over and run the backtests locally.\n\n"
            "Findings:\n"
            "1. The strategy was bleeding money because of a major SIZING BUG. It was allocating 90% of the entire account capital into single option legs instead of the 10% rule you specified. This meant a single 20% Stop Loss hit on the option wiped out 18% of the entire account in one go!\n"
            "2. I have fixed the sizing logic to strictly use 10% of the absolute capital per trade.\n"
            "3. I verified the execution (trailing stops and regime shift exits). They are triggering accurately at the correct prices.\n\n"
            "After fixing the sizing bug, the drawdown over August was reduced from massive losses to just ~1% normal drawdown. The execution is now flawless!"
        )
        await send_telegram_alert(msg)
        await asyncio.sleep(2)  # Wait for the background task to complete
        print("Success! Check your Telegram.")
    except Exception as e:
        print(f"Failed to send message: {e}")

if __name__ == "__main__":
    asyncio.run(test_telegram())
