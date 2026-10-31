/* SIM ENGINE: a deterministic synthetic futures session (same seed = same market, tick for tick).
   A hidden fair value follows a scenario script (trend / balance / news / pin / squeeze / reversal), aggressive orders chase it and are
   matched against a real limit order book: resting depth that refills, walls (real, iceberg, spoof), stop clusters beyond swing points
   that get swept, absorbers that soak up flow, and dealer gamma (pin above zero gamma, acceleration below it).
   Output: every print (time, price, size, aggressor), 1-second bars, the top 20 levels of the book every second, a liquidity heatmap every
   5 seconds, synthetic option open interest with call wall / put wall / zero gamma, and the hidden event script used to grade the trader.
   Works in the browser (window.SimEngine) and in Node (module.exports) for testing. */
(function (root) {
  "use strict";

  function rng(seed) {
    let a = seed >>> 0;
    const next = () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    let spare = null;
    const gauss = () => { if (spare !== null) { const s = spare; spare = null; return s; } let u = 0, v = 0; while (u === 0) u = next(); v = next(); const m = Math.sqrt(-2 * Math.log(u)); spare = m * Math.sin(2 * Math.PI * v); return m * Math.cos(2 * Math.PI * v); };
    const poisson = (lam) => { if (lam <= 0) return 0; if (lam > 30) return Math.max(0, Math.round(lam + Math.sqrt(lam) * gauss())); const L = Math.exp(-lam); let k = 0, p = 1; do { k++; p *= next(); } while (p > L); return k - 1; };
    const pick = (arr) => arr[Math.floor(next() * arr.length)];
    const range = (a, b) => a + (b - a) * next();
    return { next, gauss, poisson, pick, range };
  }

  const SCENARIOS = {
    random: "Random day (any mix)",
    trend: "Trend day (open drive, shallow pullbacks)",
    range: "Range day (balance, stop runs at the edges)",
    news: "News day (quiet, then a shock)",
    pin: "Gamma pin (positive gamma, price glued to a strike)",
    squeeze: "Squeeze (negative gamma, short squeeze through the call wall)",
    reversal: "Reversal day (trend, exhaustion, absorption, reversal)",
  };

  // ------------------------------------------------------------------ the scenario script
  function buildScript(R, scenario, N, startK) {
    // the scenarios are written for a 6.5 h day; a shorter session (the NY open) plays the same story faster, with the open's higher energy
    const f = Math.min(1, N / 23400), boost = f < 1 ? 1.25 : 1;
    if (scenario === "random") scenario = R.pick(["trend", "range", "news", "pin", "squeeze", "reversal"]);
    const segs = [], events = [];
    const dir = R.next() < 0.5 ? 1 : -1;
    const seg = (t0, t1, type, p) => segs.push({ t0: Math.round(t0), t1: Math.round(Math.min(t1, N)), type, ...p, ...(p && p.vol ? { vol: p.vol * boost } : {}), ...(p && p.drift ? { drift: p.drift * (f < 1 ? 1.5 : 1) } : {}) });
    const ev = (t, type, p) => events.push({ t: Math.round(t), type, ...p });
    const m = (x) => x * 60 * f;
    if (scenario === "trend") {
      seg(0, m(30), "drive", { drift: dir * R.range(0.035, 0.06), vol: 1.3 });
      let t = m(30);
      while (t < N) { const len = m(R.range(25, 60)); seg(t, t + len, "trend", { drift: dir * R.range(0.009, 0.022), vol: 1.0 }); t += len; const pb = m(R.range(8, 18)); if (t < N) seg(t, t + pb, "pullback", { drift: -dir * R.range(0.015, 0.03), vol: 0.9 }); t += pb; }
      ev(R.range(m(120), m(200)), "absorb", { dir });
    } else if (scenario === "range") {
      seg(0, N, "balance", { vol: 1.0, mr: 0.004 });
      ev(R.range(m(70), m(150)), "sweep", { dir: 1 }); ev(R.range(m(200), m(320)), "sweep", { dir: -1 });
      ev(R.range(m(160), m(190)), "spoof", { dir: R.next() < 0.5 ? 1 : -1 });
    } else if (scenario === "news") {
      const tn = R.range(m(120), m(240));
      seg(0, tn, "balance", { vol: 0.75, mr: 0.004 });
      seg(tn, tn + m(45), "trend", { drift: dir * R.range(0.03, 0.05), vol: 1.6 });
      seg(tn + m(45), N, "balance", { vol: 1.0, mr: 0.003 });
      ev(tn, "news", { dir, jump: R.range(35, 70) });
      ev(R.range(m(40), m(100)), "sweep", { dir: R.next() < 0.5 ? 1 : -1 });
    } else if (scenario === "pin") {
      seg(0, N, "balance", { vol: 0.8, mr: 0.006 });
      ev(R.range(m(90), m(200)), "spoof", { dir: R.next() < 0.5 ? 1 : -1 });
      ev(R.range(m(220), m(330)), "sweep", { dir: R.next() < 0.5 ? 1 : -1 });
    } else if (scenario === "squeeze") {
      seg(0, m(90), "balance", { vol: 1.0, mr: 0.003 });
      seg(m(90), m(160), "trend", { drift: 0.02, vol: 1.2 });
      seg(m(160), m(200), "squeeze", { drift: 0.07, vol: 1.8 });
      seg(m(200), N, "balance", { vol: 1.2, mr: 0.002 });
      ev(m(160), "squeeze", { dir: 1 });
      ev(R.range(m(30), m(80)), "sweep", { dir: -1 });
    } else { // reversal
      const tr = R.range(m(170), m(230));
      seg(0, m(20), "drive", { drift: dir * 0.05, vol: 1.2 });
      seg(m(20), tr - m(30), "trend", { drift: dir * R.range(0.015, 0.025), vol: 1.0 });
      seg(tr - m(30), tr, "exhaust", { drift: dir * 0.01, vol: 1.3 });
      seg(tr, N, "trend", { drift: -dir * R.range(0.02, 0.035), vol: 1.1 });
      ev(tr - m(12), "absorb", { dir: -dir, reversal: true });
    }
    segs.sort((a, b) => a.t0 - b.t0);
    events.sort((a, b) => a.t - b.t);
    return { scenario, segs, events };
  }

  // ------------------------------------------------------------------ synthetic option open interest -> gamma levels
  function buildGamma(R, startK, scenario, strikeStep) {
    const strikes = [];
    const centre = Math.round(startK / strikeStep) * strikeStep;
    for (let i = -12; i <= 12; i++) {
      const k = centre + i * strikeStep, d = Math.abs(i);
      const big = (k / strikeStep) % 4 === 0 ? 2.2 : 1;
      strikes.push({ k, call: Math.round((i >= 0 ? 1 : 0.35) * big * R.range(2000, 9000) * Math.exp(-d / 7)), put: Math.round((i <= 0 ? 1 : 0.35) * big * R.range(2000, 9000) * Math.exp(-d / 7)) });
    }
    // scenario shaping: a pin day has one huge strike at the money; a squeeze day sits below zero gamma with a call wall overhead
    if (scenario === "pin") { const s = strikes[12]; s.call *= 3.5; s.put *= 3.5; }
    if (scenario === "squeeze") { strikes.forEach((s) => { if (s.k < centre) s.put *= 2.2; if (s.k > centre + 2 * strikeStep && s.k <= centre + 4 * strikeStep) s.call *= 2.5; }); }
    return strikes;
  }

  function gex(strikes, spotK, tickPrice) {
    // dealer gamma exposure by strike (calls +, puts -), Black-Scholes gamma, 1 week, 18% vol; units: relative
    const S = spotK * tickPrice, T = 5 / 252, v = 0.18, out = [];
    let total = 0;
    for (const s of strikes) {
      const K = s.k * tickPrice, d1 = (Math.log(S / K) + 0.5 * v * v * T) / (v * Math.sqrt(T));
      const g = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI) / (S * v * Math.sqrt(T));
      const val = g * S * S * 0.01 * (s.call - s.put);
      out.push({ k: s.k, gex: val, call: s.call, put: s.put });
      total += val;
    }
    return { byStrike: out, total };
  }

  function gammaLevels(strikes, refK, tickPrice, strikeStep) {
    let callWall = strikes[0], putWall = strikes[0];
    let cw = null, pw = null;
    for (const s of strikes) { if (s.k >= refK + 2 * strikeStep && (!cw || s.call > cw.call)) cw = s; if (s.k <= refK - 2 * strikeStep && (!pw || s.put > pw.put)) pw = s; }
    callWall = cw || callWall; putWall = pw || putWall;
    // zero gamma: spot where total GEX changes sign, scanning +-8 strikes
    let zero = null, prev = null;
    for (let k = refK - 8 * strikeStep; k <= refK + 8 * strikeStep; k += Math.max(1, Math.round(strikeStep / 8))) {
      const t = gex(strikes, k, tickPrice).total;
      if (prev !== null && Math.sign(t) !== Math.sign(prev.t)) { zero = Math.round(prev.k + (k - prev.k) * (Math.abs(prev.t) / (Math.abs(prev.t) + Math.abs(t)))); break; }
      prev = { k, t };
    }
    if (zero === null) zero = gex(strikes, refK, tickPrice).total > 0 ? putWall.k - strikeStep : callWall.k + strikeStep;
    let hvl = strikes[0];
    for (const s of strikes) if (s.call + s.put > hvl.call + hvl.put) hvl = s;
    return { callWall: callWall.k, putWall: putWall.k, zeroGamma: zero, hvl: hvl.k };
  }

  // ------------------------------------------------------------------ the session
  function generate(opts) {
    const seed = (opts && opts.seed) >>> 0 || 1;
    const R = rng(seed);
    const minutes = (opts && opts.minutes) || 390;
    const N = minutes * 60;
    const tick = 0.25, pointValue = 50, strikeStep = 20; // ES-like: 0.25 tick, $12.50 a tick, strikes every 5 points
    const startPrice = Math.round(R.range(4800, 6200) / 5) * 5;
    const startK = Math.round(startPrice / tick);
    const script = buildScript(R, (opts && opts.scenario) || "random", N, startK);
    const strikes = buildGamma(R, startK, script.scenario, strikeStep);
    const lv = gammaLevels(strikes, startK, tick, strikeStep);
    if (script.scenario === "squeeze" && (lv.zeroGamma === null || lv.zeroGamma < startK)) lv.zeroGamma = startK + 6 * 4;
    if (script.scenario === "pin" && (lv.zeroGamma === null || lv.zeroGamma > startK)) lv.zeroGamma = startK - 10 * 4;

    const G = 12000, base = startK - G / 2;               // liquidity grid (absolute tick index = base + i)
    const L = new Float32Array(G);                         // resting size at each level
    let bid = startK - 1 - base, ask = startK - base;      // grid indices of best bid / best ask
    const depthT = (d) => 18 + 2.2 * d;                    // typical resting size d levels from the inside
    for (let d = 0; d < 60; d++) { L[bid - d] = depthT(d) * R.range(0.6, 1.4); L[ask + d] = depthT(d) * R.range(0.6, 1.4); }

    // outputs
    const cap = N * 26;
    let pt = new Float32Array(cap), pp = new Int32Array(cap), ps = new Uint16Array(cap), pside = new Int8Array(cap), np = 0;
    const secStart = new Int32Array(N + 1);
    const O = new Int32Array(N), H = new Int32Array(N), Lo = new Int32Array(N), C = new Int32Array(N), V = new Float32Array(N), BV = new Float32Array(N), SV = new Float32Array(N);
    const BB = new Int32Array(N), BA = new Int32Array(N);
    const D = 20, bookB = new Float32Array(N * D), bookA = new Float32Array(N * D);
    const HS = 5, HH = 110, nh = Math.ceil(N / HS), heatMid = new Int32Array(nh), heat = new Float32Array(nh * (2 * HH + 1));
    const pushPrint = (t, k, q, side) => {
      if (np >= pt.length) { const grow = (A, T) => { const B = new T(A.length * 2); B.set(A); return B; }; pt = grow(pt, Float32Array); pp = grow(pp, Int32Array); ps = grow(ps, Uint16Array); pside = grow(pside, Int8Array); }
      pt[np] = t; pp[np] = k + base; ps[np] = Math.min(65535, Math.round(q)); pside[np] = side; np++;
    };

    let F = startK - 0.5;                                  // hidden fair value (absolute ticks)
    const walls = [];                                       // {i (grid), size, kind: real|iceberg|spoof, refills, side, active, t0, t1, tag}
    const stops = [];                                       // {i, side (+1 buy stops above, -1 sell stops below), size, armed}
    const hist = [];                                        // mid every second (for swings / momentum)
    let liqMult = 1, thinUntil = -1, evIdx = 0;
    const active = [];                                      // events in progress {ev, phase, ...}
    const truth = [];                                       // what really happened (for grading): event, level, ideal trade
    const levelI = (k) => k - base;
    // walls at the gamma levels: dealers' hedging shows up as resting size at the big strikes
    if (lv.callWall) walls.push({ i: levelI(lv.callWall), size: 260, kind: "iceberg", refills: 3, active: true, tag: "call wall" });
    if (lv.putWall) walls.push({ i: levelI(lv.putWall), size: 260, kind: "iceberg", refills: 3, active: true, tag: "put wall" });

    const segAt = (t) => { for (let j = script.segs.length - 1; j >= 0; j--) if (script.segs[j].t0 <= t) return script.segs[j]; return script.segs[0]; };
    const activity = (t) => { const x = t / N; return 0.75 + 1.4 * Math.exp(-x * 9) + 0.9 * Math.exp(-(1 - x) * 10); }; // U-shaped volume through the day

    function swingHigh(t, w) { let m = -Infinity; for (let j = Math.max(0, t - w); j < t; j++) m = Math.max(m, H[j]); return m; }
    function swingLow(t, w) { let m = Infinity; for (let j = Math.max(0, t - w); j < t; j++) m = Math.min(m, Lo[j]); return m; }

    function marketOrder(t, side, q, tag) {
      let left = q;
      let guard = 0;
      while (left > 0.5 && guard++ < 400) {
        if (side > 0) {
          if (L[ask] <= 0.5) { ask++; continue; }
          const take = Math.min(left, L[ask]); L[ask] -= take; left -= take; pushPrint(t, ask, take, 1);
          for (const w of walls) if (w.active && w.i === ask) w.touched = true;
          if (L[ask] <= 0.5) {
            const w = walls.find((x) => x.active && x.i === ask && x.kind === "iceberg" && x.refills > 0);
            if (w) { L[ask] = w.size; w.refills--; w.absorbed = (w.absorbed || 0) + take; } else ask++;
          }
        } else {
          if (L[bid] <= 0.5) { bid--; continue; }
          const take = Math.min(left, L[bid]); L[bid] -= take; left -= take; pushPrint(t, bid, take, -1);
          for (const w of walls) if (w.active && w.i === bid) w.touched = true;
          if (L[bid] <= 0.5) {
            const w = walls.find((x) => x.active && x.i === bid && x.kind === "iceberg" && x.refills > 0);
            if (w) { L[bid] = w.size; w.refills--; w.absorbed = (w.absorbed || 0) + take; } else bid--;
          }
        }
      }
      if (side > 0 && ask - bid > 1) { bid = ask - 1; L[bid] = Math.max(L[bid], depthT(0) * 0.45 * liqMult); }
      if (side < 0 && ask - bid > 1) { ask = bid + 1; L[ask] = Math.max(L[ask], depthT(0) * 0.45 * liqMult); }
      if (bid >= ask) { if (side > 0) bid = ask - 1; else ask = bid + 1; }
    }

    for (let t = 0; t < N; t++) {
      secStart[t] = np;
      const mid = (bid + ask) / 2 + base;
      const sg = segAt(t);
      // ---- scripted events
      while (evIdx < script.events.length && script.events[evIdx].t <= t) {
        const e = script.events[evIdx++];
        const midK = Math.round(mid);
        if (e.type === "news") { thinUntil = t + 90; F += e.dir * e.jump; truth.push({ t, type: "news", k: midK, dir: e.dir, note: `News shock: ${e.dir > 0 ? "up" : "down"} ${Math.round(e.jump * tick)} pts, the book thins out` }); }
        if (e.type === "sweep") {
          const hi = swingHigh(t, 3600), lo = swingLow(t, 3600);
          const lvl = e.dir > 0 ? hi : lo;
          if (isFinite(lvl)) {
            const beyond = lvl + e.dir * Math.round(R.range(6, 12));
            stops.push({ i: levelI(lvl + e.dir * 2), side: e.dir, size: R.range(300, 520), armed: true, tag: "sweep" });
            walls.push({ i: levelI(beyond), size: 420, kind: "iceberg", refills: 5, active: true, tag: "absorber" });
            active.push({ e, phase: 0, lvl, beyond, t0: t });
          }
        }
        if (e.type === "absorb") {
          const lvl = Math.round(mid) + (e.dir > 0 ? -4 : 4);
          walls.push({ i: levelI(lvl), size: 380, kind: "iceberg", refills: 7, active: true, tag: "absorber" });
          active.push({ e, phase: 0, lvl, t0: t });
        }
        if (e.type === "spoof") {
          const lvl = Math.round(mid) + e.dir * 10;
          walls.push({ i: levelI(lvl), size: 900, kind: "spoof", active: true, tag: "spoof", t0: t });
          truth.push({ t, type: "spoof", k: lvl, dir: e.dir, note: `Spoof: a ${e.dir > 0 ? "sell" : "buy"} wall of 900 at ${(lvl * tick).toFixed(2)} that will be pulled before price gets there` });
        }
        if (e.type === "squeeze") { stops.push({ i: levelI(lv.callWall + 2), side: 1, size: 700, armed: true, tag: "squeeze" }); truth.push({ t, type: "squeeze", k: Math.round(mid), dir: 1, note: "Short squeeze: below zero gamma, dealers chase the move; stops above the call wall" }); }
      }
      // ---- event phases
      for (const a of active) {
        if (a.done) continue;
        const e = a.e;
        if (e.type === "sweep") {
          if (a.phase === 0) { F += e.dir * 0.9; if ((e.dir > 0 ? mid >= a.lvl + 3 : mid <= a.lvl - 3)) { a.phase = 1; a.tSweep = t; } if (t - a.t0 > 900) a.done = true; }
          else if (a.phase === 1) { if (t - a.tSweep > 25) { a.phase = 2; a.tRev = t; a.ext = e.dir > 0 ? swingHigh(t, 90) : swingLow(t, 90); } }
          else if (a.phase === 2) { F -= e.dir * 0.55; if (t - a.tRev > 240) { a.done = true; truth.push({ t: a.tSweep, type: "sweep", k: a.lvl, dir: e.dir, ext: a.ext, note: `Stop run ${e.dir > 0 ? "above the high" : "below the low"} at ${(a.lvl * tick).toFixed(2)}: stops triggered, a passive absorber beyond, then back inside`, ideal: { side: -e.dir, entryT: a.tRev, entry: a.lvl, stop: a.ext + e.dir * 2, target: a.lvl - e.dir * 24 } }); } }
        }
        if (e.type === "absorb") {
          if (a.phase === 0) { F += -e.dir * 0.25; if (t - a.t0 > 300) { a.phase = 1; a.tRev = t; } }
          else if (a.phase === 1) { F += e.dir * 0.35; if (t - a.tRev > 300) { a.done = true; truth.push({ t: a.t0, type: "absorption", k: a.lvl, dir: e.dir, note: `Absorption: a passive ${e.dir > 0 ? "buyer" : "seller"} soaked up the ${e.dir > 0 ? "selling" : "buying"} at ${(a.lvl * tick).toFixed(2)} (heavy delta, no progress), then price turned${e.reversal ? " and the trend reversed" : ""}`, ideal: { side: e.dir, entryT: a.tRev, entry: a.lvl + e.dir * 2, stop: a.lvl - e.dir * 6, target: a.lvl + e.dir * 30 } }); } }
        }
      }
      // ---- fair value
      const gpos = lv.zeroGamma !== null && mid >= lv.zeroGamma;
      let drift = sg.drift || 0, vol = (sg.vol || 1) * 1.15, mr = sg.mr || 0;
      if (!sg.anchor) sg.anchor = mid;
      if (gpos) { vol *= 0.85; const pin = [lv.hvl, lv.callWall, lv.putWall].filter(Boolean).reduce((b, k) => (Math.abs(k - mid) < Math.abs(b - mid) ? k : b), lv.hvl); if (Math.abs(pin - mid) < 30) F += (pin - F) * 0.0025; }
      else { vol *= 1.25; const mom = hist.length > 120 ? mid - hist[hist.length - 120] : 0; F += mom * 0.004; }
      F += drift + vol * R.gauss() * 0.85 + mr * (sg.anchor - F);
      // ---- liquidity provision
      liqMult += ((t < thinUntil ? 0.25 : 1) - liqMult) * 0.08;
      for (let d = 0; d < 45; d++) {
        const tb = depthT(d) * liqMult, ib = bid - d, ia = ask + d;
        L[ib] = Math.max(0, L[ib] + (tb - L[ib]) * 0.18 + R.gauss() * 1.6);
        L[ia] = Math.max(0, L[ia] + (tb - L[ia]) * 0.18 + R.gauss() * 1.6);
      }
      for (const w of walls) {
        if (!w.active) continue;
        const dist = Math.abs(w.i + base - mid);
        if (w.kind === "spoof" && dist <= 4) { w.active = false; L[w.i] = Math.max(0, L[w.i] - w.size); w.pulledT = t; continue; }
        if (dist < 90 && !w.touched && ((w.i <= bid) || (w.i >= ask))) L[w.i] = Math.max(L[w.i], w.size * (w.kind === "spoof" ? 1 : 0.9));
        if (w.kind === "iceberg" && w.touched && w.refills <= 0 && L[w.i] < 1) w.active = false;
      }
      while (ask - bid > 1) { if (F > mid) { bid++; L[bid] = depthT(0) * 0.5 * liqMult; } else { ask--; L[ask] = depthT(0) * 0.5 * liqMult; } }
      // ---- aggressive flow chasing fair value
      const gap = F - ((bid + ask) / 2 + base);
      const imb = Math.tanh(gap / 5);
      const lam = 5.2 * activity(t) * (t < thinUntil ? 1.6 : 1);
      const nb = R.poisson(lam * (1 + 0.85 * imb)), ns = R.poisson(lam * (1 - 0.85 * imb));
      const orders = [];
      const sz = () => { if (R.next() < 0.012) return Math.round(R.range(40, 220)); return 1 + Math.floor(Math.exp(0.5 + 0.95 * R.gauss()) * 0.9); };
      for (let j = 0; j < nb; j++) orders.push([R.next(), 1, sz()]);
      for (let j = 0; j < ns; j++) orders.push([R.next(), -1, sz()]);
      orders.sort((x, y) => x[0] - y[0]);
      for (const o of orders) marketOrder(t + o[0], o[1], o[2]);
      // ---- stops beyond swing points (new clusters every few minutes) and their triggering
      if (t % 300 === 299 && t > 900) {
        const hi = swingHigh(t, 1800), lo = swingLow(t, 1800);
        if (isFinite(hi) && !stops.some((s) => s.armed && Math.abs(s.i + base - (hi + 2)) < 3)) stops.push({ i: levelI(hi + 2), side: 1, size: R.range(60, 160), armed: true, tag: "swing" });
        if (isFinite(lo) && !stops.some((s) => s.armed && Math.abs(s.i + base - (lo - 2)) < 3)) stops.push({ i: levelI(lo - 2), side: -1, size: R.range(60, 160), armed: true, tag: "swing" });
      }
      for (const s of stops) {
        if (!s.armed) continue;
        if ((s.side > 0 && ask >= s.i) || (s.side < 0 && bid <= s.i)) {
          s.armed = false; s.t = t;
          let left = s.size; while (left > 0) { const q = Math.min(left, R.range(8, 30)); marketOrder(t + R.next() * 0.99, s.side, q, "stop"); left -= q; }
          if (s.tag === "swing" && s.size > 135 && !truth.some((x) => x.type === "stops" && t - x.t < 1800)) truth.push({ t, type: "stops", k: s.i + base, dir: s.side, note: `Stops triggered ${s.side > 0 ? "above a swing high" : "below a swing low"} at ${((s.i + base) * tick).toFixed(2)}` });
        }
      }
      // ---- record the second
      const a0 = secStart[t];
      if (np > a0) {
        let o = pp[a0], h = o, l = o, v = 0, bv = 0, sv = 0;
        for (let j = a0; j < np; j++) { const k = pp[j]; if (k > h) h = k; if (k < l) l = k; v += ps[j]; if (pside[j] > 0) bv += ps[j]; else sv += ps[j]; }
        O[t] = o; H[t] = h; Lo[t] = l; C[t] = pp[np - 1]; V[t] = v; BV[t] = bv; SV[t] = sv;
      } else { const c = t ? C[t - 1] : startK; O[t] = H[t] = Lo[t] = C[t] = c; }
      BB[t] = bid + base; BA[t] = ask + base;
      for (let d = 0; d < D; d++) { bookB[t * D + d] = L[bid - d]; bookA[t * D + d] = L[ask + d]; }
      if (t % HS === 0) {
        const hi = t / HS, m0 = Math.round((bid + ask) / 2);
        heatMid[hi] = m0 + base;
        for (let j = -HH; j <= HH; j++) heat[hi * (2 * HH + 1) + j + HH] = L[m0 + j] || 0;
      }
      hist.push((bid + ask) / 2 + base);
      // keep the hidden value from drifting far from traded price (market makers lean)
      F += (((bid + ask) / 2 + base) - F) * 0.02;
    }
    secStart[N] = np;
    // walls the trader could see: for the report
    for (const w of walls) if (w.tag === "spoof" && w.pulledT !== undefined) { const tr = truth.find((x) => x.type === "spoof" && Math.abs(x.k - (w.i + base)) < 2); if (tr) tr.pulledT = w.pulledT; }
    truth.sort((a, b) => a.t - b.t);
    return {
      seed, scenario: script.scenario, scenarioName: SCENARIOS[script.scenario], N, tick, pointValue, tickValue: tick * pointValue, startK, strikeStep,
      prints: { t: pt.subarray(0, np), k: pp.subarray(0, np), q: ps.subarray(0, np), side: pside.subarray(0, np), n: np }, secStart,
      sec: { o: O, h: H, l: Lo, c: C, v: V, bv: BV, sv: SV, bid: BB, ask: BA }, book: { D, b: bookB, a: bookA },
      heat: { step: HS, half: HH, mid: heatMid, v: heat, n: nh }, strikes, levels: lv, truth, segs: script.segs,
    };
  }

  const api = { generate, gex, gammaLevels, SCENARIOS, rng };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SimEngine = api;
})(typeof window !== "undefined" ? window : globalThis);
