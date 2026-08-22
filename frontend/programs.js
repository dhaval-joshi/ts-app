let allPrograms = [];
let allRiskGroups = [];
let indicesCache = [];
let editingProgramId = null;
let editingRiskGroupId = null;

const PROGRAM_STATUS_LABEL = {
  running: "Running",
  stopped_by_user: "Stopped",
  halted_consecutive_loss: "Halted — consecutive losses",
  halted_daily_loss: "Halted — daily loss cap",
  halted_risk_group: "Halted — Risk Group cap",
  halted_portfolio: "Halted — portfolio cap",
};
const PROGRAM_STATUS_CLASS = {
  running: "adv-accent-bg-50 adv-accent-text-700 adv-accent-border-200",
  stopped_by_user: "bg-slate-100 text-slate-600 border-slate-200",
  halted_consecutive_loss: "bg-red-50 text-red-700 border-red-200",
  halted_daily_loss: "bg-red-50 text-red-700 border-red-200",
  halted_risk_group: "bg-red-50 text-red-700 border-red-200",
  halted_portfolio: "bg-red-50 text-red-700 border-red-200",
};
const HALT_STATUSES = ["halted_consecutive_loss", "halted_daily_loss", "halted_risk_group", "halted_portfolio"];

// ------------------------------------------------------------ tab entry

async function loadAdvancedOms() {
  try {
    const [programsList, riskGroups, sentinelGroups, indices, allOrders] = await Promise.all([
      api("/api/programs"),
      api("/api/risk-groups"),
      api("/api/sentinel-groups"),
      api("/api/indices"),
      api("/api/orders"),
    ]);
    allPrograms = programsList;
    allRiskGroups = riskGroups;
    allSentinelGroups = sentinelGroups;
    indicesCache = indices;

    const programOrders = allOrders.filter((o) => o.program_id);
    const programModeById = {};
    allPrograms.forEach((p) => { programModeById[p.config.program_id] = p.config.mode; });
    const liveOrders = programOrders.filter((o) => programModeById[o.program_id] !== "paper");
    const paperOrders = programOrders.filter((o) => programModeById[o.program_id] === "paper");

    const liveKpiEl = document.getElementById("advancedLiveKpis");
    if (liveKpiEl) liveKpiEl.innerHTML = kpiCardsHtml(computeSummary(liveOrders), { title: "Live Programs only" });
    const paperKpiEl = document.getElementById("advancedPaperKpis");
    if (paperKpiEl) paperKpiEl.innerHTML = kpiCardsHtml(computeSummary(paperOrders), { title: "Paper Programs only" });

    renderProgramsTab(allOrders);
    renderRiskGroupsTab();
    renderSentinelGroups();
    renderArchivedProgramsTab(allOrders);
  } catch (e) {
    toast("Couldn't load Advanced OMS: " + e.message, "error");
  }
}

function riskGroupName(id) {
  const g = allRiskGroups.find((x) => x.risk_group_id === id);
  return g ? g.name : "— none —";
}

function scheduleSummary(schedule) {
  if (!schedule) return "";
  if (schedule.continuous) {
    return `Continuous${schedule.inter_cycle_delay_seconds ? ` · ${schedule.inter_cycle_delay_seconds}s between cycles` : ""}`;
  }
  const dayBit = schedule.days === "expiry_day" ? "expiry day only" : "every day";
  const delayBit = schedule.inter_cycle_delay_seconds ? ` · ${schedule.inter_cycle_delay_seconds}s between cycles` : "";
  return `${schedule.start_time}–${schedule.end_time}, ${dayBit}${delayBit}`;
}

function renderProgramsTab(allOrders) {
  const el = document.getElementById("programList");
  if (!el) return;
  selectedProgramIds.clear(); // selection doesn't persist across a full data refresh, same as
                               // Regular OMS's own order bulk-select
  const active = allPrograms.filter((p) => !p.archived);
  el.innerHTML = active.length
    ? active.map((p) => programCardHtml(p, allOrders)).join("")
    : `<div class="text-center text-slate-400 py-12">No Programs yet -- click "New Program" to set one up.</div>`;
  updateProgramBulkBar();
}

function renderRiskGroupsTab() {
  const el = document.getElementById("riskGroupList");
  if (el) {
    el.innerHTML = allRiskGroups.length
      ? allRiskGroups.map(riskGroupRowHtml).join("")
      : `<div class="text-xs text-slate-400">No Risk Groups yet.</div>`;
  }
}

function renderArchivedProgramsTab(allOrders) {
  const el = document.getElementById("archivedProgramList");
  if (!el) return;
  const archived = allPrograms.filter((p) => p.archived);
  el.innerHTML = archived.length
    ? archived.map((p) => programCardHtml(p, allOrders, { archived: true })).join("")
    : `<div class="text-center text-slate-400 py-12">No archived Programs.</div>`;
}

function riskGroupRowHtml(g) {
  const memberCount = allPrograms.filter((p) => p.config.risk_group_id === g.risk_group_id).length;
  return `
  <div class="flex items-center justify-between border border-slate-200 rounded-[0.3rem] px-4 py-2.5">
    <div>
      <div class="text-sm font-medium text-slate-900">${g.name}</div>
      <div class="text-xs text-slate-400">${memberCount} Program(s) · cap: ${g.daily_loss_amount_override !== null && g.daily_loss_amount_override !== undefined ? "₹" + fmt(g.daily_loss_amount_override) + " (override)" : "sum of members' own caps"}</div>
    </div>
    <div class="flex items-center gap-2">
      <button type="button" onclick="editRiskGroup('${g.risk_group_id}')" class="relative inline-flex items-center justify-center w-8 h-8 rounded-[0.3rem] border border-slate-200 bg-white shadow-sm text-slate-400 adv-accent-hover-text-600"><span class="material-symbols-outlined !text-lg">edit</span></button>
      <button type="button" onclick="deleteRiskGroup('${g.risk_group_id}')" class="relative inline-flex items-center justify-center w-8 h-8 rounded-[0.3rem] border border-slate-200 bg-white shadow-sm text-slate-400 hover:text-red-600"><span class="material-symbols-outlined !text-lg">delete</span></button>
    </div>
  </div>`;
}

// ------------------------------------------------------------- program card

function activeLegsInnerHtml(activeLegs, status) {
  if (!activeLegs.length) {
    if (status === "running") {
      return `<div class="mt-3 flex items-center gap-2 px-3 py-2 bg-indigo-50 border border-indigo-100 rounded-lg">
                <div class="relative flex h-2 w-2">
                  <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                  <span class="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                </div>
                <span class="text-xs font-medium text-indigo-700">Waiting for indicator entry flag...</span>
              </div>`;
    }
    return `<div class="text-xs text-slate-400 mt-3">No active cycle right now.</div>`;
  }
  const activeCyclePnl = activeLegs.reduce((sum, o) => {
    const p = o.pnl && (o.pnl.realized ?? o.pnl.unrealized);
    return sum + (typeof p === "number" ? p : 0);
  }, 0);
  const activeCycleHasUnknown = activeLegs.some((o) => !o.pnl || (o.pnl.realized == null && o.pnl.unrealized == null));

  return `<div class="mt-3">
        <div class="flex items-center justify-between text-xs mb-1.5">
          <span class="text-slate-400">Current cycle P&amp;L (live)</span>
          <span class="font-semibold ${pnlColorClass(activeCyclePnl)}">${signed(activeCyclePnl)}${activeCycleHasUnknown ? " *" : ""}</span>
        </div>
        <div class="grid grid-cols-2 gap-3">
        ${activeLegs.map((o) => {
          const p = o.pnl && (o.pnl.realized ?? o.pnl.unrealized);
          const isRealized = o.pnl && o.pnl.realized != null;
          const isClosing = o.status === "closing";
          const pnlLabel = isRealized ? " (closed)" : isClosing ? " (at trigger, pending confirmation)" : " (live)";
          const pnlPct = !isRealized && o.pnl && typeof o.pnl.unrealized_pct === "number" ? ` (${signed(o.pnl.unrealized_pct)}%)` : "";
          const pnlText = typeof p === "number" ? `${signed(p)}${pnlPct}${pnlLabel}` : "unknown";
          const pnlCls = typeof p === "number" ? pnlColorClass(p) : "text-slate-400";
          
          let borderCls = "border-slate-200";
          let arrowHtml = "";
          if (o.status === "watching") {
              if (o.momentum_state === "Dark Green") { borderCls = "border-green-600"; arrowHtml = `<span class="text-green-600 font-bold ml-1">↑</span>`; }
              else if (o.momentum_state === "Light Green") { borderCls = "border-green-400"; arrowHtml = `<span class="text-green-400 font-bold ml-1">↓</span>`; }
              else if (o.momentum_state === "Amber") { borderCls = "border-amber-500"; arrowHtml = `<span class="text-amber-500 font-bold ml-1">↑</span>`; }
              else if (o.momentum_state === "Red") { borderCls = "border-red-600"; arrowHtml = `<span class="text-red-600 font-bold ml-1">↓</span>`; }
              else if (o.momentum_state === "Steady") { borderCls = "border-slate-300"; arrowHtml = `<span class="text-slate-400 font-bold ml-1">-</span>`; }
          }
          
          let prevDotHtml = "";
          if (o.status === "watching" && o.momentum_prev) {
              let dotColor = "bg-slate-300";
              if (o.momentum_prev === "Dark Green") dotColor = "bg-green-600";
              else if (o.momentum_prev === "Light Green") dotColor = "bg-green-400";
              else if (o.momentum_prev === "Amber") dotColor = "bg-amber-500";
              else if (o.momentum_prev === "Red") dotColor = "bg-red-600";
              prevDotHtml = `<div class="w-2 h-2 rounded-full ${dotColor} inline-block mr-1 opacity-75" title="Previous state: ${o.momentum_prev}"></div>`;
          }

          return `
          <div class="border ${borderCls} border-[2px] rounded-[0.3rem] p-2.5 transition-colors duration-300">
            <div class="text-xs font-medium text-slate-900 flex items-center justify-between">
                <span>${o.program_leg} — ${o.sym_id}</span>
                <div class="flex items-center">${prevDotHtml}</div>
            </div>
            <div class="text-xs text-slate-400">${STATUS_LABEL[o.status] || o.status} · entry ${fmt(o.entry.avg_price)}</div>
            ${o.status === "watching" && o.last_ltp != null ? `<div class="text-xs text-slate-400">Live price ${fmt(o.last_ltp)}${arrowHtml}</div>` : ""}
            <div class="text-xs font-medium ${pnlCls} mt-0.5">${pnlText}</div>
            <div class="text-[11px] text-slate-400 mt-1">SL ${fmt(o.stop && o.stop.current_trig_price)} · Target ${fmt(o.target && o.target.current_trig_price)} <span class="text-slate-300">(live, in-memory)</span></div>
            ${o.status === "watching" ? `<button type="button" onclick="closeLeg('${o.order_id}')" class="mt-1.5 text-[11px] text-red-600 hover:underline">Close this leg</button>` : ""}
          </div>`;
        }).join("")}
        </div>
        ${activeCycleHasUnknown ? `<div class="text-[11px] text-slate-400 mt-1">* at least one leg's P&amp;L isn't known yet (still resolving with the broker)</div>` : ""}
       </div>`;
}

function programCardHtml(program, allOrders, opts = {}) {
  const cfg = program.config;
  const rt = program.runtime;
  const index = indicesCache.find((i) => i.index_id === cfg.index_id);
  const statusClass = PROGRAM_STATUS_CLASS[rt.status] || "bg-slate-100 text-slate-600 border-slate-200";
  const isHalted = HALT_STATUSES.includes(rt.status);
  const isStopped = rt.status === "stopped_by_user";
  const activeLegs = rt.active_cycle_id ? allOrders.filter((o) => o.cycle_id === rt.active_cycle_id) : [];

  const haltBanner = isHalted
    ? `<div class="mt-3 rounded-xl border border-red-200 bg-red-50 p-3.5 shadow-sm">
         <div class="flex items-center gap-2 text-red-700 font-medium text-sm">
           <span class="material-symbols-outlined !text-lg">warning</span>${PROGRAM_STATUS_LABEL[rt.status]}
         </div>
         <div class="text-red-700 text-xs mt-1">
           ${rt.status === "halted_risk_group" ? `Its Risk Group ("${riskGroupName(cfg.risk_group_id)}") crossed its daily loss cap -- this Program's own numbers may still be fine, another Program in the same group pulled the group past it. ` : ""}
           No new cycle will start until you review and resume. Any currently-open legs (none right now, since a cycle only halts once both legs are closed) keep running their own SL/Target untouched.
         </div>
       </div>`
    : "";

  const cooldownNote = rt.cooldown_until && new Date(rt.cooldown_until) > new Date()
    ? `<div class="text-xs text-amber-600 mt-1">Cooling down until ${rt.cooldown_until.replace("T", " ")} (max cycles/day throttle).</div>`
    : "";

  // Non-halting alert (e.g. insufficient capital even after widening) -- the
  // Program keeps running/retrying on its own; this is purely informational
  // so it's never silently invisible. Amber, not red, since nothing is halted.
  const alertBanner = program.alert
    ? `<div class="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3.5 shadow-sm">
         <div class="flex items-center gap-2 text-amber-700 font-medium text-sm">
           <span class="material-symbols-outlined !text-lg">warning</span>Alert
         </div>
         <div class="text-amber-700 text-xs mt-1">${program.alert.message}
           <span class="text-amber-500">(since ${program.alert.since.replace("T", " ")})</span></div>
       </div>`
    : "";

  const activeLegsHtml = `<div id="active-legs-${cfg.program_id}">${activeLegsInnerHtml(activeLegs, rt.status)}</div>`;

  return `
  <div class="relative ${cfg.mode === "paper" ? "bg-amber-50 border-amber-200" : "bg-white border-slate-200"} border shadow-sm rounded-xl p-5">
    <input type="checkbox" class="program-checkbox absolute top-5 left-5 w-4 h-4" data-program-id="${cfg.program_id}" onchange="onProgramCheckboxChange(this)" />
    <div class="flex items-start justify-between gap-3 pl-6">
      <div>
        <div class="text-base font-medium text-slate-900">${cfg.name}</div>
        <div class="text-xs text-slate-400 mt-0.5">${index ? index.disp_name : cfg.index_id} · ${cfg.product} · ${cfg.lots_per_leg} lot(s)/leg · Risk Group: ${riskGroupName(cfg.risk_group_id)}</div>
        <div class="text-xs text-slate-400">${scheduleSummary(cfg.schedule)}</div>
      </div>
      <span class="shrink-0 flex items-center gap-1.5">
        ${opts.archived ? `<span class="inline-flex items-center h-6 px-3 rounded-[0.3rem] text-[11px] font-medium uppercase tracking-wide border shadow-sm bg-slate-100 text-slate-500 border-slate-200">Archived</span>` : ""}
        ${cfg.mode === "paper" ? `<span class="inline-flex items-center h-6 px-3 rounded-[0.3rem] text-[11px] font-medium uppercase tracking-wide border shadow-sm bg-amber-100 text-amber-800 border-amber-300">Paper</span>` : ""}
        <span class="inline-flex items-center h-6 px-3 rounded-[0.3rem] text-[11px] font-medium uppercase tracking-wide border shadow-sm ${statusClass}">${PROGRAM_STATUS_LABEL[rt.status] || rt.status}</span>
      </span>
    </div>

    ${haltBanner}
    ${alertBanner}
    ${cooldownNote}

    <div class="grid grid-cols-4 gap-3 mt-4">
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Cycles today</div><div class="text-sm font-medium text-slate-900">${rt.cycles_today}</div></div>
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Consecutive losses</div><div class="text-sm font-medium text-slate-900">${rt.consecutive_losses} / ${cfg.safeguards.consecutive_loss_limit}</div></div>
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Today's P&amp;L</div><div class="text-sm font-medium ${pnlColorClass(rt.daily_realized_pnl)}">${signed(rt.daily_realized_pnl)}</div></div>
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Daily loss cap</div><div class="text-sm font-medium text-slate-900">₹${fmt(cfg.safeguards.daily_loss_amount)}</div></div>
    </div>
    ${cfg.safeguards.mtm_aware && program.mtm_pnl != null ? `
    <div class="mt-2 text-xs text-slate-500">
      Including this open cycle's live P&amp;L: <span class="font-medium ${pnlColorClass(rt.daily_realized_pnl + program.mtm_pnl)}">${signed(rt.daily_realized_pnl + program.mtm_pnl)}</span>
      <span class="text-slate-300">(mark-to-market aware -- this Program halts if this crosses the daily loss cap, even before the cycle closes)</span>
    </div>` : ""}

    ${activeLegsHtml}

    ${cfg.entry_mode === "manual_single_leg" && !opts.archived ? `
    <div class="mt-4 pt-4 border-t border-slate-200">
      <div class="text-[11px] uppercase tracking-wide text-slate-400 mb-2 flex items-center justify-between">
        <span>Manual Entry (Single Leg)</span>
      </div>
      <div id="indicators-${cfg.program_id}" class="text-xs text-slate-500 mb-3 h-4 flex items-center">
        <span class="text-slate-300 italic">Loading indicators...</span>
      </div>
      <div class="flex gap-2">
        <button type="button" onclick="startManualLeg('${cfg.program_id}', 'CE')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-green-300 bg-green-50 text-green-700 shadow-sm hover:bg-green-100" ${rt.active_cycle_id || isHalted || isStopped ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : ''}>Enter CE</button>
        <button type="button" onclick="startManualLeg('${cfg.program_id}', 'PE')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-red-300 bg-red-50 text-red-700 shadow-sm hover:bg-red-100" ${rt.active_cycle_id || isHalted || isStopped ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : ''}>Enter PE</button>
      </div>
    </div>` : ""}

    <div class="mt-4 pt-4 border-t border-slate-200 flex flex-wrap items-center gap-2">
      ${opts.archived
        ? `<button type="button" onclick="unarchiveProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium adv-accent-bg-600 text-white shadow-sm"><span class="material-symbols-outlined !text-base">unarchive</span>Unarchive</button>`
        : `
      ${isHalted || isStopped
        ? `<button type="button" onclick="resumeProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium adv-accent-bg-600 text-white shadow-sm"><span class="material-symbols-outlined !text-base">play_arrow</span>Resume</button>`
        : `<button type="button" onclick="stopProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">pause</span>Stop</button>`}
      ${rt.active_cycle_id
        ? `<button type="button" onclick="closeCycle('${cfg.program_id}')" title="Close just this cycle's open leg(s); the Program keeps running and will start a new cycle normally" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">task_alt</span>Close Cycle</button>`
        : ""}
      <button type="button" onclick="flattenProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium bg-red-600 text-white shadow-sm hover:bg-red-700"><span class="material-symbols-outlined !text-base">stop_circle</span>Stop &amp; Flatten</button>
      <button type="button" onclick="archiveProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">archive</span>Archive</button>
      <button type="button" onclick="editProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">edit</span>Edit</button>
      <button type="button" onclick="cloneProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">content_copy</span>Clone</button>`
      }
      <button type="button" onclick="showProgramOrders('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">receipt_long</span>Orders</button>
      <button type="button" onclick="showProgramCycles('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50"><span class="material-symbols-outlined !text-base">history</span>Cycles (${(program.cycles || []).length})</button>
      <button type="button" onclick="runChronosBacktest('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-blue-300 bg-blue-50 text-blue-700 shadow-sm hover:bg-blue-100"><span class="material-symbols-outlined !text-base">science</span>Backtest (Chronos)</button>
      <button type="button" onclick="deleteProgram('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium text-red-600 shadow-sm hover:bg-red-50"><span class="material-symbols-outlined !text-base">delete</span>Delete</button>
      <button type="button" onclick="toggleProgramLogs('${cfg.program_id}')" class="relative inline-flex items-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium adv-accent-text-600 shadow-sm adv-accent-hover-bg-50">Logs (${(program.logs || []).length})</button>
    </div>
    <div class="logs hidden mt-3 pt-3 border-t border-slate-200 text-xs text-slate-400 max-h-36 overflow-y-auto scrollbars space-y-1" id="program-logs-${cfg.program_id}">
      ${(program.logs || []).slice().reverse().map((l) => `<div>${l.ts.replace("T", " ").replace(/\+\d{2}:\d{2}$/, "")} — ${l.msg}</div>`).join("")}
    </div>
  </div>`;
}

function toggleProgramLogs(id) {
  document.getElementById(`program-logs-${id}`).classList.toggle("hidden");
}

async function stopProgram(id) {
  if (!confirm("Stop this Program? Any active cycle finishes naturally (its legs keep their own SL/Target); no new cycle will start.")) return;
  try {
    await api(`/api/programs/${id}/stop`, { method: "POST" });
    toast("Program stopped.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to stop: " + e.message, "error");
  }
}

async function resumeProgram(id) {
  try {
    await api(`/api/programs/${id}/resume`, { method: "POST" });
    toast("Program resumed.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to resume: " + e.message, "error");
  }
}

async function startManualLeg(id, leg) {
  if (!confirm(`Are you sure you want to enter a ${leg} leg manually? This will utilize the full allocated capital/lots for this program.`)) return;
  try {
    await api(`/api/programs/${id}/manual-entry`, { method: "POST", body: JSON.stringify({ leg }) });
    toast(`Manual ${leg} entry started.`);
    loadAdvancedOms();
  } catch (e) {
    toast(`Failed to enter ${leg}: ` + e.message, "error");
  }
}

async function closeLeg(orderId) {
  // Market-only, deliberately -- unlike Regular OMS's own close controls
  // (which offer an optional limit price via a dedicated input on the full
  // order card), this compact leg card has no room for that and doesn't
  // need it: a single leg being closed early is exactly the kind of
  // "get me out now" action a limit price would work against.
  if (!confirm("Close this leg now at market? The Program keeps running -- once both legs of this cycle are closed, it wraps up and the Program moves on to its next cycle normally.")) return;
  try {
    await api(`/api/orders/${orderId}/close`, { method: "POST", body: JSON.stringify({}) });
    toast("Leg close requested.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to close leg: " + e.message, "error");
  }
}

async function closeCycle(id) {
  if (!confirm("Close this cycle's open leg(s) now at market? The Program keeps running and will start a new cycle normally once this one wraps up.")) return;
  try {
    await api(`/api/programs/${id}/close-cycle`, { method: "POST" });
    toast("Cycle close requested.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to close cycle: " + e.message, "error");
  }
}

async function flattenProgram(id) {
  if (!confirm("Stop this Program AND close any open legs right now at market? This can't be undone.")) return;
  try {
    await api(`/api/programs/${id}/flatten`, { method: "POST" });
    toast("Program stopped and flattened.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to flatten: " + e.message, "error");
  }
}

async function deleteProgram(id) {
  if (!confirm("Delete this Program? This only removes the Program itself -- its past orders/cycle history stay on disk.")) return;
  try {
    await api(`/api/programs/${id}`, { method: "DELETE" });
    toast("Program deleted.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to delete: " + e.message, "error");
  }
}

async function archiveProgram(id) {
  if (!confirm("Archive this Program? It will never start a new cycle until unarchived. If a cycle is currently open, it keeps running normally to close.")) return;
  try {
    await api(`/api/programs/${id}/archive`, { method: "POST" });
    toast("Program archived.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to archive: " + e.message, "error");
  }
}

async function unarchiveProgram(id) {
  try {
    await api(`/api/programs/${id}/unarchive`, { method: "POST" });
    toast("Program unarchived -- eligible to trade again.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to unarchive: " + e.message, "error");
  }
}

// ------------------------------------------------------------- create/edit

function closeProgramDialog() {
  document.getElementById("programDialogRoot").classList.remove("show");
}

// Preset choices for the timeframe-aggregation interval, shared by the select's
// option list and the reverse-mapping (stored seconds -> matching preset, or
// "custom") done when a form opens for editing. 0 means "Off -- every tick".
const TRAIL_INTERVAL_PRESETS = [
  { value: 0, label: "Off -- every tick (default)" },
  { value: 5, label: "5 seconds" },
  { value: 10, label: "10 seconds" },
  { value: 30, label: "30 seconds" },
  { value: 60, label: "1 minute" },
  { value: 180, label: "3 minutes" },
  { value: 300, label: "5 minutes" },
];

function trailIntervalFieldHtml(currentInterval, idPrefix) {
  // Reverse-maps a stored seconds value back to its matching preset option, or
  // falls through to "custom" pre-filled with that value if it doesn't match any
  // preset exactly (e.g. a stored 45 must show "Custom: 45", not silently snap
  // to "30" or "60").
  const isCustom = !TRAIL_INTERVAL_PRESETS.some((p) => p.value === currentInterval);
  const options = TRAIL_INTERVAL_PRESETS.map(
    (p) => `<option value="${p.value}" ${!isCustom && currentInterval === p.value ? "selected" : ""}>${p.label}</option>`
  ).join("");
  return `
    <div>
      <label class="field-label">Re-evaluation interval</label>
      <select name="trail_check_interval_preset" id="${idPrefix}TrailIntervalPreset" class="js-enhance-select field-input"
        onchange="document.getElementById('${idPrefix}TrailIntervalCustomField').classList.toggle('hidden', this.value !== 'custom')">
        ${options}
        <option value="custom" ${isCustom ? "selected" : ""}>Custom...</option>
      </select>
    </div>
    <div id="${idPrefix}TrailIntervalCustomField" class="${isCustom ? "" : "hidden"}">
      <label class="field-label">Custom interval (seconds)</label>
      <input name="trail_check_interval_custom" type="number" min="1" step="1" value="${isCustom ? currentInterval : ""}" class="field-input" />
    </div>`;
}

function readTrailIntervalFromForm(fd) {
  const preset = fd.get("trail_check_interval_preset");
  return preset === "custom" ? (num(fd.get("trail_check_interval_custom")) || 0) : (num(preset) || 0);
}

function programFormHtml(p) {
  const cfg = p ? p.config : {
    name: "", index_id: "", risk_group_id: null, product: "intraday", mode: "live", broker_id: "tradejini", entry_mode: "auto_pair",
    min_working_days_to_expiry: 2, lots_per_leg: 1, sizing_mode: "lots", capital_per_leg: null,
    stop: { offset_mode: "percent", trig_offset: 20, limit_offset: 0, trailing: { enabled: true, trail_by: 5, activation_offset: 0 } },
    target: { offset_mode: "percent", trig_offset: 40, limit_offset: 0, trailing: { enabled: true, trail_by: 10, activation_offset: 0 } },
    time_exit: { mode: "intraday_window", window_start: "15:10", window_end: "15:15", at: null },
    safeguards: { consecutive_loss_limit: 3, daily_loss_amount: 5000, max_cycles_per_day: 5, cooldown_minutes: 5, mtm_aware: false },
    schedule: { continuous: false, start_time: "09:15", end_time: "14:55", days: "all", inter_cycle_delay_seconds: 0 },
    trail_check_interval_seconds: 0, exit_confirmation_windows: 1, stop_breach_force_close_count: 0,
    entry_signals: { enabled: false, max_vix: null, max_oi_chng_pct: null, max_session_range_pct: null,
      max_vix_percentile: null, vix_percentile_min_days: 10, max_iv_session_rank_pct: null,
      require_ttm_squeeze: false, squeeze_bollinger_period: 20, squeeze_bollinger_std: 2.0, squeeze_keltner_mult: 1.5,
      squeeze_min_days: 10, on_greeks_unverifiable: "allow" },
  };
  const indexOptions = indicesCache.map((i) => `<option value="${i.index_id}" ${i.index_id === cfg.index_id ? "selected" : ""}>${i.disp_name}</option>`).join("");
  const riskGroupOptions = allRiskGroups.map((g) => `<option value="${g.risk_group_id}" ${g.risk_group_id === cfg.risk_group_id ? "selected" : ""}>${g.name}</option>`).join("");

  return `
  <form id="programForm" class="flex flex-col gap-5">
    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Identity</legend>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="field-label">Program name</label><input name="name" required value="${cfg.name}" placeholder="e.g. NIFTY ATM Straddle" class="field-input" /></div>
        <div><label class="field-label">Underlying index</label>
          <select name="index_id" required class="js-enhance-select field-input">
            <option value="">${indicesCache.length ? "— choose —" : "No indices loaded yet -- check the app is logged in"}</option>
            ${indexOptions}
          </select>
        </div>
        <div><label class="field-label">Risk Group</label>
          <select name="risk_group_id" required class="js-enhance-select field-input">
            <option value="">${allRiskGroups.length ? "— choose —" : "No Risk Groups yet -- create one below first"}</option>
            ${riskGroupOptions}
          </select>
        </div>
        <div><label class="field-label">Product</label>
          <select name="product" class="js-enhance-select field-input">
            <option value="intraday" ${cfg.product === "intraday" ? "selected" : ""}>Intraday</option>
            <option value="normal" ${cfg.product === "normal" ? "selected" : ""}>Normal</option>
            <option value="delivery" ${cfg.product === "delivery" ? "selected" : ""}>Delivery</option>
          </select>
        </div>
        <div><label class="field-label">Mode</label>
          <select name="mode" class="js-enhance-select field-input">
            <option value="live" ${cfg.mode === "live" ? "selected" : ""}>Live -- real orders</option>
            <option value="paper" ${cfg.mode === "paper" ? "selected" : ""}>Paper -- simulated, no real orders</option>
          </select>
        </div>
        <div><label class="field-label">Broker</label>
          <select name="broker_id" class="js-enhance-select field-input">
            <option value="tradejini" ${!cfg.broker_id || cfg.broker_id === "tradejini" ? "selected" : ""}>Tradejini</option>
          </select>
        </div>
        <div><label class="field-label">Entry Mode</label>
          <select name="entry_mode" class="js-enhance-select field-input" onchange="if(window.onEntryModeChange) window.onEntryModeChange(this)">
            <option value="auto_pair" ${!cfg.entry_mode || cfg.entry_mode === "auto_pair" ? "selected" : ""}>Auto-Pair (Straddle/Strangle)</option>
            <option value="manual_single_leg" ${cfg.entry_mode === "manual_single_leg" ? "selected" : ""}>Manual Single-Leg</option>
            <option value="signal_single_leg" ${cfg.entry_mode === "signal_single_leg" ? "selected" : ""}>Signal Single-Leg (Breakout)</option>
          </select>
        </div>
        <div id="orbDurationField" class="${cfg.entry_mode === 'signal_single_leg' ? '' : 'hidden'}">
          <label class="field-label">ORB Tracking Duration (mins)</label>
          <input name="orb_duration_minutes" type="number" min="1" step="1" value="${cfg.orb_duration_minutes || 15}" class="field-input" />
        </div>
        <div><label class="field-label">Min working days to expiry</label><input name="min_working_days_to_expiry" type="number" min="0" step="1" value="${cfg.min_working_days_to_expiry}" class="field-input" /></div>
      </div>
    </fieldset>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Sizing</legend>
      <p class="text-xs text-slate-400 mb-3">Capital sizing gives each leg the SAME rupee allocation (lot counts may differ between CE/PE, since their premiums differ) -- predictable risk on both sides regardless of which side is pricier that day, rather than strictly equal contract counts.</p>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="field-label">Sizing method</label>
          <select name="sizing_mode" id="programSizingMode" class="js-enhance-select field-input"
            onchange="document.getElementById('lotsSizingField').classList.toggle('hidden', this.value !== 'lots'); document.getElementById('capitalSizingField').classList.toggle('hidden', this.value !== 'capital');">
            <option value="lots" ${cfg.sizing_mode === "lots" ? "selected" : ""}>By number of lots (equal both legs)</option>
            <option value="capital" ${cfg.sizing_mode === "capital" ? "selected" : ""}>By capital allocation (equal capital, lots may differ)</option>
          </select>
        </div>
        <div id="lotsSizingField" class="${cfg.sizing_mode === "capital" ? "hidden" : ""}">
          <label class="field-label">Lots per leg</label><input name="lots_per_leg" type="number" min="1" step="1" value="${cfg.lots_per_leg}" class="field-input" />
        </div>
        <div id="capitalSizingField" class="${cfg.sizing_mode === "capital" ? "" : "hidden"}">
          <label class="field-label">Capital per leg (₹)</label><input name="capital_per_leg" type="number" min="1" step="any" value="${cfg.capital_per_leg || ""}" class="field-input" />
        </div>
      </div>
    </fieldset>

    <div class="grid md:grid-cols-2 gap-4">
      <fieldset class="border border-slate-200 rounded-xl p-4">
        <legend class="px-2 text-sm font-medium adv-accent-text-700">Stop loss (both legs)</legend>
        <div class="grid grid-cols-2 gap-3">
          <div class="col-span-2"><label class="field-label">Offset unit</label>
            <select name="stop_offset_mode" class="js-enhance-select field-input">
              <option value="percent" ${cfg.stop.offset_mode === "percent" ? "selected" : ""}>Percent (%)</option>
              <option value="points" ${cfg.stop.offset_mode === "points" ? "selected" : ""}>Points</option>
            </select>
          </div>
          <div><label class="field-label">Trigger offset</label><input name="stop_trig_offset" type="number" step="any" value="${cfg.stop.trig_offset}" class="field-input" /></div>
          <div><label class="field-label">Limit buffer</label><input name="stop_limit_offset" type="number" step="any" value="${cfg.stop.limit_offset}" class="field-input" /></div>
        </div>
        <label class="flex items-center gap-2 text-sm mt-3 text-slate-700"><input type="checkbox" name="stop_trailing_enabled" ${cfg.stop.trailing.enabled ? "checked" : ""} class="w-4 h-4" /> Enable trailing</label>
        <div class="grid grid-cols-2 gap-3 mt-2">
          <div><label class="field-label">Trail by</label><input name="stop_trail_by" type="number" step="any" value="${cfg.stop.trailing.trail_by}" class="field-input" /></div>
          <div><label class="field-label">Activation offset</label><input name="stop_activation_offset" type="number" step="any" value="${cfg.stop.trailing.activation_offset}" class="field-input" /></div>
        </div>
      </fieldset>

      <fieldset class="border border-slate-200 rounded-xl p-4">
        <legend class="px-2 text-sm font-medium adv-accent-text-700">Target (both legs)</legend>
        <div class="grid grid-cols-2 gap-3">
          <div class="col-span-2"><label class="field-label">Offset unit</label>
            <select name="target_offset_mode" class="js-enhance-select field-input">
              <option value="percent" ${cfg.target.offset_mode === "percent" ? "selected" : ""}>Percent (%)</option>
              <option value="points" ${cfg.target.offset_mode === "points" ? "selected" : ""}>Points</option>
            </select>
          </div>
          <div><label class="field-label">Trigger offset</label><input name="target_trig_offset" type="number" step="any" value="${cfg.target.trig_offset}" class="field-input" /></div>
          <div><label class="field-label">Limit buffer</label><input name="target_limit_offset" type="number" step="any" value="${cfg.target.limit_offset}" class="field-input" /></div>
        </div>
        <label class="flex items-center gap-2 text-sm mt-3 text-slate-700"><input type="checkbox" name="target_trailing_enabled" ${cfg.target.trailing.enabled ? "checked" : ""} class="w-4 h-4" /> Enable trailing</label>
        <div class="grid grid-cols-2 gap-3 mt-2">
          <div><label class="field-label">Trail by</label><input name="target_trail_by" type="number" step="any" value="${cfg.target.trailing.trail_by}" class="field-input" /></div>
          <div><label class="field-label">Activation offset</label><input name="target_activation_offset" type="number" step="any" value="${cfg.target.trailing.activation_offset}" class="field-input" /></div>
        </div>
      </fieldset>
    </div>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Each leg's own EOD safety net</legend>
      <p class="text-xs text-slate-400 mb-3">Separate from the cutoff below, which only governs starting a NEW cycle -- this closes a leg that's still open near end of day.</p>
      <div class="grid sm:grid-cols-3 gap-3">
        <div><label class="field-label">Mode</label>
          <select name="time_exit_mode" class="js-enhance-select field-input">
            <option value="none" ${cfg.time_exit.mode === "none" ? "selected" : ""}>No scheduled close</option>
            <option value="intraday_window" ${cfg.time_exit.mode === "intraday_window" ? "selected" : ""}>Intraday window</option>
          </select>
        </div>
        <div><label class="field-label">Window start</label><input name="window_start" value="${cfg.time_exit.window_start || "15:10"}" class="field-input" /></div>
        <div><label class="field-label">Window end</label><input name="window_end" value="${cfg.time_exit.window_end || "15:15"}" class="field-input" /></div>
      </div>
    </fieldset>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Safeguards</legend>
      <div class="grid sm:grid-cols-3 gap-3">
        <div><label class="field-label">Consecutive loss limit</label><input name="consecutive_loss_limit" type="number" min="1" step="1" value="${cfg.safeguards.consecutive_loss_limit}" class="field-input" /></div>
        <div><label class="field-label">Daily loss cap (₹)</label><input name="daily_loss_amount" type="number" min="1" step="any" value="${cfg.safeguards.daily_loss_amount}" class="field-input" /></div>
        <div><label class="field-label">Max cycles/day</label><input name="max_cycles_per_day" type="number" min="1" step="1" value="${cfg.safeguards.max_cycles_per_day}" class="field-input" /></div>
        <div><label class="field-label">Cooldown (minutes)</label><input name="cooldown_minutes" type="number" min="0" step="1" value="${cfg.safeguards.cooldown_minutes}" class="field-input" /></div>
      </div>
      <p class="text-xs text-slate-400 mt-2">Once past max cycles/day, every subsequent cycle that day waits the cooldown before starting (a standing slow-down, not a one-time pause). Consecutive-loss and daily-loss caps are hard stops -- they need a manual Resume.</p>
      <label class="flex items-center gap-2 text-sm mt-3 text-slate-700">
        <input type="checkbox" name="mtm_aware" ${cfg.safeguards.mtm_aware ? "checked" : ""} class="w-4 h-4" />
        Mark-to-market aware
      </label>
      <p class="text-xs text-slate-400 mt-1">Include this Program's currently-OPEN cycle's live unrealized P&amp;L in the daily loss cap above (and in its Risk Group's / the portfolio's aggregate), not just realized P&amp;L at cycle-close. Off by default -- an open cycle bleeding loss is otherwise invisible to every cap until it closes on its own. Halts only; never auto-closes the open cycle -- its own SL/target/trailing keep running untouched.</p>
    </fieldset>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Schedule</legend>
      <p class="text-xs text-slate-400 mb-3">WHEN this Program is even eligible to consider a new cycle -- a different question from Safeguards above (which decides whether it should STOP due to bad performance).</p>
      <label class="flex items-center gap-2 text-sm mb-3 text-slate-700">
        <input type="checkbox" name="schedule_continuous" id="scheduleContinuous" ${cfg.schedule.continuous ? "checked" : ""}
          onchange="document.getElementById('scheduleWindowFields').classList.toggle('opacity-40', this.checked); document.getElementById('scheduleWindowFields').classList.toggle('pointer-events-none', this.checked);"
          class="w-4 h-4" /> Continuous -- ignore the window below entirely, always eligible (e.g. a 24-hour Crypto Program)
      </label>
      <div id="scheduleWindowFields" class="grid sm:grid-cols-2 gap-3 ${cfg.schedule.continuous ? "opacity-40 pointer-events-none" : ""}">
        <div><label class="field-label">Start time (HH:MM)</label><input name="schedule_start_time" value="${cfg.schedule.start_time}" class="field-input" /></div>
        <div><label class="field-label">End time (HH:MM)</label><input name="schedule_end_time" value="${cfg.schedule.end_time}" class="field-input" /></div>
        <div>
          <label class="field-label">Days</label>
          <select name="schedule_days" class="js-enhance-select field-input">
            <option value="all" ${cfg.schedule.days === "all" ? "selected" : ""}>All days</option>
            <option value="expiry_day" ${cfg.schedule.days === "expiry_day" ? "selected" : ""}>Expiry day only</option>
          </select>
        </div>
        <div><label class="field-label">Inter-cycle delay (seconds)</label><input name="schedule_inter_cycle_delay_seconds" type="number" min="0" step="1" value="${cfg.schedule.inter_cycle_delay_seconds}" class="field-input" /></div>
      </div>
      <p class="text-xs text-slate-400 mt-2">Inter-cycle delay: how long to wait after a cycle closes before considering a new one. 0 = immediate re-entry.</p>
    </fieldset>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Entry signals</legend>
      <p class="text-xs text-slate-400 mb-3">Optional live-market preconditions checked right before a cycle actually starts -- a different question from Schedule above (WHEN is a cycle eligible) and Safeguards (whether this Program should STOP due to bad performance): this asks whether conditions right now actually favor entering a long straddle. A blocked entry is non-halting -- the Program keeps retrying automatically as conditions change. Off by default; each threshold below is independently optional on top of the master switch.</p>
      <label class="flex items-center gap-2 text-sm mb-3 text-slate-700">
        <input type="checkbox" name="entry_signals_enabled" id="entrySignalsEnabled" ${cfg.entry_signals.enabled ? "checked" : ""}
          onchange="document.getElementById('entrySignalsFields').classList.toggle('opacity-40', !this.checked); document.getElementById('entrySignalsFields').classList.toggle('pointer-events-none', !this.checked);"
          class="w-4 h-4" /> Enable entry signal gates
      </label>
      <div id="entrySignalsFields" class="${cfg.entry_signals.enabled ? "" : "opacity-40 pointer-events-none"}">
        <div class="grid sm:grid-cols-2 gap-3">
          <div><label class="field-label">Max India VIX</label><input name="entry_signals_max_vix" type="number" min="0" step="any" value="${cfg.entry_signals.max_vix ?? ""}" placeholder="e.g. 18 -- blank = off" class="field-input" />
            <p class="text-[11px] text-slate-400 mt-1">Skip the cycle if India VIX is above this -- premium already expensive for a long-vol entry.</p></div>
          <div id="gate_max_oi"><label class="field-label">Max OI change (%)</label><input name="entry_signals_max_oi_chng_pct" type="number" min="0" step="any" value="${cfg.entry_signals.max_oi_chng_pct ?? ""}" placeholder="e.g. 50 -- blank = off" class="field-input" />
            <p class="text-[11px] text-slate-400 mt-1">Skip if either leg's Open Interest has already built up more than this % today.</p></div>
          <div id="gate_max_session_range"><label class="field-label">Max session range (% of open)</label><input name="entry_signals_max_session_range_pct" type="number" min="0" step="any" value="${cfg.entry_signals.max_session_range_pct ?? ""}" placeholder="e.g. 1.5 -- blank = off" class="field-input" />
            <p class="text-[11px] text-slate-400 mt-1">Skip once today's (high-low)/open on the underlying already exceeds this -- avoids entering AFTER the day's move already happened.</p></div>
          <div><label class="field-label">Max VIX percentile</label><input name="entry_signals_max_vix_percentile" type="number" min="0" max="100" step="any" value="${cfg.entry_signals.max_vix_percentile ?? ""}" placeholder="e.g. 40 -- blank = off" class="field-input" />
            <p class="text-[11px] text-slate-400 mt-1">Skip unless today's VIX ranks below this percentile of its own recent history (builds up automatically, a small daily snapshot -- needs the minimum days below before it applies).</p></div>
          <div><label class="field-label">VIX percentile: minimum days of history</label><input name="entry_signals_vix_percentile_min_days" type="number" min="1" step="1" value="${cfg.entry_signals.vix_percentile_min_days ?? 10}" class="field-input" /></div>
          <div><label class="field-label">Max IV session rank (%)</label><input name="entry_signals_max_iv_session_rank_pct" type="number" min="0" max="100" step="any" value="${cfg.entry_signals.max_iv_session_rank_pct ?? ""}" placeholder="e.g. 30 -- blank = off" class="field-input" />
            <p class="text-[11px] text-slate-400 mt-1">Skip unless a leg's live IV ranks below this % of TODAY's own IV range -- needs live Greeks from the broker; entitlement for that channel isn't guaranteed (see the option below).</p></div>
          <div>
            <label class="field-label" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
              <input name="entry_signals_require_ttm_squeeze" type="checkbox" ${cfg.entry_signals.require_ttm_squeeze ? 'checked' : ''} />
              Require TTM Squeeze (Bollinger in Keltner)
            </label>
          </div>
            <p class="text-[11px] text-slate-400 mt-1">Skip unless the underlying's Bollinger Band Width ranks below this percentile of its own recent sessions -- the real multi-day squeeze signal (price compressed over DAYS, not just today). Builds up automatically from the same daily history as the VIX percentile gate; needs ~(period + min days) trading days before it applies -- roughly 6 weeks at the defaults below.</p></div>
          <div><label class="field-label">Squeeze: Bollinger period (days)</label><input name="entry_signals_squeeze_bollinger_period" type="number" min="2" step="1" value="${cfg.entry_signals.squeeze_bollinger_period ?? 20}" class="field-input" /></div>
          <div><label class="field-label">Squeeze: Bollinger std deviations</label><input name="entry_signals_squeeze_bollinger_std" type="number" min="0.1" step="any" value="${cfg.entry_signals.squeeze_bollinger_std ?? 2.0}" class="field-input" /></div>
          <div><label class="field-label">Squeeze: minimum days of history</label><input name="entry_signals_squeeze_min_days" type="number" min="1" step="1" value="${cfg.entry_signals.squeeze_min_days ?? 10}" class="field-input" /></div>
        </div>
        <div class="mt-3">
          <label class="field-label">If Greeks/IV never arrive (entitlement unconfirmed)</label>
          <select name="entry_signals_on_greeks_unverifiable" class="js-enhance-select field-input w-64">
            <option value="allow" ${cfg.entry_signals.on_greeks_unverifiable !== "skip" ? "selected" : ""}>Allow the cycle (fail open)</option>
            <option value="skip" ${cfg.entry_signals.on_greeks_unverifiable === "skip" ? "selected" : ""}>Skip the cycle (fail closed)</option>
          </select>
          <p class="text-[11px] text-slate-400 mt-1">Only matters if "Max IV session rank" above is set. Applies only when Greeks didn't arrive from the broker AT ALL within the fetch timeout -- not when they simply say conditions are fine.</p>
        </div>
      </div>
    </fieldset>

    <fieldset class="border border-slate-200 rounded-xl p-4">
      <legend class="px-2 text-sm font-medium adv-accent-text-700">Timeframe aggregation &amp; exit confirmation</legend>
      <p class="text-xs text-slate-400 mb-3">Reduces sensitivity to single-tick noise in a choppy market: trailing and the stop/target check re-evaluate on this cadence instead of every raw tick, using the MEDIAN price seen during the window rather than one noisy instant tick. This only changes the DECISION, never execution -- a confirmed trigger still fires a plain market order, same as always. Off (0) = react to every tick, today's exact behavior.</p>
      <div class="grid sm:grid-cols-2 gap-4">
        ${trailIntervalFieldHtml(cfg.trail_check_interval_seconds || 0, "program")}
      </div>
      <div class="mt-3">
        <label class="field-label">Exit confirmation (consecutive evaluations)</label>
        <input name="exit_confirmation_windows" type="number" min="1" step="1" value="${cfg.exit_confirmation_windows || 1}" class="field-input w-40" />
        <p class="text-xs text-slate-400 mt-1">A crossed stop/target must stay crossed for this many evaluations in a row before the close actually fires -- 1 = fire on the first crossing (default). Each evaluation is one raw tick if the interval above is Off, or one window close otherwise.</p>
      </div>
      <div class="mt-3">
        <label class="field-label">Force-close after N stop breaches (0 = off)</label>
        <input name="stop_breach_force_close_count" type="number" min="0" step="1" value="${cfg.stop_breach_force_close_count || 0}" class="field-input w-40" />
        <p class="text-xs text-slate-400 mt-1">If a leg's stop is hit and recovers (without closing) this many times, the NEXT hit force-closes immediately, skipping exit confirmation for that close -- for a stop that keeps getting tested and bouncing back rather than clearly holding. Stop side only.</p>
      </div>
    </fieldset>

    <div class="flex gap-3">
      <button type="button" onclick="submitProgramForm()" class="relative inline-flex items-center justify-center h-11 px-6 rounded-[0.3rem] text-sm font-medium adv-accent-bg-600 text-white shadow-sm">
        ${editingProgramId ? "Save changes" : "Create Program"}
      </button>
      <button type="button" onclick="closeProgramDialog()" class="relative inline-flex items-center justify-center h-11 px-6 rounded-[0.3rem] text-sm font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">
        Close
      </button>
    </div>
  </form>`;
}

function showNewProgramDialog() {
  editingProgramId = null;
  document.getElementById("programDialogTitle").textContent = "New Program";
  document.getElementById("programDialogSaveBtn").classList.remove("hidden");
  document.getElementById("programDialogBody").innerHTML = programFormHtml(null);
  enhanceAllSelects(document.getElementById("programDialogBody"));
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

function editProgram(id) {
  const p = allPrograms.find((x) => x.config.program_id === id);
  if (!p) return;
  editingProgramId = id;
  document.getElementById("programDialogTitle").textContent = `Edit: ${p.config.name}`;
  document.getElementById("programDialogSaveBtn").classList.remove("hidden");
  document.getElementById("programDialogBody").innerHTML = programFormHtml(p);
  enhanceAllSelects(document.getElementById("programDialogBody"));
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

async function submitProgramForm() {
  const form = document.getElementById("programForm");
  const fd = new FormData(form);
  const payload = {
    name: fd.get("name"),
    index_id: fd.get("index_id"),
    risk_group_id: fd.get("risk_group_id") || null,
    product: fd.get("product"),
    mode: fd.get("mode") || "live",
    broker_id: fd.get("broker_id") || "tradejini",
    entry_mode: fd.get("entry_mode") || "auto_pair",
    orb_duration_minutes: num(fd.get("orb_duration_minutes")) || 15,
    lots_per_leg: num(fd.get("lots_per_leg")) || 1,
    sizing_mode: fd.get("sizing_mode") || "lots",
    capital_per_leg: num(fd.get("capital_per_leg")),
    min_working_days_to_expiry: num(fd.get("min_working_days_to_expiry")) ?? 2,
    stop: {
      offset_mode: fd.get("stop_offset_mode"),
      trig_offset: num(fd.get("stop_trig_offset")) || 0,
      limit_offset: num(fd.get("stop_limit_offset")) || 0,
      trailing: {
        enabled: fd.get("stop_trailing_enabled") === "on",
        trail_by: num(fd.get("stop_trail_by")) || 0,
        activation_offset: num(fd.get("stop_activation_offset")) || 0,
      },
    },
    target: {
      offset_mode: fd.get("target_offset_mode"),
      trig_offset: num(fd.get("target_trig_offset")) || 0,
      limit_offset: num(fd.get("target_limit_offset")) || 0,
      trailing: {
        enabled: fd.get("target_trailing_enabled") === "on",
        trail_by: num(fd.get("target_trail_by")) || 0,
        activation_offset: num(fd.get("target_activation_offset")) || 0,
      },
    },
    time_exit: {
      mode: fd.get("time_exit_mode"),
      window_start: fd.get("window_start") || null,
      window_end: fd.get("window_end") || null,
      at: null,
    },
    safeguards: {
      consecutive_loss_limit: num(fd.get("consecutive_loss_limit")) || 3,
      daily_loss_amount: num(fd.get("daily_loss_amount")) || 5000,
      max_cycles_per_day: num(fd.get("max_cycles_per_day")) || 5,
      cooldown_minutes: num(fd.get("cooldown_minutes")) ?? 5,
      mtm_aware: fd.get("mtm_aware") === "on",
    },
    schedule: {
      continuous: fd.get("schedule_continuous") === "on",
      start_time: fd.get("schedule_start_time") || "09:15",
      end_time: fd.get("schedule_end_time") || "14:55",
      days: fd.get("schedule_days") || "all",
      inter_cycle_delay_seconds: num(fd.get("schedule_inter_cycle_delay_seconds")) ?? 0,
    },
    trail_check_interval_seconds: readTrailIntervalFromForm(fd),
    exit_confirmation_windows: num(fd.get("exit_confirmation_windows")) || 1,
    stop_breach_force_close_count: num(fd.get("stop_breach_force_close_count")) ?? 0,
    entry_signals: {
      enabled: fd.get("entry_signals_enabled") === "on",
      max_vix: num(fd.get("entry_signals_max_vix")),
      max_oi_chng_pct: num(fd.get("entry_signals_max_oi_chng_pct")),
      max_session_range_pct: num(fd.get("entry_signals_max_session_range_pct")),
      max_vix_percentile: num(fd.get("entry_signals_max_vix_percentile")),
      vix_percentile_min_days: num(fd.get("entry_signals_vix_percentile_min_days")) || 10,
      max_iv_session_rank_pct: num(fd.get("entry_signals_max_iv_session_rank_pct")),
      require_ttm_squeeze: fd.get("entry_signals_require_ttm_squeeze") === "on",
      squeeze_bollinger_period: num(fd.get("entry_signals_squeeze_bollinger_period")) || 20,
      squeeze_bollinger_std: num(fd.get("entry_signals_squeeze_bollinger_std")) || 2.0,
      squeeze_min_days: num(fd.get("entry_signals_squeeze_min_days")) || 10,
      on_greeks_unverifiable: fd.get("entry_signals_on_greeks_unverifiable") || "allow",
    },
  };
  try {
    if (editingProgramId) {
      await api(`/api/programs/${editingProgramId}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("Program updated.");
    } else {
      await api("/api/programs", { method: "POST", body: JSON.stringify(payload) });
      toast("Program created.");
    }
    closeProgramDialog();
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to save: " + e.message, "error");
  }
}

// Portfolio-wide safeguards moved to the Admin page (own tab there) --
// see admin.js's loadPortfolioSafeguardsForm/savePortfolioSafeguards.
// This is a genuine app-level setting spanning all Programs, which fits
// Admin's role better than sitting inside the Risk Groups tab here.


// -------------------------------------------------------------- risk groups
//
// Reuses the SAME programDialogRoot/programDialogTitle/programDialogBody as
// the Program create/edit dialog above -- only one dialog is ever open at a
// time, so there's no need for a second, near-identical set of dialog
// markup just for this.

function riskGroupFormHtml(g) {
  const name = g ? g.name : "";
  const override = g && g.daily_loss_amount_override !== null && g.daily_loss_amount_override !== undefined
    ? g.daily_loss_amount_override
    : "";
  return `
  <form id="riskGroupForm" class="flex flex-col gap-4">
    <div><label class="field-label">Risk Group name</label><input name="name" required value="${name}" placeholder="e.g. Stock F&amp;O" class="field-input" /></div>
    <div><label class="field-label">Daily loss cap override (₹) -- blank = sum of member Programs' own caps</label><input name="daily_loss_amount_override" type="number" step="any" min="1" value="${override}" class="field-input" /></div>
    <div class="flex gap-3">
      <button type="button" onclick="submitRiskGroupForm()" class="relative inline-flex items-center justify-center h-11 px-6 rounded-[0.3rem] text-sm font-medium adv-accent-bg-600 text-white shadow-sm">
        ${g ? "Save changes" : "Create Risk Group"}
      </button>
      <button type="button" onclick="closeProgramDialog()" class="relative inline-flex items-center justify-center h-11 px-6 rounded-[0.3rem] text-sm font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">
        Close
      </button>
    </div>
  </form>`;
}

function showNewRiskGroupDialog() {
  editingRiskGroupId = null;
  document.getElementById("programDialogTitle").textContent = "New Risk Group";
  document.getElementById("programDialogSaveBtn").classList.remove("hidden");
  document.getElementById("programDialogBody").innerHTML = riskGroupFormHtml(null);
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

function editRiskGroup(id) {
  const g = allRiskGroups.find((x) => x.risk_group_id === id);
  if (!g) return;
  editingRiskGroupId = id;
  document.getElementById("programDialogTitle").textContent = `Edit Risk Group: ${g.name}`;
  document.getElementById("programDialogSaveBtn").classList.remove("hidden");
  document.getElementById("programDialogBody").innerHTML = riskGroupFormHtml(g);
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

async function submitRiskGroupForm() {
  const form = document.getElementById("riskGroupForm");
  const fd = new FormData(form);
  const override = fd.get("daily_loss_amount_override");
  const payload = {
    name: fd.get("name"),
    daily_loss_amount_override: override === "" ? null : Number(override),
  };
  try {
    if (editingRiskGroupId) {
      await api(`/api/risk-groups/${editingRiskGroupId}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("Risk Group updated.");
    } else {
      await api("/api/risk-groups", { method: "POST", body: JSON.stringify(payload) });
      toast("Risk Group created.");
    }
    closeProgramDialog();
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to save: " + e.message, "error");
  }
}

async function deleteRiskGroup(id) {
  if (!confirm("Delete this Risk Group? Only possible if no Programs currently belong to it.")) return;
  try {
    await api(`/api/risk-groups/${id}`, { method: "DELETE" });
    toast("Risk Group deleted.");
    loadAdvancedOms();
  } catch (e) {
    toast("Failed to delete: " + e.message, "error");
  }
}

// ------------------------------------------------------------ cycle history
//
// Deliberately on-demand, not always-visible -- fetches fresh orders each
// time rather than relying on a stale cache, same pattern as
// showTradeDetails(). Reuses the SAME programDialogRoot as the
// Program/Risk Group forms above (only one of these is ever open at once).

async function showProgramCycles(programId) {
  const program = allPrograms.find((p) => p.config.program_id === programId);
  if (!program) return;

  let allOrders;
  try {
    allOrders = await api("/api/orders");
  } catch (e) {
    toast("Couldn't load orders: " + e.message, "error");
    return;
  }

  const ordersByCycleId = {};
  for (const o of allOrders) {
    if (o.program_id !== programId) continue;
    (ordersByCycleId[o.cycle_id] = ordersByCycleId[o.cycle_id] || []).push(o);
  }

  const rt = program.runtime;
  const cycles = (program.cycles || []).slice().reverse(); // most recent first

  let activeSectionHtml = "";
  if (rt.active_cycle_id) {
    const activeOrders = ordersByCycleId[rt.active_cycle_id] || [];
    activeSectionHtml = `
      <div class="border adv-accent-border-200 adv-accent-bg-50 rounded-[0.3rem] p-3 mb-3">
        <div class="text-xs font-medium adv-accent-text-700 mb-2">Current cycle (in progress)</div>
        ${activeOrders.map(cycleOrderLineHtml).join("")}
      </div>`;
  }

  const historyHtml = cycles.length
    ? cycles.map((c) => {
        const orders = ordersByCycleId[c.cycle_id] || [];
        const pnlCls = c.pnl >= 0 ? "text-green-600" : "text-red-600";
        return `
          <div class="border border-slate-200 rounded-[0.3rem] p-3 mb-2">
            <div class="flex items-center justify-between mb-1">
              <div class="text-sm font-medium text-slate-900">Cycle #${c.cycle_number}</div>
              <div class="text-sm font-semibold ${pnlCls}">${signed(c.pnl)}${c.pnl_unknown ? " *" : ""}</div>
            </div>
            <div class="text-[11px] text-slate-400 mb-2">${(c.started_at || "").replace("T", " ")} → ${(c.closed_at || "").replace("T", " ")}${c.pnl_unknown ? " · * at least one leg's P&L was unknown" : ""}</div>
            ${orders.map(cycleOrderLineHtml).join("")}
          </div>`;
      }).join("")
    : `<div class="text-center text-slate-400 py-8 text-sm">No closed cycles yet.</div>`;

  document.getElementById("programDialogTitle").textContent = `Cycles: ${program.config.name}`;
  document.getElementById("programDialogSaveBtn").classList.add("hidden"); // read-only view, nothing to save
  document.getElementById("programDialogBody").innerHTML = `${activeSectionHtml}${historyHtml}`;
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

function cycleOrderLineHtml(o) {
  const pnl = o.pnl && o.pnl.realized;
  const pnlText = typeof pnl === "number" ? signed(pnl) : (o.status === "closed" ? "unknown" : STATUS_LABEL[o.status] || o.status);
  const pnlCls = typeof pnl === "number" ? pnlColorClass(pnl) : "text-slate-400";
  return `
    <div class="flex items-center justify-between py-1.5 border-t border-slate-100 first:border-t-0 text-xs">
      <div class="text-slate-700">${o.program_leg || "?"} — ${o.sym_id}</div>
      <div class="flex items-center gap-2">
        <span class="${pnlCls} font-medium">${pnlText}</span>
        <button type="button" onclick="closeProgramDialog(); showTradeDetails('${o.order_id}', false)" class="adv-accent-text-600 hover:underline">View →</button>
      </div>
    </div>`;
}

// Called from an order's Info panel (app.js's showTradeDetails) for a
// Program-tagged order -- switches to Advanced OMS and opens that
// Program's cycle history. Explicitly awaits a fresh load rather than
// assuming allPrograms is already populated, since this can be triggered
// before the person has ever visited the Advanced OMS tab this session.
async function jumpToProgramCycle(programId) {
  _closeModal();
  switchSection("advanced");
  await loadAdvancedOms();
  showProgramCycles(programId);
}

// ----------------------------------------------------------- Orders view

async function showProgramOrders(programId) {
  const program = allPrograms.find((p) => p.config.program_id === programId);
  if (!program) return;
  let allOrders;
  try {
    const [active, archived] = await Promise.all([api("/api/orders"), api("/api/orders-archived")]);
    archived.forEach((o) => (o.__archived = true));
    allOrders = active.concat(archived).filter((o) => o.program_id === programId);
  } catch (e) {
    toast("Couldn't load orders: " + e.message, "error");
    return;
  }
  document.getElementById("programDialogTitle").textContent = `Orders: ${program.config.name}`;
  document.getElementById("programDialogSaveBtn").classList.add("hidden"); // read-only view, nothing to save
  document.getElementById("programDialogBody").innerHTML = allOrders.length
    ? `<div class="flex flex-col gap-3">${allOrders
        .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
        .map((o) => renderOrderCard(o, { editable: false, isArchived: !!o.__archived }))
        .join("")}</div>`
    : `<div class="text-center text-slate-400 py-8 text-sm">No orders for this Program yet.</div>`;
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

// -------------------------------------------------------------- multi-select

let selectedProgramIds = new Set();

function onProgramCheckboxChange(el) {
  const id = el.dataset.programId;
  if (el.checked) selectedProgramIds.add(id);
  else selectedProgramIds.delete(id);
  updateProgramBulkBar();
}

function toggleSelectAllPrograms(checkbox) {
  document.querySelectorAll(".program-checkbox").forEach((el) => {
    el.checked = checkbox.checked;
    onProgramCheckboxChange(el);
  });
}

function updateProgramBulkBar() {
  const countEl = document.getElementById("selectedProgramCount");
  const btn = document.getElementById("archiveSelectedProgramsBtn");
  if (countEl) countEl.textContent = selectedProgramIds.size;
  if (btn) btn.disabled = selectedProgramIds.size === 0;
}

async function archiveSelectedPrograms() {
  if (!selectedProgramIds.size) return;
  if (!confirm(`Archive ${selectedProgramIds.size} selected Program(s)? Each will never start a new cycle until unarchived; any cycle currently open keeps running normally to close.`)) return;
  const ids = [...selectedProgramIds];
  let failCount = 0;
  for (const id of ids) {
    try {
      await api(`/api/programs/${id}/archive`, { method: "POST" });
    } catch (e) {
      failCount++;
    }
  }
  selectedProgramIds.clear();
  toast(failCount ? `Archived ${ids.length - failCount}, ${failCount} failed.` : `Archived ${ids.length} Program(s).`, failCount ? "error" : "ok");
  loadAdvancedOms();
}

// Called from the dialog header's top "Save" icon button -- this dialog
// is reused for the Program form, the Risk Group form, AND read-only
// views (Cycles, Orders), so this tries whichever form actually exists
// right now and is a harmless no-op when neither does (i.e. a read-only
// view is showing).
function submitCurrentProgramDialogForm() {
  // Deliberately calls the submit FUNCTIONS directly (same as the bottom
  // Save button's onclick) rather than form.requestSubmit() -- neither
  // programForm nor riskGroupForm has a "submit" event listener
  // (unlike order.js/strategies.js's forms, which do, and call
  // preventDefault() inside it), so requestSubmit() here would trigger
  // the browser's own native, unhandled form submission -- a real GET
  // request navigating away from the app entirely, landing back at "/".
  // That was the literal cause of "the tick icon takes me to the
  // homepage." Calling the function directly never goes anywhere near
  // that native submission path at all.
  if (document.getElementById("programForm")) {
    submitProgramForm();
  } else if (document.getElementById("riskGroupForm")) {
    submitRiskGroupForm();
  }
}

// ------------------------------------------------------------ clone program

function cloneProgram(id) {
  const p = allPrograms.find((x) => x.config.program_id === id);
  if (!p) return;
  editingProgramId = null; // creating a NEW program -- submitting this form must NOT overwrite the original
  const clonedConfig = { ...p.config, name: `Copy of ${p.config.name}` };
  delete clonedConfig.program_id; // this is a NEW entity -- must not carry the original's id forward
  document.getElementById("programDialogTitle").textContent = `Clone: ${p.config.name}`;
  document.getElementById("programDialogSaveBtn").classList.remove("hidden");
  document.getElementById("programDialogBody").innerHTML = programFormHtml({ config: clonedConfig });
  enhanceAllSelects(document.getElementById("programDialogBody"));
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
}

// ------------------------------------------------------- live card refresh
//
// Registered on the SAME shared websocket connection Regular OMS already
// uses (see app.js's onOrdersUpdate) -- Program cards used to have NO
// live-refresh mechanism at all: they only ever updated on navigating to
// Advanced OMS or after an explicit action, so a card watched continuously
// would go stale and just sit there. This targets ONLY each active-legs
// container (a stable, dedicated id per Program), leaving the rest of the
// card -- checkboxes, buttons, everything else -- untouched, so a
// selection mid-bulk-archive isn't wiped out by a push landing at the
// wrong moment. Program-level runtime state (status, cycles_today,
// daily_realized_pnl) isn't in this push at all -- see
// _periodicProgramRefreshLoop below for that.
onOrdersUpdate((orders) => {
  const section = document.getElementById("section-advanced");
  if (!section || section.classList.contains("hidden")) return;
  if (!allPrograms.length) return;

  for (const p of allPrograms) {
    const el = document.getElementById(`active-legs-${p.config.program_id}`);
    if (!el) continue; // this Program isn't currently rendered (e.g. archived tab is showing instead)
    const rt = p.runtime;
    const activeLegs = rt.active_cycle_id ? orders.filter((o) => o.cycle_id === rt.active_cycle_id) : [];
    el.innerHTML = activeLegsInnerHtml(activeLegs);
  }
});

// Safety net for the Program-level state the live push above can't cover
// (status, cycles_today, daily_realized_pnl, consecutive_losses -- none
// of that lives on an order) -- a full reload every 20s, only while
// Advanced OMS is actually the visible section, so nothing sits stale
// for more than a few seconds even without an explicit action.
setInterval(() => {
  const section = document.getElementById("section-advanced");
  if (section && !section.classList.contains("hidden")) loadAdvancedOms();
}, 20000);

window.onEntryModeChange = function(select) {
  const isSignal = select.value === "signal_single_leg";
  const orbField = document.getElementById("orbDurationField");
  if (orbField) orbField.classList.toggle("hidden", !isSignal);

  const maxOiGate = document.getElementById("gate_max_oi");
  if (maxOiGate) maxOiGate.classList.toggle("hidden", isSignal);

  const maxSessionRangeGate = document.getElementById("gate_max_session_range");
  if (maxSessionRangeGate) maxSessionRangeGate.classList.toggle("hidden", isSignal);

  if (isSignal) {
    const form = select.closest("form");
    if (form) {
      // Auto-populate prop-desk standards
      const bp = form.querySelector('[name="entry_signals_squeeze_bollinger_period"]');
      if (bp) bp.value = 20;
      
      const sqz = form.querySelector('[name="entry_signals_require_ttm_squeeze"]');
      if (sqz) sqz.checked = true;
      const vix = form.querySelector('[name="entry_signals_max_vix_percentile"]');
      if (vix) vix.value = 80;
      
      // We should check the "enable entry signals" checkbox if it exists
      const enableSignals = form.querySelector('[name="entry_signals_enabled"]');
      if (enableSignals && !enableSignals.checked) {
         enableSignals.checked = true;
         // Trigger change to show the entry signals section
         enableSignals.dispatchEvent(new Event('change'));
      }
    }
  }

}; // end window.onEntryModeChange

// ------------------------------------------------------------- chronos backtest --

window.runChronosBacktest = function(programId, isGroup = false, providedName = null) {
  let name = providedName;
  if (!name) {
      const p = allPrograms.find((x) => x.config.program_id === programId);
      name = p ? p.config.name : "Strategy";
  }

  document.getElementById("programDialogTitle").textContent = `Backtest: ${name}`;
  document.getElementById("programDialogSaveBtn").classList.add("hidden");
  
  const formHtml = `
    <div class="flex flex-col gap-4 text-sm text-slate-700">
      <p class="text-slate-500">Run a native Chronos simulation to evaluate strategy performance using historical market data.</p>
      
      <div id="backtestFormInputs" class="flex flex-col gap-4">
        <div>
          <label class="field-label">Days to backtest</label>
          <input type="number" id="chronosDays" value="5" min="1" class="field-input" />
        </div>
        <div>
          <label class="field-label">Starting Capital (₹)</label>
          <input type="number" id="chronosCapital" value="100000" min="1000" class="field-input" />
        </div>
        <button type="button" onclick="executeChronosBacktest('${programId}', ${isGroup})" class="relative inline-flex items-center justify-center gap-1.5 h-10 px-4 rounded-[0.3rem] text-sm font-medium bg-blue-600 text-white shadow-sm hover:bg-blue-700 mt-2">
          <span class="material-symbols-outlined !text-base">science</span> Run Simulation
        </button>
      </div>
      
      <div id="backtestLoading" class="hidden flex-col items-center justify-center py-10 gap-3">
         <span class="material-symbols-outlined animate-spin text-blue-500 !text-4xl">autorenew</span>
         <span class="text-blue-600 font-medium">Running simulation... this may take a while</span>
         <div id="backtestLiveStatus" class="mt-2 text-xs font-mono text-slate-500 text-center px-4 max-w-full truncate">Preparing data...</div>
      </div>
      
      <div id="backtestResults" class="hidden flex-col gap-4">
         <!-- Results injected here -->
      </div>
    </div>
  `;
  
  document.getElementById("programDialogBody").innerHTML = formHtml;
  resetDialogScroll("programDialogRoot");
  document.getElementById("programDialogRoot").classList.add("show");
};

window.executeChronosBacktest = async function(programId, isGroup = false) {
  const days = parseInt(document.getElementById("chronosDays").value, 10);
  const capital = parseFloat(document.getElementById("chronosCapital").value);
  
  if (isNaN(days) || days <= 0 || isNaN(capital) || capital <= 0) {
    toast("Invalid inputs", "error");
    return;
  }
  
  document.getElementById("backtestFormInputs").classList.add("hidden");
  document.getElementById("backtestLoading").classList.remove("hidden");
  document.getElementById("backtestResults").classList.add("hidden");
  
  const statusEl = document.getElementById("backtestLiveStatus");
  if (statusEl) statusEl.innerText = "Preparing data...";
  
  const onBacktestEvent = (e) => {
    const el = document.getElementById("backtestLiveStatus");
    if (el) el.innerText = e.detail.message;
  };
  window.addEventListener("ws_backtest_event", onBacktestEvent);
  
  try {
    const res = await api(`/api/backtest/run/${programId}`, {
      method: "POST",
      body: JSON.stringify({ days, capital, is_group: isGroup })
    });
    
    document.getElementById("backtestLoading").classList.add("hidden");
    document.getElementById("backtestResults").classList.remove("hidden");
    window.removeEventListener("ws_backtest_event", onBacktestEvent);
    
    const pnlClass = res.total_pnl >= 0 ? "text-emerald-600" : "text-rose-600";
    const pnlSign = res.total_pnl > 0 ? "+" : "";
    const pnlPercent = (res.total_pnl / capital) * 100;
    
    document.getElementById("backtestResults").innerHTML = `
      <div class="grid grid-cols-2 gap-3 mt-2">
        <div class="p-4 border border-slate-200 rounded-xl bg-slate-50 text-center shadow-sm">
           <div class="text-xs font-medium text-slate-500 mb-1">Trades Taken</div>
           <div class="text-2xl font-semibold text-slate-700">${res.trade_count}</div>
        </div>
        <div class="p-4 border border-slate-200 rounded-xl bg-slate-50 text-center shadow-sm">
           <div class="text-xs font-medium text-slate-500 mb-1">Net P&L</div>
           <div class="text-2xl font-semibold ${pnlClass}">
             ${pnlSign}₹${res.total_pnl.toFixed(2)} 
             <span class="text-sm opacity-80 ml-1">(${pnlSign}${pnlPercent.toFixed(2)}%)</span>
           </div>
        </div>
        <div class="col-span-2 p-5 border border-blue-100 rounded-xl bg-blue-50/50 text-center shadow-sm mt-1 flex flex-row items-center justify-center gap-8">
           <div>
               <div class="text-xs font-medium text-blue-500 mb-1">Simulated Duration</div>
               <div class="text-3xl font-bold text-blue-700">${days} Days</div>
           </div>
           <div class="border-l border-blue-200 h-10"></div>
           <div>
               <div class="text-xs font-medium text-blue-500 mb-1">Final Capital</div>
               <div class="text-3xl font-bold text-blue-700">₹${res.final_capital.toFixed(2)}</div>
           </div>
        </div>
      </div>
      <button type="button" onclick="hideProgramDialog()" class="relative inline-flex items-center justify-center gap-1.5 h-10 px-4 rounded-[0.3rem] text-sm font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50 w-full mt-2">Close</button>
    `;
    
  } catch (e) {
    document.getElementById("backtestLoading").classList.add("hidden");
    document.getElementById("backtestResults").classList.remove("hidden");
    window.removeEventListener("ws_backtest_event", onBacktestEvent);
    document.getElementById("backtestResults").innerHTML = `
      <div class="p-4 rounded-xl bg-red-50 text-red-700 border border-red-200 shadow-sm mt-2">
         <div class="font-semibold mb-2 flex items-center gap-2">
            <span class="material-symbols-outlined !text-lg">error</span> Simulation Failed
         </div>
         <div class="text-sm opacity-90">${e.message}</div>
      </div>
      <button type="button" onclick="hideProgramDialog()" class="relative inline-flex items-center justify-center gap-1.5 h-10 px-4 rounded-[0.3rem] text-sm font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50 w-full mt-4">Close</button>
    `;
  }
};

let allSentinelGroups = [];

function renderSentinelGroups() {
  const container = document.getElementById("sentinelGroupList");
  if (!container) return;

  if (allSentinelGroups.length === 0) {
    container.innerHTML = `<div class="text-sm text-slate-500 py-8 text-center bg-slate-50 rounded-xl border border-slate-200 border-dashed">No Sentinel Groups created yet.</div>`;
    return;
  }

  container.innerHTML = allSentinelGroups.map(sg => {
    const isActive = sg.is_active;
    const statusClass = isActive ? "bg-green-100 text-green-800 border-green-300" : "bg-slate-100 text-slate-600 border-slate-200";
    const statusLabel = isActive ? "Running" : "Stopped";
    
    // Group children stats
    const children = sg.children || [];
    let childrenHtml = "";
    if (children.length > 0) {
        childrenHtml = `<div class="mt-4 border-t border-slate-100 pt-4 flex flex-col gap-2">
            <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Managed Programs</div>
            ${children.map(child => {
                const rt = child.runtime;
                const pStatus = PROGRAM_STATUS_LABEL[rt.status] || rt.status;
                const activeCycle = rt.active_cycle_id ? `<span class="text-[10px] bg-amber-100 text-amber-800 px-1.5 rounded ml-2">Active Cycle</span>` : "";
                return `<div class="flex items-center justify-between text-sm py-2 px-3 bg-slate-50 border border-slate-100 rounded-lg">
                    <div>
                        <div class="font-medium text-slate-800">${child.config.name}</div>
                        <div class="text-[11px] text-slate-500 mt-0.5">Regime: ${child.config.target_regime} · ${pStatus} ${activeCycle}</div>
                    </div>
                    <button type="button" onclick="showProgramCycles('${child.config.program_id}')" class="text-xs text-blue-600 hover:underline">View Logs</button>
                </div>`;
            }).join("")}
        </div>`;
    }

    return `
    <div class="bg-white border border-slate-200 shadow-sm rounded-xl p-5 mb-2">
      <div class="flex items-start justify-between">
        <div>
          <div class="text-base font-medium text-slate-900">${sg.name}</div>
          <div class="text-xs text-slate-400 mt-0.5">Index: ${sg.index_id} · Capital/Leg: ${sg.capital_per_leg || 'N/A'} · Sizing: ${sg.sizing_mode}</div>
        </div>
        <span class="shrink-0 flex items-center gap-1.5">
          <span class="inline-flex items-center h-6 px-3 rounded-[0.3rem] text-[11px] font-medium uppercase tracking-wide border shadow-sm ${statusClass}">${statusLabel}</span>
        </span>
      </div>
      
      <div class="flex items-center gap-2 mt-4">
        ${!isActive ? `<button type="button" onclick="startSentinelGroup('${sg.sentinel_group_id}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium bg-green-600 text-white shadow-sm hover:bg-green-700">Start</button>` : ""}
        ${isActive ? `<button type="button" onclick="stopSentinelGroup('${sg.sentinel_group_id}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">Stop</button>` : ""}
        <button type="button" onclick="flattenSentinelGroup('${sg.sentinel_group_id}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium bg-amber-500 text-white shadow-sm hover:bg-amber-600" title="Stops the orchestrator and immediately flattens all active child programs">Flatten All</button>
        <button type="button" onclick="runChronosBacktest('${sg.sentinel_group_id}', true, '${sg.name}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium border border-blue-300 bg-blue-50 text-blue-700 shadow-sm hover:bg-blue-100">Backtest</button>
        <button type="button" onclick="editSentinelGroup('${sg.sentinel_group_id}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">Edit Config</button>
        <button type="button" onclick="deleteSentinelGroup('${sg.sentinel_group_id}')" class="h-8 px-3 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-red-600 shadow-sm hover:bg-slate-50 ml-auto">Delete</button>
      </div>
      
      ${childrenHtml}
    </div>
    `;
  }).join("");
}

async function startSentinelGroup(id) {
  if (!confirm("Start Sentinel Orchestrator for this group? It will automatically deploy the correct child program based on the current regime.")) return;
  try {
    await api("/api/sentinel-groups/" + id + "/start", { method: "POST" });
    toast("Sentinel Group started");
    loadAdvancedOms();
  } catch(e) { toast(e.message, "error"); }
}

async function stopSentinelGroup(id) {
  try {
    await api("/api/sentinel-groups/" + id + "/stop", { method: "POST" });
    toast("Sentinel Group stopped. Any open positions are NOT flattened automatically.");
    loadAdvancedOms();
  } catch(e) { toast(e.message, "error"); }
}

async function flattenSentinelGroup(id) {
  if (!confirm("Stop Orchestrator AND flatten all active child programs in this group?")) return;
  try {
    await api("/api/sentinel-groups/" + id + "/flatten", { method: "POST" });
    toast("Sentinel Group flattened.");
    loadAdvancedOms();
  } catch(e) { toast(e.message, "error"); }
}

async function deleteSentinelGroup(id) {
  if (!confirm("Delete this Sentinel Group AND its 3 child programs permanently?")) return;
  try {
    await api("/api/sentinel-groups/" + id, { method: "DELETE" });
    toast("Sentinel Group deleted.");
    loadAdvancedOms();
  } catch(e) { toast(e.message, "error"); }
}

function showNewSentinelGroupDialog() {
  document.getElementById("dialogTitle").innerText = "New Sentinel Group";
  document.getElementById("dialogBody").innerHTML = sentinelGroupFormHtml(null);
  resetDialogScroll("dialogRoot");
  document.getElementById("dialogRoot").classList.add("show");
  enhanceAllSelects(document.getElementById("dialogRoot"));
}

function editSentinelGroup(id) {
  const sg = allSentinelGroups.find(g => g.sentinel_group_id === id);
  if (!sg) return;
  document.getElementById("dialogTitle").innerText = "Edit Sentinel Group";
  document.getElementById("dialogBody").innerHTML = sentinelGroupFormHtml(sg);
  resetDialogScroll("dialogRoot");
  document.getElementById("dialogRoot").classList.add("show");
  enhanceAllSelects(document.getElementById("dialogRoot"));
}

function sentinelGroupFormHtml(sg) {
  sg = sg || {
    name: "",
    index_id: "IDX_NIFTY_NSE",
    sizing_mode: "capital",
    capital_per_leg: 100000
  };
  
  return `
  <form id="sentinelGroupForm" class="flex flex-col gap-5">
    <div class="bg-amber-50 border border-amber-200 text-amber-800 text-xs p-3 rounded-lg">
      Creating a Sentinel Group automatically generates 3 mutually exclusive child programs under the hood. The Orchestrator rotates active capital between them.
    </div>
    <div class="grid sm:grid-cols-2 gap-4">
      <div><label class="field-label">Group Name</label><input name="name" required placeholder="e.g. Nifty Sentinel" value="${sg.name}" class="field-input" /></div>
      <div><label class="field-label">Underlying Index</label>
        <select name="index_id" class="js-enhance-select field-input">
          ${indicesCache.map(i => `<option value="${i.index_id}" ${sg.index_id === i.index_id ? "selected" : ""}>${i.disp_name}</option>`).join("")}
        </select>
      </div>
      <div><label class="field-label">Sizing Mode</label>
        <select name="sizing_mode" class="js-enhance-select field-input">
          <option value="capital" ${sg.sizing_mode === "capital" ? "selected" : ""}>Capital per leg (₹)</option>
        </select>
      </div>
      <div><label class="field-label">Capital (₹)</label><input name="capital_per_leg" type="number" min="1000" step="any" value="${sg.capital_per_leg || 100000}" class="field-input" /></div>
    </div>
    
    <div class="flex justify-end gap-3 pt-4 border-t border-slate-200 mt-2">
      <button type="button" onclick="_closeModal()" class="h-10 px-4 rounded-[0.3rem] text-sm font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">Cancel</button>
      <button type="button" onclick="submitSentinelGroupForm('${sg.sentinel_group_id || ""}')" class="h-10 px-6 rounded-[0.3rem] text-sm font-medium adv-accent-bg-600 text-white shadow-sm">Save Macro-Program</button>
    </div>
  </form>
  `;
}

async function submitSentinelGroupForm(id) {
  const form = document.getElementById("sentinelGroupForm");
  if (!form.reportValidity()) return;
  const fd = new FormData(form);
  
  const payload = {
    name: fd.get("name"),
    index_id: fd.get("index_id"),
    sizing_mode: fd.get("sizing_mode"),
    capital_per_leg: parseFloat(fd.get("capital_per_leg"))
  };

  try {
    const url = id ? `/api/sentinel-groups/${id}` : "/api/sentinel-groups";
    const method = id ? "PUT" : "POST";
    await api(url, { method, body: JSON.stringify(payload) });
    _closeModal();
    loadAdvancedOms();
    toast("Sentinel Group saved successfully", "success");
  } catch(e) { 
    toast("Failed to save: " + e.message, "error"); 
  }
}
