# AGENTS.md — Codex / Shared Coding-Agent Instructions

This repository is actively developed by **both Claude Code and Codex**.
`AGENTS.md` is the Codex/generic coding-agent operating contract. It is **not**
the sole source of project knowledge.

## Read before working

Start with:

1. `README.md`
2. `docs/INDEX.md`
3. `docs/SAFETY.md`
4. `docs/ARCHITECTURE.md`
5. `docs/TESTING.md`
6. `docs/DEVELOPMENT.md`
7. `docs/DECISIONS.md` when the task touches established design decisions
8. relevant files under `agent-actions/`

Then inspect the actual current source code. Never rely on chat history or
memory when the current code can answer the question.

## This is live trading software

A bug can cause real financial loss.

Non-negotiable rules:

- Never intentionally read, print, copy, expose, commit, or modify `.env`,
  `.env.*` (except `.env.example`), `secrets/`, private keys/certificates,
  credential files, or other secret-bearing local artifacts.
- Never dump environment variables as part of diagnosis.
- Never place, modify, cancel, or close a real broker order as ordinary
  verification. Live-broker actions require explicit human authorization in
  the current task.
- Prefer Paper mode, fake brokers, simulations, compilation, static checks,
  and other non-live verification.
- Never silently switch Live/Paper behavior.
- Never silently increase quantity, exposure, or trading scope.
- Never bypass, weaken, or remove safety checks merely to make a test pass.
- Never reintroduce broker-side OCO/conditional exits. The application-watched
  market-exit mechanism is the current design.
- Never introduce automatic flattening on a safeguard halt without explicit
  human approval.
- Preserve atomic persistence and recovery behavior.

The repository instructions are behavioral safeguards, not a substitute for
OS-level access control. The project includes conservative Codex sandbox and
approval defaults in `.codex/config.toml`, but `.env` must still remain outside
any execution/workspace boundary you do not trust. Never intentionally inspect it.

## Shared project truth

Do not duplicate project-specific truth in `AGENTS.md` when it belongs in
`README.md` or `docs/`.

- `README.md` — detailed current product behavior and architecture.
- `docs/` — focused shared engineering knowledge.
- `CLAUDE.md` — Claude Code-specific workflow.
- `.claude/` — Claude-only commands/rules/skills/settings.
- `.codex/` — Codex project configuration and command rules.
- `.agents/skills/` — repository-scoped portable skills discovered by current Codex.
- `agent-actions/` — durable planning and implementation handovers.

## Multi-agent workflow

Claude Code and Codex share the same repository and may leave uncommitted work
for each other.

Before changing anything:

```text
git status
git diff
git log -5 --oneline
```

Never reset, stash, checkout, overwrite, or discard another agent's uncommitted
changes without explicit human authorization.

Inspect `agent-actions/planned/` and `agent-actions/coded/` before starting.
If a matching artifact already exists, continue it rather than creating a
duplicate.

## Mandatory planning handover

For every non-trivial task that may change code, the agent must create a
planning artifact **without being asked**.

Location:

```text
agent-actions/planned/
```

Filename:

```text
plan_<epoch>_<short-slug>.md
```

The plan must include:

- objective/problem;
- verified current behavior;
- proposed design;
- files/components likely affected;
- safety implications;
- alternatives/trade-offs where relevant;
- verification plan;
- open questions/assumptions;
- explicit scope and non-scope.

A plan in `planned/` is an **action item, not approval**. Present it to the
human and obtain confirmation before implementation. If it remains in
`planned/`, do not assume permission to code it.

## Mandatory code-change handover

After implementing code changes, the agent must create a code-change artifact
**without being asked**.

Location:

```text
agent-actions/coded/
```

Filename:

```text
code_<epoch>_<short-slug>.md
```

The code-change artifact must include:

- associated `plan_...` filename;
- objective;
- files changed;
- behavior changed;
- important implementation decisions;
- tests/simulations actually run and their results;
- what was not verified;
- known risks/follow-ups;
- whether another agent can safely continue.

The agent must **not** move anything into `agent-actions/done/`. The human owns
acceptance and moves approved artifacts to `done/`.

## Development standards

- Inspect before modifying.
- Prefer the smallest coherent change.
- Read `backend/models.py` before changing persisted/config schemas.
- Route backend time through `backend/clock.py`.
- Preserve broker abstraction; do not duplicate Live/Paper order-management paths.
- For renames/removals, sweep backend, frontend, documentation, and relevant
  configuration for stale references.
- Comments should explain why, especially around safety-critical behavior.
- Report exactly what was tested and what remains uncertain.
- Distinguish verified, inferred, and live-unverified behavior.

## Handover quality

Another agent must be able to continue from the repository alone. Do not rely
on "I explained this in the chat" for important decisions.
