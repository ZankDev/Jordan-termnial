/* WHY? — "Every move has a cause. Clue. Rumour. Headline."  Mark any chart, get the reasons.

   Loaded right after lightweight-charts (index.html <head>): it wraps LightweightCharts.createChart, so EVERY chart in the
   terminal is registered without touching its page code. On any registered chart:
     SHIFT + drag   select a time range  -> WHY? panel for that range
     ALT + click    one candle          -> WHY? panel for that bar (± one bar)
     WHY? button    (top-right of the chart on hover) -> the visible range
   Command bar: "WHY NVDA", "WHY CL=F 3d", "WHY BTC-USD 6h". Elements with data-sym: ALT+click -> last 24 h.
   The symbol comes from the closest [data-sym], the open SECURITY page, or the panel's input.
   Data: /api/cause (app/world/cause.py). Cause markers are drawn on the panel's chart and as an overlay on the source chart. */
(function () {
  "use strict";
  let LWC = window.LightweightCharts;
  const REG = []; // { chart, el, series: [] }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BK = { clue: ["CLUE", "#5aa9ff", "C"], rumour: ["RUMOUR", "#ff3bd0", "R"], headline: ["HEADLINE", "#ffa028", "H"] };
  const fmtT = (t) => new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  const rel = (m) => (Math.abs(m) < 60 ? `${m >= 0 ? "+" : "−"}${Math.abs(m)}m` : Math.abs(m) < 2880 ? `${m >= 0 ? "+" : "−"}${(Math.abs(m) / 60).toFixed(1)}h` : `${m >= 0 ? "+" : "−"}${(Math.abs(m) / 1440).toFixed(1)}d`);
  const toUnix = (t) => (typeof t === "number" ? t : t && t.year ? Date.UTC(t.year, t.month - 1, t.day) / 1000 : typeof t === "string" ? Date.parse(t) / 1000 : null);

  // ------------------------------------------------------------------ chart registry (wrap createChart)
  if (LWC && LWC.createChart && !LWC.__whyWrapped) {
    const orig = LWC.createChart, LightweightChartsNS = LWC;
    // the UMD namespace object is frozen: publish a copy whose createChart registers every chart
    const wrapped = function (el, opts) {
      const chart = orig.call(LightweightChartsNS, el, opts);
      const node = typeof el === "string" ? document.getElementById(el) : el;
      const rec = { chart, el: node, series: [] };
      ["addCandlestickSeries", "addLineSeries", "addAreaSeries", "addBarSeries", "addHistogramSeries", "addBaselineSeries"].forEach((m) => {
        if (typeof chart[m] !== "function") return;
        const o = chart[m].bind(chart);
        chart[m] = (...a) => { const s = o(...a); rec.series.push(s); return s; };
      });
      const rm = chart.remove && chart.remove.bind(chart);
      if (rm) chart.remove = () => { const i = REG.indexOf(rec); if (i >= 0) REG.splice(i, 1); return rm(); };
      REG.push(rec);
      try { attach(rec); } catch (e) { /* never break a page's chart */ }
      return chart;
    };
    try {
      const copy = Object.assign(Object.create(null), LWC, { createChart: wrapped, __whyWrapped: true });
      window.LightweightCharts = copy;
      if (window.LightweightCharts === copy) LWC = copy;
    } catch (e) { console.warn("WHY?: could not register charts", e); }
  }

  function guessSym(el) {
    const d = el && el.closest && el.closest("[data-sym]");
    if (d) return d.dataset.sym;
    if (window.NX && typeof NX.curSecurity === "function" && document.querySelector("#view-security.active")) return NX.curSecurity();
    return (W.sym || "");
  }

  function attach(rec) {
    const el = rec.el; if (!el || el.__why) return; el.__why = true;
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    const btn = document.createElement("button");
    btn.className = "why-btn"; btn.type = "button"; btn.textContent = "WHY?"; btn.title = "Why did it move? (SHIFT+drag a range, ALT+click a candle)";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const r = rec.chart.timeScale().getVisibleRange();
      const t0 = r && toUnix(r.from), t1 = r && toUnix(r.to);
      W.open(guessSym(el), t0 || Date.now() / 1000 - 86400, t1 || Date.now() / 1000, rec);
    });
    el.appendChild(btn);
    let sel = null, x0 = 0;
    const box = () => { const b = document.createElement("div"); b.className = "why-sel"; el.appendChild(b); return b; };
    el.addEventListener("mousedown", (e) => {
      if (!e.shiftKey || e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      const r = el.getBoundingClientRect(); x0 = e.clientX - r.left; sel = box(); sel.style.left = `${x0}px`; sel.style.width = "0px";
    }, true);
    window.addEventListener("mousemove", (e) => {
      if (!sel) return;
      const r = el.getBoundingClientRect(), x = Math.max(0, Math.min(r.width, e.clientX - r.left));
      sel.style.left = `${Math.min(x0, x)}px`; sel.style.width = `${Math.abs(x - x0)}px`;
    });
    window.addEventListener("mouseup", (e) => {
      if (!sel) return;
      const r = el.getBoundingClientRect(), x = Math.max(0, Math.min(r.width, e.clientX - r.left));
      const ts = rec.chart.timeScale();
      let a = toUnix(ts.coordinateToTime(Math.min(x0, x))), b = toUnix(ts.coordinateToTime(Math.max(x0, x)));
      setTimeout(() => sel && sel.remove(), 900); const s = sel; sel = null; s.classList.add("done");
      if (a && b && Math.abs(x - x0) > 4) W.open(guessSym(el), a, b, rec);
    });
    el.addEventListener("click", (e) => {
      if (!e.altKey) return;
      e.preventDefault(); e.stopPropagation();
      const r = el.getBoundingClientRect(), ts = rec.chart.timeScale();
      const t = toUnix(ts.coordinateToTime(e.clientX - r.left)); if (!t) return;
      const t2 = toUnix(ts.coordinateToTime(e.clientX - r.left + 12)) || t + 3600, bar = Math.max(300, Math.abs(t2 - t));
      W.open(guessSym(el), t - bar, t + bar, rec);
    }, true);
  }

  // ALT+click on any ticker element -> last 24 h
  document.addEventListener("click", (e) => {
    if (!e.altKey) return;
    const d = e.target.closest && e.target.closest("[data-sym]");
    if (!d || d.querySelector(".why-btn")) return;
    e.preventDefault(); e.stopPropagation();
    W.open(d.dataset.sym, Date.now() / 1000 - 86400, Date.now() / 1000);
  }, true);

  // command bar: WHY <SYM> [N h|d|w]
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target || e.target.id !== "bb-cmd") return;
    const m = /^\s*WHY\s+([A-Za-z0-9.=^\-]{1,15})(?:\s+(\d+)\s*([hdw]))?\s*$/i.exec(e.target.value);
    if (!m) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const n = Number(m[2] || 1), u = { h: 3600, d: 86400, w: 604800 }[(m[3] || "d").toLowerCase()];
    e.target.value = ""; e.target.blur();
    W.open(m[1].toUpperCase(), Date.now() / 1000 - n * u, Date.now() / 1000);
  }, true);

  // ------------------------------------------------------------------ the panel
  const W = (window.WHY = { sym: "", open: null, last: null });
  let P = null, chart = null, series = null, src = null, req = 0;

  function panel() {
    if (P) return P;
    P = document.createElement("aside");
    P.id = "why-panel"; P.className = "why-panel"; P.hidden = true;
    P.innerHTML = `<div class="why-h"><b>WHY?</b><span>Every move has a cause. <i>Clue.</i> <i>Rumour.</i> <i>Headline.</i></span><button class="why-x" title="close (Esc)">✕</button></div>
      <form class="why-f"><input name="sym" placeholder="SYMBOL" spellcheck="false" autocomplete="off"/><input name="from" type="datetime-local"/><input name="to" type="datetime-local"/><button>GO</button></form>
      <div class="why-b" id="why-body"></div>`;
    document.body.appendChild(P);
    P.querySelector(".why-x").addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !P.hidden) close(); });
    P.querySelector(".why-f").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target, a = Date.parse(f.from.value) / 1000, b = Date.parse(f.to.value) / 1000;
      if (f.sym.value.trim()) W.open(f.sym.value.trim().toUpperCase(), a || Date.now() / 1000 - 86400, b || Date.now() / 1000);
    });
    P.addEventListener("click", (e) => {
      const ai = e.target.closest("#why-ai"); if (ai) { summary(); return; }
      const it = e.target.closest("[data-t]"); if (it && chart && !e.target.closest("a")) chart.timeScale().scrollToPosition(0, false);
    });
    return P;
  }
  function close() { if (P) P.hidden = true; clearOverlay(); }
  const local = (t) => { const d = new Date(t * 1000 - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 16); };

  W.open = async (sym, t0, t1, rec) => {
    panel(); P.hidden = false;
    const f = P.querySelector(".why-f");
    sym = (sym || W.sym || "").toUpperCase(); W.sym = sym;
    f.sym.value = sym; f.from.value = local(t0); f.to.value = local(t1);
    src = rec || null;
    const body = P.querySelector("#why-body");
    if (!sym) { body.innerHTML = `<div class="why-empty">Type a symbol above (e.g. NVDA, CL=F, BTC-USD) — or SHIFT+drag on any chart.</div>`; f.sym.focus(); return; }
    const my = ++req;
    body.innerHTML = `<div class="why-load"><i></i>READING THE TAPE · ${esc(sym)} · ${fmtT(t0)} → ${fmtT(t1)}<em>GDELT · Google News · Yahoo · Reddit · SPIDER memory · calendar · world events · related assets (8 s budget per source)</em></div>`;
    try {
      const d = await api(`/cause?symbol=${encodeURIComponent(sym)}&from=${Math.round(t0)}&to=${Math.round(t1)}`);
      if (my !== req) return;
      W.last = d; render(d);
    } catch (e) { if (my === req) body.innerHTML = `<div class="why-empty"><b>NO ANSWER</b> ${esc(e.message)}</div>`; }
  };

  function item(x) {
    const [, c, k] = BK[x.bucket] || BK.headline;
    return `<div class="why-i" data-t="${x.ts}"><div class="why-i1"><span class="why-k" style="--c:${c}">${k}</span><em>${fmtT(x.ts)} · ${rel(x.dt_min)} vs onset</em><span class="why-src">${esc(x.source)}</span></div>
      <a href="${esc(x.link || "#")}" target="_blank" rel="noopener">${esc(x.title)}</a>
      <div class="why-cf"><i style="width:${x.conf}%;background:${c}"></i><b>${x.conf}</b><span>${esc(x.via)}${x.exposed ? " · exposed" : ""}</span></div></div>`;
  }

  function render(d) {
    const body = P.querySelector("#why-body"), mv = d.move || {}, up = (mv.pct || 0) >= 0;
    const st = Object.entries(d.status).map(([k, v]) => `<span class="${v.ok ? "ok" : "bad"}" title="${esc(v.ok ? `${v.n} items` : v.err)}">${esc(k.toUpperCase().replace("_", " "))}</span>`).join("");
    const col = (b) => `<div class="why-col"><div class="why-ch" style="--c:${BK[b][1]}">${BK[b][0]} <i>${d.buckets[b].length}</i><em>${{ clue: "early, weak signals before the onset", rumour: "social / unconfirmed", headline: "confirmed news at / after the onset" }[b]}</em></div>
      ${d.buckets[b].length ? d.buckets[b].map(item).join("") : `<div class="why-none">nothing found</div>`}</div>`;
    body.innerHTML = `<div class="why-top"><div><b>${esc(d.symbol)}</b> <span>${esc(d.name)}${d.sector ? ` · ${esc(d.sector)}` : ""}</span></div>
        <div class="why-mv ${up ? "up" : "dn"}">${up ? "▲" : "▼"} ${(mv.pct || 0).toFixed(2)}%<em>onset ${mv.onset ? fmtT(mv.onset) : "—"} · range ${(mv.range_pct || 0).toFixed(2)}%</em></div></div>
      <div class="why-chart" id="why-chart"></div>
      <div class="why-leg"><span style="--c:#5aa9ff">C clue</span><span style="--c:#ff3bd0">R rumour</span><span style="--c:#ffa028">H headline</span><span style="--c:#9fe0ff">W world</span><span style="--c:#2be07e">M macro</span><span class="sp"></span><button id="why-ai" ${d.ai_available ? "" : "disabled title='claude CLI not found on the server'"}>AI SUMMARY</button></div>
      <div class="why-ai" id="why-aitext" hidden></div>
      <div class="why-cols">${col("clue")}${col("rumour")}${col("headline")}</div>
      <div class="why-grid">
        <div><div class="why-sh">RELATED ASSETS · SAME WINDOW</div><table class="why-rt">${d.related.map((r) => `<tr><td>${esc(r.sym)}</td><td>${esc(r.name)}</td><td class="${r.pct >= 0 ? "up" : "dn"}">${r.pct >= 0 ? "+" : ""}${r.pct.toFixed(2)}%</td></tr>`).join("") || "<tr><td>—</td></tr>"}</table></div>
        <div><div class="why-sh">WORLD EVENTS · MACRO</div>${[...d.world, ...d.macro].map((x) => `<div class="why-w"><b style="color:${x.via === "World" ? "#9fe0ff" : "#2be07e"}">${x.via === "World" ? "W" : "M"}</b> ${esc(x.title)}<em>${fmtT(x.ts)}${x.exposed ? " · EXPOSED" : ""}</em></div>`).join("") || `<div class="why-none">none in the window</div>`}</div>
      </div>
      <div class="why-st">SOURCES ${st}<span class="sp"></span>${d.n_items} items scored · confidence = 45% timing + 35% relevance + 20% source</div>`;
    drawChart(d); drawOverlay(d);
  }

  function markers(d) {
    const all = [...d.buckets.clue, ...d.buckets.rumour, ...d.buckets.headline, ...d.world, ...d.macro]
      .filter((x) => x.ts >= d.bars[0][0] && x.ts <= d.bars[d.bars.length - 1][0] + 3600).sort((a, b) => b.conf - a.conf).slice(0, 18);
    const snap = (t) => { let best = d.bars[0][0]; for (const b of d.bars) { if (b[0] <= t) best = b[0]; else break; } return best; };
    const C = { clue: "#5aa9ff", rumour: "#ff3bd0", headline: "#ffa028" };
    return all.map((x) => ({ time: snap(x.ts), position: x.bucket === "headline" ? "aboveBar" : "belowBar", color: x.via === "World" ? "#9fe0ff" : x.via === "Calendar" ? "#2be07e" : C[x.bucket],
      shape: x.bucket === "headline" ? "arrowDown" : "circle", text: x.via === "World" ? "W" : x.via === "Calendar" ? "M" : BK[x.bucket][2] })).sort((a, b) => a.time - b.time);
  }

  function drawChart(d) {
    const el = P.querySelector("#why-chart");
    if (chart) { try { chart.remove(); } catch (e) { /* gone */ } chart = null; }
    if (!LWC || !d.bars.length) { el.innerHTML = `<div class="why-none">no chart</div>`; return; }
    chart = LWC.createChart(el, { height: 210, layout: { background: { color: "#000" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, monospace", fontSize: 10 },
      grid: { vertLines: { color: "#111" }, horzLines: { color: "#111" } }, timeScale: { timeVisible: d.interval !== "1d", secondsVisible: false, borderColor: "#262626" },
      rightPriceScale: { borderColor: "#262626" }, crosshair: { mode: 0 } });
    const el2 = el.querySelector(".why-btn"); if (el2) el2.remove(); // the panel chart does not need its own WHY button
    series = chart.addCandlestickSeries({ upColor: "#2be07e", downColor: "#ff4a4a", borderVisible: false, wickUpColor: "#2be07e", wickDownColor: "#ff4a4a" });
    series.setData(d.bars.map((b) => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] })));
    series.setMarkers(markers(d));
    // shade the selected window
    const shade = document.createElement("div"); shade.className = "why-shade"; el.appendChild(shade);
    const place = () => { const ts = chart.timeScale(), a = ts.timeToCoordinate(d.from) ?? 0, b = ts.timeToCoordinate(d.to) ?? el.clientWidth - 60; shade.style.left = `${Math.min(a, b)}px`; shade.style.width = `${Math.abs(b - a)}px`; };
    chart.timeScale().fitContent(); setTimeout(place, 30); chart.timeScale().subscribeVisibleTimeRangeChange(place);
  }

  // non-destructive markers on the chart the user marked: a thin overlay strip, repositioned on scroll / zoom
  let ov = null, ovSub = null;
  function clearOverlay() { if (ov) ov.remove(); ov = null; if (ovSub && src) { try { src.chart.timeScale().unsubscribeVisibleTimeRangeChange(ovSub); } catch (e) { /* gone */ } } ovSub = null; }
  function drawOverlay(d) {
    clearOverlay();
    if (!src || !src.el || !document.body.contains(src.el)) return;
    ov = document.createElement("div"); ov.className = "why-ov"; src.el.appendChild(ov);
    const items = [...d.buckets.clue, ...d.buckets.rumour, ...d.buckets.headline].sort((a, b) => b.conf - a.conf).slice(0, 12);
    const C = { clue: "#5aa9ff", rumour: "#ff3bd0", headline: "#ffa028" };
    const place = () => {
      const ts = src.chart.timeScale();
      const xy = (t) => { // the source chart may key bars by unix seconds, "YYYY-MM-DD" or a business-day object
        const d = new Date(t * 1000), iso = d.toISOString().slice(0, 10);
        for (const k of [t, iso, { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() }]) { try { const v = ts.timeToCoordinate(k); if (v != null) return v; } catch (e) { /* wrong key type */ } }
        return null;
      };
      ov.innerHTML = items.map((x) => { const xp = xy(x.ts); return xp == null ? "" : `<i style="left:${xp}px;--c:${C[x.bucket]}" title="${esc(fmtT(x.ts))} · ${esc(x.title)}">${BK[x.bucket][2]}</i>`; }).join("");
    };
    place(); ovSub = place; src.chart.timeScale().subscribeVisibleTimeRangeChange(place);
  }

  async function summary() {
    const d = W.last; if (!d) return;
    const box = P.querySelector("#why-aitext"), btn = P.querySelector("#why-ai");
    box.hidden = false; box.textContent = "Asking claude -p (local CLI)…"; btn.disabled = true;
    try { const r = await api(`/cause/summary?symbol=${encodeURIComponent(d.symbol)}&from=${Math.round(d.from)}&to=${Math.round(d.to)}`); box.textContent = r.summary; }
    catch (e) { box.textContent = `AI summary unavailable: ${e.message}. The ranked evidence above stands on its own.`; }
    finally { btn.disabled = false; }
  }
})();
