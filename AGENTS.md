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
- **No timezone handling existed anywhere in the backend** — every
  timestamp was a naive `datetime.now()`/`date.today()`, silently correct
  only because the dev machine happens to sit in IST. Fixed with a new
  `backend/clock.py` (hardcodes `Asia/Kolkata`, independent of host
  timezone) that every module now goes through for "now" and for parsing
  stored timestamps. `clock.parse_iso()` treats a naive stored string as
  IST — every timestamp ever written before this fix genuinely was IST
  wall-clock time, so this is backward-compatible, not a data migration.
  If you see a bare `datetime.now()`/`date.today()`/`datetime.fromisoformat()`
  anywhere in `backend/`, it's a bug — route it through `clock.*` instead.
  The L1 tick's exchange-side last-traded-time (`ltt`) is now also
  captured as `order["last_ltt"]` (diagnosis only — never compared for
  equality or used to drive a decision).
- **Advanced OMS entry decisions used to be completely blind to market
  conditions** — fetch spot, ATM strike, buy CE+PE, on a fixed schedule,
  every time, regardless of whether volatility was cheap or already
  expensive. Fixed with `backend/entry_signals.py` (pure gate logic,
  mirrors `program_schedule.py`/`program_safeguards.py`) checked once in
  `_start_new_cycle`, entirely optional (`ProgramConfig.entry_signals.
  enabled`, default `False`). Also: `OrderManager.fetch_live_price` was
  refactored to share its subscribe-and-wait logic with a new
  `fetch_market_snapshot` (full tick dict, not just `ltp`) — behavior for
  every existing caller is unchanged, confirmed by a real regression test
  before this shipped. Live Greeks/IV go through a fully separate new
  mechanism (`fetch_greeks_snapshot`), deliberately not touching the
  proven price-fetch path. See the README's "Entry Signal Gates -- BUILT"
  section for the full design and what's deliberately still deferred
  (delta-targeted strikes, theta-aware exits — both would mean editing
  the widening loop or the exit engine directly).
- **Whether this account is actually entitled to the broker's Greeks/IV
  channel is unverified** — nothing in this repo proves it, and it can
  only be confirmed live. The code fails safely either way
  (`fetch_greeks_snapshot` returns `None` on timeout;
  `on_greeks_unverifiable` controls whether that skips or allows the
  cycle), but don't claim confidence about entitlement that hasn't
  actually been observed working against a real market.
- **A real multi-day squeeze detector (Bollinger Band Width) and a
  mark-to-market safeguard cap were added**, both to `entry_signals.py`/
  `program_safeguards.py` respectively, in the very next round after
  Entry Signal Gates shipped — see the README's "Squeeze detector" and
  "Mark-to-market cap" BUILT sections. Two things worth knowing if you
  touch either: `data/signal_history/<date>.json` now stores
  `index_closes` keyed by `index_id` (not just VIX) via a
  read-modify-write helper (`_maybe_update_daily_signal_snapshot`,
  replaced the old write-once version) — don't add a new caller that
  writes this file directly. And the mark-to-market cap is opt-in per
  Program (`SafeguardsConfig.mtm_aware`, default `False`) and halts only
  — it deliberately never auto-flattens the open cycle it detected the
  breach on; that was an explicit scope decision, not an oversight, and
  reintroducing auto-flatten-on-halt needs the same deliberate
  human sign-off this decision got, not a quiet addition.

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
- **Cloud migration is not scheduled** — this remains a locally-run app.
  A product review (five features to build, five to refuse) and a
  migration-readiness plan exist as reference for whenever the move
  happens; ask the person for the artifact link if you need it. The one
  piece of that round that WAS built now, ahead of any move, is proper
  timezone handling (see above) — it was a latent correctness bug, not
  really a migration task, so it didn't make sense to wait on.

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
