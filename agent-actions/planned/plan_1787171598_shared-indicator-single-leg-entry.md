# Shared-indicator manual single-leg Program entry

## Objective

Add an opt-in Program mode where an operator selects one CE or PE leg from a
Program card. Add shared, underlying-index indicators for decision support and
persist selection telemetry for Paper and Live results analysis. Existing
Programs must remain automatic CE+PE pair Programs unless explicitly edited.

## Verified current behaviour

- A Program is persisted as `config`, `runtime`, cycle history and logs.
- Its normal lifecycle automatically starts a CE+PE pair, sizes both legs,
  performs a Live basket-margin check, and considers a cycle complete only
  after both legs are terminal.
- The stream currently accepts owner-scoped L1 subscription sets. It can union
  subscriptions across live and Paper order managers; this is the extension
  point for a shared index-indicator owner.
- L1 data provides ticks, not historical OHLC. Existing price fetches are
  temporary subscriptions. RSI/EMA therefore require locally aggregated bars.
- Paper uses the same Program and OrderManager lifecycle but deliberately has
  no broker-margin engine. Live and Paper factsheets are written through the
  same cycle-factsheet path.

## Approved design

### Program configuration and compatibility

- Add `entry_mode` to `ProgramConfig`, valid values `auto_pair` and
  `manual_single_leg`; absent legacy data defaults to `auto_pair`.
- `auto_pair` retains the current CE+PE lifecycle, including existing capital
  sizing and Live basket-margin/widened-strangle logic.
- `manual_single_leg` never starts a cycle automatically. When it is schedule-
  and safeguard-eligible, its card exposes non-modal CE and PE entry CTAs.
- The CTA API is `POST /api/programs/{program_id}/start-leg` with body
  `{ "leg": "CE" | "PE" }`. Invalid legs, archived/stopped/halted Programs,
  or active cycles return an error and never place an order.

### Shared indicators

- Add one service that maps `index_id` to the index stream symbol, one current
  minute candle, persisted completed one-minute candles, and one derived
  snapshot. It must subscribe each underlying exactly once even when several
  Programs reference it.
- Subscribe unarchived Program underlyings through StreamManager owner
  `indicators`; add/remove symbols when Programs are created, updated,
  archived, deleted, or script-master availability changes. Do not overwrite
  Live/Paper order subscriptions.
- Fetch intraday historical 1-minute OHLC data for the current trading day using
  the broker's chart data API (`/getIntervalChartData`) upon initialization or when missing data.
  Continue to bucket incoming live L1 ticks into ongoing 1-minute bars to append
  to the historical base. This avoids the 50-minute warm-up and implicitly
  handles daily rollovers by starting fresh with the new day's history.
  Atomically persist the merged bars under `data/indicator_bars/` to minimize API calls on restart.
- Calculate RSI(14), EMA(20), EMA(50) on completed closes. RSI arrow compares
  current RSI with prior RSI. Report a warming-up state only if the historical
  fetch fails or yields insufficient bars.
- Classify `bullish` only when price > EMA20 > EMA50 and RSI > 50; classify
  `bearish` only for price < EMA20 < EMA50 and RSI < 50; otherwise `neutral`.
- Indicators are display-only: they never block an entry or automatically pick
  CE/PE in this release.

### Manual candidate selection and order placement

- Recompute all eligibility at CTA execution under the Program-manager lock:
  program state, schedule/day, inter-cycle delay, Program/Risk Group/portfolio
  safeguards, script-master availability, current underlying price, expiry,
  configured entry gates, candidate option quote, sizing, and applicable
  margin. A displayed card quote is advisory and must never itself authorize
  a later order.
- Capital sizing independently searches the nearest affordable option:
  CE offsets ATM, ATM+1, ATM+2, ATM+3; PE offsets ATM, ATM-1, ATM-2, ATM-3.
  It uses LTP plus the existing two-point buffer, chooses the closest candidate
  admitting at least one whole lot. For `auto_pair`, it sizes the maximum whole-lot
  quantity within `capital_per_leg`. For `manual_single_leg`, it sizes using the
  **full** Program `capital`.
- Fixed-lot Live Programs test ATM then the same selected-leg outward offsets
  through three steps using `get_order_margin` and the existing 10% buffer.
  Fixed-lot Paper Programs select ATM at configured lots because Paper has no
  margin model. No Program is rejected at create/update time.
- If no capital candidate or no Live fixed-lot margin candidate is available
  through offset three, set a distinct `halted_entry_unaffordable` runtime
  status, persist a card alert explaining the last failed condition, log it,
  and require human Resume. Do not flatten an open cycle.
- Place exactly one normal Program leg through the existing OrderManager path,
  retaining the current app-watched exit mechanism and Program tags.

### Cycle, UI, and durable telemetry

- Snapshot `expected_legs` at cycle creation. Pair cycles snapshot `[CE, PE]`;
  manual cycles snapshot `[CE]` or `[PE]`. Complete a cycle only when every
  expected Program order is present and terminal. Stop/Flatten/Close Cycle
  must act on every actual open order in either shape.
- Store the selected leg, selected strike/offset, quote used for selection,
  lot quantity, applicable margin/affordability outcome, and current indicator
  snapshot in `active_cycle_snapshot`. Write these unchanged into the immutable
  cycle factsheet for both Paper and Live.
- Program cards show the shared trend, RSI value/arrow, and EMA20/EMA50. EMA
  values are green when EMA20 > EMA50 and red otherwise. Manual cards show CE
  and PE CTAs only with no active cycle; cards show a clear warming/unavailable
  state when required data or eligibility is absent. No dialog is introduced.

## Safety, scope, and trade-offs

- No live broker order, cancel, modify, or close is used for testing.
- Broker-side OCO/conditional exits are not introduced. Existing safety checks,
  atomic persistence, Paper/Live manager separation, and clock authority stay
  intact.
- This is decision support, not an asserted profitability improvement.
  Factsheet telemetry is the required evidence for Paper-vs-Live analysis
  before any indicator-driven automation is proposed.
- Out of scope: configurable indicator periods, option-premium indicators,
  auto-selected legs, and indicator entry blocks. (Historical vendor OHLC backfill
  is now handled via Tradejini chart-data API for the current day).

## Verification and acceptance

1. Compile all changed backend modules, check frontend JavaScript syntax, and
   inspect the final diff for stale names and whitespace errors.
2. Run focused pure tests for minute bucketing, persistence/restart restore,
   RSI/EMA warm-up, bullish/bearish/neutral classification, and shared-symbol
   subscription union/release.
3. Run a real fake-broker simulation using the repository's `OrderManager`
   verification pattern. It must prove: existing auto-pair creation/closure is
   unchanged; CE-only and PE-only cycles close after one terminal leg; duplicate
   CTA clicks cannot create duplicate orders; close-cycle and flatten find the
   one open leg; and a restarted manager restores the expected-leg snapshot.
4. Simulate the supplied capital examples and the no-affordable candidate
   halt. Simulate fixed-lot Live margin widening through ±3 and fixed-lot
   Paper ATM selection without a margin API.
5. Assert both Paper and Live cycle factsheets contain the decision snapshot
   and eventual P&L. State explicitly that no live-broker behavior was tested.

## Scope and assumptions

One-minute bars, RSI(14), EMA(20), EMA(50), underlying-index inputs, and the
definition of bullish/bearish above are fixed v1 decisions. “Put ATM +3” in
the request is interpreted as the third OTM put, ATM-3. The human reviews this
artifact before implementation; a later code handover records only simulations
actually run and remaining risks.
