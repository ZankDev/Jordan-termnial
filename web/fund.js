/* FUND: manage a portfolio like a fund. Book (positions, trades, cash), NAV, risk (VaR, beta, drawdown, correlation), limits with warnings,
   a WHY verdict on every holding, and an opportunity screen that ranks stocks with the same WHY engine. Depends on window.NX (invest.js) and api(). */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, pct, big, signed } = NX;
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n) => (isNum(n) ? "$" + num(n, 0) : "—");
  let built = false, timer = 0, scanTimer = 0, scanSort = "score", lastScan = null, state = { ov: null };

  const post = (path, body) => fetch("/api" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
    .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.status); return j; });

  function build() {
    $("#view-fund").innerHTML = `
      <div class="bb-kpis" id="fd-kpis"></div>
      <div class="bb-grid">
        <div class="bb-panel s12" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>HOLDINGS<span class="sp"></span><span class="meta" id="fd-meta"></span></div><div class="bb-pb" id="fd-pos">${NX.load()}</div></div>
        <div class="bb-panel s6 h340" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>RISK &amp; LIMITS</div><div class="bb-pb bb-pad" id="fd-risk">${NX.load()}</div></div>
        <div class="bb-panel s6 h340" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>NAV HISTORY &amp; SECTOR EXPOSURE</div><div class="bb-pb bb-pad"><div id="fd-nav" style="height:150px"></div><div id="fd-sect" style="margin-top:8px"></div></div></div>
        <div class="bb-panel s4 h340" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>RECORD A TRADE</div><div class="bb-pb bb-pad" id="fd-form"></div></div>
        <div class="bb-panel s8 h340" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>TRADE LOG</div><div class="bb-pb" id="fd-trades"></div></div>
        <div class="bb-panel s12" style="--i:6"><div class="bb-ph"><span class="bb-n">6</span>OPPORTUNITY SCREEN · RANKED BY THE WHY ENGINE<span class="sp"></span><span class="meta" id="fd-scan-meta"></span>
          <div class="bb-chips" id="fd-scan-chips"><button class="bb-chip" data-u="mega">SCAN TOP US STOCKS</button><button class="bb-chip" data-u="watch">SCAN MY WATCHLIST</button><button class="bb-chip" data-u="book">SCAN MY BOOK</button></div>
          <div class="bb-chips" id="fd-sort"><button class="bb-chip on" data-s="score">SORT: SCORE</button><button class="bb-chip" data-s="rr">REWARD / RISK</button><button class="bb-chip" data-s="up">UPSIDE</button></div></div>
          <div class="bb-pb" id="fd-scan"><div class="bb-empty"><b>NOT SCANNED YET</b>Pick a universe. Each stock is scored on quality, growth, value, trend, analysts and news; the best set-ups float to the top with their reasons.</div></div></div>
      </div>`;
    $("#fd-form").innerHTML = `<div class="fd-f">
      <input id="fd-sym" class="ctl-in" placeholder="Symbol, e.g. AAPL" />
      <select id="fd-side" class="ctl-in"><option value="buy">BUY</option><option value="sell">SELL</option></select>
      <input id="fd-qty" class="ctl-in" placeholder="Quantity" inputmode="decimal" />
      <input id="fd-px" class="ctl-in" placeholder="Price" inputmode="decimal" />
      <input id="fd-fee" class="ctl-in" placeholder="Fees (optional)" inputmode="decimal" />
      <input id="fd-thesis" class="ctl-in" placeholder="Thesis in one line (why you own it)" />
      <button class="bb-chip on" id="fd-go">RECORD TRADE</button>
      <div class="rp-msg" id="fd-msg"></div>
      <div class="sub-h">CASH</div>
      <div style="display:flex;gap:6px"><input id="fd-cash" class="ctl-in" placeholder="Set cash balance" inputmode="decimal" /><button class="bb-chip" id="fd-cash-go">SET</button></div>
      <div class="why-note" style="padding:6px 0">This is the fund's record and risk view. It does not place real orders.</div></div>`;
    $("#fd-go").addEventListener("click", async () => {
      const g = (id) => $(id).value.trim(), n = (id) => Number(g(id).replace(/[$,\s]/g, ""));
      try {
        const r = await post("/fund/trade", { symbol: g("#fd-sym"), side: $("#fd-side").value, qty: n("#fd-qty"), price: n("#fd-px"), fees: g("#fd-fee") ? n("#fd-fee") : 0, thesis: g("#fd-thesis") || null });
        $("#fd-msg").textContent = `${r.side.toUpperCase()} ${r.qty} ${r.symbol} @ ${r.price}${r.side === "sell" ? " · realised " + (r.realized >= 0 ? "+" : "") + r.realized : ""}`;
        ["#fd-sym", "#fd-qty", "#fd-px", "#fd-fee", "#fd-thesis"].forEach((i) => ($(i).value = ""));
        refresh();
      } catch (e) { $("#fd-msg").textContent = String(e.message || e); }
    });
    $("#fd-cash-go").addEventListener("click", async () => {
      try { await post("/fund/cash", { cash: Number($("#fd-cash").value.replace(/[$,\s]/g, "")) }); $("#fd-cash").value = ""; $("#fd-msg").textContent = "cash updated"; refresh(); } catch (e) { $("#fd-msg").textContent = String(e.message || e); }
    });
    document.querySelectorAll("#fd-scan-chips .bb-chip").forEach((b) => b.addEventListener("click", async () => {
      const u = b.dataset.u, syms = u === "watch" ? NX.getList(NX.WATCH_KEY).join(",") : "";
      if (u === "watch" && !syms) { $("#fd-scan").innerHTML = `<div class="bb-empty"><b>WATCHLIST IS EMPTY</b>Open a security and press WATCH to add it.</div>`; return; }
      try { await post(`/fund/screen/start?universe=${u}&symbols=${encodeURIComponent(syms)}`); pollScan(); } catch (e) { $("#fd-scan").innerHTML = NX.err(e); }
    }));
    document.querySelectorAll("#fd-sort .bb-chip").forEach((b) => b.addEventListener("click", () => { scanSort = b.dataset.s; document.querySelectorAll("#fd-sort .bb-chip").forEach((x) => x.classList.toggle("on", x === b)); if (lastScan) renderScan(lastScan); }));
    pollScan(true);
  }

  const stanceCls = (s) => ({ "BUY CANDIDATE": "up", "LEAN BUY / ACCUMULATE": "up", "LEAN AVOID": "dn", AVOID: "dn" }[s] || "dim");

  const sorters = { score: (a, b) => b.score - a.score, rr: (a, b) => (isNum(b.rr) ? b.rr : -9) - (isNum(a.rr) ? a.rr : -9), up: (a, b) => (isNum(b.upside_pct) ? b.upside_pct : -999) - (isNum(a.upside_pct) ? a.upside_pct : -999) };
  function renderScan(s) {
    lastScan = s;
    const rows = s.rows.slice().sort(sorters[scanSort]);
    $("#fd-scan-meta").textContent = s.running ? `scanning ${s.done}/${s.total}…` : `${rows.length} scored · ${s.universe}`;
    $("#fd-scan").innerHTML = rows.length ? `<table class="bb-tbl"><thead><tr><th>SYMBOL</th><th>VERDICT</th><th class="r">SCORE</th><th class="r">TO TARGET</th><th class="r">TO SUPPORT</th><th class="r">R/R</th><th class="r">RSI</th><th>WHY BUY</th><th>WHY NOT</th></tr></thead><tbody>${rows.map((r) => `<tr><td class="sym"><a href="#" data-sec="${esc(r.symbol)}">${esc(r.symbol)}</a> <span class="dim">${esc((r.name || "").slice(0, 22))}</span></td><td class="${stanceCls(r.stance)}">${esc(r.stance)}</td><td class="r ${dir(r.score)}"><b>${r.score > 0 ? "+" : ""}${r.score}</b></td><td class="r ${dir(r.upside_pct)}">${isNum(r.upside_pct) ? pct(r.upside_pct, true, 0) : "—"}</td><td class="r dn">${isNum(r.downside_pct) ? "-" + r.downside_pct + "%" : "—"}</td><td class="r">${isNum(r.rr) && r.rr > 0 ? r.rr + ":1" : "—"}</td><td class="r">${isNum(r.rsi) ? Math.round(r.rsi) : "—"}</td><td class="dim" style="font-size:11px">${esc(r.why_buy.join(" · "))}</td><td class="dim" style="font-size:11px">${esc(r.why_not.join(" · "))}</td></tr>`).join("")}</tbody></table>` : (s.running ? NX.load("SCANNING") : "");
  }

  async function pollScan(quiet) {
    clearTimeout(scanTimer);
    try {
      const s = await api("/fund/screen");
      if (!s.total && quiet) return;
      renderScan(s);
      if (s.running) scanTimer = setTimeout(pollScan, 3000);
    } catch (e) { /* the page keeps working */ }
  }

  function draw(o) {
    state.ov = o;
    const kp = (l, v, s, cls = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="s">${s || "&nbsp;"}</div></div>`;
    const r = o.risk || {};
    $("#fd-kpis").innerHTML = kp("NAV", money(o.nav), `cash ${money(o.cash)} · invested ${money(o.invested)}`)
      + kp("DAY P&L", (o.day_pnl >= 0 ? "+" : "") + money(o.day_pnl), "", dir(o.day_pnl))
      + kp("UNREALISED", (o.unrealized >= 0 ? "+" : "") + money(o.unrealized), `realised ${o.realized >= 0 ? "+" : ""}${money(o.realized)}`, dir(o.unrealized))
      + kp("1-DAY VAR 95%", isNum(r.var95_pct) ? r.var95_pct + "%" : "—", isNum(r.var95_usd) ? "≈ " + money(r.var95_usd) : "needs 60+ days of data")
      + kp("BETA vs SPY", isNum(r.beta) ? r.beta : "—", isNum(r.vol_ann_pct) ? "vol " + r.vol_ann_pct + "% / yr" : "")
      + kp("WARNINGS", String(o.warnings.filter((w) => w.level !== "info").length), o.warnings.length ? "see RISK & LIMITS" : "all within limits", o.warnings.some((w) => w.level === "high") ? "dn" : "");
    $("#fd-meta").textContent = `${o.positions.length} position${o.positions.length === 1 ? "" : "s"}`;
    $("#fd-pos").innerHTML = o.positions.length ? `<table class="bb-tbl"><thead><tr><th>SYMBOL</th><th class="r">QTY</th><th class="r">AVG COST</th><th class="r">PRICE</th><th class="r">VALUE</th><th class="r">WEIGHT</th><th class="r">DAY</th><th class="r">P&amp;L</th><th>SECTOR</th><th>VERDICT NOW</th><th>YOUR THESIS</th></tr></thead><tbody>${o.positions.map((p) => `<tr>
      <td class="sym"><a href="#" data-sec="${esc(p.symbol)}">${esc(p.symbol)}</a><div class="dim" style="font-size:10px">${esc((p.name || "").slice(0, 24))}</div></td><td class="r">${num(p.qty, p.qty % 1 ? 2 : 0)}</td><td class="r">${NX.px(p.avg_cost)}</td><td class="r">${NX.px(p.price)}</td><td class="r">${money(p.value)}</td>
      <td class="r ${p.weight > o.limits.max_position_pct ? "dn" : ""}">${num(p.weight, 1)}%</td><td class="r ${dir(p.day_pct)}">${isNum(p.day_pct) ? pct(p.day_pct) : "—"}</td><td class="r ${dir(p.pnl)}">${isNum(p.pnl) ? (p.pnl >= 0 ? "+" : "") + money(p.pnl) : "—"}<div style="font-size:10px">${isNum(p.pnl_pct) ? pct(p.pnl_pct, true, 1) : ""}</div></td>
      <td class="dim">${esc(p.sector)}</td><td>${p.verdict ? `<span class="${stanceCls(p.verdict.stance)}">${esc(p.verdict.stance)}</span> <b>${p.verdict.score > 0 ? "+" : ""}${p.verdict.score}</b><div class="dim" style="font-size:10px">${esc(p.verdict.top_buy || "")}${p.verdict.top_not ? " · risk: " + esc(p.verdict.top_not) : ""}</div>` : '<span class="dim">—</span>'}</td>
      <td class="dim" style="font-size:11px;max-width:200px">${esc(p.thesis || "")}</td></tr>`).join("")}</tbody></table>` : `<div class="bb-empty"><b>THE BOOK IS EMPTY</b>Set the cash balance, then record your first trade on the left. Every holding gets a live WHY verdict, risk numbers and limit checks.</div>`;
    const lim = o.limits;
    $("#fd-risk").innerHTML = `<table class="bb-tbl kv"><tbody>
      <tr><td class="k">1-day VaR 95% / 99%</td><td class="r">${isNum(r.var95_pct) ? r.var95_pct + "% / " + r.var99_pct + "%" : "—"}</td></tr>
      <tr><td class="k">Worst day / max drawdown (last year, at today's weights)</td><td class="r dn">${isNum(r.worst_day_pct) ? r.worst_day_pct + "% / " + r.max_dd_pct + "%" : "—"}</td></tr>
      <tr><td class="k">Most correlated pairs</td><td class="r">${(r.most_correlated || []).map((c) => `${esc(c.a)}–${esc(c.b)} ${c.corr}`).join("<br>") || "—"}</td></tr>
      <tr><td class="k">Limits (position / sector / VaR / cash / drawdown)</td><td class="r dim">${lim.max_position_pct}% / ${lim.max_sector_pct}% / ${lim.max_var95_pct}% / ${lim.min_cash_pct}% / ${lim.max_drawdown_pct}%</td></tr></tbody></table>
      <div class="sub-h">WARNINGS</div>${o.warnings.length ? `<ul class="why-watch">${o.warnings.map((w) => `<li class="${w.level === "high" ? "dn" : w.level === "med" ? "" : "dim"}">${esc(w.text)}</li>`).join("")}</ul>` : '<div class="why-empty">No limit is breached.</div>'}
      <div class="why-note" style="padding:6px 0">${esc(o.note)}</div>`;
    const h = o.nav_history || [];
    if (h.length >= 2) NX.lineChart($("#fd-nav"), h.map((x) => [new Date(x.ts * 1000).toISOString().slice(0, 16).replace("T", " "), x.nav]), { height: 150, fmt: (v) => money(v) });
    else $("#fd-nav").innerHTML = '<div class="why-empty">NAV history builds up as you open this page (one point per hour).</div>';
    $("#fd-sect").innerHTML = o.sectors.length ? o.sectors.map(([s, w]) => `<div style="display:flex;align-items:center;gap:8px;font-size:11px"><span style="width:130px" class="dim">${esc(s)}</span><span style="flex:1;height:6px;background:#1a1a1a"><i style="display:block;height:100%;width:${Math.min(100, w)}%;background:${w > lim.max_sector_pct && s !== "Other / ETF" ? "#ff4a4a" : "#ffa028"}"></i></span><b style="width:44px;text-align:right">${w}%</b></div>`).join("") : "";
    $("#fd-trades").innerHTML = o.trades.length ? `<table class="bb-tbl"><thead><tr><th>WHEN</th><th>SIDE</th><th>SYMBOL</th><th class="r">QTY</th><th class="r">PRICE</th><th class="r">FEES</th><th class="r">REALISED</th></tr></thead><tbody>${o.trades.map((t) => `<tr><td class="dim">${new Date(t.ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</td><td class="${t.side === "buy" ? "up" : "dn"}">${t.side.toUpperCase()}</td><td class="sym">${esc(t.symbol)}</td><td class="r">${num(t.qty, t.qty % 1 ? 2 : 0)}</td><td class="r">${NX.px(t.price)}</td><td class="r dim">${num(t.fees, 2)}</td><td class="r ${dir(t.realized)}">${t.side === "sell" ? signed(t.realized, 2) : "—"}</td></tr>`).join("")}</tbody></table>` : '<div class="why-empty">No trades yet.</div>';
  }

  async function refresh() {
    try { draw(await api("/fund/overview")); } catch (e) { $("#fd-pos").innerHTML = NX.err(e); }
  }

  const prev = window.onInvestTab;
  window.onInvestTab = function (tab) {
    if (prev) prev(tab);
    if (tab !== "fund") return;
    if (!built) { built = true; build(); }
    refresh();
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden && document.querySelector(".view.active")?.id === "view-fund") refresh(); }, 60000);
  };
})();
