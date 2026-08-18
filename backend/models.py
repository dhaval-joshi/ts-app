"""
Two separate shapes, on purpose:

  StrategyConfig  -- the reusable, symbol-agnostic "how do I manage this
                     trade after entry" template: product, SL/Target as
                     OFFSETS from entry (in points or percent), trailing
                     rules, time-based close. Saved under
                     data/strategies/<name>.json. Created and edited on the
                     Strategies page. Never contains a symbol, quantity,
                     entry order type, or price level -- those are chosen
                     per-trade on the Order page, not baked into a reusable
                     template (entry mechanics vary trade to trade even
                     under the same management strategy).

  CreateOrderRequest -- what the Order page submits: which instrument,
                        side/qty, which saved strategy to apply, how to
                        enter (order type/validity/price), and which exit
                        leg(s) to actually watch (both SL+Target, just
                        one, or neither).

SL/Target are stored as offsets, not absolute prices, precisely so a
strategy is reusable across symbols and price levels: "stop 1% below entry,
trailing" means the same thing on a Rs. 50 stock and a Rs. 5000 stock. The
offsets get converted into concrete price levels once, at the moment the
entry actually fills (see order_manager._finalize_exit_leg) -- from that
point on trailing operates in plain points internally, same as before.

No Pydantic here -- see the comment in the previous version of this file /
README for why (compiled Rust dependency, breaks on newer Python / Windows
ARM). Plain dataclasses + hand-written validation instead.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import uuid


class ValidationError(Exception):
    """Raised when the frontend sends a malformed strategy or order request."""


def _opt_float(v):
    return None if v in (None, "") else float(v)


def as_dict(obj) -> dict:
    return asdict(obj)


_VALID_OFFSET_MODES = ("points", "percent")
_VALID_SIDES = ("buy", "sell")
_VALID_PRODUCTS = ("delivery", "intraday", "normal")
_VALID_ENTRY_TYPES = ("limit", "market", "stoplimit", "stopmarket")
_VALID_VALIDITY = ("day", "ioc", "eos", "gtc")
_VALID_EXIT_MODES = ("both", "sl_only", "target_only", "none")


# --------------------------------------------------------------- strategy --

@dataclass
class TrailingConfig:
    enabled: bool = True
    trail_by: float = 0.0            # in the leg's offset_mode unit (points or %)
    activation_offset: float = 0.0   # ditto

    @classmethod
    def from_dict(cls, d: dict | None, *, default_enabled: bool, default_trail_by: float) -> "TrailingConfig":
        d = d or {}
        return cls(
            enabled=bool(d["enabled"]) if "enabled" in d else default_enabled,
            trail_by=float(d.get("trail_by", default_trail_by) or 0),
            activation_offset=float(d.get("activation_offset") or 0),
        )


@dataclass
class LegStrategyConfig:
    offset_mode: str = "percent"     # "points" | "percent"
    trig_offset: float = 0.0         # distance from entry price to the trigger
    limit_offset: float = 0.0        # extra buffer from trigger to limit price
    trailing: TrailingConfig = field(default_factory=TrailingConfig)

    @classmethod
    def from_dict(cls, d: dict, *, default_trig_offset: float, default_trailing_enabled: bool,
                   default_trail_by: float) -> "LegStrategyConfig":
        d = d or {}
        mode = d.get("offset_mode") or "percent"
        if mode not in _VALID_OFFSET_MODES:
            raise ValidationError(f"offset_mode must be one of {_VALID_OFFSET_MODES}")
        return cls(
            offset_mode=mode,
            trig_offset=float(d.get("trig_offset", default_trig_offset) or 0),
            limit_offset=float(d.get("limit_offset") or 0),
            trailing=TrailingConfig.from_dict(
                d.get("trailing"), default_enabled=default_trailing_enabled, default_trail_by=default_trail_by
            ),
        )


@dataclass
class TimeExitConfig:
    mode: str = "none"                  # "none" | "intraday_window" | "datetime"
    window_start: Optional[str] = None  # "HH:MM"
    window_end: Optional[str] = None    # "HH:MM"
    at: Optional[str] = None            # ISO-8601 local datetime string

    @classmethod
    def from_dict(cls, d: dict | None) -> "TimeExitConfig":
        d = d or {}
        return cls(
            mode=d.get("mode") or "none",
            window_start=d.get("window_start") or None,
            window_end=d.get("window_end") or None,
            at=d.get("at") or None,
        )


@dataclass
class StrategyConfig:
    name: str
    strategy_id: str = ""  # stable filename -- generated once, reused on edit (see from_dict), independent of name
    product: str = "intraday"
    tick_size: float = 0.05
    # defaults per product spec: trailing SL/Target ON by default, 1% / 2%
    stop: LegStrategyConfig = field(default_factory=lambda: LegStrategyConfig(
        offset_mode="percent", trig_offset=1.0, limit_offset=0.0,
        trailing=TrailingConfig(enabled=True, trail_by=1.0, activation_offset=0.0),
    ))
    target: LegStrategyConfig = field(default_factory=lambda: LegStrategyConfig(
        offset_mode="percent", trig_offset=2.0, limit_offset=0.0,
        trailing=TrailingConfig(enabled=True, trail_by=2.0, activation_offset=0.0),
    ))
    time_exit: TimeExitConfig = field(default_factory=TimeExitConfig)
    # NOTE: there used to be a configurable exit_mechanism field here
    # ("broker" real-OCO vs "app_market" app-watched), and before that,
    # broker-side OCO/stop/target orders were the ONLY mechanism at all.
    # Both are fully gone now -- every exit is always the app-watched
    # market-order mechanism, hardcoded and unconditional in
    # order_manager.py. See the README's "Historical: why OCO was
    # retired" for the full story.

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyConfig":
        name = (d.get("name") or "").strip()
        if not name:
            raise ValidationError("Strategy name is required")
        product = d.get("product") or "intraday"
        if product not in _VALID_PRODUCTS:
            raise ValidationError(f"product must be one of {_VALID_PRODUCTS}")
        tick_size = float(d.get("tick_size") or 0.05)
        if tick_size <= 0:
            raise ValidationError("tick_size must be greater than 0")
        # editing an existing strategy sends its id back so the save
        # overwrites the SAME file (a true rename) instead of creating a
        # new one under the new name -- generate a fresh one only when
        # there isn't one yet (a brand-new strategy)
        strategy_id = (d.get("strategy_id") or "").strip() or uuid.uuid4().hex[:12]

        return cls(
            name=name,
            strategy_id=strategy_id,
            product=product,
            tick_size=tick_size,
            stop=LegStrategyConfig.from_dict(d.get("stop"), default_trig_offset=1.0,
                                              default_trailing_enabled=True, default_trail_by=1.0),
            target=LegStrategyConfig.from_dict(d.get("target"), default_trig_offset=2.0,
                                                default_trailing_enabled=True, default_trail_by=2.0),
            time_exit=TimeExitConfig.from_dict(d.get("time_exit")),
        )


# ---------------------------------------------------------------- program --
#
# A Program is the Advanced OMS's own entity -- distinct from Strategy
# above. Where a Strategy is a passive template someone applies by hand to
# one order at a time, a Program actively DRIVES orders itself: on its own
# schedule, it picks an expiry, derives the ATM strike, and places a CE+PE
# pair (a "cycle"), using the existing order/exit/trailing machinery
# underneath for each leg -- see program_manager.py's module docstring for
# the full cycle lifecycle and safeguard design.

@dataclass
class SafeguardsConfig:
    consecutive_loss_limit: int = 3      # N consecutive losing cycles -> hard-stop (needs manual resume)
    daily_loss_amount: float = 5000.0    # rupees; today's realized P&L below -this -> hard-stop
    max_cycles_per_day: int = 5          # beyond this many cycles today, cooldown_minutes applies to every
                                          # subsequent cycle for the rest of the day (not just once)
    cooldown_minutes: int = 5

    @classmethod
    def from_dict(cls, d: dict | None) -> "SafeguardsConfig":
        d = d or {}
        consecutive = int(d.get("consecutive_loss_limit", 3) or 3)
        daily_loss = float(d.get("daily_loss_amount", 5000.0) or 5000.0)
        max_cycles = int(d.get("max_cycles_per_day", 5) or 5)
        cooldown = int(d.get("cooldown_minutes", 5) or 5)
        if consecutive < 1:
            raise ValidationError("consecutive_loss_limit must be >= 1")
        if daily_loss <= 0:
            raise ValidationError("daily_loss_amount must be > 0")
        if max_cycles < 1:
            raise ValidationError("max_cycles_per_day must be >= 1")
        if cooldown < 0:
            raise ValidationError("cooldown_minutes must be >= 0")
        return cls(
            consecutive_loss_limit=consecutive,
            daily_loss_amount=daily_loss,
            max_cycles_per_day=max_cycles,
            cooldown_minutes=cooldown,
        )


@dataclass
class ScheduleConfig:
    """WHEN (and which days) a Program is even eligible to consider a new
    cycle -- a genuinely different question from SafeguardsConfig above
    (which decides whether it SHOULD stop because performance is bad).
    `no_new_cycle_after` used to live in SafeguardsConfig; moved here as
    `end_time`, alongside the new `start_time`, for the same reason:
    eligibility and performance-based stopping are different concerns and
    don't belong bolted onto the same config."""
    continuous: bool = False    # if True, ignores start_time/end_time/days entirely -- always eligible
                                  # (e.g. a 24-hour Crypto Program with no day-start gate at all)
    start_time: str = "09:15"   # HH:MM -- no new cycle starts before this
    end_time: str = "14:55"     # HH:MM -- no new cycle starts at or after this; a cycle already open
                                  # keeps running its own SL/Target/trailing/time-exit normally into the close
    days: str = "all"           # "all" | "expiry_day" -- a day-eligibility filter, checked against the
                                  # actual expiry dates in script master data for this Program's underlying
    inter_cycle_delay_seconds: int = 0  # wait this long after a cycle closes before considering a new
                                          # one; 0 = immediate re-entry (the default)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ScheduleConfig":
        d = d or {}
        days = d.get("days") or "all"
        if days not in ("all", "expiry_day"):
            raise ValidationError('schedule.days must be "all" or "expiry_day"')
        delay = int(d.get("inter_cycle_delay_seconds", 0) or 0)
        if delay < 0:
            raise ValidationError("schedule.inter_cycle_delay_seconds must be >= 0")
        return cls(
            continuous=bool(d.get("continuous", False)),
            start_time=d.get("start_time") or "09:15",
            end_time=d.get("end_time") or "14:55",
            days=days,
            inter_cycle_delay_seconds=delay,
        )


@dataclass
class ProgramConfig:
    program_id: str
    name: str
    index_id: str            # script master Index row id, e.g. "IDX_NIFTY_NSE" -- resolved via ScriptMaster
    risk_group_id: Optional[str] = None  # which Risk Group this Program belongs to (see RiskGroupConfig
                                           # above) -- backfilled automatically for Programs saved before
                                           # this field existed, see program_manager.py's load_from_disk()
    product: str = "intraday"
    mode: str = "live"        # "live" | "paper" -- paper mode simulates fills against live prices via
                                # PaperBrokerClient instead of placing real orders; same orchestration,
                                # safeguards, and UI either way -- only where the order actually goes differs
    min_working_days_to_expiry: int = 2
    lots_per_leg: int = 1     # used when sizing_mode == "lots" (both legs always equal)
    sizing_mode: str = "lots"  # "lots" | "capital"
    capital_per_leg: Optional[float] = None  # rupees, used when sizing_mode == "capital" -- BOTH legs get
                                                # the SAME capital, so lot counts may differ between CE/PE
                                                # (their premiums differ) -- deliberately not equal lots:
                                                # equal capital gives predictable SL risk on both sides
                                                # regardless of which side is more expensive that day
    stop: LegStrategyConfig = field(default_factory=lambda: LegStrategyConfig(
        offset_mode="percent", trig_offset=20.0, limit_offset=0.0,
        trailing=TrailingConfig(enabled=True, trail_by=5.0, activation_offset=0.0),
    ))
    target: LegStrategyConfig = field(default_factory=lambda: LegStrategyConfig(
        offset_mode="percent", trig_offset=40.0, limit_offset=0.0,
        trailing=TrailingConfig(enabled=True, trail_by=10.0, activation_offset=0.0),
    ))
    time_exit: TimeExitConfig = field(default_factory=lambda: TimeExitConfig(
        mode="intraday_window", window_start="15:10", window_end="15:15",
    ))  # a leg's own EOD safety net -- separate from schedule.end_time,
        # which only governs STARTING a new cycle, not closing one already open
    safeguards: SafeguardsConfig = field(default_factory=SafeguardsConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    # NOTE: exit_mechanism used to be configurable here too -- retired for
    # the same reason as StrategyConfig's (see that field's removal note
    # above).

    @classmethod
    def from_dict(cls, d: dict) -> "ProgramConfig":
        program_id = (d.get("program_id") or "").strip() or uuid.uuid4().hex[:12]
        name = (d.get("name") or "").strip()
        if not name:
            raise ValidationError("Program name is required")
        index_id = d.get("index_id")
        if not index_id:
            raise ValidationError("index_id is required")
        product = d.get("product") or "intraday"
        if product not in _VALID_PRODUCTS:
            raise ValidationError(f"product must be one of {_VALID_PRODUCTS}")
        min_wd = int(d.get("min_working_days_to_expiry", 2) or 2)
        if min_wd < 0:
            raise ValidationError("min_working_days_to_expiry must be >= 0")
        lots = int(d.get("lots_per_leg", 1) or 1)
        if lots < 1:
            raise ValidationError("lots_per_leg must be >= 1")
        mode = d.get("mode") or "live"
        if mode not in ("live", "paper"):
            raise ValidationError('mode must be "live" or "paper"')
        sizing_mode = d.get("sizing_mode") or "lots"
        if sizing_mode not in ("lots", "capital"):
            raise ValidationError('sizing_mode must be "lots" or "capital"')
        capital_per_leg = d.get("capital_per_leg")
        if sizing_mode == "capital":
            if capital_per_leg is None or float(capital_per_leg) <= 0:
                raise ValidationError("capital_per_leg must be > 0 when sizing_mode is \"capital\"")
            capital_per_leg = float(capital_per_leg)
        else:
            capital_per_leg = float(capital_per_leg) if capital_per_leg not in (None, "") else None

        return cls(
            program_id=program_id,
            name=name,
            index_id=index_id,
            risk_group_id=d.get("risk_group_id") or None,
            product=product,
            mode=mode,
            min_working_days_to_expiry=min_wd,
            lots_per_leg=lots,
            sizing_mode=sizing_mode,
            capital_per_leg=capital_per_leg,
            stop=LegStrategyConfig.from_dict(d.get("stop"), default_trig_offset=20.0,
                                              default_trailing_enabled=True, default_trail_by=5.0),
            target=LegStrategyConfig.from_dict(d.get("target"), default_trig_offset=40.0,
                                                default_trailing_enabled=True, default_trail_by=10.0),
            time_exit=TimeExitConfig.from_dict(d.get("time_exit") or {
                "mode": "intraday_window", "window_start": "15:10", "window_end": "15:15",
            }),
            safeguards=SafeguardsConfig.from_dict(d.get("safeguards")),
            schedule=ScheduleConfig.from_dict(d.get("schedule")),
        )


@dataclass
class RiskGroupConfig:
    """Sits between Program and Portfolio: a correlation-based grouping you
    define yourself (e.g. "Stock F&O", "Commodity Oil", "Commodity Gold",
    "Crypto") -- NOT a fixed textbook asset-class taxonomy, since two
    things can be in the same formal asset class (Oil and Gold, both
    "commodities") without actually moving together. Crossing this group's
    daily-loss cap Strictly halts every Program that's a member of it,
    even ones still under their own individual cap -- the same reasoning
    the Portfolio-wide halt used to carry alone, now correctly scoped to
    only the Programs that are actually likely to be correlated with each
    other, rather than applied across the whole portfolio regardless of
    what's actually related."""
    risk_group_id: str
    name: str
    daily_loss_amount_override: Optional[float] = None  # None = effective cap is the SUM of its member
                                                           # Programs' own daily_loss_amount

    @classmethod
    def from_dict(cls, d: dict) -> "RiskGroupConfig":
        risk_group_id = (d.get("risk_group_id") or "").strip() or uuid.uuid4().hex[:12]
        name = (d.get("name") or "").strip()
        if not name:
            raise ValidationError("Risk Group name is required")
        override = d.get("daily_loss_amount_override")
        return cls(
            risk_group_id=risk_group_id,
            name=name,
            daily_loss_amount_override=float(override) if override not in (None, "") else None,
        )


@dataclass
class PortfolioSafeguards:
    """Global, singleton (not per-Program) settings -- Strict: crossing
    this halts EVERY Program, including ones still under their own
    individual cap AND ones in a Risk Group still under ITS cap.

    With Risk Group now doing the correlation-aware halting (grouping
    Programs that actually move together), Portfolio's own reasoning
    shifted: it's no longer "these are correlated so stop everything," it's
    simply an outer, unconditional ceiling on total daily risk across
    everything you're running, for its own sake -- which is why this is
    now explicitly toggleable, rather than always-on. Some people will
    want that extra outer bound; Risk Group alone may be enough for others."""
    enabled: bool = True
    daily_loss_amount_override: Optional[float] = None  # None = effective cap is the SUM of every
                                                           # Risk Group's own daily_loss_amount (recomputed
                                                           # live as Risk Groups are added/edited/removed)

    @classmethod
    def from_dict(cls, d: dict | None) -> "PortfolioSafeguards":
        d = d or {}
        override = d.get("daily_loss_amount_override")
        enabled = d.get("enabled")
        return cls(
            enabled=True if enabled is None else bool(enabled),
            daily_loss_amount_override=float(override) if override not in (None, "") else None,
        )


# ------------------------------------------------------------------ order --

_REQUIRED_ORDER_FIELDS = ("sym_id", "side", "qty", "strategy_name")


@dataclass
class CreateOrderRequest:
    sym_id: str
    side: str
    qty: float
    strategy_name: str
    label: str = ""
    stream_symbol: Optional[str] = None
    entry_type: str = "market"
    entry_validity: str = "day"
    entry_limit_price: Optional[float] = None
    entry_trig_price: Optional[float] = None
    exit_mode: str = "both"    # "both" | "sl_only" | "target_only" | "none"
    lot_size: float = 1        # informational -- see order_manager for why this is tracked

    @classmethod
    def from_dict(cls, d: dict) -> "CreateOrderRequest":
        missing = [f for f in _REQUIRED_ORDER_FIELDS if d.get(f) in (None, "")]
        if missing:
            raise ValidationError(f"Missing required field(s): {', '.join(missing)}")
        if d["side"] not in _VALID_SIDES:
            raise ValidationError(f"side must be one of {_VALID_SIDES}")
        try:
            qty = float(d["qty"])
        except (TypeError, ValueError):
            raise ValidationError("qty must be a number")
        if qty <= 0:
            raise ValidationError("qty must be greater than 0")

        entry_type = d.get("entry_type") or "market"
        if entry_type not in _VALID_ENTRY_TYPES:
            raise ValidationError(f"entry_type must be one of {_VALID_ENTRY_TYPES}")
        entry_validity = d.get("entry_validity") or "day"
        if entry_validity not in _VALID_VALIDITY:
            raise ValidationError(f"entry_validity must be one of {_VALID_VALIDITY}")
        exit_mode = d.get("exit_mode") or "both"
        if exit_mode not in _VALID_EXIT_MODES:
            raise ValidationError(f"exit_mode must be one of {_VALID_EXIT_MODES}")

        entry_limit_price = _opt_float(d.get("entry_limit_price"))
        if entry_type in ("limit", "stoplimit") and entry_limit_price is None:
            raise ValidationError(f"entry_limit_price is required for entry_type '{entry_type}'")
        entry_trig_price = _opt_float(d.get("entry_trig_price"))
        if entry_type in ("stoplimit", "stopmarket") and entry_trig_price is None:
            raise ValidationError(f"entry_trig_price is required for entry_type '{entry_type}'")

        try:
            lot_size = float(d.get("lot_size") or 1)
        except (TypeError, ValueError):
            lot_size = 1
        if lot_size <= 0:
            lot_size = 1

        return cls(
            sym_id=str(d["sym_id"]),
            side=d["side"],
            qty=qty,
            strategy_name=d["strategy_name"],
            label=d.get("label") or "",
            stream_symbol=d.get("stream_symbol") or None,
            entry_type=entry_type,
            entry_validity=entry_validity,
            entry_limit_price=entry_limit_price,
            entry_trig_price=entry_trig_price,
            exit_mode=exit_mode,
            lot_size=lot_size,
        )
