"""
Wraps Tradejini's Script Master API (GET /api/mkt-data/scrips/symbol-store)
-- the live instrument-master feed, refreshed once daily at their
Beginning-of-Day process. We cache it to disk with the version number they
give us, and only refetch a group when the version changes, per their own
documented usage pattern -- no static CSV to keep updating by hand.

Two groups matter for the Advanced OMS: "Index" (underlyings -- NIFTY,
BANKNIFTY, etc, one row each with the token to subscribe to for spot
price) and "NSEOptions" (every listed option contract, joined back to its
underlying via the `undId` field).

An option contract's own `id` field is itself a compact encoding of
everything about that contract (underlying, expiry, strike, CE/PE) --
e.g. "OPTIDX_NIFTY_NFO_2026-08-18_24400_CE" -- matching the same symId
convention used throughout the rest of this app. _parse_option_id() below
decodes it; we don't trust separate strike/expiry columns to necessarily
exist or be named consistently across scrip groups, so decoding the id
directly is the more robust source of truth.
"""
import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

log = logging.getLogger("tradejini.script_master")

CACHE_DIR = config.DATA_DIR / "script_master"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class IndexRow:
    id: str
    disp_name: str
    exc_token: str
    asset: str
    symbol: str
    avail_flag: bool


@dataclass
class OptionRow:
    id: str              # e.g. OPTIDX_NIFTY_NFO_2026-08-18_24400_CE -- also the tradeable symId
    disp_name: str
    exc_token: str
    lot: int
    tick: float
    asset: str
    freeze_qty: int | None
    weekly: bool
    und_id: str           # joins back to IndexRow.id
    underlying: str        # decoded from id, e.g. "NIFTY"
    expiry: date            # decoded from id
    strike: float             # decoded from id
    opt_type: str              # "CE" | "PE", decoded from id


def _parse_option_id(id_: str):
    """Decodes an option's own id into (underlying, expiry, strike, opt_type).
    Returns None if the id doesn't match the expected
    OPT*_<underlying>_<exch>_<expiry>_<strike>_<CE|PE> shape -- callers
    should skip rows that don't parse rather than guess."""
    parts = id_.split("_")
    if len(parts) < 6 or not parts[0].startswith("OPT"):
        return None
    opt_type = parts[-1]
    if opt_type not in ("CE", "PE"):
        return None
    try:
        strike = float(parts[-2])
        expiry = datetime.strptime(parts[-3], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        return None
    underlying = parts[1]
    return underlying, expiry, strike, opt_type


def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader]


class ScriptMaster:
    def __init__(self, client):
        """client is a TradejiniClient specifically (not the generic
        BrokerClient interface) -- script master format/endpoints are
        Tradejini-specific market-data, not an order-management operation,
        so there's no cross-broker abstraction to maintain here."""
        self.client = client
        self._indices: dict[str, IndexRow] = {}       # id -> row
        self._options_by_und: dict[str, list[OptionRow]] = {}  # und_id -> rows

    # -------------------------------------------------------------- I/O --

    def _cache_path(self, group: str) -> Path:
        return CACHE_DIR / f"{group}.csv"

    def _version_path(self) -> Path:
        return CACHE_DIR / "version.json"

    def _load_cached_version(self) -> int:
        p = self._version_path()
        if not p.exists():
            return 0
        try:
            return json.loads(p.read_text()).get("version", 0)
        except (json.JSONDecodeError, OSError):
            return 0

    async def refresh(self, force: bool = False):
        """Checks Tradejini's version against our cached one; only
        redownloads groups if it's actually changed (or force=True, or we
        have no cache yet). Safe to call on every app startup."""
        cached_version = self._load_cached_version()
        try:
            resp = await self.client.get_scrip_groups(cached_version)
        except Exception as e:
            log.warning("Scrip master version check failed (%s) -- using cached data if any.", e)
            self._load_from_cache_only()
            return

        is_updated = resp.get("isUpdated", False)
        new_version = resp.get("version", cached_version)

        if not is_updated and not force and self._cache_path("Index").exists():
            log.info("Scrip master up to date (version %s) -- using cache.", cached_version)
            self._load_from_cache_only()
            return

        log.info("Scrip master version changed (%s -> %s) -- refetching Index and NSEOptions.", cached_version, new_version)
        for group in ("Index", "NSEOptions"):
            text = await self.client.get_scrip_group_csv(group)
            self._cache_path(group).write_text(text)
        self._version_path().write_text(json.dumps({"version": new_version}))
        self._load_from_cache_only()

    def _load_from_cache_only(self):
        indices = {}
        idx_raw_count = 0
        idx_parse_failures = 0
        idx_path = self._cache_path("Index")
        if idx_path.exists():
            for row in _parse_csv(idx_path.read_text()):
                idx_raw_count += 1
                try:
                    # csv.DictReader gives "" for a blank CELL in a column that
                    # DOES exist -- row.get("availFlag", "true") never falls
                    # back to the default in that case (the key is present,
                    # just empty), so a real feed that leaves availFlag blank
                    # for normal/available instruments (only marking
                    # exceptions) would have every row misread as unavailable.
                    # Defensive fix: blank/missing -> available; only an
                    # explicit false-ish value excludes a row.
                    avail_raw = (row.get("availFlag") or "").strip().lower()
                    avail_flag = avail_raw not in ("false", "0", "no", "n")
                    r = IndexRow(
                        id=row["id"], disp_name=row.get("dispName", ""), exc_token=row.get("excToken", ""),
                        asset=row.get("asset", ""), symbol=row.get("symbol", row.get("dispName", "")),
                        avail_flag=avail_flag,
                    )
                    indices[r.id] = r
                except KeyError as e:
                    idx_parse_failures += 1
                    if idx_parse_failures == 1:
                        # log the actual column headers on the FIRST failure only
                        # (not once per row) -- if every row is failing on the
                        # same missing key, this one line tells you exactly which
                        # column name doesn't match what the code expects,
                        # instead of a silent, empty result with no trail at all
                        log.error("Index row missing expected column %s -- actual columns in this row: %s",
                                  e, list(row.keys()))
                    continue
        self._indices = indices
        available_count = sum(1 for r in indices.values() if r.avail_flag)
        log.info("Scrip master: parsed %d/%d Index rows (%d available, %d failed to parse).",
                  len(indices), idx_raw_count, available_count, idx_parse_failures)
        if idx_raw_count and not available_count:
            log.warning("Every parsed Index row came back as unavailable (avail_flag=False) -- "
                        "if this looks wrong, the availFlag column's real values may not match what "
                        "this code expects. Check data/script_master/Index.csv directly to see the raw data.")

        options_by_und: dict[str, list[OptionRow]] = {}
        opt_path = self._cache_path("NSEOptions")
        opt_raw_count = 0
        opt_id_unparseable = 0
        opt_parse_failures = 0
        if opt_path.exists():
            for row in _parse_csv(opt_path.read_text()):
                opt_raw_count += 1
                parsed = _parse_option_id(row.get("id", ""))
                if not parsed:
                    opt_id_unparseable += 1
                    continue
                underlying, expiry, strike, opt_type = parsed
                try:
                    r = OptionRow(
                        id=row["id"], disp_name=row.get("dispName", ""), exc_token=row.get("excToken", ""),
                        lot=int(float(row.get("lot") or 0)), tick=float(row.get("tick") or 0.05),
                        asset=row.get("asset", ""),
                        freeze_qty=int(row["freezeQty"]) if row.get("freezeQty") else None,
                        weekly=bool(str(row.get("weekly", "")).strip()),  # real data uses codes like "w3"/"w4"
                                                                             # (weekly cycle number), not true/false --
                                                                             # any non-empty value means "this IS a
                                                                             # weekly contract", which is the only
                                                                             # distinction actually needed here
                        und_id=row.get("undId", ""), underlying=underlying, expiry=expiry,
                        strike=strike, opt_type=opt_type,
                    )
                except (KeyError, ValueError) as e:
                    opt_parse_failures += 1
                    if opt_parse_failures == 1:
                        log.error("NSEOptions row failed to parse (%s) -- actual columns in this row: %s",
                                  e, list(row.keys()))
                    continue
                options_by_und.setdefault(r.und_id, []).append(r)
        self._options_by_und = options_by_und
        log.info("Scrip master: parsed %d/%d NSEOptions rows (%d id-unparseable, %d other failures).",
                  sum(len(v) for v in options_by_und.values()), opt_raw_count, opt_id_unparseable, opt_parse_failures)

    # ----------------------------------------------------------- lookups --

    def is_loaded(self) -> bool:
        """True only if at least one Index row actually parsed -- NOT just
        "the cache files were read." Was previously set unconditionally at
        the end of _load_from_cache_only(), which meant an empty or fully-
        unparseable Index.csv still reported as "loaded," with no signal
        that the Underlying Index picker would be empty."""
        return bool(self._indices)

    def list_indices(self) -> list:
        return sorted([r for r in self._indices.values() if r.avail_flag], key=lambda r: r.disp_name)

    def get_index(self, index_id: str):
        return self._indices.get(index_id)

    def available_expiries(self, index_id: str) -> list:
        rows = self._options_by_und.get(index_id, [])
        return sorted({r.expiry for r in rows})

    def next_expiry(self, index_id: str, *, min_working_days: int, holidays: set, today: date):
        """The earliest available expiry that is at least `min_working_days`
        working days away from `today` (today itself never counts as one of
        those days). "Working day" = not a Saturday/Sunday and not in
        `holidays`."""
        for expiry in self.available_expiries(index_id):
            if _working_days_between(today, expiry, holidays) >= min_working_days:
                return expiry
        return None

    def option_chain(self, index_id: str, expiry: date) -> list:
        return [r for r in self._options_by_und.get(index_id, []) if r.expiry == expiry]

    def strike_interval(self, index_id: str, expiry: date):
        """Derived empirically from the distinct strikes actually listed
        for this underlying+expiry, rather than hardcoded per-index --
        self-adapts if an index's strike spacing changes over time."""
        strikes = sorted({r.strike for r in self.option_chain(index_id, expiry)})
        if len(strikes) < 2:
            return None
        diffs = [round(b - a, 2) for a, b in zip(strikes, strikes[1:])]
        diffs = [d for d in diffs if d > 0]
        if not diffs:
            return None
        return min(diffs)

    def atm_pair(self, index_id: str, expiry: date, spot: float):
        """Returns (CE, PE) contracts for the strike closest to spot among
        strikes that have BOTH a CE and a PE listed. Returns None if the
        chain is empty or no strike has both sides."""
        chain = self.option_chain(index_id, expiry)
        ce_by_strike = {r.strike: r for r in chain if r.opt_type == "CE"}
        pe_by_strike = {r.strike: r for r in chain if r.opt_type == "PE"}
        common_strikes = set(ce_by_strike) & set(pe_by_strike)
        if not common_strikes:
            return None
        nearest = min(common_strikes, key=lambda s: abs(s - spot))
        return ce_by_strike[nearest], pe_by_strike[nearest]


def load_market_holidays() -> set:
    """Reads config.MARKET_HOLIDAYS_FILE (a JSON array of "YYYY-MM-DD"
    strings) if present. Missing/empty file just means holidays aren't
    excluded from working-day counts yet (weekday-only counting) -- logged
    once as a warning rather than failing, since a wrong expiry pick from
    an un-provided holiday list is a data problem to fix, not a crash."""
    if not config.MARKET_HOLIDAYS_FILE.exists():
        log.warning("No market_holidays.json found at %s -- working-day counts for expiry "
                    "selection will skip weekends only, not exchange holidays.", config.MARKET_HOLIDAYS_FILE)
        return set()
    try:
        raw = json.loads(config.MARKET_HOLIDAYS_FILE.read_text())
        return {datetime.strptime(d, "%Y-%m-%d").date() for d in raw}
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log.error("Couldn't parse market_holidays.json (%s) -- treating as no holidays configured.", e)
        return set()


def _working_days_between(start: date, end: date, holidays: set) -> int:
    """Number of working days strictly AFTER `start` up to and including
    `end` (start itself is never counted)."""
    count = 0
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            count += 1
        d += timedelta(days=1)
    return count
