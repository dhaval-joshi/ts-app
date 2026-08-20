---
paths:
  - "backend/order_manager.py"
  - "backend/program_manager.py"
  - "backend/paper_broker.py"
  - "backend/models.py"
---

# Working in the order lifecycle code

You're touching the part of this app that decides when real money moves.
Read this in full before editing, not just skimming.

## The state machine (current, as of the last full rewrite)

`entry_pending` → `watching` → `closing` → `closed` / `cancelled` /
`entry_rejected`. `watching` means "entry filled, this app is watching
live ticks to decide when to trigger the exit" — it does NOT mean any
broker-side order is resting. There is no broker-side exit order,
ever, for any order created by current code. If you find yourself
writing code that places one, stop — that's the retired mechanism; see
`AGENTS.md`.

## Before changing anything here

1. Read the actual current function you're about to touch, in full —
   this file has been rewritten enough times that reasoning from a
   general mental model of "how a trading order manager usually works"
   will be wrong in specific, dangerous ways.
2. Trace every caller of a function before changing its signature or
   behavior. `_reconcile_once`, `handle_l1_tick`, and `_do_close` are
   central — a change to any of them touches almost everything.
3. Check `store.py` for how the change needs to persist. Every state
   transition in this file writes to disk immediately (`store.save_order`)
   so a crash mid-trade is recoverable — don't add a transition that
   skips this.

## After changing anything here

1. Write a real simulation test — a fake `BrokerClient` (see the
   `verify-trading-app` skill), a real `OrderManager` instance, actual
   ticks fed through `handle_l1_tick`. Assert on the real resulting
   state, not on what you expect it to be.
2. Specifically verify: does this still work for BOTH Regular OMS
   orders AND Advanced OMS Program legs? They share this exact code
   path — a fix that only makes sense for one can silently break the
   other.
3. Run `/verify` before considering the change done.
4. If the change touches trailing, exit triggers, or reconciliation,
   explicitly test the "market is closed" / "square-off never resolves"
   case — this codebase has a real history of bugs that only appeared
   in that specific condition (see AGENTS.md, the orphaned-position fix).

## Things that look like bugs but might be intentional

- `owner` field on every order ("live"/"paper") — this exists specifically
  to stop the two `OrderManager` instances from double-loading each
  other's orders. Don't remove it without understanding why it's there.
- `closing_since` timestamp + `SQUARE_OFF_STUCK_TIMEOUT_SECONDS` — this
  is the recovery mechanism for a square-off that never resolves. It's
  deliberately generous (90s) to avoid false-positiving on a slow but
  legitimate broker response.
- A few remaining comments mentioning "OCO" are deliberately historical
  (explaining why the current design exists, in the module docstring and
  a couple of field-removal notes). The actual legacy broker-OCO
  placement/trailing/reconciliation code was fully DELETED, not just
  deprecated, once the person confirmed no live positions depended on it
  — don't assume any "legacy path" still exists to preserve; if you find
  a genuinely stale OCO reference, it's a bug to fix, not history to keep.
