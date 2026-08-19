# Codex Project Configuration

This directory contains Codex-specific project configuration.

## Files

- `config.toml` — conservative project defaults: workspace-write sandbox and
  user approval for actions that need to leave the sandbox.
- `rules/default.rules` — additional command restrictions for direct secret
  dumping outside the sandbox.

## Important

`AGENTS.md` remains the primary Codex/generic coding-agent instruction file.
Shared project truth lives in `README.md` and `docs/`.

Do not treat this directory as a substitute for `AGENTS.md`, `docs/SAFETY.md`,
or the application's own security boundaries.
