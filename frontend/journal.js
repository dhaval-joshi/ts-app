// Trading Journal -- a read-only view over the durable factsheets written by
// order_manager.py's _on_terminal and program_manager.py's _close_cycle (see
// backend/factsheet.py). Spans both OMS types, which is why this is its own
// top-level nav section rather than living inside Regular or Advanced OMS
// specifically -- same reasoning as the Portfolio section combining both.

let journalProgramCache = [];

async function loadJournalSection() {
  try {
    journalProgramCache = await api("/api/programs");
  } catch (e) {
    journalProgramCache = [];
  }
  renderJournalFilterForm();
  await runJournalFilter();
}

function renderJournalFilterForm() {
  const el = document.getElementById("journalFilterForm");
  if (!el) return;
  const programOptions = journalProgramCache
    .map((p) => `<option value="${p.config.program_id}">${p.config.name}</option>`)
    .join("");
  el.innerHTML = `
    <div>
      <label class="field-label">Program</label>
      <select id="journalProgramFilter" class="js-enhance-select field-input w-64">
        <option value="">All Programs (and Regular OMS)</option>
        ${programOptions}
      </select>
    </div>
    <button type="button" onclick="runJournalFilter()" class="relative inline-flex items-center h-[42px] px-4 rounded-[0.3rem] text-xs font-medium bg-blue-600 text-white shadow-sm hover:bg-blue-700">Filter</button>`;
  enhanceAllSelects(el);
}

function journalProgramName(id) {
  const p = journalProgramCache.find((x) => x.config.program_id === id);
  return p ? p.config.name : id;
}

async function runJournalFilter() {
  const programId = document.getElementById("journalProgramFilter")?.value;
  const listEl = document.getElementById("journalList");
  if (!listEl) return;
  listEl.innerHTML = `<div class="text-center text-slate-400 py-8 text-sm">Loading…</div>`;
  try {
    const params = new URLSearchParams();
    if (programId) params.set("program_id", programId);
    const entries = await api(`/api/journal?${params.toString()}`);
    listEl.innerHTML = entries.length
      ? entries.map(journalEntryHtml).join("")
      : `<div class="text-center text-slate-400 py-12">No journal entries yet -- they're written as cycles and orders close.</div>`;
  } catch (e) {
    listEl.innerHTML = `<div class="text-center text-red-600 py-8 text-sm">Failed to load: ${e.message}</div>`;
  }
}

function journalEntryHtml(e) {
  const isCycle = e.type === "cycle";
  const title = isCycle ? journalProgramName(e.program_id) : (e.strategy_name || "Regular OMS order");
  const subtitle = isCycle
    ? `Cycle #${e.cycle_number ?? "?"}${e.widened ? " · widened" : ""}`
    : (e.sym_id || "");
  const pnlText = typeof e.pnl === "number" ? signed(e.pnl) : (e.pnl_unknown ? "unknown" : "—");
  const pnlCls = typeof e.pnl === "number" ? pnlColorClass(e.pnl) : "text-slate-400";
  const modeClass = e.mode === "paper" ? "bg-amber-100 text-amber-800 border-amber-300" : "bg-slate-100 text-slate-600 border-slate-200";
  const onclick = isCycle
    ? `showJournalCycleDetail('${e.program_id}', '${e.id.split(":")[1]}')`
    : `showJournalOrderDetail('${e.id}')`;
  return `
  <button type="button" onclick="${onclick}" class="text-left bg-white border border-slate-200 rounded-xl shadow-sm p-4 hover:border-blue-300 flex items-center justify-between gap-3">
    <div class="min-w-0">
      <div class="flex items-center gap-2">
        <span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide border ${isCycle ? "adv-accent-bg-50 adv-accent-text-700 adv-accent-border-200" : "bg-blue-50 text-blue-700 border-blue-200"}">${isCycle ? "Advanced OMS" : "Regular OMS"}</span>
        <span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide border ${modeClass}">${e.mode || "?"}</span>
        ${e.amended ? `<span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide border bg-orange-50 text-orange-700 border-orange-200" title="This record has been corrected by broker reconciliation">amended</span>` : ""}
      </div>
      <div class="text-sm font-medium text-slate-900 mt-1 truncate">${title}</div>
      <div class="text-xs text-slate-400">${subtitle} · ${e.closed_at ? e.closed_at.replace("T", " ") : "—"}</div>
    </div>
    <div class="text-sm font-semibold ${pnlCls} shrink-0">${pnlText}</div>
  </button>`;
}

async function showJournalCycleDetail(programId, cycleId) {
  let fs;
  try {
    fs = await api(`/api/journal/cycle/${programId}/${cycleId}`);
  } catch (e) {
    toast("Couldn't load cycle detail: " + e.message, "error");
    return;
  }
  const summary = fs.cycle_summary || {};
  const selection = fs.cycle_selection || {};
  const rows = [
    ["Program", fs.program_name],
    ["Cycle #", summary.cycle_number],
    ["Mode", fs.mode],
    ["Broker", fs.broker_id || "—"],
    ["Started", (summary.started_at || "").replace("T", " ") || "—"],
    ["Closed", (summary.closed_at || "").replace("T", " ") || "—"],
    ["Spot at entry", fmt(selection.spot)],
    ["Expiry", selection.expiry || "—"],
    ["Strikes", `${fmt(selection.ce_strike)} CE / ${fmt(selection.pe_strike)} PE`],
    ["Widened", selection.widened ? `Yes (offset ${selection.widen_offset})` : "No"],
  ];
  const legsHtml = (fs.legs || []).map((leg) => `
    <div class="border border-slate-200 rounded-[0.3rem] p-3 mt-2">
      <div class="text-sm font-medium text-slate-900">${leg.program_leg || "?"} — ${leg.sym_id}</div>
      <div class="text-xs text-slate-400">Entry ${fmt(leg.entry && leg.entry.avg_price)} · Exit ${fmt(leg.pnl && leg.pnl.exit_avg_price)}</div>
      <div class="text-xs font-medium ${pnlColorClass((leg.pnl || {}).realized || 0)} mt-1">${typeof (leg.pnl || {}).realized === "number" ? signed(leg.pnl.realized) : "unknown"}</div>
    </div>`).join("");
  const amendmentsHtml = journalAmendmentsHtml(fs.amendments);

  document.getElementById("dialogTitle").textContent = `Cycle #${summary.cycle_number ?? "?"} — ${fs.program_name}`;
  document.getElementById("dialogBody").innerHTML = `
    <div class="flex justify-between py-2 border-b border-slate-200 text-sm">
      <div class="text-slate-400">Net P&amp;L</div>
      <div class="font-medium ${pnlColorClass(summary.pnl || 0)}">${typeof summary.pnl === "number" ? signed(summary.pnl) : "unknown"}</div>
    </div>
    ${rows.map(([k, v]) => `<div class="flex justify-between py-2 border-b border-slate-200 text-sm"><div class="text-slate-400">${k}</div><div class="font-medium text-slate-900 text-right">${v}</div></div>`).join("")}
    <div class="mt-3"><div class="text-xs text-slate-400 uppercase tracking-wide mb-1">Legs</div>${legsHtml}</div>
    ${amendmentsHtml}`;
  resetDialogScroll("dialogRoot");
  document.getElementById("dialogRoot").classList.add("show");
}

async function showJournalOrderDetail(orderId) {
  let fs;
  try {
    fs = await api(`/api/journal/order/${orderId}`);
  } catch (e) {
    toast("Couldn't load order detail: " + e.message, "error");
    return;
  }
  const o = fs.order || {};
  const rows = [
    ["Symbol", o.sym_id],
    ["Side / Qty", o.side ? `${o.side.toUpperCase()} ${o.qty}` : "—"],
    ["Status", o.status],
    ["Broker", fs.broker_id || "—"],
    ["Mode", fs.owner],
    ["Entry avg", fmt(o.entry && o.entry.avg_price)],
    ["Exit avg", fmt(o.pnl && o.pnl.exit_avg_price)],
  ];
  const pnl = (o.pnl || {}).realized;
  const amendmentsHtml = journalAmendmentsHtml(fs.amendments);

  document.getElementById("dialogTitle").textContent = o.label || orderId;
  document.getElementById("dialogBody").innerHTML = `
    <div class="flex justify-between py-2 border-b border-slate-200 text-sm">
      <div class="text-slate-400">Realized P&amp;L</div>
      <div class="font-medium ${pnlColorClass(pnl || 0)}">${typeof pnl === "number" ? signed(pnl) : "unknown"}</div>
    </div>
    ${rows.map(([k, v]) => `<div class="flex justify-between py-2 border-b border-slate-200 text-sm"><div class="text-slate-400">${k}</div><div class="font-medium text-slate-900 text-right">${v}</div></div>`).join("")}
    ${amendmentsHtml}`;
  resetDialogScroll("dialogRoot");
  document.getElementById("dialogRoot").classList.add("show");
}

function journalAmendmentsHtml(amendments) {
  if (!amendments || !amendments.length) return "";
  return `<div class="mt-3 rounded-xl border border-orange-200 bg-orange-50 p-3.5">
      <div class="text-orange-700 font-medium text-sm mb-1.5">Corrected by broker reconciliation</div>
      ${amendments.map((a) => `
        <div class="text-xs text-orange-700 mt-1.5 pt-1.5 border-t border-orange-100 first:border-t-0 first:pt-0">
          <div>${(a.at || "").replace("T", " ")} — ${a.reason}</div>
          ${(a.changes || []).map((c) => `<div class="text-orange-600">${c.path}: ${c.from} → ${c.to}</div>`).join("")}
        </div>`).join("")}
    </div>`;
}
