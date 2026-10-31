/* WORLD: a 3D globe you drag around (globe.gl) that flies into a country and switches to a flat street map (Leaflet) at city level.
   Layers: live radio stations (click to listen, SCAN hops across the world), planes + military (OpenSky / adsb.lol via the server sampler),
   public CCTV (official open-data cameras only), quakes (USGS), natural events (NASA EONET), rain radar. Click a country for its dashboard:
   local time, stock index and currency, weather, headlines, top radio stations and what is over it right now. */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, big } = NX;
  const W = (NX.world = {});
  const PREF = "nx_world_prefs";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const flag = (cc) => (cc && /^[A-Za-z]{2}$/.test(cc) ? `<img class="wd-fl" src="https://flagcdn.com/w40/${cc.toLowerCase()}.png" alt="${cc}" onerror="this.style.visibility='hidden'"/>` : "");
  const rad = Math.PI / 180;

  const LAYERS = [["radio", "RADIO", "#ffa028"], ["planes", "PLANES", "#5aa9ff"], ["mil", "MILITARY", "#ff4a4a"], ["cams", "CCTV", "#2be07e"], ["quakes", "QUAKES", "#ff6b9a"], ["events", "EVENTS", "#e8d44d"], ["rain", "RAIN RADAR", "#7fd1ff"], ["heat", "NEWS HEAT", "#ff6b6b"], ["btc", "BTC RAIN", "#f7931a"]];
  const S = { built: false, mode: "globe", layers: Object.assign({ radio: true, planes: true, mil: true, cams: true, quakes: true, events: false, rain: false, heat: false, btc: false }, lget(PREF, {})), data: { radio: [], planes: [], mil: [], cams: [], quakes: [], events: [] },
    feats: [], sel: null, globe: null, map: null, groups: null, radar: null, hover: null, audio: null, hls: null, station: null, scan: { on: false, timer: null, cc: null }, planesTs: 0, tabActive: false, flatCenter: null,
    heat: { rows: {}, heads: {}, mode: "stress", ts: 0 }, quakesWeek: [], tm: { live: true, ts: null, times: [], playing: false, speed: 1, timer: null, busy: false }, btc: { on: false, ws: null, poll: null, drops: [], raf: 0, seen: new Set(), price: null, stamps: [], big: 0, lastMsg: 0 } };

  // ------------------------------------------------------------------ geometry helpers
  const bbox = (f) => {
    if (f._bb) return f._bb;
    let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
    const walk = (c) => { if (typeof c[0] === "number") { x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]); y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]); } else c.forEach(walk); };
    walk(f.geometry.coordinates);
    return (f._bb = [x0, y0, x1, y1]);
  };
  const inRing = (ring, x, y) => { let inside = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1]; if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside; } return inside; };
  const inFeature = (f, lat, lng) => {
    const b = bbox(f); if (lng < b[0] || lng > b[2] || lat < b[1] || lat > b[3]) return false;
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
    return polys.some((p) => inRing(p[0], lng, lat) && !p.slice(1).some((h) => inRing(h, lng, lat)));
  };
  const countryAt = (lat, lng) => S.feats.find((f) => inFeature(f, lat, lng)) || null;
  const centre = (f) => { const b = bbox(f); return { lat: (b[1] + b[3]) / 2, lng: (b[0] + b[2]) / 2, span: Math.max(b[2] - b[0], (b[3] - b[1]) * 1.6) }; };
  const distDeg = (lat1, lng1, lat2, lng2) => { const dl = Math.abs(lng1 - lng2) > 180 ? 360 - Math.abs(lng1 - lng2) : Math.abs(lng1 - lng2); return Math.hypot((lat1 - lat2), dl * Math.cos(((lat1 + lat2) / 2) * rad)); };
  const zoomFromAlt = (a) => Math.max(3, Math.min(12, Math.round(4.7 + Math.log2(0.3 / Math.max(a, 0.004)))));
  const altFromZoom = (z) => Math.min(2.4, 0.3 * Math.pow(2, 4.7 - z));

  // ------------------------------------------------------------------ data
  const ago = (ts) => (ts ? NX.ago(ts) : "—");
  async function loadStatic() {
    const [c, r] = await Promise.all([api("/world/countries"), api("/world/radio").catch(() => ({ stations: [] }))]);
    S.feats = c.features; S.data.radio = r.stations;
  }
  const loadPlanes = async () => { try { const r = await api("/world/planes"); S.data.planes = r.rows; S.data.mil = r.mil; S.planesTs = r.ts; } catch (e) { /* keep last */ } };
  const loadCams = async () => { try { S.data.cams = (await api("/world/cams")).cams; } catch (e) { /* keep last */ } };
  const loadQuakes = async () => { try { S.data.quakes = (await api("/world/quakes?range=day")).quakes; } catch (e) { /* keep last */ } };
  const loadEvents = async () => { try { S.data.events = (await api("/world/events")).events; } catch (e) { /* keep last */ } };
  const alive = () => document.body.contains($("#wd-stage"));

  // ------------------------------------------------------------------ globe
  // news heat: volume = headlines in the last 24 h (feed caps near 100); stress = share of headlines with severity / negative words
  const heatVal = (cc) => { const r = S.heat.rows[cc]; if (!r) return 0; return S.heat.mode === "volume" ? Math.min(1, r[0] / 100) : Math.min(1, ((r[2] + r[1] * 0.5) / Math.max(r[0], 12)) * 1.7); };
  const heatColor = (t) => (S.heat.mode === "volume" ? `rgba(255,190,60,${0.05 + 0.6 * t})` : `rgba(255,64,64,${0.05 + 0.72 * t})`);
  const capColor = (f) => (f === S.hover ? "rgba(255,160,40,0.3)" : S.sel === f ? "rgba(255,160,40,0.34)" : S.layers.heat ? heatColor(heatVal(f.properties.cc)) : "rgba(20,20,20,0.35)");
  function refreshPolys() { const g = S.globe; if (!g) return; g.polygonCapColor(capColor).polygonAltitude((f) => (f === S.hover || f === S.sel ? 0.02 : 0.004)); }
  function refreshPoints() {
    const g = S.globe; if (!g) return;
    const P = [], L = S.layers;
    if (L.radio) S.data.radio.forEach((r) => P.push({ lat: r[2], lng: r[3], c: "#ffa028", r: 0.13, a: 0.0006 }));
    if (L.cams) S.data.cams.forEach((r) => P.push({ lat: r[2], lng: r[3], c: "#2be07e", r: 0.18, a: 0.0008 }));
    if (L.events) S.data.events.forEach((r) => P.push({ lat: r[3], lng: r[4], c: "#e8d44d", r: 0.34, a: 0.001 }));
    if (L.planes) S.data.planes.forEach((r) => P.push({ lat: r[3], lng: r[4], c: "#7fc4ff", r: 0.09, a: 0.0015 }));
    if (L.mil) S.data.mil.forEach((r) => P.push({ lat: r[3], lng: r[4], c: "#ff4a4a", r: 0.24, a: 0.002 }));
    g.pointsData(P);
    g.ringsData(L.quakes ? S.data.quakes.filter((q) => (q[3] || 0) >= 3.5) : []);
    stats();
  }
  function stats() {
    const L = S.layers, d = S.data, bits = [];
    if (L.radio) bits.push(`<b>${d.radio.length.toLocaleString()}</b> stations`);
    if (L.planes) bits.push(`<b>${d.planes.length.toLocaleString()}</b> planes <i>${ago(S.planesTs)} old</i>`);
    if (L.mil) bits.push(`<b>${d.mil.length}</b> military`);
    if (L.cams) bits.push(`<b>${d.cams.length}</b> cameras`);
    if (L.quakes) bits.push(`<b>${d.quakes.length}</b> quakes 24h`);
    if (L.events) bits.push(`<b>${d.events.length}</b> events`);
    const el = $("#wd-stats"); if (el) el.innerHTML = (bits.join(" · ") || "no layers on") + (S.tm.live ? "" : `<br><b class="wd-hist">HISTORY ${new Date(S.tm.ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</b> <i>military and radar are live-only</i>`);
  }

  function initGlobe() {
    const el = $("#wd-globe"), stage = $("#wd-stage");
    const g = Globe()(el).width(stage.clientWidth).height(stage.clientHeight)
      .globeImageUrl("//unpkg.com/three-globe/example/img/earth-night.jpg").backgroundImageUrl("//unpkg.com/three-globe/example/img/night-sky.png")
      .showAtmosphere(true).atmosphereColor("#ffa028").atmosphereAltitude(0.16)
      .polygonsData(S.feats).polygonCapColor(capColor).polygonSideColor(() => "rgba(0,0,0,0)").polygonStrokeColor(() => "rgba(255,160,40,0.28)").polygonAltitude(() => 0.004)
      .polygonLabel((f) => { const r = S.layers.heat && S.heat.rows[f.properties.cc]; return `<div class="wd-tip">${flag(f.properties.cc)} ${esc(f.properties.name)}${r ? `<br><span>${r[0]} headlines · ${r[2]} severe · ${r[1]} negative</span>` : ""}</div>`; })
      .onPolygonHover((f) => { S.hover = f; refreshPolys(); el.style.cursor = f ? "pointer" : "grab"; })
      .onPolygonClick((f, ev, c) => globeClick(c.lat, c.lng, f)).onGlobeClick((c) => globeClick(c.lat, c.lng, null))
      .pointLat("lat").pointLng("lng").pointColor("c").pointRadius("r").pointAltitude("a").pointsMerge(true).pointResolution(4)
      .ringLat((q) => q[1]).ringLng((q) => q[2]).ringMaxRadius((q) => 1.2 + (q[3] || 3) * 0.9).ringPropagationSpeed(2.2).ringRepeatPeriod(1800).ringColor(() => (t) => `rgba(255,107,154,${1 - t})`)
      .onZoom((pov) => { if (S.mode === "globe" && pov.altitude < 0.27 && S.tabActive) enterFlat(pov.lat, pov.lng, zoomFromAlt(pov.altitude)); });
    g.pointOfView({ lat: 31.5, lng: 34.9, altitude: 1.25 }, 0);
    g.controls().autoRotate = false; g.controls().zoomSpeed = 0.9; g.controls().minDistance = 101.5;
    S.globe = g;
    refreshPoints();
  }

  // ------------------------------------------------------------------ flat map
  const STYLE = { radio: ["#ffa028", 4], planes: ["#7fc4ff", 3], mil: ["#ff4a4a", 5], cams: ["#2be07e", 5], quakes: ["#ff6b9a", 6], events: ["#e8d44d", 6] };
  function initFlat() {
    const map = L.map($("#wd-flat"), { preferCanvas: true, zoomControl: true, minZoom: 3, worldCopyJump: true, attributionControl: true });
    // OpenStreetMap tiles (CARTO's free dark tiles now demand an API key), darkened with a CSS filter
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, className: "wd-tiles", attribution: "© OpenStreetMap contributors" }).addTo(map);
    S.groups = L.layerGroup().addTo(map); S.map = map;
    map.on("moveend", renderFlat);
    map.on("zoomend", () => { if (S.mode === "flat" && map.getZoom() <= 3) { const c = map.getCenter(); exitFlat(c.lat, c.lng, altFromZoom(3)); } });
    map.on("click", (e) => { const f = countryAt(e.latlng.lat, e.latlng.lng); if (f) selectCountry(f, false); });
    map.setView([31.5, 34.9], 6);
  }
  function renderFlat() {
    if (!S.map || S.mode !== "flat") return;
    const b = S.map.getBounds().pad(0.15), Ly = S.layers; S.groups.clearLayers();
    const add = (key, arr, la, lo, cap, click, size) => {
      let n = 0;
      for (const r of arr) {
        if (!b.contains([r[la], r[lo]])) continue;
        if (++n > cap) break;
        const st = STYLE[key], rd = size ? size(r) : st[1];
        L.circleMarker([r[la], r[lo]], { radius: rd, color: st[0], weight: 1, fillColor: st[0], fillOpacity: key === "quakes" ? 0.25 : 0.7 }).on("click", () => click(r)).addTo(S.groups);
      }
    };
    if (Ly.radio) add("radio", S.data.radio, 2, 3, 2500, (r) => showItem("radio", r));
    if (Ly.planes) add("planes", S.data.planes, 3, 4, 2500, (r) => showItem("plane", r));
    if (Ly.mil) add("mil", S.data.mil, 3, 4, 800, (r) => showItem("mil", r));
    if (Ly.cams) add("cams", S.data.cams, 2, 3, 900, (r) => showItem("cam", r));
    if (Ly.quakes) add("quakes", S.data.quakes, 1, 2, 500, (r) => showItem("quake", r), (r) => 3 + (r[3] || 2.5) * 2);
    if (Ly.events) add("events", S.data.events, 3, 4, 400, (r) => showItem("event", r));
  }
  async function syncRadar() {
    if (!S.map) return;
    if (S.radar) { S.map.removeLayer(S.radar); S.radar = null; }
    if (!S.layers.rain) return;
    try { const r = await api("/world/radar"); S.radar = L.tileLayer(`${r.host}${r.latest.path}/256/{z}/{x}/{y}/2/1_1.png`, { opacity: 0.65, zIndex: 5, attribution: "RainViewer" }).addTo(S.map); } catch (e) { showToast("Rain radar is unavailable right now"); }
  }
  function enterFlat(lat, lng, zoom) {
    if (S.mode === "flat") return;
    S.mode = "flat"; $("#wd-flat").classList.remove("wd-hidden"); $("#wd-globe").classList.add("wd-hidden"); $("#wd-back").classList.remove("hidden");
    S.globe.pauseAnimation(); S.map.invalidateSize(); S.map.setView([lat, lng], zoom, { animate: false }); syncRadar(); renderFlat();
  }
  function exitFlat(lat, lng, alt) {
    if (S.mode === "globe") return;
    S.mode = "globe"; $("#wd-flat").classList.add("wd-hidden"); $("#wd-globe").classList.remove("wd-hidden"); $("#wd-back").classList.add("hidden");
    S.globe.resumeAnimation(); S.globe.pointOfView({ lat, lng, altitude: Math.max(alt, 0.5) }, 0);
  }

  // ------------------------------------------------------------------ clicks: nearest item (when zoomed in) or country
  function nearest(lat, lng, alt) {
    const tol = Math.max(0.12, alt * 1.1); let best = null, bd = tol;
    const scan = (kind, arr, la, lo, on) => { if (!on) return; for (const r of arr) { const d = distDeg(lat, lng, r[la], r[lo]); if (d < bd) { bd = d; best = [kind, r]; } } };
    const L2 = S.layers;
    scan("cam", S.data.cams, 2, 3, L2.cams); scan("mil", S.data.mil, 3, 4, L2.mil); scan("radio", S.data.radio, 2, 3, L2.radio); scan("plane", S.data.planes, 3, 4, L2.planes); scan("event", S.data.events, 3, 4, L2.events);
    if (L2.quakes) for (const q of S.data.quakes) { const d = distDeg(lat, lng, q[1], q[2]) - (q[3] || 0) * 0.05; if (d < bd) { bd = d; best = ["quake", q]; } }
    return best;
  }
  function globeClick(lat, lng, poly) {
    const now = performance.now(); if (now - (globeClick.t || 0) < 80) return; globeClick.t = now;
    const alt = S.globe.pointOfView().altitude;
    if (alt < 1.0) { const hit = nearest(lat, lng, alt); if (hit) return showItem(hit[0], hit[1]); }
    const f = poly || countryAt(lat, lng); if (f) selectCountry(f, true);
  }

  // ------------------------------------------------------------------ item card (station / camera / plane / quake / event)
  let camTimer = null;
  function showItem(kind, r) {
    const el = $("#wd-info"); clearInterval(camTimer); el.classList.remove("hidden");
    const close = '<span class="wd-x" id="wd-info-x">✕</span>';
    if (kind === "radio") {
      el.innerHTML = `${close}<div class="wd-k">RADIO</div><b>${esc(r[1])}</b><div class="dim">${flag(r[4])} ${esc(r[4])} · ${num(r[5], 0)} votes${r[6] ? " · HLS" : ""}</div><button class="wd-btn" id="wd-play">▶ LISTEN</button>`;
      $("#wd-play").addEventListener("click", () => { stopScan(); playStation(r[0]); });
    } else if (kind === "cam") {
      const bust = () => `${r[4]}${r[4].includes("?") ? "&" : "?"}t=${Date.now()}`;
      el.innerHTML = `${close}<div class="wd-k">CCTV · ${esc(String(r[6]).toUpperCase())}</div><b>${esc(r[1])}</b><div class="wd-cam">${r[8] ? `<iframe src="${esc(r[8])}" allow="autoplay; encrypted-media" allowfullscreen></iframe>` : `<img id="wd-cam-i" src="${bust()}" alt=""/>`}</div>
        <div class="dim">${r[5] ? `<a href="${esc(r[5])}" target="_blank" rel="noopener">short video clip</a> · ` : ""}image refreshes every 15 s · official open-data camera</div>`;
      if (!r[8]) camTimer = setInterval(() => { const i = $("#wd-cam-i"); if (i) i.src = bust(); else clearInterval(camTimer); }, 15000);
    } else if (kind === "plane" || kind === "mil") {
      el.innerHTML = `${close}<div class="wd-k">${kind === "mil" ? "MILITARY AIRCRAFT" : "AIRCRAFT"}</div><b>${esc(r[1] || r[0].toUpperCase())}</b><div class="dim">${esc(r[2] || "")}${kind === "mil" && r[8] ? " · " + esc(r[8]) : ""}</div>
        <div class="wd-kv"><span>ALT</span><b>${num(r[5] * 3.28084, 0)} ft</b><span>SPEED</span><b>${num(r[6] * 1.94384, 0)} kt</b><span>TRACK</span><b>${r[7]}°</b><span>ICAO</span><b>${esc(r[0])}</b></div><div class="dim">position sampled ${ago(S.planesTs)} ago</div>`;
    } else if (kind === "quake") {
      el.innerHTML = `${close}<div class="wd-k">EARTHQUAKE</div><b>M ${num(r[3], 1)}</b><div class="dim">${esc(r[6])}</div><div class="wd-kv"><span>DEPTH</span><b>${r[4]} km</b><span>TIME</span><b>${new Date(r[5] * 1000).toLocaleString("en-GB")}</b></div>`;
    } else if (kind === "event") {
      el.innerHTML = `${close}<div class="wd-k">${esc(String(r[2]).toUpperCase())}</div><b>${esc(r[1])}</b><div class="dim">${r[5] ? new Date(r[5] * 1000).toLocaleString("en-GB") : ""}${r[6] ? ` · <a href="${esc(r[6])}" target="_blank" rel="noopener">source</a>` : ""}</div>`;
    }
    $("#wd-info-x").addEventListener("click", () => { clearInterval(camTimer); el.classList.add("hidden"); });
    const lat = kind === "radio" || kind === "cam" ? r[2] : kind === "quake" ? r[1] : r[3], lng = kind === "radio" || kind === "cam" ? r[3] : kind === "quake" ? r[2] : r[4];
    if (S.mode === "flat") S.map.panTo([lat, lng]); else if (S.globe.pointOfView().altitude > 0.9) S.globe.pointOfView({ lat, lng, altitude: 0.7 }, 900);
  }

  // ------------------------------------------------------------------ radio player + scan
  function audio() {
    if (!S.audio) { S.audio = new Audio(); S.audio.preload = "none"; S.audio.volume = lget(PREF + "_vol", 0.8); S.audio.addEventListener("error", () => { if (S.station) barStatus("stream unavailable"); if (S.scan.on) setTimeout(scanNext, 800); }); S.audio.addEventListener("playing", () => barStatus("live")); S.audio.addEventListener("waiting", () => barStatus("buffering…")); }
    return S.audio;
  }
  function barStatus(t) { const el = $("#wd-bar-st"); if (el) el.textContent = t; }
  async function playStation(id) {
    try {
      const st = await api(`/world/radio/play/${id}`), a = audio();
      S.station = st; if (S.hls) { S.hls.destroy(); S.hls = null; }
      barShow(st); barStatus("connecting…");
      if (st.hls && window.Hls && Hls.isSupported()) { S.hls = new Hls(); S.hls.loadSource(st.url); S.hls.attachMedia(a); a.play().catch(() => {}); }
      else { a.src = st.url; await a.play(); }
    } catch (e) { barStatus("could not play"); if (S.scan.on) setTimeout(scanNext, 800); }
  }
  function barShow(st) {
    const el = $("#wd-bar-now"); if (!el) return;
    el.innerHTML = `${st.favicon ? `<img src="${esc(st.favicon)}" onerror="this.remove()" alt=""/>` : ""}<div><b>${esc(st.name)}</b><span>${flag(st.cc)} ${esc(st.country || "")} · ${esc(st.tags || "")} ${st.bitrate ? "· " + st.bitrate + " kbps" : ""}</span></div>`;
    $("#wd-stop").classList.remove("hidden");
  }
  function stopRadio() { if (S.audio) { S.audio.pause(); S.audio.removeAttribute("src"); S.audio.load(); } if (S.hls) { S.hls.destroy(); S.hls = null; } S.station = null; const el = $("#wd-bar-now"); if (el) el.innerHTML = '<span class="dim">Click a yellow dot or a country to listen.</span>'; $("#wd-stop").classList.add("hidden"); barStatus(""); }
  function stopScan() { S.scan.on = false; clearTimeout(S.scan.timer); const b = $("#wd-scan"); if (b) b.classList.remove("on"); }
  function scanNext() {
    if (!S.scan.on || !alive()) return;
    clearTimeout(S.scan.timer);
    const pool = S.data.radio.filter((r) => r[5] >= 15 && (!S.scan.cc || r[4] === S.scan.cc)), r = pool[Math.floor(Math.random() * pool.length)];
    if (!r) return stopScan();
    if (S.mode === "globe") S.globe.pointOfView({ lat: r[2], lng: r[3], altitude: S.scan.cc ? 0.9 : 1.6 }, 1600);
    playStation(r[0]); S.scan.timer = setTimeout(scanNext, 16000);
  }
  function startScan(cc) { S.scan = { on: true, timer: null, cc: cc || null }; const b = $("#wd-scan"); if (b) b.classList.add("on"); showToast(cc ? "Scanning stations in this country (16 s each)" : "Scanning the world's radio (16 s per station)"); scanNext(); }

  // ------------------------------------------------------------------ country dashboard
  const WMO = (c) => (c === 0 ? "Clear" : c <= 3 ? "Partly cloudy" : c <= 48 ? "Fog" : c <= 57 ? "Drizzle" : c <= 67 ? "Rain" : c <= 77 ? "Snow" : c <= 82 ? "Showers" : c >= 95 ? "Thunderstorm" : "—");
  function localTime(tz, lng) {
    const now = new Date();
    if (tz) { try { return `${now.toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" })} · ${now.toLocaleDateString("en-GB", { timeZone: tz, weekday: "short", day: "2-digit", month: "short" })}`; } catch (e) { /* fall through */ } }
    const off = Math.round(lng / 15), t = new Date(now.getTime() + now.getTimezoneOffset() * 60000 + off * 3600000);
    return `≈ ${t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} (UTC${off >= 0 ? "+" : ""}${off})`;
  }
  function countsFor(f) {
    const inF = (arr, la, lo) => { let n = 0; for (const r of arr) if (inFeature(f, r[la], r[lo])) n++; return n; };
    return { radio: inF(S.data.radio, 2, 3), planes: inF(S.data.planes, 3, 4), mil: inF(S.data.mil, 3, 4), cams: inF(S.data.cams, 2, 3), quakes: inF(S.data.quakes, 1, 2), events: inF(S.data.events, 3, 4) };
  }
  async function selectCountry(f, fly) {
    S.sel = f; refreshPolys(); stopScanIfOther(f);
    const cc = f.properties.cc, name = f.properties.name, c = centre(f), el = $("#wd-country");
    if (fly && S.mode === "globe") S.globe.pointOfView({ lat: c.lat, lng: c.lng, altitude: Math.max(0.3, Math.min(1.5, c.span / 50)) }, 1300);
    el.classList.remove("hidden");
    const cnt = countsFor(f), top = S.data.radio.filter((r) => r[4] === cc).slice(0, 8);
    const head = `<div class="wd-ch"><span class="wd-flag">${flag(cc)}</span><div><b>${esc(name)}</b><span id="wd-cap" class="dim"></span></div><span class="wd-x" id="wd-country-x">✕</span></div>`;
    const counts = `<div class="wd-counts"><div><b>${cnt.radio}</b>radio</div><div><b>${cnt.planes}</b>planes</div><div><b>${cnt.mil}</b>military</div><div><b>${cnt.cams}</b>cameras</div><div><b>${cnt.quakes}</b>quakes 24h</div><div><b>${cnt.events}</b>events</div></div>`;
    const radioHtml = top.length ? `<div class="wd-sec">RADIO <button class="wd-mini" id="wd-cscan">SCAN COUNTRY</button></div>${top.map((r) => `<div class="wd-row" data-play="${r[0]}"><span>▶ ${esc(r[1])}</span><em>${num(r[5], 0)}</em></div>`).join("")}` : '<div class="wd-sec">RADIO</div><div class="dim">No geo-tagged stations listed here.</div>';
    el.innerHTML = `${head}<div id="wd-cbody">${counts}<div class="wd-sec">LOADING MARKETS, WEATHER, NEWS…</div>${radioHtml}</div>`;
    $("#wd-country-x").addEventListener("click", () => { S.sel = null; refreshPolys(); el.classList.add("hidden"); });
    const bind = () => { el.querySelectorAll("[data-play]").forEach((x) => x.addEventListener("click", () => { stopScan(); playStation(x.dataset.play); })); const sb = $("#wd-cscan"); if (sb) sb.addEventListener("click", () => startScan(cc)); };
    bind();
    try {
      const d = await api(`/world/country/${cc}?name=${encodeURIComponent(name)}&lat=${c.lat.toFixed(2)}&lon=${c.lng.toFixed(2)}`);
      if (S.sel !== f) return;
      $("#wd-cap").textContent = d.capital ? ` · ${d.capital}` : ""; S.lastTz = d.tz;
      const mk = (q, label, cur) => (q ? `<div class="wd-mrow"><span>${label}</span><b>${q.symbol.includes("=X") ? num(q.price, q.price > 50 ? 2 : 4) : num(q.price, 2)}</b><em class="${dir(q.pct)}">${pct(q.pct)}</em>${NX.spark(q.spark, q.pct >= 0, 56, 14)}</div>` : "");
      const w = d.weather;
      const market = (d.index || d.fx) ? `<div class="wd-sec">MARKETS</div>${mk(d.index, d.index_name ? esc(d.index_name) : "INDEX")}${d.index ? `<div class="wd-row" id="wd-open-idx"><span>Open ${esc(d.index_name)} in SECURITY ▸</span></div>` : ""}${mk(d.fx, `USD / ${d.currency_pair ? d.currency_pair.replace("USD", "").replace("=X", "") : ""}`)}` : '<div class="wd-sec">MARKETS</div><div class="dim">No index mapped for this country yet.</div>';
      const hh = (S.heat.heads[cc] || []).map((h) => ({ ...h }));
      const heatLine = S.heat.rows[cc] ? `<div class="wd-sec">NEWS HEAT · ${S.heat.rows[cc][0]} headlines / 24 h · <span class="dn">${S.heat.rows[cc][2]} severe</span></div>` : "";
      const news = (hh.length ? hh : d.news || []).slice(0, 7).map((n) => `<a class="wd-news" href="${esc(n.link)}" target="_blank" rel="noopener"><span class="${n.sev ? "dn" : ""}">${esc(n.title)}</span><em>${esc(n.source || "")} · ${ago(n.ts)}</em></a>`).join("") || '<div class="dim">No headlines.</div>';
      $("#wd-cbody").innerHTML = `<div class="wd-time" id="wd-time">${localTime(d.tz, c.lng)}</div>${w ? `<div class="wd-weather"><b>${num(w.temperature_2m, 0)}°C</b> ${WMO(w.weather_code)} · wind ${num(w.wind_speed_10m, 0)} km/h · humidity ${num(w.relative_humidity_2m, 0)}%</div>` : ""}${counts}${market}${radioHtml}${heatLine}<div class="wd-sec">HEADLINES</div>${news}`;
      bind();
      const oi = $("#wd-open-idx"); if (oi) oi.addEventListener("click", () => window.openSecurity && openSecurity(d.index_name));
    } catch (e) { const b = $("#wd-cbody"); if (b && S.sel === f) b.insertAdjacentHTML("afterbegin", `<div class="dim">Country data is unavailable right now.</div>`); }
  }
  function stopScanIfOther(f) { if (S.scan.on && S.scan.cc && S.scan.cc !== f.properties.cc) stopScan(); }

  // ------------------------------------------------------------------ news heat
  async function loadHeat(ts) {
    try {
      const r = await api(`/world/newsheat${ts ? `?ts=${ts}` : ""}`);
      S.heat.rows = r.rows || {}; if (!ts) { S.heat.heads = r.heads || {}; S.heat.ts = r.ts; }
      const n = $("#wd-heat-n"); if (n) n.textContent = Object.keys(S.heat.rows).length ? `${Object.keys(S.heat.rows).length} countries · ${ago(S.heat.ts)} old` : "first sample builds in ~1 min";
    } catch (e) { const n = $("#wd-heat-n"); if (n) n.textContent = "no sample yet"; }
  }
  function syncFlatHeat() {
    if (!S.map) return;
    if (S.flatHeat) { S.map.removeLayer(S.flatHeat); S.flatHeat = null; }
    if (!S.layers.heat) return;
    S.flatHeat = L.geoJSON({ type: "FeatureCollection", features: S.feats }, { interactive: false, style: (f) => ({ stroke: false, fillColor: S.heat.mode === "volume" ? "#ffbe3c" : "#ff4040", fillOpacity: 0.08 + 0.55 * heatVal(f.properties.cc) }) }).addTo(S.map);
    S.flatHeat.bringToBack();
  }

  // ------------------------------------------------------------------ time machine (planes + news heat from stored snapshots, quakes from the 7-day feed)
  const fmtT = (ts) => new Date(ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  async function tmInit() {
    try { const r = await api("/world/planes/history/index"); S.tm.times = r.ts; } catch (e) { S.tm.times = []; }
    try { S.quakesWeek = (await api("/world/quakes?range=week")).quakes; } catch (e) { /* optional */ }
    const rg = $("#wd-tm-r"), n = $("#wd-tm-n"); if (!rg) return;
    rg.max = String(S.tm.times.length); if (S.tm.live) rg.value = String(S.tm.times.length);
    const t = S.tm.times;
    n.textContent = t.length ? `${t.length} snapshots · ${fmtT(t[0])} → now · every 5 min` : "history starts after the first 5-minute snapshot";
  }
  async function tmSet(ts) {
    if (S.tm.busy) return; S.tm.busy = true;
    try {
      const [p, h] = await Promise.all([api(`/world/planes/history?ts=${ts}`), S.layers.heat ? api(`/world/newsheat?ts=${ts}`).catch(() => null) : null]);
      S.tm.live = false; S.tm.ts = p.ts; S.data.planes = p.rows; S.data.mil = []; S.planesTs = p.ts;
      S.data.quakes = S.quakesWeek.filter((q) => q[5] <= p.ts && q[5] >= p.ts - 86400);
      if (h) { S.heat.rows = h.rows; }
      $("#wd-tm-live").classList.remove("on"); $("#wd-tm-t").textContent = fmtT(p.ts);
      refreshPoints(); refreshPolys(); renderFlat();
    } catch (e) { showToast("No snapshot for that time"); } finally { S.tm.busy = false; }
  }
  async function tmLive() {
    tmStop(); S.tm.live = true; S.tm.ts = null;
    $("#wd-tm-live").classList.add("on"); $("#wd-tm-t").textContent = "LIVE"; const rg = $("#wd-tm-r"); rg.value = rg.max;
    await Promise.all([loadPlanes(), loadQuakes(), S.layers.heat ? loadHeat() : null]);
    refreshPoints(); refreshPolys(); renderFlat();
  }
  function tmStop() { S.tm.playing = false; clearInterval(S.tm.timer); const b = $("#wd-tm-play"); if (b) b.textContent = "▶ REPLAY"; }
  function tmPlay() {
    if (S.tm.playing) return tmStop();
    if (!S.tm.times.length) return showToast("No history yet: a snapshot is stored every 5 minutes");
    const rg = $("#wd-tm-r"); let i = S.tm.live || Number(rg.value) >= S.tm.times.length ? 0 : Number(rg.value);
    S.tm.playing = true; $("#wd-tm-play").textContent = "■ STOP";
    const tick = async () => {
      if (!S.tm.playing || !alive()) return tmStop();
      if (i >= S.tm.times.length) return tmLive();
      rg.value = String(i); await tmSet(S.tm.times[i]); i++;
    };
    tick(); S.tm.timer = setInterval(tick, 2400 / S.tm.speed);
  }

  // ------------------------------------------------------------------ bitcoin rain: real unconfirmed transactions falling over the map (no location exists for a transaction)
  const btcSpawn = (btc) => {
    const cv = $("#wd-rain"); if (!cv) return;
    const b = S.btc, w = cv.clientWidth, big = btc >= 50, usd = b.price ? btc * b.price : null;
    b.drops.push({ x: 20 + Math.random() * (w - 40), y: -30, v: (big ? 40 : 90) + Math.random() * 110, len: 10 + Math.min(80, Math.log10(btc * 1000 + 1) * 16), w: big ? 2.8 : 1.7, big, btc, usd, life: 0 });
    b.stamps.push([Date.now(), btc]); if (btc > b.big) b.big = btc;
    if (b.drops.length > 700) b.drops.splice(0, 100);
  };
  function btcLoop(t0) {
    const b = S.btc; if (!b.on) return;
    const cv = $("#wd-rain"), g = cv && cv.getContext("2d"); if (!cv) return;
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const dt = Math.min(0.05, (performance.now() - (b.last || performance.now())) / 1000); b.last = performance.now();
    g.font = "700 11px 'JetBrains Mono', Consolas, monospace";
    for (let i = b.drops.length - 1; i >= 0; i--) {
      const d = b.drops[i]; d.y += d.v * dt; d.life += dt;
      if (d.y - d.len > h) { b.drops.splice(i, 1); continue; }
      const grad = g.createLinearGradient(d.x, d.y - d.len, d.x, d.y);
      grad.addColorStop(0, "rgba(247,147,26,0)"); grad.addColorStop(1, d.big ? "rgba(255,230,120,0.95)" : "rgba(247,147,26,0.95)");
      g.strokeStyle = grad; g.lineWidth = d.w; g.beginPath(); g.moveTo(d.x, d.y - d.len); g.lineTo(d.x, d.y); g.stroke();
      if (d.big) { g.fillStyle = "rgba(255,230,120,0.95)"; g.beginPath(); g.arc(d.x, d.y, 3.5, 0, 6.3); g.fill(); g.fillText(`${d.btc.toLocaleString(undefined, { maximumFractionDigits: 1 })} BTC${d.usd ? " · $" + big_(d.usd) : ""}`, d.x + 8, d.y + 3); }
    }
    b.raf = requestAnimationFrame(btcLoop);
  }
  const big_ = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : n.toFixed(0));
  function btcHud() {
    const b = S.btc, el = $("#wd-btcst"); if (!el) return;
    const now = Date.now(); b.stamps = b.stamps.filter((s) => now - s[0] < 60000);
    el.innerHTML = `<b>₿ BTC RAIN</b> ${b.stamps.length} tx/min · ${big_(b.stamps.reduce((a, s) => a + s[1], 0))} BTC/min · biggest ${b.big ? b.big.toLocaleString(undefined, { maximumFractionDigits: 0 }) + " BTC" : "—"} <i>${b.ws && b.ws.readyState === 1 && now - b.lastMsg < 20000 ? "live stream" : "polling"} · not geographic: transactions have no location</i>`;
  }
  async function btcPrice() { try { const r = await api("/world/btc"); S.btc.price = r.price; return r; } catch (e) { return null; } }
  function btcPollOnce() {
    btcPrice().then((r) => { if (!r) return; const b = S.btc; const fresh = r.txs.filter((t) => !b.seen.has(t.h)); fresh.forEach((t) => b.seen.add(t.h)); if (b.seen.size > 5000) b.seen = new Set([...b.seen].slice(-1500)); fresh.slice(0, 60).forEach((t, i) => setTimeout(() => S.btc.on && btcSpawn(t.btc), i * 120)); });
  }
  function btcStart() {
    const b = S.btc; if (b.on) return; b.on = true; $("#wd-btcst").classList.remove("hidden"); btcPrice();
    try {
      b.ws = new WebSocket("wss://ws.blockchain.info/inv");
      b.ws.onopen = () => b.ws.send(JSON.stringify({ op: "unconfirmed_sub" }));
      b.ws.onmessage = (m) => { try { const d = JSON.parse(m.data); if (d.op === "utx") { b.lastMsg = Date.now(); btcSpawn((d.x.out || []).reduce((a, o) => a + (o.value || 0), 0) / 1e8); } } catch (e) { /* ignore */ } };
    } catch (e) { /* fall back to polling */ }
    b.poll = setInterval(() => { if (!b.on) return; if (!b.ws || b.ws.readyState !== 1 || Date.now() - b.lastMsg > 20000) btcPollOnce(); btcHud(); }, 8000);
    b.hud = setInterval(btcHud, 2000); btcHud(); b.last = performance.now(); b.raf = requestAnimationFrame(btcLoop);
  }
  function btcStop() {
    const b = S.btc; b.on = false; cancelAnimationFrame(b.raf); clearInterval(b.poll); clearInterval(b.hud); if (b.ws) { try { b.ws.close(); } catch (e) { /* closed */ } b.ws = null; }
    b.drops = []; const cv = $("#wd-rain"); if (cv) cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); $("#wd-btcst").classList.add("hidden");
  }

  // ------------------------------------------------------------------ page
  function layerChips() {
    const box = $("#wd-layers");
    box.innerHTML = LAYERS.map(([k, l, c]) => `<button class="wd-chip ${S.layers[k] ? "on" : ""}" data-l="${k}" style="--c:${c}"><i></i>${l}</button>`).join("");
    box.querySelectorAll(".wd-chip").forEach((b) => b.addEventListener("click", async () => {
      const k = b.dataset.l; S.layers[k] = !S.layers[k]; b.classList.toggle("on", S.layers[k]); lset(PREF, S.layers);
      if (k === "rain") return syncRadar();
      if (k === "heat") { $("#wd-heat").classList.toggle("hidden", !S.layers.heat); if (S.layers.heat) await loadHeat(); refreshPolys(); syncFlatHeat(); return; }
      if (k === "btc") return S.layers.btc ? btcStart() : btcStop();
      if (S.layers[k] && k === "events" && !S.data.events.length) await loadEvents();
      refreshPoints(); renderFlat();
    }));
  }
  async function build() {
    const el = $("#view-world");
    el.innerHTML = `<div class="wd-shell"><div class="wd-stage" id="wd-stage"><div id="wd-globe" class="wd-layer"></div><div id="wd-flat" class="wd-layer wd-hidden"></div>
      <div class="wd-hud wd-tl"><div class="wd-title">WORLD <span>live</span></div><div id="wd-layers"></div><div class="wd-stats" id="wd-stats">loading…</div>
        <div class="wd-heat ${S.layers.heat ? "" : "hidden"}" id="wd-heat"><span>NEWS HEAT</span><button data-m="stress" class="${S.heat.mode === "stress" ? "on" : ""}">STRESS</button><button data-m="volume" class="${S.heat.mode === "volume" ? "on" : ""}">VOLUME</button><i class="wd-grad"></i><em id="wd-heat-n"></em></div>
        <div class="wd-btcst hidden" id="wd-btcst"></div></div>
      <canvas id="wd-rain" class="wd-rain"></canvas>
      <button class="wd-back hidden" id="wd-back">◂ GLOBE</button>
      <div class="wd-hud wd-tr hidden" id="wd-country"></div><div class="wd-hud wd-bl hidden" id="wd-info"></div>
      <div class="wd-tm" id="wd-tm"><button class="wd-btn on" id="wd-tm-live" title="Back to live data">● LIVE</button><button class="wd-mini" id="wd-tm-play" title="Replay the last hours">▶ REPLAY</button><button class="wd-mini" id="wd-tm-sp" title="Replay speed">×1</button>
        <input id="wd-tm-r" type="range" min="0" max="0" step="1" value="0"/><span class="wd-tm-t" id="wd-tm-t">LIVE</span><span class="wd-tm-n" id="wd-tm-n"></span></div>
      <div class="wd-bar"><button class="wd-btn hidden" id="wd-stop" title="Stop">■</button><div class="wd-now" id="wd-bar-now"><span class="dim">Click a yellow dot or a country to listen.</span></div><span class="wd-st" id="wd-bar-st"></span>
        <button class="wd-btn" id="wd-scan" title="Hop across the world's radio stations">◉ SCAN WORLD</button><input id="wd-vol" type="range" min="0" max="1" step="0.05" value="${lget(PREF + "_vol", 0.8)}" title="Volume"/></div>
      <div class="wd-attr">Radio: radio-browser.info · Planes: OpenSky Network, adsb.lol · Quakes: USGS · Events: NASA EONET · Cameras: TfL, LTA (open data)</div></div></div>`;
    layerChips();
    $("#wd-back").addEventListener("click", () => { const c = S.map.getCenter(); exitFlat(c.lat, c.lng, altFromZoom(S.map.getZoom())); });
    $("#wd-stop").addEventListener("click", () => { stopScan(); stopRadio(); });
    $("#wd-scan").addEventListener("click", () => (S.scan.on ? (stopScan(), stopRadio()) : startScan(null)));
    $("#wd-vol").addEventListener("input", (e) => { audio().volume = Number(e.target.value); lset(PREF + "_vol", Number(e.target.value)); });
    try { await loadStatic(); } catch (e) { $("#wd-stats").innerHTML = NX.err(e); return; }
    initGlobe(); initFlat();
    new ResizeObserver(() => { const st = $("#wd-stage"); if (!st) return; if (S.globe && st.clientWidth) S.globe.width(st.clientWidth).height(st.clientHeight); if (S.map) S.map.invalidateSize(); }).observe($("#wd-stage"));
    S.built = true; S.tabActive = true;
    $("#wd-heat").querySelectorAll("button").forEach((bt) => bt.addEventListener("click", () => { S.heat.mode = bt.dataset.m; $("#wd-heat").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === bt)); refreshPolys(); syncFlatHeat(); }));
    $("#wd-tm-live").addEventListener("click", tmLive); $("#wd-tm-play").addEventListener("click", tmPlay);
    $("#wd-tm-sp").addEventListener("click", (e) => { S.tm.speed = S.tm.speed === 1 ? 4 : S.tm.speed === 4 ? 12 : 1; e.currentTarget.textContent = `×${S.tm.speed}`; if (S.tm.playing) { tmStop(); tmPlay(); } });
    $("#wd-tm-r").addEventListener("input", (e) => { tmStop(); const i = Number(e.target.value); if (i >= S.tm.times.length) tmLive(); else tmSet(S.tm.times[i]); });
    tmInit(); if (S.layers.heat) { loadHeat().then(() => { refreshPolys(); syncFlatHeat(); }); } if (S.layers.btc) btcStart();
    setInterval(() => { if (alive() && S.tm.live) tmInit(); }, 300000); setInterval(() => { if (alive() && S.layers.heat && S.tm.live) loadHeat().then(refreshPolys); }, 600000);
    if (W.pendingFocus) { const c0 = W.pendingFocus; W.pendingFocus = null; setTimeout(() => W.focus(c0), 600); }
    if (W.pending) { const n = W.pending; W.pending = null; setTimeout(() => W.preset(n), 300); }
    Promise.all([loadPlanes(), loadCams(), loadQuakes(), S.layers.events ? loadEvents() : null]).then(() => { refreshPoints(); renderFlat(); });
    setInterval(async () => { if (!alive() || !S.tabActive) return; await loadPlanes(); refreshPoints(); renderFlat(); }, 150000);
    setInterval(async () => { if (!alive() || !S.tabActive) return; await Promise.all([loadQuakes(), loadCams()]); refreshPoints(); renderFlat(); }, 180000);
    setInterval(() => { const t = $("#wd-time"); if (t && S.sel) { const c = centre(S.sel); t.textContent = localTime(S.lastTz, c.lng); } stats(); }, 30000);
  }
  W._S = S;
  W.focus = (cc) => { if (!S.built) { W.pendingFocus = cc; return; } const f = S.feats.find((x) => x.properties.cc === cc); if (f) { if (S.mode === "flat") exitFlat(S.map.getCenter().lat, S.map.getCenter().lng, 1); selectCountry(f, true); } };
  W.preset = (name) => {
    if (!S.built) { W.pending = name; return; }
    const only = { radio: ["radio"], planes: ["planes", "mil"], cams: ["cams"], quakes: ["quakes", "events"], geo: ["events", "quakes"] }[name] || null;
    if (only) { Object.keys(S.layers).forEach((k) => (S.layers[k] = only.includes(k))); lset(PREF, S.layers); layerChips(); refreshPoints(); renderFlat(); }
    if (name === "radio") startScan(null);
  };
  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    const on = tab === "world";
    if (on && !W.started) { W.started = true; build().catch((e) => { const v = $("#view-world"); if (v) v.innerHTML = NX.err(e); }); }
    S.tabActive = on;
    if (S.globe) { if (on && S.mode === "globe") { S.globe.resumeAnimation(); const st = $("#wd-stage"); S.globe.width(st.clientWidth).height(st.clientHeight); } else if (!on) S.globe.pauseAnimation(); }
    if (on && S.map && S.mode === "flat") setTimeout(() => S.map.invalidateSize(), 50);
  };
})();
