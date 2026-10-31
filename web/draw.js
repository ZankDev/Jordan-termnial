/* NXDraw: a full drawing toolkit for any lightweight-charts chart (used by the ACADEMY replay trainer).
   Tools: trendline, ray, arrow, horizontal line / ray, vertical line, parallel channel, Andrews pitchfork, rectangle, trading range, ellipse,
   Fibonacci retracement and extension, measure, long / short position boxes, freehand brush, text note, price flag. Colours, select + delete,
   duplicate, undo, clear, and export the chart with its drawings as a PNG. Anchors are stored as (unix seconds, price), so drawings survive
   zooming and can sit beyond the last bar. */
(function () {
  "use strict";
  const NX = window.NX;
  const { esc, num, px, pct, signed } = NX;
  const FIBS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  const FIBX = [0, 0.618, 1, 1.272, 1.618, 2, 2.618];
  const COLORS = ["#ffa028", "#5aa9ff", "#2be07e", "#ff4a4a", "#c58bff", "#f2e34d", "#ffffff"];
  const TOOLS = [["cursor", "✥", "Pan / cursor"], ["select", "⬚", "Select a drawing (Delete removes it)"], "|",
    ["trend", "╱", "Trendline"], ["ray", "➚", "Ray"], ["arrow", "➜", "Arrow"], ["hline", "─", "Horizontal line"], ["hray", "⟶", "Horizontal ray"], ["vline", "│", "Vertical line"], "|",
    ["channel", "⫽", "Parallel channel (drag, then click for the width)"], ["pitch", "⋔", "Andrews pitchfork (drag, then click the third point)"], ["rect", "▭", "Rectangle / zone"], ["range", "⇕", "Trading range: top, bottom and middle"], ["ellipse", "◯", "Ellipse"], "|",
    ["fib", "☰", "Fibonacci retracement"], ["fibx", "⇗", "Fibonacci extension (drag, then click the retracement)"], ["measure", "↔", "Measure: price, %, bars, time"], ["long", "▲", "Long position box (entry, target, stop)"], ["short", "▼", "Short position box"], "|",
    ["brush", "✎", "Freehand brush"], ["text", "T", "Text note"], ["flag", "⚑", "Price flag"]];
  const HINTS = { cursor: "Pan and zoom the chart normally.", select: "Click a drawing to select it, then Delete removes it (or pick a colour to recolour it).", trend: "Drag from the start to the end.", ray: "Drag: the line continues past the second point.", arrow: "Drag from the tail to the head.",
    hline: "Click a price.", hray: "Click a point: a level that runs to the right.", vline: "Click a moment in time.", channel: "Drag the first line, release, then click to set the width.", pitch: "Drag from the pivot to the second point, release, then click the third point.",
    rect: "Drag a box around the zone.", range: "Drag a box around the trading range: it labels top, bottom and the middle.", ellipse: "Drag the bounding box.", fib: "Drag between a swing high and a swing low.", fibx: "Drag the move, release, then click where the retracement ended.",
    measure: "Drag between two points: move, %, bars and time.", long: "Drag from entry to target; the stop is placed at half that distance.", short: "Drag from entry to target; the stop is placed at half that distance.", brush: "Hold and draw freely.", text: "Click where the note goes.", flag: "Click a price to drop a flag with its value." };
  const STAGE3 = new Set(["channel", "fibx", "pitch"]);

  function ask(title, o = {}) {
    return new Promise((res) => {
      const d = document.createElement("div"); d.className = "nx-help";
      d.innerHTML = `<div class="nx-help-box gp-ask"><div class="bb-ph"><span class="bb-n">?</span>${esc(title)}</div><div class="bb-pad"><input class="ctl-in" style="max-width:none;width:100%;box-sizing:border-box" placeholder="${esc(o.placeholder || "")}" value="${esc(o.value || "")}"/><div class="gp-ask-b"><button class="bb-chip" data-x="0">CANCEL</button><button class="bb-chip on" data-x="1">OK</button></div></div></div>`;
      document.body.appendChild(d);
      const inp = d.querySelector("input"); inp.focus(); inp.select();
      const done = (v) => { d.remove(); res(v); };
      d.addEventListener("click", (e) => { if (e.target === d) return done(null); const b = e.target.closest("[data-x]"); if (b) done(b.dataset.x === "1" ? inp.value : null); });
      d.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp.value); if (e.key === "Escape") done(null); });
    });
  }

  function toolbarHtml() {
    return `<div class="gp-tools nxd-tools">${TOOLS.map((t) => (t === "|" ? '<span class="gp-sep"></span>' : `<button class="gp-tool ${t[0] === "cursor" ? "on" : ""}" data-nxd="${t[0]}" title="${t[2]}">${t[1]}</button>`)).join("")}</div>
      <span class="gp-sep"></span><div class="nxd-colors">${COLORS.map((c, i) => `<i class="nxd-c ${i === 0 ? "on" : ""}" data-c="${c}" style="background:${c}" title="Colour"></i>`).join("")}</div>
      <span class="gp-sep"></span><button class="gp-tool" data-nxd-act="dup" title="Duplicate the selected drawing">⧉</button><button class="gp-tool" data-nxd-act="del" title="Delete the selected drawing (Delete key)">✕</button><button class="gp-tool" data-nxd-act="undo" title="Undo (Ctrl+Z)">↶</button><button class="gp-tool" data-nxd-act="clear" title="Clear all drawings">🗑</button><button class="gp-tool" data-nxd-act="png" title="Save the chart with your drawings as a PNG">📷</button>
      <span class="nxd-hint" data-nxd-hint></span>`;
  }

  function attach(o) {
    const { host, chart, series, times } = o;
    const cv = document.createElement("canvas"); cv.className = "gp-cv"; host.appendChild(cv);
    const g = cv.getContext("2d");
    let drawings = [], tool = "cursor", cur = null, sel = -1, color = COLORS[0], raf = 0, undoStack = [], bar = null;
    const T = () => times() || [];
    const step = () => { const s = T(); return s.length > 1 ? (s[s.length - 1] - s[0]) / (s.length - 1) : 86400; };
    const toIdx = (t) => { const s = T(); if (!s.length) return 0; if (t <= s[0]) return (t - s[0]) / step(); if (t >= s[s.length - 1]) return s.length - 1 + (t - s[s.length - 1]) / step(); let lo = 0, hi = s.length - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; s[m] <= t ? (lo = m) : (hi = m); } return lo + (t - s[lo]) / (s[hi] - s[lo]); };
    const toSec = (i) => { const s = T(); if (!s.length) return 0; if (i <= 0) return s[0] + i * step(); if (i >= s.length - 1) return s[s.length - 1] + (i - (s.length - 1)) * step(); const f = Math.floor(i); return s[f] + (i - f) * (s[f + 1] - s[f]); };
    const xy = (p) => { const x = chart.timeScale().logicalToCoordinate(toIdx(p.t)), y = series.priceToCoordinate(p.p); return x === null || y === null ? null : { x, y }; };
    const inv = (x, y) => { const l = chart.timeScale().coordinateToLogical(x), pr = series.coordinateToPrice(y); return l === null || pr === null ? null : { t: toSec(l), p: pr }; };
    const dec = o.decimals || (() => 2);
    const fmt = (v) => (v == null ? "" : v.toFixed(dec(v)));
    const P = (d) => ({ a: d.a && xy(d.a), b: d.b && xy(d.b), c: d.c && xy(d.c) });
    const lineAt = (d, t) => { const ia = toIdx(d.a.t), ib = toIdx(d.b.t), it = toIdx(t); return ib === ia ? d.a.p : d.a.p + ((d.b.p - d.a.p) * (it - ia)) / (ib - ia); };
    const chan = (d) => { const { a, b } = P(d); if (!a || !b) return null; if (!d.c) return { a, b }; const off = d.c.p - lineAt(d, d.c.t), a2 = xy({ t: d.a.t, p: d.a.p + off }), b2 = xy({ t: d.b.t, p: d.b.p + off }); return a2 && b2 ? { a, b, a2, b2 } : { a, b }; };
    const posGeo = (d) => { const { a, b } = P(d); if (!a || !b) return null; const stopP = d.a.p - (d.b.p - d.a.p) / 2, yS = series.priceToCoordinate(stopP); return yS === null ? null : { a, b, stopP, yS, x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x) }; };
    const dur = (s) => { const v = Math.abs(s); return v >= 86400 ? `${(v / 86400).toFixed(1)}d` : v >= 3600 ? `${(v / 3600).toFixed(1)}h` : `${Math.round(v / 60)}m`; };
    const tag = (text, x, y, c) => { const w = g.measureText(text).width + 8; g.fillStyle = "rgba(0,0,0,0.78)"; g.fillRect(x, y - 11, w, 15); g.fillStyle = c; g.fillText(text, x + 4, y); };
    const alpha = (c, a) => { const n = parseInt(c.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };
    const rayEnd = (a, b, w) => { const dx = b.x - a.x, dy = b.y - a.y, k = dx === 0 ? 1 : (w * 3) / Math.abs(dx); return { x: a.x + dx * Math.max(k, 1), y: a.y + dy * Math.max(k, 1) }; };

    function shape(d, hot, w, h) {
      const col = hot ? "#ffffff" : d.col || COLORS[0]; g.strokeStyle = col; g.fillStyle = col; g.lineWidth = hot ? 2.2 : 1.4; g.setLineDash([]);
      const { a, b, c } = P(d), dot = (p) => { g.beginPath(); g.arc(p.x, p.y, 3, 0, 6.3); g.fill(); };
      const line = (p, q) => { g.beginPath(); g.moveTo(p.x, p.y); g.lineTo(q.x, q.y); g.stroke(); };
      if (d.k === "hline" && a) { line({ x: 0, y: a.y }, { x: w, y: a.y }); g.fillText(fmt(d.a.p), 6, a.y - 3); }
      else if (d.k === "hray" && a) { line(a, { x: w, y: a.y }); dot(a); g.fillText(fmt(d.a.p), Math.min(w - 60, a.x + 6), a.y - 4); }
      else if (d.k === "vline" && a) line({ x: a.x, y: 0 }, { x: a.x, y: h });
      else if (d.k === "flag" && a) { g.font = "700 11px 'JetBrains Mono', Consolas, monospace"; const t = fmt(d.a.p), tw = g.measureText(t).width + 12; g.fillRect(a.x, a.y - 8, tw, 16); g.fillStyle = "#000"; g.fillText(t, a.x + 6, a.y + 4); g.fillStyle = col; line({ x: a.x, y: a.y - 8 }, { x: a.x, y: a.y + 14 }); g.font = "10px 'JetBrains Mono', Consolas, monospace"; }
      else if (d.k === "trend" && a && b) { line(a, b); dot(a); dot(b); g.fillText(`${signed(d.b.p - d.a.p, dec(d.a.p))} (${pct((d.b.p / d.a.p - 1) * 100)})`, b.x + 6, b.y - 6); }
      else if (d.k === "arrow" && a && b) { const ang = Math.atan2(b.y - a.y, b.x - a.x); line(a, b); g.beginPath(); g.moveTo(b.x, b.y); g.lineTo(b.x - 12 * Math.cos(ang - 0.45), b.y - 12 * Math.sin(ang - 0.45)); g.lineTo(b.x - 12 * Math.cos(ang + 0.45), b.y - 12 * Math.sin(ang + 0.45)); g.closePath(); g.fill(); }
      else if (d.k === "ray" && a && b) { line(a, rayEnd(a, b, w)); dot(a); }
      else if (d.k === "channel") { const q = chan(d); if (q) { line(q.a, q.b); if (q.a2) { line(q.a2, q.b2); g.fillStyle = alpha(d.col || COLORS[0], 0.09); g.beginPath(); g.moveTo(q.a.x, q.a.y); g.lineTo(q.b.x, q.b.y); g.lineTo(q.b2.x, q.b2.y); g.lineTo(q.a2.x, q.a2.y); g.closePath(); g.fill(); g.setLineDash([4, 4]); g.strokeStyle = alpha(col, 0.7); line({ x: (q.a.x + q.a2.x) / 2, y: (q.a.y + q.a2.y) / 2 }, { x: (q.b.x + q.b2.x) / 2, y: (q.b.y + q.b2.y) / 2 }); g.setLineDash([]); } dot(q.a); dot(q.b); } }
      else if (d.k === "pitch" && a && b) {
        dot(a); dot(b); line(a, b);
        if (c) { dot(c); line(a, c); line(b, c); const mid = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }, dx = mid.x - a.x, dy = mid.y - a.y, k = dx === 0 ? 1 : Math.max(1, (w * 3) / Math.abs(dx)); const ext = (p) => ({ x: p.x + dx * k, y: p.y + dy * k }); line(a, ext(a)); g.strokeStyle = alpha(col, 0.8); line(b, ext(b)); line(c, ext(c)); g.fillStyle = alpha(d.col || COLORS[0], 0.06); g.beginPath(); g.moveTo(b.x, b.y); g.lineTo(ext(b).x, ext(b).y); g.lineTo(ext(c).x, ext(c).y); g.lineTo(c.x, c.y); g.closePath(); g.fill(); }
      }
      else if (d.k === "rect" && a && b) { g.fillStyle = alpha(d.col || COLORS[0], 0.1); g.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); g.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); }
      else if (d.k === "range" && a && b) {
        const top = Math.min(a.y, b.y), bot = Math.max(a.y, b.y), x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), hi = Math.max(d.a.p, d.b.p), lo = Math.min(d.a.p, d.b.p);
        g.fillStyle = alpha(d.col || COLORS[0], 0.08); g.fillRect(x0, top, x1 - x0, bot - top); g.strokeRect(x0, top, x1 - x0, bot - top); g.setLineDash([5, 4]); line({ x: x0, y: (top + bot) / 2 }, { x: x1, y: (top + bot) / 2 }); g.setLineDash([]);
        tag(`TOP ${fmt(hi)}`, x0 + 4, top - 4, col); tag(`BOTTOM ${fmt(lo)}`, x0 + 4, bot + 14, col); tag(`MID ${fmt((hi + lo) / 2)} · height ${pct(((hi - lo) / lo) * 100, false, 1)}`, x0 + 4, (top + bot) / 2 - 4, "#cfcfcf");
      }
      else if (d.k === "ellipse" && a && b) { g.beginPath(); g.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, 6.2832); g.fillStyle = alpha(d.col || COLORS[0], 0.1); g.fill(); g.stroke(); }
      else if (d.k === "measure" && a && b) {
        const up = d.b.p >= d.a.p, mc = hot ? "#fff" : up ? "#2be07e" : "#ff4a4a"; g.fillStyle = up ? "rgba(43,224,126,0.13)" : "rgba(255,74,74,0.13)"; g.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); g.strokeStyle = mc; g.setLineDash([4, 3]); g.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); g.setLineDash([]);
        const bars = Math.round(toIdx(d.b.t) - toIdx(d.a.t)), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2; tag(`${signed(d.b.p - d.a.p, dec(d.a.p))}  (${pct((d.b.p / d.a.p - 1) * 100)})`, mx - 50, my - 6, mc); tag(`${Math.abs(bars)} bars · ${dur(d.b.t - d.a.t)}`, mx - 50, my + 11, "#cfcfcf");
      }
      else if ((d.k === "long" || d.k === "short") && posGeo(d)) {
        const q = posGeo(d), wd = Math.max(60, q.x1 - q.x0), x0 = q.x0;
        g.fillStyle = "rgba(43,224,126,0.17)"; g.fillRect(x0, Math.min(q.a.y, q.b.y), wd, Math.abs(q.b.y - q.a.y)); g.fillStyle = "rgba(255,74,74,0.17)"; g.fillRect(x0, Math.min(q.a.y, q.yS), wd, Math.abs(q.yS - q.a.y));
        g.strokeStyle = hot ? "#fff" : "#8b8b8b"; line({ x: x0, y: q.a.y }, { x: x0 + wd, y: q.a.y }); g.strokeStyle = hot ? "#fff" : "#2be07e"; line({ x: x0, y: q.b.y }, { x: x0 + wd, y: q.b.y }); g.strokeStyle = hot ? "#fff" : "#ff4a4a"; line({ x: x0, y: q.yS }, { x: x0 + wd, y: q.yS });
        tag(`TARGET ${fmt(d.b.p)} (${pct((d.b.p / d.a.p - 1) * 100)})`, x0 + 4, q.b.y + (q.b.y < q.a.y ? -4 : 14), "#2be07e"); tag(`STOP ${fmt(q.stopP)} (${pct((q.stopP / d.a.p - 1) * 100)})`, x0 + 4, q.yS + (q.yS < q.a.y ? -4 : 14), "#ff4a4a"); tag(`${d.k === "long" ? "LONG" : "SHORT"} ${fmt(d.a.p)} · reward:risk 2.0`, x0 + 4, q.a.y - 4, "#f1f1f1");
      }
      else if ((d.k === "fib" || d.k === "fibx") && a && b) {
        const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x, x0 + 130);
        const levels = d.k === "fib" ? FIBS.map((f) => [f, d.b.p + (d.a.p - d.b.p) * f]) : c ? FIBX.map((f) => [f, d.c.p + (d.b.p - d.a.p) * f]) : [];
        levels.forEach(([f, p]) => { const y = series.priceToCoordinate(p); if (y === null) return; g.strokeStyle = f === 0 || f === 1 ? col : alpha(col, 0.7); g.beginPath(); g.moveTo(d.k === "fibx" && c ? Math.min(x0, c.x) : x0, y); g.lineTo(x1, y); g.stroke(); g.fillStyle = col; g.fillText(`${(f * 100).toFixed(1)}%  ${fmt(p)}`, x1 + 4, y + 3); });
        g.strokeStyle = alpha(col, 0.45); g.setLineDash([3, 3]); line(a, b); if (c) line(b, c); g.setLineDash([]);
      }
      else if (d.k === "brush" && d.pts) { g.beginPath(); let on = false; d.pts.forEach((pt) => { const q = xy(pt); if (!q) return; on ? g.lineTo(q.x, q.y) : g.moveTo(q.x, q.y); on = true; }); g.stroke(); }
      else if (d.k === "text" && a) { g.font = "600 12px 'JetBrains Mono', Consolas, monospace"; g.fillText(d.text, a.x, a.y); g.font = "10px 'JetBrains Mono', Consolas, monospace"; }
    }
    const segD = (p, a, b) => { const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy; let t = l2 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0; t = Math.max(0, Math.min(1, t)); return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy)); };
    const inBox = (m, a, b, T) => m.x >= Math.min(a.x, b.x) - T && m.x <= Math.max(a.x, b.x) + T && m.y >= Math.min(a.y, b.y) - T && m.y <= Math.max(a.y, b.y) + T;
    function hit(d, m) {
      const { a, b, c } = P(d), T = 7;
      switch (d.k) {
        case "hline": return !!a && Math.abs(m.y - a.y) < T;
        case "hray": return !!a && Math.abs(m.y - a.y) < T && m.x >= a.x - T;
        case "vline": return !!a && Math.abs(m.x - a.x) < T;
        case "flag": return !!a && m.x >= a.x - 3 && m.x <= a.x + 70 && Math.abs(m.y - a.y) < 12;
        case "trend": case "arrow": return !!(a && b) && segD(m, a, b) < T;
        case "ray": return !!(a && b) && segD(m, a, rayEnd(a, b, 4000)) < T;
        case "rect": case "range": case "ellipse": case "measure": return !!(a && b) && inBox(m, a, b, T);
        case "channel": { const q = chan(d); return !!q && (segD(m, q.a, q.b) < T || (q.a2 && (segD(m, q.a2, q.b2) < T || inBox(m, { x: Math.min(q.a.x, q.b.x), y: Math.min(q.a.y, q.b.y, q.a2.y, q.b2.y) }, { x: Math.max(q.a.x, q.b.x), y: Math.max(q.a.y, q.b.y, q.a2.y, q.b2.y) }, 0)))); }
        case "pitch": if (!(a && b)) return false; if (!c) return segD(m, a, b) < T; { const mid = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }, e = rayEnd(a, mid, 4000), dx = e.x - a.x, dy = e.y - a.y; return segD(m, a, e) < T || segD(m, b, { x: b.x + dx, y: b.y + dy }) < T || segD(m, c, { x: c.x + dx, y: c.y + dy }) < T || segD(m, a, b) < T || segD(m, a, c) < T; }
        case "long": case "short": { const q = posGeo(d); return !!q && inBox(m, { x: q.x0, y: Math.min(q.a.y, q.b.y, q.yS) }, { x: q.x0 + Math.max(60, q.x1 - q.x0), y: Math.max(q.a.y, q.b.y, q.yS) }, T); }
        case "fib": case "fibx": { if (!(a && b)) return false; const lv = d.k === "fib" ? FIBS.map((f) => d.b.p + (d.a.p - d.b.p) * f) : c ? FIBX.map((f) => d.c.p + (d.b.p - d.a.p) * f) : []; return lv.some((p) => { const y = series.priceToCoordinate(p); return y !== null && Math.abs(m.y - y) < T && m.x >= Math.min(a.x, b.x) - T && m.x <= Math.max(a.x, b.x, Math.min(a.x, b.x) + 130) + T; }); }
        case "text": return !!a && m.x >= a.x - 4 && m.x <= a.x + d.text.length * 7.5 + 4 && Math.abs(m.y - a.y + 4) < 12;
        case "brush": { const pts = (d.pts || []).map(xy).filter(Boolean); return pts.some((q, i) => i && segD(m, pts[i - 1], q) < T); }
        default: return false;
      }
    }

    function overlay() {
      const w = host.clientWidth, h = host.clientHeight, dpr = window.devicePixelRatio || 1;
      if (!w || !h) return;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = `${w}px`; cv.style.height = `${h}px`; }
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h); g.font = "10px 'JetBrains Mono', Consolas, monospace";
      (cur ? [...drawings, cur] : drawings).forEach((d, i) => shape(d, i === sel, w, h));
    }
    const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; overlay(); }); };
    const pushUndo = () => { undoStack.push(JSON.stringify(drawings)); if (undoStack.length > 60) undoStack.shift(); };
    const changed = () => { if (o.onChange) o.onChange(drawings); paint(); };
    const pos = (e) => { const r = cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    const setHint = () => { const el = bar && bar.querySelector("[data-nxd-hint]"); if (el) el.textContent = HINTS[tool] || ""; };
    function setTool(t) {
      tool = t; if (t !== "select") sel = -1; cur = t === "cursor" || t === "select" ? null : cur;
      cv.style.pointerEvents = t === "cursor" ? "none" : "auto"; cv.style.cursor = t === "select" ? "pointer" : "crosshair";
      if (bar) bar.querySelectorAll("[data-nxd]").forEach((b) => b.classList.toggle("on", b.dataset.nxd === t));
      setHint(); paint(); if (o.onTool) o.onTool(t);
    }
    const newDraw = (k, p) => ({ k, a: p, b: p, col: color });

    cv.addEventListener("mousedown", (e) => {
      const m = pos(e);
      if (tool === "select") { sel = -1; for (let i = drawings.length - 1; i >= 0; i--) if (hit(drawings[i], m)) { sel = i; break; } paint(); return; }
      if (tool === "cursor") return;
      const p = inv(m.x, m.y); if (!p) return;
      if (cur && cur.stage === 2) { cur.c = p; delete cur.stage; pushUndo(); drawings.push(cur); cur = null; changed(); return setTool("cursor"); }
      if (tool === "hline" || tool === "hray" || tool === "vline" || tool === "flag") { pushUndo(); drawings.push({ k: tool, a: p, col: color }); changed(); return setTool("cursor"); }
      if (tool === "text") { setTool("cursor"); ask("Text note on the chart", { placeholder: "e.g. spring, last point of support" }).then((t) => { if (t) { pushUndo(); drawings.push({ k: "text", a: p, text: String(t).slice(0, 80), col: color }); changed(); } }); return; }
      cur = tool === "brush" ? { k: "brush", pts: [p], col: color } : newDraw(tool, p); paint();
    });
    cv.addEventListener("mousemove", (e) => { if (!cur) return; const p = inv(...Object.values(pos(e))); if (!p) return; if (cur.stage === 2) cur.c = p; else if (cur.k === "brush") cur.pts.push(p); else cur.b = p; paint(); });
    window.addEventListener("mouseup", () => {
      if (!cur || cur.stage === 2) return;
      if (STAGE3.has(cur.k)) { cur.stage = 2; cur.c = cur.b; paint(); return; }
      const ok = cur.k === "brush" ? cur.pts.length > 2 : cur.a.p !== cur.b.p || cur.a.t !== cur.b.t;
      if (ok) { pushUndo(); drawings.push(cur); }
      cur = null; changed(); setTool("cursor");
    });
    document.addEventListener("keydown", function keys(e) {
      if (!document.body.contains(host)) return document.removeEventListener("keydown", keys);
      const tg = (document.activeElement || {}).tagName; if (["INPUT", "TEXTAREA", "SELECT"].includes(tg)) return;
      if (e.key === "Escape") { cur = null; setTool("cursor"); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel >= 0) { e.preventDefault(); del(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange(paint);
    chart.subscribeCrosshairMove(paint);
    new ResizeObserver(paint).observe(host);

    function del() { if (sel < 0) return; pushUndo(); drawings.splice(sel, 1); sel = -1; changed(); }
    function undo() { const s = undoStack.pop(); if (s == null) return; drawings = JSON.parse(s); sel = -1; changed(); }
    function clear() { if (!drawings.length) return; pushUndo(); drawings = []; sel = -1; changed(); }
    function dup() { if (sel < 0) return; pushUndo(); const c = JSON.parse(JSON.stringify(drawings[sel])), dp = (c.a.p || 1) * 0.004; ["a", "b", "c"].forEach((k) => c[k] && (c[k].p += dp)); if (c.pts) c.pts.forEach((q) => (q.p += dp)); drawings.push(c); sel = drawings.length - 1; changed(); }
    function setColor(c) { color = c; if (sel >= 0) { pushUndo(); drawings[sel].col = c; changed(); } if (bar) bar.querySelectorAll(".nxd-c").forEach((x) => x.classList.toggle("on", x.dataset.c === c)); }
    function png() {
      const shot = chart.takeScreenshot(), out = document.createElement("canvas"); out.width = shot.width; out.height = shot.height;
      const c2 = out.getContext("2d"); c2.drawImage(shot, 0, 0); c2.drawImage(cv, 0, 0, shot.width, shot.height); return out.toDataURL("image/png");
    }
    function bindToolbar(root) {
      bar = root;
      root.querySelectorAll("[data-nxd]").forEach((b) => b.addEventListener("click", () => { cur = null; setTool(b.dataset.nxd); }));
      root.querySelectorAll(".nxd-c").forEach((c) => c.addEventListener("click", () => setColor(c.dataset.c)));
      root.querySelectorAll("[data-nxd-act]").forEach((b) => b.addEventListener("click", () => { const a = b.dataset.nxdAct; if (a === "del") del(); else if (a === "undo") undo(); else if (a === "clear") clear(); else if (a === "dup") dup(); else if (a === "png") { const l = document.createElement("a"); l.href = png(); l.download = `${o.name || "chart"}_${Date.now()}.png`; l.click(); } }));
      setHint();
    }
    return { setTool, paint, undo, clear, del, dup, setColor, png, bindToolbar, reset() { drawings = []; sel = -1; cur = null; undoStack = []; paint(); }, get tool() { return tool; }, get drawings() { return drawings; }, set drawings(v) { drawings = v; paint(); }, get count() { return drawings.length; } };
  }
  window.NXDraw = { attach, toolbarHtml, COLORS };
})();
