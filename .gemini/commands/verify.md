---
description: Run the full verification sweep before considering any change done
disable-model-invocation: true
---

Run the complete verification sweep this project has always used before
calling a change finished. Don't skip a step because the change "seems
small" — several real bugs in this project's history came from exactly
that assumption.

1. **Backend compile check**: `python3 -m py_compile backend/*.py` — must
   be clean.

2. **Frontend syntax check**: for every changed `.js` file, confirm brace
   and paren counts balance. Report any file that doesn't.

3. **Frontend collision check**: for every pair of `.js` files loaded
   together on the same HTML page (see `.gemini/rules/frontend-conventions.md`
   for which files that is), confirm no top-level `function`/`let`/`const`
   name is defined in more than one of them.

4. **ID cross-check**: for every `document.getElementById("x")` in the
   changed JS files, confirm a matching `id="x"` exists somewhere
   reachable — either in the static HTML or in JS that constructs it via
   `innerHTML`. List anything genuinely unmatched, not just everything
   that doesn't appear in the static HTML.

5. **If the change touched `order_manager.py`, `program_manager.py`, or
   `paper_broker.py`**: write and actually run a throwaway simulation
   test using a fake `BrokerClient` (see the `verify-trading-app` skill).
   Cover the specific scenario the change was meant to fix, not just "it
   compiles." Delete the test script after it passes — this project
   doesn't keep them (see AGENTS.md's note on a possible real test suite,
   which is a separate, bigger decision, not something to do implicitly
   here).

6. **Full backend regression**: construct one instance of each core model
   (`StrategyConfig`, `ProgramConfig`, `CreateOrderRequest`,
   `RiskGroupConfig`, `PortfolioSafeguards`) via `from_dict` and confirm
   no exceptions.

7. **README consistency**: if the change renamed, removed, or changed the
   behavior of anything the README documents, grep the README for the old
   name/behavior and update it in the same change — not as a follow-up.

Report results as a short pass/fail list per step, not a narrative. Call
out anything you couldn't verify (e.g. requires a live broker connection
or live market hours) explicitly rather than treating it as passed.
