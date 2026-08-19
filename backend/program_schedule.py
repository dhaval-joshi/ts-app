"""
Pure logic (no I/O, no broker calls) for Program SCHEDULING -- a
genuinely different question from program_safeguards.py's: safeguards
decide whether a Program SHOULD stop because performance is bad; this
module decides whether a Program is even ELIGIBLE to consider a new cycle
right now, independent of performance. Kept separate on purpose (they
answer different questions) and kept pure on purpose, mirroring
program_safeguards.py's "pure functions, tested exhaustively" approach --
this too gates real money placing itself automatically, so the same
rigor applies.

Three cases this is built to cover precisely (from the actual design
conversation that shaped it):
  - "Start at 9:30, not 9:15" -> a daily start/end window
  - "Crypto: run continuously until a hard-stop, no day-start gate at all"
    -> the `continuous` flag, which ignores the window/day-filter entirely
  - "Only on expiry day, 2:30-3:15" -> `days="expiry_day"` combined with
    a narrow same-day window
"""
from datetime import datetime

from . import clock


def can_trade_now(
    *,
    continuous: bool,
    start_time: str,
    end_time: str,
    days: str,
    now: datetime,
    expiry_dates: set,
) -> tuple[bool, str | None]:
    """Returns (eligible, reason_if_not). Does NOT consider safeguards,
    an already-active cycle, or the archived flag -- those are separate
    gates program_manager.py checks alongside this one."""
    if continuous:
        return True, None

    try:
        sh, sm = (int(x) for x in (start_time or "09:15").split(":"))
        eh, em = (int(x) for x in (end_time or "14:55").split(":"))
        start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    except (ValueError, AttributeError):
        start_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        end_dt = now.replace(hour=14, minute=55, second=0, microsecond=0)

    if now < start_dt:
        return False, f"before today's {start_time} start time"
    if now >= end_dt:
        return False, f"past today's {end_time} cutoff"

    if (days or "all") == "expiry_day" and now.date() not in expiry_dates:
        return False, "today is not an expiry day for this Program's underlying"

    return True, None


def inter_cycle_delay_elapsed(*, last_cycle_closed_at: str | None, delay_seconds: int, now: datetime) -> bool:
    """True if enough time has passed since the previous cycle closed --
    or if there's no previous cycle yet, or the configured delay is 0
    (immediate re-entry, the default)."""
    if not last_cycle_closed_at or delay_seconds <= 0:
        return True
    try:
        closed_at = clock.parse_iso(last_cycle_closed_at)
    except ValueError:
        return True  # a corrupt/unexpected timestamp shouldn't be able to permanently block trading
    return (now - closed_at).total_seconds() >= delay_seconds
