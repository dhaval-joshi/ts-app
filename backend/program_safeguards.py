"""
Pure logic (no I/O, no network, no broker calls) for the Advanced OMS's
safeguard stack, kept in its own module specifically so it can be tested
exhaustively in isolation -- this is the safety-critical part of the whole
Program feature (it decides whether real money keeps trading or stops),
and pure functions are the only way to actually verify decision logic like
this with confidence without a live account to test against.

Layered design, agreed on across a design discussion before any of this
was built:

  THROTTLES (self-resolving, no human needed to resume):
    - max_cycles_per_day -> once crossed, EVERY subsequent cycle that day
      waits `cooldown_minutes` before starting (a standing slow-down for
      the rest of the day, not a one-time pause)
    - portfolio cap being at capacity -> new cycles simply wait for room

  HARD STOPS (a "something's wrong" signal -- stay halted until a person
  looks and explicitly resumes):
    - consecutive_loss_limit reached (per-Program)
    - daily_loss_amount reached (per-Program)
    - portfolio-wide daily loss cap reached (global, STRICT: halts every
      Program, including ones still under their own individual cap --
      correlated underlyings mean a day bad enough to blow the aggregate
      is usually a regime day, not one unlucky Program)

A hard stop NEVER touches currently-open legs -- those keep running their
own SL/Target/trailing exactly as before. A hard stop only means "don't
open a NEW cycle." Flattening what's open is a separate, explicit, human
action (not modeled in this module).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import clock

RUNNING = "running"
HALTED_CONSECUTIVE_LOSS = "halted_consecutive_loss"
HALTED_DAILY_LOSS = "halted_daily_loss"
HALTED_RISK_GROUP = "halted_risk_group"
HALTED_PORTFOLIO = "halted_portfolio"
HALTED_ENTRY_UNAFFORDABLE = "halted_entry_unaffordable"
STOPPED_BY_USER = "stopped_by_user"

HALT_STATUSES = (HALTED_CONSECUTIVE_LOSS, HALTED_DAILY_LOSS, HALTED_RISK_GROUP, HALTED_PORTFOLIO, HALTED_ENTRY_UNAFFORDABLE, STOPPED_BY_USER)


@dataclass
class ProgramRuntimeState:
    """Everything about a Program that changes as it trades, as opposed to
    its ProgramConfig (models.py) which is the user's saved settings."""
    status: str = RUNNING
    trading_day: str | None = None       # ISO date string -- when this no longer matches "today", daily counters reset
    cycles_today: int = 0
    consecutive_losses: int = 0
    daily_realized_pnl: float = 0.0
    cooldown_until: str | None = None    # ISO datetime string, or None
    active_cycle_id: str | None = None   # non-None while a cycle's two legs are live
    active_cycle_started_at: str | None = None  # ISO datetime -- when the current cycle started, so the
                                                  # persisted cycle history record can show a duration
    last_cycle_closed_at: str | None = None  # ISO datetime -- when the most recent cycle closed, used by
                                               # program_schedule.inter_cycle_delay_elapsed()


def ensure_fresh_trading_day(state: ProgramRuntimeState, today_iso: str) -> ProgramRuntimeState:
    """Resets the daily counters (but NOT the halt status -- a hard stop
    from yesterday must still be explicitly acknowledged, it doesn't
    silently clear itself just because a new day started) when the
    tracked trading_day no longer matches today."""
    if state.trading_day != today_iso:
        state.trading_day = today_iso
        state.cycles_today = 0
        state.consecutive_losses = 0
        state.daily_realized_pnl = 0.0
        state.cooldown_until = None
    return state


def can_start_new_cycle(
    *,
    program_status: str,
    active_cycle_id: str | None,
    cooldown_until: str | None,
    consecutive_losses: int,
    daily_realized_pnl: float,
    now: datetime,
    consecutive_loss_limit: int,
    daily_loss_amount: float,
    portfolio_daily_pnl: float,
    portfolio_daily_loss_cap: float,
) -> tuple[bool, str | None]:
    """The single decision point for SAFEGUARDS specifically -- is this
    Program allowed to open a new cycle right now, performance-wise?
    Returns (allowed, reason_if_not). Every one of these checks is
    deliberately independent and explicit -- resist the urge to collapse
    them, since the reason string is exactly what gets shown to the
    person and logged.

    Does NOT check scheduling (time window/day-filter/continuous flag) --
    that's program_schedule.can_trade_now(), a genuinely different
    question (WHEN a Program is eligible at all vs whether it SHOULD stop
    due to bad performance) checked separately by program_manager.py."""
    if program_status in HALT_STATUSES:
        return False, f"Program is {program_status.replace('_', ' ')}"

    if active_cycle_id is not None:
        return False, "a cycle is already active"

    if cooldown_until:
        try:
            # a stored cooldown_until predating this module's timezone fix is a NAIVE string
            # that was, in fact, IST wall-clock time -- clock.parse_iso interprets it as such,
            # rather than raising when compared against an aware `now` below
            if now < clock.parse_iso(cooldown_until):
                return False, f"cooling down until {cooldown_until} (max cycles/day throttle)"
        except ValueError:
            pass

    # portfolio-wide hard stop -- STRICT: applies even if this specific
    # Program's own numbers are fine
    if portfolio_daily_pnl <= -abs(portfolio_daily_loss_cap):
        return False, "portfolio-wide daily loss cap reached"

    if daily_realized_pnl <= -abs(daily_loss_amount):
        return False, "Program daily loss cap reached"

    if consecutive_losses >= consecutive_loss_limit:
        return False, "consecutive loss limit reached"

    return True, None


def on_cycle_closed(
    state: ProgramRuntimeState,
    *,
    cycle_pnl: float,
    now: datetime,
    consecutive_loss_limit: int,
    daily_loss_amount: float,
    max_cycles_per_day: int,
    cooldown_minutes: int,
) -> ProgramRuntimeState:
    """Called once both legs of a cycle are closed. Updates counters and
    decides status/cooldown transitions. Mutates and returns `state`."""
    state.active_cycle_id = None
    state.active_cycle_started_at = None
    state.last_cycle_closed_at = now.isoformat()
    state.cycles_today += 1
    state.daily_realized_pnl += cycle_pnl
    state.consecutive_losses = state.consecutive_losses + 1 if cycle_pnl < 0 else 0

    if state.consecutive_losses >= consecutive_loss_limit:
        state.status = HALTED_CONSECUTIVE_LOSS
        return state
    if state.daily_realized_pnl <= -abs(daily_loss_amount):
        state.status = HALTED_DAILY_LOSS
        return state

    # throttle, not a halt -- a standing slow-down for the rest of the day,
    # re-applied on every cycle close once past the threshold (not just once)
    if state.cycles_today >= max_cycles_per_day:
        state.cooldown_until = (now + timedelta(minutes=cooldown_minutes)).isoformat()

    return state


def effective_group_daily_loss_cap(member_daily_loss_amounts: list[float], override: float | None) -> float:
    """The shared cap-resolution rule used at BOTH the Risk Group and the
    Portfolio tier: an explicit override if the person set one, otherwise
    the SUM of the member daily_loss_amounts one level down (a Risk
    Group's members' own caps; the Portfolio's member Risk Groups' own
    caps) -- meaning each tier adds no extra restriction beyond what's
    already configured below it until deliberately tightened."""
    if override is not None:
        return abs(override)
    return sum(abs(x) for x in member_daily_loss_amounts)


# kept as an alias -- effective_portfolio_daily_loss_cap was the original,
# single-tier name before Risk Group existed; same function either way
effective_portfolio_daily_loss_cap = effective_group_daily_loss_cap


def apply_group_halt_if_needed(
    programs_runtime: dict[str, ProgramRuntimeState],
    member_program_ids: set[str],
    *,
    group_daily_pnl: float,
    group_cap: float,
    halt_status: str,
) -> list[str]:
    """STRICT: once the aggregate across `member_program_ids` crosses
    `group_cap`, halt every one of THOSE Programs that's currently running
    (not just ones over their own individual cap) -- used for both the
    Risk Group tier (member_program_ids = that group's Programs,
    halt_status=HALTED_RISK_GROUP) and the Portfolio tier
    (member_program_ids = every Program, halt_status=HALTED_PORTFOLIO).
    Returns the list of program_ids newly halted by this call."""
    if group_daily_pnl > -abs(group_cap):
        return []
    newly_halted = []
    for program_id in member_program_ids:
        state = programs_runtime.get(program_id)
        if state and state.status == RUNNING:
            state.status = halt_status
            newly_halted.append(program_id)
    return newly_halted


def apply_portfolio_halt_if_needed(
    programs_runtime: dict[str, ProgramRuntimeState],
    *,
    portfolio_daily_pnl: float,
    portfolio_cap: float,
) -> list[str]:
    """The Portfolio-tier case of apply_group_halt_if_needed above (every
    Program is a "member"). Kept as its own function since it's still a
    meaningfully distinct call site (program_manager.py calls this one
    directly with ALL programs, and apply_group_halt_if_needed per Risk
    Group separately) and for backward-compatible naming."""
    return apply_group_halt_if_needed(
        programs_runtime, set(programs_runtime.keys()),
        group_daily_pnl=portfolio_daily_pnl, group_cap=portfolio_cap, halt_status=HALTED_PORTFOLIO,
    )


def mtm_cycle_pnl(legs: list[dict]) -> float:
    """'What this cycle is worth right now' if you closed it this instant
    -- realized P&L for any leg that's already closed, live unrealized
    for any leg still open. Lets a safeguard check be mark-to-market-aware
    instead of blind to an open cycle's bleed until it closes on its own
    (see program_manager.py's mtm_pnl_map in tick() and the in-cycle halt
    check in _tick_one -- both opt-in per Program via
    SafeguardsConfig.mtm_aware, off by default)."""
    total = 0.0
    for order in legs:
        p = order.get("pnl") or {}
        val = p.get("realized") if p.get("realized") is not None else p.get("unrealized")
        if val is not None:
            total += val
    return total
