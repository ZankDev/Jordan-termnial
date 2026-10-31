/* SIM: order-flow trading simulator on a synthetic, seeded market (web/sim_engine.js).
   A live-simulated session you can trade freely: liquidity heatmap + candles + big-print bubbles + VWAP + gamma levels + volume profile,
   a clickable DOM ladder, time & sales, delta / CVD, a gamma panel, market and limit orders with a draggable bracket (real fills against
   the book: slippage and queue position). At the end (or any time) a report grades every trade, names your setups, lists your mistakes and
   the opportunities you missed (from the hidden script), replays the whole session smoothly and asks the AI coach for a written review.
   Practice only: nothing here touches a real account. */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc } = NX;
  const E = () => window.SimEngine;
  const FEE = 2.5;                       // $ per contract per side
  const OPEN_H = 9, OPEN_M = 30, OPEN_MIN = 120;  // the simulator trades the New York open only: 09:30-11:30 ET
  const TFS = [[5, "5s"], [15, "15s"], [30, "30s"], [60, "1m"], [300, "5m"], [900, "15m"]];
  const FIRST = 900;  // training starts once the first 15-minute candle (09:30-09:45) has closed
  const SPEEDS = [1, 2, 5, 10, 30, 60, 120];
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const PREF = lget("nx_sim_pref", { tf: 60, speed: 10, heat: true, bubbles: true, vwap: true, gamma: true, profile: true, hints: true, qty: 1, stopT: 16, tgtT: 32, bracket: true, minPrint: 1 });
  Object.entries({ theme: "tv", vol: true, fvg: true, htf: true, levels: true }).forEach(([k, v]) => { if (PREF[k] === undefined) PREF[k] = v; });
  if (PREF._v !== 2) { PREF.heat = false; PREF._v = 2; }   // the light TradingView look starts without the heatmap (toggle it on)

  const S = { sess: null, seed: 0, scenario: "random", simT: 0, playing: false, speed: PREF.speed, tf: PREF.tf, last: 0, pIdx: 0, active: false, built: false,
    view: { span: 1800, right: 0, follow: true, yZoom: 1, yOff: 0 }, cache: {}, volAt: null, gexCache: null, hintsShown: new Set(), feed: [],
    acct: null, orders: [], fills: [], trades: [], cur: null, oid: 1, drag: null, hover: null, ended: false, lastUi: 0, raf: 0 };

  // ------------------------------------------------------------------ helpers
  const T = () => S.sess.tick;
  const P = (k) => (k * S.sess.tick).toFixed(2);
  const clock = (t) => { const s = Math.floor(t) + (OPEN_H * 60 + OPEN_M) * 60; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`; };
  const money = (v) => `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  const sec = () => Math.min(S.sess.N - 1, Math.max(0, Math.floor(S.simT)));
  const snapSec = () => Math.max(0, sec() - 1);                         // book as of the end of the previous second (no look-ahead)
  const lastK = () => (S.pIdx > 0 ? S.sess.prints.k[S.pIdx - 1] : S.sess.startK);
  const nz = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nz")) || 1;

  function candles(tf) {
    if (S.cache[tf]) return S.cache[tf];
    const s = S.sess.sec, N = S.sess.N, n = Math.ceil(N / tf);
    const c = { o: new Int32Array(n), h: new Int32Array(n), l: new Int32Array(n), c: new Int32Array(n), v: new Float32Array(n), d: new Float32Array(n), n };
    for (let i = 0; i < n; i++) {
      const a = i * tf, b = Math.min(N, a + tf);
      let h = -Infinity, l = Infinity, v = 0, d = 0;
      for (let j = a; j < b; j++) { if (s.h[j] > h) h = s.h[j]; if (s.l[j] < l) l = s.l[j]; v += s.v[j]; d += s.bv[j] - s.sv[j]; }
      c.o[i] = s.o[a]; c.c[i] = s.c[b - 1]; c.h[i] = h; c.l[i] = l; c.v[i] = v; c.d[i] = d;
    }
    S.cache[tf] = c;
    return c;
  }
  // a candle as of now (the forming one is built from the 1-second bars and the prints of the current second)
  function liveCandle(i, tf) {
    const c = candles(tf), a = i * tf, now = S.simT, s = S.sess.sec, p = S.sess.prints;
    if ((i + 1) * tf <= now) return { o: c.o[i], h: c.h[i], l: c.l[i], c: c.c[i], v: c.v[i], d: c.d[i] };
    const full = Math.floor(now);
    let o = null, h = -Infinity, l = Infinity, cl = null, v = 0, d = 0;
    for (let j = a; j < full; j++) { if (o === null) o = s.o[j]; if (s.h[j] > h) h = s.h[j]; if (s.l[j] < l) l = s.l[j]; cl = s.c[j]; v += s.v[j]; d += s.bv[j] - s.sv[j]; }
    for (let j = S.sess.secStart[full] || 0; j < S.pIdx; j++) { const k = p.k[j]; if (o === null) o = k; if (k > h) h = k; if (k < l) l = k; cl = k; v += p.q[j]; d += p.side[j] * p.q[j]; }
    if (o === null) return null;
    return { o, h, l, c: cl, v, d };
  }
  function vwapAt(t) {
    const c = S.cache.vw;
    const i = Math.min(S.sess.N - 1, Math.max(0, Math.floor(t)));
    if (!c.v[i]) return null;
    const m = c.pv[i] / c.v[i], va = c.p2v[i] / c.v[i] - m * m;
    return { m, sd: Math.sqrt(Math.max(0, va)) };
  }
  function prep() {
    const s = S.sess, p = s.prints, N = s.N;
    const pv = new Float64Array(N), p2v = new Float64Array(N), v = new Float64Array(N);
    let a = 0, b = 0, c = 0;
    for (let t = 0; t < N; t++) { for (let j = s.secStart[t]; j < s.secStart[t + 1]; j++) { const k = p.k[j], q = p.q[j]; a += k * q; b += k * k * q; c += q; } pv[t] = a; p2v[t] = b; v[t] = c; }
    S.cache = { vw: { pv, p2v, v } };
    S.volBase = s.startK - 6000;
    S.volAt = { b: new Float32Array(12000), s: new Float32Array(12000) };
  }
  function atr1m(t) { // average 1-minute range over the last 20 minutes, in ticks
    const s = S.sess.sec; let sum = 0, n = 0;
    for (let m = 1; m <= 20; m++) { const b = Math.floor(t) - m * 60; if (b < 0) break; let h = -Infinity, l = Infinity; for (let j = b; j < b + 60; j++) { if (s.h[j] > h) h = s.h[j]; if (s.l[j] < l) l = s.l[j]; } sum += h - l; n++; }
    return n ? sum / n : 8;
  }

  // ------------------------------------------------------------------ account & orders
  function resetAcct() {
    S.acct = { pos: 0, avg: 0, realized: 0, fees: 0, peak: 0, dd: 0, equity: [] };
    S.orders = []; S.fills = []; S.trades = []; S.cur = null; S.oid = 1; S.ended = false;
  }
  function openPnl(k) { const a = S.acct; return a.pos ? (k - a.avg) * a.pos * S.sess.tickValue : 0; }
  function marketFill(side, qty, tag) {
    const s = snapSec(), D = S.sess.book.D, bk = side > 0 ? S.sess.book.a : S.sess.book.b;
    const best = side > 0 ? S.sess.sec.ask[s] : S.sess.sec.bid[s];
    let left = qty, cost = 0, d = 0;
    while (left > 0 && d < D) { const avail = Math.max(1, Math.floor(bk[s * D + d])); const take = Math.min(left, avail); cost += take * (best + side * d); left -= take; d++; }
    if (left > 0) { cost += left * (best + side * D); }
    applyFill(side, qty, cost / qty, tag || "market");
  }
  function applyFill(side, qty, k, tag) {
    const a = S.acct, t = S.simT;
    S.fills.push({ t, side, qty, k, tag });
    a.fees += FEE * qty;
    const before = a.pos;
    if (!S.cur && before === 0) startTrade(side);
    const tr = S.cur;
    if (tr) { (Math.sign(before) === side || before === 0 ? tr.entries : tr.exits).push({ t, qty, k, tag }); if (before !== 0 && Math.sign(before) === side && openPnl(lastK()) < 0) tr.addedToLoser = true; if (before !== 0 && Math.sign(before) === side) tr.adds++; }
    let q = qty;
    if (a.pos !== 0 && Math.sign(a.pos) !== side) {           // reducing / closing / flipping
      const close = Math.min(q, Math.abs(a.pos));
      a.realized += (k - a.avg) * Math.sign(a.pos) * close * S.sess.tickValue;
      a.pos += side * close; q -= close;
      if (a.pos === 0) { a.avg = 0; endTrade(tag); }
    }
    if (q > 0) { a.avg = a.pos === 0 ? k : (a.avg * Math.abs(a.pos) + k * q) / (Math.abs(a.pos) + q); if (a.pos === 0 && !S.cur) startTrade(side); a.pos += side * q; if (S.cur && S.cur.entries.length === 0) S.cur.entries.push({ t, qty: q, k, tag }); }
    if (S.cur) S.cur.maxPos = Math.max(S.cur.maxPos, Math.abs(a.pos));
    syncBracket();
    ui(true);
  }
  function startTrade(side) {
    S.cur = { id: S.trades.length + 1, side, openT: S.simT, entries: [], exits: [], adds: 0, maxPos: 0, mae: 0, mfe: 0, stopMoves: [], initStop: null, initTarget: null, realizedAt0: S.acct.realized, feesAt0: S.acct.fees, ctx: context(side) };
  }
  function endTrade(tag) {
    const tr = S.cur; if (!tr) return;
    tr.closeT = S.simT; tr.exitTag = tag;
    tr.pnl = S.acct.realized - tr.realizedAt0; tr.fees = S.acct.fees - tr.feesAt0 + FEE * 0; tr.net = tr.pnl - (S.acct.fees - tr.feesAt0);
    const eq = (xs) => { const q = xs.reduce((a, x) => a + x.qty, 0); return q ? xs.reduce((a, x) => a + x.k * x.qty, 0) / q : null; };
    tr.entryK = eq(tr.entries); tr.exitK = eq(tr.exits); tr.qty = tr.entries.reduce((a, x) => a + x.qty, 0);
    tr.riskTicks = tr.initStop !== null ? Math.abs(tr.entries[0].k - tr.initStop) : null;
    tr.R = tr.riskTicks ? (tr.pnl / (tr.riskTicks * S.sess.tickValue * (tr.entries[0].qty || 1))) : null;
    S.trades.push(tr); S.cur = null;
    S.orders = S.orders.filter((o) => o.tag !== "stop" && o.tag !== "target");
    if (tr.net < 0) S.lastLossT = S.simT;
    feed(`${tr.net >= 0 ? "✅" : "❌"} Trade #${tr.id} ${tr.side > 0 ? "LONG" : "SHORT"} ${money(tr.net)}${tr.R !== null ? ` (${tr.R.toFixed(1)}R)` : ""} · ${tag}`);
  }
  function syncBracket() {
    const a = S.acct;
    if ((!PREF.bracket && !S.planBracket) || a.pos === 0) { if (a.pos === 0) S.orders = S.orders.filter((o) => o.tag !== "stop" && o.tag !== "target"); return; }
    const side = Math.sign(a.pos), q = Math.abs(a.pos);
    let st = S.orders.find((o) => o.tag === "stop"), tg = S.orders.find((o) => o.tag === "target");
    const plan = S.planBracket && S.planBracket.side === side ? S.planBracket : null; if (!st && !tg) S.planBracket = null;
    if (!st) { st = { id: S.oid++, type: "STP", side: -side, k: plan && !S.orders.some((o) => o.tag === "stop") ? plan.stop : Math.round(a.avg - side * PREF.stopT), qty: q, tag: "stop" }; S.orders.push(st); if (S.cur && S.cur.initStop === null) S.cur.initStop = st.k; }
    if (!tg) { tg = { id: S.oid++, type: "LMT", side: -side, k: plan ? plan.target : Math.round(a.avg + side * PREF.tgtT), qty: q, tag: "target", queue: 0 }; S.orders.push(tg); if (S.cur && S.cur.initTarget === null) S.cur.initTarget = tg.k; }
    st.qty = q; tg.qty = q; st.side = -side; tg.side = -side;
  }
  function placeLimit(side, k, qty) {
    const s = snapSec(), bb = S.sess.sec.bid[s], ba = S.sess.sec.ask[s];
    if ((side > 0 && k >= ba) || (side < 0 && k <= bb)) { marketFill(side, qty || PREF.qty, "limit (marketable)"); return; }
    const D = S.sess.book.D, dist = side > 0 ? bb - k : k - ba;
    const queue = dist < D ? (side > 0 ? S.sess.book.b : S.sess.book.a)[s * D + dist] : 40;
    S.orders.push({ id: S.oid++, type: "LMT", side, k, qty: qty || PREF.qty, tag: "entry", queue: queue + 0.5 });
    ui(true);
  }
  function moveOrder(o, k) {
    if (o.tag === "stop" && S.cur) { const side = Math.sign(S.acct.pos); const wider = side > 0 ? k < o.k : k > o.k; S.cur.stopMoves.push({ t: S.simT, from: o.k, to: k, wider }); }
    o.k = k;
  }
  function flatten(tag) {
    S.orders = S.orders.filter((o) => o.tag === "stop" || o.tag === "target" ? false : o.tag !== "entry");
    if (S.acct.pos) marketFill(-Math.sign(S.acct.pos), Math.abs(S.acct.pos), tag || "flatten");
  }
  // every new print: queue position for limits, triggers for stops, excursions
  function onPrint(j) {
    const p = S.sess.prints, k = p.k[j], q = p.q[j], side = p.side[j];
    const vi = k - S.volBase; if (vi >= 0 && vi < 12000) { if (side > 0) S.volAt.b[vi] += q; else S.volAt.s[vi] += q; }
    for (const o of S.orders.slice()) {
      if (!S.orders.includes(o)) continue;
      if (o.type === "LMT") {
        const through = o.side > 0 ? k < o.k : k > o.k, at = k === o.k && side === -o.side;
        if (at) o.queue -= q;
        if (through || (at && o.queue < 0)) { S.orders = S.orders.filter((x) => x !== o); if (o.tag === "target") S.orders = S.orders.filter((x) => x.tag !== "stop"); applyFill(o.side, o.qty, o.k, o.tag === "target" ? "target" : "limit"); }
      } else if (o.type === "STP") {
        if ((o.side > 0 && k >= o.k) || (o.side < 0 && k <= o.k)) { S.orders = S.orders.filter((x) => x !== o && x.tag !== "target"); applyFill(o.side, o.qty, k, "stop"); }
      }
    }
    if (S.cur && S.acct.pos) { const ex = (k - S.acct.avg) * Math.sign(S.acct.pos); S.cur.mfe = Math.max(S.cur.mfe, ex); S.cur.mae = Math.min(S.cur.mae, ex); }
  }

  // ------------------------------------------------------------------ market context at a moment (for grading and setups)
  function context(side, t0) {
    const t = Math.floor(t0 === undefined ? S.simT : t0), s = S.sess.sec, lv = S.sess.levels, k = t0 === undefined ? lastK() : s.c[Math.max(0, t - 1)];
    const at = (x) => s.c[Math.max(0, Math.min(S.sess.N - 1, x))];
    const atr = atr1m(t);
    const trend15 = at(t) - at(t - 900), mom60 = at(t) - at(t - 60);
    let d60 = 0, cvd5 = 0; for (let j = Math.max(0, t - 300); j < t; j++) { const x = s.bv[j] - s.sv[j]; cvd5 += x; if (j >= t - 60) d60 += x; }
    const px5 = at(t) - at(t - 300);
    const vw = vwapAt(t);
    const vwDist = vw ? (k - vw.m) : 0, vwSig = vw && vw.sd ? vwDist / vw.sd : 0;
    const gpos = lv.zeroGamma !== null && k >= lv.zeroGamma;
    const lvls = [["call wall", lv.callWall], ["put wall", lv.putWall], ["zero gamma", lv.zeroGamma], ["HVL", lv.hvl]].filter((x) => x[1] !== null);
    const near = lvls.map(([n, x]) => ({ n, x, d: x - k })).sort((a, b) => Math.abs(a.d) - Math.abs(b.d))[0];
    // book: a wall ahead in the trade direction or behind it, within 10 ticks
    const sb = Math.max(0, t - 1), D = S.sess.book.D, ahead = side > 0 ? S.sess.book.a : S.sess.book.b, behind = side > 0 ? S.sess.book.b : S.sess.book.a;
    let med = []; for (let d = 0; d < D; d++) med.push(ahead[sb * D + d], behind[sb * D + d]); med.sort((a, b) => a - b); const m = med[Math.floor(med.length / 2)] || 20;
    let wallAhead = null, wallBehind = null;
    for (let d = 0; d < 10; d++) { if (!wallAhead && ahead[sb * D + d] > 5 * m) wallAhead = { d, size: Math.round(ahead[sb * D + d]) }; if (!wallBehind && behind[sb * D + d] > 5 * m) wallBehind = { d, size: Math.round(behind[sb * D + d]) }; }
    // absorption in the last 2 minutes: one price took a big share of volume while price went nowhere, against the aggressors there
    const p = S.sess.prints, a0 = S.sess.secStart[Math.max(0, t - 120)], a1 = S.sess.secStart[t];
    const byK = new Map(); let tot = 0, hi = -Infinity, lo = Infinity;
    for (let j = a0; j < a1; j++) { const x = byK.get(p.k[j]) || [0, 0]; x[p.side[j] > 0 ? 0 : 1] += p.q[j]; byK.set(p.k[j], x); tot += p.q[j]; hi = Math.max(hi, p.k[j]); lo = Math.min(lo, p.k[j]); }
    let absorb = null;
    for (const [kk, [bv, sv]] of byK) if (tot > 0 && (bv + sv) / tot > 0.22 && hi - lo < Math.max(8, atr) && (bv + sv) > 400) { const absorberSide = sv > bv ? 1 : -1; absorb = { k: kk, side: absorberSide, vol: Math.round(bv + sv) }; }
    const hi30 = (() => { let x = -Infinity; for (let j = Math.max(0, t - 1800); j < t - 60; j++) x = Math.max(x, s.h[j]); return x; })();
    const lo30 = (() => { let x = Infinity; for (let j = Math.max(0, t - 1800); j < t - 60; j++) x = Math.min(x, s.l[j]); return x; })();
    const recentSweep = S.sess.truth.find((e) => e.type === "sweep" && t - e.t >= 0 && t - e.t < 420);
    const ev = S.sess.truth.filter((e) => Math.abs(e.t - t) < 300).map((e) => e.type);
    const mark = atMark(k, t);
    return { t, k, mark, atr, trend15, mom60, d60, cvd5, px5, vwDist, vwSig, gpos, near, wallAhead, wallBehind, absorb, hi30, lo30, recentSweep: !!recentSweep, sweepDir: recentSweep ? recentSweep.dir : 0, ev,
      withTrend: Math.sign(trend15) === side && Math.abs(trend15) > atr, againstTrend: Math.sign(trend15) === -side && Math.abs(trend15) > 1.5 * atr,
      chase: mom60 * side > 1.3 * atr, divergence: Math.abs(px5) > atr && Math.sign(px5) !== Math.sign(cvd5) && Math.abs(cvd5) > 300 };
  }

  // ------------------------------------------------------------------ build the page
  function build() {
    const el = $("#view-sim");
    el.style.zoom = String(1 / nz());
    el.innerHTML = `
      <div class="sm-top">
        <b class="sm-brand"><span class="sm-logo">◆</span><span class="sm-bt">JORDAN<em>SIM</em></span></b>
        <span class="sm-open" title="Every session is the New York cash open, 09:30 to 11:30 ET"><i></i>NY OPEN · 09:30–11:30</span>
        <select id="sm-scn">${Object.entries(E().SCENARIOS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
        <label class="dim">SEED <input id="sm-seed" type="number" min="1" style="width:84px"></label>
        <button class="sm-b" id="sm-new" title="A new random market">NEW</button>
        <button class="sm-b" id="sm-again" title="The same market again from the open">REPLAY SEED</button>
        <span class="sm-sep"></span>
        <button class="sm-play" id="sm-play" title="Play / pause (space)"><i>▶</i><span>PLAY</span></button>
        <span id="sm-speeds">${SPEEDS.map((x) => `<button class="sm-chip" data-sp="${x}">${x}×</button>`).join("")}</span>
        <span class="sm-clock" id="sm-clock">09:30:00</span>
        <span class="sm-sep"></span>
        <span id="sm-tfs">${TFS.map(([s, l]) => `<button class="sm-chip" data-tf="${s}">${l}</button>`).join("")}</span>
        <span class="sm-sep"></span>
        <span class="sm-seg3" id="sm-themes"><button data-th="tv" title="TradingView light">LIGHT</button><button data-th="jordan" title="JORDAN black and orange">JORDAN</button><button data-th="dark" title="Terminal dark with heatmap">DARK</button></span>
        ${["heat:▦ HEATMAP", "vol:▮ VOLUME", "bubbles:● BUBBLES", "vwap:∿ VWAP", "gamma:γ GAMMA", "fvg:▭ FVG", "levels:≡ H/L", "htf:▥ HTF", "profile:⫴ PROFILE", "hints:✦ HINTS"].map((x) => { const [k, l] = x.split(":"); return `<button class="sm-chip sm-tg" data-tg="${k}">${l}</button>`; }).join("")}
        <span class="sp"></span>
        <button class="sm-b" id="sm-learn">LEARN</button>
        <button class="sm-b sm-rep" id="sm-report">END &amp; REPORT</button>
      </div>
      <div class="sm-main">
        <div class="sm-left">
          <div class="sm-chart"><canvas id="sm-cv"></canvas>${toolbarHtml()}<button class="sm-live" id="sm-live">● LIVE</button><div class="sm-ev" id="sm-ev"></div></div>
          <div class="sm-sub"><canvas id="sm-cv2"></canvas></div>
        </div>
        <div class="sm-right">
          <div class="sm-dom"><canvas id="sm-dom"></canvas></div>
          <div class="sm-ord">
            <div class="sm-row"><span class="dim">QTY</span>${[1, 2, 3, 5, 10].map((q) => `<button class="sm-chip" data-q="${q}">${q}</button>`).join("")}</div>
            <div class="sm-row"><button class="sm-buy" id="sm-buy">BUY MKT <small>B</small></button><button class="sm-sell" id="sm-sell">SELL MKT <small>S</small></button></div>
            <div class="sm-row"><button class="sm-b" id="sm-flat">FLATTEN <small>F</small></button><button class="sm-b" id="sm-rev">REVERSE <small>R</small></button><button class="sm-b" id="sm-cxl">CANCEL <small>C</small></button></div>
            <div class="sm-row"><label><input type="checkbox" id="sm-br"> BRACKET</label><span class="dim">STOP</span><input id="sm-st" type="number" min="1" style="width:46px"><span class="dim">TGT</span><input id="sm-tg" type="number" min="1" style="width:46px"><span class="dim">ticks</span></div>
            <div class="sm-acct" id="sm-acct"></div>
          </div>
        </div>
      </div>
      <div class="sm-bottom">
        <div class="sm-box"><div class="sm-h">TIME &amp; SALES <span class="dim">min</span> <input id="sm-minp" type="number" min="1" style="width:46px"></div><div id="sm-tape" class="sm-tape"></div></div>
        <div class="sm-box"><div class="sm-h">GAMMA · DEALER POSITIONING <span id="sm-greg" class="dim"></span></div><canvas id="sm-gex"></canvas></div>
        <div class="sm-box"><div class="sm-h">COACH FEED</div><div id="sm-feed" class="sm-feed"></div></div>
      </div>
      <div class="sm-modal" id="sm-modal" hidden></div>`;
    const sel = $("#sm-scn"); sel.value = S.scenario;
    $("#sm-new").onclick = () => start(Math.floor(Math.random() * 999999) + 1, sel.value);
    $("#sm-again").onclick = () => start(+$("#sm-seed").value || S.seed, sel.value);
    sel.onchange = () => start(Math.floor(Math.random() * 999999) + 1, sel.value);
    $("#sm-play").onclick = () => setPlay(!S.playing);
    el.querySelectorAll("[data-sp]").forEach((b) => (b.onclick = () => { S.speed = +b.dataset.sp; PREF.speed = S.speed; savePref(); chips(); }));
    el.querySelectorAll("[data-tf]").forEach((b) => (b.onclick = () => { S.tf = +b.dataset.tf; PREF.tf = S.tf; S.view.span = Math.max(S.view.span, S.tf * 40); savePref(); chips(); }));
    el.querySelectorAll("[data-tg]").forEach((b) => (b.onclick = () => { PREF[b.dataset.tg] = !PREF[b.dataset.tg]; savePref(); chips(); }));
    el.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => { PREF.qty = +b.dataset.q; savePref(); chips(); }));
    $("#sm-buy").onclick = () => trade(1); $("#sm-sell").onclick = () => trade(-1);
    $("#sm-flat").onclick = () => flatten("flatten"); $("#sm-rev").onclick = reverse; $("#sm-cxl").onclick = () => { S.orders = S.orders.filter((o) => o.tag !== "entry"); S.planBracket = null; ui(true); };
    const br = $("#sm-br"), st = $("#sm-st"), tg = $("#sm-tg"), mp = $("#sm-minp");
    br.checked = PREF.bracket; st.value = PREF.stopT; tg.value = PREF.tgtT; mp.value = PREF.minPrint;
    br.onchange = () => { PREF.bracket = br.checked; savePref(); syncBracket(); };
    st.onchange = () => { PREF.stopT = Math.max(1, +st.value || 16); savePref(); };
    tg.onchange = () => { PREF.tgtT = Math.max(1, +tg.value || 32); savePref(); };
    mp.onchange = () => { PREF.minPrint = Math.max(1, +mp.value || 1); savePref(); };
    $("#sm-live").onclick = () => { S.view.follow = true; S.view.yOff = 0; S.view.yZoom = 1; };
    $("#sm-report").onclick = () => { setPlay(false); report(); };
    $("#sm-learn").onclick = learn;
    el.querySelectorAll("[data-th]").forEach((b) => (b.onclick = () => { PREF.theme = b.dataset.th; if (PREF.theme === "dark") PREF.heat = true; savePref(); applyTheme(); }));
    applyTheme();
    chartEvents($("#sm-cv")); domEvents($("#sm-dom")); wireToolbar();
    document.addEventListener("keydown", keys);
    new ResizeObserver(() => fit()).observe(el);
    S.built = true;
    chips();
    start(Math.floor(Math.random() * 999999) + 1, S.scenario);
    loop();
  }
  const savePref = () => lset("nx_sim_pref", PREF);
  function applyTheme() { const el = $("#view-sim"); if (el) { el.classList.toggle("sm-light", PREF.theme === "tv"); el.dataset.theme = PREF.theme; el.querySelectorAll("[data-th]").forEach((b) => b.classList.toggle("on", b.dataset.th === PREF.theme)); } chips(); S.gexCache = null; }
  function chips() {
    const el = $("#view-sim"); if (!el) return;
    el.querySelectorAll("[data-sp]").forEach((b) => b.classList.toggle("on", +b.dataset.sp === S.speed));
    el.querySelectorAll("[data-tf]").forEach((b) => b.classList.toggle("on", +b.dataset.tf === S.tf));
    el.querySelectorAll("[data-tg]").forEach((b) => b.classList.toggle("on", !!PREF[b.dataset.tg]));
    el.querySelectorAll("[data-q]").forEach((b) => b.classList.toggle("on", +b.dataset.q === PREF.qty));
  }
  function keys(e) {
    if (!S.active || /INPUT|SELECT|TEXTAREA/.test((e.target || {}).tagName || "") || !$("#sm-modal").hidden) return;
    if ($("#sm-pmod") && !$("#sm-pmod").hidden && e.key !== "Escape") return;
    const k = e.key.toLowerCase();
    if ((k === "delete" || k === "backspace") && D.sel) { e.preventDefault(); D.items = D.items.filter((x) => x !== D.sel); D.sel = null; dsave(); selBar(); return; }
    if (k === "escape" && $("#sm-pmod") && !$("#sm-pmod").hidden) { closeModal(); return; }
    if (k === "escape") { D.temp = null; D.mode = null; D.sel = null; selBar(); setTool("cursor"); $("#sm-dpanel").hidden = true; return; }
    if (k === "enter" && D.temp && D.mode === "clicks" && D.temp.type === "path") { D.temp.p.pop(); finish(D.temp); return; }
    if (e.altKey) { const m = { t: "line", h: "hline", f: "fib", r: "rect", l: "long", s: "short", v: "vline", c: "channel", p: "pricerange", g: "gzone" }[k]; if (m) { e.preventDefault(); setTool(m); return; } }
    if (k === " ") { e.preventDefault(); setPlay(!S.playing); }
    else if (k === "b") trade(1); else if (k === "s") trade(-1); else if (k === "f") flatten("flatten"); else if (k === "r") reverse();
    else if (k === "c") { S.orders = S.orders.filter((o) => o.tag !== "entry"); S.planBracket = null; ui(true); }
    else if (k === "arrowup" || k === "arrowdown") { const i = SPEEDS.indexOf(S.speed); S.speed = SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, i + (k === "arrowup" ? 1 : -1)))]; chips(); }
  }
  function trade(side) { if (!S.sess || S.ended) return; marketFill(side, PREF.qty, "market"); }
  function reverse() { if (!S.acct.pos) return; const q = Math.abs(S.acct.pos) * 2, side = -Math.sign(S.acct.pos); S.orders = S.orders.filter((o) => o.tag === "entry"); marketFill(side, q, "reverse"); }
  function setPlay(on) { S.playing = on && !S.ended; const b = $("#sm-play"); if (b) { b.innerHTML = S.playing ? "<i>❚❚</i><span>PAUSE</span>" : "<i>▶</i><span>PLAY</span>"; b.classList.toggle("on", S.playing); } S.last = performance.now(); }

  function start(seed, scenario) {
    setPlay(false);
    S.seed = seed; S.scenario = scenario;
    const t0 = performance.now();
    S.sess = E().generate({ seed, scenario, minutes: OPEN_MIN });
    prep(); resetAcct(); dload();
    S.tf = 900; PREF.tf = 900; savePref();   // train on 15-minute candles
    S.simT = FIRST; S.pIdx = 0; S.hintsShown = new Set(); S.feed = []; S.gexCache = null; S.view = { span: OPEN_MIN * 60 + 1800, right: FIRST, follow: true, yZoom: 1, yOff: 0 };
    advanceTo(FIRST);
    $("#sm-seed").value = seed;
    feed(`🔔 NY OPEN 09:30–11:30 ET · seed ${seed} · ${S.sess.scenarioName.split(" (")[0]}${scenario === "random" ? " (hidden)" : ""} · generated in ${Math.round(performance.now() - t0)} ms. Press PLAY (space).`);
    feed("🕯 The first 15-minute candle (09:30–09:45) is done: its high and low are marked ORH / ORL. Build your setup from here.");
    if (scenario === "random") feed("The day type is hidden: read it from the tape.");
    const lv = S.sess.levels;
    feed(`Gamma: call wall ${P(lv.callWall)} · put wall ${P(lv.putWall)} · zero gamma ${P(lv.zeroGamma)} · ${lastK() >= lv.zeroGamma ? "positive gamma (dealers dampen moves)" : "negative gamma (dealers amplify moves)"}`);
    fit(); ui(true);
  }
  function advanceTo(t) {
    const p = S.sess.prints;
    t = Math.min(S.sess.N, t);
    while (S.pIdx < p.n && p.t[S.pIdx] <= t) { onPrint(S.pIdx); S.pIdx++; }
    S.simT = t;
    if (PREF.hints) for (const e of S.sess.truth) if (e.t <= t && !S.hintsShown.has(e)) { S.hintsShown.add(e); if (t - e.t < 600) hint(e); }
    if (t >= S.sess.N && !S.ended) { if (S.acct.pos) flatten("end of session"); S.ended = true; setPlay(false); feed("🔔 Session over."); setTimeout(report, 400); }
  }
  function hint(e) {
    const tips = { sweep: "Watch for the reclaim: if price gets back inside the range on falling delta, the stop run failed.", absorption: "Heavy delta without progress: someone passive is taking the other side. Wait for price to leave the level.",
      news: "Book is thin: slippage is bigger and moves overshoot. Let the first spike settle.", spoof: "A big wall that disappears as price nears is not real support / resistance.", squeeze: "Below zero gamma, dealers buy strength: momentum is in charge until the call wall.", stops: "Stops just triggered: does price accelerate (real breakout) or stall (failed)?" };
    feed(`📘 ${clock(e.t)} ${e.note}. ${tips[e.type] || ""}`, "hint");
  }
  function feed(txt, cls) { S.feed.unshift({ txt, cls, t: S.sess ? S.simT : 0 }); S.feed = S.feed.slice(0, 60); const el = $("#sm-feed"); if (el) el.innerHTML = S.feed.map((f) => `<div class="${f.cls || ""}"><span class="dim">${clock(f.t)}</span> ${esc(f.txt)}</div>`).join(""); }

  // ------------------------------------------------------------------ main loop
  function loop() {
    cancelAnimationFrame(S.raf);
    const step = (now) => {
      S.raf = requestAnimationFrame(step);
      if (!S.active || !S.sess) { S.last = now; return; }
      const dt = Math.min(0.25, (now - S.last) / 1000); S.last = now;
      if (S.playing) advanceTo(S.simT + dt * S.speed);
      draw();
      if (now - S.lastUi > 120) { S.lastUi = now; ui(); }
    };
    S.raf = requestAnimationFrame(step);
  }
  function fit() {
    for (const id of ["sm-cv", "sm-cv2", "sm-dom", "sm-gex"]) {
      const c = $("#" + id); if (!c) continue;
      const r = c.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      const w = Math.max(50, c.parentElement.clientWidth), h = Math.max(40, c.parentElement.clientHeight - (id === "sm-gex" ? 22 : 0));
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); c.style.width = w + "px"; c.style.height = h + "px"; c._w = w; c._h = h; c._dpr = dpr; void r;
    }
  }

  // ------------------------------------------------------------------ drawing: main chart
  // themes: "tv" = the TradingView light look (beige, dark bear candles, light bull candles); "dark" = terminal look with a full-colour heatmap
  const THEMES = {
    tv: { bg: "#d2c9bb", grid: "rgba(40,36,30,0.07)", sep: "rgba(40,36,30,0.35)", text: "#2b2a2f", dim: "#6f685e", bull: "#cfcfd3", bear: "#3b3a42", edge: "#232227", wick: "#232227",
      volUp: "rgba(78,160,140,0.55)", volDn: "rgba(210,104,96,0.55)", axisBox: "#b8afa1", last: "#3b3a42", lastText: "#f2efe9", vwap: "#c2620a", bubUp: "rgba(60,150,130,0.45)", bubDn: "rgba(200,80,70,0.45)",
      bubUpE: "#2c7a69", bubDnE: "#a8392f", heat: [30, 58, 110], heatA: 0.5, level: "#3b3a42", fvg: "rgba(60,56,70,0.16)", fvgE: "rgba(60,56,70,0.45)", pos: "rgba(70,70,80,0.18)", win: "rgba(60,150,130,0.22)", loss: "rgba(200,80,70,0.2)",
      buy: "#2f6fe0", sell: "#d6372e", water: "rgba(40,36,30,0.09)", subBg: "#d2c9bb", domBg: "#0d0d10", domAlt: "#111115", domText: "#d6d6de", domBid: "rgba(47,111,224,0.38)", domAsk: "rgba(240,70,60,0.34)", cross: "rgba(40,36,30,0.45)" },
    jordan: { bg: "#0a0a0c", grid: "rgba(255,255,255,0.045)", sep: "rgba(255,160,40,0.22)", text: "#b9b9c4", dim: "#6d6d78", bull: "#f1f1f4", bear: "#ff8a1f", edge: null, wick: "#8a8a96",
      volUp: "rgba(241,241,244,0.22)", volDn: "rgba(255,138,31,0.38)", axisBox: "#1c1c22", last: "#ffa028", lastText: "#120a00", vwap: "#4fc3f7", bubUp: "rgba(241,241,244,0.25)", bubDn: "rgba(255,138,31,0.35)",
      bubUpE: "#f1f1f4", bubDnE: "#ff8a1f", heat: null, heatA: 0.75, level: "#d6d6de", fvg: "rgba(255,160,40,0.10)", fvgE: "rgba(255,160,40,0.55)", pos: "rgba(255,160,40,0.10)", win: "rgba(34,192,143,0.16)", loss: "rgba(255,77,94,0.16)",
      buy: "#4f8dff", sell: "#ff4d5e", water: "rgba(255,160,40,0.05)", subBg: "#0a0a0c", domBg: "#0d0d10", domAlt: "#111115", domText: "#d6d6de", domBid: "rgba(79,141,255,0.38)", domAsk: "rgba(255,77,94,0.34)", cross: "rgba(255,160,40,0.5)" },
    dark: { bg: "#05070d", grid: "rgba(255,255,255,0.05)", sep: "rgba(255,255,255,0.18)", text: "#c9ced6", dim: "#7d8592", bull: "rgba(43,224,126,0.85)", bear: "rgba(255,74,74,0.85)", edge: null, wick: null,
      volUp: "rgba(43,224,126,0.35)", volDn: "rgba(255,74,74,0.35)", axisBox: "#333", last: "#ffa028", lastText: "#000", vwap: "#ffa028", bubUp: "rgba(43,224,126,0.35)", bubDn: "rgba(255,74,74,0.35)",
      bubUpE: "#2be07e", bubDnE: "#ff4a4a", heat: null, heatA: 0.85, level: "#c9ced6", fvg: "rgba(197,139,255,0.12)", fvgE: "rgba(197,139,255,0.5)", pos: "rgba(90,169,255,0.12)", win: "rgba(43,224,126,0.14)", loss: "rgba(255,74,74,0.14)",
      buy: "#5aa9ff", sell: "#ff9f43", water: "rgba(255,255,255,0.05)", subBg: "#05070d", domBg: "#06080e", domAlt: "#0a0e16", domText: "#c9ced6", domBid: "rgba(43,140,255,0.35)", domAsk: "rgba(255,74,74,0.32)", cross: "rgba(255,160,40,0.45)" },
  };
  const TH = () => { const t = THEMES[PREF.theme] ? PREF.theme : "tv", base = THEMES[t], cu = (PREF.custom || {})[t]; return cu ? { ...base, ...Object.fromEntries(Object.entries(cu).filter(([, v]) => v !== undefined && v !== "")) } : base; };
  const PAL = (() => { const stops = [[0, [5, 8, 15]], [0.18, [8, 30, 60]], [0.4, [16, 90, 160]], [0.62, [30, 180, 220]], [0.8, [255, 210, 63]], [1, [255, 255, 255]]]; const lut = new Uint8ClampedArray(256 * 3); for (let i = 0; i < 256; i++) { const x = i / 255; let j = 0; while (j < stops.length - 2 && x > stops[j + 1][0]) j++; const [a, ca] = stops[j], [b, cb] = stops[j + 1], f = (x - a) / (b - a); for (let c = 0; c < 3; c++) lut[i * 3 + c] = ca[c] + (cb[c] - ca[c]) * f; } return lut; })();
  const heatCanvas = document.createElement("canvas");
  const HTFW = 118;  // width reserved on the right for the higher-timeframe candles
  function geom(c) {
    const w = c._w, h = c._h, padR = 70, padB = 20, htf = PREF.htf ? HTFW : 0;
    const lead = Math.max(S.tf * 1.3, S.view.span * 0.07);  // room before 09:30 so the first candle clears the drawing toolbar
    const right = S.view.follow ? Math.max(S.simT + S.view.span * 0.06, S.view.span - lead) : S.view.right;
    const left = right - S.view.span;
    if (S.view.follow) S.view.right = right;
    const tf = S.tf, i0 = Math.max(0, Math.floor(left / tf)), i1 = Math.min(Math.floor(S.simT / tf), candles(tf).n - 1);
    let hi = -Infinity, lo = Infinity;
    for (let i = i0; i <= i1; i++) { const cd = liveCandle(i, tf); if (!cd) continue; if (cd.h > hi) hi = cd.h; if (cd.l < lo) lo = cd.l; }
    if (!isFinite(hi)) { hi = lastK() + 20; lo = lastK() - 20; }
    const volH = PREF.vol ? 0.18 : 0;   // bottom share of the chart used by volume bars
    const span = Math.max(16, hi - lo), mid0 = (hi + lo) / 2 - span * volH * 0.6, half = (span / 2) * (1.14 + volH * 1.3) / S.view.yZoom, mid = mid0 + S.view.yOff;
    const pMax = mid + half, pMin = mid - half, plotW = w - padR - htf;
    return { w, h, padR, padB, htf, plotW, left, right, pMax, pMin, x: (t) => ((t - left) / (right - left)) * plotW, y: (k) => ((pMax - k) / (pMax - pMin)) * (h - padB), k: (y) => pMax - (y / (h - padB)) * (pMax - pMin), t: (x) => left + (x / plotW) * (right - left) };
  }
  // text on a slanted line: a rounded pill rotated to the line's angle, placed at the start / middle / end between the two anchors
  function slantLabel(ctx, ax, ay, bx, by, text, col, o = {}) {
    if (!text) return;
    let ang = Math.atan2(by - ay, bx - ax);
    if (ang > Math.PI / 2) ang -= Math.PI; else if (ang < -Math.PI / 2) ang += Math.PI;   // never upside down
    const pos = o.pos || "center", f = pos === "left" ? 0.12 : pos === "right" ? 0.88 : 0.5;
    const x = ax + (bx - ax) * f, y = ay + (by - ay) * f;
    ctx.save(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    ctx.translate(x, y); ctx.rotate(ang);
    ctx.font = o.font || "700 12px Inter, Segoe UI, Arial, sans-serif";
    const lines = String(text).split("\n"), fs = o.size || 12, w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 18, h = lines.length * (fs + 4) + 8;
    const dy = o.style === "above" ? -(h / 2 + 4) : 0;
    if (o.style !== "plain" && o.style !== "above") {
      ctx.shadowColor = "rgba(0,0,0,0.35)"; ctx.shadowBlur = 6; ctx.shadowOffsetY = 1;
      ctx.fillStyle = col; roundRect(ctx, -w / 2, -h / 2 + dy, w, h, h / 2); ctx.fill();
      ctx.shadowColor = "transparent"; ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 1; roundRect(ctx, -w / 2 + 0.5, -h / 2 + dy + 0.5, w - 1, h - 1, h / 2); ctx.stroke();
    }
    const lum = (() => { const m = String(col).match(/#([0-9a-f]{6})/i); if (!m) { const r = String(col).match(/(\d+)\D+(\d+)\D+(\d+)/); return r ? (0.299 * r[1] + 0.587 * r[2] + 0.114 * r[3]) / 255 : 0.5; } const n = parseInt(m[1], 16); return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; })();
    ctx.fillStyle = o.style === "plain" || o.style === "above" ? (o.textColor || col) : (o.textColor || (lum > 0.62 ? "#111114" : "#fff"));  // dark text on a light badge
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    lines.forEach((l, i) => ctx.fillText(l, 0, dy - h / 2 + 4 + (fs + 4) * (i + 0.5)));
    ctx.restore();
  }

  // a horizontal line with its label centred on it (TradingView style: the line breaks around the text)
  function labelLine(ctx, x0, x1, y, text, col, dash) {
    ctx.save(); ctx.font = "600 12px Inter, Segoe UI, Arial, sans-serif"; ctx.textBaseline = "middle";
    const tw = ctx.measureText(text).width, xm = (x0 + x1) / 2;
    ctx.strokeStyle = col; ctx.lineWidth = 1.2; ctx.setLineDash(dash || []);
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(xm - tw / 2 - 8, y); ctx.moveTo(xm + tw / 2 + 8, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = col; ctx.textAlign = "center"; ctx.fillText(text, xm, y); ctx.restore();
  }
  function axisTag(ctx, g, k, text, bg, fg) { const y = g.y(k); ctx.fillStyle = bg; ctx.fillRect(g.plotW + g.htf + 1, y - 10, g.padR - 2, 20); ctx.fillStyle = fg; ctx.font = "600 11px JetBrains Mono, Consolas, monospace"; ctx.textAlign = "left"; ctx.fillText(text, g.plotW + g.htf + 5, y); }
  function arrow(ctx, x, y, up, col, dbl) { // (x, y) is the tip; an up arrow's tail hangs below it, a down arrow's above it
    ctx.save(); ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round";
    const d = up ? 1 : -1, heads = dbl ? [0, 6] : [0];
    ctx.beginPath(); ctx.moveTo(x, y + d * 22); ctx.lineTo(x, y); ctx.stroke();
    for (const o of heads) { ctx.beginPath(); ctx.moveTo(x - 6, y + d * (7 + o)); ctx.lineTo(x, y + d * o); ctx.lineTo(x + 6, y + d * (7 + o)); ctx.stroke(); }
    ctx.restore();
  }
  function fvgs(tf, i0, i1) { // three-candle fair value gaps that are still open at the moment shown
    const out = [], c = candles(tf), minGap = 2, now = S.simT;
    for (let i = Math.max(1, i0 - 120); i <= i1 - 1; i++) {
      if ((i + 1) * tf + tf > now + tf) break;
      const a = liveCandle(i - 1, tf), b = liveCandle(i + 1, tf); if (!a || !b) continue;
      let box = null;
      if (b.l - a.h >= minGap) box = { dir: 1, top: b.l, bot: a.h };
      else if (a.l - b.h >= minGap) box = { dir: -1, top: a.l, bot: b.h };
      if (!box) continue;
      box.i = i - 1; box.end = null;
      for (let j = i + 2; j <= i1; j++) { const x = liveCandle(j, tf); if (!x) break; if ((box.dir > 0 && x.l <= box.bot) || (box.dir < 0 && x.h >= box.top)) { box.end = j; break; } if (box.dir > 0 && x.l < box.top) box.top = Math.max(box.bot, x.l); if (box.dir < 0 && x.h > box.bot) box.bot = Math.min(box.top, x.h); }
      if (box.end === null || box.end >= i0) out.push(box);
    }
    return out.slice(-25);
  }
  function draw() {
    const c = $("#sm-cv"); if (!c || !c._w) return;
    const ctx = c.getContext("2d"), g = geom(c), sess = S.sess, now = S.simT, th = TH();
    S.g = g;
    ctx.setTransform(c._dpr, 0, 0, c._dpr, 0, 0);
    ctx.fillStyle = th.bg; ctx.fillRect(0, 0, g.w, g.h);
    const plotW = g.plotW, plotH = g.h - g.padB, tf = S.tf;
    const tfName = (TFS.find((x) => x[0] === tf) || [0, tf + "s"])[1];
    // watermark
    ctx.save(); ctx.fillStyle = th.noWater ? "rgba(0,0,0,0)" : th.water; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `700 ${Math.round(Math.min(plotW / 6, 110))}px Inter, Segoe UI, Arial, sans-serif`; ctx.fillText(`SIM-ES, ${tfName}`, plotW / 2, plotH * 0.42);
    ctx.font = `500 ${Math.round(Math.min(plotW / 18, 40))}px Inter, Segoe UI, Arial, sans-serif`; ctx.fillText("E-mini S&P 500 · simulated", plotW / 2, plotH * 0.42 + Math.min(plotW / 9, 80)); ctx.restore();
    // liquidity heatmap
    if (PREF.heat) {
      const H = sess.heat, h0 = Math.max(0, Math.floor(g.left / H.step)), h1 = Math.min(Math.floor(now / H.step), H.n - 1);
      const kTop = Math.ceil(g.pMax), kBot = Math.floor(g.pMin), rows = Math.max(1, kTop - kBot + 1), cols = Math.max(1, h1 - h0 + 1);
      if (rows < 2000) {
        heatCanvas.width = cols; heatCanvas.height = rows;
        const hc = heatCanvas.getContext("2d"), img = hc.createImageData(cols, rows), W = 2 * H.half + 1, ref = Math.log1p(320);
        for (let ci = 0; ci < cols; ci++) {
          const hi = h0 + ci, m = H.mid[hi];
          for (let r = 0; r < rows; r++) {
            const j = kTop - r - m + H.half, v = j >= 0 && j < W ? H.v[hi * W + j] : 0;
            const x = Math.min(255, Math.round((Math.log1p(v) / ref) * 255)), o = (r * cols + ci) * 4;
            if (th.heat) { const a = Math.max(0, x - 70) / 185; img.data[o] = th.heat[0]; img.data[o + 1] = th.heat[1]; img.data[o + 2] = th.heat[2]; img.data[o + 3] = Math.round(a * a * 255); }
            else { img.data[o] = PAL[x * 3]; img.data[o + 1] = PAL[x * 3 + 1]; img.data[o + 2] = PAL[x * 3 + 2]; img.data[o + 3] = x < 30 ? x * 4 : 255; }
          }
        }
        hc.putImageData(img, 0, 0);
        ctx.save(); ctx.imageSmoothingEnabled = true; ctx.globalAlpha = th.heatA;
        ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();
        const x0 = g.x(h0 * H.step), x1 = Math.min(g.x((h1 + 1) * H.step), g.x(now));
        ctx.drawImage(heatCanvas, x0, g.y(kTop + 0.5), Math.max(1, x1 - x0), g.y(kBot - 0.5) - g.y(kTop + 0.5));
        ctx.restore();
      }
    }
    // grid: hour separators dashed, price grid faint
    ctx.font = "11px JetBrains Mono, Consolas, monospace"; ctx.textBaseline = "middle";
    const stepPts = niceStep((g.pMax - g.pMin) * T() / 9), stepK = Math.max(1, Math.round(stepPts / T()));
    ctx.strokeStyle = th.noGrid ? "rgba(0,0,0,0)" : th.grid; ctx.fillStyle = th.text;
    for (let k = Math.ceil(g.pMin / stepK) * stepK; k <= g.pMax; k += stepK) { const y = g.y(k); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke(); ctx.font = (k * T()) % (stepPts * 2) === 0 ? "700 12px JetBrains Mono, Consolas, monospace" : "12px JetBrains Mono, Consolas, monospace"; ctx.textAlign = "left"; ctx.fillText(P(k), plotW + g.htf + 6, y); }
    const tStep = niceTime(g.right - g.left);
    ctx.textAlign = "center"; ctx.font = "12px JetBrains Mono, Consolas, monospace";
    for (let t = Math.ceil(g.left / tStep) * tStep; t < g.right; t += tStep) { const x = g.x(t); if (x > plotW) continue; ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.fillStyle = th.text; ctx.fillText(clock(t).slice(0, tStep < 60 ? 8 : 5), x, g.h - 9); }
    for (let t = Math.ceil(g.left / 3600) * 3600 - 1800; t < g.right; t += 3600) { if (t <= 0) continue; const x = g.x(t); if (x < 0 || x > plotW) continue; ctx.strokeStyle = th.sep; ctx.setLineDash([5, 5]); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.setLineDash([]); }
    ctx.textAlign = "left";
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();
    // volume profile (session so far) on the right edge of the plot
    if (PREF.profile) {
      let mx = 1; for (let k = Math.floor(g.pMin); k <= g.pMax; k++) { const i = k - S.volBase; if (i >= 0 && i < 12000) mx = Math.max(mx, S.volAt.b[i] + S.volAt.s[i]); }
      for (let k = Math.floor(g.pMin); k <= g.pMax; k++) {
        const i = k - S.volBase; if (i < 0 || i >= 12000) continue; const b = S.volAt.b[i], s2 = S.volAt.s[i]; if (b + s2 <= 0) continue;
        const wpx = ((b + s2) / mx) * plotW * 0.1, y = g.y(k), hh = Math.max(1, Math.abs(g.y(k) - g.y(k + 1)) - 0.4);
        ctx.fillStyle = th.volUp; ctx.fillRect(plotW - wpx, y - hh / 2, wpx * (b / (b + s2)), hh);
        ctx.fillStyle = th.volDn; ctx.fillRect(plotW - wpx * (s2 / (b + s2)), y - hh / 2, wpx * (s2 / (b + s2)), hh);
      }
    }
    const cw = Math.max(1, (plotW / ((g.right - g.left) / tf)) * 0.72);
    const i0 = Math.max(0, Math.floor(g.left / tf)), i1 = Math.min(Math.floor(now / tf), candles(tf).n - 1);
    // volume bars at the bottom
    if (PREF.vol) {
      let mv = 1; const vs = []; for (let i = i0; i <= i1; i++) { const cd = liveCandle(i, tf); if (cd) { vs.push([i, cd]); mv = Math.max(mv, cd.v); } }
      const vh = plotH * 0.2;
      for (const [i, cd] of vs) { const x = g.x(i * tf + tf / 2), hh = (cd.v / mv) * vh; ctx.fillStyle = cd.c >= cd.o ? th.volUp : th.volDn; ctx.fillRect(x - cw / 2, plotH - hh, cw, hh); }
    }
    // fair value gaps
    if (PREF.fvg) {
      for (const b of fvgs(tf, i0, i1)) {
        const x0 = g.x(b.i * tf), x1 = b.end !== null ? g.x((b.end + 1) * tf) : g.x(now) + 30, y0 = g.y(b.top), y1 = g.y(b.bot);
        ctx.fillStyle = th.fvg; ctx.fillRect(x0, y0, x1 - x0, y1 - y0); ctx.strokeStyle = th.fvgE; ctx.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0);
        if (x1 - x0 > 34) { ctx.fillStyle = th.text; ctx.font = "italic 600 11px Inter, Segoe UI, Arial, sans-serif"; ctx.textAlign = "right"; ctx.fillText("FVG", x1 - 4, (y0 + y1) / 2); ctx.textAlign = "left"; }
      }
    }
    // session high / low so far, labelled on the line
    if (PREF.levels) {
      let hi = -Infinity, lo = Infinity, hiT = 0, loT = 0; const s = sess.sec;
      for (let t = 0; t < Math.floor(now); t++) { if (s.h[t] > hi) { hi = s.h[t]; hiT = t; } if (s.l[t] < lo) { lo = s.l[t]; loT = t; } }
      if (isFinite(hi) && now > 300) { labelLine(ctx, Math.max(0, g.x(hiT)), plotW, g.y(hi), "High", th.level); labelLine(ctx, Math.max(0, g.x(loT)), plotW, g.y(lo), "Low", th.level); }
      // opening range = the first 15-minute candle
      if (now >= FIRST) { let oh = -Infinity, ol = Infinity; for (let t = 0; t < FIRST; t++) { oh = Math.max(oh, s.h[t]); ol = Math.min(ol, s.l[t]); } const orl = (k, name) => { const x0 = Math.max(0, g.x(0)), xl = Math.min(plotW, Math.max(x0, g.x(FIRST)) + 150); labelLine(ctx, x0, xl, g.y(k), name, th.level, [4, 4]); ctx.save(); ctx.strokeStyle = th.level; ctx.globalAlpha = 0.55; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(xl, g.y(k)); ctx.lineTo(plotW, g.y(k)); ctx.stroke(); ctx.restore(); };
        orl(oh, "ORH 15m"); orl(ol, "ORL 15m"); }
    }
    // trade boxes (long / short position tool) for closed trades and the open one
    const box = (t0, t1, entry, stop, target, side) => {
      const x0 = g.x(t0), x1 = Math.max(x0 + 24, g.x(t1)), ye = g.y(entry);
      if (target !== null) { ctx.fillStyle = th.win; ctx.fillRect(x0, Math.min(ye, g.y(target)), x1 - x0, Math.abs(g.y(target) - ye)); }
      if (stop !== null) { ctx.fillStyle = th.loss; ctx.fillRect(x0, Math.min(ye, g.y(stop)), x1 - x0, Math.abs(g.y(stop) - ye)); }
      ctx.strokeStyle = th.level; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x0, ye); ctx.lineTo(x1, ye); ctx.stroke();
      if (stop !== null && x1 - x0 > 60) { const r = Math.abs(entry - stop); labelLine(ctx, x0, x1, g.y(entry + side * r), "1R", th.level); }
    };
    for (const t of S.trades) { if ((t.closeT || 0) < g.left || t.openT > g.right) continue; box(t.openT, t.closeT, t.entries[0].k, t.initStop, t.initTarget, t.side); }
    if (S.acct.pos && S.cur) { const st = S.orders.find((o) => o.tag === "stop"), tg = S.orders.find((o) => o.tag === "target"); box(S.cur.openT, now + S.view.span * 0.05, S.acct.avg, st ? st.k : null, tg ? tg.k : null, Math.sign(S.acct.pos)); if (st && !S.cur.stopMoves.some((m) => m.wider) && (st.k - S.acct.avg) * Math.sign(S.acct.pos) >= 0) labelLine(ctx, g.x(S.cur.openT), g.x(now) + 40, g.y(S.acct.avg), "BE", th.level); }
    // candles
    for (let i = i0; i <= i1; i++) {
      const cd = liveCandle(i, tf); if (!cd) continue;
      const x = g.x(i * tf + tf / 2), up = cd.c >= cd.o;
      const y0 = g.y(Math.max(cd.o, cd.c)), y1 = g.y(Math.min(cd.o, cd.c)), bh = Math.max(1, y1 - y0);
      ctx.strokeStyle = th.wick || (up ? "#2be07e" : "#ff4a4a"); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(x, g.y(cd.h)); ctx.lineTo(x, y0); ctx.moveTo(x, y0 + bh); ctx.lineTo(x, g.y(cd.l)); ctx.stroke();
      ctx.fillStyle = up ? th.bull : th.bear; ctx.fillRect(x - cw / 2, y0, cw, bh);
      if (th.edge && cw > 3) { ctx.strokeStyle = th.edge; ctx.lineWidth = 1; ctx.strokeRect(Math.round(x - cw / 2) + 0.5, Math.round(y0) + 0.5, Math.round(cw) - 1, Math.max(1, Math.round(bh) - 1)); }
    }
    ctx.lineWidth = 1;
    // VWAP and bands
    if (PREF.vwap) {
      const stepT = Math.max(1, Math.round((g.right - g.left) / 300));
      for (const [mul, a, dash] of [[0, 0.95, []], [1, 0.4, [4, 4]], [-1, 0.4, [4, 4]], [2, 0.25, [2, 5]], [-2, 0.25, [2, 5]]]) {
        ctx.strokeStyle = th.vwap; ctx.globalAlpha = a; ctx.setLineDash(dash); ctx.lineWidth = mul ? 1 : 1.6; ctx.beginPath(); let st = false;
        for (let t = Math.max(1, Math.floor(g.left)); t <= now; t += stepT) { const v = vwapAt(t); if (!v) continue; const y = g.y(v.m + mul * v.sd); if (!st) { ctx.moveTo(g.x(t), y); st = true; } else ctx.lineTo(g.x(t), y); }
        ctx.stroke();
      }
      ctx.setLineDash([]); ctx.lineWidth = 1; ctx.globalAlpha = 1;
      const v = vwapAt(now); if (v) { ctx.fillStyle = th.vwap; ctx.font = "600 11px Inter, Segoe UI, Arial, sans-serif"; ctx.fillText("VWAP", Math.min(g.x(now) + 6, plotW - 40), g.y(v.m) - 8); }
    }
    // big prints as bubbles
    if (PREF.bubbles) {
      const p = sess.prints, a0 = sess.secStart[Math.max(0, Math.floor(g.left))], thr = S.tf >= 300 ? 120 : S.tf >= 60 ? 80 : 45;
      for (let j = a0; j < S.pIdx; j++) {
        if (p.q[j] < thr) continue;
        const x = g.x(p.t[j]), y = g.y(p.k[j]), r = 2 + Math.sqrt(p.q[j]) * 0.85;
        ctx.fillStyle = p.side[j] > 0 ? th.bubUp : th.bubDn; ctx.strokeStyle = p.side[j] > 0 ? th.bubUpE : th.bubDnE;
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    }
    // gamma levels, labelled on the line
    if (PREF.gamma) {
      const lv = sess.levels;
      for (const [k, name, col] of [[lv.callWall, "Call Wall", "#2c7a69"], [lv.putWall, "Put Wall", "#a8392f"], [lv.zeroGamma, "Zero γ", "#b8650a"], [lv.hvl, "HVL", "#6b4fa8"]]) {
        if (k === null || k < g.pMin || k > g.pMax) continue;
        labelLine(ctx, 0, plotW, g.y(k), `${name} ${P(k)}`, PREF.theme !== "tv" ? { "Call Wall": "#2be07e", "Put Wall": "#ff4a4a", "Zero γ": "#ffa028", HVL: "#c58bff" }[name] : col, [7, 5]);
      }
    }
    drawItems(ctx, g.x, g.y, 0, plotW, { sel: D.sel });
    // entry / exit arrows (blue double arrow under a buy, red arrow over a sell)
    for (const f of S.fills) {
      if (f.t < g.left) continue;
      const i = Math.floor(f.t / tf), cd = liveCandle(i, tf), x = g.x(i * tf + tf / 2);
      if (f.side > 0) arrow(ctx, x, g.y(cd ? cd.l : f.k) + 7, true, th.buy, true); else arrow(ctx, x, g.y(cd ? cd.h : f.k) - 7, false, th.sell, false);
    }
    ctx.restore();
    // higher-timeframe candles on the right (current 15m / 1H with countdown)
    if (PREF.htf) {
      const x0 = plotW + 4, wcol = (g.htf - 8) / 2;
      ctx.fillStyle = th.bg; ctx.fillRect(plotW, 0, g.htf, plotH);
      ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(plotW + 0.5, 0); ctx.lineTo(plotW + 0.5, plotH); ctx.stroke();
      [[900, "15M"], [3600, "1H"]].forEach(([ts, name], n) => {
        const i = Math.floor(now / ts), a = i * ts, s = sess.sec; let o = null, h = -Infinity, l = Infinity, cl = null;
        for (let t = a; t < Math.min(Math.floor(now) + 1, sess.N); t++) { if (o === null) o = s.o[t]; h = Math.max(h, s.h[t]); l = Math.min(l, s.l[t]); cl = s.c[t]; }
        if (o === null) return;
        cl = lastK(); h = Math.max(h, cl); l = Math.min(l, cl);
        const xc = x0 + n * wcol + wcol / 2, up = cl >= o, y0 = g.y(Math.max(o, cl)), y1 = g.y(Math.min(o, cl));
        ctx.strokeStyle = th.wick || th.text; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.moveTo(xc, g.y(h)); ctx.lineTo(xc, g.y(l)); ctx.stroke(); ctx.lineWidth = 1;
        ctx.fillStyle = up ? th.bull : th.bear; ctx.fillRect(xc - wcol * 0.28, y0, wcol * 0.56, Math.max(1, y1 - y0));
        if (th.edge) { ctx.strokeStyle = th.edge; ctx.strokeRect(xc - wcol * 0.28 + 0.5, y0 + 0.5, wcol * 0.56 - 1, Math.max(1, y1 - y0) - 1); }
        const left = a + ts - now, mm = Math.floor(left / 60), ss = Math.floor(left % 60);
        ctx.fillStyle = th.text; ctx.textAlign = "center"; ctx.font = "600 12px Inter, Segoe UI, Arial, sans-serif"; ctx.fillText(name, xc, 14);
        ctx.fillStyle = th.dim; ctx.font = "10px JetBrains Mono, monospace"; ctx.fillText(`(${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")})`, xc, 28); ctx.textAlign = "left";
      });
    }
    // position, bracket and working orders: lines with tags you can drag
    const a = S.acct;
    const hline = (k, col, label, dash) => { if (k < g.pMin || k > g.pMax) return; const y = g.y(k); ctx.strokeStyle = col; ctx.setLineDash(dash || []); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1; ctx.font = "600 11px Inter, Segoe UI, Arial, sans-serif"; const tw = ctx.measureText(label).width + 12; ctx.fillStyle = col; ctx.fillRect(plotW - tw - 80, y - 10, tw, 20); ctx.fillStyle = "#fff"; ctx.fillText(label, plotW - tw - 74, y); axisTag(ctx, g, k, P(k), col, "#fff"); };
    if (a.pos) hline(a.avg, th.buy, `${a.pos > 0 ? "LONG" : "SHORT"} ${Math.abs(a.pos)}  ${money(openPnl(lastK()))}`);
    for (const o of S.orders) {
      const risk = a.pos ? (o.k - a.avg) * Math.sign(a.pos) * Math.abs(a.pos) * sess.tickValue : 0;
      if (o.tag === "stop") hline(o.k, "#c0392b", `SL ${money(risk)} ⇕`);
      else if (o.tag === "target") hline(o.k, "#24806b", `TP ${money(risk)} ⇕`);
      else hline(o.k, o.side > 0 ? th.buy : th.sell, `${o.side > 0 ? "BUY" : "SELL"} LMT ${o.qty} · queue ${Math.max(0, Math.round(o.queue))}`, [5, 4]);
    }
    // last price tag
    const lk = lastK(), ly = g.y(lk);
    ctx.strokeStyle = th.dim; ctx.setLineDash([1, 3]); ctx.beginPath(); ctx.moveTo(0, ly); ctx.lineTo(plotW, ly); ctx.stroke(); ctx.setLineDash([]);
    axisTag(ctx, g, lk, P(lk), th.last, th.lastText);
    // crosshair
    if (S.hover && S.hover.x < plotW) {
      const { x, y } = S.hover; ctx.strokeStyle = th.cross; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke(); ctx.setLineDash([]);
      axisTag(ctx, g, Math.round(g.k(y)), P(Math.round(g.k(y))), th.axisBox, th.text);
      ctx.fillStyle = th.axisBox; ctx.fillRect(x - 34, plotH + 1, 68, g.padB - 2); ctx.fillStyle = th.text; ctx.textAlign = "center"; ctx.font = "11px JetBrains Mono, monospace"; ctx.fillText(clock(g.t(x)), x, plotH + g.padB / 2); ctx.textAlign = "left";
    }
    drawSub(g); drawDom();
  }
  function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), n = x / p; return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p; }
  function niceTime(span) { const c = [15, 30, 60, 120, 300, 600, 900, 1800, 3600]; return c.find((s) => span / s <= 9) || 3600; }

  // delta bars + cumulative delta under the chart, on the same time axis
  function drawSub(g) {
    const c = $("#sm-cv2"); if (!c || !c._w) return;
    const th = TH(), ctx = c.getContext("2d"); ctx.setTransform(c._dpr, 0, 0, c._dpr, 0, 0);
    const w = c._w, h = c._h, plotW = g.plotW; ctx.fillStyle = th.subBg; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(0, 0.5); ctx.lineTo(w, 0.5); ctx.stroke();
    const tf = S.tf, i0 = Math.max(0, Math.floor(g.left / tf)), i1 = Math.min(Math.floor(S.simT / tf), candles(tf).n - 1);
    const ds = []; let mx = 1, cvd = 0; const cv = [];
    for (let i = 0; i < i0; i++) cvd += candles(tf).d[i];
    for (let i = i0; i <= i1; i++) { const cd = liveCandle(i, tf); const d = cd ? cd.d : 0; ds.push([i, d]); mx = Math.max(mx, Math.abs(d)); cvd += d; cv.push([i, cvd]); }
    const mid = h * 0.5, cw = Math.max(1, (plotW / ((g.right - g.left) / tf)) * 0.72);
    for (const [i, d] of ds) { const x = g.x(i * tf + tf / 2); if (x > plotW) continue; const hh = (d / mx) * (h * 0.42); ctx.fillStyle = d >= 0 ? th.volUp : th.volDn; ctx.fillRect(x - cw / 2, d >= 0 ? mid - hh : mid, cw, Math.abs(hh)); }
    if (cv.length > 1) {
      let lo = Infinity, hi = -Infinity; for (const [, v] of cv) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      const yy = (v) => h - 5 - ((v - lo) / Math.max(1, hi - lo)) * (h - 10);
      ctx.strokeStyle = th.buy; ctx.lineWidth = 1.6; ctx.beginPath(); cv.forEach(([i, v], n) => { const x = g.x(i * tf + tf / 2); if (!n) ctx.moveTo(x, yy(v)); else ctx.lineTo(x, yy(v)); }); ctx.stroke(); ctx.lineWidth = 1;
      ctx.fillStyle = th.buy; ctx.font = "600 11px JetBrains Mono, monospace"; ctx.textBaseline = "middle"; ctx.fillText(`CVD ${Math.round(cvd).toLocaleString()}`, plotW + g.htf + 4, 12);
    }
    ctx.fillStyle = th.dim; ctx.font = "600 11px Inter, Segoe UI, Arial, sans-serif"; ctx.textBaseline = "middle"; ctx.fillText("Delta · CVD", 8, 12);
  }

  // ------------------------------------------------------------------ DOM ladder
  const DOMC = [["MY BUY", 0.13], ["BID", 0.17], ["PRICE", 0.18], ["ASK", 0.17], ["MY SELL", 0.13], ["VOLUME", 0.22]];
  function domLayout(c) { const rowH = 18, rows = Math.floor((c._h - 20) / rowH); let x = 0; const cols = DOMC.map(([n, f]) => { const o = { n, x, w: f * c._w }; x += o.w; return o; }); return { rowH, rows, cols }; }
  function drawDom() {
    const c = $("#sm-dom"); if (!c || !c._w || !S.sess) return;
    const ctx = c.getContext("2d"); ctx.setTransform(c._dpr, 0, 0, c._dpr, 0, 0);
    const L = domLayout(c), s = snapSec(), sess = S.sess, D = sess.book.D, bb = sess.sec.bid[s], ba = sess.sec.ask[s], lk = lastK();
    if (S.domTop === undefined || lk > S.domTop - 3 || lk < S.domTop - L.rows + 3) S.domTop = lk + Math.floor(L.rows / 2);
    const th = TH();
    ctx.fillStyle = th.domBg; ctx.fillRect(0, 0, c._w, c._h);
    ctx.font = "10px JetBrains Mono, monospace"; ctx.textBaseline = "middle"; ctx.fillStyle = th.dim;
    for (const col of L.cols) ctx.fillText(col.n, col.x + 4, 9);
    let mxB = 1; for (let d = 0; d < D; d++) mxB = Math.max(mxB, sess.book.b[s * D + d], sess.book.a[s * D + d]);
    let mxV = 1; for (let r = 0; r < L.rows; r++) { const i = S.domTop - r - S.volBase; if (i >= 0 && i < 12000) mxV = Math.max(mxV, S.volAt.b[i] + S.volAt.s[i]); }
    ctx.font = "11px JetBrains Mono, monospace";
    for (let r = 0; r < L.rows; r++) {
      const k = S.domTop - r, y = 20 + r * L.rowH, yc = y + L.rowH / 2;
      ctx.fillStyle = r % 2 ? th.domAlt : th.domBg; ctx.fillRect(0, y, c._w, L.rowH);
      if (k === lk) { ctx.fillStyle = "rgba(255,160,40,0.18)"; ctx.fillRect(0, y, c._w, L.rowH); }
      const [cMB, cB, cP, cA, cMS, cV] = L.cols;
      if (k <= bb && bb - k < D) { const v = sess.book.b[s * D + bb - k]; ctx.fillStyle = th.domBid; ctx.fillRect(cB.x + cB.w - (v / mxB) * cB.w, y + 1, (v / mxB) * cB.w, L.rowH - 2); ctx.fillStyle = (v > mxB * 0.6 ? "#fff" : "#9cc8ff"); ctx.textAlign = "right"; ctx.fillText(Math.round(v), cB.x + cB.w - 4, yc); }
      if (k >= ba && k - ba < D) { const v = sess.book.a[s * D + k - ba]; ctx.fillStyle = th.domAsk; ctx.fillRect(cA.x, y + 1, (v / mxB) * cA.w, L.rowH - 2); ctx.fillStyle = (v > mxB * 0.6 ? "#fff" : "#ffb0b0"); ctx.textAlign = "left"; ctx.fillText(Math.round(v), cA.x + 4, yc); }
      ctx.textAlign = "center"; ctx.fillStyle = k === lk ? "#ffa028" : th.domText; ctx.font = k === lk ? "700 11px JetBrains Mono, monospace" : "11px JetBrains Mono, monospace"; ctx.fillText(P(k), cP.x + cP.w / 2, yc);
      const vi = k - S.volBase;
      if (vi >= 0 && vi < 12000) { const vb = S.volAt.b[vi], vs = S.volAt.s[vi], tot = vb + vs; if (tot > 0) { const ww = (tot / mxV) * (cV.w - 6); ctx.fillStyle = "rgba(43,224,126,0.4)"; ctx.fillRect(cV.x + 3, y + 3, ww * (vb / tot), L.rowH - 6); ctx.fillStyle = "rgba(255,74,74,0.4)"; ctx.fillRect(cV.x + 3 + ww * (vb / tot), y + 3, ww * (vs / tot), L.rowH - 6); ctx.fillStyle = th.domText; ctx.textAlign = "left"; ctx.font = "9px JetBrains Mono, monospace"; ctx.fillText(Math.round(tot), cV.x + 5, yc); ctx.font = "11px JetBrains Mono, monospace"; } }
      for (const o of S.orders) {
        if (o.k !== k) continue;
        const colX = o.side > 0 ? cMB : cMS, lbl = o.tag === "stop" ? `STP ${o.qty}` : o.tag === "target" ? `TGT ${o.qty}` : `${o.qty} ✕`;
        ctx.fillStyle = o.tag === "stop" ? "#ff4a4a" : o.tag === "target" ? "#2be07e" : o.side > 0 ? "#5aa9ff" : "#ff9f43"; ctx.fillRect(colX.x + 2, y + 2, colX.w - 4, L.rowH - 4);
        ctx.fillStyle = "#000"; ctx.textAlign = "center"; ctx.fillText(lbl, colX.x + colX.w / 2, yc);
      }
      if (S.acct.pos && Math.round(S.acct.avg) === k) { ctx.strokeStyle = "#5aa9ff"; ctx.strokeRect(1, y + 0.5, c._w - 2, L.rowH - 1); }
      ctx.textAlign = "left";
    }
  }
  function domEvents(c) {
    const at = (e) => { const r = c.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, L = domLayout(c); const row = Math.floor((y - 20) / L.rowH); const col = L.cols.find((q) => x >= q.x && x < q.x + q.w); return { k: S.domTop - row, col: col ? col.n : null, row }; };
    c.addEventListener("click", (e) => {
      if (!S.sess || S.ended) return; const h = at(e); if (h.row < 0) return;
      if (h.col === "BID" || h.col === "MY BUY") { const ex = S.orders.find((o) => o.k === h.k && o.side > 0 && o.tag === "entry"); if (ex && h.col === "MY BUY") { S.orders = S.orders.filter((o) => o !== ex); ui(true); return; } if (h.col === "BID") placeLimit(1, h.k); }
      if (h.col === "ASK" || h.col === "MY SELL") { const ex = S.orders.find((o) => o.k === h.k && o.side < 0 && o.tag === "entry"); if (ex && h.col === "MY SELL") { S.orders = S.orders.filter((o) => o !== ex); ui(true); return; } if (h.col === "ASK") placeLimit(-1, h.k); }
    });
    c.addEventListener("contextmenu", (e) => { e.preventDefault(); const h = at(e); S.orders = S.orders.filter((o) => !(o.k === h.k && o.tag === "entry")); ui(true); });
    c.title = "Click BID to place a buy limit, ASK to place a sell limit. Click your order (or right-click the row) to cancel.";
  }

  // ------------------------------------------------------------------ drawing tools (TradingView style)
  // Every drawing is a list of anchor points in time / price (seconds, ticks), so it follows zoom and pan; saved per seed.
  // A tool = { id, name, cat, pts (1, 2, 3, "multi", "free"), icon (svg) }; geometry, rendering, hit-testing and editing below.
  const SV = (d) => `<svg viewBox="0 0 28 28" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const DOT = (x, y) => `<circle cx="${x}" cy="${y}" r="2.2" fill="currentColor"/>`;
  const TOOLS = [
    // lines
    { id: "line", name: "Trend line", cat: "lines", pts: 2, key: "Alt+T", icon: SV(`<path d="M5 22 23 6"/>${DOT(5, 22)}${DOT(23, 6)}`) },
    { id: "ray", name: "Ray", cat: "lines", pts: 2, icon: SV(`<path d="M5 20 26 8"/>${DOT(5, 20)}${DOT(14, 15)}`) },
    { id: "extline", name: "Extended line", cat: "lines", pts: 2, icon: SV(`<path d="M2 22 26 6"/>${DOT(9, 17)}${DOT(19, 11)}`) },
    { id: "infoline", name: "Info line", cat: "lines", pts: 2, icon: SV(`<path d="M5 22 20 8"/>${DOT(5, 22)}${DOT(20, 8)}<rect x="15" y="16" width="10" height="7" rx="1.5"/>`) },
    { id: "arrowline", name: "Arrow", cat: "lines", pts: 2, icon: SV(`<path d="M5 22 22 7M14 7h8v8"/>`) },
    { id: "hline", name: "Horizontal line", cat: "lines", pts: 1, key: "Alt+H", icon: SV(`<path d="M3 14h22"/>${DOT(14, 14)}`) },
    { id: "hray", name: "Horizontal ray", cat: "lines", pts: 1, icon: SV(`<path d="M8 14h18"/>${DOT(8, 14)}`) },
    { id: "vline", name: "Vertical line", cat: "lines", pts: 1, icon: SV(`<path d="M14 3v22"/>${DOT(14, 14)}`) },
    { id: "cross", name: "Cross line", cat: "lines", pts: 1, icon: SV(`<path d="M14 3v22M3 14h22"/>${DOT(14, 14)}`) },
    // channels & fibonacci
    { id: "channel", name: "Parallel channel", cat: "fib", pts: 2, icon: SV(`<path d="M3 17 21 7M7 23 25 13"/>${DOT(3, 17)}${DOT(21, 7)}`) },
    { id: "regression", name: "Regression trend", cat: "fib", pts: 2, icon: SV(`<path d="M3 19 25 9" stroke-dasharray="2 2"/><path d="M3 14 25 4M3 24 25 14"/>`) },
    { id: "fib", name: "Fib retracement", cat: "fib", pts: 2, key: "Alt+F", icon: SV(`<path d="M3 5h22M3 10h22M3 14h22M3 18h22M3 23h22" opacity=".9"/>${DOT(5, 23)}${DOT(23, 5)}`) },
    { id: "fibext", name: "Trend-based fib extension", cat: "fib", pts: 3, icon: SV(`<path d="M3 22 10 8 17 17M3 6h22M3 11h22" />${DOT(3, 22)}${DOT(10, 8)}${DOT(17, 17)}`) },
    // shapes
    { id: "rect", name: "Rectangle", cat: "shapes", pts: 2, key: "Alt+R", icon: SV(`<rect x="5" y="7" width="18" height="14" rx="1"/>${DOT(5, 7)}${DOT(23, 21)}`) },
    { id: "fvg", name: "FVG box", cat: "shapes", pts: 2, icon: SV(`<rect x="4" y="9" width="20" height="10" rx="1" fill="currentColor" fill-opacity=".18"/><text x="14" y="17" font-size="7" text-anchor="middle" fill="currentColor" stroke="none" font-weight="700">FVG</text>`) },
    { id: "ellipse", name: "Ellipse", cat: "shapes", pts: 2, icon: SV(`<ellipse cx="14" cy="14" rx="10" ry="7"/>`) },
    { id: "triangle", name: "Triangle", cat: "shapes", pts: 3, icon: SV(`<path d="M4 22 12 6 24 19Z"/>`) },
    { id: "path", name: "Path", cat: "shapes", pts: "multi", icon: SV(`<path d="M4 20 10 9 16 17 24 6"/>${DOT(4, 20)}${DOT(24, 6)}`) },
    { id: "brush", name: "Brush", cat: "shapes", pts: "free", icon: SV(`<path d="M4 20c4-8 7 2 10-6s6-4 10-8"/>`) },
    { id: "highlighter", name: "Highlighter", cat: "shapes", pts: "free", icon: SV(`<path d="M4 18c5-6 9 0 13-5s5-3 7-5" stroke-width="5" opacity=".45"/>`) },
    // annotation
    { id: "text", name: "Text", cat: "annot", pts: 1, icon: SV(`<path d="M7 7h14M14 7v15"/>`) },
    { id: "note", name: "Note", cat: "annot", pts: 1, icon: SV(`<rect x="4" y="5" width="20" height="14" rx="2"/><path d="M9 19l-2 5 6-5M8 10h12M8 14h8"/>`) },
    { id: "pricelabel", name: "Price label", cat: "annot", pts: 1, icon: SV(`<path d="M4 14 9 8h15v12H9Z"/><path d="M12 14h8"/>`) },
    { id: "flag", name: "Flag mark", cat: "annot", pts: 1, icon: SV(`<path d="M8 24V4M8 5h13l-3 4 3 4H8"/>`) },
    { id: "up", name: "Arrow mark up", cat: "annot", pts: 1, icon: SV(`<path d="M14 24V5M8 11l6-6 6 6"/>`) },
    { id: "down", name: "Arrow mark down", cat: "annot", pts: 1, icon: SV(`<path d="M14 4v19M8 17l6 6 6-6"/>`) },
    // forecasting & measurement
    { id: "long", name: "Long position", cat: "forecast", pts: 2, key: "Alt+L", icon: SV(`<path d="M5 6h18M5 14h18M5 22h18"/>${DOT(5, 6)}${DOT(5, 22)}<text x="14" y="12.5" font-size="7" text-anchor="middle" fill="currentColor" stroke="none" font-weight="700">L</text>`) },
    { id: "short", name: "Short position", cat: "forecast", pts: 2, key: "Alt+S", icon: SV(`<path d="M5 6h18M5 14h18M5 22h18"/>${DOT(5, 6)}${DOT(5, 22)}<text x="14" y="20.5" font-size="7" text-anchor="middle" fill="currentColor" stroke="none" font-weight="700">S</text>`) },
    { id: "pricerange", name: "Price range", cat: "forecast", pts: 2, icon: SV(`<path d="M8 4h12M14 4v20M8 24h12M10 8l4-4 4 4"/>`) },
    { id: "daterange", name: "Date range", cat: "forecast", pts: 2, icon: SV(`<path d="M4 8v12M24 8v12M4 14h20M20 10l4 4-4 4"/>`) },
    { id: "dprange", name: "Date and price range", cat: "forecast", pts: 2, icon: SV(`<rect x="4" y="5" width="20" height="18" rx="1"/><path d="M14 9v10M9 14h10"/>`) },
    { id: "avwap", name: "Anchored VWAP", cat: "forecast", pts: 1, icon: SV(`<path d="M3 20c5 0 6-9 11-9s6 5 11 3"/><path d="M6 8v12M5 11h2" />${DOT(6, 20)}`) },
    { id: "frvp", name: "Fixed range volume profile", cat: "forecast", pts: 2, icon: SV(`<path d="M5 4v20M23 4v20M5 7h7M5 11h12M5 15h9M5 19h5"/>`) },
    // gamma
    { id: "gzone", name: "Gamma zone (3D)", cat: "gamma", pts: 2, key: "Alt+G", icon: SV(`<rect x="4" y="6" width="20" height="16" rx="2" stroke-dasharray="3 2"/><text x="14" y="18.5" font-size="11" text-anchor="middle" fill="currentColor" stroke="none" font-weight="700">γ</text>`) },
    { id: "gprofile", name: "Gamma profile", cat: "gamma", pts: 1, icon: SV(`<path d="M6 4v20M6 7h12M6 11h7M6 15h15M6 19h5" /><path d="M6 15h-3" />`) },
    { id: "dealer", name: "Dealer levels", cat: "gamma", pts: 1, icon: SV(`<path d="M3 7h22M3 12h22M3 17h22M3 22h22" stroke-dasharray="4 2"/>`) },
  ];
  const TOOL = Object.fromEntries(TOOLS.map((t) => [t.id, t]));
  const CATS = [["lines", "Lines"], ["fib", "Channels & Fibonacci"], ["shapes", "Geometric shapes"], ["annot", "Annotation"], ["forecast", "Forecasting and measurement"], ["gamma", "Gamma"]];
  const GROUPS = [["lines", "line"], ["fib", "fib"], ["shapes", "rect"], ["annot", "text"], ["forecast", "long"], ["gamma", "gzone"]];
  const ICON_CURSOR = SV(`<path d="M8 4v17l4-4 3 7 3-1-3-7h6Z"/>`);
  const DCOLORS = { tv: ["#2b2a2f", "#2f6fe0", "#d6372e", "#1f9d7a", "#d98a00", "#7b4fd0", "#e0559b", "#0d8ea8"], dark: ["#e6e8ec", "#5aa9ff", "#ff4a4a", "#2be07e", "#ffa028", "#c58bff", "#ff7ab8", "#3fd0e6"] };
  const LABELS = ["High", "Low", "AR", "SC", "ST", "Spring", "UTAD", "BOS", "CHoCH", "CISD", "$$$", "PO2", "1R", "BE", "EQH", "EQL", "OB", "Breaker"];
  const FIBR = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618];
  const FIBX = [0, 0.618, 1, 1.272, 1.618, 2, 2.618];
  const D = { tool: "cursor", items: [], sel: null, temp: null, mode: null, color: 0, magnet: true, stay: false, hidden: false, cur: Object.fromEntries(GROUPS), panel: null };
  const dkey = () => `nx_sim_draw_${S.seed}`;
  const dsave = () => lset(dkey(), D.items);
  function dload() {
    const raw = lget(dkey(), []) || [];
    D.items = raw.map((it) => { // migrate the first version (a / b / k fields) to anchor lists
      if (it.p) return it;
      const p = []; if (it.a) p.push(it.a); if (it.b) p.push(it.b); if (it.type === "hline") p.push({ t: 0, k: it.k });
      const o = { ...it, p }; delete o.a; delete o.b; return o;
    });
    D.sel = null; D.temp = null; D.mode = null; selBar();
  }
  const dcol = (i) => (typeof i === "string" ? i : (DCOLORS[PREF.theme !== "tv" ? "dark" : "tv"])[(i || 0) % 8]);
  const colOf = (it) => dcol(it.color);
  const FIBC = ["#7d7d85", "#d6372e", "#d98a00", "#1f9d7a", "#2f6fe0", "#0d8ea8", "#7d7d85", "#7b4fd0"];
  function fibLevels(it) { if (it.levels) return it.levels; const base = it.type === "fibext" ? FIBX : FIBR; return base.map((v, i) => ({ v, on: true, color: FIBC[i % FIBC.length] })); }
  function snap(g, x, y) {
    let t = g.t(x), k = g.k(y);
    if (D.magnet) {
      const tf = S.tf, i = Math.floor(t / tf), cd = i >= 0 ? liveCandle(i, tf) : null;
      if (cd) { const best = [cd.o, cd.h, cd.l, cd.c].reduce((b, v) => (Math.abs(g.y(v) - y) < Math.abs(g.y(b) - y) ? v : b), cd.h); if (Math.abs(g.y(best) - y) < 10) { k = best; t = i * tf + tf / 2; } }
    }
    return { t: Math.max(0, t), k: Math.round(k) };
  }
  const lineAt = (a, b, t) => (b.t === a.t ? a.k : a.k + ((b.k - a.k) * (t - a.t)) / (b.t - a.t));
  const fmtDur = (s) => { s = Math.abs(Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}h ${m}m` : m ? `${m}m ${x}s` : `${x}s`; };
  const tv = () => S.sess.tickValue;
  // closes of the current timeframe between two times (for regression)
  function closesBetween(t0, t1) { const tf = S.tf, out = []; for (let i = Math.floor(Math.min(t0, t1) / tf); i * tf <= Math.min(Math.max(t0, t1), S.simT); i++) { const c = liveCandle(i, tf); if (c) out.push([i * tf + tf / 2, c.c]); } return out; }
  function regress(it) {
    const pts = closesBetween(it.p[0].t, it.p[1].t); if (pts.length < 3) return null;
    const n = pts.length, mx = pts.reduce((a, p) => a + p[0], 0) / n, my = pts.reduce((a, p) => a + p[1], 0) / n;
    let sxy = 0, sxx = 0; for (const [x, y] of pts) { sxy += (x - mx) * (y - my); sxx += (x - mx) ** 2; }
    const b = sxx ? sxy / sxx : 0, a = my - b * mx, sd = Math.sqrt(pts.reduce((s, [x, y]) => s + (y - (a + b * x)) ** 2, 0) / n);
    return { at: (t) => a + b * t, sd, t0: pts[0][0], t1: pts[n - 1][0] };
  }
  function avwapAt(t0, t) { const c = S.cache.vw, i0 = Math.max(0, Math.floor(t0) - 1), i = Math.min(S.sess.N - 1, Math.floor(t)); const v = c.v[i] - (i0 > 0 ? c.v[i0] : 0); return v > 0 ? (c.pv[i] - (i0 > 0 ? c.pv[i0] : 0)) / v : null; }
  function profileBetween(t0, t1) {
    const a = Math.max(0, Math.floor(Math.min(t0, t1))), b = Math.min(Math.floor(Math.max(t0, t1)), Math.floor(S.simT)), p = S.sess.prints, m = new Map();
    for (let j = S.sess.secStart[a]; j < S.sess.secStart[Math.min(S.sess.N, b)]; j++) { const x = m.get(p.k[j]) || [0, 0]; x[p.side[j] > 0 ? 0 : 1] += p.q[j]; m.set(p.k[j], x); }
    const rows = [...m.entries()].sort((x, y) => x[0] - y[0]); if (!rows.length) return null;
    const tot = rows.reduce((s, r) => s + r[1][0] + r[1][1], 0); let poc = rows[0]; for (const r of rows) if (r[1][0] + r[1][1] > poc[1][0] + poc[1][1]) poc = r;
    // value area: 70% of volume around the POC
    let lo = rows.indexOf(poc), hi = lo, acc = poc[1][0] + poc[1][1];
    while (acc < tot * 0.7 && (lo > 0 || hi < rows.length - 1)) { const dn = lo > 0 ? rows[lo - 1][1][0] + rows[lo - 1][1][1] : -1, up = hi < rows.length - 1 ? rows[hi + 1][1][0] + rows[hi + 1][1][1] : -1; if (up >= dn) { hi++; acc += up; } else { lo--; acc += dn; } }
    return { rows, poc: poc[0], vah: rows[hi][0], val: rows[lo][0], tot };
  }
  // long / short position tool: p[0] = entry (and left edge), p[1] = stop (k) and right edge (t), it.tgt = target
  function posOutcome(it) {
    const side = it.type === "long" ? 1 : -1, e = it.p[0].k, st = it.p[1].k, tg = it.tgt, s = S.sess.sec;
    let entered = false;
    for (let t = Math.max(0, Math.floor(it.p[0].t)); t < Math.min(Math.floor(S.simT), S.sess.N); t++) {
      if (!entered) { if (s.l[t] <= e && s.h[t] >= e) entered = true; else continue; }
      const hitStop = side > 0 ? s.l[t] <= st : s.h[t] >= st, hitTgt = side > 0 ? s.h[t] >= tg : s.l[t] <= tg;
      if (hitStop) return { st: "STOPPED", t }; if (hitTgt) return { st: "TARGET", t };
    }
    return { st: entered ? "OPEN" : "WAITING" };
  }
  function ensurePos(it) { const side = it.type === "long" ? 1 : -1; if (it.tgt === undefined) it.tgt = it.p[0].k + side * 2 * Math.abs(it.p[0].k - it.p[1].k); }

  // ---- geometry: screen segments / boxes / handles for hit-testing (and most of the drawing)
  function geo(it, M) {
    const X = M.X, Y = M.Y, P = it.p, o = { segs: [], boxes: [], handles: [] };
    const xy = (q) => [X(q.t), Y(q.k)];
    const H = (q, i) => o.handles.push([X(q.t), Y(q.k), i]);
    const T = it.type;
    if (["line", "ray", "extline", "infoline", "arrowline"].includes(T) && P.length >= 2) {
      let [xa, ya] = xy(P[0]), [xb, yb] = xy(P[1]);
      const L = it.extendLeft || T === "extline", Rr = it.extend || T === "ray" || T === "extline";
      if ((L || Rr) && xb !== xa) { const sl = (yb - ya) / (xb - xa), l = xb >= xa ? [L ? M.x0 : xa, L ? ya + sl * (M.x0 - xa) : ya] : [L ? M.x1 : xa, L ? ya + sl * (M.x1 - xa) : ya], r = xb >= xa ? [Rr ? M.x1 : xb, Rr ? ya + sl * (M.x1 - xa) : yb] : [Rr ? M.x0 : xb, Rr ? ya + sl * (M.x0 - xa) : yb]; o.segs.push([...l, ...r]); }
      else o.segs.push([xa, ya, xb, yb]);
      H(P[0], 0); H(P[1], 1);
    } else if (T === "hline" || T === "hray") { const y = Y(P[0].k), x = T === "hray" ? X(P[0].t) : M.x0; o.segs.push([x, y, M.x1, y]); o.handles.push([T === "hray" ? x : (M.x0 + M.x1) / 2, y, 0]); }
    else if (T === "vline") { const x = X(P[0].t); o.segs.push([x, 0, x, M.h]); o.handles.push([x, Y(P[0].k), 0]); }
    else if (T === "cross") { const x = X(P[0].t), y = Y(P[0].k); o.segs.push([x, 0, x, M.h], [M.x0, y, M.x1, y]); H(P[0], 0); }
    else if (["rect", "fvg", "ellipse", "pricerange", "daterange", "dprange", "frvp", "regression", "gzone"].includes(T) && P.length >= 2) {
      const xa = it.extendLeft ? M.x0 : X(Math.min(P[0].t, P[1].t)), xb = it.extend ? M.x1 : X(Math.max(P[0].t, P[1].t)), ya = Y(Math.max(P[0].k, P[1].k)), yb = Y(Math.min(P[0].k, P[1].k));
      if (T === "regression") { const r = regress(it); if (r) { for (const m of [0, 2, -2]) o.segs.push([X(r.t0), Y(r.at(r.t0) + m * r.sd), X(r.t1), Y(r.at(r.t1) + m * r.sd)]); } }
      else if (T === "frvp") o.boxes.push([xa, 0, xb - xa, M.h]);
      else o.boxes.push([xa, ya, xb - xa, yb - ya]);
      H(P[0], 0); H(P[1], 1);
    } else if (T === "channel" && P.length >= 2) {
      const off = it.off || 0, [xa, ya] = xy(P[0]), [xb, yb] = xy(P[1]);
      o.segs.push([xa, ya, xb, yb], [xa, Y(P[0].k + off), xb, Y(P[1].k + off)]);
      o.boxes.push(null); H(P[0], 0); H(P[1], 1); o.handles.push([(xa + xb) / 2, (Y(P[0].k + off) + Y(P[1].k + off)) / 2, "off"]);
    } else if ((T === "fib") && P.length >= 2) {
      const xa = it.extendLeft ? M.x0 : X(Math.min(P[0].t, P[1].t)), xb = it.extend === false ? X(Math.max(P[0].t, P[1].t)) : M.x1;
      for (const l of fibLevels(it)) if (l.on) { const y = Y(P[1].k + (P[0].k - P[1].k) * l.v); o.segs.push([xa, y, xb, y]); }
      o.segs.push([X(P[0].t), Y(P[0].k), X(P[1].t), Y(P[1].k)]); H(P[0], 0); H(P[1], 1);
    } else if (T === "fibext" && P.length >= 2) {
      for (let i = 0; i < P.length - 1; i++) o.segs.push([...xy(P[i]), ...xy(P[i + 1])]);
      if (P.length >= 3) { const d = P[1].k - P[0].k, xa = X(P[2].t); for (const r of FIBX) { const y = Y(P[2].k + d * r); o.segs.push([xa, y, M.x1, y]); } }
      P.forEach(H);
    } else if (T === "triangle" || T === "path") { for (let i = 0; i < P.length - 1; i++) o.segs.push([...xy(P[i]), ...xy(P[i + 1])]); if (T === "triangle" && P.length === 3) o.segs.push([...xy(P[2]), ...xy(P[0])]); P.forEach(H); }
    else if (T === "brush" || T === "highlighter") { for (let i = 0; i < P.length - 1; i++) o.segs.push([...xy(P[i]), ...xy(P[i + 1])]); }
    else if (T === "long" || T === "short") {
      ensurePos(it);
      const x0 = X(P[0].t), x1 = Math.max(x0 + 30, X(P[1].t)), ye = Y(P[0].k), ys = Y(P[1].k), yt = Y(it.tgt);
      o.boxes.push([x0, Math.min(ye, yt), x1 - x0, Math.abs(yt - ye)], [x0, Math.min(ye, ys), x1 - x0, Math.abs(ys - ye)]);
      o.handles.push([x0, ye, 0], [x0, ys, 1], [x0, yt, "tgt"], [x1, ye, "w"]);
    } else if (T === "gprofile" && P.length) { const x = X(P[0].t); o.segs.push([x, 0, x, M.h]); o.boxes.push([x, 0, 190, M.h]); o.handles.push([x, Y(P[0].k), 0]); }
    else if (P.length) { const [x, y] = xy(P[0]); o.boxes.push([x - 34, y - 14, 68, 28]); H(P[0], 0); }
    return o;
  }

  // ---- rendering
  function drawItems(ctx, X, Y, x0, x1, opts) {
    if (D.hidden) return;
    const M = { X, Y, x0, x1, h: ctx.canvas.height / (ctx.canvas._dpr || window.devicePixelRatio || 1) };
    const th = TH(), sel = opts && opts.sel, rep = opts && opts.report;
    for (const it of D.items.concat(D.temp ? [D.temp] : [])) {
      if (!it.p || !it.p.length || it.hidden || (rep && it.noReport)) continue;
      const col = colOf(it), selected = it === sel || it === D.temp, hov = !selected && it === D.hover, g = geo(it, M), T = it.type, Q = it.p, OA = it.opacity ?? 1;
      const FA = (d) => (it.fill === false ? 0 : (it.fillOpacity ?? d)) * OA, FC = it.fillColor || col, TC = it.textColor || col;
      const FONT = (d, fam) => `${it.italic ? "italic " : ""}${it.bold === false ? 500 : 700} ${it.fontSize || d}px ${fam || "Inter, Segoe UI, Arial, sans-serif"}`;
      ctx.save(); ctx.globalAlpha = OA; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = (it.width || 1.6) + (selected ? 0.5 : 0); ctx.lineCap = "round"; ctx.lineJoin = "round";
      if (hov) { ctx.shadowColor = "rgba(255,160,40,0.55)"; ctx.shadowBlur = 8; }
      ctx.setLineDash(it.style === "dash" ? [7, 5] : it.style === "dot" ? [2, 4] : []);
      const segs = () => { ctx.beginPath(); for (const s of g.segs) { ctx.moveTo(s[0], s[1]); ctx.lineTo(s[2], s[3]); } ctx.stroke(); };
      const tag = (x, y, txt, bg, fg, align) => { ctx.save(); ctx.setLineDash([]); ctx.font = "600 11px Inter, Segoe UI, Arial, sans-serif"; const lines = String(txt).split("\n"), w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14, h = lines.length * 15 + 6; const bx = align === "left" ? x : align === "right" ? x - w : x - w / 2; ctx.fillStyle = bg; roundRect(ctx, bx, y - h / 2, w, h, 4); ctx.fill(); ctx.fillStyle = fg; ctx.textBaseline = "middle"; ctx.textAlign = "center"; lines.forEach((l, i) => ctx.fillText(l, bx + w / 2, y - h / 2 + 10.5 + i * 15)); ctx.restore(); };
      if (["line", "ray", "extline", "arrowline", "cross", "vline", "triangle", "path"].includes(T)) {
        segs();
        if (T === "triangle" && Q.length === 3) { ctx.fillStyle = FC; ctx.globalAlpha = FA(0.12); ctx.beginPath(); Q.forEach((q, i) => (i ? ctx.lineTo(X(q.t), Y(q.k)) : ctx.moveTo(X(q.t), Y(q.k)))); ctx.closePath(); ctx.fill(); ctx.globalAlpha = OA; }
        const head = (a, b) => { const ang = Math.atan2(Y(b.k) - Y(a.k), X(b.t) - X(a.t)), bx = X(b.t), by = Y(b.k), L = 10 + (it.width || 1.6) * 1.5; ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(bx - L * Math.cos(ang - 0.45), by - L * Math.sin(ang - 0.45)); ctx.lineTo(bx, by); ctx.lineTo(bx - L * Math.cos(ang + 0.45), by - L * Math.sin(ang + 0.45)); ctx.stroke(); };
        if (Q.length >= 2 && (it.arrowEnd ?? (T === "arrowline" || T === "path"))) head(Q[Q.length - 2], Q[Q.length - 1]);
        if (Q.length >= 2 && it.arrowStart) head(Q[1], Q[0]);
        if (T === "vline" || T === "cross") tag(X(Q[0].t), M.h - 30, clock(Q[0].t), col, "#fff");
        if (it.label && T === "vline") slantLabel(ctx, X(Q[0].t), M.h * 0.15, X(Q[0].t), M.h * 0.85, it.label, col, { pos: it.labelPos, style: it.labelStyle, font: FONT(12), size: it.fontSize || 12, textColor: it.textColor });
        else if (it.label && Q.length >= 2) { const a = Q[0], b = Q[Q.length - 1]; slantLabel(ctx, X(a.t), Y(a.k), X(b.t), Y(b.k), it.label, col, { pos: it.labelPos, style: it.labelStyle, font: FONT(12), size: it.fontSize || 12, textColor: it.textColor }); }
      } else if (T === "infoline" && Q.length >= 2) {
        segs(); if (it.arrowEnd) { const a = Q[0], b = Q[1], ang = Math.atan2(Y(b.k) - Y(a.k), X(b.t) - X(a.t)); ctx.beginPath(); ctx.moveTo(X(b.t) - 12 * Math.cos(ang - 0.45), Y(b.k) - 12 * Math.sin(ang - 0.45)); ctx.lineTo(X(b.t), Y(b.k)); ctx.lineTo(X(b.t) - 12 * Math.cos(ang + 0.45), Y(b.k) - 12 * Math.sin(ang + 0.45)); ctx.stroke(); }
        const dk = Q[1].k - Q[0].k, pct = (dk / Q[0].k) * 100, bars = Math.round(Math.abs(Q[1].t - Q[0].t) / S.tf), ang = Math.atan2(-(Y(Q[1].k) - Y(Q[0].k)), X(Q[1].t) - X(Q[0].t)) * 180 / Math.PI;
        tag(X(Q[1].t) + 10, Y(Q[1].k) + 22, `${dk >= 0 ? "+" : ""}${(dk * S.sess.tick).toFixed(2)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%) ${dk} ticks\n${bars} bars, ${fmtDur(Q[1].t - Q[0].t)} · ${ang.toFixed(0)}°`, th.axisBox, th.text, "left");
        if (it.label) slantLabel(ctx, X(Q[0].t), Y(Q[0].k), X(Q[1].t), Y(Q[1].k), it.label, col, { pos: it.labelPos, style: it.labelStyle, font: FONT(12), size: it.fontSize || 12, textColor: it.textColor });
      } else if (T === "hline" || T === "hray") {
        const y = Y(Q[0].k), xs = T === "hray" ? X(Q[0].t) : x0;
        if (it.label) { const lp = it.labelPos || "center", w = x1 - xs; if (lp === "center") labelLine(ctx, xs, x1, y, it.label, col, ctx.getLineDash()); else { segs(); ctx.setLineDash([]); ctx.font = FONT(12); ctx.fillStyle = TC; ctx.textBaseline = "bottom"; ctx.textAlign = lp === "left" ? "left" : "right"; ctx.fillText(it.label, lp === "left" ? xs + 8 : x1 - 80, y - 3); void w; } } else segs();
        ctx.setLineDash([]); if (it.showPrices !== false) tag(x1 - 34, y, P(Q[0].k), col, "#fff");
      } else if (T === "rect" || T === "fvg") {
        const b = g.boxes[0]; ctx.fillStyle = FC; ctx.globalAlpha = FA(T === "fvg" ? 0.18 : 0.14); ctx.fillRect(b[0], b[1], b[2], b[3]); ctx.globalAlpha = OA * 0.9; ctx.strokeRect(b[0] + 0.5, b[1] + 0.5, b[2], b[3]); ctx.globalAlpha = OA;
        const lbl = it.label || (T === "fvg" ? "FVG" : ""); if (lbl) { ctx.font = it.fontSize || it.bold !== undefined ? FONT(12) : "italic 600 12px Inter, Segoe UI, Arial, sans-serif"; ctx.fillStyle = TC; ctx.textBaseline = "middle"; ctx.textAlign = "right"; ctx.fillText(lbl, b[0] + b[2] - 6, b[1] + b[3] / 2); }
        if (it.showPrices !== false && selected) { ctx.font = "600 10px JetBrains Mono, monospace"; ctx.fillStyle = col; ctx.textAlign = "left"; ctx.textBaseline = "bottom"; ctx.fillText(P(Math.max(Q[0].k, Q[1].k)), b[0] + 3, b[1] - 2); ctx.textBaseline = "top"; ctx.fillText(P(Math.min(Q[0].k, Q[1].k)), b[0] + 3, b[1] + b[3] + 2); }
      } else if (T === "ellipse") {
        const b = g.boxes[0]; ctx.beginPath(); ctx.ellipse(b[0] + b[2] / 2, b[1] + b[3] / 2, Math.abs(b[2] / 2), Math.abs(b[3] / 2), 0, 0, Math.PI * 2); ctx.fillStyle = FC; ctx.globalAlpha = FA(0.12); ctx.fill(); ctx.globalAlpha = OA; ctx.stroke();
      } else if (T === "channel" && Q.length >= 2) {
        const off = it.off || 0, xa = X(Q[0].t), xb = X(Q[1].t);
        ctx.fillStyle = FC; ctx.globalAlpha = FA(0.1); ctx.beginPath(); ctx.moveTo(xa, Y(Q[0].k)); ctx.lineTo(xb, Y(Q[1].k)); ctx.lineTo(xb, Y(Q[1].k + off)); ctx.lineTo(xa, Y(Q[0].k + off)); ctx.closePath(); ctx.fill(); ctx.globalAlpha = OA; segs();
        ctx.setLineDash([4, 4]); ctx.globalAlpha = OA * 0.55; ctx.beginPath(); ctx.moveTo(xa, Y(Q[0].k + off / 2)); ctx.lineTo(xb, Y(Q[1].k + off / 2)); ctx.stroke(); ctx.globalAlpha = OA;
        if (it.label) { const top = off >= 0 ? off : 0; slantLabel(ctx, xa, Y(Q[0].k + top), xb, Y(Q[1].k + top), it.label, col, { pos: it.labelPos, style: it.labelStyle || "above", font: FONT(12), size: it.fontSize || 12, textColor: it.textColor }); }
      } else if (T === "regression" && Q.length >= 2) {
        const r = regress(it);
        if (r) { const xa = X(r.t0), xb = X(r.t1); ctx.globalAlpha = OA * 0.1; ctx.beginPath(); ctx.moveTo(xa, Y(r.at(r.t0) + 2 * r.sd)); ctx.lineTo(xb, Y(r.at(r.t1) + 2 * r.sd)); ctx.lineTo(xb, Y(r.at(r.t1) - 2 * r.sd)); ctx.lineTo(xa, Y(r.at(r.t0) - 2 * r.sd)); ctx.closePath(); ctx.fill(); ctx.globalAlpha = OA; ctx.beginPath(); ctx.moveTo(xa, Y(r.at(r.t0) + 2 * r.sd)); ctx.lineTo(xb, Y(r.at(r.t1) + 2 * r.sd)); ctx.moveTo(xa, Y(r.at(r.t0) - 2 * r.sd)); ctx.lineTo(xb, Y(r.at(r.t1) - 2 * r.sd)); ctx.stroke(); ctx.setLineDash([5, 4]); ctx.beginPath(); ctx.moveTo(xa, Y(r.at(r.t0))); ctx.lineTo(xb, Y(r.at(r.t1))); ctx.stroke(); }
      } else if (T === "fib" && Q.length >= 2) {
        const xa = it.extendLeft ? x0 : X(Math.min(Q[0].t, Q[1].t)), xb = it.extend === false ? X(Math.max(Q[0].t, Q[1].t)) : x1, lv = fibLevels(it).filter((l) => l.on).sort((p1, p2) => p1.v - p2.v);
        lv.forEach((l, i) => { const k = Q[1].k + (Q[0].k - Q[1].k) * l.v, y = Y(k); if (i < lv.length - 1 && it.fill !== false) { const y2 = Y(Q[1].k + (Q[0].k - Q[1].k) * lv[i + 1].v); ctx.fillStyle = l.color; ctx.globalAlpha = OA * (it.fillOpacity ?? 0.07); ctx.fillRect(xa, Math.min(y, y2), xb - xa, Math.abs(y2 - y)); ctx.globalAlpha = OA; } ctx.strokeStyle = it.levels || typeof it.color !== "number" ? l.color : col; ctx.beginPath(); ctx.moveTo(xa, y); ctx.lineTo(xb, y); ctx.stroke(); ctx.fillStyle = ctx.strokeStyle; ctx.font = "600 10.5px JetBrains Mono, monospace"; ctx.textBaseline = "bottom"; ctx.textAlign = "left"; ctx.fillText(`${l.v} (${(k * S.sess.tick).toFixed(2)})`, xa + 4, y - 2); });
        ctx.strokeStyle = col; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(X(Q[0].t), Y(Q[0].k)); ctx.lineTo(X(Q[1].t), Y(Q[1].k)); ctx.stroke();
      } else if (T === "fibext") {
        segs();
        if (Q.length >= 3) { const d = Q[1].k - Q[0].k; ctx.font = "600 10.5px JetBrains Mono, monospace"; ctx.textBaseline = "bottom"; fibLevels(it).filter((l) => l.on).forEach((l) => { const k = Q[2].k + d * l.v; ctx.fillStyle = it.levels ? l.color : col; ctx.fillText(`${l.v} (${(k * S.sess.tick).toFixed(2)})`, X(Q[2].t) + 4, Y(k) - 2); }); }
      } else if (T === "brush" || T === "highlighter") {
        ctx.setLineDash([]); if (T === "highlighter") { ctx.lineWidth = 14; ctx.globalAlpha = OA * 0.28; }
        ctx.beginPath(); Q.forEach((q, i) => (i ? ctx.lineTo(X(q.t), Y(q.k)) : ctx.moveTo(X(q.t), Y(q.k)))); ctx.stroke(); ctx.globalAlpha = OA;
      } else if (T === "text") {
        ctx.font = FONT(13); ctx.fillStyle = TC; ctx.textBaseline = "middle"; ctx.textAlign = "center"; String(it.text || "Text").split("\n").forEach((ln, i, arr) => ctx.fillText(ln, X(Q[0].t), Y(Q[0].k) + (i - (arr.length - 1) / 2) * ((it.fontSize || 13) + 3)));
      } else if (T === "note") {
        const x = X(Q[0].t), y = Y(Q[0].k); ctx.setLineDash([]); ctx.font = FONT(12); const w = ctx.measureText(it.text || "Note").width + 18;
        ctx.fillStyle = FC; ctx.globalAlpha = OA * 0.92; roundRect(ctx, x - w / 2, y - 36, w, 24, 5); ctx.fill(); ctx.beginPath(); ctx.moveTo(x - 6, y - 12); ctx.lineTo(x, y - 3); ctx.lineTo(x + 6, y - 12); ctx.fill(); ctx.globalAlpha = OA;
        ctx.fillStyle = it.textColor || "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(it.text || "Note", x, y - 24);
      } else if (T === "pricelabel") {
        const x = X(Q[0].t), y = Y(Q[0].k), txt = P(Q[0].k); ctx.setLineDash([]); ctx.font = "700 11px JetBrains Mono, monospace"; const w = ctx.measureText(txt).width + 16;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 8, y - 11); ctx.lineTo(x + 8 + w, y - 11); ctx.lineTo(x + 8 + w, y + 11); ctx.lineTo(x + 8, y + 11); ctx.closePath(); ctx.fill(); ctx.fillStyle = "#fff"; ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText(txt, x + 14, y);
      } else if (T === "flag") {
        const x = X(Q[0].t), y = Y(Q[0].k); ctx.setLineDash([]); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - 30); ctx.stroke(); ctx.beginPath(); ctx.moveTo(x, y - 30); ctx.lineTo(x + 18, y - 30); ctx.lineTo(x + 13, y - 24); ctx.lineTo(x + 18, y - 18); ctx.lineTo(x, y - 18); ctx.closePath(); ctx.fill();
      } else if (T === "up" || T === "down") { ctx.setLineDash([]); arrow(ctx, X(Q[0].t), Y(Q[0].k), T === "up", col, T === "up"); }
      else if (T === "avwap") {
        ctx.setLineDash([]); ctx.lineWidth = 1.8; ctx.beginPath(); let st = false; const t0 = Q[0].t, step = Math.max(1, Math.round((S.simT - t0) / 400));
        for (let t = Math.ceil(t0); t <= S.simT; t += step) { const v = avwapAt(t0, t); if (v === null) continue; if (!st) { ctx.moveTo(X(t), Y(v)); st = true; } else ctx.lineTo(X(t), Y(v)); }
        ctx.stroke(); const v = avwapAt(t0, S.simT); ctx.beginPath(); ctx.arc(X(t0), Y(Q[0].k), 3.5, 0, Math.PI * 2); ctx.fill(); if (v !== null) { ctx.font = "600 11px Inter, Segoe UI, Arial, sans-serif"; ctx.textBaseline = "bottom"; ctx.fillText("AVWAP " + P(Math.round(v)), Math.min(X(S.simT) + 4, x1 - 90), Y(v) - 3); }
      } else if (T === "frvp" && Q.length >= 2) {
        const pr = profileBetween(Q[0].t, Q[1].t), xa = X(Math.min(Q[0].t, Q[1].t)), xb = X(Math.max(Q[0].t, Q[1].t));
        ctx.setLineDash([3, 3]); ctx.globalAlpha = OA * 0.5; ctx.beginPath(); ctx.moveTo(xa, 0); ctx.lineTo(xa, M.h); ctx.moveTo(xb, 0); ctx.lineTo(xb, M.h); ctx.stroke(); ctx.globalAlpha = OA; ctx.setLineDash([]);
        if (pr) {
          let mx = 1; for (const r of pr.rows) mx = Math.max(mx, r[1][0] + r[1][1]); const wmax = (xb - xa) * 0.85;
          for (const [k, [bv, sv]] of pr.rows) { const y = Y(k), hh = Math.max(1, Math.abs(Y(k) - Y(k + 1)) - 0.5), w = ((bv + sv) / mx) * wmax, inVA = k >= pr.val && k <= pr.vah; ctx.globalAlpha = inVA ? 0.55 : 0.28; ctx.fillStyle = th.volUp; ctx.fillRect(xa, y - hh / 2, w * (bv / (bv + sv)), hh); ctx.fillStyle = th.volDn; ctx.fillRect(xa + w * (bv / (bv + sv)), y - hh / 2, w * (sv / (bv + sv)), hh); }
          ctx.globalAlpha = OA; for (const [k, n, c] of [[pr.poc, "POC", "#d6372e"], [pr.vah, "VAH", col], [pr.val, "VAL", col]]) { ctx.strokeStyle = c; ctx.beginPath(); ctx.moveTo(xa, Y(k)); ctx.lineTo(xb, Y(k)); ctx.stroke(); ctx.fillStyle = c; ctx.font = "600 10px JetBrains Mono, monospace"; ctx.textBaseline = "bottom"; ctx.textAlign = "right"; ctx.fillText(`${n} ${P(k)}`, xb - 3, Y(k) - 2); }
        }
      } else if (T === "pricerange" || T === "daterange" || T === "dprange") {
        const b = g.boxes[0], up = Q[1].k >= Q[0].k, right = Q[1].t >= Q[0].t, c2 = T === "daterange" ? (right ? "#2f6fe0" : "#d6372e") : (up ? "#2f6fe0" : "#d6372e");
        ctx.setLineDash([]); ctx.fillStyle = c2; ctx.strokeStyle = c2; ctx.globalAlpha = OA * 0.14; ctx.fillRect(b[0], b[1], b[2], b[3]); ctx.globalAlpha = OA;
        const cx = b[0] + b[2] / 2, cy = b[1] + b[3] / 2;
        ctx.beginPath(); if (T !== "daterange") { ctx.moveTo(cx, Y(Q[0].k)); ctx.lineTo(cx, Y(Q[1].k)); } if (T !== "pricerange") { ctx.moveTo(X(Q[0].t), cy); ctx.lineTo(X(Q[1].t), cy); } ctx.stroke();
        const dk = Q[1].k - Q[0].k, dt = Q[1].t - Q[0].t, bars = Math.round(Math.abs(dt) / S.tf);
        let vol = 0; if (T !== "pricerange") { const a0 = Math.max(0, Math.floor(Math.min(Q[0].t, Q[1].t))), a1 = Math.min(Math.floor(Math.max(Q[0].t, Q[1].t)), Math.floor(S.simT)); for (let t = a0; t < a1; t++) vol += S.sess.sec.v[t]; }
        const lines = []; if (T !== "daterange") lines.push(`${dk >= 0 ? "+" : ""}${(dk * S.sess.tick).toFixed(2)} (${((dk / Q[0].k) * 100).toFixed(2)}%) ${dk} ticks · ${money(Math.abs(dk) * tv())}/ct`);
        if (T !== "pricerange") lines.push(`${bars} bars, ${fmtDur(dt)}${vol ? ` · vol ${Math.round(vol).toLocaleString()}` : ""}`);
        tag(cx, up ? b[1] - 22 : b[1] + b[3] + 22, lines.join("\n"), c2, "#fff");
      } else if (T === "long" || T === "short") {
        ensurePos(it);
        const side = T === "long" ? 1 : -1, [bt, bs] = g.boxes, x0b = bt[0], wb = bt[2], e = Q[0].k, st = Q[1].k, tg = it.tgt, q = it.qty ?? PREF.qty;
        ctx.setLineDash([]); ctx.fillStyle = "rgba(31,157,122,0.22)"; ctx.fillRect(...bt); ctx.fillStyle = "rgba(214,55,46,0.2)"; ctx.fillRect(...bs);
        ctx.strokeStyle = th.level; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x0b, Y(e)); ctx.lineTo(x0b + wb, Y(e)); ctx.stroke();
        const oc = posOutcome(it);
        if (oc.t !== undefined) { const xe = X(oc.t); ctx.fillStyle = oc.st === "TARGET" ? "rgba(31,157,122,0.25)" : "rgba(214,55,46,0.25)"; const yk = oc.st === "TARGET" ? tg : st; ctx.fillRect(x0b, Math.min(Y(e), Y(yk)), Math.min(xe, x0b + wb) - x0b, Math.abs(Y(yk) - Y(e))); }
        const rr = Math.abs(tg - e) / Math.max(1, Math.abs(e - st));
        tag(x0b + wb / 2, Y(tg) + (side > 0 ? -14 : 14), `Target: ${P(tg)} (${Math.abs(tg - e)} ticks) ${money(Math.abs(tg - e) * tv() * q)}`, "#1f9d7a", "#fff");
        tag(x0b + wb / 2, Y(st) + (side > 0 ? 14 : -14), `Stop: ${P(st)} (${Math.abs(e - st)} ticks) ${money(Math.abs(e - st) * tv() * q)}`, "#d6372e", "#fff");
        if (selected || oc.st !== "WAITING") tag(x0b + wb / 2, Y(e), `${T === "long" ? "Long" : "Short"} ${q} · R:R ${rr.toFixed(2)}${oc.st === "TARGET" ? " · ✔ TARGET HIT" : oc.st === "STOPPED" ? " · ✖ STOPPED" : oc.st === "OPEN" ? " · OPEN" : ""}`, th.axisBox, th.text);
      }
      else if (T === "gzone" && Q.length >= 2) {
        const b = g.boxes[0], lo = Math.min(Q[0].k, Q[1].k), hi = Math.max(Q[0].k, Q[1].k), z = gzoneStats(lo, hi);
        ctx.fillStyle = it.fillColor || (z.net >= 0 ? "#1f9d7a" : "#d6372e"); ctx.globalAlpha = FA(0.1); ctx.fillRect(b[0], b[1], b[2], b[3]); ctx.globalAlpha = OA;
        ctx.setLineDash([6, 4]); ctx.strokeStyle = "#ffa028"; ctx.lineWidth = 1.4; ctx.strokeRect(b[0] + 0.5, b[1] + 0.5, b[2], b[3]); ctx.setLineDash([]);
        // strike bars inside the zone (net gamma by strike)
        const mx = Math.max(1, ...z.rows.map((r) => Math.abs(r.gex)));
        for (const r of z.rows) { const y = Y(r.k), w = (Math.abs(r.gex) / mx) * Math.min(b[2] * 0.45, 120); ctx.fillStyle = r.gex >= 0 ? "rgba(31,157,122,0.75)" : "rgba(214,55,46,0.75)"; ctx.fillRect(b[0] + b[2] - w - 4, y - 3, w, 6); }
        tag(b[0] + 6, b[1] + 13, `γ ${it.label ? it.label + " · " : ""}${fmtG(z.net)} / 1%`, "#0b0b0d", "#ffa028", "left");
        if (selected) { ctx.font = "600 10px JetBrains Mono, monospace"; ctx.fillStyle = "#ffa028"; ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.fillText("double-click: 3D gamma", b[0] + 6, b[1] + b[3] - 16); }
      } else if (T === "gprofile" && Q.length) {
        const x = X(Q[0].t), rows = gammaGrid(lastK()), mx = Math.max(1, ...rows.map((r) => Math.abs(r.gex))), lv = S.sess.levels;
        ctx.setLineDash([3, 3]); ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, M.h); ctx.stroke(); ctx.setLineDash([]);
        for (const r of rows) { const y = Y(r.k); if (y < -10 || y > M.h + 10) continue; const w = (Math.abs(r.gex) / mx) * 170; ctx.fillStyle = r.gex >= 0 ? "rgba(31,157,122,0.7)" : "rgba(214,55,46,0.7)"; ctx.fillRect(x, y - 4, w, 8); if (r.k === lv.callWall || r.k === lv.putWall || r.k === lv.hvl) { ctx.fillStyle = TC; ctx.font = "700 10px JetBrains Mono, monospace"; ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText(`${r.k === lv.callWall ? "CW" : r.k === lv.putWall ? "PW" : "HVL"} ${fmtG(r.gex)}`, x + w + 4, y); } }
        tag(x + 6, 14, "γ PROFILE · $ per 1%", "#0b0b0d", "#ffa028", "left");
      }
      if ((selected || hov) && !["brush", "highlighter"].includes(T)) { if (hov) ctx.globalAlpha = 0.55; ctx.shadowBlur = 0; handles(ctx, g.handles.map((h) => [h[0], h[1]]), col); }
      ctx.restore();
    }
  }
  function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
  function handles(ctx, pts, col) { ctx.setLineDash([]); for (const [x, y] of pts) { ctx.fillStyle = "#0b0b0d"; ctx.strokeStyle = "#ffa028"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.fillStyle = "#ffa028"; ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill(); } void col; }
  const _gz = new Map();
  function gzoneStats(lo, hi) { const key = `${lo}|${hi}|${Math.round(lastK() / 8)}`; if (_gz.has(key)) return _gz.get(key); const st = S.sess.strikeStep, rows = gammaGrid(lastK()).filter((r) => r.k >= lo - st / 2 && r.k <= hi + st / 2), v = { rows, net: rows.reduce((a, r) => a + r.gex, 0) }; if (_gz.size > 60) _gz.clear(); _gz.set(key, v); return v; }
  function segDist(px, py, ax, ay, bx, by) { const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy; const u = L ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L)) : 0; return Math.hypot(px - (ax + u * dx), py - (ay + u * dy)); }
  function hitItem(g, x, y) {
    if (D.hidden) return null;
    const M = { X: g.x, Y: g.y, x0: 0, x1: g.plotW, h: g.h };
    for (let n = D.items.length - 1; n >= 0; n--) {
      const it = D.items[n]; if (!it.p || !it.p.length || it.hidden) continue;
      const o = geo(it, M);
      for (const h of o.handles) if (Math.hypot(h[0] - x, h[1] - y) < 8) return { it, part: h[2] };
      for (const s of o.segs) if (segDist(x, y, s[0], s[1], s[2], s[3]) < (it.type === "highlighter" ? 9 : 6)) return { it, part: "body" };
      for (const b of o.boxes) if (b && x >= Math.min(b[0], b[0] + b[2]) - 3 && x <= Math.max(b[0], b[0] + b[2]) + 3 && y >= Math.min(b[1], b[1] + b[3]) - 3 && y <= Math.max(b[1], b[1] + b[3]) + 3) return { it, part: "body" };
      if (it.type === "channel" && it.p.length >= 2) { const t = g.t(x); if (t >= Math.min(it.p[0].t, it.p[1].t) && t <= Math.max(it.p[0].t, it.p[1].t)) { const lo = lineAt(it.p[0], it.p[1], t), hi = lo + (it.off || 0), k = g.k(y); if (k >= Math.min(lo, hi) && k <= Math.max(lo, hi)) return { it, part: "body" }; } }
    }
    return null;
  }

  // ---- UI: left toolbar, drawings panel, selection toolbar, label box
  function toolbarHtml() {
    return `<div class="sm-dt" id="sm-dt">
        <button data-dt="cursor" title="Cursor (Esc)">${ICON_CURSOR}</button>
        ${GROUPS.map(([cat]) => `<button data-grp="${cat}" title="${esc((CATS.find((c) => c[0] === cat) || [])[1])}"><span class="sm-gi"></span><i class="sm-gc"></i></button>`).join("")}
        <span class="sm-dt-sep"></span>
        <button id="sm-dall" title="All drawing tools">${SV(`<rect x="4" y="4" width="8" height="8" rx="1.5"/><rect x="16" y="4" width="8" height="8" rx="1.5"/><rect x="4" y="16" width="8" height="8" rx="1.5"/><rect x="16" y="16" width="8" height="8" rx="1.5"/>`)}</button>
        <button id="sm-dmag" title="Magnet: snap to candle open / high / low / close">${SV(`<path d="M7 5v9a7 7 0 0 0 14 0V5h-4v9a3 3 0 0 1-6 0V5Z"/><path d="M7 9h4M17 9h4"/>`)}</button>
        <button id="sm-dstay" title="Stay in drawing mode">${SV(`<path d="M5 23l3-1 13-13-2-2L6 20Z"/><path d="M17 7l2 2"/><circle cx="21" cy="21" r="3"/>`)}</button>
        <button id="sm-dhide" title="Hide / show all drawings">${SV(`<path d="M3 14s4-7 11-7 11 7 11 7-4 7-11 7S3 14 3 14Z"/><circle cx="14" cy="14" r="3"/>`)}</button>
        <button id="sm-dtree" title="Object tree: every drawing (show / hide, lock, edit, delete)">${SV(`<path d="M5 6h4M5 14h4M5 22h4M13 6h10M13 14h10M13 22h10"/>`)}</button>
        <button id="sm-dset" title="Chart settings: candle colours, background, grid">${SV(`<circle cx="14" cy="14" r="3.5"/><path d="M14 3v3M14 22v3M3 14h3M22 14h3M6.2 6.2l2.1 2.1M19.7 19.7l2.1 2.1M6.2 21.8l2.1-2.1M19.7 8.3l2.1-2.1"/>`)}</button>
        <button id="sm-dclr" title="Remove all drawings on this seed">${SV(`<path d="M5 8h18M11 8V5h6v3M8 8l1 15h10l1-15"/>`)}</button>
      </div>
      <div class="sm-dpanel" id="sm-dpanel" hidden><div class="sm-dp-h"><div><span class="sm-dp-brand">◆ JORDAN TERMINAL</span><b>Drawings</b></div><button class="sm-dp-x" id="sm-dpx">✕</button></div>
        <div class="sm-dp-search">${SV(`<circle cx="12" cy="12" r="7"/><path d="M17 17l6 6"/>`)}<input id="sm-dps" placeholder="Search"></div>
        <div class="sm-dp-cats" id="sm-dpc"></div><div class="sm-dp-grid" id="sm-dpg"></div></div>
      <div class="sm-dsel" id="sm-dsel" hidden></div>
      <div class="sm-txt" id="sm-txt" hidden><div class="sm-txt-chips">${LABELS.map((l) => `<button data-lbl="${esc(l)}">${esc(l)}</button>`).join("")}</div><input id="sm-txt-in" placeholder="or type a label, Enter"></div>`;
  }
  function paintToolbar() {
    document.querySelectorAll("#sm-dt [data-grp]").forEach((b) => { const id = D.cur[b.dataset.grp]; b.querySelector(".sm-gi").innerHTML = TOOL[id].icon; b.title = TOOL[id].name; b.classList.toggle("on", !!(TOOL[D.tool] && TOOL[D.tool].cat === b.dataset.grp)); });
    const c = $("#sm-dt [data-dt='cursor']"); if (c) c.classList.toggle("on", D.tool === "cursor");
    const t = (id, on) => { const e = $("#" + id); if (e) e.classList.toggle("on", on); };
    t("sm-dmag", D.magnet); t("sm-dstay", D.stay); t("sm-dhide", D.hidden);
  }
  function setTool(t) {
    D.tool = t; D.temp = null; D.mode = null;
    if (TOOL[t]) D.cur[TOOL[t].cat] = t;
    const c = $("#sm-cv"); if (c) c.style.cursor = t === "cursor" ? "crosshair" : "copy";
    const tb = $("#sm-txt"); if (tb) tb.hidden = true;
    paintToolbar();
  }
  function openPanel(cat) {
    const p = $("#sm-dpanel"); D.panel = cat || "all"; p.hidden = false;
    $("#sm-dpc").innerHTML = [["all", "All"], ...CATS].map(([k, n]) => `<button data-pc="${k}" class="${D.panel === k ? "on" : ""}">${esc(n)}</button>`).join("");
    $("#sm-dpc").querySelectorAll("[data-pc]").forEach((b) => (b.onclick = () => openPanel(b.dataset.pc)));
    const q = ($("#sm-dps").value || "").toLowerCase();
    const list = TOOLS.filter((t) => (D.panel === "all" || t.cat === D.panel) && (!q || t.name.toLowerCase().includes(q)));
    $("#sm-dpg").innerHTML = list.map((t) => `<button data-tool="${t.id}" class="${D.tool === t.id ? "on" : ""}" title="${esc(t.name)}${t.key ? " · " + t.key : ""}">${t.icon}<span>${esc(t.name)}</span></button>`).join("") || '<div class="dim">No tool matches.</div>';
    $("#sm-dpg").querySelectorAll("[data-tool]").forEach((b) => (b.onclick = () => { setTool(b.dataset.tool); p.hidden = true; }));
  }
  function selBar() {
    const bar = $("#sm-dsel"); if (!bar) return;
    const it = D.sel; if (!it) { bar.hidden = true; return; }
    const T = it.type, cols = DCOLORS[PREF.theme !== "tv" ? "dark" : "tv"];
    const lineish = !["text", "note", "pricelabel", "flag", "up", "down", "long", "short", "frvp", "avwap"].includes(T) || T === "avwap";
    bar.hidden = false;
    bar.innerHTML = `<span class="sm-ds-name">${esc(TOOL[T] ? TOOL[T].name : T)}</span>
      ${cols.map((c, i) => `<button class="sm-ds-c ${(it.color ?? 0) === i ? "on" : ""}" data-c="${i}" style="background:${c}"></button>`).join("")}${typeof it.color === "string" ? `<button class="sm-ds-c on" style="background:${it.color}" title="custom colour"></button>` : ""}
      ${lineish ? `<span class="sm-ds-sep"></span>${[1, 2, 3].map((w) => `<button class="sm-ds-b ${(it.width || 1.6) === (w === 1 ? 1.6 : w + 0.4) ? "on" : ""}" data-w="${w}" title="Line width"><i style="height:${w}px"></i></button>`).join("")}
        ${[["solid", "━"], ["dash", "╍"], ["dot", "┈"]].map(([s, ic]) => `<button class="sm-ds-b ${(it.style || "solid") === s ? "on" : ""}" data-s="${s}" title="Line style">${ic}</button>`).join("")}` : ""}
      ${["rect", "fvg", "ellipse"].includes(T) ? `<button class="sm-ds-b ${it.fill === false ? "" : "on"}" data-fill title="Fill">◧</button>` : ""}
      ${["rect", "fvg", "fib"].includes(T) ? `<button class="sm-ds-b ${it.extend ? "on" : ""}" data-ext title="Extend right">⇥</button>` : ""}
      ${["text", "note", "hline", "hray", "rect", "fvg", "gzone", "line", "ray", "extline", "arrowline", "infoline", "channel", "vline"].includes(T) ? `<button class="sm-ds-b sm-ds-txt ${(it.label || it.text) ? "on" : ""}" data-lab title="Add / edit text on this drawing">T</button>` : ""}
      ${T === "long" || T === "short" ? `<button class="sm-ds-t" data-trade title="Send this plan as a limit order with this stop and target">TRADE IT</button>` : ""}
      ${T === "gzone" ? `<button class="sm-ds-t sm-ds-g" data-g3d title="Open the 3D gamma surface of this zone">γ 3D</button>` : ""}
      <span class="sm-ds-sep"></span><button class="sm-ds-b" data-props title="Settings (double-click the drawing)">⚙</button><button class="sm-ds-b ${it.locked ? "on" : ""}" data-lock title="Lock">${it.locked ? "🔒" : "🔓"}</button><button class="sm-ds-b" data-hide title="Hide">👁</button><button class="sm-ds-b" data-clone title="Clone">⧉</button><button class="sm-ds-b" data-del title="Delete (Del)">🗑</button>`;
    bar.querySelectorAll("[data-c]").forEach((b) => (b.onclick = () => { it.color = +b.dataset.c; D.color = it.color; dsave(); selBar(); }));
    bar.querySelector(".sm-ds-name").onclick = () => openProps(it);
    bar.querySelectorAll("[data-w]").forEach((b) => (b.onclick = () => { const w = +b.dataset.w; it.width = w === 1 ? 1.6 : w + 0.4; dsave(); selBar(); }));
    bar.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => { it.style = b.dataset.s; dsave(); selBar(); }));
    const on = (sel, fn) => { const b = bar.querySelector(sel); if (b) b.onclick = fn; };
    on("[data-fill]", () => { it.fill = it.fill === false; dsave(); selBar(); });
    on("[data-ext]", () => { it.extend = it.type === "fib" ? it.extend === false : !it.extend; dsave(); selBar(); });
    on("[data-lab]", () => { const r = bar.getBoundingClientRect(), h = $("#sm-cv").getBoundingClientRect(); askLabel(r.left - h.left, r.bottom - h.top + 6, { edit: it }); });
    on("[data-clone]", () => { const c = JSON.parse(JSON.stringify(it)); c.p = c.p.map((q) => ({ t: q.t + S.tf * 3, k: q.k + 4 })); if (c.tgt !== undefined) c.tgt += 4; D.items.push(c); D.sel = c; dsave(); selBar(); });
    on("[data-del]", () => { D.items = D.items.filter((x) => x !== it); D.sel = null; dsave(); selBar(); });
    on("[data-trade]", () => tradePlan(it));
    on("[data-props]", () => openProps(it));
    on("[data-lock]", () => { it.locked = !it.locked; dsave(); selBar(); });
    on("[data-hide]", () => { it.hidden = true; D.sel = null; dsave(); selBar(); });
    on("[data-g3d]", () => openGamma(Math.min(it.p[0].k, it.p[1].k), Math.max(it.p[0].k, it.p[1].k)));
  }
  // a drawn long / short position becomes a limit order with that stop and target (or a market order if price is already there)
  function tradePlan(it) {
    if (!S.sess || S.ended) return;
    ensurePos(it);
    const side = it.type === "long" ? 1 : -1, e = it.p[0].k;
    S.planBracket = { stop: it.p[1].k, target: it.tgt, side };
    const s = snapSec(), bb = S.sess.sec.bid[s], ba = S.sess.sec.ask[s];
    if ((side > 0 && e >= ba) || (side < 0 && e <= bb)) marketFill(side, it.qty ?? PREF.qty, "plan (market)");
    else placeLimit(side, e, it.qty);
    feed(`📐 Plan sent: ${side > 0 ? "LONG" : "SHORT"} ${it.qty ?? PREF.qty} @ ${P(e)} · stop ${P(it.p[1].k)} · target ${P(it.tgt)}`);
  }
  function wireToolbar() {
    $("#sm-dt [data-dt='cursor']").onclick = () => setTool("cursor");
    document.querySelectorAll("#sm-dt [data-grp]").forEach((b) => {
      b.onclick = (e) => { const r = b.getBoundingClientRect(); if (e.clientX > r.right - 9 || (TOOL[D.tool] && TOOL[D.tool].cat === b.dataset.grp)) openPanel(b.dataset.grp); else setTool(D.cur[b.dataset.grp]); };
      b.oncontextmenu = (e) => { e.preventDefault(); openPanel(b.dataset.grp); };
    });
    $("#sm-dall").onclick = () => openPanel("all");
    $("#sm-dpx").onclick = () => { $("#sm-dpanel").hidden = true; };
    $("#sm-dps").oninput = () => openPanel(D.panel);
    $("#sm-dps").onkeydown = (e) => e.stopPropagation();
    $("#sm-dmag").onclick = () => { D.magnet = !D.magnet; paintToolbar(); };
    $("#sm-dstay").onclick = () => { D.stay = !D.stay; paintToolbar(); };
    $("#sm-dhide").onclick = () => { D.hidden = !D.hidden; D.sel = null; selBar(); paintToolbar(); };
    $("#sm-dclr").onclick = () => { if (D.items.length) { D.items = []; D.sel = null; dsave(); selBar(); } };
    $("#sm-dtree").onclick = openTree; $("#sm-dset").onclick = openChartSettings;
    const box = $("#sm-txt"), inp = $("#sm-txt-in");
    const put = (txt) => {
      if (!txt || !D.pending) return; const p = D.pending;
      if (p.edit) { if (p.edit.type === "text" || p.edit.type === "note") p.edit.text = txt.slice(0, 60); else p.edit.label = txt.slice(0, 40); }
      else D.items.push({ type: p.type || "text", p: [p.at], text: txt.slice(0, 60), color: D.color });
      D.pending = null; box.hidden = true; inp.value = ""; dsave(); selBar();
    };
    box.querySelectorAll("[data-lbl]").forEach((b) => (b.onclick = () => put(b.dataset.lbl)));
    inp.onkeydown = (e) => { if (e.key === "Enter") put(inp.value.trim()); if (e.key === "Escape") { box.hidden = true; D.pending = null; } e.stopPropagation(); };
    setTool("cursor");
  }
  function askLabel(x, y, pending) {
    D.pending = pending; const box = $("#sm-txt");
    box.hidden = false; box.style.left = Math.max(48, Math.min(x + 12, box.parentElement.clientWidth - 340)) + "px"; box.style.top = Math.max(6, Math.min(y - 10, box.parentElement.clientHeight - 140)) + "px";
    setTimeout(() => $("#sm-txt-in").focus(), 0);
  }

  // ---- mouse
  function finish(it) {
    delete it.phase; if (it.type === "long" || it.type === "short") ensurePos(it);
    if (it.type === "dealer") { const lv = S.sess.levels; for (const [k, lbl, c] of [[lv.callWall, "Call wall", "#1f9d7a"], [lv.putWall, "Put wall", "#d6372e"], [lv.zeroGamma, "Zero γ", "#ffa028"], [lv.hvl, "HVL", "#7b4fd0"]]) if (k !== null) D.items.push({ type: "hline", p: [{ t: it.p[0].t, k }], label: lbl, color: c, style: "dash" }); D.temp = null; D.mode = null; dsave(); feed("γ Dealer levels added as editable lines (call wall, put wall, zero gamma, HVL)."); if (!D.stay) setTool("cursor"); return; }
    if (!it._d) { const keep = { ...it }; applyDefaults(it); it.p = keep.p; if (keep.tgt !== undefined) it.tgt = keep.tgt; if (keep.off !== undefined) it.off = keep.off; it._d = 1; }
    D.items.push(it); D.temp = null; D.mode = null; D.sel = it; dsave(); selBar();
    if (!D.stay) setTool("cursor");
    if (it.type === "gzone") openGamma(Math.min(it.p[0].k, it.p[1].k), Math.max(it.p[0].k, it.p[1].k));
  }
  function drawDown(p, g, e) {
    const tool = D.tool;
    if (tool === "cursor") {
      $("#sm-dpanel").hidden = true;
      const h = hitItem(g, p.x, p.y);
      if (h) { D.sel = h.it; selBar(); S.drag = { kind: "draw", h, start: snap(g, p.x, p.y), orig: JSON.parse(JSON.stringify(h.it)) }; if (e.detail === 2) { S.drag = null; if (h.it.type === "gzone") openGamma(Math.min(h.it.p[0].k, h.it.p[1].k), Math.max(h.it.p[0].k, h.it.p[1].k)); else openProps(h.it); } return true; }
      if (D.sel) { D.sel = null; selBar(); }
      return false;
    }
    const def = TOOL[tool], at = snap(g, p.x, p.y), c = D.color;
    // multi-click tools in progress
    if (D.temp && D.mode === "clicks") {
      const it = D.temp; it.p[it.p.length - 1] = at;
      const need = def.pts === "multi" ? Infinity : def.pts;
      if (def.pts === "multi" && (e.detail === 2 || (it.p.length > 2 && Math.hypot(g.x(it.p[it.p.length - 2].t) - p.x, g.y(it.p[it.p.length - 2].k) - p.y) < 6))) { if (e.detail === 2) it.p.pop(); finish(it); return true; }
      if (it.p.length >= need) { finish(it); return true; }
      it.p.push({ ...at }); return true;
    }
    if (D.temp && D.mode === "width") { finish(D.temp); return true; }
    if (def.pts === 1) {
      const it = { type: tool, p: [at], color: c };
      if (tool === "text" || tool === "note") { askLabel(p.x, p.y, { at, type: tool }); if (!D.stay) setTool("cursor"); return true; }
      if (tool === "hline") { finish(it); askLabel(p.x, p.y, { edit: it }); return true; }
      finish(it); return true;
    }
    if (def.pts === "free") { D.temp = { type: tool, p: [at], color: c, width: tool === "brush" ? 2 : undefined }; D.mode = "free"; S.drag = { kind: "create" }; return true; }
    D.temp = { type: tool, p: [at, { ...at }], color: c, off: 0 };
    D.mode = "drag"; D.downAt = [p.x, p.y]; S.drag = { kind: "create" };
    return true;
  }
  function drawMove(p, g) {
    if (D.temp) {
      const it = D.temp, at = snap(g, p.x, p.y);
      if (D.mode === "free") { const last = it.p[it.p.length - 1]; if (Math.hypot(g.x(last.t) - p.x, g.y(last.k) - p.y) > 3) it.p.push({ t: g.t(p.x), k: Math.round(g.k(p.y)) }); return true; }
      if (D.mode === "drag" || D.mode === "clicks") {
        it.p[it.p.length - 1] = at;
        if (it.type === "long" || it.type === "short") { const side = it.type === "long" ? 1 : -1, d = Math.abs(at.k - it.p[0].k) || 12; it.p[1] = { t: Math.max(at.t, it.p[0].t + S.tf * 8), k: it.p[0].k - side * d }; it.tgt = it.p[0].k + side * 2 * d; }
        return true;
      }
      if (D.mode === "width") { it.off = Math.round(g.k(p.y) - lineAt(it.p[0], it.p[1], g.t(p.x))); return true; }
    }
    if (S.drag && S.drag.kind === "draw") {
      const { h, start, orig } = S.drag, now = snap(g, p.x, p.y), dt = now.t - start.t, dk = now.k - start.k, it = h.it;
      if (it.locked) return true;
      if (h.part === "body") { it.p = orig.p.map((q) => ({ t: q.t + dt, k: q.k + dk })); if (orig.tgt !== undefined) it.tgt = orig.tgt + dk; }
      else if (h.part === "off") it.off = Math.round(g.k(p.y) - lineAt(it.p[0], it.p[1], g.t(p.x)));
      else if (h.part === "tgt") it.tgt = now.k;
      else if (h.part === "w") it.p[1] = { ...it.p[1], t: Math.max(it.p[0].t + S.tf, now.t) };
      else if (it.type === "long" || it.type === "short") { if (h.part === 0) { const d = now.k - orig.p[0].k; it.p[0] = { t: now.t, k: now.k }; if (orig.tgt !== undefined) it.tgt = orig.tgt + d; it.p[1] = { ...orig.p[1], k: orig.p[1].k + d }; } else it.p[1] = { ...it.p[1], k: now.k }; }
      else if (typeof h.part === "number") it.p[h.part] = now;
      return true;
    }
    return false;
  }
  function drawUp(p) {
    if (D.temp && D.mode === "free") { if (D.temp.p.length > 1) finish(D.temp); else { D.temp = null; D.mode = null; } return true; }
    if (D.temp && D.mode === "drag") {
      const it = D.temp, def = TOOL[it.type], moved = p ? Math.hypot(p.x - D.downAt[0], p.y - D.downAt[1]) > 5 : true;
      if (!moved) {
        if (it.type === "long" || it.type === "short") { const side = it.type === "long" ? 1 : -1; it.p[1] = { t: it.p[0].t + S.tf * 25, k: it.p[0].k - side * PREF.stopT }; it.tgt = it.p[0].k + side * PREF.tgtT; finish(it); return true; }
        D.mode = "clicks"; return true;   // click-click mode: the next click sets the second point
      }
      if (def.pts === 3) { it.p.push({ ...it.p[1] }); D.mode = "clicks"; return true; }
      if (def.pts === "multi") { it.p.push({ ...it.p[1] }); D.mode = "clicks"; return true; }
      if (it.type === "channel") { D.mode = "width"; return true; }
      finish(it); return true;
    }
    if (S.drag && S.drag.kind === "draw") { dsave(); return true; }
    return false;
  }
  // price levels of the trader's own drawings at time t (for grading)
  function markLevels(t) {
    const out = [];
    for (const it of D.items) {
      const P = it.p || [], T = it.type; if (!P.length) continue;
      const inSpan = (a, b, pad) => t >= Math.min(a, b) && t <= Math.max(a, b) + (pad || 0);
      if (T === "hline" || T === "pricelabel" || (T === "hray" && t >= P[0].t)) out.push({ k: P[0].k, what: it.label ? `your "${it.label}" line` : "your horizontal line" });
      else if ((T === "rect" || T === "fvg") && P.length > 1 && t >= Math.min(P[0].t, P[1].t) && (it.extend || t <= Math.max(P[0].t, P[1].t) + 3600)) out.push({ lo: Math.min(P[0].k, P[1].k), hi: Math.max(P[0].k, P[1].k), what: T === "fvg" ? "your FVG" : (it.label ? `your "${it.label}" zone` : "your zone") });
      else if (["line", "ray", "extline", "infoline"].includes(T) && P.length > 1 && (T === "ray" || T === "extline" ? t >= Math.min(P[0].t, P[1].t) : inSpan(P[0].t, P[1].t))) out.push({ k: lineAt(P[0], P[1], t), what: "your trend line" });
      else if (T === "channel" && P.length > 1 && inSpan(P[0].t, P[1].t, 1800)) { const b = lineAt(P[0], P[1], t); out.push({ k: b, what: "your channel edge" }, { k: b + (it.off || 0), what: "your channel edge" }); }
      else if (T === "fib" && P.length > 1 && t >= Math.min(P[0].t, P[1].t)) for (const r of [0.382, 0.5, 0.618, 0.786]) out.push({ k: P[1].k + (P[0].k - P[1].k) * r, what: `your fib ${r}` });
      else if (T === "fibext" && P.length > 2 && t >= P[2].t) for (const r of [1, 1.272, 1.618]) out.push({ k: P[2].k + (P[1].k - P[0].k) * r, what: `your fib extension ${r}` });
      else if (T === "avwap" && t > P[0].t) { const v = avwapAt(P[0].t, t); if (v !== null) out.push({ k: v, what: "your anchored VWAP" }); }
      else if ((T === "long" || T === "short") && t >= P[0].t && t <= P[1].t) out.push({ k: P[0].k, what: "your planned entry" });
    }
    return out;
  }
  function atMark(k, t) {
    for (const m of markLevels(t)) { if (m.lo !== undefined ? k >= m.lo - 2 && k <= m.hi + 2 : Math.abs(k - m.k) <= 3) return m.what; }
    return null;
  }

  // ------------------------------------------------------------------ editing: properties dialog, object tree, chart settings, gamma zone 3D
  const PALETTE = ["#ffffff", "#d1d4dc", "#9598a1", "#5d606b", "#2b2a2f", "#000000", "#ffa028", "#ff6d00", "#f7525f", "#d6372e", "#e0559b", "#9c27b0", "#7b4fd0", "#2962ff", "#2f6fe0", "#00bcd4", "#0d8ea8", "#1f9d7a", "#22ab94", "#4caf50", "#cddc39", "#ffeb3b", "#c49a6c", "#795548"];
  const toHex = (c) => (typeof c === "string" && c[0] === "#" ? c.slice(0, 7) : "#ffa028");
  const DEF_KEY = "nx_sim_draw_defaults";
  const toolDefaults = () => lget(DEF_KEY, {}) || {};
  function applyDefaults(it) { const d = toolDefaults()[it.type]; if (d) Object.assign(it, JSON.parse(JSON.stringify(d))); return it; }
  const clockToT = (s) => { const m = String(s).trim().match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/); if (!m) return null; return (+m[1] * 3600 + +m[2] * 60 + +(m[3] || 0)) - (OPEN_H * 60 + OPEN_M) * 60; };
  function modal(html, cls) {
    let m = $("#sm-pmod"); if (!m) { m = document.createElement("div"); m.id = "sm-pmod"; m.className = "sm-pmod"; $("#view-sim").appendChild(m); }
    m.className = "sm-pmod " + (cls || ""); m.hidden = false; m.innerHTML = `<div class="sm-pbox">${html}</div>`;
    m.onmousedown = (e) => { if (e.target === m) closeModal(); };
    return m;
  }
  function closeModal() { const m = $("#sm-pmod"); if (m) { m.hidden = true; m.innerHTML = ""; } }
  const swatches = (key, cur) => `<div class="sm-sw">${PALETTE.map((c) => `<button data-sw="${key}" data-c="${c}" class="${toHex(cur) === c ? "on" : ""}" style="background:${c}"></button>`).join("")}<label class="sm-swc" title="Custom colour"><input type="color" data-swc="${key}" value="${toHex(cur)}"><span>+</span></label></div>`;

  // ---- properties dialog for any drawing
  function openProps(it, tab) {
    if (!it) return;
    const before = JSON.stringify(it), T = it.type, def = TOOL[T] || { name: T };
    const hasText = ["text", "note", "hline", "hray", "rect", "fvg", "gzone", "line", "ray", "extline", "arrowline", "infoline", "channel", "vline"].includes(T);
    const isFib = T === "fib" || T === "fibext", isPos = T === "long" || T === "short", lineish = !["text", "note", "pricelabel", "flag"].includes(T);
    const tabs = [["style", "Style"], ...(hasText ? [["text", "Text"]] : []), ...(isFib ? [["levels", "Levels"]] : []), ...(isPos ? [["pos", "Position"]] : []), ["coords", "Coordinates"], ["vis", "Visibility"]];
    let cur = tab || "style";
    const render = () => {
      const col = colOf(it);
      const body = {
        style: `<div class="sm-pf"><label>Line colour</label>${swatches("color", col)}</div>
          <div class="sm-pf"><label>Opacity <b>${Math.round((it.opacity ?? 1) * 100)}%</b></label><input type="range" min="10" max="100" value="${Math.round((it.opacity ?? 1) * 100)}" data-k="opacity" data-scale="100"></div>
          ${lineish ? `<div class="sm-pf"><label>Width</label><div class="sm-seg">${[1, 1.6, 2.4, 3.4, 4.5].map((w, i) => `<button data-set="width" data-v="${w}" class="${(it.width || 1.6) === w ? "on" : ""}"><i style="height:${i + 1}px"></i></button>`).join("")}</div></div>
          <div class="sm-pf"><label>Style</label><div class="sm-seg">${[["solid", "Solid"], ["dash", "Dashed"], ["dot", "Dotted"]].map(([v, n]) => `<button data-set="style" data-v="${v}" class="${(it.style || "solid") === v ? "on" : ""}">${n}</button>`).join("")}</div></div>` : ""}
          ${["line", "ray", "extline", "infoline", "arrowline", "rect", "fvg", "fib", "gzone"].includes(T) ? `<div class="sm-pf"><label>Extend</label><div class="sm-seg"><button data-tog="extendLeft" class="${it.extendLeft ? "on" : ""}">⇤ Left</button><button data-tog="extend" class="${(T === "fib" ? it.extend !== false : it.extend) ? "on" : ""}">Right ⇥</button></div></div>` : ""}
          ${["line", "ray", "extline", "infoline", "arrowline", "path"].includes(T) ? `<div class="sm-pf"><label>Arrows</label><div class="sm-seg"><button data-tog="arrowStart" class="${it.arrowStart ? "on" : ""}">◀ Start</button><button data-tog="arrowEnd" class="${(it.arrowEnd ?? (T === "arrowline" || T === "path")) ? "on" : ""}">End ▶</button></div></div>` : ""}
          ${["rect", "fvg", "ellipse", "triangle", "channel", "regression", "gzone", "note"].includes(T) ? `<div class="sm-pf"><label>Fill</label><div class="sm-row2"><button class="sm-ptg ${it.fill === false ? "" : "on"}" data-tog="fill" data-inv>${it.fill === false ? "Off" : "On"}</button></div>${swatches("fillColor", it.fillColor || col)}</div>
            <div class="sm-pf"><label>Fill opacity <b>${Math.round((it.fillOpacity ?? 0.15) * 100)}%</b></label><input type="range" min="0" max="80" value="${Math.round((it.fillOpacity ?? 0.15) * 100)}" data-k="fillOpacity" data-scale="100"></div>` : ""}
          ${["hline", "hray", "rect", "fvg", "line", "ray", "extline", "channel"].includes(T) ? `<div class="sm-pf"><label>Price labels</label><div class="sm-seg"><button data-tog="showPrices" class="${it.showPrices !== false ? "on" : ""}">${it.showPrices !== false ? "Shown" : "Hidden"}</button></div></div>` : ""}`,
        text: `<div class="sm-pf"><label>Text</label><textarea data-k="${T === "text" || T === "note" ? "text" : "label"}" rows="2" placeholder="Label">${esc((T === "text" || T === "note" ? it.text : it.label) || "")}</textarea></div>
          <div class="sm-pf"><label>Quick labels</label><div class="sm-chips">${LABELS.map((l) => `<button data-qlbl="${esc(l)}">${esc(l)}</button>`).join("")}</div></div>
          <div class="sm-pf"><label>Font size <b>${it.fontSize || 13}px</b></label><input type="range" min="9" max="32" value="${it.fontSize || 13}" data-k="fontSize"></div>
          <div class="sm-pf"><label>Style</label><div class="sm-seg"><button data-tog="bold" class="${it.bold !== false ? "on" : ""}"><b>B</b></button><button data-tog="italic" class="${it.italic ? "on" : ""}"><i>I</i></button></div></div>
          <div class="sm-pf"><label>Text colour</label>${swatches("textColor", it.textColor || col)}</div>
          ${["line", "ray", "extline", "arrowline", "infoline", "channel", "vline"].includes(T) ? `<div class="sm-pf"><label>Text style</label><div class="sm-seg">${[["pill", "Badge"], ["above", "Above line"], ["plain", "On line"]].map(([v, n]) => `<button data-set="labelStyle" data-v="${v}" class="${(it.labelStyle || (T === "channel" ? "above" : "pill")) === v ? "on" : ""}">${n}</button>`).join("")}</div></div>
          <div class="sm-pf"><label>Text position</label><div class="sm-seg">${[["left", "Start"], ["center", "Middle"], ["right", "End"]].map(([v, n]) => `<button data-set="labelPos" data-v="${v}" class="${(it.labelPos || "center") === v ? "on" : ""}">${n}</button>`).join("")}</div></div>` : ""}
          ${["hline", "hray"].includes(T) ? `<div class="sm-pf"><label>Label position</label><div class="sm-seg">${[["left", "Left"], ["center", "Centre"], ["right", "Right"]].map(([v, n]) => `<button data-set="labelPos" data-v="${v}" class="${(it.labelPos || "center") === v ? "on" : ""}">${n}</button>`).join("")}</div></div>` : ""}`,
        levels: (() => { const lv = fibLevels(it); return `<div class="sm-lv">${lv.map((l, i) => `<div class="sm-lvr"><input type="checkbox" data-lvon="${i}" ${l.on ? "checked" : ""}><input type="number" step="0.001" data-lvv="${i}" value="${l.v}"><label class="sm-swc sm-swc-s"><input type="color" data-lvc="${i}" value="${toHex(l.color)}"><span style="background:${l.color}"></span></label><button data-lvdel="${i}" title="Remove">✕</button></div>`).join("")}</div>
          <div class="sm-row2"><button class="sm-pbtn" data-lvadd>+ Add level</button><button class="sm-pbtn" data-lvreset>Reset</button></div>`; })(),
        pos: (() => { ensurePos(it); const side = T === "long" ? 1 : -1, e = it.p[0].k, st = it.p[1].k, tg = it.tgt, q = it.qty ?? PREF.qty, risk = Math.abs(e - st) * tv() * q, rew = Math.abs(tg - e) * tv() * q;
          return `<div class="sm-grid2"><div class="sm-pf"><label>Entry price</label><input type="number" step="${S.sess.tick}" data-pk="entry" value="${(e * S.sess.tick).toFixed(2)}"></div><div class="sm-pf"><label>Quantity</label><input type="number" min="1" step="1" data-pk="qty" value="${q}"></div>
            <div class="sm-pf"><label>Stop price</label><input type="number" step="${S.sess.tick}" data-pk="stop" value="${(st * S.sess.tick).toFixed(2)}"></div><div class="sm-pf"><label>Target price</label><input type="number" step="${S.sess.tick}" data-pk="target" value="${(tg * S.sess.tick).toFixed(2)}"></div></div>
            <div class="sm-pstats"><div><span>Risk</span><b class="dn">${money(risk)}</b><i>${Math.abs(e - st)} ticks</i></div><div><span>Reward</span><b class="up">${money(rew)}</b><i>${Math.abs(tg - e)} ticks</i></div><div><span>R:R</span><b>${(rew / Math.max(1, risk)).toFixed(2)}</b><i>${side > 0 ? "long" : "short"}</i></div></div>
            <div class="sm-row2"><button class="sm-pbtn sm-pgo" data-ptrade>TRADE THIS PLAN</button></div>`; })(),
        coords: `<div class="sm-coords">${it.p.slice(0, 6).map((q, i) => `<div class="sm-cr"><span>Point ${i + 1}</span><input type="number" step="${S.sess.tick}" data-ck="${i}" value="${(q.k * S.sess.tick).toFixed(2)}"><input type="text" data-ct="${i}" value="${clock(q.t)}"></div>`).join("")}${it.p.length > 6 ? `<div class="dim">+${it.p.length - 6} more points (free-hand)</div>` : ""}</div>`,
        vis: `<div class="sm-pf"><label>Lock</label><div class="sm-seg"><button data-tog="locked" class="${it.locked ? "on" : ""}">${it.locked ? "🔒 Locked" : "🔓 Unlocked"}</button></div></div>
          <div class="sm-pf"><label>Visibility</label><div class="sm-seg"><button data-tog="hidden" class="${it.hidden ? "" : "on"}" data-inv>${it.hidden ? "Hidden" : "Visible"}</button></div></div>
          <div class="sm-pf"><label>Show in the report</label><div class="sm-seg"><button data-tog="noReport" class="${it.noReport ? "" : "on"}" data-inv>${it.noReport ? "No" : "Yes"}</button></div></div>`,
      }[cur];
      const m = modal(`<div class="sm-ph"><span class="sm-brandmark">◆</span><b>${esc(def.name)}</b><span class="sp"></span><button class="sm-px" data-close>✕</button></div>
        <div class="sm-ptabs">${tabs.map(([k, n]) => `<button data-tab="${k}" class="${cur === k ? "on" : ""}">${n}</button>`).join("")}</div>
        <div class="sm-pbody">${body}</div>
        <div class="sm-pfoot"><button class="sm-pbtn" data-tpl title="Every new ${esc(def.name)} starts with this style">Save as default</button><span class="sp"></span><button class="sm-pbtn" data-cancel>Cancel</button><button class="sm-pbtn sm-pok" data-close>OK</button></div>`, "sm-props");
      const changed = () => { dsave(); selBar(); };
      m.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { cur = b.dataset.tab; render(); }));
      m.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModal));
      m.querySelector("[data-cancel]").onclick = () => { Object.keys(it).forEach((k) => delete it[k]); Object.assign(it, JSON.parse(before)); changed(); closeModal(); };
      m.querySelector("[data-tpl]").onclick = (e) => { const d = toolDefaults(); const keep = {}; for (const k of ["color", "opacity", "width", "style", "fill", "fillColor", "fillOpacity", "extend", "extendLeft", "arrowStart", "arrowEnd", "fontSize", "bold", "italic", "textColor", "labelPos", "levels", "showPrices"]) if (it[k] !== undefined) keep[k] = it[k]; d[T] = keep; lset(DEF_KEY, d); e.target.textContent = "Saved ✔"; };
      m.querySelectorAll("[data-sw]").forEach((b) => (b.onclick = () => { it[b.dataset.sw] = b.dataset.c; if (b.dataset.sw === "color") D.color = b.dataset.c; changed(); render(); }));
      m.querySelectorAll("[data-swc]").forEach((inp) => (inp.oninput = () => { it[inp.dataset.swc] = inp.value; changed(); }));
      m.querySelectorAll("[data-swc]").forEach((inp) => (inp.onchange = () => render()));
      m.querySelectorAll("input[type=range][data-k]").forEach((r) => (r.oninput = () => { it[r.dataset.k] = r.dataset.scale ? +r.value / +r.dataset.scale : +r.value; r.previousElementSibling.querySelector("b").textContent = r.dataset.scale ? r.value + "%" : r.value + "px"; changed(); }));
      m.querySelectorAll("textarea[data-k]").forEach((t) => (t.oninput = () => { it[t.dataset.k] = t.value.slice(0, 120); changed(); }));
      m.querySelectorAll("[data-qlbl]").forEach((b) => (b.onclick = () => { it[T === "text" || T === "note" ? "text" : "label"] = b.dataset.qlbl; changed(); render(); }));
      m.querySelectorAll("[data-set]").forEach((b) => (b.onclick = () => { const v = b.dataset.v; it[b.dataset.set] = isNaN(+v) ? v : +v; changed(); render(); }));
      m.querySelectorAll("[data-tog]").forEach((b) => (b.onclick = () => {
        const k = b.dataset.tog;
        if (k === "extend" && T === "fib") it.extend = it.extend === false; else if (k === "fill") it.fill = it.fill === false; else if (k === "bold") it.bold = it.bold === false;
        else if (k === "showPrices") it.showPrices = it.showPrices === false; else if (k === "arrowEnd") it.arrowEnd = !(it.arrowEnd ?? (T === "arrowline" || T === "path")); else it[k] = !it[k];
        changed(); render();
      }));
      // fib levels
      const setLv = (fn) => { const lv = fibLevels(it).map((x) => ({ ...x })); fn(lv); it.levels = lv; changed(); };
      m.querySelectorAll("[data-lvon]").forEach((c) => (c.onchange = () => setLv((lv) => { lv[+c.dataset.lvon].on = c.checked; })));
      m.querySelectorAll("[data-lvv]").forEach((c) => (c.onchange = () => setLv((lv) => { lv[+c.dataset.lvv].v = +c.value; })));
      m.querySelectorAll("[data-lvc]").forEach((c) => (c.oninput = () => setLv((lv) => { lv[+c.dataset.lvc].color = c.value; })));
      m.querySelectorAll("[data-lvdel]").forEach((c) => (c.onclick = () => { setLv((lv) => lv.splice(+c.dataset.lvdel, 1)); render(); }));
      const add = m.querySelector("[data-lvadd]"); if (add) add.onclick = () => { setLv((lv) => lv.push({ v: 1.272, on: true, color: "#ffa028" })); render(); };
      const rst = m.querySelector("[data-lvreset]"); if (rst) rst.onclick = () => { delete it.levels; changed(); render(); };
      // position
      m.querySelectorAll("[data-pk]").forEach((inp) => (inp.onchange = () => {
        const v = +inp.value, k = Math.round(v / S.sess.tick);
        if (inp.dataset.pk === "qty") it.qty = Math.max(1, Math.round(v));
        else if (inp.dataset.pk === "entry") { const d = k - it.p[0].k; it.p[0] = { ...it.p[0], k }; it.p[1] = { ...it.p[1], k: it.p[1].k + d }; it.tgt += d; }
        else if (inp.dataset.pk === "stop") it.p[1] = { ...it.p[1], k }; else it.tgt = k;
        changed(); render();
      }));
      const pt = m.querySelector("[data-ptrade]"); if (pt) pt.onclick = () => { closeModal(); tradePlan(it); };
      // coordinates
      m.querySelectorAll("[data-ck]").forEach((inp) => (inp.onchange = () => { const i = +inp.dataset.ck; it.p[i] = { ...it.p[i], k: Math.round(+inp.value / S.sess.tick) }; changed(); }));
      m.querySelectorAll("[data-ct]").forEach((inp) => (inp.onchange = () => { const i = +inp.dataset.ct, t = clockToT(inp.value); if (t !== null) { it.p[i] = { ...it.p[i], t: Math.max(0, Math.min(S.sess.N, t)) }; changed(); } else inp.value = clock(it.p[i].t); }));
      m.querySelectorAll("input,textarea").forEach((x) => x.addEventListener("keydown", (e) => e.stopPropagation()));
    };
    render();
  }

  // ---- object tree: every drawing, with show / hide, lock, delete
  function openTree() {
    const render = () => {
      const rows = D.items.map((it, i) => `<div class="sm-tr ${it === D.sel ? "on" : ""}" data-ti="${i}"><span class="sm-tri">${TOOL[it.type] ? TOOL[it.type].icon : ""}</span><span class="sm-trn">${esc(TOOL[it.type] ? TOOL[it.type].name : it.type)}${it.label || it.text ? ` <i>"${esc(it.label || it.text)}"</i>` : ""}</span><span class="sm-trp">${it.p && it.p[0] ? P(it.p[0].k) : ""}</span>
        <button data-tv="${i}" title="Show / hide">${it.hidden ? "◌" : "👁"}</button><button data-tl="${i}" title="Lock">${it.locked ? "🔒" : "🔓"}</button><button data-te="${i}" title="Settings">⚙</button><button data-td="${i}" title="Delete">🗑</button></div>`).join("");
      const m = modal(`<div class="sm-ph"><span class="sm-brandmark">◆</span><b>Object tree</b><span class="dim">${D.items.length} drawing${D.items.length === 1 ? "" : "s"} · seed ${S.seed}</span><span class="sp"></span><button class="sm-px" data-close>✕</button></div>
        <div class="sm-tree">${rows || '<div class="dim" style="padding:20px;text-align:center">No drawings yet. Pick a tool on the left.</div>'}</div>
        <div class="sm-pfoot"><button class="sm-pbtn" data-showall>Show all</button><button class="sm-pbtn" data-unlock>Unlock all</button><span class="sp"></span><button class="sm-pbtn" data-delall>Delete all</button><button class="sm-pbtn sm-pok" data-close>Done</button></div>`, "sm-treem");
      m.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModal));
      m.querySelectorAll("[data-ti]").forEach((r) => (r.onclick = (e) => { if (e.target.closest("button")) return; D.sel = D.items[+r.dataset.ti]; selBar(); render(); }));
      const act = (attr, fn) => m.querySelectorAll(`[${attr}]`).forEach((b) => (b.onclick = () => { fn(D.items[+b.getAttribute(attr)]); dsave(); selBar(); render(); }));
      act("data-tv", (it) => { it.hidden = !it.hidden; }); act("data-tl", (it) => { it.locked = !it.locked; }); act("data-td", (it) => { D.items = D.items.filter((x) => x !== it); if (D.sel === it) D.sel = null; });
      m.querySelectorAll("[data-te]").forEach((b) => (b.onclick = () => openProps(D.items[+b.dataset.te])));
      m.querySelector("[data-showall]").onclick = () => { D.items.forEach((x) => (x.hidden = false)); dsave(); render(); };
      m.querySelector("[data-unlock]").onclick = () => { D.items.forEach((x) => (x.locked = false)); dsave(); render(); };
      m.querySelector("[data-delall]").onclick = () => { D.items = []; D.sel = null; dsave(); selBar(); render(); };
    };
    render();
  }

  // ---- chart settings: candle colours, background, grid, watermark
  function openChartSettings() {
    const theme = PREF.theme !== "tv" ? "dark" : "tv", base = THEMES[theme];
    PREF.custom = PREF.custom || {}; const cu = PREF.custom[theme] = PREF.custom[theme] || {};
    const row = (k, n) => `<div class="sm-pf"><label>${n}</label>${swatches("cs:" + k, cu[k] || base[k] || "#000000")}</div>`;
    const m = modal(`<div class="sm-ph"><span class="sm-brandmark">◆</span><b>Chart settings</b><span class="dim">${theme === "tv" ? "light theme" : "dark theme"}</span><span class="sp"></span><button class="sm-px" data-close>✕</button></div>
      <div class="sm-pbody sm-cs">${row("bull", "Up candle body")}${row("bear", "Down candle body")}${row("wick", "Wicks")}${row("edge", "Candle borders")}${row("bg", "Background")}${row("volUp", "Volume / delta up")}${row("volDn", "Volume / delta down")}${row("vwap", "VWAP")}
        <div class="sm-pf"><label>Grid</label><div class="sm-seg"><button data-cst="noGrid" class="${cu.noGrid ? "" : "on"}">${cu.noGrid ? "Off" : "On"}</button></div></div>
        <div class="sm-pf"><label>Watermark</label><div class="sm-seg"><button data-cst="noWater" class="${cu.noWater ? "" : "on"}">${cu.noWater ? "Off" : "On"}</button></div></div></div>
      <div class="sm-pfoot"><button class="sm-pbtn" data-reset>Reset to default</button><span class="sp"></span><button class="sm-pbtn sm-pok" data-close>Done</button></div>`, "sm-props");
    m.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModal));
    m.querySelectorAll("[data-sw]").forEach((b) => (b.onclick = () => { cu[b.dataset.sw.slice(3)] = b.dataset.c; savePref(); openChartSettings(); }));
    m.querySelectorAll("[data-swc]").forEach((i) => (i.oninput = () => { cu[i.dataset.swc.slice(3)] = i.value; savePref(); }));
    m.querySelectorAll("[data-cst]").forEach((b) => (b.onclick = () => { cu[b.dataset.cst] = !cu[b.dataset.cst]; savePref(); openChartSettings(); }));
    m.querySelector("[data-reset]").onclick = () => { PREF.custom[theme] = {}; savePref(); openChartSettings(); };
  }

  // ---- gamma: synthetic option chain by strike x expiry, for the zone the trader selects
  const EXPIRIES = [[0, "0DTE"], [1, "1D"], [2, "2D"], [5, "1W"], [10, "2W"], [21, "1M"], [42, "2M"]];
  function gammaGrid(spotK) {
    const sess = S.sess, R = E().rng((sess.seed * 7919) >>> 0), S0 = spotK * sess.tick, rows = [];
    const wts = [0.32, 0.12, 0.1, 0.18, 0.1, 0.12, 0.06];
    for (const st of sess.strikes) {
      const row = { k: st.k, call: st.call, put: st.put, byExp: [] };
      EXPIRIES.forEach(([d], j) => {
        const T = Math.max(0.5, d + 0.5) / 252, v = 0.16 + 0.02 * Math.sqrt(j), K = st.k * sess.tick, d1 = (Math.log(S0 / K) + 0.5 * v * v * T) / (v * Math.sqrt(T));
        const g = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI) / (S0 * v * Math.sqrt(T));
        const c = st.call * wts[j] * (0.6 + 0.8 * R.next()), p = st.put * wts[j] * (0.6 + 0.8 * R.next());
        row.byExp.push({ call: c, put: p, gex: g * S0 * S0 * 0.01 * (c - p) * sess.pointValue, cg: g * S0 * S0 * 0.01 * c * sess.pointValue, pg: g * S0 * S0 * 0.01 * p * sess.pointValue });   // $ per 1% move
      });
      row.gex = row.byExp.reduce((a, x) => a + x.gex, 0);
      row.cg = row.byExp.reduce((a, x) => a + x.cg, 0); row.pg = row.byExp.reduce((a, x) => a + x.pg, 0); row.abs = row.cg + row.pg;
      rows.push(row);
    }
    return rows;
  }
  const fmtG = (v) => { const a = Math.abs(v); return (v < 0 ? "-" : "+") + "$" + (a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : (a / 1e3).toFixed(0) + "K"); };
  function loadPlotlySim() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (!window.__plotlyP) window.__plotlyP = new Promise((res, rej) => { const sc = document.createElement("script"); sc.src = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"; sc.onload = () => res(window.Plotly); sc.onerror = () => { window.__plotlyP = null; rej(new Error("could not load the 3D chart library")); }; document.head.appendChild(sc); });
    return window.__plotlyP;
  }
  async function openGamma(lo, hi) {
    const sess = S.sess, spot = lastK(), grid = gammaGrid(spot), step = sess.strikeStep;
    const inZone = grid.filter((r) => r.k >= lo - step / 2 && r.k <= hi + step / 2), wide = grid.filter((r) => r.k >= lo - 3 * step && r.k <= hi + 3 * step);
    const net = inZone.reduce((a, r) => a + r.gex, 0), callOI = inZone.reduce((a, r) => a + r.call, 0), putOI = inZone.reduce((a, r) => a + r.put, 0);
    const top = inZone.slice().sort((a, b) => Math.abs(b.gex) - Math.abs(a.gex))[0], lv = sess.levels;
    const walls = [["Call wall", lv.callWall], ["Put wall", lv.putWall], ["Zero gamma", lv.zeroGamma], ["HVL", lv.hvl]].filter(([, k]) => k !== null && k >= lo - 2 && k <= hi + 2);
    const pos = net >= 0, nearExp = inZone.reduce((a, r) => a + r.byExp[0].gex + r.byExp[1].gex, 0);
    const explain = pos
      ? `Dealers are <b>long gamma</b> here (${fmtG(net)} per 1% move). As price rises into this zone they sell futures, as it falls they buy: expect <b>mean reversion and pinning</b>${top ? `, strongest around <b>${P(top.k)}</b>` : ""}. Fading the edges works better than chasing breakouts while the zone holds.`
      : `Dealers are <b>short gamma</b> here (${fmtG(net)} per 1% move). They buy as price rises and sell as it falls: <b>moves extend and volatility expands</b>${top ? `, especially through <b>${P(top.k)}</b>` : ""}. Breakouts are more likely to run; fading is dangerous.`;
    const m = modal(`<div class="sm-ph"><span class="sm-brandmark">γ</span><b>Gamma zone ${P(lo)} – ${P(hi)}</b><span class="dim">${Math.round((hi - lo) * sess.tick * 100) / 100} pts · spot ${P(spot)} · ${spot >= lo && spot <= hi ? "price is inside" : spot > hi ? "price is above" : "price is below"}</span><span class="sp"></span><button class="sm-px" data-close>✕</button></div>
      <div class="sm-gstats">
        <div class="${pos ? "up" : "dn"}"><span>NET GAMMA (zone)</span><b>${fmtG(net)}</b><i>${pos ? "long γ · dampening" : "short γ · amplifying"}</i></div>
        <div><span>CALL OI / PUT OI</span><b>${Math.round(callOI).toLocaleString()} / ${Math.round(putOI).toLocaleString()}</b><i>put/call ${(putOI / Math.max(1, callOI)).toFixed(2)}</i></div>
        <div><span>DOMINANT STRIKE</span><b>${top ? P(top.k) : "—"}</b><i>${top ? fmtG(top.gex) : ""}</i></div>
        <div><span>0DTE + 1D SHARE</span><b>${net ? Math.round(Math.abs(nearExp / net) * 100) : 0}%</b><i>short-dated = faster decay</i></div>
        <div><span>LEVELS INSIDE</span><b>${walls.length ? walls.map(([n]) => n).join(", ") : "none"}</b><i>${walls.map(([, k]) => P(k)).join(" · ")}</i></div>
      </div>
      <div class="sm-gexp">${explain}</div>
      <div class="sm-gviews">${[["3d", "◆ 3D SURFACE", "Strike × expiry × gamma, rotate it"], ["bars", "▮ GAMMA BARS", "Call and put gamma per strike, like candles"], ["curve", "∿ GAMMA CURVE", "Absolute GEX profile with spot and peak, like a trend line"]].map(([k, l, tt]) => `<button data-gv="${k}" title="${tt}" class="${(PREF.gview || "3d") === k ? "on" : ""}">${l}</button>`).join("")}<span class="sp"></span><span class="dim sm-gvhint" id="sm-gvhint"></span></div>
      <div class="sm-gview" id="sm-gview"><div class="dim" style="padding:40px;text-align:center">Loading…</div></div>
      <div class="sm-gnote dim">Synthetic option chain for this simulated market (strike × expiry), gamma from Black-Scholes at the current price; $ = dealer hedging per 1% move. Click a legend item to hide or show it.</div>`, "sm-gmod");
    m.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModal));
    // three ways to read the same zone: 3D surface (strike x expiry), gamma bars per strike, and the absolute-GEX curve
    const draw = async (view) => {
      const el = $("#sm-gview"); if (!el) return;
      const Pl = await loadPlotlySim(); if ($("#sm-pmod").hidden) return;
      Pl.purge(el); el.innerHTML = "";
      const light = PREF.theme === "tv", fg = light ? "#2b2a2f" : "#d8dbe2", bgc = light ? "#f4f0e8" : "#0b0b0d", gc = light ? "#ddd5c8" : "#1d1d22";
      const X = (k) => +(k * sess.tick).toFixed(2), full = grid.filter((r) => r.k >= lo - 8 * step && r.k <= hi + 8 * step);
      const peak = full.reduce((a, r) => (r.abs > a.abs ? r : a), full[0] || { abs: 0, k: spot });
      const zoneShape = { type: "rect", xref: "x", yref: "paper", x0: X(lo), x1: X(hi), y0: 0, y1: 1, fillcolor: "rgba(255,160,40,0.10)", line: { width: 1, color: "rgba(255,160,40,0.55)", dash: "dot" }, layer: "below" };
      const vline = (k, col, dash, w) => ({ type: "line", xref: "x", yref: "paper", x0: X(k), x1: X(k), y0: 0, y1: 1, line: { color: col, width: w || 2, dash } });
      const note = (k, text, col, y) => ({ x: X(k), y: y ?? 1.04, xref: "x", yref: "paper", text, showarrow: false, font: { color: col, size: 12, family: "JetBrains Mono, monospace" } });
      const lvAnn = full.length ? [["Call wall", lv.callWall, "#1f9d7a"], ["Put wall", lv.putWall, "#d6372e"], ["Zero γ", lv.zeroGamma, "#ffa028"]].filter(([, k]) => k !== null && k >= full[0].k && k <= full[full.length - 1].k) : [];
      const base = { paper_bgcolor: bgc, plot_bgcolor: bgc, margin: { l: 64, r: 64, t: 46, b: 46 }, font: { family: "JetBrains Mono, monospace", color: fg, size: 11 }, hovermode: "x unified",
        legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.13, font: { size: 11 } }, xaxis: { title: { text: "STRIKE", font: { size: 11 } }, gridcolor: gc, tickformat: ".0f", zeroline: false } };
      const hint = $("#sm-gvhint");
      if (view === "3d") {
        if (hint) hint.textContent = "drag to rotate · scroll to zoom";
        const strikes = wide.map((r) => r.k * sess.tick), z = EXPIRIES.map((_, j) => wide.map((r) => r.byExp[j].gex / 1e6));
        const zMax = Math.max(1e-6, ...z.flat().map(Math.abs));
        await Pl.newPlot(el, [{ type: "surface", x: strikes, y: EXPIRIES.map((e) => e[0]), z, colorscale: [[0, "#d6372e"], [0.45, "#f3b3a6"], [0.5, "#f2efe9"], [0.55, "#a8dccb"], [1, "#1f9d7a"]], cmin: -zMax, cmax: zMax, showscale: true,
          colorbar: { title: { text: "$M / 1%", font: { color: fg, size: 11 } }, tickfont: { color: fg, size: 10 }, len: 0.7, thickness: 12 }, contours: { z: { show: true, usecolormap: true, project: { z: true }, width: 1 } }, lighting: { ambient: 0.75, diffuse: 0.6, specular: 0.15, roughness: 0.6 },
          hovertemplate: "strike %{x:.2f}<br>%{y} days<br>%{z:.2f} $M per 1%<extra></extra>" },
          { type: "scatter3d", mode: "lines", x: [lo * sess.tick, lo * sess.tick, hi * sess.tick, hi * sess.tick, lo * sess.tick], y: [0, 0, 0, 0, 0], z: [0, 0, 0, 0, 0], line: { color: "#ffa028", width: 6 }, hoverinfo: "skip", showlegend: false },
          { type: "scatter3d", mode: "lines", x: [spot * sess.tick, spot * sess.tick], y: [0, 42], z: [0, 0], line: { color: "#2f6fe0", width: 5, dash: "dash" }, hoverinfo: "skip", showlegend: false }],
          { paper_bgcolor: bgc, margin: { l: 0, r: 0, t: 0, b: 0 }, font: { family: "JetBrains Mono, monospace", color: fg },
            scene: { bgcolor: bgc, xaxis: { title: "Strike", color: fg, gridcolor: gc }, yaxis: { title: "Days to expiry", color: fg, gridcolor: gc, tickvals: EXPIRIES.map((e) => e[0]), ticktext: EXPIRIES.map((e) => e[1]) }, zaxis: { title: "$M / 1%", color: fg, gridcolor: gc }, camera: { eye: { x: 1.6, y: -1.5, z: 0.9 } }, aspectmode: "manual", aspectratio: { x: 1.4, y: 1, z: 0.7 } } },
          { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["toImage"] });
      } else if (view === "bars") {
        if (hint) hint.textContent = "green = call gamma (dealers dampen) · red = put gamma (dealers amplify) · line = net";
        const xs = full.map((r) => X(r.k)), inZ = (r) => r.k >= lo && r.k <= hi;
        await Pl.newPlot(el, [
          { type: "bar", x: xs, y: full.map((r) => r.cg / 1e6), name: "Call γ", marker: { color: full.map((r) => (inZ(r) ? "#1fbf8f" : "rgba(31,191,143,0.45)")), line: { color: "#0d7a5a", width: 1 } }, hovertemplate: "%{x}: +%{y:.2f} $M<extra>call γ</extra>" },
          { type: "bar", x: xs, y: full.map((r) => -r.pg / 1e6), name: "Put γ", marker: { color: full.map((r) => (inZ(r) ? "#ef4b4b" : "rgba(239,75,75,0.45)")), line: { color: "#a32020", width: 1 } }, customdata: full.map((r) => r.pg / 1e6), hovertemplate: "%{x}: −%{customdata:.2f} $M<extra>put γ</extra>" },
          { type: "scatter", mode: "lines+markers", x: xs, y: full.map((r) => r.gex / 1e6), name: "Net GEX", line: { color: light ? "#2b2a2f" : "#ffffff", width: 2.2, shape: "spline" }, marker: { size: 5 }, hovertemplate: "%{x}: %{y:.2f} $M<extra>net</extra>" },
          { type: "scatter", mode: "lines", x: xs, y: full.map((r) => r.call / 1e3), name: "Call OI (k)", yaxis: "y2", visible: "legendonly", line: { color: "#2f6fe0", dash: "dot" } },
          { type: "scatter", mode: "lines", x: xs, y: full.map((r) => r.put / 1e3), name: "Put OI (k)", yaxis: "y2", visible: "legendonly", line: { color: "#ffa028", dash: "dot" } }],
          { ...base, barmode: "relative", bargap: 0.18, yaxis: { title: { text: "GAMMA $M / 1%", font: { size: 11 } }, gridcolor: gc, zerolinecolor: fg, zerolinewidth: 1.5 }, yaxis2: { overlaying: "y", side: "right", showgrid: false, visible: false },
            shapes: [zoneShape, vline(spot, "#ffa028", "solid", 2), ...lvAnn.map(([, k, c]) => vline(k, c, "dash", 1.4))],
            annotations: [note(spot, `Spot: ${P(spot)}`, "#ffa028"), ...lvAnn.map(([n, k, c], i) => note(k, n, c, 0.97 - i * 0.05))] },
          { displaylogo: false, responsive: true, displayModeBar: false });
      } else {
        if (hint) hint.textContent = "absolute GEX profile · purple line = peak gamma (strongest magnet)";
        const xs = full.map((r) => X(r.k)), absMax = Math.max(1e-9, ...full.map((r) => r.abs));
        await Pl.newPlot(el, [
          { type: "scatter", mode: "lines+markers", x: xs, y: full.map((r) => r.abs / absMax), name: "Abs GEX", fill: "tozeroy", fillcolor: "rgba(150,130,220,0.45)", line: { color: "#8f7fe0", width: 2.4, shape: "spline", smoothing: 1.1 }, marker: { size: 6, color: "#7c6ad6" }, customdata: full.map((r) => r.abs / 1e6), hovertemplate: "%{x}: %{customdata:.2f} $M<extra>abs GEX</extra>" },
          { type: "scatter", mode: "lines", x: xs, y: full.map((r) => r.gex / absMax), name: "Net GEX", line: { color: "#1fbf8f", width: 2, shape: "spline" }, visible: "legendonly", customdata: full.map((r) => r.gex / 1e6), hovertemplate: "%{x}: %{customdata:.2f} $M<extra>net GEX</extra>" },
          { type: "scatter", mode: "lines", x: xs, y: full.map((r) => r.call), name: "Call OI", yaxis: "y2", line: { color: "#5fd39a", width: 1.6, shape: "spline" }, visible: "legendonly" },
          { type: "scatter", mode: "lines", x: xs, y: full.map((r) => r.put), name: "Put OI", yaxis: "y2", line: { color: "#f08a8a", width: 1.6, shape: "spline" }, visible: "legendonly" }],
          { ...base, yaxis: { title: { text: "GEX (normalised)", font: { size: 11 } }, gridcolor: gc, range: [0, 1.05], zeroline: false }, yaxis2: { overlaying: "y", side: "right", showgrid: false, visible: false, rangemode: "tozero" },
            shapes: [zoneShape, vline(peak.k, "#8f5fd8", "solid", 2.2), vline(spot, "#ffa028", "solid", 2.2)],
            annotations: [note(spot, `Spot: ${P(spot)}`, "#ffa028"), note(peak.k, `Peak γ ${P(peak.k)}`, "#8f5fd8", 0.99)] },
          { displaylogo: false, responsive: true, displayModeBar: false });
      }
    };
    const showErr = (e) => { const el = $("#sm-gview"); if (el) el.innerHTML = `<div class="dim" style="padding:40px">${esc(e.message || String(e))}</div>`; };
    m.querySelectorAll("[data-gv]").forEach((b) => (b.onclick = () => { m.querySelectorAll("[data-gv]").forEach((x) => x.classList.toggle("on", x === b)); PREF.gview = b.dataset.gv; lset("nx_sim_pref", PREF); draw(b.dataset.gv).catch(showErr); }));
    try { await draw(PREF.gview || "3d"); } catch (e) { showErr(e); }
  }

  // ------------------------------------------------------------------ chart mouse: zoom, pan, drag the bracket
  function chartEvents(c) {
    const pos = (e) => { const r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    c.addEventListener("wheel", (e) => { e.preventDefault(); if (e.shiftKey) { S.view.yZoom = Math.max(0.2, Math.min(8, S.view.yZoom * (e.deltaY > 0 ? 0.9 : 1.1))); return; } const f = e.deltaY > 0 ? 1.15 : 0.87; S.view.span = Math.max(S.tf * 15, Math.min(S.sess.N, S.view.span * f)); }, { passive: false });
    c.addEventListener("mousedown", (e) => {
      const p = pos(e), g = S.g; if (!g) return;
      if (e.button !== 0) return;
      if (p.x < g.plotW && drawDown(p, g, e)) return;
      const hit = S.orders.find((o) => Math.abs(g.y(o.k) - p.y) < 6);
      if (hit) { S.drag = { kind: "order", o: hit }; return; }
      S.drag = { kind: p.x > g.plotW + g.htf ? "y" : "pan", x: p.x, y: p.y, right: S.view.right, yOff: S.view.yOff, yZoom: S.view.yZoom };
    });
    window.addEventListener("mousemove", (e) => {
      if (!S.active) return; const p = pos(e), g = S.g; if (!g) return;
      S.hover = p.x >= 0 && p.y >= 0 && p.x <= g.w && p.y <= g.h ? p : null;
      if (drawMove(p, g)) return;
      if (!S.drag && D.tool === "cursor") { const hh = p.x < g.plotW ? hitItem(g, p.x, p.y) : null; D.hover = hh ? hh.it : null; if (hh && hh.it.locked) { c.style.cursor = "not-allowed"; return; } }
      if (!S.drag) { c.style.cursor = D.tool !== "cursor" ? "copy" : hitItem(g, p.x, p.y) ? (D.hover && typeof (hitItem(g, p.x, p.y) || {}).part !== "string" ? "grab" : "move") : S.orders.some((o) => Math.abs(g.y(o.k) - p.y) < 6) ? "ns-resize" : "crosshair"; return; }
      if (S.drag.kind === "order") { const k = Math.round(g.k(p.y)); if (k !== S.drag.o.k) moveOrder(S.drag.o, k); }
      else if (S.drag.kind === "pan") { const dt = ((p.x - S.drag.x) / g.plotW) * S.view.span; S.view.follow = false; S.view.right = Math.min(S.simT + S.view.span * 0.3, S.drag.right - dt); S.view.yOff = S.drag.yOff + ((p.y - S.drag.y) / (g.h - g.padB)) * (g.pMax - g.pMin); }
      else if (S.drag.kind === "y") { S.view.yZoom = Math.max(0.2, Math.min(8, S.drag.yZoom * Math.exp((S.drag.y - p.y) / 150))); }
    });
    window.addEventListener("mouseup", (e) => { drawUp(pos(e)); if (S.drag && S.drag.kind === "order") ui(true); S.drag = null; });
    c.addEventListener("dblclick", (e) => { const g = S.g, r = c.getBoundingClientRect(); if (g && hitItem(g, e.clientX - r.left, e.clientY - r.top)) return; S.view.follow = true; S.view.yOff = 0; S.view.yZoom = 1; });
    c.addEventListener("mouseleave", () => { S.hover = null; });
  }

  // ------------------------------------------------------------------ side panels (throttled)
  function ui(force) {
    if (!S.sess) return;
    $("#sm-clock").textContent = clock(S.simT);
    const a = S.acct, lk = lastK(), op = openPnl(lk), net = a.realized + op - a.fees;
    const closed = S.trades, wins = closed.filter((t) => t.net > 0).length;
    $("#sm-acct").innerHTML = `<div class="sm-kv"><span>POSITION</span><b class="${a.pos > 0 ? "up" : a.pos < 0 ? "dn" : ""}">${a.pos ? `${a.pos > 0 ? "LONG" : "SHORT"} ${Math.abs(a.pos)} @ ${P(a.avg)}` : "FLAT"}</b></div>
      <div class="sm-kv"><span>OPEN P&amp;L</span><b class="${op >= 0 ? "up" : "dn"}">${money(op)}</b></div><div class="sm-kv"><span>NET (after fees)</span><b class="${net >= 0 ? "up" : "dn"}">${money(net)}</b></div>
      <div class="sm-kv"><span>TRADES</span><b>${closed.length} · ${closed.length ? Math.round((wins / closed.length) * 100) : 0}% win</b></div><div class="sm-kv"><span>LAST</span><b>${P(lk)}</b></div>`;
    a.peak = Math.max(a.peak, net); a.dd = Math.min(a.dd, net - a.peak);
    if (!a.equity.length || S.simT - a.equity[a.equity.length - 1][0] >= 20) a.equity.push([S.simT, net]);
    // tape
    const tp = $("#sm-tape"), p = S.sess.prints, rows = [];
    for (let j = S.pIdx - 1; j >= 0 && rows.length < 34 && j > S.pIdx - 4000; j--) { if (p.q[j] < PREF.minPrint) continue; rows.push(`<div class="${p.side[j] > 0 ? "up" : "dn"}${p.q[j] >= 40 ? " big" : ""}"><span>${clock(p.t[j])}</span><span>${P(p.k[j])}</span><span>${p.q[j]}</span></div>`); }
    tp.innerHTML = rows.join("");
    // gamma panel every 30 sim-seconds
    if (force || !S.gexCache || Math.abs(S.simT - S.gexCache.t) > 30) drawGex();
  }
  function drawGex() {
    const c = $("#sm-gex"); if (!c || !c._w) return;
    const lk = lastK(), gx = E().gex(S.sess.strikes, lk, S.sess.tick); S.gexCache = { t: S.simT };
    const ctx = c.getContext("2d"); ctx.setTransform(c._dpr, 0, 0, c._dpr, 0, 0);
    const th = TH(), w = c._w, h = c._h; ctx.fillStyle = th.domBg; ctx.fillRect(0, 0, w, h);
    const rows = gx.byStrike.filter((r) => Math.abs(r.k - lk) <= 8 * S.sess.strikeStep), mx = Math.max(1, ...rows.map((r) => Math.abs(r.gex)));
    const rh = Math.max(8, Math.min(16, (h - 6) / rows.length)), mid = w * 0.55;
    ctx.font = "10px JetBrains Mono, monospace"; ctx.textBaseline = "middle";
    rows.slice().reverse().forEach((r, i) => {
      const y = 3 + i * rh, ww = (Math.abs(r.gex) / mx) * (w - mid - 6);
      ctx.fillStyle = r.gex >= 0 ? "rgba(43,224,126,0.7)" : "rgba(255,74,74,0.7)"; ctx.fillRect(r.gex >= 0 ? mid : mid - ww, y + 1, ww, rh - 2);
      ctx.fillStyle = Math.abs(r.k - lk) < S.sess.strikeStep / 2 ? "#ffa028" : th.dim; ctx.fillText(P(r.k), 4, y + rh / 2);
      const lv = S.sess.levels; const tag = r.k === lv.callWall ? "CW" : r.k === lv.putWall ? "PW" : ""; if (tag) { ctx.fillStyle = tag === "CW" ? "#2be07e" : "#ff4a4a"; ctx.fillText(tag, 62, y + rh / 2); }
    });
    ctx.strokeStyle = "#444"; ctx.beginPath(); ctx.moveTo(mid, 0); ctx.lineTo(mid, h); ctx.stroke();
    const pos = lk >= S.sess.levels.zeroGamma;
    $("#sm-greg").innerHTML = `· net ${gx.total >= 0 ? "+" : "-"} · <b style="color:${pos ? "#2be07e" : "#ff4a4a"}">${pos ? "POSITIVE γ: mean-reverting, pins" : "NEGATIVE γ: trending, moves extend"}</b>`;
  }

  // ------------------------------------------------------------------ LEARN
  function learn() {
    const m = $("#sm-modal"); m.hidden = false; setPlay(false);
    m.innerHTML = `<div class="sm-mbox"><div class="sm-mh"><b>HOW TO READ THIS SCREEN</b><span class="sp"></span><button class="sm-b" id="sm-mx">CLOSE</button></div><div class="sm-learn">
      <h4>Liquidity heatmap</h4><p>Each horizontal streak is resting limit orders at that price over time; brighter = bigger. A bright line that price stops at is real liquidity. A bright line that disappears as price approaches was a <b>spoof</b>. Price is drawn to thick liquidity and moves fast through empty (dark) areas.</p>
      <h4>Bubbles &amp; tape</h4><p>Bubbles are large aggressive trades (green = buyers lifting the offer, red = sellers hitting the bid). Clusters of bubbles at one price with no progress = <b>absorption</b>.</p>
      <h4>Delta &amp; CVD</h4><p>Delta = aggressive buy volume minus sell volume per bar; CVD is its running total. Price up with CVD down (or the reverse) is a <b>divergence</b>: the aggressors are being absorbed. Big delta with no price progress = absorption. Price moving on light delta = no one is defending.</p>
      <h4>Stop runs</h4><p>Stops sit just beyond obvious highs and lows. When price pokes through, they trigger (a burst of market orders), and a passive player often absorbs them. If price comes back inside quickly, the breakout failed: fade it, stop beyond the extreme.</p>
      <h4>Gamma</h4><p>Above <b>zero gamma</b> dealers are long gamma: they sell rallies and buy dips, so price mean-reverts and pins to big strikes (HVL). Below it they are short gamma and chase moves: trends extend. The <b>call wall</b> often caps rallies, the <b>put wall</b> supports dips, until they break.</p>
      <h4>VWAP</h4><p>The volume-weighted average price of the session. In a trend, pullbacks to VWAP are the classic entry; in balance, ±1σ / ±2σ bands are fade zones.</p>
      <h4>Orders</h4><p>B / S = market buy / sell (fills against the book, slippage included). Click the DOM: BID column = buy limit, ASK = sell limit; you join the queue and fill only when enough volume trades at your price. The bracket puts a stop and target on every entry: drag them on the chart. F = flatten, R = reverse, C = cancel limits, space = play / pause, ↑↓ = speed.</p>
      <h4>Report</h4><p>END &amp; REPORT grades the process, not just P&amp;L: stops, chasing, fighting the gamma regime, buying into walls, revenge trades, cutting winners. It also shows what the market was really doing (the hidden script) and the trades you could have taken.</p></div></div>`;
    $("#sm-mx").onclick = () => { m.hidden = true; };
  }

  // ------------------------------------------------------------------ REPORT
  const MISTAKE = { nostop: "No stop", tightstop: "Stop inside the noise", widestop: "Stop too wide", movedstop: "Moved the stop further away", avgdown: "Averaged down (added to a loser)",
    chase: "Chased a move", countertrend: "Counter-trend with no reason", wall: "Traded into a liquidity wall", gamma: "Fought the gamma level", neggamma: "Faded a move in negative gamma",
    divergence: "Traded against CVD divergence", revenge: "Revenge trade after a loss", sizeup: "Sized up after a loss", cutwinner: "Cut the winner early", heldloser: "Held a loser without a stop",
    nosetup: "No setup: nothing supported the entry" };
  const STRENGTH = { trend: "Traded with the trend", absorb: "Entered on absorption", sweep: "Faded a failed stop run", wallbehind: "Had a liquidity wall behind the entry", vwap: "VWAP pullback in a trend",
    respect: "Respected the stop", bigR: "Let a winner run (2R+)", level: "Used a gamma level correctly", band: "Faded the VWAP band in positive gamma", mark: "Traded from a level you marked", breakout: "Traded a breakout in negative gamma" };
  function grade(tr) {
    const c = tr.ctx, neg = [], pos = []; let sc = 100;
    const side = tr.side, atr = c.atr, stopT = tr.riskTicks;
    const bad = (id, pts, txt) => { sc -= pts; neg.push({ id, txt }); }, good = (id, pts, txt) => { sc += pts; pos.push({ id, txt }); };
    if (stopT === null) bad("nostop", 25, "No stop: the loss was unlimited");
    else if (stopT < 0.6 * atr) bad("tightstop", 10, `Stop ${stopT} ticks is inside the noise (the 1-minute range is about ${atr.toFixed(0)} ticks)`);
    else if (stopT > 4 * atr) bad("widestop", 6, `Stop ${stopT} ticks is very wide for this volatility`);
    if (tr.stopMoves.some((m) => m.wider)) bad("movedstop", 20, "Moved the stop further away: a planned loss became a bigger one");
    if (tr.addedToLoser) bad("avgdown", 18, "Added to a losing position");
    if (c.chase) bad("chase", 12, `Entered after a ${Math.abs(c.mom60)}-tick move in the last minute`);
    const reasonAbsorb = c.absorb && c.absorb.side === side, reasonSweep = c.recentSweep && c.sweepDir === -side;
    const reasonLevel = c.near && Math.abs(c.near.d) <= 8 && ((side > 0 && (c.near.n === "put wall" || c.near.n === "HVL") && c.near.d <= 0) || (side < 0 && (c.near.n === "call wall" || c.near.n === "HVL") && c.near.d >= 0)) && c.gpos;
    const reasonVwap = Math.abs(c.vwSig) < 0.4 && c.withTrend, reasonBand = Math.abs(c.vwSig) > 1.6 && Math.sign(c.vwSig) === -side && c.gpos, reasonBreak = ((side > 0 && c.k > c.hi30) || (side < 0 && c.k < c.lo30)) && !c.gpos;
    if (c.againstTrend && !reasonAbsorb && !reasonSweep && !reasonLevel) bad("countertrend", 10, "Against the 15-minute trend with no absorption, failed breakout or level to justify it");
    if (c.wallAhead && c.wallAhead.d <= 6) bad("wall", 8, `${side > 0 ? "Bought into" : "Sold into"} a ${c.wallAhead.size}-lot wall ${c.wallAhead.d} ticks away`);
    if (c.gpos && c.near && Math.abs(c.near.d) <= 8 && ((side > 0 && c.near.n === "call wall" && c.near.d >= 0) || (side < 0 && c.near.n === "put wall" && c.near.d <= 0))) bad("gamma", 10, `${side > 0 ? "Bought under the call wall" : "Sold above the put wall"} in positive gamma: dealers lean against you there`);
    if (!c.gpos && c.againstTrend) bad("neggamma", 5, "Faded the move in negative gamma: dealers were chasing it");
    if (c.divergence && Math.sign(c.cvd5) !== side && Math.sign(c.px5) === side) bad("divergence", 6, "CVD was diverging against the direction you traded");
    if (tr.revenge) bad("revenge", 15, "Re-entered within a minute of a loss");
    if (tr.sizeUpAfterLoss) bad("sizeup", 10, "Increased size right after a loss");
    const reasons = [reasonAbsorb, reasonSweep, reasonLevel, reasonVwap, reasonBand, reasonBreak, c.withTrend, !!c.mark].filter(Boolean).length;
    if (c.mark) good("mark", 6, `Entered at ${c.mark}: a level you marked before the trade`);
    if (!reasons) bad("nosetup", 40, "No setup: no trend, level, absorption, failed breakout or VWAP context supported this entry");
    if (c.withTrend) good("trend", 3, "With the 15-minute trend");
    if (reasonAbsorb) good("absorb", 10, `Absorption on your side at ${P(c.absorb.k)} (${c.absorb.vol} lots without progress)`);
    if (reasonSweep) good("sweep", 10, "Faded a failed stop run");
    if (reasonLevel) good("level", 8, `Traded off the ${c.near.n} in positive gamma`);
    if (c.wallBehind && c.wallBehind.d <= 6) good("wallbehind", 3, `A ${c.wallBehind.size}-lot wall behind your entry`);
    if (reasonVwap) good("vwap", 6, "Pullback to VWAP in a trend");
    if (reasonBand) good("band", 6, `Faded ${Math.abs(c.vwSig).toFixed(1)}σ from VWAP in positive gamma (mean-reverting regime)`);
    if (reasonBreak) good("breakout", 6, "Broke the 30-minute range in negative gamma (moves extend)");
    if (stopT !== null && !tr.stopMoves.some((m) => m.wider) && tr.exitTag === "stop") pos.push({ id: "respect", txt: "Respected the stop" });
    if (tr.net > 0 && tr.exitK !== null) { const got = (tr.exitK - tr.entryK) * side; if (tr.mfe >= 2.2 * Math.max(1, got) && tr.exitTag !== "target" && tr.mfe > atr) bad("cutwinner", 6, `Price went ${tr.mfe} ticks your way, you took ${Math.round(got)}`); }
    if (tr.R !== null && tr.R >= 2) good("bigR", 4, `${tr.R.toFixed(1)}R winner`);
    if (tr.net < 0 && stopT === null && Math.abs(tr.mae) > 3 * atr) bad("heldloser", 10, `Held a loser ${Math.abs(tr.mae)} ticks against you`);
    sc = Math.max(0, Math.min(100, Math.round(sc)));
    const letter = sc >= 85 ? "A" : sc >= 74 ? "B" : sc >= 62 ? "C" : sc >= 50 ? "D" : "F";
    return { score: sc, letter, neg, pos };
  }
  function setupName(tr) {
    const c = tr.ctx, side = tr.side;
    if (c.absorb && c.absorb.side === side) return "Absorption reversal";
    if (c.recentSweep && c.sweepDir === -side) return "Stop-run fade";
    if (c.gpos && c.near && Math.abs(c.near.d) <= 10 && ((side < 0 && c.near.n === "call wall") || (side > 0 && c.near.n === "put wall"))) return "Fade at a gamma wall";
    if (Math.abs(c.vwSig) < 0.4 && c.withTrend) return "VWAP pullback with the trend";
    if ((side > 0 && c.k > c.hi30) || (side < 0 && c.k < c.lo30)) return c.chase ? "Breakout chase" : "Breakout";
    if (c.chase) return "Momentum chase";
    if (c.againstTrend) return "Counter-trend fade";
    if (Math.abs(c.vwSig) > 1.6 && Math.sign(c.vwSig) === -side) return "Fade at the VWAP band";
    if (c.mark) return "Your marked level";
    return "Unclassified (impulse)";
  }
  function analyse() {
    const sess = S.sess, tr = S.trades;
    // behaviour flags that need the sequence
    tr.forEach((t, i) => { const prev = tr[i - 1]; if (prev && prev.net < 0 && t.openT - prev.closeT < 60) t.revenge = true; if (prev && prev.net < 0 && t.maxPos > prev.maxPos) t.sizeUpAfterLoss = true; t.grade = grade(t); t.setup = setupName(t); });
    const net = tr.reduce((a, t) => a + t.net, 0), wins = tr.filter((t) => t.net > 0), losses = tr.filter((t) => t.net <= 0);
    const gw = wins.reduce((a, t) => a + t.net, 0), gl = -losses.reduce((a, t) => a + t.net, 0);
    const Rs = tr.filter((t) => t.R !== null).map((t) => t.R);
    let eq = 0, peak = 0, dd = 0; const curve = [[0, 0]]; tr.forEach((t) => { eq += t.net; peak = Math.max(peak, eq); dd = Math.min(dd, eq - peak); curve.push([t.closeT, eq]); });
    const setups = {}; tr.forEach((t) => { const s = setups[t.setup] = setups[t.setup] || { n: 0, w: 0, net: 0, R: [] }; s.n++; if (t.net > 0) s.w++; s.net += t.net; if (t.R !== null) s.R.push(t.R); });
    const mistakes = {}; tr.forEach((t) => t.grade.neg.forEach((m) => { const k = MISTAKE[m.id] || m.id; (mistakes[k] = mistakes[k] || { n: 0, trades: [] }).n++; mistakes[k].trades.push(t.id); }));
    const strengths = {}; tr.forEach((t) => t.grade.pos.forEach((m) => { const k = STRENGTH[m.id] || m.id; (strengths[k] = strengths[k] || { n: 0, trades: [] }).n++; strengths[k].trades.push(t.id); }));
    // missed opportunities: the hidden script's ideal trades the trader did not take
    const missed = [];
    for (const e of sess.truth) {
      if (!e.ideal) continue;
      const id = e.ideal, took = tr.some((t) => t.side === id.side && Math.abs(t.openT - id.entryT) < 420);
      const out = simIdeal(id);
      missed.push({ e, took, out });
    }
    // the biggest swings of the day (zig-zag on 1-minute closes) and what preceded them
    const swings = zigzag();
    const avgGrade = tr.length ? tr.reduce((a, t) => a + t.grade.score, 0) / tr.length : null;
    const overtrade = tr.length > 0 ? tr.length / (sess.N / 3600) : 0;
    return { net, n: tr.length, win: tr.length ? wins.length / tr.length : 0, avgW: wins.length ? gw / wins.length : 0, avgL: losses.length ? -gl / losses.length : 0, pf: gl ? gw / gl : (gw ? Infinity : 0),
      expR: Rs.length ? Rs.reduce((a, b) => a + b, 0) / Rs.length : null, dd, curve, setups, mistakes, strengths, missed, swings, avgGrade, overtrade, fees: S.acct.fees };
  }
  function simIdeal(id) {
    const s = S.sess.sec, t0 = Math.floor(id.entryT);
    for (let t = t0; t < Math.min(S.sess.N, t0 + 3600); t++) {
      if (id.side > 0) { if (s.l[t] <= id.stop) return { R: -1, t }; if (s.h[t] >= id.target) return { R: Math.abs(id.target - id.entry) / Math.abs(id.entry - id.stop), t }; }
      else { if (s.h[t] >= id.stop) return { R: -1, t }; if (s.l[t] <= id.target) return { R: Math.abs(id.target - id.entry) / Math.abs(id.entry - id.stop), t }; }
    }
    return { R: null, t: null };
  }
  function zigzag() { // the biggest swings of the played part of the session, on 1-minute closes
    const c = candles(60), upto = Math.min(c.n, Math.floor(S.simT / 60)), th = Math.max(12, atr1m(S.simT) * 1.6), out = [];
    if (upto < 5) return out;
    let dir = 0, hiK = c.c[0], hiI = 0, loK = c.c[0], loI = 0, pivK = 0, pivI = 0, extK = 0, extI = 0;
    for (let i = 1; i < upto; i++) {
      const v = c.c[i];
      if (dir === 0) {
        if (v > hiK) { hiK = v; hiI = i; } if (v < loK) { loK = v; loI = i; }
        if (v - loK >= th) { dir = 1; pivK = loK; pivI = loI; extK = v; extI = i; } else if (hiK - v >= th) { dir = -1; pivK = hiK; pivI = hiI; extK = v; extI = i; }
      } else if (dir === 1) {
        if (v > extK) { extK = v; extI = i; } else if (extK - v >= th) { out.push({ dir: 1, from: pivK, to: extK, i0: pivI, i1: extI }); dir = -1; pivK = extK; pivI = extI; extK = v; extI = i; }
      } else {
        if (v < extK) { extK = v; extI = i; } else if (v - extK >= th) { out.push({ dir: -1, from: pivK, to: extK, i0: pivI, i1: extI }); dir = 1; pivK = extK; pivI = extI; extK = v; extI = i; }
      }
    }
    if (dir !== 0 && Math.abs(extK - pivK) >= th) out.push({ dir, from: pivK, to: extK, i0: pivI, i1: extI });
    return out.sort((a, b) => Math.abs(b.to - b.from) - Math.abs(a.to - a.from)).slice(0, 4).map((sw) => ({ ...sw, ctx: context(sw.dir, sw.i0 * 60 + 30) }));
  }

  function report() {
    if (!S.sess) return;
    if (S.cur && S.acct.pos) { /* open position: shown as open, not graded */ }
    const A = analyse(), m = $("#sm-modal"); m.hidden = false;
    const sess = S.sess, tr = S.trades;
    const gcol = { A: "#2be07e", B: "#9be15d", C: "#ffd23f", D: "#ffa028", F: "#ff4a4a" };
    const kpi = (l, v, cls) => `<div class="sm-kpi"><span>${l}</span><b class="${cls || ""}">${v}</b></div>`;
    const mistakes = Object.entries(A.mistakes).sort((a, b) => b[1].n - a[1].n);
    const strengths = Object.entries(A.strengths).sort((a, b) => b[1].n - a[1].n);
    m.innerHTML = `<div class="sm-mbox sm-rbox">
      <div class="sm-mh"><b>SESSION REPORT</b><span class="dim">seed ${sess.seed} · ${esc(sess.scenarioName)} · ${clock(0)}–${clock(Math.min(S.simT, sess.N))}</span><span class="sp"></span>
        <button class="sm-b" id="sm-rplay">▶ REPLAY SESSION</button><button class="sm-b" id="sm-rsave">SAVE</button><button class="sm-b" id="sm-ragain">TRADE THIS SEED AGAIN</button><button class="sm-b" id="sm-rnew">NEW SESSION</button><button class="sm-b" id="sm-mx">CLOSE</button></div>
      <div class="sm-kpis">${kpi("NET P&amp;L", money(A.net), A.net >= 0 ? "up" : "dn")}${kpi("TRADES", A.n)}${kpi("WIN RATE", Math.round(A.win * 100) + "%")}${kpi("AVG WIN / LOSS", `${money(A.avgW)} / ${money(A.avgL)}`)}${kpi("EXPECTANCY", A.expR === null ? "—" : A.expR.toFixed(2) + "R")}${kpi("PROFIT FACTOR", isFinite(A.pf) ? A.pf.toFixed(2) : "∞")}${kpi("MAX DRAWDOWN", money(A.dd), "dn")}${kpi("FEES", money(A.fees))}${kpi("PROCESS GRADE", A.avgGrade === null ? "—" : `${Math.round(A.avgGrade)} / 100`, A.avgGrade >= 74 ? "up" : A.avgGrade >= 55 ? "" : "dn")}</div>
      <div class="sm-rchart"><canvas id="sm-rc"></canvas></div>
      <div class="sm-rgrid">
        <div class="sm-rcol"><div class="sm-h">WHAT WENT WRONG (most frequent first)</div>${mistakes.length ? mistakes.map(([k, v]) => `<div class="sm-mist"><b>${v.n}×</b> ${esc(k)} <span class="dim">· trade${v.trades.length > 1 ? "s" : ""} #${v.trades.join(", #")}</span></div>`).join("") : '<div class="dim">No process mistakes found.</div>'}
          <div class="sm-h" style="margin-top:10px">WHAT YOU DID WELL</div>${strengths.length ? strengths.map(([k, v]) => `<div class="sm-good"><b>${v.n}×</b> ${esc(k)} <span class="dim">· #${v.trades.join(", #")}</span></div>`).join("") : '<div class="dim">—</div>'}
          ${A.overtrade > 6 ? `<div class="sm-mist" style="margin-top:8px"><b>!</b> Overtrading: ${A.overtrade.toFixed(1)} trades an hour</div>` : ""}</div>
        <div class="sm-rcol"><div class="sm-h">YOUR SETUPS</div><table class="sm-tbl"><thead><tr><th>SETUP</th><th>N</th><th>WIN</th><th>AVG R</th><th>NET</th></tr></thead><tbody>${Object.entries(A.setups).sort((a, b) => b[1].net - a[1].net).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${Math.round((v.w / v.n) * 100)}%</td><td>${v.R.length ? (v.R.reduce((a, b) => a + b, 0) / v.R.length).toFixed(2) : "—"}</td><td class="${v.net >= 0 ? "up" : "dn"}">${money(v.net)}</td></tr>`).join("") || '<tr><td colspan="5" class="dim">no trades</td></tr>'}</tbody></table>
          <div class="sm-h" style="margin-top:10px">WHAT THE MARKET WAS DOING (hidden script)</div>${sess.truth.filter((e) => e.type !== "stops").map((e) => `<div class="sm-truth"><span class="dim">${clock(e.t)}</span> ${esc(e.note)}</div>`).join("") || '<div class="dim">—</div>'}</div>
        <div class="sm-rcol"><div class="sm-h">OPPORTUNITIES</div>${A.missed.map((x) => `<div class="sm-opp ${x.took ? "took" : ""}"><b>${x.took ? "✔ TAKEN" : "✖ MISSED"}</b> ${clock(x.e.ideal.entryT)} ${x.e.ideal.side > 0 ? "LONG" : "SHORT"} ${P(x.e.ideal.entry)} · stop ${P(x.e.ideal.stop)} · target ${P(x.e.ideal.target)} → ${x.out.R === null ? "open" : x.out.R > 0 ? `+${x.out.R.toFixed(1)}R` : "−1R"}<div class="dim">${esc(x.e.type === "sweep" ? "Failed stop run: fade the reclaim" : "Absorption held: trade the turn")}</div></div>`).join("") || '<div class="dim">No scripted setups this session.</div>'}
          <div class="sm-h" style="margin-top:10px">BIGGEST MOVES OF THE DAY</div>${A.swings.map((sw) => `<div class="sm-opp"><b>${sw.dir > 0 ? "▲" : "▼"} ${Math.round(Math.abs(sw.to - sw.from) * sess.tick)} pts</b> ${clock(sw.i0 * 60)}–${clock(sw.i1 * 60)} <div class="dim">${esc(swingWhy(sw))}</div></div>`).join("")}</div>
      </div>
      <div class="sm-h" style="margin:10px 0 4px">EVERY TRADE · click a row to zoom the chart on it</div>
      <table class="sm-tbl sm-trades"><thead><tr><th>#</th><th>TIME</th><th>SIDE</th><th>QTY</th><th>SETUP</th><th>ENTRY → EXIT</th><th>EXIT</th><th>NET</th><th>R</th><th>MFE / MAE</th><th>GRADE</th><th>WHY</th></tr></thead><tbody>${tr.map((t, i) => `<tr data-tr="${i}"><td>${t.id}</td><td>${clock(t.openT)}</td><td class="${t.side > 0 ? "up" : "dn"}">${t.side > 0 ? "LONG" : "SHORT"}</td><td>${t.maxPos}</td><td>${esc(t.setup)}</td><td>${P(t.entryK)} → ${P(t.exitK)}</td><td>${esc(t.exitTag)}</td><td class="${t.net >= 0 ? "up" : "dn"}">${money(t.net)}</td><td>${t.R === null ? "—" : t.R.toFixed(1)}</td><td>${t.mfe} / ${t.mae}</td><td><b style="color:${gcol[t.grade.letter]}">${t.grade.letter}</b> <span class="dim">${t.grade.score}</span></td><td class="sm-why">${t.grade.neg.map((x) => `<div class="dn">✖ ${esc(x.txt)}</div>`).join("")}${t.grade.pos.map((x) => `<div class="up">✔ ${esc(x.txt)}</div>`).join("")}</td></tr>`).join("") || '<tr><td colspan="12" class="dim">No closed trades.</td></tr>'}</tbody></table>
      <div class="sm-h" style="margin:12px 0 4px">AI COACH <button class="sm-chip" id="sm-cl" data-lang="en">EN</button><button class="sm-chip" data-lang="he">עברית</button></div><div id="sm-coach" class="sm-coach dim">Asking the coach…</div>
    </div>`;
    $("#sm-mx").onclick = () => { m.hidden = true; S.rep = null; };
    $("#sm-ragain").onclick = () => { m.hidden = true; start(sess.seed, S.scenario); };
    $("#sm-rnew").onclick = () => { m.hidden = true; start(Math.floor(Math.random() * 999999) + 1, $("#sm-scn").value); };
    $("#sm-rsave").onclick = () => saveSession(A);
    m.querySelectorAll("[data-tr]").forEach((r) => (r.onclick = () => { const t = tr[+r.dataset.tr]; S.rep.focus = [Math.max(0, t.openT - 900), Math.min(sess.N, (t.closeT || t.openT) + 600)]; S.rep.anim = null; drawReport(); $("#sm-rc").scrollIntoView({ behavior: "smooth", block: "center" }); }));
    m.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => coach(A, b.dataset.lang)));
    S.rep = { A, focus: [0, Math.min(sess.N, Math.max(S.simT, 60))], anim: null };
    const rc = $("#sm-rc"); const host = rc.parentElement; const dpr = window.devicePixelRatio || 1; rc.width = host.clientWidth * dpr; rc.height = host.clientHeight * dpr; rc._w = host.clientWidth; rc._h = host.clientHeight; rc._dpr = dpr;
    $("#sm-rplay").onclick = () => { S.rep.focus = [0, Math.min(sess.N, Math.max(S.simT, 60))]; S.rep.anim = { t0: performance.now(), dur: 9000 }; animReport(); };
    drawReport();
    coach(A, "en");
  }
  function swingWhy(sw) {
    const c = sw.ctx, bits = [];
    if (c.absorb) bits.push(`absorption at ${P(c.absorb.k)} just before`);
    if (c.recentSweep) bits.push("it started from a failed stop run");
    if (c.divergence) bits.push("CVD was diverging from price at the turn");
    if (c.near && Math.abs(c.near.d) <= 10) bits.push(`started ${Math.abs(c.near.d)} ticks from the ${c.near.n}`);
    if (!c.gpos) bits.push("negative gamma helped it extend");
    if (Math.abs(c.vwSig) > 1.5) bits.push(`began at the ${c.vwSig > 0 ? "upper" : "lower"} VWAP band`);
    return bits.length ? "What told you: " + bits.join("; ") + "." : "No clear order-flow tell: a pure momentum move.";
  }
  function animReport() { if (!S.rep || !S.rep.anim) return; drawReport(); if (performance.now() - S.rep.anim.t0 < S.rep.anim.dur) requestAnimationFrame(animReport); else { S.rep.anim = null; drawReport(); } }
  // the report chart: smooth price path (area), trades as connected entry/exit markers, scripted events, equity curve; animated replay
  function drawReport() {
    const c = $("#sm-rc"); if (!c || !S.rep) return;
    const ctx = c.getContext("2d"); ctx.setTransform(c._dpr, 0, 0, c._dpr, 0, 0);
    const w = c._w, h = c._h, sess = S.sess, [f0, f1] = S.rep.focus, eqH = h * 0.26, ph = h - eqH - 20, padR = 64;
    let tEnd = f1;
    if (S.rep.anim) { const x = Math.min(1, (performance.now() - S.rep.anim.t0) / S.rep.anim.dur); tEnd = f0 + (f1 - f0) * (1 - Math.pow(1 - x, 2)); }
    const th = TH(); ctx.fillStyle = th.bg; ctx.fillRect(0, 0, w, h);
    const s = sess.sec, step = Math.max(1, Math.round((f1 - f0) / 900));
    let hi = -Infinity, lo = Infinity; for (let t = f0; t < f1; t += step) { hi = Math.max(hi, s.h[t]); lo = Math.min(lo, s.l[t]); }
    const pad = (hi - lo) * 0.08 + 4; hi += pad; lo -= pad;
    const X = (t) => ((t - f0) / (f1 - f0)) * (w - padR), Y = (k) => ((hi - k) / (hi - lo)) * ph;
    // faint heatmap behind when zoomed on a trade
    if (f1 - f0 <= 3600) {
      const H = sess.heat, h0 = Math.floor(f0 / H.step), h1 = Math.min(H.n - 1, Math.floor(f1 / H.step)), kTop = Math.ceil(hi), kBot = Math.floor(lo), rows = kTop - kBot + 1, cols = h1 - h0 + 1;
      if (rows > 0 && rows < 1500 && cols > 0) { heatCanvas.width = cols; heatCanvas.height = rows; const hc = heatCanvas.getContext("2d"), img = hc.createImageData(cols, rows), W = 2 * H.half + 1, ref = Math.log1p(320);
        for (let ci = 0; ci < cols; ci++) { const hh = h0 + ci, m = H.mid[hh]; for (let r = 0; r < rows; r++) { const j = kTop - r - m + H.half, v = j >= 0 && j < W ? H.v[hh * W + j] : 0, x = Math.min(255, Math.round((Math.log1p(v) / ref) * 255)), o = (r * cols + ci) * 4; img.data[o] = PAL[x * 3]; img.data[o + 1] = PAL[x * 3 + 1]; img.data[o + 2] = PAL[x * 3 + 2]; img.data[o + 3] = x < 30 ? x * 4 : 200; } }
        hc.putImageData(img, 0, 0); ctx.save(); ctx.globalAlpha = 0.55; ctx.imageSmoothingEnabled = true; ctx.drawImage(heatCanvas, X(h0 * H.step), Y(kTop), X((h1 + 1) * H.step) - X(h0 * H.step), Y(kBot) - Y(kTop)); ctx.restore(); }
    }
    // axis
    ctx.font = "11px JetBrains Mono, monospace"; ctx.textBaseline = "middle"; ctx.fillStyle = th.text; ctx.strokeStyle = th.grid;
    const stK = Math.max(1, Math.round(niceStep((hi - lo) * sess.tick / 6) / sess.tick));
    for (let k = Math.ceil(lo / stK) * stK; k <= hi; k += stK) { const y = Y(k); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w - padR, y); ctx.stroke(); ctx.fillText(P(k), w - padR + 6, y); }
    const ts = niceTime(f1 - f0); ctx.textAlign = "center";
    for (let t = Math.ceil(f0 / ts) * ts; t < f1; t += ts) ctx.fillText(clock(t).slice(0, 5), X(t), ph + 10);
    ctx.textAlign = "left";
    // candles (TradingView look), revealed progressively during the replay animation
    const rtf = [60, 120, 300, 600, 900].find((x) => (f1 - f0) / x <= 170) || 900, cwr = Math.max(1.5, ((w - padR) / ((f1 - f0) / rtf)) * 0.7);
    for (let i = Math.floor(f0 / rtf); i * rtf < Math.min(tEnd, f1); i++) {
      const a0 = i * rtf, a1 = Math.min(sess.N, a0 + rtf, Math.floor(tEnd) + 1); if (a1 <= a0) break;
      let o = s.o[a0], hh = -Infinity, ll = Infinity, cl = s.c[a1 - 1]; for (let t = a0; t < a1; t++) { hh = Math.max(hh, s.h[t]); ll = Math.min(ll, s.l[t]); }
      const x = X(a0 + rtf / 2), up = cl >= o, y0 = Y(Math.max(o, cl)), y1 = Y(Math.min(o, cl));
      ctx.strokeStyle = th.wick || (up ? "#2be07e" : "#ff4a4a"); ctx.beginPath(); ctx.moveTo(x, Y(hh)); ctx.lineTo(x, Y(ll)); ctx.stroke();
      ctx.fillStyle = up ? th.bull : th.bear; ctx.fillRect(x - cwr / 2, y0, cwr, Math.max(1, y1 - y0));
      if (th.edge && cwr > 3) { ctx.strokeStyle = th.edge; ctx.strokeRect(x - cwr / 2 + 0.5, y0 + 0.5, cwr - 1, Math.max(1, y1 - y0 - 1)); }
    }
    // position boxes for every trade (profit zone to the target, loss zone to the stop)
    for (const t of S.trades) {
      if (t.openT > tEnd || (t.closeT || 0) < f0) continue;
      const x0 = X(t.openT), x1 = Math.max(x0 + 10, X(Math.min(t.closeT, tEnd))), ye = Y(t.entries[0].k);
      if (t.initTarget !== null) { ctx.fillStyle = th.win; ctx.fillRect(x0, Math.min(ye, Y(t.initTarget)), x1 - x0, Math.abs(Y(t.initTarget) - ye)); }
      if (t.initStop !== null) { ctx.fillStyle = th.loss; ctx.fillRect(x0, Math.min(ye, Y(t.initStop)), x1 - x0, Math.abs(Y(t.initStop) - ye)); }
    }
    // VWAP
    ctx.strokeStyle = "rgba(90,169,255,0.6)"; ctx.setLineDash([4, 4]); ctx.beginPath(); let st = false;
    for (let t = Math.max(1, f0); t <= tEnd; t += step) { const v = vwapAt(t); if (!v) continue; if (!st) { ctx.moveTo(X(t), Y(v.m)); st = true; } else ctx.lineTo(X(t), Y(v.m)); }
    ctx.stroke(); ctx.setLineDash([]);
    // gamma levels
    const lv = sess.levels;
    for (const [k, n, col] of [[lv.callWall, "CALL WALL", "#2be07e"], [lv.putWall, "PUT WALL", "#ff4a4a"], [lv.zeroGamma, "ZERO γ", "#ffa028"]]) { if (k === null || k < lo || k > hi) continue; ctx.strokeStyle = col; ctx.globalAlpha = 0.5; ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(0, Y(k)); ctx.lineTo(w - padR, Y(k)); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.fillStyle = col; ctx.fillText(n, 6, Y(k) - 7); }
    drawItems(ctx, X, Y, 0, w - padR, { report: true });
    // hidden script events
    for (const e of sess.truth) { if (e.type === "stops" || e.t < f0 || e.t > tEnd) continue; const x = X(e.t); ctx.strokeStyle = "rgba(197,139,255,0.5)"; ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, ph); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = "#c58bff"; ctx.save(); ctx.translate(x + 3, 14); ctx.fillText(e.type.toUpperCase(), 0, 0); ctx.restore(); }
    // trades
    for (const t of S.trades) {
      if (t.openT > tEnd || (t.closeT || 0) < f0) continue;
      const x0 = X(t.openT), y0 = Y(t.entryK), x1 = X(Math.min(t.closeT, tEnd)), y1 = Y(t.closeT <= tEnd ? t.exitK : s.c[Math.floor(tEnd)]);
      const col = t.net >= 0 ? (PREF.theme !== "tv" ? "#2be07e" : "#1f8a6c") : (PREF.theme !== "tv" ? "#ff4a4a" : "#c0392b");
      ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.setLineDash(t.closeT <= tEnd ? [] : [3, 3]); ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1;
      ctx.fillStyle = t.side > 0 ? "#5aa9ff" : "#ff9f43"; ctx.beginPath(); if (t.side > 0) { ctx.moveTo(x0, y0 + 3); ctx.lineTo(x0 - 7, y0 + 14); ctx.lineTo(x0 + 7, y0 + 14); } else { ctx.moveTo(x0, y0 - 3); ctx.lineTo(x0 - 7, y0 - 14); ctx.lineTo(x0 + 7, y0 - 14); } ctx.fill();
      if (t.closeT <= tEnd) { ctx.fillStyle = col; ctx.font = "bold 11px JetBrains Mono, monospace"; ctx.fillText(`#${t.id} ${t.grade ? t.grade.letter : ""}`, x1 + 5, y1); ctx.font = "11px JetBrains Mono, monospace"; ctx.beginPath(); ctx.arc(x1, y1, 3.5, 0, Math.PI * 2); ctx.fill(); }
    }
    // equity curve
    const ey0 = ph + 22, eh = eqH - 6, curve = S.rep.A.curve.filter((p) => p[0] <= tEnd);
    let emin = 0, emax = 0; S.rep.A.curve.forEach((p) => { emin = Math.min(emin, p[1]); emax = Math.max(emax, p[1]); }); if (emax === emin) emax = emin + 1;
    const EY = (v) => ey0 + eh - ((v - emin) / (emax - emin)) * eh;
    ctx.fillStyle = PREF.theme !== "tv" ? "#0a0e16" : "#c8bfb1"; ctx.fillRect(0, ey0, w - padR, eh);
    ctx.strokeStyle = "#333"; ctx.beginPath(); ctx.moveTo(0, EY(0)); ctx.lineTo(w - padR, EY(0)); ctx.stroke();
    if (curve.length) { ctx.strokeStyle = curve[curve.length - 1][1] >= 0 ? "#2be07e" : "#ff4a4a"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(X(Math.max(f0, 0)), EY(0)); let last = 0; for (const [t, v] of curve) { const x = X(Math.max(f0, t)); ctx.lineTo(x, EY(last)); ctx.lineTo(x, EY(v)); last = v; } ctx.lineTo(X(tEnd), EY(last)); ctx.stroke(); ctx.lineWidth = 1; ctx.fillStyle = th.text; ctx.fillText(`EQUITY ${money(last)}`, 6, ey0 + 10); }
    if (S.rep.anim) { const x = X(tEnd); ctx.strokeStyle = "rgba(255,255,255,0.4)"; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); ctx.fillStyle = "#fff"; ctx.fillText(clock(tEnd), Math.min(x + 4, w - 120), ph - 8); }
  }

  async function coach(A, lang) {
    const el = $("#sm-coach"); if (!el) return;
    document.querySelectorAll("#sm-modal [data-lang]").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
    el.className = "sm-coach dim"; el.textContent = lang === "he" ? "המאמן כותב…" : "The coach is writing…";
    const body = { lang, seed: S.sess.seed, scenario: S.sess.scenarioName, minutes: Math.round(S.simT / 60),
      stats: { net: Math.round(A.net), trades: A.n, win_rate: +A.win.toFixed(2), expectancy_R: A.expR === null ? null : +A.expR.toFixed(2), profit_factor: isFinite(A.pf) ? +A.pf.toFixed(2) : null, max_dd: Math.round(A.dd), process_grade: A.avgGrade === null ? null : Math.round(A.avgGrade), trades_per_hour: +A.overtrade.toFixed(1) },
      trades: S.trades.slice(0, 40).map((t) => ({ id: t.id, time: clock(t.openT), side: t.side > 0 ? "long" : "short", qty: t.maxPos, setup: t.setup, net: Math.round(t.net), R: t.R === null ? null : +t.R.toFixed(2), exit: t.exitTag, mfe_ticks: t.mfe, mae_ticks: t.mae, grade: t.grade.letter, mistakes: t.grade.neg.map((x) => x.txt), good: t.grade.pos.map((x) => x.txt),
        context: { trend15_ticks: t.ctx.trend15, vwap_sigma: +t.ctx.vwSig.toFixed(2), gamma: t.ctx.gpos ? "positive" : "negative", nearest_level: t.ctx.near ? `${t.ctx.near.n} ${t.ctx.near.d} ticks` : null, absorption: !!t.ctx.absorb, chase: t.ctx.chase, divergence: t.ctx.divergence } })),
      mistakes: Object.fromEntries(Object.entries(A.mistakes).map(([k, v]) => [k, v.n])), setups: Object.fromEntries(Object.entries(A.setups).map(([k, v]) => [k, { n: v.n, win: v.w, net: Math.round(v.net) }])),
      market: S.sess.truth.filter((e) => e.type !== "stops").map((e) => `${clock(e.t)} ${e.note}`), missed: A.missed.map((x) => `${x.took ? "taken" : "missed"}: ${x.e.type} ${clock(x.e.ideal.entryT)} ${x.e.ideal.side > 0 ? "long" : "short"} -> ${x.out.R === null ? "open" : x.out.R.toFixed(1) + "R"}`) };
    try {
      const r = await api("/sim/coach", { method: "POST", body: JSON.stringify(body) });
      const sec = (t, arr, cls) => (arr && arr.length ? `<div class="sm-h">${t}</div>${arr.map((x) => `<div class="${cls || ""}">• ${esc(x)}</div>`).join("")}` : "");
      el.className = "sm-coach"; el.dir = lang === "he" ? "rtl" : "ltr";
      el.innerHTML = `<div class="sm-coach-h">${esc(r.headline || "")}</div><p>${esc(r.summary || "")}</p>${sec(lang === "he" ? "מה השתבש" : "What went wrong", r.wrong, "dn")}${sec(lang === "he" ? "מה עבד" : "What worked", r.right, "up")}
        ${r.setup ? `<div class="sm-h">${lang === "he" ? "הסטאפ שלך" : "Your setup"}</div><p>${esc(r.setup)}</p>` : ""}${sec(lang === "he" ? "חוקים לסשן הבא" : "Rules for the next session", r.rules)}${sec(lang === "he" ? "תרגילים" : "Drills", r.drills)}
        ${r.focus ? `<div class="sm-focus">🎯 ${esc(r.focus)}</div>` : ""}`;
    } catch (e) { el.className = "sm-coach dim"; el.textContent = "The AI coach is not available right now (" + (e.message || e) + "). The graded report above is complete without it."; }
  }
  async function saveSession(A) {
    try {
      await api("/sim/sessions", { method: "POST", body: JSON.stringify({ seed: S.sess.seed, scenario: S.sess.scenario, minutes: Math.round(S.simT / 60), net: Math.round(A.net), trades: A.n, win: A.win, grade: A.avgGrade, expR: A.expR, mistakes: Object.fromEntries(Object.entries(A.mistakes).map(([k, v]) => [k, v.n])), setups: Object.fromEntries(Object.entries(A.setups).map(([k, v]) => [k, { n: v.n, w: v.w, net: Math.round(v.net) }])) }) });
      const b = $("#sm-rsave"); if (b) { b.textContent = "SAVED ✔"; b.disabled = true; }
    } catch (e) { feed("Save failed: " + (e.message || e)); }
  }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    S.active = tab === "sim";
    if (S.active && !S.built) { try { build(); } catch (e) { const v = $("#view-sim"); if (v) v.innerHTML = NX.err(e); } }
    if (S.active) { fit(); S.last = performance.now(); } else setPlay(false);
  };
  NX.sim = { S };
})();
