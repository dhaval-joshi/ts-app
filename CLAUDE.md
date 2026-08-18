@AGENTS.md

# Tradejini Trading Station

A live options-trading app for NSE F&O (Regular OMS for manual orders,
Advanced OMS "Programs" for automated Index-ATM-straddle strategies).
Talks to a real broker (Tradejini) with real money. Read this whole file
before touching `backend/order_manager.py` or `backend/program_manager.py`
— see `.claude/rules/order-manager-caution.md` for the deeper detail that
doesn't fit here.

## The one rule that matters most

**A bug here can lose real money.** Before believing any fix works:
1. Read the actual current code — don't reason from memory of how a
   similar system "usually" works.
2. Write a throwaway simulation test using a fake broker client (see
   `.claude/skills/verify-trading-app/SKILL.md` for the pattern already
   established in this codebase) and actually run it. Don't just say
   "this should work now."
3. Run the full verification sweep (`/verify` — see
   `.claude/commands/verify.md`) before calling anything done.
4. State uncertainty honestly. If something can only be confirmed against
   a live market or a live broker connection, say so — don't claim
   confidence you don't have.

## Architecture, in one pass

- `backend/order_manager.py` — the brain. Owns every order's lifecycle:
  place entry → watch live ticks → fire a plain market order the instant
  a stop/target trigger crosses → reconcile the close. **No broker-side
  conditional order (OCO, stop-order, target-order) is ever placed.**
  That mechanism was tried, found unreliable in production (see
  "History" below), and fully retired — the legacy placement/trailing/
  reconciliation code for it was later deleted outright, not just
  deprecated. Don't reintroduce it, and don't be confused by the word
  "OCO" still appearing in a couple of historical comments and the
  README's retirement writeup.
- `backend/program_manager.py` — orchestrates Advanced OMS "Programs":
  on a schedule, picks an expiry, derives the ATM strike, places a CE+PE
  pair (a "cycle"), and applies safeguards (daily loss cap, consecutive
  losses, cooldown). Reuses `order_manager.py`'s machinery for every leg.
- `backend/models.py` — dataclasses + `from_dict`/validation for every
  config shape (`StrategyConfig`, `ProgramConfig`, `CreateOrderRequest`,
  etc). This is the schema; read it before changing any config shape.
- `backend/tradejini_client.py` — the REST wrapper around Tradejini's
  actual API. `backend/paper_broker.py` is a drop-in simulator (fills
  against live ticks) for Paper mode, structurally identical from
  `order_manager.py`'s point of view.
- `backend/store.py` — all disk persistence. One JSON file per order
  under `data/orders/`; Programs/Strategies/Risk Groups each get their
  own directory under `data/`.
- `frontend/` — no build step. Plain HTML + vanilla JS, Tailwind via
  CDN. `app.js` holds shared helpers loaded on every page; `index.html`
  is the whole app shell. See `.claude/rules/frontend-conventions.md`
  before touching any frontend file.

## Non-negotiable conventions

- **Every status/field rename must be swept everywhere** — backend,
  frontend, and README — not just where you're looking. This codebase
  has a documented history of confusion from a half-done rename; see
  AGENTS.md for the specifics.
- **Never guess at Claude Code's own config format.** If you're about to
  write anything Claude-Code-specific (settings.json keys, skill
  frontmatter, hook syntax), fetch the current docs first — this
  project's own setup was built that way, don't regress it.
- **Order dicts, Program configs, and Strategy configs are the schema.**
  Read `models.py`'s dataclass + `from_dict` before adding or renaming a
  field anywhere else.
- **This app is in active development.** No production users, no
  backward-compatibility obligation on stored data — the person running
  this closes positions manually on the exchange when needed. Don't
  over-engineer migrations for old data shapes; ask before assuming you
  need to.
- **Comments should say why, not just what**, especially near anything
  safety-critical. This codebase leans heavily on that pattern — match it.

## Build, run, verify

No formal `pytest` suite exists yet (see AGENTS.md's Phase 3/4 notes) —
verification has always been throwaway simulation scripts written,
run, and deleted per change. Consider proposing a real `tests/` directory
early; the person has said they're open to it now that this project has
persistent file access instead of a chat sandbox.

- Backend: `python3 -m py_compile backend/*.py` (compile check),
  then a real simulation test using a fake `BrokerClient` (see the
  `verify-trading-app` skill for the established pattern).
- Frontend: no bundler. Check brace/paren balance and run the
  function/const collision check across every script tag on a shared
  HTML page (`index.html` loads many `.js` files into one global scope —
  a name collision between them is a real, recurring risk class here).
- Full checklist: `/verify`.

## Where to look next

- `AGENTS.md` (imported above) — current status, recent history, what's
  already fixed vs. still open.
- `.claude/rules/order-manager-caution.md` — deep detail for
  `order_manager.py`/`program_manager.py` changes specifically.
- `.claude/rules/frontend-conventions.md` — deep detail for `frontend/`.
- `README.md` in the project root — the authoritative, continuously
  maintained architecture doc. Section 9 ("Phase 3/4 roadmap") has
  design-level detail for the next planned features; read it before
  proposing something that duplicates a decision already made there.
