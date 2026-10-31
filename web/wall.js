/* LIVE WALL (INVESTING › LIVE WALL) — up to 50 small live charts on one screen, picked from your ☆ WATCH list (plus anything you add).
   Candles or lines, 1D / 5D / 1M / 3M / 1Y, refreshed every 30 s while the screen is visible. Click a tile to open that security.
   Depends on dashboard.js (api(), showToast()) and invest.js (NX.getList / NX.setList / NX.WATCH_KEY). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MAX = 50, SEL_KEY = "nostro.wall.sel", PREF_KEY = "nostro.wall.pref";
  const RANGES = ["1D", "5D", "1M", "3M", "1Y"];
  const PRESET = ("SPY QQQ IWM ^VIX AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO NFLX AMD PLTR COIN MSTR SMCI ARM SHOP JPM V XOM LLY BE RGTI SOXX GC=F CL=F " +
    "BTC-USD ETH-USD SOL-USD XRP-USD DOGE-USD ADA-USD AVAX-USD LINK-USD BNB-USD LTC-USD DOT-USD").split(" ");
  const lget = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const GREEN = "#2be07e", RED = "#ff4a4a", AMBER = "#ffa028";

  const W = { started: false, rows: [], missing: [], timer: 0, busy: false, ro: null };
  const pref = Object.assign({ range: "1D", type: "candle", sort: "none", cols: 0 }, lget(PREF_KEY, {}));
  let sel = lget(SEL_KEY, null);
  const savePref = () => lset(PREF_KEY, pref);
  const watch = () => (window.NX && NX.getList ? NX.getList(NX.WATCH_KEY) : []);
  const fmtPx = (v) => (v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v >= 10 ? v.toFixed(2) : v >= 1 ? v.toFixed(3) : v.toFixed(5));
  const pct = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
  const label = (s) => s.replace(/-USD$/, "").replace(/^\^/, "");

  function shell(view) {
    view.innerHTML = `<div class="bb-panel wl" style="--i:1">
      <div class="bb-ph"><span class="bb-n">W</span>LIVE WALL<span class="sp"></span><span class="meta" id="wl-meta"></span></div>
      <div class="gp-bar" id="wl-bar">
        <span class="wl-grp" id="wl-range">${RANGES.map((r) => `<button class="bb-chip ${r === pref.range ? "on" : ""}" data-v="${r}">${r}</button>`).join("")}</span><span class="gp-sep"></span>
        <span class="wl-grp" id="wl-type"><button class="bb-chip ${pref.type === "candle" ? "on" : ""}" data-v="candle">CANDLES</button><button class="bb-chip ${pref.type === "line" ? "on" : ""}" data-v="line">LINES</button></span><span class="gp-sep"></span>
        <span class="wl-grp" id="wl-sort"><button class="bb-chip ${pref.sort === "none" ? "on" : ""}" data-v="none">MY ORDER</button><button class="bb-chip ${pref.sort === "up" ? "on" : ""}" data-v="up">GAINERS</button><button class="bb-chip ${pref.sort === "dn" ? "on" : ""}" data-v="dn">LOSERS</button></span><span class="gp-sep"></span>
        <label class="wl-cols">COLUMNS <select id="wl-cols" class="ctl-in"><option value="0">AUTO</option>${[3, 4, 5, 6, 7, 8, 10].map((n) => `<option value="${n}" ${pref.cols === n ? "selected" : ""}>${n}</option>`).join("")}</select></label><span class="gp-sep"></span>
        <button class="bb-chip on" id="wl-pick">☰ CHOOSE CHARTS <b id="wl-n"></b></button><button class="bb-chip" id="wl-fs" title="Fill the whole screen (Esc leaves)">⛶ FULL SCREEN</button><button class="bb-chip" id="wl-win" title="Open the wall in its own window, e.g. on your second screen">⧉ NEW WINDOW</button>
      </div>
      <div class="wl-picker hidden" id="wl-picker"></div>
      <div class="wl-grid" id="wl-grid"><div class="bb-empty bb-load">LOADING CHARTS</div></div>
    </div>`;
  }

  // ------------------------------------------------------------------ chart picker
  function drawPicker() {
    const box = $("#wl-picker"); if (!box) return;
    const w = watch(), all = [...new Set([...w, ...sel])];
    box.innerHTML = `<div class="wl-pk-h"><b>CHOOSE UP TO ${MAX} CHARTS</b><span>${sel.length} / ${MAX} selected</span><span class="sp"></span>
      <button class="bb-chip" data-a="all">SELECT WATCHLIST</button><button class="bb-chip" data-a="preset">LOAD A STARTER SET</button><button class="bb-chip" data-a="none">CLEAR</button><button class="bb-chip on" data-a="done">DONE</button></div>
      <div class="wl-pk-add"><input class="ctl-in" id="wl-add" placeholder="Add a symbol: TSLA, BTC-USD, ^GSPC, GC=F" /><button class="bb-chip" id="wl-addb">ADD</button><em>${w.length} on your ☆ WATCH list (open any security and press ☆ WATCH to add more)</em></div>
      <div class="wl-pk-list">${all.map((s) => `<label class="${sel.includes(s) ? "on" : ""}"><input type="checkbox" data-s="${esc(s)}" ${sel.includes(s) ? "checked" : ""}/>${esc(label(s))}${w.includes(s) ? "" : '<i title="not on your watch list">+</i>'}</label>`).join("") || '<div class="dim">Your watch list is empty: add symbols above, or load the starter set.</div>'}</div>`;
    const setSel = (list) => { sel = [...new Set(list)].slice(0, MAX); lset(SEL_KEY, sel); drawPicker(); count(); load(); };
    box.querySelectorAll("input[data-s]").forEach((c) => c.addEventListener("change", () => {
      const s = c.dataset.s; if (c.checked) { if (sel.length >= MAX) { c.checked = false; return window.showToast && showToast(`The wall holds ${MAX} charts at most`); } sel = [...sel, s]; } else sel = sel.filter((x) => x !== s);
      lset(SEL_KEY, sel); c.parentElement.classList.toggle("on", c.checked); $(".wl-pk-h span").textContent = `${sel.length} / ${MAX} selected`; count(); clearTimeout(drawPicker.t); drawPicker.t = setTimeout(load, 500);
    }));
    box.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => {
      const a = b.dataset.a;
      if (a === "all") setSel(watch()); else if (a === "preset") setSel(PRESET); else if (a === "none") setSel([]); else box.classList.add("hidden");
    }));
    const add = () => {
      const v = ($("#wl-add").value || "").trim().toUpperCase().replace(/[^A-Z0-9.\-=^]/g, ""); if (!v) return;
      if (window.NX && NX.setList) { const w2 = watch(); if (!w2.includes(v)) NX.setList(NX.WATCH_KEY, [...w2, v]); }
      if (sel.length >= MAX) return window.showToast && showToast(`The wall holds ${MAX} charts at most`);
      setSel([...sel, v]);
    };
    $("#wl-addb").addEventListener("click", add); $("#wl-add").addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  }
  const count = () => { const n = $("#wl-n"); if (n) n.textContent = `${sel.length}/${MAX}`; };

  // ------------------------------------------------------------------ drawing
  function tile(r) {
    const up = r.pct >= 0;
    return `<div class="wl-t ${up ? "up" : "dn"}" data-s="${esc(r.symbol)}" title="${esc(r.name)} · click to open"><canvas></canvas><i class="wl-x" data-x="${esc(r.symbol)}" title="Remove from the wall">✕</i></div>`;
  }
  function paintTile(el, r) {
    const cv = el.querySelector("canvas"), w = el.clientWidth, h = el.clientHeight, dpr = (window.devicePixelRatio || 1) * (parseFloat(getComputedStyle(document.body).zoom) || 1); if (w < 20 || h < 20) return;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = `${w}px`; cv.style.height = `${h}px`; }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const up = r.pct >= 0, col = up ? GREEN : RED, bars = r.bars, top = Math.min(30, h * 0.34), bot = 4, ch = h - top - bot;
    let lo = Infinity, hi = -Infinity; bars.forEach((b) => { if (b[2] < lo) lo = b[2]; if (b[1] > hi) hi = b[1]; });
    const showBase = pref.range === "1D" && r.base && r.base > lo * 0.97 && r.base < hi * 1.03; if (showBase) { lo = Math.min(lo, r.base); hi = Math.max(hi, r.base); }
    const pad = (hi - lo) * 0.06 || hi * 0.01 || 1; lo -= pad; hi += pad;
    const X = (i) => 3 + ((i + 0.5) / bars.length) * (w - 6), Y = (v) => top + ch - ((v - lo) / (hi - lo)) * ch;
    if (pref.type === "line") {
      const grad = g.createLinearGradient(0, top, 0, h); grad.addColorStop(0, up ? "rgba(43,224,126,0.28)" : "rgba(255,74,74,0.28)"); grad.addColorStop(1, "rgba(0,0,0,0)");
      g.beginPath(); bars.forEach((b, i) => (i ? g.lineTo(X(i), Y(b[3])) : g.moveTo(X(i), Y(b[3])))); g.strokeStyle = col; g.lineWidth = 1.6; g.stroke();
      g.lineTo(X(bars.length - 1), h); g.lineTo(X(0), h); g.closePath(); g.fillStyle = grad; g.fill();
    } else {
      const sw = (w - 6) / bars.length, bw = Math.max(1, Math.min(9, sw * 0.62));
      bars.forEach((b, i) => { const u = b[3] >= b[0], c = u ? GREEN : RED, x = X(i); g.strokeStyle = c; g.fillStyle = c; g.lineWidth = 1; g.beginPath(); g.moveTo(x, Y(b[1])); g.lineTo(x, Y(b[2])); g.stroke(); const yo = Y(b[0]), yc = Y(b[3]); g.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo))); });
    }
    if (showBase) { g.setLineDash([3, 3]); g.strokeStyle = "rgba(255,255,255,0.28)"; g.lineWidth = 1; g.beginPath(); g.moveTo(0, Y(r.base)); g.lineTo(w, Y(r.base)); g.stroke(); g.setLineDash([]); }
    const lastY = Y(bars[bars.length - 1][3]); g.fillStyle = col; g.beginPath(); g.arc(w - 4, lastY, 2.2, 0, 6.3); g.fill();
    const fs = Math.max(10, Math.min(15, h * 0.12));
    g.font = `700 ${fs}px 'JetBrains Mono', Consolas, monospace`; g.textBaseline = "top"; g.fillStyle = "#f1f1f1"; g.fillText(label(r.symbol), 7, 5);
    g.textAlign = "right"; g.fillStyle = col; g.fillText(pct(r.pct), w - 7, 5);
    g.font = `500 ${Math.max(9, fs - 2)}px 'JetBrains Mono', Consolas, monospace`; g.fillStyle = "#cfcfcf"; g.fillText(fmtPx(r.price), w - 7, 6 + fs); g.textAlign = "left";
    if (w > 150) { g.fillStyle = "#6f6f6f"; g.fillText((r.name || "").slice(0, Math.max(8, Math.floor(w / 9) - 8)), 7, 6 + fs); }
  }
  function sorted() {
    const rows = W.rows.slice();
    if (pref.sort === "up") rows.sort((a, b) => b.pct - a.pct); else if (pref.sort === "dn") rows.sort((a, b) => a.pct - b.pct);
    return rows;
  }
  function layout() {
    const grid = $("#wl-grid"); if (!grid || !W.rows.length) return;
    const zm = parseFloat(getComputedStyle(document.body).zoom) || 1, n = W.rows.length, free = Math.max(300, (window.innerHeight - grid.getBoundingClientRect().top) / zm - 10), width = grid.clientWidth || window.innerWidth;
    const minCols = width < 520 ? 2 : width < 820 ? 3 : 1;
    let cols = pref.cols; if (cols && cols < minCols) cols = minCols; if (!cols) { let fit = false; for (let c = minCols; c <= 12 && !fit; c++) { const rows = Math.ceil(n / c), tw = width / c, th = free / rows; cols = c; fit = tw / th <= 2.1 && th >= 110; } if (!fit) cols = Math.max(minCols, Math.min(12, Math.round(width / 170))); if (n === 0) cols = 4; }
    const rows = Math.ceil(n / cols), th = Math.max(112, Math.floor((free - (rows - 1) * 6) / rows));
    grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`; grid.style.gridAutoRows = `${th}px`; grid.style.height = `${free}px`;
    grid.querySelectorAll(".wl-t").forEach((el) => { const r = W.rows.find((x) => x.symbol === el.dataset.s); if (r) paintTile(el, r); });
  }
  function render() {
    const grid = $("#wl-grid"); if (!grid) return;
    if (!sel.length) { grid.style.height = ""; grid.innerHTML = '<div class="bb-empty"><b>NO CHARTS SELECTED</b>Press ☰ CHOOSE CHARTS to pick up to 50 from your watch list, or load the starter set.</div>'; return; }
    const rows = sorted();
    grid.innerHTML = rows.map(tile).join("") + (W.missing.length ? `<div class="wl-miss">No data for: ${W.missing.map(esc).join(", ")}</div>` : "");
    $("#wl-meta").textContent = `${rows.length} charts · ${pref.range} · ${new Date().toLocaleTimeString([], { hour12: false })} · refreshes every 30 s`;
    grid.querySelectorAll(".wl-t").forEach((el) => {
      el.addEventListener("click", (e) => {
        const x = e.target.closest("[data-x]"); if (x) { e.stopPropagation(); sel = sel.filter((s) => s !== x.dataset.x); lset(SEL_KEY, sel); count(); W.rows = W.rows.filter((r) => r.symbol !== x.dataset.x); render(); return; }
        if (window.openSecurity) { document.querySelector('.tab[data-tab="security"]')?.click(); window.openSecurity(el.dataset.s, "GP"); }
      });
    });
    layout();
  }

  // ------------------------------------------------------------------ data
  async function load() {
    if (W.busy) { W.again = true; return; }
    if (!sel.length) { W.rows = []; return render(); }
    W.busy = true; const meta = $("#wl-meta"); if (meta && !W.rows.length) meta.textContent = "loading…";
    try {
      const r = await api(`/invest/wall?symbols=${encodeURIComponent(sel.join(","))}&range=${pref.range}`);
      W.rows = r.rows.filter((x) => sel.includes(x.symbol)); W.missing = r.missing || []; render();
    } catch (e) { const grid = $("#wl-grid"); if (grid && !W.rows.length) grid.innerHTML = `<div class="bb-empty"><b>COULD NOT LOAD</b>${esc(e.message || e)}</div>`; }
    W.busy = false; if (W.again) { W.again = false; load(); }
  }
  const active = () => document.getElementById("view-wall")?.classList.contains("active") && !document.hidden;

  function wire() {
    const chips = (id, key, after) => document.querySelectorAll(`#${id} .bb-chip`).forEach((b) => b.addEventListener("click", () => {
      pref[key] = b.dataset.v; savePref(); document.querySelectorAll(`#${id} .bb-chip`).forEach((c) => c.classList.toggle("on", c === b)); after();
    }));
    chips("wl-range", "range", () => { W.rows = []; load(); }); chips("wl-type", "type", () => render()); chips("wl-sort", "sort", () => render());
    $("#wl-cols").addEventListener("change", (e) => { pref.cols = Number(e.target.value); savePref(); layout(); });
    $("#wl-pick").addEventListener("click", () => { const p = $("#wl-picker"); p.classList.toggle("hidden"); if (!p.classList.contains("hidden")) drawPicker(); setTimeout(layout, 50); });
    $("#wl-fs").addEventListener("click", () => { const v = document.getElementById("view-wall"); if (document.fullscreenElement) document.exitFullscreen(); else (v.requestFullscreen || (() => {})).call(v); });
    $("#wl-win").addEventListener("click", () => { if (window.NX && NX.openWindow) NX.openWindow("wall"); });
    document.addEventListener("fullscreenchange", () => setTimeout(layout, 120));
    if (window.ResizeObserver) { W.ro = new ResizeObserver(() => { if (active()) layout(); }); W.ro.observe($("#view-wall")); }
    window.addEventListener("resize", () => { if (active()) layout(); });
  }

  function start() {
    if (W.started) { load(); return; }
    W.started = true;
    if (!Array.isArray(sel)) { sel = watch().slice(0, MAX); if (!sel.length) sel = PRESET.slice(0, MAX); lset(SEL_KEY, sel); }
    shell($("#view-wall")); count(); wire(); load();
    W.timer = setInterval(() => { if (active()) load(); }, 30000);
  }

  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => { if (prev) prev(tab); if (tab === "wall") start(); };
})();
