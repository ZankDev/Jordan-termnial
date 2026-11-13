/* WORLD › SUPPLY CHAIN — the cause graph: straits / ports / tank farms -> commodity -> refiners, shippers, airlines, producers.
   Edges carry approximate flow shares; colour = live status of the upstream node (chokepoint risk screen, waiting ships when AIS
   is on, EVENTS MAP storms / quakes). Every node shows its tickers' % move over the chosen days. Click a node -> its causes (WHY?).
   Data: /api/world/gx/supply (app/world/supply_graph.json + supply_graph.py). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ST = { RESTRICTED: "#ff3b3b", ALERT: "#ff6a28", ELEVATED: "#ffa028", OPEN: "#9c6110", UNKNOWN: "#555" };
  const KIND = { strait: "STRAIT", port: "PORT", storage: "STORAGE", producer: "SOURCE", commodity: "COMMODITY", processor: "PROCESS", consumer: "END USER" };
  const COLS = ["SOURCE / CHOKEPOINT", "PORT / STORAGE", "COMMODITY", "PROCESSING", "EXPOSED"];
  const S = { chain: "crude", days: 5, d: null, sel: null, timer: 0, started: false };
  const pc = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "");

  function shell(v) {
    v.innerHTML = `<div class="sg-wrap">
      <div class="sg-bar"><span class="sg-t"><b>SUPPLY CHAIN</b><i>Every move has a cause. Clue. Rumour. Headline.</i></span>
        <div class="bb-chips" id="sg-chain"></div><span class="em-sep"></span>
        <div class="bb-chips" id="sg-days">${[1, 5, 20].map((d) => `<button class="bb-chip" data-d="${d}">${d}D</button>`).join("")}</div>
        <span class="sp"></span><span class="sg-live" id="sg-live"></span></div>
      <div class="sg-body"><div class="sg-graph" id="sg-graph">${NX.load("BUILDING THE GRAPH")}</div><aside class="sg-side" id="sg-side"></aside></div>
      <div class="sg-leg">${Object.entries(ST).map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("")}<em>edge width = approx. share of flow · % = node ticker move over the window · click a node for WHY?</em></div>
    </div>`;
    $("#sg-days").addEventListener("click", (e) => { const b = e.target.closest("[data-d]"); if (b) { S.days = Number(b.dataset.d); load(); } });
    $("#sg-chain").addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (b) { S.chain = b.dataset.c; S.sel = null; load(); } });
    $("#sg-graph").addEventListener("click", (e) => { const n = e.target.closest("[data-n]"); if (n) pick(n.dataset.n, true); });
    $("#sg-side").addEventListener("click", (e) => { const w = e.target.closest("[data-why]"); if (w && window.WHY) window.WHY.open(w.dataset.why, Date.now() / 1000 - S.days * 86400, Date.now() / 1000); });
    window.addEventListener("resize", () => { if (S.d && document.querySelector("#view-supply.active")) draw(); });
  }

  async function load() {
    document.querySelectorAll("#sg-days [data-d]").forEach((b) => b.classList.toggle("on", Number(b.dataset.d) === S.days));
    try { S.d = await api(`/world/gx/supply?chain=${S.chain}&days=${S.days}`); } catch (e) { $("#sg-graph").innerHTML = NX.err(e); return; }
    $("#sg-chain").innerHTML = S.d.chains.map((c) => `<button class="bb-chip${c.id === S.chain ? " on" : ""}" data-c="${c.id}">${esc(c.title)}</button>`).join("");
    const L = S.d.live;
    $("#sg-live").innerHTML = `<span class="${L.chokepoints ? "ok" : "bad"}">CHOKEPOINTS</span><span class="${L.events ? "ok" : "bad"}">EVENTS</span><span class="${L.prices ? "ok" : "bad"}">PRICES ${L.prices}/${L.prices_total}</span>`;
    draw();
    pick(S.sel || (S.d.nodes.find((n) => n.status !== "OPEN") || S.d.nodes[0]).id, false);
  }

  function draw() {
    const box = $("#sg-graph"), d = S.d;
    const W = Math.max(900, box.clientWidth - 8), nw = Math.min(210, (W - 60) / 5 - 26), nh = 70;
    const cols = [0, 1, 2, 3, 4].map((c) => d.nodes.filter((n) => n.col === c));
    const rows = Math.max(...cols.map((c) => c.length)), H = Math.max(440, rows * (nh + 22) + 50);
    const pos = {};
    cols.forEach((list, c) => {
      const x = 16 + c * ((W - 32 - nw) / 4), gap = (H - 40) / list.length;
      list.forEach((n, i) => { pos[n.id] = { x, y: 34 + gap * i + (gap - nh) / 2 }; });
    });
    const path = (a, b) => { const x1 = a.x + nw, y1 = a.y + nh / 2, x2 = b.x, y2 = b.y + nh / 2, mx = (x1 + x2) / 2; return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`; };
    const edges = d.edges.map((e) => {
      const a = pos[e.from], b = pos[e.to]; if (!a || !b) return "";
      const c = ST[e.status] || ST.OPEN, w = 1.4 + Math.sqrt(e.share) * 1.1, mx = (a.x + nw + b.x) / 2, my = (a.y + b.y) / 2 + nh / 2;
      return `<path d="${path(a, b)}" stroke="${c}" stroke-width="${w.toFixed(1)}" fill="none" opacity="${e.status === "OPEN" ? 0.55 : 0.95}"/>
        <path class="sg-flow${e.status === "RESTRICTED" ? " slow" : ""}" d="${path(a, b)}" stroke="${e.status === "OPEN" ? "#ffa028" : "#fff"}" stroke-width="1.2" fill="none" opacity=".6"/>
        <text x="${mx}" y="${my - 4}" class="sg-share" fill="${c === ST.OPEN ? "#c98a2a" : c}">${e.share}%</text>`;
    }).join("");
    const nodes = d.nodes.map((n) => {
      const p = pos[n.id], c = ST[n.status] || ST.OPEN, m = n.moves.find((x) => x.pct != null);
      return `<div class="sg-n${n.id === S.sel ? " sel" : ""}" data-n="${n.id}" style="left:${p.x}px;top:${p.y}px;width:${nw}px;height:${nh}px;--c:${c}">
        <div class="sg-n1"><span class="sg-k">${KIND[n.kind] || n.kind}</span>${n.status !== "OPEN" ? `<span class="sg-s">${n.status}</span>` : ""}${n.waiting != null ? `<span class="sg-w">${n.waiting} waiting</span>` : ""}</div>
        <b>${esc(n.label)}</b>
        <div class="sg-n2"><span>${esc(m ? m.sym : n.tickers[0])}</span><em class="${cls(n.pct)}">${pc(n.pct)}</em>${n.risk != null ? `<i>risk ${n.risk}</i>` : ""}</div></div>`;
    }).join("");
    box.innerHTML = `<div class="sg-canvas" style="width:${W}px;height:${H}px">${COLS.map((t, c) => `<div class="sg-col" style="left:${16 + c * ((W - 32 - nw) / 4)}px;width:${nw}px">${t}</div>`).join("")}
      <svg width="${W}" height="${H}">${edges}</svg>${nodes}</div>`;
  }

  function pick(id, why) {
    const n = S.d && S.d.nodes.find((x) => x.id === id); if (!n) return;
    S.sel = id;
    document.querySelectorAll("#sg-graph .sg-n").forEach((el) => el.classList.toggle("sel", el.dataset.n === id));
    const ins = S.d.edges.filter((e) => e.to === id), outs = S.d.edges.filter((e) => e.from === id), name = (x) => (S.d.nodes.find((k) => k.id === x) || {}).label || x;
    $("#sg-side").innerHTML = `<div class="bb-ph"><span class="bb-n">${KIND[n.kind] || ""}</span>${esc(n.label)}<span class="sp"></span><span class="meta" style="color:${ST[n.status]}">${n.status}</span></div>
      <div class="sg-d"><p>${esc(n.note)}</p>
        ${n.waiting != null ? `<div class="sg-kv"><span>VESSELS WAITING</span><b>${n.waiting}</b></div>` : ""}${n.risk != null ? `<div class="sg-kv"><span>RISK SCREEN</span><b>${n.risk}/100</b></div>` : ""}
        ${n.why.length ? `<div class="sg-h">LIVE SIGNALS</div>${n.why.map((w) => `<div class="sg-why">${esc(w)}</div>`).join("")}` : ""}
        <div class="sg-h">TICKERS · ${S.days}D</div>
        <table class="em-at">${n.moves.map((m) => `<tr><td class="sym">${esc(m.sym)}</td><td class="r">${m.price != null ? m.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}</td><td class="r ${cls(m.pct)}">${pc(m.pct)}</td><td><button class="sg-whyb" data-why="${esc(m.sym)}">WHY?</button></td></tr>`).join("")}</table>
        ${ins.length ? `<div class="sg-h">FED BY</div>${ins.map((e) => `<div class="sg-fl"><span>${esc(name(e.from))}</span><b style="color:${ST[e.status]}">${e.share}%</b></div>`).join("")}` : ""}
        ${outs.length ? `<div class="sg-h">FEEDS</div>${outs.map((e) => `<div class="sg-fl"><span>${esc(name(e.to))}</span><b style="color:${ST[e.status]}">${e.share}%</b></div>`).join("")}` : ""}
        <div class="em-note">Shares: ${esc(S.d.unit)} — approximate public estimates (EIA / IEA / USDA / ICSG), not live flow data.</div></div>`;
    if (why && window.WHY) { const m = n.moves.find((x) => x.pct != null) || n.moves[0]; if (m) window.WHY.open(m.sym, Date.now() / 1000 - S.days * 86400, Date.now() / 1000); }
  }

  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => {
    if (prev) prev(tab);
    if (tab !== "supply") { clearInterval(S.timer); return; }
    if (!S.started) { S.started = true; shell($("#view-supply")); }
    load(); clearInterval(S.timer); S.timer = setInterval(() => { if (document.visibilityState === "visible") load(); }, 120000);
  };
})();
