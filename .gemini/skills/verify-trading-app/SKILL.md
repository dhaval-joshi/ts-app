---
name: verify-trading-app
description: Write and run a real simulation test against OrderManager using a fake broker client, to verify a change to order_manager.py, program_manager.py, or paper_broker.py actually works. Use whenever a change touches the order lifecycle, trailing, reconciliation, or exit-trigger logic in this trading app — never claim such a change works without actually running one of these.
allowed-tools: Bash(python3 *)
---

# Verifying trading-app order lifecycle changes

This project has no formal test suite. Every fix to `order_manager.py`,
`program_manager.py`, or `paper_broker.py` throughout this project's
history has been verified with a throwaway Python script: build a fake
broker client that records every call it receives, construct a real
`OrderManager`, drive it through the actual scenario the change is meant
to fix, and assert on the real resulting state. Run it, read the output,
then delete the script — this project doesn't keep them (a future
decision to build a real `tests/` directory is tracked separately in
AGENTS.md; don't do that implicitly as part of a single fix).

## The pattern

1. Stub out `httpx` and `pyotp` (the real client's dependencies) so the
   script runs without network access or real credentials.
2. Point `config.DATA_DIR` (and `ORDERS_DIR`/`ARCHIVE_DIR`) at a fresh
   `tempfile.mkdtemp()` so the test never touches real order data.
3. Build a `RecordingClient` — a fake implementing just the methods
   `OrderManager` actually calls (`place_order`, `cancel_order`,
   `get_orders`, and only `get_positions`/`get_trades` if the specific
   code path under test needs them). Have it record every call in a
   list so you can assert on exactly what did and didn't happen —
   proving a broker call did NOT happen is often the actual point (e.g.
   confirming no OCO order gets placed).
4. Build a minimal `FakeStream` with a no-op `set_trailing_symbols`.
5. Construct a real `OrderManager(client=..., stream=...)` — never mock
   this class itself, only its dependencies.
6. Drive the scenario: create an order via
   `create_and_place_order_with_strategy`, then explicitly call
   `await om._reconcile_once()` to let it detect the "filled" entry —
   this does NOT happen automatically just from creating the order, a
   real reconcile pass is a separate step, and forgetting this is the
   single most common mistake when writing one of these tests.
7. Feed ticks with `await om.handle_l1_tick({"symbol": ..., "ltp": ...})`
   to drive trailing and trigger-crossing.
8. Assert on the real order dict returned by `om.get_order(order_id)` —
   status, trigger prices, P&L, and (critically) which broker calls did
   or didn't happen.
9. Print a clear PASS/FAIL line per assertion group as you go, so a
   partial failure is easy to locate, not just a bare traceback.

See `template.py` in this skill's directory for a working skeleton with
all of this already wired up — copy it, don't start from a blank file.

## Known gotchas (each of these caused a real wasted iteration before)

- **Forgetting the reconcile-once call after creating an order.** The
  entry stays `entry_pending` until you explicitly reconcile; ticks
  won't move it there.
- **Advanced OMS specifically**: `pm.tick()` (the Program orchestrator's
  own tick) will hang waiting for a live index spot price unless you
  feed a tick for the index's stream symbol concurrently. Pattern:
  `task = asyncio.create_task(pm.tick()); await asyncio.sleep(0.01);
  await om.handle_l1_tick({...index symbol...}); await task`.
- **Fake option data needs different `exc_token` values for CE vs PE at
  the same strike** if you're testing a Program's two-leg cycle — real
  Tradejini data differentiates them; a fake that doesn't will make one
  tick accidentally trigger both legs at once, which looks like a bug
  in the code under test but is actually a bug in the fixture.
- **A stuck square-off timeout test** needs to set `closing_since` on
  the order to something in the past (`datetime.now() -
  timedelta(seconds=SQUARE_OFF_STUCK_TIMEOUT_SECONDS + buffer)`) — don't
  just wait in real time.
