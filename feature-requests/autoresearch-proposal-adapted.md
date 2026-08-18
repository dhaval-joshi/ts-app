# Feature request: Autonomous strategy research loop (adapted from karpathy/autoresearch)

## The problem

Same as originally stated: finding profitable parameter sets for options
strategies is a slow, manual loop of hypothesize → edit → test → decide.
A human can only run a handful of iterations a day.

## What's different about this app, and why the original design doesn't
## transfer directly

Karpathy's `autoresearch` works because it automates iteration *on top
of* infrastructure that already exists (a training script, a fixed data
shard, a single fast deterministic metric). This app has no equivalent
of any of those three things yet:

- No historical data pipeline (nothing fetches or caches historical
  OHLCV/options-chain data — everything built so far is live-tick-driven).
- No backtest engine (nothing replays historical data against a
  parameterized strategy and produces a P&L series).
- No signal-strategy abstraction (`StrategyConfig` today is exit/risk
  configuration for orders triggered manually or by fixed ATM-straddle
  logic — not a parameterized entry signal you could mutate).

So "implement the loop" is really three projects, roughly in this order,
not one:

**Phase 0 — data feasibility spike (do this first, cheaply, before
committing to anything else).** Confirm whether Tradejini's API exposes
historical OHLCV for the specific index options this app trades (NIFTY
ATM straddles), at what granularity, and how far back. If it doesn't,
or doesn't cover the strikes/expiries needed, identify a third-party
historical data source before designing anything further. This is a
short, bounded investigation — don't start Phase 1 without an answer.

**Phase 1 — the backtest substrate itself**, built and validated
*manually* first (a person confirms it produces sane, correct results on
a few known scenarios) before any autonomous loop touches it. This phase
alone is comparable in size to anything built in this app so far.

**Phase 2 — the autonomous loop**, once Phase 1 is trustworthy.

## Design notes for Phase 2 (the loop itself), assuming Phase 0/1 are done

### Hard isolation from the live app — non-negotiable

This lives in a **separate repository**, not a directory inside `tj-app`.
Nothing in the backtest/research code should import from, or be
importable by, `backend/order_manager.py`, `backend/program_manager.py`,
or `backend/models.py`. The live app's `StrategyConfig` and the
research loop's strategy representation should not even share a name,
let alone a file — the goal is that an agent iterating on the research
repo has *no path*, accidental or otherwise, to touch anything that
places a real order. Cross this boundary only through an explicit,
human-reviewed promotion step (see below), never through shared code.

### The 3-file contract, adapted for options strategies

- `prepare_data.py` — immutable, agent never touches it. Fetches and
  caches historical data for a fixed instrument/date range (confirmed
  available in Phase 0). Standard cleaning, no strategy logic.
- `strategy.py` — the playground. For this app's domain, this should
  represent what's actually optimizable here: strike selection relative
  to spot (ATM vs. a defined OTM offset), stop/target offsets and
  trailing parameters (the same shape already defined in this app's
  `LegStrategyConfig`/`TrailingConfig`, reused for consistency — see
  `README.md` Section 9's timeframe-aggregated-trailing item, which
  this could eventually share parameters with), and entry/exit timing
  windows. Not a general-purpose signal framework — scope it to what
  this app's Programs actually do.
- `autoresearch_program.md` — the instruction file, with one addition
  to Karpathy's original framing: the loop runs autonomously and
  commits/reverts on its own, but a strategy is never itself promoted
  toward paper or live trading without a separate, explicit human
  review step. State this as a hard constraint in the instructions, not
  a hope.

### Fixed-cost backtesting

Agreed as proposed — a deterministic run over a fixed historical window,
fast enough for dozens of iterations per hour. Add one options-specific
requirement: the backtest needs to account for realistic bid-ask
spread/slippage at the strikes being tested, not just mid-price fills —
options liquidity varies enough by strike and expiry that ignoring this
would make the golden metric measure something that doesn't survive
contact with a real fill.

### Golden metric, with overfitting guardrails

A single comparable score is right for the *decision rule*, but the
score itself needs structure Karpathy's version doesn't need (an LLM
loss on a fixed eval set is much harder to overfit by accident than a
scalar P&L metric on a finite historical options window):

- Split the historical window into a training range (the loop optimizes
  against this) and a held-out validation range it never sees until a
  candidate is chosen — report both, and treat a strategy that does
  well on training but poorly on validation as a failed experiment.
- Require a minimum trade count before a score counts at all (a strategy
  that "wins" on 3 trades in the window is noise, not signal).
- Track max drawdown alongside the primary metric (Sharpe or Profit
  Factor) and reject anything that improves the primary metric while
  blowing past a drawdown ceiling.

### Git-driven state, with one refinement from the original

Match Karpathy's actual pattern more closely than the original draft
here: each experiment runs on its own throwaway branch off the current
best, not directly on a shared working branch. If the (validation-aware)
metric improves, the branch becomes the new baseline; if not, discard
the branch entirely rather than a `git reset --hard` on shared history.
This is strictly safer and matches what the real repo does.

### Fault tolerance

Agreed as proposed — subprocess timeout, treat a crash or hang as a
failed experiment (not a special case), revert, move on. Add: give up
and move to the next hypothesis after a bounded number of fix attempts
on the same run (Karpathy's own instructions say "a few attempts, then
give up") — an unbounded retry loop on a single broken idea defeats the
point of running many experiments overnight.

### The step this design adds on top of the original: promotion

A winning strategy from the loop is a *candidate*, not a decision. Before
it's eligible for paper mode (let alone live), it needs: a human reading
the actual parameters and hypothesis history that produced it, a
walk-forward check on data *after* the validation window (not just
train/validation from the same historical period), and only then a stint
in this app's existing Paper mode before ever reaching a real Program.
This is the step that keeps "hundreds of unsupervised overnight
experiments" from ever translating into "a bad idea trading real money"
without anyone having looked at it first.
