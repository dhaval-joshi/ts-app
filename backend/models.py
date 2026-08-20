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
    trail_check_interval_seconds: int = 0  # 0 = no aggregation, react to every raw tick (today's exact
                                             # behavior). > 0 = only re-evaluate trailing/exit-trigger every
                                             # this many seconds, using the MEDIAN of ticks seen during that
                                             # window rather than the raw tick -- smooths out single-tick
                                             # noise/spikes in a choppy market. Live P&L display still updates
                                             # on every tick regardless -- this only affects the trailing/exit
                                             # DECISION, never execution (a crossed trigger still fires a
                                             # plain market order, same as always). See order_manager.py's
                                             # handle_l1_tick.
    exit_confirmation_windows: int = 1  # a crossed stop/target trigger must stay crossed for this many
                                          # CONSECUTIVE evaluations (one raw tick if trail_check_interval_
                                          # seconds is 0, or one aggregation window close otherwise) before
                                          # the close actually fires -- 1 = fire on the first crossing
                                          # (today's exact behavior). Originally Program-only; now on both,
                                          # per the person's explicit request for full Regular OMS parity.
    stop_breach_force_close_count: int = 0  # 0 = off, today's exact behavior. > 0: a stop that's been
                                             # HIT-then-RECOVERED (without actually closing -- see
                                             # exit_confirmation_windows) this many times gets force-closed
                                             # on the next hit, bypassing exit_confirmation_windows for that
                                             # specific close -- repeated testing of the same level is itself
                                             # treated as the signal. Stop side only. See order_manager.py's
                                             # _maybe_app_market_exit.
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
        trail_check_interval_seconds = int(d.get("trail_check_interval_seconds") or 0)
        if trail_check_interval_seconds < 0:
            raise ValidationError("trail_check_interval_seconds must be >= 0")
        exit_confirmation_windows = int(d.get("exit_confirmation_windows") or 1)
        if exit_confirmation_windows < 1:
            raise ValidationError("exit_confirmation_windows must be >= 1")
        stop_breach_force_close_count = int(d.get("stop_breach_force_close_count") or 0)
        if stop_breach_force_close_count < 0:
            raise ValidationError("stop_breach_force_close_count must be >= 0")
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
            trail_check_interval_seconds=trail_check_interval_seconds,
            exit_confirmation_windows=exit_confirmation_windows,
            stop_breach_force_close_count=stop_breach_force_close_count,
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
    mtm_aware: bool = False              # opt-in, off by default: when True, daily_loss_amount is checked
                                          # against daily_realized_pnl PLUS this Program's currently-open
                                          # cycle's live unrealized P&L, every tick, not just realized P&L
                                          # at cycle-close -- closes the gap where an open cycle bleeding
                                          # loss is otherwise invisible to every cap until it closes on its
                                          # own (see program_safeguards.mtm_cycle_pnl and program_manager.py's
                                          # tick()/_tick_one). A Program that opts in also contributes its
                                          # live MTM to its Risk Group's and the portfolio's aggregate caps;
                                          # one that doesn't stays invisible in MTM terms at every tier,
                                          # exactly like today. NEVER auto-flattens the open cycle -- halts
                                          # only, matching every other halt in this app (a hard stop never
                                          # touches currently-open legs).

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
            mtm_aware=bool(d.get("mtm_aware", False)),
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
class EntrySignalConfig:
    """Optional live-market preconditions checked right before a cycle
    actually starts -- a different question again from ScheduleConfig
    (WHEN is a cycle eligible by time/day) and SafeguardsConfig (SHOULD
    this Program stop due to bad performance): this asks whether market
    CONDITIONS right now favor entering a long straddle at all. See
    entry_signals.py for the pure gate logic this config drives.

    `enabled=False` (the default) is a hard master switch -- nothing
    below has any effect at all unless explicitly turned on, same
    convention as every other additive field in this app. Each threshold
    below is independently optional on top of that: a None threshold
    means that specific gate isn't configured, so turning `enabled` on
    with nothing else set changes nothing."""
    enabled: bool = False
    max_vix: Optional[float] = None                 # skip if India VIX (IDX_-15_NSE) is above this
    max_oi_chng_pct: Optional[float] = None          # skip if either leg's OI has moved more than this % today
    max_session_range_pct: Optional[float] = None    # skip once (high-low)/open on the underlying exceeds this %
    max_vix_percentile: Optional[float] = None        # skip unless today's VIX ranks below this percentile of
                                                        # its own recent history (see signal_history.py)
    vix_percentile_min_days: int = 10                 # don't apply max_vix_percentile until this many days of
                                                        # history exist -- a percentile from too few samples
                                                        # isn't a real signal
    max_iv_session_rank_pct: Optional[float] = None   # skip unless a leg's live IV ranks below this % of
                                                        # TODAY's own [lowiv, highiv] range -- needs live Greeks
    max_squeeze_bandwidth_percentile: Optional[float] = None  # skip unless the underlying's Bollinger Band
                                                        # Width ranks below this percentile of its own recent
                                                        # sessions -- the actual multi-day squeeze signal (has
                                                        # price been compressed over DAYS, not just today); see
                                                        # entry_signals.squeeze_gate
    squeeze_bollinger_period: int = 20                 # trading days in the Bollinger moving-average window
    squeeze_bollinger_std: float = 2.0                 # standard deviations for the bands
    squeeze_min_days: int = 10                         # don't apply max_squeeze_bandwidth_percentile until
                                                        # this many days of bandwidth HISTORY exist on top of
                                                        # the period itself (~period+this many days total,
                                                        # e.g. 20+10=30 trading days before it activates)
    on_greeks_unverifiable: str = "allow"              # "allow" | "skip" -- what to do when
                                                        # max_iv_session_rank_pct is set but Greeks never
                                                        # arrived at all (entitlement unconfirmed for this
                                                        # account). "allow" (fail open) mirrors an existing,
                                                        # deliberate precedent in this exact file: a margin
                                                        # check that raises also returns True and lets the
                                                        # trade through (program_manager._check_buffered_margin)
                                                        # -- don't let an unverified diagnostic channel
                                                        # indefinitely block real trading.

    @classmethod
    def from_dict(cls, d: dict | None) -> "EntrySignalConfig":
        d = d or {}
        on_unverifiable = d.get("on_greeks_unverifiable") or "allow"
        if on_unverifiable not in ("allow", "skip"):
            raise ValidationError('entry_signals.on_greeks_unverifiable must be "allow" or "skip"')
        min_days = int(d.get("vix_percentile_min_days", 10) or 10)
        if min_days < 1:
            raise ValidationError("entry_signals.vix_percentile_min_days must be >= 1")
        squeeze_period = int(d.get("squeeze_bollinger_period", 20) or 20)
        if squeeze_period < 2:
            raise ValidationError("entry_signals.squeeze_bollinger_period must be >= 2")
        squeeze_std = float(d.get("squeeze_bollinger_std", 2.0) or 2.0)
        if squeeze_std <= 0:
            raise ValidationError("entry_signals.squeeze_bollinger_std must be > 0")
        squeeze_min_days = int(d.get("squeeze_min_days", 10) or 10)
        if squeeze_min_days < 1:
            raise ValidationError("entry_signals.squeeze_min_days must be >= 1")
        pct_fields = {
            "max_vix": d.get("max_vix"),
            "max_oi_chng_pct": d.get("max_oi_chng_pct"),
            "max_session_range_pct": d.get("max_session_range_pct"),
            "max_vix_percentile": d.get("max_vix_percentile"),
            "max_iv_session_rank_pct": d.get("max_iv_session_rank_pct"),
            "max_squeeze_bandwidth_percentile": d.get("max_squeeze_bandwidth_percentile"),
        }
        parsed = {}
        for key, raw in pct_fields.items():
            parsed[key] = _opt_float(raw)
            if parsed[key] is not None and parsed[key] < 0:
                raise ValidationError(f"entry_signals.{key} must be >= 0")
        for key in ("max_vix_percentile", "max_iv_session_rank_pct", "max_squeeze_bandwidth_percentile"):
            if parsed[key] is not None and parsed[key] > 100:
                raise ValidationError(f"entry_signals.{key} must be <= 100")
        return cls(
            enabled=bool(d.get("enabled", False)),
            vix_percentile_min_days=min_days,
            squeeze_bollinger_period=squeeze_period,
            squeeze_bollinger_std=squeeze_std,
            squeeze_min_days=squeeze_min_days,
            on_greeks_unverifiable=on_unverifiable,
            **parsed,
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
    broker_id: str = "tradejini"  # which broker this Program trades through when mode=="live" (ignored
                                    # for paper). Exactly one broker exists today, so this is schema-only
                                    # groundwork for a future where a Program can be assigned to any
                                    # registered broker -- see TradejiniClient.broker_id and
                                    # broker_interface.py's module docstring for the stated direction.
    super_program_id: Optional[str] = None  # non-None only for a Program auto-materialized as one
                                              # broker-allocation child of a future "Super Program" (a
                                              # parent holding one shared strategy template spread across
                                              # multiple broker accounts) -- reserved now, not used by
                                              # anything yet; no Super Program entity exists. Every Program
                                              # created today gets None and stays a fully independent
                                              # Program in every respect.
    entry_mode: str = "auto_pair"             # "auto_pair" | "manual_single_leg"
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
    trail_check_interval_seconds: int = 0  # same meaning as StrategyConfig's field of the same name --
                                             # 0 = react to every raw tick (today's behavior); > 0 = only
                                             # re-evaluate trailing/exit-trigger every this many seconds,
                                             # using the MEDIAN tick seen during that window. Deliberately a
                                             # top-level field here, not on `schedule` (ScheduleConfig is
                                             # strictly about cycle-START eligibility -- a different concern
                                             # from exit-check cadence, which is closer in kind to stop/
                                             # target/time_exit above).
    exit_confirmation_windows: int = 1  # a crossed stop/target trigger must stay crossed for this many
                                          # CONSECUTIVE evaluations (each evaluation being either one raw
                                          # tick, if trail_check_interval_seconds is 0, or one aggregation
                                          # window close) before the close actually fires -- 1 = fire on the
                                          # first crossing (today's exact behavior). Also on StrategyConfig
                                          # now -- full Regular OMS parity, per explicit request.
    stop_breach_force_close_count: int = 0  # 0 = off. > 0: once the stop has been HIT-then-RECOVERED
                                          # (without actually closing) this many times, the NEXT hit
                                          # force-closes immediately, bypassing exit_confirmation_windows
                                          # for that specific close -- see StrategyConfig's field of the
                                          # same name and order_manager.py's _maybe_app_market_exit. Stop
                                          # side only. Unlike exit_confirmation_windows, this one DOES also
                                          # exist on StrategyConfig -- full parity from the start.
    entry_signals: EntrySignalConfig = field(default_factory=EntrySignalConfig)  # optional live-market
                                          # preconditions checked right before a cycle starts -- see
                                          # EntrySignalConfig's own docstring and entry_signals.py
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
        entry_mode = d.get("entry_mode") or "auto_pair"
        if entry_mode not in ("auto_pair", "manual_single_leg"):
            raise ValidationError('entry_mode must be "auto_pair" or "manual_single_leg"')
        broker_id = (d.get("broker_id") or "tradejini").strip()
        super_program_id = d.get("super_program_id") or None
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
        trail_check_interval_seconds = int(d.get("trail_check_interval_seconds") or 0)
        if trail_check_interval_seconds < 0:
            raise ValidationError("trail_check_interval_seconds must be >= 0")
        exit_confirmation_windows = int(d.get("exit_confirmation_windows") or 1)
        if exit_confirmation_windows < 1:
            raise ValidationError("exit_confirmation_windows must be >= 1")
        stop_breach_force_close_count = int(d.get("stop_breach_force_close_count") or 0)
        if stop_breach_force_close_count < 0:
            raise ValidationError("stop_breach_force_close_count must be >= 0")

        return cls(
            program_id=program_id,
            name=name,
            index_id=index_id,
            risk_group_id=d.get("risk_group_id") or None,
            product=product,
            mode=mode,
            entry_mode=entry_mode,
            broker_id=broker_id,
            super_program_id=super_program_id,
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
            trail_check_interval_seconds=trail_check_interval_seconds,
            exit_confirmation_windows=exit_confirmation_windows,
            stop_breach_force_close_count=stop_breach_force_close_count,
            entry_signals=EntrySignalConfig.from_dict(d.get("entry_signals")),
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
