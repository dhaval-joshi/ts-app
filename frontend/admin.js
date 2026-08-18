// Advanced OMS accent color options -- RGB triplets matching Tailwind's
// own stock 50/200/600/700 shades for each color, so switching accents
// looks exactly as if the Advanced OMS section were built with that
// Tailwind color directly (which, functionally, it now is at render time
// -- see the .adv-accent-* CSS custom properties in style.css).
const ADV_ACCENT_OPTIONS = {
  indigo: { 50: "238 242 255", 200: "199 210 254", 600: "79 70 229", 700: "67 56 202" },
  purple: { 50: "250 245 255", 200: "233 213 255", 600: "147 51 234", 700: "126 34 206" },
  violet: { 50: "245 243 255", 200: "221 214 254", 600: "124 58 237", 700: "109 40 217" },
  teal: { 50: "240 253 250", 200: "153 246 228", 600: "13 148 136", 700: "15 118 110" },
  rose: { 50: "255 241 242", 200: "254 205 211", 600: "225 29 72", 700: "190 18 60" },
  amber: { 50: "255 251 235", 200: "253 230 138", 600: "217 119 6", 700: "180 83 9" },
};

let currentAdvAccent = "indigo";

function applyAdvAccent(name) {
  const shades = ADV_ACCENT_OPTIONS[name] || ADV_ACCENT_OPTIONS.indigo;
  const root = document.documentElement.style;
  root.setProperty("--adv-accent-50", shades[50]);
  root.setProperty("--adv-accent-200", shades[200]);
  root.setProperty("--adv-accent-600", shades[600]);
  root.setProperty("--adv-accent-700", shades[700]);
  currentAdvAccent = name;
}

// Applied on every page load, not just when the Admin dialog happens to be
// opened -- otherwise a saved choice would only take effect after visiting
// Admin once that session.
(async function loadSavedAccent() {
  try {
    const settings = await api("/api/settings");
    applyAdvAccent(settings.advanced_oms_accent || "indigo");
  } catch (e) {
    applyAdvAccent("indigo"); // fall back quietly -- not worth a toast for a cosmetic default
  }
})();

// ------------------------------------------------------------- page init
//
// Admin used to be a dialog inside index.html; it's a standalone page now
// (infrequent, setup-like usage fits a real page better than a dialog --
// and the Failures table specifically needed room a fixed-width dialog
// couldn't give it). This runs once on page load instead of on a dialog
// open.

(function initAdminPage() {
  renderAccentPicker();
  loadPortfolioSafeguardsForm();
  loadFailuresTab();
})();

const ADMIN_TAB_ACTIVE_CLASSES = ["text-blue-600", "border-blue-600"];
const ADMIN_TAB_INACTIVE_CLASSES = ["text-slate-400", "border-transparent"];

function switchAdminTab(name) {
  document.querySelectorAll(".admin-tab-btn").forEach((b) => {
    const isActive = b.dataset.adminTab === name;
    b.classList.toggle("font-semibold", isActive);
    ADMIN_TAB_ACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, isActive));
    ADMIN_TAB_INACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, !isActive));
  });
  document.querySelectorAll(".admin-tab-panel").forEach((p) => {
    p.classList.toggle("hidden", p.id !== `admintab-${name}`);
  });
}

// ------------------------------------------------------------- General tab

function renderAccentPicker() {
  const el = document.getElementById("accentColorPicker");
  if (!el) return;
  el.innerHTML = Object.keys(ADV_ACCENT_OPTIONS)
    .map((name) => {
      const shade600 = ADV_ACCENT_OPTIONS[name][600].split(" ").join(",");
      const isActive = name === currentAdvAccent;
      return `
        <button type="button" onclick="chooseAdvAccent('${name}')" title="${name}"
          class="relative w-10 h-10 rounded-[0.3rem] shadow-sm border-2 ${isActive ? "border-slate-900" : "border-transparent"}"
          style="background-color: rgb(${shade600})">
          ${isActive ? `<span class="material-symbols-outlined !text-lg text-white absolute inset-0 flex items-center justify-center">check</span>` : ""}
        </button>`;
    })
    .join("");
}

async function chooseAdvAccent(name) {
  applyAdvAccent(name); // apply immediately -- don't make the person wait on a round trip to see the change
  renderAccentPicker();
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ advanced_oms_accent: name }) });
    toast("Accent color saved.");
  } catch (e) {
    toast("Saved locally, but failed to persist: " + e.message, "error");
  }
}

// ----------------------------------------------------------- Failures tab

let failuresProgramCache = [];

async function loadFailuresTab() {
  try {
    failuresProgramCache = await api("/api/programs");
  } catch (e) {
    failuresProgramCache = [];
  }
  renderFailuresFilterForm();
  await runFailuresFilter();
}

function renderFailuresFilterForm() {
  const el = document.getElementById("failuresFilterForm");
  if (!el) return;
  const programOptions = failuresProgramCache
    .map((p) => `<option value="${p.config.program_id}">${p.config.name}</option>`)
    .join("");
  el.innerHTML = `
    <div>
      <label class="field-label">Category</label>
      <input id="failuresCategoryFilter" placeholder="e.g. entry_rejected" class="field-input" />
    </div>
    <div>
      <label class="field-label">Order ID</label>
      <input id="failuresOrderFilter" placeholder="exact order id" class="field-input" />
    </div>
    <div>
      <label class="field-label">Program</label>
      <select id="failuresProgramFilter" class="js-enhance-select field-input">
        <option value="">— any —</option>
        ${programOptions}
      </select>
    </div>
    <div>
      <label class="field-label">OMS type</label>
      <select id="failuresOmsTypeFilter" class="js-enhance-select field-input">
        <option value="">— any —</option>
        <option value="regular">Regular OMS</option>
        <option value="advanced">Advanced OMS</option>
      </select>
    </div>
    <div>
      <label class="field-label">Since</label>
      <input id="failuresSinceFilter" type="date" class="field-input" />
    </div>
    <div class="flex items-end gap-2">
      <div class="flex-1">
        <label class="field-label">Until</label>
        <input id="failuresUntilFilter" type="date" class="field-input" />
      </div>
      <button type="button" onclick="runFailuresFilter()" class="relative inline-flex items-center h-[42px] px-4 rounded-[0.3rem] text-xs font-medium bg-blue-600 text-white shadow-sm hover:bg-blue-700">Filter</button>
    </div>`;
  enhanceAllSelects(el);
}

function failuresProgramName(id) {
  const p = failuresProgramCache.find((x) => x.config.program_id === id);
  return p ? p.config.name : id;
}

async function runFailuresFilter() {
  const category = document.getElementById("failuresCategoryFilter")?.value.trim();
  const orderId = document.getElementById("failuresOrderFilter")?.value.trim();
  const programId = document.getElementById("failuresProgramFilter")?.value;
  const omsType = document.getElementById("failuresOmsTypeFilter")?.value;
  const since = document.getElementById("failuresSinceFilter")?.value;
  const until = document.getElementById("failuresUntilFilter")?.value;

  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (orderId) params.set("order_id", orderId);
  if (programId) params.set("program_id", programId);
  if (omsType) params.set("oms_type", omsType);
  if (since) params.set("since", since);
  if (until) params.set("until", until + "T23:59:59"); // date-only input -> end of that day, inclusive

  const listEl = document.getElementById("failuresList");
  listEl.innerHTML = `<div class="text-center text-slate-400 py-8 text-sm">Loading…</div>`;
  try {
    const entries = await api(`/api/failures?${params.toString()}`);
    listEl.innerHTML = entries.length
      ? entries.map(failureRowHtml).join("")
      : `<div class="text-center text-slate-400 py-8 text-sm">No failures match these filters.</div>`;
  } catch (e) {
    listEl.innerHTML = `<div class="text-center text-red-600 py-8 text-sm">Couldn't load: ${e.message}</div>`;
  }
}

function failureRowHtml(f) {
  const detailId = `failure-detail-${f.ts.replace(/[^0-9]/g, "")}-${Math.random().toString(36).slice(2, 8)}`;
  const hasDetail = f.request || f.response;
  const programBit = f.program_id ? ` · Program: ${failuresProgramName(f.program_id)}` : "";
  const orderBit = f.order_id ? ` · Order: ${f.order_id}` : "";
  return `
    <div class="border border-slate-200 rounded-[0.3rem] p-3">
      <div class="flex items-start justify-between gap-3">
        <div>
          <span class="inline-flex items-center h-5 px-2 rounded-[0.3rem] text-[10px] font-medium uppercase tracking-wide bg-red-50 text-red-700 border border-red-200">${f.category}</span>
          <span class="text-xs text-slate-400 ml-2">${f.ts.replace("T", " ")}${orderBit}${programBit}</span>
        </div>
        ${hasDetail ? `<button type="button" onclick="document.getElementById('${detailId}').classList.toggle('hidden')" class="text-xs text-blue-600 hover:underline shrink-0">Details</button>` : ""}
      </div>
      <div class="text-sm text-slate-700 mt-1.5">${f.message}</div>
      ${hasDetail ? `
        <div id="${detailId}" class="hidden mt-2 pt-2 border-t border-slate-100 text-[11px] text-slate-500 font-mono whitespace-pre-wrap break-all">
          ${f.request ? `<div class="mb-1"><span class="text-slate-400">request:</span> ${JSON.stringify(f.request)}</div>` : ""}
          ${f.response ? `<div><span class="text-slate-400">response:</span> ${JSON.stringify(f.response)}</div>` : ""}
        </div>` : ""}
    </div>`;
}

// -------------------------------------------------------- Portfolio Safeguards
//
// Moved here from the Advanced OMS Risk Groups tab -- this is a genuine
// portfolio-wide (all Programs combined) setting, which fits Admin's
// "app-level configuration" role better than sitting inside one specific
// Risk Group's tab. Self-contained (fetches its own Programs/Risk Groups
// data) since this page doesn't share programs.js's cached globals.

async function loadPortfolioSafeguardsForm() {
  const el = document.getElementById("portfolioSafeguardsForm");
  if (!el) return;
  let cfg = {};
  let riskGroups = [];
  let programsList = [];
  try {
    [cfg, riskGroups, programsList] = await Promise.all([
      api("/api/portfolio-safeguards").catch(() => ({})),
      api("/api/risk-groups").catch(() => []),
      api("/api/programs").catch(() => []),
    ]);
  } catch (e) { /* defaults above cover this */ }

  const enabled = cfg.enabled !== false;
  const sumOfCaps = riskGroups.reduce((sum, g) => {
    const memberCaps = programsList
      .filter((p) => p.config.risk_group_id === g.risk_group_id)
      .map((p) => p.config.safeguards.daily_loss_amount || 0);
    return sum + (g.daily_loss_amount_override ?? memberCaps.reduce((a, b) => a + b, 0));
  }, 0);

  el.innerHTML = `
    <label class="flex items-center gap-2 text-sm text-slate-700">
      <input type="checkbox" id="portfolioSafeguardEnabled" ${enabled ? "checked" : ""} class="w-4 h-4" /> Enabled
    </label>
    <div class="flex items-end gap-3">
      <div>
        <label class="field-label">Override (₹) -- blank = sum of Risk Groups' own caps (currently ₹${fmt(sumOfCaps)})</label>
        <input id="portfolioCapOverride" type="number" step="any" min="1" placeholder="e.g. 10000"
          value="${cfg.daily_loss_amount_override ?? ""}" class="field-input w-64" ${enabled ? "" : "disabled"} />
      </div>
      <button type="button" onclick="savePortfolioSafeguards()" class="relative inline-flex items-center h-10 px-4 rounded-[0.3rem] text-xs font-medium bg-blue-600 text-white shadow-sm hover:bg-blue-700">Save</button>
    </div>`;
  document.getElementById("portfolioSafeguardEnabled").addEventListener("change", (e) => {
    document.getElementById("portfolioCapOverride").disabled = !e.target.checked;
  });
}

async function savePortfolioSafeguards() {
  const raw = document.getElementById("portfolioCapOverride").value;
  const enabled = document.getElementById("portfolioSafeguardEnabled").checked;
  try {
    await api("/api/portfolio-safeguards", {
      method: "POST",
      body: JSON.stringify({ enabled, daily_loss_amount_override: raw === "" ? null : Number(raw) }),
    });
    toast("Portfolio safeguards saved.");
    loadPortfolioSafeguardsForm();
  } catch (e) {
    toast("Failed to save: " + e.message, "error");
  }
}
