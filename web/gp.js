/* GP: the full price chart. Timeframes 1m to 1M, candle/bar/line/area/Heikin-Ashi, 16 overlays, 21 studies in synced sub-panes,
   drawing tools (trendline, ray, horizontal/vertical line, rectangle, Fibonacci, text), compare overlay, volume profile, log scale.
   Rendered by security.js (funcGP) through window.NXGP.render. Indicators are computed here, in the browser, from the chart data. */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, big, signed } = NX;
  const NZ = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nz")) || 1; // the page zoom the chart hosts cancel (see responsive.css)
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n) && Number.isFinite(n);
  const q = encodeURIComponent;

  // ------------------------------------------------------------------ indicator maths (arrays aligned to the bars, null = not defined yet)
  const nulls = (n) => new Array(n).fill(null);
  const sma = (a, n) => { const o = nulls(a.length); let s = 0, c = 0; for (let i = 0; i < a.length; i++) { if (isNum(a[i])) { s += a[i]; c++; } if (i >= n && isNum(a[i - n])) { s -= a[i - n]; c--; } if (i >= n - 1 && c === n) o[i] = s / n; } return o; };
  const ema = (a, n) => { const o = nulls(a.length), k = 2 / (n + 1); let p = null, seed = [], first = a.findIndex(isNum); for (let i = first < 0 ? a.length : first; i < a.length; i++) { if (!isNum(a[i])) continue; if (p === null) { seed.push(a[i]); if (seed.length === n) { p = seed.reduce((x, y) => x + y, 0) / n; o[i] = p; } } else { p = a[i] * k + p * (1 - k); o[i] = p; } } return o; };
  const wma = (a, n) => a.map((_, i) => { if (i < n - 1) return null; let s = 0, w = 0; for (let j = 0; j < n; j++) { const x = a[i - j]; if (!isNum(x)) return null; s += x * (n - j); w += n - j; } return s / w; });
  const rma = (a, n) => { const o = nulls(a.length); let p = null, seed = []; a.forEach((v, i) => { if (!isNum(v)) return; if (p === null) { seed.push(v); if (seed.length === n) { p = seed.reduce((x, y) => x + y, 0) / n; o[i] = p; } } else { p = (p * (n - 1) + v) / n; o[i] = p; } }); return o; };
  const std = (a, n) => a.map((_, i) => { if (i < n - 1) return null; const w = a.slice(i - n + 1, i + 1); if (w.some((x) => !isNum(x))) return null; const m = w.reduce((x, y) => x + y, 0) / n; return Math.sqrt(w.reduce((x, y) => x + (y - m) ** 2, 0) / n); });
  const hh = (a, n) => a.map((_, i) => (i < n - 1 ? null : Math.max(...a.slice(i - n + 1, i + 1))));
  const ll = (a, n) => a.map((_, i) => (i < n - 1 ? null : Math.min(...a.slice(i - n + 1, i + 1))));
  const tr = (d) => d.map((x, i) => (i ? Math.max(x.high - x.low, Math.abs(x.high - d[i - 1].close), Math.abs(x.low - d[i - 1].close)) : x.high - x.low));
  const atr = (d, n) => rma(tr(d), n);
  const zip = (a, f) => a.map((v, i) => f(v, i));

  const I = {
    rsi: (d, n = 14) => { const c = d.map((x) => x.close), up = c.map((v, i) => (i ? Math.max(0, v - c[i - 1]) : 0)), dn = c.map((v, i) => (i ? Math.max(0, c[i - 1] - v) : 0)), au = rma(up.slice(1), n), ad = rma(dn.slice(1), n); return [null, ...au.map((u, i) => (u === null ? null : ad[i] === 0 ? 100 : 100 - 100 / (1 + u / ad[i])))]; },
    macd: (d) => { const c = d.map((x) => x.close), f = ema(c, 12), s = ema(c, 26), m = zip(f, (v, i) => (v !== null && s[i] !== null ? v - s[i] : null)), sig = ema(m, 9); return { line: m, signal: sig, hist: zip(m, (v, i) => (v !== null && sig[i] !== null ? v - sig[i] : null)) }; },
    stoch: (d, n = 14, k = 3, s = 3) => { const H = hh(d.map((x) => x.high), n), L = ll(d.map((x) => x.low), n), raw = d.map((x, i) => (H[i] === null || H[i] === L[i] ? null : ((x.close - L[i]) / (H[i] - L[i])) * 100)), kk = sma(raw, k); return { k: kk, d: sma(kk, s) }; },
    stochrsi: (d) => { const r = I.rsi(d, 14), H = hh(r.map((v) => (v === null ? 0 : v)), 14), L = ll(r.map((v) => (v === null ? 0 : v)), 14), raw = r.map((v, i) => (v === null || i < 27 || H[i] === L[i] ? null : ((v - L[i]) / (H[i] - L[i])) * 100)), k = sma(raw, 3); return { k, d: sma(k, 3) }; },
    cci: (d, n = 20) => { const tp = d.map((x) => (x.high + x.low + x.close) / 3), m = sma(tp, n); return tp.map((v, i) => { if (m[i] === null) return null; const md = tp.slice(i - n + 1, i + 1).reduce((s, x) => s + Math.abs(x - m[i]), 0) / n; return md ? (v - m[i]) / (0.015 * md) : 0; }); },
    atr: (d) => atr(d, 14),
    adx: (d, n = 14) => { const up = d.map((x, i) => (i ? x.high - d[i - 1].high : 0)), dn = d.map((x, i) => (i ? d[i - 1].low - x.low : 0)), pdm = up.map((u, i) => (u > dn[i] && u > 0 ? u : 0)), mdm = dn.map((v, i) => (v > up[i] && v > 0 ? v : 0)), a = atr(d, n), pd = rma(pdm, n).map((v, i) => (v === null || !a[i] ? null : (100 * v) / a[i])), md = rma(mdm, n).map((v, i) => (v === null || !a[i] ? null : (100 * v) / a[i])), dx = pd.map((v, i) => (v === null || md[i] === null || v + md[i] === 0 ? null : (100 * Math.abs(v - md[i])) / (v + md[i]))); return { adx: rma(dx.map((v) => (v === null ? NaN : v)).map((v) => (Number.isNaN(v) ? null : v)), n), pdi: pd, mdi: md }; },
    obv: (d) => { let o = 0; return d.map((x, i) => { if (i) o += x.close > d[i - 1].close ? x.volume : x.close < d[i - 1].close ? -x.volume : 0; return o; }); },
    mfi: (d, n = 14) => { const tp = d.map((x) => (x.high + x.low + x.close) / 3), pos = tp.map((v, i) => (i && v > tp[i - 1] ? v * d[i].volume : 0)), neg = tp.map((v, i) => (i && v < tp[i - 1] ? v * d[i].volume : 0)); return d.map((_, i) => { if (i < n) return null; const p = pos.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0), m = neg.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0); return m === 0 ? 100 : 100 - 100 / (1 + p / m); }); },
    willr: (d, n = 14) => { const H = hh(d.map((x) => x.high), n), L = ll(d.map((x) => x.low), n); return d.map((x, i) => (H[i] === null || H[i] === L[i] ? null : ((H[i] - x.close) / (H[i] - L[i])) * -100)); },
    roc: (d, n = 12) => d.map((x, i) => (i < n ? null : (x.close / d[i - n].close - 1) * 100)),
    mom: (d, n = 10) => d.map((x, i) => (i < n ? null : x.close - d[i - n].close)),
    cmf: (d, n = 20) => { const mfv = d.map((x) => (x.high === x.low ? 0 : (((x.close - x.low) - (x.high - x.close)) / (x.high - x.low)) * x.volume)); return d.map((_, i) => { if (i < n - 1) return null; const v = d.slice(i - n + 1, i + 1).reduce((s, x) => s + x.volume, 0); return v ? mfv.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0) / v : 0; }); },
    aroon: (d, n = 25) => { const up = d.map((_, i) => { if (i < n) return null; const w = d.slice(i - n, i + 1).map((x) => x.high); return ((n - (n - w.lastIndexOf(Math.max(...w)))) / n) * 100; }), dn = d.map((_, i) => { if (i < n) return null; const w = d.slice(i - n, i + 1).map((x) => x.low); return ((n - (n - w.lastIndexOf(Math.min(...w)))) / n) * 100; }); return { up, down: dn }; },
    trix: (d) => { const e = ema(ema(ema(d.map((x) => x.close), 15), 15), 15); return e.map((v, i) => (v === null || !i || e[i - 1] === null ? null : (v / e[i - 1] - 1) * 100)); },
    pctb: (d) => { const c = d.map((x) => x.close), m = sma(c, 20), s = std(c, 20); return c.map((v, i) => (m[i] === null || !s[i] ? null : (v - (m[i] - 2 * s[i])) / (4 * s[i]))); },
    bbw: (d) => { const c = d.map((x) => x.close), m = sma(c, 20), s = std(c, 20); return c.map((_, i) => (m[i] === null ? null : ((4 * s[i]) / m[i]) * 100)); },
    force: (d) => ema(d.map((x, i) => (i ? (x.close - d[i - 1].close) * x.volume : 0)), 13),
    chop: (d, n = 14) => { const t = tr(d), H = hh(d.map((x) => x.high), n), L = ll(d.map((x) => x.low), n); return d.map((_, i) => (i < n || H[i] === L[i] ? null : (100 * Math.log10(t.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0) / (H[i] - L[i]))) / Math.log10(n))); },
    hv: (d, n = 20) => { const r = d.map((x, i) => (i ? Math.log(x.close / d[i - 1].close) : null)); return d.map((_, i) => { if (i < n) return null; const w = r.slice(i - n + 1, i + 1), m = w.reduce((s, x) => s + x, 0) / n; return Math.sqrt(w.reduce((s, x) => s + (x - m) ** 2, 0) / (n - 1)) * Math.sqrt(252) * 100; }); },
    adl: (d) => { let s = 0; return d.map((x) => { s += x.high === x.low ? 0 : (((x.close - x.low) - (x.high - x.close)) / (x.high - x.low)) * x.volume; return s; }); },
  };
  const C = (d) => d.map((x) => x.close);
  // New York clock, for session-based overlays (opening range, previous-day levels); crypto uses the UTC day
  const NYF = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false, year: "numeric", month: "2-digit", day: "2-digit" });
  const nyParts = (t) => { const o = {}; NYF.formatToParts(new Date(t * 1000)).forEach((q2) => { o[q2.type] = q2.value; }); return { day: `${o.year}-${o.month}-${o.day}`, min: (Number(o.hour) % 24) * 60 + Number(o.minute) }; };
  const byDay = (d, o) => { const days = new Map(); d.forEach((x, i) => { const k = o && o.crypto ? new Date(x.t * 1000).toISOString().slice(0, 10) : nyParts(x.t).day; if (!days.has(k)) days.set(k, []); days.get(k).push(i); }); return days; };
  // each day's opening-range high and low as short flat lines that start when the range is complete and run to the end of the day
  const orbLines = (d, o, mins) => {
    const out = [], days = byDay(d, o), open = o && o.crypto ? 0 : 570;
    [...days.values()].slice(-25).forEach((idx) => {
      const inOr = idx.filter((i) => { const m = o && o.crypto ? (d[i].t % 86400) / 60 : nyParts(d[i].t).min; return m >= open && m < open + mins; });
      if (!inOr.length) return;
      const hi = Math.max(...inOr.map((i) => d[i].high)), lo = Math.min(...inOr.map((i) => d[i].low)), from = inOr[inOr.length - 1] + 1, to = idx[idx.length - 1];
      if (from > to) return;
      const H = nulls(d.length), L = nulls(d.length); for (let i = from; i <= to; i++) { H[i] = hi; L[i] = lo; }
      out.push({ v: H, c: "#2be07e" }, { v: L, c: "#ff4a4a" });
    });
    return out;
  };
  // each day's pre-market (04:00-09:30 New York) high and low, drawn across that day's regular session. Needs PRE/POST bars on.
  const preLevels = (d, o) => {
    if (o && o.crypto) return [];
    const out = [];
    [...byDay(d, o).values()].slice(-25).forEach((idx) => {
      const pre = idx.filter((i) => nyParts(d[i].t).min < 570), reg = idx.filter((i) => nyParts(d[i].t).min >= 570);
      if (!pre.length || !reg.length) return;
      const hi = Math.max(...pre.map((i) => d[i].high)), lo = Math.min(...pre.map((i) => d[i].low)), H = nulls(d.length), L = nulls(d.length);
      reg.forEach((i) => { H[i] = hi; L[i] = lo; });
      out.push({ v: H, c: "#4dd0e1" }, { v: L, c: "#ff8fb1", dash: true });
    });
    return out;
  };
  const dayLevels = (d, o) => {
    const out = [], ks = [...byDay(d, o).values()];
    ks.slice(-25).forEach((idx, n, arr) => {
      const gi = ks.indexOf(idx); if (gi < 1) return; const pv = ks[gi - 1], hi = Math.max(...pv.map((i) => d[i].high)), lo = Math.min(...pv.map((i) => d[i].low)), cl = d[pv[pv.length - 1]].close;
      [[hi, "#ffd166", false], [lo, "#ffd166", false], [cl, "#8b8b8b", true]].forEach(([val, c, dash]) => { const S = nulls(d.length); idx.forEach((i) => { S[i] = val; }); out.push({ v: S, c, dash }); });
    });
    return out;
  };
  const OV = {
    sma20: { l: "SMA 20", c: "#f2e34d", f: (d) => [{ v: sma(C(d), 20) }] }, sma50: { l: "SMA 50", c: "#5aa9ff", f: (d) => [{ v: sma(C(d), 50) }] },
    sma100: { l: "SMA 100", c: "#3fd1c9", f: (d) => [{ v: sma(C(d), 100) }] }, sma200: { l: "SMA 200", c: "#c58bff", f: (d) => [{ v: sma(C(d), 200) }] },
    ema9: { l: "EMA 9", c: "#ff8fb1", f: (d) => [{ v: ema(C(d), 9) }] }, ema21: { l: "EMA 21", c: "#ffa028", f: (d) => [{ v: ema(C(d), 21) }] },
    ema50: { l: "EMA 50", c: "#7fd1ff", f: (d) => [{ v: ema(C(d), 50) }] }, ema200: { l: "EMA 200", c: "#e08bff", f: (d) => [{ v: ema(C(d), 200) }] },
    wma20: { l: "WMA 20", c: "#9be564", f: (d) => [{ v: wma(C(d), 20) }] },
    bb: { l: "Bollinger 20,2", c: "#5aa9ff", f: (d) => { const c = C(d), m = sma(c, 20), s = std(c, 20); return [{ v: zip(m, (x, i) => (x === null ? null : x + 2 * s[i])) }, { v: m, dash: true }, { v: zip(m, (x, i) => (x === null ? null : x - 2 * s[i])) }]; } },
    kelt: { l: "Keltner 20,10,2", c: "#2be07e", f: (d) => { const m = ema(C(d), 20), a = atr(d, 10); return [{ v: zip(m, (x, i) => (x === null || a[i] === null ? null : x + 2 * a[i])) }, { v: m, dash: true }, { v: zip(m, (x, i) => (x === null || a[i] === null ? null : x - 2 * a[i])) }]; } },
    donch: { l: "Donchian 20", c: "#ff6b9a", f: (d) => [{ v: hh(d.map((x) => x.high), 20) }, { v: ll(d.map((x) => x.low), 20) }] },
    vwap: { l: "VWAP (session)", c: "#ffd166", intraday: true, f: (d) => { let pv = 0, vv = 0, day = null; return [{ v: d.map((x) => { const dd = new Date(x.t * 1000).toISOString().slice(0, 10); if (dd !== day) { day = dd; pv = 0; vv = 0; } const tp = (x.high + x.low + x.close) / 3; pv += tp * x.volume; vv += x.volume; return vv ? pv / vv : null; }) }]; } },
    ichi: { l: "Ichimoku 9,26,52", c: "#3fd1c9", f: (d) => { const mid = (n) => { const H = hh(d.map((x) => x.high), n), L = ll(d.map((x) => x.low), n); return H.map((h, i) => (h === null ? null : (h + L[i]) / 2)); }, t = mid(9), k = mid(26), a = zip(t, (x, i) => (x === null || k[i] === null ? null : (x + k[i]) / 2)), b = mid(52); return [{ v: t, c: "#3fd1c9" }, { v: k, c: "#ff6b9a" }, { v: a, c: "rgba(43,224,126,0.7)", shift: 26 }, { v: b, c: "rgba(255,74,74,0.7)", shift: 26 }]; } },
    psar: { l: "Parabolic SAR", c: "#ffa028", dots: true, f: (d) => { const o = nulls(d.length); if (d.length < 3) return [{ v: o }]; let up = d[1].close > d[0].close, sar = up ? d[0].low : d[0].high, ep = up ? d[1].high : d[1].low, af = 0.02; for (let i = 2; i < d.length; i++) { sar = sar + af * (ep - sar); if (up) { sar = Math.min(sar, d[i - 1].low, d[i - 2].low); if (d[i].low < sar) { up = false; sar = ep; ep = d[i].low; af = 0.02; } else if (d[i].high > ep) { ep = d[i].high; af = Math.min(0.2, af + 0.02); } } else { sar = Math.max(sar, d[i - 1].high, d[i - 2].high); if (d[i].high > sar) { up = true; sar = ep; ep = d[i].high; af = 0.02; } else if (d[i].low < ep) { ep = d[i].low; af = Math.min(0.2, af + 0.02); } } o[i] = sar; } return [{ v: o }]; } },
    st: { l: "Supertrend 10,3", c: "#2be07e", f: (d) => { const a = atr(d, 10), up = nulls(d.length), dn = nulls(d.length); let fu = null, fl = null, trend = 1; d.forEach((x, i) => { if (a[i] === null) return; const m = (x.high + x.low) / 2, bu = m + 3 * a[i], bl = m - 3 * a[i]; fu = fu === null || bu < fu || d[i - 1].close > fu ? bu : fu; fl = fl === null || bl > fl || d[i - 1].close < fl ? bl : fl; trend = x.close > fu ? 1 : x.close < fl ? -1 : trend; if (trend === 1) up[i] = fl; else dn[i] = fu; }); return [{ v: up, c: "#2be07e" }, { v: dn, c: "#ff4a4a" }]; } },
    hma21: { l: "Hull MA 21", c: "#ff9f43", f: (d) => { const c = C(d), h = wma(c, 10), f = wma(c, 21), raw = zip(h, (v, i) => (v !== null && f[i] !== null ? 2 * v - f[i] : null)); return [{ v: wma(raw, 5) }]; } },
    vwma20: { l: "VWMA 20", c: "#4dd0e1", f: (d) => [{ v: d.map((_, i) => { if (i < 19) return null; let pv = 0, vv = 0; for (let j = i - 19; j <= i; j++) { pv += d[j].close * d[j].volume; vv += d[j].volume; } return vv ? pv / vv : null; }) }] },
    orb15: { l: "Opening range 15m (high / low)", c: "#2be07e", intraday: true, f: (d, o) => orbLines(d, o, 15) },
    orb30: { l: "Opening range 30m (high / low)", c: "#7be0a8", intraday: true, f: (d, o) => orbLines(d, o, 30) },
    orb40: { l: "Opening range 40m (high / low)", c: "#a8f0c6", intraday: true, f: (d, o) => orbLines(d, o, 40) },
    pmhl: { l: "Pre-market high / low", c: "#4dd0e1", intraday: true, f: (d, o) => preLevels(d, o) },
    pdhl: { l: "Previous day high / low / close", c: "#ffd166", intraday: true, f: (d, o) => dayLevels(d, o) },
  };
  const ST = {
    rsi: { l: "RSI 14", lines: (d) => [{ v: I.rsi(d), c: "#ffa028" }], levels: [30, 70] },
    macd: { l: "MACD 12,26,9", lines: (d) => { const m = I.macd(d); return [{ v: m.hist, hist: true }, { v: m.line, c: "#5aa9ff" }, { v: m.signal, c: "#ffa028" }]; } },
    stoch: { l: "Stochastic 14,3,3", lines: (d) => { const s = I.stoch(d); return [{ v: s.k, c: "#5aa9ff" }, { v: s.d, c: "#ffa028" }]; }, levels: [20, 80] },
    stochrsi: { l: "Stoch RSI", lines: (d) => { const s = I.stochrsi(d); return [{ v: s.k, c: "#5aa9ff" }, { v: s.d, c: "#ffa028" }]; }, levels: [20, 80] },
    cci: { l: "CCI 20", lines: (d) => [{ v: I.cci(d), c: "#c58bff" }], levels: [-100, 100] },
    atr: { l: "ATR 14", lines: (d) => [{ v: I.atr(d), c: "#ff6b9a" }] },
    adx: { l: "ADX / DMI 14", lines: (d) => { const a = I.adx(d); return [{ v: a.adx, c: "#ffa028" }, { v: a.pdi, c: "#2be07e" }, { v: a.mdi, c: "#ff4a4a" }]; }, levels: [25] },
    obv: { l: "On-Balance Volume", lines: (d) => [{ v: I.obv(d), c: "#5aa9ff" }] },
    mfi: { l: "Money Flow Index", lines: (d) => [{ v: I.mfi(d), c: "#2be07e" }], levels: [20, 80] },
    willr: { l: "Williams %R", lines: (d) => [{ v: I.willr(d), c: "#f2e34d" }], levels: [-80, -20] },
    roc: { l: "Rate of Change 12", lines: (d) => [{ v: I.roc(d), c: "#3fd1c9" }], levels: [0] },
    mom: { l: "Momentum 10", lines: (d) => [{ v: I.mom(d), c: "#9be564" }], levels: [0] },
    cmf: { l: "Chaikin Money Flow", lines: (d) => [{ v: I.cmf(d), hist: true }], levels: [0] },
    aroon: { l: "Aroon 25", lines: (d) => { const a = I.aroon(d); return [{ v: a.up, c: "#2be07e" }, { v: a.down, c: "#ff4a4a" }]; } },
    trix: { l: "TRIX 15", lines: (d) => [{ v: I.trix(d), c: "#e08bff" }], levels: [0] },
    pctb: { l: "Bollinger %B", lines: (d) => [{ v: I.pctb(d), c: "#5aa9ff" }], levels: [0, 1] },
    bbw: { l: "Bollinger Bandwidth", lines: (d) => [{ v: I.bbw(d), c: "#5aa9ff" }] },
    force: { l: "Force Index 13", lines: (d) => [{ v: I.force(d), hist: true }], levels: [0] },
    chop: { l: "Choppiness 14", lines: (d) => [{ v: I.chop(d), c: "#ffd166" }], levels: [38.2, 61.8] },
    hv: { l: "Historical Vol 20 (ann.)", lines: (d) => [{ v: I.hv(d), c: "#ff8fb1" }] },
    adl: { l: "Accumulation / Distribution", lines: (d) => [{ v: I.adl(d), c: "#7fd1ff" }] },
    volsma: { l: "Volume + SMA 20", lines: (d) => [{ v: d.map((x) => x.volume), hist: true, vol: true }, { v: sma(d.map((x) => x.volume), 20), c: "#ffa028" }] },
    ao: { l: "Awesome Oscillator", lines: (d) => { const m = d.map((x) => (x.high + x.low) / 2), a = sma(m, 5), b = sma(m, 34); return [{ v: zip(a, (v, i) => (v !== null && b[i] !== null ? v - b[i] : null)), hist: true }]; }, levels: [0] },
    ppo: { l: "PPO 12,26,9", lines: (d) => { const c = C(d), f = ema(c, 12), sl = ema(c, 26), p = zip(f, (v, i) => (v !== null && sl[i] ? ((v - sl[i]) / sl[i]) * 100 : null)), sg = ema(p, 9); return [{ v: zip(p, (v, i) => (v !== null && sg[i] !== null ? v - sg[i] : null)), hist: true }, { v: p, c: "#5aa9ff" }, { v: sg, c: "#ffa028" }]; }, levels: [0] },
    rvol: { l: "Relative volume (vs 20 bars)", lines: (d) => { const v = d.map((x) => x.volume), m = sma(v, 20); return [{ v: v.map((x, i) => (m[i] ? x / m[i] : null)), hist: true }]; }, levels: [1, 2] },
    vtx: { l: "Vortex 14", lines: (d) => { const n = 14, t = tr(d), vp = d.map((x, i) => (i ? Math.abs(x.high - d[i - 1].low) : 0)), vm = d.map((x, i) => (i ? Math.abs(x.low - d[i - 1].high) : 0)), sm = (a, i) => { let z = 0; for (let j = i - n + 1; j <= i; j++) z += a[j]; return z; }; return [{ v: d.map((_, i) => (i < n ? null : sm(vp, i) / (sm(t, i) || 1))), c: "#2be07e" }, { v: d.map((_, i) => (i < n ? null : sm(vm, i) / (sm(t, i) || 1))), c: "#ff4a4a" }]; }, levels: [1] },
  };
  NX.gpIndicatorCount = Object.keys(OV).length + Object.keys(ST).length;

  // ------------------------------------------------------------------ helpers
  const PREF = "nx_gp_prefs", DRAW = "nx_gp_draw:";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const INTERVALS = [["1m", "1M"], ["2m", "2M"], ["5m", "5M"], ["15m", "15M"], ["30m", "30M"], ["60m", "1H"], ["2h", "2H"], ["4h", "4H"], ["1d", "D"], ["1wk", "W"], ["1mo", "MO"], ["3mo", "3MO"]];
  const INTERVAL_RANGE = { "1m": "1D", "2m": "5D", "5m": "5D", "15m": "1M", "30m": "1M", "60m": "3M", "2h": "3M", "4h": "6M", "1d": "1Y", "1wk": "5Y", "1mo": "MAX", "3mo": "MAX" };
  const API_INTERVAL = { "2h": "60m", "4h": "60m" }; // Yahoo has no 2h/4h bars: built here from hourly bars
  const INTRADAY = new Set(["1m", "2m", "5m", "15m", "30m", "60m", "2h", "4h"]);
  const RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "2Y", "5Y", "MAX"];
  const TOOLS = [["cursor", "✥", "Pan / cursor"], ["select", "⬚", "Select a drawing (then Delete removes it)"], "|", ["trend", "╱", "Trendline"], ["ray", "➚", "Ray"], ["arrow", "➜", "Arrow"], ["hline", "─", "Horizontal line"], ["hray", "⟶", "Horizontal ray (level that runs to the right)"], ["vline", "│", "Vertical line"], ["channel", "⫽", "Parallel channel"], ["pitch", "ψ", "Andrews pitchfork"], ["avwap", "Ⓥ", "Anchored VWAP: click a candle, a VWAP line starts from it"], "|",
    ["match", "🔍", "Pattern match: drag a box around candles and find the same shape earlier in history"], ["gex3d", "γ", "Gamma 3D: drag a box over a price range to see the gamma surface of those strikes"], ["rect", "▭", "Rectangle"], ["ellipse", "◯", "Ellipse"], ["fib", "☰", "Fibonacci retracement"], ["measure", "↔", "Measure (price, %, bars, time)"], ["long", "▲", "Long position (entry, target, stop)"], ["short", "▼", "Short position (entry, target, stop)"], "|", ["brush", "✎", "Freehand brush"], ["text", "T", "Text note"], ["alert", "🔔", "Price alert line: click a price, get a Telegram message when it trades there"]];
  const HINTS = { cursor: "Right-click anywhere on the chart for options (alert, lines, draw from here, snapshot, copy price). Drag a drawing, or its square handles, to move or reshape it.", select: "Click a drawing to select it, then press Delete to remove it.", trend: "Drag from the start to the end of the line.", ray: "Drag: the line continues past the second point.", arrow: "Drag from the tail to the head.", hline: "Click a price level.", hray: "Click a price level: the line runs from there to the right.", avwap: "Click the candle to anchor the VWAP on (a swing low or high, an open, news).", pitch: "Drag from the first pivot to the second pivot, release, then click the third pivot. Hold Shift to ignore the magnet.", vline: "Click a point in time.",
    channel: "Drag the first line, release, then click to set the channel width.", gex3d: "Drag a box over the price range you care about: a 3D gamma surface of the strikes in that range opens (strike x expiration).", match: "Drag a box around the candles you want to match. The same shape is searched in earlier history, with what happened next.", rect: "Drag a box around the zone.", ellipse: "Drag the bounding box.", fib: "Drag from the swing high to the swing low (or the reverse).", measure: "Drag between two points: shows the move, %, bars and time.",
    long: "Drag from the entry to the target. The stop is placed at half that distance (2:1 reward to risk).", short: "Drag from the entry to the target. The stop is placed at half that distance (2:1 reward to risk).", brush: "Hold and draw freely.", text: "Click where the note goes.", alert: "Click the price level to alert on. You get a Telegram message when it trades there." };
  const FIBS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  // build 2h / 4h bars from hourly bars: crypto by UTC blocks, stocks by chunks of n bars inside each New York session
  const aggregate = (cs, n, crypto) => {
    const out = []; let cur = null, key = null, day = null, cnt = 0;
    for (const c of cs) {
      let k;
      if (crypto) k = Math.floor(c[0] / (n * 3600));
      else { const d = new Date(c[0] * 1000).toLocaleDateString("en-CA", { timeZone: "America/New_York" }); if (d !== day) { day = d; cnt = 0; } k = `${d}:${Math.floor(cnt++ / n)}`; }
      if (k !== key) { key = k; cur = [c[0], c[1], c[2], c[3], c[4], c[5]]; out.push(cur); } else { cur[2] = Math.max(cur[2], c[2]); cur[3] = Math.min(cur[3], c[3]); cur[4] = c[4]; cur[5] += c[5]; }
    }
    return out;
  };
  // small in-page dialog (input or confirm) that resolves to the text, true, or null when cancelled
  const ask = (title, o = {}) => new Promise((res) => {
    const d = document.createElement("div"); d.className = "nx-help";
    d.innerHTML = `<div class="nx-help-box gp-ask"><div class="bb-ph"><span class="bb-n">?</span>${esc(title)}</div><div class="bb-pad">${o.input === false ? "" : `<input class="ctl-in" style="max-width:none;width:100%;box-sizing:border-box" placeholder="${esc(o.placeholder || "")}" value="${esc(o.value || "")}"/>`}<div class="gp-ask-b"><button class="bb-chip" data-x="0">CANCEL</button><button class="bb-chip on" data-x="1">${o.ok || "SAVE"}</button></div></div></div>`;
    document.body.appendChild(d);
    const inp = d.querySelector("input"); if (inp) { inp.focus(); inp.select(); }
    const done = (v) => { d.remove(); res(v); };
    d.addEventListener("click", (e) => { if (e.target === d) return done(null); const b = e.target.closest("[data-x]"); if (b) done(b.dataset.x === "1" ? (inp ? inp.value : true) : null); });
    d.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp ? inp.value : true); if (e.key === "Escape") done(null); });
  });
  const heikin = (d) => { const o = []; d.forEach((x, i) => { const c = (x.open + x.high + x.low + x.close) / 4, op = i ? (o[i - 1].open + o[i - 1].close) / 2 : (x.open + x.close) / 2; o.push({ ...x, open: op, close: c, high: Math.max(x.high, op, c), low: Math.min(x.low, op, c) }); }); return o; };
  // swing-pivot detector for the "AI, mark highs and lows" chart action (window.NX.chartAI.markHighsLows):
  // bar i is a swing high if its high is the max of a window of `look` bars on each side, a swing low the mirror
  // of that - the standard, deterministic definition. Never a guess from an LLM reading a wall of numbers.
  const LOOKBACK_BY_SENSITIVITY = { tight: 3, normal: 5, wide: 10 };
  const swingPoints = (bars, look = 5) => {
    const out = [];
    for (let i = look; i < bars.length - look; i++) {
      const win = bars.slice(i - look, i + look + 1);
      if (bars[i].high === Math.max(...win.map((x) => x.high))) out.push({ t: bars[i].t, p: bars[i].high, label: "H" });
      else if (bars[i].low === Math.min(...win.map((x) => x.low))) out.push({ t: bars[i].t, p: bars[i].low, label: "L" });
    }
    return out;
  };
  // "AI, mark a Wyckoff setup" chart action (window.NX.chartAI.markWyckoff): the detector itself runs server-side
  // (app/academy/wyckoff.py - the exact same rule-based finder the ACADEMY drill library uses), scanning whatever
  // candles are already loaded on this chart. Only GP intervals that map to one of its timeframes are supported.
  const WYCKOFF_TF = { "15m": "15m", "60m": "1h", "4h": "4h", "1d": "1d" };
  // which Wyckoff events sit at a LOW vs a HIGH price on the real (un-mirrored) bars - accumulation's SC/ST/
  // Spring/LPS are lows and AR/SOS are highs; distribution mirrors every one of those roles (see wyckoff.py's
  // "mirror the price axis" comment), and its own event names change too (BC/UT/SOW/LPSY).
  const WYCKOFF_LOW_EVENT = { sc: true, st: true, spring: true, lps: true, ar: false, sos: false };
  const WYCKOFF_LABEL = {
    accumulation: { sc: "SC", ar: "AR", st: "ST", spring: "SPRING", sos: "SOS", lps: "LPS" },
    distribution: { sc: "BC", ar: "AR", st: "ST", spring: "UT", sos: "SOW", lps: "LPSY" },
  };
  const menuHtml = (id, title, obj, on) => `<div class="gp-dd" id="${id}"><button class="bb-chip">${title} ▾</button><div class="gp-dd-pop hidden">${Object.entries(obj).map(([k, v]) => `<label><input type="checkbox" data-k="${k}" ${on.includes(k) ? "checked" : ""}/><i style="background:${v.c || "#888"}"></i>${v.l}</label>`).join("")}</div></div>`;

  // ------------------------------------------------------------------ render
  async function render(body, ctx) {
    const { sx, getData, disposeChart } = ctx, s = sx.sec, LW = window.LightweightCharts;
    const st = (sx.gp = sx.gp || Object.assign({ range: "1Y", interval: null, type: "candle", ov: ["sma50", "sma200"], st: ["volsma"], log: false, vp: false, cmp: "" }, lget(PREF, {})));
    st.ov = st.ov || []; st.st = st.st || ["volsma"];
    const save = () => lset(PREF, { range: st.range, interval: st.interval, type: st.type, ov: st.ov, st: st.st, log: st.log, vp: st.vp, cmenu: st.cmenu, magnet: st.magnet, keep: st.keep, pre: st.pre, wheel: st.wheel });
    const key = DRAW + s.symbol;
    let drawings = lget(key, []), tool = "cursor", cur = null, sel = -1, hov = -1, drag = null, saveT = 0;
    if (st.magnet === undefined) st.magnet = true;
    if (st.pre === undefined) st.pre = true; // pre-market / after-hours bars on intraday stock charts
    body.innerHTML = `<div class="bb-panel gp" style="--i:1">
      <div class="bb-ph"><span class="bb-n">1</span>PRICE CHART<span class="sp"></span><span class="meta" id="gp-meta"></span></div>
      <div class="gp-bar">${NX.chips("gp-int", INTERVALS.map(([v, l]) => [v, l]), st.interval || "")}<span class="gp-sep"></span>${NX.chips("gp-range", RANGES.map((r) => [r, r]), st.range)}<span class="gp-sep"></span>
        ${NX.chips("gp-type", [["candle", "CANDLE"], ["bar", "BAR"], ["ha", "HEIKIN"], ["line", "LINE"], ["area", "AREA"], ["fp", "FOOTPRINT"]], st.type)}</div>
      <div class="gp-bar">${menuHtml("gp-ov", "OVERLAYS", OV, st.ov)}${menuHtml("gp-st", "STUDIES", ST, st.st)}<span class="gp-sep"></span>
        <div class="gp-tools">${TOOLS.map((t) => (t === "|" ? '<span class="gp-sep"></span>' : `<button class="gp-tool ${t[0] === "cursor" ? "on" : ""}" data-t="${t[0]}" title="${t[2]}">${t[1]}</button>`)).join("")}<button class="gp-tool" id="gp-undo" title="Undo last drawing">↶</button><button class="gp-tool" id="gp-clear" title="Clear all drawings">🗑</button></div><span class="gp-sep"></span>
        <input class="ctl-in" id="gp-cmp" placeholder="Compare: MSFT" value="${esc(st.cmp)}" style="min-width:90px;max-width:120px"/><button class="bb-chip ${st.log ? "on" : ""}" id="gp-log">LOG</button><button class="bb-chip ${st.vp ? "on" : ""}" id="gp-vp">VOL PROFILE</button><button class="bb-chip ${st.cmenu === true ? "on" : ""}" id="gp-cm" title="Also open the options menu on a left-click (right-click always opens it)">CLICK MENU</button><button class="bb-chip ${st.pre ? "on" : ""}" id="gp-pre" title="Include pre-market (04:00-09:30 ET) and after-hours bars on intraday stock charts">PRE/POST</button><button class="bb-chip ${st.wheel ? "on" : ""}" id="gp-wheel" title="Off: the mouse wheel scrolls the page (Ctrl + wheel zooms the chart). On: the wheel zooms the chart.">WHEEL ZOOM</button><button class="bb-chip ${st.magnet ? "on" : ""}" id="gp-mag" title="Magnet: drawings snap to the nearest candle open/high/low/close (hold Shift to ignore)">MAGNET</button><button class="bb-chip ${st.keep ? "on" : ""}" id="gp-keep" title="Keep the drawing tool armed after each drawing (Esc to stop)">KEEP TOOL</button><span class="gp-sep"></span><button class="bb-chip on" id="gp-snap" title="Save this chart, with your drawings, as a screenshot attached to this security">📷 SAVE SNAPSHOT</button></div>
      <div class="gp-alerts" id="gp-al"></div>
      <div class="gp-legend" id="gp-legend">&nbsp;</div><div class="gp-wrap" id="gp-wrap"><div id="gp-chart" class="gp-main"></div><canvas id="gp-cv" class="gp-cv"></canvas></div><div id="gp-subs"></div><div id="gp-match" class="gp-match hidden"></div></div>
      <div class="bb-panel gp-snaps" style="--i:2" id="gp-snaps-box"><div class="bb-ph"><span class="bb-n">2</span>SAVED SNAPSHOTS<span class="sp"></span><span class="meta" id="gp-snaps-n"></span></div><div class="gp-snaps-g" id="gp-snaps"><div class="bb-empty">Loading…</div></div></div>`;
    const wrap = $("#gp-wrap"), mainEl = $("#gp-chart"), cv = $("#gp-cv"), subsEl = $("#gp-subs"), legendEl = $("#gp-legend");
    let alive = true, charts = [], series = null, data = [], secs = [], obs = null, ctxd = cv.getContext("2d");
    let effInterval = null; // the interval actually on screen right now: st.interval when explicitly chosen, else whatever Yahoo picked for the range (see build())
    let chartAI = null;
    sx.chart = { remove() { alive = false; charts.forEach((c) => { try { c.remove(); } catch (e) { /* gone */ } }); charts = []; if (obs) obs.disconnect(); if (NX.chartAI === chartAI) NX.chartAI = null; } };

    const isIntraday = () => (st.interval ? INTRADAY.has(st.interval) : ["1D", "5D", "1M"].includes(st.range));
    const isCrypto = /-USD$/.test(s.symbol) || String(s.type || "").toUpperCase() === "CRYPTOCURRENCY";
    const tfLabel = () => (st.interval ? `${(INTERVALS.find((x) => x[0] === st.interval) || [0, st.interval])[1]} bars · ${st.range}` : st.range);
    // logical index <-> unix seconds (linear inside the data, extrapolated with the average bar spacing outside it)
    // the median bar spacing, not the average: overnight/weekend gaps inflate the average, which pushed drawings that lie
    // outside the loaded bars (e.g. made on an earlier day) to the wrong place on intraday charts
    let stepC = 0, stepN = -1;
    const step = () => { if (secs.length < 2) return 86400; if (stepN !== secs.length) { const dl = []; for (let i = 1; i < secs.length; i++) dl.push(secs[i] - secs[i - 1]); dl.sort((a, b) => a - b); stepC = dl[dl.length >> 1] || 86400; stepN = secs.length; } return stepC; };
    const toIdx = (t) => { if (!secs.length) return 0; if (t <= secs[0]) return (t - secs[0]) / step(); if (t >= secs[secs.length - 1]) return secs.length - 1 + (t - secs[secs.length - 1]) / step(); let lo = 0, hi = secs.length - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; secs[m] <= t ? (lo = m) : (hi = m); } return lo + (t - secs[lo]) / (secs[hi] - secs[lo]); };
    const toSec = (i) => (i <= 0 ? secs[0] + i * step() : i >= secs.length - 1 ? secs[secs.length - 1] + (i - (secs.length - 1)) * step() : secs[Math.floor(i)] + (i - Math.floor(i)) * (secs[Math.floor(i) + 1] - secs[Math.floor(i)]));

    const xy = (p) => { const chart = charts[0]; if (!chart || !series) return null; const x = chart.timeScale().logicalToCoordinate(toIdx(p.t)), y = series.priceToCoordinate(p.p); return x === null || y === null ? null : { x, y }; };
    const inv = (x, y) => { const chart = charts[0]; const l = chart.timeScale().coordinateToLogical(x), pr = series.coordinateToPrice(y); return l === null || pr === null ? null : { t: toSec(l), p: pr }; };

    let raf = 0;
    const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; overlay(); }); };
    const overlay = () => {
      if (!alive || !charts[0] || !series) return;
      const w = mainEl.clientWidth, h = mainEl.clientHeight, dpr = window.devicePixelRatio || 1;
      if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; cv.style.width = `${w}px`; cv.style.height = `${h}px`; }
      ctxd.setTransform(dpr, 0, 0, dpr, 0, 0); ctxd.clearRect(0, 0, w, h);
      ctxd.font = `${Math.round(10 * NZ())}px 'JetBrains Mono', Consolas, monospace`;
      if (st.type === "fp" && data.length) footprint(w, h);
      if (st.vp && data.length) volumeProfile(w, h);
      const list = [...drawings, ...(cur ? [cur] : []), ...(matchBox ? [matchBox] : [])];
      list.forEach((dr, i) => shape(dr, i === sel || i === hov, w, h));
      if (sel >= 0) handlesOf(sel); if (hov >= 0 && hov !== sel) handlesOf(hov);
      if (matchHi) markHi(w, h);
    };
    const markHi = (w, h) => {
      const x = (t) => charts[0].timeScale().logicalToCoordinate(toIdx(t)), x0 = x(matchHi.t0), x1 = x(matchHi.t1); if (x0 === null || x1 === null) return;
      ctxd.fillStyle = "rgba(90,169,255,0.16)"; ctxd.fillRect(x0, 0, Math.max(3, x1 - x0), h); ctxd.strokeStyle = "#5aa9ff"; ctxd.lineWidth = 1.6; ctxd.setLineDash([]); ctxd.strokeRect(x0, 1, Math.max(3, x1 - x0), h - 2);
      tag(matchHi.label, x0 + 4, 14, "#5aa9ff");
      if (matchHi.f0 !== null) { const y0 = x(matchHi.f0), y1 = x(matchHi.f1); if (y0 !== null && y1 !== null) { ctxd.fillStyle = "rgba(43,224,126,0.10)"; ctxd.fillRect(y0, 0, Math.max(3, y1 - y0), h); ctxd.strokeStyle = "#2be07e"; ctxd.setLineDash([4, 3]); ctxd.strokeRect(y0, 1, Math.max(3, y1 - y0), h - 2); ctxd.setLineDash([]); tag("what followed", y0 + 4, 28, "#2be07e"); } }
    };
    // VOLUME FOOTPRINT (estimated). Yahoo serves candles with a total volume, not the trades behind them, so the volume of each
    // candle is spread over its price range (heaviest near where it closed) and split into sell / buy by how the candle closed.
    // It shows where each candle did its business and a delta estimate, but it is NOT real order flow (no bid / ask data).
    const niceStep = (x) => { const e = Math.pow(10, Math.floor(Math.log10(x))), m = x / e; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e; };
    const fpRows = (b, step) => { // [{p, buy, sell}] price rows of one candle
      const lo = Math.floor(b.low / step) * step, hi = Math.ceil(b.high / step) * step, rng = Math.max(b.high - b.low, step), mu = (b.high + b.low + 2 * b.close) / 4, sg = rng / 3.2;
      const rows = []; let tot = 0;
      for (let p = lo; p <= hi + 1e-9; p += step) { const wgt = Math.exp(-0.5 * ((p + step / 2 - mu) / sg) ** 2) + 0.05; rows.push({ p, wgt, buy: 0, sell: 0 }); tot += wgt; }
      const share = Math.max(0.1, Math.min(0.9, 0.5 + 0.4 * ((b.close - b.open) / rng)));
      rows.forEach((r, i) => { const v = (b.volume || 0) * (r.wgt / tot), tilt = rows.length > 1 ? (i / (rows.length - 1) - 0.5) * 0.2 : 0, bs = Math.max(0.05, Math.min(0.95, share + tilt)); r.buy = v * bs; r.sell = v * (1 - bs); });
      return rows;
    };
    const fpFmt = (v) => (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + "k" : String(Math.round(v)));
    const footprint = (w, h) => {
      const ts = charts[0].timeScale(), r = ts.getVisibleLogicalRange(); if (!r) return;
      const a = Math.max(0, Math.floor(r.from) - 1), b = Math.min(data.length - 1, Math.ceil(r.to) + 1); if (b < a) return;
      const x0 = ts.logicalToCoordinate(a), x1 = ts.logicalToCoordinate(Math.min(a + 1, data.length - 1)), sp = x0 !== null && x1 !== null ? Math.abs(x1 - x0) : 0;
      const nz = NZ(), ref = data[Math.max(0, b)].close, y0 = series.priceToCoordinate(ref), y1 = series.priceToCoordinate(ref * 1.001);
      if (y0 === null || y1 === null || !sp) return;
      const pxPerPrice = Math.abs(y1 - y0) / (ref * 0.001), step = niceStep((13 * nz) / pxPerPrice), bw = Math.max(2, sp * 0.86);
      if (sp < 16 * nz) { // too narrow for numbers: plain candles so the chart stays readable when zoomed out
        for (let i = a; i <= b; i++) { const x = ts.logicalToCoordinate(i), c = data[i]; if (x === null) continue; const up = c.close >= c.open, col = up ? "#2be07e" : "#ff4a4a", yH = series.priceToCoordinate(c.high), yL = series.priceToCoordinate(c.low), yO = series.priceToCoordinate(c.open), yC = series.priceToCoordinate(c.close); if ([yH, yL, yO, yC].some((v) => v === null)) continue; ctxd.strokeStyle = col; ctxd.fillStyle = col; ctxd.lineWidth = 1; ctxd.beginPath(); ctxd.moveTo(x, yH); ctxd.lineTo(x, yL); ctxd.stroke(); ctxd.fillRect(x - bw / 2, Math.min(yO, yC), bw, Math.max(1, Math.abs(yC - yO))); }
        return;
      }
      const rowH = Math.abs(step * pxPerPrice), showNum = bw >= 44 * nz && rowH >= 9 * nz, half = bw / 2;
      ctxd.font = `${Math.round(9 * nz)}px 'JetBrains Mono', Consolas, monospace`; ctxd.textBaseline = "middle";
      for (let i = a; i <= b; i++) {
        const x = ts.logicalToCoordinate(i); if (x === null || x < -bw || x > w + bw) continue;
        const bar = data[i];
        if (!(bar.volume > 0)) { // no volume reported (e.g. pre-market bars of some stocks): draw it as a plain candle
          const up = bar.close >= bar.open, col = up ? "#2be07e" : "#ff4a4a", yH = series.priceToCoordinate(bar.high), yL = series.priceToCoordinate(bar.low), yO = series.priceToCoordinate(bar.open), yC = series.priceToCoordinate(bar.close);
          if ([yH, yL, yO, yC].every((v) => v !== null)) { ctxd.strokeStyle = col; ctxd.fillStyle = col; ctxd.lineWidth = 1; ctxd.beginPath(); ctxd.moveTo(x, yH); ctxd.lineTo(x, yL); ctxd.stroke(); ctxd.globalAlpha = 0.55; ctxd.fillRect(x - Math.min(bw, 12) / 2, Math.min(yO, yC), Math.min(bw, 12), Math.max(1, Math.abs(yC - yO))); ctxd.globalAlpha = 1; }
          continue;
        }
        const rows = fpRows(bar, step); let mx = 0, poc = 0, dsum = 0;
        rows.forEach((rw, k) => { const t = rw.buy + rw.sell; if (t > mx) { mx = t; poc = k; } dsum += rw.buy - rw.sell; });
        rows.forEach((rw, k) => {
          const yT = series.priceToCoordinate(rw.p + step), yB = series.priceToCoordinate(rw.p); if (yT === null || yB === null) return;
          const hh = Math.max(1, Math.abs(yB - yT) - 1), top = Math.min(yT, yB), heat = mx ? (rw.buy + rw.sell) / mx : 0;
          ctxd.fillStyle = `rgba(255,74,74,${(0.12 + 0.5 * heat * (rw.sell / ((rw.buy + rw.sell) || 1))).toFixed(3)})`; ctxd.fillRect(x - half, top, half - 0.5, hh);
          ctxd.fillStyle = `rgba(43,224,126,${(0.12 + 0.5 * heat * (rw.buy / ((rw.buy + rw.sell) || 1))).toFixed(3)})`; ctxd.fillRect(x + 0.5, top, half - 0.5, hh);
          if (k === poc) { ctxd.strokeStyle = "#ffa028"; ctxd.lineWidth = 1.4; ctxd.strokeRect(x - half, top, bw, hh); }
          if (showNum) { ctxd.fillStyle = "#e8e8e8"; ctxd.textAlign = "right"; ctxd.fillText(fpFmt(rw.sell), x - 3, top + hh / 2); ctxd.textAlign = "left"; ctxd.fillText(fpFmt(rw.buy), x + 3, top + hh / 2); }
        });
        const yLow = series.priceToCoordinate(bar.low), yHigh = series.priceToCoordinate(bar.high);
        if (yLow !== null && yHigh !== null) { ctxd.strokeStyle = bar.close >= bar.open ? "#2be07e" : "#ff4a4a"; ctxd.lineWidth = 1; ctxd.beginPath(); ctxd.moveTo(x, yHigh - 4); ctxd.lineTo(x, yHigh); ctxd.moveTo(x, yLow); ctxd.lineTo(x, yLow + 4); ctxd.stroke(); }
        if (showNum && yLow !== null) { ctxd.textAlign = "center"; ctxd.fillStyle = dsum >= 0 ? "#2be07e" : "#ff4a4a"; ctxd.fillText(`Δ ${dsum >= 0 ? "+" : "-"}${fpFmt(Math.abs(dsum))}`, x, yLow + 15 * nz); ctxd.fillStyle = "#8b8b8b"; ctxd.fillText(fpFmt(bar.volume || 0), x, yLow + 26 * nz); }
      }
      ctxd.textAlign = "left"; ctxd.textBaseline = "alphabetic"; tag("FOOTPRINT · estimated from candle volume (no bid/ask data) · sell | buy · amber = most volume · Δ = buy - sell", 8, h - 8, "#ffa028");
    };
    const volumeProfile = (w, h) => {
      const r = charts[0].timeScale().getVisibleLogicalRange(); if (!r) return;
      const a = Math.max(0, Math.floor(r.from)), b = Math.min(data.length - 1, Math.ceil(r.to)); if (b <= a) return;
      const vis = data.slice(a, b + 1), lo = Math.min(...vis.map((x) => x.low)), hi = Math.max(...vis.map((x) => x.high)), N = 32, bins = new Array(N).fill(0);
      vis.forEach((x) => { const l = Math.max(0, Math.min(N - 1, Math.floor(((x.low - lo) / (hi - lo || 1)) * N))), u = Math.max(0, Math.min(N - 1, Math.floor(((x.high - lo) / (hi - lo || 1)) * N))); for (let k = l; k <= u; k++) bins[k] += x.volume / (u - l + 1); });
      const mx = Math.max(...bins), poc = bins.indexOf(mx), pw = w * 0.2 - 60;
      bins.forEach((v, k) => { const p0 = lo + ((k + 1) / N) * (hi - lo), p1 = lo + (k / N) * (hi - lo), y0 = series.priceToCoordinate(p0), y1 = series.priceToCoordinate(p1); if (y0 === null || y1 === null) return; ctxd.fillStyle = k === poc ? "rgba(255,160,40,0.55)" : "rgba(90,169,255,0.28)"; const bw = (v / mx) * pw; ctxd.fillRect(w - 62 - bw, y0, bw, Math.max(1, y1 - y0 - 1)); });
      const yp = series.priceToCoordinate(lo + ((poc + 0.5) / N) * (hi - lo)); if (yp !== null) { ctxd.fillStyle = "#ffa028"; ctxd.fillText("POC " + px(lo + ((poc + 0.5) / N) * (hi - lo)), w - 62 - pw, yp - 3); }
    };
    const P = (dr) => ({ a: dr.a && xy(dr.a), b: dr.b && xy(dr.b), c: dr.c && xy(dr.c) });
    const lineAt = (dr, t) => { const ia = toIdx(dr.a.t), ib = toIdx(dr.b.t), it = toIdx(t); return ib === ia ? dr.a.p : dr.a.p + ((dr.b.p - dr.a.p) * (it - ia)) / (ib - ia); };
    const chan = (dr) => { const { a, b } = P(dr); if (!a || !b) return null; if (!dr.c) return { a, b }; const off = dr.c.p - lineAt(dr, dr.c.t), a2 = xy({ t: dr.a.t, p: dr.a.p + off }), b2 = xy({ t: dr.b.t, p: dr.b.p + off }); return a2 && b2 ? { a, b, a2, b2 } : { a, b }; };
    const posGeo = (dr) => { const { a, b } = P(dr); if (!a || !b) return null; const stopP = dr.a.p - (dr.b.p - dr.a.p) / 2, yS = series.priceToCoordinate(stopP); return yS === null ? null : { a, b, stopP, yS, x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x) }; };
    const fmtDur = (sec) => { const v = Math.abs(sec); return v >= 86400 ? `${(v / 86400).toFixed(1)}d` : v >= 3600 ? `${(v / 3600).toFixed(1)}h` : `${Math.round(v / 60)}m`; };
    const tag = (text, x, y, color) => { const w = ctxd.measureText(text).width + 8; ctxd.fillStyle = "rgba(0,0,0,0.78)"; ctxd.fillRect(x, y - 11, w, 15); ctxd.fillStyle = color; ctxd.fillText(text, x + 4, y); };
    const shape = (dr, hot, w, h) => {
      const col = hot ? "#ffffff" : (dr.color || "#ffa028"); ctxd.strokeStyle = col; ctxd.fillStyle = col; ctxd.lineWidth = hot ? 2.2 : 1.4; ctxd.setLineDash([]);
      const { a, b } = P(dr), dot = (p) => { ctxd.beginPath(); ctxd.arc(p.x, p.y, 3, 0, 6.3); ctxd.fill(); };
      if (dr.k === "hray" && a) { ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(w, a.y); ctxd.stroke(); dot(a); ctxd.fillText(`${dr.title ? dr.title + " " : ""}${px(dr.a.p)}`, a.x + 6, a.y - 4); }
      else if (dr.k === "avwap" && a) { const pts = avwapPts(dr); ctxd.strokeStyle = hot ? "#fff" : (dr.color || "#c58bff"); ctxd.lineWidth = hot ? 2.4 : 1.8; ctxd.beginPath(); let on = false; pts.forEach((q2) => { on ? ctxd.lineTo(q2.x, q2.y) : ctxd.moveTo(q2.x, q2.y); on = true; }); ctxd.stroke(); ctxd.fillStyle = ctxd.strokeStyle; ctxd.beginPath(); ctxd.arc(a.x, a.y, 3.5, 0, 6.3); ctxd.fill(); const lp = pts[pts.length - 1]; if (lp) tag(`AVWAP ${px(lp.v)}`, Math.max(4, lp.x - 90), lp.y - 6, hot ? "#fff" : "#c58bff"); }
      else if (dr.k === "pitch" && a && b) {
        const c = dr.c && xy(dr.c);
        ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(b.x, b.y); ctxd.stroke(); dot(a); dot(b);
        if (c) {
          const M = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }, dx = M.x - a.x, dy = M.y - a.y, k = dx === 0 ? 0 : Math.max(1, (w * 3) / Math.abs(dx)), ext = (q2) => ({ x: q2.x + dx * k, y: q2.y + dy * k });
          ctxd.fillStyle = "rgba(255,160,40,0.08)"; ctxd.beginPath(); ctxd.moveTo(b.x, b.y); ctxd.lineTo(ext(b).x, ext(b).y); ctxd.lineTo(ext(c).x, ext(c).y); ctxd.lineTo(c.x, c.y); ctxd.closePath(); ctxd.fill();
          ctxd.strokeStyle = col; [a, b, c].forEach((q2, n) => { const e2 = ext(q2); ctxd.setLineDash(n ? [] : [5, 4]); ctxd.beginPath(); ctxd.moveTo(q2.x, q2.y); ctxd.lineTo(e2.x, e2.y); ctxd.stroke(); }); ctxd.setLineDash([]); dot(c);
        }
      }
      else if (dr.k === "hline" && a) { ctxd.beginPath(); ctxd.moveTo(0, a.y); ctxd.lineTo(w, a.y); ctxd.stroke(); ctxd.fillText(`${dr.title ? dr.title + " " : ""}${px(dr.a.p)}`, 6, a.y - 3); }
      else if (dr.k === "vline" && a) { ctxd.beginPath(); ctxd.moveTo(a.x, 0); ctxd.lineTo(a.x, h); ctxd.stroke(); }
      else if (dr.k === "trend" && a && b) { ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(b.x, b.y); ctxd.stroke(); dot(a); dot(b); ctxd.fillText(`${signed(dr.b.p - dr.a.p)} (${pct((dr.b.p / dr.a.p - 1) * 100)})`, b.x + 6, b.y - 6); }
      else if (dr.k === "arrow" && a && b) { const ang = Math.atan2(b.y - a.y, b.x - a.x); ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(b.x, b.y); ctxd.stroke(); ctxd.beginPath(); ctxd.moveTo(b.x, b.y); ctxd.lineTo(b.x - 12 * Math.cos(ang - 0.45), b.y - 12 * Math.sin(ang - 0.45)); ctxd.lineTo(b.x - 12 * Math.cos(ang + 0.45), b.y - 12 * Math.sin(ang + 0.45)); ctxd.closePath(); ctxd.fill(); }
      else if (dr.k === "ray" && a && b) { const dx = b.x - a.x, dy = b.y - a.y, k = dx === 0 ? 0 : (w * 3) / Math.abs(dx); ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(a.x + dx * Math.max(k, 1), a.y + dy * Math.max(k, 1)); ctxd.stroke(); dot(a); }
      else if (dr.k === "channel") { const c = chan(dr); if (c) { ctxd.beginPath(); ctxd.moveTo(c.a.x, c.a.y); ctxd.lineTo(c.b.x, c.b.y); ctxd.stroke(); if (c.a2) { ctxd.beginPath(); ctxd.moveTo(c.a2.x, c.a2.y); ctxd.lineTo(c.b2.x, c.b2.y); ctxd.stroke(); ctxd.fillStyle = "rgba(255,160,40,0.09)"; ctxd.beginPath(); ctxd.moveTo(c.a.x, c.a.y); ctxd.lineTo(c.b.x, c.b.y); ctxd.lineTo(c.b2.x, c.b2.y); ctxd.lineTo(c.a2.x, c.a2.y); ctxd.closePath(); ctxd.fill(); ctxd.setLineDash([4, 4]); ctxd.beginPath(); ctxd.moveTo((c.a.x + c.a2.x) / 2, (c.a.y + c.a2.y) / 2); ctxd.lineTo((c.b.x + c.b2.x) / 2, (c.b.y + c.b2.y) / 2); ctxd.stroke(); ctxd.setLineDash([]); } dot(c.a); dot(c.b); } }
      else if (dr.k === "rect" && a && b) { ctxd.fillStyle = "rgba(255,160,40,0.10)"; ctxd.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); }
      else if (dr.k === "gex3d" && a && b) { ctxd.fillStyle = "rgba(180,140,255,0.13)"; ctxd.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.strokeStyle = "#b48cff"; ctxd.setLineDash([5, 3]); ctxd.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.setLineDash([]); tag(`GAMMA 3D ${Math.min(dr.a.p, dr.b.p).toFixed(2)} – ${Math.max(dr.a.p, dr.b.p).toFixed(2)}`, Math.min(a.x, b.x) + 4, Math.min(a.y, b.y) - 4, "#b48cff"); }
      else if (dr.k === "match" && a && b) { ctxd.fillStyle = "rgba(90,169,255,0.12)"; ctxd.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.strokeStyle = "#5aa9ff"; ctxd.setLineDash([5, 3]); ctxd.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.setLineDash([]); tag("MATCH", Math.min(a.x, b.x) + 4, Math.min(a.y, b.y) - 4, "#5aa9ff"); }
      else if (dr.k === "ellipse" && a && b) { ctxd.beginPath(); ctxd.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, 6.2832); ctxd.fillStyle = "rgba(255,160,40,0.10)"; ctxd.fill(); ctxd.stroke(); }
      else if (dr.k === "measure" && a && b) {
        const up = dr.b.p >= dr.a.p, c = hot ? "#fff" : up ? "#2be07e" : "#ff4a4a";
        ctxd.fillStyle = up ? "rgba(43,224,126,0.13)" : "rgba(255,74,74,0.13)"; ctxd.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.strokeStyle = c; ctxd.setLineDash([4, 3]); ctxd.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); ctxd.setLineDash([]);
        const bars = Math.round(toIdx(dr.b.t) - toIdx(dr.a.t)), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        tag(`${signed(dr.b.p - dr.a.p)}  (${pct((dr.b.p / dr.a.p - 1) * 100)})`, mx - 50, my - 6, c); tag(`${Math.abs(bars)} bars · ${fmtDur(dr.b.t - dr.a.t)}`, mx - 50, my + 11, "#cfcfcf");
      }
      else if ((dr.k === "long" || dr.k === "short") && posGeo(dr)) {
        const g = posGeo(dr), wd = Math.max(60, g.x1 - g.x0), x0 = g.x0;
        ctxd.fillStyle = "rgba(43,224,126,0.17)"; ctxd.fillRect(x0, Math.min(g.a.y, g.b.y), wd, Math.abs(g.b.y - g.a.y)); ctxd.fillStyle = "rgba(255,74,74,0.17)"; ctxd.fillRect(x0, Math.min(g.a.y, g.yS), wd, Math.abs(g.yS - g.a.y));
        ctxd.strokeStyle = hot ? "#fff" : "#8b8b8b"; ctxd.beginPath(); ctxd.moveTo(x0, g.a.y); ctxd.lineTo(x0 + wd, g.a.y); ctxd.stroke();
        ctxd.strokeStyle = hot ? "#fff" : "#2be07e"; ctxd.beginPath(); ctxd.moveTo(x0, g.b.y); ctxd.lineTo(x0 + wd, g.b.y); ctxd.stroke(); ctxd.strokeStyle = hot ? "#fff" : "#ff4a4a"; ctxd.beginPath(); ctxd.moveTo(x0, g.yS); ctxd.lineTo(x0 + wd, g.yS); ctxd.stroke();
        tag(`TARGET ${px(dr.b.p)} (${pct((dr.b.p / dr.a.p - 1) * 100)})`, x0 + 4, g.b.y + (g.b.y < g.a.y ? -4 : 14), "#2be07e"); tag(`STOP ${px(g.stopP)} (${pct((g.stopP / dr.a.p - 1) * 100)})`, x0 + 4, g.yS + (g.yS < g.a.y ? -4 : 14), "#ff4a4a");
        tag(`${dr.k === "long" ? "LONG" : "SHORT"} ${px(dr.a.p)} · reward:risk 2.0`, x0 + 4, g.a.y - 4, "#f1f1f1");
      }
      else if (dr.k === "fib" && a && b) { const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x, x0 + 120); FIBS.forEach((f) => { const p = dr.b.p + (dr.a.p - dr.b.p) * f, y = series.priceToCoordinate(p); if (y === null) return; ctxd.strokeStyle = f === 0 || f === 1 ? col : "rgba(255,160,40,0.7)"; ctxd.beginPath(); ctxd.moveTo(x0, y); ctxd.lineTo(x1, y); ctxd.stroke(); ctxd.fillStyle = col; ctxd.fillText(`${(f * 100).toFixed(1)}%  ${px(p)}`, x1 + 4, y + 3); }); ctxd.strokeStyle = "rgba(255,160,40,0.4)"; ctxd.setLineDash([3, 3]); ctxd.beginPath(); ctxd.moveTo(a.x, a.y); ctxd.lineTo(b.x, b.y); ctxd.stroke(); ctxd.setLineDash([]); }
      else if (dr.k === "brush" && dr.pts) { ctxd.beginPath(); let on = false; dr.pts.forEach((pt) => { const q2 = xy(pt); if (!q2) return; on ? ctxd.lineTo(q2.x, q2.y) : ctxd.moveTo(q2.x, q2.y); on = true; }); ctxd.stroke(); }
      else if (dr.k === "text" && a) { ctxd.font = `600 ${Math.round(12 * NZ())}px 'JetBrains Mono', Consolas, monospace`; ctxd.fillText(dr.text, a.x, a.y); ctxd.font = `${Math.round(10 * NZ())}px 'JetBrains Mono', Consolas, monospace`; }
      else if (dr.k === "pt" && a) {
        // `up` picks the arrow direction (true = a high, points down from above; false = a low, points up from
        // below) - explicit rather than inferred from the label text, since Wyckoff events (SC, AR, UT, ...) don't
        // spell out H/L the way the swing-highs/lows marker does.
        const up = dr.up !== undefined ? dr.up : dr.label === "H", c2 = hot ? "#ffffff" : (dr.color || (up ? "#ff4a4a" : "#2be07e"));
        ctxd.fillStyle = c2; ctxd.beginPath();
        if (up) { ctxd.moveTo(a.x, a.y - 10); ctxd.lineTo(a.x - 5, a.y - 1); ctxd.lineTo(a.x + 5, a.y - 1); } else { ctxd.moveTo(a.x, a.y + 10); ctxd.lineTo(a.x - 5, a.y + 1); ctxd.lineTo(a.x + 5, a.y + 1); }
        ctxd.closePath(); ctxd.fill();
        tag(`${dr.label} ${px(dr.a.p)}`, a.x + 7, a.y + (up ? -8 : 12), c2);
      }
    };
    // anchored VWAP from the anchor candle onward, recomputed from the loaded bars (so it works at every timeframe)
    const avwapPts = (dr) => {
      const out = []; if (!data.length) return out; let pv = 0, vv = 0;
      for (let i = Math.max(0, Math.ceil(toIdx(dr.a.t) - 1e-9)); i < data.length; i++) { const x = data[i], v = x.volume || 1; pv += ((x.high + x.low + x.close) / 3) * v; vv += v; const q2 = xy({ t: x.t, p: pv / vv }); if (q2) out.push({ x: q2.x, y: q2.y, v: pv / vv }); }
      return out;
    };
    const SINGLE = ["hline", "vline", "text", "pt", "avwap", "hray", "brush"];
    const handleAt = (dr, m) => { if (SINGLE.includes(dr.k)) return null; for (const k of ["a", "b", "c"]) { if (!dr[k]) continue; const q2 = xy(dr[k]); if (q2 && Math.hypot(m.x - q2.x, m.y - q2.y) < 9) return k; } return null; };
    const edgeHit = (dr, m) => { // like hit(), but a filled box/zone only counts on its outline, so the chart can still be panned inside a rectangle
      const { a, b } = P(dr), T = 7;
      if (dr.k === "rect" || dr.k === "measure" || dr.k === "ellipse") { if (!(a && b)) return false; const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y); return m.x >= x0 - T && m.x <= x1 + T && m.y >= y0 - T && m.y <= y1 + T && !(m.x > x0 + T && m.x < x1 - T && m.y > y0 + T && m.y < y1 - T); }
      if (dr.k === "long" || dr.k === "short") { const g = posGeo(dr); if (!g) return false; const wd = Math.max(60, g.x1 - g.x0); return m.x >= g.x0 - T && m.x <= g.x0 + wd + T && [g.a.y, g.b.y, g.yS].some((y) => Math.abs(m.y - y) < T); }
      if (dr.k === "channel") { const c = chan(dr); return !!c && (segD(m, c.a, c.b) < T || (!!c.a2 && segD(m, c.a2, c.b2) < T)); }
      return hit(dr, m);
    };
    const topHit = (m, edge) => { for (let i = drawings.length - 1; i >= 0; i--) if (edge ? edgeHit(drawings[i], m) : hit(drawings[i], m)) return i; return -1; };
    const handlesOf = (i) => { const dr = drawings[i]; if (!dr || SINGLE.includes(dr.k)) return; ctxd.setLineDash([]); ctxd.lineWidth = 1.5; ctxd.strokeStyle = "#ffffff"; ctxd.fillStyle = "#050505"; ["a", "b", "c"].forEach((k) => { if (!dr[k]) return; const q2 = xy(dr[k]); if (q2) { ctxd.beginPath(); ctxd.rect(q2.x - 4, q2.y - 4, 8, 8); ctxd.fill(); ctxd.stroke(); } }); };
    const snapPt = (m, p, e) => { // magnet: snap to the nearest candle's open/high/low/close when the mouse is close to one (hold Shift to switch it off)
      if (!st.magnet || (e && e.shiftKey) || !data.length || !p) return p;
      const i = Math.round(toIdx(p.t)); if (i < 0 || i >= data.length) return p;
      const b = data[i]; let best = null, bd = 18;
      [b.open, b.high, b.low, b.close].forEach((v) => { const y = series.priceToCoordinate(v); if (y !== null && Math.abs(y - m.y) < bd) { bd = Math.abs(y - m.y); best = v; } });
      return { t: b.t, p: best !== null ? best : p.p };
    };
    const segD = (p, a, b) => { const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy; let t = l2 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0; t = Math.max(0, Math.min(1, t)); return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy)); };
    const inBox = (m, a, b, T) => m.x >= Math.min(a.x, b.x) - T && m.x <= Math.max(a.x, b.x) + T && m.y >= Math.min(a.y, b.y) - T && m.y <= Math.max(a.y, b.y) + T;
    const hit = (dr, m) => {
      const { a, b } = P(dr), T = 7;
      if (dr.k === "hline") return !!a && Math.abs(m.y - a.y) < T;
      if (dr.k === "hray") return !!a && m.x >= a.x - T && Math.abs(m.y - a.y) < T;
      if (dr.k === "avwap") { const pts = avwapPts(dr); return pts.some((q2, i) => (i ? segD(m, pts[i - 1], q2) < T : Math.hypot(m.x - q2.x, m.y - q2.y) < T + 3)); }
      if (dr.k === "pitch") { const c = dr.c && xy(dr.c); if (!(a && b)) return false; if (!c) return segD(m, a, b) < T; const M = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }, f = (q2) => ({ x: q2.x + (M.x - a.x) * 60, y: q2.y + (M.y - a.y) * 60 }); return [a, b, c].some((q2) => segD(m, q2, f(q2)) < T) || segD(m, a, b) < T; }
      if (dr.k === "vline") return !!a && Math.abs(m.x - a.x) < T;
      if (dr.k === "trend" || dr.k === "arrow") return !!(a && b) && segD(m, a, b) < T;
      if (dr.k === "ray") return !!(a && b) && segD(m, a, { x: a.x + (b.x - a.x) * 60, y: a.y + (b.y - a.y) * 60 }) < T;
      if (dr.k === "rect" || dr.k === "ellipse" || dr.k === "measure") return !!(a && b) && inBox(m, a, b, T);
      if (dr.k === "channel") { const c = chan(dr); return !!c && (segD(m, c.a, c.b) < T || (c.a2 && (segD(m, c.a2, c.b2) < T || inBox(m, { x: Math.min(c.a.x, c.b.x), y: Math.min(c.a.y, c.b.y, c.a2.y, c.b2.y) }, { x: Math.max(c.a.x, c.b.x), y: Math.max(c.a.y, c.b.y, c.a2.y, c.b2.y) }, 0)))); }
      if (dr.k === "long" || dr.k === "short") { const g = posGeo(dr); return !!g && inBox(m, { x: g.x0, y: Math.min(g.a.y, g.b.y, g.yS) }, { x: g.x0 + Math.max(60, g.x1 - g.x0), y: Math.max(g.a.y, g.b.y, g.yS) }, T); }
      if (dr.k === "fib") return !!(a && b) && FIBS.some((f) => { const y = series.priceToCoordinate(dr.b.p + (dr.a.p - dr.b.p) * f); return y !== null && Math.abs(m.y - y) < T && m.x >= Math.min(a.x, b.x) - T && m.x <= Math.max(a.x, b.x, Math.min(a.x, b.x) + 120) + T; });
      if (dr.k === "text") return !!a && m.x >= a.x - 4 && m.x <= a.x + dr.text.length * 7.5 + 4 && Math.abs(m.y - a.y + 4) < 12;
      if (dr.k === "pt") return !!a && Math.hypot(m.x - a.x, m.y - a.y) < 10;
      if (dr.k === "brush") { const pts = (dr.pts || []).map(xy).filter(Boolean); return pts.some((q2, i) => i && segD(m, pts[i - 1], q2) < T); }
      return false;
    };
    // drawings are saved in the browser AND in the database (debounced), so they are still there in the exe window,
    // another browser or after the cache is cleared. Times are unix seconds, so they stay on the same candles at every timeframe.
    const putServer = () => { try { api("/snaps/drawings", { method: "PUT", body: JSON.stringify({ symbol: s.symbol, drawings }) }).catch(() => {}); } catch (e) { /* offline */ } };
    const persist = () => { lset(key, drawings); clearTimeout(saveT); saveT = setTimeout(putServer, 600); };
    (async () => { // on open: the server copy wins; a browser-only copy is uploaded once
      try {
        const r = await api(`/snaps/drawings?symbol=${q(s.symbol)}`);
        if (!alive) return;
        if (Array.isArray(r.drawings)) { if (JSON.stringify(r.drawings) !== JSON.stringify(drawings)) { drawings = r.drawings; lset(key, drawings); paint(); } }
        else if (drawings.length) putServer();
      } catch (e) { /* keep the local copy */ }
    })();

    // drawing interaction. Tool armed: the canvas takes the mouse. Cursor: the canvas only takes it while the pointer is on a
    // drawing's line or handle (so the chart still pans and zooms everywhere else) and lets you drag a drawing or one of its ends.
    let hovH = null;
    const pos = (e) => { const r = cv.getBoundingClientRect(), k = cv.offsetWidth ? r.width / cv.offsetWidth : 1; return { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k }; }; // k = leftover page zoom, if any
    const CHANNELISH = ["channel", "pitch"];
    const setTool = (t) => { tool = t; if (t !== "select") sel = -1; hov = -1; hovH = null; cv.style.pointerEvents = t === "cursor" ? "none" : "auto"; cv.style.cursor = t === "select" ? "pointer" : "crosshair"; body.querySelectorAll(".gp-tool[data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === t)); const hint = $("#gp-hint"); if (hint) hint.textContent = HINTS[t] || ""; paint(); };
    // after a drawing is finished: stay in the same tool when KEEP TOOL is on, otherwise back to the cursor
    const finishTool = () => { if (st.keep && !["text", "alert", "match", "gex3d"].includes(tool)) { cur = null; paint(); } else setTool("cursor"); };
    const setHover = (m) => {
      if (tool !== "cursor" || cur || drag) return;
      let i = -1, h = null;
      for (let k = drawings.length - 1; k >= 0; k--) { const hh = handleAt(drawings[k], m); if (hh) { i = k; h = hh; break; } }
      if (i < 0) i = topHit(m, true);
      hovH = h;
      if (i !== hov) { hov = i; paint(); }
      cv.style.pointerEvents = i >= 0 ? "auto" : "none"; cv.style.cursor = i >= 0 ? (h ? "crosshair" : "move") : "crosshair";
    };
    wrap.addEventListener("mousemove", (e) => { if (tool === "cursor" && cv.style.pointerEvents === "none" && !e.buttons) setHover(pos(e)); });
    // the wheel scrolls the PAGE by default (the chart used to swallow it); Ctrl + wheel, or the WHEEL ZOOM chip, zooms the time axis around the pointer
    wrap.addEventListener("wheel", (e) => {
      if (!(st.wheel || e.ctrlKey) || !charts[0]) return;
      const ts = charts[0].timeScale(), r = ts.getVisibleLogicalRange(); if (!r) return;
      e.preventDefault();
      const rect = mainEl.getBoundingClientRect(), k = mainEl.offsetWidth ? rect.width / mainEl.offsetWidth : 1, x = (e.clientX - rect.left) / k;
      const piv = ts.coordinateToLogical(x); const pv = piv === null ? (r.from + r.to) / 2 : piv, f = Math.exp(Math.max(-3, Math.min(3, e.deltaY / 100)) * 0.18);
      ts.setVisibleLogicalRange({ from: pv - (pv - r.from) * f, to: pv + (r.to - pv) * f });
    }, { passive: false });
    cv.addEventListener("mousedown", (e) => {
      const m = pos(e);
      if (tool === "select" || tool === "cursor") {
        let i = -1, h = null;
        if (tool === "cursor") { i = hov; h = hovH; }
        else { for (let k = drawings.length - 1; k >= 0; k--) { const hh = handleAt(drawings[k], m); if (hh) { i = k; h = hh; break; } } if (i < 0) i = topHit(m, false); }
        sel = i; if (i < 0) { paint(); return; }
        e.preventDefault(); const p0 = inv(m.x, m.y); if (!p0) return;
        drag = { i, h, p0, orig: JSON.parse(JSON.stringify(drawings[i])), moved: false }; paint(); return;
      }
      const p = snapPt(m, inv(m.x, m.y), e); if (!p) return;
      if (cur && cur.armed) { cur.b = p; delete cur.armed; if (CHANNELISH.includes(cur.k)) { cur.stage = 2; cur.c = p; paint(); return; } drawings.push(cur); cur = null; persist(); finishTool(); return; }
      if (cur && cur.stage === 2) { cur.c = p; delete cur.stage; drawings.push(cur); cur = null; persist(); finishTool(); return; }
      if (["hline", "vline", "hray", "avwap"].includes(tool)) { drawings.push({ k: tool, a: p }); persist(); paint(); return finishTool(); }
      if (tool === "text") { setTool("cursor"); ask("Text note on the chart", { placeholder: "e.g. breakout retest" }).then((t) => { if (t) { drawings.push({ k: "text", a: p, text: String(t).slice(0, 80) }); persist(); paint(); } }); return; }
      if (tool === "alert") { setTool("cursor"); createAlert(inv(m.x, m.y).p); return; }
      cur = tool === "brush" ? { k: "brush", pts: [p] } : { k: tool, a: p, b: p }; paint();
    });
    cv.addEventListener("mousemove", (e) => {
      const m = pos(e);
      if (drag) {
        const p = inv(m.x, m.y); if (!p) return; const dr = drawings[drag.i], o = drag.orig; drag.moved = true;
        if (drag.h) { const q2 = snapPt(m, p, e); dr[drag.h] = { t: q2.t, p: q2.p }; }
        else { const dt = p.t - drag.p0.t, dp = p.p - drag.p0.p; ["a", "b", "c"].forEach((k) => { if (o[k]) dr[k] = { t: dr.k === "hline" || dr.k === "hray" ? o[k].t : o[k].t + dt, p: dr.k === "vline" ? o[k].p : o[k].p + dp }; }); if (o.pts) dr.pts = o.pts.map((q2) => ({ t: q2.t + dt, p: q2.p + dp })); }
        paint(); return;
      }
      if (tool === "cursor") { setHover(m); return; }
      if (!cur) return; const p = snapPt(m, inv(m.x, m.y), e); if (!p) return; if (cur.stage === 2) cur.c = p; else if (cur.k === "brush") cur.pts.push(p); else cur.b = p; paint();
    });
    window.addEventListener("mouseup", () => {
      if (drag) { if (drag.moved) persist(); drag = null; paint(); return; }
      if (!cur || cur.stage === 2 || cur.armed) return;
      if (CHANNELISH.includes(cur.k)) { cur.stage = 2; cur.c = cur.b; paint(); return; }
      const ok = cur.k === "brush" ? cur.pts.length > 2 : (cur.a.p !== cur.b.p || cur.a.t !== cur.b.t);
      if (ok && cur.k === "match") { matchBox = cur; cur = null; setTool("cursor"); runMatch(); return; }
      if (cur.k === "gex3d") { const lo = Math.min(cur.a.p, cur.b.p), hi = Math.max(cur.a.p, cur.b.p); cur = null; setTool("cursor"); paint(); if (hi > lo) openGex3d(lo, hi); return; }
      if (ok) { drawings.push(cur); persist(); }
      cur = null; finishTool();
    });
    body.querySelectorAll(".gp-tool[data-t]").forEach((b) => b.addEventListener("click", () => { cur = null; setTool(b.dataset.t); }));
    $("#gp-undo").addEventListener("click", () => { drawings.pop(); persist(); paint(); });
    $("#gp-clear").addEventListener("click", async () => { if (drawings.length && (await ask("Remove all drawings on this chart?", { input: false, ok: "REMOVE" }))) { drawings = []; sel = -1; persist(); paint(); } });
    document.addEventListener("keydown", function keys_(e) {
      if (!alive) return document.removeEventListener("keydown", keys_);
      if (e.key === "Escape") { cur = null; setTool("cursor"); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel >= 0 && !["INPUT", "TEXTAREA"].includes((document.activeElement || {}).tagName)) { e.preventDefault(); drawings.splice(sel, 1); sel = -1; persist(); paint(); }
    });

    // price alerts: dashed price lines on the chart, stored server-side and delivered to Telegram by the alert engine
    let alerts = [], plines = [], tgOn = null;
    const applyAlertLines = () => {
      plines.forEach((l) => { try { series.removePriceLine(l); } catch (e) { /* series replaced */ } }); plines = [];
      if (series) alerts.forEach((a) => plines.push(series.createPriceLine({ price: a.value, color: "#ffd166", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `🔔 ${a.op}` })));
    };
    const drawAlertBar = () => {
      const el = $("#gp-al"); if (!el) return;
      el.innerHTML = `<b class="gp-al-h">🔔 PRICE ALERTS</b>${alerts.map((a) => `<span class="gp-al-c" title="${esc(a.note || "")}">${esc(a.op)} ${px(a.value)}<i data-del="${a.id}">✕</i></span>`).join("") || '<span class="dim">none on this security</span>'}
        <input class="ctl-in" id="gp-alp" placeholder="price" style="min-width:70px;max-width:84px"/><button class="bb-chip" id="gp-alb">+ ALERT</button>
        <span class="gp-al-t ${tgOn === false ? "warn" : ""}">${tgOn === null ? "" : tgOn ? "delivered to Telegram" : "Telegram is not set up on the server (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID): alerts will only show in this app"}</span><span class="sp"></span><span class="gp-hint" id="gp-hint">${esc(HINTS[tool] || "")}</span>`;
      el.querySelectorAll("[data-del]").forEach((x) => x.addEventListener("click", async () => { await api(`/alerts/${x.dataset.del}`, { method: "DELETE" }); loadAlerts(); }));
      $("#gp-alb").addEventListener("click", () => { const v = Number($("#gp-alp").value); if (v > 0) createAlert(v); });
      $("#gp-alp").addEventListener("keydown", (e) => { if (e.key === "Enter") { const v = Number(e.target.value); if (v > 0) createAlert(v); } });
    };
    const loadAlerts = async () => {
      try { const r = await api("/alerts"); tgOn = !!r.telegram; alerts = r.alerts.filter((a) => a.kind === "price" && a.active && String(a.symbol || "").toUpperCase() === s.symbol.toUpperCase()); } catch (e) { alerts = []; }
      applyAlertLines(); drawAlertBar();
    };
    const createAlert = async (price) => {
      if (!data.length) return;
      const value = Number(Number(price).toPrecision(6)), last = data[data.length - 1].close, op = value > last ? "above" : "below";
      try { await api("/alerts", { method: "POST", body: JSON.stringify({ kind: "price", symbol: s.symbol, op, value, note: "Set from the chart", once: true }) }); showToast(`Alert set: ${s.symbol} ${op} ${px(value)}${tgOn === false ? " (Telegram not configured: in-app only)" : " → Telegram"}`); await loadAlerts(); } catch (e) { showToast(e.message); }
    };

    // snapshots: the chart image (main + study panes + your drawings) saved against this security
    const snapsEl = $("#gp-snaps");
    const loadSnaps = async () => {
      try { const r = await api(`/snaps?symbol=${q(s.symbol)}`); drawSnaps(r.snaps); } catch (e) { snapsEl.innerHTML = NX.err(e); }
    };
    const drawSnaps = (list) => {
      $("#gp-snaps-n").textContent = `${list.length} saved for ${esc(s.symbol)}`;
      const now = data.length ? data[data.length - 1].close : null;
      snapsEl.innerHTML = list.length ? list.map((x) => `<div class="gp-snap" data-id="${x.id}"><img src="${x.url}" loading="lazy" data-a="open"/><div class="gp-snap-m"><b>${new Date(x.ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" })}</b><span>${esc(x.timeframe || "")}${x.price ? ` · at ${px(x.price)}${now ? ` (now <b class="${dir(now - x.price)}">${pct((now / x.price - 1) * 100)}</b>)` : ""}` : ""}</span>
        <div class="gp-snap-n">${x.note ? esc(x.note) : '<i class="dim">no note</i>'}</div><div class="gp-snap-b"><button data-a="open">OPEN</button><button data-a="restore" ${x.drawings.length ? "" : "disabled"}>RESTORE DRAWINGS (${x.drawings.length})</button><button data-a="note">NOTE</button><button data-a="del">DELETE</button></div></div></div>`).join("")
        : '<div class="bb-empty"><b>NO SNAPSHOTS YET</b>Draw on the chart, then press 📷 SAVE SNAPSHOT. The screenshot and your drawings are kept with this security.</div>';
      snapsEl.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", async () => {
        const id = Number(b.closest(".gp-snap").dataset.id), sn = list.find((x) => x.id === id), a = b.dataset.a;
        if (a === "open") { const d = document.createElement("div"); d.className = "nx-help"; d.innerHTML = `<div class="gp-full"><img src="${sn.url}"/></div>`; d.addEventListener("click", () => d.remove()); document.body.appendChild(d); }
        else if (a === "restore") { if (!drawings.length || (await ask("Replace the drawings on the chart with this snapshot's drawings?", { input: false, ok: "REPLACE" }))) { drawings = JSON.parse(JSON.stringify(sn.drawings)); persist(); paint(); showToast("Drawings restored"); } }
        else if (a === "note") { const t = await ask("Note for this snapshot", { value: sn.note || "" }); if (t !== null) { await api(`/snaps/${id}`, { method: "PUT", body: JSON.stringify({ note: t }) }); loadSnaps(); } }
        else if (a === "del") { if (await ask("Delete this snapshot?", { input: false, ok: "DELETE" })) { await api(`/snaps/${id}`, { method: "DELETE" }); loadSnaps(); } }
      }));
    };
    const capture = async () => {
      if (!charts.length || !data.length) return;
      const note = await ask("Save this chart as a snapshot", { placeholder: "Note (optional): what do you see?" });
      if (note === null) return;
      cur = null; sel = -1; overlay();
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 60)));
      try {
        const shots = charts.map((c) => c.takeScreenshot()), main = shots[0], k = main.width / mainEl.clientWidth, hdr = Math.round(36 * k);
        const out = document.createElement("canvas"); out.width = main.width; out.height = hdr + shots.reduce((t, c) => t + c.height, 0);
        const g = out.getContext("2d"), last = data[data.length - 1];
        g.fillStyle = "#050505"; g.fillRect(0, 0, out.width, out.height); g.font = `700 ${12 * k}px 'JetBrains Mono', Consolas, monospace`; g.fillStyle = "#ffa028";
        g.fillText(`${s.bbg || s.symbol}  ·  ${tfLabel()}  ·  ${new Date().toLocaleString("en-GB")}`, 10 * k, 15 * k);
        g.font = `${11 * k}px 'JetBrains Mono', Consolas, monospace`; g.fillStyle = "#f1f1f1"; g.fillText(`Last ${px(last.close)}${note ? "   —   " + String(note).slice(0, 120) : ""}`, 10 * k, 30 * k);
        let y = hdr; g.drawImage(main, 0, y); g.drawImage(cv, 0, y, main.width, main.height); y += main.height;
        shots.slice(1).forEach((c) => { g.drawImage(c, 0, y); y += c.height; });
        await api("/snaps", { method: "POST", body: JSON.stringify({ symbol: s.symbol, data_url: out.toDataURL("image/png"), timeframe: tfLabel(), price: last.close, note: note || "", drawings }) });
        showToast(`Snapshot saved for ${s.symbol}`); loadSnaps();
      } catch (e) { showToast(`Snapshot failed: ${e.message}`); }
    };

    // pattern match: drag a box around some candles; the same shape is searched for in the security's earlier history, with what happened next
    const matchEl = $("#gp-match");
    let matchBox = null, matchHi = null, lastMatch = null;
    const shapeNorm = (O, H, Lo, C, s, L) => { // one window as open/high/low/close scaled to 0..1 by the window's own range
      let lo = Infinity, hi = -Infinity; for (let i = s; i < s + L; i++) { if (Lo[i] < lo) lo = Lo[i]; if (H[i] > hi) hi = H[i]; }
      const rg = hi - lo || 1, v = new Float64Array(L * 4); for (let i = 0; i < L; i++) { const k = s + i; v[i * 4] = (O[k] - lo) / rg; v[i * 4 + 1] = (H[k] - lo) / rg; v[i * 4 + 2] = (Lo[k] - lo) / rg; v[i * 4 + 3] = (C[k] - lo) / rg; }
      return { v, lo, rg };
    };
    const fmtWhen = (t) => new Date(t * 1000).toLocaleString("en-GB", isIntraday() ? { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "short", year: "numeric" });
    const clearMatch = () => { matchBox = null; matchHi = null; lastMatch = null; matchEl.innerHTML = ""; matchEl.classList.add("hidden"); paint(); };
    // "gamma 3D" box tool: a popup with the gamma surface of the strikes inside the price range that was boxed
    const openGex3d = (lo, hi) => {
      const t = String(s.type || "").toUpperCase();
      document.getElementById("gx3-modal")?.remove();
      const m = document.createElement("div");
      m.id = "gx3-modal";
      m.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px)";
      m.innerHTML = `<div style="width:min(1100px,94vw);height:min(760px,90vh);background:#0b111c;border:1px solid #2a2f3a;border-radius:10px;display:flex;flex-direction:column;box-shadow:0 30px 80px rgba(0,0,0,.6)">
        <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #1f2633;font:600 13px JetBrains Mono,Consolas,monospace;color:#ffa028">
          <span>γ GAMMA 3D · ${esc(s.symbol)} · STRIKES ${lo.toFixed(2)} – ${hi.toFixed(2)}</span><span id="gx3-lv" style="color:#8b93a3;font-weight:400"></span><span style="flex:1"></span>
          <span style="color:#8b93a3;font-weight:400">drag to rotate · scroll to zoom · Esc to close</span><button id="gx3-x" style="background:#1b2230;color:#fff;border:1px solid #2a2f3a;border-radius:5px;padding:3px 10px;cursor:pointer">✕</button></div>
        <div id="gx3-body" style="flex:1;min-height:0"><div class="bb-empty"><b>LOADING…</b>Reading every expiry up to 120 days for these strikes.</div></div></div>`;
      document.body.appendChild(m);
      const close = () => { m.remove(); document.removeEventListener("keydown", onKey); };
      const onKey = (e) => { if (e.key === "Escape") close(); };
      document.addEventListener("keydown", onKey);
      m.addEventListener("mousedown", (e) => { if (e.target === m) close(); });
      m.querySelector("#gx3-x").onclick = close;
      const body = m.querySelector("#gx3-body");
      if (!["EQUITY", "ETF"].includes(t) || !window.NXGEX3D) { body.innerHTML = `<div class="bb-empty"><b>NO OPTIONS DATA</b>Gamma levels need a stock or ETF with listed options.</div>`; return; }
      window.NXGEX3D(body, s.symbol, { lo, hi });
      ctx.getData(`/invest/gamma?symbol=${encodeURIComponent(s.symbol)}`).then((g) => {
        if (!g || !g.ok) return;
        const inR = [["call wall", g.call_wall, "#2be07e"], ["zero gamma", g.zero_gamma, "#ffa028"], ["put wall", g.put_wall, "#ff4a4a"]].filter((x) => x[1] && x[1] >= lo && x[1] <= hi);
        const el = m.querySelector("#gx3-lv");
        if (el) el.innerHTML = (inR.length ? "in range: " + inR.map((x) => `<span style="color:${x[2]}">${x[0]} ${x[1]}</span>`).join(" · ") : "no key level inside this range") + ` · net gamma ${g.regime.toLowerCase()}`;
      }).catch(() => {});
    };
    const runMatch = async () => {
      const b = matchBox; if (!b) return;
      const lo = Math.min(toIdx(b.a.t), toIdx(b.b.t)), hi = Math.max(toIdx(b.a.t), toIdx(b.b.t)), i0 = Math.max(0, Math.ceil(lo - 1e-9)), i1 = Math.min(data.length - 1, Math.floor(hi + 1e-9)), L = i1 - i0 + 1;
      matchEl.classList.remove("hidden");
      if (L < 5) { matchEl.innerHTML = '<div class="bb-empty"><b>SELECT AT LEAST 5 CANDLES</b>Drag a wider box around the candles you want to match.</div>'; return; }
      if (L > 300) { matchEl.innerHTML = '<div class="bb-empty"><b>SELECTION TOO LONG</b>Pick 300 candles or fewer.</div>'; return; }
      matchEl.innerHTML = '<div class="bb-empty bb-load">SEARCHING HISTORY FOR THE SAME SHAPE</div>';
      let d;
      try { const iv = effInterval === "2h" || effInterval === "4h" ? "60m" : effInterval; d = await getData(`/invest/chart?symbol=${q(s.symbol)}&range=MAX&interval=${iv}`); } catch (e) { matchEl.innerHTML = NX.err(e); return; }
      if (!alive) return;
      const agg = st.interval === "2h" ? 2 : st.interval === "4h" ? 4 : 0;
      let cs = agg ? aggregate(d.candles, agg, isCrypto) : d.candles; const seenT = new Set(); cs = cs.filter((c) => (seenT.has(c[0]) ? false : (seenT.add(c[0]), true)));
      const N = cs.length, T = cs.map((c) => c[0]), O = cs.map((c) => c[1]), H = cs.map((c) => c[2]), Lo = cs.map((c) => c[3]), C = cs.map((c) => c[4]);
      if (N < L * 2) { matchEl.innerHTML = '<div class="bb-empty"><b>NOT ENOUGH HISTORY</b>Yahoo serves too few bars at this timeframe to search. Try a daily or weekly timeframe.</div>'; return; }
      const sel = { O: data.slice(i0, i1 + 1).map((x) => x.open), H: data.slice(i0, i1 + 1).map((x) => x.high), Lo: data.slice(i0, i1 + 1).map((x) => x.low), C: data.slice(i0, i1 + 1).map((x) => x.close) };
      const ref = shapeNorm(sel.O, sel.H, sel.Lo, sel.C, 0, L), tA = data[i0].t, tB = data[i1].t, found = [];
      for (let sidx = 0; sidx + L <= N; sidx++) {
        if (T[sidx] <= tB && T[sidx + L - 1] >= tA) continue; // skip the box itself
        const w = shapeNorm(O, H, Lo, C, sidx, L); let e = 0; for (let k = 0; k < w.v.length; k++) { const df = w.v[k] - ref.v[k]; e += df * df; }
        found.push({ s: sidx, rmse: Math.sqrt(e / w.v.length), w });
      }
      found.sort((x, y) => x.rmse - y.rmse);
      const top = []; for (const f of found) { if (top.every((t) => Math.abs(t.s - f.s) >= L)) top.push(f); if (top.length >= 6) break; }
      const fwd = (f) => { const end = f.s + L - 1, n = Math.min(L, N - 1 - end); return n >= 1 ? { n, ret: (C[end + n] / C[end] - 1) * 100 } : null; };
      top.forEach((f) => { f.sim = Math.max(0, Math.round(100 * (1 - f.rmse / 0.3))); f.fw = fwd(f); });
      lastMatch = { top, T, O, H, Lo, C, L, N, i0, i1, tA, tB, sel };
      const rets = top.filter((f) => f.fw).map((f) => f.fw.ret), avg = rets.length ? rets.reduce((a, c) => a + c, 0) / rets.length : null, up = rets.filter((r) => r > 0).length;
      const card = (f, i) => `<div class="gp-mt" data-i="${i}"><canvas width="240" height="96"></canvas><div class="gp-mt-m"><b>#${i + 1} · ${f.sim}% similar</b><span>${fmtWhen(T[f.s])} → ${fmtWhen(T[f.s + L - 1])}</span>${f.fw ? `<span>next ${f.fw.n} bars: <b class="${dir(f.fw.ret)}">${pct(f.fw.ret)}</b></span>` : '<span class="dim">no bars after it yet</span>'}</div></div>`;
      matchEl.innerHTML = `<div class="bb-ph"><span class="bb-n">M</span>PATTERN MATCH · ${L} candles (${fmtWhen(tA)} → ${fmtWhen(tB)})<span class="sp"></span><span class="meta">searched ${N.toLocaleString()} ${effInterval} bars from ${fmtWhen(T[0])} to ${fmtWhen(T[N - 1])}</span><button class="bb-chip on" id="gp-mshot" title="Download one image: today's setup next to the earlier matches and what happened after them">📷 SAVE IMAGE</button><button class="bb-chip" id="gp-mclear">CLEAR</button></div>
        <div class="gp-mt-sum">${top.length ? `Closest ${top.length} earlier matches. After them, price rose in <b>${up} of ${rets.length}</b>${avg !== null ? ` and moved <b class="${dir(avg)}">${pct(avg)}</b> on average over the next ${L} bars` : ""}. Orange = your selection, cyan = the match, dashed = what followed.` : "No comparable windows found."}</div>
        <div class="gp-mt-g">${top.map(card).join("")}</div>
        <div class="gp-mt-n">Shape similarity only (candle open/high/low/close scaled to each window's own range). It describes past behaviour; it does not predict the next move, and a few matches are a small sample.</div>`;
      $("#gp-mclear").addEventListener("click", clearMatch); $("#gp-mshot").addEventListener("click", matchImage);
      matchEl.querySelectorAll(".gp-mt").forEach((el) => {
        const f = top[Number(el.dataset.i)], cv2 = el.querySelector("canvas"), g = cv2.getContext("2d"), W = cv2.width, Hh = cv2.height, end = f.s + L - 1, n = f.fw ? f.fw.n : 0;
        const selC = Array.from({ length: L }, (_, k) => ref.v[k * 4 + 3]), mC = Array.from({ length: L }, (_, k) => f.w.v[k * 4 + 3]), fC = Array.from({ length: n }, (_, k) => (C[end + 1 + k] - f.w.lo) / f.w.rg);
        const all = [...selC, ...mC, ...fC], mn = Math.min(...all), mx = Math.max(...all), span = L + n, X = (k) => 4 + (k / Math.max(1, span - 1)) * (W - 8), Y = (v) => Hh - 6 - ((v - mn) / (mx - mn || 1)) * (Hh - 12);
        g.fillStyle = "#050505"; g.fillRect(0, 0, W, Hh);
        const line = (arr, off, col, dash) => { g.strokeStyle = col; g.setLineDash(dash || []); g.lineWidth = 1.6; g.beginPath(); arr.forEach((v, k) => (k ? g.lineTo(X(off + k), Y(v)) : g.moveTo(X(off + k), Y(v)))); g.stroke(); };
        line(selC, 0, "#ffa028"); line(mC, 0, "#5aa9ff"); if (n) line([mC[L - 1], ...fC], L - 1, "#5aa9ff", [3, 3]);
        g.setLineDash([2, 3]); g.strokeStyle = "#333"; g.beginPath(); g.moveTo(X(L - 1), 0); g.lineTo(X(L - 1), Hh); g.stroke();
        el.style.cursor = "pointer"; el.title = "Click to mark this match on the main chart";
        el.addEventListener("click", async () => {
          const i = Number(el.dataset.i); matchEl.querySelectorAll(".gp-mt").forEach((e2) => e2.classList.toggle("on", e2 === el));
          matchHi = { t0: T[f.s], t1: T[end], label: `MATCH #${i + 1} · ${f.sim}%`, f0: n ? T[end + 1] : null, f1: n ? T[end + n] : null };
          if (T[f.s] < data[0].t) { // older than what is on screen: widen to the longest history at this timeframe, then jump there
            st.interval = effInterval; st.range = "MAX"; sync(); save(); showToast("Loading longer history to show this match"); await build();
          }
          const j = data.findIndex((x) => x.t >= T[f.s]);
          if (j >= 0 && charts[0]) charts[0].timeScale().setVisibleLogicalRange({ from: Math.max(0, j - 15), to: Math.min(data.length + 6, j + L + n + 15) });
          paint(); wrap.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
      });
      paint();
    };

    // one shareable image: today's setup beside the earlier matches and what happened after each, signed JORDAN TERMINAL
    const matchImage = async () => {
      const M = lastMatch; if (!M) return;
      const { top, T, O, H, Lo, C, L, tA, tB, sel } = M, W = 1500, PAD = 24, cols = 3, cw = (W - PAD * 2 - (cols - 1) * 16) / cols, ch = 250, rows = Math.ceil(top.length / cols);
      const topH = 330, hdr = 62, ftr = 52, Ht = hdr + topH + 30 + rows * (ch + 16) + 30 + ftr, k = 2;
      const cvs = document.createElement("canvas"); cvs.width = W * k; cvs.height = Ht * k; const g = cvs.getContext("2d"); g.scale(k, k);
      const F = (px_, w_ = 400) => `${w_} ${px_}px 'JetBrains Mono', Consolas, monospace`, GR = "#2be07e", RD = "#ff4a4a", OR = "#ffa028", BL = "#5aa9ff";
      g.fillStyle = "#050505"; g.fillRect(0, 0, W, Ht);
      g.font = F(20, 700); g.fillStyle = OR; g.fillText(`${s.bbg || s.symbol}  ·  PATTERN MATCH  ·  ${effInterval} bars`, PAD, 32);
      g.font = F(13); g.fillStyle = "#cfcfcf"; g.fillText(`Setup today: ${fmtWhen(tA)} → ${fmtWhen(tB)} (${L} candles)   ·   ${new Date().toLocaleString("en-GB")}`, PAD, 54);
      // candles for one window; arrays are price series, `from`/`count` pick the part, lo/rg is the scaling shared by the whole panel
      const candles = (g_, o, h, l, c, from, count, x0, y0, w_, h_, lo, rg, slots, dim) => {
        const sw = w_ / slots, bw = Math.max(2, sw * 0.62), Y = (v) => y0 + h_ - ((v - lo) / rg) * h_;
        for (let i = 0; i < count; i++) { const kk = from + i, up = c[kk] >= o[kk], col = up ? GR : RD, cx = x0 + (i + 0.5) * sw; g_.globalAlpha = dim ? 0.7 : 1; g_.strokeStyle = col; g_.fillStyle = col; g_.lineWidth = 1; g_.beginPath(); g_.moveTo(cx, Y(h[kk])); g_.lineTo(cx, Y(l[kk])); g_.stroke(); const yo = Y(o[kk]), yc = Y(c[kk]); g_.fillRect(cx - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo))); }
        g_.globalAlpha = 1;
      };
      const panel = (x, y, w_, h_, title, sub, border) => { g.fillStyle = "#0b0b0b"; g.fillRect(x, y, w_, h_); g.strokeStyle = border; g.lineWidth = 1.4; g.strokeRect(x, y, w_, h_); g.font = F(13, 700); g.fillStyle = border; g.fillText(title, x + 10, y + 20); g.font = F(11); g.fillStyle = "#9a9a9a"; g.fillText(sub, x + 10, y + 36); };
      // today
      const ty = hdr; panel(PAD, ty, W - PAD * 2, topH, "TODAY'S SETUP", `${fmtWhen(tA)} → ${fmtWhen(tB)}`, OR);
      let slo = Infinity, shi = -Infinity; sel.Lo.forEach((v) => { if (v < slo) slo = v; }); sel.H.forEach((v) => { if (v > shi) shi = v; });
      const srg = (shi - slo) * 1.08 || 1, sx0 = PAD + 70, sw_ = W - PAD * 2 - 90;
      candles(g, sel.O, sel.H, sel.Lo, sel.C, 0, L, sx0, ty + 48, sw_ * 0.62, topH - 70, slo - (shi - slo) * 0.04, srg, L, false);
      g.font = F(11); g.fillStyle = "#8b8b8b"; g.fillText(px(shi), PAD + 10, ty + 62); g.fillText(px(slo), PAD + 10, ty + topH - 22);
      const rets = top.filter((f) => f.fw).map((f) => f.fw.ret), upN = rets.filter((r) => r > 0).length, avg = rets.length ? rets.reduce((a, c) => a + c, 0) / rets.length : null;
      const bx = sx0 + sw_ * 0.66; g.font = F(15, 700); g.fillStyle = "#f1f1f1"; g.fillText("WHAT HAPPENED AFTER THE SAME SETUP", bx, ty + 66);
      g.font = F(13); let yy = ty + 96; const line = (t, c = "#cfcfcf") => { g.fillStyle = c; g.fillText(t, bx, yy); yy += 24; };
      line(`${top.length} closest earlier matches found`); if (avg !== null) { line(`Price rose after ${upN} of ${rets.length}`, upN * 2 >= rets.length ? GR : RD); line(`Average move over the next ${L} bars: ${pct(avg)}`, avg >= 0 ? GR : RD); line(`Best ${pct(Math.max(...rets))}   ·   Worst ${pct(Math.min(...rets))}`); }
      yy += 8; g.font = F(11); g.fillStyle = "#8b8b8b"; ["Candles are scaled to each window's own range,", "so the shape is compared, not the price level.", "Past behaviour only: not a prediction."].forEach((t) => { g.fillText(t, bx, yy); yy += 17; });
      // matches
      const gy = ty + topH + 30; g.font = F(14, 700); g.fillStyle = BL; g.fillText("EARLIER MATCHES  (solid = the matching candles, faded = what followed)", PAD, gy - 8);
      top.forEach((f, i) => {
        const col = i % cols, row = Math.floor(i / cols), x = PAD + col * (cw + 16), y = gy + row * (ch + 16), end = f.s + L - 1, n = f.fw ? f.fw.n : 0, span = L + n;
        panel(x, y, cw, ch, `#${i + 1}  ·  ${f.sim}% similar`, `${fmtWhen(T[f.s])} → ${fmtWhen(T[end])}`, BL);
        let lo = f.w.lo, hi = f.w.lo + f.w.rg; for (let kk = end + 1; kk <= end + n; kk++) { if (Lo[kk] < lo) lo = Lo[kk]; if (H[kk] > hi) hi = H[kk]; }
        const rg = (hi - lo) * 1.08 || 1, lo2 = lo - (hi - lo) * 0.04, cx0 = x + 10, cw2 = cw - 20, ch2 = ch - 84, cy0 = y + 46;
        candles(g, O, H, Lo, C, f.s, L, cx0, cy0, cw2, ch2, lo2, rg, span, false);
        if (n) { candles(g, O, H, Lo, C, end + 1, n, cx0 + (L / span) * cw2, cy0, (n / span) * cw2, ch2, lo2, rg, n, true); g.setLineDash([3, 3]); g.strokeStyle = "#444"; g.beginPath(); g.moveTo(cx0 + (L / span) * cw2, cy0); g.lineTo(cx0 + (L / span) * cw2, cy0 + ch2); g.stroke(); g.setLineDash([]); }
        g.font = F(12, 700); g.fillStyle = f.fw ? (f.fw.ret >= 0 ? GR : RD) : "#8b8b8b"; g.fillText(f.fw ? `Next ${f.fw.n} bars: ${pct(f.fw.ret)}   (${fmtWhen(T[end + 1])} → ${fmtWhen(T[end + n])})` : "No bars after this match yet", x + 10, y + ch - 12);
      });
      // footer
      g.fillStyle = "#101010"; g.fillRect(0, Ht - ftr, W, ftr); g.strokeStyle = OR; g.lineWidth = 1; g.beginPath(); g.moveTo(0, Ht - ftr); g.lineTo(W, Ht - ftr); g.stroke();
      g.font = F(22, 700); g.fillStyle = OR; g.textAlign = "center"; g.fillText("JORDAN TERMINAL", W / 2, Ht - ftr / 2 + 8); g.textAlign = "left";
      g.font = F(10); g.fillStyle = "#6f6f6f"; g.fillText("screening data, not investment advice", PAD, Ht - 18);
      const url = cvs.toDataURL("image/png"), a = document.createElement("a"); a.href = url; a.download = `${s.symbol}_pattern_match_${new Date().toISOString().slice(0, 10)}.png`; document.body.appendChild(a); a.click(); a.remove();
      try { await api("/snaps", { method: "POST", body: JSON.stringify({ symbol: s.symbol, data_url: url, timeframe: tfLabel(), price: data[data.length - 1].close, note: `Pattern match: ${L} candles, ${top.length} earlier matches`, drawings: [] }) }); loadSnaps(); showToast("Image downloaded and saved to this security's snapshots"); }
      catch (e) { showToast("Image downloaded (could not save to snapshots)"); }
    };

    // left-click menu: click the chart (without dragging) for actions at that price and time
    const menuEl = document.createElement("div"); menuEl.className = "gp-menu hidden"; wrap.appendChild(menuEl);
    const closeMenu = () => menuEl.classList.add("hidden");
    document.addEventListener("mousedown", function md_(e) { if (!alive) return document.removeEventListener("mousedown", md_); if (!menuEl.contains(e.target)) closeMenu(); });
    document.addEventListener("keydown", function mk_(e) { if (!alive) return document.removeEventListener("keydown", mk_); if (e.key === "Escape") closeMenu(); });
    const arm = (k, p) => { closeMenu(); setTool(k); cur = { k, a: p, b: p, armed: true }; paint(); showToast("Move the mouse and click where it should end (Esc cancels)"); };
    const openMenu = (m, p) => {
      const last = data[data.length - 1].close, when = new Date(p.t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
      const di = (() => { for (let i = drawings.length - 1; i >= 0; i--) if (hit(drawings[i], m)) return i; return -1; })();
      const ai = alerts.findIndex((a) => { const y = series.priceToCoordinate(a.value); return y !== null && Math.abs(m.y - y) < 7; });
      const op = p.p > last ? "above" : "below", groups = [];
      if (ai >= 0) groups.push(["THIS ALERT", [["🗑", `Remove alert (${alerts[ai].op} ${px(alerts[ai].value)})`, async () => { await api(`/alerts/${alerts[ai].id}`, { method: "DELETE" }); showToast("Alert removed"); loadAlerts(); }]]]);
      if (di >= 0) {
        const dr = drawings[di], items = [["🗑", `Delete this ${dr.k === "hline" ? "horizontal line" : dr.k === "pt" ? (dr.label === "H" ? "high mark" : "low mark") : dr.k}`, () => { drawings.splice(di, 1); persist(); paint(); }]];
        if (dr.k === "text") items.unshift(["✎", "Edit text", async () => { const t = await ask("Edit note", { value: dr.text }); if (t) { dr.text = String(t).slice(0, 80); persist(); paint(); } }]);
        items.push(["⧉", "Duplicate", () => { const c = JSON.parse(JSON.stringify(dr)), dp = (dr.a.p * 0.002); ["a", "b", "c"].forEach((k2) => c[k2] && (c[k2].p += dp)); drawings.push(c); persist(); paint(); }]);
        groups.push(["THIS DRAWING", items]);
      }
      groups.push(["ALERT", [["🔔", `Alert when price goes ${op} ${px(p.p)}`, () => createAlert(p.p)]]]);
      groups.push(["MARK", [["─", `Horizontal line at ${px(p.p)}`, () => { drawings.push({ k: "hline", a: p }); persist(); paint(); }], ["│", "Vertical line here", () => { drawings.push({ k: "vline", a: p }); persist(); paint(); }], ["T", "Text note here…", async () => { const t = await ask("Text note on the chart", { placeholder: "e.g. breakout retest" }); if (t) { drawings.push({ k: "text", a: p, text: String(t).slice(0, 80) }); persist(); paint(); } }]]]);
      groups.push(["DRAW FROM HERE (click the end point)", [["╱", "Trendline", () => arm("trend", p)], ["➚", "Ray", () => arm("ray", p)], ["➜", "Arrow", () => arm("arrow", p)], ["⫽", "Parallel channel", () => arm("channel", p)], ["▭", "Rectangle", () => arm("rect", p)], ["☰", "Fibonacci", () => arm("fib", p)], ["↔", "Measure", () => arm("measure", p)], ["▲", "Long position (entry here)", () => arm("long", p)], ["▼", "Short position (entry here)", () => arm("short", p)]]]);
      groups.push(["CHART", [["📷", "Save snapshot", () => capture()], ["⧉", `Copy price ${px(p.p)}`, async () => { try { await navigator.clipboard.writeText(String(Number(p.p.toPrecision(7)))); showToast("Price copied"); } catch (e) { showToast("Copy is blocked by the browser"); } }], ["⤢", "Fit all bars", () => charts[0] && charts[0].timeScale().fitContent()]]]);
      const fns = [];
      menuEl.innerHTML = `<div class="gp-menu-h"><b>${px(p.p)}</b><span class="${dir(p.p - last)}">${pct((p.p / last - 1) * 100)} vs last</span><span>${when}</span></div>${groups.map(([h, items]) => `<div class="gp-menu-g">${h}</div>${items.map(([ic, label, fn]) => { fns.push(fn); return `<button class="gp-menu-i" data-i="${fns.length - 1}"><i>${ic}</i>${esc(label)}</button>`; }).join("")}`).join("")}`;
      menuEl.querySelectorAll(".gp-menu-i").forEach((b) => b.addEventListener("click", () => { closeMenu(); fns[Number(b.dataset.i)](); }));
      menuEl.classList.remove("hidden");
      const W = wrap.clientWidth, H = wrap.clientHeight, mw = menuEl.offsetWidth, mh = menuEl.offsetHeight;
      menuEl.style.left = `${Math.max(4, Math.min(W - mw - 4, m.x + 8))}px`; menuEl.style.top = `${Math.max(4, Math.min(Math.max(4, H - mh - 4), m.y + 8))}px`;
    };
    wrap.addEventListener("contextmenu", (e) => { // right-click anywhere on the chart: the same menu, without stealing normal clicks
      if (!data.length || !series) return; e.preventDefault(); const m = pos(e), p = inv(m.x, m.y); if (p) openMenu(m, p);
    });
    const onChartClick = (param) => {
      if (sel >= 0) { sel = -1; paint(); }
      if (st.cmenu !== true || tool !== "cursor" || cur || !param.point || !series || !data.length) return;
      const p = inv(param.point.x, param.point.y); if (!p) return;
      openMenu(param.point, p);
    };

    // chart build
    const chartOpts = (h, intraday, showTime) => ({ width: mainEl.clientWidth, height: h, layout: { background: { type: "solid", color: "#050505" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: Math.round(11 * NZ()) },
      grid: { vertLines: { color: "#121212" }, horzLines: { color: "#121212" } }, localization: { locale: "en-US" },
      crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" }, horzLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" } },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false }, handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      rightPriceScale: { borderColor: "#262626", minimumWidth: 70 }, timeScale: { borderColor: "#262626", timeVisible: intraday, secondsVisible: false, visible: showTime } });

    async function build() {
      const my = ++build.n;
      const aiv = st.interval ? (API_INTERVAL[st.interval] || st.interval) : null, url = `/invest/chart?symbol=${q(s.symbol)}&range=${st.range}${aiv ? "&interval=" + aiv : ""}${st.pre && !isCrypto ? "&prepost=1" : ""}`;
      let d, cmpD = null;
      try { [d, cmpD] = await Promise.all([getData(url), st.cmp ? getData(`/invest/chart?symbol=${q(st.cmp)}&range=${st.range}${aiv ? "&interval=" + aiv : ""}`).catch(() => null) : null]); }
      catch (e) { if (alive && my === build.n) mainEl.innerHTML = NX.err(e); return; }
      if (!alive || my !== build.n) return;
      const agg = st.interval === "2h" ? 2 : st.interval === "4h" ? 4 : 0;
      if (agg) { d = { ...d, candles: aggregate(d.candles, agg, isCrypto) }; if (cmpD) cmpD = { ...cmpD, candles: aggregate(cmpD.candles, agg, isCrypto) }; }
      const intraday = isIntraday(), seen = new Set(); data = []; secs = [];
      for (const c of d.candles) { const time = intraday ? c[0] : new Date(c[0] * 1000).toISOString().slice(0, 10); if (seen.has(time)) continue; seen.add(time); data.push({ time, t: c[0], open: c[1], high: c[2], low: c[3], close: c[4], volume: c[5] }); secs.push(c[0]); }
      if (!data.length) { mainEl.innerHTML = NX.err("no bars returned"); return; }
      effInterval = st.interval || d.interval; stepN = -1; build.cfg = { url, agg, intraday };
      $("#gp-meta").textContent = `${esc(s.bbg || s.symbol)} · ${effInterval} bars · ${data.length} bars${!agg && d.used_range && d.used_range.toUpperCase() !== st.range && !["YTD"].includes(st.range) ? ` (Yahoo serves ${d.interval} bars only for ${d.used_range})` : ""} · ${NX.gpIndicatorCount} indicators`;
      charts.forEach((c) => { try { c.remove(); } catch (e) { /* gone */ } }); charts = []; mainEl.innerHTML = ""; subsEl.innerHTML = "";
      const subs = st.st.filter((k) => ST[k]), total = Math.max(380, Math.min(900, Math.round(window.innerHeight * 0.72))), subH = Math.round((window.innerHeight < 700 ? 90 : 112) * NZ()), mainH = Math.max(280, total - subs.length * subH);
      const chart = LW.createChart(mainEl, chartOpts(mainH, intraday, !subs.length)); charts.push(chart); chart.subscribeClick(onChartClick);
      const dim = (x) => { // pre-market / after-hours bars are drawn fainter than regular-session bars
        if (st.type === "fp" || !intraday || isCrypto) return x; const m = nyParts(x.t).min; if (m >= 570 && m < 960) return x;
        const up = x.close >= x.open, c = up ? "rgba(43,224,126,0.4)" : "rgba(255,74,74,0.4)"; return { ...x, color: c, borderColor: c, wickColor: c };
      };
      const shown = (st.type === "ha" ? heikin(data) : data).map(dim);
      build.pointAt = (i) => { const x = dim((st.type === "ha" ? heikin(data) : data)[i]); return ["line", "area"].includes(st.type) ? { time: x.time, value: x.close } : x; }; // one bar, shaped for the main series
      const cmpOn = !!(st.cmp && cmpD);
      if (st.type === "line") series = chart.addLineSeries({ color: "#ffa028", lineWidth: 2 });
      else if (st.type === "area") series = chart.addAreaSeries({ lineColor: "#ffa028", topColor: "rgba(255,160,40,0.35)", bottomColor: "rgba(255,160,40,0)", lineWidth: 2 });
      else if (st.type === "fp") series = chart.addCandlestickSeries({ upColor: "rgba(0,0,0,0)", downColor: "rgba(0,0,0,0)", borderVisible: false, wickUpColor: "rgba(0,0,0,0)", wickDownColor: "rgba(0,0,0,0)" }); // invisible: it only supplies the scales; the footprint is drawn on the canvas
      else if (st.type === "bar") series = chart.addBarSeries({ upColor: "#2be07e", downColor: "#ff4a4a" });
      else series = chart.addCandlestickSeries({ upColor: "#2be07e", downColor: "#ff4a4a", borderVisible: false, wickUpColor: "#2be07e", wickDownColor: "#ff4a4a" });
      series.setData(["line", "area"].includes(st.type) ? shown.map((x) => ({ time: x.time, value: x.close })) : shown);
      chart.priceScale("right").applyOptions({ mode: cmpOn ? 2 : st.log ? 1 : 0 });
      plines = []; applyAlertLines();
      if (["EQUITY", "ETF"].includes(String(s.type || "").toUpperCase())) {  // gamma levels from the option chain (decoration; never blocks the chart)
        const ser = series;
        ctx.getData(`/invest/gamma?symbol=${encodeURIComponent(s.symbol)}`).then((g) => {
          if (!alive || !g || !g.ok || ser !== series) return;
          const mk = (price, color, title) => price && ser.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
          mk(g.call_wall, "#2be07e", "CALL WALL"); mk(g.put_wall, "#ff4a4a", "PUT WALL"); mk(g.zero_gamma, "#ffa028", "ZERO GAMMA");
        }).catch(() => {});
      }
      if (!intraday && data.length > 20) {  // historical events (library; reaction measured from prices) as markers + a hover card
        const ser = series;
        ctx.getData(`/intel/history/symbol?symbol=${encodeURIComponent(s.symbol)}`).then((hx) => {
          if (!alive || !hx || ser !== series) return;
          const words = [s.symbol, String(s.name || "").split(/[ ,.]/)[0]].map((w) => String(w || "").replace(/[^A-Za-z0-9]/g, "")).filter((w) => w.length > 2);
          const nameRx = words.length ? new RegExp(String.raw`\b(` + words.join("|") + String.raw`)\b`, "i") : null;
          const barOf = (day) => { let lo = 0, hi = data.length - 1, best = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (data[m].time <= day) { best = m; lo = m + 1; } else hi = m - 1; } return best; };
          const byBar = new Map(), marks = [];
          for (const e of hx.events || []) {
            const own = !!nameRx && nameRx.test(e.title);
            if (!own && !(Math.abs(e.z0 || 0) >= 1.8)) continue;
            const i = barOf(e.day);
            if (i < 0 || (i === data.length - 1 && e.day > data[i].time && !intraday && effInterval === "1d")) continue;
            const t = data[i].time;
            if (!byBar.has(t)) { byBar.set(t, []); marks.push({ time: t, position: e.d0 >= 0 ? "aboveBar" : "belowBar", color: "#c58bff", shape: "square", text: e.label }); }
            byBar.get(t).push(e);
          }
          if (!marks.length) return;
          ser.setMarkers(marks.sort((a, b) => (a.time < b.time ? -1 : 1)));
          if (getComputedStyle(mainEl).position === "static") mainEl.style.position = "relative";
          const tip = document.createElement("div");
          tip.style.cssText = "position:absolute;left:10px;top:10px;z-index:6;max-width:360px;padding:8px 10px;background:rgba(10,8,20,0.95);border:1px solid #c58bff;border-radius:4px;font:11px JetBrains Mono,Consolas,monospace;color:#ddd;pointer-events:none;display:none";
          mainEl.appendChild(tip);
          const pcs = (v) => (v === null || v === undefined ? "—" : `<span style="color:${v >= 0 ? "#2be07e" : "#ff4a4a"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</span>`);
          chart.subscribeCrosshairMove((p) => {
            const evs = p && p.time ? byBar.get(p.time) : null;
            if (!evs) { tip.style.display = "none"; return; }
            tip.innerHTML = evs.slice(0, 3).map((e) => `<div style="color:#c58bff;font-weight:700">📜 ${esc(e.label)} · ${esc(e.date)}</div><div style="margin:2px 0 4px">${esc(e.title)}</div>` +
              `<div>${esc(s.symbol)}: day ${pcs(e.d0)} · week ${pcs(e.d5)}${isNum(e.z0) ? ` · ${Math.abs(e.z0).toFixed(1)}× its normal day` : ""} · S&amp;P ${pcs(e.spx)}</div>`).join('<hr style="border:0;border-top:1px solid #333;margin:6px 0">');
            tip.style.display = "block";
          });
        }).catch(() => {});
      }
      const tags = [];
      st.ov.filter((k) => OV[k] && !(OV[k].intraday && !intraday)).forEach((k) => {
        const def = OV[k];
        def.f(data, { crypto: isCrypto }).forEach((ln) => {
          const sh = ln.shift || 0, pts = [];
          ln.v.forEach((v, i) => { if (v === null) return; const j = i + sh; const time = j < data.length ? data[j].time : null; if (time !== null) pts.push({ time, value: v }); });
          if (!pts.length) return;
          const ls = def.dots ? chart.addLineSeries({ color: ln.c || def.c, lineWidth: 1, lineStyle: 0, pointMarkersVisible: true, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }) : chart.addLineSeries({ color: ln.c || def.c, lineWidth: 1, lineStyle: ln.dash ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          if (def.dots) ls.applyOptions({ lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.6 });
          ls.setData(pts);
        });
        tags.push(`<em style="color:${def.c}">${def.l}</em>`);
      });
      if (st.ov.some((k) => OV[k] && OV[k].intraday) && !intraday) tags.push('<em class="dim">VWAP / opening range / previous-day levels need an intraday timeframe</em>');
      if (cmpOn) {
        const cm = new Map(cmpD.candles.map((c) => [intraday ? c[0] : new Date(c[0] * 1000).toISOString().slice(0, 10), c[4]]));
        chart.addLineSeries({ color: "#2be07e", lineWidth: 2, priceLineVisible: false, title: st.cmp }).setData(data.filter((x) => cm.has(x.time)).map((x) => ({ time: x.time, value: cm.get(x.time) })));
        tags.push(`<em style="color:#2be07e">${esc(st.cmp)} (both shown as % change)</em>`);
      }
      // sub-panes: one small synced chart per study
      subs.forEach((k, idx) => {
        const def = ST[k], host = document.createElement("div"); host.className = "gp-sub"; host.innerHTML = `<span class="gp-sub-t">${def.l}</span>`; const holder = document.createElement("div"); host.appendChild(holder); subsEl.appendChild(host);
        const c2 = LW.createChart(holder, chartOpts(subH, intraday, idx === subs.length - 1)); charts.push(c2);
        let first = null;
        def.lines(data).forEach((ln) => {
          const pts = []; ln.v.forEach((v, i) => { if (v !== null && isNum(v)) pts.push(ln.hist ? { time: data[i].time, value: v, color: ln.vol ? (data[i].close >= data[i].open ? "rgba(43,224,126,0.55)" : "rgba(255,74,74,0.55)") : v >= 0 ? "rgba(43,224,126,0.6)" : "rgba(255,74,74,0.6)" } : { time: data[i].time, value: v }); });
          const sr = ln.hist ? c2.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }) : c2.addLineSeries({ color: ln.c, lineWidth: 1.5, priceLineVisible: false });
          sr.setData(pts); first = first || sr;
        });
        (def.levels || []).forEach((lv) => first && first.createPriceLine({ price: lv, color: "#3a3a3a", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
      });
      // link the time scales (guarded so the panes do not echo each other)
      let lock = false;
      charts.forEach((c, i) => c.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (lock || !r) return; lock = true; charts.forEach((o, j) => { if (j !== i) o.timeScale().setVisibleLogicalRange(r); }); lock = false; paint(); }));
      chart.timeScale().fitContent();
      if (data.length > 140 && !intraday) chart.timeScale().setVisibleLogicalRange({ from: data.length - 140, to: data.length + 6 });
      // legend + crosshair
      const last = data[data.length - 1];
      const setLegend = (c, prev) => { const ch = prev ? c.close - prev : c.close - c.open; legendEl.innerHTML = `<b>${esc(s.bbg || s.symbol)}</b> <span>O</span>${px(c.open)} <span>H</span>${px(c.high)} <span>L</span>${px(c.low)} <span>C</span>${px(c.close)} <b class="${dir(ch)}">${signed(ch)} (${pct((ch / (prev || c.open)) * 100)})</b> <span>VOL</span>${big(c.volume)} ${tags.join(" ")}`; };
      setLegend(last, data[data.length - 2] && data[data.length - 2].close);
      build.legend = () => setLegend(data[data.length - 1], data[data.length - 2] && data[data.length - 2].close);
      chart.subscribeCrosshairMove((p) => {
        const idx = p.time ? data.findIndex((x) => x.time === p.time) : -1;
        if (idx < 0) setLegend(last, data[data.length - 2] && data[data.length - 2].close); else setLegend(data[idx], idx ? data[idx - 1].close : null);
        charts.slice(1).forEach((c2) => { try { if (idx >= 0) c2.setCrosshairPosition(NaN, p.time, c2._first || undefined); else c2.clearCrosshairPosition(); } catch (e) { /* older build */ } });
        paint();
      });
      if (!build.loop) {
        build.loop = true; let sig = "";
        const tick = () => {
          if (!alive) return; requestAnimationFrame(tick);
          if (!charts[0] || !series || !data.length) return;
          const r = charts[0].timeScale().getVisibleLogicalRange(), a0 = series.priceToCoordinate(data[data.length - 1].close), a1 = series.priceToCoordinate(data[0].open);
          const k = `${r ? r.from.toFixed(3) + r.to.toFixed(3) : ""}|${a0 === null ? "" : a0.toFixed(1)}|${a1 === null ? "" : a1.toFixed(1)}|${mainEl.clientWidth}x${mainEl.clientHeight}`;
          if (k !== sig) { sig = k; overlay(); }
        };
        requestAnimationFrame(tick);
      }
      if (obs) obs.disconnect();
      obs = new ResizeObserver(() => { if (!alive) return; const w = mainEl.clientWidth; charts.forEach((c) => c.applyOptions({ width: w })); paint(); });
      obs.observe(wrap);
      setTool(tool);
      paint();
    }
    build.n = 0;

    // LIVE: every few seconds the newest bars are fetched again (not through the 4-minute cache). The forming candle, its volume and the price
    // update in place; when a NEW candle opens the chart is rebuilt once (indicators and studies recomputed) with the same zoom and position.
    let liveBusy = false, liveSince = 0;
    const liveRefresh = async () => {
      if (!alive || liveBusy || document.hidden || !series || !data.length || !build.cfg || tool !== "cursor" || cur || drag) return;
      liveBusy = true;
      try {
        const { url: u, agg: ag, intraday: ind } = build.cfg, my = build.n;
        let d = await api(u); if (!alive || my !== build.n) return;
        let cs = ag ? aggregate(d.candles, ag, isCrypto) : d.candles;
        const lastT = data[data.length - 1].t, fresh = cs.filter((c) => c[0] >= lastT);
        if (!fresh.length) return;
        if (fresh[fresh.length - 1][0] > lastT) { // a new candle: rebuild, keeping the view
          const r = charts[0].timeScale().getVisibleLogicalRange(), n0 = data.length, atEdge = r && r.to >= n0 - 2;
          await build(); if (!alive || !r || !charts[0]) return;
          const shift = atEdge ? data.length - n0 : 0; charts[0].timeScale().setVisibleLogicalRange({ from: r.from + shift, to: r.to + shift }); return;
        }
        const c = fresh[0], L = data[data.length - 1], same = L.close === c[4] && L.high === c[2] && L.low === c[3] && L.volume === c[5];
        if (same) return;
        Object.assign(L, { open: c[1], high: c[2], low: c[3], close: c[4], volume: c[5] });
        series.update(build.pointAt(data.length - 1));
        if (build.legend && !mainEl.matches(":hover")) build.legend();
        const hp = document.getElementById("sx-price"); if (hp) { hp.textContent = px(c[4]); } // the big price in the security header follows the chart
        liveSince = Date.now(); const meta = $("#gp-meta"); if (meta && !/LIVE/.test(meta.textContent)) meta.textContent += " · LIVE"; paint();
      } catch (e) { /* offline or rate-limited: keep the picture, try again */ } finally { liveBusy = false; }
    };
    const liveEvery = () => (isIntraday() ? 6000 : 30000);
    const liveLoop = () => { if (!alive) return; setTimeout(async () => { await liveRefresh(); liveLoop(); }, liveEvery()); };

    // controls
    const rebuild = () => { save(); build(); };
    NX.wireChips("gp-int", (v) => { st.interval = v || null; if (v) st.range = INTERVAL_RANGE[v]; sync(); rebuild(); });
    NX.wireChips("gp-range", (r) => { st.range = r; st.interval = null; sync(); rebuild(); });
    NX.wireChips("gp-type", (t) => { st.type = t; rebuild(); });
    const sync = () => { const set = (id, val) => document.querySelectorAll(`#${id} .bb-chip`).forEach((c) => c.classList.toggle("on", c.dataset.f === val)); set("gp-int", st.interval || ""); set("gp-range", st.range); };
    const dd = (id, list) => { const box = $(`#${id}`), pop = box.querySelector(".gp-dd-pop"); box.querySelector("button").addEventListener("click", (e) => { e.stopPropagation(); document.querySelectorAll(".gp-dd-pop").forEach((p) => p !== pop && p.classList.add("hidden")); pop.classList.toggle("hidden"); }); pop.addEventListener("change", () => { st[list] = [...pop.querySelectorAll("input:checked")].map((i) => i.dataset.k); rebuild(); }); };
    dd("gp-ov", "ov"); dd("gp-st", "st");
    document.addEventListener("click", (e) => { if (!e.target.closest(".gp-dd")) document.querySelectorAll(".gp-dd-pop").forEach((p) => p.classList.add("hidden")); });
    $("#gp-log").addEventListener("click", (e) => { st.log = !st.log; e.currentTarget.classList.toggle("on", st.log); rebuild(); });
    $("#gp-cm").addEventListener("click", (e) => { st.cmenu = st.cmenu !== true; e.currentTarget.classList.toggle("on", st.cmenu); closeMenu(); save(); });
    $("#gp-pre").addEventListener("click", (e) => { st.pre = !st.pre; e.currentTarget.classList.toggle("on", st.pre); rebuild(); });
    $("#gp-wheel").addEventListener("click", (e) => { st.wheel = !st.wheel; e.currentTarget.classList.toggle("on", st.wheel); save(); });
    $("#gp-mag").addEventListener("click", (e) => { st.magnet = !st.magnet; e.currentTarget.classList.toggle("on", st.magnet); save(); });
    $("#gp-keep").addEventListener("click", (e) => { st.keep = !st.keep; e.currentTarget.classList.toggle("on", st.keep); save(); });
    $("#gp-vp").addEventListener("click", (e) => { st.vp = !st.vp; e.currentTarget.classList.toggle("on", st.vp); save(); paint(); });
    $("#gp-cmp").addEventListener("keydown", (e) => { if (e.key === "Enter") { st.cmp = e.target.value.trim().toUpperCase(); rebuild(); } });
    $("#gp-snap").addEventListener("click", capture);
    drawAlertBar();
    await build();
    liveLoop();
    loadAlerts(); loadSnaps();

    // "AI mark high and lows on the graph": window.NX.chartAI is the one place the AI command bar (capital.js)
    // reaches into whichever chart is on screen. Registered last, once `data`/`drawings`/`persist`/`paint` above
    // are all live, and cleared by sx.chart.remove() so a stale handler never survives leaving this security.
    chartAI = {
      markHighsLows(opts) {
        if (!data.length) { showToast("No chart data loaded yet."); return; }
        const look = LOOKBACK_BY_SENSITIVITY[(opts || {}).sensitivity] || LOOKBACK_BY_SENSITIVITY.normal;
        drawings = drawings.filter((d) => !d.auto); // a fresh AI marking replaces its own previous one, not the user's drawings
        const pts = swingPoints(data, look);
        pts.forEach((pt) => drawings.push({ k: "pt", a: { t: pt.t, p: pt.p }, label: pt.label, auto: true }));
        persist(); paint();
        showToast(pts.length ? `Marked ${pts.length} swing highs/lows` : "No clear swing highs/lows found on this window");
      },
      async markWyckoff(opts) {
        if (!data.length) { showToast("No chart data loaded yet."); return; }
        const tf = WYCKOFF_TF[effInterval];
        if (!tf) { showToast(`Wyckoff detection needs a 15M, 1H, 4H or 1D chart — this one is ${effInterval || tfLabel()}.`); return; }
        // 1D's own default range (1Y ≈ 252 bars) can never reach the 300-bar minimum the detector needs, since
        // picking the 1D interval chip always snaps the range back to 1Y (see INTERVAL_RANGE) - widen it here
        // rather than fail on the single interval where "not enough history" is really just a chart-view default.
        if (effInterval === "1d" && data.length < 300) {
          st.interval = "1d"; st.range = "5Y"; save(); sync();
          showToast("Widening to 5Y of daily bars for enough history…");
          await build();
          if (data.length < 300) { showToast("Still not enough daily history on this security for a Wyckoff scan."); return; }
        }
        const side = (opts || {}).side || "either";
        showToast("Scanning for a Wyckoff setup…");
        let res;
        try {
          res = await api("/academy/wyckoff/scan", { method: "POST", body: JSON.stringify({ candles: data.map((x) => [x.t, x.open, x.high, x.low, x.close, x.volume]), interval: st.interval }) });
        } catch (e) { showToast(`Couldn't scan for a Wyckoff setup: ${e.message || e}`); return; }
        const wantKind = side === "long" ? "accumulation" : side === "short" ? "distribution" : null;
        const pool = wantKind ? res.setups.filter((s) => s.kind === wantKind) : res.setups;
        if (!pool.length) {
          showToast(res.setups.length
            ? `No ${side} (${wantKind}) Wyckoff setup found on this chart — try the other side, or widen the range.`
            : "No clear Wyckoff setup found on this chart. Try a longer range, a different timeframe, or another security — most stretches of history don't have a textbook one.");
          return;
        }
        const setup = pool[pool.length - 1]; // most recent
        drawings = drawings.filter((d) => !d.auto);
        const labels = WYCKOFF_LABEL[setup.kind];
        // `setup.events`/`entry_i` are indices into the exact array just sent (`data`, in order), so they map
        // straight back onto it - no need to hunt for a matching bar by time.
        const eventIdxs = [];
        Object.entries(setup.events).forEach(([key, idx]) => {
          if (idx == null || !data[idx]) return;
          eventIdxs.push(idx);
          const bar = data[idx], low = setup.kind === "accumulation" ? WYCKOFF_LOW_EVENT[key] : !WYCKOFF_LOW_EVENT[key];
          drawings.push({ k: "pt", a: { t: bar.t, p: low ? bar.low : bar.high }, label: labels[key], up: !low, color: "#e8d44d", auto: true });
        });
        const entryBar = data[setup.entry_i] || data[data.length - 1];
        [["RANGE TOP", setup.range_high, "#ffa028"], ["RANGE BOTTOM", setup.range_low, "#ffa028"],
         ["ENTRY", setup.entry, "#5aa9ff"], ["STOP", setup.stop, "#ff4a4a"], ["TARGET", setup.target, "#2be07e"]]
          .forEach(([title, p, color]) => drawings.push({ k: "hline", a: { t: entryBar.t, p }, title, color, auto: true }));
        persist(); paint();
        // the setup can be anywhere in however much history was scanned - bring it into view rather than leave it
        // off-screen (the default view only shows the most recent ~140 bars) with nothing visibly different.
        if (charts[0]) charts[0].timeScale().setVisibleLogicalRange({ from: Math.min(...eventIdxs, setup.entry_i) - 15, to: setup.entry_i + 60 });
        const outcome = setup.outcome.result === "win" ? "reached target" : setup.outcome.result === "loss" ? "was stopped out" : "is still open in this data";
        showToast(`${setup.kind === "accumulation" ? "Accumulation (long)" : "Distribution (short)"} setup: entry ${px(setup.entry)}, stop ${px(setup.stop)}, `
          + `target ${px(setup.target)} (${setup.rr}R). In this history it ${outcome}. Wyckoff is discretionary and most textbook setups fail — this is not a signal.`);
      },
      clearAiMarks() {
        const before = drawings.length;
        drawings = drawings.filter((d) => !d.auto);
        persist(); paint();
        showToast(before === drawings.length ? "No AI marks to clear" : "AI marks cleared");
      },
    };
    NX.chartAI = chartAI;
  }
  window.NXGP = { render };
})();
