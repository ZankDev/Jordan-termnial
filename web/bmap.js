/* BMAP — global logistics & physical assets: live ships (AIS) around chokepoints and ports, power plants >= 1 GW,
   LNG / crude terminals, refineries, top container ports, chokepoint risk (news + nearby disasters), GDACS / NHC hazards,
   rain radar, and the commodity prices those risks move. Leaflet (loaded by index.html). Depends on api(), showToast(), UIX. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const pct = (n) => (!isN(n) ? "—" : `${n > 0 ? "+" : ""}${Number(n).toFixed(2)}%`);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const FUEL = { Coal: "#9a9a9a", Gas: "#ffa028", Nuclear: "#c792ff", Hydro: "#5aa9ff", Oil: "#ff4a4a", Solar: "#ffe14a", Wind: "#7fe0c8" };
  const INFRA = { lng: ["#36d6e7", "LNG terminal"], oil: ["#ff4a4a", "Crude terminal"], refinery: ["#ff8a3d", "Refinery"], port: ["#f1f1f1", "Container port"] };
  const SHIP = { tanker: "#ff6b3d", cargo: "#4aa3ff", military: "#ff4a4a", passenger: "#b4ff6b", other: "#9a9a9a", unknown: "#6d6d6d" };
  const HAZ = { TC: "🌀 Cyclone", EQ: "⚠ Earthquake", FL: "🌊 Flood", VO: "🌋 Volcano", DR: "☀ Drought", WF: "🔥 Wildfire" };
  const LVL = { Red: "#ff4a4a", Orange: "#ffa028", Green: "#2be07e" };
  const B = { built: false, map: null, groups: {}, layers: { ships: true, power: true, infra: true, choke: true, hazards: true, rain: false },
    st: null, risk: null, haz: null, ships: null, timers: [], radar: null };
  try { Object.assign(B.layers, JSON.parse(localStorage.getItem("bmap_layers") || "{}")); } catch (e) { /* private mode */ }
  const save = () => { try { localStorage.setItem("bmap_layers", JSON.stringify(B.layers)); } catch (e) { /* ignore */ } };

  function initMap() {
    const map = L.map($("#bm-map"), { preferCanvas: true, worldCopyJump: true, minZoom: 2, zoomControl: true }).setView([24, 30], 3);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, className: "wd-tiles", attribution: "© OpenStreetMap contributors" }).addTo(map);
    ["power", "infra", "choke", "hazards", "ships"].forEach((k) => { B.groups[k] = L.layerGroup(); if (B.layers[k]) B.groups[k].addTo(map); });
    B.map = map;
    B.renderer = L.canvas({ padding: 0.3 });
  }

  const pop = (html) => `<div class="bm-pop">${html}</div>`;

  function drawStatic() {
    const s = B.st; if (!s) return;
    const g = B.groups.power; g.clearLayers();
    s.power.forEach(([name, country, mw, lat, lon, fuel]) => {
      L.circleMarker([lat, lon], { renderer: B.renderer, radius: Math.max(2.5, Math.sqrt(mw) / 14), color: FUEL[fuel] || "#888", weight: 0.6, fillOpacity: 0.55 })
        .bindPopup(pop(`<b>${esc(name)}</b><span>${esc(fuel)} power plant · ${esc(country)}</span><span>${mw.toLocaleString()} MW</span>`)).addTo(g);
    });
    const gi = B.groups.infra; gi.clearLayers();
    s.infra.forEach((x) => {
      const [c, label] = INFRA[x.kind] || ["#fff", x.kind];
      L.marker([x.lat, x.lon], { icon: L.divIcon({ className: "bm-ico", html: `<i class="bm-in ${x.kind}" style="--c:${c}"></i>`, iconSize: [14, 14] }) })
        .bindPopup(pop(`<b>${esc(x.name)}</b><span>${label}</span>${x.note ? `<span>${esc(x.note)}</span>` : ""}`)).addTo(gi);
    });
  }

  function drawRisk() {
    const g = B.groups.choke; g.clearLayers();
    (B.risk?.rows || B.st?.chokepoints || []).forEach((c) => {
      const r = c.risk ?? 0, col = r >= 60 ? "#ff4a4a" : r >= 30 ? "#ffa028" : "#2be07e";
      const sh = c.ships || {};
      L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "bm-ico", html: `<div class="bm-choke ${r >= 60 ? "hot" : ""}" style="--c:${col}"><b>${isN(c.risk) ? r : ""}</b></div>`, iconSize: [34, 34] }) })
        .bindPopup(pop(`<b>${esc(c.name)}</b><span>Risk <b style="color:${col}">${isN(c.risk) ? r + "/100 · " + c.level : "—"}</b></span><span>Flows: ${esc((c.commodities || []).join(", "))}</span>
          ${c.headlines_48h != null ? `<span>${c.headlines_48h} headlines in 48h · ${c.negative_48h} conflict/disruption</span>` : ""}
          ${sh.tanker != null ? `<span>Ships now: ${sh.tanker} tankers · ${sh.cargo} cargo · ${sh.stopped} stopped</span>` : ""}
          ${(c.hazards || []).length ? `<span class="dn">Nearby: ${esc(c.hazards.join("; "))}</span>` : ""}
          ${(c.top || []).map((t) => `<a href="${esc(t.link)}" target="_blank" rel="noopener noreferrer">${esc(t.title)}</a>`).join("")}`), { maxWidth: 340 }).addTo(g);
    });
  }

  function drawHazards() {
    const g = B.groups.hazards; g.clearLayers();
    (B.haz?.items || []).forEach((h) => {
      if (!isN(h.lat) || !isN(h.lon)) return;
      const col = LVL[h.level] || "#888";
      L.marker([h.lat, h.lon], { icon: L.divIcon({ className: "bm-ico", html: `<div class="bm-haz ${h.level}" style="--c:${col}">${(HAZ[h.type] || "•").split(" ")[0]}</div>`, iconSize: [22, 22] }) })
        .bindPopup(pop(`<b>${esc(h.name)}</b><span>${esc(HAZ[h.type] || h.type)} · alert <b style="color:${col}">${esc(h.level)}</b> · ${esc(h.src)}</span>${h.country ? `<span>${esc(h.country)}</span>` : ""}${h.movement ? `<span>moving ${esc(h.movement)}</span>` : ""}${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">report ▸</a>` : ""}`)).addTo(g);
    });
  }

  function drawShips() {
    const g = B.groups.ships; g.clearLayers();
    const s = B.ships; if (!s) return;
    s.ships.forEach(([mmsi, name, lat, lon, sog, cog, cat, dest]) => {
      const col = SHIP[cat] || "#888", moving = (sog || 0) >= 0.5;
      const m = moving && isN(cog)
        ? L.marker([lat, lon], { icon: L.divIcon({ className: "bm-ico", html: `<i class="bm-ship" style="--c:${col};transform:rotate(${cog}deg)"></i>`, iconSize: [10, 10] }) })
        : L.circleMarker([lat, lon], { renderer: B.renderer, radius: 2.4, color: col, weight: 0, fillOpacity: 0.9 });
      m.bindPopup(pop(`<b>${esc(name || "MMSI " + mmsi)}</b><span>${esc(cat)} · ${isN(sog) ? sog.toFixed(1) + " kn" : "—"}${isN(cog) ? " · course " + Math.round(cog) + "°" : ""}</span>${dest ? `<span>→ ${esc(dest)}</span>` : ""}<span class="dim">MMSI ${mmsi}</span>`)).addTo(g);
    });
  }

  // ------------------------------------------------------------------ side panel
  function side() {
    const r = B.risk?.rows || [];
    $("#bm-risk").innerHTML = r.length ? r.map((c) => { const col = c.risk >= 60 ? "dn" : c.risk >= 30 ? "warn" : "up";
      return `<div class="bm-rr click" data-fly="${c.lat},${c.lon},6" data-uix-tip="${esc(c.name)}|${c.headlines_48h} headlines 48h · ${c.negative_48h} conflict/disruption|${(c.hazards || []).length ? "nearby: " + c.hazards.join("; ") : "no major hazard nearby"}">
        <span>${esc(c.name)}</span><div class="bm-rbar"><i class="${col}" style="width:${c.risk}%"></i></div><b class="${col}">${c.risk}</b><small>${esc((c.commodities || []).join(" · "))}${c.ships && (c.ships.tanker + c.ships.cargo) ? ` · ${c.ships.tanker}🛢 ${c.ships.cargo}📦` : ""}</small></div>`; }).join("")
      : `<div class="dim acd-load">Scoring chokepoints…</div>`;
    const hz = (B.haz?.items || []).filter((h) => h.level !== "Green").sort((a, b) => (a.level === "Red" ? -1 : 1) - (b.level === "Red" ? -1 : 1));
    $("#bm-haz").innerHTML = hz.length ? hz.slice(0, 18).map((h) => `<div class="bm-hr click" data-fly="${h.lat},${h.lon},5"><i style="background:${LVL[h.level]}"></i><span>${esc(HAZ[h.type] || h.type)}</span><b>${esc(h.name)}</b></div>`).join("") : `<div class="dim">No Orange/Red alerts right now.</div>`;
    const s = B.ships;
    $("#bm-ais").innerHTML = !s ? "" : s.configured
      ? `<div class="bm-aisk"><span class="${s.connected ? "up" : "dn"}">● ${s.connected ? "LIVE" : "CONNECTING"}</span><b>${s.count.toLocaleString()}</b> ships · ${s.ships.filter((x) => x[6] === "tanker").length} tankers · ${s.ships.filter((x) => x[6] === "cargo").length} cargo${s.error ? `<div class="dn">${esc(s.error)}</div>` : ""}</div>`
      : `<div class="bm-aisk"><b>Live ships need a free aisstream.io key.</b> <span class="dim">Sign up at <a href="https://aisstream.io" target="_blank" rel="noopener noreferrer">aisstream.io</a> → API Keys, then paste it here:</span>
         <div class="bm-keyrow"><input id="bm-key" type="password" placeholder="aisstream API key" autocomplete="off"/><button class="bb-chip" id="bm-keygo">CONNECT</button></div></div>`;
    $("#bm-keygo")?.addEventListener("click", async () => {
      try { await api("/bmap/ais-key", { method: "POST", body: JSON.stringify({ key: $("#bm-key").value }) }); showToast("AIS connected — ships appear within a minute"); setTimeout(loadShips, 8000); } catch (e) { showToast(e.message); }
    });
  }
  async function markets() {
    try {
      const r = await api("/bmap/markets");
      $("#bm-mkt").innerHTML = r.rows.map((m) => `<div class="bm-m" data-uix-tip="${esc(m.name)}|${m.price}|1W ${pct(m.p1w)} · 1M ${pct(m.p1m)}"><span>${esc(m.name)}</span><b>${Number(m.price).toFixed(m.price < 20 ? 3 : 2)}</b><em class="${cls(m.pct)}">${pct(m.pct)}</em>${window.UIX ? UIX.spark(m.spark, { w: 60, h: 16 }) : ""}</div>`).join("");
    } catch (e) { /* cosmetic */ }
  }

  function legend() {
    return `<div class="bm-leg">
      <b>SHIPS</b>${Object.entries({ tanker: "tanker", cargo: "cargo", other: "other" }).map(([k, l]) => `<span><i style="background:${SHIP[k]}"></i>${l}</span>`).join("")}
      <b>POWER ≥1 GW</b>${Object.entries(FUEL).slice(0, 5).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("")}
      <b>INFRA</b>${Object.entries(INFRA).map(([k, [c, l]]) => `<span><i class="sq" style="background:${c}"></i>${l}</span>`).join("")}
      <b>RISK</b><span><i style="background:#2be07e"></i>normal</span><span><i style="background:#ffa028"></i>elevated</span><span><i style="background:#ff4a4a"></i>high</span></div>`;
  }

  async function syncRain() {
    if (B.radar) { B.map.removeLayer(B.radar); B.radar = null; }
    if (!B.layers.rain) return;
    try { const r = await api("/world/radar"); B.radar = L.tileLayer(`${r.host}${r.latest.path}/256/{z}/{x}/{y}/2/1_1.png`, { opacity: 0.6, zIndex: 5, attribution: "RainViewer" }).addTo(B.map); } catch (e) { showToast("Rain radar unavailable"); }
  }

  async function loadShips() { try { B.ships = await api("/bmap/ships"); drawShips(); side(); } catch (e) { /* ignore */ } }
  async function loadRisk() { try { B.risk = await api("/bmap/risk"); drawRisk(); side(); } catch (e) { /* ignore */ } }
  async function loadHaz() { try { B.haz = await api("/bmap/hazards"); drawHazards(); side(); } catch (e) { /* ignore */ } }

  async function build() {
    const v = $("#view-bmap");
    if (!B.built) {
      B.built = true;
      v.innerHTML = `<div class="bm-shell"><div class="bm-main"><div class="bm-bar"><b class="hs-t">BMAP</b><span class="dim">global logistics & physical assets</span><span class="sp"></span>
          ${[["ships", "SHIPS"], ["choke", "CHOKEPOINTS"], ["infra", "TERMINALS & PORTS"], ["power", "POWER PLANTS"], ["hazards", "HAZARDS"], ["rain", "RAIN RADAR"]].map(([k, l]) => `<button class="bb-chip ${B.layers[k] ? "on" : ""}" data-l="${k}">${l}</button>`).join("")}</div>
          <div id="bm-map" class="bm-map"></div>${legend()}<div class="bm-mkt" id="bm-mkt"></div></div>
        <div class="bm-side">
          <div class="bb-panel"><div class="bb-ph"><span class="bb-n">1</span>CHOKEPOINT RISK<span class="sp"></span><span class="meta">click to fly</span></div><div class="bb-pad" id="bm-risk"></div></div>
          <div class="bb-panel"><div class="bb-ph"><span class="bb-n">2</span>LIVE SHIPS (AIS)</div><div class="bb-pad" id="bm-ais"></div></div>
          <div class="bb-panel"><div class="bb-ph"><span class="bb-n">3</span>DISASTER ALERTS<span class="sp"></span><span class="meta">GDACS · NOAA</span></div><div class="bb-pad bm-hazw" id="bm-haz"></div></div>
        </div></div>`;
      initMap();
      v.addEventListener("click", (e) => {
        const l = e.target.closest("[data-l]");
        if (l) {
          const k = l.dataset.l; B.layers[k] = !B.layers[k]; l.classList.toggle("on", B.layers[k]); save();
          if (k === "rain") syncRain(); else if (B.layers[k]) B.groups[k].addTo(B.map); else B.map.removeLayer(B.groups[k]);
          return;
        }
        const f = e.target.closest("[data-fly]"); if (f) { const [la, lo, z] = f.dataset.fly.split(",").map(Number); B.map.flyTo([la, lo], z, { duration: 1.1 }); }
      });
      try { B.st = await api("/bmap/static"); drawStatic(); drawRisk(); } catch (e) { showToast(e.message); }
      syncRain();
    }
    setTimeout(() => B.map.invalidateSize(), 120);
    loadHaz(); loadRisk(); loadShips(); markets();
    stop(); B.timers.push(setInterval(loadShips, 20000), setInterval(loadHaz, 600000), setInterval(loadRisk, 900000), setInterval(markets, 120000));
  }
  function stop() { B.timers.forEach(clearInterval); B.timers = []; }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => { if (prev) prev(tab); if (tab === "bmap") build().catch((e) => showToast(e.message)); else stop(); };
})();
