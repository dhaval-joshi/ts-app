# AGENTS.md — status ledger

Read by any coding agent working on this repo (Claude Code reads this via
the `@AGENTS.md` import at the top of `CLAUDE.md`; other tools — Cursor,
Aider, etc. — read it directly). Keep this current as work happens: update
"Recently fixed" and "Known open items" as part of the change that touches
them, not as a separate cleanup pass later.

## What this project is

Tradejini Trading Station — a self-hosted live trading app for NSE F&O.
Regular OMS: manual single orders. Advanced OMS: automated "Program"
strategies (Index ATM straddles) with safeguards, scheduling, and
Paper/Live modes. Built almost entirely through an extended conversation
with Claude in claude.ai chat, now moving to Claude Code for continued
work. This file (plus CLAUDE.md and the README) is the handoff.

## Foundational history worth knowing before you touch anything

- **OCO (broker-side conditional orders) was the original exit
  mechanism and was fully retired.** Tradejini's own OCO/conditional-
  order engine was observed, in real trading, to silently fail to
  trigger — at real financial cost. The app now watches live price
  itself and fires a plain market order the instant a trigger crosses.
  This was a multi-round fix: first a config *choice* between "broker"
  and "app_market" mechanisms, then (after a real bug where Programs
  silently used the old mechanism regardless of config) the choice was
  removed entirely and app-watched became the only mechanism. Then, in
  a later round, all the *legacy code* for the broker mechanism was
  deleted outright (not just deprecated) once the person confirmed no
  live positions depended on it. **Do not reintroduce any broker-side
  conditional order placement.** The README's "Historical: why OCO was
  retired" section has the full story if you need it.
- **A duplicate-orders bug** existed because both the live and paper
  `OrderManager` instances loaded every order file indiscriminately with
  no ownership filter. Fixed by tagging each order with an explicit
  `owner` field ("live"/"paper") at creation time. If you ever see an
  order duplicated in a list, check this first.
- **An orphaned-position bug**: a square-off (close) order that got
  rejected, or that got accepted but never resolved to any terminal
  state (most likely: fired at/after market close), used to leave the
  position stuck forever with no warning. Fixed with a timeout-based
  recovery (`SQUARE_OFF_STUCK_TIMEOUT_SECONDS` in `order_manager.py`)
  that reverts to a watched state and retries automatically. If you're
  investigating "why did this position stay in `closing` forever,"
  check this mechanism is still intact.
- **The `oco_placed` status name was renamed to `watching`** — it was
  actively misleading once OCO was retired (implied a broker order was
  involved when none ever is). If you see `oco_placed` anywhere, it's
  either dead/stale text that should be fixed, or a mistake.
- **A live-refresh gap**: Advanced OMS Program cards used to have *zero*
  live-refresh mechanism — they only updated on navigation or an
  explicit action. Fixed by wiring them into the same shared websocket
  push Regular OMS already used (`onOrdersUpdate()` in `app.js`), plus a
  20s periodic safety net for state that isn't in that push.

## Known open items / deliberately deferred

- **`exit_mode`'s internal value `"oco"`** (meaning "watch both legs",
  a different concept from the retired exit *mechanism*) was renamed to
  `"both"` in the same round the legacy OCO code was deleted — already
  done, not open. Listed here only so a future search for "oco" isn't
  surprised to find zero live references outside the historical README
  section and a couple of explanatory comments.
- **No formal automated test suite exists yet.** Verification has always
  been done via throwaway simulation scripts (write a fake `BrokerClient`,
  run a real scenario through `OrderManager`, assert, delete the script).
  This worked well for a chat-based sandbox with no persistent files, but
  now that this project has real file access via Claude Code, it's worth
  proposing a real `tests/` directory with these scenarios kept
  permanently rather than rewritten each time. Ask the person before
  doing this — it's a real structural change, not a small one.
- **Phase 3/4 roadmap** — three items are already designed (not built) in
  the README's Section 9: timeframe-aggregated trailing (reduce tick-level
  noise), broker reconciliation (sync this app's own records against the
  broker's authoritative history), and decoupling Programs from
  Index-only trading. Read that section before starting any of these —
  it has real design constraints already worked out, including specific
  things to avoid.
- **Market vs. Limit order for the close**, configurable per Program, is
  also designed in the README (tied to the timeframe-trailing item) but
  not built.

## Working style established across this whole build

- Every fix was verified with a real, runnable simulation before being
  called done — not just reasoned about. Keep doing this.
- When something is genuinely uncertain (can only be confirmed against a
  live market/broker), that uncertainty was stated plainly rather than
  papered over with false confidence. Keep doing this too.
- Renames and removals were swept exhaustively — backend, frontend, AND
  the README — with a final grep sweep to confirm zero stragglers before
  calling anything finished.
- The person prefers direct, honest engineering communication over
  reassurance. Report exactly what was tested and what wasn't.
