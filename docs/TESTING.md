# Testing and Verification

## Current state

There is not yet a formal permanent `pytest` suite. The project has historically
used runnable throwaway simulations with a fake `BrokerClient`. Preserve that
verification discipline while the test-suite decision remains open.

Do not create a formal test framework solely as a side effect of an unrelated
feature. If a task would benefit from making the test suite permanent, document
the proposal in the planning artifact and obtain human approval.

## Minimum verification

For backend changes:

1. Compile the affected modules.
2. Exercise the changed behavior with a runnable fake-broker/simulation where practical.
3. Test failure and recovery paths for safety-critical lifecycle changes.
4. Review the final diff.
5. Search for stale names/references after renames/removals.

For frontend changes:

1. Check JavaScript syntax.
2. Check shared global-scope function/constant collisions.
3. Exercise the affected UI path where practical.
4. Review the final diff.

For safety-critical changes in `order_manager.py`, `program_manager.py`,
broker/reconciliation, safeguards, persistence, or streaming:

- Prefer a focused executable simulation.
- Explicitly test the failure path, not only the happy path.
- Do not claim broker behavior was verified unless it was actually observed.

## Live broker

Live testing is never part of the default verification loop.

A test that could place, modify, cancel, or close a real order requires explicit
human authorization for that task.

## Completion report

Every implementation handover must state:

- what changed;
- what was verified;
- what was not verified;
- any live-broker assumptions;
- any known follow-up items.
