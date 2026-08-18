// Generalized so the SAME calendar component can serve multiple scopes at
// once (Regular OMS's own, the combined Portfolio view, and eventually
// Advanced OMS's own) -- each createCalendarInstance() call owns its own
// view-date/orders state and its own set of DOM element ids, registered by
// name so the day-detail dialog (shared across all instances, same reuse
// pattern as the Program/Risk Group dialog elsewhere in this app) can route
// a click back to the right one.

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const _calendarInstances = {};

function dateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function orderDateKey(o) {
  if (!o.updated_at) return null;
  const d = new Date(o.updated_at);
  return dateKey(d);
}

function createCalendarInstance(name, { weekdaysId, gridId, monthLabelId, orderFilter }) {
  const state = { viewDate: new Date(), orders: [] };

  function renderWeekdayHeader() {
    document.getElementById(weekdaysId).innerHTML = WEEKDAYS.map(
      (w) => `<div class="text-center text-[11px] uppercase tracking-wide text-slate-400 pb-1">${w}</div>`
    ).join("");
  }

  function renderMonth() {
    const year = state.viewDate.getFullYear();
    const month = state.viewDate.getMonth();
    document.getElementById(monthLabelId).textContent = state.viewDate.toLocaleString(undefined, {
      month: "long",
      year: "numeric",
    });

    const byDate = {};
    for (const o of state.orders) {
      if (o.status !== "closed") continue;
      const key = orderDateKey(o);
      if (!key) continue;
      if (!byDate[key]) byDate[key] = { pnl: 0, count: 0, hasUnknown: false };
      const realized = o.pnl && o.pnl.realized;
      if (typeof realized === "number") {
        byDate[key].pnl += realized;
      } else {
        byDate[key].hasUnknown = true;
      }
      byDate[key].count += 1;
    }

    const firstOfMonth = new Date(year, month, 1);
    const startWeekday = firstOfMonth.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const cells = [];
    for (let i = 0; i < startWeekday; i++) cells.push(`<div class="invisible"></div>`);
    for (let day = 1; day <= daysInMonth; day++) {
      const d = new Date(year, month, day);
      const key = dateKey(d);
      const info = byDate[key];
      const pnlHtml = info
        ? `<div class="text-sm font-semibold ${pnlColorClass(info.pnl)}">${signed(info.pnl)}${info.hasUnknown ? "*" : ""}</div>
           <div class="text-[10px] text-slate-400">${info.count} order${info.count === 1 ? "" : "s"}</div>`
        : "";
      cells.push(`
        <div onclick="_calendarShowDay('${name}', '${key}')" class="border border-slate-200 rounded-[0.3rem] shadow-sm p-2 min-h-[64px] cursor-pointer bg-white hover:border-blue-400 transition-colors">
          <div class="text-xs text-slate-400">${day}</div>
          ${pnlHtml}
        </div>`);
    }
    document.getElementById(gridId).innerHTML = cells.join("");
  }

  function shiftMonth(delta) {
    state.viewDate = new Date(state.viewDate.getFullYear(), state.viewDate.getMonth() + delta, 1);
    renderMonth();
  }

  function getOrdersForDay(key) {
    return state.orders.filter((o) => o.status === "closed" && orderDateKey(o) === key);
  }

  async function load() {
    const [active, archived] = await Promise.all([
      api("/api/orders"),
      api("/api/orders-archived"),
    ]);
    archived.forEach((o) => (o.__archived = true));
    state.orders = [...active, ...archived].filter(orderFilter);
    renderWeekdayHeader();
    renderMonth();
  }

  const instance = { load, shiftMonth, getOrdersForDay };
  _calendarInstances[name] = instance;
  return instance;
}

function _calendarShowDay(name, key) {
  const instance = _calendarInstances[name];
  const orders = instance.getOrdersForDay(key);
  if (!orders.length) {
    toast("No closed orders on this day.");
    return;
  }
  const rows = orders
    .map((o) => {
      const pnl = o.pnl && o.pnl.realized;
      const pnlText = typeof pnl === "number" ? signed(pnl) : "unknown";
      const pnlCls = typeof pnl === "number" ? pnlColorClass(pnl) : "text-slate-400";
      const omsBadge = o.program_id
        ? `<span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide adv-accent-bg-50 adv-accent-text-700 border adv-accent-border-200 mr-2">Advanced</span>`
        : `<span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide bg-blue-50 text-blue-700 border border-blue-200 mr-2">Regular</span>`;
      // deliberately NOT closing the day dialog before opening the order's
      // Info panel -- the Info panel (z-[60]) renders on top of this one,
      // so closing IT later reveals this dialog still open underneath,
      // giving a natural "back to the list" instead of a dead end
      return `<div class="flex justify-between items-center py-2 border-b border-slate-200 text-sm cursor-pointer hover:text-blue-600" onclick="showTradeDetails('${o.order_id}', ${o.__archived ? "true" : "false"})">
                <div class="flex items-center">${omsBadge}${o.label} (${o.side.toUpperCase()} ${o.qty})</div>
                <div class="font-medium ${pnlCls}">${pnlText}</div>
              </div>`;
    })
    .join("");

  document.getElementById("dayDialogTitle").textContent = `Orders closed on ${key}`;
  document.getElementById("dayDialogBody").innerHTML = `
    ${rows}
    <div class="text-xs text-slate-400 mt-3">Click an order to see its full details.</div>`;
  resetDialogScroll("dayDialogRoot");
  document.getElementById("dayDialogRoot").classList.add("show");
}

function closeDayDialog() {
  document.getElementById("dayDialogRoot").classList.remove("show");
}

// -------------------------------------------------------------- instances

const regularCalendar = createCalendarInstance("regular", {
  weekdaysId: "calendarWeekdays", gridId: "calendarGrid", monthLabelId: "monthLabel",
  orderFilter: (o) => !o.program_id,
});

const portfolioCalendar = createCalendarInstance("portfolio", {
  weekdaysId: "portfolioCalendarWeekdays", gridId: "portfolioCalendarGrid", monthLabelId: "portfolioMonthLabel",
  orderFilter: () => true,
});

const advancedCalendar = createCalendarInstance("advanced", {
  weekdaysId: "advancedCalendarWeekdays", gridId: "advancedCalendarGrid", monthLabelId: "advancedMonthLabel",
  orderFilter: (o) => !!o.program_id,
});

// loaded on demand by tabs.js/switchSection -- see switchTab("calendar")
// and switchSection("portfolio")
