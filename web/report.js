/* FULL REPORT — a professional, white-page equity research report for any security: masthead with the Jordan Terminal
   mark, an at-a-glance KPI strip, an AI/headline-driven summary, business description, a full price chart, key
   statistics, financial statements, analyst coverage, ownership, dividends, peer comparison and recent news. Opens
   full-screen over the terminal, can be marked up with a highlighter/pen and text notes like a printed report, and
   saved as a real PDF file (the marks are baked into it).
   Opened from SECURITY's "FULL REPORT" button (security.js) via window.openFullReport(symbol).
   Depends on: window.NX (invest.js: esc/px/pct/dir/signed/big/num/date/ago), api() (dashboard.js), and the CDN
   libraries html2canvas + jsPDF (index.html) for the PDF export only — the report itself has no other dependency. */
(function () {
  "use strict";
  const NX = window.NX;
  const { esc, px, pct, dir, signed, big, num, date: fmtDate, ago } = NX;
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const S = (v, f) => (isNum(v) ? f(v) : "—");
  const MARKERS = [["#ffe066", "yellow"], ["#8ce99a", "green"], ["#ffa8cc", "pink"], ["#74c0fc", "blue"], ["#ffb677", "orange"]];
  const PEN_COLORS = ["#111111", "#c92a2a", "#1971c2", "#2f9e44"]; // dark pen colors — this report is a white page

  let R = null; // { symbol, box, tok, setToolUi }
  let anno = null;

  // ------------------------------------------------------------------ light-theme charts (this report is a white page;
  // the terminal's own NX.lineChart/barChart are tuned dark and stay untouched for the rest of the app)
  function fitCanvas(el, h) {
    el.innerHTML = "";
    const cv = document.createElement("canvas"), w = Math.max(200, el.clientWidth || el.parentElement.clientWidth || 600), dpr = Math.max(2, window.devicePixelRatio || 1);
    cv.width = w * dpr; cv.height = h * dpr; cv.style.width = `${w}px`; cv.style.height = `${h}px`; cv.style.display = "block";
    el.appendChild(cv);
    const g = cv.getContext("2d"); g.scale(dpr, dpr); g.font = "10px 'JetBrains Mono', Consolas, monospace";
    return { g, w, h };
  }
  function priceChart(el, candles, o = {}) {
    const h = o.height || 260, showVol = candles.some((c) => c.v);
    const { g, w } = fitCanvas(el, h);
    if (candles.length < 2) { el.innerHTML = '<div class="rpt-nodata">No chart data.</div>'; return; }
    const padT = 8, padB = showVol ? 76 : 24, padR = 56, padL = 4;
    const volH = 44, volY = h - padB + 8;
    const closes = candles.map((c) => c.c);
    let lo = Math.min(...closes), hi = Math.max(...closes); const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    const x = (i) => padL + (i / (candles.length - 1)) * (w - padL - padR);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB - padT);
    const up = candles[candles.length - 1].c >= candles[0].c;
    const col = up ? "#0a9b52" : "#c0392b";
    // gridlines + price labels
    g.strokeStyle = "#eceef1"; g.fillStyle = "#8a8f9a"; g.lineWidth = 1;
    for (let i = 0; i <= 4; i++) { const v = lo + ((hi - lo) * i) / 4, yy = y(v); g.beginPath(); g.moveTo(padL, yy); g.lineTo(w - padR, yy); g.stroke(); g.textAlign = "left"; g.fillText(px(v), w - padR + 6, yy + 3); }
    // month tick labels
    const ticks = 6;
    for (let i = 0; i <= ticks; i++) { const idx = Math.round((i / ticks) * (candles.length - 1)); g.textAlign = i === 0 ? "left" : i === ticks ? "right" : "center"; g.fillText(String(candles[idx].t).slice(0, 7), x(idx), h - (showVol ? volY + volH + 14 : 6)); }
    // area + line
    g.beginPath(); candles.forEach((c, i) => (i ? g.lineTo(x(i), y(c.c)) : g.moveTo(x(i), y(c.c))));
    g.lineTo(x(candles.length - 1), y(lo)); g.lineTo(x(0), y(lo)); g.closePath();
    const gr = g.createLinearGradient(0, padT, 0, y(lo)); gr.addColorStop(0, col + "26"); gr.addColorStop(1, col + "00"); g.fillStyle = gr; g.fill();
    g.beginPath(); candles.forEach((c, i) => (i ? g.lineTo(x(i), y(c.c)) : g.moveTo(x(i), y(c.c)))); g.strokeStyle = col; g.lineWidth = 1.8; g.stroke();
    // volume
    if (showVol) {
      const maxV = Math.max(...candles.map((c) => c.v || 0)) || 1, bw = Math.max(1, ((w - padL - padR) / candles.length) * 0.7);
      candles.forEach((c, i) => { const bh = ((c.v || 0) / maxV) * volH; g.fillStyle = (i === 0 || c.c >= candles[i - 1].c ? "#0a9b52" : "#c0392b") + "55"; g.fillRect(x(i) - bw / 2, volY + volH - bh, bw, bh); });
      g.fillStyle = "#8a8f9a"; g.textAlign = "left"; g.fillText("VOLUME", padL, volY - 4);
    }
    // last-price tag
    const lastY = y(closes[closes.length - 1]);
    g.fillStyle = col; g.fillRect(w - padR + 1, lastY - 8, padR - 3, 16); g.fillStyle = "#fff"; g.textAlign = "left"; g.fillText(px(closes[closes.length - 1]), w - padR + 5, lastY + 3);
  }
  function barChart(el, labels, series, o = {}) {
    const h = o.height || 190, { g, w } = fitCanvas(el, h), pad = { l: 8, r: 8, t: 16, b: 24 };
    const all = series.flatMap((s) => s.values.filter(isNum));
    if (!all.length) { el.innerHTML = '<div class="rpt-nodata">No data.</div>'; return; }
    const hi = Math.max(0, ...all), lo = Math.min(0, ...all), span = hi - lo || 1;
    const y = (v) => pad.t + (1 - (v - lo) / span) * (h - pad.t - pad.b);
    const gw = (w - pad.l - pad.r) / labels.length, bw = Math.min(38, (gw * 0.7) / series.length);
    const fmt = o.fmt || ((v) => big(v));
    g.strokeStyle = "#d8dade"; g.beginPath(); g.moveTo(pad.l, y(0)); g.lineTo(w - pad.r, y(0)); g.stroke();
    labels.forEach((lab, i) => {
      const cx = pad.l + gw * i + gw / 2;
      series.forEach((s, k) => {
        const v = s.values[i]; if (!isNum(v)) return;
        const x0 = cx - (bw * series.length) / 2 + k * bw;
        g.fillStyle = v >= 0 ? s.color : "#c0392b"; g.fillRect(x0 + 1, Math.min(y(v), y(0)), bw - 2, Math.abs(y(v) - y(0)));
        g.fillStyle = "#3a3d45"; g.textAlign = "center"; g.fillText(fmt(v), x0 + bw / 2, (v >= 0 ? y(v) : y(v) + 12) - (v >= 0 ? 4 : 0));
      });
      g.fillStyle = "#8a8f9a"; g.textAlign = "center"; g.fillText(lab, cx, h - 6);
    });
  }
  const sparkline = (vals, w = 90, h = 26, col) => {
    if (!vals || vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const c = col || (vals[vals.length - 1] >= vals[0] ? "#0a9b52" : "#c0392b");
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / span) * (h - 4)).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${c}" stroke-width="1.4" points="${pts}"/></svg>`;
  };

  // ------------------------------------------------------------------ small render helpers
  const kv = (rows) => `<table class="rpt-kv"><tbody>${rows.filter((r) => r[1] !== null && r[1] !== undefined && r[1] !== "" && r[1] !== "—").map(([k, v]) => `<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`).join("")}</tbody></table>`;
  const sec = (n, title, meta, body) => `<section class="rpt-sec"><div class="rpt-sh"><span class="rpt-num">${n}</span><h2>${title}</h2><span class="sp"></span>${meta ? `<span class="rpt-meta">${meta}</span>` : ""}</div>${body}</section>`;
  const kpiTile = (label, value, sub, tone) => `<div class="rpt-kpi ${tone || ""}"><span class="l">${label}</span><b class="v">${value}</b>${sub ? `<span class="s">${sub}</span>` : ""}</div>`;

  function logoSvg() {
    return `<svg viewBox="0 0 32 32" width="34" height="34"><rect width="32" height="32" rx="7" fill="#0b0d12"/><path d="M8 22 L13 12 L18 19 L24 8" stroke="#c9820a" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  function masthead(s) {
    return `<div class="rpt-mast">
        <div class="rpt-brand">${logoSvg()}<div><b>JORDAN TERMINAL</b><span>Equity Research · Generated ${new Date().toLocaleString()}</span></div></div>
        <div class="rpt-mast-r"><span class="rpt-tag">FOR INTERNAL USE ONLY</span></div>
      </div><div class="rpt-mast-bar"></div>`;
  }
  function identity(s) {
    const pf = s.profile || {};
    return `<div class="rpt-ident">
      <div class="rpt-ident-l">
        <div class="rpt-pills">${[pf.sector, pf.industry, s.exchange].filter(Boolean).map((t) => `<span>${esc(t)}</span>`).join("")}</div>
        <h1>${esc(s.name)}</h1>
        <div class="rpt-tickerline">${esc(s.bbg)} ${s.currency ? " · " + esc(s.currency) : ""}</div>
      </div>
      <div class="rpt-ident-r"><b>${px(s.price)}</b><span class="${dir(s.chg)}">${signed(s.chg)} (${pct(s.pct)})</span></div>
    </div>`;
  }
  function kpiStrip(s) {
    const st = s.stats || {}, t = s.target || {};
    const up = isNum(t.mean) && s.price ? (t.mean / s.price - 1) * 100 : null;
    const pos = s.week52 && s.week52[1] > s.week52[0] ? Math.round(((s.price - s.week52[0]) / (s.week52[1] - s.week52[0])) * 100) : null;
    return `<div class="rpt-kpis">
      ${kpiTile("MARKET CAP", big(s.market_cap), "USD")}
      ${kpiTile("P/E (TTM)", S(st.pe, (v) => num(v, 1)), S(st.fwd_pe, (v) => "fwd " + num(v, 1)))}
      ${kpiTile("ANALYST RATING", esc((t.rating || "—").toUpperCase().replace("_", " ")), t.n ? `${t.n} analysts` : "")}
      ${kpiTile("TARGET UPSIDE", isNum(up) ? pct(up, true, 1) : "—", isNum(t.mean) ? "mean " + px(t.mean) : "", up > 0 ? "pos" : up < 0 ? "neg" : "")}
      ${kpiTile("BETA", S(st.beta, (v) => num(v, 2)), "vs S&P 500")}
      ${kpiTile("DIV YIELD", S(st.div_yield, (v) => pct(v * 100, false, 2)), "annual")}
      ${kpiTile("1Y PERFORMANCE", isNum(s.perf && s.perf["1Y"]) ? pct(s.perf["1Y"], true, 1) : "—", "", s.perf && s.perf["1Y"] > 0 ? "pos" : "neg")}
      ${kpiTile("52-WEEK RANGE", isNum(pos) ? `${pos}%` : "—", isNum(pos) ? "of range" : "")}
    </div>`;
  }
  function summarySection(s, ai) {
    const bullets = [];
    const t = s.target || {};
    if (isNum(t.mean) && s.price) { const up = (t.mean / s.price - 1) * 100; bullets.push(`Trading ${up >= 0 ? `${Math.abs(up).toFixed(1)}% below` : `${Math.abs(up).toFixed(1)}% above`} the ${t.n || ""}-analyst mean target of ${px(t.mean)}, consensus rating ${esc((t.rating || "—").replace("_", " "))}.`); }
    if (s.perf) bullets.push(`Year-to-date ${pct(s.perf.YTD, true, 1)}, one year ${pct(s.perf["1Y"], true, 1)}.`);
    if (isNum(s.stats.pe)) bullets.push(`Trades at ${num(s.stats.pe, 1)}x trailing earnings${isNum(s.stats.fwd_pe) ? ` (${num(s.stats.fwd_pe, 1)}x forward)` : ""}.`);
    if (isNum(s.stats.rev_growth)) bullets.push(`Revenue growing ${pct(s.stats.rev_growth * 100, true, 1)} y/y, net margin ${S(s.stats.profit_margin, (v) => pct(v * 100, false, 1))}.`);
    return `<div class="rpt-summary">
        ${ai ? `<p class="rpt-ai">${esc(ai.text || "").split("\n").join("<br>")}</p><span class="rpt-ai-src">${ai.mode === "llm" ? "AI-generated from recent headlines" : "Headline digest"} · not investment advice</span>` : ""}
        ${bullets.length ? `<ul class="rpt-bullets">${bullets.map((b) => `<li>${b}</li>`).join("")}</ul>` : ""}
      </div>`;
  }
  function perfStrip(perf) {
    return `<div class="rpt-perf">${["1W", "1M", "3M", "6M", "YTD", "1Y"].map((k) => `<div><span>${k}</span><b class="${dir(perf[k])}">${pct(perf[k], true, 1)}</b></div>`).join("")}</div>`;
  }
  function targetBlock(s) {
    const t = s.target || {};
    if (!isNum(t.mean) || !isNum(t.low) || !isNum(t.high)) return "";
    const lo = Math.min(t.low, s.price) * 0.98, hi = Math.max(t.high, s.price) * 1.02, at = (v) => ((v - lo) / (hi - lo)) * 100;
    const up = (t.mean / s.price - 1) * 100;
    return `<div class="rpt-tgt"><div class="rpt-tgt-h">ANALYST PRICE TARGET · ${t.n || "?"} analysts · <b>${esc((t.rating || "").toUpperCase().replace("_", " "))}</b></div>
      <div class="rpt-tgt-bar"><i class="rng" style="left:${at(t.low)}%;width:${at(t.high) - at(t.low)}%"></i><i class="mean" style="left:${at(t.mean)}%"></i><i class="px" style="left:${at(s.price)}%"></i></div>
      <div class="rpt-tgt-l"><span>LOW ${px(t.low)}</span><span>MEAN ${px(t.mean)} (${pct(up, true, 1)})</span><span>HIGH ${px(t.high)}</span></div></div>`;
  }
  function statsGrid(s) {
    const st = s.stats;
    return `<div class="rpt-cols4">
      <div><div class="rpt-h">VALUATION</div>${kv([["MARKET CAP", big(s.market_cap)], ["ENTERPRISE VALUE", big(st.ev)], ["P/E (TTM)", S(st.pe, (v) => num(v, 1))], ["P/E (FWD)", S(st.fwd_pe, (v) => num(v, 1))], ["PEG", S(st.peg, (v) => num(v, 2))], ["P/B", S(st.pb, (v) => num(v, 2))], ["P/S", S(st.ps, (v) => num(v, 2))], ["EV/EBITDA", S(st.ev_ebitda, (v) => num(v, 1))]])}</div>
      <div><div class="rpt-h">PROFITABILITY &amp; GROWTH</div>${kv([["REVENUE (TTM)", big(st.revenue)], ["EBITDA", big(st.ebitda)], ["GROSS MARGIN", S(st.gross_margin, (v) => pct(v * 100, false, 1))], ["OP. MARGIN", S(st.op_margin, (v) => pct(v * 100, false, 1))], ["NET MARGIN", S(st.profit_margin, (v) => pct(v * 100, false, 1))], ["ROE", S(st.roe, (v) => pct(v * 100, false, 1))], ["REV GROWTH", S(st.rev_growth, (v) => pct(v * 100, true, 1))], ["EARNINGS GROWTH", S(st.earn_growth, (v) => pct(v * 100, true, 1))]])}</div>
      <div><div class="rpt-h">BALANCE SHEET</div>${kv([["TOTAL CASH", big(st.total_cash)], ["TOTAL DEBT", big(st.total_debt)], ["DEBT/EQUITY", S(st.debt_equity, (v) => num(v, 1) + "%")], ["CURRENT RATIO", S(st.current_ratio, (v) => num(v, 2))], ["FREE CASH FLOW", big(st.fcf)], ["EPS (TTM)", S(st.eps, (v) => px(v))], ["EPS (FWD)", S(st.fwd_eps, (v) => px(v))]])}</div>
      <div><div class="rpt-h">TRADING &amp; DIVIDEND</div>${kv([["SHARES OUT", big(st.shares_out)], ["FLOAT", big(st.float)], ["SHORT % FLOAT", S(st.short_pct_float, (v) => pct(v * 100, false, 2))], ["AVG VOLUME", big(s.avg_volume)], ["DIV YIELD", S(st.div_yield, (v) => pct(v * 100, false, 2))], ["ANNUAL DIV", S(st.div_rate, (v) => px(v))], ["50D / 200D SMA", `${S(st.sma50, px)} / ${S(st.sma200, px)}`]])}</div>
    </div>`;
  }
  function financialsTable(fa) {
    if (!fa || !fa.available) return `<p class="rpt-nodata">${esc((fa && fa.reason) || "Financial statements are not available for this security.")}</p>`;
    const idx = fa.periods.map((_, i) => i).reverse().slice(0, 5).reverse();
    const periods = idx.map((i) => fa.periods[i]);
    const R2 = {}; fa.rows.forEach((r) => (R2[r.key] = idx.map((i) => r.values[i])));
    const fy = (d) => new Date(d).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
    const row = (label, vals, kind) => (vals ? `<tr><td class="k">${label}</td>${vals.map((v) => `<td class="v r">${kind === "pct" ? S(v, (x) => pct(x, false, 1)) : kind === "eps" ? S(v, (x) => num(x, 2)) : big(v)}</td>`).join("")}</tr>` : "");
    const margin = (a, b) => (R2[a] && R2[b] ? R2[a].map((v, i) => (isNum(v) && isNum(R2[b][i]) && R2[b][i] ? (v / R2[b][i]) * 100 : null)) : null);
    return `<div class="rpt-cols2"><div>
      <table class="rpt-fa"><thead><tr><th>ANNUAL</th>${periods.map((p) => `<th class="r">${fy(p)}</th>`).join("")}</tr></thead><tbody>
      ${row("Revenue", R2.revenue)}${row("Net income", R2.netinc)}${row("Diluted EPS ($)", R2.eps, "eps")}${row("Gross margin", margin("gross", "revenue"), "pct")}${row("Operating margin", margin("opinc", "revenue"), "pct")}${row("Free cash flow", R2.fcf)}${row("Total assets", R2.assets)}${row("Shareholders' equity", R2.equity)}
      </tbody></table><p class="rpt-nodata">Source: ${esc(fa.source || "")}${fa.company ? " · " + esc(fa.company) : ""}</p></div>
      <div id="rpt-fa-chart" style="height:190px"></div></div>`;
  }
  function analystSection(a, s) {
    if (!a) return '<p class="rpt-nodata">Analyst coverage is not available for this security.</p>';
    const t = a.target || {}, trend = a.recommendation || [], first = trend[0] || {};
    const cats = [["strongBuy", "STRONG BUY", "#0a9b52"], ["buy", "BUY", "#63b57a"], ["hold", "HOLD", "#c9820a"], ["sell", "SELL", "#e08a5c"], ["strongSell", "STRONG SELL", "#c0392b"]];
    const n = cats.reduce((x, c) => x + (first[c[0]] || 0), 0) || 1;
    const ACTION_LABEL = { up: "UPGRADE", down: "DOWNGRADE", main: "MAINTAINED", reit: "REITERATED", init: "INITIATED" };
    const changes = [...(a.changes || [])].sort((x, y) => (y.ts || 0) - (x.ts || 0)).slice(0, 6);
    return `<div class="rpt-cols2">
      <div>${targetBlock({ ...s, target: t })}
        <div class="rpt-h">RATING DISTRIBUTION — ${t.n || 0} ANALYSTS</div>
        <div class="rpt-distbar">${cats.map((c) => `<i style="width:${((first[c[0]] || 0) / n) * 100}%;background:${c[2]}" title="${c[1]}: ${first[c[0]] || 0}"></i>`).join("")}</div>
        <div class="rpt-distkey">${cats.map((c) => `<span><i style="background:${c[2]}"></i>${c[1]} <b>${first[c[0]] ?? 0}</b></span>`).join("")}</div></div>
      <div><div class="rpt-h">RECENT RATING CHANGES</div>
        <table class="rpt-fa"><thead><tr><th>DATE</th><th>FIRM</th><th>ACTION</th><th>RATING</th></tr></thead><tbody>
        ${changes.map((x) => `<tr><td class="k">${fmtDate(x.ts)}</td><td class="k">${esc(x.firm)}</td><td class="k">${esc(ACTION_LABEL[x.action] || (x.action || "").toUpperCase())}</td><td class="k">${x.from ? esc(x.from) + " → " : ""}<b>${esc(x.to || "")}</b></td></tr>`).join("") || '<tr><td colspan="4" class="k">No recent changes.</td></tr>'}
        </tbody></table></div>
    </div>
    <div class="rpt-h" style="margin-top:16px">EARNINGS OUTLOOK</div>
    ${earningsBlock(a, s)}`;
  }
  function ownershipSection(h) {
    if (!h) return '<p class="rpt-nodata">Ownership data is not available for this security.</p>';
    const b = h.breakdown || {};
    const rows = (arr) => (arr || []).slice(0, 5).map((r) => `<tr><td class="k">${esc(r.name)}</td><td class="v r">${isNum(r.pct) ? pct(r.pct * 100, false, 2) : "—"}</td><td class="v r">${big(r.shares)}</td></tr>`).join("") || '<tr><td colspan="3" class="k">No data.</td></tr>';
    return `<div class="rpt-kpis sm">${kpiTile("INSIDER OWNERSHIP", isNum(b.insidersPercentHeld) ? pct(b.insidersPercentHeld * 100, false, 2) : "—")}${kpiTile("INSTITUTIONAL OWNERSHIP", isNum(b.institutionsPercentHeld) ? pct(b.institutionsPercentHeld * 100, false, 1) : "—")}${kpiTile("INSTITUTIONAL HOLDERS", isNum(b.institutionsCount) ? num(b.institutionsCount, 0) : "—")}</div>
      <div class="rpt-cols2"><div><div class="rpt-h">TOP INSTITUTIONAL HOLDERS</div><table class="rpt-fa"><thead><tr><th>HOLDER</th><th class="r">% HELD</th><th class="r">SHARES</th></tr></thead><tbody>${rows(h.institutions)}</tbody></table></div>
      <div><div class="rpt-h">RECENT INSIDER TRANSACTIONS</div><table class="rpt-fa"><thead><tr><th>INSIDER</th><th>ACTIVITY</th><th class="r">SHARES</th></tr></thead><tbody>${(h.transactions || []).slice(0, 5).map((t) => `<tr><td class="k">${esc(t.name)}</td><td class="k">${esc((t.text || "").slice(0, 34))}</td><td class="v r">${big(t.shares)}</td></tr>`).join("") || '<tr><td colspan="3" class="k">No data.</td></tr>'}</tbody></table></div></div>`;
  }
  function dividendSection(d, st) {
    if (!d || (!d.dividends.length && !isNum(st.div_yield))) return '<p class="rpt-nodata">No dividend history for this security.</p>';
    const yrs = [...(d.yearly || [])].reverse().slice(-6);
    return `<div class="rpt-kpis sm">${kpiTile("DIVIDEND YIELD", S(st.div_yield, (v) => pct(v * 100, false, 2)))}${kpiTile("ANNUAL DIVIDEND", S(st.div_rate, px))}${kpiTile("PAYOUT RATIO", S(st.payout, (v) => pct(v * 100, false, 1)))}</div>
      ${yrs.length ? `<div id="rpt-dvd-chart" style="height:170px"></div>` : '<p class="rpt-nodata">No dividend payment history on record.</p>'}`;
  }
  function peersSection(r, s) {
    if (!r || !r.peers || !r.peers.length) return '<p class="rpt-nodata">No comparable peers found.</p>';
    const rows = [{ symbol: s.symbol, name: s.name, price: s.price, pct: s.pct, self: true, spark: s.spark.slice(-24) }, ...r.peers.slice(0, 6).map((p) => ({ symbol: p.symbol, name: p.name, price: p.price, pct: p.pct, spark: p.spark }))];
    return `<table class="rpt-fa"><thead><tr><th>SYMBOL</th><th>NAME</th><th class="r">PRICE</th><th class="r">CHANGE</th><th>30D</th></tr></thead><tbody>${rows.map((x) => `<tr class="${x.self ? "self" : ""}"><td class="k"><b>${esc(x.symbol)}</b></td><td class="k">${esc((x.name || "").slice(0, 28))}</td><td class="v r">${px(x.price)}</td><td class="v r ${dir(x.pct)}">${pct(x.pct)}</td><td>${sparkline(x.spark, 80, 20)}</td></tr>`).join("")}</tbody></table>`;
  }
  function earningsCta(s, a) {
    const ne = (a && a.next_earnings) || s.next_earnings || [];
    const d0 = ne[0];
    if (!isNum(d0)) return "";
    const days = Math.round((d0 * 1000 - Date.now()) / 86400000);
    const est = (a && a.estimates) || [];
    const cur = est.find((e) => e.period === "0q") || est[0];
    return `<div class="rpt-earn-cta"><b>NEXT EARNINGS · ${fmtDate(d0)}</b>
      <span>${days >= 0 ? `in ${days} day${days === 1 ? "" : "s"}` : `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} ago`}${ne.length > 1 ? " (estimated window)" : ""}</span>
      ${cur && isNum(cur.eps_avg) ? `<span class="sep">·</span><span>EPS EST ${px(cur.eps_avg)}</span>` : ""}
      ${cur && isNum(cur.rev_avg) ? `<span class="sep">·</span><span>REV EST ${big(cur.rev_avg)}</span>` : ""}</div>`;
  }
  function earningsBlock(a, s) {
    const est = ((a && a.estimates) || []).slice(0, 4);
    const sur = [...((a && a.surprises) || [])].sort((x, y) => (y.quarter || 0) - (x.quarter || 0)).slice(0, 4);
    const isoDate = (d) => (d ? new Date(d).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—");
    const estRows = est.map((e) => `<tr><td class="k">${esc(e.period || "—")}</td><td class="k">${isoDate(e.end)}</td><td class="v r">${S(e.eps_low, (v) => num(v, 2))} – ${S(e.eps_high, (v) => num(v, 2))}</td><td class="v r">${S(e.eps_avg, (v) => num(v, 2))}</td><td class="v r">${big(e.rev_avg)}</td><td class="v r">${S(e.growth, (v) => pct(v * 100, true, 1))}</td></tr>`).join("");
    const surRows = sur.map((x) => `<tr><td class="k">${isNum(x.quarter) ? fmtDate(x.quarter) : "—"}</td><td class="v r">${S(x.estimate, (v) => num(v, 2))}</td><td class="v r">${S(x.actual, (v) => num(v, 2))}</td><td class="v r ${x.surprise_pct >= 0 ? "up" : "dn"}">${S(x.surprise_pct, (v) => pct(v, true, 1))}</td></tr>`).join("");
    return `<div class="rpt-cols2">
      <div><div class="rpt-h">EARNINGS ESTIMATES</div><table class="rpt-fa"><thead><tr><th>PERIOD</th><th>ENDS</th><th class="r">EPS RANGE</th><th class="r">EPS AVG</th><th class="r">REV AVG</th><th class="r">GROWTH</th></tr></thead><tbody>${estRows || '<tr><td colspan="6" class="k">No estimates available.</td></tr>'}</tbody></table></div>
      <div><div class="rpt-h">SURPRISE HISTORY</div><table class="rpt-fa"><thead><tr><th>QUARTER</th><th class="r">ESTIMATE</th><th class="r">ACTUAL</th><th class="r">SURPRISE</th></tr></thead><tbody>${surRows || '<tr><td colspan="4" class="k">No surprise history.</td></tr>'}</tbody></table></div>
    </div>`;
  }
  const MACRO_PICK = ["FEDFUNDS", "DGS10", "DGS2", "T10Y2Y", "CPIAUCSL", "CPILFESL", "UNRATE", "UMCSENT"];
  function macroSection(m) {
    if (!m || !m.rows || !m.rows.length) return '<p class="rpt-nodata">Macro data is not available right now.</p>';
    const rows = MACRO_PICK.map((id) => m.rows.find((r) => r.id === id)).filter(Boolean);
    const fmtv = (r) => `${num(r.value, 2)}${r.unit && r.unit.includes("%") ? "%" : r.unit === "index" ? "" : ` ${r.unit}`}`;
    return `<div class="rpt-kpis">${rows.map((r) => {
      const chg = isNum(r.prev) ? r.value - r.prev : null;
      return kpiTile(r.label.toUpperCase(), fmtv(r), `${sparkline(r.spark, 62, 18)}<b class="${dir(chg)}" style="font:700 9px 'JetBrains Mono',monospace">${isNum(chg) ? signed(chg, 2) : ""}</b>`);
    }).join("")}</div><p class="rpt-small">Source: Federal Reserve Economic Data (FRED), as of ${esc(rows[0] ? rows[0].date : "")}.</p>`;
  }
  function filingsSection(f) {
    if (!f || !f.available) return `<p class="rpt-nodata">${esc((f && f.reason) || "SEC filings are not available for this security.")}</p>`;
    const items = f.items.slice(0, 12);
    return `<table class="rpt-fa"><thead><tr><th>FILED</th><th>FORM</th><th>DESCRIPTION</th><th class="r">LINK</th></tr></thead><tbody>
      ${items.map((x) => `<tr><td class="k">${esc(x.filed)}</td><td class="k"><b>${esc(x.form)}</b></td><td class="k">${esc(x.note || x.desc || "")}</td><td class="v r"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">VIEW ↗</a></td></tr>`).join("") || '<tr><td colspan="4" class="k">No filings on record.</td></tr>'}
      </tbody></table><p class="rpt-small">CIK ${esc(f.cik)} · ${esc(f.name || "")} · Source: SEC EDGAR — full history at sec.gov/edgar.</p>`;
  }
  function newsList(items) {
    if (!items || !items.length) return '<p class="rpt-nodata">No recent headlines.</p>';
    return `<ul class="rpt-news">${items.slice(0, 8).map((i) => `<li><span class="t">${esc(ago(i.ts) || "")}</span><span class="s">${esc((i.source || "").slice(0, 18))}</span>${esc(i.title)}</li>`).join("")}</ul>`;
  }

  // ------------------------------------------------------------------ compose the full report
  async function renderContent(box, symbol) {
    const tok = box.dataset.tok;
    box.innerHTML = `<div class="rpt-loadwrap"><div class="rpt-spin"></div><div>Building the full report for ${esc(symbol)}…</div><small>chart · financials · analysts · ownership · dividends · news</small></div>`;
    const enc = encodeURIComponent(symbol);
    const [rs, rnews, rfa, ranr, rhds, rdvd, rpeers, rai, rchart, rfil, rmac] = await Promise.allSettled([
      api(`/invest/security?symbol=${enc}`),
      api(`/invest/news?symbol=${enc}`),
      api(`/invest/financials?symbol=${enc}`),
      api(`/invest/analysts?symbol=${enc}`),
      api(`/invest/holders?symbol=${enc}`),
      api(`/invest/dividends?symbol=${enc}`),
      api(`/invest/peers?symbol=${enc}`),
      api(`/ai/summary?symbol=${enc}`),
      api(`/invest/chart?symbol=${enc}&range=1Y`),
      api(`/invest/filings?symbol=${enc}`),
      api(`/invest/macro`),
    ]);
    if (box.dataset.tok !== tok) return;
    if (rs.status !== "fulfilled") { box.innerHTML = `<div class="rpt-nodata" style="padding:60px;text-align:center">Could not load ${esc(symbol)}: ${esc(rs.reason && rs.reason.message)}</div>`; return; }
    const s = rs.value;
    const val = (r) => (r.status === "fulfilled" ? r.value : null);
    const news = val(rnews) || { items: [] }, fa = val(rfa), a = val(ranr), h = val(rhds), d = val(rdvd), peers = val(rpeers), ai = val(rai), chart = val(rchart);
    const fil = val(rfil), mac = val(rmac);
    const pf = s.profile || {};
    const site = pf.website ? `<a href="${esc(pf.website)}" target="_blank" rel="noopener noreferrer">${esc(pf.website.replace(/^https?:\/\//, ""))}</a>` : "";

    box.innerHTML = `<div class="rpt-page">
      <div class="rpt-cover">
        ${masthead(s)}
        ${identity(s)}
        ${earningsCta(s, a)}
        ${kpiStrip(s)}
      </div>
      ${sec("1", "INVESTMENT SUMMARY", "", summarySection(s, ai))}
      ${sec("2", "BUSINESS DESCRIPTION", esc(s.exchange || ""), `
        <p class="rpt-about">${esc(s.description || "No business description is available for this security.")}</p>
        <div class="rpt-cols2">${kv([["SECTOR", esc(pf.sector)], ["INDUSTRY", esc(pf.industry)], ["COUNTRY", esc(pf.country)], ["HEADQUARTERS", esc([pf.city, pf.state].filter(Boolean).join(", "))], ["EMPLOYEES", isNum(pf.employees) ? num(pf.employees, 0) : ""], ["WEBSITE", site]])}
        ${kv([["TICKER", esc(s.symbol)], ["52-WEEK RANGE", `${px(s.week52[0])} – ${px(s.week52[1])}`], ["NEXT EARNINGS", (s.next_earnings || []).length ? fmtDate(s.next_earnings[0]) : "—"], ["CURRENCY", esc(s.currency)]])}</div>
        ${pf.officers && pf.officers.length ? `<div class="rpt-h">KEY OFFICERS</div><table class="rpt-kv wide"><tbody>${pf.officers.slice(0, 6).map((o) => `<tr><td class="k">${esc(o.name)}</td><td class="v">${esc(o.title)}</td></tr>`).join("")}</tbody></table>` : ""}`)}
      ${sec("3", "PRICE PERFORMANCE", "1 year", `<div id="rpt-chart" style="height:260px"></div>${perfStrip(s.perf)}`)}
      ${sec("4", "KEY STATISTICS", "", statsGrid(s))}
      ${sec("5", "FINANCIAL STATEMENTS", "annual", financialsTable(fa))}
      ${sec("6", "ANALYST COVERAGE &amp; EARNINGS", "", analystSection(a, s))}
      ${sec("7", "OWNERSHIP", "", ownershipSection(h))}
      ${sec("8", "DIVIDENDS", "", dividendSection(d, s.stats))}
      ${sec("9", "PEER COMPARISON", "", peersSection(peers, s))}
      ${sec("10", "MACRO &amp; FED BACKDROP", "", macroSection(mac))}
      ${sec("11", "REGULATORY FILINGS", "SEC EDGAR", filingsSection(fil))}
      ${sec("12", "RECENT NEWS", "", newsList(news.items))}
      <div class="rpt-foot"><div>JORDAN TERMINAL — this report is generated from market data for personal research and is not investment advice.</div><div>${esc(s.bbg)} · ${new Date().toLocaleDateString()}</div></div>
    </div>`;

    const candles = chart && chart.candles ? chart.candles.map((c) => ({ t: new Date(c[0] * 1000).toISOString().slice(0, 10), c: c[4], v: c[5] })) : s.spark.map((v, i) => ({ t: String(i), c: v }));
    priceChart(box.querySelector("#rpt-chart"), candles, { height: 260 });
    if (fa && fa.available) {
      const idx = fa.periods.map((_, i) => i).reverse().slice(0, 5).reverse();
      const labels = idx.map((i) => new Date(fa.periods[i]).toLocaleDateString("en-GB", { month: "short", year: "2-digit" }));
      const R2 = {}; fa.rows.forEach((r) => (R2[r.key] = idx.map((i) => r.values[i])));
      const el = box.querySelector("#rpt-fa-chart"); if (el) barChart(el, labels, [{ name: "Revenue", color: "#c9820a", values: R2.revenue || [] }, { name: "Net income", color: "#1971c2", values: R2.netinc || [] }], { height: 190 });
    }
    if (d && d.yearly && d.yearly.length) {
      const yrs = [...d.yearly].reverse().slice(-6);
      const el = box.querySelector("#rpt-dvd-chart"); if (el) barChart(el, yrs.map((y) => String(y.year)), [{ name: "Dividends", color: "#c9820a", values: yrs.map((y) => y.total) }], { height: 170, fmt: (v) => num(v, 2) });
    }
    resizeAnno();
  }

  // ------------------------------------------------------------------ annotation layer (marker / pen + text notes)
  function buildAnno(scrollHost, contentEl) {
    const wrap = document.createElement("div"); wrap.className = "rpt-anno-wrap";
    const cv = document.createElement("canvas"); cv.className = "rpt-anno-cv";
    const notes = document.createElement("div"); notes.className = "rpt-notes";
    wrap.append(cv, notes);
    contentEl.appendChild(wrap);
    const g = cv.getContext("2d");
    const state = { tool: "none", color: MARKERS[0][0], strokes: [], drawing: false, pts: [] };
    function size() {
      const w = contentEl.scrollWidth, h = contentEl.scrollHeight;
      wrap.style.height = `${h}px`;
      const dpr = window.devicePixelRatio || 1;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = `${w}px`; cv.style.height = `${h}px`; }
      redraw();
    }
    function redraw(liveStroke) {
      const dpr = window.devicePixelRatio || 1;
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, cv.width, cv.height);
      [...state.strokes, liveStroke].filter(Boolean).forEach((st) => {
        g.globalAlpha = st.mode === "marker" ? 0.42 : 1; g.strokeStyle = st.color; g.lineWidth = st.size; g.lineCap = "round"; g.lineJoin = "round";
        g.beginPath(); st.pts.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]))); g.stroke();
      });
      g.globalAlpha = 1;
    }
    function setTool(t, color) {
      state.tool = t; if (color) state.color = color;
      wrap.style.pointerEvents = t === "none" ? "none" : "auto";
      cv.style.cursor = t === "note" ? "text" : t === "erase" ? "cell" : t === "none" ? "default" : "crosshair";
      if (R && R.setToolUi) R.setToolUi(t, state.color);
    }
    const relPos = (e) => { const r = wrap.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    cv.addEventListener("pointerdown", (e) => {
      if (state.tool === "marker" || state.tool === "pen") { state.drawing = true; state.pts = [relPos(e)]; cv.setPointerCapture(e.pointerId); }
      else if (state.tool === "erase") eraseAt(relPos(e));
    });
    cv.addEventListener("pointermove", (e) => {
      if (!state.drawing) return;
      state.pts.push(relPos(e));
      redraw({ color: state.color, size: state.tool === "marker" ? 20 : 2.5, mode: state.tool, pts: state.pts });
    });
    window.addEventListener("pointerup", () => {
      if (!state.drawing) return;
      state.drawing = false;
      if (state.pts.length > 1) state.strokes.push({ color: state.color, size: state.tool === "marker" ? 20 : 2.5, mode: state.tool, pts: state.pts });
      state.pts = []; redraw();
    });
    function eraseAt(p) {
      const before = state.strokes.length;
      state.strokes = state.strokes.filter((st) => !st.pts.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 16));
      if (state.strokes.length !== before) redraw();
    }
    wrap.addEventListener("click", (e) => { if (state.tool === "note" && (e.target === wrap || e.target === cv)) addNote(...relPos(e)); });
    function addNote(x, y, text) {
      const n = document.createElement("div"); n.className = "rpt-note"; n.style.left = `${x}px`; n.style.top = `${y}px`;
      n.innerHTML = `<div class="rpt-note-bar"><span>NOTE</span><button data-del title="remove">✕</button></div><div class="rpt-note-txt" contenteditable="true" spellcheck="false"></div>`;
      notes.appendChild(n);
      n.querySelector(".rpt-note-txt").textContent = text || "";
      const bar = n.querySelector(".rpt-note-bar"); let drag = null;
      bar.addEventListener("pointerdown", (e) => { drag = { x: e.clientX - n.offsetLeft, y: e.clientY - n.offsetTop }; bar.setPointerCapture(e.pointerId); });
      bar.addEventListener("pointermove", (e) => { if (drag) { n.style.left = `${e.clientX - drag.x}px`; n.style.top = `${e.clientY - drag.y}px`; } });
      bar.addEventListener("pointerup", () => { drag = null; });
      n.querySelector("[data-del]").addEventListener("click", () => n.remove());
      if (!text) { setTool("none"); n.querySelector(".rpt-note-txt").focus(); }
    }
    function undo() { state.strokes.pop(); redraw(); }
    function clearAll() { if (!state.strokes.length && !notes.children.length) return; if (!confirm("Clear every mark and note on this report?")) return; state.strokes = []; notes.innerHTML = ""; redraw(); }
    new ResizeObserver(size).observe(contentEl);
    size();
    return { setTool, undo, clearAll, size, get hasMarks() { return state.strokes.length > 0 || notes.children.length > 0; } };
  }
  function resizeAnno() { if (anno) anno.size(); }

  // ------------------------------------------------------------------ toolbar + shell
  function toolbarHtml() {
    return `<div class="rpt-tools">
        <button class="rpt-tb" data-rtool="none" title="Pointer (scroll and click links normally)">✥</button>
        ${MARKERS.map(([c, name]) => `<button class="rpt-tb rpt-mk" data-rtool="marker" data-rc="${c}" style="--c:${c}" title="Highlighter — ${name}"></button>`).join("")}
        <span class="rpt-sep"></span>
        ${PEN_COLORS.map((c) => `<button class="rpt-tb rpt-pen" data-rtool="pen" data-rc="${c}" style="--c:${c}" title="Fine pen">✎</button>`).join("")}
        <button class="rpt-tb" data-rtool="erase" title="Erase a mark">⌫</button>
        <button class="rpt-tb" data-rtool="note" title="Add a text note (click the report to place it)">🗒</button>
        <span class="rpt-sep"></span>
        <button class="rpt-tb" data-ract="undo" title="Undo last mark">↶</button>
        <button class="rpt-tb" data-ract="clear" title="Clear all marks and notes">🗑</button>
        <span class="sp"></span>
        <button class="bb-chip" data-ract="print">PRINT</button>
        <button class="bb-chip on" data-ract="pdf">💾 SAVE AS PDF</button>
        <button class="rpt-close" data-ract="close" title="Close">✕</button>
      </div>`;
  }

  async function open(symbol) {
    const tok = String(Date.now());
    let modal = document.getElementById("rpt-modal");
    if (!modal) {
      modal = document.createElement("div"); modal.id = "rpt-modal"; modal.className = "rpt-modal";
      modal.innerHTML = `${toolbarHtml()}<div class="rpt-scroll" id="rpt-scroll"><div class="rpt-box" id="rpt-box"></div></div>`;
      document.body.appendChild(modal);
      modal.addEventListener("click", (e) => {
        const t = e.target.closest("[data-rtool]");
        if (t) { anno && anno.setTool(t.dataset.rtool, t.dataset.rc); modal.querySelectorAll("[data-rtool]").forEach((b) => b.classList.toggle("on", b === t)); return; }
        const act = e.target.closest("[data-ract]"); if (!act) return;
        const a = act.dataset.ract;
        if (a === "undo") anno && anno.undo();
        else if (a === "clear") anno && anno.clearAll();
        else if (a === "close") close();
        else if (a === "print") window.print();
        else if (a === "pdf") exportPdf(symbol);
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.getElementById("rpt-modal")) close(); });
    }
    modal.classList.add("show");
    const box = document.getElementById("rpt-box");
    box.dataset.tok = tok;
    R = { symbol, box, tok, setToolUi: (tool, color) => { modal.querySelectorAll("[data-rtool]").forEach((b) => b.classList.toggle("on", b.dataset.rtool === tool && ((tool !== "marker" && tool !== "pen") || b.dataset.rc === color))); } };
    anno = null;
    await renderContent(box, symbol);
    if (box.dataset.tok !== tok) return;
    anno = buildAnno(document.getElementById("rpt-scroll"), box);
    modal.querySelector('[data-rtool="none"]').classList.add("on");
  }
  function close() {
    const modal = document.getElementById("rpt-modal");
    if (!modal) return;
    if (anno && anno.hasMarks && !confirm("Close the report? Your marks and notes will be lost unless you save it as a PDF first.")) return;
    modal.classList.remove("show");
    R = null; anno = null;
  }

  // Each section is captured and paginated separately, so a page break never lands mid-paragraph or mid-table —
  // a tall section simply spills onto a second page of its own rather than sharing a page with its neighbour.
  async function exportPdf(symbol) {
    const btn = document.querySelector('[data-ract="pdf"]');
    const old = btn.textContent; btn.disabled = true;
    try {
      const page = document.getElementById("rpt-box").querySelector(".rpt-page");
      const blocks = [page.querySelector(".rpt-cover"), ...page.querySelectorAll(".rpt-sec")].filter(Boolean);
      if (!window.jspdf || !window.html2canvas) await NX.loadGroup("pdf"); // the PDF libraries load on first export only (loader.js)
      const { jsPDF } = window.jspdf;
      const pdf = new jsPDF({ unit: "pt", format: "a4" });
      const pageW = pdf.internal.pageSize.getWidth(), pageH = pdf.internal.pageSize.getHeight();
      const margin = 28, footH = 20, usableW = pageW - margin * 2, usableH = pageH - margin * 2 - footH;
      const scale = Math.max(3, (window.devicePixelRatio || 1) * 2); // fixed high supersampling — print quality, not tied to the screen's own pixel density
      let pageNum = 0;
      const stampFoot = () => {
        pdf.setFont("courier", "normal"); pdf.setFontSize(7); pdf.setTextColor(150);
        pdf.text(`JORDAN TERMINAL · ${symbol} EQUITY RESEARCH · NOT INVESTMENT ADVICE`, margin, pageH - 10);
        pdf.text(`${pageNum}`, pageW - margin, pageH - 10, { align: "right" });
        pdf.setTextColor(0);
      };
      for (let i = 0; i < blocks.length; i++) {
        btn.textContent = `RENDERING ${i + 1}/${blocks.length}…`;
        const canvas = await html2canvas(blocks[i], { backgroundColor: "#ffffff", scale, useCORS: true, windowWidth: blocks[i].scrollWidth, windowHeight: blocks[i].scrollHeight });
        const imgH = (canvas.height * usableW) / canvas.width;
        if (imgH <= usableH) {
          pdf.addPage(); pageNum++;
          pdf.addImage(canvas.toDataURL("image/jpeg", 0.98), "JPEG", margin, margin, usableW, imgH, undefined, "NONE");
          stampFoot();
          continue;
        }
        // section is taller than one page: split its own canvas across consecutive pages only (never mixes with another section)
        const pxPerPage = Math.floor((usableH * canvas.width) / usableW);
        for (let y = 0; y < canvas.height; y += pxPerPage) {
          const sliceH = Math.min(pxPerPage, canvas.height - y);
          const slice = document.createElement("canvas"); slice.width = canvas.width; slice.height = sliceH;
          const sg = slice.getContext("2d"); sg.imageSmoothingEnabled = false; sg.drawImage(canvas, 0, y, canvas.width, sliceH, 0, 0, canvas.width, sliceH);
          pdf.addPage(); pageNum++;
          pdf.addImage(slice.toDataURL("image/jpeg", 0.98), "JPEG", margin, margin, usableW, (sliceH * usableW) / canvas.width, undefined, "NONE");
          stampFoot();
        }
      }
      pdf.deletePage(1); // discard jsPDF's default leading blank page — every real page was added explicitly above
      pdf.save(`${symbol}_report_${new Date().toISOString().slice(0, 10)}.pdf`);
      if (window.showToast) showToast(`Saved: ${symbol} report PDF (${pageNum} pages)`);
    } catch (e) {
      const msg = "Could not build the PDF: " + (e.message || e);
      if (window.showToast) showToast(msg); else alert(msg);
    } finally {
      btn.textContent = old; btn.disabled = false;
    }
  }

  window.openFullReport = open;
})();
