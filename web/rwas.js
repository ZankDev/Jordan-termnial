/* RWAS (INVESTING › RWAS / STABLECOINS) — the stablecoin dashboard: total supply and its trend, mints and burns, every stablecoin over $100M
   side by side, breakdown by blockchain and by peg type, and a drill-down per coin. Mirrors the Bloomberg Terminal's RWAS <GO> screen.
   Data: DefiLlama (free). Hourly on-chain transfer volume and velocity are NOT included: that feed is paid. Mints and burns here are the net daily change in supply.
   Depends on dashboard.js (api()) and invest.js (NX.* formatters, NX.lineChart). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const usd = (n) => (isN(n) ? NX.big(n, "$") : "—");
  const sgn = (n) => (isN(n) ? `${n > 0 ? "+" : n < 0 ? "-" : ""}${NX.big(Math.abs(n), "$")}` : "—");
  const pc = (n, d = 2) => (isN(n) ? `${n > 0 ? "+" : ""}${n.toFixed(d)}%` : "—");
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const iso = (pts) => pts.map((p) => [new Date(p[0] * 1000).toISOString().slice(0, 10), p[1]]);
  const S = { d: null, sort: "supply", asc: false, range: 365, sel: null, timer: 0, busy: false };

  function shell(v) {
    v.innerHTML = `<div class="bb-grid">
      <div class="bb-panel s12 rw-kpis" style="--i:0" id="rw-kpis"><div class="bb-empty bb-load">LOADING STABLECOIN DATA</div></div>
      <div class="bb-panel s6" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>TOTAL STABLECOIN SUPPLY<span class="sp"></span>
        <div class="bb-chips" id="rw-range"><button class="bb-chip" data-f="90">3M</button><button class="bb-chip" data-f="180">6M</button><button class="bb-chip on" data-f="365">1Y</button></div><span class="meta" id="rw-h-meta"></span></div><div class="bb-pad"><div id="rw-hist" style="height:300px"></div></div></div>
      <div class="bb-panel s6" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>MINTS AND BURNS<span class="sp"></span><span class="meta">net daily change in supply, last 120 days</span></div><div class="bb-pad"><div id="rw-net" style="height:300px"></div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>STABLECOINS OVER $100M<span class="sp"></span><span class="meta" id="rw-t-meta"></span></div><div class="bb-pb" id="rw-tbl" style="max-height:520px;overflow:auto"></div></div>
      <div class="bb-panel s12 hidden" style="--i:4" id="rw-drill"></div>
      <div class="bb-panel s6" style="--i:5"><div class="bb-ph"><span class="bb-n">4</span>BY BLOCKCHAIN<span class="sp"></span><span class="meta">where the supply lives</span></div><div class="bb-pb" id="rw-chain" style="max-height:480px;overflow:auto"></div></div>
      <div class="bb-panel s6" style="--i:6"><div class="bb-ph"><span class="bb-n">5</span>BY PEG TYPE AND BY MECHANISM<span class="sp"></span><span class="meta">what each dollar is backed by</span></div><div class="bb-pb" id="rw-peg"></div></div>
    </div>
    <div class="rw-foot">Source: DefiLlama stablecoin data, refreshed every 5 minutes. Hourly on-chain transfer volume and velocity are not shown: that feed is paid. "Mints and burns" is the net change in circulating supply per day (new issuance minus redemptions).</div>`;
  }

  function kpis(d) {
    const tile = (l, v, sub, c) => `<div class="rw-k"><span>${l}</span><b class="${c || ""}">${v}</b><em>${sub || ""}</em></div>`;
    const top2 = d.coins.slice(0, 2).reduce((a, c) => a + c.share, 0);
    $("#rw-kpis").innerHTML = `<div class="rw-kg">${tile("TOTAL SUPPLY", usd(d.total), `${d.n_coins} coins over $100M`)}
      ${tile("24H", sgn(d.d1), pc(d.d1_pct), cls(d.d1))}${tile("7 DAYS", sgn(d.d7), pc(d.d7_pct), cls(d.d7))}${tile("30 DAYS", sgn(d.d30), pc(d.d30_pct), cls(d.d30))}
      ${tile("NET MINT / BURN 24H", d.d1 >= 0 ? "MINTING" : "BURNING", sgn(d.d1), cls(d.d1))}${tile("USDT + USDC SHARE", `${top2.toFixed(1)}%`, `${esc(d.coins[0].symbol)} alone ${d.coins[0].share.toFixed(1)}%`)}</div>`;
  }

  function hist(d) {
    const pts = iso(d.history.slice(-S.range));
    NX.lineChart($("#rw-hist"), pts, { height: 300, fmt: (v) => usd(v) });
    const a = pts[0], b = pts[pts.length - 1];
    $("#rw-h-meta").textContent = a && b ? `${pc((b[1] / a[1] - 1) * 100)} over the period` : "";
  }

  // mint (green) and burn (red) bars, drawn directly so the sign colours are exact
  function bars(box, net, h = 300) {
    if (box.offsetWidth === 0) { box.innerHTML = NX.load(); const ro = new ResizeObserver(() => { if (box.offsetWidth > 0) { ro.disconnect(); bars(box, net, h); } }); ro.observe(box); return; }
    if (!net || net.length < 2) { box.innerHTML = '<div class="bb-empty"><b>NO DATA</b></div>'; return; }
    const w = box.clientWidth, dpr = window.devicePixelRatio || 1;
    box.innerHTML = `<canvas style="width:${w}px;height:${h}px"></canvas>`;
    const cv = box.firstChild; cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pad = { l: 8, r: 60, t: 10, b: 22 }, vals = net.map((p) => p[1]), m = Math.max(...vals.map(Math.abs)) * 1.1 || 1;
    const x = (i) => pad.l + ((i + 0.5) / net.length) * (w - pad.l - pad.r), y = (v) => pad.t + (1 - (v + m) / (2 * m)) * (h - pad.t - pad.b), bw = Math.max(1, (w - pad.l - pad.r) / net.length * 0.72);
    g.font = "10px 'JetBrains Mono', Consolas, monospace"; g.fillStyle = "#8b8b8b"; g.strokeStyle = "#171717"; g.textAlign = "left";
    [-m, -m / 2, 0, m / 2, m].forEach((v) => { g.beginPath(); g.moveTo(pad.l, y(v)); g.lineTo(w - pad.r, y(v)); g.stroke(); g.fillText(sgn(v), w - pad.r + 5, y(v) + 3); });
    net.forEach((p, i) => { g.fillStyle = p[1] >= 0 ? "#2be07e" : "#ff4a4a"; const y0 = y(0), y1 = y(p[1]); g.fillRect(x(i) - bw / 2, Math.min(y0, y1), bw, Math.max(1, Math.abs(y1 - y0))); });
    g.fillStyle = "#8b8b8b"; g.textAlign = "center";
    [0, Math.floor(net.length / 2), net.length - 1].forEach((i) => g.fillText(new Date(net[i][0] * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }), Math.min(w - pad.r - 20, Math.max(pad.l + 20, x(i))), h - 6));
    const tip = document.createElement("div"); tip.className = "rw-tip hidden"; box.style.position = "relative"; box.appendChild(tip);
    cv.addEventListener("mousemove", (e) => { const r = cv.getBoundingClientRect(), i = Math.max(0, Math.min(net.length - 1, Math.floor(((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * net.length))); const p = net[i];
      tip.classList.remove("hidden"); tip.style.left = `${Math.min(w - 150, e.clientX - r.left + 10)}px`; tip.style.top = "8px"; tip.innerHTML = `${new Date(p[0] * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}<br><b class="${cls(p[1])}">${p[1] >= 0 ? "MINTED" : "BURNED"} ${usd(Math.abs(p[1]))}</b>`; });
    cv.addEventListener("mouseleave", () => tip.classList.add("hidden"));
  }

  const COLS = {
    rank: ["#", (c, i) => i + 1, null, true], coin: ["STABLECOIN", (c) => `<b>${esc(c.symbol)}</b> <span class="dim">${esc(c.name)}</span>`, (c) => c.symbol],
    peg: ["PEG", (c) => esc((c.peg || "").replace("pegged", "")), (c) => c.peg], mech: ["BACKING", (c) => esc((c.mechanism || "").replace("-backed", "")), (c) => c.mechanism || ""],
    supply: ["SUPPLY", (c) => usd(c.supply), (c) => c.supply, true], share: ["SHARE", (c) => `<span class="rw-bar"><i style="width:${Math.min(100, c.share * 1.6)}%"></i></span>${c.share.toFixed(1)}%`, (c) => c.share, true],
    d1: ["24H", (c) => `<span class="${cls(c.d1)}">${sgn(c.d1)}</span>`, (c) => c.d1, true], d7: ["7D", (c) => `<span class="${cls(c.d7_pct)}">${pc(c.d7_pct)}</span>`, (c) => c.d7_pct, true],
    d30: ["30D", (c) => `<span class="${cls(c.d30_pct)}">${pc(c.d30_pct)}</span>`, (c) => c.d30_pct, true],
    dev: ["PRICE vs $1", (c) => (isN(c.dev_bps) ? `<span class="${Math.abs(c.dev_bps) > 50 ? "dn" : ""}">${c.price.toFixed(4)} (${c.dev_bps > 0 ? "+" : ""}${c.dev_bps.toFixed(0)} bp)</span>` : `<span class="dim">${isN(c.price) ? "$" + c.price.toFixed(4) : "—"}</span>`), (c) => (isN(c.dev_bps) ? Math.abs(c.dev_bps) : -1), true],
    chains: ["CHAINS", (c) => `${c.n_chains} <span class="dim">${c.chains.slice(0, 2).map((x) => esc(x.chain)).join(", ")}</span>`, (c) => c.n_chains, true],
  };
  function table(d) {
    const keys = ["rank", "coin", "peg", "mech", "supply", "share", "d1", "d7", "d30", "dev", "chains"];
    let rows = d.coins.map((c, i) => ({ ...c, _i: i }));
    const col = COLS[S.sort];
    if (col && col[2]) rows.sort((a, b) => { const x = col[2](a), y = col[2](b); return ((x ?? -Infinity) > (y ?? -Infinity) ? 1 : -1) * (S.asc ? 1 : -1); });
    $("#rw-tbl").innerHTML = `<table class="bb-tbl"><thead><tr>${keys.map((k) => `<th class="${COLS[k][3] ? "r" : ""} ${COLS[k][2] ? "sortable" : ""} ${S.sort === k ? "sorted" : ""}" data-c="${k}">${COLS[k][0]}${S.sort === k ? (S.asc ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((c) => `<tr class="click ${S.sel === c.id ? "sel" : ""}" data-id="${esc(c.id)}">${keys.map((k) => `<td class="${COLS[k][3] ? "r" : ""}">${COLS[k][1](c, c._i)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    $("#rw-t-meta").textContent = `${d.coins.length} shown · click a coin for its history`;
    $("#rw-tbl").querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => { S.asc = S.sort === th.dataset.c ? !S.asc : false; S.sort = th.dataset.c; table(d); }));
    $("#rw-tbl").querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => drill(tr.dataset.id)));
  }

  function chains(d) {
    const m = Math.max(...d.by_chain.map((c) => c.supply));
    $("#rw-chain").innerHTML = `<table class="bb-tbl"><thead><tr><th>CHAIN</th><th class="r">SUPPLY</th><th>SHARE</th><th class="r">7D</th><th class="r">30D</th></tr></thead><tbody>${d.by_chain.map((c) => `<tr><td><b>${esc(c.chain)}</b></td><td class="r">${usd(c.supply)}</td>
      <td><span class="rw-bar w"><i style="width:${(c.supply / m) * 100}%"></i></span>${c.share.toFixed(1)}%</td><td class="r ${cls(c.d7)}">${pc(c.d7_pct)}</td><td class="r ${cls(c.d30)}">${pc(c.d30_pct)}</td></tr>`).join("")}</tbody></table>`;
  }
  function peg(d) {
    const mech = {};
    d.coins.forEach((c) => { const k = c.mechanism || "unknown"; mech[k] = (mech[k] || 0) + c.supply; });
    const block = (title, rows) => { const t = rows.reduce((a, r) => a + r[1], 0) || 1; return `<div class="rw-sec">${title}</div>${rows.map((r) => `<div class="rw-row"><span>${esc(r[0])}</span><span class="rw-bar w"><i style="width:${Math.max(1, (r[1] / t) * 100)}%"></i></span><b>${usd(r[1])}</b><em>${((r[1] / t) * 100).toFixed(1)}%</em></div>`).join("")}`; };
    $("#rw-peg").innerHTML = `<div class="rw-pad">${block("BY PEG TYPE", d.by_peg.map((p) => [p.peg, p.supply]))}${block("BY BACKING MECHANISM", Object.entries(mech).sort((a, b) => b[1] - a[1]))}</div>`;
  }

  async function drill(id) {
    S.sel = id; table(S.d);
    const box = $("#rw-drill"); box.classList.remove("hidden"); box.innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>LOADING<span class="sp"></span></div>${NX.load()}`;
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
    try {
      const j = await api(`/invest/rwas/coin?id=${encodeURIComponent(id)}`);
      const first = j.history[0], last = j.history[j.history.length - 1];
      box.innerHTML = `<div class="bb-ph"><span class="bb-n">▸</span>${esc(j.symbol)} · ${esc(j.name)}<span class="sp"></span><span class="meta">${esc(j.mechanism || "")} · ${last && first ? pc((last[1] / first[1] - 1) * 100) + " over the period" : ""}</span><button class="bb-chip" id="rw-close">CLOSE</button></div>
        <div class="rw-dgrid"><div><div class="rw-sec">SUPPLY, LAST YEAR</div><div id="rw-d-h" style="height:240px"></div></div><div><div class="rw-sec">MINTS AND BURNS, LAST 120 DAYS</div><div id="rw-d-n" style="height:240px"></div></div>
        <div><div class="rw-sec">WHERE IT LIVES</div>${j.chains.map((c) => `<div class="rw-row"><span>${esc(c.chain)}</span><span class="rw-bar w"><i style="width:${(c.supply / j.chains[0].supply) * 100}%"></i></span><b>${usd(c.supply)}</b></div>`).join("")}</div>
        <div class="rw-desc"><div class="rw-sec">ABOUT</div>${esc(j.description || "")}<br><br>${j.mint_redeem ? `<div class="rw-sec">MINT AND REDEEM</div>${esc(j.mint_redeem)}` : ""}${j.url ? `<br><a href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">${esc(j.url)}</a>` : ""}</div></div>`;
      $("#rw-close").addEventListener("click", () => { box.classList.add("hidden"); S.sel = null; table(S.d); });
      NX.lineChart($("#rw-d-h"), iso(j.history), { height: 240, fmt: (v) => usd(v) });
      bars($("#rw-d-n"), j.net, 240);
    } catch (e) { box.innerHTML = NX.err(e); }
  }

  async function load(first) {
    if (S.busy) return;
    S.busy = true;
    try {
      const d = await api("/invest/rwas"); S.d = d;
      kpis(d); hist(d); bars($("#rw-net"), d.net.slice(-120)); table(d); chains(d); peg(d);
    } catch (e) { if (first) $("#rw-kpis").innerHTML = NX.err(e); }
    S.busy = false;
  }

  function start() {
    const v = $("#view-rwas"); if (!v) return;
    if (!S.timer) {
      shell(v);
      $("#rw-range").querySelectorAll(".bb-chip").forEach((b) => b.addEventListener("click", () => { S.range = Number(b.dataset.f); $("#rw-range").querySelectorAll(".bb-chip").forEach((c) => c.classList.toggle("on", c === b)); if (S.d) hist(S.d); }));
      S.timer = setInterval(() => { if (document.getElementById("view-rwas")?.classList.contains("active") && !document.hidden) load(false); }, 300000);
      load(true);
    } else load(false);
  }

  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => { if (prev) prev(tab); if (tab === "rwas") start(); };
})();
