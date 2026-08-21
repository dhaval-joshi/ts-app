async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) {
    // the session expired (or was never there) mid-use -- bounce to login
    // with a way back to whatever page/tab this was, rather than leaving
    // the UI silently broken with failed API calls everywhere
    const next = encodeURIComponent(location.pathname + location.search);
    location.href = `/login?next=${next}`;
    return new Promise(() => {}); // never resolves -- we're navigating away
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (e) { /* ignore -- redirecting regardless */ }
  location.href = "/login";
}

async function triggerKillSwitch() {
  if (!confirm("EMERGENCY KILL SWITCH\n\nThis will instantly halt all running programs and square-off all active orders in both live and paper modes.\n\nAre you sure you want to proceed?")) return;
  try {
    const res = await api("/api/kill-switch", { method: "POST" });
    toast(`Kill Switch activated. Halted ${res.halted_programs} programs and closing ${res.closing_orders} orders.`, "error");
    setTimeout(() => location.reload(), 2000);
  } catch (e) {
    toast("Failed to activate kill switch: " + e.message, "error");
  }
}

// -------------------------------------------------------- ul/li dropdown
//
// Progressively enhances every <select class="js-enhance-select"> into a
// ul/li widget, per an explicit request to not use native <select>/<option>
// styling. The real <select> stays in the DOM (visually hidden, not
// display:none, so it stays focusable/tabbable) and remains the actual
// source of truth -- every existing .value read, FormData collection, and
// addEventListener('change', ...) elsewhere in the app keeps working
// completely unchanged, since they all still operate on that real element.

function enhanceSelect(selectEl) {
  if (selectEl.dataset.enhanced) return;
  selectEl.dataset.enhanced = "1";

  const wrapper = document.createElement("div");
  wrapper.className = "relative";
  selectEl.parentNode.insertBefore(wrapper, selectEl);
  selectEl.classList.add("select-native-hidden");
  wrapper.appendChild(selectEl);

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className =
    "field-input flex items-center justify-between gap-2 cursor-pointer text-left";
  const label = document.createElement("span");
  label.textContent = selectEl.options[selectEl.selectedIndex]?.text || "";
  const chevron = document.createElement("span");
  chevron.className = "material-symbols-outlined !text-lg text-slate-400";
  chevron.textContent = "expand_more";
  trigger.appendChild(label);
  trigger.appendChild(chevron);
  wrapper.appendChild(trigger);

  const list = document.createElement("ul");
  list.className = "select-list hidden";
  function renderOptions() {
    list.innerHTML = "";
    Array.from(selectEl.options).forEach((opt) => {
      const li = document.createElement("li");
      li.textContent = opt.text;
      li.className = "select-option" + (opt.selected ? " active" : "");
      li.onclick = () => {
        selectEl.value = opt.value;
        label.textContent = opt.text;
        selectEl.dispatchEvent(new Event("change", { bubbles: true }));
        list.classList.add("hidden");
      };
      list.appendChild(li);
    });
  }
  renderOptions();
  wrapper.appendChild(list);

  trigger.onclick = (e) => {
    e.stopPropagation();
    document.querySelectorAll(".select-list").forEach((l) => {
      if (l !== list) l.classList.add("hidden");
    });
    list.classList.toggle("hidden");
  };
  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target)) list.classList.add("hidden");
  });

  // Existing code across this app sets a select's value the plain way --
  // `someSelect.value = "x"` -- in many places (reorder prefill, loading a
  // saved strategy/order into a form for editing). That's a PROPERTY
  // assignment, which a MutationObserver does not reliably catch (it
  // watches the DOM tree/attributes, not JS property writes). Intercepting
  // the actual `value` property setter is the only way to guarantee the
  // visible label stays correct for every one of those existing call
  // sites without having to go find and change each of them.
  const nativeValueDescriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  Object.defineProperty(selectEl, "value", {
    get() {
      return nativeValueDescriptor.get.call(selectEl);
    },
    set(v) {
      nativeValueDescriptor.set.call(selectEl, v);
      label.textContent = selectEl.options[selectEl.selectedIndex]?.text || "";
      renderOptions();
    },
    configurable: true,
  });

  // still keep the mutation/change listeners too, for the innerHTML-replace
  // case (loadStrategies() rebuilding <option> children wholesale) and for
  // completeness -- belt and suspenders, since both are cheap
  const observer = new MutationObserver(() => {
    label.textContent = selectEl.options[selectEl.selectedIndex]?.text || "";
    renderOptions();
  });
  observer.observe(selectEl, { attributes: true, childList: true, subtree: true });
  selectEl.addEventListener("change", () => {
    label.textContent = selectEl.options[selectEl.selectedIndex]?.text || "";
    renderOptions();
  });
}

function enhanceAllSelects(root = document) {
  root.querySelectorAll("select.js-enhance-select").forEach(enhanceSelect);
}
document.addEventListener("DOMContentLoaded", () => enhanceAllSelects());

// Resets a dialog's scroll position to the top -- without this, a dialog
// that was scrolled down before being closed reopens still scrolled down
// (the same DOM element persists across show/hide, scrollTop included).
// Call this at the start of every "open a dialog" function.
function resetDialogScroll(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const scrollable = root.querySelector(".overflow-y-auto");
  if (!scrollable) return;
  scrollable.scrollTop = 0;
  // Also deferred to AFTER the browser's next paint: setting scrollTop
  // immediately after innerHTML changes can land before the browser has
  // actually finished laying out the new content (a real, known timing
  // gap, not just theoretical), so the synchronous reset above can
  // silently no-op on longer content. The deferred one is the one that
  // reliably sticks.
  requestAnimationFrame(() => { scrollable.scrollTop = 0; });
}

function toast(msg, kind = "ok") {
  const el = document.getElementById("snackbar");
  if (!el) return;
  const textEl = el.querySelector("[data-snackbar-text]");
  if (textEl) textEl.textContent = msg;
  el.classList.remove("bg-slate-800", "bg-red-600");
  el.classList.add(kind === "error" ? "bg-red-600" : "bg-slate-800");
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 4000);
}

// Single shared websocket connection, one per page load -- multiple parts
// of the app (Regular OMS's own render, Advanced OMS's card refresh) each
// register a listener rather than each opening their own connection,
// which would just double up server-side connections for no benefit.
const _orderUpdateListeners = [];
function onOrdersUpdate(callback) {
  _orderUpdateListeners.push(callback);
}

function connectStatusSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "orders") {
      _orderUpdateListeners.forEach((cb) => cb(msg.orders));
    }
  };
  ws.onclose = () => setTimeout(connectStatusSocket, 3000);
  return ws;
}
connectStatusSocket(); // started once here (shared across every page that loads app.js) --
                        // dashboard.js/programs.js register listeners via onOrdersUpdate() above
                        // rather than each opening their own connection

const HEARTBEAT_ZONE_COLOR_CLASS = {
  green: "bg-green-500",
  yellow: "bg-amber-400",
  orange: "bg-orange-500",
  red: "bg-red-500",
};
const ALL_HEARTBEAT_COLOR_CLASSES = Object.values(HEARTBEAT_ZONE_COLOR_CLASS);

async function refreshServerStatus() {
  try {
    const s = await api("/api/status");
    const dot = document.getElementById("streamDot");
    if (dot) dot.classList.toggle("bg-green-500", s.stream_connected);
    if (dot) dot.classList.toggle("bg-red-500", !s.stream_connected);

    const hbDot = document.getElementById("heartbeatDot");
    const hbWrap = document.getElementById("heartbeatWrap");
    if (hbDot && hbWrap) {
      const hb = s.heartbeat || { zone: "green", internet_up: true, entities: [], checked_at: null };
      // a stuck orchestration loop is arguably the MOST severe possible
      // signal (you might think it's working when it silently isn't) --
      // shown as red regardless of what connectivity looks like
      const effectiveZone = s.tick_stale ? "red" : hb.zone;
      ALL_HEARTBEAT_COLOR_CLASSES.forEach((c) => hbDot.classList.remove(c));
      hbDot.classList.add(HEARTBEAT_ZONE_COLOR_CLASS[effectiveZone] || "bg-green-500");

      const entityLines = (hb.entities || []).map((e) => `${e.name}: ${e.connected ? "connected" : "DOWN"}`).join(", ");
      const tickLine = s.last_tick_at
        ? `Last Program tick: ${s.last_tick_at.replace("T", " ")}${s.tick_stale ? " -- STALE, orchestration loop may be stuck" : ""}`
        : "Programs haven't ticked yet";
      hbWrap.title = `Internet: ${hb.internet_up ? "up" : "DOWN"}${entityLines ? " · " + entityLines : ""}\n${tickLine}`;
    }
    _serverUnreachableCount = 0;
    hideServerDownWarning();
  } catch (e) {
    // A missed poll or two is normal (a slow network blip); this only
    // becomes a real signal after several IN A ROW. IMPORTANT limitation,
    // stated plainly rather than oversold: this can only warn if a
    // browser tab with this app is already open and watching -- if the
    // server process itself has stopped, there is no way for
    // client-side code running in a closed tab to alert anyone. This is
    // an in-tab safety net, not a phone/email alert.
    _serverUnreachableCount++;
    if (_serverUnreachableCount >= 3) showServerDownWarning();
  }
}
let _serverUnreachableCount = 0;

function showServerDownWarning() {
  const el = document.getElementById("serverDownWarning");
  if (el) el.classList.remove("hidden");
}

function hideServerDownWarning() {
  const el = document.getElementById("serverDownWarning");
  if (el) el.classList.add("hidden");
}

setInterval(refreshServerStatus, 5000);
refreshServerStatus();

// ------------------------------------------------------- shared formatting

function fmt(n) {
  if (n === null || n === undefined || n === "") return "—";
  const num = Number(n);
  return Number.isFinite(num) ? num.toFixed(2) : n;
}

function signed(n) {
  const num = Number(n);
  if (!Number.isFinite(num)) return fmt(n);
  return (num >= 0 ? "+" : "") + num.toFixed(2);
}

function pnlColorClass(n) {
  return Number(n) >= 0 ? "text-green-600" : "text-red-600";
}

// Shared small utility -- was duplicated identically in order.js and
// programs.js (both build form payloads with the same "blank means null,
// not 0" convention); consolidated here now that both load on the same
// page, so there's one definition instead of two copies that could
// silently drift out of sync if only one were ever edited.
function num(v) {
  return v === "" || v === null || v === undefined ? null : Number(v);
}

// Shared P&L/KPI computation -- used for the top-level combined KPIs
// (Regular + Advanced OMS together), the Regular OMS dashboard's own
// scoped view, and the Advanced OMS tab's own scoped view. Same
// definition everywhere: Day = today's realized (closed positions) + all
// currently-open unrealized; Overall = all-time realized + all currently-
// open unrealized.
function computeSummary(allOrders) {
  const todayStr = new Date().toDateString();
  let dayRealized = 0, overallRealized = 0, unrealizedTotal = 0, openCount = 0, liveCount = 0;
  let dayCapital = 0, overallCapital = 0;
  const pnlByDate = {};

  for (const o of allOrders) {
    const p = o.pnl || {};
    const ev = entryValue(o);
    if (o.status === "closed" && typeof p.realized === "number") {
      overallRealized += p.realized;
      overallCapital += ev;
      const dateKey = new Date(o.updated_at).toDateString();
      pnlByDate[dateKey] = (pnlByDate[dateKey] || 0) + p.realized;
      if (dateKey === todayStr) {
        dayRealized += p.realized;
        dayCapital += ev;
      }
    }
    if (o.status === "watching") {
      openCount++;
      overallCapital += ev;
      dayCapital += ev;
      if (typeof p.unrealized === "number") {
        unrealizedTotal += p.unrealized;
        liveCount++;
      }
    }
  }

  const dates = Object.values(pnlByDate);
  const day = dayRealized + unrealizedTotal;
  const overall = overallRealized + unrealizedTotal;

  return {
    day, overall, openCount, liveCount,
    dayPct: dayCapital ? (day / dayCapital) * 100 : null,
    overallPct: overallCapital ? (overall / overallCapital) * 100 : null,
    daysTraded: dates.length,
    daysProfit: dates.filter((v) => v > 0).length,
    daysLoss: dates.filter((v) => v < 0).length,
  };
}

function kpiCard(label, valueHtml, colorClass = "text-slate-900") {
  return `
    <div class="w-full sm:w-28 md:w-32 h-12 flex flex-col items-center justify-center text-center bg-white border border-slate-200 shadow-sm rounded-[0.3rem] px-2 py-1">
      <div class="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5 leading-tight">${label}</div>
      <div class="text-[12px] font-semibold ${colorClass} leading-snug break-words">${valueHtml}</div>
    </div>`;
}

function kpiCardsHtml(s, opts = {}) {
  const coverage =
    s.openCount > s.liveCount
      ? ` (${s.liveCount}/${s.openCount} open positions have a live price)`
      : "";
  const pctText = (v) => (v === null ? "" : ` (${signed(v)}%)`);
  const cardsPnLSummary = [
    kpiCard("Day P&amp;L", `${signed(s.day)}${pctText(s.dayPct)}`, pnlColorClass(s.day)),
    kpiCard("Overall P&amp;L", `${signed(s.overall)}${pctText(s.overallPct)}`, pnlColorClass(s.overall)),
  ];
  const cardsDaysSummary = [
    kpiCard("Days traded", s.daysTraded),
    kpiCard("Days in profit", s.daysProfit, "text-green-600"),
    kpiCard("Days in loss", s.daysLoss, "text-red-600"),
  ];
  return `
    <div>
      ${opts.title ? `<div class="text-sm font-medium text-slate-500 mb-2">${opts.title}</div>` : ""}
      <div class="flex flex-wrap">${cardsPnLSummary.join("")}</div>
      <div class="flex flex-wrap">${cardsDaysSummary.join("")}</div>
      <div class="text-xs text-slate-400 mt-2">Price-only, excludes brokerage/taxes.${coverage}</div>
    </div>`;
}

// ----------------------------------------------------- shared order card

const STATUS_LABEL = {
  entry_pending: "Entry pending",
  entry_rejected: "Entry rejected",
  position_open: "Position open",
  watching: "SL/Target live",
  closing: "Closing…",
  closed: "Closed",
  cancelled: "Cancelled",
};

const STATUS_CHIP_CLASS = {
  entry_pending: "bg-amber-50 text-amber-700 border-amber-200",
  closing: "bg-amber-50 text-amber-700 border-amber-200",
  position_open: "bg-blue-50 text-blue-700 border-blue-200",
  watching: "bg-blue-50 text-blue-700 border-blue-200",
  entry_rejected: "bg-red-50 text-red-700 border-red-200",
  cancelled: "bg-red-50 text-red-700 border-red-200",
};

const EXIT_MODE_LABEL = {
  both: null,
  sl_only: "Stop-loss only",
  target_only: "Target only",
  none: "No SL/Target — manual/time close only",
};

function legDisplay(leg) {
  if (leg.current_trig_price !== null && leg.current_trig_price !== undefined) {
    return fmt(leg.current_trig_price);
  }
  const unit = leg.offset_mode === "percent" ? "%" : "pts";
  return `${leg.trig_offset}${unit} (on fill)`;
}

function entryValue(o) {
  const price = o.entry && o.entry.avg_price;
  const qty = (o.entry && o.entry.fill_qty) || o.qty;
  return typeof price === "number" ? price * qty : 0;
}

function kv(label, valueHtml) {
  return `<div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${label}</div><div class="text-sm font-medium text-slate-900 flex items-center gap-1.5 flex-wrap">${valueHtml}</div></div>`;
}

function pnlCellHtml(o) {
  const p = o.pnl || {};
  if (o.status === "closed") {
    if (p.realized === null || p.realized === undefined) {
      return kv("Realized P&amp;L", `<span class="text-slate-400">unknown — check broker</span>`);
    }
    return kv("Realized P&amp;L", `<span class="${pnlColorClass(p.realized)}">${signed(p.realized)}</span>`);
  }
  if (o.status === "watching") {
    if (p.unrealized === null || p.unrealized === undefined) {
      return kv("Live P&amp;L", `<span class="text-slate-400">no live price${o.stream_symbol ? "" : " (no stream symbol)"}</span>`);
    }
    return kv("Live P&amp;L", `<span class="${pnlColorClass(p.unrealized)}">${signed(p.unrealized)} (${signed(p.unrealized_pct)}%)</span>`);
  }
  return "";
}

function buySellAmountsHtml(o) {
  const hasEntry = o.entry.avg_price !== null && o.entry.avg_price !== undefined;
  if (!hasEntry) return "";
  const entryAmt = entryValue(o);
  const exitAmt = o.pnl && typeof o.pnl.exit_avg_price === "number" ? o.pnl.exit_avg_price * o.qty : null;
  const buyAmt = o.side === "buy" ? entryAmt : exitAmt;
  const sellAmt = o.side === "buy" ? exitAmt : entryAmt;
  return kv("Buy amount", buyAmt !== null ? "₹" + fmt(buyAmt) : "—") + kv("Sell amount", sellAmt !== null ? "₹" + fmt(sellAmt) : "—");
}

function warningBannerHtml(o) {
  if (!o.warning) return "";
  const since = o.warning.since ? o.warning.since.replace("T", " ") : "";
  return `
    <div class="mt-3 rounded-xl border border-red-200 bg-red-50 p-3.5 shadow-sm">
      <div class="flex items-center gap-2 text-red-700 font-medium text-sm">
        <span class="material-symbols-outlined !text-lg">warning</span>Needs your attention
      </div>
      <div class="text-red-700 text-sm mt-1">${o.warning.message}</div>
      <div class="text-slate-400 text-xs mt-1.5">Since ${since}</div>
    </div>`;
}

function btnOutlined(label, onclick, opts = {}) {
  const disabled = opts.disabled ? "disabled" : "";
  const icon = opts.icon ? `<span class="material-symbols-outlined !text-base">${opts.icon}</span>` : "";
  const extraAttrs = opts.id ? `id="${opts.id}"` : "";
  const title = opts.title ? `title="${opts.title}"` : "";
  return `<button type="button" ${extraAttrs} ${title} onclick="${onclick}" ${disabled} class="relative inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none">${icon}${label}</button>`;
}

function btnFilled(label, onclick, opts = {}) {
  const disabled = opts.disabled ? "disabled" : "";
  const icon = opts.icon ? `<span class="material-symbols-outlined !text-base">${opts.icon}</span>` : "";
  const colorClasses = opts.danger ? "bg-red-600 text-white hover:bg-red-700" : "bg-blue-600 text-white hover:bg-blue-700";
  return `<button type="button" onclick="${onclick}" ${disabled} class="relative inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium shadow-sm ${colorClasses} disabled:opacity-40 disabled:pointer-events-none">${icon}${label}</button>`;
}

function btnText(label, onclick) {
  return `<button type="button" onclick="${onclick}" class="relative inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-[0.3rem] text-xs font-medium text-blue-600 shadow-sm hover:bg-blue-50">${label}</button>`;
}

/**
 * opts:
 *   editable            -- show Close controls (price/%/fetch/close button) and Re-enter
 *   showArchiveButton   -- show an Archive button (terminal orders on the dashboard)
 *   showUnarchiveButton -- show an Unarchive button (Archive page)
 *   showCheckbox        -- show a multi-select checkbox (terminal orders on the dashboard)
 *   checked             -- checkbox initial state
 *   isArchived          -- passed through to the Info panel for the correct filename/path
 */
function renderOrderCard(o, opts = {}) {
  const trailBits = [];
  if (o.stop.trailing.enabled && (o.exit_mode === "both" || o.exit_mode === "sl_only")) trailBits.push(`SL trailing ${o.stop.trailing.trail_by_display || ""}`);
  if (o.target.trailing.enabled && (o.exit_mode === "both" || o.exit_mode === "target_only")) trailBits.push(`Target trailing ${o.target.trailing.trail_by_display || ""}`);

  const timeExit =
    o.time_exit.mode === "intraday_window"
      ? `Close ${o.time_exit.window_start}–${o.time_exit.window_end}`
      : o.time_exit.mode === "datetime"
      ? `Close at ${o.time_exit.at}`
      : "No scheduled close";

  const canClose = opts.editable && !["closed", "cancelled", "entry_rejected", "closing"].includes(o.status);
  const isTerminal = ["closed", "cancelled", "entry_rejected"].includes(o.status);
  const hasLivePrice = o.status === "watching" && o.last_ltp !== null && o.last_ltp !== undefined;
  const hasEntryAvg = o.entry.avg_price !== null && o.entry.avg_price !== undefined;
  const hasExitAvg = o.pnl && o.pnl.exit_avg_price !== null && o.pnl.exit_avg_price !== undefined;
  const exitModeLabel = EXIT_MODE_LABEL[o.exit_mode];

  const chipClass = o.status === "closed" && typeof (o.pnl && o.pnl.realized) === "number"
    ? (o.pnl.realized >= 0 ? "bg-green-50 text-green-700 border-green-200" : "bg-red-50 text-red-700 border-red-200")
    : (STATUS_CHIP_CLASS[o.status] || "bg-slate-100 text-slate-600 border-slate-200");

  const closeControlsHtml = opts.editable
    ? `
    <div class="mt-4 pt-4 border-t border-slate-200 flex flex-col gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <input
          type="number" step="any" placeholder="Close @ price"
          id="closePrice-${o.order_id}"
          oninput="updateClosePricePreview('${o.order_id}', ${o.tick_size || 0.05})"
          ${canClose ? "" : "disabled"}
          class="field-input w-36 !py-2 !text-xs"
        />
        ${btnOutlined("Fetch price", `fetchClosePrice('${o.order_id}', '${o.stream_symbol || ""}', ${o.tick_size || 0.05})`, { icon: "refresh", disabled: !(canClose && o.stream_symbol), id: `fetchClosePrice-${o.order_id}`, title: o.stream_symbol ? "" : "No stream symbol set on this order" })}
        ${btnFilled("Close position", `closeOrder('${o.order_id}')`, { icon: "close", disabled: !canClose, danger: true })}
        ${btnOutlined("Re-enter", `reenterOrder('${o.order_id}')`, { icon: "restart_alt" })}
        ${opts.showArchiveButton && isTerminal ? btnOutlined("Archive", `archiveOrder('${o.order_id}')`, { icon: "archive" }) : ""}
        ${btnText(`Logs (${o.logs.length})`, `toggleLogs('${o.order_id}')`)}
      </div>
      ${
        canClose && hasEntryAvg
          ? `<div class="flex flex-wrap items-center gap-2">
               <input type="number" step="any" placeholder="% profit on entry" id="closePct-${o.order_id}" class="field-input w-40 !py-2 !text-xs" />
               ${btnOutlined("Set price from %", `applyPctProfit('${o.order_id}', '${o.side}', ${o.entry.avg_price}, ${o.tick_size || 0.05})`)}
             </div>`
          : ""
      }
      <div class="text-xs text-slate-400" id="closePricePreview-${o.order_id}">Leave the price blank to close at market, or enter one for a limit order.</div>
    </div>`
    : `
    <div class="mt-4 pt-4 border-t border-slate-200 flex flex-wrap items-center gap-2">
      ${opts.showUnarchiveButton ? btnOutlined("Unarchive", `unarchiveOrder('${o.order_id}')`, { icon: "unarchive" }) : ""}
      ${btnOutlined("Re-enter", `reenterOrder('${o.order_id}')`, { icon: "restart_alt" })}
      ${btnText(`Logs (${o.logs.length})`, `toggleLogs('${o.order_id}')`)}
    </div>`;

  let borderCls = "border-slate-200";
  let arrowHtml = "";
  if (o.status === "watching") {
      if (o.momentum_state === "Dark Green") { borderCls = "border-green-600 border-[2px]"; arrowHtml = `<span class="text-green-600 font-bold ml-1">↑</span>`; }
      else if (o.momentum_state === "Light Green") { borderCls = "border-green-400 border-[2px]"; arrowHtml = `<span class="text-green-400 font-bold ml-1">↓</span>`; }
      else if (o.momentum_state === "Amber") { borderCls = "border-amber-500 border-[2px]"; arrowHtml = `<span class="text-amber-500 font-bold ml-1">↑</span>`; }
      else if (o.momentum_state === "Red") { borderCls = "border-red-600 border-[2px]"; arrowHtml = `<span class="text-red-600 font-bold ml-1">↓</span>`; }
      else if (o.momentum_state === "Steady") { borderCls = "border-slate-300 border-[2px]"; arrowHtml = `<span class="text-slate-400 font-bold ml-1">-</span>`; }
  }
  
  let prevDotHtml = "";
  if (o.status === "watching" && o.momentum_prev) {
      let dotColor = "bg-slate-300";
      if (o.momentum_prev === "Dark Green") dotColor = "bg-green-600";
      else if (o.momentum_prev === "Light Green") dotColor = "bg-green-400";
      else if (o.momentum_prev === "Amber") dotColor = "bg-amber-500";
      else if (o.momentum_prev === "Red") dotColor = "bg-red-600";
      prevDotHtml = `<div class="w-2 h-2 rounded-full ${dotColor} inline-block mr-2 opacity-75" title="Previous state: ${o.momentum_prev}"></div>`;
  }

  return `
  <div class="relative bg-white border ${borderCls} rounded-xl shadow-sm p-5 pb-12 transition-colors duration-300 ${opts.showCheckbox && isTerminal ? "pl-11" : ""}" data-id="${o.order_id}">
    ${opts.showCheckbox && isTerminal ? `<input type="checkbox" class="order-card-checkbox absolute top-5 left-5 w-4 h-4" data-order-id="${o.order_id}" onchange="onCardCheckboxChange(this)" ${opts.checked ? "checked" : ""} />` : ""}
    <div class="flex items-start justify-between gap-3">
      <div>
        <div class="text-base font-medium text-slate-900 flex items-center">${prevDotHtml}${o.label} <span class="text-slate-400 font-normal ml-2">${o.side.toUpperCase()} ${o.qty}</span></div>
        <div class="text-xs text-slate-400 mt-0.5">${o.sym_id} · ${o.product} · strategy: ${o.strategy_name || "—"}</div>
        <div class="text-xs text-slate-400">${timeExit}${trailBits.length ? " · " + trailBits.join(", ") : ""}</div>
        ${exitModeLabel ? `<div class="text-xs text-amber-600">${exitModeLabel}</div>` : ""}
      </div>
      <span class="shrink-0 inline-flex items-center h-6 px-3 rounded-[0.3rem] text-[11px] font-medium uppercase tracking-wide border shadow-sm ${chipClass}">${STATUS_LABEL[o.status] || o.status}</span>
    </div>

    ${warningBannerHtml(o)}

    <div class="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-4 mt-4">
      ${hasLivePrice ? kv("Live price", `${fmt(o.last_ltp)}${arrowHtml}`) : ""}
      ${kv("Entry avg", `${fmt(o.entry.avg_price)}${hasEntryAvg ? `<button type="button" title="Copy" onclick="copyEntryPrice(${o.entry.avg_price})" class="relative inline-flex items-center justify-center w-6 h-6 rounded-[0.3rem] text-slate-400 hover:text-blue-600 hover:bg-blue-50"><span class="material-symbols-outlined !text-base">content_copy</span></button>` : ""}`)}
      ${kv("Exit avg", hasExitAvg ? fmt(o.pnl.exit_avg_price) : "—")}
      ${kv("Stop trigger", legDisplay(o.stop))}
      ${kv("Target trigger", legDisplay(o.target))}
      ${kv("Fill qty", fmt(o.entry.fill_qty))}
      ${buySellAmountsHtml(o)}
      ${pnlCellHtml(o)}
    </div>

    ${closeControlsHtml}
    <div class="logs hidden mt-3 pt-3 border-t border-slate-200 text-xs text-slate-400 max-h-36 overflow-y-auto scrollbars space-y-1" id="logs-${o.order_id}">
      ${o.logs
        .slice()
        .reverse()
        .map((l) => `<div>${l.ts.replace("T", " ").replace(/\+\d{2}:\d{2}$/, "")} — ${l.msg}</div>`)
        .join("")}
    </div>
    <button type="button" title="Trade details" onclick="showTradeDetails('${o.order_id}', ${opts.isArchived ? "true" : "false"})" class="absolute bottom-3 right-3 inline-flex items-center justify-center w-8 h-8 rounded-[0.3rem] border border-slate-200 bg-white shadow-sm text-slate-400 hover:text-blue-600">
      <span class="material-symbols-outlined !text-lg">info</span>
    </button>
  </div>`;
}

function toggleLogs(id) {
  document.getElementById(`logs-${id}`).classList.toggle("hidden");
}

// -------------------------------------------------------- trade details

function _fmtDetail(n) {
  if (n === null || n === undefined || n === "") return "—";
  const num = Number(n);
  return Number.isFinite(num) ? num.toFixed(2) : String(n);
}

function _closeModal() {
  const dlg = document.getElementById("dialogRoot");
  if (dlg) dlg.classList.remove("show");
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch (e2) {
      toast("Couldn't copy to clipboard.", "error");
      document.body.removeChild(ta);
      return;
    }
    document.body.removeChild(ta);
  }
  toast(label ? `${label} copied: ${text}` : `Copied: ${text}`);
}

async function showTradeDetails(orderId, isArchived) {
  let o;
  try {
    o = await api(`/api/orders/${orderId}`);
  } catch (e) {
    toast("Couldn't load trade details: " + e.message, "error");
    return;
  }

  const filename = isArchived ? `data/orders/archive/${o.order_id}.json` : `data/orders/${o.order_id}.json`;
  const pnl = o.pnl || {};
  const pnlValue = o.status === "closed" ? pnl.realized : pnl.unrealized;
  const pnlLabel = o.status === "closed" ? "Final P&L" : "Live P&L";
  const pnlClass = pnlValue === null || pnlValue === undefined ? "text-slate-400" : pnlColorClass(pnlValue);
  const pnlText = pnlValue === null || pnlValue === undefined ? "unknown" : (Number(pnlValue) >= 0 ? "+" : "") + Number(pnlValue).toFixed(2);

  const rows = [
    ["Symbol", o.sym_id],
    ["Side / Qty", `${o.side.toUpperCase()} ${o.qty}`],
    ["Product", o.product],
    ["Strategy", o.strategy_name || "—"],
    ["Status", o.status],
    ["Close reason", o.close_reason || "—"],
    ["Entry avg price", _fmtDetail(o.entry.avg_price)],
    ["Entry filled at", o.entry.filled_at ? o.entry.filled_at.replace("T", " ") : "—"],
    ["Exit avg price", _fmtDetail(pnl.exit_avg_price)],
    ["P&L source", pnl.source || "—"],
    ["SL/Target trail updates", o.trail_update_count ?? 0],
    ["SL/Target trail failures", o.trail_failure_count ?? 0],
    ["Created", o.created_at ? o.created_at.replace("T", " ") : "—"],
    ["Last updated", o.updated_at ? o.updated_at.replace("T", " ") : "—"],
  ];

  const programRow = o.program_id
    ? `<div class="flex justify-between py-2 border-b border-slate-200 text-sm">
         <div class="text-slate-400">Advanced OMS</div>
         <div class="font-medium text-right">
           <button type="button" onclick="jumpToProgramCycle('${o.program_id}')" class="adv-accent-text-600 hover:underline">
             ${o.program_leg || "?"} leg — view cycle →
           </button>
         </div>
       </div>`
    : "";

  const idRow = (label, value) => value
    ? `<div class="flex justify-between items-center py-2 border-b border-slate-200 text-sm">
         <div class="text-slate-400">${label}</div>
         <div class="font-mono text-xs text-slate-700 flex items-center gap-2">
           ${value}
           <button type="button" onclick="copyText('${value}', '${label}')" title="Copy" class="text-slate-400 hover:text-slate-700"><span class="material-symbols-outlined !text-base">content_copy</span></button>
         </div>
       </div>`
    : "";
  // Only App and Broker order ids -- Tradejini's API doesn't appear to
  // expose a separate exchange-level order number distinct from its own
  // order id anywhere in its documented responses, so nothing is shown
  // here labeled "Exchange" rather than guessing at a field that may not
  // exist.
  const idsHtml = `
    ${idRow("App Order ID", o.order_id)}
    ${idRow("Broker Entry Order ID", o.entry && o.entry.broker_order_id)}
    ${idRow("Broker Close Order ID", o.square_off && o.square_off.broker_order_id)}`;

  const overlay = document.getElementById("dialogRoot");
  document.getElementById("dialogTitle").textContent = o.label;
  document.getElementById("dialogBody").innerHTML = `
    <div class="flex justify-between py-2 border-b border-slate-200 text-sm">
      <div class="text-slate-400">${pnlLabel}</div><div class="font-medium ${pnlClass}">${pnlText}</div>
    </div>
    ${programRow}
    ${idsHtml}
    ${rows.map(([k, v]) => `<div class="flex justify-between py-2 border-b border-slate-200 text-sm"><div class="text-slate-400">${k}</div><div class="font-medium text-slate-900 text-right">${v}</div></div>`).join("")}
    ${o.warning ? `<div class="text-xs text-red-600 mt-3">⚠ ${o.warning.message} (since ${o.warning.since.replace("T", " ")})</div>` : ""}
    <div class="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-[0.3rem] px-3 py-2 mt-3">
      <code class="flex-1 text-xs overflow-x-auto whitespace-nowrap text-slate-700">${filename}</code>
      ${btnText("Copy", `copyText('${filename}', 'Filename')`)}
      <a href="/api/orders/${o.order_id}" target="_blank" rel="noopener" class="relative inline-flex items-center justify-center h-9 px-4 rounded-[0.3rem] text-xs font-medium border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50">Open JSON</a>
    </div>`;
  resetDialogScroll("dialogRoot");
  overlay.classList.add("show");
}
