# Agent Actions

This directory is the durable handover channel between Claude Code, Codex, and
the human operator.

## States

### `planned/`

Contains planning artifacts that have been proposed but are not yet approved
for implementation.

Naming:

```text
plan_<epoch>_<short-slug>.md
```

If a plan remains here, the next agent must treat it as an action item and ask
the human whether to proceed. Do not assume presence equals approval.

### `coded/`

Contains implementation handovers produced automatically after code changes.

Naming:

```text
code_<epoch>_<short-slug>.md
```

These documents explain what changed, what was verified, what remains uncertain,
and how another agent can continue.

### `done/`

The human moves accepted `plan_` and `code_` artifacts here. Agents must not
move artifacts to `done/` unless explicitly instructed.

## Rules

- Both Claude Code and Codex must create these artifacts without the human
  needing to ask.
- Before starting work, inspect all three state folders.
- Reuse a matching existing plan rather than creating duplicates.
- Keep the artifacts concise but technically useful.
- Do not put secrets, tokens, passwords, `.env` contents, or sensitive
  credential material in these documents.
- These artifacts are shared project history, not agent-specific notes.
