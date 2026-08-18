"""
Every failure that matters operationally -- an order rejected, an exit
protection order failing to place, a trailing modify failing, a square-off
failing -- used to only ever land in that one order's own log array. Fine
for "what happened to this trade," useless for "how often is X failing
across everything" or for handing a developer a clean reproduction case.

This gives every such failure a second home: one shared, append-only JSON
Lines file (easy to tail, grep, or load a line at a time -- no risk of one
corrupt write breaking the whole file the way a single JSON array would).
"""
import json
import time
from pathlib import Path

from . import config

FAILURES_PATH = config.DATA_DIR / "failures.jsonl"
MAX_TAIL_BYTES = 2_000_000  # only read the last ~2MB when tailing a big file


def log_failure(*, category: str, order_id: str | None, message: str,
                 program_id: str | None = None, request: dict | None = None, response: dict | None = None):
    """Appends one failure record. Never raises -- a logging failure must
    never be allowed to break the operation that triggered it.

    program_id is a real structured field (not just embedded in message
    text) specifically so the Failures view can filter by Program properly
    -- pass it whenever the failure is Program-related, whether directly
    (an Advanced OMS orchestration failure) or indirectly (an ordinary
    order-level failure that happens to belong to a Program's leg, via
    order.get("program_id"))."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "category": category,       # e.g. "entry_rejected", "trailing_failed", "square_off_failed"
        "order_id": order_id,
        "program_id": program_id,
        "message": message,
        "request": request,
        "response": response,
    }
    try:
        with open(FAILURES_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # best-effort -- don't let disk issues cascade


def read_recent_failures(limit: int = 200, *, category: str | None = None, order_id: str | None = None,
                          program_id: str | None = None, since: str | None = None, until: str | None = None) -> list[dict]:
    """Returns the most recent failures, newest first, optionally filtered.
    `since`/`until` compare against the "ts" field as plain ISO strings
    (YYYY-MM-DD or full timestamps both sort/compare correctly this way,
    no need to parse into datetime objects for a simple range filter).
    Filtering happens AFTER reading the tail window (MAX_TAIL_BYTES), same
    as before -- a filtered request still won't reach further back into
    the file than an unfiltered one would."""
    if not FAILURES_PATH.exists():
        return []
    try:
        size = FAILURES_PATH.stat().st_size
        with open(FAILURES_PATH, "rb") as f:
            if size > MAX_TAIL_BYTES:
                f.seek(size - MAX_TAIL_BYTES)
                f.readline()  # discard a possibly-partial first line
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        out = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if category and entry.get("category") != category:
                continue
            if order_id and entry.get("order_id") != order_id:
                continue
            if program_id and entry.get("program_id") != program_id:
                continue
            if since and entry.get("ts", "") < since:
                continue
            if until and entry.get("ts", "") > until:
                continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out
    except OSError:
        return []
