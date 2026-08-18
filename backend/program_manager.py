"""
The Advanced OMS orchestration engine. A "Program" is an entity that
actively DRIVES orders on its own schedule -- as opposed to a Strategy
(models.py), which is a passive template someone applies by hand to one
order at a time.

Cycle lifecycle (per running Program, each tick):
  1. If a cycle is already active: check whether BOTH its legs have
     closed (order_manager.get_orders_by_cycle). If so, compute the
     cycle's net P&L, hand it to program_safeguards.on_cycle_closed()
     (which updates counters and may halt/throttle the Program), log it,
     and stop -- the NEXT tick decides whether to start a new cycle.
  2. If no cycle is active: ask program_safeguards.can_start_new_cycle()
     whether it's allowed right now (time cutoff, cooldown, hard stops,
     portfolio cap). If yes: fetch the underlying's live spot price
     (reusing order_manager.fetch_live_price -- the exact mechanism the
     New Order page's "Fetch price" button already uses), pick the next
     qualifying expiry and the ATM strike (script_master.py), and place
     BOTH legs through order_manager.create_and_place_order_with_strategy
     -- the same entry/exit/trailing/reconciliation machinery every other
     order in this app uses, tagged with program_id/cycle_id/leg so this
     module can find them again.

Every tick also recomputes the portfolio-wide aggregate daily P&L across
every Program and applies the Strict (A) portfolio-wide hard stop if
crossed -- see program_safeguards.py's module docstring for the full
design reasoning.

A hard stop or a manual "Stop" NEVER touches currently-open legs -- those
keep running their own SL/Target/trailing untouched. Flattening what's
open is the separate, explicit stop_and_flatten_program() below.
"""
import asyncio
import dataclasses
import logging
import uuid
from datetime import date, datetime
from types import SimpleNamespace

from . import store, failure_log
from . import program_safeguards as sg
from . import program_schedule as psched
from .models import ProgramConfig, RiskGroupConfig, ScheduleConfig, as_dict
from .script_master import ScriptMaster, load_market_holidays

log = logging.getLogger("tradejini.program")

TICK_INTERVAL_SECONDS = 15
CAPITAL_SIZING_SLIPPAGE_POINTS = 2  # same convention as the New Order page's own capital-based sizing
                                     # (order.js) -- a small buffer against price ticking slightly before
                                     # the entry actually fills, so the order doesn't get sized too tight


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ProgramManager:
    def __init__(self, live_order_manager, paper_order_manager, script_master: ScriptMaster):
        self.live_order_manager = live_order_manager
        self.paper_order_manager = paper_order_manager
        self.script_master = script_master
        self._programs: dict[str, dict] = {}   # program_id -> {"config": {...}, "runtime": {...}, "logs": [...]}
        self._risk_groups: dict[str, dict] = {}  # risk_group_id -> {"risk_group_id", "name", "daily_loss_amount_override"}
        self._holidays: set = set()
        self._lock = asyncio.Lock()
        self.last_tick_at: datetime | None = None  # set only on a SUCCESSFUL tick() completion -- if this
                                                      # stops advancing, the orchestration loop has silently
                                                      # died or is stuck; see the Heartbeat feature (main.py)

    def _order_manager_for(self, cfg: dict):
        """Routes to the paper-dedicated OrderManager (backed by
        PaperBrokerClient) or the real one, based on this Program's own
        `mode` -- the single place this decision is made, so a paper
        Program's legs go through PaperBrokerClient's simulated fills and
        a live Program's go through the real Tradejini client, with every
        other line of orchestration code identical either way."""
        return self.paper_order_manager if cfg.get("mode") == "paper" else self.live_order_manager

    # --------------------------------------------------------------- boot --

    async def load_from_disk(self):
        for p in store.list_programs():
            p.setdefault("logs", [])
            p.setdefault("cycles", [])
            p.setdefault("archived", False)
            # a Program saved before Scheduling/Paper-Live/capital-sizing existed
            # is missing these keys in its config entirely -- backfill sensible
            # defaults so downstream reads never need defensive None-checks
            p["config"].setdefault("schedule", as_dict(ScheduleConfig()))
            p["config"].setdefault("mode", "live")
            p["config"].setdefault("sizing_mode", "lots")
            p["config"].setdefault("capital_per_leg", None)
            self._programs[p["config"]["program_id"]] = p
        for g in store.list_risk_groups():
            self._risk_groups[g["risk_group_id"]] = g
        self._backfill_missing_risk_groups()
        self._holidays = load_market_holidays()
        log.info("Loaded %d Program(s), %d Risk Group(s) from disk.", len(self._programs), len(self._risk_groups))

    def _backfill_missing_risk_groups(self):
        """A Program saved before Risk Group existed has no risk_group_id.
        One is auto-created per distinct underlying (index_id) among the
        Programs that need it -- e.g. two NIFTY Programs saved before this
        feature existed land in the same auto-created group, a BANKNIFTY
        one gets its own. Named after the raw index_id for now (not its
        display name -- script master data may not be loaded yet this early
        in startup); freely renamable afterward through the UI. Nothing
        about this needs to be exact, just non-destructive: it only ever
        creates a group and assigns it, never touches an existing
        assignment."""
        groups_by_index: dict[str, str] = {}  # index_id -> risk_group_id, populated as we create new ones below.
                                                # Deliberately NOT pre-populated from existing groups by matching
                                                # names to an index -- that could silently merge a Program into
                                                # a group a person deliberately renamed for something else.
                                                # Backfill only ever creates fresh groups, never reuses existing ones.

        for program in self._programs.values():
            if program["config"].get("risk_group_id"):
                continue
            index_id = program["config"]["index_id"]
            if index_id not in groups_by_index:
                new_group = {"risk_group_id": uuid.uuid4().hex[:12], "name": f"Auto: {index_id}",
                             "daily_loss_amount_override": None}
                self._risk_groups[new_group["risk_group_id"]] = new_group
                store.save_risk_group(new_group)
                groups_by_index[index_id] = new_group["risk_group_id"]
                log.info("Backfilled a new Risk Group '%s' for pre-existing Program(s) on %s.",
                          new_group["name"], index_id)
            program["config"]["risk_group_id"] = groups_by_index[index_id]
            store.save_program(program)

    async def tick_loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Program tick failed -- will retry next cycle.")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    # ------------------------------------------------------- risk groups --

    def list_risk_groups(self) -> list:
        return sorted(self._risk_groups.values(), key=lambda g: g["name"].lower())

    def get_risk_group(self, risk_group_id: str):
        return self._risk_groups.get(risk_group_id)

    def create_risk_group(self, config_dict: dict) -> dict:
        cfg = RiskGroupConfig.from_dict(config_dict)
        group = as_dict(cfg)
        self._risk_groups[cfg.risk_group_id] = group
        store.save_risk_group(group)
        return group

    def update_risk_group(self, risk_group_id: str, config_dict: dict) -> dict:
        if risk_group_id not in self._risk_groups:
            raise ValueError("Risk Group not found")
        config_dict = dict(config_dict)
        config_dict["risk_group_id"] = risk_group_id
        cfg = RiskGroupConfig.from_dict(config_dict)
        group = as_dict(cfg)
        self._risk_groups[risk_group_id] = group
        store.save_risk_group(group)
        return group

    def delete_risk_group(self, risk_group_id: str):
        if risk_group_id not in self._risk_groups:
            raise ValueError("Risk Group not found")
        members = [p["config"]["name"] for p in self._programs.values()
                   if p["config"].get("risk_group_id") == risk_group_id]
        if members:
            raise ValueError(f"can't delete -- {len(members)} Program(s) still belong to this group "
                              f"({', '.join(members)}); reassign them first")
        del self._risk_groups[risk_group_id]
        store.delete_risk_group(risk_group_id)

    # ---------------------------------------------------------- public API --

    def list_programs(self) -> list:
        return sorted(self._programs.values(), key=lambda p: p["config"]["name"].lower())

    def get_program(self, program_id: str):
        return self._programs.get(program_id)

    def create_program(self, config_dict: dict) -> dict:
        cfg = ProgramConfig.from_dict(config_dict)
        rt = sg.ProgramRuntimeState(trading_day=date.today().isoformat())
        program = {"config": as_dict(cfg), "runtime": dataclasses.asdict(rt), "logs": [], "cycles": [], "archived": False}
        self._programs[cfg.program_id] = program
        store.save_program(program)
        return program

    def archive_program(self, program_id: str) -> dict:
        """Deliberately orthogonal to `runtime.status` (running/stopped/
        halted) -- archiving permanently excludes a Program from ever
        starting a NEW cycle, full stop, regardless of what its status
        says, until explicitly unarchived. Any cycle already open at the
        moment of archiving keeps running normally to close -- same
        "never touch open positions" principle as every other safeguard/
        stop mechanism in this app; archiving only ever blocks the future."""
        program = self._require(program_id)
        program["archived"] = True
        store.save_program(program)
        self._log(program, "Archived by user -- no new cycle will start until unarchived. Any cycle "
                            "already open keeps running normally to close.")
        return program

    def unarchive_program(self, program_id: str) -> dict:
        program = self._require(program_id)
        program["archived"] = False
        store.save_program(program)
        self._log(program, "Unarchived by user -- eligible to trade again (subject to its own status "
                            "and safeguards, same as any other Program).")
        return program

    def update_program(self, program_id: str, config_dict: dict) -> dict:
        """An edit reuses the same program_id (same in-place-rename pattern
        already used for Strategies) -- runtime state/counters/history are
        untouched by a config edit."""
        existing = self._require(program_id)
        config_dict = dict(config_dict)
        config_dict["program_id"] = program_id
        cfg = ProgramConfig.from_dict(config_dict)
        existing["config"] = as_dict(cfg)
        store.save_program(existing)
        return existing

    def delete_program(self, program_id: str):
        program = self._require(program_id)
        if program["runtime"].get("active_cycle_id"):
            raise ValueError("can't delete a Program with an active cycle -- stop it first")
        del self._programs[program_id]
        store.delete_program(program_id)

    def stop_program(self, program_id: str) -> dict:
        """Soft stop: any active cycle finishes naturally (its legs keep
        running their own SL/Target/trailing); no new cycle starts."""
        program = self._require(program_id)
        program["runtime"]["status"] = sg.STOPPED_BY_USER
        store.save_program(program)
        self._log(program, "Stopped by user -- current cycle (if any) will finish naturally, no new cycle will start.")
        return program

    def resume_program(self, program_id: str) -> dict:
        """Clears a halt/manual-stop and resumes -- the explicit human
        action a hard stop is designed to require.

        Resets `consecutive_losses` to 0: that counter specifically means
        "N losses IN A ROW, right now" -- a human reviewing and choosing to
        resume is exactly the reset condition that streak is meant to wait
        for, so leaving it unchanged would make every resume a silent
        no-op (the very next check would immediately re-trip on the same
        still-at-limit counter).

        Deliberately does NOT touch `daily_realized_pnl` -- that's real
        money already lost today, not a streak to forgive. If the halt was
        for the DAILY loss cap specifically, resuming clears the status but
        the Program will still correctly decline to start a new cycle for
        the rest of today (the daily figure is still past the cap) --
        logged clearly below so that isn't a silent, confusing no-op."""
        program = self._require(program_id)
        old_status = program["runtime"]["status"]
        program["runtime"]["status"] = sg.RUNNING
        program["runtime"]["consecutive_losses"] = 0
        store.save_program(program)

        cfg = program["config"]
        still_over_daily_cap = program["runtime"]["daily_realized_pnl"] <= -abs(cfg["safeguards"]["daily_loss_amount"])
        note = (" NOTE: today's realized P&L is still past the daily loss cap, so this Program will "
                "stay inactive for new cycles until that resets tomorrow -- editing the cap is the "
                "only way to let it trade again today.") if still_over_daily_cap else ""
        self._log(program, f"Resumed by user (was: {old_status}). Consecutive-loss streak reset to 0.{note}")
        return program

    async def stop_and_flatten_program(self, program_id: str) -> dict:
        """The explicit, separate action from a soft stop: close whatever's
        open right now, immediately, at market -- via the exact same
        request_close() every other order in this app uses to close."""
        program = self._require(program_id)
        program["runtime"]["status"] = sg.STOPPED_BY_USER
        cycle_id = program["runtime"].get("active_cycle_id")
        om = self._order_manager_for(program["config"])
        closed_any = False
        if cycle_id:
            for o in om.get_orders_by_cycle(cycle_id):
                if o["status"] not in ("closed", "cancelled", "entry_rejected"):
                    try:
                        await om.request_close(o["order_id"], reason="program_flatten")
                        closed_any = True
                    except Exception as e:
                        log.error("Flatten failed for order %s: %s", o["order_id"], e)
                        failure_log.log_failure(category="program_flatten_failed", order_id=o["order_id"], program_id=program_id, message=str(e))
        store.save_program(program)
        self._log(program, f"Stopped and flattened by user.{' Close requested on open leg(s).' if closed_any else ' No open legs to close.'}")
        return program

    def _require(self, program_id: str) -> dict:
        program = self._programs.get(program_id)
        if not program:
            raise ValueError("Program not found")
        return program

    def _log(self, program: dict, msg: str):
        program.setdefault("logs", []).append({"ts": now_iso(), "msg": msg})
        program["logs"] = program["logs"][-200:]
        store.save_program(program)

    # -------------------------------------------------------- orchestration --

    async def tick(self):
        async with self._lock:
            now = datetime.now()
            today_iso = now.date().isoformat()

            for program in self._programs.values():
                rt = sg.ProgramRuntimeState(**program["runtime"])
                sg.ensure_fresh_trading_day(rt, today_iso)
                program["runtime"] = dataclasses.asdict(rt)

            runtime_map = {pid: sg.ProgramRuntimeState(**p["runtime"]) for pid, p in self._programs.items()}

            # ---- tier 1: Risk Group -- halt only THAT group's members ----
            risk_group_caps: dict[str, float] = {}
            for group_id, group in self._risk_groups.items():
                member_ids = {pid for pid, p in self._programs.items() if p["config"].get("risk_group_id") == group_id}
                if not member_ids:
                    continue
                member_caps = [self._programs[pid]["config"]["safeguards"]["daily_loss_amount"] for pid in member_ids]
                group_cap = sg.effective_group_daily_loss_cap(member_caps, group.get("daily_loss_amount_override"))
                risk_group_caps[group_id] = group_cap
                group_pnl = sum(runtime_map[pid].daily_realized_pnl for pid in member_ids)
                newly_halted = sg.apply_group_halt_if_needed(
                    runtime_map, member_ids, group_daily_pnl=group_pnl, group_cap=group_cap,
                    halt_status=sg.HALTED_RISK_GROUP,
                )
                for pid in newly_halted:
                    program = self._programs[pid]
                    program["runtime"] = dataclasses.asdict(runtime_map[pid])
                    self._log(program, f"HALTED: Risk Group '{group['name']}' daily loss cap reached "
                                        f"(group aggregate {group_pnl:.2f} vs cap {group_cap:.2f}). "
                                        f"This Program's own numbers may still be fine -- other Programs in "
                                        f"'{group['name']}' pulled the group past its cap. Resume manually once reviewed.")
                    failure_log.log_failure(category="program_risk_group_halt", order_id=None, program_id=pid,
                                             message=f"Program {program['config']['name']} halted: "
                                                     f"Risk Group '{group['name']}' cap reached")

            # ---- tier 2: Portfolio -- toggleable; halts EVERYTHING if enabled ----
            portfolio_pnl = sum(rt.daily_realized_pnl for rt in runtime_map.values())
            portfolio_cfg = store.load_portfolio_safeguards()
            portfolio_enabled = portfolio_cfg.get("enabled", True)

            if portfolio_enabled:
                # rolls up from Risk Group caps now that Risk Group sits between Program and
                # Portfolio; a Program with no group (shouldn't normally happen post-backfill,
                # but handled defensively) counts by its own cap directly so it's never silently
                # excluded from the portfolio ceiling
                ungrouped_caps = [p["config"]["safeguards"]["daily_loss_amount"] for p in self._programs.values()
                                   if not p["config"].get("risk_group_id")]
                portfolio_cap = sg.effective_group_daily_loss_cap(
                    list(risk_group_caps.values()) + ungrouped_caps, portfolio_cfg.get("daily_loss_amount_override")
                )
                newly_halted = sg.apply_portfolio_halt_if_needed(
                    runtime_map, portfolio_daily_pnl=portfolio_pnl, portfolio_cap=portfolio_cap
                )
                for pid in newly_halted:
                    program = self._programs[pid]
                    program["runtime"] = dataclasses.asdict(runtime_map[pid])
                    self._log(program, f"HALTED: portfolio-wide daily loss cap reached "
                                        f"(aggregate {portfolio_pnl:.2f} vs cap {portfolio_cap:.2f}). "
                                        f"This Program's own numbers may still be fine. Resume manually once reviewed.")
                    failure_log.log_failure(category="program_portfolio_halt", order_id=None, program_id=pid,
                                             message=f"Program {program['config']['name']} halted: portfolio cap reached")
            else:
                portfolio_cap = float("inf")  # disabled -- never trips the per-cycle check below

            for program in list(self._programs.values()):
                try:
                    await self._tick_one(program, now=now, portfolio_pnl=portfolio_pnl, portfolio_cap=portfolio_cap)
                except Exception:
                    # isolate one Program's failure from every other Program in this same tick,
                    # AND from last_tick_at below -- without this, a single persistently-broken
                    # Program could silently freeze the heartbeat signal (and every other Program's
                    # ticking) forever, which is exactly the class of failure Heartbeat exists to catch
                    log.exception("Tick failed for Program %s -- other Programs still processed this tick.",
                                  program["config"]["name"])
                    failure_log.log_failure(category="program_tick_failed", order_id=None,
                                             program_id=program["config"]["program_id"],
                                             message=f"Program {program['config']['name']}: tick raised an exception")

            self.last_tick_at = now  # only reached after every Program above has been attempted --
                                       # a repeatedly-failing tick loop itself (not a single Program)
                                       # correctly leaves this stale, which is the real "silently died" signal

    async def _tick_one(self, program: dict, *, now: datetime, portfolio_pnl: float, portfolio_cap: float):
        cfg = program["config"]
        rt = program["runtime"]

        if rt["active_cycle_id"]:
            om = self._order_manager_for(cfg)
            legs = om.get_orders_by_cycle(rt["active_cycle_id"])
            terminal = ("closed", "cancelled", "entry_rejected")
            if len(legs) == 2 and all(o["status"] in terminal for o in legs):
                self._close_cycle(program, legs, now)
            return

        if program.get("archived"):
            return  # never starts a new cycle while archived, regardless of status -- but an
                     # already-open cycle (handled above) still gets tracked/closed normally

        schedule = cfg.get("schedule", {})
        expiry_dates = set(self.script_master.available_expiries(cfg["index_id"])) if schedule.get("days") == "expiry_day" else set()
        schedule_ok, schedule_reason = psched.can_trade_now(
            continuous=schedule.get("continuous", False),
            start_time=schedule.get("start_time", "09:15"),
            end_time=schedule.get("end_time", "14:55"),
            days=schedule.get("days", "all"),
            now=now, expiry_dates=expiry_dates,
        )
        if not schedule_ok:
            return  # not logged every tick -- would spam the log every 15s all day; the Program's own
                     # card shows its schedule config directly, so the reason is already visible there

        if not psched.inter_cycle_delay_elapsed(
            last_cycle_closed_at=rt.get("last_cycle_closed_at"),
            delay_seconds=schedule.get("inter_cycle_delay_seconds", 0), now=now,
        ):
            return

        allowed, reason = sg.can_start_new_cycle(
            program_status=rt["status"], active_cycle_id=rt["active_cycle_id"],
            cooldown_until=rt["cooldown_until"], consecutive_losses=rt["consecutive_losses"],
            daily_realized_pnl=rt["daily_realized_pnl"], now=now,
            consecutive_loss_limit=cfg["safeguards"]["consecutive_loss_limit"],
            daily_loss_amount=cfg["safeguards"]["daily_loss_amount"],
            portfolio_daily_pnl=portfolio_pnl, portfolio_daily_loss_cap=portfolio_cap,
        )
        if not allowed:
            return
        await self._start_new_cycle(program, now)

    def _close_cycle(self, program: dict, legs: list, now: datetime):
        cfg = program["config"]
        cycle_pnl = 0.0
        any_unknown = False
        for o in legs:
            realized = (o.get("pnl") or {}).get("realized")
            if realized is None:
                any_unknown = True
            else:
                cycle_pnl += realized
        if any_unknown:
            self._log(program, f"Cycle {program['runtime']['active_cycle_id']} closed with at least one leg's "
                                f"P&L unknown -- treated as 0 for safeguard counters. Check the leg's own Info "
                                f"panel for its P&L source.")

        # persist a numbered cycle record BEFORE on_cycle_closed() clears
        # active_cycle_id/active_cycle_started_at below -- this is the only
        # place these values are ever recorded, so it has to happen here
        program.setdefault("cycles", [])
        cycle_number = len(program["cycles"]) + 1
        program["cycles"].append({
            "cycle_number": cycle_number,
            "cycle_id": program["runtime"]["active_cycle_id"],
            "started_at": program["runtime"].get("active_cycle_started_at"),
            "closed_at": now_iso(),
            "pnl": round(cycle_pnl, 2),
            "pnl_unknown": any_unknown,
            "order_ids": [o["order_id"] for o in legs],
        })
        program["cycles"] = program["cycles"][-500:]  # bounded, same reasoning as the 200-cap on logs

        state = sg.ProgramRuntimeState(**program["runtime"])
        s = cfg["safeguards"]
        state = sg.on_cycle_closed(
            state, cycle_pnl=cycle_pnl, now=now,
            consecutive_loss_limit=s["consecutive_loss_limit"], daily_loss_amount=s["daily_loss_amount"],
            max_cycles_per_day=s["max_cycles_per_day"], cooldown_minutes=s["cooldown_minutes"],
        )
        program["runtime"] = dataclasses.asdict(state)
        store.save_program(program)

        status_note = ""
        if state.status != sg.RUNNING:
            status_note = f" Program is now {state.status.replace('_', ' ')} -- needs manual resume."
        elif state.cooldown_until:
            status_note = f" Cooling down until {state.cooldown_until} (max cycles/day throttle)."
        self._log(program, f"Cycle #{cycle_number} closed. Net P&L: {cycle_pnl:.2f}. "
                            f"Consecutive losses: {state.consecutive_losses}. "
                            f"Today's total: {state.daily_realized_pnl:.2f} ({state.cycles_today} cycle(s)).{status_note}")

    async def _start_new_cycle(self, program: dict, now: datetime):
        cfg = program["config"]
        om = self._order_manager_for(cfg)

        index_row = self.script_master.get_index(cfg["index_id"])
        if not index_row:
            self._log(program, f"Can't start a cycle: index '{cfg['index_id']}' not found in script master data "
                                f"-- has it loaded yet? Check the app startup log.")
            return

        index_stream_symbol = f"{index_row.exc_token}_NSE"
        spot = await om.fetch_live_price(index_stream_symbol, timeout=8.0)
        if spot is None:
            self._log(program, f"Can't start a cycle: no live price for {index_row.disp_name} "
                                f"({index_stream_symbol}) within 8s -- market closed, or feed not connected?")
            failure_log.log_failure(category="program_spot_fetch_failed", order_id=None, program_id=cfg["program_id"],
                                     message=f"Program {cfg['name']}: spot price fetch timed out")
            return

        expiry = self.script_master.next_expiry(
            cfg["index_id"], min_working_days=cfg["min_working_days_to_expiry"],
            holidays=self._holidays, today=now.date(),
        )
        if not expiry:
            self._log(program, f"Can't start a cycle: no expiry at least {cfg['min_working_days_to_expiry']} "
                                f"working day(s) out was found in the option chain.")
            return

        pair = self.script_master.atm_pair(cfg["index_id"], expiry, spot)
        if not pair:
            self._log(program, f"Can't start a cycle: no strike near spot {spot} has both a CE and a PE "
                                f"listed for expiry {expiry}.")
            return
        ce, pe = pair

        cycle_id = uuid.uuid4().hex[:12]
        self._log(program, f"Starting cycle {cycle_id}: spot {spot}, expiry {expiry}, "
                            f"ATM strike {ce.strike} -- {ce.id} + {pe.id}.")

        # Determine BOTH legs' quantities BEFORE placing anything. For
        # capital sizing specifically, both legs' prices must be knowable
        # up front so neither leg is ever placed without the other -- a
        # half-placed straddle would defeat the whole premise of the
        # strategy. "Equal capital, not equal lots" was a deliberate
        # choice over the simpler equal-lots rule: it gives predictable
        # SL risk on both sides regardless of which leg is more expensive
        # that day, at the cost of not being strictly delta-neutral.
        leg_qty = {}
        if cfg.get("sizing_mode") == "capital":
            capital = cfg.get("capital_per_leg") or 0
            for opt, leg_name in ((ce, "CE"), (pe, "PE")):
                leg_stream_symbol = f"{opt.exc_token}_NFO"
                leg_price = await om.fetch_live_price(leg_stream_symbol, timeout=5.0)
                if leg_price is None:
                    self._log(program, f"Can't start a cycle: no live price for {leg_name} leg ({opt.id}) "
                                        f"to size by capital -- aborting this cycle attempt (not placing "
                                        f"the other leg either), will retry next tick.")
                    failure_log.log_failure(category="program_leg_price_fetch_failed", order_id=None,
                                             program_id=cfg["program_id"],
                                             message=f"Program {cfg['name']}: {leg_name} leg price fetch "
                                                     f"timed out for capital sizing")
                    return
                effective_price = leg_price + CAPITAL_SIZING_SLIPPAGE_POINTS
                lots = int(capital / effective_price) // opt.lot if effective_price > 0 else 0
                if lots < 1:
                    self._log(program, f"Can't start a cycle: capital {capital} too small for even 1 lot "
                                        f"of {leg_name} ({opt.lot} qty/lot) at ~{leg_price} + slippage buffer.")
                    return
                leg_qty[leg_name] = lots * opt.lot
        else:
            leg_qty["CE"] = ce.lot * cfg["lots_per_leg"]
            leg_qty["PE"] = pe.lot * cfg["lots_per_leg"]

        # Margin pre-check -- tied to THIS Program's own broker specifically
        # (via a capability check on om.client, not a portfolio-wide check),
        # per the explicit design call: each Program's margin reality is
        # whatever ITS OWN broker says, not some aggregate across everything
        # running. Only ever applies to live Programs -- PaperBrokerClient
        # has no get_basket_margin method at all, so paper Programs skip
        # this automatically with no special-casing on `mode` needed. A
        # FAILED check (API error/timeout) deliberately does NOT block the
        # cycle -- only a check that SUCCEEDS and clearly reports a
        # shortfall does; an extra API call failing shouldn't become a new
        # point of failure preventing an otherwise-valid trade, when the
        # existing rejection-handling already covers that case at the
        # broker's own place-order step.
        if hasattr(om.client, "get_basket_margin"):
            basket = [
                {"symId": ce.id, "qty": leg_qty["CE"], "side": "buy", "type": "market", "product": cfg["product"]},
                {"symId": pe.id, "qty": leg_qty["PE"], "side": "buy", "type": "market", "product": cfg["product"]},
            ]
            try:
                margin_resp = await om.client.get_basket_margin(basket)
                margin_data = margin_resp.get("d") or {}
                shortfall = margin_data.get("shortfall") or 0
                if shortfall > 0:
                    self._log(program, f"Can't start a cycle: margin shortfall of ~₹{shortfall:.2f} "
                                        f"(available ~₹{margin_data.get('availableMargin')}, "
                                        f"required ~₹{margin_data.get('requiredMargin')} for both legs).")
                    failure_log.log_failure(category="program_margin_shortfall", order_id=None,
                                             program_id=cfg["program_id"],
                                             message=f"Program {cfg['name']}: margin shortfall ~₹{shortfall:.2f}")
                    return
            except Exception as e:
                log.warning("Margin pre-check failed for Program %s (proceeding anyway -- a failed "
                            "check itself shouldn't block an otherwise-valid trade): %s", cfg["name"], e)

        for opt, leg_name in ((ce, "CE"), (pe, "PE")):
            req = SimpleNamespace(
                sym_id=opt.id, side="buy", qty=leg_qty[leg_name], lot_size=opt.lot,
                strategy_name=None, label=f"{cfg['name']} {leg_name}",
                stream_symbol=f"{opt.exc_token}_NFO",
                entry_type="market", entry_validity="day",
                entry_limit_price=None, entry_trig_price=None,
                exit_mode="both",
            )
            leg_strategy = {
                "product": cfg["product"], "tick_size": opt.tick or 0.05,
                "stop": cfg["stop"], "target": cfg["target"], "time_exit": cfg["time_exit"],
            }
            try:
                order = await om.create_and_place_order_with_strategy(
                    req, leg_strategy,
                    program_tag={"program_id": cfg["program_id"], "cycle_id": cycle_id, "leg": leg_name},
                )
                if order["status"] == "entry_rejected":
                    self._log(program, f"{leg_name} leg REJECTED by broker: {opt.id} -- see its own log for the reason.")
            except Exception as e:
                log.exception("Failed to place %s leg for Program %s", leg_name, cfg["name"])
                self._log(program, f"Failed to place {leg_name} leg ({opt.id}): {e}")
                failure_log.log_failure(category="program_leg_place_failed", order_id=None, program_id=cfg["program_id"],
                                         message=f"Program {cfg['name']} {leg_name} leg: {e}")

        program["runtime"]["active_cycle_id"] = cycle_id
        program["runtime"]["active_cycle_started_at"] = now_iso()
        store.save_program(program)
