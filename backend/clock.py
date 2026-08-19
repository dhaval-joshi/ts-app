"""
Single source of time for this app. Every "now" and every persisted
timestamp goes through here -- see .claude/plans/keen-splashing-rocket.md
for the full writeup of why.

NSE/BSE/MCX all run on India Standard Time, and every existing stored
timestamp in this codebase (order logs, program logs, factsheets, reconcile
reports) was written as a NAIVE local-clock string on a machine physically
in IST. Hardcoding IST here -- rather than reading the host's local
timezone -- is deliberate: correctness must not depend on where this
process happens to run. A cloud VM in UTC must behave identically to the
current Windows box.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now() -> datetime:
    """Aware, IST. Use everywhere `datetime.now()` was used before."""
    return datetime.now(IST)


def today() -> date:
    """IST calendar date. Use everywhere `date.today()` was used before."""
    return now().date()


def now_iso() -> str:
    """ISO string carrying the +05:30 offset, for persistence. Matches the
    seconds-precision every existing now_iso() helper in this codebase used."""
    return now().isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    """Parses a persisted ISO timestamp, aware or naive.

    A NAIVE string (no offset) is interpreted as IST -- every timestamp
    ever written by this app before this module existed is naive and was,
    in fact, IST wall-clock time. Treating it as anything else would be
    wrong, and comparing it against an aware `now()` without this step
    raises TypeError on the very first order file loaded from disk.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)
