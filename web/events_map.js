/* WORLD › EVENTS MAP — live natural events on a dark Leaflet map, and what they can move in markets.
   Layers (each toggleable, refreshed every 90 s from /api/world/live/all, which caches every upstream feed server-side):
     QUAKES   USGS GeoJSON (hour/day/week/month): circles sized by magnitude, coloured by depth, fresh / big ones pulse
     STORMS   NHC CurrentStorms + KMZ cone, GDACS cyclones (track + uncertainty cone), NASA EONET storm tracks
              (projected cone when there is no official one): the storm icon sails along its track, then its forecast
     HAZARDS  GDACS alerts (floods, volcanoes, wildfires, droughts) + NASA EONET open events
     RADAR    RainViewer precipitation tiles with a time animation (keyless, /api/world/radar)
   Right: MARKET IMPACT — each significant event with the assets exposed to it (rule table app/world/impact.py) and live quotes.
   Bottom: ticker of the latest events, "Every move has a cause". Depends on dashboard.js (api), invest.js (NX.*), Leaflet. */
(function () {
  "use strict";
  const NX = window.NX;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const PREF = "nx_evmap_prefs";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const LAYERS = [["quakes", "QUAKES", "#ff4a4a"], ["storms", "STORMS", "#ffa028"], ["hazards", "GDACS", "#e8d44d"], ["eonet", "EONET", "#7fd1ff"], ["fires", "WILDFIRES", "#ff7a2f"], ["radar", "RADAR", "#5aa9ff"]];
  const FEEDS = [["hour", "1H"], ["day", "24H"], ["week", "7D M2.5+"], ["month", "30D M4.5+"]];
  const CAT_C = { TD: "#5aa9ff", TS: "#2be07e", C1: "#ffe14d", C2: "#ffa028", C3: "#ff6a28", C4: "#ff3b3b", C5: "#ff3bd0" };
  const ALERT_C = { Red: "#ff3b3b", Orange: "#ffa028", Green: "#2be07e" };
  const KIND = { quake: ["EQ", "#ff4a4a"], cyclone: ["TC", "#ffa028"], storm: ["TC", "#ffa028"], volcano: ["VO", "#ff3bd0"], wildfire: ["WF", "#ff7a2f"], flood: ["FL", "#5aa9ff"],
    drought: ["DR", "#c9a46b"], dust: ["DU", "#c9a46b"], landslide: ["LS", "#b07b4f"], heat: ["HT", "#ff6a28"], snow: ["SN", "#dfe8ff"], tsunami: ["TS", "#3bd5ff"], manmade: ["MM", "#aaa"], other: ["EV", "#aaa"] };
  const kindOf = (k) => KIND[k] || KIND.other;
  const S = { built: false, active: false, map: null, g: {}, d: null, feed: "day", quotes: {}, timer: 0, anim: 0, storms: [], radar: { frames: [], i: 0, layers: {}, timer: 0, host: "" },
    layers: Object.assign({ quakes: true, storms: true, hazards: true, eonet: true, fires: false, radar: false }, lget(PREF, {}).layers || {}), sel: null, lastOk: 0, markers: {} };
  S.feed = lget(PREF, {}).feed || "day";

  const depthC = (d) => (d < 35 ? "#ff4a4a" : d < 70 ? "#ff8a28" : d < 150 ? "#ffd23f" : d < 300 ? "#2be07e" : "#5aa9ff");
  const magR = (m) => Math.max(2.5, 2.2 + Math.pow(Math.max(0, m - 1.5), 1.55) * 1.6);
  const utc = (ts) => (ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "—");
  const ago = (ts) => (ts ? NX.ago(ts) + " ago" : "");
  const savePrefs = () => lset(PREF, { layers: S.layers, feed: S.feed });

  // ------------------------------------------------------------------ assets
  function needLeaflet() {
    if (!document.getElementById("leaflet-css")) {
      const l = document.createElement("link"); l.id = "leaflet-css"; l.rel = "stylesheet"; l.href = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css";
      document.head.appendChild(l);
    }
    if (window.L) return Promise.resolve();
    return new Promise((res, rej) => {
      const s = document.createElement("script"); s.src = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js";
      s.onload = res; s.onerror = () => rej(new Error("could not load the map library (Leaflet)")); document.body.appendChild(s);
    });
  }

  // ------------------------------------------------------------------ shell
  function shell(v) {
    v.innerHTML = `<div class="em-wrap" id="em-wrap">
      <div class="em-bar">
        <span class="em-title"><b>EVENTS MAP</b><i>quakes · storms · hazards · radar</i></span>
        <div class="bb-chips" id="em-layers">${LAYERS.map(([k, n, c]) => `<button class="bb-chip em-lc" data-l="${k}" style="--lc:${c}"><i></i>${n}</button>`).join("")}</div>
        <span class="em-sep"></span>
        <div class="bb-chips" id="em-feed" title="earthquake window">${FEEDS.map(([k, n]) => `<button class="bb-chip" data-f="${k}">${n}</button>`).join("")}</div>
        <span class="sp"></span>
        <div class="em-src" id="em-src"></div>
        <span class="em-upd" id="em-upd">—</span>
        <button class="bb-chip" id="em-refresh" title="refresh now">⟳</button>
        <button class="bb-chip" id="em-fs" title="full screen">⛶</button>
      </div>
      <div class="em-bar em-bar2" id="em-bar2" hidden></div>
      <div class="em-body">
        <div class="em-mapbox">
          <div id="em-map"></div>
          <div class="em-legend" id="em-legend"></div>
          <div class="em-radar hidden" id="em-radar"><button class="bb-chip" id="em-rplay">❚❚</button><input type="range" id="em-rr" min="0" max="0" value="0"/><span id="em-rt">—</span></div>
          <div class="em-kpis" id="em-kpis"></div>
        </div>
        <aside class="em-side">
          <div class="em-tabs" id="em-tabs"><button class="on" data-p="impact">IMPACT</button></div>
          <div class="em-pane" data-p="impact">
            <div class="bb-ph"><span class="bb-n">1</span>MARKET IMPACT<span class="sp"></span><span class="meta" id="em-imeta">exposed assets</span></div>
            <div class="em-sel" id="em-sel" hidden></div>
            <div class="bb-pb em-impact" id="em-impact">${NX.load("LOADING LIVE EVENTS")}</div>
            <div class="em-note">Rule table: which assets sit in or near an event's path, and their usual first reaction (▲ up / ▼ down / ◆ volatile). A historical tendency, not a forecast.</div>
          </div>
        </aside>
      </div>
      <div class="em-ticker"><div class="em-tlabel">EVERY MOVE HAS A CAUSE</div><div class="em-tview"><div class="em-ttrack" id="em-ttrack"></div></div></div>
    </div>`;
  }

  function legend() {
    const parts = [];
    if (S.layers.quakes) parts.push(`<div class="em-lg"><b>DEPTH</b>${[["&lt;35km", "#ff4a4a"], ["&lt;70", "#ff8a28"], ["&lt;150", "#ffd23f"], ["&lt;300", "#2be07e"], ["300+", "#5aa9ff"]].map(([t, c]) => `<span><i style="background:${c}"></i>${t}</span>`).join("")}</div>`);
    if (S.layers.storms) parts.push(`<div class="em-lg"><b>STORM</b>${Object.entries(CAT_C).map(([t, c]) => `<span><i style="background:${c}"></i>${t}</span>`).join("")}<span><i class="cone"></i>cone</span></div>`);
    if (S.layers.hazards || S.layers.eonet) parts.push(`<div class="em-lg"><b>ALERT</b>${Object.entries(ALERT_C).map(([t, c]) => `<span><i style="background:${c}"></i>${t.toUpperCase()}</span>`).join("")}</div>`);
    $("#em-legend").innerHTML = parts.join("");
  }

  function chips() {
    document.querySelectorAll("#em-layers .em-lc").forEach((b) => b.classList.toggle("on", !!S.layers[b.dataset.l]));
    document.querySelectorAll("#em-feed .bb-chip").forEach((b) => b.classList.toggle("on", b.dataset.f === S.feed));
    $("#em-feed").classList.toggle("dim", !S.layers.quakes);
  }

  function sizeStage() {
    const box = $("#em-wrap"); if (!box || !S.active) return;
    const fs = document.fullscreenElement === box;
    // phones: the page stacks toolbar / map / side panel and scrolls (bloomberg.css), so no fixed stage height
    if (!fs && window.matchMedia("(max-width: 860px)").matches) { box.style.height = ""; if (S.map) S.map.invalidateSize(); return; }
    const top = fs ? 0 : box.getBoundingClientRect().top + window.scrollY;
    box.style.height = `${Math.max(520, window.innerHeight - (fs ? 0 : Math.min(top, window.innerHeight * 0.4)) - (fs ? 0 : 8))}px`;
    if (S.map) S.map.invalidateSize();
  }

  // ------------------------------------------------------------------ map
  function buildMap() {
    const L = window.L;
    const map = (S.map = L.map("em-map", { center: [20, 10], zoom: 2, zoomSnap: 0.2, minZoom: 1.6, maxZoom: 10, worldCopyJump: true, preferCanvas: true, zoomControl: false,
      maxBounds: [[-85, -400], [85, 400]], maxBoundsViscosity: 0.8, attributionControl: true }));
    L.control.zoom({ position: "topleft" }).addTo(map);
    // Esri dark-gray canvas: free, keyless (CARTO's dark tiles now require an API key)
    const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
    L.tileLayer(`${ESRI}World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, className: "em-base",
      attribution: "Tiles &copy; Esri · USGS · NOAA NHC · GDACS · NASA EONET · RainViewer" }).addTo(map);
    map.createPane("radar"); map.getPane("radar").style.zIndex = 330; map.getPane("radar").style.pointerEvents = "none";
    map.createPane("labels"); map.getPane("labels").style.zIndex = 380; map.getPane("labels").style.pointerEvents = "none";
    map.createPane("cones"); map.getPane("cones").style.zIndex = 390;
    L.tileLayer(`${ESRI}World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { pane: "labels", maxZoom: 16, opacity: 0.8 }).addTo(map);
    const canvas = L.canvas({ padding: 0.4 });
    ["quakes", "pulse", "storms", "hazards", "eonet", "fires"].forEach((k) => (S.g[k] = L.layerGroup().addTo(map)));
    S.canvas = canvas;
    map.on("click", () => select(null));
    S.home = () => { map.invalidateSize(); const w = map.getSize().x; map.setView([24, 15], Math.max(1.6, Math.min(3, Math.log2(w / 256))), { animate: false }); };
    map.on("zoomend", () => map.getContainer().classList.toggle("em-zlo", map.getZoom() < 3.2));
  }

  const unwrap = (pts) => { // keep consecutive longitudes continuous so a dateline-crossing track does not streak across the map
    const out = []; let off = 0, prev = null;
    for (const p of pts) { let lon = p[1] + off; if (prev !== null) { if (lon - prev > 180) { off -= 360; lon -= 360; } else if (prev - lon > 180) { off += 360; lon += 360; } } out.push([p[0], lon]); prev = lon; }
    return out;
  };

  function quakePopup(q) {
    const tsu = q.tsunami ? `<div class="em-pp-flag">TSUNAMI FLAG — check NOAA tsunami.gov</div>` : "";
    return `<div class="em-pp"><div class="em-pp-h" style="--c:${depthC(q.depth)}"><b>M${q.mag.toFixed(1)}</b> EARTHQUAKE</div>
      <div class="em-pp-t">${esc(q.place)}</div>
      <table><tr><td>TIME</td><td>${utc(q.t)} <i>${ago(q.t)}</i></td></tr><tr><td>DEPTH</td><td>${q.depth} km</td></tr>
      ${q.alert ? `<tr><td>PAGER</td><td style="color:${ALERT_C[q.alert[0].toUpperCase() + q.alert.slice(1)] || "#fff"}">${esc(q.alert.toUpperCase())}</td></tr>` : ""}
      ${q.felt ? `<tr><td>FELT</td><td>${q.felt.toLocaleString()} reports</td></tr>` : ""}<tr><td>TSUNAMI</td><td>${q.tsunami ? '<b class="dn">YES</b>' : "no"}</td></tr></table>${tsu}
      ${q.url ? `<a href="${esc(q.url)}" target="_blank" rel="noopener">USGS EVENT PAGE ›</a>` : ""}</div>`;
  }

  function drawQuakes() {
    const L = window.L; S.g.quakes.clearLayers(); S.g.pulse.clearLayers();
    if (!S.layers.quakes || !S.d) return;
    const now = Date.now() / 1000;
    const qs = [...S.d.quakes.quakes].sort((a, b) => a.mag - b.mag);
    for (const q of qs) {
      const m = L.circleMarker([q.lat, q.lon], { renderer: S.canvas, radius: magR(q.mag), color: depthC(q.depth), weight: q.mag >= 5 ? 1.6 : 1, opacity: 0.95, fillColor: depthC(q.depth), fillOpacity: q.mag >= 4.5 ? 0.45 : 0.28 });
      m.bindPopup(() => quakePopup(q), { className: "em-popup", maxWidth: 300 });
      m.bindTooltip(`M${q.mag.toFixed(1)} · ${esc(q.place)}`, { className: "em-tip", direction: "top" });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); select(q.id); });
      m.addTo(S.g.quakes); S.markers[q.id] = m;
      if (q.mag >= 5.5 || (now - q.t < 3 * 3600 && q.mag >= 3.5) || q.tsunami) {
        const r = Math.round(magR(q.mag) * 2 + 6);
        const ic = L.divIcon({ className: "em-pulse", iconSize: [r, r], html: `<i style="--c:${depthC(q.depth)}"></i><i style="--c:${depthC(q.depth)}"></i>` });
        L.marker([q.lat, q.lon], { icon: ic, interactive: false, keyboard: false }).addTo(S.g.pulse);
      }
    }
  }

  function stormIcon(s) {
    const c = CAT_C[s.cat] || "#ffa028";
    return window.L.divIcon({ className: "em-storm", iconSize: [34, 34], iconAnchor: [17, 17],
      html: `<svg viewBox="-17 -17 34 34" class="spin" style="--c:${c}"><circle r="4.2" fill="${c}"/><path d="M0-13 C8-13 12-6 10 0 C9-6 4-8 0-8 Z" fill="${c}"/><path d="M0 13 C-8 13 -12 6 -10 0 C-9 6 -4 8 0 8 Z" fill="${c}"/></svg><b>${esc(s.cat)}</b>` });
  }

  function stormPopup(s) {
    const f = s.track.filter((p) => p.fc), o = s.track.filter((p) => !p.fc);
    return `<div class="em-pp"><div class="em-pp-h" style="--c:${CAT_C[s.cat] || "#ffa028"}"><b>${esc(s.cat)}</b> ${esc((s.name || "").toUpperCase())}${s.recent ? " · ENDED" : ""}</div>
      <table>${s.kt ? `<tr><td>WIND</td><td>${Math.round(s.kt)} kt · ${Math.round(s.kt * 1.852)} km/h</td></tr>` : ""}${s.mb ? `<tr><td>PRESSURE</td><td>${s.mb} mb</td></tr>` : ""}
      ${s.move ? `<tr><td>MOVING</td><td>${esc(s.move)}</td></tr>` : ""}<tr><td>UPDATED</td><td>${utc(s.t)} <i>${ago(s.t)}</i></td></tr>
      <tr><td>TRACK</td><td>${o.length} observed · ${f.length} forecast</td></tr><tr><td>CONE</td><td>${s.cone_kind === "official" ? "official" : s.cone_kind === "projected" ? "projected from recent motion (not an official forecast)" : "—"}</td></tr>
      <tr><td>SOURCE</td><td>${esc([s.src, ...(s.also || [])].join(" + "))}</td></tr>${s.countries && s.countries.length ? `<tr><td>AFFECTS</td><td>${esc(s.countries.join(", "))}</td></tr>` : ""}</table>
      ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">ADVISORY / REPORT ›</a>` : ""}</div>`;
  }

  function drawStorms() {
    const L = window.L; S.g.storms.clearLayers(); S.storms = [];
    if (!S.layers.storms || !S.d) return;
    const list = [...S.d.storms.storms];
    if (S.d.storms.recent) list.push(S.d.storms.recent);
    for (const s of list) {
      if (!s.track || !s.track.length) s.track = [{ lat: s.lat, lon: s.lon, cat: s.cat, fc: false }];
      const pts = unwrap(s.track.map((p) => [p.lat, p.lon]));
      const shift = pts.length ? pts[0][1] - s.track[0].lon : 0;
      for (const ring of s.cone || []) {
        const r = unwrap(ring).map((p) => [p[0], p[1] + shift + (Math.abs(p[1] + shift - pts[pts.length - 1][1]) > 180 ? (pts[pts.length - 1][1] > p[1] + shift ? 360 : -360) : 0)]);
        L.polygon(r, { pane: "cones", color: "#ffa028", weight: 1, dashArray: s.cone_kind === "projected" ? "4 4" : null, fillColor: "#ffa028", fillOpacity: s.recent ? 0.1 : 0.2, opacity: 0.95, interactive: false }).addTo(S.g.storms);
      }
      const obsN = s.track.filter((p) => !p.fc).length;
      const obs = pts.slice(0, Math.max(1, obsN)), fc = pts.slice(Math.max(0, obsN - 1));
      if (obs.length > 1) L.polyline(obs, { color: "#f1f1f1", weight: 2, opacity: s.recent ? 0.45 : 0.85 }).addTo(S.g.storms);
      if (fc.length > 1) L.polyline(fc, { color: "#ffa028", weight: 2, dashArray: "6 6", opacity: 0.9 }).addTo(S.g.storms);
      s.track.forEach((p, i) => {
        L.circleMarker(pts[i], { radius: p.fc ? 3 : 3.5, color: "#000", weight: 1, fillColor: CAT_C[p.cat] || "#ffa028", fillOpacity: p.fc ? 0.6 : 1 })
          .bindTooltip(`${esc(p.label || "")} ${esc(p.cat)}${p.kt ? ` · ${Math.round(p.kt)} kt` : ""}${p.fc ? " · forecast" : ""}`, { className: "em-tip", direction: "top" }).addTo(S.g.storms);
      });
      const now = pts[Math.max(0, obsN - 1)] || pts[0];
      const mk = L.marker(now, { icon: stormIcon(s), zIndexOffset: 1000 }).bindPopup(() => stormPopup(s), { className: "em-popup", maxWidth: 320 }).addTo(S.g.storms);
      mk.on("click", (e) => { L.DomEvent.stopPropagation(e); select(s.id); });
      L.marker(now, { icon: L.divIcon({ className: "em-slabel", iconSize: [160, 14], iconAnchor: [-14, 22], html: `${esc((s.name || "").toUpperCase())}${s.recent ? " · ENDED" : ""}` }), interactive: false }).addTo(S.g.storms);
      const ghost = L.marker(pts[0], { icon: stormIcon(s), interactive: false, opacity: 0.85 }).addTo(S.g.storms);
      S.markers[s.id] = mk;
      S.storms.push({ s, pts, ghost, t0: performance.now() + Math.random() * 1500 });
    }
  }

  // the "ghost" storm sails along the observed track and on into the forecast/cone, then loops
  function animate(now) {
    S.anim = requestAnimationFrame(animate);
    if (!S.active || !S.storms.length) return;
    for (const st of S.storms) {
      const n = st.pts.length; if (n < 2) continue;
      const dur = Math.min(14000, 3500 + n * 320), hold = 1400;
      const t = ((now - st.t0) % (dur + hold) + dur + hold) % (dur + hold);
      const f = Math.min(1, t / dur) * (n - 1), i = Math.min(n - 2, Math.floor(f)), u = f - i;
      const a = st.pts[i], b = st.pts[i + 1];
      st.ghost.setLatLng([a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]);
      const el = st.ghost.getElement(); if (el) el.style.opacity = t > dur ? String(Math.max(0, 1 - (t - dur) / hold)) : "0.9";
    }
  }

  function hazardIcon(h) {
    const [code, kc] = kindOf(h.kind), c = h.alert ? ALERT_C[h.alert] || kc : kc;
    return window.L.divIcon({ className: "em-hz", iconSize: [22, 16], iconAnchor: [11, 8], html: `<b style="--c:${c};--k:${kc}">${code}</b>` });
  }
  function hazardPopup(h) {
    const [code, kc] = kindOf(h.kind);
    return `<div class="em-pp"><div class="em-pp-h" style="--c:${h.alert ? ALERT_C[h.alert] || kc : kc}"><b>${code}</b> ${esc(h.kind.toUpperCase())}${h.alert ? ` · ${esc(h.alert.toUpperCase())} ALERT` : ""}</div>
      <div class="em-pp-t">${esc(h.title)}</div><table>${h.sev ? `<tr><td>SEVERITY</td><td>${esc(h.sev)}</td></tr>` : ""}
      <tr><td>SINCE</td><td>${utc(h.t0 || h.t)}</td></tr><tr><td>LATEST</td><td>${utc(h.t)} <i>${ago(h.t)}</i></td></tr><tr><td>SOURCE</td><td>${esc(h.src)}</td></tr></table>
      ${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener">SOURCE REPORT ›</a>` : ""}</div>`;
  }
  function drawHazards() {
    const L = window.L; ["hazards", "eonet", "fires"].forEach((k) => S.g[k].clearLayers());
    if (!S.d) return;
    for (const h of S.d.events) {
      const grp = h.kind === "wildfire" && !(h.alert === "Orange" || h.alert === "Red") ? "fires" : h.src === "GDACS" ? "hazards" : "eonet";
      if (!S.layers[grp]) continue;
      let m;
      if (grp === "fires") m = L.circleMarker([h.lat, h.lon], { renderer: S.canvas, radius: 3, color: "#ff7a2f", weight: 1, fillColor: "#ffb15c", fillOpacity: 0.7 });
      else {
        if (h.kind === "drought" || h.kind === "flood") L.circle([h.lat, h.lon], { radius: h.kind === "drought" ? 600000 : 160000, color: kindOf(h.kind)[1], weight: 1, dashArray: "3 5", fillOpacity: 0.06, interactive: false }).addTo(S.g[grp]);
        m = L.marker([h.lat, h.lon], { icon: hazardIcon(h) });
      }
      m.bindPopup(() => hazardPopup(h), { className: "em-popup", maxWidth: 300 }).bindTooltip(esc(h.title), { className: "em-tip", direction: "top" });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); select(h.id); });
      m.addTo(S.g[grp]); S.markers[h.id] = m;
    }
  }

  function drawAll() { S.markers = {}; drawQuakes(); drawStorms(); drawHazards(); legend(); kpis(); }

  // ------------------------------------------------------------------ radar (RainViewer)
  async function radarOn() {
    const box = $("#em-radar");
    if (!S.layers.radar) { radarOff(); return; }
    box.classList.remove("hidden");
    try {
      const r = await api("/world/radar");
      S.radar.host = r.host; S.radar.frames = r.frames || [];
      src("rainviewer", true);
    } catch (e) { src("rainviewer", false, e.message); $("#em-rt").textContent = "radar unavailable"; return; }
    const n = S.radar.frames.length; if (!n) return;
    const rr = $("#em-rr"); rr.max = String(n - 1);
    const past = S.radar.frames.findIndex((f) => f.time > Date.now() / 1000) ; S.radar.i = past > 0 ? past - 1 : n - 1;
    showFrame(S.radar.i); radarPlay(true);
  }
  function radarLayer(i) {
    const f = S.radar.frames[i]; if (!f) return null;
    if (!S.radar.layers[f.path]) S.radar.layers[f.path] = window.L.tileLayer(`${S.radar.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`, { pane: "radar", opacity: 0, maxNativeZoom: 7, maxZoom: 10, tileSize: 256 });
    return S.radar.layers[f.path];
  }
  function showFrame(i) {
    const n = S.radar.frames.length; if (!n || !S.map) return;
    S.radar.i = ((i % n) + n) % n;
    const cur = radarLayer(S.radar.i), nxt = radarLayer((S.radar.i + 1) % n);
    Object.values(S.radar.layers).forEach((l) => { if (l !== cur && l !== nxt && S.map.hasLayer(l)) S.map.removeLayer(l); else if (l !== cur) l.setOpacity(0); });
    if (!S.map.hasLayer(cur)) cur.addTo(S.map);
    if (nxt && !S.map.hasLayer(nxt)) nxt.addTo(S.map); // preload the next frame
    cur.setOpacity(0.62);
    const f = S.radar.frames[S.radar.i], fut = f.time > Date.now() / 1000;
    $("#em-rr").value = String(S.radar.i);
    $("#em-rt").innerHTML = `${new Date(f.time * 1000).toISOString().slice(11, 16)} UTC${fut ? ' <b class="em-nc">NOWCAST</b>' : ""}`;
  }
  function radarPlay(on) {
    clearInterval(S.radar.timer); S.radar.timer = 0;
    const b = $("#em-rplay"); if (b) b.textContent = on ? "❚❚" : "▶";
    if (on) S.radar.timer = setInterval(() => { if (S.active) showFrame(S.radar.i + 1); }, 700);
  }
  function radarOff() {
    radarPlay(false); $("#em-radar").classList.add("hidden");
    Object.values(S.radar.layers).forEach((l) => S.map && S.map.hasLayer(l) && S.map.removeLayer(l));
  }

  // ------------------------------------------------------------------ status, kpis
  function src(name, ok, err) {
    S.src = S.src || {}; S.src[name] = { ok, err };
    const el = $("#em-src"); if (!el) return;
    const label = { usgs: "USGS", nhc: "NHC", nhcgis: "NHC GIS", gdacs: "GDACS", eonet: "EONET", rainviewer: "RADAR", gfs: "GFS", nowcoast: "NOWCOAST", adsb: "ADSB.LOL", ais: "AIS", celestrak: "CELESTRAK" };
    el.innerHTML = Object.entries(S.src).map(([k, v]) => `<span class="${v.ok ? "ok" : "bad"}" data-uix-tip="${esc(label[k] || k)}|${v.ok ? "feed OK" : esc(v.err || "feed down — showing last good data")}"><i></i>${label[k] || k}</span>`).join("");
  }
  function kpis() {
    const d = S.d; if (!d) return;
    const q = d.quakes.quakes, big = q.filter((x) => x.mag >= 5).length, mx = q.reduce((a, x) => (x.mag > a.mag ? x : a), { mag: 0 });
    const alerts = d.events.filter((e) => e.alert === "Red" || e.alert === "Orange").length;
    const tile = (l, v, s, c) => `<div><span>${l}</span><b class="${c || ""}">${v}</b><em>${s || ""}</em></div>`;
    $("#em-kpis").innerHTML = tile("QUAKES", q.length, (FEEDS.find((f) => f[0] === S.feed) || [0, ""])[1]) + tile("M5+", big, "", big ? "dn" : "") +
      tile("LARGEST", mx.mag ? `M${mx.mag.toFixed(1)}` : "—", mx.place ? esc(mx.place.split(" of ").pop().slice(0, 22)) : "") +
      tile("STORMS", d.storms.n, d.storms.n ? d.storms.storms.map((s) => esc((s.name || "").replace(/^(Tropical Storm|Typhoon|Hurricane|Tropical Cyclone)\s+/i, ""))).join(", ").slice(0, 26) : d.storms.recent ? "none active" : "", d.storms.n ? "am" : "") +
      tile("GDACS ALERTS", alerts, "orange + red", alerts ? "am" : "") + tile("HAZARDS", d.events.length, "open events");
  }

  // ------------------------------------------------------------------ impact sidebar
  const BIAS = { "+": ['<b class="up">▲</b>', "usually up"], "-": ['<b class="dn">▼</b>', "usually down"], "~": ['<b class="am">◆</b>', "volatile"] };
  function quoteCell(sym) {
    const q = S.quotes[sym];
    if (!q) return `<span class="em-q" data-q="${esc(sym)}">—</span>`;
    const c = q.pct > 0 ? "up" : q.pct < 0 ? "dn" : "";
    const p = q.price >= 1000 ? q.price.toLocaleString(undefined, { maximumFractionDigits: 0 }) : q.price.toFixed(q.price < 10 ? 3 : 2);
    return `<span class="em-q" data-q="${esc(sym)}">${p} <b class="${c}">${q.pct > 0 ? "+" : ""}${q.pct.toFixed(2)}%</b></span>`;
  }
  function impactRow(r, sel) {
    const [code, kc] = kindOf(r.kind);
    return `<div class="em-ir${sel ? " sel" : ""}" data-id="${esc(r.id)}">
      <div class="em-ir-h"><span class="em-k" style="--k:${kc}">${code}</span><b>${esc(r.title)}</b><em>${ago(r.t)}</em></div>
      <div class="em-ir-m"><span class="em-sev"><i style="width:${Math.round(r.sev * 100)}%"></i></span><span>${esc(r.regions.join(" · ") || "global")}</span></div>
      <table class="em-at">${r.assets.map((a) => `<tr data-uix-tip="${esc(a.sym)} · ${esc(a.name)}|${esc(a.why)}|first reaction: ${BIAS[a.bias] ? BIAS[a.bias][1] : "?"}"><td class="sym">${esc(a.sym)}</td><td class="nm">${esc(a.name)}</td><td>${(BIAS[a.bias] || BIAS["~"])[0]}</td><td class="r">${quoteCell(a.sym)}</td></tr>`).join("")}</table></div>`;
  }
  function renderImpact() {
    const el = $("#em-impact"); if (!el || !S.d) return;
    const rows = S.d.impact;
    $("#em-imeta").textContent = `${rows.length} event${rows.length === 1 ? "" : "s"} with exposure`;
    el.innerHTML = rows.length ? rows.map((r) => impactRow(r, r.id === S.sel)).join("") : `<div class="bb-empty">No current event sits in a mapped market region. Quiet is information too.</div>`;
  }
  async function loadQuotes() {
    const syms = [...new Set([...(S.d ? S.d.impact.flatMap((r) => r.assets.map((a) => a.sym)) : []), ...[...document.querySelectorAll("#em-wrap .em-q[data-q]")].map((el) => el.dataset.q)])].slice(0, 40);
    if (!syms.length) return;
    try {
      const r = await api(`/world/live/quotes?s=${encodeURIComponent(syms.join(","))}`);
      Object.entries(r.quotes).forEach(([k, v]) => { if (v) S.quotes[k] = v; });
      document.querySelectorAll("#em-wrap .em-q[data-q]").forEach((el) => { el.outerHTML = quoteCell(el.dataset.q); });
    } catch (e) { /* quotes are a bonus: keep the dashes */ }
  }

  function findEvent(id) {
    const d = S.d; if (!d) return null;
    return d.quakes.quakes.find((q) => q.id === id) && { kind: "quake", ...d.quakes.quakes.find((q) => q.id === id) }
      || [...d.storms.storms, ...(d.storms.recent ? [d.storms.recent] : [])].find((s) => s.id === id)
      || d.events.find((e) => e.id === id) || null;
  }

  function select(id, fly) {
    S.sel = id;
    const box = $("#em-sel"); if (!box) return;
    document.querySelectorAll("#em-impact .em-ir").forEach((el) => el.classList.toggle("sel", el.dataset.id === id));
    if (!id) { box.hidden = true; return; }
    const ev = findEvent(id), imp = S.d && S.d.impact.find((r) => r.id === id);
    if (!ev) { box.hidden = true; return; }
    const title = ev.kind === "quake" ? `M${ev.mag.toFixed(1)} ${ev.place}` : ev.title || ev.name;
    box.hidden = false;
    box.innerHTML = imp ? `<div class="em-sel-h">SELECTED</div>${impactRow(imp, true)}` :
      `<div class="em-sel-h">SELECTED</div><div class="em-ir sel"><div class="em-ir-h"><span class="em-k" style="--k:${kindOf(ev.kind || "other")[1]}">${kindOf(ev.kind || "other")[0]}</span><b>${esc(title)}</b><em>${ago(ev.t)}</em></div><div class="em-none">No mapped market exposure for this event (outside the rule table's regions or below its severity bar).</div></div>`;
    if (imp) loadQuotes();
    const row = document.querySelector(`#em-impact .em-ir[data-id="${CSS.escape(id)}"]`); if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (fly && S.map) {
      S.map.flyTo([ev.lat, ev.lon], Math.max(S.map.getZoom(), 4.4), { duration: 1.1 });
      const m = S.markers[id]; if (m) setTimeout(() => m.openPopup && m.openPopup(), 1150);
    }
  }

  // ------------------------------------------------------------------ ticker
  function renderTicker() {
    const tr = $("#em-ttrack"); if (!tr || !S.d) return;
    const items = S.d.ticker;
    if (!items.length) { tr.innerHTML = `<span class="em-ti">NO EVENTS ABOVE THE TICKER THRESHOLD RIGHT NOW</span>`; tr.style.animation = "none"; return; }
    const one = items.map((i) => { const [code, kc] = kindOf(i.kind); return `<span class="em-ti" data-id="${esc(i.id)}"><span class="em-k" style="--k:${kc}">${code}</span>${esc(i.text)}<em>${ago(i.t)}</em></span>`; }).join("");
    tr.innerHTML = one + one;
    const secs = Math.max(40, items.length * 7);
    tr.style.animation = "none"; void tr.offsetWidth; tr.style.animation = `em-scroll ${secs}s linear infinite`;
  }

  // ------------------------------------------------------------------ data loop
  async function refresh() {
    const btn = $("#em-refresh"); if (btn) btn.classList.add("spin");
    try {
      const d = await api(`/world/live/all?feed=${encodeURIComponent(S.feed)}`);
      S.d = d; S.lastOk = Date.now();
      Object.entries(d.status || {}).forEach(([k, v]) => src(k, v.ok, v.err));
      drawAll(); renderImpact(); renderTicker();
      HOOKS.refresh.forEach((fn) => { try { fn(d); } catch (e) { console.warn("evmap hook", e); } });
      if (S.sel) select(S.sel);
      loadQuotes();
    } catch (e) {
      if (!S.d) $("#em-impact").innerHTML = NX.err(e);
      if (window.showToast) window.showToast(`EVENTS MAP: ${e.message}`, true);
    } finally { if (btn) btn.classList.remove("spin"); upd(); }
  }
  function upd() { const el = $("#em-upd"); if (el) el.textContent = S.lastOk ? `UPDATED ${Math.max(0, Math.round((Date.now() - S.lastOk) / 1000))}s AGO · AUTO 90s` : "—"; }

  async function build() {
    const v = $("#view-evmap"); shell(v); chips(); legend();
    await needLeaflet();
    buildMap(); S.built = true; sizeStage();
    $("#em-layers").addEventListener("click", (e) => {
      const b = e.target.closest("[data-l]"); if (!b) return;
      const k = b.dataset.l; S.layers[k] = !S.layers[k]; savePrefs(); chips();
      if (k === "radar") radarOn(); else if (k === "quakes") { drawQuakes(); kpis(); } else if (k === "storms") drawStorms(); else drawHazards();
      legend();
    });
    $("#em-feed").addEventListener("click", (e) => { const b = e.target.closest("[data-f]"); if (!b || b.dataset.f === S.feed) return; S.feed = b.dataset.f; savePrefs(); chips(); refresh(); });
    $("#em-refresh").addEventListener("click", refresh);
    $("#em-fs").addEventListener("click", () => { const w = $("#em-wrap"); if (document.fullscreenElement) document.exitFullscreen(); else if (w.requestFullscreen) w.requestFullscreen().catch(() => {}); });
    document.addEventListener("fullscreenchange", () => setTimeout(sizeStage, 60));
    $("#em-rplay").addEventListener("click", () => radarPlay(!S.radar.timer));
    $("#em-rr").addEventListener("input", (e) => { radarPlay(false); showFrame(Number(e.target.value)); });
    const pick = (e) => { const r = e.target.closest("[data-id]"); if (r) select(r.dataset.id, true); };
    $("#em-impact").addEventListener("click", pick); $("#em-ttrack").addEventListener("click", pick); $("#em-sel").addEventListener("click", pick);
    window.addEventListener("resize", sizeStage);
    HOOKS.built.forEach((fn) => { try { fn(); } catch (e) { console.warn("evmap hook", e); } });
    sizeStage(); S.home(); S.map.getContainer().classList.toggle("em-zlo", S.map.getZoom() < 3.2);
    $("#em-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-p]"); if (b) pane(b.dataset.p); });
    await refresh();
    if (S.layers.radar) radarOn();
    S.timer = setInterval(() => { if (S.active && document.visibilityState === "visible") refresh(); }, 90000);
    setInterval(upd, 5000);
    S.anim = requestAnimationFrame(animate);
  }

  function pane(p) {
    document.querySelectorAll("#em-tabs [data-p]").forEach((b) => b.classList.toggle("on", b.dataset.p === p));
    document.querySelectorAll("#em-wrap .em-pane").forEach((el) => { el.hidden = el.dataset.p !== p; });
    S.pane = p; HOOKS.pane.forEach((fn) => fn(p));
  }
  const HOOKS = { built: [], refresh: [], pane: [], active: [] };
  // extension API for godseye_layers.js (and future layer packs): they add chips, panes and map layers through this
  NX.evmap = { _S: S, refresh, select, quoteCell, loadQuotes, src, pane, esc, ago, utc, unwrap, savePrefs, HOOKS, on: (k, fn) => HOOKS[k].push(fn) };
  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => {
    if (prev) prev(tab);
    const on = tab === "evmap", was = S.active;
    S.active = on;
    if (on && !S.started) { S.started = true; dock(true); build().catch((e) => { const v = $("#view-evmap"); if (v) v.innerHTML = NX.err(e); }); return; }
    dock(on);
    HOOKS.active.forEach((fn) => fn(on));
    if (on) { setTimeout(sizeStage, 30); if (!was && S.lastOk && Date.now() - S.lastOk > 60000) refresh(); }
  };
  // full-screen page: fold the side dock away while the map is open and put it back on leaving
  function dock(on) {
    const t = document.getElementById("sd-toggle"); if (!t) return;
    if (on && document.body.classList.contains("sd-open")) { S.dockFolded = true; t.click(); }
    else if (!on && S.dockFolded) { S.dockFolded = false; if (!document.body.classList.contains("sd-open")) t.click(); }
  }
})();
