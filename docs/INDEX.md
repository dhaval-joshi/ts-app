# Shared Project Knowledge Index

This repository is intentionally developed by **both Claude Code and Codex**.
The agent configuration files are not the project source of truth.

## Source of truth hierarchy

1. **Current source code** — what the application actually does.
2. **README.md** — current product behavior, architecture, setup, and operational context.
3. **docs/** — focused shared engineering knowledge.
4. **agent-actions/** — durable handover artifacts produced by agents.
5. **Git history/diff** — what changed and what is currently uncommitted.

## Shared engineering documents

- `README.md` — product behavior and detailed project documentation.
- `docs/ARCHITECTURE.md` — concise component and data-flow map.
- `docs/SAFETY.md` — non-negotiable trading and operational safety invariants.
- `docs/TESTING.md` — verification expectations.
- `docs/DEVELOPMENT.md` — common development workflow.
- `docs/DECISIONS.md` — important historical decisions and constraints.

## Agent-specific instructions

- `CLAUDE.md` — Claude Code-specific operating instructions.
- `AGENTS.md` — Codex / generic coding-agent operating instructions.
- `.claude/` — Claude Code commands, rules, skills, and settings.
- `CLAUDE.local.md` — local Claude-only notes; ignored by Git.

Do not copy project truth into both agent instruction files. If a rule describes
how the product must behave, put it in the shared documentation instead.

## Before taking a task

An agent must:

1. Inspect `git status` and `git diff`.
2. Read the relevant shared documents.
3. Inspect existing `agent-actions/planned/` and `agent-actions/coded/`.
4. Determine whether another agent already planned or partially implemented the task.
5. Read the actual current source code before making conclusions.

## Incoming requests

- `feature-requests/incoming/` — requests/context handed in for planning. Presence here is not approval to implement.

## Pending work

Anything in `agent-actions/planned/` is **not automatically approved for implementation**.
It is an action item. The agent must present the applicable plan and ask the
human whether to proceed.

A request in `feature-requests/incoming/` is input to be planned, not permission
to implement.

## Multi-agent handover

When work is interrupted or handed to another agent, the repository must contain
enough durable context for the next agent to continue without relying on the
previous chat session. See `agent-actions/README.md`.

## Codex tooling

- `.codex/config.toml` — project-local Codex sandbox/approval defaults.
- `.codex/rules/` — project-local Codex command rules.
- `.agents/skills/` — repository-scoped portable Codex skills.
- `AGENTS.md` — primary Codex/generic coding-agent instructions.
