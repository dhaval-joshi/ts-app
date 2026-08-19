# Architectural and Historical Decisions

This document captures decisions that both agents must understand. The detailed
narrative remains in `README.md`.

## Exit mechanism

Broker-side OCO/conditional orders were tried and found unreliable in real
trading. They were fully retired. The application now watches prices locally
and sends plain market exits.

**Do not reintroduce broker-side OCO.**

## Ownership separation

Live and Paper `OrderManager` instances previously loaded each other's orders.
Orders now carry explicit ownership (`live`/`paper`) so each manager can load
only its own state.

## Recovery

Square-off failures/stalls have a timeout-based recovery path. Changes to this
behavior must preserve the distinction between trigger-driven exits and explicit
close requests.

## Time

The backend uses `Asia/Kolkata` through `backend/clock.py`. This was introduced
to remove dependence on the developer machine's timezone.

## Entry signals

Advanced OMS entry gates are optional and pure. They are checked when starting
a new cycle. Do not move signal logic into the order-exit engine without an
explicit design decision.

## MTM safeguard

The mark-to-market safeguard is opt-in and halts new activity; it deliberately
does not auto-flatten the open cycle that triggered the breach. Changing that
behavior requires explicit human approval.

## Test philosophy

The project values runnable scenario verification over claims based only on
reasoning. Formalizing a permanent test suite is a separate structural decision.

## Current known work

The 19-Aug-2026 discussions identified, among other things:

- a close-reason/retry design issue for manual and Program-driven closes;
- richer SL/target trail history and trigger-based slippage metrics;
- script-master freshness/self-healing;
- websocket liveness/ping and stale-tick detection;
- reconciliation count/coverage concerns;
- single-leg Program entry selection;
- per-leg market direction / RSI / EMA display.

These are **not automatically approved implementation tasks**. They require
planning and human confirmation through the `agent-actions/` workflow.
