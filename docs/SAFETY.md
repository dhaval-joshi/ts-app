# Safety Invariants

This is live trading software. A defect can cause real financial loss.

These rules apply equally to Claude Code, Codex, and human development.

## Secrets

Agents must never intentionally read, print, copy, expose, commit, or modify:

- `.env`
- `.env.*` except `.env.example`
- `secrets/`
- private keys/certificates such as `*.pem`, `*.key`, `*.p12`, `*.pfx`
- credential files or other secret-bearing local artifacts

`.env.example` is the safe configuration reference.

Do not use `cat`, `type`, `Get-Content`, `grep`, `findstr`, Python, shell expansion,
or equivalent commands to dump secret values.

Git ignore rules are not an access-control mechanism. Agent runtime permissions/
sandboxing must provide the actual boundary where supported.

## Live trading

- Never place a real broker order as part of ordinary verification.
- Never run a live trading action merely to prove that code "probably works."
- Live-broker testing requires explicit human authorization in the current task.
- Prefer Paper, fake-broker, simulation, compile, and static verification.
- Never silently switch a Program between Live and Paper.
- Never silently increase quantity, exposure, or trading scope.

## Exit safety

- Broker-side OCO/conditional exit placement is retired and must not be reintroduced.
- Application-watched market exits are the current exit mechanism.
- Do not weaken, bypass, remove, or reorder safety checks merely to make a test pass.
- Do not introduce automatic flattening on a safeguard halt without explicit human approval.
- Preserve close intent/reason semantics when changing the order lifecycle.

## Persistence and recovery

- Preserve atomic persistence semantics.
- Treat broker truth and application state as distinct until reconciliation proves otherwise.
- Changes to recovery/reconciliation logic require tests for failure, retry, and restart paths.
- Never discard runtime data to make a development issue disappear unless explicitly instructed.

## Time

Backend timestamps must use `backend/clock.py`. Do not introduce bare
`datetime.now()`, `date.today()`, or direct naive timestamp parsing in backend code.

## Uncertainty

Separate:

- **Verified** — directly demonstrated by code/tests.
- **Inferred** — reasoned from current behavior but not directly demonstrated.
- **Live-unverified** — requires observation against the broker/market.

Never present live-unverified behavior as fact.
