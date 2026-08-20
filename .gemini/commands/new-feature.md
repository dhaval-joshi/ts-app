---
description: Structured intake for a new feature request on this project
disable-model-invocation: true
---

I'm about to describe a new feature or change for this project.

Before writing any code:

1. Check `README.md` Section 9 (Phase 3/4 roadmap) — if this overlaps
   with something already designed there, use that design rather than
   re-deriving one, and tell me if what I'm asking for conflicts with a
   decision already made there.
2. If the request touches `order_manager.py`, `program_manager.py`, or
   `paper_broker.py`, read `.gemini/rules/order-manager-caution.md` in
   full first.
3. If the request touches any `frontend/*.js` or `*.html` file, read
   `.gemini/rules/frontend-conventions.md` in full first.
4. Ask me anything genuinely necessary to scope the work before starting
   — but don't ask about things you can determine yourself by reading
   the existing code.
5. After implementing, run the full `/verify` sweep before telling me
   it's done.

Here's the feature:

$ARGUMENTS
