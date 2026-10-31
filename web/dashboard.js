// NOSTRO Algo Terminal — dashboard logic (vanilla JS, no build step)

const state = {
  strategies: null,
  currentAccountId: null,
  charts: {}, // lazily created lightweight-charts instances, keyed by container id
  replayEngine: null,
  trainerInitialized: false,
  showArchived: false,
  trainerSymbol: "NQ",
  trainerNextRunAt: null,
  trainerPollTimer: null,
  lastChampionScore: {}, // symbol -> score, used to detect a new champion between polls
  accountFormType: "standard",
  apexTiers: null,
  calendarYear: null,
  calendarMonth: null,
  calendarAccountId: null,
  accountCardEls: new Map(), // account id -> persistent card DOM node, so live polling updates in place instead of rebuilding the grid
  todayTradesByAccount: {}, // account name -> { pnl, count }, derived from the dashboard summary's today_trades list
  sparkLastRunAt: {},
  equityChartEls: {}, // containerId -> persistent custom SVG chart elements (see renderEquityComparisonChart)
  customCharts: {}, // containerId -> CustomCandleChart instance (trainer replay chart)
  customPanes: {}, // containerId -> CustomLinePane instance (trainer replay indicator sub-panel)
};

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function setStatus(text, isError = false) {
  const el = document.getElementById("status-text");
  el.textContent = text;
  el.style.color = isError ? "var(--red)" : "var(--text)";
}

function fmtMoney(n) {
  if (n === null || n === undefined) return "-";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}
function pnlClass(n) {
  return n >= 0 ? "pnl-pos" : "pnl-neg";
}
// Animates a number-bearing element from its previously displayed value to `newValue`
// (GSAP tweens a plain object, formatting each frame with `formatFn`) so live polling
// reads as ticking numbers instead of a text snap. Falls back to a plain set if GSAP
// isn't loaded yet, on first render, or when the value didn't actually change.
function tweenNumberText(el, newValue, formatFn) {
  if (!el) return;
  const prev = el.dataset.rawValue !== undefined ? parseFloat(el.dataset.rawValue) : newValue;
  el.dataset.rawValue = newValue;
  if (!window.gsap || Number.isNaN(prev) || prev === newValue) {
    el.textContent = formatFn(newValue);
    return;
  }
  const obj = { v: prev };
  gsap.to(obj, {
    v: newValue,
    duration: 0.6,
    ease: "power2.out",
    onUpdate: () => { el.textContent = formatFn(obj.v); },
  });
}
function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    timeZone: "America/New_York", hour12: false,
  }) + " ET";
}
function sqlToDate(sqlDatetime) {
  // sqlite datetime('now') returns UTC "YYYY-MM-DD HH:MM:SS" with no timezone marker
  return new Date(sqlDatetime.includes("T") ? sqlDatetime : sqlDatetime.replace(" ", "T") + "Z");
}
function fmtTimeAgo(sqlDatetime) {
  if (!sqlDatetime) return "—";
  const diffSec = Math.max(0, Math.floor((Date.now() - sqlToDate(sqlDatetime).getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}

// ---------------------------------------------------------------- popups + toasts

function showTradePopup(trade, equityBefore) {
  // #trade-popup is a global, position:fixed overlay (outside any .view container),
  // and the replay engine driving it keeps ticking in the background regardless of
  // which tab is active — without this guard it can pop up over other tabs (e.g.
  // Accounts) while the trainer's hero replay is still animating behind the scenes.
  const trainerView = document.getElementById("view-trainer");
  if (!trainerView || !trainerView.classList.contains("active")) return;

  const el = document.getElementById("trade-popup");
  const isWin = trade.pnl >= 0;
  const pct = equityBefore ? (trade.pnl / equityBefore) * 100 : 0;
  el.classList.remove("win", "loss", "show", "animate");
  void el.offsetWidth; // force reflow so the animation restarts on back-to-back trades
  el.classList.add(isWin ? "win" : "loss");
  el.querySelector(".tp-amount").textContent = `${isWin ? "+" : ""}${fmtMoney(trade.pnl)}`;
  el.querySelector(".tp-pct").textContent = `${isWin ? "+" : ""}${pct.toFixed(2)}%`;
  el.querySelector(".tp-reason").textContent = `${trade.side.toUpperCase()} · ${trade.exit_reason.toUpperCase()}`;
  el.classList.add("show", "animate");
  setTimeout(() => el.classList.remove("show", "animate"), 1100);
}

function showToast(text) {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = text;
  stack.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// ticker tape: see capital.js (multi-asset tape from /api/capital/tape)

// ---------------------------------------------------------------- tabs

function initTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`view-${btn.dataset.tab}`).classList.add("active");

      if (window.onTabChange) window.onTabChange(btn.dataset.tab);
      if (btn.dataset.tab === "capital" && window.initCapitalTab) {
        window.initCapitalTab();
      }
      if (btn.dataset.tab === "trainer") {
        initTrainerTab();
      }
      if (btn.dataset.tab === "live") {
        initLiveTab();
      }
      if (btn.dataset.tab === "field") {
        initFieldTab();
      }
    });
  });
}

function tickClock() {
  const el = document.getElementById("clock");
  const now = new Date();
  el.textContent = now.toLocaleTimeString([], { hour12: false }) + " LOCAL";
}

// ---------------------------------------------------------------- strategies

async function loadStrategies() {
  if (state.strategies) return state.strategies;
  const data = await api("/strategies");
  state.strategies = data.strategies;

  ["bt-strategy", "opt-strategy"].forEach((id) => {
    const sel = document.getElementById(id);
    sel.innerHTML = state.strategies
      .map((s) => `<option value="${s.name}">${s.name}</option>`)
      .join("");
  });

  renderStrategyLibrary();
  renderParamInputs("bt-params", currentStrategy("bt-strategy"));
  return state.strategies;
}

function currentStrategy(selectId) {
  const name = document.getElementById(selectId).value;
  return state.strategies.find((s) => s.name === name);
}

function renderParamInputs(containerId, strategy) {
  const container = document.getElementById(containerId);
  if (!strategy) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = strategy.params
    .map(
      (p) => `
      <div class="form-row">
        <label title="${p.kind}, ${p.min}-${p.max}">${p.name}</label>
        <input type="number" step="${p.step}" value="${p.default}" data-param="${p.name}" />
      </div>`
    )
    .join("");
}

function readParams(containerId) {
  const params = {};
  document.querySelectorAll(`#${containerId} [data-param]`).forEach((input) => {
    params[input.dataset.param] = parseFloat(input.value);
  });
  return params;
}

// "Passes NOSTRO" = replayed through the Apex evaluation rules from many start dates, the
// share that hit the profit target before breaching the trailing drawdown (app/accounts/apex_sim.py).
// >= 60% earns the green badge; anything lower is shown as a plain proxy number, never as a pass.
function apexBadge(rate) {
  if (rate == null) return "";
  const pct = Math.round(rate * 100);
  return rate >= 0.6
    ? `<span class="pill pill-gate-pass" title="Would have passed an Apex evaluation from ${pct}% of start dates">PASSES NOSTRO ${pct}%</span>`
    : `<span class="pill pill-idle" title="Best Apex evaluation pass rate seen: ${pct}% of start dates">APEX ${pct}%</span>`;
}

const SKILL_ORDER = ["noob", "apprentice", "journeyman", "expert", "master"];
const stratView = { sort: "level", open: new Set(), wired: false };

function renderStrategyLibrary() {
  const el = document.getElementById("strategy-list");
  if (!el || !state.strategies) return;
  const sk = (s) => s.skill || { level: "Noob", learning_percent: 0, total_trades_evaluated: 0, total_cycles: 0, best_score: null, ever_confirmed_champion: false };
  const apex = (s) => (sk(s).apex && sk(s).apex.best_pass_rate != null ? sk(s).apex.best_pass_rate : -1);
  const sorters = {
    level: (a, b) => SKILL_ORDER.indexOf(sk(b).level.toLowerCase()) - SKILL_ORDER.indexOf(sk(a).level.toLowerCase()) || sk(b).learning_percent - sk(a).learning_percent,
    learn: (a, b) => sk(b).learning_percent - sk(a).learning_percent,
    apex: (a, b) => apex(b) - apex(a),
    trades: (a, b) => sk(b).total_trades_evaluated - sk(a).total_trades_evaluated,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  const rows = [...state.strategies].sort(sorters[stratView.sort] || sorters.level);

  // KPI strip
  const kp = document.getElementById("strat-kpis");
  if (kp) {
    const n = rows.length;
    const top = rows.filter((s) => ["expert", "master"].includes(sk(s).level.toLowerCase())).length;
    const confirmed = rows.filter((s) => sk(s).ever_confirmed_champion).length;
    const avgLearn = n ? rows.reduce((t, s) => t + sk(s).learning_percent, 0) / n : 0;
    const bestApex = Math.max(-1, ...rows.map(apex));
    const cyc = rows.reduce((t, s) => t + sk(s).total_cycles, 0);
    kp.innerHTML = [
      ["STRATEGIES", n, "in the rotation"],
      ["EXPERT + MASTER", top, "proven edge or better"],
      ["CONFIRMED", confirmed, "passed deep validation"],
      ["AVG LEARNED", `${avgLearn.toFixed(0)}%`, "across the library"],
      ["BEST APEX PASS", bestApex >= 0 ? `${Math.round(bestApex * 100)}%` : "—", "of start dates, best seen"],
      ["TRAINING CYCLES", cyc.toLocaleString(), "strategy-cycles logged"],
    ].map(([l, v, sub]) => `<div class="bb-kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`).join("");
  }
  const meta = document.getElementById("strat-meta");
  if (meta) meta.textContent = `${rows.length} STRATEGIES`;

  el.innerHTML = `<table class="bb-tbl"><thead><tr>
      <th>STRATEGY</th><th>LEVEL</th><th>LEARNED</th><th class="r">TRADES</th><th class="r">CYCLES</th><th class="r">BEST SCORE</th><th>APEX PASS</th><th>STATUS</th><th class="r">PARAMS</th></tr></thead><tbody>
    ${rows.map((s) => {
      const k = sk(s), lvl = k.level.toLowerCase();
      const ap = k.apex && k.apex.best_pass_rate != null ? Math.round(k.apex.best_pass_rate * 100) : null;
      const open = stratView.open.has(s.name);
      return `<tr class="click strat-row ${open ? "open" : ""}" data-name="${s.name}">
        <td class="sym">${s.name}</td>
        <td><span class="pill pill-skill-${lvl}" title="${k.level_description || ""}">${k.level.toUpperCase()}</span></td>
        <td><span class="bb-prog wide"><i style="width:${Math.max(2, k.learning_percent)}%"></i></span><span class="dim">${k.learning_percent}%</span></td>
        <td class="r">${k.total_trades_evaluated.toLocaleString()}</td><td class="r dim">${k.total_cycles.toLocaleString()}</td>
        <td class="r ${k.best_score > 0 ? "up" : "dim"}">${k.best_score == null ? "—" : k.best_score.toFixed(2)}</td>
        <td>${ap == null ? '<span class="dim">—</span>' : ap >= 60 ? `<span class="pill pill-gate-pass" title="Would have passed an Apex evaluation from ${ap}% of start dates">PASSES ${ap}%</span>` : `<span class="dim">${ap}%</span>`}</td>
        <td>${k.ever_confirmed_champion ? '<span class="pill pill-gate-pass">CONFIRMED</span>' : '<span class="dim">—</span>'}</td>
        <td class="r dim">${s.params.length} ${open ? "&#9650;" : "&#9660;"}</td></tr>
      ${open ? `<tr class="strat-detail"><td colspan="9"><div class="strat-desc">${s.description}</div>
        <div class="params">${s.params.map((p) => `<span class="pill pill-param" title="${p.kind}, range ${p.min}-${p.max}">${p.name}=${p.default}</span>`).join("")}</div></td></tr>` : ""}`;
    }).join("")}</tbody></table>`;

  el.querySelectorAll("tr.strat-row").forEach((tr) => tr.addEventListener("click", () => {
    const n = tr.dataset.name;
    if (stratView.open.has(n)) stratView.open.delete(n); else stratView.open.add(n);
    renderStrategyLibrary();
  }));
  if (!stratView.wired) {
    stratView.wired = true;
    document.querySelectorAll("#strat-sort .bb-chip").forEach((chip) => chip.addEventListener("click", () => {
      document.querySelectorAll("#strat-sort .bb-chip").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      stratView.sort = chip.dataset.sort;
      renderStrategyLibrary();
    }));
  }
}

// ---------------------------------------------------------------- charts (lightweight-charts)

function getOrCreateChart(containerId, kind) {
  if (state.charts[containerId]) return state.charts[containerId];
  const container = document.getElementById(containerId);
  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#050505" }, textColor: "#8b8b8b" },
    grid: { vertLines: { color: "#161616" }, horzLines: { color: "#161616" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#262626" },
    rightPriceScale: { borderColor: "#262626" },
    autoSize: true,
  });
  const series =
    kind === "candles"
      ? chart.addCandlestickSeries({
          upColor: "#21c97a", downColor: "#ff4d5e",
          borderVisible: false, wickUpColor: "#21c97a", wickDownColor: "#ff4d5e",
        })
      : chart.addLineSeries({ color: "#ff9d00", lineWidth: 2 });
  state.charts[containerId] = { chart, series };
  return state.charts[containerId];
}

// ---------------------------------------------------------------- custom candlestick chart (canvas)
// Used only for the Trainer hero's replay chart — a from-scratch canvas renderer instead
// of lightweight-charts, so it carries no third-party attribution mark and can draw the
// annotation style asked for directly: a leader-line-labeled SL/TP, an entry/exit marker
// with its own caption, a strategy-params box in the corner, and a live last-price badge.
// Exposes the same setData/update/setMarkers method names lightweight-charts' series
// object has, so ReplayEngine's own logic didn't need to change, just which chart it talks to.
class CustomCandleChart {
  constructor(containerId, opts = {}) {
    this.container = document.getElementById(containerId);
    this.canvas = document.createElement("canvas");
    this.canvas.style.width = "100%";
    this.canvas.style.height = "100%";
    this.canvas.style.display = "block";
    this.container.innerHTML = "";
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.data = [];
    this.markers = [];
    this.overlayLines = []; // { color, width, points: [{time, value}] }
    this.priceLines = []; // { price, color, label }
    this.paramsBox = opts.paramsBox || null; // { title, rows: [[label, value], ...] }
    this.visibleBars = opts.visibleBars || 100;
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(this.container);
    this._resize();
  }

  _resize() {
    const rect = this.container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = rect.width;
    this.h = rect.height;
    this.draw();
  }

  setData(data) { this.data = data ? data.slice() : []; this.draw(); }
  update(bar) {
    const last = this.data[this.data.length - 1];
    if (last && last.time === bar.time) this.data[this.data.length - 1] = bar;
    else this.data.push(bar);
    this.draw();
  }
  setMarkers(markers) { this.markers = markers || []; this.draw(); }
  setOverlayLines(lines) { this.overlayLines = lines || []; this.draw(); }
  setPriceLines(lines) { this.priceLines = lines || []; this.draw(); }
  setParamsBox(box) { this.paramsBox = box; this.draw(); }

  draw() {
    const ctx = this.ctx;
    const w = this.w, h = this.h;
    if (!w || !h) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#050505";
    ctx.fillRect(0, 0, w, h);
    if (!this.data.length) return;

    const padL = 8, padR = 64, padT = 16, padB = 22;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const visible = this.data.slice(-this.visibleBars);
    const n = visible.length;
    const slot = plotW / n;
    const candleW = Math.max(2, Math.min(9, slot * 0.6));
    const visibleTimes = new Set(visible.map((c) => c.time));

    let lo = Infinity, hi = -Infinity;
    visible.forEach((c) => { lo = Math.min(lo, c.low); hi = Math.max(hi, c.high); });
    this.overlayLines.forEach((ov) => ov.points.forEach((p) => {
      if (visibleTimes.has(p.time) && p.value != null) { lo = Math.min(lo, p.value); hi = Math.max(hi, p.value); }
    }));
    (this.markers || []).forEach((m) => {
      if (visibleTimes.has(m.time) && m.price != null) { lo = Math.min(lo, m.price); hi = Math.max(hi, m.price); }
    });
    this.priceLines.forEach((pl) => { lo = Math.min(lo, pl.price); hi = Math.max(hi, pl.price); });
    if (lo === hi) { lo -= 1; hi += 1; }
    const padV = (hi - lo) * 0.1;
    lo -= padV;
    hi += padV;

    const xAt = (i) => padL + i * slot + slot / 2;
    const yAt = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;
    const idxByTime = new Map(visible.map((c, i) => [c.time, i]));
    const timeToX = (t) => {
      if (idxByTime.has(t)) return xAt(idxByTime.get(t));
      const first = visible[0].time, last = visible[n - 1].time;
      const frac = (t - first) / ((last - first) || 1);
      return padL + frac * plotW;
    };

    ctx.strokeStyle = "#161616";
    ctx.lineWidth = 1;
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.fillStyle = "#8b8b8b";
    const yTicks = 5;
    for (let i = 0; i <= yTicks; i++) {
      const v = lo + ((hi - lo) * i) / yTicks;
      const yy = yAt(v);
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.fillText(v.toFixed(2), w - padR + 6, yy + 3);
    }

    visible.forEach((c, i) => {
      const cx = xAt(i);
      const up = c.close >= c.open;
      const color = up ? "#21c97a" : "#ff4d5e";
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(cx, yAt(c.high)); ctx.lineTo(cx, yAt(c.low)); ctx.stroke();
      const top = yAt(Math.max(c.open, c.close)), bot = yAt(Math.min(c.open, c.close));
      ctx.fillRect(cx - candleW / 2, top, candleW, Math.max(1, bot - top));
    });

    this.overlayLines.forEach((ov) => {
      const byTime = new Map(ov.points.map((p) => [p.time, p.value]));
      ctx.strokeStyle = ov.color;
      ctx.lineWidth = ov.width || 1.4;
      ctx.beginPath();
      let started = false;
      visible.forEach((c, i) => {
        const v = byTime.get(c.time);
        if (v == null) return;
        if (!started) { ctx.moveTo(xAt(i), yAt(v)); started = true; }
        else ctx.lineTo(xAt(i), yAt(v));
      });
      ctx.stroke();
    });

    this.priceLines.forEach((pl) => {
      const yy = yAt(pl.price);
      ctx.strokeStyle = pl.color;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.setLineDash([]);
      if (pl.label) {
        ctx.fillStyle = pl.color;
        ctx.font = "bold 10px 'JetBrains Mono', monospace";
        ctx.fillText(pl.label, w - padR + 6, yy + 3);
      }
    });

    (this.markers || []).forEach((m) => {
      const mx = timeToX(m.time);
      const my = yAt(m.price);
      ctx.fillStyle = m.color;
      ctx.beginPath();
      if (m.shape === "arrowUp") { ctx.moveTo(mx, my + 10); ctx.lineTo(mx - 6, my + 20); ctx.lineTo(mx + 6, my + 20); }
      else if (m.shape === "arrowDown") { ctx.moveTo(mx, my - 10); ctx.lineTo(mx - 6, my - 20); ctx.lineTo(mx + 6, my - 20); }
      else { ctx.arc(mx, my, 4.5, 0, Math.PI * 2); }
      ctx.closePath();
      ctx.fill();
      if (m.text) {
        ctx.font = "9.5px 'JetBrains Mono', monospace";
        ctx.fillStyle = "#d7dee6";
        const labelY = m.shape === "arrowUp" ? my + 32 : m.shape === "arrowDown" ? my - 24 : my - 10;
        ctx.fillText(m.text, Math.min(mx + 8, w - padR - ctx.measureText(m.text).width - 4), labelY);
      }
    });

    if (this.paramsBox && this.paramsBox.rows.length) {
      const rows = this.paramsBox.rows;
      ctx.font = "10px 'JetBrains Mono', monospace";
      const rowH = 15;
      const labelW = Math.max(...rows.map(([k]) => ctx.measureText(k).width)) + 10;
      const valueW = Math.max(...rows.map(([, v]) => ctx.measureText(String(v)).width)) + 8;
      const boxW = labelW + valueW + 14;
      const boxH = rowH * rows.length + (this.paramsBox.title ? 20 : 10);
      ctx.fillStyle = "rgba(9, 13, 19, 0.82)";
      ctx.strokeStyle = "#262626";
      ctx.lineWidth = 1;
      const bx = padL + 6, by = padT + 6;
      if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(bx, by, boxW, boxH, 5); ctx.fill(); ctx.stroke(); }
      else { ctx.fillRect(bx, by, boxW, boxH); ctx.strokeRect(bx, by, boxW, boxH); }
      let ty = by + 14;
      if (this.paramsBox.title) {
        ctx.fillStyle = "#ff9d00";
        ctx.font = "bold 10.5px 'JetBrains Mono', monospace";
        ctx.fillText(this.paramsBox.title, bx + 8, ty);
        ty += 16;
        ctx.font = "10px 'JetBrains Mono', monospace";
      }
      rows.forEach(([k, v]) => {
        ctx.fillStyle = "#8b8b8b";
        ctx.fillText(k, bx + 8, ty);
        ctx.fillStyle = "#d7dee6";
        ctx.fillText(String(v), bx + 8 + labelW, ty);
        ty += rowH;
      });
    }

    const lastBar = this.data[this.data.length - 1];
    if (lastBar) {
      const prev = this.data.length > 1 ? this.data[this.data.length - 2] : null;
      const up = lastBar.close >= (prev ? prev.close : lastBar.open);
      const yy = yAt(lastBar.close);
      ctx.fillStyle = up ? "#21c97a" : "#ff4d5e";
      ctx.fillRect(w - padR + 1, yy - 8, padR - 3, 16);
      ctx.fillStyle = "#04110a";
      ctx.font = "bold 10.5px 'JetBrains Mono', monospace";
      ctx.fillText(lastBar.close.toFixed(2), w - padR + 5, yy + 3.5);
    }
  }
}

function getOrCreateCustomChart(containerId, opts) {
  if (state.customCharts[containerId]) {
    if (opts && opts.paramsBox) state.customCharts[containerId].setParamsBox(opts.paramsBox);
    return state.customCharts[containerId];
  }
  const chart = new CustomCandleChart(containerId, opts);
  state.customCharts[containerId] = chart;
  return chart;
}

// Companion sub-panel for RSI/MACD-style oscillator overlays under the custom candle
// chart — its own canvas, its own y-scale, x-axis kept in the same time domain.
class CustomLinePane {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.canvas = document.createElement("canvas");
    this.canvas.style.width = "100%";
    this.canvas.style.height = "100%";
    this.canvas.style.display = "block";
    this.container.innerHTML = "";
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.lines = [];
    this.hlines = [];
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(this.container);
    this._resize();
  }

  _resize() {
    const rect = this.container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = rect.width;
    this.h = rect.height;
    this.draw();
  }

  setLines(lines) { this.lines = lines || []; this.draw(); }
  setHLines(hlines) { this.hlines = hlines || []; this.draw(); }

  draw() {
    const ctx = this.ctx, w = this.w, h = this.h;
    if (!w || !h) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#050505";
    ctx.fillRect(0, 0, w, h);
    const allPts = this.lines.flatMap((l) => l.points).filter((p) => p.value != null);
    if (!allPts.length) return;

    const padL = 8, padR = 64, padT = 8, padB = 8;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const values = allPts.map((p) => p.value).concat(this.hlines.map((hl) => hl.value));
    let lo = Math.min(...values), hi = Math.max(...values);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.12;
    lo -= pad;
    hi += pad;
    const tMin = Math.min(...allPts.map((p) => p.time)), tMax = Math.max(...allPts.map((p) => p.time));
    const xAt = (t) => padL + ((t - tMin) / ((tMax - tMin) || 1)) * plotW;
    const yAt = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

    this.hlines.forEach((hl) => {
      const yy = yAt(hl.value);
      ctx.strokeStyle = hl.color;
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.setLineDash([]);
      if (hl.label) {
        ctx.fillStyle = hl.color;
        ctx.font = "9.5px 'JetBrains Mono', monospace";
        ctx.fillText(hl.label, w - padR + 6, yy + 3);
      }
    });
    this.lines.forEach((l) => {
      ctx.strokeStyle = l.color;
      ctx.lineWidth = l.width || 1.3;
      ctx.beginPath();
      let started = false;
      l.points.forEach((p) => {
        if (p.value == null) return;
        if (!started) { ctx.moveTo(xAt(p.time), yAt(p.value)); started = true; }
        else ctx.lineTo(xAt(p.time), yAt(p.value));
      });
      ctx.stroke();
    });
  }
}

function getOrCreateCustomLinePane(containerId) {
  if (state.customPanes[containerId]) return state.customPanes[containerId];
  const pane = new CustomLinePane(containerId);
  state.customPanes[containerId] = pane;
  return pane;
}

function renderPriceChartWithTrades(containerId, candles, trades, strategyName, params, indicatorPaneId) {
  if (typeof LightweightCharts === "undefined" || !candles.length) return;
  const { series } = getOrCreateChart(containerId, "candles");
  series.setData(candles);

  const markers = [];
  for (const t of trades) {
    const isLong = t.side === "long";
    markers.push({
      time: toUnixSeconds(t.entry_time),
      position: isLong ? "belowBar" : "aboveBar",
      color: isLong ? "#21c97a" : "#ff4d5e",
      shape: isLong ? "arrowUp" : "arrowDown",
      text: `${t.side.toUpperCase()} @ ${t.entry_price}  SL ${t.stop_price} / TP ${t.target_price}`,
    });
    markers.push({
      time: toUnixSeconds(t.exit_time),
      position: isLong ? "aboveBar" : "belowBar",
      color: t.pnl >= 0 ? "#21c97a" : "#ff4d5e",
      shape: "circle",
      text: `${t.exit_reason} ${t.pnl >= 0 ? "+" : ""}${t.pnl}`,
    });
  }
  markers.sort((a, b) => a.time - b.time);
  series.setMarkers(markers);

  if (strategyName) renderStrategyOverlay(containerId, indicatorPaneId, strategyName, candles, params || {});
}

// ---------------------------------------------------------------- strategy setup overlays
// Draws what each strategy is actually looking at (VWAP, bands, EMAs, channel,
// opening range, previous-day levels) directly on the candlestick chart, not
// just entry/exit arrows — computed client-side from the same candles already
// on screen, mirroring app/strategies/indicators.py.

const nyDateFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
const nyTimeFmt = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
function nyDateKey(unixSec) {
  return nyDateFmt.format(new Date(unixSec * 1000));
}
function nyMinuteOfDay(unixSec) {
  const [h, m] = nyTimeFmt.format(new Date(unixSec * 1000)).split(":").map(Number);
  return h * 60 + m;
}

function emaSeries(values, period) {
  const k = 2 / (period + 1);
  const out = [values[0]];
  for (let i = 1; i < values.length; i++) out.push(values[i] * k + out[i - 1] * (1 - k));
  return out;
}
function smaSeries(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}
function stdSeries(values, period) {
  const out = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const slice = values.slice(i - period + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / period;
    out[i] = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period);
  }
  return out;
}
function rsiSeries(values, period) {
  const out = new Array(values.length).fill(null);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    const gain = Math.max(change, 0), loss = Math.max(-change, 0);
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    if (i >= period) out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

function clearOverlays(containerId) {
  const custom = state.customCharts[containerId];
  if (custom) { custom.setOverlayLines([]); return; }
  const entry = state.charts[containerId];
  if (!entry) return;
  (entry.overlays || []).forEach((s) => {
    try { entry.chart.removeSeries(s); } catch (e) {}
  });
  entry.overlays = [];
}

function addOverlayLine(containerId, times, values, color, lineWidth) {
  const points = times.map((t, i) => ({ time: t, value: values[i] })).filter((p) => p.value !== null && p.value !== undefined && !isNaN(p.value));
  if (!points.length) return;
  const custom = state.customCharts[containerId];
  if (custom) {
    custom.overlayLines.push({ color, width: lineWidth || 1, points });
    custom.draw();
    return;
  }
  const entry = state.charts[containerId];
  if (!entry) return;
  const series = entry.chart.addLineSeries({ color, lineWidth: lineWidth || 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  series.setData(points);
  entry.overlays = entry.overlays || [];
  entry.overlays.push(series);
}

function renderStrategyOverlay(containerId, indicatorPaneId, strategyName, candles, params) {
  clearOverlays(containerId);
  const paneEl = indicatorPaneId ? document.getElementById(indicatorPaneId) : null;
  if (paneEl) {
    paneEl.classList.add("hidden");
    if (state.charts[indicatorPaneId]) {
      try { state.charts[indicatorPaneId].chart.remove(); } catch (e) {}
      delete state.charts[indicatorPaneId];
      paneEl.innerHTML = "";
    }
  }
  if (!candles.length) return;

  const times = candles.map((c) => c.time);
  const closes = candles.map((c) => c.close);

  switch (strategyName) {
    case "vwap_reversion": {
      let cumPV = 0, cumVol = 0, lastDay = null;
      const vwap = candles.map((c) => {
        const day = nyDateKey(c.time);
        if (day !== lastDay) { cumPV = 0; cumVol = 0; lastDay = day; }
        const vol = c.volume || 1;
        const typical = (c.high + c.low + c.close) / 3;
        cumPV += typical * vol;
        cumVol += vol;
        return cumVol ? cumPV / cumVol : null;
      });
      addOverlayLine(containerId, times, vwap, "#3fd1ff", 2);
      break;
    }
    case "bollinger_breakout": {
      const period = params.bb_period || 20, stdMult = params.bb_std || 2;
      const mid = smaSeries(closes, period);
      const std = stdSeries(closes, period);
      addOverlayLine(containerId, times, mid, "#ff9d00", 1);
      addOverlayLine(containerId, times, mid.map((m, i) => (m != null ? m + stdMult * std[i] : null)), "#21c97a", 1);
      addOverlayLine(containerId, times, mid.map((m, i) => (m != null ? m - stdMult * std[i] : null)), "#ff4d5e", 1);
      break;
    }
    case "ema_crossover": {
      addOverlayLine(containerId, times, emaSeries(closes, params.fast_period || 9), "#21c97a", 1.5);
      addOverlayLine(containerId, times, emaSeries(closes, params.slow_period || 21), "#ff4d5e", 1.5);
      break;
    }
    case "donchian_breakout": {
      const period = params.channel_period || 20;
      const highs = candles.map((c) => c.high), lows = candles.map((c) => c.low);
      const upper = new Array(candles.length).fill(null), lower = new Array(candles.length).fill(null);
      for (let i = period - 1; i < candles.length; i++) {
        upper[i] = Math.max(...highs.slice(i - period + 1, i + 1));
        lower[i] = Math.min(...lows.slice(i - period + 1, i + 1));
      }
      addOverlayLine(containerId, times, upper, "#21c97a", 1);
      addOverlayLine(containerId, times, lower, "#ff4d5e", 1);
      break;
    }
    case "prev_day_range_breakout": {
      const dayHiLo = {};
      candles.forEach((c) => {
        const d = nyDateKey(c.time);
        if (!dayHiLo[d]) dayHiLo[d] = { high: -Infinity, low: Infinity };
        dayHiLo[d].high = Math.max(dayHiLo[d].high, c.high);
        dayHiLo[d].low = Math.min(dayHiLo[d].low, c.low);
      });
      const days = Object.keys(dayHiLo).sort();
      const prevHigh = candles.map((c) => {
        const idx = days.indexOf(nyDateKey(c.time));
        return idx > 0 ? dayHiLo[days[idx - 1]].high : null;
      });
      const prevLow = candles.map((c) => {
        const idx = days.indexOf(nyDateKey(c.time));
        return idx > 0 ? dayHiLo[days[idx - 1]].low : null;
      });
      addOverlayLine(containerId, times, prevHigh, "#21c97a", 1);
      addOverlayLine(containerId, times, prevLow, "#ff4d5e", 1);
      break;
    }
    case "ny_open_breakout":
    case "orb_fade":
    case "orb_retest": {
      const orMinutes = params.opening_range_minutes || 15;
      const openMin = 9 * 60 + 30;
      const rangeEndMin = openMin + orMinutes;
      const ranges = {};
      candles.forEach((c) => {
        const min = nyMinuteOfDay(c.time);
        if (min >= openMin && min < rangeEndMin) {
          const day = nyDateKey(c.time);
          if (!ranges[day]) ranges[day] = { high: -Infinity, low: Infinity };
          ranges[day].high = Math.max(ranges[day].high, c.high);
          ranges[day].low = Math.min(ranges[day].low, c.low);
        }
      });
      const high = candles.map((c) => { const r = ranges[nyDateKey(c.time)]; return r && r.high > -Infinity ? r.high : null; });
      const low = candles.map((c) => { const r = ranges[nyDateKey(c.time)]; return r && r.low < Infinity ? r.low : null; });
      addOverlayLine(containerId, times, high, "#ff9d00", 1);
      addOverlayLine(containerId, times, low, "#ff9d00", 1);
      break;
    }
    case "rsi_reversal": {
      if (!paneEl) break;
      const period = params.rsi_period || 14;
      const rsi = rsiSeries(closes, period);
      const points = times.map((t, i) => ({ time: t, value: rsi[i] })).filter((p) => p.value != null);
      const overbought = params.overbought || 70, oversold = params.oversold || 30;
      if (indicatorPaneId === "replay-indicator-pane") {
        const pane = getOrCreateCustomLinePane(indicatorPaneId);
        pane.setLines([{ color: "#3fd1ff", points }]);
        pane.setHLines([
          { value: overbought, color: "#ff4d5e", label: "OB" },
          { value: oversold, color: "#21c97a", label: "OS" },
        ]);
      } else {
        const { series } = getOrCreateChart(indicatorPaneId, "line");
        series.applyOptions({ color: "#3fd1ff" });
        series.setData(points);
        const chart = state.charts[indicatorPaneId].chart;
        chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
        series.createPriceLine({ price: overbought, color: "#ff4d5e", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "OB" });
        series.createPriceLine({ price: oversold, color: "#21c97a", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "OS" });
      }
      paneEl.classList.remove("hidden");
      break;
    }
    case "macd_cross": {
      if (!paneEl) break;
      const fastP = emaSeries(closes, params.fast || 12), slowP = emaSeries(closes, params.slow || 26);
      const macdLine = fastP.map((f, i) => f - slowP[i]);
      const signalLine = emaSeries(macdLine, params.signal || 9);
      if (indicatorPaneId === "replay-indicator-pane") {
        const pane = getOrCreateCustomLinePane(indicatorPaneId);
        pane.setLines([
          { color: "#ff9d00", points: times.map((t, i) => ({ time: t, value: macdLine[i] })) },
          { color: "#3fd1ff", points: times.map((t, i) => ({ time: t, value: signalLine[i] })) },
        ]);
        pane.setHLines([]);
      } else {
        const { series } = getOrCreateChart(indicatorPaneId, "line");
        series.applyOptions({ color: "#ff9d00" });
        series.setData(times.map((t, i) => ({ time: t, value: macdLine[i] })));
        const sigSeries = state.charts[indicatorPaneId].chart.addLineSeries({ color: "#3fd1ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        sigSeries.setData(times.map((t, i) => ({ time: t, value: signalLine[i] })));
        state.charts[indicatorPaneId].overlays = [sigSeries];
      }
      paneEl.classList.remove("hidden");
      break;
    }
    case "liquidity_sweep": {
      const lookback = params.lookback_bars || 20;
      const highs = candles.map((c) => c.high), lows = candles.map((c) => c.low);
      const swingHigh = new Array(candles.length).fill(null), swingLow = new Array(candles.length).fill(null);
      for (let i = lookback; i < candles.length; i++) {
        swingHigh[i] = Math.max(...highs.slice(i - lookback, i));
        swingLow[i] = Math.min(...lows.slice(i - lookback, i));
      }
      addOverlayLine(containerId, times, swingHigh, "#ff9d00", 1);
      addOverlayLine(containerId, times, swingLow, "#ff9d00", 1);
      break;
    }
    default:
      break; // inside_bar_breakout, fair_value_gap, order_block etc: entry/exit markers already convey the setup
  }
}

function toUnixSeconds(isoOrSql) {
  const iso = isoOrSql.includes("T") ? isoOrSql : isoOrSql.replace(" ", "T") + "Z";
  return Math.floor(new Date(iso).getTime() / 1000);
}

function renderEquityCurve(containerId, equityCurve, fallbackStartTime) {
  if (typeof LightweightCharts === "undefined") {
    console.error("LightweightCharts library failed to load; skipping chart render.");
    return;
  }
  const { series } = getOrCreateChart(containerId, "line");
  const points = equityCurve.map((p, i) => ({
    time: toUnixSeconds(p.time || fallbackStartTime),
    value: p.equity,
  }));
  points.sort((a, b) => a.time - b.time);
  // lightweight-charts requires strictly ascending unique times
  const deduped = [];
  for (const pt of points) {
    if (deduped.length && deduped[deduped.length - 1].time >= pt.time) {
      pt.time = deduped[deduped.length - 1].time + 1;
    }
    deduped.push(pt);
  }
  series.setData(deduped);
}

// ---------------------------------------------------------------- replay engine (Trainer hero)

class ReplayEngine {
  constructor(containerId, candles, trades, startingBalance, paramsBox) {
    this.candles = candles;
    this.trades = [...trades].sort((a, b) => toUnixSeconds(a.entry_time) - toUnixSeconds(b.entry_time));
    this.idx = 0;
    this.speed = 4;
    this.playing = false;
    this.timer = null;
    this.markers = [];
    this.entered = new Set();
    this.exited = new Set();
    this.startingBalance = startingBalance;
    this.equity = startingBalance;
    this.onTradeClose = null; // (trade, equityBefore)
    this.onEquity = null; // (equity)
    this.onDone = null;
    this.series = getOrCreateCustomChart(containerId, { paramsBox });
    this.series.setData([]);
    this.series.setMarkers([]);
  }

  play() {
    if (this.idx >= this.candles.length) return;
    this.playing = true;
    this.timer = setTimeout(() => this._tick(), 45);
  }

  pause() {
    this.playing = false;
    if (this.timer) clearTimeout(this.timer);
  }

  restart() {
    this.pause();
    this.idx = 0;
    this.entered.clear();
    this.exited.clear();
    this.markers = [];
    this.equity = this.startingBalance;
    this.series.setData([]);
    this.series.setMarkers([]);
    if (this.series.setPriceLines) this.series.setPriceLines([]);
    this.play();
  }

  setSpeed(n) {
    this.speed = n;
  }

  _tick() {
    if (!this.playing) return;
    let paused = false;
    for (let i = 0; i < this.speed && this.idx < this.candles.length; i++) {
      const bar = this.candles[this.idx];
      this.series.update(bar);
      if (this._checkTrades(bar.time)) paused = true;
      this.idx++;
      if (paused) break;
    }
    if (this.idx >= this.candles.length) {
      this.playing = false;
      if (this.onDone) this.onDone();
      return;
    }
    if (paused) {
      this.playing = false; // resumed externally (by caller) after the popup delay
      return;
    }
    this.timer = setTimeout(() => this._tick(), 45);
  }

  _checkTrades(barTime) {
    let paused = false;
    for (const t of this.trades) {
      const entryT = toUnixSeconds(t.entry_time);
      const exitT = toUnixSeconds(t.exit_time);
      if (!this.entered.has(t) && entryT <= barTime) {
        this.entered.add(t);
        this._addMarker(t, "entry");
      }
      if (!this.exited.has(t) && exitT <= barTime) {
        this.exited.add(t);
        this._addMarker(t, "exit");
        const equityBefore = this.equity;
        this.equity += t.pnl;
        if (this.onEquity) this.onEquity(this.equity);
        if (this.onTradeClose) this.onTradeClose(t, equityBefore);
        paused = true;
      }
    }
    return paused;
  }

  _addMarker(t, kind) {
    const isLong = t.side === "long";
    if (kind === "entry") {
      this.markers.push({
        time: toUnixSeconds(t.entry_time),
        price: t.entry_price,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#21c97a" : "#ff4d5e",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: `${t.side.toUpperCase()} @ ${t.entry_price}`,
      });
      if (this.series.setPriceLines) {
        this.series.setPriceLines([
          { price: t.stop_price, color: "#ff4d5e", label: `SL ${t.stop_price}` },
          { price: t.target_price, color: "#21c97a", label: `TP ${t.target_price}` },
        ]);
      }
    } else {
      this.markers.push({
        time: toUnixSeconds(t.exit_time),
        price: t.exit_price,
        position: isLong ? "aboveBar" : "belowBar",
        color: t.pnl >= 0 ? "#21c97a" : "#ff4d5e",
        shape: "circle",
        text: `${t.exit_reason} ${t.pnl >= 0 ? "+" : ""}${t.pnl}`,
      });
      if (this.series.setPriceLines) this.series.setPriceLines([]);
    }
    this.markers.sort((a, b) => a.time - b.time);
    this.series.setMarkers(this.markers);
  }
}

// ---------------------------------------------------------------- metrics + trades rendering

const METRIC_LABELS = {
  net_pnl: "NET PNL (after costs)",
  gross_pnl: "GROSS PNL (before commission)",
  return_pct: "RETURN",
  win_rate_pct: "WIN RATE",
  profit_factor: "PROFIT FACTOR",
  reward_risk_ratio: "REWARD:RISK",
  max_drawdown_pct: "MAX DD",
  sharpe_approx: "SHARPE (approx)",
  total_trades: "TRADES",
};

function renderMetrics(containerId, metrics) {
  const el = document.getElementById(containerId);
  el.innerHTML = Object.entries(METRIC_LABELS)
    .map(([key, label]) => {
      let val = metrics[key];
      let cls = "";
      if (key === "net_pnl" || key === "gross_pnl") { val = fmtMoney(val); cls = pnlClass(metrics[key]); }
      else if (key === "return_pct" || key === "win_rate_pct" || key === "max_drawdown_pct") {
        cls = key === "return_pct" ? pnlClass(metrics[key]) : "";
        val = `${val}%`;
      } else if (key === "reward_risk_ratio") { val = val === null || val === undefined ? "∞" : `${val} : 1`; }
      else if (val === null || val === undefined) { val = "∞"; }
      return `<div class="metric-tile flash"><div class="label">${label}</div><div class="value ${cls}">${val}</div></div>`;
    })
    .join("");
}

function renderTrades(tableId, trades) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = trades
    .map(
      (t) => `
      <tr>
        <td>${fmtTime(t.entry_time)}</td>
        <td>${fmtTime(t.exit_time)}</td>
        <td>${t.side.toUpperCase()}</td>
        <td>${t.entry_price}</td>
        <td>${t.exit_price}</td>
        <td class="muted">${t.exit_reason}</td>
        <td class="${pnlClass(t.pnl)}">${fmtMoney(t.pnl)}</td>
        <td>${fmtMoney(t.equity_after)}</td>
      </tr>`
    )
    .join("");
}

// ---------------------------------------------------------------- backtest tab

async function runBacktest() {
  const strategy = document.getElementById("bt-strategy").value;
  const body = {
    symbol: document.getElementById("bt-symbol").value,
    strategy,
    interval: document.getElementById("bt-interval").value,
    lookback_days: parseInt(document.getElementById("bt-lookback").value, 10) || null,
    starting_balance: parseFloat(document.getElementById("bt-balance").value),
    entry_style: document.getElementById("bt-entry-style").value,
    htf_confirm_bars: parseInt(document.getElementById("bt-htf-confirm").value, 10) || 0,
    params: readParams("bt-params"),
  };
  setStatus("RUNNING BACKTEST...");
  try {
    const result = await api("/backtest", { method: "POST", body: JSON.stringify(body) });
    document.getElementById("bt-empty").classList.add("hidden");
    document.getElementById("bt-results").classList.remove("hidden");
    renderMetrics("bt-metrics", result.metrics);
    renderEquityCurve("bt-equity-chart", result.equity_curve, result.data_range.start);
    renderTrades("bt-trades-table", result.trades);
    document.getElementById("bt-price-panel").classList.remove("hidden");
    renderPriceChartWithTrades("bt-price-chart", result.candles, result.trades, strategy, result.params_used, "bt-indicator-pane");
    setStatus(`BACKTEST DONE — ${result.bars_used} bars, ${result.metrics.total_trades} trades`);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

// ---------------------------------------------------------------- optimizer tab

async function runOptimize() {
  const strategy = document.getElementById("opt-strategy").value;
  const body = {
    symbol: document.getElementById("opt-symbol").value,
    strategy,
    interval: document.getElementById("opt-interval").value,
    lookback_days: parseInt(document.getElementById("opt-lookback").value, 10) || null,
    n_iterations: parseInt(document.getElementById("opt-iterations").value, 10),
    train_frac: parseFloat(document.getElementById("opt-trainfrac").value),
  };
  setStatus("TRAINING (searching parameter space)...");
  document.getElementById("opt-status").textContent = "Running backtests across the parameter space, please wait...";
  try {
    const result = await api("/optimizer/run", { method: "POST", body: JSON.stringify(body) });
    document.getElementById("opt-empty").classList.add("hidden");
    const table = document.getElementById("opt-table");
    table.classList.remove("hidden");
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = result.results
      .map((r, i) => {
        const paramsStr = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(", ");
        return `
        <tr>
          <td>${i + 1}</td>
          <td class="small">${paramsStr}</td>
          <td>${r.train_metrics.profit_factor ?? "∞"}</td>
          <td>${r.train_metrics.win_rate_pct}%</td>
          <td>${r.test_metrics.profit_factor ?? "∞"}</td>
          <td>${r.test_metrics.win_rate_pct}%</td>
          <td>${r.test_metrics.total_trades}</td>
          <td class="${pnlClass(r.score)}">${r.score}</td>
          <td><button class="btn btn-small promote-btn" data-idx="${i}">→ ACCOUNT</button></td>
        </tr>`;
      })
      .join("");

    tbody.querySelectorAll(".promote-btn").forEach((btn) => {
      btn.addEventListener("click", () => promoteToAccount(strategy, body.symbol, result.results[btn.dataset.idx].params));
    });

    document.getElementById("opt-status").textContent =
      `Run #${result.run_id} — ${result.results.length} viable parameter sets survived out-of-sample testing.`;
    setStatus(`TRAINING DONE — run #${result.run_id}`);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
    document.getElementById("opt-status").textContent = "Failed: " + e.message;
  }
}

// Tiny bar-per-fold strip: each walk-forward fold's out-of-sample edge t-stat as a
// baseline-anchored bar (green above the line, red below) — a quick "was this good
// across every window it was tested on, or did it get lucky on just one" read, echoed
// from the same fold_scores the Strategy Lab's full chart uses.
function buildFoldBarsHtml(foldScores) {
  if (!foldScores || !foldScores.length) return "";
  const maxAbs = Math.max(...foldScores.map(Math.abs), 0.5);
  const bars = foldScores
    .map((s) => {
      const pct = Math.min(100, (Math.abs(s) / maxAbs) * 100);
      return `<div class="fold-bar ${s >= 0 ? "pos" : "neg"}" style="height:${Math.max(pct, 6).toFixed(0)}%" title="fold score ${s.toFixed(2)}"></div>`;
    })
    .join("");
  return `<div class="model-folds">${bars}</div>`;
}

function renderAllLeaderboard(result, symbol) {
  // state.lastLeaderboard keeps every viable result (the hero auto-cycle showcase reads
  // from it) — the card grid itself only shows the top 10 by score, since "leaderboard"
  // means the best of the batch, not every parameter set that happened to pass the gate.
  state.lastLeaderboard = { results: result.results, symbol };
  document.getElementById("all-empty").classList.add("hidden");
  const grid = document.getElementById("all-cards");
  grid.classList.remove("hidden");

  const topResults = result.results.slice(0, 10);
  const scores = topResults.map((r) => r.score);
  const minScore = Math.min(...scores), maxScore = Math.max(...scores);
  const scoreRange = maxScore - minScore || 1;

  grid.innerHTML = topResults
    .map((r, i) => {
      const flagged = r.diverged || r.too_smooth || r.exceeds_burn_limit || r.regime_inconsistent || r.clustering_inflated;
      const gatePill = flagged
        ? `<span class="pill pill-gate-fail" title="${[r.diverged && "diverged", r.too_smooth && "too smooth", r.exceeds_burn_limit && "exceeds burn limit", r.regime_inconsistent && "regime inconsistent", r.clustering_inflated && "clustering inflated"].filter(Boolean).join(", ")}">FAIL</span>`
        : (r.test_metrics.total_trades < 100
          ? `<span class="pill pill-gate-warn" title="Only ${r.test_metrics.total_trades} test trades: far too few to trust (100+ needed). No red flags were raised, but the profit factor and win rate on this few trades are NOT reliable, and a high win rate here can be luck.">LOW SAMPLE &middot; ${r.test_metrics.total_trades}</span>`
          : `<span class="pill pill-gate-pass" title="No quality flags raised (not diverged, not too smooth, no regime or clustering problems). This is a screen, not proof: the multi-year check decides what goes live.">NO FLAGS</span>`);
      const paramsHtml = Object.entries(r.params).map(([k, v]) => `<span class="pill pill-param">${k}=${v}</span>`).join("");
      const scorePct = Math.max(4, ((r.score - minScore) / scoreRange) * 100);
      const accent = equityLineColor(i);
      // Joined against the already-cached strategy list (loadStrategies()) rather than a
      // fresh backend call — a strategy's skill level is a slow-moving stat, not worth a
      // round-trip per leaderboard render. Note: state.strategies is cached for the whole
      // session, so this won't reflect training progress from the current session until reload.
      const stratDef = state.strategies && state.strategies.find((x) => x.name === r.strategy);
      const skillPill = stratDef && stratDef.skill
        ? `<span class="pill pill-skill-${stratDef.skill.level.toLowerCase()}" title="${stratDef.skill.level_description || ""}">${stratDef.skill.level.toUpperCase()}</span>`
        : "";
      return `
      <div class="model-card" data-idx="${i}" data-strategy="${r.strategy}" data-params='${JSON.stringify(r.params).replace(/'/g, "&#39;")}' style="--model-accent:${accent};">
        <div class="model-card-top">
          <span class="model-rank">#${i + 1}</span>
          <span class="model-name">${r.strategy}</span>
          ${skillPill}
          ${gatePill}
        </div>
        <div class="model-params">${paramsHtml}</div>
        <div class="model-metrics">
          <div class="mm"><span class="mm-label">TRAIN PF</span><span class="mm-value">${r.train_metrics.profit_factor ?? "∞"}</span></div>
          <div class="mm"><span class="mm-label">TEST PF</span><span class="mm-value">${r.test_metrics.profit_factor ?? "∞"}</span></div>
          <div class="mm"><span class="mm-label">WIN%</span><span class="mm-value">${r.test_metrics.win_rate_pct}%</span></div>
          <div class="mm"><span class="mm-label">TRADES</span><span class="mm-value">${r.test_metrics.total_trades}</span></div>
        </div>
        <div class="model-score-row">
          <span class="model-score-label">SCORE</span>
          <span class="model-score-value ${pnlClass(r.score)}">${r.score.toFixed(2)}</span>
        </div>
        <div class="model-score-bar"><div class="model-score-fill" style="width:${scorePct.toFixed(0)}%"></div></div>
        ${buildFoldBarsHtml(r.fold_scores)}
        <div class="model-actions">
          <button class="btn btn-small promote-all-btn" data-idx="${i}">→ ACCOUNT</button>
          <button class="btn btn-small lab-btn" data-idx="${i}">🔬 LAB</button>
        </div>
      </div>`;
    })
    .join("");

  grid.querySelectorAll(".promote-all-btn").forEach((btn) => {
    const r = topResults[btn.dataset.idx];
    btn.addEventListener("click", () => promoteToAccount(r.strategy, symbol, r.params));
  });
  grid.querySelectorAll(".lab-btn").forEach((btn) => {
    const r = topResults[btn.dataset.idx];
    btn.addEventListener("click", () => openStrategyLab(r, symbol));
  });

  document.getElementById("all-status").textContent =
    `run #${result.run_id} — top ${topResults.length} of ${result.results.length} results (${result.strategies_trained} strategies trained), ranked out-of-sample`;

  if (window.gsap) gsap.from("#all-cards .model-card", { opacity: 0, y: 14, scale: 0.97, duration: 0.45, stagger: 0.05, ease: "power2.out" });
  if (state.currentHeroStrategy) highlightShowcasedModelCard(state.currentHeroStrategy, state.currentHeroParams);
}

function openStrategyLab(r, symbol) {
  const panel = document.getElementById("strategy-lab-panel");
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });

  document.getElementById("lab-strategy-name").textContent = `${r.strategy} · ${symbol}`;
  document.getElementById("lab-params").textContent = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(", ");

  const flagged = r.diverged || r.too_smooth || r.exceeds_burn_limit || r.regime_inconsistent || r.clustering_inflated;
  const badge = document.getElementById("lab-verdict-badge");
  badge.className = "pill " + (flagged ? "pill-gate-fail" : "pill-gate-pass");
  badge.textContent = flagged ? "FAILS GATE" : "PASSES GATE";

  const m = r.test_metrics;
  renderMetrics("lab-metrics", m);

  document.getElementById("lab-fold-scores").textContent = r.fold_scores
    ? `Fold scores (out-of-sample, oldest→newest window): ${r.fold_scores.map((s) => s.toFixed(3)).join(", ")}`
    : "";

  document.getElementById("lab-mc-results").classList.add("hidden");
  document.getElementById("lab-mc-status").textContent = "";
  document.getElementById("lab-stress-table").classList.add("hidden");
  document.getElementById("lab-stress-status").textContent = "";

  document.getElementById("btn-run-montecarlo").onclick = () => runStrategyLabMonteCarlo(r, symbol);
  document.getElementById("btn-run-stresstest").onclick = () => runStrategyLabStressTest(r, symbol);
}

function _replayFade(el) {
  if (window.gsap) {
    gsap.fromTo(el, { opacity: 0, y: 12, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.55, ease: "power2.out" });
  } else {
    el.classList.remove("lab-fade");
    void el.offsetWidth; // force reflow so the animation restarts
    el.classList.add("lab-fade");
  }
}

// Manual, on-demand Monte Carlo run from the Strategy Lab panel (🔬 LAB button per
// leaderboard row) — separate from the hero auto-cycle's own Monte Carlo call, but
// shares the same tile-rendering via renderMonteCarloTiles (defined near the hero
// auto-cycle code) so there's one implementation of that markup, not two.
async function runStrategyLabMonteCarlo(r, symbol) {
  const statusEl = document.getElementById("lab-mc-status");
  statusEl.textContent = "Running 1,000 bootstrap simulations…";
  try {
    const res = await api("/backtest/montecarlo", {
      method: "POST",
      body: JSON.stringify({ symbol, strategy: r.strategy, params: r.params, n_simulations: 1000 }),
    });
    const el = document.getElementById("lab-mc-results");
    el.classList.remove("hidden");
    renderMonteCarloTiles(el, res.monte_carlo);
    statusEl.textContent = `Done — resampled ${res.monte_carlo.n_trades} trades across ${res.monte_carlo.n_simulations} simulated paths.`;
  } catch (e) {
    statusEl.textContent = `ERROR: ${e.message}`;
  }
}

async function runStrategyLabStressTest(r, symbol) {
  const statusEl = document.getElementById("lab-stress-status");
  statusEl.textContent = "Fetching historical crash-window data and running backtests…";
  try {
    const res = await api("/backtest/stress-test", {
      method: "POST",
      body: JSON.stringify({ symbol, strategy: r.strategy, params: r.params, interval: "15m" }),
    });
    const table = document.getElementById("lab-stress-table");
    table.classList.remove("hidden");
    table.querySelector("tbody").innerHTML = res.windows
      .map((w) => {
        if (w.error) return `<tr><td>${w.name}</td><td colspan="5" class="muted small">${w.error}</td></tr>`;
        return `<tr>
          <td>${w.name}</td>
          <td class="small">${w.start} → ${w.end}</td>
          <td>${w.total_trades}</td>
          <td class="${pnlClass(w.return_pct)}">${w.return_pct}%</td>
          <td>${fmtMoney(w.max_drawdown)} (${w.max_drawdown_pct}%)</td>
          <td class="${w.would_have_burned_account ? "pnl-neg" : ""}">${w.would_have_burned_account ? "YES" : "no"}</td>
        </tr>`;
      })
      .join("");
    statusEl.textContent = `Done — ${res.windows.length} historical stress windows tested.`;
  } catch (e) {
    statusEl.textContent = `ERROR: ${e.message}`;
  }
}

const TRAINER_STARTING_BALANCE = 50000;

// ---------------------------------------------------------------- always-on trainer

function trainerStageText(status) {
  if (!status.training_now) return "LIVE — TRAINING 24/7";
  const sym = status.stage_symbol ? `${status.stage_symbol} · ` : "";
  switch (status.stage) {
    case "fetching_data":
      return `📡 ${sym}fetching market data…`;
    case "training": {
      const d = status.stage_detail;
      if (!d) return `🔍 ${sym}scanning…`;
      // Only call out the adaptive budget when it's meaningfully different from
      // baseline — "promising" strategies get a bigger share, "stalled" ones a
      // smaller one (see trainer_service._allocate_iterations); cold_start/baseline
      // don't need to say anything extra.
      const extra =
        d.label === "promising" ? `, ${d.iterations} iters — promising`
        : d.label === "stalled" ? `, ${d.iterations} iters — stalled`
        : "";
      // Strategies that never clear the fast 60-day screen's trade-count gate get
      // routed through a 2-year Dukascopy search instead (see trainer_service.
      // _run_long_window_strategies) — call that out so it doesn't look like the same
      // fast pass every other strategy gets.
      const windowTag = d.window === "2yr" ? " · 2yr window" : "";
      return `🔍 ${sym}scanning ${d.strategy}${windowTag} (${d.index}/${d.total}${extra})…`;
    }
    case "deep_validating": {
      const d = status.stage_detail;
      return `🧪 ${sym}deep-validating ${d ? d.strategy : "candidate"} (8yr)…`;
    }
    case "checking_robustness": {
      const d = status.stage_detail;
      return `🧭 ${sym}checking ${d ? d.strategy : "candidate"} isn't a lucky spike…`;
    }
    case "saving":
      return `💾 ${sym}saving results…`;
    case "live_feedback":
      return `📈 ${sym}checking live trades against the backtest…`;
    default:
      return "TRAINING NOW…";
  }
}

function updateLiveBar(status) {
  const dot = document.getElementById("live-dot");
  const text = document.getElementById("live-text");
  if (!dot) return;
  dot.className = "live-dot" + (status.training_now ? " busy" : "") + (status.last_error ? " dead" : "");
  text.textContent = trainerStageText(status);
  document.getElementById("live-total-runs").textContent = status.total_runs;
  document.getElementById("live-last-run").textContent = status.last_run
    ? `${status.last_run.symbol} · ${status.last_run.top_strategy || "—"} (${fmtTimeAgo(status.last_run.created_at)})`
    : "—";
  state.trainerNextRunAt = status.next_run_at;

  const progWrap = document.getElementById("live-progress-wrap");
  const progFill = document.getElementById("live-progress-fill");
  const progLabel = document.getElementById("live-progress-label");
  if (progWrap && progFill && progLabel) {
    if (status.training_now && status.stage === "training" && status.stage_detail) {
      const { index, total } = status.stage_detail;
      progWrap.classList.remove("hidden");
      progFill.classList.remove("indeterminate");
      progFill.style.width = `${Math.round((index / total) * 100)}%`;
      progLabel.textContent = `${index}/${total} strategies`;
    } else if (status.training_now) {
      progWrap.classList.remove("hidden");
      progFill.classList.add("indeterminate");
      const labels = { fetching_data: "loading market data", deep_validating: "deep-validating (8yr)", checking_robustness: "checking robustness", saving: "saving results" };
      progLabel.textContent = labels[status.stage] || "working…";
    } else {
      progWrap.classList.add("hidden");
    }
  }
}

function tickTrainerCountdown() {
  const el = document.getElementById("live-next-run");
  if (!el || !state.trainerNextRunAt) return;
  const remaining = Math.max(0, Math.round(state.trainerNextRunAt - Date.now() / 1000));
  if (remaining <= 0) { el.textContent = "any moment…"; return; }
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  el.textContent = `${m}:${s.toString().padStart(2, "0")}`;
}

async function pollTrainerOnce() {
  try {
    const status = await api("/trainer/status");
    updateLiveBar(status);
  } catch (e) {
    return;
  }
  try {
    const champ = await api(`/trainer/champion?symbol=${state.trainerSymbol}`);
    // Compare the monotonic champion version, not the score: re-scoring can lower a score
    // without any new champion, and a new champion can have a lower same-scale score.
    state.lastChampionVersion = state.lastChampionVersion || {};
    const prevVersion = state.lastChampionVersion[state.trainerSymbol];
    state.lastChampionVersion[state.trainerSymbol] = champ.version;
    state.pollCount = (state.pollCount || 0) + 1;
    if (prevVersion === undefined || state.pollCount % 6 === 0) loadPipeline(state.trainerSymbol);
    if (prevVersion !== undefined && champ.version !== prevVersion) {
      showToast(`🏆 NEW ${champ.tier === "confirmed" ? "CHAMPION" : "LEADER"}: ${champ.strategy} on ${state.trainerSymbol} (score ${champ.score.toFixed(2)})`);
      loadPipeline(state.trainerSymbol);
      // A genuine new champion interrupts whatever the auto-cycle is showing, shows the
      // champion immediately, then hands back to cycling — a real result takes priority
      // over "it happened to be strategy #4's turn."
      const wasCycling = state.heroCycleActive;
      if (wasCycling) stopHeroCycle();
      await loadChampionAndReplay(state.trainerSymbol);
      await loadHistory(state.trainerSymbol);
      if (wasCycling) startHeroCycle();
    } else {
      state.lastChampionScore[state.trainerSymbol] = champ.score;
    }
  } catch (e) {
    // no champion yet for this symbol — fine, still waiting on the first cycle
  }
}

function _pipelineCard(slot, tier, why) {
  if (!slot) {
    return `<div class="pipeline-card is-empty"><div class="pc-tier">${tier.toUpperCase()}</div>
      <div class="pc-name" style="color:var(--muted)">none yet</div>
      <div class="pc-meta">${why ? `<b>Why not yet:</b> ${why}` : tier === "confirmed" ? "Needs multi-year validation, Apex pass rate and robustness." : "Waiting for a candidate that passes every gate."}</div></div>`;
  }
  const hist = (slot.rescore_history || []).map((h) => h.score).filter((v) => v != null);
  const spark = hist.length > 1 ? buildSparklineSvg(hist, { w: 240, h: 34, cls: "" }) : "";
  const score = tier === "confirmed" && slot.deep_score != null ? `deep ${slot.deep_score.toFixed(2)}` : `score ${(slot.score ?? 0).toFixed(2)}`;
  const ax = slot.apex;
  const risk = ax && ax.recommended_risk_dollars ? ` @ $${ax.recommended_risk_dollars} risk` : "";
  const apex = ax && ax.pass_rate != null
    ? ` · ${ax.pass_rate >= 0.6
        ? `<span class="pill pill-gate-pass">PASSES NOSTRO ${Math.round(ax.pass_rate * 100)}%${risk}</span>`
        : `<span class="pill pill-idle" title="${ax.reason || ""}">APEX ${Math.round(ax.pass_rate * 100)}% / breach ${Math.round((ax.breach_rate || 0) * 100)}%${risk}</span>`}`
    : "";
  const cert = slot.certification;
  const certTip = cert && cert.checks ? cert.checks.map((c) => `${c.ok ? "PASS" : "FAIL"} ${c.name}${c.hard ? "" : " (soft)"}: ${c.value}`).join("&#10;").replace(/"/g, "'") : "";
  const certPill = cert && cert.checks && cert.checks.length
    ? `<div class="pc-cert"><span class="pill ${cert.status === "certified" ? "pill-gate-pass" : cert.status === "watch" ? "pill-watch" : "pill-idle"}" title="${certTip}">STRESS-TESTED: ${cert.status.toUpperCase()} ${cert.passed}/${cert.of}${cert.months ? ` · ${cert.months} mo` : ""}</span>
        ${(cert.hard_failed || []).length ? `<span class="muted small"> failed: ${cert.hard_failed.join(", ").replace(/_/g, " ")}</span>` : ""}</div>`
    : "";
  return `<div class="pipeline-card ${tier === "confirmed" ? "is-confirmed" : ""}">
    <div class="pc-tier">${tier.toUpperCase()} · v${slot.version}</div>
    <div class="pc-name">${slot.strategy}</div>
    ${certPill}
    <div class="pc-meta">${score}${apex}<br>since ${fmtTimeAgo(slot.promoted_at)} · re-verified ${fmtTimeAgo(slot.last_rescored_at)}
      ${slot.consecutive_fails ? `· <span class="pnl-neg">${slot.consecutive_fails} failed re-check(s)</span>` : ""}</div>
    <div class="pc-spark">${spark}</div></div>`;
}

async function loadPipeline(symbol) {
  const cards = document.getElementById("pipeline-cards");
  if (!cards) return;
  try {
    const p = await api(`/trainer/pipeline?symbol=${symbol}`);
    if (symbol !== state.trainerSymbol) return;
    document.getElementById("pipeline-version").textContent = `v${p.champion_version}`;
    const lastConfirmed = (p.attempts || []).find((a) => a.tier === "confirmed" && a.outcome !== "promoted");
    const confirmedWhy = lastConfirmed ? `${lastConfirmed.strategy ? lastConfirmed.strategy + ": " : ""}${lastConfirmed.reason}` : "";
    cards.innerHTML = _pipelineCard(p.leader, "leader") + _pipelineCard(p.confirmed, "confirmed", confirmedWhy);
    const why = document.getElementById("pipeline-why");
    if (!p.blockers.length) {
      why.innerHTML = `<div class="pipeline-why-title">NO CURRENT BLOCKERS</div>`;
    } else {
      why.innerHTML = `<div class="pipeline-why-title">WHY IT HASN'T UPGRADED FURTHER (last ${p.recent_window} decisions)</div><ul>` +
        p.blockers.map((b) => `<li><span class="pw-count">${b.count}x</span><span class="pw-stage">${b.tier.toUpperCase()} · ${b.stage}</span><span>${b.text}</span></li>`).join("") +
        `</ul>`;
    }
  } catch (e) { /* pipeline is informational; never break the tab */ }
}

// Generalized version of what used to be "load THE champion and replay it" — takes any
// strategy+params so the hero section can show an arbitrary leaderboard entry, not just
// the persisted champion. Returns { btResult } once the animated replay has genuinely
// finished playing (via ReplayEngine.onDone), so callers can chain the next stage (e.g.
// Monte Carlo) off real completion instead of a guessed timer.
function loadStrategyReplay(strategy, params, symbol, opts = {}) {
  const { isChampion = false, scoreLabel = "" } = opts;
  const heroTraining = document.getElementById("hero-training");
  const heroReplay = document.getElementById("hero-replay");

  return (async () => {
    await loadStrategies();
    const stratDef = state.strategies.find((s) => s.name === strategy);

    const btResult = await api("/backtest", {
      method: "POST",
      body: JSON.stringify({
        symbol, strategy, interval: "5m", lookback_days: 15,
        starting_balance: TRAINER_STARTING_BALANCE, params,
      }),
    });

    heroTraining.classList.add("hidden");
    heroReplay.classList.remove("hidden");
    document.getElementById("champion-line").textContent = isChampion
      ? `🏆 ${strategy.toUpperCase()} on ${symbol} — ${scoreLabel}`
      : `📊 SHOWCASE — ${strategy.toUpperCase()} on ${symbol} — ${scoreLabel}`;
    document.getElementById("champion-why").textContent =
      `${stratDef ? stratDef.description : ""} Replaying the last 15 days (${btResult.trades.length} trades) at 4x.`;
    document.getElementById("champion-params").innerHTML = Object.entries(params)
      .map(([k, v]) => `<span class="pill pill-param">${k}=${v}</span>`)
      .join("");
    state.currentHeroStrategy = strategy;
    state.currentHeroParams = params;
    highlightShowcasedModelCard(strategy, params);

    const mcEl = document.getElementById("hero-montecarlo");
    mcEl.classList.add("hidden");
    mcEl.innerHTML = "";

    if (state.replayEngine) state.replayEngine.pause();
    const paramsBox = {
      title: strategy.toUpperCase(),
      rows: [["symbol", symbol], ...Object.entries(params)],
    };
    const engine = new ReplayEngine("replay-chart", btResult.candles, btResult.trades, TRAINER_STARTING_BALANCE, paramsBox);
    renderStrategyOverlay("replay-chart", "replay-indicator-pane", strategy, btResult.candles, params);
    state.replayEngine = engine;
    updateReplayEquity(engine.equity);
    document.getElementById("hero-metrics").classList.add("hidden");

    engine.onEquity = (eq) => updateReplayEquity(eq);
    engine.onTradeClose = (trade, equityBefore) => {
      showTradePopup(trade, equityBefore);
      setTimeout(() => engine.play(), 1200);
    };

    document.querySelectorAll(".speed-btn").forEach((b) => b.classList.remove("active"));
    document.querySelector('.speed-btn[data-speed="4"]').classList.add("active");
    engine.setSpeed(4);

    return new Promise((resolve) => {
      engine.onDone = async () => {
        renderMetrics("hero-metrics", btResult.metrics);
        document.getElementById("hero-metrics").classList.remove("hidden");
        _replayFade(document.getElementById("hero-metrics"));
        // Whatever strategy is actually on screen gets its Monte Carlo run — the
        // champion's very first showing included, not just while auto-cycling.
        await _showHeroMonteCarlo(strategy, params, symbol);
        resolve({ btResult });
      };
      engine.play();
    });
  })();
}

async function loadChampionAndReplay(symbol) {
  let champion;
  try {
    champion = await api(`/trainer/champion?symbol=${symbol}`);
  } catch (e) {
    if (state.replayEngine) state.replayEngine.pause();
    document.getElementById("hero-replay").classList.add("hidden");
    document.getElementById("hero-training").classList.remove("hidden");
    document.getElementById("hero-status-text").textContent = "WAITING FOR FIRST TRAINING CYCLE…";
    document.getElementById("hero-status-sub").textContent =
      `The background trainer is warming up on ${symbol} — this happens once, then results are always ready instantly.`;
    return null;
  }
  state.lastChampionScore[symbol] = champion.score;
  const tierLabel = champion.tier === "confirmed" ? "CONFIRMED" : "LEADER";
  const reverified = champion.last_rescored_at ? ` · re-verified ${fmtTimeAgo(champion.last_rescored_at)}` : "";
  await loadStrategyReplay(champion.strategy, champion.params, symbol, {
    isChampion: true,
    scoreLabel: `${tierLabel} · out-of-sample score ${champion.score.toFixed(2)} · since ${fmtTimeAgo(champion.updated_at)}${reverified}`,
  });
  return champion;
}

// Marks whichever leaderboard card matches the strategy currently playing in the hero
// replay so it reads as "this one, right now" against the other 9 — bold accent border
// and glow instead of the same flat treatment as every other card.
function highlightShowcasedModelCard(strategy, params) {
  const grid = document.getElementById("all-cards");
  if (!grid) return;
  const paramsKey = JSON.stringify(params);
  const cards = [...grid.querySelectorAll(".model-card")];
  let matched = cards.find((c) => c.dataset.strategy === strategy && c.dataset.params === paramsKey);
  if (!matched) matched = cards.find((c) => c.dataset.strategy === strategy);
  cards.forEach((c) => c.classList.toggle("is-showcased", c === matched));
}

// ---------------------------------------------------------------- hero auto-cycle
// The always-visible top-of-page showcase: walks the current leaderboard forever,
// candlestick replay -> Monte Carlo -> hold -> next strategy. Runs automatically (no
// button needed) once the Trainer tab has a leaderboard to show.

function renderMonteCarloTiles(el, mc) {
  el.innerHTML = `
    <div class="muted small" style="margin-bottom:4px;">VaR / CVaR — MONTE CARLO (1,000 bootstrap-resampled paths)</div>
    <div class="metrics-row">
      <div class="metric-tile"><div class="label">VaR 95% (loss)</div><div class="value pnl-neg">${fmtMoney(mc.value_at_risk_95_pct)}</div></div>
      <div class="metric-tile"><div class="label">CVaR 95% (avg tail loss)</div><div class="value pnl-neg">${fmtMoney(mc.cvar_95_pct)}</div></div>
      <div class="metric-tile"><div class="label">P(account burned)</div><div class="value ${mc.prob_burned > 5 ? "pnl-neg" : ""}">${mc.prob_burned}%</div></div>
      <div class="metric-tile"><div class="label">Max DD p5/p50/p95</div><div class="value">${mc.max_drawdown_pct.p5}% / ${mc.max_drawdown_pct.p50}% / ${mc.max_drawdown_pct.p95}%</div></div>
      <div class="metric-tile"><div class="label">Ending equity p5/p50/p95</div><div class="value">${fmtMoney(mc.ending_equity.p5)} / ${fmtMoney(mc.ending_equity.p50)} / ${fmtMoney(mc.ending_equity.p95)}</div></div>
    </div>`;
}

// Runs Monte Carlo for whichever strategy is currently in the hero replay — called from
// loadStrategyReplay itself so it always appears for the strategy actually on screen
// (the very first champion load included), not only while the auto-cycle is stepping
// through the leaderboard.
async function _showHeroMonteCarlo(strategy, params, symbol) {
  const mcEl = document.getElementById("hero-montecarlo");
  if (!mcEl) return;
  try {
    const res = await api("/backtest/montecarlo", {
      method: "POST",
      body: JSON.stringify({ symbol, strategy, params, n_simulations: 1000 }),
    });
    renderMonteCarloTiles(mcEl, res.monte_carlo);
  } catch (e) {
    mcEl.innerHTML = `<div class="muted small">Monte Carlo failed: ${e.message}</div>`;
  }
  mcEl.classList.remove("hidden");
  _replayFade(mcEl);
}

function _heroCycleStatus(text) {
  const el = document.getElementById("hero-cycle-status");
  if (el) el.textContent = text;
}

function startHeroCycle() {
  if (state.heroCycleActive) return;
  state.heroCycleActive = true;
  if (state.heroCycleIndex === undefined) state.heroCycleIndex = 0;
  const btn = document.getElementById("btn-toggle-herocycle");
  if (btn) btn.textContent = "⏸ PAUSE AUTO-CYCLE";
  runHeroCycleStep();
}

function stopHeroCycle() {
  state.heroCycleActive = false;
  if (state.heroCycleTimer) clearTimeout(state.heroCycleTimer);
  const btn = document.getElementById("btn-toggle-herocycle");
  if (btn) btn.textContent = "▶ RESUME AUTO-CYCLE";
  _heroCycleStatus("Auto-cycle paused.");
}

function _heroCycleWait(ms) {
  return new Promise((resolve) => {
    state.heroCycleTimer = setTimeout(resolve, ms);
  });
}

async function runHeroCycleStep() {
  if (!state.heroCycleActive) return;
  if (!document.getElementById("view-trainer").classList.contains("active")) {
    // Trainer tab isn't the active view right now — check back shortly rather than
    // doing chart work on a hidden tab.
    await _heroCycleWait(3000);
    if (state.heroCycleActive) runHeroCycleStep();
    return;
  }

  const board = state.lastLeaderboard;
  if (!board || !board.results.length) {
    _heroCycleStatus("🔄 auto-cycling — waiting for leaderboard results…");
    await _heroCycleWait(5000);
    if (state.heroCycleActive) runHeroCycleStep();
    return;
  }

  const idx = state.heroCycleIndex % board.results.length;
  const r = board.results[idx];
  const symbol = board.symbol;
  const label = `${idx + 1}/${board.results.length} — ${r.strategy}`;
  _heroCycleStatus(`🔄 auto-cycling · ${label} — loading replay…`);

  let btResult = null;
  try {
    // loadStrategyReplay now runs the replay AND the current strategy's Monte Carlo
    // before its promise resolves, so there's nothing left to do here but await it.
    const result = await loadStrategyReplay(r.strategy, r.params, symbol, {
      isChampion: false,
      scoreLabel: `score ${r.score.toFixed(3)} · rank #${idx + 1}`,
    });
    btResult = result && result.btResult;
  } catch (e) {
    _heroCycleStatus(`🔄 auto-cycling · ${label} — couldn't load replay (${e.message}), skipping…`);
  }
  if (!state.heroCycleActive) return;

  const flagged = r.diverged || r.too_smooth || r.exceeds_burn_limit || r.regime_inconsistent || r.clustering_inflated;
  _heroCycleStatus(`🔄 auto-cycling · ${label} — ${flagged ? "FAILS GATE" : "PASSES GATE"} — reviewing…`);
  // Hold on the rendered Monte Carlo results for at least 10s before moving on — long
  // enough to actually read the VaR/CVaR numbers, not just glimpse them mid-fade.
  await _heroCycleWait(12000);
  if (!state.heroCycleActive) return;

  state.heroCycleIndex = idx + 1;
  runHeroCycleStep();
}

async function loadHistory(symbol) {
  const data = await api(`/trainer/history?symbol=${symbol}&limit=15`);
  const runs = data.runs; // newest first

  const listEl = document.getElementById("history-list");
  listEl.innerHTML =
    runs
      .map(
        (r) => `
      <div class="history-item" data-run-id="${r.id}">
        <span class="muted">${fmtTimeAgo(r.created_at)}</span>
        <span class="strat">${r.top_strategy || "—"}</span>
        <span class="${pnlClass(r.top_score || 0)}">${r.top_score != null ? r.top_score.toFixed(2) : "—"}</span>
      </div>`
      )
      .join("") || '<div class="muted small pad">No runs yet.</div>';

  listEl.querySelectorAll(".history-item").forEach((el) => {
    el.addEventListener("click", async () => {
      const run = await api(`/optimizer/runs/${el.dataset.runId}`);
      renderAllLeaderboard({ run_id: run.id, results: run.result, strategies_trained: state.strategies.length }, symbol);
    });
  });

  const scores = [...runs].reverse().map((r) => r.top_score).filter((v) => v != null);
  document.getElementById("history-sparkline").innerHTML = buildSparklineSvg(scores, { w: 260, h: 70, cls: "sparkline-lg" });

  if (runs.length && runs[0].top_score != null) {
    const run = await api(`/optimizer/runs/${runs[0].id}`);
    renderAllLeaderboard({ run_id: run.id, results: run.result, strategies_trained: state.strategies.length }, symbol);
  }
}

async function initTrainerTab() {
  if (state.trainerInitialized) return;
  state.trainerInitialized = true;
  await loadStrategies();
  await pollTrainerOnce();
  await loadChampionAndReplay(state.trainerSymbol);
  await loadHistory(state.trainerSymbol); // populates state.lastLeaderboard
  // only while the TRAINER page is on screen (the header TRAIN chip has its own status poll)
  state.trainerPollTimer = setInterval(() => { if (!document.hidden && document.getElementById("view-trainer")?.classList.contains("active")) pollTrainerOnce(); }, 5000);
  startHeroCycle(); // auto-run, no click needed — this is the actual fix for "not showing"
}

async function runNow() {
  const btn = document.getElementById("btn-run-now");
  btn.disabled = true;
  setStatus(`RUNNING EXTRA TRAINING CYCLE ON ${state.trainerSymbol}...`);
  try {
    await api("/trainer/run-now", { method: "POST", body: JSON.stringify({ symbol: state.trainerSymbol }) });
    await pollTrainerOnce();
    await loadChampionAndReplay(state.trainerSymbol);
    await loadHistory(state.trainerSymbol);
    setStatus("TRAINING CYCLE COMPLETE");
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

async function runLongHorizonTraining() {
  const btn = document.getElementById("btn-run-long-horizon");
  const symbol = document.getElementById("lh-symbol").value;
  const body = {
    symbol,
    interval: document.getElementById("lh-interval").value,
    years: parseInt(document.getElementById("lh-years").value, 10) || 2,
    n_iterations_per_strategy: parseInt(document.getElementById("lh-iterations").value, 10) || 150,
  };
  btn.disabled = true;
  setStatus(`RUNNING LONG-HORIZON TRAINING ON ${symbol}... this can take several minutes on first use.`);
  document.getElementById("lh-status").textContent =
    "Fetching Dukascopy tick data and searching all 10 strategies across two ~1-year windows — please wait...";
  try {
    const result = await api("/trainer/long-horizon/run", { method: "POST", body: JSON.stringify(body) });
    document.getElementById("lh-empty").classList.add("hidden");
    const table = document.getElementById("lh-table");
    table.classList.remove("hidden");
    table.querySelector("tbody").innerHTML = result.results
      .map((r, i) => {
        const paramsStr = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(", ");
        return `
        <tr>
          <td>${i + 1}</td>
          <td style="color:var(--cyan)">${r.strategy}</td>
          <td class="small">${paramsStr}</td>
          <td>${r.train_metrics.profit_factor ?? "∞"}</td>
          <td>${r.test_metrics.profit_factor ?? "∞"}</td>
          <td>${r.test_metrics.win_rate_pct}%</td>
          <td>${r.test_metrics.total_trades}</td>
          <td class="${pnlClass(r.score)}">${r.score}</td>
          <td><button class="btn btn-small promote-lh-btn" data-idx="${i}">→ ACCOUNT</button></td>
        </tr>`;
      })
      .join("");
    table.querySelectorAll(".promote-lh-btn").forEach((b) => {
      const r = result.results[b.dataset.idx];
      b.addEventListener("click", () => promoteToAccount(r.strategy, symbol, r.params));
    });
    document.getElementById("lh-meta").textContent =
      `run #${result.run_id} — ${result.bars_used.toLocaleString()} bars, ${result.data_range.start.slice(0, 10)} → ${result.data_range.end.slice(0, 10)} — ${result.data_source}`;
    document.getElementById("lh-status").textContent = `Done — ${result.results.length} viable results.`;
    setStatus("LONG-HORIZON TRAINING COMPLETE");
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
    document.getElementById("lh-status").textContent = "Failed: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

function updateReplayEquity(equity) {
  const el = document.getElementById("replay-equity-amt");
  if (!el) return;
  el.textContent = fmtMoney(equity);
  el.className = "amt " + (equity >= TRAINER_STARTING_BALANCE ? "pnl-pos" : "pnl-neg");
}

async function promoteToAccount(strategy, symbol, params) {
  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
  const name = `${strategy}-${symbol}-${stamp}`;
  try {
    const account = await api("/accounts", { method: "POST", body: JSON.stringify({ name, starting_balance: 50000 }) });
    await api(`/accounts/${account.id}/strategy`, {
      method: "POST",
      body: JSON.stringify({ strategy, symbol, params }),
    });
    setStatus(`Created account "${name}" with the promoted parameter set.`);
    document.querySelector('.tab[data-tab="capital"]').click();
    await loadAccounts();
    openAccountModal(account.id);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

function computeMetricsFromTrades(trades, startingBalance) {
  let equity = startingBalance;
  let peak = startingBalance;
  let maxDd = 0;
  let maxDdPct = 0;
  const wins = [];
  const losses = [];
  for (const t of trades) {
    equity = t.equity_after;
    peak = Math.max(peak, equity);
    const dd = peak - equity;
    maxDd = Math.max(maxDd, dd);
    maxDdPct = Math.max(maxDdPct, peak > 0 ? (dd / peak) * 100 : 0);
    (t.pnl > 0 ? wins : losses).push(t.pnl);
  }
  const total = trades.length;
  const netPnl = equity - startingBalance;
  const grossWin = wins.reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0;
  const allPnls = [...wins, ...losses];
  let sharpe = 0;
  if (allPnls.length > 1) {
    const mean = allPnls.reduce((a, b) => a + b, 0) / allPnls.length;
    const variance = allPnls.reduce((a, b) => a + (b - mean) ** 2, 0) / (allPnls.length - 1);
    const std = Math.sqrt(variance);
    // t-stat, not an annualized daily-return Sharpe: allPnls is per-trade, not per-day, so
    // scaling by sqrt(252) inflates confidence the smaller the trade count is (backwards).
    // sqrt(n) is the correct scaling for a per-trade mean's statistical significance.
    sharpe = std > 0 ? (mean / std) * Math.sqrt(allPnls.length) : 0;
  }
  return {
    total_trades: total,
    win_rate_pct: total ? Math.round((wins.length / total) * 10000) / 100 : 0,
    profit_factor: profitFactor === null ? null : Math.round(profitFactor * 1000) / 1000,
    net_pnl: Math.round(netPnl * 100) / 100,
    return_pct: Math.round((netPnl / startingBalance) * 10000) / 100,
    max_drawdown: Math.round(maxDd * 100) / 100,
    max_drawdown_pct: Math.round(maxDdPct * 100) / 100,
    sharpe_approx: Math.round(sharpe * 1000) / 1000,
    ending_equity: Math.round(equity * 100) / 100,
  };
}

// ---------------------------------------------------------------- accounts tab

function buildSparklineSvg(values, opts = {}) {
  const { w = 90, h = 30, cls = "sparkline" } = opts;
  if (!values || values.length < 2) return '<span class="muted small">—</span>';
  const pad = 3;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = (w - 2 * pad) / (values.length - 1);
  const pts = values
    .map((v, i) => `${(pad + i * step).toFixed(1)},${(h - pad - ((v - min) / range) * (h - 2 * pad)).toFixed(1)}`)
    .join(" ");
  const color = values[values.length - 1] >= values[0] ? "#21c97a" : "#ff4d5e";
  return `<svg class="${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

// Builds an empty card shell once per account id — text/class content is filled in
// (and refreshed on every poll) by updateAccountCard, so the node itself, its click
// listener, and any GSAP tween state on its children survive across live updates.
function accountCardSkeleton(a) {
  const div = document.createElement("div");
  div.dataset.id = a.id;
  div.innerHTML = `
    <div class="ac-top">
      <div><div class="ac-name"></div><div class="ac-id"></div></div>
      <span class="pill"></span>
    </div>
    <div class="ac-type"></div>
    <div class="ac-balance"></div>
    <div class="ac-today-row">
      <span class="ac-today-chip"></span>
      <span class="ac-todate"></span>
    </div>
    <div class="ac-trades-today muted small hidden"></div>
    <div class="ac-spark" id="spark-${a.id}"></div>
    <div class="ac-meta"></div>`;
  div.addEventListener("click", () => openAccountModal(parseInt(div.dataset.id, 10)));
  return div;
}

function updateAccountCard(card, a, today) {
  const pnl = a.balance - a.starting_balance;
  const isApex = a.account_type === "apex_eval";
  const cardClass =
    a.is_archived ? "is-archived" : a.status === "live" ? "is-live" : a.eval_status === "passed" ? "is-passed" : a.eval_status === "failed" ? "is-failed" : isApex ? "is-apex" : "";
  const typeLabel = isApex ? `APEX ${fmtMoney(a.starting_balance)}` : "STANDARD";
  const statusLabel = a.is_archived ? "burned" : isApex && a.eval_status ? a.eval_status.replace("_", " ") : a.status;
  const statusClass = a.is_archived ? "burned" : isApex && a.eval_status === "passed" ? "ran" : isApex && a.eval_status === "failed" ? "idle" : a.status;
  const goalBar = isApex
    ? (() => {
        const pct = Math.max(0, Math.min(100, (pnl / a.profit_target) * 100));
        const danger = pnl < 0 ? "danger" : "";
        return `<div class="goal-bar-wrap" style="min-width:70px;"><div class="goal-bar"><div class="goal-bar-fill ${danger}" style="width:${Math.max(pct, 2)}%"></div></div><div class="goal-bar-label">${pct.toFixed(0)}% to goal</div></div>`;
      })()
    : "";
  // Floor/room bar: same "5% of starting balance is the burn line" rule as the
  // auto-archive check, shown for every account so the safety margin is visible
  // before an account actually burns, not just after.
  const burnFloor = a.starting_balance * 0.95;
  const roomLeft = a.balance - burnFloor;
  const totalRoom = a.starting_balance * 0.05;
  const roomPct = Math.max(0, Math.min(100, (roomLeft / totalRoom) * 100));
  const roomDanger = roomPct < 30 ? "danger" : "";
  const floorBar = `<div class="goal-bar-wrap" style="min-width:70px;"><div class="goal-bar"><div class="goal-bar-fill ${roomDanger}" style="width:${Math.max(roomPct, 2)}%"></div></div><div class="goal-bar-label">floor ${fmtMoney(burnFloor)} · room ${roomLeft >= 0 ? "+" : ""}${fmtMoney(roomLeft)}</div></div>`;

  const prevBalance = card.dataset.balance !== undefined ? parseFloat(card.dataset.balance) : a.balance;
  card.dataset.balance = a.balance;
  card.className = `account-card ${cardClass}`;
  card.querySelector(".ac-name").textContent = a.name;
  card.querySelector(".ac-id").textContent = `#${a.id} · ${typeLabel}${a.is_system ? " · AUTO (follows the champion)" : ""}`;
  card.classList.toggle("account-auto", !!a.is_system);
  const pillEl = card.querySelector(".pill");
  pillEl.className = `pill pill-${statusClass}`;
  pillEl.textContent = statusLabel;
  card.querySelector(".ac-type").textContent = `${a.symbol || "unassigned"}${a.strategy ? " · " + a.strategy : ""}`;
  tweenNumberText(card.querySelector(".ac-balance"), a.balance, fmtMoney);

  const todayChip = card.querySelector(".ac-today-chip");
  todayChip.className = `ac-today-chip ${pnlClass(today.pnl)}`;
  tweenNumberText(todayChip, today.pnl, (v) => `today ${v >= 0 ? "+" : ""}${fmtMoney(v)}`);
  const todateEl = card.querySelector(".ac-todate");
  todateEl.className = `ac-todate ${pnlClass(pnl)}`;
  tweenNumberText(todateEl, pnl, (v) => `to date ${v >= 0 ? "+" : ""}${fmtMoney(v)}`);
  const tradesEl = card.querySelector(".ac-trades-today");
  tradesEl.classList.toggle("hidden", today.count <= 0);
  if (today.count > 0) tradesEl.textContent = `${today.count} trade${today.count === 1 ? "" : "s"} today`;

  card.querySelector(".ac-meta").innerHTML = `${goalBar}${a.is_archived ? "" : floorBar}`;

  // Balance moved since the last poll — pulse the card's glow so a live-updating
  // number doesn't just silently snap to a new value.
  if (!Number.isNaN(prevBalance) && prevBalance !== a.balance) {
    card.style.setProperty("--flash-color", a.balance > prevBalance ? "rgba(33, 201, 122, 0.55)" : "rgba(255, 77, 94, 0.55)");
    card.classList.remove("flash-update");
    void card.offsetWidth; // restart the CSS animation
    card.classList.add("flash-update");
  }
}

async function loadAccounts() {
  const showArchived = state.showArchived || false;
  const data = await api("/accounts?include_archived=true");
  const archivedCount = data.accounts.filter((a) => a.is_archived).length;
  document.getElementById("archived-count").textContent = archivedCount;
  const shown = showArchived ? data.accounts : data.accounts.filter((a) => !a.is_archived);

  const grid = document.getElementById("accounts-grid");
  document.getElementById("accounts-count").textContent = `(${shown.length})`;

  // Keyed reconciliation instead of an innerHTML rebuild: existing cards are updated
  // in place (so hover/tween state survives a 5s poll) and only genuinely new/removed
  // accounts touch the DOM structure or play an enter/exit animation.
  const todayMap = state.todayTradesByAccount || {};
  const seenIds = new Set();
  shown.forEach((a, index) => {
    seenIds.add(a.id);
    let card = state.accountCardEls.get(a.id);
    const isNew = !card;
    if (isNew) {
      card = accountCardSkeleton(a);
      state.accountCardEls.set(a.id, card);
    }
    updateAccountCard(card, a, todayMap[a.name] || { pnl: 0, count: 0 });
    const ref = grid.children[index] || null;
    if (ref !== card) grid.insertBefore(card, ref);
    if (isNew && window.gsap) {
      gsap.from(card, { opacity: 0, y: 18, scale: 0.94, duration: 0.5, ease: "power2.out" });
    }
  });
  state.accountCardEls.forEach((card, id) => {
    if (seenIds.has(id)) return;
    state.accountCardEls.delete(id);
    if (window.gsap) {
      gsap.to(card, { opacity: 0, scale: 0.92, duration: 0.25, ease: "power1.in", onComplete: () => card.remove() });
    } else {
      card.remove();
    }
  });

  // Sparklines need each account's full trade history, which is expensive to refetch
  // for all 23+ accounts on every fast poll — most haven't traded since the last poll.
  // Only refetch when last_run_at actually moved (or it's the first time we've drawn
  // this card), so the fast 5s cycle stays cheap and the important live numbers
  // (balance, P&L, open positions) never wait on sparkline work that didn't change.
  data.accounts.forEach(async (a) => {
    const cell = document.getElementById(`spark-${a.id}`);
    if (!cell) return;
    if (a.status === "idle" && !a.last_run_at) {
      cell.innerHTML = '<span class="muted small">no history yet</span>';
      return;
    }
    if (state.sparkLastRunAt[a.id] === a.last_run_at) return; // unchanged since last draw
    state.sparkLastRunAt[a.id] = a.last_run_at;
    try {
      const t = await api(`/accounts/${a.id}/trades`);
      cell.innerHTML = buildSparklineSvg([a.starting_balance, ...t.trades.map((x) => x.equity_after)], { w: 220, h: 34, cls: "" });
    } catch (e) {
      cell.innerHTML = "";
    }
  });

  return data.accounts;
}

async function loadApexTiers() {
  if (state.apexTiers) return state.apexTiers;
  const data = await api("/apex/tiers");
  state.apexTiers = data.tiers;
  return state.apexTiers;
}

function updateTierInfo() {
  const size = document.getElementById("acc-tier").value;
  const tier = state.apexTiers && state.apexTiers[size];
  const info = document.getElementById("acc-tier-info");
  if (tier) {
    info.textContent = `Profit target ${fmtMoney(tier.profit_target)} · Max drawdown ${fmtMoney(tier.max_drawdown)} · up to ${tier.max_contracts_minis} minis`;
    info.classList.remove("hidden");
  }
}

async function createAccount() {
  const name = document.getElementById("acc-name").value.trim() || "demo";
  setStatus("OPENING ACCOUNT...");
  try {
    let body;
    if (state.accountFormType === "apex_eval") {
      const tier_size = parseInt(document.getElementById("acc-tier").value, 10);
      body = { name, account_type: "apex_eval", tier_size };
    } else {
      const starting_balance = parseFloat(document.getElementById("acc-balance").value) || 50000;
      body = { name, account_type: "standard", starting_balance };
    }
    const lossLimitRaw = document.getElementById("acc-daily-loss-limit").value;
    const profitLimitRaw = document.getElementById("acc-daily-profit-limit").value;
    if (lossLimitRaw) body.daily_loss_limit = parseFloat(lossLimitRaw);
    if (profitLimitRaw) body.daily_profit_limit = parseFloat(profitLimitRaw);
    const account = await api("/accounts", { method: "POST", body: JSON.stringify(body) });
    await loadAccounts();
    openAccountModal(account.id);
    setStatus(`Account "${name}" opened.`);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

function isAccountModalOpen() {
  return !document.getElementById("account-modal-backdrop").classList.contains("hidden");
}

function openAccountModal(id) {
  document.getElementById("account-modal-backdrop").classList.remove("hidden");
  selectAccount(id);
}

function closeAccountModal() {
  document.getElementById("account-modal-backdrop").classList.add("hidden");
}

async function selectAccount(id) {
  state.currentAccountId = id;
  const account = await api(`/accounts/${id}`);
  document.getElementById("acc-detail-name").textContent = `— ${account.name} (#${account.id})`;

  await loadStrategies();

  let assignFormHtml = "";
  if (!account.strategy) {
    assignFormHtml = `
      <div class="form-row"><label>Symbol</label>
        <select id="assign-symbol"><option value="NQ">NQ</option><option value="ES">ES</option></select>
      </div>
      <div class="form-row"><label>Strategy</label>
        <select id="assign-strategy">${state.strategies.map((s) => `<option value="${s.name}">${s.name}</option>`).join("")}</select>
      </div>
      <button class="btn" id="btn-assign-strategy">ASSIGN STRATEGY</button>
    `;
  } else if (account.status === "live") {
    assignFormHtml = `
      <div class="muted small pad">Strategy: <span style="color:var(--cyan)">${account.strategy}</span> on ${account.symbol} —
        <span class="pill pill-live">live</span></div>
      <button class="btn" id="btn-stop-live">STOP LIVE</button>
      <button class="btn" id="btn-reset-account" style="margin-top:6px;">RESET ACCOUNT</button>
    `;
  } else if (account.account_type === "apex_eval" && (account.eval_status === "passed" || account.eval_status === "failed")) {
    assignFormHtml = `
      <div class="muted small pad">Strategy: <span style="color:var(--cyan)">${account.strategy}</span> on ${account.symbol} —
        <span class="ec-badge ${account.eval_status}">${account.eval_status}</span></div>
      <button class="btn" id="btn-reset-account">RESET &amp; TRY AGAIN</button>
    `;
  } else {
    const evalBtn =
      account.account_type === "apex_eval"
        ? `<button class="btn" id="btn-backtest-eval" style="margin-top:6px;">📊 BACKTEST EVALUATION (preview)</button>`
        : "";
    assignFormHtml = `
      <div class="muted small pad">Strategy: <span style="color:var(--cyan)">${account.strategy}</span> on ${account.symbol}</div>
      <button class="btn" id="btn-go-live">⚡ GO LIVE</button>
      ${evalBtn}
      <button class="btn" id="btn-reset-account" style="margin-top:6px;">RESET ACCOUNT</button>
    `;
  }

  document.getElementById("acc-controls").innerHTML = assignFormHtml;

  if (document.getElementById("btn-assign-strategy")) {
    document.getElementById("btn-assign-strategy").addEventListener("click", async () => {
      const strategy = document.getElementById("assign-strategy").value;
      const symbol = document.getElementById("assign-symbol").value;
      await api(`/accounts/${id}/strategy`, { method: "POST", body: JSON.stringify({ strategy, symbol }) });
      selectAccount(id);
    });
  }
  if (document.getElementById("btn-go-live")) {
    document.getElementById("btn-go-live").addEventListener("click", () => goLive(id));
  }
  if (document.getElementById("btn-stop-live")) {
    document.getElementById("btn-stop-live").addEventListener("click", () => stopLive(id));
  }
  if (document.getElementById("btn-reset-account")) {
    document.getElementById("btn-reset-account").addEventListener("click", async () => {
      await api(`/accounts/${id}/reset`, { method: "POST" });
      await loadAccounts();
      selectAccount(id);
    });
  }
  if (document.getElementById("btn-backtest-eval")) {
    document.getElementById("btn-backtest-eval").addEventListener("click", () => runBacktestEvaluation(id));
  }

  renderTraderspostCard(id, account);
  renderEvalCard(account);

  const posCard = document.getElementById("acc-position-card");
  if (account.open_position) {
    posCard.classList.remove("hidden");
    posCard.innerHTML = `
      <div class="pc-title">● OPEN POSITION</div>
      <div class="pc-row">
        <span>${account.open_position.side.toUpperCase()} @ ${account.open_position.entry_price}</span>
        <span class="muted small">SL ${account.open_position.stop_price} / TP ${account.open_position.target_price}</span>
        <span class="pc-pnl" id="pc-pnl">…</span>
        <span class="pc-pct" id="pc-pct">…</span>
      </div>
      <div id="pc-bars"></div>`;
    refreshOpenPositionPnl(account);
  } else {
    posCard.classList.add("hidden");
    posCard.innerHTML = "";
  }

  try {
    const trades = await api(`/accounts/${id}/trades`);
    if (trades.trades.length) {
      renderMetrics("acc-metrics", computeMetricsFromTrades(trades.trades, account.starting_balance));
      renderTrades("acc-trades-table", trades.trades);
      renderEquityCurve(
        "acc-equity-chart",
        [{ time: null, equity: account.starting_balance }, ...trades.trades.map((t) => ({ time: t.exit_time, equity: t.equity_after }))],
        account.created_at
      );
    } else {
      document.getElementById("acc-metrics").innerHTML = "";
      document.querySelector("#acc-trades-table tbody").innerHTML = "";
    }
    await renderAccountPriceChart(account, trades.trades);
  } catch (e) {
    setStatus(`ERROR loading account trades: ${e.message}`, true);
  }

  if (state.calendarAccountId !== id) {
    const now = new Date();
    state.calendarYear = now.getFullYear();
    state.calendarMonth = now.getMonth() + 1;
    state.calendarAccountId = id;
  }
  document.getElementById("acc-calendar-section").classList.remove("hidden");
  await loadCalendar(id, state.calendarYear, state.calendarMonth);
}

// The TradersPost/Tradovate real-execution link: exactly one account can carry this at a
// time (enforced server-side). Linking is opt-in and requires an explicit confirm() since
// it makes this one account start sending real orders once TRADERSPOST_WEBHOOK_URL is set
// in run.bat — every other account's local simulation is unaffected either way.
async function renderTraderspostCard(id, account) {
  const host = document.getElementById("acc-traderspost-card");
  if (!host) return;
  if (account.is_system) {
    host.classList.add("hidden");
    host.innerHTML = "";
    return;
  }
  host.classList.remove("hidden");

  if (account.traderspost_linked) {
    host.innerHTML = `
      <div class="pc-title">🔴 LINKED TO TRADERSPOST — REAL ORDERS${account.tradovate_account_name ? ` (${account.tradovate_account_name})` : ""}</div>
      <div id="tp-real-data" class="muted small pad">Loading real balance…</div>
      <button class="btn" id="btn-tp-unlink" style="margin-top:6px;">UNLINK (STOP SENDING REAL ORDERS)</button>
    `;
    document.getElementById("btn-tp-unlink").addEventListener("click", async () => {
      if (!confirm(`Unlink "${account.name}" from TradersPost? It stops sending real orders immediately.`)) return;
      await api(`/nostro/account/${id}/traderspost/unlink`, { method: "POST" });
      await loadAccounts();
      selectAccount(id);
    });
    try {
      const real = await api(`/nostro/account/${id}/traderspost/real`);
      const dataHost = document.getElementById("tp-real-data");
      if (!dataHost) return;
      if (!real.webhook_configured) {
        dataHost.innerHTML = `<span style="color:var(--red)">TRADERSPOST_WEBHOOK_URL is not set in run.bat — this account will not actually send orders.</span>`;
      } else if (real.balance) {
        const b = real.balance;
        const cash = isNum(b.cash_balance) ? `$${b.cash_balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—";
        const day = isNum(b.day_pnl) ? `${b.day_pnl >= 0 ? "+" : ""}$${b.day_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—";
        dataHost.innerHTML = `Real cash balance: <b>${cash}</b> · day P&amp;L <b>${day}</b> · ${(real.fills || []).length} recent fill(s)`;
      } else if (real.error) {
        dataHost.innerHTML = `<span style="color:var(--red)">${real.error}</span>`;
      } else {
        dataHost.innerHTML = `Webhook is configured and will send real orders. No Tradovate sub-account is linked for balance display (optional) — check TradersPost's own Broker page for the real balance.`;
      }
    } catch (e) {
      // the real-data panel is a nice-to-have; link/unlink itself doesn't depend on it
    }
    return;
  }

  if (!account.strategy || !account.symbol) {
    host.innerHTML = `<div class="muted small pad">Assign a strategy before linking this account to TradersPost.</div>`;
    return;
  }
  host.innerHTML = `<div class="muted small pad">Checking for a connected Tradovate account…</div>`;
  let tv = null;
  try {
    tv = await api("/nostro/tradovate/balances");
  } catch (e) {
    tv = null;
  }
  const tvAccounts = (tv && tv.accounts) || [];
  // The Tradovate sub-account picker is optional cosmetic metadata (for the real-balance
  // panel only) - TradersPost itself connects to the broker on its own site, so linking
  // for real execution works with none of this filled in.
  const picker = tvAccounts.length
    ? `<div class="form-row"><label>Tradovate sub-account <span class="muted small">(optional, for the balance display)</span></label>
         <select id="tp-tv-account"><option value="">— none —</option>${tvAccounts.map((a) => `<option value="${a.id}">${a.name} (#${a.id})</option>`).join("")}</select>
       </div>`
    : `<div class="muted small pad">No Tradovate account connected here (optional) — linking still works without it; you just won't see a real-balance panel.</div>`;
  host.innerHTML = `
    ${picker}
    <button class="btn" id="btn-tp-link" style="margin-top:6px;">⚠ LINK TO TRADERSPOST (REAL MONEY)</button>
  `;
  document.getElementById("btn-tp-link").addEventListener("click", async () => {
    const sel = document.getElementById("tp-tv-account");
    const tvId = sel ? sel.value : "", tvName = sel && sel.value ? sel.options[sel.selectedIndex].text : null;
    if (!confirm(`Link "${account.name}" to TradersPost for real execution?\n\nThis account will send REAL orders once TRADERSPOST_WEBHOOK_URL is configured in run.bat and the TradersPost subscription is enabled.`)) return;
    try {
      await api(`/nostro/account/${id}/traderspost/link`, { method: "POST", body: JSON.stringify({ tradovate_account_id: tvId || null, tradovate_account_name: tvName }) });
      await loadAccounts();
      selectAccount(id);
    } catch (e) {
      alert(e.message);
    }
  });
}

const CONTRACT_MULTIPLIER = { NQ: 20, ES: 50 };
const MICRO_MULTIPLIER = { NQ: 2, ES: 5 };
// Dollars per point for a whole open position: qty x (micro or mini point value). A position
// without a qty predates position sizing and is 1 mini, so old positions keep their exact P&L.
function positionPointValue(symbol, pos) {
  const per = pos && pos.contract === "micro" ? MICRO_MULTIPLIER[symbol] : CONTRACT_MULTIPLIER[symbol];
  return (per || 20) * ((pos && pos.qty) || 1);
}

// How an open position is doing right now, in terms every trade card shows: the plain
// price % move (signed so it reads + when winning, - when losing, whichever side you're on),
// and how far price has travelled from entry toward the target vs. toward the stop - each
// 0-100%, clamped, and 0 (not negative) while price is moving the other way.
function positionProgress(pos, currentPrice) {
  const isLong = pos.side === "long", entry = pos.entry_price, stop = pos.stop_price, target = pos.target_price;
  const pct = isNum(currentPrice) && entry ? ((currentPrice - entry) / entry) * (isLong ? 1 : -1) * 100 : null;
  const toTargetRaw = isLong ? (currentPrice - entry) / (target - entry) : (entry - currentPrice) / (entry - target);
  const toStopRaw = isLong ? (entry - currentPrice) / (entry - stop) : (currentPrice - entry) / (stop - entry);
  const clamp = (v) => Math.max(0, Math.min(100, (v || 0) * 100));
  return { pct, toTarget: clamp(toTargetRaw), toStop: clamp(toStopRaw) };
}
function isNum(n) {
  return n !== null && n !== undefined && !Number.isNaN(n);
}
function fmtPct(n, dec = 2) {
  if (!isNum(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(dec)}%`;
}
// The two-bar gauge markup shared by the LIVE tab's open-position cards and the account
// modal's position card: a thin TO TARGET bar (green) and TO STOP bar (red), each 0-100%.
function tpslBarsHtml(progress) {
  return `<div class="tpsl-bars">
    <div class="tpsl-row"><span class="tpsl-lbl">TO TARGET</span><div class="tpsl-track"><i class="tpsl-fill tp" style="width:${progress.toTarget.toFixed(0)}%"></i></div><span class="tpsl-val">${progress.toTarget.toFixed(0)}%</span></div>
    <div class="tpsl-row"><span class="tpsl-lbl">TO STOP</span><div class="tpsl-track"><i class="tpsl-fill sl" style="width:${progress.toStop.toFixed(0)}%"></i></div><span class="tpsl-val">${progress.toStop.toFixed(0)}%</span></div>
  </div>`;
}

async function renderAccountPriceChart(account, trades) {
  const container = document.getElementById("acc-price-chart");
  if (!account.symbol) {
    container.innerHTML = '<div class="muted small pad">No symbol assigned yet.</div>';
    return;
  }
  try {
    const data = await api(`/candles?symbol=${account.symbol}&interval=5m&lookback_days=60`);
    if (!data.candles.length || typeof LightweightCharts === "undefined") return;
    const { series } = getOrCreateChart("acc-price-chart", "candles");
    series.setData(data.candles);

    const markers = [];
    for (const t of trades) {
      const isLong = t.side === "long";
      markers.push({
        time: toUnixSeconds(t.entry_time),
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#21c97a" : "#ff4d5e",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: `${t.side.toUpperCase()} @ ${t.entry_price}`,
      });
      markers.push({
        time: toUnixSeconds(t.exit_time),
        position: isLong ? "aboveBar" : "belowBar",
        color: t.pnl >= 0 ? "#21c97a" : "#ff4d5e",
        shape: "circle",
        text: `${t.exit_reason} ${t.pnl >= 0 ? "+" : ""}${fmtMoney(t.pnl)}`,
      });
    }
    markers.sort((a, b) => a.time - b.time);
    series.setMarkers(markers);
  } catch (e) {
    // chart is a nice-to-have; don't block the rest of the modal on it
  }
}

function renderEvalCard(account) {
  const host = document.getElementById("acc-eval-card");
  if (account.account_type !== "apex_eval") {
    host.classList.add("hidden");
    host.innerHTML = "";
    return;
  }
  host.classList.remove("hidden");

  const profitProgress = account.balance - account.starting_balance;
  const profitPct = Math.max(0, Math.min(100, (profitProgress / account.profit_target) * 100));

  const peak = account.peak_equity || account.starting_balance;
  const floor = account.drawdown_floor != null ? account.drawdown_floor : account.starting_balance - account.max_drawdown;
  const range = peak - floor || 1;
  const headroomPct = Math.max(0, Math.min(100, ((account.balance - floor) / range) * 100));
  const ddClass = headroomPct > 50 ? "dd-safe" : headroomPct > 20 ? "dd-warn" : "dd-danger";

  const badge = account.eval_status
    ? `<span class="ec-badge ${account.eval_status}">${account.eval_status.replace("_", " ")}</span>`
    : `<span class="ec-badge in_progress">not started</span>`;

  host.innerHTML = `
    <div class="ec-title">🎯 APEX EVALUATION — $${account.starting_balance.toLocaleString()} ${badge}</div>
    <div class="ec-row">
      <div class="ec-row-label"><span>PROFIT TARGET</span><span>${fmtMoney(profitProgress)} / ${fmtMoney(account.profit_target)} (${profitPct.toFixed(0)}%)</span></div>
      <div class="ec-bar"><div class="ec-bar-fill profit" style="width:${Math.max(profitPct, 2)}%"></div></div>
    </div>
    <div class="ec-row">
      <div class="ec-row-label"><span>DRAWDOWN HEADROOM</span><span>floor ${fmtMoney(floor)} · peak ${fmtMoney(peak)}</span></div>
      <div class="ec-bar"><div class="ec-bar-fill ${ddClass}" style="width:${Math.max(headroomPct, 2)}%"></div></div>
    </div>
    ${account.eval_fail_reason ? `<div class="ec-reason">${account.eval_fail_reason}</div>` : ""}
    <div id="eval-preview"></div>
  `;
}

async function runBacktestEvaluation(id) {
  setStatus("RUNNING BACKTEST EVALUATION PREVIEW...");
  try {
    const r = await api(`/accounts/${id}/backtest-evaluation`, { method: "POST" });
    const host = document.getElementById("eval-preview");
    if (!host) return;
    const verdictColor = r.result === "passed" ? "var(--green)" : r.result === "failed" ? "var(--red)" : "var(--accent)";
    host.innerHTML = `
      <div class="position-card" style="border-left-color:${verdictColor};margin-top:10px;">
        <div class="pc-title">PREVIEW (historical, not real trades) — result: <span style="color:${verdictColor}">${r.result.toUpperCase()}</span></div>
        <div class="pc-row">
          <span>${r.trades_used} trades used</span>
          <span>ending equity ${fmtMoney(r.ending_equity)}</span>
          <span>peak ${fmtMoney(r.peak_equity)}</span>
          <span>floor ${fmtMoney(r.drawdown_floor)}</span>
          ${r.result_day ? `<span>on ${r.result_day}</span>` : ""}
        </div>
      </div>`;
    setStatus(`BACKTEST EVALUATION: ${r.result.toUpperCase()}`);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

// ---------------------------------------------------------------- trade calendar

async function loadCalendar(accountId, year, month) {
  const data = await api(`/accounts/${accountId}/calendar?year=${year}&month=${month}`);
  const monthNames = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  document.getElementById("cal-title").textContent = `${monthNames[month - 1]} ${year}`;

  const firstOfMonth = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const leadingBlanks = firstOfMonth.getDay();

  const dow = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"].map((d) => `<div class="cal-dow">${d}</div>`).join("");
  let cells = "";
  for (let i = 0; i < leadingBlanks; i++) cells += `<div class="cal-day empty"></div>`;
  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const info = data.days[dateStr];
    if (info) {
      const cls = info.pnl >= 0 ? "profit" : "loss";
      cells += `<div class="cal-day has-trades ${cls}" data-date="${dateStr}"><span class="cd-num">${day}</span><span class="cd-pnl">${info.pnl >= 0 ? "+" : ""}${fmtMoney(info.pnl)}</span></div>`;
    } else {
      cells += `<div class="cal-day"><span class="cd-num">${day}</span></div>`;
    }
  }
  document.getElementById("cal-grid").innerHTML = dow + cells;
  document.getElementById("cal-day-detail").innerHTML = "";

  document.querySelectorAll(".cal-day.has-trades").forEach((cell) => {
    cell.addEventListener("click", () => {
      const info = data.days[cell.dataset.date];
      const rows = info.trades
        .map(
          (t) => `
        <tr>
          <td>${fmtTime(t.entry_time)}</td><td>${fmtTime(t.exit_time)}</td><td>${t.side.toUpperCase()}</td>
          <td class="muted">${t.exit_reason}</td><td class="${pnlClass(t.pnl)}">${fmtMoney(t.pnl)}</td>
        </tr>`
        )
        .join("");
      document.getElementById("cal-day-detail").innerHTML = `
        <div class="muted small pad">${cell.dataset.date} — ${info.trade_count} trade(s), ${info.pnl >= 0 ? "+" : ""}${fmtMoney(info.pnl)}</div>
        <table class="data-table"><thead><tr><th>ENTRY</th><th>EXIT</th><th>SIDE</th><th>REASON</th><th>PNL</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
    });
  });
}

function shiftCalendarMonth(delta) {
  let m = state.calendarMonth + delta;
  let y = state.calendarYear;
  if (m > 12) { m = 1; y += 1; }
  if (m < 1) { m = 12; y -= 1; }
  state.calendarMonth = m;
  state.calendarYear = y;
  loadCalendar(state.calendarAccountId, y, m);
}

async function refreshOpenPositionPnl(account) {
  const el = document.getElementById("pc-pnl");
  if (!el || !account.open_position) return;
  try {
    const data = await api(`/candles?symbol=${account.symbol}&interval=5m&lookback_days=1`);
    if (!data.candles.length) return;
    const lastPrice = data.candles[data.candles.length - 1].close;
    const direction = account.open_position.side === "long" ? 1 : -1;
    const pnl = (lastPrice - account.open_position.entry_price) * direction * positionPointValue(account.symbol, account.open_position);
    el.textContent = `${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)} unrealized`;
    el.className = "pc-pnl " + pnlClass(pnl);
    const progress = positionProgress(account.open_position, lastPrice);
    const pctEl = document.getElementById("pc-pct");
    if (pctEl) { pctEl.textContent = fmtPct(progress.pct); pctEl.className = "pc-pct " + pnlClass(progress.pct); }
    const barsEl = document.getElementById("pc-bars");
    if (barsEl) barsEl.innerHTML = tpslBarsHtml(progress);
  } catch (e) {
    el.textContent = "—";
  }
}

async function goLive(id) {
  setStatus("GOING LIVE...");
  try {
    await api(`/accounts/${id}/go-live`, { method: "POST" });
    await loadAccounts();
    await selectAccount(id);
    loadDashboardSummary();
    setStatus("ACCOUNT IS NOW LIVE — trading forward in real time");
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

async function stopLive(id) {
  setStatus("STOPPING LIVE TRADING...");
  try {
    await api(`/accounts/${id}/stop-live`, { method: "POST" });
    await loadAccounts();
    await selectAccount(id);
    loadDashboardSummary();
    setStatus("LIVE TRADING STOPPED");
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

// ---------------------------------------------------------------- home dashboard

async function loadDashboardSummary() {
  try {
    const s = await api("/dashboard/summary");
    tweenNumberText(document.getElementById("dash-total-equity"), s.total_equity, fmtMoney);

    state.equityHistory = state.equityHistory || [];
    state.equityHistory.push(s.total_equity);
    if (state.equityHistory.length > 60) state.equityHistory.shift();
    const sparkEl = document.getElementById("dash-equity-spark");
    if (sparkEl) sparkEl.innerHTML = buildSparklineSvg(state.equityHistory, { w: 100, h: 26, cls: "sparkline" });
    const pnlEl = document.getElementById("dash-today-pnl");
    pnlEl.className = "value " + pnlClass(s.today_pnl);
    tweenNumberText(pnlEl, s.today_pnl, (v) => `${v >= 0 ? "+" : ""}${fmtMoney(v)}`);
    const pctEl = document.getElementById("dash-today-pnl-pct");
    pctEl.className = "value " + pnlClass(s.today_pnl_pct);
    tweenNumberText(pctEl, s.today_pnl_pct, (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
    tweenNumberText(document.getElementById("dash-open-positions"), s.open_positions_count, (v) => Math.round(v).toLocaleString());
    tweenNumberText(document.getElementById("dash-live-accounts"), s.live_accounts_count, (v) => Math.round(v).toLocaleString());
    tweenNumberText(document.getElementById("dash-today-trades"), s.today_trades_count, (v) => Math.round(v).toLocaleString());
    tweenNumberText(document.getElementById("trainer-today-runs"), s.training_runs_today, (v) => Math.round(v).toLocaleString());
    tweenNumberText(document.getElementById("trainer-today-backtests"), s.training_backtests_today, (v) => Math.round(v).toLocaleString());

    // Per-account today's realized P&L + trade count, derived from the same list that
    // fills the Today's Trades table — reused by the account cards below instead of
    // firing an extra request per account just to show a "today" figure on each card.
    const todayByAccount = {};
    (s.today_trades || []).forEach((t) => {
      const bucket = todayByAccount[t.account_name] || { pnl: 0, count: 0 };
      bucket.pnl += t.pnl;
      bucket.count += 1;
      todayByAccount[t.account_name] = bucket;
    });
    state.todayTradesByAccount = todayByAccount;

    const emptyEl = document.getElementById("today-trades-empty");
    const table = document.getElementById("today-trades-table");
    if (s.today_trades.length) {
      emptyEl.classList.add("hidden");
      table.classList.remove("hidden");
      table.querySelector("tbody").innerHTML = s.today_trades
        .map(
          (t) => `
        <tr>
          <td>${fmtTime(t.exit_time)}</td>
          <td>${t.account_name}</td>
          <td>${t.account_symbol}</td>
          <td>${t.side.toUpperCase()}</td>
          <td class="muted">${t.exit_reason}</td>
          <td class="${pnlClass(t.pnl)}">${fmtMoney(t.pnl)}</td>
        </tr>`
        )
        .join("");
    } else {
      emptyEl.classList.remove("hidden");
      table.classList.add("hidden");
    }

    renderOpenPositions(s.open_positions || []);
  } catch (e) {
    // non-critical widget; fail quietly
  }
}

function renderOpenPositions(positions) {
  const emptyEl = document.getElementById("open-positions-empty");
  const table = document.getElementById("open-positions-table");
  if (!emptyEl || !table) return;

  if (!positions.length) {
    emptyEl.classList.remove("hidden");
    table.classList.add("hidden");
    return;
  }
  emptyEl.classList.add("hidden");
  table.classList.remove("hidden");
  table.querySelector("tbody").innerHTML = positions
    .map((p) => {
      const pnlKnown = p.unrealized_pnl !== null && p.unrealized_pnl !== undefined;
      const sideClass = p.side === "long" ? "pnl-pos" : "pnl-neg";
      return `
      <tr class="open-position-row" data-account-id="${p.account_id}">
        <td>${p.account_name}</td>
        <td>${p.symbol}</td>
        <td class="muted small">${p.strategy || "—"}</td>
        <td class="${sideClass}">${p.side.toUpperCase()}</td>
        <td>${fmtTime(p.entry_time)}</td>
        <td>${p.entry_price}</td>
        <td>${p.current_price ?? "—"}</td>
        <td class="small">SL ${p.stop_price} / TP ${p.target_price}</td>
        <td class="${pnlKnown ? pnlClass(p.unrealized_pnl) : "muted"}">${pnlKnown ? (p.unrealized_pnl >= 0 ? "+" : "") + fmtMoney(p.unrealized_pnl) : "—"}</td>
      </tr>`;
    })
    .join("");
  table.querySelectorAll(".open-position-row").forEach((row) => {
    row.addEventListener("click", () => openAccountModal(parseInt(row.dataset.accountId, 10)));
  });
}

async function loadFearGreed() {
  try {
    const fg = await api("/fear-greed?symbol=NQ");
    document.getElementById("fg-value").textContent = fg.value;
    document.getElementById("fg-label").textContent = fg.label;
    let pointer = document.querySelector("#fg-gauge .fg-pointer");
    if (!pointer) {
      pointer = document.createElement("div");
      pointer.className = "fg-pointer";
      document.getElementById("fg-gauge").appendChild(pointer);
    }
    pointer.style.left = `${fg.value}%`;
    document.getElementById("fg-components").textContent =
      `RSI ${fg.components.rsi} · MOMENTUM ${fg.components.momentum} · VOLATILITY ${fg.components.volatility}`;
  } catch (e) {
    document.getElementById("fg-label").textContent = "UNAVAILABLE";
  }
}

async function refreshHomeIfActive() {
  if (!document.getElementById("view-accounts").classList.contains("active")) return;
  await loadDashboardSummary();
  await loadAccounts();
  await loadEquityComparison();
  if (state.currentAccountId !== null && isAccountModalOpen()) await selectAccount(state.currentAccountId);
}

// ---------------------------------------------------------------- equity comparison chart

const EQUITY_LINE_COLORS = ["#ff9d00", "#3fd1ff", "#21c97a", "#ff4d5e", "#8e6bff", "#ffe066"];
// The comparison chart now tracks every account, not just the handful that are live,
// so the curated 6-color palette above runs out fast — golden-angle hue rotation keeps
// every extra line visually distinct instead of the palette repeating and colliding.
function equityLineColor(i) {
  if (i < EQUITY_LINE_COLORS.length) return EQUITY_LINE_COLORS[i];
  const hue = (i * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, 72%, 58%)`;
}

async function loadEquityComparison() {
  try {
    const { curves } = await api("/accounts/equity-curves");
    const emptyEl = document.getElementById("equity-comparison-empty");
    const chartEl = document.getElementById("equity-comparison-chart");
    const legendEl = document.getElementById("equity-comparison-legend");
    if (!curves.length) {
      emptyEl.classList.remove("hidden");
      chartEl.classList.add("hidden");
      legendEl.classList.add("hidden");
      return;
    }
    emptyEl.classList.add("hidden");
    chartEl.classList.remove("hidden");
    legendEl.classList.remove("hidden");
    renderEquityComparisonChart("equity-comparison-chart", curves);
    renderEquityComparisonLegend(legendEl, curves);
  } catch (e) {
    // non-critical widget; fail quietly
  }
}

// A step path, not a smoothed curve: equity here only moves at the instant a trade
// closes and is flat in between, so a curve (even a straight diagonal) between two
// trade points would draw a slope across a stretch of time where nothing actually
// happened. Trying a Catmull-Rom spline through these sparse, unevenly-time-spaced
// points also produced wild overshoot loops between far-apart trades — a step
// function has no such failure mode and is the honest shape of the data besides.
// Slightly rounded corners (small radius arcs) keep it from looking like a raw
// staircase while still reading as discrete jumps rather than a fitted trend line.
function stepSvgPath(pts) {
  if (!pts.length) return "";
  if (pts.length === 1) return `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  const r = 3; // corner rounding radius, in svg viewBox units
  let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1];
    const cur = pts[i];
    const dx = cur.x - prev.x;
    const dy = cur.y - prev.y;
    const stepDir = dy > 0 ? 1 : -1;
    const cornerX = Math.min(r, Math.abs(dx) / 2);
    const cornerY = Math.min(r, Math.abs(dy) / 2) * stepDir;
    d += ` L ${(cur.x - cornerX).toFixed(2)} ${prev.y.toFixed(2)}`;
    d += ` Q ${cur.x.toFixed(2)} ${prev.y.toFixed(2)}, ${cur.x.toFixed(2)} ${(prev.y + cornerY).toFixed(2)}`;
    d += ` L ${cur.x.toFixed(2)} ${cur.y.toFixed(2)}`;
  }
  return d;
}

// Custom SVG multi-line chart for the live equity comparison panel — deliberately not
// the TradingView (lightweight-charts) library used for candlesticks elsewhere, since
// that always stamps its own attribution mark on the chart and this is a simple
// percentage-return comparison, not a full OHLC chart. Elements persist across polls
// (keyed by account name) and only have their d/cx/cy attributes updated, so the CSS
// `transition: d …` on .eq-line/.eq-area/.eq-dot animates each redraw into the next
// instead of popping.
const EQ_CHART_W = 1000;
const EQ_CHART_H = 280;
const EQ_PAD = { l: 4, r: 50, t: 14, b: 24 };

function renderEquityComparisonChart(containerId, curves) {
  const container = document.getElementById(containerId);
  if (!container) return;

  // % return from THIS account's own starting balance, not raw dollar equity —
  // accounts here start at different sizes ($25k eval vs $50k standard), so plotting
  // absolute equity squashes the smaller ones flat against the bigger ones' scale.
  // Every line starting at 0% on one shared axis is the actually-comparable view.
  const series = curves
    .map((c, i) => {
      const seen = new Set();
      const pts = c.points
        .map((p) => ({ t: toUnixSeconds(p.time), v: ((p.equity - c.starting_balance) / c.starting_balance) * 100 }))
        .sort((a, b) => a.t - b.t)
        .filter((d) => (seen.has(d.t) ? false : (seen.add(d.t), true)));
      return { key: c.name, color: equityLineColor(i), pts };
    })
    .filter((s) => s.pts.length);

  const plotW = EQ_CHART_W - EQ_PAD.l - EQ_PAD.r;
  const plotH = EQ_CHART_H - EQ_PAD.t - EQ_PAD.b;

  let ui = state.equityChartEls[containerId];
  if (!ui) {
    const clipId = `eq-plot-clip-${containerId}`;
    container.innerHTML = `<svg class="equity-chart-svg" viewBox="0 0 ${EQ_CHART_W} ${EQ_CHART_H}" preserveAspectRatio="none">
      <defs><clipPath id="${clipId}"><rect x="${EQ_PAD.l}" y="${EQ_PAD.t}" width="${plotW}" height="${plotH}" /></clipPath></defs>
      <g class="eq-grid"></g>
      <g class="eq-areas" clip-path="url(#${clipId})"></g>
      <g class="eq-lines" clip-path="url(#${clipId})"></g>
      <g class="eq-dots"></g>
      <g class="eq-labels"></g>
    </svg>`;
    const svg = container.querySelector("svg");
    ui = {
      svg,
      defs: svg.querySelector("defs"),
      gridG: svg.querySelector(".eq-grid"),
      labelsG: svg.querySelector(".eq-labels"),
      areasG: svg.querySelector(".eq-areas"),
      linesG: svg.querySelector(".eq-lines"),
      dotsG: svg.querySelector(".eq-dots"),
      byKey: new Map(), // account name -> { area, line, dot, grad }
    };
    state.equityChartEls[containerId] = ui;
  }

  if (!series.length) {
    ui.byKey.forEach((els) => els.area.parentNode && els.area.parentNode.removeChild(els.area));
    ui.byKey.clear();
    ui.gridG.innerHTML = "";
    ui.labelsG.innerHTML = "";
    ui.linesG.innerHTML = "";
    ui.dotsG.innerHTML = "";
    return;
  }

  const allT = series.flatMap((s) => s.pts.map((p) => p.t));
  const allV = series.flatMap((s) => s.pts.map((p) => p.v));
  const tMin = Math.min(...allT), tMax = Math.max(...allT);

  // Tracking every account means a couple of old one-off "RUN" backtests with huge
  // cumulative returns can sit alongside accounts that haven't moved at all — scaling
  // the axis to fit the single biggest outlier would flatten every other line into an
  // unreadable smear at 0%. Instead, size the axis to the 85th-percentile magnitude
  // (the "typical" account) and let anything further out run off the top/bottom edge —
  // the plot groups are clipped, so an outlier just exits the frame instead of
  // dominating the scale.
  const magnitudes = allV.map(Math.abs).sort((a, b) => a - b);
  const pct85 = magnitudes.length ? magnitudes[Math.min(Math.floor(magnitudes.length * 0.85), magnitudes.length - 1)] : 5;
  const scale = Math.max(pct85 * 1.4, 3);
  let vMin = -scale, vMax = scale;

  const xs = (t) => EQ_PAD.l + (tMax === tMin ? plotW / 2 : ((t - tMin) / (tMax - tMin)) * plotW);
  const ys = (v) => EQ_PAD.t + plotH - ((Math.max(vMin, Math.min(vMax, v)) - vMin) / (vMax - vMin)) * plotH;
  const zeroY = ys(0);

  // "Nice" gridline step so labels read as round percentages instead of jagged decimals.
  const rawStep = (vMax - vMin) / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep || 1)));
  const niceStep = (Math.ceil(rawStep / mag) || 1) * mag;
  const ticks = [];
  for (let v = Math.ceil(vMin / niceStep) * niceStep; v <= vMax; v += niceStep) ticks.push(v);

  ui.gridG.innerHTML = ticks
    .map((v) => `<line class="eq-grid-line${Math.abs(v) < 1e-9 ? " eq-zero-line" : ""}" x1="${EQ_PAD.l}" x2="${EQ_CHART_W - EQ_PAD.r}" y1="${ys(v).toFixed(2)}" y2="${ys(v).toFixed(2)}" />`)
    .join("");
  ui.labelsG.innerHTML = ticks
    .map((v) => `<text class="eq-axis-label" x="${EQ_CHART_W - EQ_PAD.r + 8}" y="${(ys(v) + 3).toFixed(2)}">${v >= 0 ? "+" : ""}${v.toFixed(Math.abs(v) < niceStep && niceStep < 1 ? 1 : 0)}%</text>`)
    .join("")
    + (allT.length
      ? `<text class="eq-axis-label" x="${EQ_PAD.l}" y="${EQ_CHART_H - 6}" text-anchor="start">${fmtTime(new Date(tMin * 1000).toISOString())}</text>
         <text class="eq-axis-label" x="${EQ_CHART_W - EQ_PAD.r}" y="${EQ_CHART_H - 6}" text-anchor="end">${fmtTime(new Date(tMax * 1000).toISOString())}</text>`
      : "");

  // Tracking every account (not just a handful of live ones) means this can render
  // anywhere from 1 to 25+ overlapping series — thin out stroke weight, dot size, and
  // fill opacity as the count grows so a full account list still reads as a chart
  // instead of a solid smear of overlapping fills.
  const density = series.length;
  const areaOpacity = density > 12 ? 0.08 : density > 6 ? 0.16 : 0.3;
  const lineWidth = density > 12 ? 1.25 : density > 6 ? 1.6 : 2;
  const dotR = density > 12 ? 2.25 : density > 6 ? 3 : 4;

  const seenKeys = new Set();
  series.forEach((s) => {
    seenKeys.add(s.key);
    const pixelPts = s.pts.map((p) => ({ x: xs(p.t), y: ys(p.v) }));
    const lineD = stepSvgPath(pixelPts);
    const last = pixelPts[pixelPts.length - 1];
    const areaD = `${lineD} L ${last.x.toFixed(2)} ${zeroY.toFixed(2)} L ${pixelPts[0].x.toFixed(2)} ${zeroY.toFixed(2)} Z`;

    let els = ui.byKey.get(s.key);
    if (!els) {
      const gradId = `eq-grad-${containerId}-${ui.byKey.size}-${Math.random().toString(36).slice(2, 7)}`;
      const grad = document.createElementNS("http://www.w3.org/2000/svg", "linearGradient");
      grad.id = gradId;
      grad.setAttribute("x1", "0"); grad.setAttribute("y1", "0"); grad.setAttribute("x2", "0"); grad.setAttribute("y2", "1");
      ui.defs.appendChild(grad);

      const area = document.createElementNS("http://www.w3.org/2000/svg", "path");
      area.setAttribute("class", "eq-area");
      area.setAttribute("fill", `url(#${gradId})`);
      ui.areasG.appendChild(area);

      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("class", "eq-line");
      line.setAttribute("stroke", s.color);
      ui.linesG.appendChild(line);

      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("class", "eq-dot");
      dot.setAttribute("fill", s.color);
      dot.style.setProperty("--dot-color", s.color);
      ui.dotsG.appendChild(dot);

      els = { grad, area, line, dot };
      ui.byKey.set(s.key, els);
    }
    els.grad.innerHTML = `<stop offset="0%" stop-color="${s.color}" stop-opacity="${areaOpacity}"/><stop offset="100%" stop-color="${s.color}" stop-opacity="0"/>`;
    els.area.setAttribute("d", areaD);
    els.line.setAttribute("d", lineD);
    els.line.setAttribute("stroke-width", lineWidth);
    els.dot.setAttribute("r", dotR);
    els.dot.setAttribute("cx", last.x.toFixed(2));
    els.dot.setAttribute("cy", last.y.toFixed(2));
  });

  // Drop lines for accounts that dropped out of the live set (e.g. went idle/archived).
  ui.byKey.forEach((els, key) => {
    if (seenKeys.has(key)) return;
    [els.area, els.line, els.dot].forEach((el) => el.parentNode && el.parentNode.removeChild(el));
    els.grad.parentNode && els.grad.parentNode.removeChild(els.grad);
    ui.byKey.delete(key);
  });
}

function renderEquityComparisonLegend(el, curves) {
  el.innerHTML = curves
    .map((c, i) => {
      const color = equityLineColor(i);
      const pnl = c.current_equity - c.starting_balance;
      return `
      <div class="equity-legend-card" style="--legend-color:${color};">
        <div class="equity-legend-name"><span class="equity-legend-dot"></span>${c.name}</div>
        <div class="equity-legend-value ${pnlClass(pnl)}">${fmtMoney(c.current_equity)}</div>
      </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------- chart tab

async function loadPriceChart() {
  const symbol = document.getElementById("chart-symbol").value;
  const interval = document.getElementById("chart-interval").value;
  setStatus("LOADING CHART...");
  try {
    const data = await api(`/candles?symbol=${symbol}&interval=${interval}&lookback_days=30`);
    if (typeof LightweightCharts === "undefined") throw new Error("chart library failed to load");
    const { series } = getOrCreateChart("price-chart", "candles");
    series.setData(data.candles);
    setStatus(`CHART LOADED — ${data.candles.length} bars`);
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
}

// ---------------------------------------------------------------- wire up

document.addEventListener("DOMContentLoaded", async () => {
  initTabs();
  tickClock();
  setInterval(tickClock, 1000);

  document.getElementById("btn-create-account").addEventListener("click", createAccount);
  document.getElementById("btn-toggle-new-account").addEventListener("click", (e) => {
    const form = document.getElementById("new-account-form");
    const opening = form.classList.contains("hidden");
    form.classList.toggle("hidden", !opening);
    e.currentTarget.textContent = opening ? "− CLOSE" : "+ NEW ACCOUNT";
    if (opening && window.gsap) gsap.from(form, { opacity: 0, y: -8, duration: 0.3, ease: "power2.out" });
  });
  document.getElementById("btn-toggle-archived").addEventListener("click", () => {
    state.showArchived = !state.showArchived;
    document.getElementById("btn-toggle-archived").classList.toggle("active", state.showArchived);
    document.getElementById("archived-toggle-label").textContent = state.showArchived ? "HIDE" : "SHOW";
    loadAccounts();
  });
  document.getElementById("btn-toggle-herocycle").addEventListener("click", () => {
    if (state.heroCycleActive) stopHeroCycle();
    else startHeroCycle();
  });
  document.getElementById("btn-close-modal").addEventListener("click", closeAccountModal);
  document.getElementById("account-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "account-modal-backdrop") closeAccountModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isAccountModalOpen()) closeAccountModal();
  });
  document.querySelectorAll(".account-type-toggle .symbol-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".account-type-toggle .symbol-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.accountFormType = btn.dataset.acctype;
      const isApex = state.accountFormType === "apex_eval";
      document.getElementById("acc-balance-row").classList.toggle("hidden", isApex);
      document.getElementById("acc-tier-row").classList.toggle("hidden", !isApex);
      document.getElementById("acc-tier-info").classList.toggle("hidden", !isApex);
      if (isApex) {
        await loadApexTiers();
        updateTierInfo();
      }
    });
  });
  document.getElementById("acc-tier").addEventListener("change", updateTierInfo);
  document.getElementById("cal-prev").addEventListener("click", () => shiftCalendarMonth(-1));
  document.getElementById("cal-next").addEventListener("click", () => shiftCalendarMonth(1));
  document.getElementById("btn-run-backtest").addEventListener("click", runBacktest);
  document.getElementById("btn-run-optimize").addEventListener("click", runOptimize);
  document.getElementById("btn-run-now").addEventListener("click", runNow);
  document.getElementById("btn-run-long-horizon").addEventListener("click", runLongHorizonTraining);
  document.getElementById("btn-load-chart").addEventListener("click", loadPriceChart);
  document.getElementById("bt-strategy").addEventListener("change", () =>
    renderParamInputs("bt-params", currentStrategy("bt-strategy"))
  );

  document.getElementById("btn-replay-play").addEventListener("click", () => state.replayEngine && state.replayEngine.play());
  document.getElementById("btn-replay-pause").addEventListener("click", () => state.replayEngine && state.replayEngine.pause());
  document.getElementById("btn-replay-restart").addEventListener("click", () => {
    if (!state.replayEngine) return;
    document.getElementById("hero-metrics").classList.add("hidden");
    state.replayEngine.restart();
  });
  document.querySelectorAll(".speed-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".speed-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      if (state.replayEngine) state.replayEngine.setSpeed(parseInt(btn.dataset.speed, 10));
    });
  });
  document.querySelectorAll("#symbol-toggle .symbol-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.classList.contains("active")) return;
      document.querySelectorAll("#symbol-toggle .symbol-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.trainerSymbol = btn.dataset.symbol;
      await loadChampionAndReplay(state.trainerSymbol);
      await loadHistory(state.trainerSymbol);
      loadPipeline(state.trainerSymbol);
    });
  });

  setInterval(tickTrainerCountdown, 1000);

  try {
    await loadStrategies();
    // the old home page (view-accounts) is no longer in the menu: its summary/accounts load only when it is shown
    loadFearGreed();
    loadTickerTape();
    setInterval(() => { if (!document.hidden) loadTickerTape(); }, 20000);
    setInterval(() => { if (!document.hidden) loadFearGreed(); }, 120000);
    // 5s, not 15s: open positions' unrealized P&L and account balances should feel
    // live, not like the page needs a manual refresh to catch up.
    setInterval(refreshHomeIfActive, 5000);
    setStatus("READY");
    // One-time entrance on first paint — the account cards already animate themselves
    // in via loadAccounts (every card is "new" on this first call), this just brings
    // in the surrounding chrome so the whole home page arrives as one composed scene.
    if (window.gsap) {
      gsap.from(".metric-tile", { opacity: 0, y: 14, duration: 0.5, stagger: 0.05, ease: "power2.out" });
      gsap.from("#view-accounts .panel", { opacity: 0, y: 12, duration: 0.55, stagger: 0.06, ease: "power2.out", delay: 0.1 });
    }
  } catch (e) {
    setStatus(`ERROR: ${e.message}`, true);
  }
});


// ---------------------------------------------------------------- live trades page
// Cursor-based: /trades/feed?after_id=N returns only trades newer than the last one we
// rendered, so a 3 s poll is cheap. Rows are keyed by trade id in a Map; a new trade slides
// in at the top with a green/red flash. Open positions are keyed by account+entry time and
// tick their unrealized P&L in place. Polling only runs while this tab is visible.
const liveState = { init: false, rows: new Map(), lastId: 0, range: "today", polls: 0, open: new Map(), busy: false };

function liveRowHtml(t) {
  const r = t.r_multiple;
  const rTxt = r == null ? "—" : `${r > 0 ? "+" : ""}${r}R`;
  const side = `<span class="${t.side === "long" ? "pnl-pos" : "pnl-neg"}">${t.side.toUpperCase()}</span>`;
  const arch = t.archived ? ' <span class="pill pill-idle">archived</span>' : "";
  return `<td>${fmtTime(t.exit_time)}</td><td>${t.account_name}${arch}</td><td class="muted">${t.strategy || "—"}</td>
    <td>${t.symbol || "—"}</td><td>${side}</td><td>${t.qty ?? 1}</td><td class="muted">${t.exit_reason}</td>
    <td class="${pnlClass(t.pnl)}">${fmtMoney(t.pnl)}</td><td class="${pnlClass(r ?? 0)}">${rTxt}</td>`;
}

function liveShowTable() {
  const has = liveState.rows.size > 0;
  document.getElementById("live-trades-empty").classList.toggle("hidden", has);
  document.getElementById("live-trades-table").classList.toggle("hidden", !has);
  document.getElementById("live-load-more").classList.toggle("hidden", !has);
}

function liveInsertRow(t, animate) {
  if (liveState.rows.has(t.id)) return;
  const tr = document.createElement("tr");
  tr.dataset.id = t.id;
  tr.innerHTML = liveRowHtml(t);
  if (animate) tr.className = `row-new ${t.pnl >= 0 ? "win" : "loss"}`;
  const body = document.getElementById("live-trades-body");
  // keep newest-first order regardless of arrival order
  const next = [...body.children].find((c) => Number(c.dataset.id) < t.id);
  body.insertBefore(tr, next || null);
  liveState.rows.set(t.id, tr);
}

function renderLiveOpen(positions) {
  const wrap = document.getElementById("live-open");
  document.getElementById("live-open-empty").classList.toggle("hidden", positions.length > 0);
  document.getElementById("lt-open").textContent = positions.length;
  const seen = new Set();
  positions.forEach((p) => {
    const key = `${p.account_id}:${p.entry_time}`;
    seen.add(key);
    let card = liveState.open.get(key);
    if (!card) {
      card = document.createElement("div");
      card.className = `live-open-card ${p.side === "long" ? "is-long" : "is-short"}`;
      card.innerHTML = `<div class="lo-top"><span>${p.account_name}</span><span class="${p.side === "long" ? "pnl-pos" : "pnl-neg"}">${p.side.toUpperCase()} ${p.symbol}</span></div>
        <div class="lo-pnl-row"><div class="lo-pnl">—</div><div class="lo-pct">—</div></div>
        <div class="lo-meta">${p.strategy || ""} · entry ${p.entry_price} · SL ${p.stop_price} · TP ${p.target_price}<br><span class="lo-price"></span></div>
        <div class="lo-bars"></div>`;
      wrap.appendChild(card);
      liveState.open.set(key, card);
      if (window.gsap) gsap.from(card, { opacity: 0, y: 14, scale: 0.96, duration: 0.45, ease: "power2.out" });
    }
    const pnlEl = card.querySelector(".lo-pnl");
    if (p.unrealized_pnl != null) {
      pnlEl.className = `lo-pnl ${pnlClass(p.unrealized_pnl)}`;
      tweenNumberText(pnlEl, p.unrealized_pnl, fmtMoney);
    }
    card.querySelector(".lo-price").textContent = p.current_price != null ? `last ${p.current_price}` : "";
    const progress = positionProgress(p, p.current_price);
    const pctEl = card.querySelector(".lo-pct");
    pctEl.className = `lo-pct ${isNum(progress.pct) ? pnlClass(progress.pct) : ""}`;
    pctEl.textContent = fmtPct(progress.pct);
    card.querySelector(".lo-bars").innerHTML = p.current_price != null ? tpslBarsHtml(progress) : "";
  });
  for (const [key, card] of [...liveState.open.entries()]) {
    if (seen.has(key)) continue;
    liveState.open.delete(key);
    if (window.gsap) gsap.to(card, { opacity: 0, scale: 0.94, duration: 0.3, onComplete: () => card.remove() });
    else card.remove();
  }
}

async function loadLiveSummary() {
  try {
    const s = await api(`/trades/summary?since=${liveState.range}`);
    const net = document.getElementById("lt-net");
    net.className = `value ${pnlClass(s.net_pnl)}`;
    tweenNumberText(net, s.net_pnl, fmtMoney);
    // what the tiles cover: today, or the last session when nothing has closed yet today; plus the open trades right now
    const sub = document.getElementById("live-feed-sub");
    if (sub) sub.innerHTML = `${(s.label || "").toUpperCase()}${s.open_count ? ` · open <span class="${pnlClass(s.open_pnl)}">${fmtMoney(s.open_pnl)}</span>` : ""}`;
    const openSub = document.getElementById("lt-open")?.parentElement?.querySelector(".s");
    if (openSub) openSub.innerHTML = s.open_count ? `open P&amp;L <span class="${pnlClass(s.open_pnl)}">${fmtMoney(s.open_pnl)}</span>` : "positions being tracked";
    tweenNumberText(document.getElementById("lt-trades"), s.trades, (v) => String(Math.round(v)));
    document.getElementById("lt-wr").textContent = s.trades ? `${s.win_rate}%` : "—";
    document.getElementById("lt-pf").textContent = s.profit_factor == null ? "—" : s.profit_factor;
    document.getElementById("lt-r").textContent = s.avg_r == null ? "—" : `${s.avg_r > 0 ? "+" : ""}${s.avg_r}R`;
    document.getElementById("live-by-strategy").innerHTML = s.by_strategy.length
      ? s.by_strategy.map((b) => `<div class="live-by-row"><span><span class="lb-name">${b.name}</span><br><span class="lb-meta">${b.trades} trades · ${b.win_rate}% win</span></span><span class="${pnlClass(b.net_pnl)}">${fmtMoney(b.net_pnl)}</span></div>`).join("")
      : '<span class="muted">—</span>';
    const spark = document.getElementById("live-cum-spark");
    spark.innerHTML = buildSparklineSvg(s.cum_pnl.map((p) => p.pnl), { w: 260, h: 90, cls: "" });
  } catch (e) { /* summary is decorative; the feed is the source of truth */ }
}

async function loadLiveInitial() {
  const feed = await api("/trades/feed?limit=200");
  document.getElementById("live-trades-body").innerHTML = "";
  liveState.rows.clear();
  [...feed.trades].reverse().forEach((t) => liveInsertRow(t, false)); // oldest first so order settles newest-on-top
  liveState.lastId = feed.last_id;
  renderLiveOpen(feed.open_positions);
  liveShowTable();
  loadLiveSummary();
}

async function pollLive() {
  const view = document.getElementById("view-live");
  if (!view || !view.classList.contains("active") || document.hidden || liveState.busy) return;
  liveState.busy = true;
  try {
    liveState.polls += 1;
    const feed = await api(`/trades/feed?after_id=${liveState.lastId}&limit=100`);
    renderLiveOpen(feed.open_positions);
    if (feed.trades.length) {
      [...feed.trades].reverse().forEach((t) => liveInsertRow(t, true));
      liveState.lastId = Math.max(liveState.lastId, feed.last_id);
      liveShowTable();
      const newest = feed.trades[0];
      showToast(`${newest.pnl >= 0 ? "🟢" : "🔴"} ${newest.account_name}: ${newest.side.toUpperCase()} ${newest.symbol} closed ${fmtMoney(newest.pnl)} (${newest.exit_reason})`);
      const dot = document.getElementById("live-feed-dot");
      dot.classList.remove("busy"); void dot.offsetWidth; dot.classList.add("busy");
      loadLiveSummary();
    } else if (liveState.polls % 10 === 0) {
      loadLiveSummary(); // keep the tiles honest even on a quiet stretch
    }
    if (liveState.polls % 20 === 0) {
      // reconcile: drop rows that no longer exist (an account reset deletes its trades)
      const latest = await api("/trades/feed?limit=200");
      const ids = new Set(latest.trades.map((t) => t.id));
      const floor = Math.min(...ids);
      for (const [id, tr] of [...liveState.rows.entries()]) {
        if (id >= floor && !ids.has(id)) { tr.remove(); liveState.rows.delete(id); }
      }
      liveShowTable();
    }
  } catch (e) {
    /* transient network hiccup: the next poll retries */
  } finally {
    liveState.busy = false;
  }
}

async function initLiveTab() {
  if (liveState.init) { pollLive(); return; }
  liveState.init = true;
  document.querySelectorAll("#live-range-toggle .symbol-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#live-range-toggle .symbol-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      liveState.range = btn.dataset.range;
      loadLiveSummary();
    });
  });
  document.getElementById("live-load-more").addEventListener("click", async () => {
    const oldest = Math.min(...liveState.rows.keys());
    const page = await api(`/trades/feed?before_id=${oldest}&limit=200`);
    page.trades.forEach((t) => liveInsertRow(t, false));
    if (!page.trades.length) document.getElementById("live-load-more").classList.add("hidden");
  });
  try { await loadLiveInitial(); } catch (e) { setStatus(`LIVE PAGE ERROR: ${e.message}`, true); }
  setInterval(pollLive, 3000);
}


// ---------------------------------------------------------------- the field page
// A canvas scatter of every evaluated candidate: x = win rate, y = average reward-to-risk (log),
// colour = expectancy per trade in R. Modelled on CustomCandleChart (dpr-aware canvas, full
// redraw) with a spatial hit-test grid for hover, and a short grow-in animation for points
// that arrive on a refresh.
const FIELD_NEUTRAL = [43, 58, 77], FIELD_ORANGE = [239, 133, 84], FIELD_GREEN = [61, 220, 151];
const FIELD_Y_MIN = 0.1, FIELD_Y_MAX = 10, FIELD_BORN_MS = 700;

class ScatterField {
  constructor(containerId, hooks = {}) {
    this.container = document.getElementById(containerId);
    this.container.innerHTML = "";
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "width:100%;height:100%;display:block;";
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.hooks = hooks;
    this.points = [];
    this.mode = "net";
    this.medianCost = 0;
    this.cell = 14;
    this.grid = new Map();
    this.screen = [];
    this.hoverIdx = -1;
    this.animating = false;
    this.pad = { l: 50, r: 64, t: 12, b: 36 };
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(this.container);
    this.canvas.addEventListener("mousemove", (e) => this._onMove(e));
    this.canvas.addEventListener("mouseleave", () => { this.hoverIdx = -1; this.hooks.onHover && this.hooks.onHover(null); this.draw(); });
    this.canvas.addEventListener("click", () => { if (this.hoverIdx >= 0 && this.hooks.onClick) this.hooks.onClick(this.points[this.hoverIdx]); });
    this._resize();
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = this.container.getBoundingClientRect();
    this.w = Math.max(200, r.width);
    this.h = Math.max(200, r.height);
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  setMode(mode) { this.mode = mode; this.draw(); }
  setMedianCost(c) { this.medianCost = c || 0; this.draw(); }

  setPoints(list, animate = false) {
    const now = performance.now();
    this.points = list.map((p) => this._mk(p, animate ? now : 0));
    this.draw();
  }

  addPoints(list) {
    const now = performance.now();
    const known = new Set(this.points.map((p) => p.id));
    list.forEach((p) => { if (!known.has(p[0])) this.points.push(this._mk(p, now)); });
    this._kick();
  }

  _mk(p, born) {
    return { id: p[0], wrN: p[1], rrN: p[2], eN: p[3], wrG: p[4], rrG: p[5], eG: p[6], n: p[7], tier: p[8], s: p[9], apex: p[10], born };
  }

  _vals(p) { return this.mode === "net" ? [p.wrN, p.rrN, p.eN] : [p.wrG, p.rrG, p.eG]; }
  _x(wr) { return this.pad.l + (wr / 100) * (this.w - this.pad.l - this.pad.r); }
  _y(rr) {
    const t = (Math.log10(Math.min(FIELD_Y_MAX, Math.max(FIELD_Y_MIN, rr))) - Math.log10(FIELD_Y_MIN)) / (Math.log10(FIELD_Y_MAX) - Math.log10(FIELD_Y_MIN));
    return this.pad.t + (1 - t) * (this.h - this.pad.t - this.pad.b);
  }

  _color(e, a = 1) {
    const t = Math.max(-1, Math.min(1, e / 0.3));
    const tgt = t < 0 ? FIELD_ORANGE : FIELD_GREEN;
    const k = Math.abs(t);
    const c = FIELD_NEUTRAL.map((v, i) => Math.round(v + (tgt[i] - v) * k));
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  }

  _kick() {
    if (this.animating) return;
    this.animating = true;
    const step = () => {
      this.draw();
      const now = performance.now();
      if (this.points.some((p) => p.born && now - p.born < FIELD_BORN_MS)) requestAnimationFrame(step);
      else this.animating = false;
    };
    requestAnimationFrame(step);
  }

  draw() {
    const { ctx, w, h, pad } = this;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#050505";
    ctx.fillRect(0, 0, w, h);
    const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

    // grid + axes
    ctx.font = "10px JetBrains Mono, monospace";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 1;
    for (const wr of [0, 20, 40, 60, 80, 100]) {
      const x = this._x(wr);
      ctx.strokeStyle = "#1b1b1b"; ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
      ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "center"; ctx.fillText(String(wr), x, pad.t + ph + 14);
    }
    for (const rr of [0.1, 0.2, 0.5, 1, 2, 5, 10]) {
      const y = this._y(rr);
      ctx.strokeStyle = rr === 1 ? "#2c2c2c" : "#1b1b1b"; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
      ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "right"; ctx.fillText(String(rr), pad.l - 6, y);
    }
    ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "center"; ctx.fillText("win rate (%)", pad.l + pw / 2, h - 8);
    ctx.save(); ctx.translate(12, pad.t + ph / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("average RR (win / loss)", 0, 0); ctx.restore();

    // break-even curves: WR = (1 + cost)/(1 + RR); the gross line is cost = 0
    const curve = (cost, color) => {
      ctx.save(); ctx.setLineDash([6, 5]); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
      let first = true;
      for (let i = 0; i <= 120; i++) {
        const rr = FIELD_Y_MIN * Math.pow(FIELD_Y_MAX / FIELD_Y_MIN, i / 120);
        const wr = ((1 + cost) / (1 + rr)) * 100;
        if (wr > 100) continue;
        const x = this._x(wr), y = this._y(rr);
        if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      }
      ctx.stroke(); ctx.restore();
    };
    curve(0, "#3fd1ff");
    if (this.medianCost > 0) curve(this.medianCost, "#ff8f6b");

    // points: the crowd first, survivors (with halo) on top
    const now = performance.now();
    this.grid = new Map();
    this.screen = new Array(this.points.length);
    const draws = [[], []];
    this.points.forEach((p, i) => {
      const [wr, rr, e] = this._vals(p);
      if (rr == null || wr == null) return;
      const x = this._x(wr), y = this._y(rr);
      this.screen[i] = [x, y];
      const key = `${Math.floor(x / this.cell)},${Math.floor(y / this.cell)}`;
      if (!this.grid.has(key)) this.grid.set(key, []);
      this.grid.get(key).push(i);
      draws[p.tier === 2 ? 1 : 0].push([p, x, y, e]);
    });
    for (const layer of draws) {
      for (const [p, x, y, e] of layer) {
        let k = 1;
        if (p.born) { const t = Math.min(1, (now - p.born) / FIELD_BORN_MS); k = 1 - Math.pow(1 - t, 3); }
        const r = (1.4 + Math.min(4.6, Math.sqrt(p.n) / 4)) * (0.35 + 0.65 * k);
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
        if (p.tier === 2) {
          ctx.save();
          ctx.shadowColor = "rgba(61,220,151,0.95)"; ctx.shadowBlur = 16 * k;
          ctx.fillStyle = this._color(Math.max(e, 0.12), 1); ctx.fill();
          ctx.restore();
          ctx.strokeStyle = "rgba(220,255,240,0.9)"; ctx.lineWidth = 1.2; ctx.stroke();
        } else {
          ctx.fillStyle = this._color(e, (p.tier === 0 ? 0.35 : 0.62) * k); ctx.fill();
        }
      }
    }
    if (this.hoverIdx >= 0 && this.screen[this.hoverIdx]) {
      const [x, y] = this.screen[this.hoverIdx];
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.stroke();
    }

    // colour bar (expectancy per trade, R)
    const bx = w - pad.r + 22, bt = pad.t + 20, bh = ph - 40;
    const g = ctx.createLinearGradient(0, bt, 0, bt + bh);
    g.addColorStop(0, this._color(0.3)); g.addColorStop(0.5, this._color(0)); g.addColorStop(1, this._color(-0.3));
    ctx.fillStyle = g; ctx.fillRect(bx, bt, 9, bh);
    ctx.fillStyle = "#8b8b8b"; ctx.textAlign = "left";
    [["0.3", 0], ["0", 0.5], ["-0.3", 1]].forEach(([lab, f]) => ctx.fillText(lab, bx + 13, bt + f * bh));
    ctx.save(); ctx.translate(bx - 6, bt + bh / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = "center"; ctx.fillText("exp / trade (R)", 0, 0); ctx.restore();
  }

  _onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const cx = Math.floor(mx / this.cell), cy = Math.floor(my / this.cell);
    let best = -1, bestD = 11 * 11;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const bucket = this.grid.get(`${cx + dx},${cy + dy}`);
      if (!bucket) continue;
      for (const i of bucket) {
        const [x, y] = this.screen[i];
        const d = (x - mx) ** 2 + (y - my) ** 2;
        if (d < bestD || (d === bestD && this.points[i].tier === 2)) { best = i; bestD = d; }
      }
    }
    if (best !== this.hoverIdx) {
      this.hoverIdx = best;
      this.draw();
      this.hooks.onHover && this.hooks.onHover(best >= 0 ? this.points[best] : null, mx, my);
    } else if (best >= 0) {
      this.hooks.onHover && this.hooks.onHover(this.points[best], mx, my);
    }
  }
}

const fieldState = { init: false, symbol: "NQ", hours: 24, strategy: "", mode: "net", lastId: 0, polls: 0, chart: null, strategies: [], busy: false };

function fieldFmtStat(el, n, pct) {
  el.innerHTML = pct == null ? `${n}` : `${Number(n).toLocaleString()} <span class="fs-pct">(${pct}%)</span>`;
}

function renderFieldStats(st) {
  document.getElementById("fs-field").textContent = Number(st.field).toLocaleString();
  document.getElementById("fs-surv").textContent = Number(st.survivors).toLocaleString();
  fieldFmtStat(document.getElementById("fs-gp"), st.gross_pos.n, st.gross_pos.pct);
  fieldFmtStat(document.getElementById("fs-np"), st.net_pos.n, st.net_pos.pct);
  fieldFmtStat(document.getElementById("fs-gpnn"), st.gp_nn.n, st.gp_nn.pct);
  document.getElementById("field-cost-r").textContent = st.median_cost_r ? st.median_cost_r.toFixed(3) : "-";
  document.getElementById("field-meta").textContent = `${st.cycles} cycles · ${st.sampled_points.toLocaleString()} points plotted`;
  if (fieldState.chart) fieldState.chart.setMedianCost(st.median_cost_r);
}

async function loadField(full) {
  const q = `symbol=${fieldState.symbol}&hours=${fieldState.hours}` + (fieldState.strategy ? `&strategy=${encodeURIComponent(fieldState.strategy)}` : "");
  const incremental = !full && fieldState.lastId > 0;
  const data = await api(`/field?${q}` + (incremental ? `&after_id=${fieldState.lastId}` : ""));
  const sel = document.getElementById("field-strategy");
  if (JSON.stringify(data.strategies) !== JSON.stringify(fieldState.strategies)) {
    fieldState.strategies = data.strategies;
    const cur = fieldState.strategy;
    sel.innerHTML = '<option value="">all strategies</option>' + data.strategies.map((s) => `<option value="${s}" ${s === cur ? "selected" : ""}>${s}</option>`).join("");
    if (incremental) return loadField(true); // strategy indexes shifted: reload cleanly
  }
  if (incremental) fieldState.chart.addPoints(data.points);
  else fieldState.chart.setPoints(data.points, false);
  fieldState.lastId = Math.max(fieldState.lastId, data.last_id);
  renderFieldStats(data.stats);
}

async function pollField() {
  const view = document.getElementById("view-field");
  if (!view || !view.classList.contains("active") || document.hidden || fieldState.busy) return;
  fieldState.busy = true;
  try {
    fieldState.polls += 1;
    await loadField(fieldState.polls % 6 === 0); // periodic full reload also prunes expired points
  } catch (e) { /* next poll retries */ } finally { fieldState.busy = false; }
}

async function initFieldTab() {
  if (fieldState.init) { pollField(); return; }
  fieldState.init = true;
  const tip = document.getElementById("field-tooltip");
  fieldState.chart = new ScatterField("field-chart", {
    onHover: (p, mx, my) => {
      if (!p) { tip.classList.add("hidden"); return; }
      const [wr, rr, e] = fieldState.chart._vals(p);
      const tierName = ["rejected by the trade-count gate", "scored but flagged", "SURVIVOR"][p.tier];
      tip.innerHTML = `<b>${fieldState.strategies[p.s] || "?"}</b><br>win rate ${wr.toFixed(1)}% · RR ${rr == null ? "-" : rr.toFixed(2)}<br>` +
        `expectancy ${e >= 0 ? "+" : ""}${e.toFixed(3)} R (${fieldState.mode}) · ${p.n} trades<br>${tierName}${p.tier === 2 ? " · click for parameters" : ""}`;
      tip.classList.remove("hidden");
      const wrap = tip.parentElement.getBoundingClientRect(), cv = fieldState.chart.canvas.getBoundingClientRect();
      tip.style.left = `${Math.min(mx + cv.left - wrap.left + 14, wrap.width - 250)}px`;
      tip.style.top = `${my + cv.top - wrap.top + 14}px`;
    },
    onClick: async (p) => {
      const box = document.getElementById("field-selected");
      try {
        const d = await api(`/field/point/${p.id}`);
        box.innerHTML = `<div class="fs-label">SELECTED</div><b style="color:var(--cyan)">${d.strategy}</b> ${d.tier === 2 ? '<span class="pill pill-gate-pass">SURVIVOR</span>' : ""}<br>` +
          (d.params ? Object.entries(d.params).map(([k, v]) => `<span class="pill pill-param">${k}=${v}</span>`).join("") : '<span class="muted">parameters are kept for survivors only</span>') +
          `<br>${d.n} trades · net ${d.exp_net.toFixed(3)} R · gross ${d.exp_gross.toFixed(3)} R`;
      } catch (e) { box.innerHTML = `<span class="muted">${e.message}</span>`; }
    },
  });
  const reload = () => { fieldState.lastId = 0; fieldState.strategies = []; loadField(true).catch(() => {}); };
  document.querySelectorAll("#field-mode-toggle .symbol-btn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#field-mode-toggle .symbol-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); fieldState.mode = b.dataset.mode; fieldState.chart.setMode(b.dataset.mode);
  }));
  document.querySelectorAll("#field-controls-toggle .symbol-btn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#field-controls-toggle .symbol-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    const show = b.dataset.show === "1", panel = document.getElementById("field-controls");
    if (window.gsap && show) { panel.classList.remove("is-hidden"); gsap.from(panel, { opacity: 0, y: -8, duration: 0.3 }); }
    else panel.classList.toggle("is-hidden", !show);
    fieldState.chart._resize();
  }));
  document.querySelectorAll("#field-symbol-toggle .symbol-btn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#field-symbol-toggle .symbol-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); fieldState.symbol = b.dataset.symbol; fieldState.strategy = ""; reload();
  }));
  document.getElementById("field-hours").addEventListener("change", (e) => { fieldState.hours = Number(e.target.value); reload(); });
  document.getElementById("field-strategy").addEventListener("change", (e) => { fieldState.strategy = e.target.value; reload(); });
  try { await loadField(true); } catch (e) { setStatus(`FIELD PAGE ERROR: ${e.message}`, true); }
  setInterval(pollField, 10000);
}
