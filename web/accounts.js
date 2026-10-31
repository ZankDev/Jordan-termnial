/* NOSTRO › ACCOUNTS — every account on one page: label (PASSED / IN EVALUATION / LIVE / FAILED), today's profit and
   trade count, eval progress, withdrawable estimate, sparkline and headline stats; a swipeable strip for accounts that
   passed their evaluation; and the read-only Tradovate link that pulls the real account's cash balance.
   Depends on dashboard.js (api(), showToast(), openAccountModal()). Lazy-built through the window.onToolsTab chain. */
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n, d = 0) => (!isN(n) ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const smoney = (n, d = 0) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const AC = { data: null, filter: "all", timers: [], started: false, tv: null, last: {}, formOpen: false };

  function paint(sel, html) {
    const el = $(sel);
    if (!el || AC.last[sel] === html) return false;
    AC.last[sel] = html;
    el.innerHTML = html;
    return true;
  }
  const kpi = (l, v, s, c = "") => `<div class="bb-kpi"><span class="l">${l}</span><span class="v ${c}">${v}</span>${s ? `<span class="s">${s}</span>` : ""}</div>`;
  const panel = (n, title, meta, id, extra = "") => `<div class="bb-panel ac-p ${extra}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${meta || ""}</span></div><div class="bb-pad" id="${id}"><div class="dim">Loading…</div></div></div>`;

  function spark(vals, up) {
    if (!vals || vals.length < 2) return `<svg class="ac-spark" viewBox="0 0 100 28" preserveAspectRatio="none"><line x1="0" y1="14" x2="100" y2="14" class="flat"/></svg>`;
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * 100).toFixed(1)},${(26 - ((v - lo) / span) * 24).toFixed(1)}`).join(" ");
    return `<svg class="ac-spark ${up ? "up" : "dn"}" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="${pts}" fill="none" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function progressBar(p) {
    if (!p) return "";
    const buf = isN(p.buffer) && isN(p.max_drawdown) && p.max_drawdown ? Math.max(0, Math.min(100, (p.buffer / p.max_drawdown) * 100)) : null;
    return `<div class="ac-prog"><div class="ac-prog-l"><span>TARGET ${money(p.target)}</span><b>${p.pct}%</b></div>
      <div class="ac-bar"><i style="width:${p.pct}%"></i></div>
      ${buf !== null ? `<div class="ac-prog-l"><span>DRAWDOWN BUFFER</span><b class="${buf < 30 ? "dn" : ""}">${money(p.buffer)}</b></div><div class="ac-bar dd"><i style="width:${buf}%"></i></div>` : ""}
      ${p.fail_reason ? `<div class="ac-fail">${esc(p.fail_reason)}</div>` : ""}</div>`;
  }

  function stat(l, v) { return `<div><span>${l}</span><b>${v}</b></div>`; }

  // where the last price sits between the stop and the target of an open trade
  function bracket(op) {
    if (!isN(op.stop) || !isN(op.target) || !isN(op.last) || op.stop === op.target) return "";
    const pct = Math.max(0, Math.min(100, (100 * (op.last - op.stop)) / (op.target - op.stop)));
    return `<div class="ac-brk" title="stop ← price → target"><i style="left:${pct}%"></i></div>`;
  }

  function card(a, big) {
    const s = a.stats || {};
    const op = a.open_position, net = a.net_pnl ?? a.profit, netPct = a.net_pct ?? a.profit_pct;
    const passed = a.label.code === "passed";
    return `<article class="ac-card ${a.label.code} ${big ? "big" : ""}" data-id="${a.id}" tabindex="0">
      ${passed ? '<div class="ac-shine"></div>' : ""}
      <header><span class="ac-badge ${a.label.code}">${passed ? "✓ " : ""}${esc(a.label.text)}</span>${a.awaiting_payout ? `<span class="ac-tag" style="color:#ffd24d;border-color:#5c4a1d" title="Reached its payout target of ${money(a.payout_target)} and stopped trading">💰 WAITING FOR PAYMENT</span>` : a.real_linked ? `<span class="ac-tag live-real" title="Real orders go through TradersPost to Tradovate${a.tradovate_account_name ? " (" + esc(a.tradovate_account_name) + ")" : ""}">🔴 LIVE — REAL MONEY</span>` : ""}${a.is_system ? '<span class="ac-tag">AUTO</span>' : ""}${a.sim_trades ? `<span class="ac-tag sim" title="${a.sim_trades} simulated trades">SIM</span>` : ""}${a.combo_member ? `<span class="ac-tag combo" title="member of a combo evaluation — the combo owns the drawdown rule">COMBO</span>` : ""}<span class="sp"></span><span class="ac-sym">${esc(a.symbol || "—")}</span></header>
      <h3 title="${esc(a.name)}">${esc(a.name)}</h3>
      <div class="ac-strat">${esc((a.strategy || "no strategy").replace(/_/g, " "))} ${a.open_position ? `<b class="ac-open">● ${esc(a.open_position.side.toUpperCase())} ×${a.open_position.qty}</b>` : ""}</div>
      <div class="ac-bal"><span>EQUITY${op ? " · LIVE" : ""}</span><b>${money(a.equity ?? a.balance)}</b><em class="${cls(net)}" data-uix-tip="NET P&L|${smoney(net)} since the start|realized ${smoney(a.profit)} (closed trades)|open ${op ? smoney(op.pnl) : "—"} (marked at the last price, after commission)">NET ${smoney(net)} (${netPct >= 0 ? "+" : ""}${netPct}%)</em></div>
      ${op ? `<div class="ac-openrow" data-uix-tip="OPEN ${esc(op.side.toUpperCase())} ×${op.qty}|entry ${op.entry_price}${op.entry_time ? " at " + esc(String(op.entry_time).slice(11, 16)) + " NY" : ""}|last ${op.last ?? "—"}${op.last_time ? " (" + esc(String(op.last_time).slice(11, 16)) + " NY)" : ""}|stop ${op.stop ?? "—"} · target ${op.target ?? "—"}"><span>OPEN ${esc(op.side.toUpperCase())} @ ${op.entry_price}</span><span class="dim">last ${op.last ?? "—"}</span><b class="${cls(op.pnl)}">${isN(op.pnl) ? smoney(op.pnl) : "—"}</b>${bracket(op)}</div>` : ""}
      ${spark(a.spark, net >= 0)}
      <div class="ac-today"><div><span>TODAY (NET)</span><b class="${cls(a.today_net ?? a.today_pnl)}" data-uix-tip="TODAY|closed ${smoney(a.today_pnl)}|open ${op ? smoney(op.pnl) : "—"}">${smoney(a.today_net ?? a.today_pnl)}</b></div><div><span>TRADES TODAY</span><b>${a.today_trades}${a.today_trades ? ` <small>${a.today_wins}W/${a.today_trades - a.today_wins}L</small>` : ""}</b></div><div><span>REALIZED</span><b class="${cls(a.profit)}">${smoney(a.profit)}</b></div></div>
      ${passed ? `<div class="ac-wd"><span>WITHDRAWABLE <small>est.</small></span><b>${money(a.withdrawable)}</b></div>` : ""}
      ${progressBar(a.progress)}
      <div class="ac-stats">${stat("TRADES", s.trades ?? 0)}${stat("WIN%", isN(s.win_rate) ? s.win_rate + "%" : "—")}${stat("PF", isN(s.profit_factor) ? s.profit_factor : "—")}${stat("AVG", isN(s.avg_trade) ? smoney(s.avg_trade) : "—")}</div>
    </article>`;
  }

  // ------------------------------------------------------------------ passed strip (swipe)
  function bindSwipe(el) {
    if (!el || el.dataset.swipe) return;
    el.dataset.swipe = "1";
    let down = false, sx = 0, sl = 0, moved = false;
    el.addEventListener("pointerdown", (e) => { down = true; moved = false; sx = e.clientX; sl = el.scrollLeft; el.classList.add("drag"); });
    window.addEventListener("pointermove", (e) => { if (!down) return; const dx = e.clientX - sx; if (Math.abs(dx) > 4) moved = true; el.scrollLeft = sl - dx; });
    window.addEventListener("pointerup", () => { if (!down) return; down = false; el.classList.remove("drag"); });
    el.addEventListener("click", (e) => { if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
    el.addEventListener("scroll", () => {
      const mid = el.getBoundingClientRect().left + el.clientWidth / 2;
      el.querySelectorAll(".ac-card").forEach((c) => { const r = c.getBoundingClientRect(); c.classList.toggle("focus", Math.abs(r.left + r.width / 2 - mid) < r.width * 0.55); });
    }, { passive: true });
    el.dispatchEvent(new Event("scroll"));
  }

  function renderPassed(list) {
    if (!list.length) {
      return paint("#ac-passed", `<div class="ac-empty"><b>No account has passed its evaluation yet.</b><span>The moment one hits its profit target without breaching the drawdown floor it lands here with a PASSED stamp and its withdrawable amount.</span></div>`);
    }
    const changed = paint("#ac-passed", `<div class="ac-swipe" id="ac-swipe">${list.map((a) => card(a, true)).join("")}</div><div class="ac-hint">‹ swipe or drag ›</div>`);
    bindSwipe($("#ac-swipe"));
    return changed;
  }

  // ------------------------------------------------------------------ all accounts
  const FILTERS = [["all", "ALL"], ["live", "LIVE"], ["eval", "IN EVAL"], ["passed", "PASSED"], ["failed", "FAILED"], ["idle", "IDLE"]];
  function renderAll() {
    const d = AC.data;
    if (!d) return;
    const counts = {}; d.accounts.forEach((a) => { counts[a.label.code] = (counts[a.label.code] || 0) + 1; });
    counts.all = d.accounts.length;
    const chips = FILTERS.map(([k, t]) => `<button class="bb-chip ${AC.filter === k ? "on" : ""}" data-f="${k}">${t} ${counts[k] ?? 0}</button>`).join("");
    const list = d.accounts.filter((a) => AC.filter === "all" || a.label.code === AC.filter);
    const html = `<div class="ac-chips">${chips}</div><div class="ac-grid">${list.map((a) => card(a, false)).join("") || '<div class="dim">Nothing in this group.</div>'}</div>`;
    if (paint("#ac-all", html)) {
      $("#ac-all").querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { AC.filter = b.dataset.f; renderAll(); }));
    }
  }

  function renderTop() {
    const t = AC.data.totals;
    paint("#ac-top", [
      kpi("ACCOUNTS", t.accounts, `${t.live} live`),
      kpi("PASSED", t.passed, `${t.in_eval} in evaluation · ${t.failed} failed`, t.passed ? "up" : ""),
      kpi("TOTAL EQUITY", money(t.equity), `balance ${money(t.balance ?? t.equity)} + open`),
      kpi("NET P&L", smoney(t.net_pnl ?? 0), `realized ${smoney(t.realized ?? 0)} · open ${smoney(t.open_pnl ?? 0)}`, cls(t.net_pnl)),
      kpi("OPEN P&L", smoney(t.open_pnl ?? 0), `${t.open_positions ?? 0} open trades · last price`, cls(t.open_pnl)),
      kpi("TODAY (NET)", smoney(t.today_net ?? t.today_pnl), `${AC.data.date} · closed ${smoney(t.today_pnl)}`, cls(t.today_net ?? t.today_pnl)),
      kpi("TRADES TODAY", t.today_trades, "closed today, NY time"),
      kpi("WITHDRAWABLE", money(t.withdrawable), "passed accounts · estimate", t.withdrawable ? "up" : ""),
    ].join(""));
  }

  // ------------------------------------------------------------------ combo evaluations
  function comboCard(c) {
    const st = c.status === "passed" ? ["✓ PASSED", "passed"] : c.status === "failed" ? ["FAILED", "failed"] : ["IN EVALUATION", "eval"];
    const r = c.research || {};
    const buf = c.max_drawdown ? Math.max(0, Math.min(100, (c.buffer / c.max_drawdown) * 100)) : 0;
    return `<article class="ac-card ${st[1]} ac-combo">
      ${c.status === "passed" ? '<div class="ac-shine"></div>' : ""}
      <header><span class="ac-badge ${st[1]}">${st[0]}</span><span class="ac-tag combo">COMBO</span><span class="sp"></span><span class="ac-sym">${c.members.length} STRATEGIES</span></header>
      <h3 title="${esc(c.name)}">${esc(c.name)}</h3>
      <div class="ac-bal"><span>COMBINED EQUITY</span><b>${money(c.equity)}</b><em class="${cls(c.profit)}">${smoney(c.profit)}</em></div>
      <div class="ac-prog"><div class="ac-prog-l"><span>TARGET ${money(c.profit_target)}</span><b>${c.progress_pct}%</b></div><div class="ac-bar"><i style="width:${c.progress_pct}%"></i></div>
        <div class="ac-prog-l"><span>DRAWDOWN BUFFER</span><b class="${buf < 30 ? "dn" : ""}">${money(c.buffer)}</b></div><div class="ac-bar dd"><i style="width:${buf}%"></i></div>${c.fail_reason ? `<div class="ac-fail">${esc(c.fail_reason)}</div>` : ""}</div>
      <div class="ac-cm">${c.members.map((m) => `<div class="ac-cmr" data-id="${m.id}"><span>${esc((m.strategy || "").replace(/_/g, " "))} ${esc(m.symbol)}${m.open ? ' <b class="ac-open">●</b>' : ""}</span><span class="dim">$${Math.round(m.risk || 0)}/trade</span><b class="${cls(m.pnl)}">${smoney(m.pnl)}</b></div>`).join("")}</div>
      ${isN(r.pass) ? `<div class="ac-stats"><div data-uix-tip="RESEARCH|long-history Monte Carlo when launched"><span>SIM PASS</span><b>${r.pass}%</b></div><div><span>SIM BREACH</span><b>${r.breach}%</b></div><div><span>TRADES</span><b>${c.trades}</b></div><div><span>SINCE</span><b>${esc(String(c.created_at).slice(5, 10))}</b></div></div>` : ""}
    </article>`;
  }
  async function refreshCombos() {
    try {
      const r = await fetch("/api/nostro/combo-evals").then((x) => x.json());
      AC.combos = r.combos || [];
      const html = AC.combos.length ? `<div class="ac-grid">${AC.combos.map(comboCard).join("")}</div>`
        : `<div class="ac-empty"><b>No combo evaluation running.</b><span>Over 3–6 years, two uncorrelated profitable strategies sharing one $50k account passed the evaluation ~4× as often as the best single strategy. Press LAUNCH BEST COMBO to start one (paper).</span></div>`;
      if (paint("#ac-combos", html)) document.querySelectorAll("#ac-combos .ac-cmr").forEach((row) => row.addEventListener("click", (e) => { e.stopPropagation(); window.openAccountModal && openAccountModal(Number(row.dataset.id)); }));
    } catch (e) { /* cosmetic */ }
  }

  function paintData() {
    renderTop();
    renderPassed(AC.data.accounts.filter((a) => a.label.code === "passed"));
    renderAll();
    document.querySelectorAll("#view-acct .ac-card").forEach((c) => { if (!c.dataset.bound) { c.dataset.bound = "1"; c.addEventListener("click", () => window.openAccountModal && openAccountModal(Number(c.dataset.id))); } });
  }
  async function refresh() {
    try {
      AC.data = await api("/nostro/accounts-page");
      paintData();
      try { sessionStorage.setItem("ac_last", JSON.stringify(AC.data)); } catch (x) { /* storage full / blocked */ }
    } catch (e) { if (!AC.data) { const t = $("#ac-top"); if (t) t.innerHTML = `<div class="dn">${esc(e.message || e)}</div>`; } }
  }
  // opening the page: draw the last known numbers instantly, the live refresh replaces them a moment later
  function paintCached() {
    try { const d = JSON.parse(sessionStorage.getItem("ac_last") || "null"); if (d && d.accounts) { AC.data = d; paintData(); } } catch (x) { /* ignore */ }
  }

  // ------------------------------------------------------------------ Tradovate
  async function tvCall(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(`/api/nostro/tradovate${path}`, opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    return j;
  }

  function tvForm(link) {
    const redirect = esc(link.redirect_uri);
    return `<div class="pm-help ac-help"><b>Connect your real Tradovate account — read-only (it can never place a trade)</b>
      <ol><li>In Tradovate: <b>Application Settings → API Access</b>, create an app, and add this <b>redirect URI</b>: <code id="ac-redirect">${redirect}</code> <button class="btn btn-small" id="ac-copy">COPY</button></li>
      <li>Paste the app's <b>Client ID</b> and <b>Secret</b> below, press <b>SAVE &amp; SIGN IN</b>, approve on Tradovate — the page comes back with your cash balance.</li>
      <li>Just testing? Paste an access token in the box at the bottom instead.</li></ol></div>
      <div class="ac-form">
        <label>Client ID<input id="ac-cid" value="${esc(link.client_id || "")}" autocomplete="off" placeholder="1234"/></label>
        <label>Client secret<input id="ac-sec" type="password" autocomplete="off" placeholder="${esc(link.client_secret_hint || "app secret")}"/></label>
        <label>Redirect URI<input id="ac-red" value="${redirect}"/></label>
        <label>Account<select id="ac-env"><option value="live" ${link.env === "live" ? "selected" : ""}>LIVE (real money)</option><option value="demo" ${link.env === "demo" ? "selected" : ""}>DEMO / SIM</option></select></label>
        <button class="btn" id="ac-go">SAVE &amp; SIGN IN</button>
      </div>
      <div class="ac-form ac-tok"><label>…or paste an access token<input id="ac-tok" type="password" autocomplete="off" placeholder="eyJ…"/></label><button class="btn btn-small" id="ac-tokgo">USE TOKEN</button></div>
      <div class="ac-msg dn" id="ac-msg"></div>`;
  }

  function tvBalances(b) {
    const link = b.link || AC.tv || {};
    const rows = (b.accounts || []).map((a) => `<article class="ac-card real">
      <header><span class="ac-badge real">● REAL · ${esc((b.env || link.env || "live").toUpperCase())}</span><span class="sp"></span><span class="ac-sym">#${esc(a.id)}</span></header>
      <h3>${esc(a.name)}</h3>
      <div class="ac-bal"><span>CASH BALANCE</span><b>${money(a.cash_balance, 2)}</b><em>${isN(a.net_liq) ? "net liq " + money(a.net_liq, 2) : ""}</em></div>
      <div class="ac-today"><div><span>DAY P&amp;L</span><b class="${cls(a.day_pnl)}">${smoney(a.day_pnl, 2)}</b></div><div><span>OPEN P&amp;L</span><b class="${cls(a.open_pnl)}">${smoney(a.open_pnl, 2)}</b></div></div>
      <div class="ac-stats">${stat("REALIZED", smoney(a.realized_pnl, 2))}${stat("WEEK", smoney(a.week_pnl, 2))}${stat("MARGIN", money(a.margin_used))}${stat("ACTIVE", a.active === false ? "no" : "yes")}</div></article>`).join("");
    const exp = isN(link.expires_in) ? (link.expires_in > 0 ? `session ${Math.floor(link.expires_in / 60)} min` : "expired") : "";
    return `<div class="ac-tvbar"><span class="ac-badge ${link.state === "connected" ? "passed" : "failed"}">${link.state === "connected" ? "CONNECTED" : "RECONNECT NEEDED"}</span><span class="dim">${esc(exp)} · token ${esc(link.token_hint || "")}</span><span class="sp"></span>
      <button class="btn btn-small" id="ac-refresh">REFRESH</button>${link.authorize_url ? `<a class="btn btn-small" href="${esc(link.authorize_url)}">RECONNECT</a>` : ""}<button class="btn btn-small" id="ac-disc">DISCONNECT</button></div>
      ${b.error ? `<div class="ac-msg dn">${esc(b.error)}</div>` : ""}
      <div class="ac-grid">${rows || '<div class="dim">${link.state === "connected" ? "Connected, but Tradovate returned no accounts." : "Press RECONNECT to sign in to Tradovate again."}</div>'}</div>`;
  }

  function bindTv() {
    const msg = (t) => { const m = $("#ac-msg"); if (m) m.textContent = t; };
    $("#ac-copy")?.addEventListener("click", () => { navigator.clipboard?.writeText($("#ac-redirect").textContent); showToast("Redirect URI copied"); });
    $("#ac-go")?.addEventListener("click", async () => {
      const cid = $("#ac-cid").value.trim(), sec = $("#ac-sec").value.trim();
      if (!cid || (!sec && !AC.tv?.client_secret_hint)) return msg("Client ID and secret are required.");
      try {
        const st = await tvCall("/connect", { client_id: cid, client_secret: sec || "__keep__", redirect_uri: $("#ac-red").value.trim(), env: $("#ac-env").value });
        if (st.authorize_url) location.href = st.authorize_url;
      } catch (e) { msg(e.message); }
    });
    $("#ac-tokgo")?.addEventListener("click", async () => {
      try { await tvCall("/token", { token: $("#ac-tok").value, env: $("#ac-env").value }); showToast("Token saved"); AC.last["#ac-real"] = null; refreshTv(true); } catch (e) { msg(e.message); }
    });
    $("#ac-refresh")?.addEventListener("click", () => refreshTv(true));
    $("#ac-disc")?.addEventListener("click", async () => { if (confirm("Disconnect Tradovate? Your saved token is removed (the app keys stay).")) { await tvCall("/disconnect", {}); AC.last["#ac-real"] = null; refreshTv(); } });
  }

  async function refreshTv(force) {
    try {
      const b = await tvCall(`/balances${force ? "?force=true" : ""}`);
      AC.tv = b.link;
      const connected = b.link.state === "connected" || b.link.state === "reauthorize";
      const html = connected ? tvBalances(b) : tvForm(b.link);
      if (!connected && AC.formOpen && $("#ac-cid")) return; // never wipe a form the user is typing in
      if (paint("#ac-real", html)) bindTv();
      const meta = $("#ac-real-meta"); if (meta) meta.textContent = connected ? "live cash balance from Tradovate" : "not connected";
    } catch (e) { const el = $("#ac-real"); if (el) el.innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ simulation
  async function simCall(path, post) {
    const r = await fetch(`/api/nostro${path}`, post ? { method: "POST" } : {});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    return j;
  }
  const VERDICT = { passed: ["PASSED", "passed"], failed: ["FAILED", "failed"], in_progress: ["IN PROGRESS", "eval"], "n/a": ["NO EVAL", "idle"] };
  function renderSim(st) {
    const btn = `<button class="btn" id="ac-simgo" ${st.running ? "disabled" : ""}>${st.running ? "SIMULATING…" : "▶ SIMULATE ALL ACCOUNTS"}</button>`;
    const note = `<span class="dim">Replays each account's strategy over the recent history through its own position size, daily limit and Apex target / trailing drawdown. Trades are marked SIM; accounts still in progress stay LIVE and carry on from where the simulation ends.</span>`;
    let body = "";
    if (st.running || st.done) {
      const pct = st.total ? Math.round((100 * st.done) / st.total) : 0;
      body += `<div class="ac-simbar"><div class="ac-bar"><i style="width:${pct}%"></i></div><span>${st.done}/${st.total}${st.current ? " · " + esc(st.current) : ""}</span></div>`;
    }
    const res = (st.results || []).slice().sort((a, b) => (b.net ?? -1e9) - (a.net ?? -1e9));
    if (res.length && !st.running) {
      const cnt = {}; res.forEach((r) => { cnt[r.verdict || "error"] = (cnt[r.verdict || "error"] || 0) + 1; });
      body += `<div class="ac-simsum">${Object.entries(VERDICT).map(([k, [t, c]]) => `<span class="ac-badge ${c}">${cnt[k] || 0} ${t}</span>`).join(" ")}</div>
        <div class="ac-simtbl">${res.map((r) => { const v = VERDICT[r.verdict] || ["ERROR", "failed"]; return `<div class="ac-simrow" data-id="${r.id}"><span class="ac-badge ${v[1]}">${v[0]}</span><b>${esc(r.name)}</b><span class="dim">${esc((r.strategy || "").replace(/_/g, " "))} ${esc(r.symbol || "")}</span><span class="sp"></span><span>${r.trades ?? "—"} trades</span><b class="${cls(r.net)}">${isN(r.net) ? smoney(r.net) : esc(r.error || "")}</b></div>`; }).join("")}</div>`;
    }
    return `<div class="ac-simtop">${btn}${note}</div>${body}`;
  }
  async function refreshSim() {
    try {
      const st = await simCall("/simulate-status");
      const was = AC.simRunning; AC.simRunning = st.running;
      if (paint("#ac-sim", renderSim(st))) {
        $("#ac-simgo")?.addEventListener("click", async () => { try { await simCall("/simulate-all", true); AC.simTimer = setInterval(pollSim, 1200); pollSim(); } catch (e) { showToast(e.message); } });
        $("#ac-sim").querySelectorAll(".ac-simrow").forEach((r) => r.addEventListener("click", () => window.openAccountModal && openAccountModal(Number(r.dataset.id))));
      }
      if (was && !st.running) refresh();
      return st;
    } catch (e) { /* status is cosmetic */ }
  }
  async function pollSim() {
    const st = await refreshSim();
    if (st && !st.running && AC.simTimer) { clearInterval(AC.simTimer); AC.simTimer = null; refresh(); }
  }

  function skeleton() {
    return `<div class="ac-page">
      <div id="ac-top" class="bb-kpis ac-top">Loading…</div>
      ${panel("🎯", "NOSTRO FARM GOALS · the work to get funded", '<a class="bb-chip" href="#" id="ac-goals-all">ALL GOALS ›</a>', "ac-goals")}
      ${panel("▶", "SIMULATION · REPLAY EVERY ACCOUNT", "runs on the last ~10 weeks of 5-minute data", "ac-sim", "ac-simp")}
      ${panel("C", "COMBO EVALUATIONS · several strategies, one shared $50k evaluation", '<button class="bb-chip" id="ac-combo-go" title="Launch the best combo from the long-history ranking">+ LAUNCH BEST COMBO</button>', "ac-combos")}
      ${panel(1, "PASSED ACCOUNTS", "evaluation passed · withdrawable", "ac-passed", "ac-pp")}
      ${panel(2, "REAL ACCOUNT · TRADOVATE", '<span id="ac-real-meta">…</span>', "ac-real")}
      ${panel(3, "ALL ACCOUNTS", "click a card for the full detail", "ac-all")}
    </div>`;
  }

  // ------------------------------------------------------------------ farm goals (shared with WEALTH › INVESTMENTS › GOALS & MISSIONS)
  function goalRing(pct, color, id) {
    const r = 46, C = 2 * Math.PI * r;
    return `<svg viewBox="0 0 110 110" class="wl-hring"><defs><linearGradient id="acg${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="#fff" stop-opacity=".85"/></linearGradient></defs><circle cx="55" cy="55" r="${r}" class="bg"/><circle cx="55" cy="55" r="${r}" class="fg" stroke="url(#acg${id})" stroke-dasharray="${((C * pct) / 100).toFixed(1)} 999" transform="rotate(-90 55 55)"/><text x="55" y="63" text-anchor="middle" class="pc">${Math.round(pct)}%</text></svg>`;
  }
  const fmtG = (v, u) => (!isN(v) ? "—" : u === "$" ? money(v) : u === "%" ? `${(+v).toFixed(1)}%` : u === "count" ? Math.round(v) : `${(+v).toFixed(1)} ${u || ""}`);
  async function refreshGoals() {
    let o;
    try { const r = await fetch("/api/wealth/hub?area=nostro"); if (!r.ok) throw new Error(r.status === 423 ? "WEALTH is locked (PIN)" : `HTTP ${r.status}`); o = await r.json(); }
    catch (e) { paint("#ac-goals", `<div class="dim">${esc(e.message)}</div>`); return; }
    const html = `<div class="ac-goals">${o.goals.map((g) => `<div class="ac-goal" style="--c:${g.color}" data-uix-tip="${esc(g.title)}|${esc(g.metric_label || "")}|${g.tasks_done}/${g.tasks.length} tasks done${g.days_left != null ? `|${g.days_left} days left` : ""}">
        ${goalRing(g.pct, g.color, g.id)}<div><b>${esc(g.emoji || "")} ${esc(g.title)}</b><small>${fmtG(g.value, g.unit)} / ${fmtG(g.target, g.unit)} · ${g.status}</small><small>${g.tasks_done}/${g.tasks.length} tasks · ${g.days_left ?? "—"} days left</small></div>
        ${g.next_task ? `<div class="ac-gnext"><span>▶ ${esc(g.next_task.title)}</span><button data-gtk="${g.next_task.id}">✓ DONE</button></div>` : ""}</div>`).join("") || '<div class="dim">No NOSTRO goals.</div>'}</div>`;
    if (paint("#ac-goals", html)) {
      $("#ac-goals").querySelectorAll("[data-gtk]").forEach((b) => b.addEventListener("click", async (e) => {
        e.stopPropagation(); b.textContent = "✓ +10 XP"; b.disabled = true;
        try { await fetch(`/api/wealth/hub/task/${b.dataset.gtk}/toggle`, { method: "POST" }); showToast("+10 XP"); } catch (err) { showToast(String(err)); }
        refreshGoals();
      }));
    }
  }

  function stop() { AC.timers.forEach(clearInterval); AC.timers = []; }
  function start() { stop(); AC.timers.push(setInterval(refresh, 8000), setInterval(() => refreshTv(false), 30000), setInterval(refreshCombos, 15000), setInterval(refreshGoals, 60000)); }

  async function build() {
    $("#view-acct").innerHTML = skeleton();
    $("#view-acct").addEventListener("mousemove", (e) => { // ac-spot: spotlight follows the mouse over a card
      const c = e.target.closest?.(".ac-card"); if (!c) return; const r = c.getBoundingClientRect();
      c.style.setProperty("--mx", `${e.clientX - r.left}px`); c.style.setProperty("--my", `${e.clientY - r.top}px`);
    });
    $("#view-acct").addEventListener("focusin", (e) => { AC.formOpen = !!e.target.closest?.(".ac-form"); });
    $("#view-acct").addEventListener("focusout", () => { AC.formOpen = false; });
    $("#ac-combo-go")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      const n = (AC.combos || []).length;
      try { const r = await fetch("/api/nostro/combo-evals/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rank: n }) }); const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.status); showToast(`Launched: ${j.name}`); refreshCombos(); refresh(); } catch (err) { showToast(String(err.message || err)); }
    });
    $("#ac-goals-all")?.addEventListener("click", (e) => {  // jump to WEALTH › INVESTMENTS › GOALS & MISSIONS
      e.preventDefault(); e.stopPropagation();
      try { localStorage.setItem("wealth_page", "invest"); } catch (x) { /* ignore */ }
      document.querySelector('[data-wpage="invest"]')?.click();
    });
    paintCached();
    await Promise.all([refresh(), refreshTv(false), refreshSim(), refreshCombos(), refreshGoals()]);

    start();
  }

  // returning from the Tradovate sign-in lands on /#accounts: open this tab automatically
  if (location.hash === "#acct") window.addEventListener("load", () => setTimeout(() => document.querySelector('.tab[data-tab="acct"]')?.click(), 400));

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "acct") {
      if (!AC.started) { AC.started = true; build().catch((e) => { const v = $("#view-acct"); if (v) v.innerHTML = `<div class="dn">${esc(e.message || e)}</div>`; }); }
      else if (!AC.timers.length) { start(); refresh(); refreshTv(false); }
    } else stop();
  };
})();
