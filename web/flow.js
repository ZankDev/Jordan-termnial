/* INVESTING › MOST / DARK POOLS — money flow & institutional activity.
   MOST ACTIVE (dollar volume / unusual volume / gainers / losers, live), DARK POOL (FINRA off-exchange volume, DPI per stock and
   the market DIX with 20-day history), NAMED DARK POOLS per symbol (FINRA ATS weekly by venue), BLOCK-LIKE PRINTS (1-minute
   volume bursts, proxy). Depends on dashboard.js (api(), showToast()) and ui_kit.js (UIX). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const pct = (n, d = 2) => (!isN(n) ? "—" : `${n > 0 ? "+" : ""}${Number(n).toFixed(d)}%`);
  const big = (n) => (!isN(n) ? "—" : n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n).toLocaleString()}`);
  const vol = (n) => (!isN(n) ? "—" : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}K` : String(Math.round(n)));
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const F = { tab: "dollar", most: null, dp: null, sort: "offex_usd", sel: null, timers: [], built: false };
  const openSec = (sym) => { if (window.openSecurity) { document.querySelector('.tab[data-tab="security"]')?.click(); window.openSecurity(sym, "DES"); } };

  function spark(vals, w = 80, h = 20) {
    const v = vals.filter(isN);
    if (v.length < 2) return "";
    const lo = Math.min(...v), hi = Math.max(...v), s = hi - lo || 1;
    const pts = vals.map((x, i) => (isN(x) ? `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((x - lo) / s) * (h - 4)).toFixed(1)}` : null)).filter(Boolean).join(" ");
    return `<svg class="uix-spark ${v[v.length - 1] >= v[0] ? "up" : "dn"}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none"/></svg>`;
  }

  // ------------------------------------------------------------------ MOST
  function renderMost() {
    const m = F.most; if (!m) return;
    const rows = m[F.tab] || [];
    $("#fl-most").innerHTML = `<table class="bb-tbl uix-sort fl-tbl"><thead><tr><th>#</th><th>SYMBOL</th><th>NAME</th><th class="r">PRICE</th><th class="r">CHG</th><th class="r">$ VOLUME</th><th class="r">VOLUME</th><th class="r">REL VOL</th><th class="r">EXT HRS</th></tr></thead><tbody>
      ${rows.map((r, i) => `<tr class="click" data-sym="${esc(r.symbol)}"><td class="dim">${i + 1}</td><td class="sym">${esc(r.symbol)}</td><td class="dim fl-nm">${esc(r.name || "")}</td><td class="r">${isN(r.price) ? r.price.toFixed(2) : "—"}</td>
        <td class="r ${cls(r.pct)}" data-v="${r.pct ?? ""}">${pct(r.pct)}</td><td class="r" data-v="${r.dollar_volume}">${big(r.dollar_volume)}</td><td class="r" data-v="${r.volume}">${vol(r.volume)}</td>
        <td class="r" data-v="${r.rel_volume ?? ""}"><span class="fl-rv ${r.rel_volume >= 2 ? "hot" : ""}" style="--w:${Math.min(100, (r.rel_volume || 0) * 25)}%">${isN(r.rel_volume) ? r.rel_volume.toFixed(2) + "×" : "—"}</span></td>
        <td class="r ${cls(r.pre_pct ?? r.post_pct)}">${pct(r.pre_pct ?? r.post_pct)}</td></tr>`).join("")}</tbody></table>`;
    $("#fl-most-meta").textContent = `${m.universe} names · updated ${new Date(m.at * 1000).toLocaleTimeString()}`;
  }
  async function loadMost() { try { F.most = await api("/flow/most"); renderMost(); } catch (e) { $("#fl-most").innerHTML = `<div class="dn">${esc(e.message)}</div>`; } }

  // ------------------------------------------------------------------ DARK POOL
  function dixChart(hist) {
    const v = hist.map((h) => h.dix).filter(isN);
    if (v.length < 2) return "";
    const W = 360, H = 90, lo = Math.min(...v, 40), hi = Math.max(...v, 50), s = hi - lo || 1;
    const x = (i) => 30 + (i / (hist.length - 1)) * (W - 36), y = (d) => H - 14 - ((d - lo) / s) * (H - 24);
    const pts = hist.map((h, i) => (isN(h.dix) ? `${x(i).toFixed(1)},${y(h.dix).toFixed(1)}` : null)).filter(Boolean).join(" ");
    return `<svg viewBox="0 0 ${W} ${H}" class="acd-svg fl-dix"><line x1="30" x2="${W - 6}" y1="${y(45)}" y2="${y(45)}" class="stt"/><text x="26" y="${y(45) + 3}" class="t" text-anchor="end">45</text>
      <polyline points="${pts}" class="ln up"/>${hist.map((h, i) => (isN(h.dix) ? `<rect class="hit" x="${x(i) - 6}" y="0" width="12" height="${H}" data-uix-tip="${h.date}|DIX ${h.dix}%|${h.dix >= 45 ? "above" : "below"} the 45% line"/>` : "")).join("")}</svg>`;
  }
  function renderDark() {
    const d = F.dp; if (!d) return;
    const rows = [...d.rows].sort((a, b) => ((b[F.sort] ?? -1e18) - (a[F.sort] ?? -1e18)));
    const dix = d.market_dix;
    $("#fl-dix").innerHTML = `<div class="fl-dixk" data-uix-tip="DIX (dark index)|dollar-weighted short share of off-exchange volume across ~300 stocks|historically: > 45% leaned bullish, < 40% leaned bearish"><span>MARKET DIX</span><b class="${dix >= 45 ? "up" : dix < 40 ? "dn" : ""}">${isN(dix) ? dix.toFixed(1) + "%" : "—"}</b><small>${esc(d.date)} · ${d.days} sessions</small></div>${dixChart(d.market_hist)}`;
    $("#fl-dark").innerHTML = `<table class="bb-tbl fl-tbl"><thead><tr><th>SYMBOL</th><th class="r" data-s="offex_usd">OFF-EXCH $</th><th class="r" data-s="offex_share">% OF VOL</th><th class="r" data-s="dpi">DPI</th><th class="r" data-s="dpi_change">VS 20D</th><th>DPI 20D</th><th class="r" data-s="pct">CHG</th></tr></thead><tbody>
      ${rows.slice(0, 120).map((r) => `<tr class="click ${F.sel === r.symbol ? "sel" : ""}" data-dp="${esc(r.symbol)}"><td class="sym">${esc(r.symbol)}</td><td class="r">${big(r.offex_usd)}</td><td class="r">${isN(r.offex_share) ? r.offex_share + "%" : "—"}</td>
        <td class="r ${r.dpi >= 50 ? "up" : r.dpi < 40 ? "dn" : ""}" data-uix-tip="DPI ${r.symbol}|${r.dpi}% of off-exchange volume reported short|market makers shorting to fill buyers — higher = more buying pressure">${r.dpi}%</td>
        <td class="r ${cls(r.dpi_change)}">${isN(r.dpi_change) ? (r.dpi_change > 0 ? "+" : "") + r.dpi_change : "—"}</td><td>${spark(r.dpi_hist)}</td><td class="r ${cls(r.pct)}">${pct(r.pct)}</td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("#fl-dark th[data-s]").forEach((th) => th.classList.toggle("uix-srt", th.dataset.s === F.sort));
  }
  async function loadDark() { try { F.dp = await api("/flow/darkpool"); renderDark(); if (!F.sel && F.dp.rows[0]) loadAts(F.dp.rows[0].symbol); } catch (e) { $("#fl-dark").innerHTML = `<div class="dn">${esc(e.message)}</div>`; } }

  async function loadAts(sym) {
    F.sel = sym;
    document.querySelectorAll("#fl-dark tr[data-dp]").forEach((tr) => tr.classList.toggle("sel", tr.dataset.dp === sym));
    const el = $("#fl-ats");
    el.innerHTML = `<div class="dim acd-load">Loading FINRA ATS data for ${esc(sym)}…</div>`;
    try {
      const a = await api(`/flow/ats?symbol=${encodeURIComponent(sym)}`);
      if (F.sel !== sym) return;
      if (!a.venues.length) { el.innerHTML = `<div class="dim">${esc(a.note)}</div>`; return; }
      const mx = Math.max(...a.venues.map((v) => v.shares));
      const wmx = Math.max(...a.weeks.map((w) => w.shares));
      el.innerHTML = `<div class="fl-atsh"><b class="sym">${esc(sym)}</b><span class="dim">week of ${esc(a.week)} · ${a.venues.length} dark pools</span><button class="bb-chip" data-open="${esc(sym)}">OPEN ${esc(sym)} ▸</button></div>
        <div class="fl-venues">${a.venues.map((v) => `<div class="fl-v" data-uix-tip="${esc(v.venue)}|${vol(v.shares)} shares · ${v.trades.toLocaleString()} trades|avg ${v.avg_size.toLocaleString()} shares per trade · ${big(v.notional)}"><span>${esc(v.venue)}</span><div class="fl-vb"><i style="width:${(100 * v.shares) / mx}%"></i></div><b>${v.share_pct}%</b></div>`).join("")}</div>
        <div class="fl-wk"><span class="dim">WEEKLY DARK-POOL VOLUME</span><div class="fl-wkb">${a.weeks.map((w) => `<div class="fl-wkc" data-uix-tip="week of ${w.week}|${vol(w.shares)} shares · ${w.trades.toLocaleString()} trades|avg trade ${w.avg_size ? w.avg_size.toLocaleString() : "—"} shares · ${big(w.notional)}"><i style="height:${(100 * w.shares) / wmx}%"></i><span>${w.week.slice(5)}</span></div>`).join("")}</div></div>
        <p class="acd-note">${esc(a.note)}</p>`;
    } catch (e) { el.innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ BLOCK-LIKE PRINTS
  async function loadBlocks() {
    try {
      const b = await api("/flow/blocks");
      $("#fl-blocks").innerHTML = b.prints.length ? `<div class="fl-prints">${b.prints.slice(0, 80).map((p) => `<div class="fl-p ${p.dir} click" data-sym="${esc(p.symbol)}" data-uix-tip="${esc(p.symbol)} ${new Date(p.ts * 1000).toLocaleTimeString()}|${vol(p.volume)} shares in one minute (${p.x_median}× normal)|${big(p.usd)} at ${p.price.toFixed(2)} · bar ${pct(p.move_pct, 3)}">
          <span class="fl-pt">${new Date(p.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</span><b class="sym">${esc(p.symbol)}</b><span class="fl-pusd">${big(p.usd)}</span><span class="fl-px">${p.x_median}×</span><span class="${p.dir === "up" ? "up" : "dn"}">${p.dir === "up" ? "▲" : "▼"}</span></div>`).join("")}</div>`
        : `<div class="dim">No outsized 1-minute prints in the ${b.scanned} most active names yet today.</div>`;
      $("#fl-blocks-meta").textContent = `${b.prints.length} prints · ${b.scanned} names scanned`;
    } catch (e) { $("#fl-blocks").innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }

  async function build() {
    const v = $("#view-flow");
    if (!F.built) {
      F.built = true;
      const P = (n, t, meta, id, extra = "") => `<div class="bb-panel ${extra}"><div class="bb-ph"><span class="bb-n">${n}</span>${t}<span class="sp"></span><span class="meta" id="${id}-meta">${meta}</span></div><div class="bb-pad fl-wrap" id="${id}"><div class="dim">Loading…</div></div></div>`;
      v.innerHTML = `<div class="fl-page">
        <div class="hs-top"><b class="hs-t">MOST · DARK POOLS · BLOCKS</b><span class="dim">where the money is moving — and the part of it that trades away from the lit exchanges</span></div>
        <div class="fl-row">
          <div class="bb-panel fl-mostp"><div class="bb-ph"><span class="bb-n">1</span>MOST ACTIVE<span class="sp"></span><span id="fl-most-tabs">${[["dollar", "$ VOLUME"], ["unusual", "UNUSUAL VOL"], ["gainers", "GAINERS"], ["losers", "LOSERS"]].map(([k, l]) => `<button class="bb-chip ${k === F.tab ? "on" : ""}" data-t="${k}">${l}</button>`).join("")}</span><span class="meta" id="fl-most-meta"></span></div><div class="bb-pad fl-wrap" id="fl-most"><div class="dim">Loading…</div></div></div>
          ${P(2, "BLOCK-LIKE PRINTS (TODAY)", "", "fl-blocks")}
        </div>
        <div class="fl-row">
          <div class="bb-panel"><div class="bb-ph"><span class="bb-n">3</span>DARK POOL · OFF-EXCHANGE VOLUME<span class="sp"></span><span class="meta">FINRA daily · click a header to sort · click a stock for its dark pools</span></div>
            <div class="bb-pad"><div class="fl-dixrow" id="fl-dix"></div><div class="fl-wrap fl-darkw" id="fl-dark"><div class="dim acd-load">Loading 20 sessions of FINRA data…</div></div></div></div>
          ${P(4, "NAMED DARK POOLS (ATS)", "FINRA weekly", "fl-ats")}
        </div>
        <p class="acd-note">Sources: FINRA Reg SHO daily files (off-exchange volume, next day) · FINRA OTC transparency (ATS by venue, 2–4 week lag) · Yahoo (live quotes, 1-minute bars). Block-like prints are a proxy: true exchange / dark prints need a paid feed.</p></div>`;
      v.addEventListener("click", (e) => {
        const t = e.target.closest("#fl-most-tabs [data-t]"); if (t) { F.tab = t.dataset.t; document.querySelectorAll("#fl-most-tabs [data-t]").forEach((b) => b.classList.toggle("on", b === t)); renderMost(); return; }
        const th = e.target.closest("#fl-dark th[data-s]"); if (th) { F.sort = th.dataset.s; renderDark(); return; }
        const dp = e.target.closest("[data-dp]"); if (dp) { loadAts(dp.dataset.dp); return; }
        const o = e.target.closest("[data-open],[data-sym]"); if (o) openSec(o.dataset.open || o.dataset.sym);
      });
    }
    await Promise.all([loadMost(), loadDark(), loadBlocks()]);
    stop(); F.timers.push(setInterval(loadMost, 60000), setInterval(loadBlocks, 90000));
  }
  function stop() { F.timers.forEach(clearInterval); F.timers = []; }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "flow") build().catch((e) => showToast(e.message)); else stop();
  };
})();
