/* JORDAN TERMINAL boot screen: a full-screen intro (rotating wireframe globe, data rain, typed boot log, progress bar tied to real loading)
   that fades out into the terminal. Click or press any key to skip. Skipped entirely when the OS asks for reduced motion. */
(function () {
  "use strict";
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = document.getElementById("boot");
  if (!root) return;
  // shown once per browser session (and briefly): reloads and new windows go straight to the terminal
  let seen = false; try { seen = sessionStorage.getItem("nx_booted") === "1"; sessionStorage.setItem("nx_booted", "1"); } catch (e) { /* storage blocked */ }
  if ((reduce && location.hash !== "#boot") || /^#win=/.test(location.hash) || (seen && location.hash !== "#boot")) { root.remove(); return; }
  document.documentElement.classList.add("booting");

  const cv = document.getElementById("boot-cv"), g = cv.getContext("2d");
  const bar = document.getElementById("boot-bar"), pctEl = document.getElementById("boot-pct"), log = document.getElementById("boot-log"), title = document.getElementById("boot-title");
  let W = 0, H = 0, dpr = 1, raf = 0, done = false, t0 = performance.now();
  const fit = () => { dpr = window.devicePixelRatio || 1; W = innerWidth; H = innerHeight; cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px"; g.setTransform(dpr, 0, 0, dpr, 0, 0); initRain(); };

  // ---- data rain columns
  let cols = [];
  const GLYPH = "01ABCDEF$€¥₿Ξ◎▲▼%+-.0123456789";
  function initRain() { const n = Math.ceil(W / 22); cols = Array.from({ length: n }, (_, i) => ({ x: i * 22 + 6, y: Math.random() * -H, v: 40 + Math.random() * 120, len: 6 + Math.floor(Math.random() * 14), seed: Math.random() })); }

  // ---- globe points (lat/lon of big financial centres + random scatter)
  const CITIES = [[40.7, -74], [51.5, -0.1], [35.7, 139.7], [22.3, 114.2], [1.35, 103.8], [31.8, 35.2], [32.1, 34.8], [48.9, 2.3], [50.1, 8.7], [47.4, 8.5], [25.2, 55.3], [19.1, 72.9], [37.6, 127], [-33.9, 151.2], [-23.5, -46.6], [43.7, -79.4], [41.9, -87.6], [37.8, -122.4], [55.7, 37.6], [30, 31.2], [-26.2, 28], [39.9, 116.4], [31.2, 121.5], [6.5, 3.4]];
  const rand = Array.from({ length: 90 }, () => [Math.asin(Math.random() * 2 - 1) * 57.3, Math.random() * 360 - 180]);
  function drawGlobe(t, cx, cy, R) {
    const rot = t * 0.00035, tilt = 0.42;
    const P = (lat, lon) => { const a = lat * Math.PI / 180, b = lon * Math.PI / 180 + rot, x = Math.cos(a) * Math.sin(b), y = Math.sin(a), z = Math.cos(a) * Math.cos(b); const y2 = y * Math.cos(tilt) - z * Math.sin(tilt), z2 = y * Math.sin(tilt) + z * Math.cos(tilt); return [cx + x * R, cy - y2 * R, z2]; };
    // glow
    const gr = g.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 1.5); gr.addColorStop(0, "rgba(255,160,40,0.10)"); gr.addColorStop(1, "rgba(255,160,40,0)"); g.fillStyle = gr; g.beginPath(); g.arc(cx, cy, R * 1.5, 0, 6.3); g.fill();
    g.lineWidth = 1;
    for (let lat = -75; lat <= 75; lat += 15) { g.beginPath(); let on = false; for (let lon = -180; lon <= 180; lon += 6) { const p = P(lat, lon); if (p[2] < 0) { on = false; continue; } on ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]); on = true; } g.strokeStyle = "rgba(255,160,40,0.22)"; g.stroke(); }
    for (let lon = -180; lon < 180; lon += 15) { g.beginPath(); let on = false; for (let lat = -90; lat <= 90; lat += 6) { const p = P(lat, lon); if (p[2] < 0) { on = false; continue; } on ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]); on = true; } g.strokeStyle = "rgba(255,160,40,0.16)"; g.stroke(); }
    g.strokeStyle = "rgba(255,160,40,0.55)"; g.beginPath(); g.arc(cx, cy, R, 0, 6.3); g.stroke();
    rand.forEach(([la, lo]) => { const p = P(la, lo); if (p[2] > 0) { g.fillStyle = `rgba(255,190,90,${0.15 + p[2] * 0.4})`; g.fillRect(p[0], p[1], 1.6, 1.6); } });
    const pts = CITIES.map(([la, lo]) => P(la, lo));
    // arcs between neighbouring hubs
    g.lineWidth = 1; for (let i = 0; i < pts.length; i += 2) { const a = pts[i], b = pts[(i + 5) % pts.length]; if (a[2] > 0 && b[2] > 0) { const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2 - R * 0.18; g.strokeStyle = "rgba(255,120,30,0.35)"; g.beginPath(); g.moveTo(a[0], a[1]); g.quadraticCurveTo(mx, my, b[0], b[1]); g.stroke(); const k = ((t / 1400 + i * 0.13) % 1), qx = (1 - k) * (1 - k) * a[0] + 2 * (1 - k) * k * mx + k * k * b[0], qy = (1 - k) * (1 - k) * a[1] + 2 * (1 - k) * k * my + k * k * b[1]; g.fillStyle = "#fff"; g.fillRect(qx - 1.2, qy - 1.2, 2.4, 2.4); } }
    pts.forEach((p, i) => { if (p[2] > 0) { const pulse = 2.2 + Math.sin(t / 260 + i) * 1.2; g.fillStyle = "#ffa028"; g.beginPath(); g.arc(p[0], p[1], 2.2, 0, 6.3); g.fill(); g.strokeStyle = `rgba(255,160,40,${0.5 - pulse * 0.08})`; g.beginPath(); g.arc(p[0], p[1], pulse * 2.4, 0, 6.3); g.stroke(); } });
    // orbit rings
    g.strokeStyle = "rgba(255,160,40,0.28)"; g.setLineDash([2, 7]); g.beginPath(); g.ellipse(cx, cy, R * 1.28, R * 0.42, -0.35, 0, 6.3); g.stroke(); g.beginPath(); g.ellipse(cx, cy, R * 1.5, R * 0.5, 0.25, 0, 6.3); g.stroke(); g.setLineDash([]);
    const oa = t / 900; g.fillStyle = "#fff"; g.beginPath(); g.arc(cx + Math.cos(oa) * R * 1.28 * Math.cos(-0.35) - Math.sin(oa) * R * 0.42 * Math.sin(-0.35), cy + Math.cos(oa) * R * 1.28 * Math.sin(-0.35) + Math.sin(oa) * R * 0.42 * Math.cos(-0.35), 2.6, 0, 6.3); g.fill();
  }

  function frame(now) {
    const t = now - t0; g.fillStyle = "rgba(0,0,0,0.34)"; g.fillRect(0, 0, W, H);
    g.font = "12px 'JetBrains Mono', Consolas, monospace";
    cols.forEach((c) => { c.y += c.v / 60; for (let k = 0; k < c.len; k++) { const y = c.y - k * 15; if (y < -15 || y > H + 15) continue; g.fillStyle = k === 0 ? "rgba(255,225,160,0.9)" : `rgba(255,160,40,${0.22 * (1 - k / c.len)})`; g.fillText(GLYPH[(Math.floor(c.seed * 997 + k * 7 + t / 90 + c.x) % GLYPH.length + GLYPH.length) % GLYPH.length], c.x, y); } if (c.y - c.len * 15 > H) { c.y = -20; c.v = 40 + Math.random() * 120; } });
    const R = Math.min(W, H) * 0.21; drawGlobe(t, W / 2, H * 0.33, R);
    // scanline sweep
    const sy = ((t / 2600) % 1) * H; const sg = g.createLinearGradient(0, sy - 60, 0, sy + 4); sg.addColorStop(0, "rgba(255,160,40,0)"); sg.addColorStop(1, "rgba(255,160,40,0.10)"); g.fillStyle = sg; g.fillRect(0, sy - 60, W, 64);
    if (!done) raf = requestAnimationFrame(frame);
  }

  // ---- typed boot log + progress tied to what is really loading
  const LINES = ["BOOT SEQUENCE INITIATED", "LOADING MARKET ENGINES ............ OK", "SYNCING NOSTRO ALGO CORE ............. OK", "ALL SYSTEMS NOMINAL"];
  let shown = 0, target = 0, cur = 0;
  // no waiting for the API any more: the page shows its own loading states, the intro only covers the page itself loading
  const stages = { dom: false, load: false }, MIN = location.hash === "#boot" ? 90000 : 800; // open the page with #boot to hold the intro for a preview
  const need = () => (stages.dom ? 45 : 8) + (stages.load ? 55 : 0);
  document.addEventListener("DOMContentLoaded", () => { stages.dom = true; });
  if (document.readyState !== "loading") stages.dom = true;
  if (document.readyState === "complete") stages.load = true; else window.addEventListener("load", () => { stages.load = true; });

  const tick = setInterval(() => {
    const t = performance.now() - t0, timeShare = Math.min(1, t / MIN);
    target = Math.min(need(), timeShare * 100);           // the bar can never outrun either the real loading or the minimum show time
    cur += (target - cur) * 0.12; if (target - cur < 0.4) cur = target;
    bar.style.width = cur.toFixed(1) + "%"; pctEl.textContent = String(Math.floor(cur)).padStart(3, "0") + "%";
    const want = Math.min(LINES.length, Math.floor((cur / 100) * (LINES.length + 0.6)));
    while (shown < want) { const d = document.createElement("div"); d.textContent = "› " + LINES[shown]; if (shown === LINES.length - 1) d.className = "ok"; log.appendChild(d); log.scrollTop = log.scrollHeight; shown++; }
    if (cur >= 99.5 && t >= MIN) finish();
  }, 60);

  // ---- title scramble reveal
  const WORD = "JORDAN", ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#$%&*@";
  const letters = [...WORD].map((ch) => { const s = document.createElement("span"); s.textContent = ALPHA[Math.floor(Math.random() * ALPHA.length)]; s.className = "scr"; title.appendChild(s); return s; });
  letters.forEach((s, i) => { const start = 80 + i * 70; let n = 0; setTimeout(() => { const iv = setInterval(() => { n++; if (n > 9) { s.textContent = WORD[i]; s.className = "set"; clearInterval(iv); } else s.textContent = ALPHA[Math.floor(Math.random() * ALPHA.length)]; }, 55); }, start); });

  function finish() {
    if (done) return; done = true; clearInterval(tick); bar.style.width = "100%"; pctEl.textContent = "100%";
    root.classList.add("out");
    setTimeout(() => { cancelAnimationFrame(raf); root.remove(); document.documentElement.classList.remove("booting"); }, 350);
  }
  root.addEventListener("click", finish); document.addEventListener("keydown", finish, { once: true });
  window.addEventListener("resize", fit); fit(); raf = requestAnimationFrame(frame);
})();
