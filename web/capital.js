/* CAPITAL page + Bloomberg-style header behaviour.
   Depends on globals from dashboard.js: api(), openAccountModal(), showToast(). */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ formatting
  const f = {
    px(n) {
      if (n === null || n === undefined || Number.isNaN(n)) return "—";
      if (Math.abs(n) >= 1) return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      return Number(n.toPrecision(3)).toString();
    },
    pct(n, sign = true) {
      if (n === null || n === undefined || Number.isNaN(n)) return "—";
      return `${sign && n > 0 ? "+" : ""}${n.toFixed(2)}%`;
    },
    money(n, dec = 0) {
      if (n === null || n === undefined || Number.isNaN(n)) return "—";
      return `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
    },
    signed(n, dec = 0) {
      if (n === null || n === undefined || Number.isNaN(n)) return "—";
      return `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
    },
    compact(n) {
      if (!n) return "—";
      const a = Math.abs(n);
      if (a >= 1e12) return (n / 1e12).toFixed(2) + "T";
      if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
      if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
      if (a >= 1e3) return (n / 1e3).toFixed(0) + "K";
      return String(Math.round(n));
    },
    qty(n) { return n >= 100 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : Number(n.toPrecision(6)).toString(); },
    hm(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }); },
  };
  const dir = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");

  function spark(values, up, w = 54, h = 16) {
    if (!values || values.length < 2) return "";
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 1 - ((v - lo) / span) * (h - 2)).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle"><polyline fill="none" stroke="${up ? "#2be07e" : "#ff4a4a"}" stroke-width="1.2" points="${pts}"/></svg>`;
  }

  // flash a cell when its numeric value changed since last render
  const lastVals = new Map();
  function flash(el, key, value) {
    if (!el) return;
    const prev = lastVals.get(key);
    lastVals.set(key, value);
    if (prev === undefined || prev === value) return;
    const cls = value > prev ? "fl-up" : "fl-dn";
    el.classList.remove("fl-up", "fl-dn");
    void el.offsetWidth;
    el.classList.add(cls);
  }

  const visible = () => !document.hidden && $("#view-capital")?.classList.contains("active");

  // ------------------------------------------------------------------ ticker tape (global, all tabs)
  const tapeState = { syms: "" };

  function tkHtml(it) {
    const d = it.pct >= 0 ? "up" : "dn";
    return `<span class="tk ${d}" data-sym="${esc(it.sym)}"><span class="tk-l">${esc(it.label)}</span><span class="tk-p">${f.px(it.price)}</span><span class="tk-c">${it.pct >= 0 ? "▲" : "▼"} ${f.pct(it.pct)}</span></span>`;
  }

  window.loadTickerTape = async function loadTickerTape() {
    try {
      const { items } = await api("/capital/tape");
      if (!items.length) return;
      const track = $("#ticker-track");
      const key = items.map((i) => i.sym).join("|");
      if (key !== tapeState.syms) {
        // structure changed (first load): build two copies for the seamless loop
        const seg = items.map(tkHtml).join("");
        track.innerHTML = seg + seg;
        tapeState.syms = key;
        items.forEach((it) => lastVals.set(`tk:${it.sym}`, it.price));
        return;
      }
      for (const it of items) {
        track.querySelectorAll(`.tk[data-sym="${CSS.escape(it.sym)}"]`).forEach((el) => {
          const prev = lastVals.get(`tk:${it.sym}`);
          el.classList.toggle("up", it.pct >= 0);
          el.classList.toggle("dn", it.pct < 0);
          el.querySelector(".tk-p").textContent = f.px(it.price);
          el.querySelector(".tk-c").textContent = `${it.pct >= 0 ? "▲" : "▼"} ${f.pct(it.pct)}`;
          if (prev !== undefined && prev !== it.price) {
            el.classList.remove("fl-up", "fl-dn");
            void el.offsetWidth;
            el.classList.add(it.price > prev ? "fl-up" : "fl-dn");
          }
        });
        lastVals.set(`tk:${it.sym}`, it.price);
      }
    } catch (e) { /* atmosphere; keep whatever is showing */ }
  };

  // ------------------------------------------------------------------ header: command line, clocks, status
  const FUNCTIONS = [
    { code: "CAP", tab: "capital", desc: "HOME — NOSTRO P&L, news, Polymarket, markets, wealth", alias: ["CAPITAL", "HOME", "ACCT", "ACCOUNTS"] },
    { code: "MKT", tab: "mkts", desc: "MARKETS › MARKETS — world monitor", alias: ["MARKETS", "WEI"] },
    { code: "STK", tab: "stocks", desc: "MARKETS › STOCKS — equity monitor", alias: ["STOCKS", "EQUITIES"] },
    { code: "CRYPTO", tab: "crypto", desc: "MARKETS › CRYPTO — coin monitor", alias: ["COIN", "COINS"] },
    { code: "ETF", tab: "funds", desc: "MARKETS › ETFs & FUNDS", alias: ["ETFS", "FUNDS"] },
    { code: "FX", tab: "fxc", desc: "MARKETS › FX & COMMODITIES", alias: ["CMDTY", "COMMODITIES", "CURRENCIES"] },
    { code: "ECST", tab: "econ", desc: "MARKETS › ECONOMY — macro dashboard (FRED)", alias: ["ECONOMY", "MACRO"] },
    { code: "ECO", tab: "cal", sub: "eco", desc: "MARKETS › CALENDAR — economic releases + trading blackout", alias: ["CAL", "CALENDAR", "ECON"] },
    { code: "EVTS", tab: "cal", sub: "evts", desc: "MARKETS › CALENDAR — earnings calendar", alias: ["ERN", "EARNINGS"] },
    { code: "GC", tab: "rates", sub: "gc", desc: "MARKETS › RATES — US Treasury yield curve", alias: ["CURVE", "YIELDS", "RATES"] },
    { code: "WIRP", tab: "rates", sub: "wirp", desc: "MARKETS › RATES — Fed rate path from futures", alias: ["FED", "FOMC"] },
    { code: "EQS", tab: "eqs", desc: "MARKETS › SCREENER — equity screener", alias: ["SCREENER", "SCREEN"] },
    { code: "REAL", tab: "connect", desc: "NOSTRO › REAL ACCOUNT SETUP — connect TradersPost + Telegram, signals sent, real balance", alias: ["CONNECT", "SETUP", "WEBHOOK", "TELEGRAM"] },
    { code: "RWAS", tab: "rwas", desc: "MARKETS › RWAS — stablecoin supply, mints and burns, by chain and peg type", alias: ["STABLECOINS", "STABLES", "USDT"] },
    { code: "WALL", tab: "wall", desc: "MARKETS › LIVE WALL — up to 50 live charts from your watchlist", alias: ["LIVEWALL", "CHARTS"] },
    { code: "IMAP", tab: "heat", desc: "MARKETS › HEATMAP — S&P 500 / Nasdaq-100 treemap", alias: ["HEATMAP", "MAP"] },
    { code: "COMP", tab: "ana", sub: "comp", desc: "MARKETS › ANALYTICS — relative performance", alias: ["COMPARE", "PERF"] },
    { code: "CORR", tab: "ana", sub: "corr", desc: "MARKETS › ANALYTICS — correlation matrix", alias: ["CORRELATION"] },
    { code: "SEAS", tab: "ana", sub: "seas", desc: "MARKETS › ANALYTICS — seasonality", alias: ["SEASONALITY"] },
    { code: "COT", tab: "ana", sub: "cot", desc: "MARKETS › ANALYTICS — CFTC futures positioning", alias: ["POSITIONING"] },
    { code: "OVDV", tab: "opts", sub: "ovdv", desc: "MARKETS › OPTIONS LAB — vol surface, skew, IV rank", alias: ["VOL", "SURFACE", "OPTIONS"] },
    { code: "OVME", tab: "opts", sub: "ovme", desc: "MARKETS › OPTIONS LAB — pricer, Greeks, payoff builder", alias: ["PRICER", "PAYOFF"] },
    { code: "UOA", tab: "opts", sub: "uoa", desc: "MARKETS › OPTIONS LAB — unusual options activity", alias: ["FLOW", "UNUSUAL"] },
    { code: "PORT", tab: "port", desc: "MARKETS › PORTFOLIO RISK — beta, VaR, Sharpe, scenarios", alias: ["RISK", "PRTU"] },
    { code: "ALRT", tab: "alrt", desc: "MARKETS › ALERTS — price, move, volume, news, releases → Telegram", alias: ["ALERT", "ALERTS"] },
    { code: "WKSP", tab: "wksp", desc: "MARKETS › WORKSPACE — multi-panel layouts you can save", alias: ["WORKSPACE", "LAYOUT", "LP"] },
    { code: "WFO", tab: "wfo", desc: "NOSTRO › RESEARCH › WALK-FORWARD — rolling re-optimise and unseen test", alias: ["WALKFORWARD"] },
    { code: "REGIME", tab: "regime", desc: "NOSTRO › RESEARCH › REGIMES — strategy results by market regime", alias: ["REGIMES"] },
    { code: "EXEC", tab: "exec", desc: "NOSTRO › RESEARCH › EXECUTION — costs, timing, blackout", alias: ["EXECUTION", "BLACKOUT"] },
    { code: "NEWS", tab: "news", desc: "NEWS — market wire, critical events, what moved the market, calendar", alias: ["WIRE", "TOP", "HEADLINES"] },
    { code: "PM", tab: "pmx", desc: "POLYMARKET › MARKETS — whole market: trending, odds moves, whale trades, linked news", alias: ["PREDICT", "ODDS", "BETS", "PMX"] },
    { code: "PMOMO", tab: "polymarket", desc: "POLYMARKET — momo strategy paper accounts, trainer, momo radar, session chart", alias: ["POLYMARKET", "MOMO", "BUZZER"] },
    { code: "SPIDER", tab: "__spider", desc: "SPIDER — the news brain: what it marked as important, ask it anything about the news", alias: ["SPDR", "BRAIN", "ASK"] },
    { code: "HELP", tab: "__help", desc: "HELP — every function code and shortcut", alias: ["?", "KEYS"] },
    { code: "SEC", tab: "security", desc: "MARKETS › SECURITY — deep dive on any ticker. Try “SEC AAPL”, “GP TSLA”, “FA MSFT”, “N BTC”", alias: ["SECURITY", "S"] },
    { code: "LIVE", tab: "live", desc: "NOSTRO › LIVE TRADES — every trade, as it happens", alias: ["TRADES"] },
    { code: "STRAT", tab: "strategies", desc: "NOSTRO › STRATEGIES — library and skill levels", alias: ["STRATEGIES"] },
    { code: "TRAIN", tab: "trainer", desc: "NOSTRO › TRAINER — champion pipeline", alias: ["TRAINER"] },
    { code: "FIELD", tab: "field", desc: "NOSTRO › RESEARCH › FIELD — every candidate on one chart", alias: [] },
    { code: "BT", tab: "backtest", desc: "NOSTRO › BACKTEST — run a strategy on history", alias: ["BACKTEST"] },
    { code: "JRNL", tab: "journal", desc: "NOSTRO › JOURNAL — trade notes, tags, screenshots, mistake analysis", alias: ["JOURNAL"] },
    { code: "RSCH", tab: "field", desc: "NOSTRO › RESEARCH — field, walk-forward, regimes, execution", alias: ["RESEARCH"] },
    { code: "ACC", tab: "acct", desc: "NOSTRO › ACCOUNTS — every account, passed, today's P&L", alias: ["NOSTRO"] },
    { code: "LAB", tab: "slab", desc: "NOSTRO › STRATEGY LAB — Apex pass odds, compare, recommended risk", alias: ["STRATLAB"] },
    { code: "WLTH", tab: "wealth", desc: "WEALTH — net worth, cash flow, investments, spending, business", alias: ["WEALTH", "MONEY", "NW"] },
    { code: "HOT", tab: "hot", desc: "MARKETS › HOT STOCKS — morning brief picks and track record", alias: ["BRIEF"] },
    { code: "MOST", tab: "flow", desc: "MARKETS › MOST / DARK POOLS — most active, off-exchange prints", alias: ["DARKPOOL", "DARK"] },
    { code: "FUND", tab: "fund", desc: "MARKETS › FUND — portfolio, NAV, risk limits, WHY on every holding", alias: ["NAV"] },
  ];

  const CLASS_WORD = /\b(EQUITY|INDEX|CURNCY|CMDTY|COMDTY|CRYPTO|GO)\b/i;
  // command-first security syntax: "SEC AAPL", "GP TSLA", "FA MSFT", "N BTC" — the word up front picks the function,
  // the rest is the security (ticker, name, or Bloomberg-style "AAPL US"). Mirrors /invest/resolve on the backend.
  const SEC_CMD = { SEC: null, GP: "GP", DES: "DES", FA: "FA", N: "N", NEWS: "N", ANR: "ANR", HDS: "HDS", FIL: "FIL", FILINGS: "FIL", DVD: "DVD", OMON: "OMON", OPT: "OMON", RV: "RV", PEERS: "RV", DATA: "DATA", LP: "LP", LAUNCHPAD: "LP" };
  const leadingSecCmd = (q) => {
    const m = q.trim().match(/^(\S+)\s+(\S.*)$/);
    if (!m || !(m[1].toUpperCase() in SEC_CMD)) return null;
    return { word: m[1].toUpperCase(), fn: SEC_CMD[m[1].toUpperCase()], rest: m[2] };
  };

  function goTab(tab, sub, preset) {
    if (tab === "__help") return window.showHelp && window.showHelp();
    if (tab === "__spider") return window.NX && NX.spider && NX.spider.open();
    const btn = document.querySelector(`.tab[data-tab="${tab}"]`);
    if (btn) btn.click();
    if (preset && window.NX && NX.world) NX.world.preset(preset);
    if (sub && window.NX && NX.SUB && NX.SUB[tab]) NX.SUB[tab](sub);
  }
  window.runFunction = (code) => { const U = String(code).toUpperCase(), fn = FUNCTIONS.find((f) => f.code === U || f.alias.includes(U)); if (fn) goTab(fn.tab, fn.sub, fn.preset); };

  const isSecurityQuery = (q) => q.trim().split(/\s+/).length >= 2 || CLASS_WORD.test(q);
  // shared with initCommand()'s own Enter-key handling, and with the "AI <question>" command: runs a command-bar
  // string exactly as if the user had typed it and pressed Enter (a page code, or "SEC AAPL" / "GP TSLA" / a ticker).
  function openCommand(text) {
    const q = String(text || "").trim();
    if (!q) return;
    const U = q.toUpperCase();
    const exact = FUNCTIONS.find((fn) => fn.code === U || fn.alias.includes(U));
    if (!isSecurityQuery(q) && exact) return goTab(exact.tab, exact.sub, exact.preset);
    window.openSecurityQuery && openSecurityQuery(q);
  }
  window.runCommand = openCommand;

  // ------------------------------------------------------------------ "AI <question>" in the command line
  const leadingAI = (q) => { const m = q.match(/^ai[\s:]+(\S.*)$/i); return m ? m[1].trim() : null; };
  function showAiAnswer(question, text, isErr) {
    let d = $("#nx-ai");
    if (!d) {
      d = document.createElement("div"); d.id = "nx-ai"; d.className = "nx-help";
      d.addEventListener("click", (e) => { if (e.target === d) d.remove(); });
      document.body.appendChild(d);
    }
    d.innerHTML = `<div class="nx-help-box nx-ai-box">
      <div class="bb-ph"><span class="bb-n">AI</span>ANSWER<span class="sp"></span><span class="meta">Esc or click outside to close</span></div>
      <div class="nx-ai-q">${esc(question)}</div>
      <div class="nx-ai-a ${isErr ? "dn" : ""}">${esc(text).replace(/\n/g, "<br>")}</div>
    </div>`;
  }
  // AI decided this is a "find/recommend a product" request: it does not trust its own memory for live tickers,
  // leverage multiples, expense ratios etc., so it hands back a search phrase instead of naming one. Run that
  // search on the real STOCKS page (any stock, ETF or coin — backed by /invest/search, not the model's memory)
  // so the user picks from what actually exists and can click straight into it (SECURITY DEEP DIVE opens on click).
  function runAiSearch(query) {
    goTab("stocks");
    setTimeout(() => {
      const inp = document.getElementById("st-q");
      if (!inp) return;
      inp.value = query;
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      inp.focus();
    }, 80);
  }
  // AI decided the user wants something done ON the chart currently on screen (mark highs/lows, etc.) rather than
  // navigate or answer. window.NX.chartAI is set by whichever chart-owning page is currently active (see gp.js) -
  // if nothing has registered one, there's no chart open to act on.
  function runAiChartAction(action, opts) {
    // NX.chartAI can outlive the Security tab being on screen (switching to another top-level
    // tab doesn't tear the chart down, only switching security or function does) - require the
    // Security view to actually be the visible one, or "mark the graph" could silently act on a
    // chart the user isn't even looking at right now.
    const onScreen = document.querySelector("#view-security.active");
    const handler = onScreen && window.NX && NX.chartAI;
    const fn = action.replace(/_([a-z])/g, (_, c) => c.toUpperCase()); // mark_highs_lows -> markHighsLows
    if (!handler || typeof handler[fn] !== "function") { showToast("Open a price chart first (SEC <ticker>, then the GP tab) to mark it."); return; }
    try { handler[fn](opts || {}); } catch (e) { showToast(`Couldn't mark the chart: ${e.message || e}`); }
  }
  async function runAi(question) {
    showToast("Asking AI…");
    try {
      const r = await api(`/ai/command?q=${encodeURIComponent(question)}`);
      if (r.type === "command" && r.cmd) { openCommand(r.cmd); showToast(r.note ? `AI → ${r.note}` : `AI → ${r.cmd}`); }
      else if (r.type === "search" && r.query) { runAiSearch(r.query); showToast(r.note ? `AI → ${r.note}` : `AI → searching “${r.query}”`); }
      else if (r.type === "chart_action" && r.action) { runAiChartAction(r.action, { sensitivity: r.sensitivity, side: r.side }); showToast(r.note || `AI → ${r.action}`); }
      else showAiAnswer(question, r.text || "No answer.");
    } catch (e) { showAiAnswer(question, String(e.message || e), true); }
  }
  window.runAi = runAi;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#nx-ai")) $("#nx-ai").remove(); });

  // every drop-down menu (INVESTING, NOSTRO): opens on hover or click, closes on outside click / Esc / picking an item
  function initMenus() {
    document.querySelectorAll(".tab-menu").forEach((menu) => {
      const btn = menu.querySelector(".tab-menu-btn"), pop = menu.querySelector(".tab-menu-pop");
      let timer = null;
      const open = () => {
        document.querySelectorAll(".tab-menu").forEach((m) => { if (m !== menu) { m.classList.remove("open"); m.querySelector(".tab-menu-pop").classList.add("hidden"); } });
        clearTimeout(timer); pop.classList.remove("hidden"); btn.setAttribute("aria-expanded", "true"); menu.classList.add("open");
      };
      const close = () => { pop.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); menu.classList.remove("open"); };
      btn.addEventListener("click", (e) => { e.stopPropagation(); open(); });
      menu.addEventListener("mouseenter", open);
      menu.addEventListener("mouseleave", () => { timer = setTimeout(close, 220); });
      pop.addEventListener("click", close);
      document.addEventListener("click", (e) => { if (!menu.contains(e.target)) close(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    });
  }

  // phone width: the top .tabs bar becomes a slide-in drawer behind #nx-burger (pure CSS repositioning —
  // initMenus() above is untouched, so the WEALTH/INVESTING/NOSTRO dropdowns still work exactly the same,
  // just flowing inline in the drawer instead of popping over the page).
  function initMobileNav() {
    const burger = $("#nx-burger"), tabs = $("#tabs");
    if (!burger || !tabs) return;
    const setOpen = (open) => { document.body.classList.toggle("nav-open", open); burger.classList.toggle("on", open); burger.setAttribute("aria-expanded", String(open)); };
    burger.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!document.body.classList.contains("nav-open")); });
    tabs.addEventListener("click", (e) => { if (e.target.closest(".tab")) setOpen(false); });
    document.addEventListener("click", (e) => { if (document.body.classList.contains("nav-open") && !e.target.closest("#tabs") && !e.target.closest("#nx-burger")) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }

  // called by dashboard.js on every tab switch: highlight the owning menu and show where you are
  window.onTabChange = function onTabChange(tab) {
    document.querySelectorAll(".tab-menu").forEach((menu) => {
      // several items can open the same view (WEALTH pages): the one marked .cur is where you are
      const item = menu.querySelector(`.tab.sub[data-tab="${tab}"].cur`) || menu.querySelector(`.tab.sub[data-tab="${tab}"]`);
      menu.querySelector(".tab-menu-btn").classList.toggle("active", !!item);
      const crumb = menu.querySelector(".crumb");
      if (crumb) crumb.textContent = item ? ` › ${item.querySelector("b").textContent}` : "";
    });
    // NOSTRO › RESEARCH: one menu item, four pages (field / walk-forward / regimes / execution) under a shared sub-menu
    const rn = document.getElementById("nx-research-nav");
    if (rn) {
      const on = ["field", "wfo", "regime", "exec"].includes(tab);
      rn.hidden = !on;
      rn.querySelectorAll("[data-go]").forEach((b) => b.classList.toggle("on", b.dataset.go === tab));
      if (!rn.dataset.bound) { rn.dataset.bound = "1"; rn.addEventListener("click", (e) => { const g = e.target.closest("[data-go]"); if (g) document.querySelector(`.tab[data-tab="${g.dataset.go}"]`)?.click(); }); }
    }
    pageHead(tab);
    if (window.onInvestTab) window.onInvestTab(tab);
    if (window.onExtraTab) window.onExtraTab(tab);
  };

  // one header line on every page: where you are (SECTION › PAGE) and what the page is for — taken from the menu item
  // itself (its <b> and <span>), so a page's description lives in one place
  const TOP_PAGES = {
    capital: ["HOME", "your trading, money, news and markets at a glance"],
    news: ["NEWS", "every headline that can move a price · what actually moved · what is coming"],
  };
  function pageHead(tab) {
    let el = document.getElementById("nx-phead");
    if (!el) {
      const app = document.getElementById("app"); if (!app) return;
      el = document.createElement("div"); el.id = "nx-phead"; el.className = "nx-phead";
      app.insertBefore(el, app.firstChild);
    }
    const item = document.querySelector(`.tab.sub[data-tab="${tab}"].cur`) || document.querySelector(`.tab.sub[data-tab="${tab}"]`);
    let path, what;
    if (item) {
      const sec = item.closest(".tab-menu")?.querySelector(".tab-menu-btn")?.childNodes;
      const secName = sec ? [...sec].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("") : "";
      path = `${secName}${secName ? " › " : ""}${item.querySelector("b")?.textContent || tab.toUpperCase()}`;
      what = item.querySelector("span")?.textContent || "";
    } else if (TOP_PAGES[tab]) [path, what] = TOP_PAGES[tab];
    else { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<b>${esc(path)}</b><span>${esc(what)}</span>`;
  }

  // menu items that open a section of a page (POLYMARKET › COPYTRADER): mark the item as where you are, then scroll to it
  document.addEventListener("click", (e) => {
    const it = e.target.closest(".tab.sub[data-tab]");
    if (!it || it.dataset.wpage) return;
    document.querySelectorAll(`.tab.sub[data-tab="${it.dataset.tab}"]`).forEach((x) => x.classList.toggle("cur", x === it));
    const id = it.dataset.scroll; if (!id) return;
    let n = 0;
    const t = setInterval(() => { const el = document.getElementById(id); if (el || ++n > 40) { clearInterval(t); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); } }, 100);
  }, true);  // capture: marked before the page switch reads it for the header

  function initCommand() {
    const input = $("#bb-cmd"), hints = $("#bb-cmd-hints"), go = $("#bb-go");
    if (!input) return;
    let sel = 0, entries = [], secHits = [], secTimer = null, secSeq = 0;

    const fnMatches = (q) => {
      q = q.trim().toUpperCase();
      if (!q) return FUNCTIONS;
      const rank = (fn) => (fn.code === q ? 0 : fn.code.startsWith(q) ? 1 : fn.alias.includes(q) ? 2 : fn.alias.some((a) => a.startsWith(q)) ? 3 : 9);
      const pre = FUNCTIONS.filter((fn) => rank(fn) < 9).sort((a, b) => rank(a) - rank(b));
      return pre.length ? pre : [];
    };
    const render = () => {
      const q = input.value, aiQ = leadingAI(q);
      if (aiQ !== null) {
        entries = [{ kind: "ai", text: aiQ }];
        sel = 0;
        hints.classList.toggle("hidden", document.activeElement !== input);
        hints.innerHTML = `<div class="bb-hint sel ai" data-i="0"><b>AI</b><span>Ask: <em>${esc(aiQ)}</em> — press Enter</span></div>`;
        return;
      }
      if (/^ai$/i.test(q.trim())) {
        entries = [];
        hints.classList.toggle("hidden", document.activeElement !== input);
        hints.innerHTML = `<div class="bb-hint ai"><b>AI</b><span>Keep typing your question — “AI take me to stocks”, “AI show me apple's chart”, “AI what is a Wyckoff spring?”</span></div>`;
        return;
      }
      const cmd = leadingSecCmd(q);
      const fns = isSecurityQuery(q) ? [] : fnMatches(q);
      entries = [...fns.map((fn) => ({ kind: "fn", fn })), ...secHits.map((h) => ({ kind: "sec", hit: h, fn: cmd ? cmd.fn : null }))];
      if (q.trim().length >= 1 && !fns.some((f) => f.code === q.trim().toUpperCase())) {
        entries.push({ kind: "go", text: q.trim(), cmd });
      }
      sel = Math.min(sel, Math.max(0, entries.length - 1));
      hints.classList.toggle("hidden", !entries.length || document.activeElement !== input);
      hints.innerHTML = entries.map((e, i) => {
        const cls = `bb-hint ${i === sel ? "sel" : ""}`;
        if (e.kind === "fn") return `<div class="${cls}" data-i="${i}"><b>${e.fn.code}</b><span>${esc(e.fn.desc)}</span></div>`;
        if (e.kind === "sec") return `<div class="${cls} sec" data-i="${i}"><b>${esc(e.hit.symbol)}</b><span>${esc(e.hit.bbg)} — ${esc(e.hit.name)}${e.fn ? ` · opens ${e.fn}` : ""}</span></div>`;
        if (e.cmd) return `<div class="${cls} sec" data-i="${i}"><b>&#9166;</b><span>Open <em>${esc(e.cmd.rest.toUpperCase())}</em>${e.cmd.fn ? ` · ${e.cmd.fn}` : ""} &lt;GO&gt;</span></div>`;
        return `<div class="${cls} sec" data-i="${i}"><b>&#9166;</b><span>Open security: <em>${esc(e.text.toUpperCase())}</em> &lt;GO&gt;</span></div>`;
      }).join("");
    };
    const searchSecurities = () => {
      clearTimeout(secTimer);
      const q = input.value.trim();
      if (leadingAI(q) !== null) { secHits = []; return; }
      const cmd = leadingSecCmd(q);
      const term = (cmd ? cmd.rest : q).trim();
      if (term.length < 2 || term.split(/\s+/).length > 2) { secHits = []; return; }
      const seq = ++secSeq;
      secTimer = setTimeout(async () => {
        try {
          const r = await api(`/invest/search?q=${encodeURIComponent(term.replace(CLASS_WORD, "").trim() || term)}&limit=6`);
          if (seq === secSeq && document.activeElement === input) { secHits = r.results; render(); }
        } catch (e) { /* suggestions are a convenience */ }
      }, 240);
    };
    const finish = () => { input.value = ""; secHits = []; input.blur(); hints.classList.add("hidden"); };
    const run = (e) => {
      if (!e) return;
      if (e.kind === "fn") { finish(); goTab(e.fn.tab, e.fn.sub, e.fn.preset); }
      else if (e.kind === "sec") { finish(); window.openSecurity && openSecurity(e.hit.symbol, e.fn || undefined); }
      else if (e.kind === "ai") { finish(); runAi(e.text); }
      else { const t = e.text; finish(); window.openSecurityQuery && openSecurityQuery(t); }
    };
    const submit = () => {
      const q = input.value.trim();
      if (!q) return;
      const aiQ = leadingAI(q);
      if (aiQ !== null) { finish(); return runAi(aiQ); }
      const U = q.toUpperCase();
      const exact = FUNCTIONS.find((fn) => fn.code === U || fn.alias.includes(U));
      if (!isSecurityQuery(q) && exact) return run({ kind: "fn", fn: exact });
      if (!isSecurityQuery(q) && entries[sel] && entries[sel].kind === "fn") return run(entries[sel]);
      if (entries[sel] && entries[sel].kind === "sec" && !isSecurityQuery(q)) return run(entries[sel]);
      finish();
      window.openSecurityQuery && openSecurityQuery(q);
    };

    input.addEventListener("focus", () => { sel = 0; render(); });
    input.addEventListener("input", () => { sel = 0; secHits = []; render(); searchSecurities(); });
    input.addEventListener("blur", () => setTimeout(() => hints.classList.add("hidden"), 150));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { sel = Math.min(entries.length - 1, sel + 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); render(); e.preventDefault(); }
      else if (e.key === "Enter") submit();
      else if (e.key === "Escape") { input.value = ""; secHits = []; input.blur(); }
    });
    hints.addEventListener("mousedown", (e) => {
      const row = e.target.closest(".bb-hint");
      if (row) run(entries[Number(row.dataset.i)]);
    });
    go.addEventListener("click", submit);

    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") || !!document.activeElement?.isContentEditable;  // rich-text editors (journal) count as typing
      if ((e.key === "/" || e.key === "`") && !typing) { e.preventDefault(); input.focus(); return; }
      if (e.altKey && /^[1-9]$/.test(e.key)) { e.preventDefault(); goTab(FUNCTIONS[Number(e.key) - 1].tab); }
    });
  }

  function nyseState(now = new Date()) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(now);
    const g = (t) => parts.find((p) => p.type === t)?.value;
    const mins = Number(g("hour")) * 60 + Number(g("minute"));
    const wkday = !["Sat", "Sun"].includes(g("weekday"));
    if (wkday && mins >= 570 && mins < 960) return "open";
    if (wkday && ((mins >= 240 && mins < 570) || (mins >= 960 && mins < 1200))) return "ext";
    return "closed";
  }

  function tickClocks() {
    const now = new Date();
    const fmt = (tz) => now.toLocaleTimeString("en-GB", { timeZone: tz, hour12: false });
    const set = (id, tz) => { const el = $(id); if (el) el.textContent = fmt(tz); };
    set("#bb-clk-ny", "America/New_York");
    set("#bb-clk-ldn", "Europe/London");
    const st = nyseState(now);
    const led = $("#bb-mkt-led"), txt = $("#bb-mkt-txt");
    if (led) led.className = `bb-led ${st === "open" ? "on" : st === "ext" ? "warn" : "off"}`;
    if (txt) txt.textContent = st === "open" ? "NYSE OPEN" : st === "ext" ? "NYSE EXT HRS" : "NYSE CLOSED";
  }

  async function pollStatus() {
    try {
      const [live, tr] = await Promise.all([api("/live/status"), api("/trainer/status")]);
      const ok = !live.last_error;
      $("#bb-live-led").className = `bb-led ${ok ? "on" : "off"}`;
      $("#bb-live-txt").textContent = `LIVE ${live.live_account_count}${live.open_position_count ? ` · ${live.open_position_count} OPEN` : ""}`;
      const training = tr.stage && tr.stage !== "idle";
      $("#bb-train-led").className = `bb-led ${tr.last_error ? "off" : training ? "on" : "warn"}`;
      $("#bb-train-txt").textContent = training ? `TRAIN ${tr.stage_symbol || ""} ${tr.stage.replace("_", " ").toUpperCase()}` : "TRAIN IDLE";
    } catch (e) {
      $("#bb-live-led").className = "bb-led off";
      $("#bb-live-txt").textContent = "OFFLINE";
    }
  }

  // ------------------------------------------------------------------ CAPITAL page
  const cap = {
    init: false,
    pf: { filter: "all", data: null },
    hs: { tab: "hot" }, hc: { tab: "trending" }, hot: null,
    news: { filter: "all", items: [], seen: new Set(), first: true },
    summary: null,
    timers: [],
    showArchived: false,
  };

  // ---- KPIs
  function setKpi(id, text, cls = "") {
    const el = $(id);
    if (!el) return;
    el.className = `v ${cls}`;
    el.textContent = text;
  }
  function renderKpis() {
    const s = cap.summary, p = cap.pf.data;
    if (s) {
      const v = $("#kpi-equity");
      if (window.tweenNumberText) tweenNumberText(v, s.total_equity, (x) => f.money(x)); else v.textContent = f.money(s.total_equity);
      $("#kpi-equity-s").textContent = `${s.live_accounts_count} LIVE · ${s.eval_in_progress_count} EVAL RUNNING · ${s.eval_passed_count} PASSED`;
      // today = closed trades + open trades at the last price; before anything closes, the last session is shown underneath
      const net = s.today_net ?? s.today_pnl, ls = s.last_session;
      setKpi("#kpi-today", f.signed(net), dir(net));
      $("#kpi-today-s").innerHTML = s.today_trades_count
        ? `closed <span class="${dir(s.today_pnl)}">${f.signed(s.today_pnl)}</span> · open <span class="${dir(s.today_open_pnl)}">${f.signed(s.today_open_pnl ?? 0)}</span> · ${s.today_trades_count} TRADES`
        : `open <span class="${dir(s.today_open_pnl)}">${f.signed(s.today_open_pnl ?? 0)}</span>${ls ? ` · LAST SESSION ${ls.date.slice(5)}: <span class="${dir(ls.pnl)}">${f.signed(ls.pnl)}</span> (${ls.trades} trades, ${ls.wins}W)` : " · no trades closed yet today"}`;
      setKpi("#kpi-open", String(s.open_positions_count));
      $("#kpi-open-s").textContent = `${s.training_runs_today} TRAINING RUNS TODAY`;
    }
    if (p) {
      const t = p.totals;
      const v = $("#kpi-pf");
      if (window.tweenNumberText) tweenNumberText(v, t.value, (x) => f.money(x)); else v.textContent = f.money(t.value);
      $("#kpi-pf-s").innerHTML = p.holdings.length
        ? `<span class="${dir(t.pnl)}">${f.signed(t.pnl)} (${f.pct(t.pnl_pct)})</span> ALL-TIME`
        : "NO POSITIONS YET";
      setKpi("#kpi-pfday", p.holdings.length ? f.signed(t.day_pnl) : "—", dir(t.day_pnl));
      $("#kpi-pfday-s").innerHTML = p.holdings.length ? `<span class="${dir(t.day_pct)}">${f.pct(t.day_pct)}</span> TODAY` : "—";
    }
  }

  async function loadFearGreed() {
    try {
      const fg = await api("/fear-greed?symbol=NQ");
      const v = $("#kpi-fg");
      v.className = `v ${fg.value >= 60 ? "up" : fg.value <= 40 ? "dn" : ""}`;
      v.textContent = String(fg.value);
      $("#kpi-fg-s").textContent = `${String(fg.label).toUpperCase()} · RSI ${fg.components.rsi} · MOM ${fg.components.momentum}`;
    } catch (e) {
      $("#kpi-fg-s").textContent = "UNAVAILABLE";
    }
  }

  // an open account-detail window used to refresh via the Accounts page's poll; keep that alive here
  function refreshOpenAccountModal() {
    try {
      if (typeof isAccountModalOpen === "function" && isAccountModalOpen() && state.currentAccountId !== null) selectAccount(state.currentAccountId);
    } catch (e) { /* modal is best-effort */ }
  }

  // ---- 1) NOSTRO accounts
  async function loadAccountsPanel() {
    try {
      const [summary, acc] = await Promise.all([api("/dashboard/summary"), api(`/accounts?include_archived=${cap.showArchived ? "true" : "false"}`)]);
      cap.summary = summary;
      const rows = acc.accounts.slice().sort((a, b) => {
        const rank = (x) => (x.status === "live" ? 0 : x.eval_status === "in_progress" ? 1 : 2);
        return rank(a) - rank(b) || b.balance - a.balance;
      });
      const live = rows.filter((a) => a.status === "live").length;
      const evalN = rows.filter((a) => a.eval_status === "in_progress").length;
      $("#acc-meta").textContent = `${live} LIVE · ${evalN} EVAL · ${rows.length} TOTAL`;
      const body = $("#acc-body");
      body.innerHTML = `<table class="bb-tbl"><thead><tr>
        <th></th><th>ID</th><th>ACCOUNT</th><th>TYPE</th><th>STRATEGY</th><th class="r">EQUITY</th><th class="r">P&amp;L</th><th class="r">%</th><th>EVAL</th></tr></thead><tbody>${rows.map((a) => {
        const pnl = a.balance - a.starting_balance, pct = a.starting_balance ? (pnl / a.starting_balance) * 100 : 0;
        const apex = a.account_type === "apex_eval";
        const led = a.is_archived ? "idle" : a.status === "live" ? "live" : a.eval_status === "in_progress" ? "eval" : "idle";
        let evalCell = "";
        if (apex) {
          if (a.eval_status === "passed") evalCell = `<span class="up">PASSED</span>`;
          else if (a.eval_status === "failed") evalCell = `<span class="dn">FAILED</span>`;
          else if (a.profit_target) {
            const pr = Math.max(0, Math.min(100, (pnl / a.profit_target) * 100));
            evalCell = `<span class="bb-prog ${pnl < 0 ? "bad" : ""}"><i style="width:${Math.max(pr, 2)}%"></i></span><span class="dim">${pr.toFixed(0)}%</span>`;
          }
        }
        return `<tr class="click" data-id="${a.id}"><td><span class="bb-led-c ${led}"></span></td><td class="dim">${a.id}</td>
          <td>${esc(a.name)}${a.is_system ? '<span class="bb-tag auto">AUTO</span>' : ""}${a.is_archived ? '<span class="bb-tag arch">ARCHIVED</span>' : ""}</td>
          <td class="dim">${apex ? "APEX " + Math.round(a.starting_balance / 1000) + "K" : "STD"}</td>
          <td class="dim">${a.strategy ? esc(a.strategy) + " · " + esc(a.symbol || "") : "—"}</td>
          <td class="r" data-k="acc:${a.id}">${f.money(a.balance)}</td>
          <td class="r ${dir(pnl)}">${f.signed(pnl)}</td><td class="r ${dir(pct)}">${f.pct(pct)}</td><td>${evalCell}</td></tr>`;
      }).join("")}</tbody></table>`;
      body.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => window.openAccountModal && openAccountModal(Number(tr.dataset.id))));
      rows.forEach((a) => flash(body.querySelector(`td[data-k="acc:${a.id}"]`), `acc:${a.id}`, a.balance));
      renderKpis();
    } catch (e) {
      $("#acc-body").innerHTML = `<div class="bb-empty"><b>ACCOUNTS UNAVAILABLE</b>${esc(e.message)}</div>`;
    }
  }

  // ---- 2) portfolio
  async function loadPortfolio() {
    try {
      cap.pf.data = await api("/capital/portfolio");
      renderPortfolio();
      renderKpis();
    } catch (e) {
      $("#pf-body").innerHTML = `<div class="bb-empty"><b>PORTFOLIO UNAVAILABLE</b>${esc(e.message)}</div>`;
    }
  }

  function renderPortfolio() {
    const p = cap.pf.data;
    if (!p) return;
    const body = $("#pf-body");
    const rows = p.holdings.filter((h) => cap.pf.filter === "all" || h.kind === cap.pf.filter);
    $("#pf-meta").textContent = `${p.holdings.length} POSITIONS`;
    if (!p.holdings.length) {
      body.innerHTML = `<div class="bb-empty"><div><b>NO POSITIONS YET</b></div><div>Track stocks &amp; crypto: click <b>+ ADD</b> above.</div><div>Live prices, P&amp;L and allocation update automatically.</div></div>`;
      return;
    }
    const t = p.totals, bk = t.by_kind;
    const palette = ["#ffa028", "#5aa9ff", "#2be07e", "#c58bff", "#ff6b9a", "#f2e34d", "#3fd1c9", "#ff7a3d"];
    const segs = p.holdings.filter((h) => h.value).sort((a, b) => b.value - a.value);
    body.innerHTML = `
      <div class="bb-pf-sum">
        <div><span>VALUE</span><b>${f.money(t.value)}</b></div>
        <div><span>TODAY</span><b class="${dir(t.day_pnl)}">${f.signed(t.day_pnl)} <small>${f.pct(t.day_pct)}</small></b></div>
        <div><span>ALL-TIME P&amp;L</span><b class="${dir(t.pnl)}">${f.signed(t.pnl)} <small>${f.pct(t.pnl_pct)}</small></b></div>
        <div><span>STOCKS / CRYPTO</span><b>${t.value ? Math.round((bk.stock / t.value) * 100) : 0}% <small>/</small> ${t.value ? Math.round((bk.crypto / t.value) * 100) : 0}%</b></div>
      </div>
      <div class="bb-alloc">${segs.map((h, i) => `<i style="width:0;background:${palette[i % palette.length]}" data-w="${h.weight.toFixed(2)}" title="${esc(h.symbol)} ${h.weight.toFixed(1)}%"></i>`).join("")}</div>
      <div class="bb-alloc-key">${segs.slice(0, 8).map((h, i) => `<span><i style="background:${palette[i % palette.length]}"></i>${esc(h.symbol)} ${h.weight.toFixed(0)}%</span>`).join("")}</div>
      <table class="bb-tbl"><thead><tr><th>SYMBOL</th><th></th><th class="r">QTY</th><th class="r">PRICE</th><th class="r">DAY</th><th class="r">VALUE</th><th class="r">P&amp;L</th><th class="r">P&amp;L %</th><th class="r">WT</th><th></th></tr></thead><tbody>
      ${rows.map((h) => `<tr><td class="sym">${esc(h.symbol)} <span class="dim">${h.kind === "crypto" ? "CRYPTO" : "EQ"}</span>${h.ext && h.ext.pct != null ? ` <span class="ext-tag ${dir(h.ext.pct)}" title="${h.ext.state === "PRE" ? "Pre-market vs previous close" : "After-hours vs today's close"}: ${f.px(h.ext.price)}">${h.ext.state === "PRE" ? "PRE" : "AH"} ${f.pct(h.ext.pct)}</span>` : ""}</td><td>${spark(h.spark, (h.day_pct || 0) >= 0)}</td>
        <td class="r dim">${f.qty(h.qty)}</td><td class="r" data-k="pf:${h.id}">${f.px(h.price)}</td>
        <td class="r ${dir(h.day_pct)}">${f.pct(h.day_pct)}</td><td class="r">${f.money(h.value)}</td>
        <td class="r ${dir(h.pnl)}">${f.signed(h.pnl)}</td><td class="r ${dir(h.pnl_pct)}">${f.pct(h.pnl_pct)}</td>
        <td class="r dim">${h.weight.toFixed(1)}%</td><td><span class="bb-x" data-del="${h.id}" title="Remove position">✕</span></td></tr>`).join("")}
      </tbody></table>`;
    requestAnimationFrame(() => body.querySelectorAll(".bb-alloc i").forEach((el) => (el.style.width = `${el.dataset.w}%`)));
    body.querySelectorAll("[data-del]").forEach((x) => x.addEventListener("click", async () => {
      await api(`/capital/holdings/${x.dataset.del}`, { method: "DELETE" });
      loadPortfolio();
    }));
    rows.forEach((h) => flash(body.querySelector(`td[data-k="pf:${h.id}"]`), `pf:${h.id}`, h.price));
  }

  function initPortfolioForm() {
    const form = $("#pf-form"), err = $("#pf-err");
    $("#pf-add-toggle").addEventListener("click", () => {
      form.classList.toggle("hidden");
      if (!form.classList.contains("hidden")) $("#pf-symbol").focus();
    });
    document.querySelectorAll("#pf-filters .bb-chip").forEach((chip) => chip.addEventListener("click", () => {
      document.querySelectorAll("#pf-filters .bb-chip").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      cap.pf.filter = chip.dataset.f;
      renderPortfolio();
    }));
    const submit = async () => {
      err.textContent = "";
      const body = {
        kind: $("#pf-kind").value, symbol: $("#pf-symbol").value.trim(),
        qty: parseFloat($("#pf-qty").value), cost_basis: parseFloat($("#pf-cost").value || "0"),
      };
      if (!body.symbol || !(body.qty > 0)) { err.textContent = "ENTER A SYMBOL AND A QUANTITY > 0"; return; }
      try {
        $("#pf-submit").textContent = "…";
        await api("/capital/holdings", { method: "POST", body: JSON.stringify(body) });
        ["#pf-symbol", "#pf-qty", "#pf-cost"].forEach((s) => ($(s).value = ""));
        form.classList.add("hidden");
        await loadPortfolio();
      } catch (e) {
        err.textContent = String(e.message || e).toUpperCase();
      } finally {
        $("#pf-submit").textContent = "ADD";
      }
    };
    $("#pf-submit").addEventListener("click", submit);
    form.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  }

  // ---- 3) news
  async function loadNews() {
    try {
      const { items } = await api("/capital/news");
      const fresh = new Set();
      if (!cap.news.first) items.forEach((i) => { if (!cap.news.seen.has(i.link)) fresh.add(i.link); });
      items.forEach((i) => cap.news.seen.add(i.link));
      cap.news.first = false;
      cap.news.items = items;
      renderNews(fresh);
    } catch (e) {
      $("#news-body").innerHTML = `<div class="bb-empty"><b>NEWS UNAVAILABLE</b>${esc(e.message)}</div>`;
    }
  }
  // headlines naming a market-wide trigger are marked (same rule as the BREAKING toast in invest.js)
  const IMPORTANT = /\b(fed|fomc|powell|ecb|lagarde|boj|rate (cut|hike)|tariffs?|sanctions?|cpi|inflation|payrolls|jobs report|gdp|recession|default|halt(ed)?|crash|plunge|surge|soar|war|missile|ceasefire|opec|bankrupt|downgrade)\b/i;
  function renderNews(fresh = new Set()) {
    const items = cap.news.items.filter((i) => cap.news.filter === "all" || i.tag === cap.news.filter).slice(0, 60);
    $("#news-meta").textContent = String(items.length);
    $("#news-body").innerHTML = items.length
      ? `<ul class="bb-news">${items.map((i) => `<li class="${fresh.has(i.link) ? "new" : ""} ${IMPORTANT.test(i.title) || i.tag === "CB" ? "hm-hot" : ""}"><span class="t">${f.hm(i.ts)}</span><span class="src ${i.tag === "CRYPTO" ? "cr" : ""}">${esc(i.source)}</span><a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></li>`).join("")}</ul>`
      : `<div class="bb-empty"><b>NO HEADLINES</b></div>`;
  }
  function initNewsFilters() {
    document.querySelectorAll("#news-filters .bb-chip").forEach((chip) => chip.addEventListener("click", () => {
      document.querySelectorAll("#news-filters .bb-chip").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      cap.news.filter = chip.dataset.f;
      renderNews();
    }));
  }

  // ---- 2) NOSTRO live: open positions with unrealized P&L, then the latest closed trades
  async function loadLive() {
    const box = $("#hm-live"); if (!box) return;
    try {
      const r = await api("/trades/feed?limit=12");
      const op = r.open_positions || [], tr = r.trades || [];
      const upnl = op.reduce((a, p) => a + (p.unrealized_pnl || 0), 0);
      $("#hm-live-meta").innerHTML = `${op.length} OPEN${op.length ? ` · <span class="${dir(upnl)}">${f.signed(upnl)}</span>` : ""}`;
      const opHtml = op.length ? `<table class="bb-tbl"><thead><tr><th>OPEN</th><th>SIDE</th><th class="r">ENTRY</th><th class="r">LAST</th><th class="r">P&amp;L</th></tr></thead><tbody>${op.map((p) => `<tr class="click" data-acc="${p.account_id}"><td>${esc(p.symbol)} <span class="dim">#${p.account_id}</span></td><td class="${p.side === "long" ? "up" : "dn"}">${esc(String(p.side).toUpperCase())}</td><td class="r">${f.px(p.entry_price)}</td><td class="r">${p.current_price == null ? "—" : f.px(p.current_price)}</td><td class="r ${dir(p.unrealized_pnl)}">${p.unrealized_pnl == null ? "—" : f.signed(p.unrealized_pnl)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="hm-note">NO OPEN POSITIONS</div>`;
      const trHtml = tr.length ? `<table class="bb-tbl"><thead><tr><th>CLOSED</th><th>TRADE</th><th>EXIT</th><th class="r">P&amp;L</th></tr></thead><tbody>${tr.map((t) => `<tr class="click" data-acc="${t.account_id}"><td class="dim">${String(t.exit_time || "").slice(5, 16).replace("T", " ")}</td><td>${esc(t.symbol || "")} <span class="${t.side === "long" ? "up" : "dn"}">${esc(String(t.side || "").toUpperCase())}</span> <span class="dim">#${t.account_id}</span></td><td class="dim">${esc(String(t.exit_reason || "").toUpperCase())}</td><td class="r ${dir(t.pnl)}">${f.signed(t.pnl)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="hm-note">NO CLOSED LIVE TRADES ON RECORD${cap.summary?.last_session ? ` · LAST SESSION ${esc(cap.summary.last_session.date)}: <span class="${dir(cap.summary.last_session.pnl)}">${f.signed(cap.summary.last_session.pnl)}</span> (${cap.summary.last_session.trades} trades)` : ""}</div>`;
      box.innerHTML = opHtml + trHtml;
      box.querySelectorAll("tr[data-acc]").forEach((el) => el.addEventListener("click", () => window.openAccountModal && openAccountModal(Number(el.dataset.acc))));
    } catch (e) { box.innerHTML = `<div class="bb-empty"><b>LIVE FEED UNAVAILABLE</b>${esc(e.message)}</div>`; }
  }

  // ---- 4) Polymarket: biggest non-sports markets by 24h volume + the largest odds moves
  async function loadPm() {
    const box = $("#hm-pm"); if (!box) return;
    try {
      const [ov, mv] = await Promise.all([api("/pmx/overview?limit=60"), api("/pmx/movers?n=4")]);
      const top = ov.events.filter((e) => e.cat !== "SPORTS").slice(0, 8);
      const pc = (p) => (p == null ? "—" : `${Math.round(p * 100)}%`), pts = (d) => (d == null ? "" : `${d > 0 ? "+" : ""}${(d * 100).toFixed(1)}`);
      $("#hm-pm-meta").textContent = `$${f.compact(ov.kpis.vol24)} 24H · ${ov.kpis.events} EVENTS`;
      box.innerHTML = `<table class="bb-tbl"><tbody>${top.map((e) => { const m = e.markets[0] || {}; return `<tr class="click" data-pev="${esc(e.slug)}"><td><span class="hm-ttl">${esc(e.title)}</span><span class="dim hm-sub">${esc(m.label || (m.outcomes || [])[0] || "")} · ${e.cat}</span></td><td class="r"><b>${pc(m.p)}</b></td><td class="r ${dir(m.chg1d)}">${pts(m.chg1d)}</td><td class="r dim">$${f.compact(e.vol24)}</td></tr>`; }).join("")}</tbody></table>
        <div class="hm-subh">BIGGEST ODDS MOVES · 24H</div><table class="bb-tbl"><tbody>${mv.items.map((m) => `<tr class="click" data-pev="${esc(m.slug)}"><td><span class="hm-ttl">${esc(m.event)}</span><span class="dim hm-sub">${esc(m.q !== m.event ? m.q : m.outcome)}</span></td><td class="r"><b>${pc(m.p)}</b></td><td class="r ${dir(m.chg1d)}">${pts(m.chg1d)}</td></tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("[data-pev]").forEach((el) => el.addEventListener("click", () => {
        const slug = el.dataset.pev;
        if (NX.openPolymarketEvent) return NX.openPolymarketEvent(slug);
        NX.loadGroup("pmx").then(() => NX.openPolymarketEvent && NX.openPolymarketEvent(slug));
      }));
    } catch (e) { box.innerHTML = `<div class="bb-empty"><b>POLYMARKET UNAVAILABLE</b>${esc(e.message)}</div>`; }
  }

  // ---- 5) markets: the tape as tiles (index futures, rates, FX, commodities, crypto) with an intraday sparkline
  async function loadMarketTiles() {
    const box = $("#hm-mk"); if (!box) return;
    try {
      const { items } = await api("/capital/tape");
      $("#hm-mk-meta").textContent = `${items.filter((i) => i.pct >= 0).length} UP · ${items.filter((i) => i.pct < 0).length} DOWN`;
      box.innerHTML = `<div class="hm-tiles">${items.map((i) => `<div class="hm-tile" data-sec="${esc(i.sym)}" title="${esc(i.sym)}"><span class="l">${esc(i.label)}</span><b data-k="hmk:${esc(i.sym)}">${f.px(i.price)}</b><span class="c ${dir(i.pct)}">${f.pct(i.pct)}</span>${spark(i.spark, i.pct >= 0, 64, 16)}</div>`).join("")}</div>`;
      items.forEach((i) => flash(box.querySelector(`[data-k="hmk:${CSS.escape(i.sym)}"]`), `hmk:${i.sym}`, i.price));
    } catch (e) { box.innerHTML = `<div class="bb-empty"><b>MARKETS UNAVAILABLE</b>${esc(e.message)}</div>`; }
  }

  async function loadNetWorth() {
    try {
      const nw = await api("/wealth/networth");
      setKpi("#kpi-nw", f.money(nw.total_usd));
      const d = nw.change && (nw.change["30d"] || nw.change["7d"]);
      $("#kpi-nw-s").innerHTML = `₪${Math.round(nw.total_ils).toLocaleString()}${d ? ` · <span class="${dir(d.usd)}">${f.signed(d.usd)}</span> ${nw.change["30d"] ? "30D" : "7D"}` : ""}`;
    } catch (e) { $("#kpi-nw-s").textContent = "UNAVAILABLE"; }
  }

  // ---- 4/5) hot stocks + hot coins
  async function loadHot() {
    try {
      cap.hot = await api("/capital/hot");
      renderHotStocks();
      renderHotCoins();
    } catch (e) { /* keep last */ }
  }
  function renderHotStocks() {
    const body = $("#hs-body"), d = cap.hot?.stocks;
    if (!d) { body.innerHTML = `<div class="bb-empty bb-load">LOADING</div>`; return; }
    const rows = d[cap.hs.tab] || [];
    body.innerHTML = `<table class="bb-tbl"><thead><tr><th>SYM</th><th></th><th class="r">LAST</th><th class="r">CHG%</th><th class="r">VOL</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="click" data-u="https://finance.yahoo.com/quote/${encodeURIComponent(r.sym)}"><td class="sym">${esc(r.sym)}</td><td>${spark(r.spark, r.pct >= 0, 44, 14)}</td>
      <td class="r" data-k="hs:${r.sym}">${f.px(r.price)}</td><td class="r ${dir(r.pct)}">${f.pct(r.pct)}</td><td class="r dim">${f.compact(r.vol)}</td></tr>`).join("")}</tbody></table>`;
    body.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => window.open(tr.dataset.u, "_blank", "noopener")));
    rows.forEach((r) => flash(body.querySelector(`td[data-k="hs:${r.sym}"]`), `hs:${r.sym}`, r.price));
  }
  function renderHotCoins() {
    const body = $("#hc-body"), d = cap.hot?.coins;
    if (!d) { body.innerHTML = `<div class="bb-empty bb-load">LOADING</div>`; return; }
    const rows = d[cap.hc.tab] || [];
    body.innerHTML = `<table class="bb-tbl"><thead><tr><th>COIN</th><th class="r">PRICE</th><th class="r">1H</th><th class="r">24H</th><th class="r">VOL</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="click" data-u="https://www.coingecko.com/en/search?query=${encodeURIComponent(r.sym)}"><td class="sym">${esc(r.sym)}${r.trending && cap.hc.tab !== "trending" ? " <span class='dim'>🔥</span>" : ""}</td>
      <td class="r" data-k="hc:${r.sym}">${r.price == null ? "—" : f.px(r.price)}</td><td class="r ${dir(r.pct1h)}">${f.pct(r.pct1h)}</td>
      <td class="r ${dir(r.pct)}">${f.pct(r.pct)}</td><td class="r dim">${f.compact(r.vol)}</td></tr>`).join("")}</tbody></table>`;
    body.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => window.open(tr.dataset.u, "_blank", "noopener")));
    rows.forEach((r) => r.price != null && flash(body.querySelector(`td[data-k="hc:${r.sym}"]`), `hc:${r.sym}`, r.price));
  }
  function initHotTabs() {
    const wire = (sel, key, render) => document.querySelectorAll(`${sel} .bb-chip`).forEach((chip) => chip.addEventListener("click", () => {
      document.querySelectorAll(`${sel} .bb-chip`).forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      cap[key].tab = chip.dataset.f;
      render();
    }));
    wire("#hs-tabs", "hs", renderHotStocks);
    wire("#hc-tabs", "hc", renderHotCoins);
  }

  // ------------------------------------------------------------------ boot
  const every = (fn, ms) => { fn(); cap.timers.push(setInterval(() => { if (visible()) fn(); }, ms)); };

  window.initCapitalTab = function initCapitalTab() {
    if (cap.init) return;
    cap.init = true;
    $("#cap-archived")?.addEventListener("click", (e) => {
      cap.showArchived = !cap.showArchived;
      e.currentTarget.classList.toggle("on", cap.showArchived);
      loadAccountsPanel();
    });
    initPortfolioForm();
    initNewsFilters();
    initHotTabs();
    document.getElementById("view-capital").addEventListener("click", (e) => {
      const g = e.target.closest("[data-goto]"); if (g) document.querySelector(`.tab[data-tab="${g.dataset.goto}"]`)?.click();
    });
    every(loadAccountsPanel, 5000);
    every(loadLive, 10000);
    every(loadPortfolio, 20000);
    every(loadNews, 120000);
    every(loadPm, 90000);
    every(loadMarketTiles, 30000);
    every(loadNetWorth, 300000);
    every(loadHot, 90000);
    every(loadFearGreed, 120000);
  };

  document.addEventListener("DOMContentLoaded", () => {
    initCommand();
    initMenus();
    initMobileNav();
    setInterval(refreshOpenAccountModal, 5000);
    tickClocks();
    setInterval(tickClocks, 1000);
    pollStatus();
    setInterval(() => { if (!document.hidden) pollStatus(); }, 10000);
    if ($("#view-capital")?.classList.contains("active")) initCapitalTab();
    pageHead(document.querySelector(".view.active")?.id?.replace("view-", "") || "capital");
  });
})();
