# Repository Restructure Audit

This package was restructured for shared Claude Code + Codex development.

- Restructure timestamp (Unix epoch): 1787164597
- Application source files were not modified.
- Runtime `data/` files were excluded from this package.
- `.env` was not present; `.env.example` is retained.
- Shared knowledge lives under `docs/`.
- Agent handovers live under `agent-actions/`.

The audit compared SHA-256 hashes of every original Python/JavaScript/HTML/CSS/SVG
file against the restructured working tree. No code-file hashes changed.

## Codex-specific configuration update

The project now includes `.codex/config.toml` and `.codex/rules/default.rules`
for Codex-specific project configuration. `AGENTS.md` remains the primary
Codex/generic agent instruction file.

Repository-scoped reusable Codex skills are under `.agents/skills/`. This is the
current Codex repository skill discovery path documented by OpenAI. The existing
Claude-specific skills remain under `.claude/skills/`.

No application source files were changed as part of this update.
