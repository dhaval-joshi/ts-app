let archivedCache = [];
let selectedIds = new Set();

function renderPortfolioKpis(activeOrders) {
  const el = document.getElementById("portfolioKpis");
  if (!el) return;
  const all = activeOrders.concat(archivedCache);
  el.innerHTML = kpiCardsHtml(computeSummary(all), { title: "Portfolio (Regular + Advanced OMS combined)" });
}

function renderRegularKpis(activeOrders) {
  const el = document.getElementById("regularKpis");
  if (!el) return;
  const regular = activeOrders.concat(archivedCache).filter((o) => !o.program_id);
  el.innerHTML = kpiCardsHtml(computeSummary(regular), { title: "Regular OMS" });
}

function orderCard(o) {
  return renderOrderCard(o, {
    editable: true,
    showArchiveButton: true,
    showCheckbox: true,
    checked: selectedIds.has(o.order_id),
    isArchived: false,
  });
}

function render(orders) {
  renderPortfolioKpis(orders);
  renderRegularKpis(orders);

  // Advanced OMS legs are real orders underneath (same reconciliation/
  // trailing/P&L machinery), but they don't belong in the Regular OMS
  // dashboard's own list -- they're typically many small re-entries
  // through the day and would just be noise here. They still count
  // toward the combined KPIs above; they're just not shown in this grid.
  const regularOrders = orders.filter((o) => !o.program_id);

  const grid = document.getElementById("orderGrid");
  updateBulkBar();
  if (!regularOrders.length) {
    grid.innerHTML = `<div class="text-center text-slate-400 py-12">No orders yet. Head to <a href="/order" class="text-blue-600 underline">New Order</a> to place one.</div>`;
    return;
  }
  const openLogIds = new Set(
    [...document.querySelectorAll(".logs:not(.hidden)")].map((el) => el.id.replace("logs-", ""))
  );

  const drafts = {};
  document.querySelectorAll('[id^="closePrice-"], [id^="closePct-"]').forEach((el) => {
    if (el.value) drafts[el.id] = el.value;
  });
  const active = document.activeElement;
  const activeId = active && active.id && (active.id.startsWith("closePrice-") || active.id.startsWith("closePct-")) ? active.id : null;
  const selStart = activeId ? active.selectionStart : null;
  const selEnd = activeId ? active.selectionEnd : null;

  grid.innerHTML = regularOrders.map(orderCard).join("");
  openLogIds.forEach((id) => document.getElementById(`logs-${id}`)?.classList.remove("hidden"));

  const byId = Object.fromEntries(regularOrders.map((o) => [o.order_id, o]));
  Object.entries(drafts).forEach(([elId, val]) => {
    const el = document.getElementById(elId);
    if (el) el.value = val;
    if (elId.startsWith("closePrice-")) {
      const orderId = elId.replace("closePrice-", "");
      updateClosePricePreview(orderId, byId[orderId]?.tick_size || 0.05);
    }
  });

  if (activeId) {
    const el = document.getElementById(activeId);
    if (el) {
      el.focus();
      if (selStart !== null && el.setSelectionRange) el.setSelectionRange(selStart, selEnd);
    }
  }
}

function onCardCheckboxChange(el) {
  const id = el.dataset.orderId;
  if (el.checked) selectedIds.add(id);
  else selectedIds.delete(id);
  updateBulkBar();
}

function toggleSelectAll(checkbox) {
  document.querySelectorAll(".order-card-checkbox").forEach((el) => {
    el.checked = checkbox.checked;
    onCardCheckboxChange(el);
  });
}

function updateBulkBar() {
  document.getElementById("selectedCount").textContent = selectedIds.size;
  document.getElementById("archiveSelectedBtn").disabled = selectedIds.size === 0;
}

async function archiveSelected() {
  if (!selectedIds.size) return;
  if (!confirm(`Archive ${selectedIds.size} selected order(s)? They'll move to the Archive tab.`)) return;
  try {
    const res = await api("/api/orders/archive-bulk", {
      method: "POST",
      body: JSON.stringify({ order_ids: [...selectedIds] }),
    });
    toast(`Archived ${res.archived.length} order(s).${Object.keys(res.failed).length ? " Some couldn't be archived." : ""}`);
    selectedIds.clear();
    await loadDashboard();
  } catch (e) {
    toast("Bulk archive failed: " + e.message, "error");
  }
}

async function copyEntryPrice(price) {
  await copyText(String(price), "Entry price");
}

async function fetchClosePrice(id, streamSymbol, tickSize) {
  if (!streamSymbol) {
    toast("This order has no stream symbol set, so there's no live price to fetch.", "error");
    return;
  }
  const btn = document.getElementById(`fetchClosePrice-${id}`);
  const input = document.getElementById(`closePrice-${id}`);
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Fetching…";
  try {
    const res = await api("/api/price/fetch", {
      method: "POST",
      body: JSON.stringify({ stream_symbol: streamSymbol }),
    });
    input.value = res.ltp;
    toast(`Fetched price ${res.ltp}.`);
    updateClosePricePreview(id, tickSize);
  } catch (e) {
    toast("Couldn't fetch a price: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function applyPctProfit(id, side, entryAvg, tickSize) {
  const pctInput = document.getElementById(`closePct-${id}`);
  const pct = Number(pctInput.value);
  if (!Number.isFinite(pct)) {
    toast("Enter a % profit first.", "error");
    return;
  }
  const sign = side === "buy" ? 1 : -1;
  const price = entryAvg * (1 + (sign * pct) / 100);
  const priceInput = document.getElementById(`closePrice-${id}`);
  priceInput.value = price.toFixed(2);
  updateClosePricePreview(id, tickSize);
}

function updateClosePricePreview(id, tickSize) {
  const input = document.getElementById(`closePrice-${id}`);
  const preview = document.getElementById(`closePricePreview-${id}`);
  if (!input || !preview) return;
  const raw = input.value.trim();
  if (!raw) {
    preview.textContent = "Leave the price blank to close at market, or enter one for a limit order.";
    return;
  }
  const price = Number(raw);
  if (!Number.isFinite(price)) {
    preview.textContent = "";
    return;
  }
  const tick = Number(tickSize) || 0.05;
  const rounded = Math.floor(price / tick + 1e-9) * tick;
  preview.textContent = `Will place a LIMIT order at ${rounded.toFixed(2)} (rounded down to tick size ${tick}).`;
}

function reenterOrder(id) {
  // used to be a full page navigation to /order?reorder=<id> -- New Order
  // is a dialog now, opened in place instead
  showNewOrderDialog(id);
}

async function archiveOrder(id) {
  if (!confirm("Archive this order? It'll move off the dashboard into the Archive tab (P&L totals stay the same either way).")) return;
  try {
    await api(`/api/orders/${id}/archive`, { method: "POST" });
    toast("Order archived.");
    loadDashboard();
  } catch (e) {
    toast("Failed to archive: " + e.message, "error");
  }
}

async function closeOrder(id) {
  const priceInput = document.getElementById(`closePrice-${id}`);
  const priceVal = priceInput ? priceInput.value.trim() : "";
  const price = priceVal ? Number(priceVal) : null;

  const msg =
    price !== null
      ? `Close this position with a limit order at ~${price} (rounded down to the instrument's tick size)?`
      : "Close this position now at market?";
  if (!confirm(msg)) return;

  try {
    await api(`/api/orders/${id}/close`, {
      method: "POST",
      body: JSON.stringify(price !== null ? { price } : {}),
    });
    toast(price !== null ? `Close requested at ~${price}.` : "Close requested at market.");
  } catch (e) {
    toast("Failed to close: " + e.message, "error");
  }
}

async function refreshArchivedCache() {
  try {
    archivedCache = await api("/api/orders-archived");
  } catch (e) {
    archivedCache = [];
  }
}

async function loadDashboard() {
  await refreshArchivedCache();
  const orders = await api("/api/orders");
  render(orders);
}

loadDashboard();
onOrdersUpdate(render); // registers on the single shared connection app.js already opened --
                          // see connectStatusSocket's comment there for why
