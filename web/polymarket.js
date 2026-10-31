/* POLYMARKET tab — the momo strategy's whole loop on one screen: what the trainer is doing
   right now (phase, progress, score history), the champion, the leaderboard, the paper
   accounts forward-testing it with live open positions, the radar of running sessions,
   the trade feed, a per-session line chart, the Field, and the copytrader.
   Depends on dashboard.js (api(), showToast(), ScatterField, tpslBarsHtml()) and
   pm_chart.js. Lazy-built through the same window.onToolsTab chain as academy.js. */
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n, dec = 2) => (!isN(n) ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}`);
  const smoney = (n, dec = 2) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}`);
  const pct = (n, dec = 2) => (!isN(n) ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(dec)}%`);
  const px = (n) => (!isN(n) ? "—" : Number(n).toFixed(3));
  const num = (n, dec = 2) => (!isN(n) ? "—" : Number(n).toFixed(dec));
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const dur = (s) => { if (!isN(s)) return "—"; s = Math.max(0, Math.round(s)); return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
  const ago = (ts) => (isN(ts) && ts > 0 ? `${dur(Date.now() / 1000 - ts)} ago` : "never");
  const day = (ts) => (isN(ts) ? new Date(ts * 1000).toLocaleDateString(undefined, { day: "2-digit", month: "short" }) : "—");

  const PM = { defaults: {}, last: {}, timers: [], sel: null, chart: null, radar: [], status: null, accounts: [], showArch: false, field: null, started: false };

  // paint only when the markup actually changed: no flicker, no lost scroll/hover
  function paint(sel, html) {
    const el = $(sel);
    if (!el || PM.last[sel] === html) return;
    PM.last[sel] = html;
    el.innerHTML = html;
  }
  const panel = (n, title, meta, body, id, extra = "") =>
    `<div class="bb-panel pm-p ${extra}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${meta || ""}</span></div><div class="bb-pad" id="${id}">${body}</div></div>`;
  const kpi = (label, value, sub, c = "") => `<div class="bb-kpi"><span class="l">${label}</span><span class="v ${c}">${value}</span>${sub ? `<span class="s">${sub}</span>` : ""}</div>`;

  function skeleton() {
    return `<div class="pm-page">
      <div id="pm-top" class="bb-kpis pm-top">Loading…</div>
      ${panel(0, "REAL ACCOUNT · LIVE MOMO TRADES", "your Polymarket account — off until you connect a wallet and ARM it", '<div class="dim">Loading…</div>', "pm-real", "pm-realp")}
      <div class="pm-row pm-r-train">
        ${panel(1, "TRAINING · WHAT THE TRAINER IS DOING", "continuous walk-forward search → holdout check → paper accounts", '<div class="dim">Loading…</div>', "pm-train")}
        ${panel(2, "CORPUS", "resolved 5-minute sessions the trainer learns from", '<div class="dim">Loading…</div>', "pm-corpus")}
      </div>
      <div class="pm-row pm-r-champ">
        ${panel(3, "CHAMPION", "best parameter set that also passed the holdout", '<div class="dim">Loading…</div>', "pm-champ")}
        ${panel(4, "LEADERBOARD", "top candidates of the latest search cycle (out-of-sample)", '<div class="dim">Loading…</div>', "pm-board")}
      </div>
      ${panel(5, "PAPER ACCOUNTS · FORWARD TEST", "the same strategy trading live 5-minute sessions on paper money — this is what backtests can't tell you", '<div class="dim">Loading…</div>', "pm-accts")}
      <div class="pm-row pm-r-live">
        ${panel(6, "MOMO RADAR", "every running session, live — click one to chart it", '<div class="dim">Loading…</div>', "pm-radar")}
        ${panel(7, "TRADE FEED", "every paper trade as it closes", '<div class="dim">Loading…</div>', "pm-feed")}
      </div>
      <div class="pm-row pm-r-chart">
        ${panel(8, "SESSION CHART", "share price (left) · spot vs strike (right)", '<div id="pm-chart-controls" class="dim"></div><div id="pm-chart-host" style="height:300px"></div>', "pm-chartbox")}
        ${panel(9, "MOMO FIELD", "every candidate tested: win rate vs reward:risk", '<div id="pm-field-tip" class="pm-tip hidden"></div><div id="pm-field-chart" style="height:300px;position:relative"></div><div id="pm-field-stats" class="dim" style="margin-top:6px"></div>', "pm-fieldbox")}
      </div>
      <div class="pm-row pm-r-log">
        ${panel(10, "TRAINER LOG", "promotions, holds and rejections — why the champion did or didn't change", '<div class="dim">Loading…</div>', "pm-log")}
        ${panel(11, "COPYTRADER", "paper-shadow a tracked wallet's real Polymarket trades", '<div id="pm-copy-new"></div><div id="pm-copy-accounts" class="dim">Loading…</div>', "pm-copybox")}
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------ tiny SVG charts
  function lineChart(series, opts = {}) {
    const W = 640, H = opts.h || 150, pad = { l: 34, r: 8, t: 8, b: 16 };
    const all = series.flatMap((s) => s.pts.map((p) => p[1])).filter(isN);
    if (!all.length) return `<div class="dim" style="padding:26px 6px">${opts.empty || "No data yet."}</div>`;
    let lo = Math.min(...all, opts.zero ? 0 : Infinity), hi = Math.max(...all, opts.zero ? 0 : -Infinity);
    if (hi === lo) { hi += 1; lo -= 1; }
    const span = hi - lo; lo -= span * 0.06; hi += span * 0.06;
    const n = Math.max(...series.map((s) => s.pts.length));
    const X = (i) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const grid = [0, 0.5, 1].map((f) => { const v = lo + (hi - lo) * f, y = Y(v); return `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}" class="pm-grid"/><text x="${pad.l - 4}" y="${y + 3}" class="pm-ax" text-anchor="end">${opts.fmt ? opts.fmt(v) : v.toFixed(1)}</text>`; }).join("");
    const zero = opts.zero && lo < 0 && hi > 0 ? `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(0)}" y2="${Y(0)}" class="pm-zero"/>` : "";
    const lines = series.map((s) => {
      const pts = s.pts.map((p, i) => (isN(p[1]) ? `${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}` : null)).filter(Boolean);
      if (pts.length < 1) return "";
      const last = pts[pts.length - 1].split(",");
      return `<polyline fill="none" stroke="${s.color}" stroke-width="${s.w || 1.6}" ${s.dash ? 'stroke-dasharray="4 3"' : ""} points="${pts.join(" ")}"/><circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="${s.color}"/>`;
    }).join("");
    const legend = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="pm-svg" style="height:${H}px">${grid}${zero}${lines}</svg><div class="pm-legend">${legend}</div>`;
  }

  function spark(points, w = 120, h = 30) {
    const v = points.map((p) => p[1]).filter(isN);
    if (v.length < 2) return `<svg width="${w}" height="${h}" class="pm-spark"><line x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}" class="pm-grid"/></svg>`;
    const lo = Math.min(...v), hi = Math.max(...v), sp = hi - lo || 1;
    const pts = v.map((y, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 2 - ((y - lo) / sp) * (h - 4)).toFixed(1)}`).join(" ");
    const up = v[v.length - 1] >= v[0];
    return `<svg width="${w}" height="${h}" class="pm-spark"><polyline fill="none" stroke="${up ? "#2be07e" : "#ff4a4a"}" stroke-width="1.5" points="${pts}"/></svg>`;
  }

  // ------------------------------------------------------------------ top strip
  function renderTop() {
    const d = PM.dash, s = PM.status;
    if (!d || !s) return;
    const st = s.state, live = d.engine || {}, age = Date.now() / 1000 - (live.last_tick || 0);
    const engineOk = live.last_tick && age < 20 && !live.error;
    paint("#pm-top", [
      kpi("PAPER EQUITY", money(d.total_equity), `${smoney(d.total_pnl)} all-time`, cls(d.total_pnl)),
      kpi("TODAY", smoney(d.today_pnl), `${d.today_trades_count} trades closed`, cls(d.today_pnl)),
      kpi("OPEN NOW", d.open_positions_count, `${d.live_accounts_count} of ${d.accounts_count} accounts live`),
      kpi("CHAMPION", isN(st.champion_score) ? num(st.champion_score) : "—", isN(st.champion_version) ? `version ${st.champion_version}` : "none yet — needs a candidate that passes the holdout"),
      kpi("CORPUS", (s.corpus.total || 0).toLocaleString(), `${(s.corpus.price_ticks || 0).toLocaleString()} price ticks`),
      kpi("TRAINING", `#${st.cycle || 0}`, `${(st.total_evaluated || 0).toLocaleString()} candidates tested`),
      kpi("ENGINE", `<span class="${engineOk ? "up" : "dn"}">${engineOk ? "● LIVE" : "● STALLED"}</span>`, live.last_tick ? `tick ${dur(age)} ago · ${live.tick_ms ?? "—"}ms · ${live.running_sessions} sessions` : "waiting for first tick"),
      kpi("TELEGRAM", `<span class="${d.telegram ? "up" : "dn"}">${d.telegram ? "● ON" : "● OFF"}</span>`, d.telegram ? `<a href="#" id="pm-tg-test">send digest now</a>` : "set TELEGRAM_BOT_TOKEN / CHAT_ID"),
    ].join(""));
    const t = $("#pm-tg-test");
    if (t && !t.dataset.b) { t.dataset.b = 1; t.addEventListener("click", async (e) => { e.preventDefault(); try { await api("/polymarket/telegram/digest", { method: "POST" }); showToast("Digest sent to Telegram"); } catch (x) { showToast(String(x.message || x), true); } }); }
  }

  // ------------------------------------------------------------------ training
  const PHASE_TXT = {
    "starting": "starting up", "loading corpus": "loading the corpus into memory", "searching": "searching parameter sets (out-of-sample scoring)",
    "validating on holdout": "checking leaders on sessions the search never saw", "updating paper accounts": "pointing the paper accounts at the new results",
    "resting": "resting between cycles", "waiting for data": "waiting for enough data", "error": "error — retrying",
  };
  function renderTrain() {
    const s = PM.status; if (!s) return;
    const st = s.state, hist = s.history || [], busy = ["loading corpus", "searching", "validating on holdout", "updating paper accounts"].includes(st.phase);
    const frac = st.phase === "searching" && st.iter_total ? Math.min(1, st.iter_done / st.iter_total) : busy ? 0.02 : 1;
    const started = st.cycle_started_at;
    const line = busy ? `${PHASE_TXT[st.phase] || st.phase} · ${started ? dur(Date.now() / 1000 - started) : ""}` : (PHASE_TXT[st.phase] || st.phase);
    const elig = hist.length ? hist[hist.length - 1].n_elig : 0;
    const chart = lineChart([
      { name: "best score this cycle", color: "#ffa028", pts: hist.map((h) => [h.cycle, h.best_score]) },
      { name: "champion", color: "#2be07e", w: 1.4, dash: true, pts: hist.map((h) => [h.cycle, h.champion_score]) },
    ], { zero: true, h: 150, empty: "The first training cycle has not finished yet — the chart fills in as cycles complete.", fmt: (v) => v.toFixed(1) });
    paint("#pm-train", `
      <div class="pm-phase"><span class="pm-dot ${busy ? "busy" : ""}"></span><b>${esc(st.phase || "—").toUpperCase()}</b><span class="dim"> — ${esc(line)}</span></div>
      <div class="pm-prog"><i style="width:${(frac * 100).toFixed(1)}%"></i></div>
      <div class="pm-prog-l dim">${st.phase === "searching" ? `${st.iter_done} / ${st.iter_total} candidates · best so far ${isN(st.cycle_best) ? num(st.cycle_best) : "—"}` : st.last_cycle_at ? `last cycle finished ${ago(st.last_cycle_at)} (${dur(st.cycle_seconds)})` : "no cycle finished yet"}</div>
      <div class="pm-mini">
        <div><b>${st.cycle || 0}</b><span>CYCLES</span></div>
        <div><b>${(st.total_evaluated || 0).toLocaleString()}</b><span>CANDIDATES TESTED</span></div>
        <div><b>${isN(st.best_score_all) ? num(st.best_score_all) : "—"}</b><span>BEST SCORE EVER</span></div>
        <div><b>${elig}</b><span>SURVIVORS LAST CYCLE</span></div>
        <div><b>${(st.n_search || 0).toLocaleString()} / ${(st.n_holdout || 0).toLocaleString()}</b><span>SEARCH / HOLDOUT SESSIONS</span></div>
      </div>
      ${chart}
      <div class="pm-note ${st.last_outcome === "promoted" ? "up" : "dim"}">${st.last_outcome ? `<b>${esc(String(st.last_outcome).toUpperCase())}</b> — ${esc(st.last_reason || "")}` : ""}${st.error ? `<span class="dn"> ${esc(st.error)}</span>` : ""}</div>`);
  }

  function renderCorpus() {
    const s = PM.status; if (!s) return;
    const c = s.corpus, st = s.state, assets = Object.entries(c.per_asset || {}).sort((a, b) => b[1].n - a[1].n);
    const max = Math.max(1, ...assets.map(([, v]) => v.n));
    const deep = st.deep || {}, series = deep.series || {};
    const done = deep.exhausted;
    paint("#pm-corpus", `
      <div class="pm-mini"><div><b>${(c.total || 0).toLocaleString()}</b><span>SESSIONS</span></div><div><b>${day(c.oldest)} → ${day(c.newest)}</b><span>SPAN</span></div><div><b>${(c.price_ticks || 0).toLocaleString()}</b><span>PRICE TICKS</span></div></div>
      ${assets.map(([a, v]) => `<div class="pm-bar"><span class="pm-bar-l">${esc(a.toUpperCase())}</span><div class="pm-bar-t"><i style="width:${(v.n / max) * 100}%"></i></div><span class="pm-bar-v">${v.n.toLocaleString()}</span></div>`).join("") || '<div class="dim">No sessions yet.</div>'}
      <div class="pm-note dim">${done ? "Deep history fully loaded (last 30 days)." : `Backfill is walking further back in time every ~15s${deep.at ? ` · last batch ${ago(deep.at)}` : ""} — more sessions means more trades per fold and a training signal that means something.`}${Object.keys(series).length ? "" : ""}</div>
      <div class="pm-note dim">Live ticks: while the engine runs it records the live share price every ~4s, so newer sessions have far finer price data than the ~1-per-minute history.</div>`);
  }

  // ------------------------------------------------------------------ champion + leaderboard
  const GROUPS = [
    ["ENTRY", ["momo_secs", "momo_min_secs", "momo_min_px", "momo_max_px", "drift_window_secs", "momo_pct", "max_flip", "min_margin", "min_lead"]],
    ["EXIT", ["tp_pct", "tp_px", "trail_trigger_pct", "trail_giveback_pct", "sl_pct", "hold_max_flip", "hard_sl_pct", "bank_flip_min", "exit_secs", "exit_loss_pct"]],
    ["RISK MODEL", ["lock_sigma", "min_move", "guard_z"]],
  ];
  function paramsHtml(params) {
    return GROUPS.map(([g, keys]) => `<div class="pm-pg"><div class="pm-pgh">${g}</div>${keys.filter((k) => k in params).map((k) => {
      const d = PM.defaults[k], v = params[k], changed = isN(d) && Math.abs(d - v) > 1e-9;
      return `<div class="pm-pr ${changed ? "chg" : ""}" title="${changed ? `default ${d}` : "default"}"><span>${k}</span><b>${typeof v === "number" ? +v.toFixed(4) : v}</b></div>`;
    }).join("")}</div>`).join("");
  }
  function metricsRow(label, m, extra = "") {
    if (!m) return "";
    return `<tr><td class="dim">${label}</td><td class="r">${m.total_trades ?? m.n ?? "—"}</td><td class="r">${isN(m.win_rate_pct) ? m.win_rate_pct.toFixed(0) + "%" : "—"}</td><td class="r">${isN(m.profit_factor) ? m.profit_factor.toFixed(2) : "—"}</td><td class="r ${cls(m.net_pnl)}">${smoney(m.net_pnl)}</td><td class="r">${isN(m.edge_t_stat) ? m.edge_t_stat.toFixed(2) : "—"}</td>${extra}</tr>`;
  }
  function renderChamp() {
    const s = PM.status; if (!s) return;
    const c = s.champion;
    if (!c) {
      const st = s.state;
      paint("#pm-champ", `<div class="bb-empty"><b>NO CHAMPION YET</b>A champion must be profitable out-of-sample, clear the overfitting gates, AND be profitable on a holdout of the newest sessions the search never saw.<br>${isN(st.best_score_all) ? `Best candidate so far scored ${num(st.best_score_all)} but has not cleared the holdout.` : "Candidates need at least 30 out-of-sample trades to be scored at all — the corpus is still growing."}<br><span class="dim">${esc(st.last_reason || "")}</span></div>`);
      return;
    }
    const ho = c.holdout || {};
    paint("#pm-champ", `
      <div class="pm-champ-h"><b>v${c.version}</b><span class="dim">promoted ${esc(c.promoted_at || "")} UTC</span><span class="pm-tag">score ${num(c.score)}</span><span class="pm-tag ${ho.ok ? "ok" : "bad"}">HOLDOUT ${ho.ok ? "PASSED" : "NOT MET"}</span></div>
      <table class="bb-tbl pm-mt"><thead><tr><th></th><th class="r">TRADES</th><th class="r">WIN</th><th class="r">PF</th><th class="r">NET</th><th class="r">T-STAT</th></tr></thead><tbody>
        ${metricsRow("OUT-OF-SAMPLE", c.test_metrics)}${metricsRow("HOLDOUT (unseen)", ho.n !== undefined ? { total_trades: ho.n, win_rate_pct: ho.win_rate_pct, profit_factor: ho.profit_factor, net_pnl: ho.net_pnl, edge_t_stat: ho.edge_t_stat } : null)}${metricsRow("IN-SAMPLE", c.train_metrics)}
      </tbody></table>
      <div class="pm-params">${paramsHtml(c.params)}</div>
      <div class="pm-note dim">Amber = tuned away from the source bot's defaults. The live paper account "AUTO · CHAMPION" trades exactly this set.</div>`);
  }
  function renderBoard() {
    const s = PM.status; if (!s) return;
    const run = s.latest_run;
    if (!run || !run.top.length) {
      paint("#pm-board", `<div class="bb-empty"><b>NO SCORED CANDIDATES YET</b>${esc(s.state.last_reason || "Waiting for the first search cycle that produces a candidate with enough out-of-sample trades.")}</div>`);
      return;
    }
    const rows = run.top.map((r, i) => {
      const m = r.test_metrics || {}, flags = [r.diverged && "diverged", r.too_smooth && "too smooth", r.exceeds_burn_limit && "drawdown"].filter(Boolean);
      return `<tr><td class="dim">${i + 1}</td><td class="r ${cls(r.score)}"><b>${num(r.score)}</b></td><td class="r">${m.total_trades ?? "—"}</td><td class="r">${isN(m.win_rate_pct) ? m.win_rate_pct.toFixed(0) + "%" : "—"}</td><td class="r">${isN(m.profit_factor) ? m.profit_factor.toFixed(2) : "—"}</td><td class="r ${cls(m.net_pnl)}">${smoney(m.net_pnl)}</td><td class="r">${r.profitable_folds ?? "—"}/3</td><td>${flags.length ? `<span class="dn">${flags.join(", ")}</span>` : '<span class="up">clean</span>'}</td></tr>`;
    }).join("");
    paint("#pm-board", `<table class="bb-tbl"><thead><tr><th>#</th><th class="r">SCORE</th><th class="r">OOS TRADES</th><th class="r">WIN</th><th class="r">PF</th><th class="r">NET $</th><th class="r">FOLDS +</th><th>GATES</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="pm-note dim">Run #${run.id} · ${esc(run.created_at)} UTC · ${(run.n_sessions || 0).toLocaleString()} sessions. Score = pooled out-of-sample t-statistic, penalised for inconsistency across folds. Slippage of 1¢ per fill is included.</div>`);
  }
  function renderLog() {
    const s = PM.status; if (!s) return;
    const rows = (s.attempts || []).map((a) => `<tr><td class="dim">${esc((a.ts || "").slice(5, 16))}</td><td><span class="pm-tag ${a.outcome === "promoted" ? "ok" : a.outcome === "demoted" ? "bad" : ""}">${esc(a.outcome)}</span></td><td>${esc(a.reason || "")}</td><td class="r">${isN(a.cand_score) ? num(a.cand_score) : ""}</td><td class="r">${isN(a.inc_score) ? num(a.inc_score) : ""}</td></tr>`).join("");
    paint("#pm-log", rows ? `<table class="bb-tbl pm-wrap"><thead><tr><th>TIME (UTC)</th><th>OUTCOME</th><th>WHY</th><th class="r">CHALLENGER</th><th class="r">CHAMPION</th></tr></tbody></thead><tbody>${rows}</tbody></table>` : '<div class="dim">No promotion decisions yet.</div>');
  }

  // ------------------------------------------------------------------ paper accounts
  function acctParams(a) { try { return JSON.parse(a.strategy_params_json || "{}"); } catch (e) { return {}; } }
  function positionHtml(a, p) {
    const row = PM.radar.find((r) => r.slug === p.session_slug);
    const params = acctParams(a);
    const cur = row ? (p.side === "up" ? row.up_price : row.down_price) : null;
    const left = p.end_ts - Date.now() / 1000;
    const pnlPct = isN(cur) && p.entry_price ? (cur / p.entry_price - 1) * 100 : null;
    const tp = params.tp_pct || 40, sl = params.sl_pct || 40;
    const prog = { toTarget: isN(pnlPct) ? Math.max(0, Math.min(100, (pnlPct / tp) * 100)) : 0, toStop: isN(pnlPct) ? Math.max(0, Math.min(100, (-pnlPct / sl) * 100)) : 0 };
    const usd = isN(cur) ? (cur - p.entry_price) * p.shares : null;
    return `<div class="pm-pos"><div class="pm-pos-h"><b>${esc(p.asset.toUpperCase())} ${esc(p.side.toUpperCase())}</b><span class="dim">@ ${px(p.entry_price)} → ${px(cur)}</span><span class="${cls(pnlPct)}">${pct(pnlPct)} ${isN(usd) ? `(${smoney(usd)})` : ""}</span><span class="dim">${left > 0 ? dur(left) + " left" : "settling…"}</span></div>${typeof tpslBarsHtml === "function" ? tpslBarsHtml(prog) : ""}</div>`;
  }
  function renderAccounts() {
    const list = PM.accounts;
    if (!list.length) { paint("#pm-accts", '<div class="bb-empty"><b>NO PAPER ACCOUNTS YET</b>The auto accounts are created after the trainer starts — give it a few seconds.</div>'); return; }
    const card = (a) => {
      const role = a.role || (a.is_auto ? "auto" : "manual");
      const opens = (a.open_positions || []).map((p) => positionHtml(a, p)).join("");
      return `<div class="pm-acct ${a.status === "live" ? "live" : ""} ${a.is_archived ? "arch" : ""}">
        <div class="pm-acct-h"><b>${esc(a.name.replace("AUTO · ", ""))}</b><span class="pm-role ${esc(role)}">${esc(role.toUpperCase())}</span><span class="pm-status ${a.is_archived ? "arch" : a.status}">${a.is_archived ? "ARCHIVED" : a.status.toUpperCase()}</span></div>
        <div class="pm-acct-bal"><span>${money(a.balance)}</span><em class="${cls(a.pnl)}">${smoney(a.pnl)} · ${pct(a.pnl_pct)}</em></div>
        ${spark(a.curve || [])}
        <div class="pm-acct-stats"><span><b>${a.n_trades}</b> trades</span><span><b>${a.n_trades ? a.win_rate.toFixed(0) + "%" : "—"}</b> win</span><span>PF <b>${isN(a.profit_factor) ? a.profit_factor.toFixed(2) : "—"}</b></span><span>avg <b class="${cls(a.avg_pnl)}">${a.n_trades ? smoney(a.avg_pnl) : "—"}</b></span></div>
        ${opens || `<div class="pm-flat dim">${a.status === "live" ? "flat — watching for a momo setup" : "not trading"}</div>`}
        <div class="pm-acct-actions">${a.is_archived ? "" : (a.status === "live" ? `<button class="btn btn-small" data-stop="${a.id}">STOP</button>` : `<button class="btn btn-small" data-live="${a.id}" ${a.strategy ? "" : "disabled"}>GO LIVE</button>`)}${a.is_auto ? "" : `<button class="btn btn-small" data-reset="${a.id}">RESET</button><button class="btn btn-small danger" data-del="${a.id}">DEL</button>`}<button class="btn btn-small" data-trades="${a.id}">TRADES</button></div>
      </div>`;
    };
    paint("#pm-accts", `<div class="pm-toolbar"><button id="pm-new" class="btn btn-small">+ NEW PAPER ACCOUNT</button><label class="pm-chk"><input type="checkbox" id="pm-arch" ${PM.showArch ? "checked" : ""}/> show archived (rotated champions/candidates)</label></div><div class="pm-cards">${list.map(card).join("")}</div><div id="pm-acct-trades"></div>`);
    bindAccounts();
  }
  async function act(id, path, method = "POST") {
    try { await api(`/polymarket/accounts/${id}${path}`, { method }); refreshAccounts(); } catch (e) { showToast(String(e.message || e), true); }
  }
  function bindAccounts() {
    const box = $("#pm-accts");
    box.querySelectorAll("[data-live]").forEach((b) => (b.onclick = () => act(b.dataset.live, "/go-live")));
    box.querySelectorAll("[data-stop]").forEach((b) => (b.onclick = () => act(b.dataset.stop, "/stop-live")));
    box.querySelectorAll("[data-reset]").forEach((b) => (b.onclick = () => { if (confirm("Reset this account (clears its trades)?")) act(b.dataset.reset, "/reset"); }));
    box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => { if (confirm("Delete this paper account?")) act(b.dataset.del, "", "DELETE"); }));
    box.querySelectorAll("[data-trades]").forEach((b) => (b.onclick = () => showTrades(b.dataset.trades)));
    const nw = $("#pm-new");
    if (nw) nw.onclick = async () => {
      const name = prompt("Account name", `momo-${Date.now() % 10000}`); if (!name) return;
      try { const a = await api("/polymarket/accounts", { method: "POST", body: JSON.stringify({ name, starting_balance: 1000 }) }); await api(`/polymarket/accounts/${a.id}/strategy`, { method: "POST", body: JSON.stringify({ strategy: "momo" }) }); await api(`/polymarket/accounts/${a.id}/go-live`, { method: "POST" }); showToast(`${name} is live`); refreshAccounts(); } catch (e) { showToast(String(e.message || e), true); }
    };
    const ar = $("#pm-arch");
    if (ar) ar.onchange = () => { PM.showArch = ar.checked; PM.last["#pm-accts"] = null; refreshAccounts(); };
  }
  async function showTrades(id) {
    const box = $("#pm-acct-trades"); if (!box) return;
    try {
      const tr = (await api(`/polymarket/accounts/${id}/trades`)).slice(-40).reverse();
      box.innerHTML = `<div class="pm-sub">LAST ${tr.length} TRADES — ${esc((PM.accounts.find((a) => String(a.id) === String(id)) || {}).name || "")}</div>` + (tr.length ? `<table class="bb-tbl"><thead><tr><th>CLOSED</th><th>ASSET</th><th>SIDE</th><th class="r">ENTRY</th><th class="r">EXIT</th><th class="r">P&amp;L</th><th>REASON</th></tr></thead><tbody>${tr.map((t) => `<tr class="click" data-slug="${esc(t.session_slug)}"><td class="dim">${esc(String(t.exit_time).slice(5, 19).replace("T", " "))}</td><td><b>${esc(t.asset.toUpperCase())}</b></td><td>${esc(t.side.toUpperCase())}</td><td class="r">${px(t.entry_price)}</td><td class="r">${px(t.exit_price)}</td><td class="r ${cls(t.pnl)}">${smoney(t.pnl)}</td><td class="dim">${esc(t.exit_reason)}</td></tr>`).join("")}</tbody></table>` : '<div class="dim">No trades yet.</div>');
      box.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => loadChart(tr.dataset.slug)));
    } catch (e) { box.innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ radar + feed
  const GATE_TXT = {
    "out-of-window": "not in the entry window yet", "price-band": "price outside entry band", "fav-behind": "favourite is behind spot",
    "no-drift": "not enough trace yet", "lead-too-small": "lead too small", "weak-momentum": "momentum too weak", "reverting": "lead is reverting",
    "thin-margin": "lead thin vs required move", "mc-flip-high": "flip risk too high", "no-price": "no quote", "wide-spread": "book too thin (wide spread)", "no-spot-read": "no spot read",
  };
  function renderRadar() {
    const rows = PM.radar;
    if (!rows.length) { paint("#pm-radar", '<div class="bb-empty"><b>NO RUNNING SESSIONS</b>Waiting for the next 5-minute window.</div>'); return; }
    const body = rows.map((r) => {
      const f = r.features || {}, gs = r.gate_status || "", enter = gs.startsWith("entered"), key = gs.replace("skip:", "");
      const left = Math.max(0, r.end_ts - Date.now() / 1000), frac = Math.min(1, left / 300);
      return `<tr class="click ${PM.sel === r.slug ? "sel" : ""}" data-slug="${esc(r.slug)}">
        <td class="sym">${esc(r.asset.toUpperCase())}</td>
        <td><div class="pm-cd"><i style="width:${(frac * 100).toFixed(0)}%"></i></div><span class="dim">${dur(left)}</span></td>
        <td class="r">${px(r.up_price)}</td><td class="r">${px(r.down_price)}</td>
        <td class="r ${cls(r.move_pct)}">${isN(r.move_pct) ? pct(r.move_pct, 3) : "—"}</td>
        <td class="r dim">${isN(f.drift_fav) ? pct(f.drift_fav, 3) : "—"}</td>
        <td class="r ${isN(f.mc_flip) && f.mc_flip > 0.25 ? "dn" : ""}">${isN(f.mc_flip) ? (f.mc_flip * 100).toFixed(0) + "%" : "—"}</td>
        <td class="${enter ? "up" : "dim"}">${enter ? "● ENTRY SIGNAL " + esc(gs.split(":")[1].toUpperCase()) : esc(GATE_TXT[key] || key)}</td>
        <td>${(r.held_by || []).map((n) => `<span class="pm-tag ok">${esc(n)}</span>`).join(" ")}</td></tr>`;
    }).join("");
    paint("#pm-radar", `<table class="bb-tbl"><thead><tr><th>ASSET</th><th>LEFT</th><th class="r">UP</th><th class="r">DOWN</th><th class="r">MOVE</th><th class="r">DRIFT</th><th class="r">FLIP</th><th>GATE</th><th>HELD BY</th></tr></thead><tbody>${body}</tbody></table>`);
    $("#pm-radar").querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => loadChart(tr.dataset.slug)));
  }
  function renderFeed(trades) {
    if (!trades.length) { paint("#pm-feed", '<div class="bb-empty"><b>NO TRADES YET</b>Paper trades appear here as they close.</div>'); return; }
    paint("#pm-feed", `<table class="bb-tbl"><thead><tr><th>CLOSED</th><th>ACCOUNT</th><th>TRADE</th><th class="r">P&amp;L</th><th>EXIT</th></tr></thead><tbody>${trades.map((t) => `<tr class="click" data-slug="${esc(t.session_slug)}"><td class="dim">${esc(String(t.exit_time).slice(11, 19))}</td><td class="dim">${esc((t.account_name || "").replace("AUTO · ", ""))}</td><td><b>${esc(t.asset.toUpperCase())}</b> ${esc(t.side.toUpperCase())} ${px(t.entry_price)}→${px(t.exit_price)}</td><td class="r ${cls(t.pnl)}"><b>${smoney(t.pnl)}</b></td><td class="dim">${esc(t.exit_reason)}</td></tr>`).join("")}</tbody></table>`);
    $("#pm-feed").querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => loadChart(tr.dataset.slug)));
  }

  // ------------------------------------------------------------------ session chart
  async function loadChart(slug) {
    PM.sel = slug;
    const host = $("#pm-chart-host"); if (!host) return;
    try {
      const data = await api(`/polymarket/sessions/${encodeURIComponent(slug)}/chart`);
      if (PM.chart) { try { PM.chart.remove(); } catch (e) { /* gone */ } }
      PM.chart = NX.pmChart.renderSessionChart(host, data);
      const ctl = $("#pm-chart-controls"), s = data.session;
      if (ctl) ctl.innerHTML = `<b>${esc(slug)}</b> · strike ${px(s.strike_price)} ${s.resolved_up === null || s.resolved_up === undefined ? '<span class="up">· LIVE</span>' : `· resolved ${s.resolved_up ? "UP" : "DOWN"}`} · ${data.up_price.length} price ticks · ${data.trades.length} paper trades`;
      renderRadar();
    } catch (e) { host.innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ copytrader
  function newCopyForm() {
    const box = $("#pm-copy-new"); if (!box) return;
    box.innerHTML = `<div class="pm-newrow"><input id="pm-copy-name" placeholder="Label" style="width:120px"/><input id="pm-copy-wallet" placeholder="0x wallet address" style="width:300px"/><input id="pm-copy-stake" type="number" value="5" title="Stake per shadowed trade ($)" style="width:64px"/><button id="pm-copy-create" class="btn btn-small">+ TRACK WALLET</button></div>`;
    $("#pm-copy-create", box).onclick = async () => {
      const name = $("#pm-copy-name", box).value.trim() || "wallet", wallet = $("#pm-copy-wallet", box).value.trim(), stake = Number($("#pm-copy-stake", box).value) || 5;
      if (!/^0x[0-9a-fA-F]{40}$/.test(wallet)) return showToast("Enter a valid 0x wallet address", true);
      try { await api("/polymarket/copy-accounts", { method: "POST", body: JSON.stringify({ name, wallet_address: wallet, stake_usd: stake }) }); showToast(`Tracking ${name}`); refreshCopy(); } catch (e) { showToast(String(e.message || e), true); }
    };
  }
  async function copyAct(id, path, method = "POST") { try { await api(`/polymarket/copy-accounts/${id}${path}`, { method }); refreshCopy(); } catch (e) { showToast(String(e.message || e), true); } }
  async function refreshCopy() {
    try {
      const list = await api("/polymarket/copy-accounts");
      paint("#pm-copy-accounts", !list.length ? '<div class="dim">No tracked wallets yet — add one above.</div>' : `<table class="bb-tbl"><thead><tr><th>LABEL</th><th>WALLET</th><th class="r">BALANCE</th><th class="r">STAKE</th><th>STATUS</th><th></th></tr></thead><tbody>${list.map((a) => `<tr><td><b>${esc(a.name)}</b></td><td class="dim">${esc(a.wallet_address.slice(0, 8))}…${esc(a.wallet_address.slice(-5))}</td><td class="r">${money(a.balance)}</td><td class="r">${money(a.stake_usd)}</td><td><span class="pm-status ${a.status}">${a.status.toUpperCase()}</span></td><td class="pm-actions">${a.status === "live" ? `<button class="btn btn-small" data-cs="${a.id}">STOP</button>` : `<button class="btn btn-small" data-cl="${a.id}">TRACK</button>`}<button class="btn btn-small" data-cp="${a.id}">POLL</button><button class="btn btn-small danger" data-cd="${a.id}">DEL</button></td></tr>`).join("")}</tbody></table>`);
      const box = $("#pm-copy-accounts");
      box.querySelectorAll("[data-cl]").forEach((b) => (b.onclick = () => copyAct(b.dataset.cl, "/go-live")));
      box.querySelectorAll("[data-cs]").forEach((b) => (b.onclick = () => copyAct(b.dataset.cs, "/stop-live")));
      box.querySelectorAll("[data-cp]").forEach((b) => (b.onclick = () => copyAct(b.dataset.cp, "/poll")));
      box.querySelectorAll("[data-cd]").forEach((b) => (b.onclick = () => { if (confirm("Stop tracking this wallet?")) copyAct(b.dataset.cd, "", "DELETE"); }));
    } catch (e) { const b = $("#pm-copy-accounts"); if (b) b.innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ field (ScatterField reuse)
  const FIELD = { chart: null, lastId: 0, busy: false };
  async function loadField(fresh) {
    const data = await api(`/pm/field?hours=48${fresh ? "" : `&after_id=${FIELD.lastId}`}`);
    if (fresh) FIELD.chart.setPoints(data.points, false); else FIELD.chart.addPoints(data.points);
    FIELD.lastId = Math.max(FIELD.lastId, data.last_id);
    const s = data.stats, box = $("#pm-field-stats");
    if (box) box.textContent = s.field ? `${s.field.toLocaleString()} candidates scored in the last 48h · ${s.survivors} survivors (${s.survivor_pct}%) · ${s.cycles} cycles — up-and-to-the-right is better` : "No candidates scored yet — the Field fills as training cycles complete.";
  }
  function initField() {
    if (typeof ScatterField === "undefined" || FIELD.chart) return;
    const tip = $("#pm-field-tip");
    FIELD.chart = new ScatterField("pm-field-chart", {
      onHover: (p) => { if (!tip) return; if (!p) { tip.classList.add("hidden"); return; } tip.classList.remove("hidden"); tip.innerHTML = `win ${p.wrN.toFixed(1)}% · RR ${p.rrN == null ? "—" : p.rrN.toFixed(2)} · avg ${p.eN >= 0 ? "+" : ""}${p.eN.toFixed(3)}$ · ${p.n} trades`; },
    });
    loadField(true).catch(() => {});
  }

  // ------------------------------------------------------------------ REAL account
  const REAL = { st: null, trades: [], editing: false };
  async function realCall(path, body, method = "POST") {
    const res = await fetch(`/api/polymarket/real${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${res.status}`);
    return data;
  }
  async function refreshReal() {
    if (REAL.editing) return;
    try {
      const [st, tr] = await Promise.all([realCall("/status", null, "GET"), realCall("/trades?limit=25", null, "GET")]);
      REAL.st = st; REAL.trades = tr; renderReal();
    } catch (e) { paint("#pm-real", NX.err(e)); }
  }
  function realForm() {
    return `<div class="pm-real-form">
      <div class="dim" style="margin-bottom:8px">Connect the Polymarket account that will place the real $1 momo trades. The key is stored only on this PC (in your user folder, outside the project), is never shown again, and can be wiped with DISCONNECT. Use a wallet that holds only what you are willing to risk.</div>
      <div class="pm-newrow"><input id="pm-rk" type="password" placeholder="wallet private key (0x…)" style="width:330px" autocomplete="off"/>
        <input id="pm-rf" placeholder="Polymarket profile / proxy address (0x…)" style="width:330px"/>
        <select id="pm-rs" title="Signature type"><option value="1">1 · Polymarket email / Magic account</option><option value="2">2 · browser wallet (Safe proxy)</option><option value="3">3 · deposit wallet</option><option value="0">0 · plain wallet (no proxy)</option></select></div>
      <div class="pm-help">
        <b>WHERE TO GET THESE</b>
        <ol>
          <li><b>Private key</b> — signed up with email / Google on Polymarket: export it at <a href="https://reveal.magic.link/polymarket" target="_blank" rel="noopener">reveal.magic.link/polymarket</a> (log in with the same email). Using MetaMask or another wallet: export the key from that wallet's account settings. Never share it with anyone.</li>
          <li><b>Profile / proxy address</b> — open <a href="https://polymarket.com/settings" target="_blank" rel="noopener">polymarket.com/settings</a> (or your profile) and copy the wallet address shown there; that is the address that holds your USDC. Pick signature type 1 for an email / Google account, 2 for a browser-wallet account.</li>
          <li><b>No separate API key is needed</b> — the trading API credentials are created automatically from the private key when you press CONNECT &amp; TEST. Already using the PolyMarket bot? Press IMPORT and nothing has to be pasted at all.</li>
        </ol>
      </div>
      <div class="pm-newrow"><button id="pm-rconn" class="btn btn-small">CONNECT &amp; TEST</button><button id="pm-rimp" class="btn btn-small">IMPORT FROM THE POLYMARKET BOT (.env)</button></div></div>`;
  }
  function renderReal() {
    const s = REAL.st; if (!s) return;
    if (!s.connected) { if (!$("#pm-rk")) { PM.last["#pm-real"] = null; paint("#pm-real", realForm()); bindReal(); } return; }
    const set = s.settings, t = s.today || {}, armed = set.armed;
    const opens = (s.open_positions || []).map((p) => `<tr><td><b>${esc(p.asset.toUpperCase())} ${esc(p.side.toUpperCase())}</b></td><td class="r">${px(p.entry_price)}</td><td class="r">${num(p.shares)} sh</td><td class="r">${money(p.cost)}</td><td class="dim">${dur(p.end_ts - Date.now() / 1000)} left</td></tr>`).join("");
    const rows = REAL.trades.filter((x) => x.status === "closed").slice(0, 12).map((x) => `<tr><td class="dim">${esc(String(x.closed_at || "").slice(5, 19).replace("T", " "))}</td><td><b>${esc(x.asset.toUpperCase())}</b> ${esc(x.side.toUpperCase())} ${px(x.entry_price)}→${px(x.exit_price)}</td><td class="r ${cls(x.pnl)}"><b>${smoney(x.pnl)}</b></td><td class="dim">${esc(x.exit_reason || "")}${x.settled_estimate ? " (est.)" : ""}</td></tr>`).join("");
    paint("#pm-real", `
      <div class="pm-real-top">
        <div><span class="pm-tag ${armed ? "ok" : ""}">${armed ? "● ARMED — TRADING REAL MONEY" : "DISARMED"}</span> <span class="dim">${esc(s.address ? s.address.slice(0, 8) + "…" + s.address.slice(-6) : "")} · type ${s.signature_type}</span></div>
        <div class="pm-real-kpis"><span><b class="${isN(s.balance) ? "" : "dn"}">${isN(s.balance) ? money(s.balance) : "n/a"}</b> USDC</span><span><b class="${cls(t.pnl)}">${smoney(t.pnl)}</b> today (${t.trades || 0} trades)</span><span><b class="${cls(t.all_time_pnl)}">${smoney(t.all_time_pnl)}</b> all-time</span><span>loss allowance left <b>${money(t.loss_remaining)}</b></span></div>
      </div>
      ${s.disarm_reason ? `<div class="pm-note dn">Auto-disarmed: ${esc(s.disarm_reason)}</div>` : ""}
      ${s.last_error ? `<div class="pm-note dim">last error: ${esc(String(s.last_error).slice(0, 220))}</div>` : ""}
      <div class="pm-real-set">
        <label>STAKE $<input id="pm-r-stake" type="number" step="0.5" min="1" max="${s.hard_max_stake}" value="${set.stake_usd}"/></label>
        <label>TAKE-PROFIT % <input id="pm-r-tpmin" type="number" value="${set.tp_min_pct}"/>–<input id="pm-r-tpmax" type="number" value="${set.tp_max_pct}"/></label>
        <label>DAILY LOSS STOP $<input id="pm-r-loss" type="number" step="1" value="${set.daily_loss_limit}"/></label>
        <label>ASSETS <input id="pm-r-assets" value="${esc((set.assets || []).join(","))}" style="width:120px"/></label>
        <button id="pm-rsave" class="btn btn-small">SAVE</button>
      </div>
      <div class="pm-newrow">
        ${armed ? '<button id="pm-rdis" class="btn btn-small">DISARM</button>' : '<button id="pm-rarm" class="btn btn-small">ARM REAL TRADING</button>'}
        <button id="pm-rpanic" class="btn btn-small danger">PANIC · SELL ALL &amp; DISARM</button>
        <button id="pm-rdisc" class="btn btn-small">DISCONNECT (wipe key)</button>
      </div>
      ${opens ? `<div class="pm-sub">OPEN REAL POSITIONS</div><table class="bb-tbl"><tbody>${opens}</tbody></table>` : ""}
      <div class="pm-sub">RECENT REAL TRADES</div>${rows ? `<table class="bb-tbl"><tbody>${rows}</tbody></table>` : '<div class="dim">No real trades yet. Momo enters only when its gates pass — that can take a while.</div>'}
      <div class="pm-note dim">Each trade risks the stake; take-profit is clipped to the range above. Settlement P&amp;L is estimated from Polymarket's resolved outcome. Real trades alert on Telegram; paper trades don't.</div>`);
    bindReal();
  }
  function bindReal() {
    const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
    const wrap = (fn) => async () => { try { await fn(); await refreshReal(); } catch (e) { showToast(String(e.message || e), true); } };
    ["#pm-rk", "#pm-rf"].forEach((id) => { const el = $(id); if (el) { el.onfocus = () => (REAL.editing = true); el.onblur = () => (REAL.editing = false); } });
    on("#pm-rconn", wrap(async () => { const r = await realCall("/connect", { private_key: $("#pm-rk").value, funder: $("#pm-rf").value, signature_type: Number($("#pm-rs").value) }); REAL.editing = false; PM.last["#pm-real"] = null; showToast(r.reachable ? `Connected · balance ${money(r.balance)}` : "Connected, but the balance could not be read"); }));
    on("#pm-rimp", wrap(async () => { await realCall("/import-bot-env"); REAL.editing = false; PM.last["#pm-real"] = null; showToast("Imported the bot's wallet"); }));
    on("#pm-rsave", wrap(async () => { await realCall("/settings", { stake_usd: Number($("#pm-r-stake").value), tp_min_pct: Number($("#pm-r-tpmin").value), tp_max_pct: Number($("#pm-r-tpmax").value), daily_loss_limit: Number($("#pm-r-loss").value), assets: $("#pm-r-assets").value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean) }); showToast("Saved"); }));
    on("#pm-rarm", wrap(async () => { const set = REAL.st.settings; if (!confirm(`ARM real trading?\n\nThe bot will place REAL orders of $${set.stake_usd} each on your Polymarket account whenever momo signals, on ${set.assets.join(", ").toUpperCase()}.\nDaily loss stop: $${set.daily_loss_limit}. You can DISARM or PANIC at any time.`)) return; await realCall("/arm", { armed: true, confirm: true }); showToast("REAL trading ARMED"); }));
    on("#pm-rdis", wrap(async () => { await realCall("/arm", { armed: false }); showToast("Disarmed"); }));
    on("#pm-rpanic", wrap(async () => { if (!confirm("Sell every open real position at market and disarm?")) return; const r = await realCall("/panic"); showToast(`Panic done: ${r.closed.length} position(s) handled`); }));
    on("#pm-rdisc", wrap(async () => { if (!confirm("Wipe the stored key and disarm?")) return; await realCall("/disconnect"); PM.last["#pm-real"] = null; showToast("Disconnected"); }));
  }

  // ------------------------------------------------------------------ refreshers
  async function refreshStatus() { try { PM.status = await api("/polymarket/trainer/status"); renderTop(); renderTrain(); renderCorpus(); renderChamp(); renderBoard(); renderLog(); } catch (e) { paint("#pm-train", NX.err(e)); } }
  async function refreshDash() { try { PM.dash = await api("/polymarket/dashboard"); renderTop(); } catch (e) { /* next poll */ } }
  async function refreshAccounts() { try { PM.accounts = await api(`/polymarket/accounts?include_archived=${PM.showArch}`); renderAccounts(); } catch (e) { paint("#pm-accts", NX.err(e)); } }
  async function refreshRadar() { try { const r = await api("/polymarket/sessions/live"); PM.radar = r.rows || []; renderRadar(); if (PM.accounts.some((a) => (a.open_positions || []).length)) { PM.last["#pm-accts"] = null; renderAccounts(); } if (PM.sel && PM.radar.some((x) => x.slug === PM.sel) && PM.tick++ % 2 === 0) loadChart(PM.sel); } catch (e) { paint("#pm-radar", NX.err(e)); } }
  async function refreshFeed() { try { renderFeed(await api("/polymarket/trades/recent?limit=40")); } catch (e) { /* next poll */ } }
  async function pollField() { if (!FIELD.chart || FIELD.busy) return; FIELD.busy = true; try { await loadField(false); } catch (e) { /* next */ } finally { FIELD.busy = false; } }
  PM.tick = 0;

  function startTimers() {
    stopTimers();
    PM.timers.push(setInterval(refreshStatus, 4000), setInterval(refreshDash, 5000), setInterval(refreshAccounts, 6000), setInterval(refreshRadar, 3000),
      setInterval(refreshFeed, 6000), setInterval(pollField, 10000), setInterval(refreshCopy, 20000), setInterval(refreshReal, 8000));
  }
  function stopTimers() { PM.timers.forEach(clearInterval); PM.timers = []; }

  async function build() {
    $("#view-polymarket").innerHTML = skeleton();
    newCopyForm();
    try { const s = await api("/polymarket/strategies"); (s.strategies[0]?.params || []).forEach((p) => (PM.defaults[p.name] = p.default)); } catch (e) { /* defaults are cosmetic */ }
    await Promise.all([refreshStatus(), refreshDash(), refreshAccounts(), refreshRadar(), refreshFeed(), refreshCopy(), refreshReal()]);
    initField();
    startTimers();
    const first = PM.radar[0]; if (first) loadChart(first.slug);
  }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "polymarket") {
      if (!PM.started) { PM.started = true; build().catch((e) => { const v = $("#view-polymarket"); if (v) v.innerHTML = NX.err(e); }); }
      else if (!PM.timers.length) { startTimers(); refreshStatus(); refreshRadar(); refreshAccounts(); }
    } else stopTimers();
  };
})();
