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
import copy
import dataclasses
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace

from . import clock, store, failure_log, factsheet
from . import entry_signals
from . import program_safeguards as sg
from . import program_schedule as psched
from .models import ProgramConfig, RiskGroupConfig, ScheduleConfig, EntrySignalConfig, as_dict
from .script_master import ScriptMaster, load_market_holidays

log = logging.getLogger("tradejini.program")

TICK_INTERVAL_SECONDS = 15
CAPITAL_SIZING_SLIPPAGE_POINTS = 2  # same convention as the New Order page's own capital-based sizing
                                     # (order.js) -- a small buffer against price ticking slightly before
                                     # the entry actually fills, so the order doesn't get sized too tight
MARGIN_SAFETY_BUFFER = 1.10  # require available margin >= requiredMargin * this, not just >= requiredMargin
                              # -- the broker's own basket-margin response has zero safety margin baked in
                              # (its own "shortfall" field is 0 the instant available == required exactly),
                              # which is too tight for a live cycle to actually enter reliably
STRANGLE_WIDEN_MAX_STEPS = 3  # if the ATM straddle fails the buffered margin check, retry widened into a
                                # strangle (CE at ATM+N, PE at ATM-N strikes) for N in 1..this, before giving
                                # up -- see _start_new_cycle and script_master.strike_pair_at_offset
ENTRY_SIGNAL_FETCH_TIMEOUT_SECONDS = 3.0  # short and separate from the 8.0s used for the essential spot-
                                # price fetch -- entry_signals inputs are optional/best-effort, and
                                # _tick_one calls are sequential across every Program (see tick()), so a
                                # slow/closed feed here must not stall every other Program's tick behind it


def now_iso() -> str:
    return clock.now_iso()


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
        self._greeks_unverifiable_logged: dict[str, str] = {}  # program_id -> ISO date already logged today --
                                                      # in-memory only (same precedent as OrderManager's
                                                      # _trail_state: harmless to lose on restart, just means
                                                      # one extra log line the day the app happens to restart),
                                                      # so entry_signals' Greeks-entitlement-unverified case is
                                                      # logged once per Program per day, not every 15s tick

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
            p.setdefault("updated_at", p.get("created_at") or now_iso())  # a Program saved before this field
                                           # existed just gets "now" once at load time -- affects only its
                                           # initial sort position in list_programs(), not correctness
            p.setdefault("alert", None)  # {"message": str, "since": iso timestamp} -- a non-halting,
                                           # persistent card banner (e.g. "insufficient funds even after
                                           # widening"); distinct from a halt (runtime.status) since the
                                           # Program keeps running/retrying. Loose top-level key, not on
                                           # `runtime`, on purpose -- `runtime` round-trips through
                                           # ProgramRuntimeState (a strict dataclass) on every tick
            p.setdefault("active_cycle_snapshot", None)  # captured in _start_new_cycle the moment a cycle
                                           # starts (program config/spot/expiry/strikes/widen offset as
                                           # they stood then), consumed and cleared in _close_cycle for the
                                           # cycle factsheet -- same "loose top-level key, not on runtime"
                                           # reasoning as `alert` above. None whenever no cycle is active.
            p.setdefault("mtm_pnl", None)  # transient live display value, refreshed every tick() -- see
                                           # tick()'s own comment; None until the first tick runs regardless
            # a Program saved before Scheduling/Paper-Live/capital-sizing existed
            # is missing these keys in its config entirely -- backfill sensible
            # defaults so downstream reads never need defensive None-checks
            p["config"].setdefault("schedule", as_dict(ScheduleConfig()))
            p["config"].setdefault("mode", "live")
            p["config"].setdefault("broker_id", "tradejini")
            p["config"].setdefault("super_program_id", None)
            p["config"].setdefault("sizing_mode", "lots")
            p["config"].setdefault("capital_per_leg", None)
            p["config"].setdefault("entry_signals", as_dict(EntrySignalConfig()))  # enabled=False -- a
                                           # Program saved before this round exists gets today's exact
                                           # behavior (no gate checked at all) until explicitly opted in
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
        """Live Programs first, then paper; within each group, most
        recently updated first. Two-pass STABLE sort (recency first, then
        mode) rather than one combined key -- sorted()'s stability means
        the mode pass preserves each group's recency order from the first
        pass, which is simpler than building one composite sort key that
        has to invert direction between an ascending grouping and a
        descending recency field."""
        by_recency = sorted(self._programs.values(), key=lambda p: p.get("updated_at", ""), reverse=True)
        return sorted(by_recency, key=lambda p: p["config"]["mode"] != "live")

    def get_program(self, program_id: str):
        return self._programs.get(program_id)

    def create_program(self, config_dict: dict) -> dict:
        cfg = ProgramConfig.from_dict(config_dict)
        rt = sg.ProgramRuntimeState(trading_day=clock.today().isoformat())
        program = {"config": as_dict(cfg), "runtime": dataclasses.asdict(rt), "logs": [], "cycles": [], "archived": False,
                    "alert": None, "active_cycle_snapshot": None, "updated_at": now_iso()}
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
        # super_program_id has no edit-form UI yet (no Super Program entity exists) -- from_dict wipes
        # any field the incoming dict doesn't explicitly send, so without this an edit made through
        # today's form would silently clear a future Super Program's link back to its parent
        config_dict.setdefault("super_program_id", existing["config"].get("super_program_id"))
        cfg = ProgramConfig.from_dict(config_dict)
        existing["config"] = as_dict(cfg)
        existing["updated_at"] = now_iso()
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

    async def close_cycle(self, program_id: str) -> dict:
        """Closes the CURRENT cycle's open leg(s) at market and lets the
        Program continue running otherwise -- the explicit action for
        "I'm happy with this cycle's P&L, close it now" without stopping
        the Program the way Stop & Flatten does. Deliberately does NOT
        touch `runtime.status` or `active_cycle_id` here: once both legs
        actually reach a terminal state, the existing per-tick logic in
        _tick_one (which already detects "both legs of the active cycle
        are terminal" every 15s, regardless of WHY they closed) picks this
        up on its own and runs the normal _close_cycle wrap-up -- P&L,
        safeguard counters, cycle history -- exactly as it would for an
        automatic SL/target/time-exit close. A manually-closed cycle's
        P&L counts toward the daily total and consecutive-loss streak the
        same as any other cycle's does; there's no special-casing here for
        "the user chose to close this one," since profit or loss is a real
        result either way.

        request_close()'s own `reason` parameter already gives this a
        distinct, visible close_reason ("program_cycle_manual_close") on
        each leg's order record -- no order_manager.py changes needed for
        that traceability at all."""
        program = self._require(program_id)
        cycle_id = program["runtime"].get("active_cycle_id")
        if not cycle_id:
            raise ValueError("no active cycle to close")
        om = self._order_manager_for(program["config"])
        closed_any = False
        for o in om.get_orders_by_cycle(cycle_id):
            if o["status"] not in ("closed", "cancelled", "entry_rejected"):
                try:
                    await om.request_close(o["order_id"], reason="program_cycle_manual_close")
                    closed_any = True
                except Exception as e:
                    log.error("Manual cycle close failed for order %s: %s", o["order_id"], e)
                    failure_log.log_failure(category="program_cycle_manual_close_failed", order_id=o["order_id"],
                                             program_id=program_id, message=str(e))
        detail = ("Close requested on open leg(s) -- the Program will pick up the cycle wrap-up "
                  "(P&L, safeguard counters) once they resolve, same as any other close.") if closed_any \
            else "No open legs to close."
        self._log(program, f"Cycle {cycle_id} manually closed by user. {detail}")
        return program

    def _require(self, program_id: str) -> dict:
        program = self._programs.get(program_id)
        if not program:
            raise ValueError("Program not found")
        return program

    def _log(self, program: dict, msg: str):
        program.setdefault("logs", []).append({"ts": now_iso(), "msg": msg})
        program["logs"] = program["logs"][-200:]
        program["updated_at"] = now_iso()  # drives list_programs()'s recency sort -- covers every
                                             # state-changing action that already goes through _log
                                             # (halts, cycle start/close, stop/resume/flatten/archive/unarchive)
        store.save_program(program)

    def _set_program_alert(self, program: dict, message: str):
        """A persistent, visible banner on the Program's card -- e.g.
        "insufficient funds even after widening" -- distinct from a halt
        (`runtime.status`): the Program keeps running and retrying on its
        own on every subsequent tick, this is purely informational so it's
        never silently invisible. Mirrors order_manager.py's
        _set_warning/_clear_warning for the same reason."""
        program["alert"] = {"message": message, "since": now_iso()}
        store.save_program(program)

    def _clear_program_alert(self, program: dict):
        if program.get("alert") is not None:
            program["alert"] = None
            store.save_program(program)

    # -------------------------------------------------------- orchestration --

    async def tick(self):
        async with self._lock:
            now = clock.now()
            today_iso = now.date().isoformat()

            for program in self._programs.values():
                rt = sg.ProgramRuntimeState(**program["runtime"])
                sg.ensure_fresh_trading_day(rt, today_iso)
                program["runtime"] = dataclasses.asdict(rt)

            runtime_map = {pid: sg.ProgramRuntimeState(**p["runtime"]) for pid, p in self._programs.items()}

            # Mark-to-market: an open cycle's live unrealized P&L, per Program that has opted in
            # (SafeguardsConfig.mtm_aware) -- computed fresh every tick, never persisted, so the Risk
            # Group/Portfolio aggregate checks below see today's true exposure, not just what's already
            # realized. A Program that hasn't opted in is simply absent from this map (contributes 0 to
            # every aggregate below -- today's exact behavior). See program_safeguards.mtm_cycle_pnl.
            mtm_pnl_map: dict[str, float] = {}
            for pid, program in self._programs.items():
                cycle_id = program["runtime"].get("active_cycle_id")
                if cycle_id and program["config"].get("safeguards", {}).get("mtm_aware"):
                    om = self._order_manager_for(program["config"])
                    mtm_pnl_map[pid] = sg.mtm_cycle_pnl(om.get_orders_by_cycle(cycle_id))
                    # transient, in-memory display value only -- NOT persisted every tick (same reasoning
                    # as order["last_ltp"]: needless I/O for a value that changes constantly; the frontend
                    # already polls Program runtime periodically and picks this up straight off the live
                    # dict). A loose top-level key, not on `runtime`, for the same reason `alert`/
                    # `active_cycle_snapshot` already are: `runtime` round-trips through a strict dataclass
                    # every tick and would reject an unknown key.
                    program["mtm_pnl"] = mtm_pnl_map[pid]
                else:
                    program["mtm_pnl"] = None

            # ---- tier 1: Risk Group -- halt only THAT group's members ----
            risk_group_caps: dict[str, float] = {}
            for group_id, group in self._risk_groups.items():
                member_ids = {pid for pid, p in self._programs.items() if p["config"].get("risk_group_id") == group_id}
                if not member_ids:
                    continue
                member_caps = [self._programs[pid]["config"]["safeguards"]["daily_loss_amount"] for pid in member_ids]
                group_cap = sg.effective_group_daily_loss_cap(member_caps, group.get("daily_loss_amount_override"))
                risk_group_caps[group_id] = group_cap
                group_pnl = sum(runtime_map[pid].daily_realized_pnl + mtm_pnl_map.get(pid, 0.0) for pid in member_ids)
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
            portfolio_pnl = sum(rt.daily_realized_pnl for rt in runtime_map.values()) + sum(mtm_pnl_map.values())
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
                    await self._tick_one(program, now=now, portfolio_pnl=portfolio_pnl, portfolio_cap=portfolio_cap,
                                          mtm_pnl=mtm_pnl_map.get(program["config"]["program_id"], 0.0))
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

    async def _tick_one(self, program: dict, *, now: datetime, portfolio_pnl: float, portfolio_cap: float,
                         mtm_pnl: float = 0.0):
        cfg = program["config"]
        rt = program["runtime"]

        if rt["active_cycle_id"]:
            om = self._order_manager_for(cfg)
            legs = om.get_orders_by_cycle(rt["active_cycle_id"])
            terminal = ("closed", "cancelled", "entry_rejected")
            if len(legs) == 2 and all(o["status"] in terminal for o in legs):
                self._close_cycle(program, legs, now)
                return
            # Mark-to-market cap (opt-in, SafeguardsConfig.mtm_aware): every safeguard below this point
            # in the function only ever runs when NO cycle is active, so an open cycle bleeding
            # unrealized loss was previously invisible to every cap until it closed on its own -- this
            # is the one check in the whole safeguard stack that runs WHILE a cycle is still open, and
            # it deliberately does nothing to the open legs themselves (same "a hard stop never touches
            # currently-open legs" invariant every other halt in this app already follows) -- it only
            # ever stops the NEXT cycle from starting.
            if rt["status"] == sg.RUNNING and cfg.get("safeguards", {}).get("mtm_aware"):
                effective_pnl = rt["daily_realized_pnl"] + mtm_pnl
                daily_loss_amount = cfg["safeguards"]["daily_loss_amount"]
                if effective_pnl <= -abs(daily_loss_amount):
                    rt["status"] = sg.HALTED_DAILY_LOSS
                    self._log(program, f"HALTED (mark-to-market): today's P&L including this still-OPEN "
                                        f"cycle's live unrealized P&L is {effective_pnl:.2f}, past the daily "
                                        f"loss cap {daily_loss_amount:.2f} (realized so far: "
                                        f"{rt['daily_realized_pnl']:.2f}, this cycle's live P&L: {mtm_pnl:.2f}). "
                                        f"The open cycle's own SL/target/trailing keep running untouched -- "
                                        f"this only stops a NEW cycle from starting once this one closes. "
                                        f"Resume manually once reviewed.")
                    failure_log.log_failure(category="program_mtm_daily_loss_halt", order_id=None,
                                             program_id=cfg["program_id"],
                                             message=f"Program {cfg['name']}: mark-to-market daily loss cap "
                                                     f"reached (effective_pnl={effective_pnl:.2f})")
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
        cycle_record = {
            "cycle_number": cycle_number,
            "cycle_id": program["runtime"]["active_cycle_id"],
            "started_at": program["runtime"].get("active_cycle_started_at"),
            "closed_at": now_iso(),
            "pnl": round(cycle_pnl, 2),
            "pnl_unknown": any_unknown,
            "order_ids": [o["order_id"] for o in legs],
        }
        program["cycles"].append(cycle_record)
        program["cycles"] = program["cycles"][-500:]  # bounded, same reasoning as the 200-cap on logs

        # Consumed by the cycle factsheet write below -- captured once, at _start_new_cycle time, since
        # neither the config-as-it-was nor the widening outcome survive on their own until now (config
        # can be edited mid-cycle; the widen offset was otherwise just a local in _start_new_cycle). See
        # that assignment's own comment. Cleared here regardless of whether a factsheet write follows,
        # since a stale snapshot from a finished cycle must never be mistaken for an active one.
        cycle_snapshot = program.get("active_cycle_snapshot")
        program["active_cycle_snapshot"] = None
        factsheet.write_cycle_factsheet(program=program, cycle_record=cycle_record, legs=legs,
                                         snapshot=cycle_snapshot)  # never raises -- see its own docstring

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

    async def _size_legs(self, om, cfg: dict, ce, pe) -> tuple[dict | None, str | None]:
        """Determines both legs' quantities for a candidate CE/PE pair.
        Extracted from _start_new_cycle so it can be re-run per strike
        candidate during the capital-shortfall widening retry (see that
        method) -- same lots/capital sizing logic as before, unchanged.
        Returns (leg_qty, None) on success or (None, reason) on failure --
        never partial (both legs size, or neither does). Deliberately
        quiet (no logging/failure_log here) -- the caller decides what's
        worth logging once it knows whether every candidate failed, so a
        4-candidate widening retry doesn't spam 4x the log/failure entries
        for what's really one decision."""
        leg_qty = {}
        if cfg.get("sizing_mode") == "capital":
            capital = cfg.get("capital_per_leg") or 0
            for opt, leg_name in ((ce, "CE"), (pe, "PE")):
                leg_stream_symbol = f"{opt.exc_token}_NFO"
                leg_price = await om.fetch_live_price(leg_stream_symbol, timeout=5.0)
                if leg_price is None:
                    return None, f"no live price for {leg_name} leg ({opt.id}) to size by capital"
                effective_price = leg_price + CAPITAL_SIZING_SLIPPAGE_POINTS
                lots = int(capital / effective_price) // opt.lot if effective_price > 0 else 0
                if lots < 1:
                    return None, (f"capital {capital} too small for even 1 lot of {leg_name} "
                                   f"({opt.lot} qty/lot) at ~{leg_price} + slippage buffer")
                leg_qty[leg_name] = lots * opt.lot
        else:
            leg_qty["CE"] = ce.lot * cfg["lots_per_leg"]
            leg_qty["PE"] = pe.lot * cfg["lots_per_leg"]
        return leg_qty, None

    async def _check_buffered_margin(self, om, cfg: dict, ce, pe, leg_qty: dict) -> tuple[bool, str | None]:
        """LIVE-only basket-margin check (see the hasattr guard at the call
        site) with an explicit safety buffer on top of the broker's own
        zero-buffer number: requires available margin to cover
        requiredMargin * MARGIN_SAFETY_BUFFER, not just requiredMargin
        exactly -- the broker's own `shortfall` field is 0 the instant
        available == required exactly, which is too tight to reliably
        enter a live cycle.

        An API call that raises (network error/timeout) deliberately does
        NOT block the cycle -- same as before this change -- an extra
        check failing shouldn't become a new point of failure preventing
        an otherwise-valid trade, when the existing rejection-handling
        already covers that case at the broker's own place-order step.

        A call that SUCCEEDS but returns missing/malformed margin fields
        is a DIFFERENT failure mode from that (a real response that can't
        be trusted, not an absent one) -- gets its own warning rather than
        silently defaulting both sides to 0 and passing through as "fine"."""
        basket = [
            {"symId": ce.id, "qty": leg_qty["CE"], "side": "buy", "type": "market", "product": cfg["product"]},
            {"symId": pe.id, "qty": leg_qty["PE"], "side": "buy", "type": "market", "product": cfg["product"]},
        ]
        try:
            margin_resp = await om.client.get_basket_margin(basket)
        except Exception as e:
            log.warning("Margin pre-check failed for Program %s (proceeding anyway -- a failed "
                        "check itself shouldn't block an otherwise-valid trade): %s", cfg["name"], e)
            return True, None
        margin_data = margin_resp.get("d") or {}
        available = margin_data.get("availableMargin")
        required = margin_data.get("requiredMargin")
        if available is None or required is None:
            log.warning("Margin pre-check for Program %s returned an unexpected shape (missing "
                        "availableMargin/requiredMargin) -- proceeding anyway, but this response "
                        "couldn't actually be verified: %s", cfg["name"], margin_data)
            return True, None
        buffered_required = required * MARGIN_SAFETY_BUFFER
        if available < buffered_required:
            buffer_pct = int(round((MARGIN_SAFETY_BUFFER - 1) * 100))
            return False, (f"margin shortfall with {buffer_pct}% buffer -- available ~₹{available:.2f}, "
                            f"need ~₹{buffered_required:.2f} (₹{required:.2f} required + buffer)")
        return True, None

    # ---------------------------------------------------------- entry signals

    async def _fetch_entry_signal_snapshots(self, om, entry_cfg: dict, index_stream_symbol: str,
                                             ce_stream_symbol: str, pe_stream_symbol: str) -> dict:
        """Fetches everything entry_signals.evaluate_entry might need,
        CONCURRENTLY and with a short timeout. These are optional,
        best-effort inputs to an opt-in precondition, NOT essential-path
        data like the spot-price fetch above -- _tick_one calls are
        sequential across every Program (see tick()), so a slow or closed
        feed here must not stall every other Program's tick behind it."""
        timeout = ENTRY_SIGNAL_FETCH_TIMEOUT_SECONDS
        wants_vix = entry_cfg.get("max_vix") is not None or entry_cfg.get("max_vix_percentile") is not None
        wants_greeks = entry_cfg.get("max_iv_session_rank_pct") is not None

        vix_stream_symbol = None
        if wants_vix:
            vix_index = self.script_master.get_index("IDX_-15_NSE")  # India VIX -- filtered out of
                                                                        # list_indices() by avail_flag=N,
                                                                        # but directly fetchable by id
            if vix_index:
                vix_stream_symbol = f"{vix_index.exc_token}_NSE"

        fetches = {
            "index_snapshot": om.fetch_market_snapshot(index_stream_symbol, timeout=timeout),
            "ce_snapshot": om.fetch_market_snapshot(ce_stream_symbol, timeout=timeout),
            "pe_snapshot": om.fetch_market_snapshot(pe_stream_symbol, timeout=timeout),
        }
        if vix_stream_symbol:
            fetches["vix_ltp"] = om.fetch_live_price(vix_stream_symbol, timeout=timeout)
        if wants_greeks:
            fetches["ce_greeks"] = om.fetch_greeks_snapshot(ce_stream_symbol, timeout=timeout)
            fetches["pe_greeks"] = om.fetch_greeks_snapshot(pe_stream_symbol, timeout=timeout)

        keys = list(fetches.keys())
        results = await asyncio.gather(*fetches.values())
        out = dict(zip(keys, results))
        return {
            "index_snapshot": out.get("index_snapshot"), "ce_snapshot": out.get("ce_snapshot"),
            "pe_snapshot": out.get("pe_snapshot"), "vix_ltp": out.get("vix_ltp"),
            "ce_greeks": out.get("ce_greeks"), "pe_greeks": out.get("pe_greeks"),
        }

    def _maybe_update_daily_signal_snapshot(self, now: datetime, *, index_id: str | None = None,
                                             index_close: float | None = None, vix_ltp: float | None = None):
        """Read-modify-write, at most touching each field ONCE per calendar
        day -- VIX and a given index's price can each first arrive on
        different ticks, from different Programs (a NIFTY Program's gate
        check runs before any BANKNIFTY Program's does, or vice versa), so
        this can't be a simple write-once-if-file-absent like the single-
        scalar version this replaced. See config.SIGNAL_HISTORY_DIR's
        comment for why this is a small forward-only daily log, not a
        historical-data pipeline -- captured value is "first price
        observed this signal today," an approximation of a true close,
        acceptable because squeeze_gate/vix_percentile_gate are both
        rolling-window signals over weeks, where day-to-day noise in
        exactly which intraday moment got captured washes out."""
        if vix_ltp is None and (index_id is None or index_close is None):
            return
        date_str = now.date().isoformat()
        snap = store.load_signal_snapshot(date_str) or {"date": date_str, "vix_close_seen": None, "index_closes": {}}
        snap.setdefault("index_closes", {})  # old files (pre-squeeze-detector) lack this key entirely
        changed = False
        if vix_ltp is not None and snap.get("vix_close_seen") is None:
            snap["vix_close_seen"] = vix_ltp
            changed = True
        if index_id and index_close is not None and index_id not in snap["index_closes"]:
            snap["index_closes"][index_id] = index_close
            changed = True
        if changed:
            store.save_signal_snapshot(date_str, snap)

    def _index_close_history(self, index_id: str, days: int) -> list[float]:
        """This index's daily price history, chronological (oldest first)
        -- entry_signals.squeeze_gate's input. Reads the same daily files
        vix_percentile_gate's history comes from; a day with no recorded
        price for THIS index (e.g. before any Program on it ticked, or a
        day this Program didn't exist yet) is simply absent, not a gap
        filled with a guess."""
        snaps = store.list_recent_signal_snapshots(days=days)  # newest first
        closes = []
        for s in reversed(snaps):  # oldest first
            c = (s.get("index_closes") or {}).get(index_id)
            if c is not None:
                closes.append(c)
        return closes

    def _maybe_log_greeks_unverifiable(self, program: dict):
        """Logged once per Program per day (not every 15s tick) -- see
        _greeks_unverifiable_logged's own comment in __init__ for why this
        is in-memory only."""
        program_id = program["config"]["program_id"]
        today = clock.today().isoformat()
        if self._greeks_unverifiable_logged.get(program_id) == today:
            return
        self._greeks_unverifiable_logged[program_id] = today
        msg = ("Greeks/IV data did not arrive from the broker within the fetch timeout -- this account's "
               "entitlement for that channel is unconfirmed. IV-rank-dependent entry gates are failing "
               "open/closed per on_greeks_unverifiable until this resolves.")
        self._log(program, msg)
        failure_log.log_failure(category="program_greeks_unverifiable", order_id=None,
                                 program_id=program_id, message=f"Program {program['config']['name']}: {msg}")

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

        atm_pair = self.script_master.atm_pair(cfg["index_id"], expiry, spot)
        if not atm_pair:
            self._log(program, f"Can't start a cycle: no strike near spot {spot} has both a CE and a PE "
                                f"listed for expiry {expiry}.")
            return

        # Optional live-market precondition: does this moment actually favor entering a long straddle?
        # Checked ONCE here, on the ATM pair specifically -- before the widening/margin loop below, since
        # there's no reason to run that loop at all if the entry is going to be skipped anyway. See
        # entry_signals.py for what this can gate on and why every gate defaults to allowing (OFF unless
        # entry_signals.enabled=True, and each individual threshold is itself optional on top of that).
        entry_cfg = cfg.get("entry_signals") or {}
        if entry_cfg.get("enabled"):
            atm_ce, atm_pe = atm_pair
            ce_stream_symbol, pe_stream_symbol = f"{atm_ce.exc_token}_NFO", f"{atm_pe.exc_token}_NFO"
            snaps = await self._fetch_entry_signal_snapshots(om, entry_cfg, index_stream_symbol,
                                                               ce_stream_symbol, pe_stream_symbol)
            vix_history = []
            if entry_cfg.get("max_vix_percentile") is not None:
                min_days = entry_cfg.get("vix_percentile_min_days", 10)
                vix_history = [s["vix_close_seen"] for s in store.list_recent_signal_snapshots(days=min_days * 3)
                                if s.get("vix_close_seen") is not None]
            index_close_history = []
            if entry_cfg.get("max_squeeze_bandwidth_percentile") is not None:
                period = entry_cfg.get("squeeze_bollinger_period", 20)
                min_days = entry_cfg.get("squeeze_min_days", 10)
                # need `period` days just to compute ONE bandwidth reading, plus `min_days` more
                # readings' worth of history to rank today's against -- see squeeze_gate's own docstring
                index_close_history = self._index_close_history(cfg["index_id"], days=(period + min_days) * 2)
            index_price_now = snaps["index_snapshot"].get("ltp") if snaps["index_snapshot"] else None
            if index_price_now is not None:
                index_close_history = index_close_history + [index_price_now]  # today's own reading, not
                                                                                  # yet in signal_history
            self._maybe_update_daily_signal_snapshot(now, index_id=cfg["index_id"], index_close=index_price_now,
                                                       vix_ltp=snaps["vix_ltp"])

            allowed, reason, greeks_unverifiable = entry_signals.evaluate_entry(
                entry_cfg, index_snapshot=snaps["index_snapshot"], ce_snapshot=snaps["ce_snapshot"],
                pe_snapshot=snaps["pe_snapshot"], ce_greeks=snaps["ce_greeks"], pe_greeks=snaps["pe_greeks"],
                vix_ltp=snaps["vix_ltp"], vix_history=vix_history, index_close_history=index_close_history,
            )
            if greeks_unverifiable:
                self._maybe_log_greeks_unverifiable(program)
            if not allowed:
                self._set_program_alert(program, f"Entry signals blocked this cycle: {reason}. This "
                                                  f"Program will keep retrying automatically as conditions "
                                                  f"change.")
                self._log(program, f"Cycle start skipped -- entry signal gate: {reason}.")
                failure_log.log_failure(category="program_entry_signal_blocked", order_id=None,
                                         program_id=cfg["program_id"], message=f"Program {cfg['name']}: {reason}")
                return

        # Determine BOTH legs' quantities -- and, for LIVE Programs, verify
        # (with a 10% safety buffer) there's enough margin for BOTH legs
        # together -- BEFORE ever placing either. A half-placed straddle
        # would defeat the whole premise of the strategy, so nothing here
        # ever places one leg without the other.
        #
        # If the plain ATM straddle doesn't clear the buffered margin
        # check, retry progressively WIDENED into a strangle (CE at
        # ATM+N, PE at ATM-N strikes, N=1..STRANGLE_WIDEN_MAX_STEPS)
        # instead of just giving up -- widening symmetrically is what
        # reliably lowers combined premium/margin; shifting a shared
        # straddle strike doesn't (one leg gets more ITM as the other
        # gets more OTM, so the net effect on combined cost is ambiguous).
        # Paper Programs never widen and never margin-check at all
        # (PaperBrokerClient has no get_basket_margin -- paper mode
        # deliberately assumes capital is always available by design).
        is_live = hasattr(om.client, "get_basket_margin")
        offsets_to_try = range(0, STRANGLE_WIDEN_MAX_STEPS + 1) if is_live else (0,)
        chosen = None
        last_reason = None
        for offset in offsets_to_try:
            candidate_pair = self.script_master.strike_pair_at_offset(cfg["index_id"], expiry, spot, offset)
            if not candidate_pair:
                last_reason = f"no strike pair at offset {offset} strike(s) from ATM"
                continue
            c_ce, c_pe = candidate_pair
            leg_qty, size_reason = await self._size_legs(om, cfg, c_ce, c_pe)
            if leg_qty is None:
                last_reason = size_reason
                continue
            if is_live:
                ok, margin_reason = await self._check_buffered_margin(om, cfg, c_ce, c_pe, leg_qty)
                if not ok:
                    last_reason = margin_reason
                    continue
            chosen = (c_ce, c_pe, leg_qty, offset)
            break

        if chosen is None:
            atm_ce, atm_pe = atm_pair
            widen_bit = f" even after widening into a strangle up to {STRANGLE_WIDEN_MAX_STEPS} strikes" if is_live else ""
            self._set_program_alert(program, f"Insufficient funds to start a cycle -- ATM strike "
                                              f"{atm_ce.strike} failed{widen_bit}. Add capital; this Program "
                                              f"will keep retrying automatically. ({last_reason})")
            self._log(program, f"Can't start a cycle: {last_reason}. Reporting ATM strike {atm_ce.strike} "
                                f"-- {atm_ce.id} + {atm_pe.id} -- and skipping this cycle (will retry next tick).")
            failure_log.log_failure(category="program_capital_insufficient", order_id=None,
                                     program_id=cfg["program_id"], message=f"Program {cfg['name']}: {last_reason}")
            return

        ce, pe, leg_qty, offset = chosen
        self._clear_program_alert(program)

        cycle_id = uuid.uuid4().hex[:12]
        widen_note = f" (WIDENED to a strangle at offset {offset} -- the ATM straddle was insufficient)" if offset else ""
        self._log(program, f"Starting cycle {cycle_id}: spot {spot}, expiry {expiry}, "
                            f"strike {ce.strike}/{pe.strike} -- {ce.id} + {pe.id}{widen_note}.")

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
                "trail_check_interval_seconds": cfg.get("trail_check_interval_seconds", 0),
                "exit_confirmation_windows": cfg.get("exit_confirmation_windows", 1),
                "stop_breach_force_close_count": cfg.get("stop_breach_force_close_count", 0),
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
        # Captured HERE, not derived later at _close_cycle time, because neither piece of this
        # survives until then: update_program has no active-cycle guard, so cfg can be edited
        # mid-cycle (program["config"] would no longer reflect what was actually in effect when
        # this cycle started), and `offset` above is otherwise just a local that vanishes once this
        # function returns. This is the only point that knows both. Loose top-level key (sibling of
        # `alert`), not on `runtime`, for the same reason `alert` already is -- see its own comment.
        program["active_cycle_snapshot"] = {
            "program_config": copy.deepcopy(cfg),
            "spot": spot,
            "expiry": str(expiry),
            "ce_id": ce.id, "pe_id": pe.id,
            "ce_strike": ce.strike, "pe_strike": pe.strike,
            "leg_qty": leg_qty,
            "widen_offset": offset,
        }
        store.save_program(program)
