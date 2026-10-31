/* HOT STOCKS (INVESTING › HOT STOCKS) — the morning brief on screen: today's 3 picks with the reasons, a factor breakdown,
   headlines and a chart per pick, the NOSTRO report, and every past brief with the realised 1/3/5-day returns and hit rate.
   Also adds a MORNING BRIEF panel to the CAPITAL home page and a "morning pick" badge on the SECURITY page.
   Depends on dashboard.js (api(), showToast()) and ui_kit.js (UIX). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const pct = (n, d = 1) => (!isN(n) ? "—" : `${n > 0 ? "+" : ""}${Number(n).toFixed(d)}%`);
  const money = (n, d = 0) => (!isN(n) ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const FACTORS = [["gap", "MOVE", "size of the pre-market / last-session move vs the other names"], ["volume", "VOLUME", "relative volume vs its 30-day average"],
    ["news", "NEWS", "how many fresh headlines mention it"], ["momentum", "MOMENTUM", "size of its 1-month move"], ["analysts", "ANALYSTS", "target upside × consensus rating"]];
  const H = { started: false, busy: false };

  const openSec = (sym) => { if (window.openSecurity) { document.querySelector('.tab[data-tab="security"]')?.click(); window.openSecurity(sym, "DES"); } };

  function factorBars(f) {
    return `<div class="hs-f">${FACTORS.map(([k, l, d]) => { const v = f[k] || 0, w = Math.min(50, Math.abs(v) * 14);
      return `<div class="hs-fr" data-uix-tip="${esc(l)}|z-score ${v.toFixed(2)}|${esc(d)}"><span>${l}</span><div class="hs-ft"><i class="${v >= 0 ? "p" : "n"}" style="${v >= 0 ? "left:50%" : `left:${50 - w}%`};width:${w}%"></i></div><b class="${cls(v)}">${v >= 0 ? "+" : ""}${v.toFixed(1)}</b></div>`; }).join("")}</div>`;
  }

  function pickCard(p, i) {
    const up = (p.move_pct || 0) >= 0;
    return `<article class="hs-card ${up ? "up" : "dn"}" data-sym="${esc(p.symbol)}" style="--d:${i * 90}ms">
      <header><span class="hs-rank">${i + 1}</span><b class="hs-sym">${esc(p.symbol)}</b><span class="hs-name">${esc(p.name || "")}</span><span class="sp"></span><span class="hs-mv ${up ? "up" : "dn"}">${pct(p.move_pct)}</span></header>
      <div class="hs-sub">${esc(p.sector || "")} · ${esc(p.move_src || "")} · heat <b>${isN(p.heat) ? p.heat.toFixed(2) : "—"}</b></div>
      <div class="hs-chart" id="hs-ch-${esc(p.symbol)}"><div class="dim">chart…</div></div>
      <p class="hs-why">${esc(p.why || "")}</p>
      ${factorBars(p.factors || {})}
      <div class="hs-kv"><div data-uix-tip="REFERENCE PRICE|price when the brief was built"><span>REF</span><b>${isN(p.ref_price) ? p.ref_price.toFixed(2) : "—"}</b></div>
        <div><span>REL VOL</span><b>${isN(p.rel_volume) ? p.rel_volume.toFixed(1) + "×" : "—"}</b></div><div><span>1M</span><b class="${cls(p.p1m)}">${pct(p.p1m)}</b></div>
        <div><span>RSI</span><b>${isN(p.rsi14) ? p.rsi14.toFixed(0) : "—"}</b></div><div data-uix-tip="ANALYST TARGET|mean target vs price"><span>TARGET</span><b class="${cls(p.target_upside)}">${pct(p.target_upside, 0)}</b></div>
        <div><span>NEWS</span><b>${p.n_news ?? 0}</b></div></div>
      ${(p.headlines || []).length ? `<ul class="hs-heads">${p.headlines.map((h) => `<li><a href="${esc(h.link)}" target="_blank" rel="noopener noreferrer">${esc(h.title)}</a> <span class="dim">${esc(h.source || "")}</span></li>`).join("")}</ul>` : ""}
      <button class="bb-chip hs-open" data-sym="${esc(p.symbol)}">OPEN IN SECURITY ▸</button>
    </article>`;
  }

  async function drawChart(sym) {
    const el = document.getElementById(`hs-ch-${sym}`);
    if (!el) return;
    try {
      const r = await api(`/invest/chart?symbol=${encodeURIComponent(sym)}&range=3M`);
      const c = (r.candles || r.data || []).map((x) => (Array.isArray(x) ? x[4] : x.close ?? x.c)).filter(isN).slice(-60);
      if (c.length < 2) { el.innerHTML = ""; return; }
      const w = 320, h = 70, lo = Math.min(...c), hi = Math.max(...c), s = hi - lo || 1;
      const pts = c.map((v, i) => [(i / (c.length - 1)) * w, h - 3 - ((v - lo) / s) * (h - 6)]);
      const up = c[c.length - 1] >= c[0];
      el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="hs-svg ${up ? "up" : "dn"}"><path class="a" d="M${pts.map((p) => p.join(",")).join("L")}L${w},${h}L0,${h}Z"/><polyline points="${pts.map((p) => p.join(",")).join(" ")}"/>
        ${pts.map((p, i) => `<rect x="${p[0] - w / c.length / 2}" y="0" width="${w / c.length}" height="${h}" class="hit" data-uix-tip="${esc(sym)}|close ${c[i].toFixed(2)}|${i === c.length - 1 ? "latest" : `${c.length - 1 - i} sessions ago`}"/>`).join("")}</svg>`;
    } catch (e) { el.innerHTML = ""; }
  }

  function nostroBlock(n) {
    if (!n) return "";
    const row = (l, v, c = "") => `<div class="hs-nr"><span>${l}</span><b class="${c}">${v}</b></div>`;
    return `<div class="hs-nostro">
      ${row("LAST 24H", `${n.trades_24h} trades · ${money(n.pnl_24h)}`, cls(n.pnl_24h))}
      ${row("EVALUATIONS", `${n.in_eval} in progress · ${n.passed} passed · ${n.failed} failed`)}
      ${(n.closest || []).map((a) => row("CLOSEST", `${esc(a.name)} ${money(a.profit)} / ${money(a.target)}`)).join("")}
      ${(n.best_odds || []).map((o) => `<div class="hs-nr click" data-acc="${o.id}" data-uix-tip="${esc(o.name)}|Monte Carlo on ${o.n} trades|click to open the account"><span>BEST ODDS</span><b>${esc(o.name)} · pass ${o.pass}% / breach ${o.breach}%</b></div>`).join("")}
      ${n.polymarket?.today ? row("POLYMARKET", `${n.polymarket.armed ? "ARMED" : "off"} · all-time ${money(n.polymarket.today.all_time_pnl, 2)}`, cls(n.polymarket.today.all_time_pnl)) : ""}
    </div>`;
  }

  function historyTable(briefs, rec) {
    const rows = briefs.flatMap((b) => b.picks.map((p) => ({ ...p, day: b.day })));
    if (!rows.length) return `<div class="dim">No past briefs yet — the record builds up from today.</div>`;
    return `<div class="hs-rec">${[["NEXT-DAY HIT RATE", rec.hit1, rec.n1, rec.avg1], ["5-DAY HIT RATE", rec.hit5, rec.n5, rec.avg5]].map(([l, h, n, a]) =>
      `<div class="bb-kpi" data-uix-tip="${l}|share of picks that kept moving in the morning direction|${n || 0} picks measured"><span class="l">${l}</span><span class="v uix-count ${isN(h) && h >= 50 ? "up" : isN(h) ? "dn" : ""}">${isN(h) ? h + "%" : "—"}</span><span class="s">${n || 0} picks · avg ${pct(a, 2)}</span></div>`).join("")}</div>
      <table class="bb-tbl uix-sort hs-hist"><thead><tr><th>DAY</th><th>SYMBOL</th><th>SECTOR</th><th class="r">MOVE</th><th class="r">HEAT</th><th class="r">+1D</th><th class="r">+3D</th><th class="r">+5D</th></tr></thead><tbody>
      ${rows.map((p) => `<tr class="click" data-sym="${esc(p.symbol)}"><td>${esc(p.day)}</td><td class="sym">${esc(p.symbol)}</td><td class="dim">${esc(p.sector || "")}</td><td class="r ${cls(p.move_pct)}">${pct(p.move_pct)}</td><td class="r">${isN(p.heat) ? p.heat.toFixed(2) : "—"}</td>
        ${["r1", "r3", "r5"].map((k) => `<td class="r ${cls(p[k])}">${pct(p[k], 2)}</td>`).join("")}</tr>`).join("")}</tbody></table>
      <p class="dim hs-note">Returns are measured in the direction of the morning move (a stock that gapped down "wins" if it kept falling). Click a row to open it.</p>`;
  }

  async function build(force) {
    const v = $("#view-hot");
    if (!v) return;
    if (!v.dataset.built) {
      v.dataset.built = "1";
      v.innerHTML = `<div class="hs-page"><div class="hs-top"><div><b class="hs-t">🔥 HOT STOCKS</b> <span class="dim" id="hs-meta">…</span></div><span class="sp"></span>
        <button class="bb-chip" id="hs-rebuild" data-uix-tip="REBUILD|re-scan ~300 names now (takes ~1 min)|does not send Telegram">↻ REBUILD NOW</button>
        <button class="bb-chip" id="hs-send" data-uix-tip="SEND TO TELEGRAM|rebuild and send the brief now">✈ SEND TO TELEGRAM</button></div>
        <div class="hs-grid" id="hs-picks"><div class="dim">Loading…</div></div>
        <div class="hs-row2"><div class="bb-panel"><div class="bb-ph"><span class="bb-n">N</span>NOSTRO REPORT</div><div class="bb-pad" id="hs-nostro"></div></div>
          <div class="bb-panel"><div class="bb-ph"><span class="bb-n">R</span>TRACK RECORD — every past pick<span class="sp"></span><span class="meta">click a header to sort</span></div><div class="bb-pad" id="hs-hist"></div></div></div></div>`;
      $("#hs-rebuild").addEventListener("click", () => act("/morning/build"));
      $("#hs-send").addEventListener("click", () => act("/morning/send-now"));
      v.addEventListener("click", (e) => {
        const b = e.target.closest("[data-sym]"); if (b && (e.target.closest(".hs-open") || e.target.closest("tr"))) return openSec(b.dataset.sym);
        const a = e.target.closest("[data-acc]"); if (a && window.openAccountModal) openAccountModal(Number(a.dataset.acc));
      });
    }
    const [lt, hs] = await Promise.all([api("/morning/latest"), api("/morning/history?limit=30")]);
    const b = lt.brief;
    if (!b) { $("#hs-picks").innerHTML = `<div class="dim">No brief yet. Press REBUILD NOW, or wait for 8:30 New York time.</div>`; return; }
    $("#hs-meta").textContent = `${b.day} · ${b.kind} · built ${new Date(b.created_at * 1000).toLocaleTimeString()}${b.sent ? " · sent to Telegram" : ""}`;
    $("#hs-picks").innerHTML = b.picks.map(pickCard).join("");
    b.picks.forEach((p) => drawChart(p.symbol));
    $("#hs-nostro").innerHTML = nostroBlock(b.nostro);
    $("#hs-hist").innerHTML = historyTable(hs.briefs, hs.record);
    window.UIX && UIX.countUp($("#view-hot"));
  }

  async function act(path) {
    if (H.busy) return;
    H.busy = true;
    const btns = document.querySelectorAll("#hs-rebuild, #hs-send");
    btns.forEach((x) => (x.disabled = true));
    showToast("Scanning the universe — about a minute…");
    try { const r = await api(path, { method: "POST" }); showToast(r.sent === false ? "Built (Telegram not configured)" : path.includes("send") ? "Sent to Telegram" : "Rebuilt"); await build(); }
    catch (e) { showToast(`Failed: ${e.message}`); }
    finally { H.busy = false; btns.forEach((x) => (x.disabled = false)); }
  }

  // ------------------------------------------------------------------ CAPITAL: morning brief panel + draggable panels
  async function capitalWidget() {
    const grid = document.querySelector("#view-capital .bb-grid");
    if (!grid) return;
    let p = document.getElementById("cap-morning");
    if (!p) {
      p = document.createElement("div");
      p.className = "bb-panel s6"; p.id = "cap-morning"; p.style.setProperty("--i", 0);
      p.innerHTML = `<div class="bb-ph"><span class="bb-n">☀</span>MORNING BRIEF<span class="sp"></span><span class="meta" id="capm-meta"></span></div><div class="bb-pad" id="capm-body"><div class="dim">Loading…</div></div>`;
      grid.insertBefore(p, grid.firstChild);
      p.addEventListener("click", (e) => { const r = e.target.closest("[data-sym]"); if (r) openSec(r.dataset.sym); if (e.target.closest("#capm-more")) document.querySelector('.tab[data-tab="hot"]')?.click(); });
    }
    try {
      const lt = await api("/morning/latest"), b = lt.brief;
      if (!b) { $("#capm-body").innerHTML = `<div class="dim">First brief arrives at 8:30 New York time.</div>`; return; }
      $("#capm-meta").textContent = `${b.day} · ${b.kind}`;
      const n = b.nostro || {};
      $("#capm-body").innerHTML = `<div class="capm-picks">${b.picks.map((x) => `<div class="capm-p click" data-sym="${esc(x.symbol)}" data-uix-tip="${esc(x.symbol)} — ${esc(x.name || "")}|${esc((x.why || "").slice(0, 140))}"><b>${esc(x.symbol)}</b><span class="${cls(x.move_pct)}">${pct(x.move_pct)}</span><small>${esc(x.sector || "")}</small></div>`).join("")}</div>
        <div class="capm-n"><span>NOSTRO 24H <b class="${cls(n.pnl_24h)}">${money(n.pnl_24h)}</b> · ${n.trades_24h ?? 0} trades</span><span>EVALS <b>${n.in_eval ?? 0}</b> live · <b class="up">${n.passed ?? 0}</b> passed · <b class="dn">${n.failed ?? 0}</b> failed</span>
        <button class="bb-chip" id="capm-more">FULL BRIEF ▸</button></div>`;
    } catch (e) { /* cosmetic */ }
    if (window.UIX) UIX.draggable(grid, "capital");
  }

  // ------------------------------------------------------------------ SECURITY: morning-pick badge
  function securityBadge() {
    const view = document.getElementById("view-security");
    if (!view) return;
    new MutationObserver(async () => {
      const top = document.getElementById("sx-top");
      if (!top) return;
      const act = top.querySelector(".sx-actions");
      const sym = top.querySelector(".sx-bbg")?.textContent?.split(" ")[0];
      if (!act || !sym || act.dataset.pickFor === sym) return;
      act.dataset.pickFor = sym;
      try {
        const r = await api(`/morning/pick?symbol=${encodeURIComponent(sym)}`);
        if (!r.pick || act.dataset.pickFor !== sym) return;
        const p = r.pick, ret = isN(p.r5) ? `5d ${pct(p.r5)}` : isN(p.r1) ? `1d ${pct(p.r1)}` : "result pending";
        act.insertAdjacentHTML("afterbegin", `<button class="bb-chip on hs-badge" data-uix-tip="MORNING PICK ${esc(r.day)}|${esc((p.why || "").slice(0, 160))}|${ret}">🔥 PICK ${esc(r.day.slice(5))} · ${ret}</button>`);
        act.querySelector(".hs-badge").addEventListener("click", () => document.querySelector('.tab[data-tab="hot"]')?.click());
      } catch (e) { /* no badge */ }
    }).observe(view, { childList: true, subtree: true });
  }

  window.addEventListener("load", () => {
    securityBadge();
    capitalWidget();
    setInterval(() => { if (document.getElementById("view-capital")?.classList.contains("active")) capitalWidget(); }, 120000);
  });

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "hot") build().catch((e) => { const v = $("#hs-picks"); if (v) v.innerHTML = `<div class="dn">${esc(e.message)}</div>`; });
  };
})();
