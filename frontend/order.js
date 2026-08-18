const form = document.getElementById("orderForm");
const strategyPicker = document.getElementById("strategyPicker");
const noStrategyHint = document.getElementById("noStrategyHint");
const entryTrigField = document.getElementById("entryTrigField");
const entryTypeSelect = document.getElementById("entryTypeSelect");
const marketProtHint = document.getElementById("marketProtHint");
const exitModeSelect = document.getElementById("exitModeSelect");
const exitModeHint = document.getElementById("exitModeHint");
const strategyPreview = document.getElementById("strategyPreview");
const trailingWarning = document.getElementById("trailingWarning");
const streamSymbolInput = form.stream_symbol;

const sizingMode = document.getElementById("sizingMode");
const lotsSizingFields = document.getElementById("lotsSizingFields");
const capitalSizingFields = document.getElementById("capitalSizingFields");
const capitalHint = document.getElementById("capitalHint");
const sizingPreview = document.getElementById("sizingPreview");
const SLIPPAGE_POINTS = 2;

const exchangeMismatchWarning = document.getElementById("exchangeMismatchWarning");
const fetchPriceBtn = document.getElementById("fetchPriceBtn");
const fetchPriceStatus = document.getElementById("fetchPriceStatus");
const KNOWN_EXCHANGE_SEGMENTS = ["NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "NCDEX"];

const EXIT_MODE_HINTS = {
  both: "Both stop-loss and target are watched — whichever crosses first closes the position.",
  sl_only: "Only the stop-loss is watched — no target. You'd close in profit manually or let a time-based rule handle it.",
  target_only: "Only the target is watched — no stop-loss. Downside isn't protected at all; close manually if it moves against you.",
  none: "Nothing is watched after entry — this position is only ever closed by you, or by the strategy's time-based rule.",
};

function checkExchangeMismatch() {
  const symId = form.sym_id.value.trim().toUpperCase();
  const streamSymbol = form.stream_symbol.value.trim().toUpperCase();
  if (!symId || !streamSymbol) {
    exchangeMismatchWarning.classList.add("hidden");
    return;
  }
  const symTokens = symId.split("_");
  const symExchanges = symTokens.filter((t) => KNOWN_EXCHANGE_SEGMENTS.includes(t));
  const streamExchange = streamSymbol.split("_").pop();

  if (symExchanges.length && !symExchanges.includes(streamExchange)) {
    exchangeMismatchWarning.classList.remove("hidden");
    exchangeMismatchWarning.textContent =
      `Heads up: the symbol ID looks like it's on ${symExchanges.join("/")}, but the stream symbol ` +
      `ends in "${streamExchange}". These usually need to match (e.g. an NFO option needs a stream ` +
      `symbol ending "_NFO", not "_NSE") or live prices for this order won't arrive -- trailing and ` +
      `live P&L will silently stay blank. Double-check the exchange segment.`;
  } else {
    exchangeMismatchWarning.classList.add("hidden");
  }
}
form.sym_id.addEventListener("input", checkExchangeMismatch);
form.stream_symbol.addEventListener("input", checkExchangeMismatch);

function updateFetchButtonState() {
  fetchPriceBtn.disabled = !form.stream_symbol.value.trim();
}
form.stream_symbol.addEventListener("input", updateFetchButtonState);
updateFetchButtonState();

async function fetchPrice() {
  const streamSymbol = form.stream_symbol.value.trim();
  if (!streamSymbol) {
    toast("Enter a stream symbol first.", "error");
    return;
  }
  fetchPriceBtn.disabled = true;
  fetchPriceBtn.textContent = "Fetching…";
  fetchPriceStatus.textContent = "Waiting for a live price (up to a few seconds)…";
  try {
    const res = await api("/api/price/fetch", {
      method: "POST",
      body: JSON.stringify({ stream_symbol: streamSymbol }),
    });
    form.entry_price.value = res.ltp;
    fetchPriceStatus.textContent = `Fetched: ${res.ltp} (at ${new Date().toLocaleTimeString()})`;
    updateSizingPreview();
  } catch (err) {
    fetchPriceStatus.textContent = "";
    toast("Couldn't fetch a price: " + err.message, "error");
  } finally {
    fetchPriceBtn.textContent = "Fetch price";
    updateFetchButtonState();
  }
}

let orderFormStrategies = [];  // renamed from 'strategies' -- collides with strategies.js's own, now that both load on the same page

sizingMode.addEventListener("change", () => {
  const byCapital = sizingMode.value === "capital";
  lotsSizingFields.classList.toggle("hidden", byCapital);
  capitalSizingFields.classList.toggle("hidden", !byCapital);
  capitalHint.classList.toggle("hidden", !byCapital);
  updateSizingPreview();
});
[form.lot_size, form.num_lots, form.capital, form.entry_price].forEach((el) =>
  el.addEventListener("input", updateSizingPreview)
);

/** Returns the final integer quantity to send to the backend, or null if
 * the current sizing inputs don't resolve to at least one whole lot. */
function computeQty() {
  const lotSize = Math.max(1, Math.floor(Number(form.lot_size.value) || 1));
  if (sizingMode.value === "lots") {
    const lots = Math.max(1, Math.floor(Number(form.num_lots.value) || 0));
    return { qty: lots * lotSize, lots, lotSize };
  }
  const capital = Number(form.capital.value) || 0;
  const refPrice = Number(form.entry_price.value) || 0;
  if (capital <= 0 || refPrice <= 0) return { qty: null, lots: 0, lotSize };
  const effectivePrice = refPrice + SLIPPAGE_POINTS;
  const rawQty = Math.floor(capital / effectivePrice);
  const lots = Math.floor(rawQty / lotSize);
  return { qty: lots * lotSize, lots, lotSize, effectivePrice };
}

function updateSizingPreview() {
  const price = Number(form.entry_price.value) || 0;

  if (sizingMode.value === "lots") {
    const lotSize = Math.max(1, Math.floor(Number(form.lot_size.value) || 1));
    const lots = Math.max(1, Math.floor(Number(form.num_lots.value) || 0));
    const qty = lots * lotSize;
    let text = `Order quantity: ${qty} (${lots} lot${lots === 1 ? "" : "s"} × ${lotSize}).`;
    if (price > 0) text += ` Capital utilized ≈ ₹${(qty * price).toFixed(2)} at ${price}/unit.`;
    sizingPreview.textContent = text;
    return;
  }

  const { qty, lots, lotSize, effectivePrice } = computeQty();
  if (!qty) {
    sizingPreview.textContent = "Enter capital and an entry/reference price to estimate quantity.";
    return;
  }
  if (lots < 1) {
    sizingPreview.textContent = `Capital too small for even 1 lot of ${lotSize} at this price + slippage buffer.`;
    return;
  }
  const estCost = (qty * (effectivePrice - SLIPPAGE_POINTS)).toFixed(2);
  sizingPreview.textContent = `Order quantity: ${qty} (${lots} lot${lots === 1 ? "" : "s"} × ${lotSize}), using ₹${effectivePrice.toFixed(2)}/unit (incl. slippage buffer) → ~₹${estCost} capital utilized.`;
}
updateSizingPreview();

function legSummary(leg, legName) {
  const unit = leg.offset_mode === "percent" ? "%" : "pts";
  const trailBit = leg.trailing.enabled
    ? `trailing by ${leg.trailing.trail_by}${unit}${leg.trailing.activation_offset ? ` after ${leg.trailing.activation_offset}${unit} profit` : ""}`
    : "no trailing";
  return `${legName}: ${leg.trig_offset}${unit} from entry${leg.limit_offset ? ` (+${leg.limit_offset}${unit} limit buffer)` : ""} — ${trailBit}`;
}

function renderPreview(s) {
  const timeExit =
    s.time_exit.mode === "intraday_window"
      ? `Closes ${s.time_exit.window_start}–${s.time_exit.window_end}`
      : s.time_exit.mode === "datetime"
      ? `Closes at ${s.time_exit.at}`
      : "No scheduled close";

  const mode = exitModeSelect.value;
  const legLines = [];
  if (mode === "both" || mode === "sl_only") legLines.push(`<div class="text-xs text-slate-400 mt-2">${legSummary(s.stop, "Stop")}</div>`);
  if (mode === "both" || mode === "target_only") legLines.push(`<div class="text-xs text-slate-400">${legSummary(s.target, "Target")}</div>`);

  strategyPreview.classList.remove("hidden");
  strategyPreview.innerHTML = `
    <div class="grid grid-cols-2 gap-4">
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Product</div><div class="text-sm font-medium text-slate-900">${s.product}</div></div>
      <div><div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Time exit</div><div class="text-sm font-medium text-slate-900">${timeExit}</div></div>
    </div>
    ${legLines.join("")}
  `;
}

function updateTrailingWarning(s) {
  const mode = exitModeSelect.value;
  const hasTrailing =
    s &&
    ((mode === "both" || mode === "sl_only") && s.stop.trailing.enabled) ||
    (s && (mode === "both" || mode === "target_only") && s.target.trailing.enabled);
  const missingStream = !streamSymbolInput.value.trim();
  if (hasTrailing && missingStream) {
    trailingWarning.classList.remove("hidden");
    trailingWarning.textContent =
      "This strategy trails the SL and/or target, but no stream symbol is set above — trailing will silently do nothing without live prices. Add the stream symbol (excToken_exchange, e.g. 2885_NSE) if you want trailing to actually work.";
  } else {
    trailingWarning.classList.add("hidden");
  }
}

function updateEntryTypeVisibility() {
  const t = entryTypeSelect.value;
  const needsTrig = t === "stoplimit" || t === "stopmarket";
  entryTrigField.classList.toggle("hidden", !needsTrig);
  marketProtHint.classList.toggle("hidden", !(t === "market" || t === "stopmarket"));
}

function onStrategyChange() {
  const s = orderFormStrategies.find((x) => x.name === strategyPicker.value);
  exitModeHint.textContent = EXIT_MODE_HINTS[exitModeSelect.value] || "";
  if (!s) {
    strategyPreview.classList.add("hidden");
    trailingWarning.classList.add("hidden");
    return;
  }
  renderPreview(s);
  updateTrailingWarning(s);
}

strategyPicker.addEventListener("change", onStrategyChange);
exitModeSelect.addEventListener("change", onStrategyChange);
entryTypeSelect.addEventListener("change", updateEntryTypeVisibility);
streamSymbolInput.addEventListener("input", () => {
  const s = orderFormStrategies.find((x) => x.name === strategyPicker.value);
  updateTrailingWarning(s);
});
updateEntryTypeVisibility();
exitModeHint.textContent = EXIT_MODE_HINTS[exitModeSelect.value];

async function loadStrategiesForOrderForm() {
  orderFormStrategies = await api("/api/strategies");
  if (!orderFormStrategies.length) {
    noStrategyHint.classList.remove("hidden");
    return;
  }
  noStrategyHint.classList.add("hidden");
  strategyPicker.innerHTML =
    `<option value="">— choose a strategy —</option>` +
    orderFormStrategies.map((s) => `<option value="${s.name}">${s.name}</option>`).join("");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const { qty, lotSize } = computeQty();
  if (!qty || qty < 1) {
    toast("Quantity resolves to 0 — check lot size / capital / entry price.", "error");
    return;
  }
  const entryType = entryTypeSelect.value;
  const needsLimit = entryType === "limit" || entryType === "stoplimit";
  if (needsLimit && !form.entry_price.value) {
    toast("This entry type needs a limit price — fill in or fetch the Entry/reference price above.", "error");
    return;
  }
  const needsTrig = entryType === "stoplimit" || entryType === "stopmarket";
  if (needsTrig && !form.entry_trig_price.value) {
    toast("This entry type needs a trigger price.", "error");
    return;
  }

  const fd = new FormData(form);
  const payload = {
    label: fd.get("label") || "",
    sym_id: fd.get("sym_id"),
    stream_symbol: fd.get("stream_symbol") || null,
    side: fd.get("side"),
    qty,
    lot_size: lotSize,
    strategy_name: fd.get("strategy_name"),
    entry_type: entryType,
    entry_validity: fd.get("entry_validity"),
    entry_limit_price: needsLimit ? num(fd.get("entry_price")) : null,
    entry_trig_price: needsTrig ? num(fd.get("entry_trig_price")) : null,
    exit_mode: exitModeSelect.value,
  };
  try {
    const order = await api("/api/orders", { method: "POST", body: JSON.stringify(payload) });
    if (order.status === "entry_rejected") {
      toast("Order rejected by broker — check the dashboard log.", "error");
    } else {
      toast(`Order placed for qty ${qty}.`);
      closeOrderDialog();
      form.reset();
      strategyPreview.classList.add("hidden");
      trailingWarning.classList.add("hidden");
      exchangeMismatchWarning.classList.add("hidden");
      fetchPriceStatus.textContent = "";
      updateFetchButtonState();
      updateEntryTypeVisibility();
      sizingMode.dispatchEvent(new Event("change"));
    }
  } catch (err) {
    toast("Failed to place order: " + err.message, "error");
  }
});

// ------------------------------------------------------- re-enter a trade

async function applyReorderPrefill(orderId) {
  let o;
  try {
    o = await api(`/api/orders/${orderId}`);
  } catch (err) {
    toast("Couldn't load the original order to prefill from: " + err.message, "error");
    return;
  }

  form.label.value = o.label || "";
  form.sym_id.value = o.sym_id || "";
  form.stream_symbol.value = o.stream_symbol || "";
  form.side.value = o.side;
  entryTypeSelect.value = o.entry.type || "market";
  form.entry_validity.value = o.entry.validity || "day";
  exitModeSelect.value = o.exit_mode || "both";
  updateEntryTypeVisibility();

  if (o.strategy_name && orderFormStrategies.some((s) => s.name === o.strategy_name)) {
    strategyPicker.value = o.strategy_name;
  } else if (o.strategy_name) {
    toast(`Original strategy "${o.strategy_name}" no longer exists — pick one to continue.`, "error");
  }

  // reproduce the SAME lot size and number of lots the original order used
  // (both are stored on the order specifically so this works correctly --
  // previously only the final qty was known, which meant defaulting to
  // "1 lot of size <total qty>", visually indistinguishable from the real
  // lot_size/num_lots having been swapped)
  sizingMode.value = "lots";
  const lotSize = o.lot_size && o.lot_size > 0 ? o.lot_size : 1;
  form.lot_size.value = lotSize;
  form.num_lots.value = Math.round(o.qty / lotSize) || 1;

  const priceToPrefill = o.entry?.limit_price ?? o.entry?.avg_price ?? "";
  if (priceToPrefill !== "") form.entry_price.value = priceToPrefill;
  if (o.entry?.trig_price != null) form.entry_trig_price.value = o.entry.trig_price;

  onStrategyChange();
  checkExchangeMismatch();
  updateFetchButtonState();
  sizingMode.dispatchEvent(new Event("change"));
  toast("Prefilled from the previous order — review before placing.");
}

// ------------------------------------------------------ dialog open/close
//
// Was a full page (order.html) with its own load-time IIFE; now a dialog
// that lives inside index.html and opens on demand -- from the "+ New
// Order" FAB in Regular OMS, or via reenterOrder() elsewhere, which passes
// an order id to prefill from instead of a blank form.

function closeOrderDialog() {
  document.getElementById("orderDialogRoot").classList.remove("show");
}

async function showNewOrderDialog(reorderId) {
  document.getElementById("orderDialogTitle").textContent = reorderId ? "Re-enter order" : "Place an order";
  resetDialogScroll("orderDialogRoot");
  document.getElementById("orderDialogRoot").classList.add("show");

  form.reset();
  strategyPreview.classList.add("hidden");
  trailingWarning.classList.add("hidden");
  exchangeMismatchWarning.classList.add("hidden");
  fetchPriceStatus.textContent = "";
  updateFetchButtonState();
  updateEntryTypeVisibility();
  sizingMode.dispatchEvent(new Event("change"));

  await loadStrategiesForOrderForm();
  if (reorderId) {
    await applyReorderPrefill(reorderId);
  } else {
    exitModeHint.textContent = EXIT_MODE_HINTS[exitModeSelect.value] || "";
  }
}
