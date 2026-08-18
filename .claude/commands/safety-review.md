---
description: Review recent changes to order_manager.py/program_manager.py for safety before they're trusted
disable-model-invocation: true
---

Review the current uncommitted changes (or, if none, the most recent
commit) to `backend/order_manager.py`, `backend/program_manager.py`, or
`backend/paper_broker.py` against this checklist. Answer each point
directly — don't summarize around them.

1. **Every state transition still writes to disk immediately**
   (`store.save_order` or equivalent) — a crash mid-trade must be
   recoverable from what's on disk.
2. **No broker-side conditional order (OCO, standalone stop, standalone
   target) is placed anywhere in the new code.** If you find one, this
   is a serious regression — flag it as the top finding, not a footnote.
3. **Every new failure path sets a visible warning** (`_set_warning` or
   equivalent) rather than failing silently. A position that might be
   unprotected must never fail quietly.
4. **The change works identically for Regular OMS orders and Advanced
   OMS Program legs**, since they share this exact code path — check
   both, not just whichever one prompted the change.
5. **A real simulation test was actually run**, not just described. If
   one wasn't, say so plainly and either run one now or tell me clearly
   that this change is unverified.
6. **The "market is closed" / "never resolves" case was considered**
   for anything touching reconciliation or the exit trigger — this
   project has a real history of bugs that only appeared in that
   specific condition.

Give me a short pass/fail per point, then a one-line overall verdict:
safe to trust, or needs another pass before I rely on it live.
