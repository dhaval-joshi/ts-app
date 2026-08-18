---
paths:
  - "frontend/**/*.js"
  - "frontend/**/*.html"
---

# Frontend conventions

No build step. Plain HTML, vanilla JS, Tailwind via CDN. Multiple `.js`
files are loaded as separate `<script>` tags into ONE shared global
scope per page — this is the single biggest source of subtle bugs in
this codebase, and it needs active checking, not just careful writing.

## The collision risk, concretely

`index.html` loads roughly: `app.js`, `tabs.js`, `dashboard.js`,
`archive.js`, `calendar.js`, `order.js`, `strategies.js`, `programs.js`
— all in one global namespace. `admin.html` loads a different, smaller
subset. A top-level `function foo()` or `const bar = ...` in two of
these files silently overwrites one with the other — no error, just
wrong behavior at runtime. Before adding a new top-level function or
const to any of these files, check it doesn't already exist in another
file loaded on the same page.

## Verification checklist for any frontend change

1. **Brace/paren balance** per file — a missing `}` in one function can
   silently swallow everything after it as part of that function's body.
2. **Collision check** — for every pair of files loaded together on the
   same HTML page, no top-level `function`/`let`/`const` name repeats.
3. **ID cross-check** — every `document.getElementById("x")` in the JS
   has a matching `id="x"` somewhere in the HTML it's used with. A
   handful of dynamically-created elements (built via `innerHTML` at
   runtime rather than present in the static HTML) will show up as
   "missing" in a naive check — that's expected, not a bug; check the
   specific ID against the JS that creates it before assuming it's wrong.
4. Actually re-read the function you edited after editing it — an
   `str_replace`-style edit that lands one line off can produce code
   that still balances braces/parens but is structurally wrong.

## Established UI patterns, so new work matches

- Dialogs: `resetDialogScroll(rootId)` (in `app.js`) is called on every
  dialog-open, deferred via `requestAnimationFrame` — a plain synchronous
  `scrollTop = 0` right after an `innerHTML` change can land before the
  browser finishes layout, so don't drop the deferred call.
- Live data: the shared websocket connection (`connectStatusSocket()` in
  `app.js`, opened once) delivers order updates every ~2s via
  `onOrdersUpdate(callback)` registration — don't open a second
  connection from a new file; register a listener on the existing one.
- KPI cards: fixed sizing (`text-[12px]`, `h-12`), not relative
  (`text-xs xs:text-base`) — a past attempt at relative sizing caused
  real layout problems on mobile widths.
- Copy-to-clipboard: reuse `copyText(text, label)` from `app.js`, don't
  write a new clipboard implementation.
