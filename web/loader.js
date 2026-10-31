/* LOADER — page modules load the first time their page is opened, not all at start-up.

   Before this, every script of every page (~4.7 MB with the CDN libraries) was downloaded and run at start-up. Now the
   start-up set is only what the shell needs (dashboard, invest, analytics, tools, capital, ui_kit, sidedock, shell); each
   page's module is fetched when its tab is first clicked.

   How: a capture-phase click listener on any `.tab[data-tab]` (top bar, dropdown items, and every programmatic
   `.tab[...].click()` — command bar, deep links, "OPEN ›" links) checks the page's group; if it is not loaded yet the click
   is held, the scripts are loaded in order, and the same click is replayed — so each module's existing
   window.onToolsTab / onInvestTab / onExtraTab hook runs exactly as before.
   Globals other code calls before a module is loaded (openSecurity, openAccountModal, …) are stubs that load the module
   and forward the call. NX.loadGroup(name) loads a group on demand (e.g. the PDF libraries on first export).

   A version change of a module is made HERE (its ?v=), since index.html no longer lists these scripts. */
(function () {
  "use strict";
  window.NX = window.NX || {};
  const CDN = "https://cdn.jsdelivr.net/npm/";
  const GROUPS = {
    pdf: [`${CDN}html2canvas@1.4.1/dist/html2canvas.min.js`, `${CDN}jspdf@2.5.2/dist/jspdf.umd.min.js`],
    security: ["gp.js?v=20261010b", "deep.js?v=20261003d", "report.js?v=20261021a", "security.js?v=20261022a"],
    fund: ["fund.js?v=20261009b"],
    journal: ["journal.js?v=20261011c"],
    polymarket: ["pm_chart.js?v=20260924a", "polymarket.js?v=20260925b"],
    pmx: ["pm_markets.js?v=20261022a"],
    ntlab: ["ntlab.js?v=20261030b"],
    account: ["account_detail.js?v=20260929b"],
    acct: ["account_detail.js?v=20260929b", "accounts.js?v=20260929d"],
    hot: ["account_detail.js?v=20260929b", "hot.js?v=20260928b"],
    slab: ["account_detail.js?v=20260929b", "strat_lab.js?v=20260928b"],
    live: ["account_detail.js?v=20260929b", "trades_ui.js?v=20260929a", "realpanel.js?v=20261006a"],
    flow: ["flow.js?v=20260928a"],
    wealth: ["wealth.js?v=20260929g"],
    wall: ["wall.js?v=20261003d"],
    connect: ["connect.js?v=20261004b"],
    rwas: ["rwas.js?v=20261002b"],
  };
  const TAB_GROUP = { security: "security", fund: "fund", journal: "journal", polymarket: "polymarket", pmx: "pmx", ntlab: "ntlab", acct: "acct", hot: "hot",
    slab: "slab", live: "live", flow: "flow", wealth: "wealth", wall: "wall", connect: "connect", rwas: "rwas" };

  const loaded = new Set(), pending = new Map(), BEFORE = {};
  function script(src) {
    if (loaded.has(src)) return Promise.resolve();
    if (pending.has(src)) return pending.get(src);
    const hook = BEFORE[src.split("?")[0]]; if (hook) hook();
    const p = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.async = false;
      s.onload = () => { loaded.add(src); pending.delete(src); res(); };
      s.onerror = () => { pending.delete(src); rej(new Error(`could not load ${src.split("?")[0]}`)); };
      document.body.appendChild(s);
    });
    pending.set(src, p);
    return p;
  }
  async function loadGroup(name) {
    const list = GROUPS[name] || [];
    document.documentElement.classList.add("nx-loading");
    try { for (const src of list) await script(src); } // in order: later files use what earlier ones define
    finally { if (!pending.size) document.documentElement.classList.remove("nx-loading"); }
  }
  const groupLoaded = (name) => (GROUPS[name] || []).every((s) => loaded.has(s));
  NX.loadGroup = loadGroup;
  NX.groupLoaded = groupLoaded;

  // hold a page click until its module is in, then replay it
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".tab[data-tab]");
    if (!btn) return;
    const g = TAB_GROUP[btn.dataset.tab];
    if (!g || groupLoaded(g)) return;
    e.stopImmediatePropagation(); e.preventDefault();
    loadGroup(g).then(() => btn.click()).catch((err) => { if (window.showToast) window.showToast(String(err.message || err), true); });
  }, true);

  // globals that pages call before their module exists: load it, then forward the call to the real function
  function stub(name, group) {
    if (typeof window[name] === "function") return;
    const s = function (...args) {
      return loadGroup(group).then(() => (window[name] !== s && typeof window[name] === "function" ? window[name](...args) : undefined));
    };
    window[name] = s;
  }
  stub("openSecurity", "security");
  stub("openSecurityQuery", "security");
  stub("openFullReport", "security");
  // dashboard.js declares the old account modal; account_detail.js keeps it as openLegacyAccountModal and installs the new one.
  // Until it loads, a stub forwards — and the old function is put back right before account_detail.js runs, so it records
  // the real legacy modal (not the stub) and nothing can loop.
  const legacyModal = window.openAccountModal;
  const acctStub = function (...args) { return loadGroup("account").then(() => (window.openAccountModal !== acctStub ? window.openAccountModal(...args) : undefined)); };
  window.openAccountModal = acctStub;
  stub("openAccountDetail", "account");
  BEFORE["account_detail.js"] = () => { if (window.openAccountModal === acctStub) window.openAccountModal = legacyModal; };

  // warm the most used modules once the start-up work is done, so the first click on them is instant
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 2500));
  window.addEventListener("load", () => idle(() => { ["account", "security"].forEach((g) => loadGroup(g).catch(() => {})); }, { timeout: 8000 }));
})();
