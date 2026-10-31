/* POLYMARKET › MARKETS — the whole prediction market on one page: what the crowd is betting on, where the odds moved,
   who is betting big, and the news behind it. Read-only (public Polymarket data via /api/pmx, cached on the server).

   KPI strip · categories · TRENDING (by 24h volume / liquidity / total / newest / ending soon, searchable) · 24H MOVERS ·
   WHALE TRADES · CROWD vs NEWS + ODDS SHOCKS (shared intel panels from invest.js) · MARKET DETAIL (odds chart, every
   outcome, large trades, linked headlines, rules) opened by clicking any event.
   Depends on dashboard.js (api()) and invest.js (NX.* helpers, NX.lineChart, NX.intelPanels). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = NX.esc;
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const usd = (n) => (isN(n) ? NX.big(n, "$") : "—");
  const odds = (p) => (isN(p) ? `${(p * 100).toFixed(p < 0.01 || p > 0.99 ? 1 : 0)}%` : "—");
  const pts = (d) => (isN(d) ? `${d > 0 ? "+" : ""}${(d * 100).toFixed(1)}` : "—");
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const ends = (ts) => {
    if (!ts) return "—";
    const d = (ts - Date.now() / 1000) / 86400;
    return d < 0 ? "ended" : d < 1 ? `${Math.max(1, Math.round(d * 24))}h` : d < 60 ? `${Math.round(d)}d` : NX.date(ts);
  };
  const bar = (p) => `<span class="px-bar"><i style="width:${Math.max(1, Math.min(100, (p || 0) * 100))}%"></i></span>`;
  const S = { cat: "", sort: "vol24", q: "", min: 10000, sel: null, iv: "1w", timer: 0, data: null };

  function panel(n, title, id, o = {}) {
    return `<div class="bb-panel ${o.cls || "s6"}" style="--i:${n}" ${o.attr || ""}><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span>${o.head || ""}<span class="meta" id="${id}-meta"></span></div><div class="bb-pb" id="${id}">${NX.load()}</div></div>`;
  }

  function shell(v) {
    v.innerHTML = `
      <div class="bb-kpis" id="px-kpis">${NX.load("LOADING THE WHOLE POLYMARKET")}</div>
      <div class="px-bar-top"><div class="bb-chips" id="px-cats"></div><span class="sp"></span>
        <select id="px-sort"><option value="vol24">24H VOLUME</option><option value="liq">LIQUIDITY</option><option value="vol">ALL-TIME VOLUME</option><option value="new">NEWEST</option><option value="ending">ENDING SOON</option></select>
        <input id="px-q" placeholder="search markets…"/></div>
      <div class="bb-grid">
        <div class="bb-panel s12 hidden" id="px-detail" style="--i:0"></div>
        ${panel(1, "TRENDING MARKETS", "px-ev", { cls: "s8 h620" })}
        ${panel(2, "BIGGEST ODDS MOVES · 24H", "px-mv", { cls: "s4 h620" })}
        ${panel(3, "WHALE TRADES · ACROSS ALL MARKETS", "px-wh", { cls: "s6 h460", head: `<div class="bb-chips" id="px-min">${[[5000, "$5K+"], [10000, "$10K+"], [50000, "$50K+"], [100000, "$100K+"]].map(([v, l]) => `<button class="bb-chip ${v === S.min ? "on" : ""}" data-f="${v}">${l}</button>`).join("")}</div>` })}
        ${panel(4, "CROWD vs NEWS · WHAT THE BETTING MARKET SAYS ABOUT THE HEADLINES", "mk-pm", { cls: "s6 h460" })}
        ${panel(5, "ODDS SHOCKS · SUDDEN REPRICING IN THE LAST 24H", "mk-pms", { cls: "s12 h420" })}
      </div>
      <div class="rw-foot">Source: Polymarket public APIs (Gamma, CLOB, data-api) — the 500 most-traded open events, refreshed every 90 s. Odds = price of the first outcome (YES). Decided markets waiting to be settled are hidden. Read-only: nothing here places a bet.</div>`;
    v.querySelector("#px-sort").onchange = (e) => { S.sort = e.target.value; load(); };
    let t = 0;
    v.querySelector("#px-q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { S.q = e.target.value.trim(); load(); }, 300); };
    v.querySelectorAll("#px-min .bb-chip").forEach((c) => (c.onclick = () => {
      v.querySelectorAll("#px-min .bb-chip").forEach((x) => x.classList.remove("on")); c.classList.add("on"); S.min = Number(c.dataset.f); whales();
    }));
    v.addEventListener("click", (e) => {
      const cat = e.target.closest("[data-pcat]");
      if (cat) { S.cat = cat.dataset.pcat; load(); return; }
      const ev = e.target.closest("[data-pev]");
      if (ev && !e.target.closest("a")) { open(ev.dataset.pev); return; }
      const iv = e.target.closest("[data-piv]");
      if (iv) { S.iv = iv.dataset.piv; chart(); return; }
      const mk = e.target.closest("[data-pmk]");
      if (mk) { S.mk = Number(mk.dataset.pmk); detail(); return; }
      if (e.target.closest("[data-pclose]")) { S.sel = null; $("#px-detail").classList.add("hidden"); }
    });
  }

  const k = (l, v, s, c = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;

  async function load() {
    let d;
    try { d = await api(`/pmx/overview?cat=${encodeURIComponent(S.cat)}&sort=${S.sort}&q=${encodeURIComponent(S.q)}&limit=120`); }
    catch (e) { $("#px-ev").innerHTML = NX.err(e); $("#px-kpis").innerHTML = ""; return; }
    S.data = d;
    const K = d.kpis, mv = K.top_mover;
    $("#px-kpis").innerHTML = k("VOLUME · 24H", usd(K.vol24), `${usd(K.vol1w)} in 7 days`)
      + k("OPEN EVENTS", K.events.toLocaleString(), `${K.markets.toLocaleString()} markets`)
      + k("LIQUIDITY", usd(K.liq), "resting on the order books")
      + k("BIGGEST MOVE · 24H", mv ? `${pts(mv.chg1d)} pts` : "—", mv ? esc(`${mv.event}${mv.q && mv.q !== mv.event ? ` · ${mv.q}` : ""}`.slice(0, 70)) : "", mv ? cls(mv.chg1d) : "")
      + k("TOP CATEGORY", (() => { const c = [...d.categories].sort((a, b) => b.vol24 - a.vol24)[0]; return c ? c.cat : "—"; })(), "by 24h volume");
    const tot = d.categories.reduce((a, c) => a + c.n, 0);
    $("#px-cats").innerHTML = [["", "ALL", tot]].concat(d.categories.map((c) => [c.cat, c.cat, c.n]))
      .map(([v, l, n]) => `<button class="bb-chip ${v === S.cat ? "on" : ""}" data-pcat="${v}">${l} <span class="dim">${n}</span></button>`).join("");
    $("#px-ev-meta").textContent = `${d.events.length} of ${d.total}`;
    $("#px-ev").innerHTML = !d.events.length ? '<div class="bb-empty">NO MARKETS MATCH</div>' : `<table class="bb-tbl px-tbl"><thead><tr><th>EVENT</th><th>LEADING OUTCOME</th><th class="r">ODDS</th><th class="r">24H</th><th class="r">VOL 24H</th><th class="r">LIQUIDITY</th><th class="r">ENDS</th></tr></thead><tbody>${d.events.map((e) => {
      const m = e.markets[0] || {}, lead = m.label || (m.outcomes && m.outcomes[0]) || "";
      return `<tr class="px-row" data-pev="${esc(e.slug)}"><td class="px-ttl">${e.image ? `<img src="${esc(e.image)}" loading="lazy" alt=""/>` : ""}<span><b>${esc(e.title)}</b><small>${e.cat}${e.n_markets > 1 ? ` · ${e.n_markets} markets` : ""}</small></span></td>
        <td class="px-lead">${esc(lead).slice(0, 40)}</td><td class="r">${bar(m.p)} ${odds(m.p)}</td><td class="r ${cls(m.chg1d)}">${pts(m.chg1d)}</td>
        <td class="r">${usd(e.vol24)}</td><td class="r dim">${usd(e.liq)}</td><td class="r dim">${ends(e.end)}</td></tr>`;
    }).join("")}</tbody></table>`;
    movers();
  }

  async function movers() {
    try {
      const r = await api(`/pmx/movers?cat=${encodeURIComponent(S.cat)}&n=40`);
      $("#px-mv-meta").textContent = S.cat === "" ? "sports in-play lines left out" : S.cat;
      $("#px-mv").innerHTML = !r.items.length ? '<div class="bb-empty">NO BIG MOVES</div>' : r.items.map((m) => `<div class="px-mv" data-pev="${esc(m.slug)}">
        <span class="px-mv-d ${cls(m.chg1d)}">${pts(m.chg1d)}</span><span class="px-mv-t"><b>${esc(m.event)}</b><small>${m.q && m.q !== m.event ? `${esc(m.q)} · ` : ""}${esc(m.outcome)} now ${odds(m.p)} · ${usd(m.vol24)} 24h · ${m.cat}</small></span></div>`).join("");
    } catch (e) { $("#px-mv").innerHTML = NX.err(e); }
  }

  const tradeRows = (items, showTitle = true) => `<table class="bb-tbl"><thead><tr><th>TIME</th>${showTitle ? "<th>MARKET</th>" : ""}<th>SIDE</th><th class="r">PRICE</th><th class="r">SIZE</th><th>TRADER</th></tr></thead><tbody>${items.map((t) => `<tr ${showTitle && t.slug ? `class="px-row" data-pev="${esc(t.slug)}"` : ""}><td class="dim">${NX.ago(t.ts)}</td>${showTitle ? `<td>${esc(t.title || "").slice(0, 60)}</td>` : ""}<td class="${t.side === "BUY" ? "up" : "dn"}">${esc(t.side)} ${esc(t.outcome || "")}</td><td class="r">${odds(t.price)}</td><td class="r"><b>${usd(t.usd)}</b></td><td class="dim">${t.wallet ? `<a href="https://polymarket.com/profile/${esc(t.wallet)}" target="_blank" rel="noopener">${esc(String(t.trader).slice(0, 16))}</a>` : esc(t.trader)}</td></tr>`).join("")}</tbody></table>`;

  async function whales() {
    try {
      const r = await api(`/pmx/whales?min_usd=${S.min}&limit=80`);
      $("#px-wh-meta").textContent = `${r.items.length} trades · total ${usd(r.items.reduce((a, t) => a + t.usd, 0))}`;
      $("#px-wh").innerHTML = r.items.length ? tradeRows(r.items) : '<div class="bb-empty">NO TRADES THAT BIG RECENTLY</div>';
    } catch (e) { $("#px-wh").innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ market detail
  async function open(slug) {
    S.sel = slug; S.mk = 0; S.ev = null;
    const box = $("#px-detail");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>MARKET<span class="sp"></span><button class="bb-chip" data-pclose>CLOSE ✕</button></div><div class="bb-pb">${NX.load()}</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    try { S.ev = await api(`/pmx/event/${encodeURIComponent(slug)}`); } catch (e) { box.querySelector(".bb-pb").innerHTML = NX.err(e); return; }
    if (S.sel !== slug) return;
    detail();
  }

  function detail() {
    const e = S.ev; if (!e) return;
    const m = e.markets[S.mk] || e.markets[0];
    $("#px-detail").innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>${esc(e.title)}<span class="sp"></span><span class="meta">${e.cat} · ${usd(e.vol24)} 24h · ${usd(e.vol)} total · liquidity ${usd(e.liq)} · ends ${ends(e.end)}</span>
        <a class="bb-chip" href="https://polymarket.com/event/${esc(e.slug)}" target="_blank" rel="noopener">POLYMARKET ↗</a><button class="bb-chip" data-pclose>CLOSE ✕</button></div>
      <div class="px-detail">
        <div class="px-d-main">
          <div class="px-d-head"><b>${esc(m.label || m.q)}</b> · ${esc(m.outcomes[0])} <span class="px-big">${odds(m.p)}</span> <span class="${cls(m.chg1d)}">${pts(m.chg1d)} pts 24h</span> <span class="${cls(m.chg1h)} dim">${pts(m.chg1h)} 1h</span>
            <span class="sp"></span><div class="bb-chips">${[["1d", "1D"], ["1w", "1W"], ["1m", "1M"], ["max", "ALL"]].map(([v, l]) => `<button class="bb-chip ${v === S.iv ? "on" : ""}" data-piv="${v}">${l}</button>`).join("")}</div></div>
          <div id="px-chart" class="px-chart"></div>
          <div class="px-d-sub">LARGE TRADES IN THIS EVENT</div><div id="px-d-tr">${NX.load()}</div>
        </div>
        <div class="px-d-side">
          <div class="px-d-sub">${e.n_markets > 1 ? `ALL ${e.n_markets} OUTCOMES` : "OUTCOMES"}</div>
          <table class="bb-tbl"><tbody>${e.n_markets > 1 ? e.markets.map((x, i) => `<tr class="px-row ${i === S.mk ? "on" : ""}" data-pmk="${i}"><td>${esc(x.label || x.q).slice(0, 44)}</td><td class="r">${bar(x.p)} ${odds(x.p)}</td><td class="r ${cls(x.chg1d)}">${pts(x.chg1d)}</td><td class="r dim">${usd(x.vol24)}</td></tr>`).join("")
              : m.outcomes.map((o, i) => `<tr><td>${esc(o)}</td><td class="r">${bar(m.prices[i])} ${odds(m.prices[i])}</td></tr>`).join("")}</tbody></table>
          <div class="px-d-sub">NEWS ABOUT THIS MARKET</div>
          ${e.news && e.news.length ? NX.newsHtml(e.news, 12) : '<div class="bb-empty">NO MATCHING HEADLINES ON THE WIRE</div>'}
          ${e.description ? `<div class="px-d-sub">RULES</div><div class="px-rules">${esc(e.description)}</div>` : ""}
        </div>
      </div>`;
    chart();
    const tr = $("#px-d-tr"), cids = e.markets.map((x) => x.cid).filter(Boolean).slice(0, 25).join(",");
    api(`/pmx/whales?min_usd=500&limit=30&market=${encodeURIComponent(cids)}`).then((r) => {
      tr.innerHTML = r.items.length ? tradeRows(r.items, false) : '<div class="bb-empty">NO $500+ TRADES RECENTLY</div>';
    }).catch((err) => { tr.innerHTML = NX.err(err); });
  }

  async function chart() {
    const e = S.ev; if (!e) return;
    const m = e.markets[S.mk] || e.markets[0], box = $("#px-chart");
    document.querySelectorAll("#px-detail [data-piv]").forEach((b) => b.classList.toggle("on", b.dataset.piv === S.iv));
    if (!box || !m.tokens || !m.tokens[0]) { if (box) box.innerHTML = '<div class="bb-empty">NO PRICE HISTORY</div>'; return; }
    box.innerHTML = NX.load();
    try {
      const r = await api(`/pmx/history?token=${m.tokens[0]}&interval=${S.iv}`);
      const f = (t) => { const d = new Date(t * 1000); return S.iv === "1d" ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) + " " + d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
      NX.lineChart(box, r.points.map((p) => [f(p[0]), p[1] * 100]), { height: 280, fmt: (v) => `${v.toFixed(1)}%`, zero: false });
    } catch (err) { box.innerHTML = NX.err(err); }
  }

  // ------------------------------------------------------------------ lifecycle
  function start() {
    const v = $("#view-pmx"); if (!v) return;
    if (!v.dataset.built) { v.dataset.built = "1"; shell(v); }
    const tick = () => { if (document.hidden || !v.classList.contains("active")) return; load(); whales(); if (NX.intelPanels) NX.intelPanels(); };
    tick();
    clearInterval(S.timer);
    S.timer = setInterval(tick, 90000);
  }
  NX.openPolymarketEvent = (slug) => { const b = document.querySelector('.tab[data-tab="pmx"]'); if (b) b.click(); setTimeout(() => open(slug), 50); };
  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => { if (prev) prev(tab); if (tab === "pmx") start(); else clearInterval(S.timer); };
})();
