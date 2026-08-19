"""
Pure logic (no I/O, no broker calls) for whether a Program's candidate
cycle should actually be ENTERED right now, given live market conditions --
a genuinely different question from program_schedule.py's (is a cycle
ELIGIBLE by time/day) and program_safeguards.py's (SHOULD this Program
stop due to bad performance). Kept separate and kept pure on purpose,
mirroring both of those modules -- this also gates whether real money
gets deployed, so the same "pure functions, tested exhaustively" rigor
applies.

Why this exists: today's entry logic (program_manager._start_new_cycle)
is completely blind to market conditions -- it buys the ATM straddle on
a fixed schedule regardless of whether volatility is cheap or already
expensive. For a strategy that profits from a big move and bleeds to time
decay when the market sits still, that's the single biggest lever
available. Every gate below is OFF by default (EntrySignalConfig.enabled
= False) -- today's exact behavior for anyone who doesn't opt in, same
convention as every other additive field in this app.

Each gate takes already-fetched data (never fetches anything itself) and
returns (allowed: bool, reason_if_not: str | None). A gate whose
required input is missing (None) or whose own threshold isn't configured
(None) always ALLOWS -- a gate that can't evaluate itself must never be
the reason a Program silently stops trading; that's a config/data
problem to surface via logging, not a trading decision.
"""

VERDICT_INSUFFICIENT_HISTORY = "insufficient_history"  # vix_percentile_gate only, before min_days exist


def vix_ceiling_gate(vix_ltp: float | None, max_vix: float | None) -> tuple[bool, str | None]:
    """Skip the cycle if India VIX is already above a configured ceiling --
    premium is already expensive for a long-volatility entry."""
    if max_vix is None or vix_ltp is None:
        return True, None
    if vix_ltp > max_vix:
        return False, f"India VIX {vix_ltp:.2f} is above the configured ceiling {max_vix:.2f}"
    return True, None


def oi_buildup_gate(ce_snapshot: dict | None, pe_snapshot: dict | None,
                     max_oi_chng_pct: float | None) -> tuple[bool, str | None]:
    """Skip if either leg's Open Interest has already built up sharply in
    one direction today -- a crude "the move may already be priced in"
    read. `OIChngPer` ships on the same L1 packet already fetched for the
    candidate strikes, no extra subscription needed."""
    if max_oi_chng_pct is None:
        return True, None
    for label, snap in (("CE", ce_snapshot), ("PE", pe_snapshot)):
        if not snap:
            continue
        chng = snap.get("OIChngPer")
        if chng is None:
            continue
        if abs(chng) > max_oi_chng_pct:
            return False, f"{label} leg's OI change ({chng:.1f}%) exceeds the configured limit ({max_oi_chng_pct:.1f}%)"
    return True, None


def session_range_gate(index_snapshot: dict | None, max_range_pct_of_open: float | None) -> tuple[bool, str | None]:
    """Skip once today's (high - low) / open already exceeds a configured
    band -- avoids entering a long straddle AFTER the day's move already
    happened. A same-session approximation only, not a real multi-day
    compression/squeeze detector (see the plan's "deliberately not built"
    section for why that needs a price history this app doesn't keep)."""
    if max_range_pct_of_open is None or not index_snapshot:
        return True, None
    day_open = index_snapshot.get("open")
    high = index_snapshot.get("high")
    low = index_snapshot.get("low")
    if not day_open or high is None or low is None:
        return True, None
    range_pct = (high - low) / day_open * 100
    if range_pct > max_range_pct_of_open:
        return False, (f"today's range so far ({range_pct:.2f}% of open) already exceeds the "
                        f"configured limit ({max_range_pct_of_open:.2f}%)")
    return True, None


def vix_percentile_gate(vix_ltp: float | None, history: list[float], min_days: int,
                         max_percentile: float | None) -> tuple[bool, str | None]:
    """Skip unless today's VIX ranks below a configured percentile of its
    own recent history (the persisted daily snapshots -- see
    signal_history.py). Degrades to ALLOW, logged as such by the caller,
    until at least `min_days` of history have accumulated -- a strategy
    correctness signal must never come from a sample too small to mean
    anything."""
    if max_percentile is None or vix_ltp is None:
        return True, None
    if len(history) < max(min_days, 1):
        return True, None
    below_or_equal = sum(1 for v in history if v <= vix_ltp)
    percentile = below_or_equal / len(history) * 100
    if percentile > max_percentile:
        return False, (f"India VIX {vix_ltp:.2f} ranks at the {percentile:.0f}th percentile of the last "
                        f"{len(history)} trading days -- above the configured {max_percentile:.0f}th ceiling")
    return True, None


def _bollinger_bandwidth(closes: list[float], period: int, num_std: float) -> float | None:
    """Bollinger Band Width, as a percent of the mean -- the textbook
    "squeeze" metric: a NARROW band means price has been unusually
    compressed relative to its own recent behavior. Uses the LAST
    `period` values in `closes` (chronological, oldest first). Returns
    None if there aren't yet `period` closes to compute a real reading
    from -- squeeze_gate below treats that as "can't evaluate yet"."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    if mean == 0:
        return None
    variance = sum((c - mean) ** 2 for c in window) / period
    std = variance ** 0.5
    return (2 * num_std * std) / mean * 100


def squeeze_gate(closes: list[float], period: int, num_std: float, min_days: int,
                  max_bandwidth_percentile: float | None) -> tuple[bool, str | None]:
    """Skip unless price has been genuinely compressed over the last
    `period` days relative to ITS OWN recent history of that same
    compression measure -- the actual multi-day squeeze signal
    session_range_gate only approximates with a single day. `closes` is
    this Program's underlying's daily price history (chronological,
    oldest first, ending with today's) -- see
    program_manager._index_close_history and the daily signal-history
    file this reads from (config.SIGNAL_HISTORY_DIR).

    Needs roughly `period + min_days` days of accumulated closes before
    it can rank today's reading against anything -- degrades to ALLOW,
    logged as such by the caller, until then. Same "a gate that can't
    evaluate itself must never be the reason trading silently stops"
    rule as every other gate in this module."""
    if max_bandwidth_percentile is None:
        return True, None
    current_bw = _bollinger_bandwidth(closes, period, num_std)
    if current_bw is None:
        return True, None  # not enough closes yet to compute a Bollinger Bandwidth reading at all
    # every PRIOR day's own bandwidth reading, each computed using only the data that would have
    # been available AS OF that day -- a real historical distribution to rank today's reading against,
    # not today's reading compared to itself
    history = [bw for i in range(period, len(closes))
               if (bw := _bollinger_bandwidth(closes[:i], period, num_std)) is not None]
    if len(history) < max(min_days, 1):
        return True, None
    below_or_equal = sum(1 for b in history if b <= current_bw)
    percentile = below_or_equal / len(history) * 100
    if percentile > max_bandwidth_percentile:
        return False, (f"Bollinger Band Width {current_bw:.2f}% ranks at the {percentile:.0f}th percentile of "
                        f"its own last {len(history)} sessions -- above the configured {max_bandwidth_percentile:.0f}th "
                        f"ceiling (not currently compressed enough for a squeeze entry)")
    return True, None


def iv_session_rank_gate(leg_iv: float | None, leg_lowiv: float | None, leg_highiv: float | None,
                          max_rank_pct: float | None) -> tuple[bool, str | None]:
    """Skip unless the live option's IV sits in the lower portion of
    TODAY's own iv range so far -- genuine same-session volatility
    timing, needs zero stored history (highiv/lowiv ship in the same
    packet as iv). The one gate with real entitlement risk: if Greeks
    weren't fetched at all (leg_iv is None -- see
    OrderManager.fetch_greeks_snapshot returning None on timeout), this
    ALLOWS regardless of max_rank_pct; the caller applies
    EntrySignalConfig.on_greeks_unverifiable separately, since "no data"
    and "data says it's fine" are different situations worth logging
    differently."""
    if max_rank_pct is None or leg_iv is None or leg_lowiv is None or leg_highiv is None:
        return True, None
    span = leg_highiv - leg_lowiv
    if span <= 0:
        return True, None  # degenerate range (e.g. first tick of the day) -- can't rank, don't block on it
    rank_pct = (leg_iv - leg_lowiv) / span * 100
    if rank_pct > max_rank_pct:
        return False, (f"IV {leg_iv:.2f} ranks at {rank_pct:.0f}% of today's own range "
                        f"[{leg_lowiv:.2f}, {leg_highiv:.2f}] -- above the configured {max_rank_pct:.0f}% ceiling")
    return True, None


def evaluate_entry(cfg: dict, *, index_snapshot: dict | None, ce_snapshot: dict | None, pe_snapshot: dict | None,
                    ce_greeks: dict | None, pe_greeks: dict | None, vix_ltp: float | None,
                    vix_history: list[float], index_close_history: list[float]) -> tuple[bool, str | None, bool]:
    """The single decision point program_manager calls -- mirrors
    program_safeguards.can_start_new_cycle's shape (one function, every
    check explicit, the reason string is exactly what gets shown to the
    person and logged). Returns (allowed, reason_if_not,
    greeks_unverifiable) -- the third value lets the caller log the
    entitlement question distinctly from an ordinary gate rejection,
    without this module doing any logging itself (stays pure, per the
    module docstring).

    `cfg` is EntrySignalConfig.as_dict() -- every threshold is Optional;
    a None threshold means that specific gate isn't configured, same
    "nothing extra happens unless explicitly opted into" convention as
    every other field in this app. `cfg["enabled"]` is the master switch:
    False short-circuits everything, today's exact behavior."""
    if not cfg.get("enabled"):
        return True, None, False

    iv_gate_configured = cfg.get("max_iv_session_rank_pct") is not None
    # "unverifiable" specifically means: the IV-rank gate was configured, but NEITHER leg's greeks
    # arrived at all (OrderManager.fetch_greeks_snapshot timed out on both) -- this is the live signal
    # for "is this account actually entitled to the Greeks channel," discovered naturally rather than
    # needing a separate diagnostic step (see the plan). A single leg missing greeks while the other has
    # them is NOT unverifiable -- that's just one strike's data not arriving yet, ordinary gate logic below.
    greeks_unverifiable = iv_gate_configured and ce_greeks is None and pe_greeks is None
    if greeks_unverifiable and cfg.get("on_greeks_unverifiable", "allow") == "skip":
        return False, ("Greeks/IV data did not arrive in time (Greeks entitlement unconfirmed for this "
                        "account) -- on_greeks_unverifiable is 'skip'"), True

    checks = [
        vix_ceiling_gate(vix_ltp, cfg.get("max_vix")),
        oi_buildup_gate(ce_snapshot, pe_snapshot, cfg.get("max_oi_chng_pct")),
        session_range_gate(index_snapshot, cfg.get("max_session_range_pct")),
        vix_percentile_gate(vix_ltp, vix_history, cfg.get("vix_percentile_min_days", 10),
                             cfg.get("max_vix_percentile")),
        squeeze_gate(index_close_history, cfg.get("squeeze_bollinger_period", 20),
                     cfg.get("squeeze_bollinger_std", 2.0), cfg.get("squeeze_min_days", 10),
                     cfg.get("max_squeeze_bandwidth_percentile")),
    ]
    for label, greeks in (("CE", ce_greeks), ("PE", pe_greeks)):
        g = greeks or {}
        ok, reason = iv_session_rank_gate(g.get("iv"), g.get("lowiv"), g.get("highiv"),
                                           cfg.get("max_iv_session_rank_pct"))
        checks.append((ok, f"{label} leg: {reason}" if reason else reason))

    for ok, reason in checks:
        if not ok:
            return False, reason, greeks_unverifiable

    return True, None, greeks_unverifiable
