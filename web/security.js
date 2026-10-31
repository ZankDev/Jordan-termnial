/* SECURITY deep-dive (Bloomberg-style): one screen per security with function tabs
   DES description · GP price chart · FA financials · N news + search · ANR analysts · HDS holders ·
   FIL SEC filings · DVD dividends · OMON options · RV peers · DATA every field, plus LAUNCHPAD (multi-panel) and PDF.
   Opened from the command line ("AAPL US Equity GP", "VOD LN", "TEVA IT", "BTC", "EURUSD Curncy", "SPX Index"),
   from any monitor row, or from the search box on this page. Depends on window.NX (invest.js) and api(). */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, big, signed } = NX;
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);

  const FN_LIST = [
    ["WHY", "WHY BUY / HOLD"], ["DES", "DESCRIPTION"], ["GP", "PRICE CHART"], ["FA", "FINANCIALS"], ["N", "NEWS"], ["ANR", "ANALYSTS"], ["HDS", "HOLDERS"],
    ["FIL", "SEC FILINGS"], ["DVD", "DIVIDENDS"], ["OMON", "OPTIONS"], ["GEX", "GAMMA LEVELS"], ["QNT", "QUANT LAB"], ["RV", "PEERS"], ["DATA", "ALL DATA"], ["DEEP", "DEEP DIVE"],
  ];
  const FN_ALIAS = { DD: "DEEP", INSIGHT: "DEEP", G: "GP", NEWS: "N", CN: "N", FILINGS: "FIL", OPT: "OMON", PEERS: "RV", FLDS: "DATA", EE: "ANR", LAUNCHPAD: "LP", SEC: "DES" };
  const sx = { symbol: null, sec: null, fn: "DES", lp: false, tok: 0, chart: null };
  const cache = new Map();

  const getData = (path) => {
    if (!cache.has(path)) {
      const p = api(path).catch((e) => { cache.delete(path); throw e; });
      cache.set(path, p);
      setTimeout(() => cache.delete(path), 4 * 60 * 1000);
    }
    return cache.get(path);
  };
  const q = (s) => encodeURIComponent(s);

  function availFns(sec) {
    const t = (sec.type || "").toUpperCase();
    if (t === "EQUITY") return FN_LIST.map((f) => f[0]);
    if (t === "ETF" || t === "MUTUALFUND") return ["DES", "GP", "N", "DVD", "OMON", "GEX", "QNT", "RV", "DATA"];
    if (t === "CRYPTOCURRENCY") return ["DES", "GP", "N", "DEEP", "DATA"];
    return ["DES", "GP", "N", "DATA"];
  }
  const curLabel = (c) => (c === "GBp" ? "GBp (pence)" : c === "ILA" ? "ILA (agorot)" : c || "");
  const stateLabel = (s) => ({ REGULAR: "MARKET OPEN", CLOSED: "MARKET CLOSED", PRE: "PRE-MARKET", POST: "AFTER HOURS", PREPRE: "CLOSED", POSTPOST: "CLOSED" }[s] || "");

  // ------------------------------------------------------------------ entry points
  window.initSecurityTab = () => { if (!sx.symbol) renderEmpty(); };

  window.openSecurity = (symbol, fn) => {
    const btn = document.querySelector('.tab[data-tab="security"]');
    if (btn && !document.querySelector("#view-security.active")) btn.click();
    let f = (fn || "WHY").toUpperCase();
    f = FN_ALIAS[f] || f;
    sx.symbol = symbol; sx.sec = null;
    sx.lp = f === "LP";
    sx.fn = sx.lp ? "DES" : f;
    const rec = NX.getList(NX.RECENT_KEY).filter((s) => s !== symbol);
    NX.setList(NX.RECENT_KEY, [symbol, ...rec].slice(0, 12));
    renderShell();
    loadSecurity();
  };

  window.openSecurityQuery = async (text) => {
    try {
      const r = await api(`/invest/resolve?q=${q(text)}`);
      window.openSecurity(r.symbol, r.fn || "DES");
    } catch (e) {
      const btn = document.querySelector('.tab[data-tab="security"]');
      if (btn) btn.click();
      renderEmpty(`Could not find “${text}”. Try a ticker (AAPL), a Bloomberg-style code (VOD LN, TEVA IT, SPX Index, EURUSD Curncy) or a company name.`);
    }
  };

  // ------------------------------------------------------------------ empty state
  function renderEmpty(msg) {
    disposeChart();
    sx.symbol = null;
    const w = NX.getList(NX.WATCH_KEY), rc = NX.getList(NX.RECENT_KEY);
    $("#view-security").innerHTML = `
      <div class="bb-panel" style="--i:0">
        <div class="bb-ph"><span class="bb-n">1</span>SECURITY DEEP DIVE<span class="sp"></span><span class="meta">the Bloomberg &lt;EQUITY&gt; screen for any public security</span></div>
        <div class="sx-search"><input id="sx-q0" placeholder="AAPL US Equity   ·   VOD LN   ·   SEC AAPL   ·   GP TSLA   ·   SPX Index   ·   BTC   ·   EURUSD Curncy" autocomplete="off" spellcheck="false" /><button class="bb-go" id="sx-go0">GO</button></div>
        ${msg ? `<div class="sx-msg">${esc(msg)}</div>` : ""}
        <div class="sx-results" id="sx-res0"></div>
        <div class="sx-chips">${["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "TEVA.TA", "VOD.L", "BTC-USD", "ETH-USD", "^GSPC", "EURUSD=X", "GC=F"].map((s) => `<button class="bb-chip" data-sec="${s}">${s}</button>`).join("")}</div>
      </div>
      <div class="bb-grid">
        <div class="bb-panel s6 h340" style="--i:1"><div class="bb-ph"><span class="bb-n">2</span>FUNCTIONS<span class="sp"></span><span class="meta">before or after the ticker: “NVDA FA” or “FA NVDA”</span></div><div class="bb-pb"><table class="bb-tbl"><tbody>
          ${[["DES", "Security description: business summary, profile, officers, key statistics, valuation"], ["GP", "Interactive price chart with volume, moving averages, 1D to MAX"], ["FA", "Audited financial statements (income, balance sheet, cash flow) with margins and growth"], ["N", "News for the security, plus a free-text news search on any topic"], ["ANR", "Analyst ratings, price targets, estimates, earnings surprises, upgrades/downgrades"], ["HDS", "Institutional and fund holders, insiders, recent insider transactions"], ["FIL", "SEC EDGAR filings: 10-K, 10-Q, 8-K, Form 4, proxy, with direct links"], ["DVD", "Dividend and split history, yearly totals, yield"], ["OMON", "Options monitor: calls and puts by expiry, IV, open interest"], ["RV", "Relative value: peers and comparable companies"], ["DATA", "Every data field we hold for the security, searchable"], ["LP", "Launchpad: chart, snapshot, news, filings, analysts and peers on one screen"]].map(([c, d]) => `<tr><td class="sym" style="width:60px">${c}</td><td>${d}</td></tr>`).join("")}
        </tbody></table></div></div>
        <div class="bb-panel s6 h340" style="--i:2"><div class="bb-ph"><span class="bb-n">3</span>WATCHLIST · RECENT<span class="sp"></span><span class="meta">${w.length} pinned</span></div><div class="bb-pb" id="sx-recent">${w.length + rc.length ? NX.load() : '<div class="bb-empty"><b>NOTHING YET</b>Securities you open appear here; press ☆ WATCH to pin one.</div>'}</div></div>
      </div>`;
    const input = $("#sx-q0"), res = $("#sx-res0");
    let seq = 0, t = null;
    const doSearch = async () => {
      const v = input.value.trim(); const my = ++seq;
      if (!v) { res.innerHTML = ""; return; }
      try {
        const r = await api(`/invest/search?q=${q(v)}&limit=10`);
        if (my !== seq) return;
        res.innerHTML = r.results.map((x) => `<div class="sx-res" data-sec="${esc(x.symbol)}"><b>${esc(x.symbol)}</b><span class="bbg">${esc(x.bbg)}</span><span>${esc(x.name)}</span><span class="dim">${esc(x.exchange || "")} · ${esc(x.type || "")}</span></div>`).join("") || '<div class="bb-empty">no matches</div>';
      } catch (e) { res.innerHTML = NX.err(e); }
    };
    input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(doSearch, 250); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) window.openSecurityQuery(input.value.trim()); });
    $("#sx-go0").addEventListener("click", () => { if (input.value.trim()) window.openSecurityQuery(input.value.trim()); });
    const syms = [...w, ...rc.filter((s) => !w.includes(s))];
    if (syms.length) {
      api(`/invest/quotes?symbols=${q(syms.join(","))}`).then((r) => {
        const box = $("#sx-recent");
        if (!box) return; // the user already opened a security
        const by = Object.fromEntries(r.rows.map((x) => [x.symbol, x]));
        NX.monitorTable(box, syms.map((s) => by[s]).filter(Boolean).map((x) => ({ ...x, name: (w.includes(x.symbol) ? "★ " : "") + (x.name || "") })), { cols: ["sym", "last", "pct", "spark"], id: "sx-recent" });
      }).catch((e) => { const box = $("#sx-recent"); if (box) box.innerHTML = NX.err(e); });
    }
    input.focus();
  }

  // ------------------------------------------------------------------ shell + header
  function disposeChart() { if (sx.chart) { try { sx.chart.remove(); } catch (e) { /* already gone */ } sx.chart = null; } }

  function renderShell() {
    disposeChart();
    $("#view-security").innerHTML = `
      <div class="sx-top" id="sx-top"><div class="bb-empty bb-load" style="height:64px">LOADING ${esc(sx.symbol)}</div></div>
      <div class="sx-fns" id="sx-fns"></div>
      <div class="sx-body" id="sx-body">${NX.load()}</div>`;
  }

  async function loadSecurity() {
    const tok = ++sx.tok;
    try {
      const sec = await getData(`/invest/security?symbol=${q(sx.symbol)}`);
      if (tok !== sx.tok) return;
      sx.sec = sec;
      const avail = availFns(sec);
      if (!avail.includes(sx.fn)) sx.fn = avail.includes("DES") ? "DES" : "GP";
      renderHeader();
      renderBody();
    } catch (e) {
      if (tok !== sx.tok) return;
      $("#sx-top").innerHTML = `<div class="bb-empty"><b>SECURITY UNAVAILABLE</b>${esc(e.message)}</div>`;
      $("#sx-body").innerHTML = `<div class="sx-search" style="margin:12px"><input id="sx-q1" placeholder="Search another security…" /><button class="bb-go" id="sx-go1">GO</button></div>`;
      $("#sx-go1").addEventListener("click", () => { const v = $("#sx-q1").value.trim(); if (v) window.openSecurityQuery(v); });
    }
  }

  function renderHeader() {
    const s = sx.sec, watching = NX.getList(NX.WATCH_KEY).includes(s.symbol);
    $("#sx-top").innerHTML = `
      <div class="sx-id"><div class="sx-bbg">${esc(s.bbg)}</div><div class="sx-name">${esc(s.name)}</div>
        <div class="sx-sub">${esc(s.exchange || "")}${s.currency ? " · " + esc(curLabel(s.currency)) : ""}${stateLabel(s.market_state) ? ` · <span class="${s.market_state === "REGULAR" ? "up" : ""}">${stateLabel(s.market_state)}</span>` : ""}</div></div>
      <div class="sx-px"><span class="v" id="sx-price">${px(s.price)}</span><span class="c ${dir(s.chg)}">${s.chg > 0 ? "▲" : s.chg < 0 ? "▼" : ""} ${signed(s.chg)} (${pct(s.pct)})</span></div>
      <div class="sx-mini">
        <div><span>OPEN</span><b>${px(s.open)}</b></div><div><span>HIGH</span><b>${px(s.high)}</b></div><div><span>LOW</span><b>${px(s.low)}</b></div>
        <div><span>PREV</span><b>${px(s.prev)}</b></div><div><span>VOLUME</span><b>${big(s.volume)}</b></div><div><span>MKT CAP</span><b>${big(s.market_cap)}</b></div>
        <div class="w"><span>52-WEEK RANGE</span>${NX.rangeBar(s.week52[0], s.week52[1], s.price)}</div>
      </div>
      <div class="sx-actions">
        <div class="sx-find"><input id="sx-q" placeholder="Security ▸ AAPL US, VOD LN…" autocomplete="off" spellcheck="false" /></div>
        <button class="bb-chip ${watching ? "on" : ""}" id="sx-watch">${watching ? "★ WATCHING" : "☆ WATCH"}</button>
        <button class="bb-chip ${sx.lp ? "on" : ""}" id="sx-lp" title="Multi-panel Launchpad view">LAUNCHPAD</button>
        <button class="bb-chip sx-spbtn" id="sx-spider" title="The spider reads everything public about this stock — financials, filings, the latest results, analysts, news — and gives its view">SPIDER REPORT</button>
        <button class="bb-chip" id="sx-report" title="Description, chart, statistics, financials and news on one page — mark it up and save as a PDF">FULL REPORT</button>
        <button class="bb-chip" id="sx-pdf" title="Print this screen — choose 'Save as PDF'">PDF</button>
      </div>`;
    const avail = availFns(s);
    $("#sx-fns").innerHTML = FN_LIST.filter((f) => avail.includes(f[0])).map(([c, l]) => `<button class="sx-fn ${!sx.lp && c === sx.fn ? "on" : ""}" data-fn="${c}"><b>${c}</b><span>${l}</span></button>`).join("");
    document.querySelectorAll("#sx-fns .sx-fn").forEach((b) => b.addEventListener("click", () => { sx.lp = false; sx.fn = b.dataset.fn; renderHeaderTabs(); renderBody(); }));
    $("#sx-q").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.value.trim()) window.openSecurityQuery(e.target.value.trim()); });
    $("#sx-watch").addEventListener("click", (e) => {
      let w = NX.getList(NX.WATCH_KEY);
      w = w.includes(s.symbol) ? w.filter((x) => x !== s.symbol) : [s.symbol, ...w];
      NX.setList(NX.WATCH_KEY, w);
      e.currentTarget.classList.toggle("on", w.includes(s.symbol));
      e.currentTarget.textContent = w.includes(s.symbol) ? "★ WATCHING" : "☆ WATCH";
    });
    $("#sx-lp").addEventListener("click", () => { sx.lp = !sx.lp; renderHeaderTabs(); renderBody(); });
    $("#sx-report").addEventListener("click", () => window.openFullReport && window.openFullReport(s.symbol));
    $("#sx-pdf").addEventListener("click", printPdf);
    $("#sx-spider").addEventListener("click", () => window.NX && NX.spider && NX.spider.report(s.symbol));
    spiderStrip(s.symbol);
  }
  // the spider's latest view on this stock (a stored report; never starts one by itself)
  async function spiderStrip(sym) {
    const top = $("#sx-top"); if (!top || !window.NX || !NX.spider) return;
    const el = document.createElement("div"); el.className = "sx-spider"; el.title = "Open the SPIDER report"; top.appendChild(el);
    el.innerHTML = `<b>SPIDER</b><span class="sp">checking for a report on ${esc(sym)}…</span>`;
    el.addEventListener("click", () => NX.spider.report(sym));
    try {
      const r = await api(`/spider/report?symbol=${encodeURIComponent(sym)}&peek=true`);
      if (sx.symbol !== sym) return;
      const p = r.report, v = p && p.view;
      const cls = { BULLISH: "bull2", "LEANING BULLISH": "bull1", NEUTRAL: "neu", "LEANING BEARISH": "bear1", BEARISH: "bear2" }[v && v.stance] || "neu";
      el.innerHTML = v ? `<b>SPIDER</b><span class="st ${cls}">${esc(v.stance)}</span><span>conviction ${esc(v.conviction)}/5</span><span class="sp">${esc(v.headline || "")}</span><span class="dim">${esc(NX.ago(p.generated_at) || "")}</span><span>OPEN REPORT ›</span>`
        : `<b>SPIDER</b><span class="sp">No report yet — click and the spider reads the financials, filings, the latest results, analysts and the news (about a minute).</span><span>RESEARCH ${esc(sym)} ›</span>`;
    } catch (e) { el.remove(); }
  }
  function renderHeaderTabs() {
    $("#sx-lp").classList.toggle("on", sx.lp);
    document.querySelectorAll("#sx-fns .sx-fn").forEach((b) => b.classList.toggle("on", !sx.lp && b.dataset.fn === sx.fn));
  }
  function printPdf() {
    const old = document.title;
    document.title = `${sx.sec ? sx.sec.bbg : "Security"} ${sx.lp ? "LAUNCHPAD" : sx.fn} - NOSTRO Terminal`;
    document.body.classList.add("printing-sx");
    const done = () => { document.body.classList.remove("printing-sx"); document.title = old; window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    setTimeout(() => window.print(), 80);
  }

  const panel = (n, title, id, o = {}) => NX.panel(n, title, id, o);
  const kvTable = (rows) => `<table class="bb-tbl kv"><tbody>${rows.filter((r) => r[1] !== null && r[1] !== undefined && r[1] !== "" && r[1] !== "—").map(([k, v]) => `<tr><td class="k">${k}</td><td class="r">${v}</td></tr>`).join("")}</tbody></table>`;

  async function renderBody() {
    const body = $("#sx-body");
    const tok = ++sx.tok;
    disposeChart();
    body.innerHTML = NX.load();
    try {
      if (sx.lp) await renderLaunchpad(body, tok);
      else await FUNCS[sx.fn](body, tok);
    } catch (e) {
      if (tok === sx.tok) body.innerHTML = NX.err(e);
    }
  }

  // ------------------------------------------------------------------ DES
  function perfStrip(perf) {
    return `<div class="perf">${["1W", "1M", "3M", "6M", "YTD", "1Y"].map((k) => `<div><span>${k}</span><b class="${dir(perf[k])}">${pct(perf[k], true, 1)}</b></div>`).join("")}</div>`;
  }
  function targetBlock(s) {
    const t = s.target || {};
    if (!isNum(t.mean) || !isNum(t.low) || !isNum(t.high)) return "";
    const lo = Math.min(t.low, s.price) * 0.98, hi = Math.max(t.high, s.price) * 1.02, at = (v) => ((v - lo) / (hi - lo)) * 100;
    const up = ((t.mean / s.price) - 1) * 100;
    return `<div class="tgt"><div class="tgt-h">ANALYST PRICE TARGET · ${t.n || "?"} analysts · <b class="rating">${esc((t.rating || "").toUpperCase().replace("_", " "))}</b></div>
      <div class="tgt-bar"><i class="rng" style="left:${at(t.low)}%;width:${at(t.high) - at(t.low)}%"></i><i class="mean" style="left:${at(t.mean)}%"></i><i class="px" style="left:${at(s.price)}%"></i></div>
      <div class="tgt-l"><span>LOW ${px(t.low)}</span><span>MEAN ${px(t.mean)} <b class="${dir(up)}">(${pct(up, true, 1)})</b></span><span>HIGH ${px(t.high)}</span></div></div>`;
  }
  const S = (v, f) => (isNum(v) ? f(v) : "—");

  async function desEquity(body, tok, s) {
    const st = s.stats, pf = s.profile;
    const site = pf.website ? `<a href="${esc(pf.website)}" target="_blank" rel="noopener noreferrer">${esc(pf.website.replace(/^https?:\/\//, ""))}</a>` : "";
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s7 h470" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>SECURITY DESCRIPTION<span class="sp"></span><span class="meta">${s.description_source ? "source: " + esc(s.description_source) : ""}</span></div>
        <div class="bb-pb bb-pad"><p class="about">${esc(s.description || "No business description is available for this security.")}</p>
          <div class="cols2">${kvTable([["SECTOR", esc(pf.sector)], ["INDUSTRY", esc(pf.industry)], ["COUNTRY", esc(pf.country)], ["HEADQUARTERS", esc([pf.city, pf.state].filter(Boolean).join(", "))], ["EMPLOYEES", isNum(pf.employees) ? num(pf.employees, 0) : ""], ["WEBSITE", site]])}
          ${kvTable([["TICKER", esc(s.symbol)], ["EXCHANGE", esc(s.exchange)], ["CURRENCY", esc(curLabel(s.currency))], ["SECURITY TYPE", esc(s.type)], ["PHONE", esc(pf.phone)], ["NEXT EARNINGS", (s.next_earnings || []).length ? NX.date(s.next_earnings[0]) : ""]])}</div>
          ${pf.officers && pf.officers.length ? `<div class="sub-h">KEY OFFICERS</div><table class="bb-tbl"><tbody>${pf.officers.map((o) => `<tr><td class="sym">${esc(o.name)}</td><td>${esc(o.title)}</td><td class="r dim">${isNum(o.age) ? "age " + o.age : ""}</td><td class="r">${isNum(o.pay) ? big(o.pay, "$") : ""}</td></tr>`).join("")}</tbody></table>` : ""}
        </div></div>
      <div class="bb-panel s5 h470" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>PRICE SNAPSHOT<span class="sp"></span><span class="meta">1-year daily</span></div>
        <div class="bb-pb bb-pad"><div id="des-chart" style="height:190px"></div>${perfStrip(s.perf)}${targetBlock(s)}
          <div class="tech">${kvTable([["50-DAY SMA", S(st.sma50, (v) => px(v))], ["200-DAY SMA", S(st.sma200, (v) => px(v))], ["BETA", S(st.beta, (v) => num(v, 2))]])}</div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>KEY STATISTICS</div><div class="bb-pb bb-pad"><div class="cols4">
        <div><div class="sub-h">VALUATION</div>${kvTable([["MARKET CAP", big(s.market_cap)], ["ENTERPRISE VALUE", big(st.ev)], ["P/E (TTM)", S(st.pe, (v) => num(v, 1))], ["P/E (FORWARD)", S(st.fwd_pe, (v) => num(v, 1))], ["PEG", S(st.peg, (v) => num(v, 2))], ["PRICE / BOOK", S(st.pb, (v) => num(v, 2))], ["PRICE / SALES", S(st.ps, (v) => num(v, 2))], ["EV / REVENUE", S(st.ev_rev, (v) => num(v, 2))], ["EV / EBITDA", S(st.ev_ebitda, (v) => num(v, 1))]])}</div>
        <div><div class="sub-h">PROFITABILITY &amp; GROWTH</div>${kvTable([["REVENUE (TTM)", big(st.revenue)], ["EBITDA", big(st.ebitda)], ["GROSS MARGIN", S(st.gross_margin, (v) => pct(v * 100, false, 1))], ["OPERATING MARGIN", S(st.op_margin, (v) => pct(v * 100, false, 1))], ["NET MARGIN", S(st.profit_margin, (v) => pct(v * 100, false, 1))], ["ROE", S(st.roe, (v) => pct(v * 100, false, 1))], ["ROA", S(st.roa, (v) => pct(v * 100, false, 1))], ["REVENUE GROWTH", S(st.rev_growth, (v) => pct(v * 100, true, 1))], ["EARNINGS GROWTH", S(st.earn_growth, (v) => pct(v * 100, true, 1))]])}</div>
        <div><div class="sub-h">BALANCE SHEET</div>${kvTable([["TOTAL CASH", big(st.total_cash)], ["TOTAL DEBT", big(st.total_debt)], ["DEBT / EQUITY", S(st.debt_equity, (v) => num(v, 1) + "%")], ["CURRENT RATIO", S(st.current_ratio, (v) => num(v, 2))], ["FREE CASH FLOW", big(st.fcf)], ["BOOK VALUE / SHARE", S(st.book_value, (v) => px(v))], ["EPS (TTM)", S(st.eps, (v) => px(v))], ["EPS (FORWARD)", S(st.fwd_eps, (v) => px(v))]])}</div>
        <div><div class="sub-h">TRADING &amp; DIVIDEND</div>${kvTable([["SHARES OUTSTANDING", big(st.shares_out)], ["FLOAT", big(st.float)], ["SHORT % OF FLOAT", S(st.short_pct_float, (v) => pct(v * 100, false, 2))], ["AVG VOLUME", big(s.avg_volume)], ["DIVIDEND YIELD", S(st.div_yield, (v) => pct(v * 100, false, 2))], ["ANNUAL DIVIDEND", S(st.div_rate, (v) => px(v))], ["PAYOUT RATIO", S(st.payout, (v) => pct(v * 100, false, 1))], ["EX-DIVIDEND", isNum(st.ex_div) ? NX.date(st.ex_div) : ""]])}</div>
      </div></div></div></div>`;
    if (tok !== sx.tok) return;
    NX.lineChart($("#des-chart"), s.spark.map((v, i) => [String(i), v]), { height: 190, fmt: (v) => px(v), color: s.pct >= 0 ? "#2be07e" : "#ff4a4a" });
    const ch = $("#des-chart");
    // label the mini chart with real dates by fetching the 1Y series once
    getData(`/invest/chart?symbol=${q(s.symbol)}&range=1Y`).then((d) => {
      if (tok !== sx.tok) return;
      NX.lineChart(ch, d.candles.map((c) => [new Date(c[0] * 1000).toISOString().slice(0, 10), c[4]]), { height: 190, fmt: (v) => px(v), color: s.pct >= 0 ? "#2be07e" : "#ff4a4a" });
    }).catch(() => {});
  }

  async function desCrypto(body, tok, s) {
    const c = await getData(`/invest/crypto/coin?symbol=${q(s.symbol)}`).catch(() => null);
    if (tok !== sx.tok) return;
    const m = c ? c.market : {};
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s7 h470" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>CRYPTO ASSET DESCRIPTION<span class="sp"></span><span class="meta">source: CoinGecko</span></div><div class="bb-pb bb-pad">
        <p class="about">${esc((c && c.description) || "No description available for this asset.")}</p>
        ${c ? `<div class="cols2">${kvTable([["NAME", esc(c.name)], ["SYMBOL", esc(c.symbol)], ["MARKET CAP RANK", c.rank ? "#" + c.rank : ""], ["GENESIS", esc(c.genesis || "")], ["HASHING", esc(c.hashing || "")]])}
        ${kvTable([["WEBSITE", c.links.website ? `<a href="${esc(c.links.website)}" target="_blank" rel="noopener noreferrer">${esc(c.links.website.replace(/^https?:\/\//, ""))}</a>` : ""], ["EXPLORER", c.links.explorer ? `<a href="${esc(c.links.explorer)}" target="_blank" rel="noopener noreferrer">${esc(c.links.explorer.replace(/^https?:\/\//, "").slice(0, 32))}</a>` : ""], ["GITHUB", c.links.github ? `<a href="${esc(c.links.github)}" target="_blank" rel="noopener noreferrer">repository</a>` : ""], ["TWITTER", c.links.twitter ? "@" + esc(c.links.twitter) : ""]])}</div>
        ${c.categories.length ? `<div class="sub-h">CATEGORIES</div><div class="sx-chips">${c.categories.map((x) => `<span class="pill pill-param">${esc(x)}</span>`).join("")}</div>` : ""}` : ""}
      </div></div>
      <div class="bb-panel s5 h470" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>MARKET DATA</div><div class="bb-pb bb-pad">
        <div id="des-chart" style="height:170px"></div>${perfStrip(s.perf)}
        ${c ? kvTable([["MARKET CAP", big(m.mcap, "$")], ["FULLY DILUTED VALUE", big(m.fdv, "$")], ["24H VOLUME", big(m.vol, "$")], ["24H HIGH / LOW", `${px(m.high24)} / ${px(m.low24)}`], ["CIRCULATING SUPPLY", big(m.supply)], ["TOTAL SUPPLY", big(m.total_supply)], ["MAX SUPPLY", m.max_supply ? big(m.max_supply) : "∞"], ["ALL-TIME HIGH", `${px(m.ath)} <span class="${dir(m.ath_pct)}">(${pct(m.ath_pct, true, 1)})</span>`], ["ALL-TIME LOW", `${px(m.atl)}`], ["1H / 24H / 7D / 30D", [m.p1h, m.p24h, m.p7d, m.p30d].map((v) => `<span class="${dir(v)}">${pct(v, true, 1)}</span>`).join(" · ")]]) : ""}
      </div></div></div>`;
    getData(`/invest/chart?symbol=${q(s.symbol)}&range=1Y`).then((d) => {
      if (tok === sx.tok) NX.lineChart($("#des-chart"), d.candles.map((x) => [new Date(x[0] * 1000).toISOString().slice(0, 10), x[4]]), { height: 170, fmt: (v) => px(v), color: s.pct >= 0 ? "#2be07e" : "#ff4a4a" });
    }).catch(() => {});
  }

  async function desOther(body, tok, s) {
    body.innerHTML = `<div class="bb-grid"><div class="bb-panel s7 h420" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>INSTRUMENT<span class="sp"></span></div><div class="bb-pb bb-pad">
      <p class="about">${esc(s.name)} — ${esc((s.type || "instrument").toLowerCase())} quoted on ${esc(s.exchange || "its exchange")}${s.currency ? " in " + esc(curLabel(s.currency)) : ""}. Use GP for the price chart, N for news, and DATA for every field.</p>
      ${kvTable([["SYMBOL", esc(s.symbol)], ["BLOOMBERG STYLE", esc(s.bbg)], ["TYPE", esc(s.type)], ["EXCHANGE", esc(s.exchange)], ["CURRENCY", esc(curLabel(s.currency))], ["TIME ZONE", esc(s.timezone)]])}</div></div>
      <div class="bb-panel s5 h420" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>PRICE SNAPSHOT</div><div class="bb-pb bb-pad"><div id="des-chart" style="height:190px"></div>${perfStrip(s.perf)}</div></div></div>`;
    getData(`/invest/chart?symbol=${q(s.symbol)}&range=1Y`).then((d) => {
      if (tok === sx.tok) NX.lineChart($("#des-chart"), d.candles.map((x) => [new Date(x[0] * 1000).toISOString().slice(0, 10), x[4]]), { height: 190, fmt: (v) => px(v), color: s.pct >= 0 ? "#2be07e" : "#ff4a4a" });
    }).catch(() => {});
  }

  // ------------------------------------------------------------------ GP: price chart (lightweight-charts)
  const sma = (data, n) => {
    const out = []; let sum = 0;
    data.forEach((d, i) => { sum += d.close; if (i >= n) sum -= data[i - n].close; if (i >= n - 1) out.push({ time: d.time, value: sum / n }); });
    return out;
  };
  async function drawChart(el, legendEl, symbol, range, o) {
    const d = await getData(`/invest/chart?symbol=${q(symbol)}&range=${range}`);
    const intraday = ["1D", "5D", "1M"].includes(range);
    const seen = new Set(), data = [];
    for (const c of d.candles) {
      const time = intraday ? c[0] : new Date(c[0] * 1000).toISOString().slice(0, 10);
      if (seen.has(time)) continue;
      seen.add(time);
      data.push({ time, open: c[1], high: c[2], low: c[3], close: c[4], volume: c[5] });
    }
    disposeChart();
    el.innerHTML = "";
    const LW = window.LightweightCharts;
    const chart = LW.createChart(el, {
      autoSize: true, localization: { locale: "en-US" }, layout: { background: { type: "solid", color: "#050505" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#121212" }, horzLines: { color: "#121212" } },
      crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" }, horzLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" } },
      rightPriceScale: { borderColor: "#262626" }, timeScale: { borderColor: "#262626", timeVisible: intraday, secondsVisible: false },
    });
    sx.chart = chart;
    let main;
    if (o.type === "line") main = chart.addLineSeries({ color: "#ffa028", lineWidth: 2 });
    else if (o.type === "area") main = chart.addAreaSeries({ lineColor: "#ffa028", topColor: "rgba(255,160,40,0.35)", bottomColor: "rgba(255,160,40,0)", lineWidth: 2 });
    else main = chart.addCandlestickSeries({ upColor: "#2be07e", downColor: "#ff4a4a", borderVisible: false, wickUpColor: "#2be07e", wickDownColor: "#ff4a4a" });
    main.setData(o.type === "candle" ? data : data.map((x) => ({ time: x.time, value: x.close })));
    // gamma levels as horizontal lines (equities / ETFs with listed options); decoration only, never blocks the chart
    if (["EQUITY", "ETF"].includes(String((sx.sec || {}).type || "").toUpperCase())) {
      getData(`/invest/gamma?symbol=${q(symbol)}`).then((g) => {
        if (!g || !g.ok || sx.chart !== chart) return;
        const line = (price, color, title) => price && main.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
        line(g.call_wall, "#2be07e", "CALL WALL"); line(g.put_wall, "#ff4a4a", "PUT WALL"); line(g.zero_gamma, "#ffa028", "ZERO γ");
      }).catch(() => {});
    }
    // markers: big move days (what the move was), and critical events the terminal detected (war, CPI, market shocks)
    try {
      const marks = [];
      if (!intraday && data.length > 30) {
        const rets = data.map((x, i) => (i ? (x.close / data[i - 1].close - 1) * 100 : 0));
        const sd = Math.sqrt(rets.slice(1).reduce((a, r) => a + r * r, 0) / (rets.length - 1)) || 1;
        rets.forEach((r, i) => { if (i && Math.abs(r) >= Math.max(4, 3 * sd)) marks.push({ time: data[i].time, position: r > 0 ? "aboveBar" : "belowBar", color: r > 0 ? "#2be07e" : "#ff4a4a", shape: r > 0 ? "arrowUp" : "arrowDown", text: `${r > 0 ? "+" : ""}${r.toFixed(1)}%` }); });
        const ev = await getData("/intel/events?hours=8760&min_sev=2").catch(() => ({ events: [] }));
        const days = new Set(data.map((x) => x.time));
        for (const e of ev.events) {
          const day = new Date(e.ts * 1000).toISOString().slice(0, 10);
          if (days.has(day)) marks.push({ time: day, position: "aboveBar", color: e.severity >= 3 ? "#ff4a4a" : "#ffa028", shape: "circle", text: (e.kind || "event").toUpperCase() });
        }
      }
      // historical events (library, reaction measured from prices): shown when this security reacted strongly or the event is about it
      const hist = [];
      if (!intraday && data.length > 30) {
        const hx = await getData(`/intel/history/symbol?symbol=${q(symbol)}`).catch(() => ({ events: [] }));
        const days = new Set(data.map((x) => x.time));
        const words = [symbol, ((sx.sec || {}).name || "").split(/[ ,.]/)[0]].map((w) => String(w || "").replace(/[^A-Za-z0-9]/g, "")).filter((w) => w.length > 2);
        const nameRx = words.length ? new RegExp(String.raw`\b(` + words.join("|") + String.raw`)\b`, "i") : null;
        for (const e of hx.events || []) {
          if (!days.has(e.day)) continue;
          const own = !!nameRx && nameRx.test(e.title);
          if (!own && !(Math.abs(e.z0 || 0) >= 1.8)) continue;
          hist.push(e);
          marks.push({ time: e.day, position: e.d0 >= 0 ? "aboveBar" : "belowBar", color: "#c58bff", shape: "square", text: e.label });
        }
      }
      if (hist.length) {
        const byDay = new Map(hist.map((e) => [e.day, e]));
        if (getComputedStyle(el).position === "static") el.style.position = "relative";
        const tip = document.createElement("div");
        tip.style.cssText = "position:absolute;left:10px;top:34px;z-index:5;max-width:340px;padding:8px 10px;background:rgba(10,8,20,0.94);border:1px solid #c58bff;border-radius:4px;font:11px JetBrains Mono,Consolas,monospace;color:#ddd;pointer-events:none;display:none";
        el.appendChild(tip);
        const pc = (v) => (v === null || v === undefined ? "—" : `<span style="color:${v >= 0 ? "#2be07e" : "#ff4a4a"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</span>`);
        chart.subscribeCrosshairMove((p) => {
          const t = p && p.time ? (typeof p.time === "string" ? p.time : p.time.year ? `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}` : "") : "";
          const e = byDay.get(t);
          if (!e) { tip.style.display = "none"; return; }
          tip.innerHTML = `<div style="color:#c58bff;font-weight:700">📜 ${esc(e.label)} · ${esc(e.date)}</div><div style="margin:3px 0 5px">${esc(e.title)}</div>` +
            `${esc(symbol)}: day ${pc(e.d0)} · week ${pc(e.d5)}${isNum(e.z0) ? ` · ${Math.abs(e.z0).toFixed(1)}× normal move` : ""}<br>S&amp;P 500 that day: ${pc(e.spx)}`;
          tip.style.display = "block";
        });
      }
      const seenAt = new Set();
      main.setMarkers(marks.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)).filter((m) => { const k = m.time + m.text; if (seenAt.has(k)) return false; seenAt.add(k); return true; }));
    } catch (e) { /* markers are decoration: never break the chart */ }
    if (o.vol !== false && data.some((x) => x.volume)) {
      const vs = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      vs.setData(data.map((x, i) => ({ time: x.time, value: x.volume, color: x.close >= (data[i - 1] ? data[i - 1].close : x.open) ? "rgba(43,224,126,0.35)" : "rgba(255,74,74,0.35)" })));
    }
    if (o.sma && !intraday) {
      [[50, "#5aa9ff"], [200, "#c58bff"]].forEach(([n, color]) => { if (data.length > n) chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(sma(data, n)); });
    }
    chart.timeScale().fitContent();
    const last = data[data.length - 1], first = data[0];
    const setLegend = (c, prev) => {
      if (!legendEl || !c) return;
      const ch = prev ? c.close - prev : c.close - c.open;
      legendEl.innerHTML = `<b>${esc(sx.sec ? sx.sec.bbg : symbol)}</b> <span>O</span>${px(c.open)} <span>H</span>${px(c.high)} <span>L</span>${px(c.low)} <span>C</span>${px(c.close)} <b class="${dir(ch)}">${signed(ch)} (${pct((ch / (prev || c.open)) * 100)})</b> <span>VOL</span>${big(c.volume)}`;
    };
    setLegend(last, data[data.length - 2] && data[data.length - 2].close);
    chart.subscribeCrosshairMove((p) => {
      const v = p.seriesData && p.seriesData.get(main);
      if (!v) { setLegend(last, data[data.length - 2] && data[data.length - 2].close); return; }
      const c = "open" in v ? v : { open: v.value, high: v.value, low: v.value, close: v.value, volume: (data.find((x) => x.time === p.time) || {}).volume };
      const idx = data.findIndex((x) => x.time === p.time);
      setLegend({ ...c, volume: (data[idx] || {}).volume }, idx > 0 ? data[idx - 1].close : null);
    });
    return { first, last, n: data.length };
  }

  async function funcGP(body, tok) {
    if (window.NXGP) return window.NXGP.render(body, { sx, getData, disposeChart });
    const s = sx.sec;
    const st = (sx.gp = sx.gp || { range: "1Y", type: "candle", sma: true, vol: true });
    body.innerHTML = `<div class="bb-panel gp" style="--i:1">
      <div class="bb-ph"><span class="bb-n">1</span>PRICE CHART<span class="sp"></span>
        ${NX.chips("gp-range", ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"].map((r) => [r, r]), st.range)}
        <span style="width:8px"></span>${NX.chips("gp-type", [["candle", "CANDLE"], ["line", "LINE"], ["area", "AREA"]], st.type)}
        <button class="bb-chip ${st.sma ? "on" : ""}" id="gp-sma">SMA 50/200</button><button class="bb-chip ${st.vol ? "on" : ""}" id="gp-vol">VOLUME</button></div>
      <div class="gp-legend" id="gp-legend">&nbsp;</div><div id="gp-chart" style="height:560px"></div></div>`;
    const draw = async () => {
      const my = sx.tok;
      try { await drawChart($("#gp-chart"), $("#gp-legend"), s.symbol, st.range, st); }
      catch (e) { if (my === sx.tok) $("#gp-chart").innerHTML = NX.err(e); }
    };
    NX.wireChips("gp-range", (r) => { st.range = r; draw(); });
    NX.wireChips("gp-type", (t) => { st.type = t; draw(); });
    $("#gp-sma").addEventListener("click", (e) => { st.sma = !st.sma; e.currentTarget.classList.toggle("on", st.sma); draw(); });
    $("#gp-vol").addEventListener("click", (e) => { st.vol = !st.vol; e.currentTarget.classList.toggle("on", st.vol); draw(); });
    await draw();
  }

  // ------------------------------------------------------------------ FA: financial statements
  const fyLabel = (d) => new Date(d).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
  async function funcFA(body, tok) {
    const s = sx.sec;
    const fa = await getData(`/invest/financials?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    if (!fa.available) {
      const st = s.stats || {};
      body.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>FINANCIAL ANALYSIS</div><div class="bb-empty"><b>STATEMENTS NOT AVAILABLE</b>${esc(fa.reason)}</div></div>
        <div class="bb-panel" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>SUMMARY (TTM)</div><div class="bb-pb bb-pad">${kvTable([["REVENUE", big(st.revenue)], ["EBITDA", big(st.ebitda)], ["NET MARGIN", S(st.profit_margin, (v) => pct(v * 100, false, 1))], ["FREE CASH FLOW", big(st.fcf)], ["TOTAL CASH", big(st.total_cash)], ["TOTAL DEBT", big(st.total_debt)], ["ROE", S(st.roe, (v) => pct(v * 100, false, 1))]])}</div></div>`;
      return;
    }
    // oldest -> newest, newest highlighted
    const idx = fa.periods.map((_, i) => i).reverse();
    const periods = idx.map((i) => fa.periods[i]);
    const R = {};
    fa.rows.forEach((r) => (R[r.key] = idx.map((i) => r.values[i])));
    const ratio = (a, b) => (R[a] && R[b] ? R[a].map((v, i) => (isNum(v) && isNum(R[b][i]) && R[b][i] ? (v / R[b][i]) * 100 : null)) : null);
    const growth = (k) => (R[k] ? R[k].map((v, i) => (i && isNum(v) && isNum(R[k][i - 1]) && R[k][i - 1] ? (v / R[k][i - 1] - 1) * 100 : null)) : null);
    const line = (label, vals, kind) => vals ? `<tr class="${kind === "sub" ? "sub" : ""}"><td class="k">${label}</td>${vals.map((v, i) => `<td class="r ${i === vals.length - 1 ? "cur" : ""} ${kind === "pct" ? dir(v) : ""}">${kind === "pct" ? (isNum(v) ? pct(v, kind === "pct", 1) : "—") : kind === "eps" ? (isNum(v) ? num(v, 2) : "—") : kind === "x" ? (isNum(v) ? num(v, 2) + "x" : "—") : big(v)}</td>`).join("")}</tr>` : "";
    const head = (t) => `<tr class="sec"><td colspan="${periods.length + 1}">${t}</td></tr>`;
    const cur = R.equity ? R.liab.map((v, i) => (isNum(v) && isNum(R.equity[i]) && R.equity[i] ? v / R.equity[i] : null)) : null;
    const table = `<table class="bb-tbl fa"><thead><tr><th>FISCAL YEAR END</th>${periods.map((p, i) => `<th class="r ${i === periods.length - 1 ? "cur" : ""}">${fyLabel(p)}</th>`).join("")}</tr></thead><tbody>
      ${head("INCOME STATEMENT")}${line("Revenue", R.revenue)}${line("Cost of revenue", R.cogs)}${line("Gross profit", R.gross)}${line("R&amp;D expense", R.rnd)}${line("SG&amp;A expense", R.sga)}${line("Operating income", R.opinc)}${line("Net income", R.netinc)}${line("Diluted EPS ($)", R.eps, "eps")}${line("Diluted shares", R.shares)}
      ${head("MARGINS &amp; GROWTH")}${line("Revenue growth y/y", growth("revenue"), "pct")}${line("Net income growth y/y", growth("netinc"), "pct")}${line("EPS growth y/y", growth("eps"), "pct")}${line("Gross margin", ratio("gross", "revenue"), "pct")}${line("Operating margin", ratio("opinc", "revenue"), "pct")}${line("Net margin", ratio("netinc", "revenue"), "pct")}${line("Free-cash-flow margin", ratio("fcf", "revenue"), "pct")}${line("Return on equity", ratio("netinc", "equity"), "pct")}
      ${head("BALANCE SHEET")}${line("Cash &amp; equivalents", R.cash)}${line("Current assets", R.curassets)}${line("Total assets", R.assets)}${line("Current liabilities", R.curliab)}${line("Long-term debt", R.debt)}${line("Total liabilities", R.liab)}${line("Shareholders' equity", R.equity)}${line("Liabilities / equity", cur, "x")}
      ${head("CASH FLOW")}${line("Operating cash flow", R.ocf)}${line("Capital expenditure", R.capex)}${line("Free cash flow", R.fcf)}${line("Dividends paid", R.div)}${line("Share repurchases", R.buyback)}
    </tbody></table>`;
    const labels = periods.map(fyLabel);
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s6 h270" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>REVENUE · NET INCOME<span class="sp"></span><span class="meta">annual, USD</span></div><div class="bb-pb bb-pad"><div id="fa-c1"></div></div></div>
      <div class="bb-panel s6 h270" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>OPERATING CASH FLOW · FREE CASH FLOW<span class="sp"></span><span class="meta">annual, USD</span></div><div class="bb-pb bb-pad"><div id="fa-c2"></div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>FINANCIAL STATEMENTS<span class="sp"></span><span class="meta">${esc(fa.source)} · ${esc(fa.company || "")}</span></div><div class="bb-pb bb-pad" style="overflow-x:auto">${table}</div></div></div>`;
    NX.barChart($("#fa-c1"), labels, [{ name: "Revenue", color: "#ffa028", values: R.revenue || [] }, { name: "Net income", color: "#5aa9ff", values: R.netinc || [] }], { height: 190 });
    NX.barChart($("#fa-c2"), labels, [{ name: "Operating cash flow", color: "#2be07e", values: R.ocf || [] }, { name: "Free cash flow", color: "#c58bff", values: R.fcf || [] }], { height: 190 });
  }

  // ------------------------------------------------------------------ N: news + search
  async function funcN(body, tok) {
    const s = sx.sec;
    const st = (sx.news = { mode: "symbol", q: s.symbol });
    body.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>NEWS<span class="sp"></span><span class="meta" id="n-meta"></span>
        ${NX.chips("n-mode", [["symbol", `${esc(s.symbol)} NEWS`], ["search", "NEWS SEARCH"]], "symbol")}</div>
      <div class="sx-search"><input id="n-q" value="${esc(s.symbol)}" placeholder="Search news on any topic, company, person or event…" autocomplete="off" spellcheck="false" /><button class="bb-go" id="n-go">SEARCH</button></div>
      <div class="ai-box" id="n-ai"><span class="dim">Summarising headlines…</span></div>
      <div class="bb-pb news-body" id="n-body">${NX.load()}</div></div>`;
    getData(`/ai/summary?symbol=${q(s.symbol)}`).then((r) => {
      const el = $("#n-ai"); if (!el || tok !== sx.tok) return;
      el.innerHTML = `<div class="ai-h"><b>${r.mode === "llm" ? "AI SUMMARY · " + esc(r.model) : "HEADLINE DIGEST"}</b><span>${esc(r.note)}</span></div><p>${esc(r.text).split("\n").join("<br>")}</p>${(r.themes || []).map((t) => `<div class="ai-t"><em>${esc(t.theme)}</em> (${t.count}) — ${t.examples.map((x) => esc(x)).join(" · ")}</div>`).join("")}`;
    }).catch(() => { const el = $("#n-ai"); if (el) el.remove(); });
    const go = async () => {
      const val = $("#n-q").value.trim(); if (!val) return;
      const my = ++sx.tok, sym = st.mode === "symbol" && val.toUpperCase() === s.symbol.toUpperCase();
      $("#n-body").innerHTML = NX.load("SEARCHING");
      try {
        const r = await getData(`/invest/news?${sym ? "symbol" : "q"}=${q(val)}`);
        if (my !== sx.tok) return;
        $("#n-body").innerHTML = NX.newsHtml(r.items, 60); $("#n-meta").textContent = `${r.items.length} HEADLINES · ${esc(val)}`;
      } catch (e) { $("#n-body").innerHTML = NX.err(e); }
    };
    NX.wireChips("n-mode", (m) => { st.mode = m; $("#n-q").value = m === "symbol" ? s.symbol : ""; if (m === "symbol") go(); else $("#n-q").focus(); });
    $("#n-go").addEventListener("click", go);
    $("#n-q").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    await go();
  }

  // ------------------------------------------------------------------ ANR: analysts
  async function funcANR(body, tok) {
    const s = sx.sec, a = await getData(`/invest/analysts?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    const t = a.target, cur = s.price;
    const trend = a.recommendation || [];
    const cats = [["strongBuy", "STRONG BUY", "#2be07e"], ["buy", "BUY", "#7ee8a6"], ["hold", "HOLD", "#ffa028"], ["sell", "SELL", "#ff8a5c"], ["strongSell", "STRONG SELL", "#ff4a4a"]];
    const first = trend[0] || {}, total = cats.reduce((n, c) => n + (first[c[0]] || 0), 0) || 1;
    const up = isNum(t.mean) && cur ? (t.mean / cur - 1) * 100 : null;
    const periodName = { "0m": "Current", "-1m": "1 month ago", "-2m": "2 months ago", "-3m": "3 months ago" };
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s4 h330" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>CONSENSUS</div><div class="bb-pb bb-pad">
        <div class="cons"><div class="cons-r">${esc((t.rating || "—").toUpperCase().replace("_", " "))}</div><div class="dim">${isNum(t.score) ? "score " + num(t.score, 2) + " of 5 (1 = strong buy)" : ""} · ${t.n || "?"} analysts</div></div>
        ${targetBlock({ ...s, target: t })}
        ${kvTable([["MEAN TARGET", px(t.mean)], ["MEDIAN TARGET", px(t.median)], ["HIGH / LOW", `${px(t.high)} / ${px(t.low)}`], ["UPSIDE TO MEAN", isNum(up) ? `<span class="${dir(up)}">${pct(up, true, 1)}</span>` : ""], ["NEXT EARNINGS", (a.next_earnings || []).length ? NX.date(a.next_earnings[0]) : ""]])}</div></div>
      <div class="bb-panel s4 h330" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>RATING DISTRIBUTION</div><div class="bb-pb bb-pad">
        ${trend.map((tr) => { const n = cats.reduce((x, c) => x + (tr[c[0]] || 0), 0) || 1; return `<div class="dist"><div class="dist-l">${periodName[tr.period] || tr.period}</div><div class="dist-b">${cats.map((c) => `<i style="width:${((tr[c[0]] || 0) / n) * 100}%;background:${c[2]}" title="${c[1]}: ${tr[c[0]] || 0}"></i>`).join("")}</div></div>`; }).join("")}
        <div class="dist-key">${cats.map((c) => `<span><i style="background:${c[2]}"></i>${c[1]} <b>${first[c[0]] ?? 0}</b></span>`).join("")}</div></div></div>
      <div class="bb-panel s4 h330" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>EARNINGS SURPRISES</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>QUARTER</th><th class="r">ACTUAL</th><th class="r">ESTIMATE</th><th class="r">SURPRISE</th></tr></thead><tbody>
        ${(a.surprises || []).map((x) => `<tr><td>${esc(x.quarter ? NX.date(x.quarter) : "")}</td><td class="r">${px(x.actual)}</td><td class="r dim">${px(x.estimate)}</td><td class="r ${dir(x.surprise_pct)}">${isNum(x.surprise_pct) ? pct(x.surprise_pct * 100, true, 1) : "—"}</td></tr>`).join("") || `<tr><td colspan="4" class="dim">no data</td></tr>`}</tbody></table></div></div>
      <div class="bb-panel s6 h340" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>ESTIMATES</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>PERIOD</th><th class="r">EPS AVG</th><th class="r">EPS RANGE</th><th class="r">REVENUE AVG</th><th class="r">GROWTH</th><th class="r">#</th></tr></thead><tbody>
        ${(a.estimates || []).map((x) => `<tr><td class="sym">${esc({ "0q": "Current quarter", "+1q": "Next quarter", "0y": "Current year", "+1y": "Next year" }[x.period] || x.period)}</td><td class="r">${px(x.eps_avg)}</td><td class="r dim">${px(x.eps_low)} – ${px(x.eps_high)}</td><td class="r">${big(x.rev_avg)}</td><td class="r ${dir(x.growth)}">${isNum(x.growth) ? pct(x.growth * 100, true, 1) : "—"}</td><td class="r dim">${x.analysts ?? ""}</td></tr>`).join("") || `<tr><td colspan="6" class="dim">no estimates</td></tr>`}</tbody></table></div></div>
      <div class="bb-panel s6 h340" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>UPGRADES &amp; DOWNGRADES</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>DATE</th><th>FIRM</th><th>ACTION</th><th>RATING CHANGE</th></tr></thead><tbody>
        ${(a.changes || []).map((x) => `<tr><td class="dim">${NX.date(x.ts)}</td><td class="sym">${esc(x.firm)}</td><td class="${/up/i.test(x.action) ? "up" : /down/i.test(x.action) ? "dn" : "dim"}">${esc((x.action || "").toUpperCase())}</td><td>${x.from ? esc(x.from) + " → " : ""}<b>${esc(x.to || "")}</b></td></tr>`).join("") || `<tr><td colspan="4" class="dim">none</td></tr>`}</tbody></table></div></div></div>`;
  }

  // ------------------------------------------------------------------ HDS: holders
  async function funcHDS(body, tok) {
    const h = await getData(`/invest/holders?symbol=${q(sx.sec.symbol)}`);
    if (tok !== sx.tok) return;
    const b = h.breakdown || {};
    const own = (rows) => `<table class="bb-tbl"><thead><tr><th>HOLDER</th><th class="r">% HELD</th><th class="r">SHARES</th><th class="r">VALUE</th><th class="r">CHANGE</th><th>REPORTED</th></tr></thead><tbody>${rows.map((r) => `<tr><td class="sym">${esc(r.name)}</td><td class="r">${isNum(r.pct) ? pct(r.pct * 100, false, 2) : "—"}</td><td class="r">${big(r.shares)}</td><td class="r">${big(r.value, "$")}</td><td class="r ${dir(r.chg)}">${isNum(r.chg) ? pct(r.chg * 100, true, 1) : "—"}</td><td class="dim">${NX.date(r.date)}</td></tr>`).join("") || '<tr><td colspan="6" class="dim">no data</td></tr>'}</tbody></table>`;
    body.innerHTML = `<div class="bb-kpis" style="margin-bottom:10px">${[["HELD BY INSIDERS", isNum(b.insidersPercentHeld) ? pct(b.insidersPercentHeld * 100, false, 2) : "—"], ["HELD BY INSTITUTIONS", isNum(b.institutionsPercentHeld) ? pct(b.institutionsPercentHeld * 100, false, 1) : "—"], ["INSTITUTIONS % OF FLOAT", isNum(b.institutionsFloatPercentHeld) ? pct(b.institutionsFloatPercentHeld * 100, false, 1) : "—"], ["INSTITUTIONAL HOLDERS", isNum(b.institutionsCount) ? num(b.institutionsCount, 0) : "—"]].map(([l, v]) => `<div class="bb-kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
      <div class="bb-grid">
        <div class="bb-panel s6 h380" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>TOP INSTITUTIONAL HOLDERS</div><div class="bb-pb">${own(h.institutions)}</div></div>
        <div class="bb-panel s6 h380" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>TOP FUND HOLDERS</div><div class="bb-pb">${own(h.funds)}</div></div>
        <div class="bb-panel s6 h380" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>INSIDERS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>NAME</th><th>RELATION</th><th class="r">SHARES</th><th>LAST ACTIVITY</th><th>DATE</th></tr></thead><tbody>${h.insiders.map((i) => `<tr><td class="sym">${esc(i.name)}</td><td class="dim">${esc(i.relation)}</td><td class="r">${big(i.shares)}</td><td>${esc(i.last || "")}</td><td class="dim">${NX.date(i.date)}</td></tr>`).join("") || '<tr><td colspan="5" class="dim">no data</td></tr>'}</tbody></table></div></div>
        <div class="bb-panel s6 h380" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>RECENT INSIDER TRANSACTIONS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>DATE</th><th>INSIDER</th><th>TRANSACTION</th><th class="r">SHARES</th><th class="r">VALUE</th></tr></thead><tbody>${h.transactions.map((t) => `<tr><td class="dim">${NX.date(t.date)}</td><td class="sym">${esc(t.name)}</td><td>${esc((t.text || "").slice(0, 60))}</td><td class="r">${big(t.shares)}</td><td class="r">${big(t.value, "$")}</td></tr>`).join("") || '<tr><td colspan="5" class="dim">no data</td></tr>'}</tbody></table></div></div>
      </div>`;
  }

  // ------------------------------------------------------------------ FIL: SEC filings
  async function funcFIL(body, tok) {
    const s = sx.sec;
    const f = await getData(`/invest/filings?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    if (!f.available) { body.innerHTML = `<div class="bb-panel"><div class="bb-ph"><span class="bb-n">1</span>SEC FILINGS</div><div class="bb-empty"><b>NOT AVAILABLE</b>${esc(f.reason)}</div></div>`; return; }
    let form = "ALL";
    const draw = () => {
      const rows = f.items.filter((x) => form === "ALL" || x.form === form);
      $("#fil-body").innerHTML = `<table class="bb-tbl"><thead><tr><th>FORM</th><th>DESCRIPTION</th><th>FILED</th><th>PERIOD</th><th>DOCUMENT</th></tr></thead><tbody>${rows.map((x) => `<tr><td class="sym">${esc(x.form)}</td><td>${esc(x.note || x.desc || "")}${x.desc && x.note ? ` <span class="dim">· ${esc(x.desc.slice(0, 50))}</span>` : ""}</td><td>${esc(x.filed)}</td><td class="dim">${esc(x.period || "")}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">DOC ↗</a> <a href="${esc(x.index)}" target="_blank" rel="noopener noreferrer" class="dim">INDEX ↗</a></td></tr>`).join("") || '<tr><td colspan="5" class="dim">no filings of this form</td></tr>'}</tbody></table>`;
      $("#fil-meta").textContent = `${rows.length} FILINGS`;
    };
    const forms = ["ALL", "10-K", "10-Q", "8-K", "4", "DEF 14A", "S-1", "13F-HR"];
    body.innerHTML = `<div class="bb-grid"><div class="bb-panel s12" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>SEC EDGAR FILINGS<span class="sp"></span><span class="meta" id="fil-meta"></span>${NX.chips("fil-f", forms.map((x) => [x, x]), "ALL")}</div>
      <div class="fil-info">${esc(f.name)} · CIK ${esc(f.cik)} · ${esc(f.sic || "")} · fiscal year end ${esc(String(f.fiscal_year_end || "").replace(/(\d\d)(\d\d)/, "$1/$2"))} · <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${esc(f.cik)}&type=&dateb=&owner=include&count=40" target="_blank" rel="noopener noreferrer">EDGAR company page ↗</a></div>
      <div class="bb-pb fil-body" id="fil-body"></div></div></div>`;
    NX.wireChips("fil-f", (x) => { form = x; draw(); });
    draw();
  }

  // ------------------------------------------------------------------ DVD
  async function funcDVD(body, tok) {
    const s = sx.sec, d = await getData(`/invest/dividends?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    const st = s.stats || {};
    body.innerHTML = `<div class="bb-kpis" style="margin-bottom:10px">${[["DIVIDEND YIELD", S(st.div_yield, (v) => pct(v * 100, false, 2))], ["ANNUAL DIVIDEND", S(st.div_rate, (v) => px(v))], ["PAYOUT RATIO", S(st.payout, (v) => pct(v * 100, false, 1))], ["EX-DIVIDEND DATE", isNum(st.ex_div) ? NX.date(st.ex_div) : "—"]].map(([l, v]) => `<div class="bb-kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
      <div class="bb-grid">
        <div class="bb-panel s6 h300" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>DIVIDENDS PER YEAR<span class="sp"></span><span class="meta">${esc(curLabel(d.currency))}</span></div><div class="bb-pb bb-pad"><div id="dvd-c"></div></div></div>
        <div class="bb-panel s6 h300" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>STOCK SPLITS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>DATE</th><th>RATIO</th></tr></thead><tbody>${d.splits.map((x) => `<tr><td>${NX.date(x.ts)}</td><td class="sym">${esc(x.ratio)}</td></tr>`).join("") || '<tr><td colspan="2" class="dim">no splits on record</td></tr>'}</tbody></table></div></div>
        <div class="bb-panel s12 h360" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>DIVIDEND HISTORY<span class="sp"></span><span class="meta">${d.dividends.length} payments</span></div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>EX-DATE</th><th class="r">AMOUNT</th></tr></thead><tbody>${d.dividends.map((x) => `<tr><td>${NX.date(x.ts)}</td><td class="r">${px(x.amount, 4)}</td></tr>`).join("") || '<tr><td colspan="2" class="dim">no dividends on record</td></tr>'}</tbody></table></div></div></div>`;
    const yrs = [...d.yearly].reverse();
    if (yrs.length) NX.barChart($("#dvd-c"), yrs.map((y) => String(y.year)), [{ name: "Dividends", color: "#ffa028", values: yrs.map((y) => y.total) }], { height: 210, fmt: (v) => num(v, 2) });
    else $("#dvd-c").innerHTML = '<div class="bb-empty"><b>NO DIVIDENDS</b></div>';
  }

  // ------------------------------------------------------------------ OMON: options
  async function funcOMON(body, tok) {
    const s = sx.sec;
    const draw = async (date) => {
      const o = await getData(`/invest/options?symbol=${q(s.symbol)}${date ? "&date=" + date : ""}`);
      if (tok !== sx.tok) return;
      if (!o.expirations.length) { body.innerHTML = `<div class="bb-panel"><div class="bb-ph"><span class="bb-n">1</span>OPTIONS MONITOR</div><div class="bb-empty"><b>NO OPTIONS</b>No listed options for this security.</div></div>`; return; }
      const price = o.price || s.price;
      const byK = {};
      o.calls.forEach((c) => ((byK[c.strike] = byK[c.strike] || {}).c = c));
      o.puts.forEach((p) => ((byK[p.strike] = byK[p.strike] || {}).p = p));
      let strikes = Object.keys(byK).map(Number).sort((a, b) => a - b);
      const atm = strikes.reduce((b, k) => (Math.abs(k - price) < Math.abs(b - price) ? k : b), strikes[0]);
      const i0 = strikes.indexOf(atm);
      strikes = strikes.slice(Math.max(0, i0 - 12), i0 + 13);
      const cell = (o2) => o2 ? `<td class="r">${px(o2.last)}</td><td class="r dim">${px(o2.bid)}</td><td class="r dim">${px(o2.ask)}</td><td class="r ${dir(o2.chg)}">${isNum(o2.chg) ? pct(o2.chg, true, 1) : ""}</td><td class="r">${isNum(o2.vol) ? num(o2.vol, 0) : "—"}</td><td class="r dim">${isNum(o2.oi) ? num(o2.oi, 0) : "—"}</td><td class="r">${isNum(o2.iv) ? pct(o2.iv * 100, false, 1) : "—"}</td>` : '<td colspan="7"></td>';
      body.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>OPTIONS MONITOR · ${esc(s.bbg)}<span class="sp"></span><span class="meta">underlying ${px(price)} · ${strikes.length} strikes around the money</span></div>
        <div class="exp-row" id="om-exp">${o.expirations.slice(0, 16).map((e) => `<button class="bb-chip ${e === o.date ? "on" : ""}" data-e="${e}">${new Date(e * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" })}</button>`).join("")}</div>
        <div class="bb-pb" style="max-height:600px"><table class="bb-tbl om"><thead><tr><th colspan="7" class="ctr">CALLS</th><th class="ctr">STRIKE</th><th colspan="7" class="ctr">PUTS</th></tr><tr>${["LAST", "BID", "ASK", "CHG", "VOL", "OI", "IV"].map((x) => `<th class="r">${x}</th>`).join("")}<th></th>${["LAST", "BID", "ASK", "CHG", "VOL", "OI", "IV"].map((x) => `<th class="r">${x}</th>`).join("")}</tr></thead><tbody>
        ${strikes.map((k) => { const r = byK[k]; return `<tr class="${k === atm ? "atm" : ""}"><td class="itm-c" style="display:none"></td>${cell(r.c).replace(/^<td/, `<td class="${r.c && r.c.itm ? "itm" : ""}"`)}<td class="strike">${px(k)}</td>${cell(r.p)}</tr>`; }).join("")}</tbody></table></div></div>`;
      document.querySelectorAll("#om-exp .bb-chip").forEach((c) => c.addEventListener("click", () => draw(Number(c.dataset.e))));
    };
    await draw(null);
  }

  // ------------------------------------------------------------------ RV: peers
  async function funcRV(body, tok) {
    const s = sx.sec, r = await getData(`/invest/peers?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    const rows = [{ symbol: s.symbol, name: s.name, price: s.price, pct: s.pct, chg: s.chg, lo: s.week52[0], hi: s.week52[1], spark: s.spark.slice(-30), p1m: s.perf["1M"] }, ...r.peers.map((p) => ({ symbol: p.symbol, name: p.name, price: p.price, pct: p.pct, lo: p.week52[0], hi: p.week52[1], spark: p.spark }))];
    body.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>RELATIVE VALUE · PEERS<span class="sp"></span><span class="meta">${r.peers.length} comparable securities</span></div><div class="bb-pb" id="rv-b"></div></div>`;
    NX.monitorTable($("#rv-b"), rows, { cols: ["sym", "last", "pct", "range", "spark"], id: "rv-b" });
  }

  // ------------------------------------------------------------------ DATA: every field
  async function funcDATA(body) {
    const s = sx.sec, rows = [];
    const walk = (o, path) => {
      Object.entries(o || {}).forEach(([k, v]) => {
        if (k === "spark" || k === "description" || k === "officers") return;
        const p = path ? `${path}.${k}` : k;
        if (v && typeof v === "object" && !Array.isArray(v)) walk(v, p);
        else if (Array.isArray(v)) rows.push([p, v.length && typeof v[0] !== "object" ? v.join(", ") : `[${v.length} items]`]);
        else rows.push([p, v]);
      });
    };
    walk(s, "");
    const show = (f) => {
      const ff = f.toLowerCase();
      $("#data-b").innerHTML = `<table class="bb-tbl kv"><thead><tr><th>FIELD</th><th class="r">VALUE</th></tr></thead><tbody>${rows.filter((r) => !ff || r[0].toLowerCase().includes(ff)).map(([k, v]) => `<tr><td class="k">${esc(k.toUpperCase())}</td><td class="r">${v === null || v === undefined ? '<span class="dim">—</span>' : typeof v === "number" ? esc(String(Number(v.toPrecision(10)))) : esc(String(v))}</td></tr>`).join("")}</tbody></table>`;
    };
    body.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>ALL DATA FIELDS<span class="sp"></span><span class="meta">${rows.length} fields · ${esc(s.bbg)}</span></div>
      <div class="sx-search"><input id="data-f" placeholder="Filter fields (e.g. margin, pe, dividend)…" autocomplete="off" spellcheck="false" /></div><div class="bb-pb" id="data-b" style="max-height:620px"></div></div>`;
    $("#data-f").addEventListener("input", (e) => show(e.target.value.trim()));
    show("");
  }

  // ------------------------------------------------------------------ LAUNCHPAD
  async function renderLaunchpad(body, tok) {
    const s = sx.sec;
    body.innerHTML = `<div class="bb-grid lp">
      <div class="bb-panel s8 h420" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span><b class="fcode">GP</b>PRICE<span class="sp"></span><span class="meta">${esc(s.bbg)}</span><button class="bb-chip lp-open" data-fn="GP">OPEN ▸</button></div><div class="gp-legend" id="lp-legend">&nbsp;</div><div id="lp-chart" style="height:352px"></div></div>
      <div class="bb-panel s4 h420" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span><b class="fcode">DES</b>SNAPSHOT<span class="sp"></span><button class="bb-chip lp-open" data-fn="DES">OPEN ▸</button></div><div class="bb-pb bb-pad">${perfStrip(s.perf)}${targetBlock(s)}${kvTable([["MKT CAP", big(s.market_cap)], ["P/E (TTM)", S(s.stats.pe, (v) => num(v, 1))], ["FWD P/E", S(s.stats.fwd_pe, (v) => num(v, 1))], ["EPS (TTM)", S(s.stats.eps, (v) => px(v))], ["DIV YIELD", S(s.stats.div_yield, (v) => pct(v * 100, false, 2))], ["BETA", S(s.stats.beta, (v) => num(v, 2))], ["SECTOR", esc(s.profile.sector)]])}</div></div>
      <div class="bb-panel s4 h340" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span><b class="fcode">N</b>NEWS<span class="sp"></span><button class="bb-chip lp-open" data-fn="N">OPEN ▸</button></div><div class="bb-pb" id="lp-news">${NX.load()}</div></div>
      <div class="bb-panel s4 h340" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span><b class="fcode">FIL</b>SEC FILINGS<span class="sp"></span><button class="bb-chip lp-open" data-fn="FIL">OPEN ▸</button></div><div class="bb-pb" id="lp-fil">${NX.load()}</div></div>
      <div class="bb-panel s4 h340" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span><b class="fcode">ANR</b>ANALYSTS<span class="sp"></span><button class="bb-chip lp-open" data-fn="ANR">OPEN ▸</button></div><div class="bb-pb bb-pad" id="lp-anr">${NX.load()}</div></div>
    </div>`;
    document.querySelectorAll(".lp-open").forEach((b) => b.addEventListener("click", () => { sx.lp = false; sx.fn = b.dataset.fn; renderHeaderTabs(); renderBody(); }));
    drawChart($("#lp-chart"), $("#lp-legend"), s.symbol, "1Y", { type: "candle", sma: true, vol: true }).catch((e) => ($("#lp-chart").innerHTML = NX.err(e)));
    const t = (id, p, fn) => getData(p).then((d) => { if (tok === sx.tok) $(id).innerHTML = fn(d); }).catch((e) => { if (tok === sx.tok) $(id).innerHTML = NX.err(e); });
    t("#lp-news", `/invest/news?symbol=${q(s.symbol)}`, (d) => NX.newsHtml(d.items, 14));
    t("#lp-fil", `/invest/filings?symbol=${q(s.symbol)}`, (d) => !d.available ? `<div class="bb-empty"><b>US ISSUERS ONLY</b>${esc(d.reason)}</div>` : `<table class="bb-tbl"><tbody>${d.items.slice(0, 14).map((x) => `<tr><td class="sym">${esc(x.form)}</td><td>${esc(x.note || "")}</td><td class="dim">${esc(x.filed)}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">↗</a></td></tr>`).join("")}</tbody></table>`);
    t("#lp-anr", `/invest/analysts?symbol=${q(s.symbol)}`, (d) => {
      const tr = (d.recommendation || [])[0] || {}, cats = [["strongBuy", "STRONG BUY", "#2be07e"], ["buy", "BUY", "#7ee8a6"], ["hold", "HOLD", "#ffa028"], ["sell", "SELL", "#ff8a5c"], ["strongSell", "STRONG SELL", "#ff4a4a"]], n = cats.reduce((x, c) => x + (tr[c[0]] || 0), 0) || 1;
      return `<div class="cons"><div class="cons-r">${esc((d.target.rating || "—").toUpperCase().replace("_", " "))}</div><div class="dim">${d.target.n || "?"} analysts · mean target ${px(d.target.mean)}</div></div><div class="dist-b" style="margin:8px 0">${cats.map((c) => `<i style="width:${((tr[c[0]] || 0) / n) * 100}%;background:${c[2]}"></i>`).join("")}</div><div class="dist-key">${cats.map((c) => `<span><i style="background:${c[2]}"></i>${c[1]} <b>${tr[c[0]] ?? 0}</b></span>`).join("")}</div>`;
    });
  }

  // ------------------------------------------------------------------ WHY: reasons to buy / not buy / hold, each with its numbers
  async function funcWHY(body, tok) {
    const s = sx.sec;
    const v = await getData(`/invest/verdict?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    const cls = { "BUY CANDIDATE": "buy", "LEAN BUY / ACCUMULATE": "lean-buy", "WAIT / NEUTRAL": "wait", "LEAN AVOID": "lean-avoid", AVOID: "avoid" }[v.stance] || "none";
    const card = (r, side) => `<div class="why-card ${side}"><h4>${esc(r.title)}<span class="w">WEIGHT ${"●".repeat(r.w)}${"○".repeat(Math.max(0, 3 - r.w))}</span></h4><p>${esc(r.detail)}</p><div class="why-ev">${r.evidence.map((e) => `<span>${esc(e[0])} <b>${esc(e[1])}</b></span>`).join("")}</div></div>`;
    const col = (n, title, list, side, emptyMsg) => `<div class="bb-panel s4 h560" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${list.length} reason${list.length === 1 ? "" : "s"}</span></div><div class="bb-pb why-col">${list.length ? list.map((r) => card(r, side)).join("") : `<div class="why-empty">${emptyMsg}</div>`}</div></div>`;
    const lv = v.levels || {}, rr = v.risk_reward, pos = Math.max(0, Math.min(100, (v.score + 100) / 2));
    const money = (x) => (isNum(x) ? "$" + NX.num(x, 2) : "—");
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s12" style="--i:0"><div class="bb-ph"><span class="bb-n">0</span>VERDICT · ${esc(s.symbol)}<span class="sp"></span><span class="meta">model vote · ${v.votes.for} for / ${v.votes.against} against</span></div>
        <div class="why-banner">
          <div><div class="why-stance ${cls}">${esc(v.stance)}</div><div class="why-sub">${esc(v.stance_why)}</div></div>
          <div><div class="why-gl"><span>AVOID</span><span>NEUTRAL</span><span>BUY</span></div><div class="why-gauge"><i style="left:${pos}%"></i></div><div class="why-gl"><span>-100</span><span>score ${v.score > 0 ? "+" : ""}${v.score}</span><span>+100</span></div></div>
          <div class="why-kv">
            <div><span>RSI (14)</span><b>${isNum(v.rsi) ? v.rsi : "—"}</b></div>
            <div><span>TO MEAN TARGET</span><b class="${rr ? dir(rr.upside_pct) : ""}">${rr ? pct(rr.upside_pct, true, 0) : isNum(lv.target_mean) ? pct((lv.target_mean / lv.price - 1) * 100, true, 0) : "—"}</b></div>
            <div><span>TO SUPPORT</span><b class="${rr ? "dn" : ""}">${rr ? "-" + rr.downside_pct + "% @ " + money(rr.support) : "—"}</b></div>
            <div><span>REWARD / RISK</span><b>${rr && isNum(rr.ratio) && rr.ratio > 0 ? rr.ratio + " : 1" : "no edge"}</b></div>
          </div></div></div>
      ${col(1, "WHY BUY", v.why_buy, "buy", "No strong reason to buy was found in the data.")}
      ${col(2, "WHY NOT", v.why_not, "not", "No clear red flags in the measurable data.")}
      ${col(3, "WHY HOLD", v.why_hold, "hold", "Nothing in the data specifically argues for holding.")}
      <div class="bb-panel s6 h300" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>KEY LEVELS &amp; WHAT TO WATCH</div><div class="bb-pb bb-pad">
        ${kvTable([["PRICE", money(lv.price)], ["50-DAY AVERAGE", money(lv.sma50)], ["200-DAY AVERAGE", money(lv.sma200)], ["52-WEEK LOW / HIGH", `${money(lv.low52)} / ${money(lv.high52)}`], ["ANALYST TARGET (LOW / MEAN / HIGH)", `${money(lv.target_low)} / ${money(lv.target_mean)} / ${money(lv.target_high)}`]])}
        <div class="sub-h">WATCH</div><ul class="why-watch">${(v.watch.length ? v.watch : ["No specific levels available."]).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
      <div class="bb-panel s6 h300" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>NEWS TONE · LAST 7 DAYS<span class="sp"></span><span class="meta">${v.news.n} headlines · net ${v.news.score > 0 ? "+" : ""}${v.news.score}</span></div><div class="bb-pb bb-pad">
        <ul class="why-news">${v.news.items.map((n) => `<li><span class="${n.tone > 0 ? "t-up" : n.tone < 0 ? "t-dn" : "t-0"}">${n.tone > 0 ? "▲" : n.tone < 0 ? "▼" : "•"}</span><a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a><span class="dim">${NX.ago(n.ts)}</span></li>`).join("") || '<li class="dim">No recent headlines.</li>'}</ul></div></div>
    </div>
    <div class="why-note">${esc(v.method)}${v.missing.length ? " Not measured for this security: " + esc(v.missing.join(", ")) + "." : ""}</div>`;
  }

  // ------------------------------------------------------------------ GEX: gamma exposure levels
  async function funcGEX(body, tok) {
    const s = sx.sec;
    const g = await getData(`/invest/gamma?symbol=${q(s.symbol)}`);
    if (tok !== sx.tok) return;
    if (!g.ok) { body.innerHTML = `<div class="bb-empty"><b>NO GAMMA DATA</b>${esc(g.note || "")}</div>`; return; }
    const m = (x) => (isNum(x) ? "$" + NX.num(x, 2) : "—");
    const big = (x) => (x >= 0 ? "+" : "-") + "$" + NX.num(Math.abs(x) / 1e6, 1) + "M";
    const near = g.strikes.filter((r) => Math.abs(r.strike / g.spot - 1) <= 0.08 && (r.wall_call || r.wall_put))
      .sort((x, y) => Math.max(y.wall_call, y.wall_put) - Math.max(x.wall_call, x.wall_put)).slice(0, 25).sort((x, y) => x.strike - y.strike);  // the 25 strongest strikes
    const mx = Math.max(1, ...near.map((r) => Math.max(r.wall_call, r.wall_put)));
    const bars = near.slice().reverse().map((r) => {
      const tag = r.strike === g.call_wall ? " ◀ CALL WALL" : r.strike === g.put_wall ? " ◀ PUT WALL" : "";
      const here = Math.abs(r.strike - g.spot) === Math.min(...near.map((x) => Math.abs(x.strike - g.spot)));
      return `<div style="display:grid;grid-template-columns:70px 1fr 1fr 110px;gap:6px;align-items:center;font-size:11px;${here ? "background:#1a1408" : ""}">
        <span style="text-align:right;${here ? "color:#ffa028;font-weight:700" : ""}">${NX.num(r.strike, 2)}</span>
        <span style="display:flex;justify-content:flex-end"><i style="display:block;height:9px;width:${(r.wall_put / mx) * 100}%;background:#ff4a4a"></i></span>
        <span><i style="display:block;height:9px;width:${(r.wall_call / mx) * 100}%;background:#2be07e"></i></span>
        <span class="dim">${tag}</span></div>`;
    }).join("");
    const prof = g.profile, pmin = Math.min(...prof.map((p) => p.gex)), pmax = Math.max(...prof.map((p) => p.gex));
    const W = 560, H = 160, xs = (i) => (i / (prof.length - 1)) * W, ys = (v) => H - ((v - pmin) / (pmax - pmin || 1)) * H;
    const zeroY = ys(0), spotX = ((g.spot - prof[0].spot) / (prof[prof.length - 1].spot - prof[0].spot)) * W;
    const svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;background:#050505">
      <line x1="0" x2="${W}" y1="${zeroY}" y2="${zeroY}" stroke="#444" stroke-dasharray="3 3"/>
      <polyline fill="none" stroke="#ffa028" stroke-width="2" points="${prof.map((p, i) => `${xs(i)},${ys(p.gex)}`).join(" ")}"/>
      <line x1="${spotX}" x2="${spotX}" y1="0" y2="${H}" stroke="#5aa9ff"/><text x="${spotX + 4}" y="12" fill="#5aa9ff" font-size="10">price</text></svg>`;
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s12" style="--i:0"><div class="bb-ph"><span class="bb-n">0</span>GAMMA LEVELS · ${esc(s.symbol)}<span class="sp"></span><span class="meta">${g.expirations} expiries · ${g.n_options} options with open interest</span></div>
        <div class="bb-pb bb-pad"><div class="why-kv" style="grid-template-columns:repeat(5,1fr)">
          <div><span>PRICE</span><b>${m(g.spot)}</b></div>
          <div><span>CALL WALL</span><b class="up">${m(g.call_wall)}</b></div>
          <div><span>PUT WALL</span><b class="dn">${m(g.put_wall)}</b></div>
          <div><span>ZERO GAMMA</span><b>${m(g.zero_gamma)}</b></div>
          <div><span>NET GEX (1% MOVE)</span><b class="${g.net_gex >= 0 ? "up" : "dn"}">${big(g.net_gex)} · ${esc(g.regime)}</b></div></div>
          <div class="why-note" style="margin-top:8px">${esc(g.read)}</div></div></div>
      <div class="bb-panel s6 h560" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>TOP 25 STRIKES BY HEDGING CONCENTRATION (±8%)<span class="sp"></span><span class="meta"><span style="color:#ff4a4a">■ puts</span> · <span style="color:#2be07e">■ calls</span></span></div><div class="bb-pb bb-pad">${bars}</div></div>
      <div class="bb-panel s6 h560" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>TOTAL GEX IF PRICE MOVES ±15%</div><div class="bb-pb bb-pad">${svg}
        <div class="why-note">Where the orange line crosses the dashed zero line is the zero-gamma level. Right of it (positive) dealers dampen moves; left of it (negative) they amplify them.</div>
        <div class="why-note">${esc(g.note)}</div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>3D GAMMA SURFACE · STRIKE × EXPIRATION<span class="sp"></span><span class="meta">drag to rotate · scroll to zoom · hover for values</span></div>
        <div class="bb-pb"><div id="gex-3d" style="height:680px"><div class="bb-empty"><b>LOADING 3D SURFACE…</b>Reading every expiry up to 120 days.</div></div></div></div></div>`;
    gex3d(s.symbol, g.spot, tok);
  }

  function loadPlotly() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (!window.__plotlyP) {
      window.__plotlyP = new Promise((res, rej) => {
        const sc = document.createElement("script");
        sc.src = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js";
        sc.onload = () => res(window.Plotly);
        sc.onerror = () => { window.__plotlyP = null; rej(new Error("could not load the 3D chart library")); };
        document.head.appendChild(sc);
      });
    }
    return window.__plotlyP;
  }

  async function gex3d(symbol, spot, tok) {
    const el = $("#gex-3d");
    try {
      const d = await getData(`/invest/gamma/surface?symbol=${q(symbol)}`);
      if (tok !== sx.tok || !el || !document.body.contains(el)) return;
      await renderSurface(el, d, spot);
    } catch (e) { if (el) el.innerHTML = NX.err(e); }
  }

  // shared by the GEX page and the chart's "gamma 3D" box tool (gp.js calls window.NXGEX3D)
  window.NXGEX3D = async (el, symbol, opts = {}) => {
    try {
      const qs = opts.lo !== undefined && opts.hi !== undefined ? `&lo=${opts.lo.toFixed(2)}&hi=${opts.hi.toFixed(2)}` : "";
      const d = await getData(`/invest/gamma/surface?symbol=${q(symbol)}${qs}`);
      if (!document.body.contains(el)) return d;
      await renderSurface(el, d, d.spot);
      return d;
    } catch (e) { el.innerHTML = NX.err(e); return null; }
  };

  // Rich 3D gamma surface: NET / CALLS / PUTS, sqrt or linear scale, optional smoothing, floor heatmap "shadow", price plane, wall lines,
  // labelled peaks, real days-to-expiry spacing, slow auto-rotate (stops as soon as the user drags) and preset views.
  async function renderSurface(el, d, spot) {
    const P = await loadPlotly();
    if (!d.ok) { el.innerHTML = `<div class="bb-empty"><b>NO SURFACE</b>${esc(d.note || "")}</div>`; return; }
    const st = { mode: "net", scale: "sqrt", smooth: false, rotate: true, view: "3d" };
    const lv = await getData(`/invest/gamma?symbol=${q(d.symbol)}`).catch(() => null);
    el.innerHTML = `<div class="g3-bar" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:6px 8px;background:#070b13;border-bottom:1px solid #1b2230;font:600 11px JetBrains Mono,Consolas,monospace">
        ${[["mode", [["net", "NET"], ["calls", "CALLS"], ["puts", "PUTS"]]], ["scale", [["sqrt", "√ SCALE"], ["lin", "LINEAR"]]], ["view", [["3d", "3D"], ["top", "TOP"], ["side", "SIDE"]]]]
          .map(([k, opts]) => `<span style="display:inline-flex;gap:2px">${opts.map(([v, l]) => `<button class="bb-chip ${st[k] === v ? "on" : ""}" data-k="${k}" data-v="${v}">${l}</button>`).join("")}</span>`).join('<span style="width:8px"></span>')}
        <button class="bb-chip ${st.smooth ? "on" : ""}" data-t="smooth">SMOOTH</button><button class="bb-chip ${st.rotate ? "on" : ""}" data-t="rotate">⟳ ROTATE</button>
        <span style="flex:1"></span><span class="g3-info" style="color:#8b93a3;font-weight:400"></span></div>
      <div class="g3-plot" style="height:calc(100% - 38px);min-height:420px"></div>`;
    const plot = el.querySelector(".g3-plot");
    const nz = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nz")) || 1;
    if (nz !== 1) {  // body is zoomed on wide screens; WebGL hover/drag maths need real pixels, so cancel the zoom on the plot host
      plot.style.zoom = String(1 / nz);
      plot.style.height = `${Math.max(420, el.clientHeight - 38) * nz}px`;
    }
    const money = (v) => (v < 0 ? "-$" : "$") + (Math.abs(v) >= 1e9 ? (Math.abs(v) / 1e9).toFixed(2) + "B" : Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(1) + "M" : (Math.abs(v) / 1e3).toFixed(0) + "K");
    const ys = d.dte && d.dte.length === d.expirations.length ? d.dte : d.expirations.map((_, i) => i);
    const smooth = (Z) => Z.map((row, i) => row.map((v, j) => {  // light 3x3 blur for display only; hover always shows the raw dollars
      let s = 0, w = 0;
      for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) { const r = Z[i + a], x = r && r[j + b]; if (x !== undefined) { const k = (a === 0 ? 2 : 1) * (b === 0 ? 2 : 1); s += x * k; w += k; } }
      return s / w;
    }));
    const CS = [[0, "#3a0ca3"], [0.18, "#2d5bff"], [0.36, "#00c8ff"], [0.5, "#20f59a"], [0.64, "#d8ff3a"], [0.8, "#ffae1a"], [1, "#ff2a2a"]];
    let timer = null, angle = -2.4, userMoved = false;

    // Catmull-Rom resample along strikes (and expiries) when the grid is sparse, so a narrow box does not draw flat triangles.
    // Display only: every hover reads the raw value of the nearest real strike / expiry.
    const cr = (p0, p1, p2, p3, t) => 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t);
    const resample = (xs, n) => {
      if (xs.length >= n || xs.length < 3) return { pos: xs.map((_, i) => i) };
      const pos = [];
      for (let k = 0; k < n; k++) pos.push((k * (xs.length - 1)) / (n - 1));
      return { pos };
    };
    const at = (arr, f) => { const i = Math.min(arr.length - 2, Math.floor(f)), t = f - i; const g = (j) => arr[Math.max(0, Math.min(arr.length - 1, j))]; return cr(g(i - 1), g(i), g(i + 1), g(i + 2), t); };
    const lerp = (arr, f) => { const i = Math.min(arr.length - 2, Math.floor(f)), t = f - i; return arr[i] + (arr[i + 1] - arr[i]) * t; };
    const RX = resample(d.strikes, 48), RY = resample(ys, Math.min(30, ys.length * 3));
    const gx = RX.pos.map((f) => lerp(d.strikes, f)), gy = RY.pos.map((f) => (ys.length > 1 ? lerp(ys, f) : ys[0]));
    const interp2 = (Z) => RY.pos.map((fy) => { const rows = Z.map((r) => r); const colVals = (c) => rows.map((r) => r[c]); return RX.pos.map((fx) => {
      const byRow = rows.map((r) => (r.length > 2 ? at(r, fx) : lerp(r, fx)));
      return rows.length > 2 ? at(byRow, fy) : rows.length === 2 ? lerp(byRow, fy) : byRow[0]; }); });
    const near = (f) => Math.round(f);

    const draw = () => {
      const raw = st.mode === "calls" ? d.zc || d.z : st.mode === "puts" ? d.zp || d.z : d.z;
      const tr = st.scale === "sqrt" ? (v) => Math.sign(v) * Math.sqrt(Math.abs(v)) : (v) => v;
      const inv = st.scale === "sqrt" ? (t) => Math.sign(t) * t * t : (t) => t;
      const base = raw.map((r) => r.map(tr));
      const Z0 = st.smooth ? smooth(base) : base;
      const Z = interp2(Z0);
      const zmax = Math.max(...Z.flat().map(Math.abs)) || 1;
      const ticks = [-1, -0.5, 0, 0.5, 1].map((f) => f * zmax);
      const ttext = ticks.map((t) => money(inv(t)));
      const hoverRaw = raw.map((row, i) => row.map((v, j) => `<b>Strike ${d.bin_width ? (d.strikes[j] - d.bin_width / 2).toFixed(2) + "–" + (d.strikes[j] + d.bin_width / 2).toFixed(2) : d.strikes[j]}</b><br>Expiry ${d.expirations[i]} (${Math.round(ys[i])}d)<br>${st.mode.toUpperCase()} GEX <b>${money(v)}</b> per 1%`));
      const hover = RY.pos.map((fy) => RX.pos.map((fx) => hoverRaw[near(fy)][near(fx)]));
      const floorZ = -zmax * 1.08;
      const traces = [
        { type: "surface", x: gx, y: gy, z: Z, colorscale: CS, cmin: -zmax, cmax: zmax, text: hover, hoverinfo: "text", opacity: 1,
          lighting: { ambient: 0.55, diffuse: 0.85, specular: 0.35, roughness: 0.45, fresnel: 0.2 }, lightposition: { x: 100, y: 200, z: 1000 },
          contours: { x: { highlight: false }, y: { highlight: false }, z: { highlight: false } },
          hoverlabel: { bgcolor: "#0b111c", bordercolor: "#ffa028", font: { color: "#ffffff", family: "JetBrains Mono, Consolas, monospace", size: 12 } },
          colorbar: { title: { text: `${st.mode} GEX`, font: { color: "#8b93a3", size: 10 } }, tickvals: ticks, ticktext: ttext, tickfont: { color: "#8b93a3", size: 10 }, len: 0.62, thickness: 10, x: 1.0, outlinewidth: 0 } },
        { type: "surface", x: gx, y: gy, z: Z.map((r) => r.map(() => floorZ)), surfacecolor: Z, colorscale: CS, cmin: -zmax, cmax: zmax, showscale: false, opacity: 0.55, hoverinfo: "none", contours: { x: { highlight: false }, y: { highlight: false }, z: { highlight: false } } },
      ];
      const y0 = ys[0], y1 = ys[ys.length - 1];
      if (spot >= d.strikes[0] && spot <= d.strikes[d.strikes.length - 1]) {
        traces.push({ type: "surface", x: [[spot, spot], [spot, spot]], y: [[y0, y1], [y0, y1]], z: [[floorZ, floorZ], [zmax, zmax]], showscale: false, opacity: 0.12, colorscale: [[0, "#ffffff"], [1, "#ffffff"]], hoverinfo: "none", contours: { x: { highlight: false }, y: { highlight: false }, z: { highlight: false } } });
        traces.push({ type: "scatter3d", mode: "lines+text", x: [spot, spot], y: [y0, y1], z: [floorZ, floorZ], line: { color: "#ffffff", width: 5 }, text: ["", `PRICE ${spot.toFixed(2)}`], textfont: { color: "#ffffff", size: 11 }, hoverinfo: "none" });
      }
      if (lv && lv.ok) {
        [["CALL WALL", lv.call_wall, "#2be07e"], ["ZERO γ", lv.zero_gamma, "#ffa028"], ["PUT WALL", lv.put_wall, "#ff4a4a"]].forEach(([name, k, c]) => {
          if (!k || k < d.strikes[0] || k > d.strikes[d.strikes.length - 1]) return;
          traces.push({ type: "scatter3d", mode: "lines+text", x: [k, k], y: [y0, y1], z: [floorZ, floorZ], line: { color: c, width: 6, dash: name.startsWith("ZERO") ? "dash" : "solid" }, text: [`${name} ${k}`, ""], textfont: { color: c, size: 11 }, textposition: "top center", hoverinfo: "none" });
        });
      }
      // label the three biggest peaks (largest |value|)
      const pts = [];
      raw.forEach((row, i) => row.forEach((v, j) => pts.push({ v, i, j })));
      pts.sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
      const top = [];  // the biggest peaks that are at least 4 strike bins (or 2 expiries) apart, so the labels never sit on top of each other
      for (const p of pts) {
        if (top.length >= 3) break;
        if (top.every((t) => Math.abs(t.j - p.j) >= 4 || Math.abs(t.i - p.i) >= 2 && Math.abs(t.j - p.j) >= 2)) top.push(p);
      }
      const peakZ = (p) => Math.max(Z0[p.i][p.j], 0) + zmax * 0.04;
      traces.push({ type: "scatter3d", mode: "markers", x: top.map((p) => d.strikes[p.j]), y: top.map((p) => ys[p.i]), z: top.map(peakZ),
        marker: { size: 4, color: "#ffffff", line: { color: "#000", width: 1 } }, hoverinfo: "none" });
      // labels as scene annotations: always drawn on top of the mesh (plain 3D text gets hidden behind nearer peaks)
      const peakNotes = top.map((p, k) => ({ x: d.strikes[p.j], y: ys[p.i], z: peakZ(p), text: `<b>${money(p.v)}</b> · ${d.strikes[p.j]} · ${d.expirations[p.i].slice(5)}`,
        showarrow: true, arrowcolor: "#ffffff", arrowwidth: 1, arrowhead: 0, ax: [-60, 60, 0][k % 3], ay: [-45, -45, -70][k % 3],
        font: { color: "#ffffff", size: 11, family: "JetBrains Mono, Consolas, monospace" }, bgcolor: "rgba(5,8,15,0.85)", bordercolor: "#ffa028", borderwidth: 1, borderpad: 3 }));
      const ax = (title, extra = {}) => ({ title: { text: title, font: { color: "#c9ced8", size: 11 } }, tickfont: { color: "#7d8594", size: 10 }, gridcolor: "rgba(120,140,170,0.14)", zerolinecolor: "rgba(255,255,255,0.25)",
        backgroundcolor: "#060a12", showbackground: true, showspikes: false, ...extra });
      const step = Math.max(1, Math.ceil(ys.length / 7));
      const eye = st.view === "top" ? { x: 0, y: 0, z: 2.7 } : st.view === "side" ? { x: 0, y: -2.6, z: 0.15 } : { x: 2.1 * Math.cos(angle), y: 2.1 * Math.sin(angle), z: 0.9 };
      const up = st.view === "top" ? { x: 0, y: 1, z: 0 } : { x: 0, y: 0, z: 1 };
      const layout = {
        paper_bgcolor: "#05080f", plot_bgcolor: "#05080f", margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false, font: { family: "JetBrains Mono, Consolas, monospace" },
        scene: { xaxis: ax("Strike"), yaxis: ax("Days to expiry", { tickvals: ys.filter((_, i) => i % step === 0), ticktext: d.expirations.filter((_, i) => i % step === 0).map((x) => x.slice(5)) }),
                 zaxis: ax(`${st.mode} gamma ($/1%)`, { range: [floorZ, zmax * 1.2], tickvals: ticks, ticktext: ttext }),
                 annotations: peakNotes, camera: { eye, up, center: { x: 0, y: 0, z: st.view === "top" ? 0 : -0.12 }, projection: { type: st.view === "top" ? "orthographic" : "perspective" } }, aspectmode: "manual", aspectratio: { x: 1.7, y: 1.25, z: 0.85 }, dragmode: "turntable" },
      };
      P.react(plot, traces, layout, { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["toImage", "resetCameraLastSave3d"] });
      const big = top[0];
      el.querySelector(".g3-info").innerHTML = `${d.expirations.length} expiries · ${d.strikes.length} strikes${big ? ` · biggest: <b style="color:#fff">${money(big.v)}</b> at ${d.strikes[big.j]} (${d.expirations[big.i]})` : ""}`;
    };

    const spin = () => {
      clearInterval(timer);
      if (!st.rotate || st.view !== "3d") return;
      timer = setInterval(() => {
        if (!document.body.contains(plot)) { clearInterval(timer); return; }
        if (userMoved) return;
        angle += 0.006;
        P.relayout(plot, { "scene.camera.eye": { x: 2.1 * Math.cos(angle), y: 2.1 * Math.sin(angle), z: 0.9 } });
      }, 60);
    };
    draw();
    spin();
    plot.addEventListener("mousedown", () => { userMoved = true; st.rotate = false; clearInterval(timer); el.querySelector('[data-t="rotate"]').classList.remove("on"); });
    plot.addEventListener("wheel", () => { userMoved = true; }, { passive: true });
    el.querySelector(".g3-bar").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.k) { st[b.dataset.k] = b.dataset.v; el.querySelectorAll(`[data-k="${b.dataset.k}"]`).forEach((x) => x.classList.toggle("on", x === b)); if (b.dataset.k === "view") userMoved = false; }
      if (b.dataset.t) { st[b.dataset.t] = !st[b.dataset.t]; b.classList.toggle("on", st[b.dataset.t]); if (b.dataset.t === "rotate") userMoved = false; }
      draw();
      spin();
    });
  }

  // ------------------------------------------------------------------ QNT: quant lab (pandas / NumPy / SciPy / statsmodels / yfinance data / plots)
  const QNT_LIBS = [["1_numpy", "NumPy"], ["2_matplotlib", "matplotlib"], ["3_pandas", "pandas"], ["4_scipy", "SciPy"], ["5_statsmodels", "statsmodels"], ["6_yfinance", "yfinance"]];
  async function funcQNT(body, tok) {
    const s = sx.sec;
    const st = (sx.qnt = sx.qnt || { bench: "SPY", pair: "", years: 3 });
    body.innerHTML = `<div class="bb-empty"><b>RUNNING QUANT LAB…</b>Downloading ${st.years} years of prices and fitting the models.</div>`;
    const qs = `symbol=${q(s.symbol)}&bench=${q(st.bench)}&years=${st.years}${st.pair ? "&pair=" + q(st.pair) : ""}`;
    const [d, P] = await Promise.all([getData(`/invest/quant?${qs}`), loadPlotly().catch(() => null)]);
    if (tok !== sx.tok) return;
    if (!d.ok) { body.innerHTML = `<div class="bb-empty"><b>NOT ENOUGH DATA</b>${esc(d.note || "")}</div>`; return; }
    const pc = (v, dp = 1) => (isNum(v) ? (v * 100).toFixed(dp) + "%" : "—");
    const nm = (v, dp = 2) => (isNum(v) ? Number(v).toFixed(dp) : "—");
    const D = d.dist, R = d.regression || {}, T = d.timeseries, M = d.montecarlo, PR = d.pairs;
    const kv = (rows) => `<table class="bb-tbl"><tbody>${rows.map(([k, v, c]) => `<tr><td class="dim">${k}</td><td style="text-align:right${c ? ";color:" + c : ""}"><b>${v}</b></td></tr>`).join("")}</tbody></table>`;
    const verdict = (ok, yes, no) => `<span style="color:${ok ? "#2be07e" : "#ffa028"}">${ok ? yes : no}</span>`;
    const panel = (n, title, meta, inner, cls = "s6 h420") => `<div class="bb-panel ${cls}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${meta}</span></div><div class="bb-pb bb-pad">${inner}</div></div>`;
    body.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s12" style="--i:0"><div class="bb-ph"><span class="bb-n">0</span>QUANT LAB · ${esc(s.symbol)}<span class="sp"></span><span class="meta">${d.n_days} daily returns · ${esc(d.start)} → ${esc(d.end)} · pandas ${esc(d.libraries.pandas)} · NumPy ${esc(d.libraries.numpy)} · SciPy ${esc(d.libraries.scipy)} · statsmodels ${esc(d.libraries.statsmodels)}</span></div>
        <div class="bb-pb bb-pad" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;font:12px JetBrains Mono,Consolas,monospace">
          <span class="dim">BENCHMARK</span><input id="qn-b" class="ctl-in" value="${esc(st.bench)}" style="width:80px">
          <span class="dim">PAIR (cointegration)</span><input id="qn-p" class="ctl-in" value="${esc(st.pair)}" placeholder="e.g. GOOGL" style="width:100px">
          <span class="dim">YEARS</span>${[1, 3, 5, 10].map((y) => `<button class="bb-chip ${st.years === y ? "on" : ""}" data-qy="${y}">${y}Y</button>`).join("")}
          <button class="bb-chip on" id="qn-go">RUN</button></div></div>
      ${panel(1, `REGRESSION VS ${esc(R.bench || st.bench)} · statsmodels OLS`, R.n ? `${R.n} days` : "", R.error ? `<div class="dim">${esc(R.error)}</div>` : `<div style="display:flex;gap:10px"><div id="qn-reg" style="flex:1.3;height:340px"></div><div style="flex:1">${kv([
          ["Beta", `${nm(R.beta)} <span class="dim">[${nm(R.beta_ci[0])}–${nm(R.beta_ci[1])}]</span>`], ["Beta t-stat", nm(R.beta_t)], ["Alpha (annual)", pc(R.alpha_annual), R.alpha_annual > 0 ? "#2be07e" : "#ff4a4a"],
          ["Alpha p-value", `${nm(R.alpha_p, 3)} ${verdict(R.alpha_p < 0.05, "significant", "not significant")}`], ["R²", nm(R.r2)], ["Correlation", nm(R.corr)], ["Idiosyncratic vol", pc(R.resid_vol_annual)]])}
          <div class="why-note">Beta = how much the stock moves per 1% in ${esc(R.bench)}. Alpha is only meaningful when its p-value is below 0.05.</div></div></div>`)}
      ${panel(2, "RETURN DISTRIBUTION · SciPy", D.normal ? "normal" : "fat tails", `<div style="display:flex;gap:10px"><div id="qn-dist" style="flex:1.3;height:340px"></div><div style="flex:1">${kv([
          ["Annual vol", pc(D.vol_annual)], ["Annual return (hist.)", pc(D.ret_annual), D.ret_annual > 0 ? "#2be07e" : "#ff4a4a"], ["Skew", nm(D.skew)], ["Excess kurtosis", nm(D.excess_kurtosis)],
          ["Jarque-Bera p", `${nm(D.jb_p, 4)} ${verdict(D.normal, "normal", "NOT normal")}`], ["Student-t df", nm(D.t_df)], ["VaR 95% (1 day, hist.)", pc(D.var95_hist, 2), "#ff4a4a"],
          ["VaR 99% normal vs t", `${pc(D.var99_normal, 2)} vs ${pc(D.var99_t, 2)}`, "#ff4a4a"], ["CVaR 95%", pc(D.cvar95, 2), "#ff4a4a"], ["Worst day", `${pc(D.worst_day)} <span class="dim">${esc(D.worst_date)}</span>`, "#ff4a4a"]])}
          <div class="why-note">When the t-fit (orange) beats the normal (blue) in the tails, normal-based VaR understates risk.</div></div></div>`)}
      ${panel(3, "MONTE CARLO · NumPy", `${M.sims} paths · ${M.horizon_days} days`, `<div style="display:flex;gap:10px"><div id="qn-mc" style="flex:1.3;height:325px"></div><div style="flex:1">${kv([
          ["Median in 1y", `$${nm(M.end_p50)}`], ["5% – 95% range", `$${nm(M.end_p5)} – $${nm(M.end_p95)}`], ["P(up)", pc(M.prob_up, 0)], ["P(+20%) / P(−20%)", `${pc(M.prob_up20, 0)} / ${pc(M.prob_dn20, 0)}`],
          ["Past-trend median", `$${nm(M.hist_drift_median)} <span class="dim">(${pc(M.hist_drift_annual, 0)}/yr)</span>`]])}<div class="why-note">${esc(M.note)}</div></div></div>`, "s6")}
      ${panel(4, "TIME SERIES · statsmodels", "ADF + ACF", `${kv([["ADF on log price (p)", `${nm(T.adf_price_p, 3)} ${verdict(T.price_stationary, "stationary", "random walk")}`],
          ["ADF on returns (p)", `${nm(T.adf_return_p, 3)} ${verdict(T.returns_stationary, "stationary", "not stationary")}`],
          ["Significant autocorrelation", T.significant_lags.length ? "lags " + T.significant_lags.join(", ") : "none (no easy momentum / reversal)"]])}<div id="qn-acf" style="height:220px"></div>
          <div class="why-note">Bars outside the dashed band (±${nm(T.acf_band, 3)}) mean yesterday's return says something about today's.</div>`, "s6")}
      ${PR ? panel(5, `PAIRS · ${esc(s.symbol)} / ${esc(PR.pair)} · Engle-Granger`, PR.error ? "error" : (PR.cointegrated ? "cointegrated" : "not cointegrated"), PR.error ? `<div class="dim">${esc(PR.error)}</div>` : `${kv([
          ["Cointegration p-value", `${nm(PR.coint_p, 3)} ${verdict(PR.cointegrated, "cointegrated", "not cointegrated")}`], ["Hedge ratio", nm(PR.hedge_ratio, 3)], ["Spread z-score now", nm(PR.z_now), Math.abs(PR.z_now) > 2 ? "#ffa028" : ""],
          ["Half-life of the spread", isNum(PR.half_life_days) ? nm(PR.half_life_days, 1) + " days" : "no mean reversion"]])}<div id="qn-z" style="height:220px"></div>
          <div class="why-note">A pair trade only makes sense if the two are cointegrated (p &lt; 0.05); |z| &gt; 2 is the usual entry zone.</div>`, "s12") : ""}
      ${panel(6, "6 PYTHON LIBRARIES EVERY QUANT SHOULD KNOW", "click to enlarge", `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">${QNT_LIBS.map(([f, n]) => `<a href="img/quant/${f}.webp" target="_blank" rel="noopener noreferrer" title="${n}" style="display:block;border:1px solid #262b36;border-radius:6px;overflow:hidden;background:#0b111c"><img src="img/quant/${f}.webp" alt="${n}" style="width:100%;display:block" loading="lazy"><div style="padding:4px 6px;font:600 11px JetBrains Mono,monospace;color:#ffa028">${n}</div></a>`).join("")}</div>`, "s12")}
    </div>`;
    const go = () => { st.bench = ($("#qn-b").value || "SPY").trim().toUpperCase(); st.pair = ($("#qn-p").value || "").trim().toUpperCase(); funcQNT(body, sx.tok); };
    $("#qn-go").onclick = go;
    ["#qn-b", "#qn-p"].forEach((id) => $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }));
    body.querySelectorAll("[data-qy]").forEach((b) => (b.onclick = () => { st.years = +b.dataset.qy; go(); }));
    if (!P) return;
    const base = (t) => ({ paper_bgcolor: "#05080f", plot_bgcolor: "#05080f", margin: { l: 46, r: 10, t: 22, b: 34 }, font: { color: "#9aa1ad", size: 10, family: "JetBrains Mono, Consolas, monospace" },
      title: { text: t, font: { size: 11, color: "#cfd3da" }, x: 0.01 }, xaxis: { gridcolor: "#151a24", zerolinecolor: "#333" }, yaxis: { gridcolor: "#151a24", zerolinecolor: "#333" }, showlegend: false });
    const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["toImage", "lasso2d", "select2d"] };
    const nz = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nz")) || 1;
    ["#qn-reg", "#qn-dist", "#qn-mc", "#qn-acf", "#qn-z"].forEach((id) => { const el = $(id); if (el && nz !== 1) { el.style.zoom = String(1 / nz); el.style.height = `${parseFloat(el.style.height) * nz}px`; } });
    if (R.points) {
      const xs = R.points.x, lo = Math.min(...xs), hi = Math.max(...xs);
      P.newPlot($("#qn-reg"), [{ type: "scattergl", mode: "markers", x: xs, y: R.points.y, marker: { size: 4, color: "rgba(90,169,255,0.55)" }, hovertemplate: `${esc(R.bench)} %{x:.2%}<br>${esc(s.symbol)} %{y:.2%}<extra></extra>` },
        { type: "scatter", mode: "lines", x: [lo, hi], y: [R.alpha_daily + R.beta * lo, R.alpha_daily + R.beta * hi], line: { color: "#ffa028", width: 2 }, hoverinfo: "skip" }],
        { ...base(`y = ${nm(R.alpha_daily, 5)} + ${nm(R.beta)}·x   R² ${nm(R.r2)}`), xaxis: { ...base("").xaxis, tickformat: ".0%", title: { text: R.bench } }, yaxis: { ...base("").yaxis, tickformat: ".0%", title: { text: s.symbol } } }, cfg);
    }
    P.newPlot($("#qn-dist"), [{ type: "bar", x: D.hist.x, y: D.hist.y, marker: { color: "rgba(43,224,126,0.55)" }, hovertemplate: "%{x:.2%}: %{y} days<extra></extra>" },
      { type: "scatter", mode: "lines", x: D.pdf_normal.x, y: D.pdf_normal.y, line: { color: "#5aa9ff", width: 2 }, name: "normal", hoverinfo: "skip" },
      { type: "scatter", mode: "lines", x: D.pdf_t.x, y: D.pdf_t.y, line: { color: "#ffa028", width: 2 }, name: "Student-t", hoverinfo: "skip" }],
      { ...base("Daily returns · normal (blue) vs Student-t (orange)"), bargap: 0.02, xaxis: { ...base("").xaxis, tickformat: ".0%" } }, cfg);
    const days = M.bands.p50.map((_, i) => i * 5);
    const band = (lo, hi, c) => [{ x: days, y: M.bands[hi], mode: "lines", line: { width: 0 }, hoverinfo: "skip" }, { x: days, y: M.bands[lo], mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: c, hoverinfo: "skip" }];
    P.newPlot($("#qn-mc"), [...band("p5", "p95", "rgba(90,169,255,0.15)"), ...band("p25", "p75", "rgba(90,169,255,0.3)"),
      { x: days, y: M.bands.p50, mode: "lines", line: { color: "#ffa028", width: 2 }, hovertemplate: "day %{x}: median $%{y:.2f}<extra></extra>" }],
      { ...base("Price paths · 5–95% and 25–75% bands, median"), xaxis: { ...base("").xaxis, title: { text: "trading days ahead" } }, yaxis: { ...base("").yaxis, tickprefix: "$" } }, cfg);
    P.newPlot($("#qn-acf"), [{ type: "bar", x: T.acf.map((_, i) => i + 1), y: T.acf, marker: { color: T.acf.map((v) => (Math.abs(v) > T.acf_band ? "#ffa028" : "#3b4558")) }, hovertemplate: "lag %{x}: %{y:.3f}<extra></extra>" }],
      { ...base("Autocorrelation of daily returns"), shapes: [T.acf_band, -T.acf_band].map((y) => ({ type: "line", x0: 0.5, x1: 10.5, y0: y, y1: y, line: { color: "#ff4a4a", dash: "dash", width: 1 } })) }, cfg);
    if (PR && PR.z) P.newPlot($("#qn-z"), [{ x: PR.z.t, y: PR.z.v, mode: "lines", line: { color: "#5aa9ff", width: 1.5 }, hovertemplate: "%{x}: z %{y:.2f}<extra></extra>" }],
      { ...base("Spread z-score"), shapes: [2, 0, -2].map((y) => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color: y ? "#ffa028" : "#555", dash: "dash", width: 1 } })) }, cfg);
  }

  const FUNCS = {
    WHY: funcWHY,
    DES: async (body, tok) => { const s = sx.sec, t = (s.type || "").toUpperCase(); if (t === "CRYPTOCURRENCY") return desCrypto(body, tok, s); if (t === "EQUITY" || t === "ETF" || t === "MUTUALFUND") return desEquity(body, tok, s); return desOther(body, tok, s); },
    DEEP: (body, tok) => NX.deepDive(body, sx.sec, tok, () => tok === sx.tok), GP: funcGP, FA: funcFA, N: funcN, ANR: funcANR, HDS: funcHDS, FIL: funcFIL, DVD: funcDVD, OMON: funcOMON, GEX: funcGEX, QNT: funcQNT, RV: funcRV, DATA: funcDATA,
  };
})();
