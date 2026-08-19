const strategyForm = document.getElementById("strategyForm");
const timeExitMode = document.getElementById("timeExitMode");
const windowFields = document.getElementById("windowFields");
const atField = document.getElementById("atField");
const trailIntervalMode = document.getElementById("trailIntervalMode");
const trailIntervalCustomField = document.getElementById("trailIntervalCustomField");
const listView = document.getElementById("strategyListView");
const formView = document.getElementById("strategyFormView");
const strategyListEl = document.getElementById("strategyList");

let strategies = [];
let selectedStrategyNames = new Set();
let editingId = null; // null while creating a new strategy; the strategy_id while editing, so save overwrites in place (a real rename)

timeExitMode.addEventListener("change", () => {
  windowFields.classList.toggle("hidden", timeExitMode.value !== "intraday_window");
  atField.classList.toggle("hidden", timeExitMode.value !== "datetime");
});

trailIntervalMode.addEventListener("change", () => {
  trailIntervalCustomField.classList.toggle("hidden", trailIntervalMode.value !== "custom");
});

// Reverse-maps a stored seconds value to its matching preset option, or "custom"
// pre-filled with that value if it doesn't match any preset exactly -- reuses
// TRAIL_INTERVAL_PRESETS from programs.js (both loaded together on index.html).
function applyTrailIntervalToForm(seconds) {
  const isCustom = !TRAIL_INTERVAL_PRESETS.some((p) => p.value === seconds);
  trailIntervalMode.value = isCustom ? "custom" : String(seconds);
  strategyForm.trail_check_interval_custom.value = isCustom ? seconds : "";
  trailIntervalMode.dispatchEvent(new Event("change"));
}

// Convenience default: a fresh "intraday" strategy defaults its time-based
// close to the 15:10-15:15 window, but only while the time-exit mode is
// still untouched ("none") -- never overrides a mode you've deliberately
// picked, and never fires while loading an existing strategy for editing.
function applyIntradayTimeExitDefault() {
  if (strategyForm.product.value === "intraday" && timeExitMode.value === "none") {
    timeExitMode.value = "intraday_window";
    if (!strategyForm.window_start.value) strategyForm.window_start.value = "15:10";
    if (!strategyForm.window_end.value) strategyForm.window_end.value = "15:15";
    timeExitMode.dispatchEvent(new Event("change"));
  }
}
strategyForm.product.addEventListener("change", applyIntradayTimeExitDefault);

function buildPayload(fd) {
  return {
    strategy_id: editingId,
    name: fd.get("name"),
    product: fd.get("product"),
    tick_size: num(fd.get("tick_size")) || 0.05,
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
      at: fd.get("at") || null,
    },
    trail_check_interval_seconds: readTrailIntervalFromForm(fd),  // shared with programs.js
    exit_confirmation_windows: num(fd.get("exit_confirmation_windows")) || 1,
    stop_breach_force_close_count: num(fd.get("stop_breach_force_close_count")) ?? 0,
  };
}

strategyForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = buildPayload(new FormData(strategyForm));
  try {
    await api("/api/strategies", { method: "POST", body: JSON.stringify(payload) });
    toast("Strategy saved.");
    hideStrategyForm();
  } catch (err) {
    toast("Failed to save: " + err.message, "error");
  }
});

function applyStrategy(s) {
  editingId = s.strategy_id;
  strategyForm.name.value = s.name;
  strategyForm.product.value = s.product;
  strategyForm.tick_size.value = s.tick_size ?? 0.05;

  strategyForm.stop_offset_mode.value = s.stop.offset_mode;
  strategyForm.stop_trig_offset.value = s.stop.trig_offset;
  strategyForm.stop_limit_offset.value = s.stop.limit_offset;
  strategyForm.stop_trailing_enabled.checked = !!s.stop.trailing.enabled;
  strategyForm.stop_trail_by.value = s.stop.trailing.trail_by;
  strategyForm.stop_activation_offset.value = s.stop.trailing.activation_offset;

  strategyForm.target_offset_mode.value = s.target.offset_mode;
  strategyForm.target_trig_offset.value = s.target.trig_offset;
  strategyForm.target_limit_offset.value = s.target.limit_offset;
  strategyForm.target_trailing_enabled.checked = !!s.target.trailing.enabled;
  strategyForm.target_trail_by.value = s.target.trailing.trail_by;
  strategyForm.target_activation_offset.value = s.target.trailing.activation_offset;

  strategyForm.time_exit_mode.value = s.time_exit?.mode || "none";
  strategyForm.window_start.value = s.time_exit?.window_start || "";
  strategyForm.window_end.value = s.time_exit?.window_end || "";
  strategyForm.at.value = s.time_exit?.at || "";
  timeExitMode.dispatchEvent(new Event("change"));

  applyTrailIntervalToForm(s.trail_check_interval_seconds || 0);
  strategyForm.exit_confirmation_windows.value = s.exit_confirmation_windows || 1;
  strategyForm.stop_breach_force_close_count.value = s.stop_breach_force_close_count || 0;
}

// ------------------------------------------------------------ list view

function strategyLegSummary(leg) {
  const unit = leg.offset_mode === "percent" ? "%" : "pts";
  return `${leg.trig_offset}${unit}${leg.trailing.enabled ? " (trailing)" : ""}`;
}

function strategyTimeExitSummary(te) {
  if (te.mode === "intraday_window") return `Close ${te.window_start}–${te.window_end}`;
  if (te.mode === "datetime") return `Close at ${te.at}`;
  return "No scheduled close";
}

function strategyRow(s) {
  return `
  <div class="flex items-center gap-4 bg-white border border-slate-200 shadow-sm rounded-xl p-4">
    <input type="checkbox" class="strategy-checkbox w-4 h-4" data-name="${s.name}" onchange="onStrategyCheckboxChange(this)" ${selectedStrategyNames.has(s.name) ? "checked" : ""} />
    <div class="flex-1">
      <div class="text-sm font-medium">${s.name}</div>
      <div class="text-xs text-slate-400 mt-0.5">${s.product} · Stop ${strategyLegSummary(s.stop)} · Target ${strategyLegSummary(s.target)} · ${strategyTimeExitSummary(s.time_exit)}</div>
    </div>
    <button type="button" title="Edit" onclick="editStrategy('${s.name}')" class="relative inline-flex items-center justify-center w-9 h-9 rounded-[0.3rem] border border-slate-200 bg-white shadow-sm text-slate-400 hover:text-blue-600"><span class="material-symbols-outlined !text-lg">edit</span></button>
    <button type="button" title="Delete" onclick="deleteOneStrategy('${s.name}')" class="relative inline-flex items-center justify-center w-9 h-9 rounded-[0.3rem] border border-slate-200 bg-white shadow-sm text-slate-400 hover:text-red-600"><span class="material-symbols-outlined !text-lg">delete</span></button>
  </div>`;
}

async function loadStrategies() {
  strategies = await api("/api/strategies");
  if (!strategies.length) {
    strategyListEl.innerHTML = `<div class="text-center text-slate-400 py-12">No strategies yet — click "New strategy" to create one.</div>`;
  } else {
    strategyListEl.innerHTML = strategies.map(strategyRow).join("");
  }
  updateStrategyBulkBar();
}

function onStrategyCheckboxChange(el) {
  if (el.checked) selectedStrategyNames.add(el.dataset.name);
  else selectedStrategyNames.delete(el.dataset.name);
  updateStrategyBulkBar();
}

function toggleSelectAllStrategies(checkbox) {
  document.querySelectorAll(".strategy-checkbox").forEach((el) => {
    el.checked = checkbox.checked;
    onStrategyCheckboxChange(el);
  });
}

function updateStrategyBulkBar() {
  document.getElementById("selectedStrategyCount").textContent = selectedStrategyNames.size;
  document.getElementById("deleteSelectedStrategiesBtn").disabled = selectedStrategyNames.size === 0;
}

async function deleteSelectedStrategies() {
  if (!selectedStrategyNames.size) return;
  if (!confirm(`Delete ${selectedStrategyNames.size} selected strategy/strategies? Orders already placed with them keep working.`)) return;
  const names = [...selectedStrategyNames];
  let failed = 0;
  for (const name of names) {
    try {
      await api(`/api/strategies/${encodeURIComponent(name)}`, { method: "DELETE" });
    } catch (e) {
      failed++;
    }
  }
  toast(`Deleted ${names.length - failed} strategy/strategies.${failed ? ` ${failed} failed.` : ""}`);
  selectedStrategyNames.clear();
  await loadStrategies();
}

async function deleteOneStrategy(name) {
  if (!confirm(`Delete strategy "${name}"? Orders already placed with it keep working.`)) return;
  await api(`/api/strategies/${encodeURIComponent(name)}`, { method: "DELETE" });
  toast("Strategy deleted.");
  selectedStrategyNames.delete(name);
  await loadStrategies();
}

// ------------------------------------------------------------ strategyForm view

function showStrategyForm() {
  editingId = null;
  strategyForm.reset();
  timeExitMode.dispatchEvent(new Event("change"));
  trailIntervalMode.dispatchEvent(new Event("change"));
  applyIntradayTimeExitDefault();
  listView.classList.add("hidden");
  formView.classList.remove("hidden");
}

async function editStrategy(name) {
  const s = await api(`/api/strategies/${encodeURIComponent(name)}`);
  applyStrategy(s);  // sets editingId = s.strategy_id
  listView.classList.add("hidden");
  formView.classList.remove("hidden");
}

function hideStrategyForm() {
  editingId = null;
  listView.classList.remove("hidden");
  formView.classList.add("hidden");
  loadStrategies();
}

// no longer auto-loads at script-load time -- was a standalone page before,
// now a Regular OMS tab, loaded on demand from switchTab("strategies")
