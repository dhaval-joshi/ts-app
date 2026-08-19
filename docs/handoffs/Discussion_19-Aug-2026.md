User:
I think we are not adding correct close reason. We have multiple routes to close an order. can you please verify?

Claude:
I found a real, verified bug. Confirmed by reading the code directly (not from memory), tracing all four close-completion paths:

The bug: _revert_stuck_square_off silently discards the true close reason
There are two failure paths that both call _revert_stuck_square_off (order_manager.py:794-819):

A square-off rejected or cancelled by the broker (:766)
A square-off stuck unresolved for >90s (:787)
Both unconditionally do this:


order["square_off"] = None
order["status"] = "watching"
order["close_reason"] = None   # <-- wipes it, regardless of what it was
order["closing_since"] = None
That's correct and safe for the case it was actually designed for — a stop/target trigger fired, the square-off failed, and reverting to "watching" lets _maybe_app_market_exit naturally re-evaluate the same trigger on the next tick and retry. The docstring says exactly that: "gets a genuine retry the next time a live tick arrives and its trigger re-evaluates."

But request_close() is the same mechanism for four other reasons that have no trigger to re-evaluate at all: "manual" (Regular OMS close, and "Close this leg"), "program_flatten" (Stop & Flatten), and "program_cycle_manual_close" (Close Cycle). For those, once reverted:

The original close intent is gone with no retry path. Nothing re-invokes request_close for that Program/order. The leg just sits "watching" again — it will only close if its own configured SL/target happens to cross, unrelated to why the user actually asked to close it. If price never crosses, a "Stop & Flatten" can silently never actually flatten.
If it does eventually close via its own SL/target, close_reason will read "stop_hit"/"target_hit" — factually wrong, since the position was originally being closed for a completely different reason (a manual flatten, not a trigger).
The warning shown to the user is misleading for these cases too: "will automatically retry closing it the next time a live tick arrives and its trigger re-evaluates" — there is no trigger for a manual close to re-evaluate.
"time_exit" is the one exception that self-heals: _check_time_exits independently re-scans every "watching" order each reconcile cycle, so a reverted time-exit will correctly re-fire and re-set close_reason="time_exit" on its own.

Net: 3 of the 6 close reasons (manual, program_flatten, program_cycle_manual_close) can silently lose their close request and later get mislabeled if the broker ever rejects/stalls the square-off — which is a plausible, non-rare occurrence, not just an edge case.

Want me to fix this? It touches order_manager.py (safety-critical), so I'd want to design the retry mechanism properly — likely: remember the original close_reason through a revert, and have the reconcile loop retry the actual close request for non-trigger reasons (mirroring how _check_time_exits already self-heals), not just silently fall back to watching. I'd walk through the design with you before touching the file, per usual.


=============================================
User:
Alright, let's list all the reasons. and also, I would want to know how many times the SL / Traget moved. AND what was the first SL/Target and what was the final one. Plus, I thought we spoke about slipage sometime back. I think we need to add that in. I believe, we should put this for both Paper and live. Live will anyways get reconcilieted once the factsheet gets updated. Btw, what's the schedule for updating factsheets? Do not start development yet, give me your thoughts on all and then we will get going.

Claude:
Good questions, and the research changes a couple of your assumptions — let me walk through all of it.

1. All the close_reason values, as they exist today
Six possible values, from the four sites that ever set it:

Value | Set by
"manual" | request_close()'s default — both the Regular OMS close button and the "Close this leg" button (same endpoint, no distinction between the two)
"program_flatten" | Stop & Flatten
"program_cycle_manual_close" | Close Cycle
"time_exit" | _check_time_exits
"stop_hit" | _maybe_app_market_exit, stop trigger crossed
"target_hit" | _maybe_app_market_exit, target trigger crossed
None | never had a position to close (entry_rejected, or entry cancelled pre-fill)

The bug I found earlier directly corrupts this list: for the three manual reasons, a stuck/rejected square-off wipes the reason to None and — if the leg eventually does close via its own stop/target — silently relabels it "stop_hit"/"target_hit", which is simply false. Whatever fix we land on needs to preserve the original reason through a revert-and-retry, not just retry blindly. Worth deciding together when we design that fix, since it changes how this list gets populated.

2. How many times SL/Target moved
trail_update_count/trail_failure_count already exist on every order — but I traced _trail_leg (the only place trailing actually happens) line by line, and neither is ever incremented. They're genuinely dead fields, confirmed by reading the code, not assumed.

My recommendation: don't revive them as-is. trail_failure_count was clearly built for the retired broker-OCO era, where a trailing update meant a broker modify-order call that could fail — trailing today is 100% local (no broker call at all), so "failure" doesn't map to anything real anymore. I'd remove it rather than repurpose a concept that no longer applies.

For the actual counter, I'd track stop and target separately — order["stop"]["trail_count"] / order["target"]["trail_count"], incremented at the exact point _trail_leg currently does leg["current_trig_price"] = candidate. Nesting under each leg's own dict matches how current_trig_price already lives there, rather than inventing new top-level fields.

3. First and final SL/Target
current_trig_price is the only value stored, and trailing overwrites it in place — the original is gone the moment it first moves. I'd add order["stop"]["initial_trig_price"] / order["target"]["initial_trig_price"], set once (at the same point _finalize_exit_leg sets current_trig_price for the first time) and never touched again. "Final" needs no new field — current_trig_price at the moment the leg closes already is the final value; it's just currently discarded once the order archives rather than being read for this purpose.

4. Slippage
Confirmed nothing computes this anywhere. Here's the design point worth deciding together: slippage is only a meaningful concept for stop_hit/target_hit closes. A manual close, a flatten, or a time-exit has no "expected price" to slip from — you asked for market-now, whatever fills is the outcome, there's no comparison point. So I'd compute it only for the two trigger-based reasons: capture the trigger price at the exact instant _maybe_app_market_exit fires (it already computes trig_value right there, just needs to be stored), then once the square-off's real fill price is known, slippage = exit_avg_price − trigger_price_at_close (sign-adjusted for side). Leave it null for the other four reasons rather than inventing a number that doesn't mean anything.

Related, smaller idea worth a mention: entry slippage (a market entry's requested/reference price vs. actual fill) is the same kind of gap on the other end of the trade. Not what you asked for, but cheap to add alongside if useful — your call.

5. Paper vs. Live — and a correction to "Live will anyway get reconciled"
Both should absolutely get identical treatment — it's the same order_manager.py code path driving both today for every other field, so there's no reason for these to diverge, and building them once, uniformly, is the natural shape here anyway.

But I want to correct the assumption before it shapes the design: broker reconciliation only corrects broker-truth fields — entry.status, entry.fill_qty, entry.avg_price, square_off.status, pnl.exit_avg_price. Trail counts, first/final trigger prices, and slippage are all app-computed values that never touch the broker at all — reconciliation has no way to know or correct any of them, live or paper. So "Live will get reconciled anyway" doesn't actually cover any of the four things you just asked for — it only ever helps with fill price/quantity/status. These need to be right the first time, on both sides, equally. If anything that's an argument for treating Paper and Live with the same rigor here, not a reason Live can be looser.

6. Factsheet schedule — direct answer
Two different things, worth being precise about:

Order factsheets: written immediately, synchronously, the instant an order reaches a terminal status (_on_terminal, called from all four terminal-transition sites). No delay, no batching.
Cycle factsheets: written the next time the Program tick loop runs after both legs are terminal — worst case ~15 seconds (TICK_INTERVAL_SECONDS), in practice close to immediate.
Broker reconciliation (the thing that amends a factsheet after the fact if broker truth disagrees with it): not scheduled at all. It's on-demand only — a human clicks "Run reconciliation" on the Admin page. There's no background job, no cron, nothing automatic. Worth knowing given the point above — nothing self-heals a factsheet unless you manually run it.

Let me know how you want to scope this and I'll write it up properly before touching anything.


=============================================
User:
one more thing I noticed is that the script master update failed when i had the program running since early mornig. I believe we need to schedule the script master pull and websocket connection only around the trading day start time. maybe some sort of self-healing approach as well for them.

Claude:
Good find — traced this precisely rather than guessing, and it changes what the actual fix should be.

The real root cause (confirmed, not assumed)
ScriptMaster.is_loaded() (script_master.py:228-234) just checks "do we have any parsed Index rows at all" — it doesn't distinguish "today's confirmed-fresh data" from "yesterday's stale cache we fell back to." And _script_master_refresh_loop (main.py:147-159) uses that exact same flawed signal to decide its retry cadence: 60s until is_loaded(), then 6 hours forever after.

Trace it through: refresh() calls Tradejini's version-check endpoint first. If that call itself fails (network hiccup, or — plausibly — Tradejini's own instrument-master generation isn't finished yet, which would land right around early morning), the except branch just loads whatever's cached and returns (script_master.py:121-126). If yesterday's cache is still sitting there and parses fine, is_loaded() flips to True on that very first attempt — and the loop immediately commits to the 6-hour cadence, even though what just happened was a failed attempt to get today's data, not a successful one. One early-morning hiccup and the app can silently run on yesterday's strikes/expiries for the whole session, with nothing but a single log.warning line to show for it.

On "schedule the pull/websocket around trading-day start" — pushing back a little
I don't think delaying the connection is actually the right fix, and want to explain why before we commit to it:

It narrows the race window but doesn't close it — if Tradejini's own data happens to be running late on any given day, waiting until 8:45 instead of app-boot just moves the same failure to a different clock time.
It has a real cost: Programs need script master + a live stream ready before 9:15 to resolve expiry/strikes for their first cycle attempt of the day. Delaying the connection risks making that first attempt slower or blocked right at the moment it matters most.
Nothing about when the loop starts fixes the actual bug — a stale fallback getting treated as "loaded" is wrong at any hour, not just at boot.
What I'd propose instead: keep connecting as early as the app starts (harmless — an idle, authenticated connection costs nothing), but make freshness self-verifying and self-healing regardless of when it runs:

Track confirmed freshness, not just "ever parsed something." Store something like {"version": ..., "confirmed_date": "2026-08-20"} instead of just a version number, and have the refresh loop retry on a short interval until today specifically is confirmed — not "until any data loaded" — falling back to the slow 6h cadence only once genuinely current. This targets the actual bug directly.
Make staleness visible, not just logged. Right now heartbeat.py's entity list has exactly one thing in it — the Tradejini broker connection. Script master freshness is completely invisible to it. A generic EntityStatus already exists for exactly this purpose (entity_type is deliberately open-ended); adding a "market data" entry would surface "today's instrument data isn't confirmed" as a visible red/yellow dot immediately, not something discovered hours later when a Program can't resolve an expiry.
The websocket side has a related but separate gap
Two things, both confirmed from the actual SDK code:

No ping is ever sent. The vendored SDK has sendPing() built and working — never called. is_connected() today only reflects whether the socket handshake completed and hasn't formally closed, not whether the broker is actually still there.
No stale-tick detection. Nothing tracks "when did we last actually receive a tick." A connection that's gone silently dead server-side (common with long-lived idle sockets, no clean close frame) would report connected: true forever while Programs quietly stop getting fresh prices.
Same underlying pattern as script master: something is being treated as a permanent fact once true, instead of something that needs periodic re-confirmation. I'd fix both with the same shape — active pinging + last-tick-age tracking, both feeding into heartbeat as visible signals — rather than as two unrelated patches.

One more thread this connects to: none of this reaches you unless you're watching the heartbeat dot in an open tab, which is the same gap as the still-not-built alerting channel from the earlier product review. Worth keeping in mind if we're touching this area.

Want me to write this up as a proper plan, or do you want to react to the "don't delay the connection, fix the freshness-confirmation logic instead" framing first?


=============================================
User:
I just checked reconciliation for both Regular OMS and Advanced OMS
Regular OMS - as you suggested is working fine. However, when I tried reconciliation from Admin section, it showed success message stating both side matched, but the numbers it showed were completely off. Plus, the Preview does not work as well. 

I can see 43 orders on Broker's side, the same is fetched from broker's api as well, however, the application shows only 22 orders -- stating 22 checked and 22 match. 

Out of these 43, 1 was rejected and 4 were Regular OMS, it still comes down to 38 -- nowhere near 22 reported by app. Plus, I don't believe the check is done properly and I don't believe the Advanced OMS would have 100% match with broker. As you said yourself, the actual recommendation for Advanced OMS orders are done only when I initiate the same from admin section. 

I would also want a feature to add single leg entry. While setting up the program, I should have ability to state if I would want to choose leg before each cycle start. If I set that flag, the application should ask me to choose leg before initiating the order. It should also ensure the capital condition we already have in place is matched for single leg in such case and choose the strike prices based on that.

It would be great to also have 
 - a live general direction of trend for each leg. 
 - RSI direction and value (up in green arrow, down in red arrow)
 - 20 EMA and 50 EMA values (value in green if 20 EMA is higher than 50 EMA else value in red)

Example:
Capital = 5K
Capital after buffer = 4.5K
Lot Size = 65

Scenario 1:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 150 
 - PUT ATM - 1 = 120
 - PUT ATM - 2 = 100
 - PUT ATM - 3 = 90
 
The app should propose entries for Call ATM + 2 and no entry for Put. 

Scenario 2:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 50 
 - PUT ATM - 1 = 45
 - PUT ATM - 2 = 32
 - PUT ATM - 3 = 30
 
The app should propose entries for Call ATM + 2 and Put ATM. 


Scenario 3:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 120 
 - PUT ATM - 1 = 100
 - PUT ATM - 2 = 77
 - PUT ATM - 3 = 76.5
 
The app should propose entries for Call ATM + 2 and Put ATM + 3. 

This feature shuld be availble on both Live and Paper programs. And since multiple programs could be running at the same time, the option to select leg shoud be non-obstructive. Basically - no dialogs. Maybe CTAs on program card itself for both legs. 


Claude:





