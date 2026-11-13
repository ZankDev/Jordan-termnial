/* WORLD › ALT DATA — "Spot it first. Trade it first."  Satellite views of oil tanks, oil terminals, container ports, mines and crop
   regions, each linked to the tickers it moves; SHIP: live AIS around the chokepoints with a "ships stacking up" baseline; and an
   orange ALERTS popup listing every anomaly (asset, signal, magnitude, time).
   Imagery (keyless, loaded by the browser): Esri World Imagery (latest), Esri Wayback (dated archive releases), NASA GIBS HLS
   Sentinel-2 + Landsat 30 m (near-daily; blank where there was no clear pass). Data: /api/altdata/*. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const GIBS = (layer, date) => `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${layer}/default/${date}/GoogleMapsCompatible_Level12/{z}/{y}/{x}.png`;
  const S = { started: false, tab: "tanks", site: null, cfg: null, map: null, img: [], mode: "latest", wb: [], wbi: 0, day: 8, quotes: {}, alerts: null, shown: false,
    ship: { g: null, timer: 0 }, timer: 0 };
  const TAB_ORDER = ["tanks", "terminals", "ports", "mines", "crops", "ship"];
  const dayStr = (back) => new Date(Date.now() - back * 86400000).toISOString().slice(0, 10);
  const fmtT = (t) => (t ? new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");

  function needLeaflet() {
    if (!document.getElementById("leaflet-css")) { const l = document.createElement("link"); l.id = "leaflet-css"; l.rel = "stylesheet"; l.href = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"; document.head.appendChild(l); }
    if (window.L) return Promise.resolve();
    return new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"; s.onload = res; s.onerror = () => rej(new Error("could not load Leaflet")); document.body.appendChild(s); });
  }

  function shell(v) {
    v.innerHTML = `<div class="ad-wrap" id="ad-wrap">
      <div class="ad-bar"><span class="sg-t"><b>ALT DATA</b><i>Spot it first. Trade it first.</i></span>
        <div class="bb-chips" id="ad-tabs"></div><span class="sp"></span><button class="ad-alb" id="ad-alb">ALERTS <b id="ad-aln">…</b></button></div>
      <div class="ad-cap" id="ad-cap"></div>
      <div class="ad-body">
        <aside class="ad-list" id="ad-list"></aside>
        <div class="ad-mapbox"><div id="ad-map"></div><div class="ad-banner" id="ad-banner" hidden></div>
          <div class="ad-img" id="ad-img"><div class="bb-chips" id="ad-mode"><button class="bb-chip" data-m="latest">LATEST</button><button class="bb-chip" data-m="archive">ARCHIVE</button><button class="bb-chip" data-m="recent">RECENT 30 m</button></div>
            <input type="range" id="ad-slide" min="0" max="0" value="0"/><span id="ad-date">—</span></div></div>
        <aside class="ad-side" id="ad-side"></aside>
      </div></div>
      <div class="ad-modal" id="ad-modal" hidden><div class="ad-mbox"><div class="ad-mh"><b>⚠ ALERTS</b><span>Spot it first. Trade it first.</span><em id="ad-mmeta"></em><button id="ad-mx">✕</button></div>
        <div class="ad-mb" id="ad-mb"></div><div class="ad-mf"><span id="ad-mchk"></span><button id="ad-mclose">CLOSE</button></div></div></div>`;
    $("#ad-tabs").innerHTML = TAB_ORDER.map((t) => `<button class="bb-chip" data-t="${t}">${esc(S.cfg.tabs[t].title)}</button>`).join("");
    $("#ad-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (b) tab(b.dataset.t); });
    $("#ad-list").addEventListener("click", (e) => { const r = e.target.closest("[data-s]"); if (r) site(r.dataset.s); const c = e.target.closest("[data-fly]"); if (c) { const [a, b, z] = c.dataset.fly.split(",").map(Number); S.map.flyTo([a, b], z, { duration: 1 }); } });
    $("#ad-mode").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (b) { S.mode = b.dataset.m; imagery(); } });
    $("#ad-slide").addEventListener("input", (e) => { if (S.mode === "archive") S.wbi = Number(e.target.value); else S.day = Number(e.target.max) - Number(e.target.value) + 1; imagery(true); });
    $("#ad-side").addEventListener("click", (e) => { const w = e.target.closest("[data-why]"); if (w && window.WHY) window.WHY.open(w.dataset.why, Date.now() / 1000 - 5 * 86400, Date.now() / 1000); });
    $("#ad-alb").addEventListener("click", () => modal(true));
    ["#ad-mx", "#ad-mclose"].forEach((s) => $(s).addEventListener("click", () => modal(false)));
    $("#ad-mb").addEventListener("click", (e) => {
      const r = e.target.closest("[data-a]"); if (!r) return; const a = S.alerts.alerts[Number(r.dataset.a)];
      if (a.lat != null) { modal(false); tab("ship"); setTimeout(() => S.map.flyTo([a.lat, a.lon], 6, { duration: 1 }), 300); }
      else if (window.WHY) window.WHY.open(a.asset.split(",")[0].trim(), Date.now() / 1000 - 2 * 86400, Date.now() / 1000);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") modal(false); });
  }

  function buildMap() {
    S.map = L.map("ad-map", { zoomControl: true, attributionControl: true, minZoom: 2, maxZoom: 19, worldCopyJump: true }).setView([30, 20], 3);
    S.ship.g = L.layerGroup().addTo(S.map);
    S.map.createPane("labels"); S.map.getPane("labels").style.zIndex = 380; S.map.getPane("labels").style.pointerEvents = "none";
    S.labels = L.tileLayer(`${ESRI}Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, { pane: "labels", maxZoom: 19, opacity: 0.7 });
  }

  // ------------------------------------------------------------------ imagery
  function clearImg() { S.img.forEach((l) => S.map.removeLayer(l)); S.img = []; }
  async function imagery(quiet) {
    document.querySelectorAll("#ad-mode [data-m]").forEach((b) => b.classList.toggle("on", b.dataset.m === S.mode));
    clearImg();
    const slide = $("#ad-slide"), lab = $("#ad-date"), attr = "Imagery © Esri, Maxar, Earthstar Geographics";
    if (S.tab === "ship") return;
    if (S.mode === "latest") {
      S.img.push(L.tileLayer(`${ESRI}World_Imagery/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, maxNativeZoom: 19, attribution: attr }).addTo(S.map));
      slide.hidden = true; lab.textContent = "Esri World Imagery · most recent mosaic";
    } else if (S.mode === "archive") {
      if (!S.wb.length) { try { S.wb = (await api("/altdata/wayback")).releases; S.wbi = S.wb.length - 1; } catch (e) { lab.textContent = `archive unavailable: ${e.message}`; return; } }
      slide.hidden = false; slide.min = 0; slide.max = String(S.wb.length - 1); if (!quiet) slide.value = String(S.wbi);
      const r = S.wb[S.wbi];
      S.img.push(L.tileLayer(r.url, { maxZoom: 19, maxNativeZoom: 19, attribution: `${attr} · Wayback ${r.date}` }).addTo(S.map));
      lab.innerHTML = `ARCHIVE <b>${esc(r.date)}</b> · release ${S.wbi + 1}/${S.wb.length}`;
    } else {
      slide.hidden = false; slide.min = 1; slide.max = "60"; if (!quiet) slide.value = String(61 - S.day);
      const d = dayStr(S.day);
      S.img.push(L.tileLayer(`${ESRI}World_Imagery/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, opacity: 0.35, attribution: attr }).addTo(S.map));
      ["HLS_L30_Nadir_BRDF_Adjusted_Reflectance", "HLS_S30_Nadir_BRDF_Adjusted_Reflectance"].forEach((ly) =>
        S.img.push(L.tileLayer(GIBS(ly, d), { maxZoom: 19, maxNativeZoom: 12, attribution: "NASA GIBS · HLS (Sentinel-2 / Landsat)", errorTileUrl: "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==" }).addTo(S.map)));
      lab.innerHTML = `RECENT <b>${d}</b> · 30 m · blank = no clear pass that day (faded = latest mosaic underneath)`;
    }
    if (!S.map.hasLayer(S.labels)) S.labels.addTo(S.map);
  }

  // ------------------------------------------------------------------ tabs + sites
  function tab(t) {
    S.tab = t;
    document.querySelectorAll("#ad-tabs [data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === t));
    $("#ad-cap").textContent = S.cfg.tabs[t].look;
    $("#ad-img").hidden = t === "ship";
    clearInterval(S.ship.timer); S.ship.g.clearLayers(); $("#ad-banner").hidden = true;
    if (t === "ship") { ship(); S.ship.timer = setInterval(() => { if (document.querySelector("#view-altdata.active")) ship(); }, 30000); return; }
    const list = S.cfg.sites.filter((s) => s.tab === t);
    $("#ad-list").innerHTML = list.map((s) => `<div class="ad-si" data-s="${s.id}"><b>${esc(s.name)}</b><span>${esc(s.country)} · ${s.tickers.map(esc).join(" · ")}</span></div>`).join("");
    site(list[0].id);
  }

  function site(id) {
    const s = S.cfg.sites.find((x) => x.id === id); if (!s) return;
    S.site = s;
    document.querySelectorAll("#ad-list .ad-si").forEach((el) => el.classList.toggle("on", el.dataset.s === id));
    S.map.setView([s.lat, s.lon], s.zoom, { animate: false });
    imagery();
    $("#ad-side").innerHTML = `<div class="bb-ph"><span class="bb-n">${esc(S.cfg.tabs[s.tab].title)}</span>${esc(s.name)}</div>
      <div class="sg-d"><p>${esc(s.note)}</p><div class="sg-kv"><span>LOCATION</span><b>${s.lat.toFixed(3)}, ${s.lon.toFixed(3)} <i class="ad-approx">approx.</i></b></div>
        <div class="sg-h">WHAT TO LOOK FOR</div><div class="sg-why">${esc(S.cfg.tabs[s.tab].look)}</div>
        <div class="sg-h">LINKED TICKERS</div><table class="em-at">${s.tickers.map((t) => `<tr><td class="sym">${esc(t)}</td><td class="r ad-q" data-q="${esc(t)}">${qcell(t)}</td><td><button class="sg-whyb" data-why="${esc(t)}">WHY?</button></td></tr>`).join("")}</table>
        <div class="em-note">Imagery: LATEST = Esri mosaic (months old) · ARCHIVE = dated Esri Wayback releases · RECENT = NASA HLS 30 m passes. Coordinates are approximate.</div></div>`;
    quotes(s.tickers);
  }
  const qcell = (t) => { const q = S.quotes[t]; if (!q) return "—"; return `${q.price.toLocaleString(undefined, { maximumFractionDigits: 2 })} <b class="${q.pct >= 0 ? "up" : "dn"}">${q.pct >= 0 ? "+" : ""}${q.pct.toFixed(2)}%</b>`; };
  async function quotes(syms) {
    try { const r = await api(`/world/live/quotes?s=${encodeURIComponent(syms.join(","))}`); Object.entries(r.quotes).forEach(([k, v]) => { if (v) S.quotes[k] = v; }); } catch (e) { return; }
    document.querySelectorAll("#ad-side .ad-q[data-q]").forEach((el) => { el.innerHTML = qcell(el.dataset.q); });
  }

  // ------------------------------------------------------------------ SHIP: AIS + stacking
  async function ship() {
    if (!S.dark || !S.map.hasLayer(S.dark)) { clearImg(); if (S.map.hasLayer(S.labels)) S.map.removeLayer(S.labels);
      S.dark = S.dark || L.tileLayer(`${ESRI}Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, attribution: "Tiles © Esri · AIS aisstream.io" });
      S.img.push(S.dark.addTo(S.map)); S.map.setView([22, 50], 3); }
    let d; try { d = await api("/altdata/ships"); } catch (e) { $("#ad-list").innerHTML = NX.err(e); return; }
    let risk = {}; try { (await api("/world/gx/chokepoints")).rows.forEach((r) => (risk[r.id] = r)); } catch (e) { /* risk is a bonus */ }
    S.ship.g.clearLayers();
    const C = { tanker: "#ff8a28", cargo: "#2bd5c0" };
    d.ships.forEach((s) => L.circleMarker([s[2], s[3]], { radius: s[6] === "tanker" ? 3 : 2, stroke: false, fillColor: C[s[6]] || "#8b8b8b", fillOpacity: 0.9 }).bindTooltip(`${esc(s[1] || s[0])} · ${esc(s[6])}${s[4] != null ? ` · ${s[4]} kn` : ""}`).addTo(S.ship.g));
    const stack = d.chokepoints.filter((c) => c.stacking);
    d.chokepoints.forEach((c) => {
      const r = risk[c.id] || {}, col = c.stacking ? "#ff3b3b" : r.level === "HIGH" ? "#ff3b3b" : r.level === "ELEVATED" ? "#ffa028" : "#2be07e";
      L.circle([c.lat, c.lon], { radius: 120000, color: col, weight: c.stacking ? 2.5 : 1, dashArray: c.stacking ? null : "4 4", fillOpacity: c.stacking ? 0.18 : 0.05 })
        .bindTooltip(`${esc(c.name)}${c.stopped != null ? ` · ${c.stopped} stopped (normal ${c.baseline ?? "…"})` : ""}${r.risk != null ? ` · risk ${r.risk}` : ""}`).addTo(S.ship.g);
    });
    $("#ad-list").innerHTML = `<div class="ad-lh">CHOKEPOINTS · ${d.configured ? (d.connected ? `AIS live · ${d.count.toLocaleString()} ships` : "AIS connecting") : "AIS off"}</div>` + d.chokepoints.map((c) => {
      const r = risk[c.id] || {};
      return `<div class="ad-si${c.stacking ? " hot" : ""}" data-fly="${c.lat},${c.lon},7"><b>${esc(c.name)}</b><span>${c.stopped != null ? `${c.stopped} stopped · normal ${c.baseline ?? "learning…"}${c.z != null ? ` · z ${c.z}` : ""}` : "no AIS"}${r.level ? ` · ${r.level}` : ""}</span></div>`;
    }).join("");
    const ban = $("#ad-banner");
    if (stack.length) { ban.hidden = false; ban.className = "ad-banner hot"; ban.innerHTML = `⚠ SHIPS STACKING UP: ${stack.map((c) => `<b>${esc(c.name)}</b> ${c.stopped} stopped vs ${c.baseline} normal`).join(" · ")}`; }
    else if (!d.configured) { ban.hidden = false; ban.className = "ad-banner"; ban.innerHTML = `SHIP needs a free <b>aisstream.io</b> key: set <b>AISSTREAM_API_KEY</b> and restart. Chokepoint rings show the news/hazard risk screen meanwhile.`; }
    else ban.hidden = true;
    $("#ad-side").innerHTML = `<div class="bb-ph"><span class="bb-n">SHIP</span>EVERY TANKER, LIVE</div><div class="sg-d"><p>Vessels stopped inside each chokepoint box are compared with their own rolling 24 h baseline (sampled every ${Math.round(d.sample_every_s / 60)} min). Above max(5, mean + 2 sd) = ships stacking up — usually before the headline.</p>
      ${d.chokepoints.filter((c) => risk[c.id] && risk[c.id].level === "HIGH").map((c) => `<div class="sg-why">⚠ ${esc(c.name)}: risk ${risk[c.id].risk}/100 — ${esc((risk[c.id].top[0] || {}).title || "")}</div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ alerts popup
  async function loadAlerts() {
    try { S.alerts = await api("/altdata/alerts"); } catch (e) { $("#ad-aln").textContent = "!"; return; }
    const n = S.alerts.alerts.length; $("#ad-aln").textContent = String(n); $("#ad-alb").classList.toggle("hot", S.alerts.alerts.some((a) => a.sev >= 3));
    renderAlerts();
    // auto-open once, but never over the page menu (phones) or after you already left ALT DATA: it would block navigation
    const here = $("#view-altdata")?.classList.contains("active") && !document.body.classList.contains("nav-open");
    if (!S.shown && n && here) { S.shown = true; modal(true); }
  }
  function renderAlerts() {
    const a = S.alerts; if (!a) return;
    $("#ad-mmeta").textContent = `${a.alerts.length} anomalies · ${fmtT(a.ts)}`;
    $("#ad-mb").innerHTML = a.alerts.length ? `<table class="ad-at"><tr><th>ASSET</th><th>SIGNAL</th><th>MAGNITUDE</th><th>TIME</th><th>SOURCE</th></tr>${a.alerts.map((x, i) => `<tr data-a="${i}" class="s${x.sev}"><td>${esc(x.asset)}</td><td>${esc(x.signal)}</td><td>${esc(x.magnitude)}</td><td>${fmtT(x.ts)}</td><td>${esc(x.source)}</td></tr>`).join("")}</table>`
      : `<div class="why-none">No anomalies right now. Quiet is information too.</div>`;
    const c = a.checked; $("#ad-mchk").textContent = `checked: prices ${c.prices}/${c.prices_total} · events ${c.events ? "ok" : "—"} · AIS ${c.ships ? "ok" : "—"} · risk ${c.risk ? "ok" : "warming up"} · click a row: map / WHY?`;
  }
  function modal(on) { const m = $("#ad-modal"); if (m) m.hidden = !on; }

  async function start() {
    const v = $("#view-altdata"); v.innerHTML = NX.load("LOADING ALT DATA");
    S.cfg = await api("/altdata/sites");
    await needLeaflet();
    shell(v); buildMap(); tab(S.tab);
    loadAlerts(); S.timer = setInterval(() => { if (document.querySelector("#view-altdata.active")) loadAlerts(); }, 90000);
  }
  const prev = window.onExtraTab;
  window.onExtraTab = (t) => {
    if (prev) prev(t);
    if (t !== "altdata") return;
    if (!S.started) { S.started = true; start().catch((e) => { $("#view-altdata").innerHTML = NX.err(e); S.started = false; }); }
    else if (S.map) setTimeout(() => S.map.invalidateSize(), 50);
  };
  window.NX = window.NX || {}; NX.altdata = { _S: S, tab, site };
})();
