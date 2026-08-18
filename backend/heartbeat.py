"""
Generic health/heartbeat zone computation. Built to scale cleanly to a
fleet of brokers/exchanges without redesign -- but honestly, today, this
only ever checks ONE broker (Tradejini) and zero exchange integrations,
since that's genuinely what exists in this app right now. Building the
full N-broker/N-exchange percentage math against a fleet of one would be
speculative infrastructure for a future that isn't here yet; this module
is deliberately small and correct for today, ready to grow the moment a
second broker or an exchange integration actually shows up.

Zone thresholds, from the design conversation that shaped this (numbers
themselves are illustrative, not to be taken literally -- the SHAPE of
ascending severity bands is the actual design):
  - internet down -> RED, unconditionally, regardless of anything else --
    nothing else matters if there's no connectivity at all
  - 0% of monitored entities down -> GREEN
  - up to 20% down -> YELLOW
  - up to 30% down -> ORANGE
  - more than 30% down -> RED
"""
from dataclasses import dataclass

GREEN = "green"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"

ZONE_ORDER = (GREEN, YELLOW, ORANGE, RED)  # ascending severity, for comparisons if ever needed


@dataclass
class EntityStatus:
    entity_type: str  # "broker" | "exchange" -- open-ended on purpose; a future entity type
                        # (e.g. a second broker) needs no change here, just another entry in the list
    name: str
    connected: bool


def compute_zone(*, internet_up: bool, entities: list) -> str:
    """entities is a list of EntityStatus. Pure function -- no I/O, easy
    to test exhaustively, mirroring program_safeguards.py's approach for
    the same reason: this also drives a visible signal about whether it's
    safe to trust the automation is actually working."""
    if not internet_up:
        return RED
    if not entities:
        return GREEN
    down = sum(1 for e in entities if not e.connected)
    pct_down = down / len(entities)
    if pct_down <= 0:
        return GREEN
    if pct_down <= 0.20:
        return YELLOW
    if pct_down <= 0.30:
        return ORANGE
    return RED


async def check_internet_reachable(timeout: float = 3.0) -> bool:
    """A lightweight reachability check against a host UNRELATED to
    Tradejini specifically -- the whole point is to distinguish "internet
    itself is down" from "Tradejini specifically is having an outage,"
    which the rest of this app already handles very differently (a broker
    outage retries forever without blocking the UI; see main.py's
    _login_retry_loop). Checking Tradejini's own host here would make
    that distinction impossible. Never raises -- any failure at all just
    means "not reachable" for heartbeat purposes; a failing connectivity
    check must never be allowed to break anything else."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.head("https://www.google.com")
            return resp.status_code < 500
    except Exception:
        return False
