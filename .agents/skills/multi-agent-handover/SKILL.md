---
name: multi-agent-handover
description: Maintain durable planning and code-change handovers for this repository whenever a non-trivial development task is planned or implemented, so Claude Code and Codex can continue work from repository state without relying on chat history.
---

# Multi-agent handover

This repository is shared by Claude Code and Codex. Important work must leave
its state in the repository, not only in conversation history.

## Planning artifact

For every non-trivial task that may change code, create a plan before coding:

`agent-actions/planned/plan_<epoch>_<short-slug>.md`

Record:
- objective/problem;
- verified current behavior;
- proposed design;
- affected files/components;
- safety implications;
- alternatives/trade-offs;
- verification plan;
- open questions/assumptions;
- explicit scope and non-scope.

A plan in `planned/` is not approval. Present it to the human and wait for
confirmation before implementation.

## Code-change artifact

After implementing code changes, create:

`agent-actions/coded/code_<epoch>_<short-slug>.md`

Record:
- associated plan filename;
- objective;
- files changed;
- behavior changed;
- important implementation decisions;
- tests/simulations actually run and results;
- what was not verified;
- known risks/follow-ups;
- continuation notes for the other agent.

Never move artifacts to `agent-actions/done/`. The human owns acceptance.

## Multi-agent handoff

Before starting work, inspect:

- `git status`
- `git diff`
- `git log -5 --oneline`
- `agent-actions/planned/`
- `agent-actions/coded/`

Never discard another agent's uncommitted changes without explicit human
authorization.

Do not put secrets into plans, code-change reports, logs, or documentation.
