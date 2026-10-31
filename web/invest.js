/* INVESTING: shared helpers (window.NX) + the market pages: MARKETS, STOCKS, CRYPTO, ETFs, FX & COMMODITIES, ECONOMY.
   The SECURITY deep-dive lives in security.js and reuses these helpers. Depends on api() from dashboard.js. */
(function () {
  "use strict";
  const NX = (window.NX = {});
  const $ = (sel, root = document) => root.querySelector(sel);
  NX.$ = $;
  NX.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const esc = NX.esc;
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);

  // ------------------------------------------------------------------ formatting
  NX.dir = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  NX.num = (n, d = 2) => (isNum(n) ? Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
  NX.px = (n, dec) => {
    if (!isNum(n)) return "—";
    if (dec !== undefined) return NX.num(n, dec);
    const a = Math.abs(n);
    return a >= 1 ? NX.num(n, 2) : a >= 0.01 ? NX.num(n, 4) : Number(n.toPrecision(3)).toString();
  };
  NX.pct = (n, sign = true, d = 2) => (isNum(n) ? `${sign && n > 0 ? "+" : ""}${n.toFixed(d)}%` : "—");
  NX.signed = (n, d = 2) => (isNum(n) ? `${n > 0 ? "+" : ""}${NX.num(n, d)}` : "—");
  NX.big = (n, cur = "") => {
    if (!isNum(n)) return "—";
    const a = Math.abs(n), s = n < 0 ? "-" : "";
    if (a >= 1e12) return `${s}${cur}${(a / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `${s}${cur}${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${s}${cur}${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${s}${cur}${(a / 1e3).toFixed(1)}K`;
    return `${s}${cur}${a.toFixed(a < 10 ? 2 : 0)}`;
  };
  NX.date = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—");
  NX.ago = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
  };
  const dir = NX.dir;

  NX.spark = (values, up, w = 60, h = 16) => {
    if (!values || values.length < 2) return "";
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 1 - ((v - lo) / span) * (h - 2)).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle"><polyline fill="none" stroke="${up ? "#2be07e" : "#ff4a4a"}" stroke-width="1.2" points="${pts}"/></svg>`;
  };
  NX.rangeBar = (lo, hi, price) => {
    if (!isNum(lo) || !isNum(hi) || hi <= lo || !isNum(price)) return '<span class="dim">—</span>';
    const p = Math.max(0, Math.min(100, ((price - lo) / (hi - lo)) * 100));
    return `<span class="rb" title="${NX.px(lo)} – ${NX.px(hi)}"><span class="rb-lo">${NX.px(lo)}</span><span class="rb-track"><i style="left:${p.toFixed(1)}%"></i></span><span class="rb-hi">${NX.px(hi)}</span></span>`;
  };
  NX.newsHtml = (items, n = 40) => (items.length
    ? `<ul class="bb-news">${items.slice(0, n).map((i) => `<li><span class="t">${NX.ago(i.ts) || ""}</span><span class="src ${/coin|crypto|bitcoin|block/i.test(i.source || "") ? "cr" : ""}">${esc((i.source || "").slice(0, 16))}</span><a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></li>`).join("")}</ul>`
    : `<div class="bb-empty"><b>NO HEADLINES</b></div>`);
  NX.load = (msg = "LOADING") => `<div class="bb-empty bb-load">${msg}</div>`;
  NX.err = (e) => `<div class="bb-empty"><b>DATA UNAVAILABLE</b>${esc(e && e.message ? e.message : e)}</div>`;

  // ------------------------------------------------------------------ flash cells on change
  const lastVals = new Map();
  NX.flash = (el, key, v) => {
    if (!el || !isNum(v)) return;
    const prev = lastVals.get(key);
    lastVals.set(key, v);
    if (prev === undefined || prev === v) return;
    el.classList.remove("fl-up", "fl-dn");
    void el.offsetWidth;
    el.classList.add(v > prev ? "fl-up" : "fl-dn");
  };

  // ------------------------------------------------------------------ canvas charts (line + bars)
  function fitCanvas(box, h) {
    box.innerHTML = "";
    const cv = document.createElement("canvas");
    const w = Math.max(200, box.clientWidth);
    const dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = h * dpr; cv.style.width = `${w}px`; cv.style.height = `${h}px`; cv.style.display = "block";
    box.appendChild(cv);
    const ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.font = "10px 'JetBrains Mono', Consolas, monospace";
    return { cv, ctx, w, h };
  }

  NX.fitCanvas = fitCanvas;
  // a chart drawn while its page is hidden would measure 0px wide: wait until the box becomes visible, then draw
  NX.defer = (box, redraw) => {
    if (box.offsetWidth > 0) return false;
    box.innerHTML = NX.load();
    const ro = new ResizeObserver(() => { if (box.offsetWidth > 0) { ro.disconnect(); redraw(); } });
    ro.observe(box);
    return true;
  };
  NX.lineChart = (box, points, o = {}) => {
    if (NX.defer(box, () => NX.lineChart(box, points, o))) return;
    const h = o.height || 260;
    if (!points || points.length < 2) { box.innerHTML = '<div class="bb-empty"><b>NO DATA</b></div>'; return; }
    const { cv, ctx, w } = fitCanvas(box, h);
    const pad = { l: 8, r: 54, t: 10, b: 20 };
    const vals = points.map((p) => p[1]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi === lo) { hi += 1; lo -= 1; }
    const padv = (hi - lo) * 0.06; lo -= padv; hi += padv;
    const x = (i) => pad.l + (i / (points.length - 1)) * (w - pad.l - pad.r);
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
    const color = o.color || "#ffa028";
    const fmt = o.fmt || ((v) => NX.num(v, 2));
    const draw = (hover) => {
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "#171717"; ctx.fillStyle = "#8b8b8b"; ctx.lineWidth = 1;
      for (let g = 0; g <= 4; g++) {
        const v = lo + ((hi - lo) * g) / 4, yy = y(v);
        ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
        ctx.textAlign = "left"; ctx.fillText(fmt(v), w - pad.r + 6, yy + 3);
      }
      const ticks = 5;
      for (let g = 0; g <= ticks; g++) {
        const i = Math.round((g / ticks) * (points.length - 1));
        ctx.textAlign = g === 0 ? "left" : g === ticks ? "right" : "center";
        ctx.fillText(String(points[i][0]).slice(0, 7), x(i), h - 5);
      }
      if (o.zero !== false && lo < 0 && hi > 0) { ctx.strokeStyle = "#3a3a3a"; ctx.beginPath(); ctx.moveTo(pad.l, y(0)); ctx.lineTo(w - pad.r, y(0)); ctx.stroke(); }
      ctx.beginPath();
      points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p[1])) : ctx.moveTo(x(i), y(p[1]))));
      ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.stroke();
      if (o.fill !== false) {
        ctx.lineTo(x(points.length - 1), h - pad.b); ctx.lineTo(x(0), h - pad.b); ctx.closePath();
        const gr = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
        gr.addColorStop(0, color + "44"); gr.addColorStop(1, color + "00");
        ctx.fillStyle = gr; ctx.fill();
      }
      const last = points[points.length - 1];
      ctx.fillStyle = color; ctx.fillRect(w - pad.r + 2, y(last[1]) - 8, pad.r - 2, 16);
      ctx.fillStyle = "#000"; ctx.textAlign = "left"; ctx.fillText(fmt(last[1]), w - pad.r + 5, y(last[1]) + 3);
      if (hover != null) {
        const p = points[hover], xx = x(hover), yy = y(p[1]);
        ctx.strokeStyle = "#ffa028"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, h - pad.b); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "#ffa028"; ctx.beginPath(); ctx.arc(xx, yy, 3.5, 0, 6.3); ctx.fill();
        const txt = `${p[0]}   ${fmt(p[1])}`, tw = ctx.measureText(txt).width + 14, tx = Math.min(w - pad.r - tw, Math.max(pad.l, xx - tw / 2));
        ctx.fillStyle = "#000"; ctx.strokeStyle = "#ffa028"; ctx.fillRect(tx, pad.t, tw, 18); ctx.strokeRect(tx + 0.5, pad.t + 0.5, tw - 1, 17);
        ctx.fillStyle = "#f1f1f1"; ctx.textAlign = "left"; ctx.fillText(txt, tx + 7, pad.t + 12);
      }
    };
    draw();
    cv.addEventListener("mousemove", (e) => {
      const r = cv.getBoundingClientRect();
      const i = Math.round(((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * (points.length - 1));
      draw(Math.max(0, Math.min(points.length - 1, i)));
    });
    cv.addEventListener("mouseleave", () => draw());
  };

  NX.barChart = (box, labels, series, o = {}) => {
    if (NX.defer(box, () => NX.barChart(box, labels, series, o))) return;
    const h = o.height || 190;
    const { ctx, w } = fitCanvas(box, h);
    const pad = { l: 10, r: 10, t: 14, b: 22 };
    const all = series.flatMap((s) => s.values.filter(isNum));
    if (!all.length) { box.innerHTML = '<div class="bb-empty"><b>NO DATA</b></div>'; return; }
    const hi = Math.max(0, ...all), lo = Math.min(0, ...all), span = hi - lo || 1;
    const y = (v) => pad.t + (1 - (v - lo) / span) * (h - pad.t - pad.b);
    const gw = (w - pad.l - pad.r) / labels.length, bw = Math.min(46, (gw * 0.72) / series.length);
    const fmt = o.fmt || ((v) => NX.big(v));
    ctx.strokeStyle = "#333"; ctx.beginPath(); ctx.moveTo(pad.l, y(0)); ctx.lineTo(w - pad.r, y(0)); ctx.stroke();
    labels.forEach((lab, i) => {
      const cx = pad.l + gw * i + gw / 2;
      series.forEach((s, k) => {
        const v = s.values[i];
        if (!isNum(v)) return;
        const x0 = cx - (bw * series.length) / 2 + k * bw;
        ctx.fillStyle = v >= 0 ? s.color : "#ff4a4a";
        ctx.fillRect(x0 + 1, Math.min(y(v), y(0)), bw - 2, Math.abs(y(v) - y(0)));
        ctx.fillStyle = "#f1f1f1"; ctx.textAlign = "center";
        ctx.fillText(fmt(v), x0 + bw / 2, (v >= 0 ? y(v) : y(v) + 11) - (v >= 0 ? 4 : 0));
      });
      ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "center"; ctx.fillText(lab, cx, h - 6);
    });
    if (series.length > 1) {
      let lx = pad.l;
      series.forEach((s) => { ctx.fillStyle = s.color; ctx.fillRect(lx, 2, 8, 8); ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "left"; ctx.fillText(s.name, lx + 12, 10); lx += ctx.measureText(s.name).width + 30; });
    }
  };

  // ------------------------------------------------------------------ monitor tables (sortable, click = open security)
  const COLS = {
    sym: { h: "SECURITY", cell: (r) => `<span class="sym">${esc(r.symbol)}</span> <span class="dim">${esc((r.name && r.name !== r.symbol ? r.name : "").slice(0, 26))}</span>`, sort: (r) => r.symbol },
    name: { h: "NAME", cell: (r) => `<span class="sym">${esc((r.name || r.symbol).slice(0, 28))}</span>`, sort: (r) => r.name || r.symbol },
    last: { h: "LAST", r: 1, cell: (r, o) => NX.px(r.price, o.dec), sort: (r) => r.price, flash: true },
    chg: { h: "CHG", r: 1, cell: (r, o) => `<span class="${dir(r.chg)}">${NX.signed(r.chg, o.dec ?? 2)}</span>`, sort: (r) => r.chg },
    pct: { h: "%CHG", r: 1, cell: (r) => `<span class="${dir(r.pct)}">${NX.pct(r.pct)}</span>`, sort: (r) => r.pct },
    p1w: { h: "1W", r: 1, cell: (r) => `<span class="${dir(r.p1w)}">${NX.pct(r.p1w, true, 1)}</span>`, sort: (r) => r.p1w },
    p1m: { h: "1M", r: 1, cell: (r) => `<span class="${dir(r.p1m)}">${NX.pct(r.p1m, true, 1)}</span>`, sort: (r) => r.p1m },
    ytd: { h: "YTD", r: 1, cell: (r) => `<span class="${dir(r.ytd)}">${NX.pct(r.ytd, true, 1)}</span>`, sort: (r) => r.ytd },
    range: { h: "52W RANGE", cell: (r) => NX.rangeBar(r.lo, r.hi, r.price), sort: (r) => (r.hi > r.lo ? (r.price - r.lo) / (r.hi - r.lo) : 0) },
    vol: { h: "VOLUME", r: 1, cell: (r) => `<span class="dim">${NX.big(r.vol)}</span>`, sort: (r) => r.vol },
    spark: { h: "30D", cell: (r) => NX.spark(r.spark, (r.p1m ?? r.pct) >= 0, 64, 16) },
  };

  NX.monitorTable = (box, rows, o = {}) => {
    const cols = o.cols || ["sym", "last", "chg", "pct", "p1w", "p1m", "ytd", "range", "spark"];
    const st = (box._st = box._st || { key: o.sort || null, asc: false });
    let data = [...rows];
    if (st.key && COLS[st.key] && COLS[st.key].sort) {
      const f = COLS[st.key].sort;
      data.sort((a, b) => { const x = f(a), y = f(b); return ((x ?? -Infinity) > (y ?? -Infinity) ? 1 : -1) * (st.asc ? 1 : -1); });
    }
    if (o.limit) data = data.slice(0, o.limit);
    box.innerHTML = `<table class="bb-tbl"><thead><tr>${cols.map((c) => `<th class="${COLS[c].r ? "r" : ""} ${COLS[c].sort ? "sortable" : ""} ${st.key === c ? "sorted" : ""}" data-c="${c}">${COLS[c].h}${st.key === c ? (st.asc ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead><tbody>${data.map((r) =>
      `<tr class="click" data-sec="${esc(r.symbol)}">${cols.map((c) => `<td class="${COLS[c].r ? "r" : ""}" ${COLS[c].flash ? `data-k="${esc(r.symbol)}"` : ""}>${COLS[c].cell(r, o)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
      st.asc = st.key === th.dataset.c ? !st.asc : false;
      st.key = th.dataset.c;
      NX.monitorTable(box, rows, o);
    }));
    data.forEach((r) => NX.flash(box.querySelector(`td[data-k="${CSS.escape(r.symbol)}"]`), `${o.id || box.id}:${r.symbol}`, r.price));
  };

  NX.sectorBars = (box, rows) => {
    const sorted = [...rows].sort((a, b) => b.pct - a.pct);
    const m = Math.max(0.5, ...sorted.map((r) => Math.abs(r.pct)));
    box.innerHTML = `<div class="sb">${sorted.map((r) => `<div class="sb-row" data-sec="${esc(r.symbol)}"><span class="sb-l">${esc(r.name)}</span><span class="sb-t"><i class="${r.pct >= 0 ? "up" : "dn"}" style="width:${(Math.abs(r.pct) / m) * 48}%;${r.pct >= 0 ? "left:50%" : `right:50%`}"></i></span><span class="sb-v ${dir(r.pct)}">${NX.pct(r.pct)}</span></div>`).join("")}</div>`;
  };

  // ------------------------------------------------------------------ page plumbing
  const pages = {};
  const built = {};
  const timers = {};
  const activeView = () => document.querySelector(".view.active")?.id?.replace("view-", "");

  function panel(n, title, id, o = {}) {
    return `<div class="bb-panel ${o.cls || "s6 h360"}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span>${o.head || ""}<span class="meta" id="${id}-meta"></span></div><div class="bb-pb" id="${id}">${NX.load()}</div></div>`;
  }
  const chips = (id, items, on) => `<div class="bb-chips" id="${id}">${items.map(([v, l]) => `<button class="bb-chip ${v === on ? "on" : ""}" data-f="${v}">${l}</button>`).join("")}</div>`;
  NX.panel = panel; NX.chips = chips;
  function wireChips(id, cb) {
    document.querySelectorAll(`#${id} .bb-chip`).forEach((c) => c.addEventListener("click", () => {
      document.querySelectorAll(`#${id} .bb-chip`).forEach((x) => x.classList.remove("on"));
      c.classList.add("on");
      cb(c.dataset.f);
    }));
  }
  NX.wireChips = wireChips;
  const mon = (set) => api(`/invest/monitor?set=${set}`).then((r) => r.rows);
  const setMeta = (id, n, suffix = "") => { const el = document.getElementById(`${id}-meta`); if (el) el.textContent = `${n} ${suffix}`.trim(); };

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-sec]");
    if (el && window.openSecurity) { e.preventDefault(); window.openSecurity(el.dataset.sec); }
  });

  function every(key, fn, ms, view = key) {
    fn();
    clearInterval(timers[key]);
    timers[key] = setInterval(() => { if (!document.hidden && activeView() === view) fn(); }, ms);
  }

  // ------------------------------------------------------------------ NEWS / EVENTS / CROWD panels
  // One function fills every news & intelligence panel present on the open page (each panel lives on one page: NEWS,
  // MARKETS or POLYMARKET) and only fetches what that page shows.
  let intelTick = 0;
  if (!window.__ledBound) {
    window.__ledBound = true;
    document.addEventListener("click", async (ev) => {
      const add = ev.target.closest && ev.target.closest("#led-add");
      if (add) {
        const v = ($("#led-ch").value || "").replace(/^@|https?:\/\/t\.me\/(s\/)?/g, "").trim();
        if (!v) return;
        try { const cur = (await api("/intel/channels")).channels; cur[v] = "other"; await fetch("/api/intel/channels", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channels: cur }) }); $("#led-ch").value = ""; $("#led-chs").textContent = Object.keys(cur).join(" · "); } catch (e) { /* shown on next refresh */ }
        return;
      }
      const tr = ev.target.closest && ev.target.closest("tr[data-story]");
      if (!tr) return;
      const box = $("#led-detail");
      try {
        const x = await api("/intel/story/" + tr.dataset.story);
        const m = x.measured || {}, sg = (v) => (v > 0 ? "+" : "") + v;
        box.innerHTML = `<div style="padding:8px 10px;border-top:1px solid #262626"><div class="sub-h">${esc(x.title)}</div><div class="why-note">${esc(x.explain || "not measured yet")}</div>
          <table class="bb-tbl"><thead><tr><th>ASSET</th><th>15M BEFORE</th><th>+5M</th><th>+15M</th><th>+60M</th><th>Z15</th></tr></thead><tbody>${Object.entries(m.assets || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.pre !== undefined ? sg(v.pre) + "%" : ""}</td><td>${v.r5 !== undefined ? sg(v.r5) + "%" : ""}</td><td>${v.r15 !== undefined ? sg(v.r15) + "%" : ""}</td><td>${v.r60 !== undefined ? sg(v.r60) + "%" : ""}</td><td><b>${v.z15 ?? ""}</b></td></tr>`).join("")}</tbody></table>
          ${m.outcome ? `<div class="why-note">Follow-up: +1d ${m.d1 ?? "—"}% · +5d ${m.d5 ?? "—"}% → <b>${esc(m.outcome)}</b></div>` : ""}
          <div class="sub-h" style="margin-top:6px">SOURCES (${x.items.length})</div><ul class="bb-news">${x.items.map((i) => `<li><span class="t">${new Date(i.ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span><span class="src">${esc(i.source.split(":").pop())}</span><div>${i.link ? `<a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.text.slice(0, 220))}</a>` : esc(i.text.slice(0, 220))}</div></li>`).join("")}</ul></div>`;
        box.scrollIntoView({ block: "nearest" });
      } catch (e) { box.innerHTML = NX.err(e); }
    });
  }
  if (!window.__impBound) { window.__impBound = true; document.addEventListener("click", (ev) => { const li = ev.target.closest && ev.target.closest("li[data-ev]"); if (li && !ev.target.closest("a")) { window.__impEv = li.dataset.ev; window.__impRefresh && window.__impRefresh(); } }); }
  // the market wire (all RSS publishers): filter by group, publisher and text, newest first
  const WIRE = { items: [], tag: "ALL", src: "", q: "" };
  const drawWire = () => {
    const el = $("#mk-news"); if (!el) return;
    const q = WIRE.q.toLowerCase();
    const rows = WIRE.items.filter((i) => (WIRE.tag === "ALL" || i.tag === WIRE.tag) && (!WIRE.src || i.source === WIRE.src) && (!q || String(i.title).toLowerCase().includes(q)));
    const srcs = [...new Set(WIRE.items.map((i) => i.source))];
    const hour = WIRE.items.filter((i) => Date.now() / 1000 - i.ts < 3600).length;
    el.innerHTML = `<div class="nw-bar"><div class="bb-chips">${["ALL", ...new Set(WIRE.items.map((i) => i.tag))].map((t) => `<button class="bb-chip ${t === WIRE.tag ? "on" : ""}" data-wtag="${esc(t)}">${esc(t)}</button>`).join("")}</div>
        <select data-wsrc><option value="">ALL SOURCES (${srcs.length})</option>${srcs.map((x) => `<option ${x === WIRE.src ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <input data-wq placeholder="search headlines…" value="${esc(WIRE.q)}"/><span class="sp"></span><span class="dim">${rows.length} shown · ${hour} in the last hour</span></div>${NX.newsHtml(rows, 150)}`;
    if (WIRE.focus) { const qi = el.querySelector("[data-wq]"); qi.focus(); qi.setSelectionRange(qi.value.length, qi.value.length); }
  };
  if (!window.__wireBound) {
    window.__wireBound = true;
    document.addEventListener("click", (e) => { const t = e.target.closest && e.target.closest("[data-wtag]"); if (t) { WIRE.tag = t.dataset.wtag; drawWire(); } });
    document.addEventListener("change", (e) => { if (e.target.matches && e.target.matches("[data-wsrc]")) { WIRE.src = e.target.value; drawWire(); } });
    document.addEventListener("input", (e) => { if (e.target.matches && e.target.matches("[data-wq]")) { WIRE.q = e.target.value; WIRE.focus = true; drawWire(); WIRE.focus = false; } });
  }
  const news = async () => { const el = $("#mk-news"); if (!el) return; try { WIRE.items = (await api("/capital/news")).items; drawWire(); } catch (e) { el.innerHTML = NX.err(e); } };
  const intel = async () => {
    const opt = (id, fn) => ($("#" + id) ? fn() : Promise.reject(new Error("not on this page")));  // fetch only what the open page shows
    const [ev, il, pm] = await Promise.allSettled([opt("mk-ev", () => api("/intel/events?hours=48&min_sev=2")), opt("mk-il", () => api("/intel/feed?group=il&limit=50")), opt("mk-pm", () => api("/intel/polymarket"))]);
    const put = (id, r, fn) => { const el = $(`#${id}`); if (!el) return; r.status === "fulfilled" ? (el.innerHTML = fn(r.value)) : (el.innerHTML = NX.err(r.reason)); };
    put("mk-ev", ev, (d) => {
      setMeta("mk-ev", d.events.filter((e) => e.severity >= 3).length, "CRITICAL");
      if (!d.events.length) return `<div class="bb-empty"><b>NO CRITICAL EVENTS</b>Nothing in the last 48 hours cleared the bar. Scanning every 5 minutes; critical ones are pushed to Telegram.</div>`;
      return `<ul class="bb-news">${d.events.map((e) => `<li data-ev="${e.id}" style="cursor:pointer" title="show impact"><span class="t">${NX.ago(e.ts) || ""}</span><span class="src" style="color:${e.severity >= 3 ? "#ff4a4a" : "#ffa028"}">${e.severity >= 3 ? "CRITICAL" : "WATCH"} · ${esc((e.kind || "").toUpperCase())}</span><div>${e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>` : `<a>${esc(e.title)}</a>`} <span class="dim" style="font-size:10.5px">· ${e.sources} outlet${e.sources === 1 ? "" : "s"}${e.pushed ? " · sent to Telegram" : ""}</span></div></li>`).join("")}</ul>`;
    });
    const [calR, pmsR] = await Promise.allSettled([opt("mk-cal", () => api("/intel/calendar")), opt("mk-pms", () => api("/intel/pm-shocks?hours=24"))]);
    put("mk-cal", calR, (d) => {
      const now = Date.now() / 1000;
      const cd = (ts) => { const m = Math.round((ts - now) / 60); return m <= 0 ? "" : m < 60 ? `in ${m}m` : m < 1440 ? `in ${Math.floor(m / 60)}h ${m % 60}m` : `in ${Math.floor(m / 1440)}d`; };
      if (!d.events.length) return `<div class="bb-empty"><b>NO MAJOR US RELEASES</b>Nothing high-impact on this week's calendar.</div>`;
      return `<table class="bb-tbl"><thead><tr><th>WHEN (IL)</th><th>EVENT</th><th>FCST</th><th>PREV</th><th>ACTUAL</th><th></th></tr></thead><tbody>${d.events.map((e) => {
        const past = e.ts < now, col = e.direction === "hawkish" ? "#ff4a4a" : e.direction === "dovish" ? "#2be07e" : "#8a8a8a";
        return `<tr style="${past && !e.actual ? "opacity:.55" : ""}"><td>${new Date(e.ts * 1000).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })} <span class="dim">${cd(e.ts)}</span></td><td>${e.impact === "High" ? "<b>" + esc(e.name) + "</b>" : esc(e.name)}</td><td>${esc(e.forecast || "")}</td><td class="dim">${esc(e.previous || "")}</td><td><b>${esc(e.actual || "")}</b></td><td style="color:${col};font-weight:700">${e.direction ? esc(e.direction.toUpperCase()) : ""}</td></tr>`;
      }).join("")}</tbody></table><div class="why-note">${esc(d.source)}. A 30-minute heads-up and the release card go to Telegram for high-impact events.</div>`;
    });
    put("mk-pms", pmsR, (d) => d.shocks.length ? `<ul class="bb-news">${d.shocks.map((x) => `<li><span class="t">${NX.ago(x.ts) || ""}</span><span class="src" style="color:${x.p1 > x.p0 ? "#2be07e" : "#ff4a4a"}">${Math.round(x.p0)}→${Math.round(x.p1)}%</span><div><a href="https://polymarket.com/event/${esc(x.slug)}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a>${x.question && x.question !== x.title ? ` <span class="dim">· ${esc(x.question)}</span>` : ""} <span class="dim" style="font-size:10.5px">· in ${esc(x.window)} · vol $${Math.round(x.vol24 / 1000)}k${x.news.length ? "" : " · no news yet"}</span></div></li>`).join("")}</ul>` : `<div class="bb-empty"><b>NO SHOCKS</b>No market-relevant probability moved ≥10pt in an hour or ≥20pt in a day (volume ≥ $50k).</div>`);
    const ledEl = $("#mk-led");
    if (ledEl) {
      try {
        const d = await api("/intel/ledger?hours=48");
        const vc = { "MOVED": "#2be07e", "ABSORBED": "#ffa028", "PRICED BEFORE": "#5aa9ff", "NO REACTION": "#777", "NO DATA": "#555" };
        const sg = (v) => (v > 0 ? "+" : "") + v;
        const st = d.stats || {};
        const learned = Object.entries(st).filter(([k]) => !k.includes("|")).sort((x, y) => y[1].p_moved - x[1].p_moved);
        const rows = d.stories.slice(0, 120).map((x) => {
          const m = x.measured || {}, pa = (m.assets || {})[m.primary] || {};
          const mv = pa.r15 !== undefined ? `${sg(pa.r15.toFixed(2))}% <span class="dim">z${sg(pa.z15)}</span>` : "";
          return `<tr data-story="${x.id}" style="cursor:pointer"><td class="dim">${new Date(x.ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</td>
            <td style="color:${vc[x.verdict] || "#ffa028"};font-weight:700;white-space:nowrap">${esc(x.verdict || (x.status === "classified" ? "MEASURING…" : ""))}</td>
            <td class="dim" style="white-space:nowrap">${esc((x.ntype || "").replace("_", " ").toUpperCase())}</td>
            <td style="white-space:normal;word-break:break-word;overflow-wrap:anywhere">${esc(x.title.slice(0, 150))}<div class="dim" style="font-size:10.5px;white-space:normal">${esc(x.explain || "")}</div></td>
            <td class="dim">${x.n_sources}</td><td>${x.expected !== null ? Math.round(x.expected * 100) + "%" : ""}</td><td style="white-space:nowrap">${mv}</td></tr>`;
        }).join("");
        ledEl.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;padding:6px 10px">
            <div style="flex:3 1 600px;min-width:0"><table class="bb-tbl" style="table-layout:fixed;width:100%"><colgroup><col style="width:46px"><col style="width:112px"><col style="width:110px"><col><col style="width:34px"><col style="width:40px"><col style="width:120px"></colgroup><thead><tr><th>TIME</th><th>VERDICT</th><th>TYPE</th><th>STORY · WHY</th><th>SRC</th><th>EXP</th><th>PRIMARY 15M</th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="dim">no market-relevant stories yet</td></tr>'}</tbody></table></div>
            <div style="flex:1 1 260px"><div class="sub-h">WHAT MOVES MARKETS (LEARNED)</div>
              <table class="bb-tbl"><thead><tr><th>TYPE</th><th>N</th><th>P(MOVE)</th></tr></thead><tbody>${learned.map(([k, v]) => `<tr><td>${esc(k.replace("_", " "))}</td><td class="dim">${v.n}</td><td><b>${Math.round(v.p_moved * 100)}%</b></td></tr>`).join("")}</tbody></table>
              <div class="why-note">MOVED = the main asset moved ≥2.5× its normal move for that hour within 15 min of the first source. EXP = expected chance before the move, learned from past stories of the same type.</div>
              <div class="sub-h" style="margin-top:8px">TELEGRAM CHANNELS</div><div style="display:flex;gap:6px"><input id="led-ch" placeholder="@channel (public)" style="flex:1"><button class="bb-chip" id="led-add">ADD</button></div><div class="dim" id="led-chs" style="font-size:10.5px;margin-top:4px"></div>
            </div></div><div id="led-detail"></div>`;
        api("/intel/channels").then((c) => { const el = $("#led-chs"); if (el) el.textContent = Object.keys(c.channels).join(" · "); }).catch(() => {});
      } catch (e) { ledEl.innerHTML = NX.err(e); }
    }
    const wxEl = $("#mk-wx");
    if (wxEl) {
      try {
        const wr = await api("/world/exchanges").then((v) => ({ v }), (e) => ({ e }));
        if (wr.e) throw wr.e;
        const w = wr.v;
        const now = Date.now() / 1000;
        const off = (tz) => { try { const d = new Date(); return (new Date(d.toLocaleString("en-US", { timeZone: tz })) - new Date(d.toLocaleString("en-US", { timeZone: "UTC" }))) / 60000; } catch (e) { return 0; } };
        const hm = (x) => { const [h, m] = x.split(":").map(Number); return h * 60 + m; };
        const utcNow = new Date().getUTCHours() * 60 + new Date().getUTCMinutes();
        const dur = (sec) => { const m = Math.max(0, Math.round(sec / 60)); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; };
        const SC = { OPEN: "#2be07e", LUNCH: "#ffa028", PRE: "#5aa9ff", CLOSED: "#555" };
        const spark = (v, up) => { if (!v || v.length < 3) return ""; const lo = Math.min(...v), hi = Math.max(...v), W = 90, H = 20; const pts = v.map((x, i) => `${(i / (v.length - 1) * W).toFixed(1)},${(H - (hi > lo ? (x - lo) / (hi - lo) : 0.5) * H).toFixed(1)}`).join(" "); return `<svg width="${W}" height="${H}" style="vertical-align:middle"><polyline fill="none" stroke="${up ? "#2be07e" : "#ff4a4a"}" stroke-width="1.3" points="${pts}"/></svg>`; };
        const pc = (v) => (v === null || v === undefined ? '<span class="dim">—</span>' : `<span class="${v >= 0 ? "up" : "dn"}">${v >= 0 ? "+" : ""}${v.toFixed(2)}%</span>`);
        const regions = ["Asia-Pacific", "Middle East & Africa", "Europe", "Americas"];
        // follow-the-sun: each exchange's session on a 24h UTC strip, with a "now" line
        const lane = (r) => { if (!r.hours || !r.tz) return ""; const [a, b] = r.hours.split("-"); const o = off(r.tz); let s0 = ((hm(a) - o) % 1440 + 1440) % 1440, s1 = ((hm(b) - o) % 1440 + 1440) % 1440; const seg = (x0, x1) => `<i style="position:absolute;left:${x0 / 14.4}%;width:${(x1 - x0) / 14.4}%;top:2px;bottom:2px;background:${SC[r.status] || "#555"};opacity:${r.status === "CLOSED" ? 0.35 : 0.9};border-radius:2px"></i>`; return s1 > s0 ? seg(s0, s1) : seg(s0, 1440) + seg(0, s1); };
        const key = w.exchanges.filter((r) => ["NZX", "ASX", "TSE", "HKEX", "SSE", "KRX", "TWSE", "NSE", "SGX", "TADAWUL", "TASE", "BIST", "JSE", "XETRA", "LSE", "EPA", "B3", "TSX", "NYSE"].includes(r.code));
        const strip = `<div style="position:relative;margin:4px 0 10px;font:10px JetBrains Mono,monospace">
          ${key.map((r) => `<div style="display:flex;align-items:center;height:15px"><span style="width:70px;color:${SC[r.status]}">${esc(r.code)}</span><div style="position:relative;flex:1;height:15px;background:#0b0f18">${lane(r)}</div></div>`).join("")}
          <div style="display:flex;margin-left:70px;color:#666">${[0, 3, 6, 9, 12, 15, 18, 21].map((h) => `<span style="flex:1">${String(h).padStart(2, "0")}:00</span>`).join("")}</div>
          <div style="position:absolute;top:0;bottom:12px;left:calc(70px + (100% - 70px) * ${utcNow / 1440});width:2px;background:#ffa028" title="now (UTC)"></div></div>`;
        const movers = w.exchanges.filter((r) => isNum(r.z)).sort((x, y) => Math.abs(y.z) - Math.abs(x.z)).slice(0, 4);
        const body = regions.map((g) => {
          const rows = w.exchanges.filter((r) => r.region === g).map((r) => {
            const nx = r.next ? (r.status === "PRE" ? `opens in ${dur(r.next - now)}` : `closes in ${dur(r.next - now)}`) : "";
            const zc = isNum(r.z) && Math.abs(r.z) >= 2 ? (r.z > 0 ? "#2be07e" : "#ff4a4a") : "#888";
            return `<tr><td><span style="color:${SC[r.status] || "#555"}">●</span> <b>${esc(r.code)}</b></td><td>${esc(r.exchange)}<div class="dim" style="font-size:10px">${esc(r.city)} · ${esc(r.local || "")} local · ${esc(r.hours || "")}${r.approx ? " ~" : ""}</div></td>
              <td>${esc(r.index)}${r.proxy ? ' <span class="dim">(ETF)</span>' : ""}</td><td style="text-align:right">${isNum(r.last) ? NX.num(r.last, 2) : "—"}</td><td style="text-align:right">${pc(r.pct)}</td>
              <td style="text-align:right;color:${zc};font-weight:${isNum(r.z) && Math.abs(r.z) >= 2 ? 700 : 400}">${isNum(r.z) ? Math.abs(r.z).toFixed(1) + "×" : "—"}</td><td>${spark(r.spark, (r.pct || 0) >= 0)}</td>
              <td style="color:${SC[r.status] || "#555"}">${esc(r.status || "")}<div class="dim" style="font-size:10px">${esc(nx)}</div></td></tr>`;
          }).join("");
          return `<tr><td colspan="8" class="sub-h" style="padding-top:8px">${esc(g.toUpperCase())}</td></tr>${rows}`;
        }).join("");
        wxEl.innerHTML = `<div style="padding:6px 10px"><div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;font:12px JetBrains Mono,monospace"><b style="color:#2be07e">${w.open} OPEN NOW</b><span class="dim">of ${w.exchanges.length} exchanges · updates every minute</span><span>BIGGEST MOVES (vs normal): ${movers.map((r) => `<b>${esc(r.index)}</b> ${pc(r.pct)} <span class="dim">${Math.abs(r.z).toFixed(1)}×</span>`).join(" · ")}</span></div>
          ${strip}
          <table class="bb-tbl"><thead><tr><th>EXCH</th><th>EXCHANGE · LOCAL TIME · SESSION</th><th>INDEX</th><th style="text-align:right">LAST</th><th style="text-align:right">DAY</th><th style="text-align:right">× NORMAL</th><th>TODAY</th><th>STATUS</th></tr></thead><tbody>${body}</tbody></table>
          <div class="why-note">Status and session times come from each exchange's own feed (~ = local hours where the feed has none). × NORMAL = today's move divided by that market's typical daily move over the last year; a Telegram alert goes out when an exchange moves 3× its normal day and at least 2%. (ETF) = no free local index, the US-listed country ETF stands in. No free data: ${esc(w.no_free_data.join(", "))}.</div></div>`;
      } catch (e) { wxEl.innerHTML = NX.err(e); }
    }
    const snEl = $("#mk-sn");
    if (snEl) {
      try {
        const d = await api("/intel/stock-news?hours=72");
        const vc = { "MOVED": "#2be07e", "ABSORBED": "#ffa028", "PRICED BEFORE": "#5aa9ff", "NO REACTION": "#777", "NO DATA": "#555" };
        const mv = (v) => (!v || v.r15 === undefined ? '<span class="dim">waiting</span>' : `<span class="${v.r15 >= 0 ? "up" : "dn"}">${v.r15 >= 0 ? "+" : ""}${v.r15.toFixed(2)}%</span>${v.r60 !== undefined ? ` <span class="dim">· 1h</span> <span class="${v.r60 >= 0 ? "up" : "dn"}">${v.r60 >= 0 ? "+" : ""}${v.r60.toFixed(2)}%</span>` : ""}${isNum(v.z15) ? ` <span class="dim">${Math.abs(v.z15).toFixed(1)}×</span>` : ""}${v.closed ? ' <span class="dim" title="market was closed at the news: measured from the next open, gap included">⏰</span>' : ""}`);
        const rows = d.stories.map((x) => {
          const cos = x.companies.map((c) => `<div><a href="#" data-sym="${esc(c.symbol)}"><b>${esc(c.name)}</b></a> <span class="dim">${esc(c.symbol)} · ${esc(c.exchange || "")}</span><br>${mv(c.moves[c.symbol])}${c.us ? ` <span class="dim">· US ${esc(c.us)}</span> ${mv(c.moves[c.us])}` : ""}</div>`).join("");
          return `<tr><td class="dim" style="white-space:nowrap">${new Date(x.ts * 1000).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</td><td style="min-width:240px">${cos}</td>
            <td style="white-space:normal">${esc(x.title.slice(0, 180))}<div class="dim" style="font-size:10.5px">${esc(x.why || "")}${x.event ? " · " + esc(x.event) : ""}</div></td>
            <td style="text-align:right">${isNum(x.score) ? x.score : ""}</td><td style="color:${vc[x.verdict] || "#ffa028"};font-weight:700;white-space:nowrap">${esc(x.verdict || "MEASURING…")}</td></tr>`;
        }).join("");
        snEl.innerHTML = `<div style="padding:6px 10px"><table class="bb-tbl"><thead><tr><th>TIME</th><th>COMPANY · STOCK · 15 MIN / 1 HOUR AFTER THE NEWS</th><th>HEADLINE · AI READ</th><th style="text-align:right">AI</th><th>VERDICT</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="dim">No company stories yet: they appear as the AI reads new headlines.</td></tr>'}</tbody></table>
          <div class="why-note">The AI picks out every listed company in each story, on any exchange; the home listing (and the US listing when there is one) is found and its reaction measured from the moment of the news. When the home market is closed (a Tokyo stock on news during New York hours) the measurement waits for its open, gap included (⏰). × = move vs that stock's normal move at that hour. Click a company to open it.</div></div>`;
        snEl.querySelectorAll("[data-sym]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); if (window.openSecurityQuery) window.openSecurityQuery(a.dataset.sym); }));
      } catch (e) { snEl.innerHTML = NX.err(e); }
    }
    const hxEl = $("#mk-hist");
    if (hxEl && !hxEl.dataset.done) {
      hxEl.dataset.done = "1";
      const st = { type: "", open: null };
      const pc = (v) => (v === null || v === undefined ? '<span class="dim">—</span>' : `<span class="${v >= 0 ? "up" : "dn"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</span>`);
      const COLS = [["^GSPC", "S&P"], ["^IXIC", "NASDAQ"], ["CL=F", "OIL"], ["GC=F", "GOLD"], ["^VIX", "VIX"], ["^TNX", "10Y"], ["TA35.TA", "TA-35"], ["ILS=X", "USD/ILS"]];
      const draw = async () => {
        try {
          const [h, pb] = await Promise.all([api(`/intel/history${st.type ? "?type=" + encodeURIComponent(st.type) : ""}`), st.type ? api(`/intel/history/playbook?type=${encodeURIComponent(st.type)}`) : Promise.resolve(null)]);
          const nm = (sym) => h.names[sym] || sym;
          const rows = h.events.map((e) => {
            const top = Object.entries(e.react).filter(([sym]) => !COLS.some(([c]) => c === sym)).sort((x, y) => Math.abs(y[1].z0 || 0) - Math.abs(x[1].z0 || 0))[0];
            const det = st.open === e.id ? `<tr><td colspan="${COLS.length + 4}" style="background:#0b0f18;padding:8px 12px"><table class="bb-tbl"><thead><tr><th>ASSET</th><th>REACTION DAY</th><th>DAY</th><th>2 DAYS</th><th>1 WEEK</th><th>1 MONTH</th><th>× NORMAL</th></tr></thead><tbody>${
              Object.entries(e.react).sort((x, y) => Math.abs(y[1].z0 || 0) - Math.abs(x[1].z0 || 0)).map(([sym, x]) => `<tr><td><b>${esc(nm(sym))}</b> <span class="dim">${esc(sym)}</span></td><td class="dim">${esc(x.day)}</td><td>${pc(x.d0)}</td><td>${pc(x.d1)}</td><td>${pc(x.d5)}</td><td>${pc(x.d20)}</td><td>${x.z0 === null || x.z0 === undefined ? "—" : Math.abs(x.z0).toFixed(1) + "×"}</td></tr>`).join("")}</tbody></table></td></tr>` : "";
            return `<tr class="hx-row" data-id="${e.id}" style="cursor:pointer"><td class="dim">${esc(e.date)}</td><td><b>${esc(e.label)}</b> <span class="dim">${esc(e.title)}</span>${e.live ? ' <span class="pill">LIVE</span>' : ""}</td><td class="dim">${esc(e.type.replace("_", " "))}</td>${COLS.map(([sym]) => `<td>${pc((e.react[sym] || {}).d0)}</td>`).join("")}<td>${top ? `${esc(nm(top[0]))} ${pc(top[1].d0)}` : ""}</td></tr>${det}`;
          }).join("");
          const pbHtml = pb && pb.assets.length ? `<div class="sub-h" style="margin-top:4px">PLAYBOOK · ${esc(st.type.replace("_", " ").toUpperCase())} · ${pb.events} EVENTS · AVERAGE FIRST-DAY MOVE</div><div style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px">${pb.assets.slice(0, 14).map((r) => `<span class="bb-chip" title="median ${r.med_d0}% · same direction in ${Math.round(r.same_sign * 100)}% of ${r.n} events">${esc(nm(r.symbol))} ${pc(r.avg_d0)} <span class="dim">${Math.round(r.same_sign * 100)}%</span></span>`).join("")}</div>` : "";
          hxEl.innerHTML = `<div style="padding:6px 10px"><div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px"><button class="bb-chip ${st.type ? "" : "on"}" data-hxt="">ALL ${h.events.length}</button>${h.types.map((t) => `<button class="bb-chip ${st.type === t ? "on" : ""}" data-hxt="${esc(t)}">${esc(t.replace("_", " ").toUpperCase())}</button>`).join("")}</div>${pbHtml}
            <table class="bb-tbl"><thead><tr><th>DATE</th><th>EVENT · click for every asset</th><th>TYPE</th>${COLS.map(([, l]) => `<th>${l}</th>`).join("")}<th>BIGGEST OTHER MOVE</th></tr></thead><tbody>${rows}</tbody></table>
            <div class="why-note">First reacting session vs the last close before the news (after-close and weekend news react on the next session; Tel Aviv may react on a different day from New York). × NORMAL = the move divided by that asset's typical daily move over the prior year. The AI reads this table before judging every new story; live stories that really moved the market are added automatically.</div></div>`;
          hxEl.querySelectorAll("[data-hxt]").forEach((b) => (b.onclick = () => { st.type = b.dataset.hxt; st.open = null; draw(); }));
          hxEl.querySelectorAll(".hx-row").forEach((r) => (r.onclick = () => { st.open = st.open === +r.dataset.id ? null : +r.dataset.id; draw(); }));
        } catch (e) { hxEl.innerHTML = NX.err(e); }
      };
      draw();
    }
    const idEl = $("#mk-ideas");
    if (idEl && (intelTick % 6 === 1 || !idEl.dataset.done)) {
      idEl.dataset.done = "1";
      try {
        const [d, pmo] = await Promise.all([api("/intel/ideas"), api("/intel/pm-opps")]);
        const sg = (v) => (v > 0 ? "+" : "") + v;
        const ins = d.insiders.length ? `<table class="bb-tbl"><thead><tr><th>SYM</th><th>INSIDERS</th><th>BOUGHT</th><th>LAST</th></tr></thead><tbody>${d.insiders.map((x) => `<tr data-sec="${esc(x.symbol)}" style="cursor:pointer"><td><b>${esc(x.symbol)}</b>${x.cluster ? ' <span style="color:#2be07e">CLUSTER</span>' : ""}</td><td>${x.insiders}</td><td>$${Math.round(x.value).toLocaleString()}</td><td class="dim">${esc(x.last)}</td></tr><tr><td colspan="4" class="dim" style="font-size:10.5px">${x.buys.slice(0, 2).map((b) => esc(`${b.owner} (${b.role || "insider"}) ${Math.round(b.shares).toLocaleString()} @ $${b.price}`)).join(" · ")}</td></tr>`).join("")}</tbody></table>` : '<div class="dim" style="padding:6px">No open-market insider purchases in the universe in the last 14 days (scanned daily).</div>';
        const su = d.setups;
        const rates = su ? Object.entries(su.rates).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="dim">${v.n}</td><td class="${dir(v.x20)}">${sg(v.x20)}%</td><td>${v.hit20}%</td><td style="${v.t20 >= 2 ? "color:#2be07e;font-weight:700" : ""}">${v.t20}</td></tr>`).join("") : "";
        const hits = su ? su.hits.slice(0, 12).map((h) => `<tr data-sec="${esc(h.symbol)}" style="cursor:pointer"><td><b>${esc(h.symbol)}</b></td><td>${esc(h.label)}</td><td class="${dir(h.chg)}">${sg(h.chg)}%</td><td>${h.idea ? '<span style="color:#2be07e">IDEA</span>' : '<span class="dim">no edge</span>'}</td></tr>`).join("") : "";
        const setupsHtml = su ? `<div class="sub-h">TODAY</div><table class="bb-tbl"><tbody>${hits || '<tr><td class="dim">no setups triggered today</td></tr>'}</tbody></table><div class="sub-h" style="margin-top:6px">BASE RATE · 20 SESSIONS VS SPY</div><table class="bb-tbl"><thead><tr><th>SETUP</th><th>N</th><th>AVG</th><th>HIT</th><th>T</th></tr></thead><tbody>${rates}</tbody></table><div class="why-note">${esc(su.note)}</div>` : '<div class="dim" style="padding:6px">First scan runs ~15 min after start-up, then daily.</div>';
        const live = (pmo.live || []).filter((o) => o.edge >= 0.05).slice(0, 8);
        const pm = `<div class="sub-h">LIVE · MODEL VS PRICE</div><table class="bb-tbl"><thead><tr><th>MARKET</th><th>SIDE</th><th>PRICE</th><th>MODEL</th><th>EDGE</th></tr></thead><tbody>${live.map((o) => `<tr><td><a href="https://polymarket.com/event/${esc(o.slug)}" target="_blank" rel="noopener noreferrer">${esc(String(o.question || o.title).slice(0, 60))}</a><div class="dim" style="font-size:10px">${o.days}d · vol ${Math.round(o.sigma * 100)}% · liq $${Math.round(o.liquidity / 1000)}k</div></td><td>${o.side}</td><td>${Math.round(o.price * 100)}¢</td><td>${Math.round((o.side === "YES" ? o.fair : 1 - o.fair) * 100)}%</td><td style="color:#2be07e">+${Math.round(o.edge * 100)}pt</td></tr>`).join("") || '<tr><td class="dim" colspan="5">no gaps ≥ 5pt right now</td></tr>'}</tbody></table>
          <div class="sub-h" style="margin-top:6px">RESOLUTION CHECK · DAY ALREADY OVER</div><div id="pm-res" class="dim" style="font-size:11px">loading…</div>
          <div class="sub-h" style="margin-top:6px">PAPER LEDGER</div><div class="why-note">${pmo.n_settled ? `Settled ${pmo.n_settled}: hit rate <b>${Math.round(pmo.hit_rate * 100)}%</b> vs model ${Math.round(pmo.avg_expected * 100)}% · P&L ${sg(pmo.pnl_per_share)} per share` : "No settled opportunities yet; the hit rate appears once markets resolve."} · open ${pmo.open.length}. ${esc(pmo.note)}</div>`;
        setTimeout(() => api("/intel/pm-resolve").then((rr) => { const el = $("#pm-res"); if (!el) return; const rs = rr.results || [];
          el.innerHTML = rs.length ? `<table class="bb-tbl"><tbody>${rs.map((r) => `<tr><td><a href="https://polymarket.com/event/${esc(r.slug)}" target="_blank" rel="noopener noreferrer">${esc(r.event.replace("military action against", "→").replace(" on...?", ""))} · ${esc(r.label)}</a><div class="dim" style="font-size:10px">${esc(r.ai && r.ai.reason ? r.ai.reason : (r.evidence[0] || {}).title || "")}</div></td><td>${Math.round(r.yes * 100)}%</td><td style="color:${/YES/.test(r.verdict) ? "#2be07e" : /NO/.test(r.verdict) ? "#ff4a4a" : "#8b93a3"}"><b>${esc(r.verdict)}</b><div class="dim" style="font-size:10px">${esc(r.confidence)}</div></td><td>${r.edge ? `<span style="color:#ffa028">${esc(r.edge.side)} @ ${Math.round(r.edge.price * 100)}¢</span>` : ""}</td></tr>`).join("")}</tbody></table>` : "No date-specific market waiting for resolution right now (checked every 2 hours).";
        }).catch(() => {}), 0);
        idEl.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;padding:6px 10px"><div style="flex:1 1 320px"><div class="sub-h">INSIDER BUYING · SEC FORM 4 · 14 DAYS</div>${ins}</div><div style="flex:1 1 320px"><div class="sub-h">CHART SETUPS</div>${setupsHtml}</div><div style="flex:1.3 1 380px"><div class="sub-h">POLYMARKET · PRICE MARKETS</div>${pm}</div></div>`;
      } catch (e) { idEl.innerHTML = NX.err(e); }
    }
    const impEl = $("#mk-imp");
    if (impEl) {
      try {
        const d = await api("/intel/impact" + (window.__impEv ? "?event_id=" + window.__impEv : ""));
        if (!d.event) impEl.innerHTML = `<div class="bb-empty"><b>NO EVENT YET</b>${esc(d.note || "")}</div>`;
        else {
          const r = d.reaction || {}, u = d.understanding || {}, pb = d.playbook || {};
          const sg = (v) => (v > 0 ? "+" : "") + v;
          const topic = (u.topics || []).join(" · ") || "UNCLASSIFIED";
          const head = `<div class="why-note" style="margin:6px 10px"><b>${esc(topic)}${u.direction ? " (" + esc(u.direction) + ")" : ""}</b> · ${esc(d.event.title)} · <span class="dim">${NX.ago(d.event.ts) || ""} ago</span><br>${r.ok ? `<b style="color:${r.reacted ? "#ff4a4a" : "#8a8a8a"}">${r.reacted ? "MARKET REACTED" : "NO MEANINGFUL REACTION"}</b> · SPY ${sg(r.spy)}% (z ${sg(r.spy_z)}) · ${r.hours}h` : esc(r.note || "")}</div>`;
          const bask = (r.baskets || []).filter((b) => b.watched);
          const left = `<div style="flex:1 1 360px"><div class="sub-h">THEMES THIS STORY SHOULD MOVE (VS SPY)</div>${bask.length ? `<table class="bb-tbl"><thead><tr><th>THEME</th><th>NOW</th><th>Z</th><th>HISTORY</th><th>VERDICT</th></tr></thead><tbody>${bask.map((b) => { const h = (d.vs_history || []).find((v) => v.theme === b.theme); const ph = (pb.themes || []).find((t) => t.theme === b.theme); return `<tr><td>${esc(b.theme)}</td><td class="${dir(b.excess)}">${sg(b.excess)}%</td><td style="${Math.abs(b.z) >= 1.5 ? "font-weight:700" : "opacity:.6"}">${sg(b.z)}</td><td class="dim">${ph ? sg(ph.avg) + "% (" + ph.hit + "% of " + ph.n + ")" : ""}</td><td>${h ? esc(h.verdict) : ""}</td></tr>`; }).join("")}</tbody></table>` : '<div class="dim" style="padding:6px">no theme mapped for this story</div>'}
            <div class="sub-h" style="margin-top:8px">UNUSUAL MOVERS (|Z| ≥ 2)</div>${(r.unusual || []).length ? (r.unusual || []).map((m) => `<span data-sec="${esc(m.symbol)}" style="cursor:pointer;margin-right:10px" class="${dir(m.excess)}" title="${esc(m.theme || "")}">${esc(m.symbol)} ${sg(m.excess)}%${m.watched ? "" : "★"}</span>`).join("") : '<span class="dim">none</span>'}
            <div class="why-note">Z = move vs SPY in units of the stock's own volatility for the time elapsed. ★ = not a theme this story should move (probably other news).</div></div>`;
          const right = `<div style="flex:1 1 320px"><div class="sub-h">POLYMARKET · SAME TOPIC</div>${(d.polymarket || []).map((e) => `<div style="margin:4px 0"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a> <span class="dim">vol ${NX.big(e.volume24h, "$")} · ${esc((e.tags || []).join(", "))}</span>${e.markets.map((m) => `<div style="font-size:11px"><b>${m.prob}%</b> ${m.change1d === null ? "" : '<span class="' + dir(m.change1d) + '">' + sg(m.change1d) + "pt</span> "}<span class="dim">${esc(String(m.question || "").slice(0, 70))}</span></div>`).join("")}</div>`).join("") || '<div class="dim">no market on this topic</div>'}
            <div class="sub-h" style="margin-top:10px">PLAYBOOK · ${pb.kind ? esc(pb.kind.replace("_", " ").toUpperCase()) + " · " + (pb.events || []).length + " PAST EVENTS" : "NONE FOR THIS TOPIC"}</div>
            <div class="why-note">${(pb.events || []).map((e) => esc(e.day + " " + e.label)).join("<br>")}</div></div>`;
          impEl.innerHTML = head + `<div style="display:flex;gap:14px;flex-wrap:wrap;padding:0 10px">${left}${right}</div>`;
        }
      } catch (e) { impEl.innerHTML = NX.err(e); }
    }
    put("mk-il", il, (d) => NX.newsHtml(d.items, 50));
    put("mk-pm", pm, (d) => d.topics.map((t) => `<div class="sub-h" style="margin:8px 10px 2px">${esc(t.topic)}</div><table class="bb-tbl"><tbody>${t.events.map((e) => `<tr><td style="width:46%"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a><div class="dim" style="font-size:10px">vol 24h ${NX.big(e.volume24h, "$")}${e.ends ? " · ends " + esc(String(e.ends).slice(0, 10)) : ""}</div></td><td>${e.markets.slice(0, 3).map((m) => `<div style="display:flex;align-items:center;gap:8px"><span style="width:38px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600">${m.prob}%</span><span style="flex:0 0 90px;height:5px;background:#1a1a1a"><i style="display:block;height:100%;width:${Math.min(100, m.prob)}%;background:${m.prob >= 50 ? "#ffa028" : "#5aa9ff"}"></i></span><span class="${dir(m.change1d)}" style="width:44px;font-size:11px">${m.change1d === null ? "" : (m.change1d > 0 ? "+" : "") + m.change1d + "pt"}</span><span class="dim" style="font-size:11px">${esc(String(m.question || "").slice(0, 60))}</span></div>`).join("")}</td></tr>`).join("")}</tbody></table>`).join("") + `<div class="why-note">${esc(d.note)}</div>`);
  };
  window.__impRefresh = () => intel();
  NX.intelPanels = intel;
  NX.newsWire = news;

  // BREAKING: a fresh (< 10 min) headline from a central bank, or one naming a market-wide trigger, pops a toast on any page.
  // Uses the server-cached wire (one cheap call every 2 min, skipped while the tab is hidden); the first pass only records.
  const BREAK_RE = /\b(fed|fomc|powell|ecb|lagarde|boj|rate (cut|hike)|emergency|tariffs?|sanctions?|cpi|inflation|payrolls|jobs report|gdp|recession|default|halt(ed)?|crash|plunge|surge|soar|war|missile|ceasefire|opec|bankrupt|downgrade)\b/i;
  const breakSeen = new Set(); let breakFirst = true;
  const breaking = async () => {
    if (document.hidden) return;
    let items; try { items = (await api("/capital/news")).items || []; } catch { return; }
    const now = Date.now() / 1000;
    const hot = items.filter((i) => !breakSeen.has(i.link) && now - i.ts < 600 && (i.tag === "CB" || BREAK_RE.test(i.title)));
    items.forEach((i) => breakSeen.add(i.link));
    if (breakFirst) { breakFirst = false; return; }
    const stack = document.getElementById("toast-stack"); if (!stack) return;
    hot.slice(0, 2).forEach((i) => {
      const t = document.createElement("a");
      t.className = "toast nw-break"; t.href = i.link; t.target = "_blank"; t.rel = "noopener";
      t.innerHTML = `<b>BREAKING · ${esc(i.source)}</b>${esc(i.title)}`;
      stack.appendChild(t); setTimeout(() => t.remove(), 15000);
    });
  };
  setTimeout(() => { breaking(); setInterval(breaking, 120000); }, 8000);

  // ------------------------------------------------------------------ MARKETS
  pages.mkts = () => {
    $("#view-mkts").innerHTML = `
      <div class="bb-kpis" id="mk-kpis"></div>
      <div class="bb-grid">
        ${panel(1, "WORLD EQUITY INDICES", "mk-idx", { cls: "s6 h590" })}
        <div class="bb-col s6">
          ${panel(2, "US FUTURES", "mk-fut", { cls: "s12 h150" })}
          ${panel(3, "US TREASURY YIELDS", "mk-rates", { cls: "s12 h130" })}
          ${panel(4, "SECTOR PERFORMANCE (US, TODAY)", "mk-sect", { cls: "s12 h290" })}
        </div>
        ${panel(5, "FX MAJORS", "mk-fx", { cls: "s4 h380" })}
        ${panel(6, "COMMODITIES", "mk-cm", { cls: "s4 h380" })}
        ${panel(7, "CRYPTO · TOP 15", "mk-cr", { cls: "s4 h380" })}
        ${panel(8, "WORLD EXCHANGES · LIVE · FOLLOW THE SUN", "mk-wx", { cls: "s12" })}
        ${panel(9, "NQ GAMMA · VWAP WORKER · LIVE WALLS, SIGNALS (PAPER 1:2) & BACKTEST", "mk-gx", { cls: "s12 h460" })}
        ${panel(10, "IDEAS · INSIDERS · CHART SETUPS · POLYMARKET PRICE MARKETS (PAPER)", "mk-ideas", { cls: "s12 h420" })}
      </div>`;
    const run = async () => {
      const [idx, fut, rates, fx, cm, sect, cr] = await Promise.allSettled([mon("indices"), mon("futures"), mon("rates"), mon("fx"), mon("commodities"), mon("sectors"), api("/invest/crypto/markets")]);
      const ok = (r) => (r.status === "fulfilled" ? r.value : null);
      const put = (id, r, fn) => { const el = $(`#${id}`); if (!el) return; const v = ok(r); v ? fn(el, v) : (el.innerHTML = NX.err(r.reason)); };
      put("mk-idx", idx, (el, rows) => { NX.monitorTable(el, rows, { cols: ["name", "last", "chg", "pct", "ytd", "spark"], id: "mk-idx" }); setMeta("mk-idx", rows.length, "INDICES"); });
      put("mk-fut", fut, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "pct", "spark"], id: "mk-fut" }));
      put("mk-rates", rates, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "chg", "spark"], id: "mk-rates", dec: 3 }));
      put("mk-sect", sect, (el, rows) => NX.sectorBars(el, rows));
      put("mk-fx", fx, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "pct", "spark"], id: "mk-fx", dec: 4 }));
      put("mk-cm", cm, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "pct", "spark"], id: "mk-cm" }));
      put("mk-cr", cr, (el, r) => {
        const rows = r.rows.slice(0, 15).map((c) => ({ symbol: `${c.symbol}-USD`, name: c.name, price: c.price, pct: c.p24h, p1m: c.p7d, spark: c.spark }));
        NX.monitorTable(el, rows, { cols: ["name", "last", "pct", "spark"], id: "mk-cr" });
      });
      const find = (v, s) => (ok(v) || []).find((x) => x.symbol === s);
      const tiles = [["S&P 500", find(idx, "^GSPC")], ["NASDAQ 100", find(idx, "^NDX")], ["DOW", find(idx, "^DJI")], ["VIX", find(idx, "^VIX")], ["US 10Y", find(rates, "^TNX")], ["USD / ILS", find(fx, "ILS=X")], ["GOLD", find(cm, "GC=F")], ["WTI CRUDE", find(cm, "CL=F")]];
      $("#mk-kpis").innerHTML = tiles.filter((t) => t[1]).map(([l, r]) => `<div class="bb-kpi" data-sec="${esc(r.symbol)}" style="cursor:pointer"><div class="l">${l}</div><div class="v">${NX.px(r.price, l.includes("ILS") ? 4 : l === "US 10Y" ? 3 : undefined)}</div><div class="s"><span class="${dir(r.pct)}">${NX.signed(r.chg, l.includes("ILS") ? 4 : 2)} (${NX.pct(r.pct)})</span></div></div>`).join("");
    };
    const gx = async () => {
      const el = $("#mk-gx"); if (!el) return;
      try {
        let st = await api("/gamma-vwap/status");
        if (!st.state) st = await api("/gamma-vwap/tick");  // worker loop not running on this server: one tick on demand
        const [pp, rep] = await Promise.all([api("/gamma-vwap/paper"), window.__gxRep || api("/gamma-vwap/report")]);
        window.__gxRep = rep;
        const s = st.state || {}, w = s.walls || {}, f = (x) => (x == null ? "—" : NX.px(x)), d = (x) => (x == null || s.nq == null ? "" : `<span class="${dir(s.nq - x)}">${NX.signed(s.nq - x, 1)}</span>`);
        const lag = s.lag_s == null ? "—" : s.lag_s < 90 ? `<span class="up">${s.lag_s}s</span>` : `<span class="dn">${s.lag_s > 7200 ? "market closed" : Math.round(s.lag_s / 60) + " min"}</span>`;
        const row = (k, v) => `<tr><td>${k}</td><td><b>${f(v)}</b></td><td>${d(v)}</td></tr>`;
        const bt = (rep.runs || []).filter((r) => r.grid === 10 && !r.setups.includes("+"));
        const rs = (x) => (x && x.n ? `${x.n} · ${x.win}% · <b class="${dir(x.avg_r)}">${x.avg_r > 0 ? "+" : ""}${x.avg_r}R</b>${x.t_stat != null ? ` · t ${x.t_stat}` : ""}` : "—");
        el.innerHTML = `<div style="display:grid;grid-template-columns:minmax(260px,1fr) minmax(300px,1.3fr) minmax(320px,1.5fr);gap:10px;padding:8px">
          <div><div class="sub-h">LIVE NQ (NQ=F · 1-MIN BARS)</div>
            <div style="font-size:26px;font-weight:700;cursor:pointer" data-sec="NQ=F">${f(s.nq)}</div>
            <div class="why-note">VWAP ${f(s.vwap)} ${s.vs_vwap != null ? `(<span class="${dir(s.vs_vwap)}">${NX.signed(s.vs_vwap, 1)}</span>)` : "(after 09:30 NY)"} · data lag ${lag} · ${s.rth ? "<b>RTH</b>" : "outside RTH"}</div>
            <table class="bb-tbl"><thead><tr><th>LEVEL (NQ PTS)</th><th>PRICE</th><th>NQ −</th></tr></thead><tbody>${row("CALL WALL", w.call_wall)}${row("ZERO GAMMA", w.zero_gamma)}${row("PUT WALL", w.put_wall)}${row("VWAP", s.vwap)}</tbody></table>
            <div class="why-note">${w.regime ? `Regime <b>${w.regime}</b> gamma · QQQ walls ${w.qqq_put_wall}/${w.qqq_call_wall} × ${(+w.ratio).toFixed(2)}` : ""}${st.error ? `<br><span class="dn">${esc(st.error)}</span>` : ""}</div></div>
          <div><div class="sub-h">PAPER AUTO-ENTRIES · REAL WALLS (${pp.n_closed} closed · ${pp.total_r > 0 ? "+" : ""}${pp.total_r}R · $${pp.usd_mnq}/MNQ)</div>
            <table class="bb-tbl"><thead><tr><th>#</th><th>DAY</th><th>SETUP</th><th>SIDE</th><th>ENTRY</th><th>STOP</th><th>TARGET</th><th>R</th></tr></thead><tbody>${pp.trades.slice(0, 12).map((t) => `<tr><td>${t.id}</td><td>${t.day.slice(5)}</td><td>${esc(t.setup)}</td><td class="${t.side === "long" ? "up" : "dn"}">${t.side.toUpperCase()}</td><td>${f(t.entry)}</td><td>${f(t.stop)}</td><td>${f(t.target)}</td><td>${t.status === "open" ? "<b>OPEN</b>" : `<b class="${dir(t.r)}">${t.r > 0 ? "+" : ""}${(+t.r).toFixed(2)}</b>`}</td></tr>`).join("") || `<tr><td colspan="8" class="why-note">No paper trades yet. The worker trades the next NY session on paper (09:45–15:00 ET entries).</td></tr>`}</tbody></table>
            <div class="why-note">${Object.entries(pp.by_setup).map(([k, v]) => `${k}: ${v.n} · ${v.win}% · ${v.avg_r}R`).join(" · ")}${pp.snaps.length ? ` · ${pp.snaps.length} daily wall snapshots recorded` : ""}</div></div>
          <div><div class="sub-h">BACKTEST ${esc(rep.from || "")} → ${esc(rep.to || "")} · 1:2 · PROXY WALLS ($10 QQQ STRIKES) vs CONTROL</div>
            <table class="bb-tbl"><thead><tr><th>SETUP</th><th>LEVELS</th><th>ALL (n · win · avg)</th><th>OUT-OF-SAMPLE 2024+</th></tr></thead><tbody>${bt.map((r) => `<tr><td>${esc(r.setups.toUpperCase())}</td><td>${r.levels.startsWith("gamma") ? "gamma proxy" : "control"}</td><td>${rs(r.all)}</td><td>${rs(r.out_sample)}</td></tr>`).join("")}</tbody></table>
            <div class="why-note">Break-even at 1:2 needs a 33.3% win rate before costs. No free archive of option open interest exists, so the backtest uses round QQQ strikes as wall proxies; the real walls are recorded daily and traded on paper from now on. Paper only, no orders. Not investment advice.</div></div></div>`;
        setMeta("mk-gx", st.state && st.state.rth ? "LIVE" : "", "");
      } catch (e) { el.innerHTML = NX.err(e); }
    };
    every("mkgx", gx, 15000, "mkts");
    every("mkts", async () => { await run(); if (intelTick++ % 3 === 0) intel(); }, 60000);
  };


  // ------------------------------------------------------------------ NEWS (top-level): wire, critical events, what moved
  pages.news = () => {
    $("#view-news").innerHTML = `
      <div class="bb-kpis" id="nw-kpis">${NX.load()}</div>
      <div class="bb-grid">
        ${panel(1, "MARKET WIRE · ALL PUBLISHERS", "mk-news", { cls: "s8 h620" })}
        ${panel(2, "CRITICAL EVENTS · LAST 48H", "mk-ev", { cls: "s4 h620" })}
        ${panel(3, "NEWS ENGINE · WHAT ACTUALLY MOVED THE MARKET", "mk-led", { cls: "s8 h520" })}
        ${panel(4, "ECONOMIC CALENDAR · US · THIS WEEK", "mk-cal", { cls: "s4 h520" })}
        ${panel(5, "STOCKS IN THE NEWS · WORLDWIDE · REACTION OF EACH COMPANY", "mk-sn", { cls: "s12 h460" })}
        ${panel(6, "EVENT IMPACT · WHAT MOVED SINCE THE ALERT", "mk-imp", { cls: "s7 h420" })}
        ${panel(7, "ISRAEL NEWS · LIVE", "mk-il", { cls: "s5 h420" })}
        ${panel(8, "EVENT HISTORY · HOW MARKETS REALLY REACTED (MEASURED FROM PRICES)", "mk-hist", { cls: "s12 h460" })}
      </div>`;
    const kpis = async () => {
      const [ev, cal, led] = await Promise.allSettled([api("/intel/events?hours=48&min_sev=2"), api("/intel/calendar"), api("/intel/ledger?hours=24")]);
      const v = (r) => (r.status === "fulfilled" ? r.value : null);
      const e = v(ev), c = v(cal), l = v(led), now = Date.now() / 1000;
      const evs = (e && (e.events || e.items)) || [], crit = evs.filter((x) => (x.severity || x.sev) >= 3), watch = evs.length - crit.length;
      const stories = (l && (l.stories || l.items)) || [], moved = stories.filter((x) => /MOVED/i.test(x.verdict || "")).length;
      const cev = (c && (c.events || c.items)) || [];
      const next = cev.filter((x) => (x.ts || 0) > now && /high/i.test(x.impact || "")).sort((a, b) => a.ts - b.ts)[0];
      const inT = (ts) => { const m = Math.round((ts - now) / 60); return m < 60 ? `in ${m} min` : m < 1440 ? `in ${Math.floor(m / 60)}h ${m % 60}m` : `in ${Math.floor(m / 1440)}d`; };
      const k = (l1, v1, s1, c1 = "") => `<div class="bb-kpi"><div class="l">${l1}</div><div class="v ${c1}">${v1}</div><div class="s">${s1}</div></div>`;
      $("#nw-kpis").innerHTML = k("CRITICAL · 48H", crit.length, crit[0] ? esc(String(crit[0].title || "").slice(0, 60)) : "nothing cleared the bar", crit.length ? "dn" : "")
        + k("WATCH · 48H", watch, "lower-severity events")
        + k("STORIES · 24H", stories.length, "market-relevant, measured")
        + k("MOVED THE MARKET", moved, stories.length ? `${Math.round((100 * moved) / stories.length)}% of stories` : "—", moved ? "up" : "")
        + k("NEXT HIGH-IMPACT", next ? esc(next.name || next.title || "") : "—", next ? `${inT(next.ts)}` : "none ahead this week");
    };
    every("news", async () => { await Promise.all([news(), intel(), kpis().catch(() => {})]); }, 60000, "news");
  };

  // ------------------------------------------------------------------ STOCKS
  const WATCH_KEY = "nostro.watch", RECENT_KEY = "nostro.recent";
  NX.getList = (k) => { try { return JSON.parse(localStorage.getItem(k) || "[]"); } catch (e) { return []; } };
  NX.setList = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  NX.WATCH_KEY = WATCH_KEY; NX.RECENT_KEY = RECENT_KEY;

  pages.stocks = () => {
    $("#view-stocks").innerHTML = `
      <div class="bb-panel" style="--i:0">
        <div class="bb-ph"><span class="bb-n">1</span>SECURITY SEARCH<span class="sp"></span><span class="meta">any stock, ETF, index, FX pair, commodity or coin worldwide &middot; e.g. AAPL US &middot; VOD LN &middot; TEVA IT &middot; SPX Index &middot; EURUSD Curncy</span></div>
        <div class="sx-search"><input id="st-q" placeholder="TYPE A TICKER OR COMPANY NAME AND PRESS ENTER" autocomplete="off" spellcheck="false" /><button class="bb-go" id="st-go">GO</button></div>
        <div class="sx-results" id="st-results"></div>
        <div class="sx-chips" id="st-quick"></div>
      </div>
      <div class="bb-grid">
        <div class="bb-panel s8 h620" style="--i:1"><div class="bb-ph"><span class="bb-n">2</span>EQUITY MONITOR<span class="sp"></span><span class="meta" id="st-mon-meta"></span>
          ${chips("st-filter", [["all", "ALL"], ["up", "GAINERS"], ["down", "LOSERS"], ["move", "BIG MOVERS"], ["high", "NEAR 52W HIGH"]], "all")}</div><div class="bb-pb" id="st-mon">${NX.load()}</div></div>
        <div class="bb-col s4">
          ${panel(3, "SECTOR PERFORMANCE", "st-sect", { cls: "s12 h290" })}
          ${panel(4, "WATCHLIST · RECENT", "st-watch", { cls: "s12 h320" })}
        </div>
        ${panel(5, "MARKET NEWS", "st-news", { cls: "s12 h300" })}
      </div>`;
    const input = $("#st-q"), results = $("#st-results");
    let seq = 0;
    const doSearch = async () => {
      const q = input.value.trim();
      if (q.length < 1) { results.innerHTML = ""; return; }
      const my = ++seq;
      try {
        // the search matches a name against every word given (not any of them): a query with several qualifiers
        // ("3x leveraged crypto ETF") can match nothing even though a real fund exists, so drop trailing words
        // one at a time until something is found, and say so rather than leaving the box empty.
        let words = q.split(/\s+/), used = q, r = await api(`/invest/search?q=${encodeURIComponent(q)}&limit=10`);
        while (!r.results.length && words.length > 1) {
          words = words.slice(0, -1); used = words.join(" ");
          r = await api(`/invest/search?q=${encodeURIComponent(used)}&limit=10`);
        }
        if (my !== seq) return;
        const note = used !== q ? `<div class="dim" style="padding:4px 2px 8px">No exact match for &ldquo;${esc(q)}&rdquo; — broadened to &ldquo;${esc(used)}&rdquo;.</div>` : "";
        results.innerHTML = note + (r.results.map((x) => `<div class="sx-res" data-sec="${esc(x.symbol)}"><b>${esc(x.symbol)}</b><span class="bbg">${esc(x.bbg)}</span><span>${esc(x.name)}</span><span class="dim">${esc(x.exchange || "")} · ${esc(x.type || "")}</span></div>`).join("") || '<div class="bb-empty">no matches</div>');
      } catch (e) { results.innerHTML = NX.err(e); }
    };
    let t = null;
    input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(doSearch, 250); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { const q = input.value.trim(); if (q) window.openSecurityQuery(q); } });
    $("#st-go").addEventListener("click", () => { const q = input.value.trim(); if (q) window.openSecurityQuery(q); });
    $("#st-quick").innerHTML = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "MSTR", "COIN", "^GSPC", "BTC-USD", "EURUSD=X", "GC=F"].map((s) => `<button class="bb-chip" data-sec="${s}">${s}</button>`).join("");

    let rows = [], filter = "all";
    const draw = () => {
      let r = rows;
      if (filter === "up") r = rows.filter((x) => x.pct > 0).sort((a, b) => b.pct - a.pct);
      else if (filter === "down") r = rows.filter((x) => x.pct < 0).sort((a, b) => a.pct - b.pct);
      else if (filter === "move") r = [...rows].sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct));
      else if (filter === "high") r = rows.filter((x) => x.hi > x.lo).sort((a, b) => (b.price - b.lo) / (b.hi - b.lo) - (a.price - a.lo) / (a.hi - a.lo));
      const box = $("#st-mon");
      if (filter !== "all") box._st = { key: null, asc: false };
      NX.monitorTable(box, r, { cols: ["sym", "last", "chg", "pct", "p1w", "p1m", "ytd", "range", "vol", "spark"], id: "st-mon" });
      setMeta("st-mon", r.length, "STOCKS");
    };
    wireChips("st-filter", (f) => { filter = f; draw(); });
    const run = async () => {
      try { rows = await mon("stocks"); draw(); } catch (e) { $("#st-mon").innerHTML = NX.err(e); }
      try { NX.sectorBars($("#st-sect"), await mon("sectors")); } catch (e) { $("#st-sect").innerHTML = NX.err(e); }
    };
    every("stocks", run, 60000);
    const wl = async () => {
      const w = NX.getList(WATCH_KEY), rc = NX.getList(RECENT_KEY).filter((s) => !w.includes(s)).slice(0, 8);
      const syms = [...w, ...rc];
      const box = $("#st-watch");
      if (!syms.length) { box.innerHTML = `<div class="bb-empty"><b>NOTHING HERE YET</b>Open a security and press ☆ WATCH to pin it.<br>Recently viewed securities also appear here.</div>`; return; }
      try {
        const r = await api(`/invest/quotes?symbols=${encodeURIComponent(syms.join(","))}`);
        const by = Object.fromEntries(r.rows.map((x) => [x.symbol, x]));
        NX.monitorTable(box, syms.map((s) => by[s]).filter(Boolean).map((x) => ({ ...x, name: w.includes(x.symbol) ? "★ " + (x.name || "") : x.name })), { cols: ["sym", "last", "pct", "spark"], id: "st-watch" });
      } catch (e) { box.innerHTML = NX.err(e); }
    };
    every("stocks-watch", wl, 90000, "stocks");
    api("/capital/news").then((r) => ($("#st-news").innerHTML = NX.newsHtml(r.items, 50))).catch((e) => ($("#st-news").innerHTML = NX.err(e)));
  };

  // ------------------------------------------------------------------ CRYPTO
  const CCOLS = {
    rank: (r) => `<span class="dim">${r.rank ?? ""}</span>`,
  };
  pages.crypto = () => {
    $("#view-crypto").innerHTML = `
      <div class="bb-kpis" id="cr-kpis"></div>
      <div class="bb-grid">
        <div class="bb-panel s8 h640" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>COIN MONITOR<span class="sp"></span><span class="meta" id="cr-mon-meta"></span>
          ${chips("cr-filter", [["all", "TOP 100"], ["top20", "TOP 20"], ["up", "GAINERS 24H"], ["down", "LOSERS 24H"], ["ath", "NEAR ATH"]], "all")}</div><div class="bb-pb" id="cr-mon">${NX.load()}</div></div>
        <div class="bb-col s4">
          ${panel(2, "MOVERS · 24H", "cr-mov", { cls: "s12 h300" })}
          ${panel(3, "CRYPTO NEWS", "cr-news", { cls: "s12 h330" })}
        </div>
      </div>`;
    let rows = [], filter = "all", sortKey = "rank", asc = true;
    const th = (k, l, r = true) => `<th class="${r ? "r" : ""} sortable ${sortKey === k ? "sorted" : ""}" data-k="${k}">${l}${sortKey === k ? (asc ? " ▲" : " ▼") : ""}</th>`;
    const draw = () => {
      let r = [...rows];
      if (filter === "top20") r = r.slice(0, 20);
      else if (filter === "up") r = r.filter((c) => c.p24h > 0).sort((a, b) => b.p24h - a.p24h);
      else if (filter === "down") r = r.filter((c) => c.p24h < 0).sort((a, b) => a.p24h - b.p24h);
      else if (filter === "ath") r = r.filter((c) => c.ath_pct > -25).sort((a, b) => b.ath_pct - a.ath_pct);
      if (filter === "all" || filter === "top20") {
        const g = { rank: (c) => c.rank, price: (c) => c.price, p1h: (c) => c.p1h, p24h: (c) => c.p24h, p7d: (c) => c.p7d, p30d: (c) => c.p30d, mcap: (c) => c.mcap, vol: (c) => c.vol }[sortKey];
        r.sort((a, b) => ((g(a) ?? -Infinity) > (g(b) ?? -Infinity) ? 1 : -1) * (asc ? 1 : -1));
      }
      const box = $("#cr-mon");
      box.innerHTML = `<table class="bb-tbl"><thead><tr>${th("rank", "#", false)}<th>COIN</th>${th("price", "PRICE")}${th("p1h", "1H")}${th("p24h", "24H")}${th("p7d", "7D")}${th("p30d", "30D")}${th("mcap", "MKT CAP")}${th("vol", "24H VOL")}<th>7D</th></tr></thead><tbody>${r.map((c) =>
        `<tr class="click" data-sec="${esc(c.symbol)}-USD"><td class="dim">${c.rank ?? ""}</td><td><span class="sym">${esc(c.symbol)}</span> <span class="dim">${esc(c.name)}</span></td><td class="r" data-k="${esc(c.symbol)}">${NX.px(c.price)}</td>
        <td class="r ${dir(c.p1h)}">${NX.pct(c.p1h, true, 1)}</td><td class="r ${dir(c.p24h)}">${NX.pct(c.p24h)}</td><td class="r ${dir(c.p7d)}">${NX.pct(c.p7d, true, 1)}</td><td class="r ${dir(c.p30d)}">${NX.pct(c.p30d, true, 1)}</td>
        <td class="r">${NX.big(c.mcap, "$")}</td><td class="r dim">${NX.big(c.vol, "$")}</td><td>${NX.spark(c.spark, c.p7d >= 0, 70, 16)}</td></tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("th.sortable").forEach((h) => h.addEventListener("click", () => { asc = sortKey === h.dataset.k ? !asc : h.dataset.k === "rank"; sortKey = h.dataset.k; draw(); }));
      r.forEach((c) => NX.flash(box.querySelector(`td[data-k="${CSS.escape(c.symbol)}"]`), `cr:${c.symbol}`, c.price));
      setMeta("cr-mon", r.length, "COINS");
    };
    wireChips("cr-filter", (f) => { filter = f; draw(); });
    const run = async () => {
      try {
        const r = await api("/invest/crypto/markets");
        rows = r.rows; draw();
        const up = [...rows].sort((a, b) => b.p24h - a.p24h);
        const line = (c) => `<tr class="click" data-sec="${esc(c.symbol)}-USD"><td class="sym">${esc(c.symbol)}</td><td class="r">${NX.px(c.price)}</td><td class="r ${dir(c.p24h)}">${NX.pct(c.p24h)}</td></tr>`;
        $("#cr-mov").innerHTML = `<div class="dim mv-h">TOP GAINERS</div><table class="bb-tbl"><tbody>${up.slice(0, 6).map(line).join("")}</tbody></table><div class="dim mv-h">TOP LOSERS</div><table class="bb-tbl"><tbody>${up.slice(-6).reverse().map(line).join("")}</tbody></table>`;
      } catch (e) { $("#cr-mon").innerHTML = NX.err(e); }
      try {
        const g = await api("/invest/crypto/global");
        $("#cr-kpis").innerHTML = [["TOTAL MARKET CAP", NX.big(g.mcap, "$"), `<span class="${dir(g.chg24)}">${NX.pct(g.chg24)}</span> 24H`], ["24H VOLUME", NX.big(g.vol, "$"), "all exchanges"], ["BTC DOMINANCE", `${NX.num(g.btc_dom, 1)}%`, "of total market cap"], ["ETH DOMINANCE", `${NX.num(g.eth_dom, 1)}%`, "of total market cap"], ["ACTIVE COINS", NX.num(g.coins, 0), "tracked by CoinGecko"]]
          .map(([l, v, s]) => `<div class="bb-kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
      } catch (e) { /* KPIs are decoration */ }
    };
    every("crypto", run, 60000);
    api("/capital/news").then((r) => ($("#cr-news").innerHTML = NX.newsHtml(r.items.filter((i) => i.tag === "CRYPTO"), 40))).catch((e) => ($("#cr-news").innerHTML = NX.err(e)));
  };

  // ------------------------------------------------------------------ ETFs & FUNDS
  const ETF_CAT = { broad: "SPY QQQ DIA IWM VTI VEA EEM".split(" "), sector: "XLK XLF XLE XLV XLY XLP XLI XLU XLB XLRE XLC".split(" "), bonds: "TLT IEF HYG LQD".split(" "), commodity: "GLD SLV USO UNG".split(" "), thematic: "SMH ARKK IBIT ETHA VNQ".split(" ") };
  pages.funds = () => {
    $("#view-funds").innerHTML = `<div class="bb-grid">
      <div class="bb-panel s12 h700" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>ETF & FUND MONITOR<span class="sp"></span><span class="meta" id="fd-mon-meta"></span>
        ${chips("fd-filter", [["all", "ALL"], ["broad", "BROAD MARKET"], ["sector", "SECTORS"], ["bonds", "BONDS"], ["commodity", "COMMODITY"], ["thematic", "THEMATIC & CRYPTO"]], "all")}</div><div class="bb-pb" id="fd-mon">${NX.load()}</div></div></div>`;
    let rows = [], cat = "all";
    const draw = () => {
      const r = cat === "all" ? rows : rows.filter((x) => ETF_CAT[cat].includes(x.symbol));
      NX.monitorTable($("#fd-mon"), r, { cols: ["sym", "last", "chg", "pct", "p1w", "p1m", "ytd", "range", "vol", "spark"], id: "fd-mon" });
      setMeta("fd-mon", r.length, "FUNDS");
    };
    wireChips("fd-filter", (f) => { cat = f; draw(); });
    every("funds", async () => { try { rows = await mon("etfs"); draw(); } catch (e) { $("#fd-mon").innerHTML = NX.err(e); } }, 60000);
  };

  // ------------------------------------------------------------------ FX & COMMODITIES
  pages.fxc = () => {
    $("#view-fxc").innerHTML = `<div class="bb-grid">
      ${panel(1, "FX · MAJORS & ILS", "fx-fx", { cls: "s6 h460" })}
      ${panel(2, "COMMODITIES", "fx-cm", { cls: "s6 h460" })}
      ${panel(3, "US TREASURY YIELDS", "fx-rt", { cls: "s6 h200" })}
      ${panel(4, "US DOLLAR & PRECIOUS METALS SNAPSHOT", "fx-snap", { cls: "s6 h200" })}
    </div>`;
    every("fxc", async () => {
      const [fx, cm, rt] = await Promise.allSettled([mon("fx"), mon("commodities"), mon("rates")]);
      const put = (id, r, fn) => { const el = $(`#${id}`); r.status === "fulfilled" ? fn(el, r.value) : (el.innerHTML = NX.err(r.reason)); };
      put("fx-fx", fx, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "chg", "pct", "p1w", "p1m", "ytd", "range", "spark"], id: "fx-fx", dec: 4 }));
      put("fx-cm", cm, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "chg", "pct", "p1w", "p1m", "ytd", "range", "spark"], id: "fx-cm" }));
      put("fx-rt", rt, (el, rows) => NX.monitorTable(el, rows, { cols: ["name", "last", "chg", "p1m", "spark"], id: "fx-rt", dec: 3 }));
      if (fx.status === "fulfilled" && cm.status === "fulfilled") {
        const pick = (arr, s) => arr.find((x) => x.symbol === s);
        const items = [["US DOLLAR INDEX", pick(fx.value, "DX-Y.NYB"), 2], ["USD / ILS", pick(fx.value, "ILS=X"), 4], ["GOLD", pick(cm.value, "GC=F"), 2], ["SILVER", pick(cm.value, "SI=F"), 2]].filter((i) => i[1]);
        $("#fx-snap").innerHTML = `<div class="snap">${items.map(([l, r, d]) => `<div class="snap-c" data-sec="${esc(r.symbol)}"><div class="l">${l}</div><div class="v">${NX.px(r.price, d)}</div><div class="s ${dir(r.pct)}">${NX.pct(r.pct)}</div>${NX.spark(r.spark, r.pct >= 0, 120, 26)}</div>`).join("")}</div>`;
      }
    }, 60000);
  };

  // ------------------------------------------------------------------ ECONOMY
  pages.econ = () => {
    $("#view-econ").innerHTML = `
      <div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>MACRO DASHBOARD<span class="sp"></span><span class="meta">source: FRED &middot; St. Louis Fed &middot; click a tile to chart it</span></div>
        <div class="bb-pb econ-tiles" id="ec-tiles">${NX.load()}</div></div>
      <div class="bb-panel" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span><span id="ec-title">US 10-YEAR YIELD (%)</span><span class="sp"></span><span class="meta" id="ec-meta"></span>
        ${chips("ec-years", [["1", "1Y"], ["3", "3Y"], ["5", "5Y"], ["10", "10Y"], ["20", "20Y"], ["60", "MAX"]], "10")}</div>
        <div class="bb-pad"><div id="ec-chart" style="height:320px"></div></div></div>`;
    let series = "DGS10", years = 10, rows = [];
    const chart = async () => {
      const r = rows.find((x) => x.id === series);
      $("#ec-title").textContent = r ? `${r.label.toUpperCase()} (${r.unit})` : series;
      try {
        const d = await api(`/invest/macro/series?id=${series}&years=${years}`);
        NX.lineChart($("#ec-chart"), d.points, { height: 320, fmt: (v) => NX.num(v, Math.abs(v) >= 1000 ? 0 : 2) });
        $("#ec-meta").textContent = `${d.points.length} observations`;
      } catch (e) { $("#ec-chart").innerHTML = NX.err(e); }
    };
    wireChips("ec-years", (y) => { years = Number(y); chart(); });
    every("econ", async () => {
      try {
        rows = (await api("/invest/macro")).rows;
        $("#ec-tiles").innerHTML = rows.map((r) => {
          const chg = r.value - r.prev, yoy = r.year_ago != null ? r.value - r.year_ago : null;
          return `<div class="econ-t ${r.id === series ? "on" : ""}" data-id="${r.id}"><div class="l">${esc(r.label.toUpperCase())}</div><div class="v">${NX.num(r.value, Math.abs(r.value) >= 1000 ? 0 : 2)}<small> ${esc(r.unit)}</small></div>
            <div class="s"><span class="${dir(chg)}">${NX.signed(chg, 2)}</span> vs prior · <span class="dim">${esc(r.date)}</span></div>${NX.spark(r.spark, chg >= 0, 130, 24)}</div>`;
        }).join("");
        document.querySelectorAll("#ec-tiles .econ-t").forEach((t) => t.addEventListener("click", () => {
          series = t.dataset.id;
          document.querySelectorAll("#ec-tiles .econ-t").forEach((x) => x.classList.toggle("on", x === t));
          chart();
        }));
        if (!$("#ec-chart").firstChild || $("#ec-chart").querySelector(".bb-load")) chart();
      } catch (e) { $("#ec-tiles").innerHTML = NX.err(e); }
    }, 600000);
    chart();
  };

  // ------------------------------------------------------------------ router: build a page the first time its tab opens
  window.onInvestTab = function onInvestTab(tab) {
    if (pages[tab] && !built[tab]) { built[tab] = true; pages[tab](); }
    else if (pages[tab] && built[tab] && tab !== "security") { /* pollers resume through their own visibility checks */ }
    if (tab === "security" && window.initSecurityTab) window.initSecurityTab();
  };
})();
