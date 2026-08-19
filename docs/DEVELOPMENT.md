# Development Workflow

This repository is shared by Claude Code and Codex. Either agent may inherit
uncommitted or partially completed work from the other.

## Before work

1. Run `git status` and inspect `git diff`.
2. Read `docs/INDEX.md` and the relevant shared documents.
3. Inspect `agent-actions/planned/` and `agent-actions/coded/`.
4. Read the actual current source code.
5. Identify safety-critical paths and existing design decisions.

Never reset, stash, checkout, overwrite, or discard another agent's uncommitted
work without explicit human authorization.

## Plan before implementation

For every non-trivial task that may change code:

1. Create a planning artifact under `agent-actions/planned/`.
2. Use the exact filename pattern:
   `plan_<epoch>_<short-slug>.md`
3. The planning artifact must explain the problem, current behavior, proposed
   design, affected files, safety considerations, verification plan, and open
   questions.
4. If a clearly matching plan already exists, update/continue it rather than
   creating a duplicate.
5. Present the plan to the human and obtain confirmation before implementation.

A file remaining in `planned/` means the work is still an action item. It is not
permission to code.

## Implementation

After approval:

1. Implement the smallest coherent change.
2. Preserve shared architecture and safety invariants.
3. Run the appropriate verification.
4. Inspect the final diff.

## Code-change handover

After code changes are made, the agent must create a durable handover artifact
under `agent-actions/coded/`.

Filename:
`code_<epoch>_<short-slug>.md`

It must include:

- associated plan filename;
- objective;
- files changed;
- behavior changed;
- important implementation decisions;
- tests/simulations actually run;
- results;
- anything not verified;
- known risks or follow-up work;
- whether another agent can safely continue.

The code-change artifact is required without the human having to ask for it.

## Completion state

The agent must **not** move artifacts into `agent-actions/done/`.
The human decides when a planned/coded artifact is accepted and moves it to
`done/`.

When another agent starts, it should inspect `planned/` and `coded/` first.

## Cross-agent handover

A good handover should make the next agent capable of continuing from the
repository alone. Do not rely on "I explained this in the chat" for important
design decisions.
