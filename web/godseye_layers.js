/* GOD'S EYE layers for WORLD › EVENTS MAP — wind / temperature / pressure (NOAA GFS), nowCOAST IR clouds, US radar and lightning,
   flights + military (OpenSky / adsb.lol), GNSS-integrity cells (adsb.lol NIC/NACp, gpsjam bands), AIS ships (aisstream.io, key only),
   shipping CHOKEPOINTS, watched-zone AIRSPACE, NHC 7-day outlook areas, satellites (CelesTrak + SGP4) and per-source CREDITS.
   Methods adapted from God's Eye View (MIT, (c) 2026 Bilawal Sidhu) — see THIRD_PARTY_NOTICES.md. Plugs into events_map.js via NX.evmap. */
(function () {
  "use strict";
  const E = window.NX && window.NX.evmap;
  if (!E) return;
  const S = E._S, { esc, ago } = E;
  const $ = (s, r = document) => r.querySelector(s);
  const PREF = "nx_evmap_gx";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const CDN = "https://cdn.jsdelivr.net/npm/";
  const GX = [["wind", "WIND", "#9fe0ff"], ["temp", "TEMP", "#ff8a28"], ["pres", "PRESSURE", "#c9a0ff"], ["clouds", "IR CLOUDS", "#cfd8e3"], ["usradar", "US RADAR", "#2be07e"],
    ["lightning", "LIGHTNING", "#ffe14d"], ["flights", "FLIGHTS", "#5aa9ff"], ["mil", "MILITARY", "#ff4a4a"], ["gnss", "GNSS JAM", "#ff3bd0"], ["ships", "SHIPS", "#2bd5c0"],
    ["choke", "CHOKEPOINTS", "#ffa028"], ["outlook", "NHC OUTLOOK", "#ffd23f"], ["sats", "SATELLITES", "#b0b8ff"]];
  const G = { on: Object.assign({ wind: true, temp: false, pres: false, clouds: false, usradar: false, lightning: false, flights: true, mil: true, gnss: true, ships: false, choke: true, outlook: true, sats: false }, lget(PREF, {})),
    g: {}, tiles: {}, wind: null, windLayer: null, scalar: null, timers: {}, choke: null, zones: null, gnss: null, flights: null, ships: null, sats: [], satMarks: [], note: {} };
  const save = () => lset(PREF, G.on);
  const scripts = {};
  const need = (src) => (scripts[src] ||= new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => { delete scripts[src]; rej(new Error(`could not load ${src.split("/").pop()}`)); }; document.body.appendChild(s); }));
  const L = () => window.L;
  const every = (k, ms, fn) => { clearInterval(G.timers[k]); fn(); G.timers[k] = setInterval(() => { if (S.active && document.visibilityState === "visible") fn(); }, ms); };
  const stop = (k) => { clearInterval(G.timers[k]); G.timers[k] = 0; };

  // ------------------------------------------------------------------ shell additions
  function shell() {
    const bar = $("#em-bar2"); bar.hidden = false;
    bar.innerHTML = `<span class="em-gx-t" data-uix-tip="GOD'S EYE LAYERS|Methods adapted from God's Eye View (MIT) by Bilawal Sidhu|Keyless public feeds, proxied + cached by this server">GOD'S EYE</span>
      <div class="bb-chips" id="em-gx">${GX.map(([k, n, c]) => `<button class="bb-chip em-lc" data-g="${k}" style="--lc:${c}"><i></i>${n}</button>`).join("")}</div><span class="sp"></span><span class="em-gxnote" id="em-gxnote"></span>`;
    const tabs = $("#em-tabs");
    tabs.insertAdjacentHTML("beforeend", `<button data-p="choke">CHOKEPOINTS</button><button data-p="air">AIRSPACE</button><button data-p="cred">CREDITS</button>`);
    $(".em-side").insertAdjacentHTML("beforeend", `
      <div class="em-pane" data-p="choke" hidden><div class="bb-ph"><span class="bb-n">2</span>CHOKEPOINTS<span class="sp"></span><span class="meta" id="em-cmeta"></span></div><div class="bb-pb" id="em-choke">${NX.load()}</div>
        <div class="em-note" id="em-cnote"></div></div>
      <div class="em-pane" data-p="air" hidden><div class="bb-ph"><span class="bb-n">3</span>CONFLICT AIRSPACE<span class="sp"></span><span class="meta" id="em-ameta"></span></div><div class="bb-pb" id="em-air">${NX.load()}</div>
        <div class="em-note">Aircraft within 250 NM (adsb.lol), military contacts, and the share of aircraft reporting degraded GNSS (NIC/NACp). A busy or empty sky and GNSS interference are conflict signals, not proof.</div></div>
      <div class="em-pane" data-p="cred" hidden><div class="bb-ph"><span class="bb-n">4</span>DATA CREDITS<span class="sp"></span><span class="meta">every layer, its source and licence</span></div><div class="bb-pb" id="em-cred">${NX.load()}</div></div>`);
    $("#em-mapbox-gx")?.remove();
    $(".em-mapbox").insertAdjacentHTML("beforeend", `<div class="em-gxleg" id="em-gxleg"></div>`);
    chips();
    $("#em-gx").addEventListener("click", (e) => {
      const b = e.target.closest("[data-g]"); if (!b) return;
      const k = b.dataset.g; G.on[k] = !G.on[k];
      if (k === "temp" && G.on.temp) G.on.pres = false; if (k === "pres" && G.on.pres) G.on.temp = false;
      save(); chips(); apply(k);
    });
    const fly = (e) => { const r = e.target.closest("[data-fly]"); if (!r) return; const [la, lo, z] = r.dataset.fly.split(",").map(Number); S.map.flyTo([la, lo], z || 5, { duration: 1.1 }); };
    $("#em-choke").addEventListener("click", fly); $("#em-air").addEventListener("click", fly);
  }
  function chips() { document.querySelectorAll("#em-gx [data-g]").forEach((b) => b.classList.toggle("on", !!G.on[b.dataset.g])); }
  function note(k, txt) { if (txt) G.note[k] = txt; else delete G.note[k]; const el = $("#em-gxnote"); if (el) el.innerHTML = Object.values(G.note).join(" · "); }
  function gxLegend() {
    const p = [];
    if (G.on.wind) p.push(`<div class="em-lg"><b>WIND</b><span class="em-ramp wind"></span><span>0</span><span>25 m/s</span></div>`);
    if (G.on.temp) p.push(`<div class="em-lg"><b>TEMP</b><span class="em-ramp temp"></span><span>-40</span><span>+40 °C</span></div>`);
    if (G.on.pres) p.push(`<div class="em-lg"><b>MSLP</b><span class="em-ramp pres"></span><span>970</span><span>1040 hPa</span></div>`);
    if (G.on.gnss) p.push(`<div class="em-lg"><b>GNSS</b><span><i style="background:#ff3b3b"></i>≥10%</span><span><i style="background:#ffa028"></i>2–10%</span><span><i style="background:#2be07e;opacity:.5"></i>&lt;2%</span></div>`);
    if (G.on.flights || G.on.mil) p.push(`<div class="em-lg"><b>AIR</b><span><i style="background:#5aa9ff"></i>civil</span><span><i style="background:#ff4a4a"></i>military</span></div>`);
    if (G.on.ships) p.push(`<div class="em-lg"><b>SHIPS</b><span><i style="background:#ff8a28"></i>tanker</span><span><i style="background:#2bd5c0"></i>cargo</span><span><i style="background:#8b8b8b"></i>other</span></div>`);
    const el = $("#em-gxleg"); if (el) el.innerHTML = p.join("");
  }

  // ------------------------------------------------------------------ map plumbing
  function initMap() {
    const map = S.map;
    [["scalar", 320], ["gxtiles", 335], ["gxvec", 420]].forEach(([n, z]) => { map.createPane(n); map.getPane(n).style.zIndex = z; if (n !== "gxvec") map.getPane(n).style.pointerEvents = "none"; });
    ["flights", "mil", "gnss", "ships", "choke", "outlook", "sats"].forEach((k) => (G.g[k] = L().layerGroup().addTo(map)));
    G.canvas = L().canvas({ padding: 0.3, pane: "gxvec" });
  }
  function apply(k) {
    try {
      if (k === "wind") return G.on.wind ? windOn() : windOff();
      if (k === "temp" || k === "pres") return scalar();
      if (["clouds", "usradar", "lightning"].includes(k)) return tiles(k);
      if (k === "flights" || k === "mil") { if (G.on.flights || G.on.mil) every("flights", 30000, loadFlights); else { stop("flights"); G.g.flights.clearLayers(); G.g.mil.clearLayers(); note("fl"); } drawFlights(); return; }
      if (k === "gnss") { if (G.on.gnss) every("gnss", 60000, loadGnss); else { stop("gnss"); G.g.gnss.clearLayers(); } return; }
      if (k === "ships") { if (G.on.ships) every("ships", 30000, loadShips); else { stop("ships"); G.g.ships.clearLayers(); note("ships"); } return; }
      if (k === "choke") return drawChoke();
      if (k === "outlook") return drawOutlook();
      if (k === "sats") return G.on.sats ? satsOn() : satsOff();
    } finally { gxLegend(); }
  }

  // ------------------------------------------------------------------ wind (leaflet-velocity) + scalar overlays
  async function loadWind() {
    if (G.wind && Date.now() - G.wind._t < 30 * 60000) return G.wind;
    const d = await api("/world/gx/wind"); d._t = Date.now(); G.wind = d; E.src("gfs", true); return d;
  }
  async function windOn() {
    try {
      await need(`${CDN}leaflet-velocity@2.1.4/dist/leaflet-velocity.min.js`);
      const d = await loadWind();
      if (!G.on.wind) return;
      windOff();
      G.windLayer = L().velocityLayer({ displayValues: true, data: d.velocity, maxVelocity: 25, velocityScale: 0.0065, particleAge: 64, lineWidth: 1.2, particleMultiplier: 1 / 420, frameRate: 22, opacity: 0.85,
        displayOptions: { velocityType: "GFS 10 m wind", position: "bottomright", emptyString: "hover the map for wind", angleConvention: "bearingCW", showCardinal: true, speedUnit: "kt", directionString: "from", speedString: "speed" },
        colorScale: ["#2d4f73", "#3d6f9a", "#5a93bd", "#7fb6d8", "#a9d6ec", "#d4ecf6", "#ffe7a8", "#ffc35a", "#ffa028", "#ff6a28", "#ff3b3b"] }).addTo(S.map);
      note("wind", `WIND GFS valid ${d.valid.slice(5, 16).replace("T", " ")}Z`);
    } catch (e) { E.src("gfs", false, e.message); note("wind", `<b class="dn">WIND unavailable</b>`); }
  }
  function windOff() { if (G.windLayer) { S.map.removeLayer(G.windLayer); G.windLayer = null; } note("wind"); }
  const RAMPS = { temp: [[-40, [80, 60, 200]], [-20, [60, 120, 230]], [0, [90, 200, 230]], [10, [100, 220, 140]], [20, [240, 220, 80]], [30, [255, 140, 40]], [40, [230, 40, 40]]],
    pres: [[970, [150, 70, 220]], [995, [80, 120, 230]], [1013, [0, 0, 0]], [1025, [255, 170, 60]], [1040, [255, 70, 40]]] };
  function ramp(stops, v) {
    if (v <= stops[0][0]) return stops[0][1];
    for (let i = 1; i < stops.length; i++) if (v <= stops[i][0]) { const [a, ca] = stops[i - 1], [b, cb] = stops[i], u = (v - a) / (b - a); return ca.map((c, j) => c + (cb[j] - c) * u); }
    return stops[stops.length - 1][1];
  }
  async function scalar() {
    const gen = (G.scalarGen = (G.scalarGen || 0) + 1);
    if (G.scalar) { S.map.removeLayer(G.scalar); G.scalar = null; }
    const kind = G.on.temp ? "temp" : G.on.pres ? "pres" : null; if (!kind) return;
    let d; try { d = await loadWind(); } catch (e) { E.src("gfs", false, e.message); return; }
    if (gen !== G.scalarGen) return; // a newer toggle superseded this render
    const vals = kind === "temp" ? d.temp : d.pressure, gr = d.grid;
    // the grid is equirectangular; resample into Web-Mercator rows so the overlay lines up with the map
    const W = 720, H = 420, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d"), img = ctx.createImageData(W, H), rad = Math.PI / 180;
    const my = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * rad) / 2)), y0 = my(80), y1 = my(-80);
    const at = (j, i) => vals[Math.max(0, Math.min(gr.ny - 1, j)) * gr.nx + ((i % gr.nx) + gr.nx) % gr.nx];
    for (let py = 0; py < H; py++) {
      const lat = (2 * Math.atan(Math.exp(y0 + ((py + 0.5) / H) * (y1 - y0))) - Math.PI / 2) / rad, fy = (gr.la1 - lat) / gr.dy, j = Math.floor(fy), v = fy - j;
      for (let px = 0; px < W; px++) {
        const lon = -180 + ((px + 0.5) / W) * 360, fx = (((lon - gr.lo1) % 360) + 360) % 360 / gr.dx, i = Math.floor(fx), u = fx - i;
        const val = (at(j, i) * (1 - u) + at(j, i + 1) * u) * (1 - v) + (at(j + 1, i) * (1 - u) + at(j + 1, i + 1) * u) * v;
        const c = ramp(RAMPS[kind], val), o = (py * W + px) * 4;
        img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2];
        img.data[o + 3] = kind === "pres" ? Math.min(200, Math.abs(val - 1013) * 9) : 170;
      }
    }
    ctx.putImageData(img, 0, 0);
    G.scalar = L().imageOverlay(cv.toDataURL(), [[-80, -180], [80, 180]], { pane: "scalar", opacity: 0.55, interactive: false }).addTo(S.map);
  }

  // ------------------------------------------------------------------ nowCOAST tiles
  const TILE_OPA = { clouds: 0.55, usradar: 0.75, lightning: 0.95 };
  function tiles(k) {
    if (G.tiles[k]) { S.map.removeLayer(G.tiles[k]); G.tiles[k] = null; }
    if (!G.on[k]) return;
    G.tiles[k] = L().tileLayer(`/api/world/gx/nowcoast/${k}/{z}/{x}/{y}.png`, { pane: "gxtiles", opacity: TILE_OPA[k], maxNativeZoom: 8, maxZoom: 10, className: `em-nc-${k}` }).addTo(S.map);
    G.tiles[k].on("tileerror", () => E.src("nowcoast", false, "tile failed")); G.tiles[k].once("tileload", () => E.src("nowcoast", true));
  }

  // ------------------------------------------------------------------ flights
  async function loadFlights() {
    try { G.flights = await api("/world/gx/flights"); E.src("adsb", true); } catch (e) { E.src("adsb", false, e.message); return; }
    drawFlights();
    const f = G.flights, glob = f.global_n ? `${f.global_n.toLocaleString()} OpenSky` : "OpenSky unavailable → adsb.lol regional";
    note("fl", `FLIGHTS <b>${f.n.toLocaleString()}</b> (${glob} · ${f.mil_n} mil)`);
  }
  const plane = (trk, c) => L().divIcon({ className: "em-plane", iconSize: [14, 14], iconAnchor: [7, 7], html: `<svg viewBox="-7 -7 14 14" style="transform:rotate(${trk || 0}deg)"><path d="M0-6 L1.2-1.5 L6 1 L6 2.2 L1.2 1 L0.8 4.5 L2.4 5.8 L2.4 6.6 L0 6 L-2.4 6.6 L-2.4 5.8 L-0.8 4.5 L-1.2 1 L-6 2.2 L-6 1 L-1.2-1.5Z" fill="${c}" stroke="#000" stroke-width=".5"/></svg>` });
  function flightPopup(r) {
    return `<div class="em-pp"><div class="em-pp-h" style="--c:${r[8] ? "#ff4a4a" : "#5aa9ff"}"><b>${esc(r[1] || r[0])}</b> ${r[8] ? "MILITARY" : "AIRCRAFT"}</div>
      <table><tr><td>HEX</td><td>${esc(r[0])}</td></tr>${r[7] ? `<tr><td>TYPE</td><td>${esc(r[7])}</td></tr>` : ""}<tr><td>ALT</td><td>${Math.round(r[4] || 0).toLocaleString()} m</td></tr>
      <tr><td>SPEED</td><td>${Math.round((r[5] || 0) * 1.944)} kt</td></tr><tr><td>TRACK</td><td>${r[6] || 0}°</td></tr></table>
      <a href="https://globe.adsb.lol/?icao=${esc(String(r[0]).replace("~", ""))}" target="_blank" rel="noopener">ADSB.LOL ›</a></div>`;
  }
  function drawFlights() {
    G.g.flights.clearLayers(); G.g.mil.clearLayers();
    if (!G.flights) return;
    for (const r of G.flights.rows) {
      if (r[8]) {
        if (!G.on.mil) continue;
        L().marker([r[2], r[3]], { icon: plane(r[6], "#ff4a4a"), pane: "gxvec", keyboard: false }).bindTooltip(`${esc(r[1] || r[0])} ${esc(r[7] || "")} · MIL`, { className: "em-tip" }).bindPopup(() => flightPopup(r), { className: "em-popup" }).addTo(G.g.mil);
      } else if (G.on.flights) {
        L().circleMarker([r[2], r[3]], { renderer: G.canvas, radius: 1.7, stroke: false, fillColor: "#5aa9ff", fillOpacity: 0.75 }).bindPopup(() => flightPopup(r), { className: "em-popup" }).addTo(G.g.flights);
      }
    }
  }

  // ------------------------------------------------------------------ GNSS integrity
  async function loadGnss() {
    try { G.gnss = await api("/world/gx/gnss"); E.src("adsb", true); } catch (e) { E.src("adsb", false, e.message); return; }
    G.g.gnss.clearLayers();
    if (!G.on.gnss) return;
    const C = { high: ["#ff3b3b", 0.55], medium: ["#ffa028", 0.45], low: ["#2be07e", 0.1] };
    for (const z of G.gnss.zones) L().circle([z.lat, z.lon], { radius: z.radius_km * 1000, pane: "gxvec", color: "#ff3bd0", weight: 1, opacity: 0.35, dashArray: "2 6", fill: false, interactive: false }).addTo(G.g.gnss);
    for (const c of G.gnss.cells) {
      const [col, op] = C[c[5]], h = 0.25;
      L().rectangle([[c[0] - h, c[1] - h], [c[0] + h, c[1] + h]], { pane: "gxvec", stroke: c[5] !== "low", color: col, weight: 1, fillColor: col, fillOpacity: op })
        .bindTooltip(`GNSS ${c[5].toUpperCase()} · ${c[2]}% · ${c[4]}/${c[3]} aircraft degraded`, { className: "em-tip" }).addTo(G.g.gnss);
    }
    if (!G.gnss.cells.length) note("gnss", "GNSS: building 30-min window…"); else note("gnss");
  }

  // ------------------------------------------------------------------ ships (aisstream.io, only with a key)
  async function loadShips() {
    let d; try { d = await api("/world/gx/ships"); } catch (e) { E.src("ais", false, e.message); return; }
    G.g.ships.clearLayers();
    if (!d.configured) { note("ships", `SHIPS: <b class="am">set AISSTREAM_API_KEY</b> to stream AIS`); return; }
    E.src("ais", d.connected, d.error || (d.connected ? "" : "connecting"));
    note("ships", `SHIPS <b>${d.count.toLocaleString()}</b>${d.connected ? "" : " (connecting)"}`);
    const C = { tanker: "#ff8a28", cargo: "#2bd5c0" };
    for (const s of d.ships) L().circleMarker([s[2], s[3]], { renderer: G.canvas, radius: 2.4, stroke: false, fillColor: C[s[6]] || "#8b8b8b", fillOpacity: 0.85 })
      .bindTooltip(`${esc(s[1] || s[0])} · ${esc(s[6])}${s[4] != null ? ` · ${s[4]} kn` : ""}${s[7] ? ` → ${esc(s[7])}` : ""}`, { className: "em-tip" }).addTo(G.g.ships);
  }

  // ------------------------------------------------------------------ chokepoints
  const LVL = { HIGH: "#ff3b3b", ELEVATED: "#ffa028", NORMAL: "#2be07e" };
  async function loadChoke() {
    try { G.choke = await api("/world/gx/chokepoints"); } catch (e) { $("#em-choke").innerHTML = NX.err(e); return; }
    drawChoke(); renderChoke();
  }
  function drawChoke() {
    G.g.choke.clearLayers();
    if (!G.on.choke || !G.choke) return;
    for (const c of G.choke.rows) {
      const col = LVL[c.level] || "#ffa028", sh = c.ships ? c.ships.tanker + c.ships.cargo + c.ships.other : null;
      L().marker([c.lat, c.lon], { pane: "gxvec", icon: L().divIcon({ className: "em-choke", iconSize: [14, 14], iconAnchor: [7, 7], html: `<i style="--c:${col}"></i><b>${esc(c.name.toUpperCase())}${sh != null ? ` · ${sh}` : ""}</b>` }) })
        .bindTooltip(`${esc(c.name)} · ${esc(c.commodities.join(", "))}${c.risk != null ? ` · risk ${c.risk}` : ""}`, { className: "em-tip" })
        .on("click", () => { E.pane("choke"); const row = $(`#em-choke [data-cid="${c.id}"]`); if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" }); }).addTo(G.g.choke);
    }
  }
  function renderChoke() {
    const d = G.choke; if (!d) return;
    $("#em-cmeta").textContent = d.ais.configured ? `AIS ${d.ais.connected ? "live" : "connecting"} · ${d.ais.messages.toLocaleString()} msgs` : "AIS off (no key)";
    $("#em-choke").innerHTML = d.rows.map((c) => {
      const s = c.ships, col = LVL[c.level] || "#8b8b8b";
      const ships = s ? `<span><b>${s.tanker}</b> tankers</span><span><b>${s.cargo}</b> cargo</span><span><b>${s.other}</b> other</span><span><b>${s.stopped}</b> stopped</span>` : `<span class="dim">vessel counts need AISSTREAM_API_KEY</span>`;
      return `<div class="em-ir em-cp" data-cid="${c.id}" data-fly="${c.lat},${c.lon},6">
        <div class="em-ir-h"><span class="em-k" style="--k:${col}">${c.level ? c.level.slice(0, 4) : "…"}</span><b>${esc(c.name)}</b><em>${c.risk != null ? `risk ${c.risk}` : "risk pending"}</em></div>
        <div class="em-cp-n">${esc(c.note)}</div><div class="em-cp-s">${ships}</div>
        <div class="em-cp-c">${c.commodities.map((x) => `<span>${esc(x.toUpperCase())}</span>`).join("")}${c.hazards.length ? `<span class="dn">⚠ ${esc(c.hazards[0])}</span>` : ""}</div>
        <table class="em-at">${c.tickers.map((t) => `<tr><td class="sym">${esc(t.sym)}</td><td class="nm">${esc(t.name)}</td><td class="dim">${esc(t.for)}</td><td class="r">${E.quoteCell(t.sym)}</td></tr>`).join("")}</table>
        ${c.top.length ? `<div class="em-cp-h">${c.top.map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a>`).join("")}</div>` : ""}</div>`;
    }).join("");
    $("#em-cnote").textContent = d.note + (d.risk_ready ? "" : " Risk screen is being computed in the background (news + hazards)…");
    E.loadQuotes();
    if (!d.risk_ready) setTimeout(() => { if (S.active) loadChoke(); }, 25000);
  }

  // ------------------------------------------------------------------ airspace (watched zones)
  async function loadZones() {
    let d; try { d = await api("/world/gx/zones"); } catch (e) { $("#em-air").innerHTML = NX.err(e); return; }
    G.zones = d;
    $("#em-ameta").textContent = `${d.zones.filter((z) => z.aircraft != null).length}/${d.zones.length} zones sampled`;
    $("#em-air").innerHTML = d.zones.map((z) => {
      const tr = z.trend_1h, sh = z.gnss_bad_share;
      const gn = z.gnss_cells_high ? `<b class="dn">${z.gnss_cells_high} HIGH</b>` : z.gnss_cells_med ? `<b class="am">${z.gnss_cells_med} MED</b>` : sh != null ? "nominal" : "—";
      return `<div class="em-ir" data-fly="${z.lat},${z.lon},5">
        <div class="em-ir-h"><span class="em-k" style="--k:${z.mil ? "#ff4a4a" : "#5aa9ff"}">AIR</span><b>${esc(z.name)}</b><em>${z.ts ? ago(z.ts) : z.error ? "error" : "sampling…"}</em></div>
        <div class="em-cp-s"><span><b>${z.aircraft ?? "—"}</b> aircraft${tr ? ` <i class="${tr > 0 ? "up" : "dn"}">${tr > 0 ? "+" : ""}${tr}/1h</i>` : ""}</span><span><b class="${z.mil ? "dn" : ""}">${z.mil}</b> military</span><span>GNSS ${gn}${sh != null ? ` · ${sh}% bad` : ""}</span></div>
        ${z.mil_calls.length ? `<div class="em-cp-n">MIL: ${z.mil_calls.map(esc).join(" · ")}</div>` : ""}
        <table class="em-at">${z.assets.map((a) => `<tr><td class="sym">${esc(a.sym)}</td><td class="nm">${esc(a.name)}</td><td class="r">${E.quoteCell(a.sym)}</td></tr>`).join("")}</table></div>`;
    }).join("");
    E.loadQuotes();
  }

  // ------------------------------------------------------------------ NHC 7-day outlook areas
  function drawOutlook() {
    G.g.outlook.clearLayers();
    const ol = (S.d && S.d.storms && S.d.storms.outlook) || [];
    if (!G.on.outlook) return;
    for (const o of ol) for (const r of o.rings) {
      L().polygon(E.unwrap(r), { pane: "gxvec", color: "#ffd23f", weight: 1, dashArray: "5 4", fillColor: "#ffd23f", fillOpacity: 0.08 })
        .bindTooltip(`NHC 7-DAY OUTLOOK · ${esc(o.basin || "")} · formation chance ${esc(o.prob7 || "?")}${o.risk ? ` (${esc(o.risk)})` : ""}`, { className: "em-tip" }).addTo(G.g.outlook);
    }
  }

  // ------------------------------------------------------------------ satellites (CelesTrak + satellite.js SGP4)
  async function satsOn() {
    try {
      await need(`${CDN}satellite.js@5.0.0/dist/satellite.min.js`);
      if (!G.sats.length) {
        const groups = await Promise.all(["stations", "gps-ops"].map((g) => api(`/world/gx/sats?group=${g}`)));
        G.sats = groups.flatMap((g) => g.tles.map((t) => ({ name: t[0], grp: g.group, rec: window.satellite.twoline2satrec(t[1], t[2]) })));
        E.src("celestrak", true);
      }
      satTick(); clearInterval(G.satTimer); G.satTimer = setInterval(() => { if (S.active) satTick(); }, 2000);
      note("sats", `SATS <b>${G.sats.length}</b>`);
    } catch (e) { E.src("celestrak", false, e.message); }
  }
  function satsOff() { clearInterval(G.satTimer); G.g.sats.clearLayers(); G.satMarks = []; note("sats"); }
  function satTick() {
    const sat = window.satellite, now = new Date(), gmst = sat.gstime(now);
    if (!G.satMarks.length) {
      G.satMarks = G.sats.map((s) => {
        const big = s.grp === "stations" && /ISS|TIANGONG|CSS/.test(s.name);
        return L().circleMarker([0, 0], { pane: "gxvec", radius: big ? 4.5 : 2.4, color: big ? "#fff" : "#b0b8ff", weight: big ? 1.5 : 0, fillColor: s.grp === "gps-ops" ? "#7f8cff" : "#d8dcff", fillOpacity: 0.9 })
          .bindTooltip(`${esc(s.name)} · ${s.grp === "gps-ops" ? "GPS" : "station"}`, { className: "em-tip", permanent: big, direction: "right" }).addTo(G.g.sats);
      });
    }
    G.sats.forEach((s, i) => {
      const pv = sat.propagate(s.rec, now); if (!pv || !pv.position) return;
      const g = sat.eciToGeodetic(pv.position, gmst);
      G.satMarks[i].setLatLng([sat.degreesLat(g.latitude), sat.degreesLong(g.longitude)]);
    });
  }

  // ------------------------------------------------------------------ credits
  async function loadCredits() {
    if (G.credited) return;
    try {
      const d = await api("/world/gx/credits"); G.credited = true;
      $("#em-cred").innerHTML = `<table class="em-at em-cred">${d.credits.map((c) => `<tr><td class="sym">${esc(c.layer)}</td><td><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.source)}</a><div class="dim">${esc(c.note)}</div></td></tr>`).join("")}</table>
        <div class="em-note">God's Eye View is MIT-licensed, © 2026 Bilawal Sidhu. Its TeleGeography submarine-cable dataset (non-commercial) is <b>not</b> included here.</div>`;
    } catch (e) { $("#em-cred").innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ wiring
  E.on("built", () => {
    shell(); initMap();
    GX.forEach(([k]) => { if (G.on[k]) apply(k); });
    every("choke", 120000, loadChoke);
    gxLegend();
  });
  E.on("refresh", () => { drawOutlook(); });
  E.on("pane", (p) => {
    if (p === "air") every("zones", 60000, loadZones); else stop("zones");
    if (p === "cred") loadCredits();
    if (p === "choke" && G.choke) renderChoke();
  });
  E.on("active", (on) => { if (G.windLayer && S.map) { if (on && !S.map.hasLayer(G.windLayer)) G.windLayer.addTo(S.map); } });
  NX.godseye = { _G: G, apply };
})();
