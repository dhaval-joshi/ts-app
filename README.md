# Tradejini Trading Station

A small, self-hosted app that places an order, waits for it to fill, protects
it with a stop-loss + target, trails those if you ask it to, and closes
it on a schedule you set. Built to be readable end-to-end, not "clever."

It does **not** browse market data, symbols, or charts. You bring the exact
`symId` (and, only if you want trailing, the streaming symbol) for each
trade; the app's only job is managing that order through its life.

---

## Migrating to this version (read this first)

This version adds **login**, a full **Advanced OMS** (auto-trading Programs
with Paper/Live modes, capital-spread sizing, Risk Groups, scheduling, a
margin pre-check), a **Portfolio** landing view, and an **Admin** section
(accent color, a filterable Failures browser) on top of everything before it.
It's a large jump from the last version -- here's exactly what to do.

1. **Stop the old app first**, same as always -- never run two processes
   against the same `data/` folder at once.
2. **Set app login credentials before starting.** This version genuinely
   refuses to start at all without them (see "Login" below for why). Add to
   your `.env`:
   ```
   APP_LOGIN_USERNAME=<pick a username>
   APP_LOGIN_PASSWORD=<pick a password>
   SESSION_TTL_DAYS=7
   ```
   This is a login for the app itself, unrelated to your Tradejini
   credentials -- plain text, single shared login, no user accounts,
   deliberately simple for a single-operator local app.
3. **Copy everything from your old `data/` folder over, including
   `data/programs/`** if you have any saved Programs from earlier testing.
   Every new field this version adds (Risk Group, Schedule, Mode, capital
   sizing, the `archived` flag) is backfilled automatically with sensible
   defaults the first time an old Program loads -- nothing to migrate by
   hand, and nothing gets silently dropped. Existing Programs come back as
   `mode: "live"`, `sizing_mode: "lots"` (i.e. exactly how they already
   behaved), unarchived, with a Risk Group auto-created per underlying if
   they didn't have one yet.
4. **If you're using Advanced OMS, add `data/market_holidays.json`** (a
   JSON array of `"YYYY-MM-DD"` strings) if you haven't already -- see the
   Advanced OMS section below for what it's for.
5. **Hard-refresh your browser** (`Ctrl+F5`) on first load -- a large
   amount of frontend structure changed (New Order and Strategies both
   moved from standalone pages into the main app shell; old bookmarks to
   `/order`, `/strategy`, `/calendar`, `/archive` all still work and
   redirect to the right place, but a stale cached script could cause
   confusing errors otherwise).
6. **First login**: visit the app, you'll land on `/login` automatically.
   After signing in you'll see the new **Portfolio** landing view (combined
   KPIs + a combined Calendar) -- Regular OMS and Advanced OMS are now nav
   links instead of tabs on the dashboard.
7. **Nothing about how existing Programs trade changes on upgrade.** A
   Program that was already running keeps running exactly as before --
   `mode: "live"` and `sizing_mode: "lots"` are the defaults specifically so
   upgrading never silently changes a Program's real-money behavior. Paper
   mode and capital sizing are both opt-in per Program from here on.
8. **Existing Strategy files just work, no manual renaming needed** -- a
   Strategy file saved before the strategy-id system existed gets one
   backfilled automatically from its own filename the moment it's loaded,
   so editing or renaming it afterward correctly updates that same file in
   place rather than creating a stray duplicate.
9. **This version needs internet access to look right**, and has since the
   theme moved to Tailwind CSS -- fonts/icons/Tailwind itself all load from
   a CDN on every page load. If you're ever on a machine with restricted/no
   internet access, the core trading logic still runs fine, but the page
   will render completely unstyled until connectivity is available again.

---

## 1. How it works (read this before the setup steps)

Strategies and orders are deliberately separate things:

- A **strategy** (`/strategy` page) is a reusable, symbol-agnostic template:
  entry order type, stop-loss and target expressed as an *offset* from
  wherever you enter (in points or percent), trailing rules, and a
  time-based close rule. It knows nothing about which instrument or how
  much.
- An **order** (`/order` page) is one specific trade: symbol, side,
  quantity, which strategy to apply, and (only if that strategy's entry
  type needs one) the actual entry price/trigger.

```
 you (Strategies page)              you (New Order page)
        │  POST /api/strategies            │  POST /api/orders {strategy_name, sym_id, qty, side, ...}
        ▼                                   ▼
 data/strategies/<name>.json    ┌─────────────────────────────────────────────────────────────┐
        ▲ loaded by ────────────┤ OrderManager (backend/order_manager.py)                       │
                                 │                                                                │
                                 │  1. place entry order            → Tradejini REST              │
                                 │  2. poll GET /api/oms/orders      every 5s, notice fill         │
                                 │     (a websocket "something changed" event just wakes this      │
                                 │      poll up sooner -- the REST response is the source of        │
                                 │      truth, since the event payload fields aren't fully          │
                                 │      documented)                                                 │
                                 │  3. once filled → convert the strategy's SL/Target OFFSETS       │
                                 │     into concrete prices anchored to the fill price, then         │
                                 │     start WATCHING live ticks -- no broker order is placed        │
                                 │     for the exit at all                                            │
                                 │  4. on every live L1 tick for that order's stream symbol:          │
                                 │     ratchet the trigger price if trailing is on for a leg,         │
                                 │     then check if price has crossed the current trigger            │
                                 │  5. trigger crossed (or a time-based exit is due) → fire a         │
                                 │     plain market order for whatever's still open                   │
                                 │  6. every state change is written to data/orders/<id>.json         │
                                 │     immediately, so a crash mid-trade is recoverable                │
                                 └─────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
                                 data/orders/*.json   (one file per order = your trade history + state)
```

On startup the app reads every file in `data/orders/`, finds anything not in
a terminal state (`closed` / `cancelled` / `entry_rejected`), and resumes
managing it -- re-subscribing to live prices if it was trailing, and
re-arming its time-based exit check. That's the whole crash-recovery story.
Editing or deleting a strategy later never touches orders already placed
with it -- each order keeps its own resolved SL/Target/trailing state from
the moment it filled.

### Why offsets (points/%) instead of fixed prices in a strategy

A strategy is meant to be reused across symbols and days, where absolute
price levels are meaningless (a stop at "2400" makes sense on one stock and
is nonsense on another). So a strategy stores *how far* the stop/target sit
from wherever you actually enter -- "1% below entry" or "15 points below
entry" -- and the app resolves that into a real price the moment your entry
order fills, using the real fill price. From that point on, everything
(including trailing) operates on concrete prices.

### Exit mechanism: app-watched market order -- the ONLY mechanism now, OCO fully retired

Every exit -- Regular OMS orders and every Advanced OMS Program leg --
closes exclusively via a plain market order this app fires itself, the
instant live price crosses the stop or target trigger. **No conditional
order (OCO, a standalone stop order, a standalone target order) is ever
placed at the broker for a new position, full stop.** This is hardcoded
and unconditional in `order_manager.py`, not a per-Strategy/per-Program
choice -- there used to be one, and it's been removed entirely (see
"Historical" just below for why).

`exit_mode` (Both / Stop-loss only / Target only / Neither, chosen per
trade) still exists and still means the same thing it always did -- WHICH
leg(s) get watched and protected. What's gone is any notion of *how*:
every exit_mode now resolves the exact same way, a plain market order
fired by this app when the relevant trigger crosses, never a broker-side
conditional order of any kind.

**This requires the app to be running continuously, with the
instrument's stream symbol set, for the entire lifetime of every open
position.** Nothing watches the price -- and nothing protects an open
position -- if either isn't true. See the server-down warning
(Section 4) and keeping this app running reliably (a supervised/
auto-restarting process, not just a terminal window you might close) is
a real operational requirement.

The old broker-OCO code path (placement, trailing-modify, reconciliation
-- everything specific to that retired mechanism) has since been removed
from the codebase entirely, not just deprecated. There's no backward-
compatibility concern to weigh against that: this app was still in
active development when OCO was retired, with no live positions
depending on the old code path by the time it was removed.

### Historical: why OCO was tried first, and why it was retired

This app originally placed a real OCO order at the broker for every
exit, and this section used to be a live troubleshooting guide for it.
It's kept, condensed, because the underlying facts are still real and
still instructive -- but nothing below describes current behavior; OCO
is fully gone from the exit path (see above).

**Two confirmed, root-caused bugs in Tradejini's own OCO endpoints**, found
by testing directly against the live API, not just inferred:
- `stopLossProduct`/`targetProduct` only actually accept `"delivery"` or
  `"normal"` in Tradejini's real schema -- never `"intraday"`, despite
  their own docs claiming it's supported. Passing an intraday order's
  product straight through made every trailing modify on an intraday
  order fail outright.
- `modify-order/oco` rejected a quantity sent as a decimal (e.g.
  `"65.0"`) with *"Bad request"*, while accepting the identical value as
  a plain integer (`"65"`) -- `place-order/oco` tolerated the decimal
  form fine. Modify was measurably stricter about request shape than
  place, for no documented reason.

Beyond those two, Tradejini's OCO modify endpoint was also observed
rejecting requests for reasons that never reduced to a clean root cause
(an intermittent margin-shortfall-style rejection was one confirmed
example, independent of anything this app sent).

**The failure mode that ultimately mattered most**: when an OCO's stop
or target leg filled, Tradejini's own order-status endpoint didn't
reliably reflect it -- in practice, a position could close at the broker
while this app's dashboard kept showing it as open, even after a manual
refresh, because the OCO's own order id sometimes never surfaced a
"completed" status at all. A meaningful amount of engineering effort
(documented in earlier revisions of this README, if you need the detail)
went into a multi-tier detection fallback (position-flat checks, trade
records, last-known-tick estimates) to work around this -- and it still
wasn't fully reliable. Real capital was lost to this before it was fully
understood, which is the actual reason OCO was retired outright rather
than patched further: the fix wasn't "detect the failure better," it was
"stop depending on a mechanism this unreliable at all."

### Trailing and exit checks are now local-only -- no broker calls at all

Trailing recalculates the stop/target trigger price on every live tick,
purely in this app's own memory, and persists the updated value to
`data/orders/<id>.json` -- there's no broker API call involved anymore
(no modify-order, nothing to fail, nothing to retry, nothing to
rate-limit). The moment price crosses the current trigger, the app fires
the same reliable plain-market-order close used everywhere else in this
app (Stop & Flatten, manual close, time-based exits).

If that market order itself gets rejected, or gets accepted but never
resolves to a confirmed fill within a reasonable time (most commonly:
placed at or after market close, accepted by the broker but never
settling within the session) -- the app automatically reverts the order
to its watched state, tries again the next time a live tick arrives, and
raises a persistent, visible warning banner on the order's card the
entire time it's unresolved, with the broker's own error/reason and how
long it's been going on. It clears automatically once the position
actually closes.

### Live and realized P&L

Any order with a stream symbol gets live prices while it's being watched
(`watching` status) -- regardless of whether trailing is on, since P&L
tracking needs the same ticks trailing does. The dashboard shows:

- **Live price and live P&L** while a position is open: `(current price −
  entry avg) × qty`, in your favour or against, updated as ticks arrive.
  This is held in memory only (not written to disk on every tick -- there's
  no need to, and it would be a lot of needless disk I/O), so it reappears
  within a couple of seconds after a restart once ticks resume, rather than
  surviving a crash moment-to-moment. That's fine since it's a live
  indicator, not a record. Note: once an order enters `closing` status,
  its P&L stops updating live at all (ticks are only fed to
  still-`watching` orders) -- what's shown for a closing order is a frozen
  snapshot from the instant the close fired, not a continuing figure.
- **Realized P&L** once a position closes: `(exit avg − entry avg) × qty`,
  computed from the broker's own square-off fill price -- not from ticks,
  so this is accurate and permanent even for orders with no stream symbol
  at all.
- **Day P&L and Overall P&L** at the top of the dashboard: Day = P&L from
  positions closed *today* + live P&L of everything currently open. Overall
  = P&L from *every* closed position, ever, + live P&L of everything
  currently open. Both are computed in the browser from what `/api/orders`
  already returns -- there's no separate ledger or database. If some open
  positions don't have a live price (no stream symbol, or an exchange
  mismatch -- see above), the summary line tells you how many of your open
  positions are actually contributing a live number right now.

Neither figure accounts for brokerage, taxes, or other charges -- it's raw
price P&L only.

### Why "trailing target" needs L1 ticks, but nothing else does

You explicitly asked for trailing SL *and* trailing target. Tradejini has no
native field for a trailing profit target, so the app has to watch price to
move it -- there's no way around that for that one feature. Everything else
(order status, fills, positions) is tracked via the REST order/position
endpoints, not price data. If you never enable trailing on any order, the
app never subscribes to a single price tick.

To keep this from becoming a "market data" feature, there's no symbol
search/lookup built in. If you want trailing or live P&L on an order, you
supply the streaming symbol (`<excToken>_<exchangeName>`) directly in the
order form. Leave it blank and the order still works exactly as configured
-- trailing just won't move, and no live P&L will show (final/realized P&L
still works fine either way, since that comes from the broker's own fill
prices, not live ticks). The New Order page now warns you loudly if the
strategy you picked trails but you left the stream symbol blank.

**Getting the exchange part of the stream symbol right matters.** It is
*not* always `NSE` -- it's whichever exchange segment the instrument
actually trades on, and for anything other than plain NSE equity that's a
different code:

| Segment | Exchange code |
|---|---|
| NSE equity (cash) | `NSE` |
| NSE F&O (futures & options) | `NFO` |
| BSE equity (cash) | `BSE` |
| BSE F&O | `BFO` |
| MCX commodities | `MCX` |
| Currency derivatives | `CDS` |

The good news: you don't need to look this up separately -- it's usually
already embedded in the `symId` you're using for the order itself. For
example `OPTSTK_INDIANB_NFO_2026-08-25_880_PE` has `NFO` right there as the
third underscore-separated segment -- that's the exchange code your stream
symbol needs, e.g. `52094_NFO`, not `52094_NSE`. Get this wrong and nothing
breaks loudly -- the app just never receives a single tick for that order,
so trailing silently never moves and live P&L never appears. The New Order
page now flags an obvious mismatch between the two for you (a rough check,
not a guarantee) -- but the underlying rule above is the one to trust.

---

## 2. Setup

Requires **Python 3.10+**.

```bash
cd tradejini-trading-station
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# now edit .env:
#   TRADEJINI_API_KEY      - from your app in the developer portal
#   TRADEJINI_PASSWORD     - your Tradejini login password
#   TRADEJINI_TOTP_SECRET  - the base32 secret behind your authenticator QR
#                            code (Settings -> authenticator app setup on
#                            cubeplus.tradejini.com). The app generates a
#                            fresh 6-digit code from this every time it logs
#                            in, so you never paste an OTP by hand.
```

### Troubleshooting the install

This app's dependency stack is deliberately 100% pure Python — nothing here
compiles anything, on any Python version or Windows architecture (including
ARM64), so you should never need to install a compiler, Rust, or a second
Python version. If `pip install -r requirements.txt` ever tries to compile
something (you'd see `Compiling ... error: linker not found` or similar),
it means a dependency version snuck in that isn't pure-Python — first try:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `uvicorn` isn't recognized after installing, either the install above
didn't actually finish (check the output for errors) or your venv isn't
active in the current terminal (`.venv\Scripts\activate` on Windows). You
can always sidestep PATH issues by running it as a module instead:

```powershell
python -m uvicorn backend.main:app --reload --port 8000 --ws wsproto
```

Run it:

```bash
uvicorn backend.main:app --reload --port 8000 --ws wsproto
```

Open **http://localhost:8000** for the dashboard, **http://localhost:8000/strategy**
to build strategies, or **http://localhost:8000/order** to place a trade.

**If a page (Dashboard, Archive, Calendar, etc.) loads blank** with no
errors in the terminal, it's almost always the browser serving a cached,
stale copy of a `.js` file after an update -- do a hard refresh
(`Ctrl+F5` or `Ctrl+Shift+R` on Windows, `Cmd+Shift+R` on Mac) before
looking any further. Every script tag has a `?v=<number>` cache-buster on
it specifically to prevent this, but a browser that already had a page open
across an update can still hang onto an old cached copy until you force it.
If it's not that, open the browser's dev tools (F12) → Console tab, which
will show the actual JS error.

If login fails -- bad credentials, no internet, a DNS resolution error
(`getaddrinfo failed` on Windows), or the broker being briefly down -- the
app still starts and stays fully usable; you'll see the error in the
terminal log and the "live feed" dot on the dashboard stays red. No restart
needed for anything transient: it retries automatically every 30 seconds
and picks up the moment whatever was wrong clears. For bad credentials,
fix `.env` and either wait for the next retry or just restart.

If the terminal shows `getaddrinfo failed` (or any "could not connect"
error) specifically, double check `TRADEJINI_HOST` in `.env` is a bare
hostname like `api.tradejini.com` -- not a full URL. Accidentally including
`https://` there used to produce a malformed address that fails DNS
resolution with exactly this cryptic error; the app now strips a stray
`https://`/`http://` prefix and trailing slash automatically, but a typo'd
hostname obviously can't be auto-corrected. The exact address it's trying to
reach is logged on every startup/retry attempt.

---

## 3. Field glossary

### The four strategy fields, explained simply (with worked examples)

These four live on the **Strategies** page, one set for the Stop leg and
one for the Target leg. All four examples below assume the offset unit is
set to **percent**, since that's the default and the easiest to reason
about -- buy 100 shares at ₹100 each.

**Trigger offset from entry** -- how far the price has to move from your
entry before that leg fires, as a % of entry price.
- *Stop, 1%*: your stop-loss triggers if price falls to ₹99 (1% below ₹100).
- *Target, 2%*: your target triggers if price rises to ₹102 (2% above ₹100).

**Limit buffer past trigger** -- once a leg triggers, it becomes a *limit*
order, not a market order -- this is how much further, as a %, the limit
price sits past the trigger, as a slippage cushion so it still fills even
if price has moved a bit more by the time the order reaches the exchange.
`0` means the limit price equals the trigger price exactly.
- *Stop, 1% trigger + 0.1% buffer*: triggers at ₹99, but the actual sell
  order allows a fill down to ₹98.90 -- so it doesn't sit unfilled at
  exactly ₹99 if price gaps slightly past it.
- *Target, 2% trigger + 0.1% buffer*: triggers at ₹102, sell order allows
  down to ₹101.90 -- same idea, a small cushion to actually get filled.

**Trail by** -- once trailing kicks in, how far behind the current price
that leg re-anchors itself, as a %. It only ever moves in your favour,
never back.
- *Stop, trail by 1%*: as price rises to ₹110, your stop keeps ratcheting
  up to stay 1% below the current price (₹108.90) -- it never moves down
  again even if price dips afterward.
- *Target, trail by 2%*: as price keeps rising past your original ₹102
  target, the target itself extends further out, staying 2% above current
  price, letting a strong move run further before locking in the win.

**Activation offset (profit)** -- how much open profit, as a %, must exist
before trailing starts moving that leg at all. `0` means trail from the
very first tick after entry.
- *Stop, activation offset 0.5%*: the stop-loss sits still at its original
  ₹99 until the position is at least 0.5% in profit (price ≥ ₹100.50) --
  only then does it start trailing upward. Prevents small early wiggles
  from moving your stop before the trade has proven itself.

---

**Entry order type / Validity** (New Order page) -- how the entry order
itself behaves: `market` fills immediately at whatever price is available;
`limit` only fills at your specified price or better; `stoplimit` /
`stopmarket` sit dormant until price reaches a trigger, then fire. Validity
(`day`/`ioc`/`eos`/`gtc`) is how long the order stays live if unfilled.
These are chosen per-trade, not saved on the strategy -- the same strategy
(management style) can be entered with a market order one day and a limit
order the next.

**Entry trigger price** (New Order page, only shown for `stoplimit` /
`stopmarket` entry types) -- the price at which your entry order wakes up.
It sits at the broker as a pending "stop" order; the moment the exchange
price touches this trigger, the order fires. For `market` and `limit`
entry types this doesn't apply, so it's hidden.

**Entry / reference price** (New Order page) -- a single field serving two
purposes: it drives capital-based quantity sizing (and a "capital
utilized" preview) regardless of entry type, and if the entry type is
`limit` or `stoplimit`, it's also sent as the actual entry limit price
(rounded up to tick size). Fill it by hand or with **Fetch price**.

**Exit legs** (New Order page) -- which leg(s) actually get watched and
protected once the entry fills: **Both** (stop and target, whichever
crosses first closes the position), **Stop-loss only**, **Target only**,
or **Neither** (nothing watched; only a manual or time-based close
applies). Defaults to Both. Every option resolves the same way -- a
plain market order this app fires itself when the relevant trigger
crosses (see "Exit mechanism" in Section 1).

**Trigger price** (on a strategy's Stop or Target leg) -- see "Trigger
offset from entry" above; this is the resulting absolute price once the
offset is resolved against your actual entry fill.

**Limit price** (on a strategy's Stop or Target leg) -- see "Limit buffer
past trigger" above.

**"Trailing requires a Stream symbol... will not trail"** -- trailing needs
live price ticks, which arrive over Tradejini's *streaming* socket,
addressed by a different identifier (`excToken_exchange`, e.g. `2885_NSE`)
than the REST `symId` you use everywhere else. Without it on an order, the
app has no live price to trail against for that instrument, so the SL/Target
still get placed at their initial level from the strategy -- they just never
move. The New Order page shows a warning banner if you pick a trailing
strategy and leave this blank.

**Market protection %** -- Tradejini rejects `market`/`stop-market` entry
orders placed via the API unless a protection percentage is set (error:
*"Market protection mandatory for all market order."*). It's a price collar
-- the order can only fill within that % of the last traded price, so it
can't execute at a wildly bad price during a fast move. Handled
automatically now (5%, not user-configurable) whenever the entry type
needs one -- nothing to set.

**Tick size** -- the instrument's minimum price increment (`0.05` for most
NSE equity and F&O contracts, but not universal -- check yours if it's
unusual). Every price the app computes for you -- the initial SL/Target
levels, and every trailing update -- gets rounded to the *nearest* multiple
of this before being sent to the broker. Two deliberate exceptions round
differently, both by explicit request:
- A manually-entered **"Close @ price"** on the dashboard rounds *down*
  (never fills worse than what you asked for).
- A manually-entered **entry price** on the New Order page rounds *up*.

Skip tick_size on a strategy and the broker rejects the order outright
(error: *"Price X is not a multiple of tick size Y"*). Defaults to `0.05`
if unset, including for strategies saved before this field existed.

**Fetch price** (New Order page, and the Dashboard for a manual close) --
fetches the current price for the instrument, on demand, with a single
click. This is the one deliberate exception to "no market data" anywhere
in this app: a single, explicit, user-triggered check for a specific
instrument, using the same live feed trailing/P&L already rely on -- not a
watchlist, chart, or anything you'd browse. It needs the Stream symbol
field filled in (not just Symbol ID). Fails clearly, not silently, if the
live feed isn't connected, the symbol looks wrong, or nothing arrives
within about 8 seconds (e.g. market closed).

---

## 4. Using it

**Strategies** (`/strategy`) is pure position-management templating -- no
symbol, quantity, entry order type, or validity here (those are per-trade,
on the New Order page). Define product, stop and target as offsets from
entry (percent or points), trailing per leg, and a time-based close rule.
Save it under a name. Trailing SL and trailing Target are **on by default**
(1% and 2% respectively) when you create a new strategy -- untick the boxes
to turn either off. A fresh strategy with product `intraday` also defaults
its time-based close to the **15:10-15:15 window**; change or clear it if
you don't want a scheduled close, and this default never overrides a mode
you've deliberately set on an existing strategy you're editing. Editing a
strategy later never changes orders already placed with it; each order
snapshots what it needs at fill time.

**New Order** (`/order`) is where you actually trade: symbol, side, entry
order type/validity/price, sizing, which strategy to apply for
stop/target/trailing/time-exit, and which **exit legs** to actually watch
(Both/SL-only/Target-only/Neither -- see "Exit mechanism" in Section 1
for how every option resolves the same way).

*Getting a price*: enter the Symbol ID and Stream symbol, then click **Fetch
price** to get the current price on demand -- this is the one deliberate
exception to "no market data" in the whole app: a single, explicit,
user-triggered price check for the specific instrument you're about to
trade, using the same live feed trailing/P&L already use, not a
watchlist/chart feature. It momentarily subscribes to that symbol's ticks,
waits up to 8 seconds for one to arrive, then drops the subscription again.
Fails clearly (not silently) if the feed isn't connected, the symbol looks
wrong, or nothing arrives in time. You can also just type a price by hand
instead of fetching one.

That price (fetched or typed) feeds the **Entry / reference price** field,
which does two things depending on context:

- If the entry order type needs a limit price (`limit` or `stoplimit`),
  it's sent as that price -- rounded **up** to the strategy's tick size,
  per an explicit request (e.g. requesting `13.91` on a 0.05-tick
  instrument sends `13.95`).
- Either way, it drives quantity sizing and a capital-utilized preview:

  - **By number of lots**: set a lot size (default `1`) and how many lots;
    quantity is `lot_size × num_lots`. This is also what makes **Re-enter**
    reproduce the exact same sizing later, not just the same total
    quantity -- lot size is saved on the order specifically for this. If a
    price is set, the page also shows `≈ ₹X capital utilized`.
  - **By capital allocation**: enter capital to deploy; quantity is
    `floor(capital ÷ (entry_price + 2))`, rounded down to a whole number of
    lots. The `+2` is a fixed 2-point slippage buffer, so if the actual fill
    price is a touch worse than your reference, the order still fits your
    capital instead of getting rejected for insufficient funds/margin.

The page shows a live preview of what that strategy will do (stop/target
offsets, trailing, time-exit -- only for the leg(s) your chosen exit mode
actually places) and reveals the entry trigger price field only if the
entry type needs one. If a leg that's actually being placed trails but you
haven't given a stream symbol, you'll see a warning right there before you
submit.

**Dashboard** (`/`) shows Day and Overall P&L at the top, then every order
as a card: live price and live/realized P&L, status, entry avg price (with
a **Copy** button next to it), live stop/target trigger (or, before fill,
what it *will* be, e.g. `1% (on fill)`), fill qty, **Buy amount** and
**Sell amount** (price × qty for whichever side was the entry vs. the
exit), which strategy it's running, and a log of everything the app did to
it. Below the P&L totals, three KPIs -- **days traded**, **days in
profit**, **days in loss** -- computed across every closed order,
including archived ones. If anything happened that could leave the
position under-protected -- a trailing update rejected, an exit order
failing to place, a square-off failing -- a red warning banner appears
right on the card with the broker's actual error message, so you don't
have to go digging through logs to notice. "Close position" cancels
whatever exit order(s) are live and squares off whatever's open,
immediately, regardless of schedule:

- Leave the "Close @ price" box empty and it closes **at market**.
- Click **Fetch price** next to the box to pull the current live price into
  it (needs the order to have a stream symbol) -- a quick way to see where
  the market is before deciding what to close at.
- Enter a **% profit on entry** and click "Set price from %" to compute the
  close price for you instead of typing an absolute number -- e.g. `5` on a
  buy at 100 fills in `105`; on a short (sell) it correctly fills in `95`,
  since profit on a short comes from price falling, not rising.
- Enter a price and a line under the box shows exactly what will be sent --
  the price **rounded down** to the instrument's tick size (e.g. requesting
  `13.99` on a 0.05-tick instrument shows/places `13.95`) -- before you
  click Close, so there's no surprise about what actually gets submitted.
  It closes with a **limit order** at that (rounded) price instead of
  market. A limit close isn't guaranteed to fill immediately, or at all, if
  the market never reaches your price -- unlike a market close.

Day/Overall P&L each also show a **%** figure next to the amount --
P&L ÷ capital deployed across the relevant orders (today's for Day, all-time
for Overall; an open position's entry value counts toward both, same as its
unrealized P&L already did). It updates live over a websocket every couple
of seconds.

**Multi-select and bulk archive**: every closed/cancelled/rejected order
card gets a checkbox (top-left); "Select all closed" picks every eligible
one at once. "Archive selected" archives all of them in a single request,
and reports which (if any) couldn't be archived rather than failing the
whole batch over one.

**"Re-enter"** on any order card takes you to the New Order page with
symbol, side, stream symbol, strategy, entry type/validity, exit legs, and
**the same lot size and number of lots** prefilled from that order (lot
size is saved on the order specifically so this reproduces the actual
original sizing, not just the same total quantity). If that order had an
entry price set, it's prefilled too, but it's worth a fresh look (or a
**Fetch price**) rather than trusting an old number, especially if time has
passed. If the original strategy was since deleted, you'll be prompted to
pick a new one before placing.

**The info icon** ("i", bottom-right corner of any order card, on the
Dashboard, Archive, or a Calendar day's order list) opens a details panel
with everything about that trade in one place: entry and exit avg price,
quantity, product/strategy, how many times the SL/Target were successfully
trailed and how many of those attempts failed, final P&L and exactly which
source it came from (an exact broker fill match vs. a rough estimate --
see "Live and realized P&L" in Section 1), the current unresolved
warning if there is one, and every timestamp. At the bottom, a **copiable
filename** (so you can find the exact file on disk yourself) and an
**Open JSON** link that shows the raw, complete order record.

**Archive** (a tab on the Dashboard, alongside Calendar) is for tidying up
the dashboard's order list without losing anything or affecting your
totals. "Archive" on a closed/cancelled order
card (or a bulk selection) moves its JSON file into `data/orders/archive/`
-- it drops off the main dashboard's order list, but **still counts toward
Day/Overall P&L and the KPIs there**, and still shows in the **Calendar**.
Archiving only tidies up what you see day-to-day; it never changes what
your numbers say. Every archived order keeps its own Info panel and a
working **Unarchive** button to bring it back to the active list.

**Calendar** (a tab on the Dashboard) is a month view of realized P&L by day, across
both the active dashboard and the archive -- click a day to see the orders
that closed on it, and from there straight into that order's Info panel.
Useful for reviewing a specific day's trading, or for keeping a mental
handle on your overall track record without archived history disappearing
from view.

### Time-based close, two ways

- **Intraday window**: set `window_start` (e.g. `15:10`). The moment the
  clock hits that time, the app closes the position at market. There's no
  separate "hard deadline" enforcement beyond that -- pick a start time
  early enough that a single market order clears comfortably before your
  actual cutoff (e.g. `15:10` if your hard deadline is `15:15`).
- **Specific date/time**: set an exact datetime; the app closes at or after
  that moment, any product type, PnL irrelevant.

---

### Exit mechanism

Same as Regular OMS -- every Program leg closes via the app-watched plain
market order mechanism described in Section 1 above; no broker-side
conditional order is ever placed. Worth knowing specifically for
Programs: this used to be a per-Program *choice*, and that choice had a
real, confirmed bug -- `program_manager.py`'s leg-construction code never
actually threaded a Program's own `exit_mechanism` setting through to
order placement, so every Program silently used the old broker-OCO
mechanism regardless of what was configured. Rather than just fix that
threading bug and keep the choice available, the choice itself was
removed and app-watched is now unconditional for every new order --
eliminating the entire bug class, not just this one instance of it.

### Server-down warning

A full-screen, hard-to-miss red overlay appears in any open browser tab
if the app's status check fails 3 times in a row (~15 seconds) --
telling you plainly that Programs, safeguards, and order management are
not running, and any open positions have no active protection until the
connection returns. Disappears automatically the moment the connection
is restored, no refresh needed.

**Important limitation, stated plainly**: this can only warn you if a
browser tab with this app is already open and being watched when the
server goes down. It cannot alert you if no tab is open, and it's not an
email/SMS/push notification -- it's an in-tab safety net, not a
monitoring service.

## 5. What's deliberately left out

Per your brief -- if a feature doesn't serve "place it, protect it, close it
on schedule," it's not here:

- No market-data browsing, charts, watchlists, or symbol search.
- No multi-account / multi-broker support.
- No order book depth, greeks, OHLC -- the streaming SDK supports all of
  these, this app subscribes to none of them.
- No database server -- JSON files are the entire persistence layer. If you
  outgrow that later, `backend/store.py` is the only file that would need to
  change (it's a thin, isolated read/write layer).

## 6. Known simplifications (read before trading size)

- **Partial fills**: the entry-fill detection currently treats a broker
  order `status == "completed"` as "fully filled, arm the exit now." If
  Tradejini reports partial fills differently in practice, watch the first
  few trades closely -- this is the one piece of broker behavior that
  couldn't be confirmed from the docs alone.
- **Reconciliation runs every `EXIT_CHECK_INTERVAL_SECONDS` (default 5s)**,
  so there's up to that much lag between a fill actually happening and the
  app reacting to it (starting to watch it, noticing a close confirmed).
  Trailing and the exit trigger check are both instant (tick-driven) --
  this lag only affects "did the entry fill" and "did the close confirm"
  detection.
- **Percent offsets are computed once, at fill time, off the fill price**,
  then held as fixed points from then on -- including for trailing. "Trail
  by 1%" doesn't mean 1% of the constantly-changing current price; it means
  1% of your entry price, computed once, so the trailing distance itself
  doesn't jump around as price moves.
- **No sandbox environment exists for this API** (per the docs) -- all
  testing is against your live account. Start with the smallest quantity
  the instrument allows.
- **Market protection defaults to 5%** on any market/stop-market order (see
  the glossary above) if a strategy doesn't specify its own. If your broker
  segment/instrument needs a tighter or looser band, set it explicitly on
  the strategy rather than relying on the default.
- **Tick size defaults to 0.05** (see the glossary above) if a strategy
  doesn't specify its own -- correct for most NSE equity/F&O, but check and
  set it explicitly for instruments with a different tick.
- **All times are IST, hardcoded** -- `backend/clock.py` is the single
  source of "now" for the whole backend and always resolves to
  `Asia/Kolkata`, independent of the host machine's own timezone. This
  matters if you ever run this somewhere other than an IST-local machine
  (see Section 10, Cloud deployment): market-hours gates, the EOD square-off,
  and daily counter resets all key off this, not the host clock.
- **P&L is price-only** -- it doesn't account for brokerage, STT, or other
  charges, and live P&L needs a stream symbol (see "Getting the exchange
  part right" above) -- get the exchange segment wrong and it just silently
  never appears rather than erroring.
- **Fetch price waits up to ~8 seconds** for a tick before giving up -- if
  the market's closed, the stream symbol's wrong, or the feed just hasn't
  ticked that instrument recently, it'll time out rather than hang
  indefinitely. Type a price by hand in that case. Available on both the
  New Order page (for sizing/entry price) and the Dashboard (to help pick a
  "Close @ price" for a manual close).
  just what value goes out on the wire.
- **A trailing modify that keeps failing pauses itself** after 3 consecutive
  failures on the same order, for 60 seconds, rather than retrying every
  tick indefinitely -- see "Trailing modify failures are now self-healing"
  above.
- **The dashboard's live websocket update no longer interrupts typing.**
  Every order card gets rebuilt roughly every 2 seconds to reflect fresh
  data -- earlier this could visibly interrupt you mid-keystroke while
  filling in a "Close @ price" or "% profit" box (the value survived, but
  focus and cursor position didn't, so it looked like the field kept
  resetting). Focus and cursor position are now explicitly restored after
  every such refresh.
- **If a closed order's P&L still looks off**, open its **Info** panel and
  check the "P&L source" line -- it tells you exactly which method produced
  that number (an exact broker order/trade match vs. a rough live-tick
  estimate vs. genuinely unknown). If it says anything other than an exact
  match, that's the mechanism to look at first; the order's log also gets a
  diagnostic dump of the raw trades the app saw for that symbol at the time,
  which is the most useful thing to share if you need to report a mismatch.

### Centralized failure log

Every order's own `logs` array is great for "what happened to this trade,"
useless for "how often is X actually failing across everything." Every
operational failure -- an entry rejection, an exit order failing to place,
a trailing modify failing, a square-off failing -- now also gets appended
to one shared, append-only file: `data/failures.jsonl` (JSON Lines -- one
JSON object per line, so a single bad write can't corrupt the whole file
the way it could with one big JSON array). Each entry has a timestamp,
category, the order it happened to, the error message, and -- where
applicable -- the **full request and response** that were involved, so a
recurring problem (a specific instrument always rejecting trailing, say)
is diagnosable from the file alone. `GET /api/failures` returns the most
recent entries if you want to pull them programmatically; there's no
dedicated UI page for this yet (see the product proposals below).

### Design system

The visual layer runs on **Tailwind CSS**, loaded via its official
zero-build **Play CDN** (`https://cdn.tailwindcss.com`) -- no npm install,
no build step, JIT-compiles utility classes in the browser as the page
renders. Colors are Tailwind's own stock palette (slate/blue/green/red/
amber) used directly in markup, matching an explicit request to mirror
Tailwind's own theme rather than a customized one -- there's no palette
switcher and no custom color tokens anymore.

Every control -- buttons (filled/outlined/text/icon), inputs, chips, the
FAB -- uses `rounded-[0.3rem]` with `shadow-sm` for an elevated,
rectangular look, also by explicit request (not Material's usual pill/
circle shapes). Cards and dialogs, which are containers rather than
controls, keep a more generous `rounded-xl`.

**Dropdowns are `<ul>/<li>`, not native `<select>`/`<option>`** -- so they
can be styled properly. Built as a progressive enhancement
(`enhanceSelect()` in `app.js`): the real `<select>` stays in the DOM,
visually hidden, and remains the actual source of truth for its value --
every `.value` read, `FormData` collection, and `change` listener anywhere
in the app keeps working completely unchanged. Any `<select
class="js-enhance-select">` gets enhanced automatically on page load. One
subtlety worth knowing if you ever touch this: the app sets a select's
value programmatically in several places (reorder prefill, loading a saved
Strategy/Program for editing) via plain `someSelect.value = x` --
`enhanceSelect()` intercepts the actual `value` property setter (not just
a MutationObserver, which wouldn't reliably catch a property write) so the
visible dropdown stays in sync no matter which code path sets it.

Form inputs are plain native HTML elements with a `field-input`/
`field-label` class pairing, not a fancier animated-label component --
this keeps every bit of order-placement data collection exactly as
reliable as it's always been.

### App structure: Portfolio / Regular OMS / Advanced OMS

The nav is three things: the logo (always returns to **Portfolio**, the
landing view -- combined KPIs + a combined Calendar spanning both OMS
types), **Regular OMS** (Orders / Strategies / Calendar / Archive tabs,
plus a "+ New Order" FAB), and **Advanced OMS** (Programs / Risk Groups /
Archive / Calendar tabs). Not assumed to stay at exactly these two OMS
types forever -- nothing in the nav mechanism hardcodes that count.

New Order and Strategies both used to be their own standalone pages
(`/order`, `/strategy`); they're dialogs/tabs inside Regular OMS now. Old
bookmarks to either URL (including a `/order?reorder=<id>` deep link)
still work and redirect to the right place automatically.

### Login

A simple session-based login gates the whole app -- see the Migration
section at the top for the required `.env` setup. A few things worth
knowing:

- This is a login for **the app itself**, unrelated to your Tradejini
  credentials.
- **Login only gates viewing the app.** Trading (order reconciliation,
  trailing, Program cycles, safeguards) keeps running exactly as
  configured whether or not anyone is logged in -- verified by the fact
  that every background loop is a plain asyncio task, never an HTTP
  request, so none of it ever passes through the auth layer at all. Log
  out at the end of the day; nothing stops.
- Sessions are in-memory -- restarting the app means logging in again.
- If a session expires while you're using the app, the next API call
  redirects you to `/login` automatically rather than leaving the UI
  silently broken.

### Admin (the gear icon)

Its own standalone page (`/admin`), deliberately tucked out of the
top-level nav so failures don't demand attention they don't need --
infrequent, setup-like usage fits a real page better than a dialog would,
and it gives the Failures table room to actually breathe. Three tabs:

- **General**: the Advanced OMS accent color (6 options, applied
  immediately, persisted).
- **Portfolio Safeguards**: the outer, all-Programs-combined daily-loss
  ceiling (see "Safeguards" under Advanced OMS below) -- lives here rather
  than inside any one Risk Group's tab, since it's a genuine app-level
  setting spanning every Program, not something scoped to one Risk Group.
- **Failures**: the same centralized failure log described above, with a
  full UI -- filter by category, order ID, Program, OMS type (Regular/
  Advanced), and date range.

### Heartbeat (the "system" dot in the nav)

A green/yellow/orange/red health indicator, hover for detail. Checks
internet reachability (against a host unrelated to Tradejini specifically,
so "no internet at all" and "Tradejini's having an outage" show up as
distinguishable problems) and each configured broker's connection status
-- built generically (a list of entities, not "the Tradejini connection"
hardcoded) so a second broker or an exchange integration slots in later as
one more entry, but honestly, today, that list only ever has exactly one
thing in it. Also tracks the Program orchestration loop's own liveness --
if it ever went silently stuck, the dot turns red regardless of what
connectivity looks like, since a stuck loop you don't know about is
arguably more dangerous than one you do.

### Advanced OMS (Programs)

A **Program** is a different kind of entity from a Strategy: where a
Strategy is a passive SL/Target/trailing template you apply by hand to one
order at a time, a Program actively *drives* orders itself, continuously,
on its own schedule -- a separate top-level entity with its own page,
distinct from the Strategy/Order relationship everywhere else in this app.

**What it does, each cycle**: identifies the next expiry that's at least N
working days out, fetches the underlying's live spot price, derives the
ATM strike (the interval is read empirically from the actual option chain,
not hardcoded per index), and buys the ATM Call and the ATM Put --
"legs," each one a completely ordinary order underneath, using the exact
same entry/exit/trailing/reconciliation machinery as everything else in
this app, just placed automatically and tagged with which Program/cycle
they belong to. Once **both** legs are closed (deliberately: a Program
waits for both rather than racing to re-enter whichever side closes
first, on the theory that one side losing while the other wins is the
outcome being capitalized on, and rushing back into the losing side risks
compounding it), it repeats, subject to Scheduling below and however long
`inter_cycle_delay_seconds` says to wait first (0 = immediate re-entry).

**Sizing**: two modes, chosen per Program.
- *Lots* (the default, and what every Program from before this feature
  existed keeps behaving as on upgrade): a fixed, equal number of lots on
  both legs.
- *Capital*: both legs get the **same rupee allocation**
  (`capital_per_leg`) instead of the same contract count -- since CE/PE
  premiums differ, this means lot counts will differ between the two
  legs. Deliberate: equal capital gives predictable stop-loss risk on
  both sides regardless of which leg happens to be pricier that day,
  which matters more for this strategy's actual thesis (capturing the
  *difference* between a winning and a losing side) than strict
  delta-neutrality would. Both legs' live prices are fetched **before
  either order is placed** -- if either can't be priced, the whole cycle
  aborts rather than risking a half-placed straddle.

**Paper vs Live**: chosen per Program, and this is the whole reason the
order engine was built against a broker-agnostic interface from early on
-- a Paper Program runs through the exact same expiry/ATM selection,
safeguards, and order/exit/trailing/reconciliation machinery as a Live one,
just routed to a simulated broker that fills against real live prices
instead of placing real orders. You can run one Program live and several
others on paper simultaneously, each fully independent -- useful for
previewing how a Program would actually behave before trusting it with
capital. The simulation is deliberately simplified (immediate market
fills, limit/stop fills the moment price crosses the trigger, no partial
fills, no broker-side rejections -- paper mode assumes capital/margin is
always available) -- enough to validate orchestration logic, not a
market-microstructure engine.

**Margin pre-check with a buffer, and a strangle-widening retry** (Live
Programs only): before placing a cycle's two legs, checks both together
against Tradejini's own basket-margin endpoint -- but requires available
margin to cover `requiredMargin * MARGIN_SAFETY_BUFFER` (10% by default,
`program_manager.MARGIN_SAFETY_BUFFER`), not just the broker's own
zero-buffer shortfall figure, since "exactly enough" is too tight to
reliably enter a live cycle. If the plain ATM straddle doesn't clear that
buffered check, the cycle is NOT simply abandoned: it retries progressively
**widened into a strangle** -- CE at ATM+N strikes, PE at ATM-N strikes,
for N = 1..`STRANGLE_WIDEN_MAX_STEPS` (3 by default) -- since widening
symmetrically is what reliably lowers combined premium/margin (shifting a
*shared* straddle strike doesn't: one leg gets more ITM as the other gets
more OTM, so the net effect on combined cost is ambiguous). The first
candidate (ATM, then widened offsets in order) that passes is what
actually gets placed; a widened cycle's Starting-cycle log line says so
explicitly. If every candidate fails, nothing is placed, a persistent
**Alert** banner appears on the Program's card (distinct from a halt --
the Program keeps running and retries automatically next tick, same as
every other "can't start a cycle" precheck), and it's logged clearly. Only
a check that **succeeds** and reports insufficient margin blocks a
candidate -- a **failed check itself** (timeout or API error) does not,
same reasoning as before: an extra API call failing shouldn't become a new
reason an otherwise-valid trade doesn't happen. Deliberately tied to each
Program's own broker specifically, not a portfolio-wide check. Paper
Programs never widen and never margin-check at all (`PaperBrokerClient`
has no basket-margin endpoint -- paper mode deliberately assumes capital
is always available by design, see below).

**Scheduling** -- a genuinely different question from Safeguards below:
*when* (and on which days) a Program is even eligible to consider a new
cycle, independent of whether it's performing well. Configurable per
Program:
- `continuous`: if on, ignores everything else below -- always eligible
  (e.g. a Crypto Program meant to run 24 hours with no day-start gate at
  all).
- `start_time` / `end_time`: a daily window (e.g. start at 9:30 instead of
  9:15). A cycle already open when `end_time` passes keeps running its own
  SL/Target/trailing/time-exit normally into the close -- this only ever
  governs *starting* a new cycle.
- `days`: `"all"` or `"expiry_day"` -- e.g. a Program meant to run only on
  expiry day, in a narrow window (2:30-3:15), combines a tight `start_time`/
  `end_time` with `days: "expiry_day"`.

**Archiving**: orthogonal to a Program's own status (running/stopped/
halted) -- an archived Program never starts a new cycle, full stop,
regardless of what its status says, until explicitly unarchived. If it
happens to have a cycle open at the moment you archive it, that cycle
keeps running normally to close -- archiving only ever blocks *future*
cycles, same "never touch open positions" principle as everything else in
this app. Archived Programs live in their own tab, separate from the
active list.

**Advanced OMS's own tabs**: Programs, Risk Groups (see Safeguards below),
Archive, and its own Calendar -- mirroring Regular OMS's own tab structure
for consistency. Advanced OMS uses its own accent color throughout
(indigo by default, configurable in Admin -> General) so which "mode"
you're looking at is legible at a glance.

**Safeguards** -- three tiers now, each with the layered design and
reasoning this document already covers for trailing failures: throttles
that resolve themselves without a person, and hard stops that require one.

- *Throttles*: past a configured max cycles/day, every subsequent cycle
  that day waits a cooldown before starting -- a standing slow-down for
  the rest of the day, not a one-time pause.
- *Hard stop* (per-Program): N consecutive losing cycles, or today's
  realized P&L crossing a configured rupee floor. Halts that Program --
  no new cycle starts -- until a person reviews and resumes it from the
  Program's own card. **Resuming resets the consecutive-loss streak** (a
  human review is exactly the reset condition that streak is meant to
  wait for) but **never touches today's realized P&L** -- if the halt was
  for the daily cap, resuming clears the status but the Program correctly
  stays inactive for the rest of the day, since the underlying number that
  caused it hasn't changed.
- *Hard stop* (**Risk Group** -- a correlation-based grouping you define
  yourself, e.g. "Stock F&O", "Commodity Oil", "Commodity Gold", "Crypto"
  -- deliberately not a fixed textbook asset-class taxonomy, since two
  things in the same formal class, like Oil and Gold, don't necessarily
  move together): one daily-loss cap across every Program in that group --
  by default the sum of its members' own caps, or an explicit override.
  **Strict**: crossing it halts every running Program *in that group*,
  even ones still comfortably under their own individual cap. This is
  where the "correlated underlyings mean a bad day is usually a regime
  day" reasoning actually belongs -- scoped to Programs that are actually
  likely to be correlated with each other, not applied indiscriminately
  across everything you're running (an earlier, single-tier version of
  this safeguard made exactly that mistake: a Stock F&O Program having a
  bad day would have halted a perfectly healthy, unrelated Crypto Program
  too, for no defensible reason).
- *Hard stop* (**Portfolio**, global, **toggleable on/off**): one daily-
  loss cap across every Program combined regardless of group -- by default
  the sum of every Risk Group's own cap, or an explicit override. Also
  Strict. With Risk Group now doing the correlation-aware halting,
  Portfolio's own reasoning is simpler: it's just your own stated total
  daily risk ceiling, for its own sake, independent of what caused it --
  which is why it's optional. Turn it off if Risk Group alone covers what
  you need.
- A hard stop or a manual Stop **never** touches currently-open legs --
  those keep running their own protection untouched. Closing what's open
  immediately is the separate, explicit **Stop & Flatten** action.
- **Close Cycle** is a different, narrower manual action from Stop &
  Flatten: it closes just the active cycle's open leg(s) at market and
  lets the Program keep running otherwise -- for "I'm happy with this
  cycle's P&L, close it now" without stopping the Program the way Stop &
  Flatten does. Each leg's order record gets a distinct
  `close_reason: "program_cycle_manual_close"` for traceability. Once both
  legs actually resolve, the normal per-tick cycle wrap-up picks it up
  automatically -- P&L, safeguard counters, and cycle history are all
  updated exactly as they would be for an automatic SL/target/time-exit
  close; a manually-closed cycle's result counts toward the daily total
  and the consecutive-loss streak the same as any other cycle's does.
- **Closing a single leg**, narrower still: each active leg's own card has
  a "Close this leg" action (market only, deliberately -- this is a "get
  me out now" control, not a place for a limit-price negotiation). Purely
  exposes the same generic `request_close()` every other close in this app
  already goes through -- Program legs were simply never given a UI path
  to it before. The cycle as a whole wraps up normally once both legs
  resolve, same as any other close.
- **Conditional auto-close on repeated stop tests**
  (`stop_breach_force_close_count`, on both Strategies and Programs): for
  a leg that keeps testing its stop and recovering before ever confirming
  a close -- exactly the shape `exit_confirmation_windows` (see "Timeframe-
  aggregated trailing and exit checks" in Section 9) can prolong rather
  than prevent, since a bouncing price that keeps
  recovering just before confirmation never accumulates enough
  consecutive crossings to fire. Every time the stop is hit and then
  recovers without the leg actually closing, a persisted lifetime counter
  (`stop_breach_count` -- a real order field, like `trail_update_count`,
  not in-memory state that would reset on a restart) increments. Once
  that count reaches the configured limit, the *next* hit force-closes
  immediately, deliberately bypassing `exit_confirmation_windows` for
  that specific close -- the repeated testing itself becomes the signal
  that waiting for one more confirmation isn't buying anything. Stop side
  only; 0 (off) is the default.
- **A known, bounded timing edge case worth knowing about**: each tick
  recomputes Risk Group/Portfolio aggregates once, at the start of that
  tick, *before* processing that same tick's cycle closures -- so a cycle
  that closes with a big loss this tick won't be reflected in the group/
  portfolio aggregate until the *next* tick (`TICK_INTERVAL_SECONDS`,
  15s by default). The Program whose cycle just closed can never race
  itself on this (it never considers starting a new cycle in the same
  tick its own cycle closed in), but in principle a *different* Program in
  the same Risk Group, evaluated later in that same tick, could start one
  more cycle before the group's breach is caught -- self-correcting within
  one tick either way, and every other layer (that Program's own cap, the
  next tick's group check) still applies on top. Not considered worth the
  added complexity of a two-pass tick to close a multi-Program-same-tick
  window this narrow, but worth knowing the shape of it.

**Combined KPIs**: the Dashboard's top-level P&L cards combine Regular and
Advanced OMS together; Regular OMS and Advanced OMS each also show their
own scoped figures at the top of their respective tab. Each metric (Day
P&L, Overall P&L, days traded/profit/loss) is its own small card -- one
shared `kpiCardsHtml()` helper in `app.js` renders all three of these
(top-level, Regular OMS, Advanced OMS), so a layout change to it applies
everywhere at once. Advanced OMS legs never appear in the Regular OMS order
list (they'd mostly be noise -- many small re-entries through the day) but
always count toward the combined totals.

---

## 7. Project layout

```
backend/
  main.py               Starlette app, REST + websocket routes, startup wiring,
                          login/session enforcement, the Heartbeat check loop
  auth.py                 session-based login for the app itself (separate from
                          Tradejini credentials) -- middleware gates every route
                          by default, so a new route is protected without having
                          to remember to add a check to it
  order_manager.py       the order state machine -- offset -> price resolution at fill
                          time, exit-mode dispatch (Both/SL-only/Target-only/none),
                          also what every Advanced OMS leg (live or paper) is
                          placed/tracked through -- one instance per broker (live,
                          paper), same code path either way
  broker_interface.py     a Protocol order_manager/program_manager depend on instead
                          of TradejiniClient directly -- the seam that makes
                          PaperBrokerClient (below) a true drop-in
  tradejini_client.py     REST API wrapper (auth, orders, plain modify, cancel,
                          script master, margin checks)
  paper_broker.py          simulates fills against live prices for Paper-mode
                          Programs -- a real BrokerClient implementation, not a
                          mock; the exact same order/exit/trailing/reconciliation
                          code in order_manager.py runs against it unmodified
  script_master.py          fetches/caches Tradejini's live instrument-master feed;
                          expiry/ATM-strike/strike-interval lookups for Advanced OMS
  program_manager.py       the Advanced OMS orchestration engine -- the cycle
                          lifecycle (start/track/close), capital-spread sizing,
                          the margin pre-check, routing each Program to its live
                          or paper OrderManager based on its own `mode`
  program_safeguards.py     pure, isolated safeguard decision logic (throttles, hard
                          stops, Risk Group / Portfolio halts) -- kept dependency-free
                          specifically so it can be tested exhaustively
  program_schedule.py       pure scheduling logic (start/end window, continuous
                          flag, day-filter, inter-cycle delay) -- WHEN a Program is
                          eligible to trade, a different question from whether it
                          should stop (that's program_safeguards.py)
  entry_signals.py           pure gate logic (VIX ceiling/percentile, OI buildup,
                          session range, live-Greeks IV-session-rank) -- do
                          MARKET CONDITIONS right now favor entering, a different
                          question again from WHEN eligible or WHETHER to stop;
                          see "Entry Signal Gates" above
  clock.py                   single source of "now" for the whole backend -- always
                          resolves to Asia/Kolkata regardless of host timezone; every
                          `datetime.now()`/`date.today()`/`fromisoformat` call in this
                          codebase goes through here, see Section 6 and Section 10
  heartbeat.py              pure health-zone computation (green/yellow/orange/red)
                          + the internet-reachability check
  failure_log.py            centralized data/failures.jsonl -- see "Centralized
                          failure log" above
  factsheet.py               durable, immutable order/cycle snapshots (survive
                          Program/order deletion or editing) + the Trading Journal's
                          read-side aggregation -- see "Durable factsheets + Trading
                          Journal" above
  broker_reconcile.py        on-demand pass over already-closed LIVE orders, checking
                          this app's own records against the broker's -- distinct from
                          OrderManager.reconcile_loop (the always-on, currently-open-
                          positions loop); see "Broker reconciliation" above
  broker_interface.py        the BrokerClient Protocol both OrderManager and
                          ProgramManager depend on instead of TradejiniClient directly
                          -- the seam multi-broker execution will eventually use
  stream_manager.py         asyncio bridge around the vendored streaming SDK --
                          shared by both the live and paper OrderManager instances;
                          tracks each one's own subscription set separately so
                          neither wipes out the other's (see set_trailing_symbols)
  nxtradstream.py           vendored Tradejini streaming SDK (unmodified, except one
                          documented raw-string fix for a Python deprecation warning)
  store.py                  JSON read/write for orders (incl. archive), strategies,
                          Programs, Risk Groups, portfolio safeguards, app settings
  models.py                  StrategyConfig, CreateOrderRequest, ProgramConfig,
                          SafeguardsConfig, ScheduleConfig, RiskGroupConfig,
                          PortfolioSafeguards -- plain dataclasses, no compiled deps
  config.py                   reads .env, defines every data/ subfolder
frontend/
  login.html                       standalone login page (outside the auth-gated shell)
  admin.html                        standalone Admin page (accent color, Portfolio
                                  Safeguards, Failures browser) -- infrequent/setup-like
                                  usage, so a real page rather than a dialog
  index.html                        the whole app shell -- nav, Portfolio/Regular
                                  OMS/Advanced OMS sections, every dialog
  app.js                            shared helpers: the ul/li dropdown enhancer, the
                                  Info panel, shared P&L/KPI computation, the
                                  Heartbeat indicator, session-expiry handling
  dashboard.js / archive.js / calendar.js   Regular OMS's Orders/Archive/Calendar tabs
  order.js                          New Order, now a dialog (was order.html)
  strategies.js                     Strategy management, now a Regular OMS tab
                                  (was strategy.html)
  programs.js                       Advanced OMS: Program/Risk Group cards, create/
                                  edit dialogs, cycle drill-down, Paper/Live +
                                  capital sizing controls
  journal.js                        Trading Journal: combined Regular/Advanced OMS
                                  history read from factsheet.py's durable records --
                                  own top-level nav section, spans both OMS types the
                                  same way Portfolio already does
  admin.js                          Admin page logic: accent color, Portfolio
                                  Safeguards, Failures browser, Reconciliation tab
  tabs.js                           all tab/section-switching logic
  style.css                         custom CSS layer -- dialog show/hide, the ul/li
                                  dropdown, the Advanced OMS accent CSS variables
data/
  orders/               one JSON file per order -- trade history + resolved state,
                          including Advanced OMS legs (tagged with program_id/cycle_id),
                          both live and paper
  orders/archive/        archived orders -- still counts toward P&L/KPIs, still
                          visible in Calendar
  strategies/             one JSON file per saved Strategy template
  programs/                one JSON file per Program (config + runtime state + logs
                          + numbered cycle history + the archived flag)
  risk_groups/              one JSON file per Risk Group (name + optional cap override)
  factsheets/orders/          one JSON file per terminal order -- immutable snapshot +
                          amendments array, outlives the order/Program that created it
  factsheets/programs/<id>/   one JSON file per closed cycle, per Program -- same
                          immutable-snapshot-plus-amendments shape as orders above
  reconcile_reports/          one JSON file per broker_reconcile.py run (dry or
                          applied) -- the full per-order finding list, not just a summary
  signal_history/             one small JSON file per trading day -- the day's India
                          VIX close seen, plus each traded index's own price seen
                          (keyed by index_id) -- entry_signals.py's VIX-percentile
                          and squeeze (Bollinger Band Width) gates build real history from this over a
                          few weeks; NOT a historical-data pipeline, see above
  script_master/             cached index/option data from Tradejini's live feed,
                          refetched automatically when their version changes
  portfolio_safeguards.json  the portfolio-wide daily-loss cap + its on/off toggle
  app_settings.json          small app-level preferences (currently: Advanced OMS
                          accent color)
  market_holidays.json       optional -- exchange holidays for correct expiry
                          working-day counting (see Migration above)
  failures.jsonl            centralized operational failure log, see above
Dockerfile / docker-compose.yml / .dockerignore / Caddyfile
                            cloud deployment artifacts -- inert, not built/run as
                          part of producing them; see Section 10
```

---

## 8. Product proposals (not built -- confirm if you want any of these)

A few things stood out while working through this round of changes that
felt worth surfacing rather than building speculatively:

1. **Position-level view, not just order-level.** Right now each order
   card is exactly one entry + its exit. If you routinely scale into the
   same symbol across multiple orders, a "position" rollup (net qty,
   blended entry, combined P&L across the orders that make it up) sitting
   above the individual order cards could be more useful than reading
   several cards and adding them up yourself.
2. **Strategy-level default exit mode.** Exit legs (both/SL-only/etc.) are
   currently chosen fresh on every order. If you find yourself always
   picking the same exit mode for a given strategy, it could default from
   the strategy (still overridable per-order) rather than always starting
   at "Both."
3. **A real card redesign**, not just the spacing/overlap fixes in this
   round. The order card is doing a lot -- status, prices, P&L, buy/sell
   amounts, close controls, logs -- and a tabbed or collapsible layout
   (e.g. "Overview" / "Manage" / "Logs" as separate views within the card)
   could reduce how much is on screen at once for a card you're not
   actively acting on, while keeping everything reachable.

None of these are built. Say the word on any of them and they're next.

## 9. Phase 3/4 roadmap (deliberately not built yet -- design notes only)

These are larger, deliberately deferred items, written up in enough
design detail to hand to a fresh Claude session (e.g. after moving this
project to a Claude Project with multiple focused chats) and ask for
directly, without needing to re-derive the reasoning from scratch.

### Timeframe-aggregated trailing and exit checks -- BUILT

Originally written up here as a Phase 3/4 proposal; now implemented, with
two refinements beyond what was originally sketched below (kept for
context on how the design evolved):

- **Aggregation value: the window's MEDIAN tick, not its closing tick.** A
  closing-price approach still lets a single spike sitting right at the
  end of a window fully decide that window's outcome -- it only reduces
  how *often* a bad tick can matter, not how much any one bad tick can
  distort the decision. The median of every tick seen during the window
  actually dilutes a one-tick outlier. VWAP wasn't buildable -- the live
  tick payload doesn't carry volume.
- **`exit_confirmation_windows` (Program-only)**: a crossed stop/target
  trigger must stay crossed for this many CONSECUTIVE evaluations (one
  evaluation = one raw tick if aggregation is off, or one window close
  otherwise) before the close actually fires -- 1 (default) fires on the
  first crossing, same as before this feature existed. A recovery
  (an evaluation that DOESN'T cross) resets the streak to zero. Not
  offered on Regular OMS Strategies, only on Programs.

Otherwise as designed: `trail_check_interval_seconds` (0 = off, react to
every raw tick; presets 5s/10s/30s/1min/3min/5min plus a custom-seconds
input) exists on both `StrategyConfig` and `ProgramConfig`, feeding both
trailing recalculation and the exit-trigger check from one shared
setting. Live P&L / `last_ltp` display still updates on every raw tick
regardless -- only the trailing/exit DECISION is gated. Every order also
carries `last_ltt`, the EXCHANGE's own last-traded-time for its most
recent tick (always IST, decoded via `backend/clock.py`'s timezone) --
kept purely for diagnosis (is this tick fresh, has our clock drifted from
the exchange's), never compared for equality or used to drive any
decision. One placement
deviation from the original sketch below: `trail_check_interval_seconds`
lives as a **top-level `ProgramConfig` field**, not on `ScheduleConfig`
-- `ScheduleConfig`'s own docstring scopes it strictly to cycle-*start*
eligibility, a different concern from exit-check cadence, which sits
closer in kind to `stop`/`target`/`time_exit`. Per-order window/
confirmation-streak state is in-memory only (`OrderManager._trail_state`),
never persisted, mirroring `_latest_prices` -- losing it mid-window on a
restart just means the next window starts fresh, which is harmless.

Original design sketch, for context:

**The problem**: every single live tick that arrives for a symbol
immediately ran trailing recalculation and the stop/target trigger check
(see `handle_l1_tick` in `order_manager.py`). In a fast, choppy market
this was noisy -- the stop/target could trail on every small wiggle, and
the exit check fired the instant price crossed a trigger even if that
cross was a single noisy tick rather than a sustained move.

### Market vs. Limit order for the close (configurable per Program)

**Directly related to timeframe-aggregated trailing above**: once a
close decision is based on an aggregated value over a window rather than
the instant a raw tick crosses a trigger, firing an unconditional market
order at that moment is a slightly different tradeoff than it is today --
a limit order at (or near) the trigger price becomes a more natural fit,
since the aggregation already smooths out the single-tick noise a market
order's "close at any price right now" is mainly guarding against.

**The proposed fix**: a per-Program (and per-Strategy) configurable
choice for the close order type -- Market (today's only option) or Limit
(at the trigger price, or trigger plus/minus a configurable buffer).
Configured on the Program/Strategy create-edit dialog, alongside the
timeframe setting above.

**Design notes for whoever builds this**:
- A limit order isn't guaranteed to fill at all (price could move past it
  before it's matched) -- this needs its own timeout/fallback story (e.g.
  "if not filled within N seconds, cancel and re-fire as a market order"),
  which can mostly reuse the stuck-square-off timeout mechanism already
  built for the market-order path (`SQUARE_OFF_STUCK_TIMEOUT_SECONDS` in
  `order_manager.py`) rather than needing something new from scratch.
- Worth deciding whether the limit price should be exactly the trigger
  price, or the trigger plus/minus a small buffer in the favorable
  direction (to increase fill likelihood at a small cost to price) --
  probably worth making that buffer configurable too, defaulting to 0.

### Broker reconciliation -- BUILT

Originally written up here as a Phase 3/4 proposal; now implemented as
`backend/broker_reconcile.py`, an on-demand pass (Admin page ->
Reconciliation tab, or `POST /api/reconcile`) over already-CLOSED, LIVE
orders only -- never a currently-open position, which is what the
always-on `OrderManager.reconcile_loop()` already handles continuously
(that loop and this module are deliberately two different things with
two different names, precisely to avoid the confusion "reconcile" would
otherwise cause in this codebase).

**What actually shipped, and where it deviates from the original sketch
below**:
- **`dry_run` defaults to `True`.** The first call always previews (full
  report, corrects nothing); an explicit second call with
  `dry_run: false` actually writes. This is what makes "never a silent
  correction" real on live-money records, not just a docstring promise.
- **Comparison spine is `get_orders()`**, not `get_trades()` -- the only
  broker response shape this codebase has real production evidence for
  (the exact fields the live reconcile loop already trusts daily:
  `orderId`/`status`/`fillQty`/`avgPrice`). `get_trades()` is consumed
  defensively, as enrichment/orphan-detection only, never assumed to have
  a specific shape. `get_positions()` is out of scope entirely -- it's
  netted by symbol, with no `orderId` to join against a local order.
- **Compares BOTH broker-side legs of a local order independently** --
  the entry order and (once closed) the square-off/exit order each have
  their own `broker_order_id`. Comparing only the entry leg would never
  actually check the close side, which is exactly where a wrong exit
  price would show up.
- **`pnl.realized` is never copied from the broker -- it's recomputed
  locally**, via the exact same `_finalize_realized_pnl` every other
  close in this app already uses, from the (possibly corrected) fill
  data. A broker P&L figure is almost certainly net-of-brokerage/STT/GST
  while this app's own figure is gross; comparing them directly would
  disagree on nearly every order for reasons that have nothing to do
  with a real discrepancy.
- **`daily_realized_pnl` and every other safeguard counter are
  report-only, never auto-corrected** -- silently rewriting a counter
  that already drove a real halt/resume decision this session, or
  double-counting it on a re-run, is a materially worse failure mode
  than the drift it would be fixing.
- **Corrections are convergent** (`local := broker's value`), never
  incremental -- what makes re-running safe: a second run against
  unchanged broker state finds zero remaining diffs and writes nothing.
- **Every correction is recorded as an amendment** on the affected
  order's Journal factsheet (and, for a Program leg, the owning cycle's
  factsheet too) -- the original snapshot stays untouched; the amendment
  is the visible, auditable record of what changed and why. See "Durable
  factsheets + Trading Journal" below.
- **A day-scoping check** (`VERDICT_UNVERIFIABLE`) treats any local order
  from a previous session as unverifiable rather than a discrepancy --
  the broker's own order book is day-scoped, so an old order legitimately
  having no current broker record isn't drift.
- **`find_stale_open_orders`** is a new, report-only check beyond the
  original sketch: a LIVE order still non-terminal locally whose
  instrument's expiry has already passed is flagged loudly (possible
  invisible NSE options expiry auto-settlement/exercise -- the one class
  of drift `get_orders()`/`get_trades()` may not reliably surface at
  all). Never corrects anything automatically.
- Correcting an order that's already been archived writes back to the
  archive path (`store.save_order_in_place`), never `data/orders/`
  directly -- doing otherwise would resurrect a duplicate copy in the
  active folder, the exact bug class AGENTS.md documents for a different
  reason.

Original design sketch, for context:

**The problem**: this app's own JSON files were the only source of truth
it ever checked against itself -- there was no periodic "does what I
think happened actually match what the broker's own records say
happened" pass. If anything in this app's own state ever drifted from
reality (a missed tick, an edge case not yet found), there was no
self-healing check that would catch and correct it after the fact.

### Durable factsheets + Trading Journal -- BUILT

**The problem**: deleting a Program (or editing one mid-flight) used to
lose its history. `program["cycles"]` -- the rollup tying a Program's
cycles together (P&L, timestamps, which orders belong to which cycle) --
lives inside the Program's own JSON file and is capped at 500 entries; it
dies with the Program the moment it's deleted, even though the
underlying order files themselves already survived deletion (orders live
in their own `data/orders/` files, tagged with `program_id`/`cycle_id`,
never touched by `delete_program`). And even the orders that DID survive
never recorded the Program-level config (safeguards, schedule, sizing) or
the capital-check widening outcome as they stood at the moment a specific
cycle actually ran -- editing a Program's stop-loss % between cycle 4 and
cycle 5 left no record of which value was actually in effect for either.

**What was built**: `backend/factsheet.py` writes an immutable,
independently-durable snapshot exactly once, at the moment an outcome is
known -- for every order that reaches a terminal status (Regular OMS or
an Advanced OMS leg, live or paper) and for every Program cycle that
closes (regardless of why: SL/target/time-exit, Stop & Flatten, or the
manual Close Cycle action) -- stored entirely outside `data/programs/`
and `data/orders/`, at `data/factsheets/orders/<order_id>.json` and
`data/factsheets/programs/<program_id>/<cycle_id>.json`.

- **A cycle factsheet embeds the config as it stood at cycle START**, not
  whatever the Program's config happens to be by the time the cycle
  closes -- captured in a new `program["active_cycle_snapshot"]` the
  instant `_start_new_cycle` sets `active_cycle_id` (the only point that
  knows both the config-in-effect and the capital-check widening outcome;
  `update_program` has no active-cycle guard, so config genuinely can
  change mid-cycle), consumed and cleared in `_close_cycle`. Also records
  the widening outcome (whether/how far the strangle-widening retry --
  see the Margin pre-check section above -- kicked in for that specific
  cycle) and the spot/expiry/strikes actually selected.
- **A Program leg's order gets BOTH its own order factsheet AND a full
  embedded copy inside its cycle's factsheet**, deliberately duplicated
  -- the order factsheet fires at that leg's own terminal transition,
  which can be minutes before the cycle closes, and is the only surviving
  record if the leg is rejected at placement (the cycle never fully
  forms) or the Program is deleted in between.
- **Immutability is structural, not a convention to remember**: the only
  write path into an already-existing factsheet is `append_amendment`,
  and it only ever appends to an `amendments: []` array -- it never
  touches the original snapshot fields. `apply_amendments()` is a pure,
  read-time projection of "the snapshot with every amendment folded in";
  the file on disk is never rewritten by it. This is what
  broker_reconcile.py's corrections write into, so a corrected record and
  its original, as-it-happened snapshot are never conflated into one
  mutable blob.
- **`strategy_snapshot`** (a new field on every order, both Regular OMS
  and Program legs) captures the resolved strategy/leg config exactly as
  used for that specific order -- the same problem as the cycle-config
  snapshot, one level down: a Strategy can be renamed/edited/deleted
  after the fact too.
- **Paper Programs and orders get factsheets too** -- losing a paper
  Program's history on deletion is the same problem this feature exists
  to solve, just without real money involved. Broker reconciliation
  (above) is the one piece that stays strictly live-only, since paper
  never talks to a real broker.
- **The Trading Journal** (new top-level nav section, spanning both OMS
  types the same way Portfolio already does) is the read side: a
  chronological list of closed cycles and standalone Regular OMS orders
  -- a Program leg's own factsheet exists but isn't separately listed,
  since it's already embedded in its cycle's entry -- each opening into a
  detail view showing P&L, legs, and any reconciliation corrections
  (`GET /api/journal`, `/api/journal/cycle/{program_id}/{cycle_id}`,
  `/api/journal/order/{order_id}`, the latter two returning the
  amendments-applied "current best-known truth" view alongside the raw
  amendment trail for transparency).
- Zero migration was needed for any of this -- the orders directory had
  no existing data at the time this was built, and per this file's own
  "no backward-compatibility obligation on stored data" stance, old
  Program/order files simply never get a factsheet retroactively.

### Entry Signal Gates -- BUILT

**The problem**: `_start_new_cycle` was completely blind to market
conditions -- fetch spot, pick the nearest expiry, pick the ATM strike,
buy CE + PE (a long straddle), on a fixed schedule, every time. For a
strategy that profits from a big move and bleeds to time decay when the
market sits still, that's a real gap: nothing stopped a cycle from
entering right after volatility (and premium) was already expensive, or
right after the day's move had already happened.

**What was built**: `backend/entry_signals.py`, a pure decision module
(mirroring `program_schedule.py`/`program_safeguards.py`'s own shape --
no I/O, every gate a function returning `(allowed, reason)`) checked once
in `_start_new_cycle`, right after the ATM pair is resolved and before
the widening/margin loop runs. Entirely optional -- `ProgramConfig.
entry_signals.enabled` defaults to `False`, and every threshold
underneath that is itself independently optional, so nothing changes for
a Program that doesn't opt in.

- **Five gates, each usable on its own**: an India VIX ceiling; an Open
  Interest buildup check (`OIChngPer`, already decoded by the streaming
  SDK and previously discarded); a same-session range check
  (`(high-low)/open` on the underlying, an approximation of "the move
  already happened" -- not a real multi-day compression/squeeze
  detector, which would need a price history this app doesn't keep); a
  VIX-percentile gate against the app's own accumulated history (see
  below); and a live-Greeks IV-session-rank gate (today's IV relative to
  its own `[lowiv, highiv]` range so far -- needs zero stored history at
  all, since both bounds ship on the same tick as the current value).
- **Data plumbing, not a new pipeline**: the streaming SDK
  (`nxtradstream.py`) already decodes Open Interest, session O/H/L, VWAP,
  and India VIX on the exact same L1 subscription already in use --
  `order_manager.py` just discarded everything except `ltp`.
  `fetch_live_price` was refactored (behavior unchanged for every
  existing caller) to share its subscribe-and-wait logic with a new
  `fetch_market_snapshot`, which returns the full tick dict instead of
  just the price. Live Greeks/IV needed one genuinely new piece --
  `subscribeGreeksSnapShot`, present but unused in the vendored SDK --
  wired through as a fully separate one-shot mechanism
  (`fetch_greeks_snapshot`), never touching the proven price-fetch path.
- **Entitlement is unverified, and the code says so.** Whether this
  account can actually receive the Greeks channel isn't provable from
  this repo. `fetch_greeks_snapshot` returns `None` on timeout -- which
  **is** the live answer, discovered naturally the first time a Program
  with the IV-rank gate enabled actually runs, rather than needing a
  separate diagnostic step. `on_greeks_unverifiable` (`"allow"` default,
  or `"skip"`) decides what happens in that case; `"allow"` mirrors an
  existing, deliberate precedent in this exact file -- a margin check
  that raises also returns `True` and lets the trade through
  (`_check_buffered_margin`). Logged once per Program per day (in-memory
  only, like `_trail_state`), not spammed every 15s tick.
- **A small daily VIX snapshot, not a historical-data pipeline.** One
  scalar (`vix_close_seen`) written at most once per calendar day, to
  `data/signal_history/<date>.json`, the first time any Program's gate
  check runs that day -- reuses data already being fetched for the VIX
  ceiling gate, no new subscription or loop. Over a few weeks this builds
  real IV-percentile context (`max_vix_percentile`); before
  `vix_percentile_min_days` of history exist, that specific gate always
  allows.
- **A rejected entry is non-halting**, matching the existing
  capital-shortfall pattern exactly: `_set_program_alert` (visible,
  persistent, cleared automatically once a cycle actually starts),
  `failure_log.log_failure(category="program_entry_signal_blocked", ...)`,
  and the Program just retries next tick as conditions change.
- **Fetches run concurrently with a short (3s) timeout**, deliberately
  separate from the 8s timeout on the essential spot-price fetch --
  `_tick_one` calls are sequential across every Program, so a slow or
  closed feed on one Program's optional signal check must not stall
  every other Program's tick behind it.
- **Deliberately not built in this round**: delta-targeted strike
  selection and theta-aware dynamic exit timing are real ideas that came
  out of the same research (Greeks give real per-leg delta/theta), but
  both would mean editing the widening loop or the exit engine directly
  -- both safety-critical, both already carrying a lot of recently-added
  logic. This round is gates only: whether/when a cycle starts, never
  which strikes get chosen or when an open cycle exits. Also not built:
  L5 depth/order-book imbalance and streaming OHLC candles (still no
  concrete use case identified for this strategy) -- see the squeeze
  detector below for why a real multi-day squeeze signal didn't end up
  needing either of those anyway. Regular OMS support (Advanced OMS
  Programs only, for now).

### Squeeze detector (Bollinger Band Width) -- BUILT

A sixth entry gate, added in the same round as the mark-to-market cap
below once delta/theta refinements were judged low-priority for an
account that needs to demonstrate results before optimizing basis points.
Replaces `session_range_gate`'s same-session approximation ("hasn't moved
much *today*") with the actual textbook long-volatility timing signal:
has price been compressed *over days* relative to its own recent history.

The original deferral above assumed this needed genuinely new persistence
(streaming OHLC candles). On closer look it didn't -- Bollinger Band Width
needs only a **daily closing price**, and `data/signal_history/<date>.json`
(built for the VIX-percentile gate) already existed. Extended to
`{"date": ..., "vix_close_seen": ..., "index_closes": {"<index_id>": price}}`
-- keyed by index so multiple underlyings share one file per day, same as
VIX. `entry_signals.squeeze_gate` (pure, mirrors `vix_percentile_gate`'s
shape exactly): computes today's Bollinger Band Width off the last
`squeeze_bollinger_period` days (default 20), ranks it against every
PRIOR day's own reading over `squeeze_min_days` more days (default 10) --
allows unless today's reading is unusually *wide* (not compressed)
relative to that history. Needs roughly `period + min_days` trading days
(~6 weeks at defaults) before it activates, same "degrades to allow,
logged as such, until enough history exists" rule as VIX-percentile.
Captured value is "first price observed the day the gate first ran," not
a true end-of-day close -- same approximation `vix_close_seen` already
makes, acceptable because this is a rolling signal over weeks where
day-to-day capture-moment noise washes out.

### Mark-to-market cap -- BUILT

Confirmed, not assumed: `_tick_one` skipped every safeguard check
entirely while a cycle was active (`program_manager.py`'s active-cycle
branch just checked whether both legs were terminal, then returned) -- an
open cycle bleeding unrealized loss was invisible to its own Program's
daily cap, its Risk Group's cap, and the portfolio cap, until it closed
on its own.

- **Opt-in per Program** -- `SafeguardsConfig.mtm_aware`, default `False`.
  A Program that turns it on has `daily_realized_pnl` PLUS its currently-
  open cycle's live P&L (`program_safeguards.mtm_cycle_pnl`: realized for
  any leg already closed, live unrealized for any leg still open) checked
  against its own `daily_loss_amount` on **every tick**, not just at
  cycle-close -- and also contributes that live number to its Risk
  Group's and the portfolio's aggregate. A Program that hasn't opted in
  stays invisible in MTM terms at every tier, exactly like before this
  round.
- **`program_safeguards.py` itself needed almost no change** --
  `apply_group_halt_if_needed`/`apply_portfolio_halt_if_needed` already
  just take a P&L float; `program_manager.tick()` simply computes a
  richer number (`mtm_pnl_map`) to pass in. The one genuinely new check
  is inside `_tick_one`'s active-cycle branch, evaluating the Program's
  *own* cap while a cycle is still open -- something no code path did at
  all before this.
- **Halt only, never auto-flatten.** Matches the existing, deliberate
  invariant that a hard stop never touches currently-open legs -- this
  cap stops the *next* cycle from starting; the open one keeps running
  its own SL/target/trailing exactly as configured. Reuses the existing
  `HALTED_DAILY_LOSS` status (a timing difference in *when* the same cap
  was detected, not a different kind of stop) so no new status/label was
  needed anywhere in the UI.
- **Live MTM total shown on the Program card** (only when `mtm_aware` is
  on and a cycle is active) -- a transient, non-persisted-every-tick
  value (`program["mtm_pnl"]`, same precedent as `order["last_ltp"]`),
  refreshed by the existing periodic runtime poll.

### Programs decoupled from Index -- any tradeable instrument, not just an Index

**The problem**: a Program today is tightly coupled to trading an Index's
ATM straddle specifically (`ProgramConfig.index_id`, the whole
expiry/ATM-selection flow in `program_manager.py`'s `_start_new_cycle`
assumes an index + its option chain). There's no way to build a Program
around, say, a single equity script, a futures contract, or any other
instrument shape.

**Design notes for whoever builds this**: this is a genuinely bigger
change than it might look, since `_start_new_cycle`'s whole shape (spot
price -> derive ATM strike -> buy CE + PE) is specific to an
index-options straddle -- it isn't just a matter of swapping which
`symId` gets used. Two real approaches worth weighing against each other
before starting:
1. Keep Program's cycle logic index-options-specific (as today), and add
   a genuinely SEPARATE, simpler Program type for a single-instrument
   strategy (buy/sell one script on a schedule, no ATM/expiry logic at
   all) -- less reuse, but a much smaller, safer change.
2. Generalize `_start_new_cycle` into a pluggable "leg selection"
   strategy (index-ATM-straddle is one implementation; a single-script
   entry is another, simpler one) -- more reuse and more architecturally
   "correct," but touches the core orchestration logic that safeguards,
   scheduling, and margin-checking all currently assume a 2-leg cycle
   shape, so this needs real care to avoid regressing anything already
   working.
Given how safety-critical this file already is, option 1 is very likely
the safer starting point even though it duplicates some logic, unless
there's a clear near-term need for more than a couple of different
Program shapes.

### Multi-broker execution: the Super Program design (schema groundwork only, not built)

**The problem**: `broker_interface.py`'s `BrokerClient` Protocol already
names multi-broker execution as its stated reason for existing --
spreading capital across broker accounts to avoid entry limits or strike
saturation on any single one -- but today there is exactly one broker
(`TradejiniClient`, instantiated once, at module level, in `main.py`) and
nothing in `Order`/`ProgramConfig` identifies which broker anything
actually traded through.

**Schema groundwork that HAS shipped**, so a second broker can be added
later without a schema migration: `broker_id` on `BrokerClient` (a plain
attribute -- `TradejiniClient.broker_id = "tradejini"`; `PaperBrokerClient`
deliberately has none, read via `getattr(client, "broker_id", None)`
everywhere so paper is `None` without a special case), on every `Order`
(stamped at creation from the client that placed it), and on
`ProgramConfig` (which broker that Program trades through when
`mode == "live"`) -- plus a reserved, currently-always-`None`
`super_program_id` field on `ProgramConfig`. **Building an actual second
broker connection is explicitly NOT part of this** -- a real second
`BrokerClient` implementation, its own `StreamManager`/credentials, and
routing a Program to the right one are all separate, sizable future work.

**The confirmed design for when that future work happens**: a **Super
Program** -- a parent entity holding one shared strategy template
(index, stop/target, safeguards/schedule templates, sizing) plus a list
of broker allocations. Creating or editing it auto-materializes one
fully ORDINARY child `Program` per broker allocation -- each with its own
real `program_id`, running the existing, completely unmodified cycle
lifecycle. Deliberately NOT "teach one Program to run multiple
simultaneous cycles internally" -- that would touch the safety-critical
`_tick_one`/`_start_new_cycle` invariant that a Program has exactly one
`active_cycle_id`, for comparatively little benefit over just reusing the
machinery that already exists and is already trusted.

**Three gaps identified while designing this, worth knowing before
building it** (found by reusing the existing Risk Group mechanism to get
family-wide "the whole broker-spread strategy halts together on a bad
day" for free, rather than inventing a fourth aggregation tier):

1. `ProgramConfig.risk_group_id` is **singular** -- auto-assigning every
   child of a Super Program into a dedicated Risk Group consumes that
   child's only grouping slot. Wanting a child in *both* its Super
   Program's group and some other correlation group needs a real design
   decision (multi-membership, or accept the constraint as-is).
2. Risk Group halting only aggregates the **daily loss cap**
   (`program_safeguards.apply_group_halt_if_needed` tests nothing else).
   Reusing it does **not** get you family-wide `consecutive_loss_limit`
   or `max_cycles_per_day` for free -- those stay per-child, so an
   N-broker split family-wide tolerates roughly N times the template's
   stated streak/cycle limits unless the materializer explicitly decides
   whether to divide or replicate each one. And the group's own daily-loss
   cap defaults to the **sum** of members' caps -- the materializer must
   set an explicit override to the intended family-wide cap, or a
   template of ₹5,000 silently becomes a much larger family exposure
   across N children. **This is the one that loses real money if missed.**
3. Deletion/resume ordering: `delete_risk_group` refuses while members
   exist, so deleting a Super Program must delete its children first,
   then the group; `resume_program` is per-Program, so a halted family
   needs N manual resumes, not one.

### More Feature Requests
Review and work on MD files under "Additional Feature Requests root" folder.
Each file moves to "done" inside "Additional Feature Requests root" folder once they are implemented

## 10. Cloud deployment (artifacts ready, move not scheduled)

This app currently runs locally. A cloud move isn't scheduled, but the
artifacts and this runbook exist so the move is an afternoon of work
whenever it happens, not a project of its own. `Dockerfile`,
`docker-compose.yml`, `.dockerignore`, and `Caddyfile` are all present in
the repo root and are **inert** -- written but not built or run as part of
producing them. Building and smoke-testing the image is the first real
step of the migration itself.

### Regulatory constraint that shapes the architecture

SEBI's retail algo trading framework (fully mandatory since 1 April 2026)
requires a **dedicated static IP registered with your broker** for API
order placement, with brokers rejecting calls from non-whitelisted IPs and
only one active API key per registered IP. Two consequences:

- **Serverless is ruled out entirely** -- Cloud Run, Lambda, and anything
  autoscaling has a dynamic egress IP. A plain VM with a reserved static IP
  is the only shape that fits. (Independently, see below: this app cannot
  run as more than one process anyway.)
- Confirm with Tradejini what their actual IP-registration process is
  before provisioning anything -- their implementation specifics aren't
  assumed here.

### Provider

Current plan, when the move happens: **GCP on the $300 signup credit**,
fully containerized so the provider decision is cheap to revisit once the
credit runs out (~90 days from signup, regardless of spend). GCP has no
Mumbai (`asia-south1`) always-free tier -- the free `e2-micro` is US-only
and unusably far from the broker. Steady-state after the credit is roughly
$18-20/mo on an `e2-small`; a Mumbai-region Vultr/Linode/Lightsail box runs
roughly $5-6/mo flat, indefinitely. Re-decide at day ~75, before the credit
runs out.

**On instance size**: don't over-provision. This is a single-process,
I/O-bound asyncio app -- one event loop, no database, ~3.7MB of on-disk
state, no parallel work to exploit (more vCPUs can't be used by one event
loop). `e2-small`'s 2 vCPU / 2GB is already comfortable headroom;
`e2-micro` would genuinely run it. The $300 credit's real constraint is
the 90-day clock, not the dollar amount -- spending it on a bigger trading
VM just makes the bill bigger at day 91. If the credit should do real
work, spend it on something separate and bounded (a backtesting or data
experiment), not on idle headroom here.

### Hard constraints

1. **Exactly one process, ever.** No `--workers`, no replicas, no
   overlapping rolling deploy. Two `ProgramManager.tick_loop()`s would each
   independently decide to start cycles -- duplicate real orders. There is
   no cross-process lock anywhere in this codebase; every lock is an
   in-process `asyncio.Lock`.
2. **`data/` must be a persistent, writable volume**, writable at every
   subdirectory (atomic writes create their `.tmp` file next to the
   target). It is the sole copy of all trading history and there is no
   backup mechanism in the app itself -- the current accidental protection
   from OneDrive sync on the dev machine will not exist on a VM. Back it up.
3. **Health checks must target `/login`**, not `/api/status` -- everything
   except `/login`, `/api/auth/login`, and `/static/*` is auth-gated and
   would return a 302/401 to a probe.
4. **Egress allowlist**: `api.tradejini.com` (443, REST + the websocket
   feed) and `www.google.com` (the heartbeat's 60s reachability probe --
   deliberately not a Tradejini host, to distinguish "no internet" from
   "broker down"; blocking it makes the heartbeat report red forever).
5. **TLS and login throttling are prerequisites of exposure, not
   nice-to-haves.** The app currently serves plain HTTP with a single
   plaintext password and no login rate limiting -- fine on localhost,
   not fine on a public IP guarding a live-money account. Set
   `secure=True` on the session cookie (`main.py`) once TLS is in front
   (the `Caddyfile` here handles the TLS side automatically).
6. `APP_PORT` in `config.py` is **dead config** -- read nowhere. The port
   comes only from the uvicorn CLI flag / the Dockerfile's `CMD`.

### Timezone

Handled in code, not by relying on host configuration -- see
`backend/clock.py` and the "Known simplifications" section above. The
`Dockerfile` also sets `TZ=Asia/Kolkata` belt-and-braces, but the app no
longer actually depends on it.

### First deploy, when it happens

1. Provision the VM in Mumbai (`asia-south1` for GCP), register its static
   IP with Tradejini.
2. `git clone`, copy `.env.example` to `.env` and fill in real credentials
   (never commit `.env`).
3. `docker compose up -d --build`.
4. Point the `Caddyfile`'s placeholder domain at the VM's IP, run Caddy (or
   fold it into `docker-compose.yml` as a second service) for TLS.
5. Confirm `/login` loads over HTTPS, log in, confirm the heartbeat dot
   goes green (internet + broker both reachable) and a Program card shows
   live data.
6. Set up `data/` backups (the compose file uses a named volume -- back up
   the volume, e.g. via periodic `docker run --rm -v trading_data:/data ...`
   tar to off-VM storage).
7. Calendar reminder before day ~90 to re-decide the provider (see above).
