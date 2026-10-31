/* ACCOUNT DETAIL — the full-screen view of one account: overview & statistics, Monte Carlo of the evaluation,
   breakdowns (hour / weekday / side / exit / calendar), the trade log and the account controls.
   Data: GET /api/nostro/account/{id}/insights (server-side numpy), trades and controls use the existing /api/accounts routes.
   Interactive: crosshair + tooltips on every chart, count-up numbers, sortable/filterable trade log, click a point on the
   equity curve to jump to that trade, click a size in the sweep to see the what-if, keyboard tab switching (1–5, ← →).
   Replaces window.openAccountModal (the old modal stays reachable through ADVANCED → window.openLegacyAccountModal). */
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n, d = 0) => (!isN(n) ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const smoney = (n, d = 0) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const num = (n, d = 2) => (!isN(n) ? "—" : Number(n).toFixed(d));
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const TABS = [["ov", "OVERVIEW"], ["mc", "MONTE CARLO"], ["bk", "BREAKDOWN"], ["tr", "TRADES"], ["ct", "CONTROLS"]];
  const D = { id: null, tab: "ov", data: null, trades: null, paths: 4000, seq: 0, sort: { key: "i", dir: -1 }, tfilter: "all", tquery: "", hilite: null };
  let legacy = null;
  const tipAttr = (...lines) => `data-tip="${esc(lines.filter(Boolean).join("|"))}"`;

  // ------------------------------------------------------------------ svg helpers
  const W = 760, H = 250, PAD = { l: 58, r: 14, t: 12, b: 22 };
  function scale(lo, hi, a, b) { const s = hi - lo || 1; return (v) => a + ((v - lo) / s) * (b - a); }
  function axisY(lo, hi, fmt = money, ticks = 4) {
    const y = scale(lo, hi, H - PAD.b, PAD.t);
    let out = "";
    for (let i = 0; i <= ticks; i++) { const v = lo + ((hi - lo) * i) / ticks; out += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${PAD.l - 6}" y="${y(v) + 3}" class="t" text-anchor="end">${fmt(v)}</text>`; }
    return { y, svg: out };
  }
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const crosshair = (id) => `<g class="xh" id="${id}" style="display:none"><line class="xl" y1="${PAD.t}" y2="${H - PAD.b}"/><circle class="xd" r="4"/></g><rect class="hit" data-chart="${id}" x="${PAD.l}" y="${PAD.t}" width="${W - PAD.l - PAD.r}" height="${H - PAD.t - PAD.b}"/>`;

  function equityChart(curve, acct) {
    if (!curve || curve.length < 2) return `<div class="acd-empty">No closed trades yet.</div>`;
    const eq = curve.map((c) => c.eq);
    const start = acct.starting_balance;
    const isEval = acct.account_type === "apex_eval";
    const target = isEval && acct.profit_target ? start + acct.profit_target : null;
    let lo = Math.min(...eq, acct.drawdown_floor ?? Infinity), hi = Math.max(...eq, target ?? -Infinity);
    const padv = (hi - lo) * 0.08 || 50; lo -= padv; hi += padv;
    const { y, svg } = axisY(lo, hi);
    const x = scale(0, curve.length - 1, PAD.l, W - PAD.r);
    let peak = start; const floorPts = [], peakPts = [], info = [];
    curve.forEach((c, i) => {
      peak = Math.max(peak, c.eq); peakPts.push([x(i), y(peak)]);
      const fl = acct.max_drawdown && isEval ? Math.min(peak - acct.max_drawdown, start) : null;
      if (fl !== null) floorPts.push([x(i), y(fl)]);
      info.push({ i: c.i, t: c.t, eq: c.eq, dd: peak - c.eq, floor: fl, prev: i ? curve[i - 1].eq : start });
    });
    const pts = curve.map((c, i) => [x(i), y(c.eq)]);
    const area = `${path(pts)}L${x(curve.length - 1)},${y(lo)}L${x(0)},${y(lo)}Z`;
    const up = eq[eq.length - 1] >= start;
    let extra = "";
    if (target) extra += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(target)}" y2="${y(target)}" class="tgt"/><text x="${W - PAD.r - 4}" y="${y(target) - 4}" class="t" text-anchor="end" fill="#2be07e">TARGET ${money(target)}</text>`;
    if (floorPts.length) extra += `<path d="${path(floorPts)}" class="flr"/>`;
    extra += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(start)}" y2="${y(start)}" class="stt"/>`;
    D.charts = { ...(D.charts || {}), eqx: { info, x, y, n: curve.length, target, start, up } };
    return `<svg viewBox="0 0 ${W} ${H}" class="acd-svg acd-int" id="acd-eq"><g class="reveal">${svg}${extra}<path d="${area}" class="ar ${up ? "up" : "dn"}"/><path d="${path(peakPts)}" class="pk"/><path d="${path(pts)}" class="ln ${up ? "up" : "dn"}"/></g>${crosshair("xh-eq")}</svg>
      <div class="acd-legend"><i class="ln"></i>equity <i class="pk"></i>peak ${floorPts.length ? '<i class="flr"></i>drawdown floor ' : ""}${target ? '<i class="tgt"></i>target ' : ""}<i class="stt"></i>start <span class="acd-hint">hover for detail · click a point to open that trade</span></div>`;
  }

  function fanChart(mc) {
    const f = mc.fan, n = mc.fan_points;
    const all = [...f.p5, ...f.p95, mc.start + mc.target, Math.min(mc.start - mc.max_drawdown, mc.start)];
    let lo = Math.min(...all), hi = Math.max(...all); const pv = (hi - lo) * 0.06; lo -= pv; hi += pv;
    const { y, svg } = axisY(lo, hi);
    const x = scale(0, n - 1, PAD.l, W - PAD.r);
    const line = (a) => a.map((v, i) => [x(i), y(v)]);
    const band = (a, b) => `${path(line(a))}L${line(b).reverse().map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L")}Z`;
    const tgt = mc.start + mc.target, flr = mc.start - mc.max_drawdown;
    let ticks = "";
    [0, 25, 50, 75, 100].filter((k) => k < n).forEach((k) => { ticks += `<text x="${x(k)}" y="${H - 6}" class="t" text-anchor="middle">${k}</text>`; });
    D.charts = { ...(D.charts || {}), fan: { f, n, x, y, tgt, flr } };
    return `<svg viewBox="0 0 ${W} ${H}" class="acd-svg acd-int" id="acd-fan"><g class="reveal">${svg}
      <path d="${band(f.p5, f.p95)}" class="b1"/><path d="${band(f.p25, f.p75)}" class="b2"/>
      <line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(tgt)}" y2="${y(tgt)}" class="tgt"/><text x="${W - PAD.r - 4}" y="${y(tgt) - 4}" class="t" text-anchor="end" fill="#2be07e">PASS ${money(tgt)}</text>
      <line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(flr)}" y2="${y(flr)}" class="flr2"/><text x="${W - PAD.r - 4}" y="${y(flr) + 12}" class="t" text-anchor="end" fill="#ff4a4a">FLOOR ${money(flr)}</text>
      <path d="${path(line(f.p50))}" class="ln up"/>${ticks}</g>${crosshair("xh-fan")}</svg>
      <div class="acd-legend"><i class="b1"></i>5–95% of paths <i class="b2"></i>25–75% <i class="ln"></i>median — x axis: trades from now <span class="acd-hint">hover to read any point</span></div>`;
  }

  function bars(rows, { fmtKey = (k) => k, h = 150 } = {}) {
    if (!rows.length) return `<div class="acd-empty">No data.</div>`;
    const mx = Math.max(...rows.map((r) => Math.abs(r.pnl)), 1);
    const bw = Math.max(14, Math.min(48, Math.floor(700 / rows.length) - 6));
    const mid = h / 2;
    const cells = rows.map((r, i) => {
      const ht = (Math.abs(r.pnl) / mx) * (mid - 14), thin = r.n < 5;
      const yy = r.pnl >= 0 ? mid - ht : mid;
      const tip = tipAttr(`${fmtKey(r.key)}`, `${smoney(r.pnl)} net`, `${r.n} trades · win ${r.win_rate}%`, `PF ${r.pf ?? "—"} · avg ${smoney(r.avg)}`, thin ? "fewer than 5 trades — don't trust it" : "");
      return `<g class="bar ${thin ? "thin" : ""}" ${tip}><rect class="bg" x="${i * (bw + 6) + 2}" y="0" width="${bw + 8}" height="${h}"/><rect x="${i * (bw + 6) + 6}" y="${yy}" width="${bw}" height="${Math.max(1, ht)}" class="b ${r.pnl >= 0 ? "pos" : "neg"}"/>
        <text x="${i * (bw + 6) + 6 + bw / 2}" y="${h - 2}" class="t" text-anchor="middle">${esc(fmtKey(r.key))}</text><text x="${i * (bw + 6) + 6 + bw / 2}" y="${h - 12}" class="t n" text-anchor="middle">${r.n}</text></g>`;
    }).join("");
    return `<svg viewBox="0 0 ${rows.length * (bw + 6) + 12} ${h}" class="acd-svg bars" style="max-height:${h + 40}px"><line x1="0" x2="100%" y1="${mid}" y2="${mid}" class="g"/>${cells}</svg>`;
  }

  function histogram(hs, n) {
    if (!hs.counts.length) return `<div class="acd-empty">Needs 3+ trades.</div>`;
    const mx = Math.max(...hs.counts), w = 720 / hs.counts.length;
    const zero = hs.edges[0] < 0 && hs.edges[hs.edges.length - 1] > 0 ? ((0 - hs.edges[0]) / (hs.edges[hs.edges.length - 1] - hs.edges[0])) * 720 : null;
    const r = hs.counts.map((c, i) => { const h = (c / mx) * 100; const mid = (hs.edges[i] + hs.edges[i + 1]) / 2;
      return `<g class="bar" ${tipAttr(`${money(hs.edges[i])} … ${money(hs.edges[i + 1])}`, `${c} trades (${n ? ((100 * c) / n).toFixed(1) : 0}%)`)}><rect class="bg" x="${i * w}" y="0" width="${w}" height="112"/><rect class="b ${mid >= 0 ? "pos" : "neg"}" x="${i * w + 2}" y="${110 - h}" width="${w - 3}" height="${h}"/></g>`; }).join("");
    return `<svg viewBox="0 0 724 130" class="acd-svg bars" style="max-height:170px">${r}${zero !== null ? `<line x1="${zero + 2}" x2="${zero + 2}" y1="0" y2="112" class="stt"/>` : ""}
      <text x="2" y="126" class="t">${money(hs.edges[0])}</text><text x="722" y="126" class="t" text-anchor="end">${money(hs.edges[hs.edges.length - 1])}</text></svg>`;
  }

  function calendar(days) {
    if (!days.length) return `<div class="acd-empty">No trading days yet.</div>`;
    const mx = Math.max(...days.map((d) => Math.abs(d.pnl)), 1);
    return `<div class="acd-cal">${days.map((d) => { const a = Math.min(1, Math.abs(d.pnl) / mx); return `<span ${tipAttr(d.date, smoney(d.pnl) + " that day")} style="background:${d.pnl >= 0 ? `rgba(43,224,126,${0.18 + a * 0.8})` : `rgba(255,74,74,${0.18 + a * 0.8})`}"></span>`; }).join("")}</div>`;
  }

  // ------------------------------------------------------------------ building blocks
  const kpi = (l, v, s, c = "", tip = "") => `<div class="bb-kpi acd-k" ${tip ? tipAttr(l, tip) : ""}><span class="l">${l}</span><span class="v ${c}">${v}</span>${s ? `<span class="s">${s}</span>` : ""}</div>`;
  const stat = (l, v, tip = "") => `<div class="acd-st" ${tip ? tipAttr(l, tip) : ""}><span>${l}</span><b>${v}</b></div>`;
  const card = (title, body, extra = "") => `<section class="acd-card ${extra}"><h4>${title}</h4>${body}</section>`;

  function table(rows, cols) {
    if (!rows.length) return `<div class="acd-empty">No data.</div>`;
    return `<table class="acd-tbl"><thead><tr>${cols.map((c) => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td class="${c[2] ? c[2](r) : ""}">${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }
  const bucketCols = (k) => [[k, (r) => esc(r.key)], ["N", (r) => r.n], ["P&L", (r) => smoney(r.pnl), (r) => cls(r.pnl)], ["AVG", (r) => smoney(r.avg), (r) => cls(r.avg)], ["WIN%", (r) => r.win_rate], ["PF", (r) => r.pf ?? "—"]];

  // ------------------------------------------------------------------ tabs
  function tabOverview(d) {
    const p = d.perf, a = d.account, live = a.status === "live";
    const op = d.open, openPnl = op && isN(op.pnl) ? op.pnl : 0;
    const realized = a.balance - a.starting_balance, net = realized + openPnl;
    const prog = a.account_type === "apex_eval" && a.profit_target ? Math.max(0, Math.min(100, (realized / a.profit_target) * 100)) : null;
    const verdict = `<span class="acd-verdict ${d.verdict.code}">${esc(d.verdict.text)}</span>`;
    const findings = d.findings.map((f) => `<div class="acd-f ${f.level}"><b>${esc(f.title)}</b><span>${esc(f.text)}</span></div>`).join("");
    return `<div class="acd-kpis">
        ${kpi("EQUITY", money(a.balance + openPnl), `balance ${money(a.balance)}${op ? " + open" : ""}`, "", "balance plus the open trade marked at the last price")}
        ${kpi("NET P&L", smoney(net), `${num((net / a.starting_balance) * 100)}% · realized ${smoney(realized)}`, cls(net), "realized (closed trades) + open trade, after commission")}
        ${op ? kpi("OPEN TRADE", smoney(op.pnl), `${esc(String(op.side).toUpperCase())} ×${op.qty} @ ${op.entry_price} · last ${op.last ?? "—"}`, cls(op.pnl), `stop ${op.stop ?? "—"} · target ${op.target ?? "—"}|price as of ${esc(String(op.last_time || "").slice(11, 16))} NY`) : ""}
        ${kpi("WIN RATE", isN(p.win_rate) ? p.win_rate + "%" : "—", `${p.n || 0} trades`, "", "share of trades that closed in profit")}
        ${kpi("PROFIT FACTOR", num(p.profit_factor), `payoff ${num(p.payoff)}`, "", "gross profit ÷ gross loss (>1 = profitable)|payoff = average win ÷ average loss")}
        ${kpi("EXPECTANCY", smoney(p.expectancy), "per trade", cls(p.expectancy), "what an average trade is worth")}
        ${kpi("MAX DRAWDOWN", money(p.max_drawdown), isN(p.max_drawdown_pct) ? p.max_drawdown_pct + "% of peak" : "", p.max_drawdown ? "dn" : "", "largest peak-to-trough fall of the equity curve")}
        ${kpi("SHARPE", num(p.sharpe), `sortino ${num(p.sortino)}`, "", "daily return per unit of risk, annualised|sortino only counts downside")}
        ${kpi("STATE", live ? "LIVE" : "IDLE", `sample confidence: ${d.confidence}`, "", "confidence grows with trade count")}
      </div>
      ${prog !== null ? `<div class="acd-evalbar"><span>EVAL PROGRESS ${num(prog, 1)}% of ${money(a.profit_target)}</span><div class="ac-bar"><i style="width:${prog}%"></i></div>${a.drawdown_floor != null ? `<span>DRAWDOWN BUFFER ${money(a.balance - a.drawdown_floor)} of ${money(a.max_drawdown)} · floor ${money(a.drawdown_floor)}</span>` : ""}${a.eval_fail_reason ? `<span class="dn">${esc(a.eval_fail_reason)}</span>` : ""}</div>` : ""}
      <div class="acd-cols">
        <div class="acd-main">
          ${card(`EQUITY CURVE ${verdict}`, equityChart(d.curve, a))}
          ${card("WHAT THE DATA SAYS", `<div class="acd-fs">${findings}</div>`)}
          ${card("TRADE P&L DISTRIBUTION", histogram(d.histogram, p.n))}
        </div>
        <div class="acd-side">
          ${card("PERFORMANCE", `<div class="acd-sg">${stat("Trades", p.n)}${stat("Net", smoney(p.net))}${stat("Avg win", smoney(p.avg_win))}${stat("Avg loss", smoney(p.avg_loss))}${stat("Best / worst", `${smoney(p.best)} / ${smoney(p.worst)}`)}${stat("Break-even win%", isN(p.breakeven_win_rate) ? p.breakeven_win_rate + "%" : "—", "win rate needed to break even at this payoff")}${stat("Avg R", isN(p.avg_r) ? num(p.avg_r) + "R" : "—", "average P&L in multiples of the risk taken")}${stat("Trades ≥ 1R", isN(p.r_ge_1_pct) ? p.r_ge_1_pct + "%" : "—", "share of trades that made at least what they risked")}${stat("Commission", money(p.commission), isN(p.commission_pct_of_gross) ? p.commission_pct_of_gross + "% of gross profit" : "total fees paid")}${stat("Avg hold", isN(p.avg_hold_min) ? num(p.avg_hold_min, 0) + " min" : "—", "average time in a trade")}</div>`)}
          ${card("RISK", `<div class="acd-sg">${stat("Max drawdown", money(p.max_drawdown))}${stat("Drawdown now", money(p.current_drawdown), "distance below the equity peak")}${stat("Longest drawdown", `${p.longest_drawdown_trades ?? 0} trades`, "most trades spent below a prior peak")}${stat("Recovery factor", num(p.recovery_factor), "net profit ÷ max drawdown")}${stat("Max win streak", p.streaks?.max_win ?? "—")}${stat("Max loss streak", p.streaks?.max_loss ?? "—")}${stat("Now", p.streaks?.current > 0 ? `${p.streaks.current}W` : p.streaks?.current < 0 ? `${-p.streaks.current}L` : "—", "current streak")}${stat("Std / trade", money(p.std), "how much a single trade's P&L varies")}</div>`)}
          ${card("DAYS", `<div class="acd-sg">${stat("Trading days", p.trading_days ?? 0)}${stat("Trades / day", num(p.trades_per_day))}${stat("Green / red", `${p.green_days ?? 0} / ${p.red_days ?? 0}`)}${stat("Best day", smoney(p.best_day))}${stat("Worst day", smoney(p.worst_day))}</div>`)}
        </div>
      </div>`;
  }

  function scenario(s, cur) {
    if (!s) return "";
    const dp = s.pass - cur.pass, db = s.breach - cur.breach;
    const f = (v, goodWhenUp) => `<b class="${v === 0 ? "" : (v > 0) === goodWhenUp ? "up" : "dn"}">${v > 0 ? "+" : ""}${v.toFixed(1)} pts</b>`;
    return `<div class="acd-scn"><b>${s.scale}× SIZE</b> — pass ${s.pass}% (${f(dp, true)} vs now), breach ${s.breach}% (${f(db, false)} vs now), neither ${s.unresolved}%${isN(s.median_trades) ? `, typical pass in ${Math.round(s.median_trades)} trades` : ""}.</div>`;
  }

  function tabMC(d) {
    const mc = d.monte_carlo;
    if (!mc) return card("MONTE CARLO", `<div class="acd-empty">Needs at least 5 closed trades (this account or its strategy's pooled trades).</div>`);
    const total = mc.pass_pct + mc.breach_pct + mc.unresolved_pct || 100;
    const warn = mc.sample.source !== "own" || mc.confidence === "low" || mc.confidence === "very low"
      ? `<div class="acd-banner warn">Confidence: <b>${esc(mc.confidence)}</b> — resampling ${mc.sample.n} trades (${esc(mc.sample.label)}). ${mc.sample.source !== "own" ? "This account has too few trades of its own." : ""}</div>` : `<div class="acd-banner ok">Confidence: <b>${esc(mc.confidence)}</b> — ${mc.sample.n} trades resampled.</div>`;
    const sweep = mc.sweep.map((s) => `<tr data-scale="${s.scale}" class="${s.scale === mc.best_scale ? "best" : ""} ${s.scale === 1 ? "cur" : ""}"><td>${s.scale}×${s.scale === 1 ? " (now)" : ""}${s.scale === mc.best_scale ? " ★" : ""}</td>
      <td><div class="acd-mini"><i class="p" style="width:${s.pass}%"></i></div>${s.pass}%</td><td><div class="acd-mini"><i class="b" style="width:${s.breach}%"></i></div>${s.breach}%</td><td>${s.unresolved}%</td><td>${isN(s.median_trades) ? Math.round(s.median_trades) : "—"}</td></tr>`).join("");
    return `${warn}
      ${mc.hypothetical ? `<div class="acd-banner info">This is not an evaluation account — the odds use a hypothetical ${money(mc.target)} target / ${money(mc.max_drawdown)} trailing drawdown for its size.</div>` : ""}
      <div class="acd-kpis mc">
        ${kpi("PASS CHANCE", mc.pass_pct + "%", `reach +${money(mc.target)}`, mc.pass_pct >= 50 ? "up" : "", "share of simulated futures that hit the profit target before the drawdown floor")}
        ${kpi("BREACH CHANCE", mc.breach_pct + "%", `hit the ${money(mc.max_drawdown)} trailing drawdown`, mc.breach_pct > mc.pass_pct ? "dn" : "", "share that touched the trailing floor first — account blown")}
        ${kpi("NEITHER", mc.unresolved_pct + "%", `within ${mc.horizon} trades`, "", "still between floor and target after the whole horizon")}
        ${kpi("TYPICAL PASS", isN(mc.median_trades_to_pass) ? `${Math.round(mc.median_trades_to_pass)} trades` : "—", isN(mc.median_days_to_pass) ? `~${mc.median_days_to_pass} trading days (10–90%: ${mc.p10_trades_to_pass ?? "—"}–${mc.p90_trades_to_pass ?? "—"})` : "", "", "median number of trades among the paths that passed")}
        ${kpi("EDGE REAL?", mc.edge_probability + "%", `mean/trade ${smoney(mc.mean_ci[0], 1)} … ${smoney(mc.mean_ci[1], 1)} (90%)`, mc.edge_probability >= 90 ? "up" : mc.edge_probability <= 30 ? "dn" : "", "in this share of bootstrap resamples the average trade is positive")}
      </div>
      <div class="acd-odds"><i class="p" style="width:${(100 * mc.pass_pct) / total}%" ${tipAttr("PASS", mc.pass_pct + "% of simulated futures")}>PASS ${mc.pass_pct}%</i><i class="u" style="width:${(100 * mc.unresolved_pct) / total}%" ${tipAttr("NEITHER", mc.unresolved_pct + "% unresolved")}></i><i class="b" style="width:${(100 * mc.breach_pct) / total}%" ${tipAttr("BREACH", mc.breach_pct + "% blew the drawdown")}>BREACH ${mc.breach_pct}%</i></div>
      <div class="acd-cols">
        <div class="acd-main">${card(`${mc.paths.toLocaleString()} SIMULATED FUTURES · NEXT 100 TRADES`, fanChart(mc))}</div>
        <div class="acd-side">
          ${card("IF THE NEXT 100 TRADES GO…", `<div class="acd-sg">${stat("Bad (5%)", money(mc.end100.p5), "only 5% of futures end lower")}${stat("Median", money(mc.end100.p50))}${stat("Good (95%)", money(mc.end100.p95), "only 5% of futures end higher")}${stat("Typical worst dip", money(mc.dd100.p50), "median of each path's max drawdown")}${stat("1-in-10 dip", money(mc.dd100.p90), "a dip this deep or worse happens in 1 of 10 futures")}${stat("1-in-100 dip", money(mc.dd100.p99))}</div>`)}
          <div class="acd-actions"><span>SIMULATIONS</span>${[2000, 4000, 10000].map((n) => `<button class="bb-chip ${D.paths === n ? "on" : ""}" data-paths="${n}">${n / 1000}k</button>`).join("")}</div>
        </div>
      </div>
      ${card("POSITION-SIZE SWEEP — click a size for the what-if", `<table class="acd-tbl sweep"><thead><tr><th>SIZE</th><th>PASS</th><th>BREACH</th><th>NEITHER</th><th>MEDIAN TRADES TO PASS</th></tr></thead><tbody>${sweep}</tbody></table>
        <div id="acd-scn">${scenario(mc.sweep.find((s) => s.scale === mc.best_scale), mc.sweep.find((s) => s.scale === 1))}</div>
        <p class="acd-note">★ = best pass-minus-breach. Sizing is the strongest lever an evaluation has: it changes how fast the target arrives <i>and</i> how deep a losing run can go, without touching the strategy.</p>`)}
      ${card("HOW TO READ THIS", `<p class="acd-note">The simulator draws trades <b>at random with replacement</b> from the sample above, replays them through the Apex rules (profit target; trailing drawdown that follows the equity peak and locks at the starting balance) and repeats ${mc.paths.toLocaleString()} times. It assumes future trades look like the past and are independent — so a regime change, a streaky strategy or a tiny sample makes the true odds worse than shown. Intra-trade excursions are not modelled (closed-trade granularity), so real drawdown checks are slightly harsher.</p>`)}`;
  }

  function tabBreakdown(d) {
    const b = d.breakdown;
    return `<div class="acd-grid2">
      ${card("P&L BY ENTRY HOUR (NY time) — small numbers = trade count", bars(b.hour, { fmtKey: (k) => k + "h" }))}
      ${card("P&L BY WEEKDAY", bars(b.weekday))}
      ${card("LONG vs SHORT", table(b.side, bucketCols("SIDE")))}
      ${card("EXIT REASON", table(b.exit_reason, bucketCols("EXIT")))}
      ${card("MONTHLY", table(b.month, bucketCols("MONTH")))}
      ${card("DAILY P&L — last 90 days", calendar(b.daily))}
    </div><p class="acd-note">Buckets with fewer than 5 trades are drawn faded — do not filter on them.</p>`;
  }

  async function loadTrades() {
    if (D.trades) return D.trades;
    const r = await api(`/accounts/${D.id}/trades`);
    D.trades = (r.trades || []).map((t, i) => ({ ...t, i: i + 1, r: t.risk_dollars ? t.pnl / t.risk_dollars : null }));
    return D.trades;
  }
  const TCOLS = [["#", "i", (t) => t.i], ["EXIT (NY)", "exit_time", (t) => esc(String(t.exit_time || "").replace("T", " ").slice(0, 16))], ["SIDE", "side", (t) => esc(t.side), (t) => (t.side === "long" ? "up" : "dn")],
    ["QTY", "qty", (t) => (t.qty ?? "—") + (t.contract === "micro" ? "µ" : "")], ["ENTRY", "entry_price", (t) => num(t.entry_price)], ["EXIT", "exit_price", (t) => num(t.exit_price)],
    ["P&L", "pnl", (t) => smoney(t.pnl), (t) => cls(t.pnl)], ["R", "r", (t) => (isN(t.r) ? num(t.r, 2) : "—"), (t) => cls(t.pnl)], ["REASON", "exit_reason", (t) => esc(t.exit_reason)], ["SRC", "source", (t) => esc(t.source)]];
  function filteredTrades() {
    let rows = (D.trades || []).slice();
    const q = D.tquery.trim().toLowerCase();
    if (D.tfilter === "win") rows = rows.filter((t) => t.pnl > 0); else if (D.tfilter === "loss") rows = rows.filter((t) => t.pnl < 0);
    else if (D.tfilter === "long" || D.tfilter === "short") rows = rows.filter((t) => t.side === D.tfilter);
    if (q) rows = rows.filter((t) => `${t.exit_reason} ${t.exit_time} ${t.side} ${t.source}`.toLowerCase().includes(q));
    const k = D.sort.key, dir = D.sort.dir;
    rows.sort((a, b) => { const x = a[k] ?? -Infinity, y = b[k] ?? -Infinity; return (x < y ? -1 : x > y ? 1 : 0) * dir; });
    return rows;
  }
  function tradeRows() {
    const rows = filteredTrades();
    const tot = rows.reduce((s, t) => s + t.pnl, 0);
    const head = TCOLS.map((c) => `<th data-sort="${c[1]}" class="${D.sort.key === c[1] ? "srt" : ""}">${c[0]}${D.sort.key === c[1] ? (D.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
    const body = rows.slice(0, 400).map((t) => `<tr data-i="${t.i}" class="${D.hilite === t.i ? "hl" : ""}">${TCOLS.map((c) => `<td class="${c[3] ? c[3](t) : ""}">${c[2](t)}</td>`).join("")}</tr>`).join("");
    return { html: `<table class="acd-tbl trades"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`, summary: `${rows.length} trades · ${smoney(tot)}` };
  }
  function tabTrades() {
    const chips = [["all", "ALL"], ["win", "WINS"], ["loss", "LOSSES"], ["long", "LONG"], ["short", "SHORT"]].map(([k, t]) => `<button class="bb-chip ${D.tfilter === k ? "on" : ""}" data-tf="${k}">${t}</button>`).join("");
    const tr = tradeRows();
    return `<div class="acd-actions">${chips}<input id="acd-tq" class="acd-search" placeholder="search reason / date…" value="${esc(D.tquery)}"/><span class="sp"></span><span id="acd-tsum">${tr.summary}</span><button class="bb-chip" id="acd-csv">EXPORT CSV</button></div><div class="acd-tw" id="acd-trows">${tr.html}</div>`;
  }
  function refreshTradeRows() {
    const tr = tradeRows(), w = $("#acd-trows"); if (!w) return;
    w.innerHTML = tr.html; const s = $("#acd-tsum"); if (s) s.textContent = tr.summary;
    const hl = $("#acd-trows tr.hl"); if (hl) hl.scrollIntoView({ block: "center" });
  }

  function tabControls(d) {
    const a = d.account, live = a.status === "live";
    const finished = a.account_type === "apex_eval" && ["passed", "failed"].includes(a.eval_status);
    const strategies = (typeof state !== "undefined" && state.strategies) || [];
    return `<div class="acd-grid2">
      ${card("STATUS", `<div class="acd-sg">${stat("Type", esc(a.account_type))}${stat("Symbol", esc(a.symbol || "—"))}${stat("Strategy", esc((a.strategy || "none").replace(/_/g, " ")))}${stat("Sizing", esc(a.sizing_mode || "—"))}${stat("Risk / trade", a.risk_per_trade ? money(a.risk_per_trade) : "—")}${stat("Daily loss limit", a.daily_loss_limit ? money(a.daily_loss_limit) : "none")}${stat("Created", esc(String(a.created_at || "").slice(0, 10)))}</div>`)}
      ${card("ACTIONS", `<div class="acd-btns">
          ${!a.strategy ? `<div class="form-row"><label>Symbol</label><select id="acd-sym"><option>NQ</option><option>ES</option></select></div><div class="form-row"><label>Strategy</label><select id="acd-strat">${strategies.map((s) => `<option>${esc(s.name)}</option>`).join("")}</select></div><button class="btn" data-act="assign">ASSIGN STRATEGY</button>`
            : live ? `<button class="btn" data-act="stop">STOP LIVE</button>`
            : finished ? "" : `<button class="btn" data-act="go">⚡ GO LIVE</button>`}
          ${a.traderspost_linked
            ? `<div class="acd-note" style="color:#ff5a5a">Simulate / Reset are disabled while linked to TradersPost — they would wipe this account's real trade history. Unlink first if you need either.</div>`
            : `<button class="btn" data-act="sim">▶ SIMULATE THIS ACCOUNT (replay recent history)</button>
               <button class="btn" data-act="reset">${finished ? "RESET & TRY AGAIN" : "RESET ACCOUNT"}</button>`}
          <button class="btn btn-small" data-act="legacy">ADVANCED (price chart · calendar · backtest eval)</button></div>
        <div class="acd-note" id="acd-actmsg"></div>`)}
      ${a.is_system ? "" : card("TRADERSPOST REAL EXECUTION", tpControlsBody(a))}
    </div>`;
  }

  // Real-order link: fires a TradersPost webhook (-> the real broker) only for the one
  // account explicitly linked here. traderspost_linked/tradovate_account_name come straight
  // off the account row (GET /api/accounts/{id}), same as every other field on this tab.
  function tpControlsBody(a) {
    const payoutRow = a.awaiting_payout
      ? `<div class="acd-note" style="color:#ffd24d;font-weight:700">💰 WAITING FOR PAYMENT — stopped trading at its ${money(a.payout_target)} target</div>
         <button class="btn btn-small" data-act="payout-clear">CLEAR TARGET &amp; RESUME (you'll need GO LIVE again)</button>`
      : `<div class="form-row"><label>Stop trading at equity</label><input id="acd-payout" type="number" step="0.01" placeholder="e.g. 26500" value="${a.payout_target ?? ""}"/></div>
         <button class="btn btn-small" data-act="payout-set">SET PAYOUT TARGET</button>`;
    if (a.traderspost_linked) {
      return `<div class="acd-btns">
        <div class="acd-note" style="color:#ff5a5a;font-weight:700">🔴 LINKED — sends real orders${a.tradovate_account_name ? ` (${esc(a.tradovate_account_name)})` : ""}</div>
        <div class="acd-note" id="acd-tp-data">Loading real balance…</div>
        <button class="btn" data-act="tp-test-long">🧪 SEND TEST LONG (TP $100 / SL $50)</button>
        <button class="btn" data-act="tp-unlink">UNLINK (STOP SENDING REAL ORDERS)</button>
        <div style="height:10px"></div>
        ${payoutRow}
      </div>`;
    }
    if (!a.strategy || !a.symbol) return `<div class="acd-note">Assign a strategy before linking this account to TradersPost.</div>`;
    return `<div class="acd-btns">
      <div class="acd-note">Not linked — this account's signals stay local simulation only.</div>
      <button class="btn" data-act="tp-link">⚠ LINK TO TRADERSPOST (REAL MONEY)</button>
    </div>`;
  }

  async function loadTpData(id) {
    const host = $("#acd-tp-data");
    if (!host) return;
    try {
      const real = await api(`/nostro/account/${id}/traderspost/real`);
      if (!$("#acd-tp-data")) return; // tab may have changed while this was in flight
      if (!real.webhook_configured) {
        $("#acd-tp-data").innerHTML = `<span style="color:#ff5a5a">TRADERSPOST_WEBHOOK_URL is not set in run.bat — this account will not actually send orders.</span>`;
      } else if (real.balance) {
        const b = real.balance;
        $("#acd-tp-data").textContent = `Real cash balance: ${money(b.cash_balance, 2)} · day P&L ${smoney(b.day_pnl, 2)} · ${(real.fills || []).length} recent fill(s)`;
      } else if (real.error) {
        $("#acd-tp-data").innerHTML = `<span style="color:#ff5a5a">${esc(real.error)}</span>`;
      } else {
        $("#acd-tp-data").textContent = "Webhook is configured and will send real orders. No Tradovate sub-account linked for balance display — check TradersPost's own Broker page for the real balance.";
      }
    } catch (e) { /* the real-data panel is a nice-to-have */ }
  }

  // ------------------------------------------------------------------ interactivity
  let tipEl = null;
  function tip(html, x, y) {
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "acd-tip"; document.body.appendChild(tipEl); }
    tipEl.innerHTML = html; tipEl.style.display = "block";
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.max(6, Math.min(innerWidth - w - 6, x + 14)) + "px";
    tipEl.style.top = Math.max(6, Math.min(innerHeight - h - 6, y + 14)) + "px";
  }
  const hideTip = () => { if (tipEl) tipEl.style.display = "none"; };
  const tipHtml = (raw) => raw.split("|").map((l, i) => (i === 0 ? `<b>${esc(l)}</b>` : `<span>${esc(l)}</span>`)).join("");

  function svgPoint(svg, e) { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); }

  function onChartMove(e) {
    const hit = e.target.closest?.(".hit"); if (!hit) return;
    const svg = hit.ownerSVGElement, p = svgPoint(svg, e), id = hit.dataset.chart, g = document.getElementById(id);
    if (id === "xh-eq" && D.charts?.eqx) {
      const c = D.charts.eqx, i = Math.max(0, Math.min(c.n - 1, Math.round(((p.x - PAD.l) / (W - PAD.l - PAD.r)) * (c.n - 1)))), d = c.info[i];
      g.style.display = ""; g.querySelector(".xl").setAttribute("x1", c.x(i)); g.querySelector(".xl").setAttribute("x2", c.x(i));
      const dot = g.querySelector(".xd"); dot.setAttribute("cx", c.x(i)); dot.setAttribute("cy", c.y(d.eq)); dot.setAttribute("class", "xd " + (d.eq >= d.prev ? "up" : "dn"));
      const pnl = d.eq - d.prev;
      tip(`<b>${i === 0 ? "START" : "TRADE #" + d.i}</b>${d.t ? `<span>${esc(String(d.t).replace("T", " ").slice(0, 16))}</span>` : ""}<span>equity <em>${money(d.eq)}</em></span>${i ? `<span>this trade <em class="${cls(pnl)}">${smoney(pnl)}</em></span>` : ""}<span>below peak <em>${money(d.dd)}</em></span>${d.floor !== null ? `<span>to floor <em>${money(d.eq - d.floor)}</em></span>` : ""}${c.target ? `<span>to target <em>${money(c.target - d.eq)}</em></span>` : ""}`, e.clientX, e.clientY);
    } else if (id === "xh-fan" && D.charts?.fan) {
      const c = D.charts.fan, i = Math.max(0, Math.min(c.n - 1, Math.round(((p.x - PAD.l) / (W - PAD.l - PAD.r)) * (c.n - 1))));
      g.style.display = ""; g.querySelector(".xl").setAttribute("x1", c.x(i)); g.querySelector(".xl").setAttribute("x2", c.x(i));
      const dot = g.querySelector(".xd"); dot.setAttribute("cx", c.x(i)); dot.setAttribute("cy", c.y(c.f.p50[i])); dot.setAttribute("class", "xd up");
      tip(`<b>AFTER ${i + 1} TRADES</b><span>good luck (95%) <em class="up">${money(c.f.p95[i])}</em></span><span>upper (75%) <em>${money(c.f.p75[i])}</em></span><span>median <em>${money(c.f.p50[i])}</em></span><span>lower (25%) <em>${money(c.f.p25[i])}</em></span><span>bad luck (5%) <em class="dn">${money(c.f.p5[i])}</em></span>`, e.clientX, e.clientY);
    }
  }

  function countUp(root) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.querySelectorAll(".acd-k .v").forEach((el) => {
      const txt = el.textContent, m = txt.match(/-?[\d,]*\.?\d+/);
      if (!m) return;
      const target = parseFloat(m[0].replace(/,/g, "")), dec = (m[0].split(".")[1] || "").length, comma = m[0].includes(",");
      if (!isFinite(target) || target === 0) return;
      const t0 = performance.now(), dur = 650;
      const step = (now) => {
        const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3), v = target * e;
        const s = comma || Math.abs(v) >= 1000 ? Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) : Math.abs(v).toFixed(dec);
        el.textContent = txt.replace(m[0], (target < 0 ? "-" : "") + s);
        if (k < 1) requestAnimationFrame(step); else el.textContent = txt;
      };
      requestAnimationFrame(step);
    });
  }

  function wire() {
    const body = $("#acd-body");
    countUp(body);
    if (D.tab === "tr") {
      body.querySelector("#acd-tq")?.addEventListener("input", (e) => { D.tquery = e.target.value; refreshTradeRows(); });
    }
  }

  function onBodyMove(e) {
    if (e.target.closest?.(".hit")) return onChartMove(e);
    const t = e.target.closest?.("[data-tip]");
    if (t) tip(tipHtml(t.dataset.tip), e.clientX, e.clientY); else hideTip();
  }
  function onBodyLeave() { hideTip(); document.querySelectorAll(".xh").forEach((g) => (g.style.display = "none")); }

  // touch parity for the crosshair/tooltip above: a touchmove doesn't retarget like mousemove does when the
  // finger crosses onto a different element, so the element under the finger is re-resolved on every move.
  function onBodyTouch(e) {
    const t = e.touches[0]; if (!t) return;
    const el = document.elementFromPoint(t.clientX, t.clientY);
    if (el?.closest?.(".hit") || el?.closest?.("[data-tip]")) e.preventDefault(); // only steal the gesture from page-scroll while tracing a chart/tip target
    onBodyMove({ clientX: t.clientX, clientY: t.clientY, target: el });
  }

  // ------------------------------------------------------------------ frame
  function ensureDom() {
    if ($("#acd-back")) return;
    const el = document.createElement("div");
    el.id = "acd-back"; el.className = "acd-back hidden";
    el.innerHTML = `<div class="acd" role="dialog" aria-modal="true"><header class="acd-h"><div id="acd-title"></div><span class="sp"></span><span class="acd-keys">1-5 tabs · ← → · esc</span><button class="btn btn-small" id="acd-x">✕ CLOSE</button></header>
      <nav class="acd-tabs" id="acd-tabs">${TABS.map(([k, t], i) => `<button data-t="${k}"><i>${i + 1}</i>${t}</button>`).join("")}</nav><div class="acd-body" id="acd-body"></div></div>`;
    document.body.appendChild(el);
    el.addEventListener("mousedown", (e) => { if (e.target === el) close(); });
    $("#acd-x").addEventListener("click", close);
    $("#acd-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (b) setTab(b.dataset.t); });
    document.addEventListener("keydown", (e) => {
      if (el.classList.contains("hidden")) return;
      if (e.key === "Escape") return close();
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
      const keys = TABS.map((t) => t[0]), i = keys.indexOf(D.tab);
      if (/^[1-5]$/.test(e.key)) setTab(keys[Number(e.key) - 1]);
      else if (e.key === "ArrowRight") setTab(keys[(i + 1) % keys.length]);
      else if (e.key === "ArrowLeft") setTab(keys[(i + keys.length - 1) % keys.length]);
    });
    const body = $("#acd-body");
    body.addEventListener("click", onBody);
    body.addEventListener("mousemove", onBodyMove);
    body.addEventListener("mouseleave", onBodyLeave);
    body.addEventListener("mouseout", (e) => { if (!e.relatedTarget || !body.contains(e.relatedTarget)) onBodyLeave(); });
    body.addEventListener("touchstart", onBodyTouch, { passive: false });
    body.addEventListener("touchmove", onBodyTouch, { passive: false });
    body.addEventListener("touchend", onBodyLeave);
  }
  function setTab(t) { D.tab = t; hideTip(); render(); }
  function close() { $("#acd-back")?.classList.add("hidden"); document.body.classList.remove("acd-open"); hideTip(); D.id = null; }

  async function onBody(e) {
    const chip = e.target.closest("[data-paths]");
    if (chip) { D.paths = Number(chip.dataset.paths); await load(true); return; }
    if (e.target.id === "acd-csv") return exportCsv();
    const tf = e.target.closest("[data-tf]");
    if (tf) { D.tfilter = tf.dataset.tf; document.querySelectorAll("[data-tf]").forEach((b) => b.classList.toggle("on", b === tf)); return refreshTradeRows(); }
    const th = e.target.closest("th[data-sort]");
    if (th) { const k = th.dataset.sort; D.sort = D.sort.key === k ? { key: k, dir: -D.sort.dir } : { key: k, dir: -1 }; return refreshTradeRows(); }
    const sw = e.target.closest("tr[data-scale]");
    if (sw && D.data?.monte_carlo) {
      const mc = D.data.monte_carlo;
      document.querySelectorAll("tr[data-scale]").forEach((r) => r.classList.toggle("pin", r === sw));
      $("#acd-scn").innerHTML = scenario(mc.sweep.find((s) => s.scale === Number(sw.dataset.scale)), mc.sweep.find((s) => s.scale === 1));
      return;
    }
    const hit = e.target.closest(".hit");
    if (hit && hit.dataset.chart === "xh-eq" && D.charts?.eqx && D.data?.perf?.n === D.charts.eqx.n - 1) {
      const p = svgPoint(hit.ownerSVGElement, e), c = D.charts.eqx;
      const i = Math.max(1, Math.min(c.n - 1, Math.round(((p.x - PAD.l) / (W - PAD.l - PAD.r)) * (c.n - 1))));
      D.hilite = i; D.tfilter = "all"; D.tquery = ""; D.sort = { key: "i", dir: -1 }; setTab("tr"); return;
    }
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const id = D.id, act = btn.dataset.act, msg = $("#acd-actmsg");
    const say = (t) => { if (msg) msg.textContent = t; };
    try {
      if (act === "legacy") { close(); if (legacy) legacy(id); return; }
      if (act === "assign") await api(`/accounts/${id}/strategy`, { method: "POST", body: JSON.stringify({ strategy: $("#acd-strat").value, symbol: $("#acd-sym").value }) });
      if (act === "go") await api(`/accounts/${id}/go-live`, { method: "POST" });
      if (act === "stop") await api(`/accounts/${id}/stop-live`, { method: "POST" });
      if (act === "sim") { say("Simulating…"); const r = await api(`/nostro/account/${id}/simulate`, { method: "POST" }); say(`Done: ${r.trades} trades, ${r.verdict}, net ${r.net}`); }
      if (act === "tp-link") {
        if (!confirm("Link this account to TradersPost for real execution?\n\nIt will send REAL orders once TRADERSPOST_WEBHOOK_URL is configured in run.bat and the TradersPost subscription is enabled.")) return;
        await api(`/nostro/account/${id}/traderspost/link`, { method: "POST", body: JSON.stringify({}) });
      }
      if (act === "tp-unlink") {
        if (!confirm("Unlink this account from TradersPost? It stops sending real orders immediately.")) return;
        await api(`/nostro/account/${id}/traderspost/unlink`, { method: "POST" });
      }
      if (act === "payout-set") {
        const v = parseFloat($("#acd-payout")?.value);
        if (!isFinite(v)) { say("Enter a valid equity target."); return; }
        await api(`/nostro/account/${id}/payout-target`, { method: "POST", body: JSON.stringify({ target: v }) });
        say(`Will stop trading and flag this account once equity reaches ${money(v)}.`);
      }
      if (act === "payout-clear") {
        await api(`/nostro/account/${id}/payout-target`, { method: "POST", body: JSON.stringify({ target: null }) });
      }
      if (act === "tp-test-long") {
        if (!confirm("Send ONE REAL test long signal right now (take profit $100 / stop loss $50, 1 contract, at the current market price)?\n\nThis fires for real the instant you confirm.")) return;
        say("Sending test signal…");
        const r = await api(`/nostro/account/${id}/traderspost/test-signal`, { method: "POST", body: JSON.stringify({ side: "long", take_profit: 100, stop_loss: 50, quantity: 1 }) });
        say(r.sent
          ? `Sent: ${r.side} ${r.quantity} ${r.symbol} (${r.contract}) @ ${r.price}, stop ${r.stop_price}, target ${r.target_price}. Check TradersPost's Signals log.`
          : "Webhook call failed — check the server log.");
        return;
      }
      if (act === "reset") {
        if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "CLICK AGAIN TO ERASE THIS ACCOUNT'S TRADES"; return; }
        await api(`/accounts/${id}/reset`, { method: "POST" });
      }
      D.trades = null;
      await load(true);
    } catch (err) { say(String(err.message || err)); }
  }

  function exportCsv() {
    const rows = filteredTrades();
    const head = ["i", "exit_time", "side", "qty", "entry_price", "exit_price", "pnl", "exit_reason", "source"];
    const csv = [head.join(","), ...rows.map((t) => head.map((h) => JSON.stringify(t[h] ?? "")).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `account_${D.id}_trades.csv`; a.click();
  }

  async function render() {
    const d = D.data; if (!d) return;
    const a = d.account;
    $("#acd-title").innerHTML = `<b>${esc(a.name)}</b> <small>#${a.id} · ${esc(a.symbol || "—")} · ${esc((a.strategy || "no strategy").replace(/_/g, " "))}</small>`;
    document.querySelectorAll("#acd-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === D.tab));
    const body = $("#acd-body"), top = body.scrollTop;
    body.classList.remove("swap"); void body.offsetWidth; body.classList.add("swap");
    if (D.tab === "ov") body.innerHTML = tabOverview(d);
    else if (D.tab === "mc") body.innerHTML = tabMC(d);
    else if (D.tab === "bk") body.innerHTML = tabBreakdown(d);
    else if (D.tab === "ct") { body.innerHTML = tabControls(d); if (a.traderspost_linked) loadTpData(a.id); }
    else if (D.tab === "tr") {
      body.innerHTML = '<div class="dim">Loading trades…</div>'; const seq = D.seq; await loadTrades();
      if (seq !== D.seq || D.tab !== "tr") return;
      body.innerHTML = tabTrades();
    }
    body.scrollTop = top;
    wire();
    if (D.tab === "tr") { const hl = $("#acd-trows tr.hl"); if (hl) hl.scrollIntoView({ block: "center" }); D.hilite = null; }
  }

  async function load(keep) {
    const seq = ++D.seq;
    if (!keep) $("#acd-body").innerHTML = '<div class="dim acd-load">Crunching the numbers — statistics and a Monte Carlo…</div>';
    try {
      const d = await api(`/nostro/account/${D.id}/insights?paths=${D.paths}`);
      if (seq !== D.seq) return;
      D.data = d;
      await render();
    } catch (e) { if (seq === D.seq) $("#acd-body").innerHTML = `<div class="dn">${esc(e.message || e)}</div>`; }
  }

  function open(id) {
    ensureDom();
    D.id = Number(id); D.trades = null; D.data = null; D.tab = "ov"; D.charts = {}; D.hilite = null;
    $("#acd-back").classList.remove("hidden");
    document.body.classList.add("acd-open");
    $("#acd-title").innerHTML = `<b>ACCOUNT #${D.id}</b>`;
    if (typeof loadStrategies === "function" && !(typeof state !== "undefined" && state.strategies?.length)) Promise.resolve(loadStrategies()).catch(() => {});
    load(false);
  }

  window.addEventListener("load", () => {
    if (window.openAccountModal && window.openAccountModal !== open) { legacy = window.openAccountModal; window.openLegacyAccountModal = legacy; }
    window.openAccountModal = open;
    window.openAccountDetail = open;
  });
})();
