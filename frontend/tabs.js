// Top-level nav: Portfolio (the brand/logo link) / Regular OMS / Advanced OMS.
// "portfolio" is a real section now (combined KPIs + a combined Calendar),
// not just a KPI strip sitting above the other two -- clicking the brand
// always returns here. Not assumed to stay at exactly these 3 forever;
// nothing here hardcodes "there are only 2 OMS types."
//
// Advanced OMS gets its own accent (default indigo, user-configurable in
// Admin) as a deliberate visual differentiator -- so which "mode" you're
// in is legible at a glance: the nav link itself, the whole page
// background (body.adv-active, see style.css), and every control/tab
// within its own section (see programs.js and the adv-tab-* classes in
// index.html).
const SECTION_ACTIVE_CLASSES_BY_NAME = {
  regular: ["bg-blue-600", "text-white", "font-semibold"],
  advanced: ["adv-accent-bg-600", "text-white", "font-semibold"],
  journal: ["bg-slate-700", "text-white", "font-semibold"],
};
const SECTION_INACTIVE_CLASSES = ["text-slate-500"];
const ALL_SECTION_ACTIVE_CLASSES = ["bg-blue-600", "adv-accent-bg-600", "bg-slate-700", "text-white", "font-semibold"];

function switchSection(name) {
  document.querySelectorAll(".nav-link").forEach((b) => {
    const isActive = b.dataset.nav === name;
    ALL_SECTION_ACTIVE_CLASSES.forEach((c) => b.classList.remove(c));
    if (isActive) (SECTION_ACTIVE_CLASSES_BY_NAME[name] || []).forEach((c) => b.classList.add(c));
    SECTION_INACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, !isActive));
  });
  document.querySelectorAll(".section-panel").forEach((p) => {
    p.classList.toggle("hidden", p.id !== `section-${name}`);
  });
  document.body.classList.toggle("adv-active", name === "advanced");
  if (name === "portfolio") loadPortfolioSection();
  if (name === "advanced") loadAdvancedOms();
  if (name === "journal") loadJournalSection();
  // top-level section IS reflected in the URL (bookmarkable "which OMS am
  // I in"); this is deliberately different from the sub-tabs below, which
  // are NOT reflected in the URL at all
  const url = name === "portfolio" ? "/" : `/?section=${name}`;
  history.replaceState(null, "", url);
}

async function loadPortfolioSection() {
  // KPIs are kept live by dashboard.js's render(), which runs continuously
  // via the websocket regardless of which section is currently visible --
  // same as Regular OMS's own KPIs. Only the calendar needs an explicit
  // load here.
  portfolioCalendar.load();
}

const TAB_ACTIVE_CLASSES = ["text-blue-600", "border-blue-600"];
const TAB_INACTIVE_CLASSES = ["text-slate-400", "border-transparent"];

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    const isActive = b.dataset.tab === name;
    b.classList.toggle("font-semibold", isActive);
    TAB_ACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, isActive));
    TAB_INACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, !isActive));
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("hidden", p.id !== `tab-${name}`);
  });
  if (name === "calendar") regularCalendar.load();
  if (name === "archive") loadArchive();
  if (name === "dashboard") loadDashboard();
  if (name === "strategies") loadStrategies();
  // deliberately NOT reflected in the URL -- sub-tabs within an OMS
  // section are just a view state, not something worth a distinct URL
}

const ADV_TAB_ACTIVE_CLASSES = ["adv-accent-text-600", "adv-accent-border-600"];
const ADV_TAB_INACTIVE_CLASSES = ["text-slate-400", "border-transparent"];

function switchAdvTab(name) {
  document.querySelectorAll(".adv-tab-btn").forEach((b) => {
    const isActive = b.dataset.advTab === name;
    b.classList.toggle("font-semibold", isActive);
    ADV_TAB_ACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, isActive));
    ADV_TAB_INACTIVE_CLASSES.forEach((c) => b.classList.toggle(c, !isActive));
  });
  document.querySelectorAll(".adv-tab-panel").forEach((p) => {
    p.classList.toggle("hidden", p.id !== `advtab-${name}`);
  });
  if (name === "calendar") advancedCalendar.load();
}

// supports a ?section= (top-level nav) query param, e.g. from the old
// standalone /calendar or /archive URLs, which now redirect here. ?tab=
// is still accepted for the same old-link backward-compat reason, even
// though switchTab itself no longer sets it going forward.
(function initialSection() {
  const params = new URLSearchParams(location.search);
  const section = params.get("section");
  if (section === "advanced" || section === "regular" || section === "journal") {
    switchSection(section);
  } else {
    switchSection("portfolio"); // the default landing view for a plain visit to "/"
  }
  const t = params.get("tab");
  if (t === "calendar" || t === "archive" || t === "strategies") switchTab(t);
  const reorderId = params.get("reorder");
  if (reorderId) showNewOrderDialog(reorderId);
})();
