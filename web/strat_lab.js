/* NOSTRO › STRATEGY LAB — every strategy on one interactive board: pooled track record (live + simulated trades on record),
   Monte Carlo odds of passing the Apex $50k evaluation, the trainer's view, and the risk per trade the trainer recommends.
   Click a row for the full picture (equity curve with hover, Monte Carlo fan numbers, size sweep, hour/weekday breakdown).
   Tick two rows to compare them side by side. Depends on dashboard.js (api()) and ui_kit.js (UIX). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n, d = 0) => (!isN(n) ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const smoney = (n, d = 0) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const num = (n, d = 2) => (!isN(n) ? "—" : Number(n).toFixed(d));
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const L = { rows: [], sel: null, cmp: new Set(), started: false, cache: {} };
  const key = (r) => `${r.strategy}|${r.symbol}`;
  const nice = (s) => String(s || "").replace(/_/g, " ");

  function verdict(r) {
    if (!r.n || r.n < 15) return ["TOO FEW TRADES", "idle"];
    if ((r.pass_pct ?? 0) >= 50 && (r.breach_pct ?? 100) < 45) return ["PASSES OFTEN", "passed"];
    if ((r.edge_probability ?? 0) <= 30 || (r.net ?? 0) < 0) return ["LOSING", "failed"];
    return ["UNPROVEN", "eval"];
  }

  function board() {
    const rows = L.rows;
    return `<table class="bb-tbl uix-sort sl-tbl"><thead><tr><th></th><th>STRATEGY</th><th>SYM</th><th>VERDICT</th><th class="r">TRADES</th><th class="r">NET</th><th class="r">WIN%</th><th class="r">PF</th>
      <th class="r">APEX PASS</th><th class="r">BREACH</th><th class="r">EDGE REAL?</th><th class="r">REC. RISK</th><th>TRAINER</th></tr></thead><tbody>
      ${rows.map((r) => { const [vt, vc] = verdict(r), k = key(r);
        return `<tr class="click ${L.sel === k ? "sel" : ""}" data-k="${esc(k)}">
        <td><input type="checkbox" class="sl-cmp" data-k="${esc(k)}" ${L.cmp.has(k) ? "checked" : ""} data-uix-tip="COMPARE|tick two strategies to compare them"/></td>
        <td class="sym">${esc(nice(r.strategy))}${r.champion ? ` <span class="ac-tag" data-uix-tip="CHAMPION|current ${esc(r.champion)} champion of the trainer">★ ${esc(r.champion.toUpperCase())}</span>` : ""}</td>
        <td>${esc(r.symbol)}</td><td><span class="ac-badge ${vc}">${vt}</span></td><td class="r">${r.n}</td>
        <td class="r ${cls(r.net)}" data-v="${r.net ?? ""}">${smoney(r.net)}</td><td class="r">${isN(r.win_rate) ? r.win_rate + "%" : "—"}</td><td class="r">${num(r.profit_factor)}</td>
        <td class="r" data-v="${r.pass_pct ?? ""}" data-uix-tip="APEX PASS|Monte Carlo: share of 1,000 simulated $50k evaluations that hit +$3,000 before the $2,000 trailing drawdown"><div class="sl-bar"><i class="p" style="width:${r.pass_pct || 0}%"></i></div>${isN(r.pass_pct) ? r.pass_pct + "%" : "—"}</td>
        <td class="r" data-v="${r.breach_pct ?? ""}"><div class="sl-bar"><i class="b" style="width:${r.breach_pct || 0}%"></i></div>${isN(r.breach_pct) ? r.breach_pct + "%" : "—"}</td>
        <td class="r ${isN(r.edge_probability) && r.edge_probability >= 90 ? "up" : isN(r.edge_probability) && r.edge_probability <= 30 ? "dn" : ""}" data-v="${r.edge_probability ?? ""}" data-uix-tip="EDGE REAL?|share of bootstrap resamples whose average trade is positive">${isN(r.edge_probability) ? r.edge_probability + "%" : "—"}</td>
        <td class="r" data-v="${r.rec_risk ?? ""}" data-uix-tip="RECOMMENDED RISK|the trainer's best Apex risk per trade|${isN(r.rec_pass) ? `replay: pass ${Math.round(r.rec_pass * 100)}% / breach ${Math.round((r.rec_breach || 0) * 100)}%` : "not judged yet"}">${isN(r.rec_risk) ? money(r.rec_risk) : "—"}</td>
        <td>${r.in_training ? `<span class="up">training</span>` : `<span class="dim">off roster</span>`}</td></tr>`; }).join("")}</tbody></table>`;
  }

  // ------------------------------------------------------------------ detail
  function curveSvg(curve) {
    if (!curve || curve.length < 2) return `<div class="dim">No trades on record.</div>`;
    const W = 700, H = 200, P = 50, eq = curve.map((c) => c.eq), lo = Math.min(...eq, 48000), hi = Math.max(...eq, 53000), s = hi - lo || 1;
    const x = (i) => P + (i / (curve.length - 1)) * (W - P - 8), y = (v) => H - 16 - ((v - lo) / s) * (H - 28);
    const pts = curve.map((c, i) => `${x(i).toFixed(1)},${y(c.eq).toFixed(1)}`).join(" ");
    const up = eq[eq.length - 1] >= 50000;
    const ticks = [lo, (lo + hi) / 2, hi].map((v) => `<line x1="${P}" x2="${W - 8}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${P - 5}" y="${y(v) + 3}" class="t" text-anchor="end">${money(v)}</text>`).join("");
    const hits = curve.map((c, i) => { const prev = i ? curve[i - 1].eq : 50000; return `<rect class="hit" x="${x(i) - (W - P) / curve.length / 2}" y="0" width="${Math.max(2, (W - P) / curve.length)}" height="${H}" data-uix-tip="${i ? "TRADE #" + c.i : "START"}|${esc(String(c.t || "").replace("T", " ").slice(0, 16))}|equity ${money(c.eq)}${i ? ` · ${smoney(c.eq - prev)}` : ""}"/>`; }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" class="acd-svg sl-curve">${ticks}<line x1="${P}" x2="${W - 8}" y1="${y(53000)}" y2="${y(53000)}" class="tgt"/><line x1="${P}" x2="${W - 8}" y1="${y(48000)}" y2="${y(48000)}" class="flr2"/>
      <polyline points="${pts}" class="ln ${up ? "up" : "dn"}"/>${hits}</svg><div class="acd-legend"><i class="ln"></i>pooled equity from $50k <i class="tgt"></i>+$3,000 target <i class="flr"></i>$2,000 drawdown (from start) <span class="acd-hint">hover any point</span></div>`;
  }

  function hourBars(rows) {
    if (!rows?.length) return "";
    const mx = Math.max(...rows.map((r) => Math.abs(r.pnl)), 1);
    return `<div class="sl-hours">${rows.map((r) => `<div class="sl-h ${r.n < 5 ? "thin" : ""}" data-uix-tip="${esc(r.key)}:00 NY|${smoney(r.pnl)} over ${r.n} trades|win ${r.win_rate}% · PF ${r.pf ?? "—"}"><i class="${r.pnl >= 0 ? "p" : "n"}" style="height:${(Math.abs(r.pnl) / mx) * 100}%"></i><span>${esc(r.key)}</span></div>`).join("")}</div>`;
  }

  function detail(d) {
    const p = d.perf || {}, mc = d.monte_carlo;
    const k = (l, v, c = "", t = "") => `<div class="bb-kpi" ${t ? `data-uix-tip="${esc(l)}|${esc(t)}"` : ""}><span class="l">${l}</span><span class="v uix-count ${c}">${v}</span></div>`;
    return `<div class="sl-dh"><b>${esc(nice(d.strategy))} · ${esc(d.symbol)}</b><span class="dim">${p.n || 0} trades on record · confidence ${esc(d.confidence)}</span></div>
      <div class="acd-kpis">${k("NET", smoney(p.net), cls(p.net))}${k("WIN RATE", isN(p.win_rate) ? p.win_rate + "%" : "—")}${k("PROFIT FACTOR", num(p.profit_factor), "", "gross profit ÷ gross loss")}
        ${k("EXPECTANCY", smoney(p.expectancy), cls(p.expectancy), "average trade")}${k("MAX DD", money(p.max_drawdown), "dn")}
        ${k("APEX PASS", mc ? mc.pass_pct + "%" : "—", mc && mc.pass_pct >= 50 ? "up" : "", "Monte Carlo of the $50k evaluation")}${k("BREACH", mc ? mc.breach_pct + "%" : "—", mc && mc.breach_pct > 50 ? "dn" : "")}
        ${k("EDGE REAL?", mc ? mc.edge_probability + "%" : "—", "", "bootstrap probability the average trade is positive")}</div>
      <div class="acd-cols"><div class="acd-main"><section class="acd-card"><h4>EQUITY — every trade on record</h4>${curveSvg(d.curve)}</section>
        <section class="acd-card"><h4>P&L BY ENTRY HOUR (NY)</h4>${hourBars(d.breakdown?.hour)}</section></div>
        <div class="acd-side">${mc ? `<section class="acd-card"><h4>SIZE SWEEP — same trades, scaled</h4><table class="acd-tbl"><thead><tr><th>SIZE</th><th>PASS</th><th>BREACH</th><th>MED TRADES</th></tr></thead><tbody>
          ${mc.sweep.map((s) => `<tr class="${s.scale === mc.best_scale ? "best" : ""} ${s.scale === 1 ? "cur" : ""}"><td>${s.scale}×${s.scale === mc.best_scale ? " ★" : ""}</td><td class="up">${s.pass}%</td><td class="dn">${s.breach}%</td><td>${isN(s.median_trades) ? Math.round(s.median_trades) : "—"}</td></tr>`).join("")}</tbody></table>
          <p class="acd-note">★ best pass-minus-breach. 1× = the size these trades were taken at.</p></section>
          <section class="acd-card"><h4>NEXT 100 TRADES</h4><div class="acd-sg"><div class="acd-st"><span>Bad (5%)</span><b>${money(mc.end100.p5)}</b></div><div class="acd-st"><span>Median</span><b>${money(mc.end100.p50)}</b></div>
          <div class="acd-st"><span>Good (95%)</span><b>${money(mc.end100.p95)}</b></div><div class="acd-st"><span>1-in-10 dip</span><b>${money(mc.dd100.p90)}</b></div></div></section>` : ""}
          <section class="acd-card"><h4>EXIT REASONS</h4><table class="acd-tbl"><tbody>${(d.breakdown?.exit_reason || []).map((e) => `<tr><td>${esc(e.key)}</td><td>${e.n}</td><td class="${cls(e.pnl)}">${smoney(e.pnl)}</td></tr>`).join("")}</tbody></table></section></div></div>`;
  }

  async function loadDetail(k) {
    L.sel = k;
    document.querySelectorAll(".sl-tbl tr[data-k]").forEach((tr) => tr.classList.toggle("sel", tr.dataset.k === k));
    const [strategy, symbol] = k.split("|"), el = $("#sl-detail");
    el.innerHTML = `<div class="dim acd-load">Running the Monte Carlo…</div>`;
    try {
      const d = L.cache[k] || (L.cache[k] = await api(`/nostro/strategy-insights?strategy=${encodeURIComponent(strategy)}&symbol=${encodeURIComponent(symbol)}&paths=3000`));
      if (L.sel !== k) return;
      el.innerHTML = detail(d);
      window.UIX && UIX.countUp(el);
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { el.innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }

  function compare() {
    const el = $("#sl-compare"), ks = [...L.cmp];
    if (ks.length !== 2) { el.innerHTML = `<div class="dim">Tick two strategies in the board to compare them here.</div>`; return; }
    const [a, b] = ks.map((k) => L.rows.find((r) => key(r) === k));
    const lines = [["Trades", "n", 0, 1], ["Net", "net", 0, 1], ["Win %", "win_rate", 1, 1], ["Profit factor", "profit_factor", 2, 1], ["Expectancy", "expectancy", 0, 1],
      ["Max drawdown", "max_drawdown", 0, -1], ["Apex pass %", "pass_pct", 1, 1], ["Breach %", "breach_pct", 1, -1], ["Edge real %", "edge_probability", 1, 1], ["Recommended risk", "rec_risk", 0, 0]];
    el.innerHTML = `<table class="acd-tbl sl-cmpt"><thead><tr><th></th><th>${esc(nice(a.strategy))} ${esc(a.symbol)}</th><th>${esc(nice(b.strategy))} ${esc(b.symbol)}</th></tr></thead><tbody>
      ${lines.map(([l, f, d, better]) => { const x = a[f], y = b[f]; const wa = better && isN(x) && isN(y) && (better > 0 ? x > y : x < y), wb = better && isN(x) && isN(y) && (better > 0 ? y > x : y < x);
        return `<tr><td>${l}</td><td class="${wa ? "win" : ""}">${isN(x) ? Number(x).toLocaleString(undefined, { maximumFractionDigits: d }) : "—"}</td><td class="${wb ? "win" : ""}">${isN(y) ? Number(y).toLocaleString(undefined, { maximumFractionDigits: d }) : "—"}</td></tr>`; }).join("")}</tbody></table>`;
  }

  // ------------------------------------------------------------------ long history + combos
  function longCheck(list) {
    if (!list?.length) return `<div class="dim">No verified long history yet.</div>`;
    return `<table class="bb-tbl uix-sort sl-tbl"><thead><tr><th>STRATEGY</th><th>SYM</th><th>SOURCE</th><th class="r">YEARS</th><th class="r">TRADES</th><th class="r">NET</th><th class="r">PF</th><th class="r">YEARS +</th><th>ACCOUNTS USING IT</th></tr></thead><tbody>
      ${list.map((t) => `<tr class="${t.net > 0 ? "" : "sl-lose"}"><td class="sym">${esc(nice(t.strategy))}</td><td>${esc(t.symbol)}</td><td class="dim">${esc(t.src)}</td><td class="r">${(t.months / 12).toFixed(0)}</td><td class="r">${t.n}</td>
        <td class="r ${cls(t.net)}" data-v="${t.net}">${smoney(t.net)}</td><td class="r">${num(t.pf)}</td><td class="r" data-uix-tip="YEARS PROFITABLE|${t.years_positive} of ${t.years} calendar years made money">${t.years_positive}/${t.years}</td>
        <td>${(t.accounts || []).map((id) => `<a href="#" class="sl-acc" data-acc="${id}">#${id}</a>`).join(" ") || "<span class='dim'>—</span>"}</td></tr>`).join("")}</tbody></table>
      <p class="acd-note">Each parameter set replayed over the verified Dukascopy history (3–5 years, 5-minute bars, micro-contract sizing at $500 risk, entry + stop slippage, commissions). Red rows lose money over the long run — the accounts using them are running a regime fluke.</p>`;
  }
  function combosTable(d) {
    const rows = (d.combos || []).slice(0, 15);
    if (!rows.length) return `<div class="dim">Not enough strategies with a profitable long record to combine.</div>`;
    const bs = d.best_single;
    return `<p class="acd-note">${bs ? `Best single: <b>${esc(nice(bs.members[0].strategy))} ${esc(bs.members[0].symbol)}</b> — pass ${bs.pass}% / breach ${bs.breach}% in a ${d.eval_days}-day evaluation.` : ""} ${esc(d.note || "")}</p>
      <table class="bb-tbl uix-sort sl-tbl"><thead><tr><th>COMBO</th><th class="r">PASS</th><th class="r">BREACH</th><th class="r">EDGE</th><th class="r">SIZE EACH</th><th class="r">MED DAYS</th><th class="r">CORR</th><th>BY YEAR</th></tr></thead><tbody>
      ${rows.map((x) => `<tr><td>${x.members.map((m) => `<span class="sl-mem" data-uix-tip="${esc(nice(m.strategy))} ${esc(m.symbol)}|${m.n} trades over ${(m.months / 12).toFixed(0)} years|net ${smoney(m.net)}|source: ${esc(m.src)}">${esc(nice(m.strategy))} <small>${esc(m.symbol)}</small></span>`).join(" + ")}</td>
        <td class="r up" data-v="${x.pass}">${x.pass}%</td><td class="r dn" data-v="${x.breach}">${x.breach}%</td><td class="r ${cls(x.edge)}" data-v="${x.edge}">${x.edge > 0 ? "+" : ""}${x.edge}</td>
        <td class="r" data-uix-tip="RISK SCALE|each member trades at this multiple of $500 risk per trade (≈ $${Math.round(500 * x.best_scale)})">${x.best_scale}×</td><td class="r">${x.median_days_to_pass ?? "—"}</td><td class="r">${x.daily_corr ?? "—"}</td>
        <td class="sl-yrs">${Object.entries(x.by_year || {}).map(([y, v]) => `<span class="${cls(v)}" data-uix-tip="${y}|${smoney(v)} at this size">${y.slice(2)}</span>`).join("")}</td></tr>`).join("")}</tbody></table>`;
  }
  async function loadLong() {
    const a = $("#sl-long"), b = $("#sl-combos");
    try {
      const d = await api("/nostro/combos");
      a.innerHTML = longCheck(d.long_check);
      b.innerHTML = combosTable(d);
    } catch (e) { a.innerHTML = b.innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }

  async function build() {
    const v = $("#view-slab");
    if (!v.dataset.built) {
      v.dataset.built = "1";
      v.innerHTML = `<div class="sl-page"><div class="hs-top"><b class="hs-t">STRATEGY LAB</b><span class="dim">every strategy's full record, its odds of passing the Apex $50k evaluation, and the risk the trainer recommends</span><span class="sp"></span><button class="bb-chip" id="sl-sync" data-uix-tip="APPLY RECOMMENDED RISK|set every account's risk per trade to its strategy's recommendation now (the trainer also does this every cycle)">APPLY RECOMMENDED RISK</button></div>
        <div class="bb-panel"><div class="bb-ph"><span class="bb-n">1</span>BOARD<span class="sp"></span><span class="meta">click a row for detail · click a header to sort · tick two to compare</span></div><div class="bb-pad sl-wrap" id="sl-board"><div class="dim">Loading (runs a Monte Carlo per strategy)…</div></div></div>
        <div class="bb-panel"><div class="bb-ph"><span class="bb-n">2</span>LONG-HISTORY CHECK — do the parameters the accounts run survive YEARS?<span class="sp"></span><span class="meta">3–5 years of 5-minute bars</span></div><div class="bb-pad sl-wrap" id="sl-long"><div class="dim acd-load">Backtesting every parameter set over years of history (~30 s)…</div></div></div>
        <div class="bb-panel"><div class="bb-ph"><span class="bb-n">3</span>COMBOS — 2–3 strategies in one $50k evaluation<span class="sp"></span><span class="meta">Monte Carlo of daily P&amp;L</span></div><div class="bb-pad sl-wrap" id="sl-combos"><div class="dim">…</div></div></div>
        <div class="bb-panel"><div class="bb-ph"><span class="bb-n">4</span>COMPARE</div><div class="bb-pad" id="sl-compare"></div></div>
        <div class="bb-panel"><div class="bb-ph"><span class="bb-n">5</span>DETAIL</div><div class="bb-pad" id="sl-detail"><div class="dim">Click a strategy in the board.</div></div></div></div>`;
      v.addEventListener("change", (e) => { const c = e.target.closest(".sl-cmp"); if (!c) return; if (c.checked) { L.cmp.add(c.dataset.k); if (L.cmp.size > 2) { const first = [...L.cmp][0]; L.cmp.delete(first); const o = v.querySelector(`.sl-cmp[data-k="${CSS.escape(first)}"]`); if (o) o.checked = false; } } else L.cmp.delete(c.dataset.k); compare(); });
      loadLong();
      v.addEventListener("click", (e) => { const acc = e.target.closest(".sl-acc"); if (acc) { e.preventDefault(); if (window.openAccountModal) openAccountModal(Number(acc.dataset.acc)); return; } });
      v.addEventListener("click", (e) => { if (e.target.closest("input,th")) return; const tr = e.target.closest(".sl-tbl tr[data-k]"); if (tr) loadDetail(tr.dataset.k); });
      $("#sl-sync").addEventListener("click", async () => { try { const r = await api("/nostro/risk-sync", { method: "POST" }); showToast(r.changes.length ? `Risk updated on ${r.changes.length} accounts` : "Every account already has its recommended risk (or the trainer has not judged its strategy yet)"); } catch (e) { showToast(e.message); } });
    }
    const r = await api("/nostro/strategy-board");
    L.rows = r.rows.sort((a, b) => ((b.pass_pct ?? -1) - (b.breach_pct ?? 0)) - ((a.pass_pct ?? -1) - (a.breach_pct ?? 0)));
    $("#sl-board").innerHTML = board();
    compare();
  }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "slab") build().catch((e) => { const el = $("#sl-board"); if (el) el.innerHTML = `<div class="dn">${esc(e.message)}</div>`; });
  };
})();
