# CLAUDE.md — Claude Code Instructions

This repository is actively developed by **both Claude Code and Codex**.
This file contains Claude-specific operating guidance. Shared project truth is
kept outside this file so either agent can continue the work.

## Read before working

Read:

1. `README.md`
2. `docs/INDEX.md`
3. `docs/SAFETY.md`
4. `docs/ARCHITECTURE.md`
5. `docs/TESTING.md`
6. `docs/DEVELOPMENT.md`
7. `docs/DECISIONS.md` when relevant
8. `agent-actions/planned/` and `agent-actions/coded/`

Then inspect the actual current source code.

Do not treat Codex or Claude conversation history as a source of truth when the
repository can answer the question.

## Safety-critical development

**A bug here can lose real money.**

Before believing a fix works:

1. Read the actual current code.
2. For backend behavior, use a runnable fake-broker/simulation where practical.
3. Run the project's verification workflow (`/verify`) when applicable.
4. State uncertainty honestly.
5. Never use a live broker action merely as a test.
6. Never read or expose `.env` or other secret-bearing local files.

See `docs/SAFETY.md` and `docs/TESTING.md` for shared rules.

## Claude-specific conventions

- Use `.claude/rules/order-manager-caution.md` before touching
  `backend/order_manager.py` or `backend/program_manager.py`.
- Use `.claude/rules/frontend-conventions.md` before changing frontend files.
- Use `.claude/skills/verify-trading-app/SKILL.md` for the established
  fake-broker verification pattern.
- Use `.claude/commands/verify.md` for the full verification sweep.
- Never guess Claude Code configuration syntax; consult current Claude Code
  documentation before changing Claude-specific settings, skills, hooks, or
  permission syntax.

## Existing architecture constraints

- `backend/order_manager.py` owns order lifecycle behavior.
- `backend/program_manager.py` owns Advanced OMS cycle orchestration.
- `backend/models.py` is the schema/validation authority.
- `backend/store.py` owns persistence.
- `backend/broker_interface.py` is the broker abstraction.
- `backend/tradejini_client.py` is Live.
- `backend/paper_broker.py` is Paper.
- `backend/clock.py` is the backend time authority.
- Frontend JavaScript shares global scope; collisions are a known risk.

Do not duplicate these facts into this file when they belong in shared docs.

## Multi-agent coordination

Before modifying anything:

```text
git status
git diff
git log -5 --oneline
```

Never reset, stash, checkout, overwrite, or discard another agent's uncommitted
work without explicit human authorization.

Inspect `agent-actions/planned/` and `agent-actions/coded/` first. If Codex has
already created a matching plan or code handover, continue from it instead of
starting a parallel effort.

## Mandatory planning artifact

For every non-trivial task that may change code, create a plan **without the
human needing to ask**.

Location:

```text
agent-actions/planned/
```

Filename:

```text
plan_<epoch>_<short-slug>.md
```

The plan must record the verified current behavior, proposed design, scope,
affected files, safety implications, verification plan, and open questions.

A plan in `planned/` is an action item, not approval. Present it to the human
and get confirmation before implementation.

## Mandatory code-change artifact

After implementing code changes, create a durable handover document **without
being asked**.

Location:

```text
agent-actions/coded/
```

Filename:

```text
code_<epoch>_<short-slug>.md
```

Include the associated plan, files changed, implementation decisions, tests
actually run/results, unverified items, risks, and continuation notes.

Do not move artifacts to `agent-actions/done/`; the human decides when they are
accepted.

## Handover principle

The repository is the shared state between Claude Code and Codex. Important
design decisions must be captured in shared docs or `agent-actions/`, not only
in a chat session.
