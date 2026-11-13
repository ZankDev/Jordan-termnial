/* WORLD › BUBBLE RADAR — circular-financing detector. Force-directed graph of money flows between companies (investment, purchase
   commitment, vendor financing, compute contract, debt backstop), edges coloured by type and sized by $. Every loop found by
   networkx.simple_cycles is listed with its 0-100 bubble score; the selected loop pulses red on the graph. Side panel: score
   breakdown, red-flag checklist per company, sources, WHY? per ticker. Data: /api/bubble (app/world/bubble.py). Needs d3 (loader). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TC = { investment: "#5aa9ff", "purchase commitment": "#ffa028", "compute contract": "#2be07e", "vendor financing": "#ff3bd0", "debt backstop": "#ff3b3b" };
  const KC = { chips: "#ffa028", lab: "#ff3bd0", cloud: "#5aa9ff", neocloud: "#2bd5c0", investor: "#c9a0ff", lender: "#ff6a28" };
  const LV = { EXTREME: "#ff3b3b", HIGH: "#ff6a28", ELEVATED: "#ffa028", LOW: "#2be07e" };
  const S = { started: false, d: null, sel: null, sim: null, node: null, link: null, size: [900, 600] };
  const money = (b) => (b == null ? "undisclosed" : b >= 1 ? `$${b.toLocaleString(undefined, { maximumFractionDigits: 1 })}B` : `$${Math.round(b * 1000)}M`);

  function shell(v) {
    v.innerHTML = `<div class="br-wrap"><div class="ad-bar"><span class="sg-t"><b>BUBBLE RADAR</b><i>money that moves in a circle — A invests in B, B buys from C, C buys from A</i></span>
        <span class="sp"></span><span class="br-cr" id="br-cr"></span></div>
      <div class="br-body"><aside class="br-loops" id="br-loops">${NX.load("FINDING LOOPS")}</aside>
        <div class="br-graph" id="br-graph"><svg id="br-svg"></svg><div class="br-leg">${Object.entries(TC).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("")}<em>edge width = $ · red pulse = selected loop · drag nodes</em></div></div>
        <aside class="br-side" id="br-side"></aside></div></div>`;
    $("#br-loops").addEventListener("click", (e) => { const r = e.target.closest("[data-l]"); if (r) pick(r.dataset.l); });
    $("#br-side").addEventListener("click", (e) => { const w = e.target.closest("[data-why]"); if (w && window.WHY) window.WHY.open(w.dataset.why, Date.now() / 1000 - 30 * 86400, Date.now() / 1000); });
  }

  async function load() {
    try { S.d = await api("/bubble"); } catch (e) { $("#br-loops").innerHTML = NX.err(e); return; }
    const c = S.d.credit;
    $("#br-cr").innerHTML = c ? `HY CREDIT SPREAD <b class="${c.stress ? "dn" : ""}">${c.hy_oas.toFixed(2)}%</b> <i>${c.chg_3m >= 0 ? "+" : ""}${c.chg_3m.toFixed(2)} pt / 3m · FRED ${esc(c.series)} ${esc(c.date)}</i>${c.stress ? ' <b class="dn">STRESS</b>' : ""}` : "credit spread unavailable";
    $("#br-loops").innerHTML = `<div class="ad-lh">${S.d.loops.length} LOOPS FOUND · networkx.simple_cycles</div>` + S.d.loops.map((l) => `<div class="br-l" data-l="${esc(l.id)}">
      <div class="br-l1"><b style="--c:${LV[l.level]}">${l.score}</b><span>${esc(l.nodes.map(lab).join(" → "))} → ${esc(lab(l.nodes[0]))}</span></div>
      <div class="br-l2"><em style="color:${LV[l.level]}">${l.level}</em><i>${money(l.total_bn)} disclosed · ${l.known_hops}/${l.hops.length} hops with a figure</i></div></div>`).join("");
    graph();
    pick(S.d.loops[0] && S.d.loops[0].id);
  }
  const lab = (id) => (S.d.nodes.find((n) => n.id === id) || {}).label || id;

  function graph() {
    const d3 = window.d3, box = $("#br-graph"), W = box.clientWidth, H = box.clientHeight;
    const svg = d3.select("#br-svg").attr("width", W).attr("height", H); svg.selectAll("*").remove();
    const defs = svg.append("defs");
    Object.entries(TC).forEach(([t, c]) => defs.append("marker").attr("id", `br-a-${t.replace(/\s/g, "")}`).attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("refY", 0).attr("markerUnits", "userSpaceOnUse").attr("markerWidth", 11).attr("markerHeight", 11).attr("orient", "auto").append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", c));
    const nodes = S.d.nodes.map((n) => ({ ...n })), byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const pairN = {};
    const links = S.d.edges.map((e) => { const k = [e.from, e.to].sort().join("|"); pairN[k] = (pairN[k] || 0) + 1; return { ...e, source: e.from, target: e.to, k: pairN[k], rev: e.from > e.to }; });
    const wid = (e) => (e.amount_bn == null ? 1.2 : Math.min(7, 1.2 + Math.sqrt(e.amount_bn) * 0.4));
    const rad = (n) => 16 + Math.min(16, n.loops * 1.2);
    S.link = svg.append("g").selectAll("path").data(links).join("path").attr("class", "br-e").attr("fill", "none").attr("stroke", (e) => TC[e.type]).attr("stroke-width", wid)
      .attr("stroke-dasharray", (e) => (e.amount_bn == null ? "4 4" : null)).attr("marker-end", (e) => `url(#br-a-${e.type.replace(/\s/g, "")})`).attr("opacity", 0.75);
    S.link.append("title").text((e) => `${lab(e.from)} → ${lab(e.to)} · ${e.type} · ${money(e.amount_bn)} · ${e.date}\n${e.note}`);
    const lbl = svg.append("g").selectAll("text").data(links.filter((e) => e.amount_bn != null)).join("text").attr("class", "br-el").text((e) => money(e.amount_bn));
    S.node = svg.append("g").selectAll("g").data(nodes).join("g").attr("class", "br-n").call(d3.drag()
      .on("start", (ev, n) => { if (!ev.active) S.sim.alphaTarget(0.25).restart(); n.fx = n.x; n.fy = n.y; })
      .on("drag", (ev, n) => { n.fx = ev.x; n.fy = ev.y; })
      .on("end", (ev, n) => { if (!ev.active) S.sim.alphaTarget(0); n.fx = null; n.fy = null; }));
    S.node.append("circle").attr("r", rad).attr("fill", "#070707").attr("stroke", (n) => KC[n.kind] || "#888").attr("stroke-width", 2);
    S.node.append("text").attr("class", "br-nt").attr("dy", -2).text((n) => n.label.split(" /")[0].split(" (")[0]);
    S.node.append("text").attr("class", "br-ns").attr("dy", 11).text((n) => (n.ticker ? `${n.ticker}${n.red ? ` · ${n.red}⚑` : ""}` : "private"));
    S.node.on("click", (ev, n) => company(n.id));
    S.sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id((n) => n.id).distance(150).strength(0.35))
      .force("charge", d3.forceManyBody().strength(-900)).force("center", d3.forceCenter(W / 2, H / 2)).force("collide", d3.forceCollide().radius((n) => rad(n) + 18))
      .force("x", d3.forceX(W / 2).strength(0.05)).force("y", d3.forceY(H / 2).strength(0.07));
    const arc = (e) => { // curve parallel / opposite edges apart; stop at the target's circle
      const s = e.source, t = e.target, dx = t.x - s.x, dy = t.y - s.y, dist = Math.hypot(dx, dy) || 1, r = rad(t) + 3;
      const tx = t.x - (dx / dist) * r, ty = t.y - (dy / dist) * r, bend = (e.k * 22) * (e.rev ? -1 : 1);
      const mx = (s.x + tx) / 2 - (dy / dist) * bend, my = (s.y + ty) / 2 + (dx / dist) * bend;
      e._m = [mx, my]; return `M${s.x},${s.y} Q${mx},${my} ${tx},${ty}`;
    };
    S.sim.on("tick", () => {
      nodes.forEach((n) => { n.x = Math.max(40, Math.min(W - 40, n.x)); n.y = Math.max(40, Math.min(H - 50, n.y)); });
      S.link.attr("d", arc); lbl.attr("x", (e) => e._m && e._m[0]).attr("y", (e) => e._m && e._m[1]);
      S.node.attr("transform", (n) => `translate(${n.x},${n.y})`);
    });
    S.byId = byId;
  }

  function highlight(l) {
    const on = new Set(l ? l.hops.flatMap((h) => h.edges) : []), nodes = new Set(l ? l.nodes : []);
    S.link.classed("loop", (e) => on.has(e.id)).attr("opacity", (e) => (!l ? 0.75 : on.has(e.id) ? 1 : 0.12));
    S.node.classed("loop", (n) => nodes.has(n.id)).attr("opacity", (n) => (!l || nodes.has(n.id) ? 1 : 0.35));
    d3.selectAll(".br-el").attr("opacity", (e) => (!l || on.has(e.id) ? 1 : 0.1));
  }

  function pick(id) {
    const l = S.d.loops.find((x) => x.id === id); if (!l) return;
    S.sel = id;
    document.querySelectorAll("#br-loops .br-l").forEach((el) => el.classList.toggle("on", el.dataset.l === id));
    highlight(l);
    const comps = l.nodes.map((n) => S.d.nodes.find((x) => x.id === n));
    const bars = Object.entries(l.breakdown).map(([k, v]) => `<div class="br-b"><span>${k}</span><i><b style="width:${(v / l.weights[k]) * 100}%"></b></i><em>${v}/${l.weights[k]}</em></div>`).join("");
    $("#br-side").innerHTML = `<div class="bb-ph"><span class="bb-n" style="background:${LV[l.level]}">${l.score}</span>LOOP · ${l.level}<span class="sp"></span><span class="meta">${money(l.total_bn)} around the loop</span></div>
      <div class="sg-d"><div class="br-path">${l.hops.map((h) => `<div><b>${esc(lab(h.from))}</b> → ${esc(lab(h.to))} <em style="color:${TC[h.types[0]]}">${esc(h.types.join(" + "))}</em> <i>${money(h.amount_bn)}</i></div>`).join("")}</div>
        <div class="sg-h">SCORE BREAKDOWN (0-100)</div>${bars}
        ${l.private.length ? `<div class="em-note">Private in this loop (no filings / market data): ${esc(l.private.join(", "))}</div>` : ""}
        <div class="sg-h">RED-FLAG CHECKLIST</div>${comps.map(checks).join("")}
        <div class="sg-h">SOURCES</div>${l.sources.map((s) => `<a class="br-src" href="${esc(s.url)}" target="_blank" rel="noopener"><b>${esc(s.publisher)}</b> ${esc(s.headline)}</a>`).join("")}
        <div class="em-note">${esc(S.d.note)} Source links open a news search for the headline.</div></div>`;
  }
  function checks(n) {
    return `<div class="br-c"><div class="br-ch"><b>${esc(n.label)}</b>${n.ticker ? `<span>${esc(n.ticker)}</span><button class="sg-whyb" data-why="${esc(n.ticker)}">WHY?</button>` : "<span>private</span>"}</div>
      ${n.checks.map((c) => `<div class="br-ck ${c.hit ? "hit" : c.hit === false ? "ok" : "na"}"><i>${c.hit ? "✗" : c.hit === false ? "✓" : "–"}</i><span>${esc(c.label)}</span><em>${esc(c.value)}</em></div>`).join("")}</div>`;
  }
  function company(id) {
    const n = S.d.nodes.find((x) => x.id === id); if (!n) return;
    const l = S.d.loops.filter((x) => x.nodes.includes(id));
    highlight(null);
    S.node.classed("loop", (x) => x.id === id);
    $("#br-side").innerHTML = `<div class="bb-ph"><span class="bb-n">${esc(n.kind.toUpperCase())}</span>${esc(n.label)}<span class="sp"></span><span class="meta">${n.loops} loops</span></div>
      <div class="sg-d"><p>${esc(n.note)}</p>${checks(n)}<div class="sg-h">LOOPS THROUGH ${esc(n.label.toUpperCase())}</div>
      ${l.map((x) => `<div class="sg-fl"><span>${esc(x.nodes.map(lab).join(" → "))}</span><b style="color:${LV[x.level]}">${x.score}</b></div>`).join("") || "<div class='why-none'>none</div>"}</div>`;
  }

  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => {
    if (prev) prev(tab);
    if (tab !== "bubble") { if (S.sim) S.sim.stop(); return; }
    if (!S.started) { S.started = true; shell($("#view-bubble")); load(); } else if (S.sim) S.sim.alpha(0.1).restart();
  };
})();
