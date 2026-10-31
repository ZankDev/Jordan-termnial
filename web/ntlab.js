/* NT LAB (NOSTRO › NT LAB) — the strategy search with NinjaTrader 8 as the backtest provider.
   Status of NinjaTrader (installed, bridge, data connections, NinjaScript), the running search and its live log,
   the leaderboard (champions first: in-sample / out-of-sample / 4 walk-forward folds / never-searched holdout / Apex 50K /
   neighbour stability) and the detail of any candidate with its equity curve and the NostroLab parameters to run it in
   NinjaTrader. Backend: /api/ntlab (app/ntlab). Depends on dashboard.js (api) and invest.js (NX helpers, NX.lineChart). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = NX.esc;
  const money = (x) => (typeof x === "number" ? `${x < 0 ? "-" : ""}$${Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—");
  const cls = (x) => (x > 0 ? "up" : x < 0 ? "dn" : "");
  const S = { timer: 0, run: null, sel: null };

  function shell(v) {
    v.innerHTML = `
      <div class="bb-kpis" id="ntl-kpis">${NX.load()}</div>
      <div class="bb-grid">
        ${NX.panel(1, "NINJATRADER", "ntl-nt", { cls: "s4 h380" })}
        ${NX.panel(2, "SEARCH", "ntl-run", { cls: "s4 h380" })}
        ${NX.panel(3, "LIVE LOG", "ntl-log", { cls: "s4 h380" })}
        ${NX.panel(4, "LEADERBOARD · CHAMPIONS FIRST", "ntl-board", { cls: "s12 h620", head: '<select id="ntl-runsel"></select>' })}
        <div class="bb-panel s12 hidden" id="ntl-detail" style="--i:5"></div>
      </div>
      <div class="rw-foot">How a strategy becomes a CHAMPION: every gate on the OUT-OF-SAMPLE part (the last 30% of the searched period) —
        profit factor ≥ 1.3, Sharpe ≥ 0.8, ≥ 25 trades, ≥ 55% of months positive — plus profitable in 3 of 4 walk-forward folds,
        drawdown ≤ $6,000, 6 slightly-changed neighbours still profitable (not curve-fit), and money made in the HOLDOUT
        (the most recent year, never used by the search). Local engine results are a pre-screen; NinjaTrader is the truth.</div>`;
    v.addEventListener("click", onClick);
    v.addEventListener("change", (e) => { if (e.target.id === "ntl-runsel") { S.run = e.target.value ? +e.target.value : null; board(); } });
  }

  async function refresh() {
    let st;
    try { st = await api("/ntlab/status"); } catch (e) { $("#ntl-kpis").innerHTML = NX.err(e); return; }
    const nt = st.ninjatrader, se = st.search || {};
    const conn = (nt.ping && nt.ping.connections) || [];
    const k = (l, v, s, c = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
    $("#ntl-kpis").innerHTML = k("NINJATRADER", nt.bridge ? "CONNECTED" : nt.installed ? "INSTALLED" : "NOT INSTALLED", nt.bridge ? `${conn.filter((c) => c.status === "Connected").length} data connection(s) up` : nt.installed ? "start it / compile NostroLab" : "install NinjaTrader 8", nt.bridge ? "up" : "dn")
      + k("SEARCH", esc((se.state || "idle").toUpperCase()), se.run_id ? `run #${se.run_id}${se.generation != null ? ` · generation ${se.generation}` : ""}` : "—", se.state === "running" ? "up" : "")
      + k("BATCH", se.batch_size || "—", esc(se.batch || ""))
      + k("CHAMPIONS", se.champions ?? "—", "strategies that passed every gate", se.champions ? "up" : "");
    $("#ntl-nt").innerHTML = `<table class="bb-tbl kv"><tbody>
        <tr><td>NinjaTrader 8</td><td class="r ${nt.installed ? "up" : "dn"}">${nt.installed ? "installed" : "not found"}</td></tr>
        <tr><td>NostroLab strategy</td><td class="r ${nt.strategy_installed ? "up" : "dn"}">${nt.strategy_installed ? "installed" : "—"}</td></tr>
        <tr><td>NostroBridge add-on</td><td class="r ${nt.addon_installed ? "up" : "dn"}">${nt.addon_installed ? "installed" : "—"}</td></tr>
        <tr><td>Bridge (127.0.0.1:8099)</td><td class="r ${nt.bridge ? "up" : "dn"}">${nt.bridge ? "answering" : "offline"}</td></tr>
        ${conn.map((c) => `<tr><td>${esc(c.name)}</td><td class="r ${c.status === "Connected" ? "up" : "dn"}">${esc(c.status)}</td></tr>`).join("")}
      </tbody></table>
      ${!nt.installed ? `<ol class="ntl-steps"><li>Install NinjaTrader 8 from ninjatrader.com and log in.</li><li>Connect a data feed with futures history (Connections menu).</li><li>Come back and press INSTALL NINJASCRIPT.</li></ol>`
        : !nt.strategy_installed ? `<button class="bb-chip on" data-ntl="install">INSTALL NINJASCRIPT</button><p class="dim">Copies NostroLab + NostroBridge into NinjaTrader. Then in NinjaTrader: New › NinjaScript Editor › F5 (compile), restart NinjaTrader once.</p>`
        : !nt.bridge ? `<p class="dim">Installed. Start NinjaTrader (compile once with F5 in the NinjaScript Editor) — the bridge comes up with it.</p>` : `<p class="up">Ready: searches with provider NinjaTrader run on NinjaTrader's data and fill engine.</p>`}`;
    const running = se.state === "running" && se.alive !== false;
    $("#ntl-run").innerHTML = `<div class="ntl-form">
        <label>PROVIDER<select id="ntl-prov"><option value="local">Local engine (pre-screen)</option><option value="ninjatrader" ${nt.bridge ? "" : "disabled"}>NinjaTrader 8</option></select></label>
        <label>MARKET<select id="ntl-inst"><option>ES</option><option>NQ</option></select></label>
        <label>GENERATIONS<input id="ntl-gen" type="number" value="10" min="1" max="60"/></label>
        <label>POPULATION<input id="ntl-pop" type="number" value="150" min="20" max="2000"/></label>
        <label>STOP AT CHAMPIONS<input id="ntl-ch" type="number" value="5" min="1" max="50"/></label>
        <button class="bb-chip on" data-ntl="start" ${running ? "disabled" : ""}>${running ? "SEARCH RUNNING…" : "START SEARCH"}</button></div>
      <p class="dim">With NinjaTrader each generation is one Strategy Analyzer run: NostroLab, BatchId from the log, Optimize JobIndex 0…N-1. The log shows exactly what to set.</p>`;
    const log = se.log || [];
    $("#ntl-log").innerHTML = log.length ? `<div class="ntl-log">${log.slice().reverse().map((l) => `<div class="${/CHAMPION/.test(l) ? "up" : /rejected/.test(l) ? "dim" : /WAITING/.test(l) ? "ntl-wait" : ""}">${esc(l)}</div>`).join("")}</div>` : '<div class="bb-empty">no search yet</div>';
    const runs = (await api("/ntlab/runs?n=15")).runs;
    const sel = $("#ntl-runsel");
    if (sel && sel.options.length !== runs.length + 1) {
      sel.innerHTML = `<option value="">ALL RUNS</option>${runs.map((r) => `<option value="${r.id}">#${r.id} · ${esc(r.provider)} · ${esc(r.instrument)} · ${esc(r.status)} · ${r.candidates.n || 0} tested · ${r.candidates.champs || 0} champions</option>`).join("")}`;
      if (S.run) sel.value = S.run;
    }
    board();
  }

  async function board() {
    let r;
    try { r = await api(`/ntlab/leaderboard?n=60${S.run ? `&run_id=${S.run}` : ""}`); } catch (e) { $("#ntl-board").innerHTML = NX.err(e); return; }
    const fold = (f) => `<span class="ntl-fold ${cls(f)}" title="${money(f)}"></span>`;
    $("#ntl-board").innerHTML = !r.rows.length ? '<div class="bb-empty">no candidates yet</div>' : `<table class="bb-tbl"><thead><tr><th></th><th>RULES</th><th class="r">SCORE</th>
        <th class="r">IS PF</th><th class="r">OOS PF</th><th class="r">OOS SHARPE</th><th class="r">OOS TRADES</th><th>FOLDS</th><th class="r">HOLDOUT</th><th class="r">MAX DD</th><th class="r">APEX</th><th class="r">STABLE</th><th class="r">DSR</th><th>ML FILTER</th><th>VERDICT</th></tr></thead><tbody>
      ${r.rows.map((x) => { const m = x.metrics; return `<tr class="ntl-row" data-run="${x.run_id}" data-id="${esc(x.spec_id)}">
        <td>${x.champion === 2 ? '<span class="ntl-ch ml">ML CHAMPION</span>' : x.champion ? '<span class="ntl-ch">CHAMPION</span>' : `<span class="dim">${esc(x.role || "")}</span>`}</td><td class="ntl-rules">${esc(x.rules)}</td>
        <td class="r">${x.score.toFixed(2)}</td><td class="r">${m.is.pf}</td><td class="r ${m.oos.pf >= 1.3 ? "up" : m.oos.pf < 1 ? "dn" : ""}">${m.oos.pf}</td>
        <td class="r ${m.oos.sharpe >= 0.8 ? "up" : ""}">${m.oos.sharpe}</td><td class="r">${m.oos.n}</td><td>${m.folds.map(fold).join("")}</td>
        <td class="r ${cls(x.holdout && x.holdout.net)}">${x.holdout ? money(x.holdout.net) : "—"}</td><td class="r">${money(m.all.max_dd)}</td>
        <td class="r">${m.apex ? `${Math.round(m.apex.pass_rate * 100)}%` : "—"}</td><td class="r">${x.stability == null ? "—" : `${Math.round(x.stability * 100)}%`}</td>
        <td class="r ${x.dsr >= 0.95 ? "up" : x.dsr != null ? "dn" : ""}">${x.dsr == null ? "—" : x.dsr.toFixed(2)}</td>
        <td class="dim">${x.ml ? (x.ml.champion ? '<span class="up">passed</span>' : `AUC ${x.ml.auc_wf ?? "—"}`) : "—"}</td>
        <td class="dim ntl-why">${x.champion ? "passed every gate" : esc((x.why_not || []).slice(0, 2).join("; "))}</td></tr>`; }).join("")}</tbody></table>`;
  }

  async function detail(run, id) {
    const box = $("#ntl-detail");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>CANDIDATE ${esc(id)}<span class="sp"></span><button class="bb-chip" data-ntl="close">CLOSE ✕</button></div><div class="bb-pb">${NX.load()}</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    let c;
    try { c = await api(`/ntlab/candidate?run_id=${run}&spec_id=${encodeURIComponent(id)}`); } catch (e) { box.querySelector(".bb-pb").innerHTML = NX.err(e); return; }
    const m = c.metrics, row = (l, s) => `<tr><td>${l}</td><td class="r">${s.n}</td><td class="r ${cls(s.net)}">${money(s.net)}</td><td class="r">${s.pf}</td><td class="r">${s.sharpe}</td><td class="r">${Math.round(s.win * 100)}%</td><td class="r">${money(s.avg)}</td><td class="r">${money(s.max_dd)}</td></tr>`;
    box.innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>${c.champion ? '<span class="ntl-ch">CHAMPION</span> ' : ""}${esc(c.rules)}<span class="sp"></span><span class="meta">run #${c.run_id} · generation ${c.generation} · ${esc(c.spec_id)}</span><button class="bb-chip" data-ntl="close">CLOSE ✕</button></div>
      <div class="ntl-detail"><div>
        <div id="ntl-eq" style="height:260px"></div>
        <table class="bb-tbl"><thead><tr><th></th><th class="r">TRADES</th><th class="r">NET</th><th class="r">PF</th><th class="r">SHARPE</th><th class="r">WIN</th><th class="r">AVG</th><th class="r">MAX DD</th></tr></thead><tbody>
          ${row("IN-SAMPLE", m.is)}${row(`OUT-OF-SAMPLE (from ${m.split})`, m.oos)}${c.holdout ? row("HOLDOUT (never searched)", c.holdout) : ""}${row("ALL SEARCHED", m.all)}</tbody></table>
        <p class="dim">Walk-forward folds: ${m.folds.map((f) => `<span class="${cls(f)}">${money(f)}</span>`).join(" · ")} · OOS months positive ${Math.round((m.months_positive_oos || 0) * 100)}% ·
          Apex 50K: ${m.apex.passes} passed / ${m.apex.fails} failed (${Math.round(m.apex.pass_rate * 100)}%)${m.apex.avg_trades_to_pass ? `, ~${m.apex.avg_trades_to_pass} trades to pass` : ""}</p>
        ${c.why_not && c.why_not.length ? `<p class="dn">Not a champion: ${esc(c.why_not.join("; "))}</p>` : ""}
        ${c.dsr != null ? `<p>Deflated Sharpe: <b class="${c.dsr >= 0.95 ? "up" : "dn"}">${c.dsr.toFixed(2)}</b> — the probability the edge is real after all the strategies tried (≥ 0.95 needed).</p>` : ""}
        ${c.ml && c.ml.walk_forward ? `<div class="ntl-sub">ML FILTER — walk-forward LightGBM meta-labeling (${esc(c.ml.verdict || "")})</div>
          <table class="bb-tbl"><thead><tr><th></th><th class="r">TRADES</th><th class="r">NET</th><th class="r">PF</th><th class="r">SHARPE</th></tr></thead><tbody>
          ${[["walk-forward: all", c.ml.walk_forward.all], ["walk-forward: model TAKES", c.ml.walk_forward.taken], ["walk-forward: model SKIPS", c.ml.walk_forward.skipped], ["holdout: all", c.ml.holdout.all], ["holdout: model TAKES", c.ml.holdout.taken]].map(([l, s]) => `<tr><td>${l}</td><td class="r">${s.n}</td><td class="r ${cls(s.net)}">${money(s.net)}</td><td class="r">${s.pf}</td><td class="r">${s.sharpe}</td></tr>`).join("")}</tbody></table>
          <p class="dim">AUC ${c.ml.auc_wf ?? "—"} (0.5 = coin flip) · top features: ${(c.ml.importance || []).slice(0, 6).map((f) => esc(f[0])).join(", ")}</p>` : ""}
      </div><div>
        <div class="ntl-sub">RUN IT IN NINJATRADER — NostroLab parameters</div>
        <table class="bb-tbl kv"><tbody>${Object.entries(c.ninjatrader_params).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r">${esc(v === "" ? "(empty)" : v)}</td></tr>`).join("")}</tbody></table>
        <div class="ntl-sub">SPEC (JSON)</div><pre class="ntl-pre">${esc(JSON.stringify(c.spec, null, 1))}</pre>
      </div></div>`;
    const eq = (m.equity || []).map((p) => [p[0], p[1]]);
    if (eq.length > 1) NX.lineChart($("#ntl-eq"), eq, { height: 260, fmt: (v) => money(v) });
  }

  async function onClick(e) {
    const b = e.target.closest("[data-ntl]");
    if (b && b.dataset.ntl === "close") { $("#ntl-detail").classList.add("hidden"); return; }
    if (b && b.dataset.ntl === "install") {
      try { const r = await api("/ntlab/install", { method: "POST" }); showToast(r.ok ? "NinjaScript copied — compile with F5 in NinjaTrader" : r.error); } catch (err) { showToast(String(err.message || err)); }
      refresh(); return;
    }
    if (b && b.dataset.ntl === "start") {
      const body = { provider: $("#ntl-prov").value, instrument: $("#ntl-inst").value, generations: +$("#ntl-gen").value, population: +$("#ntl-pop").value, target_champions: +$("#ntl-ch").value };
      try { await api("/ntlab/start", { method: "POST", body: JSON.stringify(body) }); showToast("Search started"); } catch (err) { showToast(String(err.message || err)); }
      setTimeout(refresh, 1500); return;
    }
    const r = e.target.closest(".ntl-row");
    if (r) detail(r.dataset.run, r.dataset.id);
  }

  function start() {
    const v = $("#view-ntlab"); if (!v) return;
    if (!v.dataset.built) { v.dataset.built = "1"; shell(v); }
    refresh();
    clearInterval(S.timer);
    S.timer = setInterval(() => { if (!document.hidden && v.classList.contains("active")) refresh(); }, 6000);
  }
  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => { if (prev) prev(tab); if (tab === "ntlab") start(); else clearInterval(S.timer); };
})();
