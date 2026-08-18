"""
All runtime configuration lives in .env (see .env.example).
Nothing here is hard-coded except sane defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

TRADEJINI_API_KEY = os.getenv("TRADEJINI_API_KEY", "").strip()
TRADEJINI_PASSWORD = os.getenv("TRADEJINI_PASSWORD", "")
TRADEJINI_TOTP_SECRET = os.getenv("TRADEJINI_TOTP_SECRET", "").strip()

# Login for the APP ITSELF (separate from the Tradejini credentials above) --
# deliberately simple, plain-text, single-operator: this app has no user
# management, just one shared login gate in front of everything. Fails
# LOUD (see main.py startup) rather than silently running unprotected if
# these are left unset, since "the app requires login" was explicit.
APP_LOGIN_USERNAME = os.getenv("APP_LOGIN_USERNAME", "").strip()
APP_LOGIN_PASSWORD = os.getenv("APP_LOGIN_PASSWORD", "")
SESSION_COOKIE_NAME = "tjstation_session"
SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "7"))

# Tolerate a common .env mistake: pasting the host WITH a protocol prefix
# and/or a trailing slash (e.g. "https://api.tradejini.com/"), which would
# otherwise produce a malformed URL like "https://https://api.tradejini.com"
# -- that fails DNS resolution with a cryptic "getaddrinfo failed" error
# that gives no hint the host string itself is the problem.
_raw_host = os.getenv("TRADEJINI_HOST", "api.tradejini.com").strip()
TRADEJINI_HOST = _raw_host.removeprefix("https://").removeprefix("http://").rstrip("/")

EXIT_CHECK_INTERVAL_SECONDS = int(os.getenv("EXIT_CHECK_INTERVAL_SECONDS", "5"))
TRAIL_MODIFY_MIN_INTERVAL_SECONDS = int(os.getenv("TRAIL_MODIFY_MIN_INTERVAL_SECONDS", "2"))
APP_PORT = int(os.getenv("APP_PORT", "8000"))

DATA_DIR = ROOT_DIR / "data"
ORDERS_DIR = DATA_DIR / "orders"
ARCHIVE_DIR = ORDERS_DIR / "archive"
STRATEGIES_DIR = DATA_DIR / "strategies"
PROGRAMS_DIR = DATA_DIR / "programs"
RISK_GROUPS_DIR = DATA_DIR / "risk_groups"
ORDERS_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
RISK_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

# Market holidays -- a JSON array of "YYYY-MM-DD" strings, e.g. ["2026-10-21", ...].
# Used by the Advanced OMS's expiry-selection logic (program_safeguards.py /
# script_master.py) to count WORKING days correctly, not just skip weekends.
# Optional: an empty/missing file just means holidays aren't excluded yet
# (weekday-only counting), which the app warns about rather than failing.
MARKET_HOLIDAYS_FILE = DATA_DIR / "market_holidays.json"

FRONTEND_DIR = ROOT_DIR / "frontend"

REST_BASE_URL = f"https://{TRADEJINI_HOST}/v2"
