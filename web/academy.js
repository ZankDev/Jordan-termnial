/* ACADEMY: a replay trainer on real BTC / ETH / SOL history. Press PLAY and the chart reveals one candle at a time; STOP, STEP or change speed whenever
   you like. Take paper trades with a stop and target, mark Wyckoff events, and run WYCKOFF DRILLS: real accumulation / distribution setups found in
   history, paused at the decision point, then revealed and graded. Nothing here is a signal: it is practice on data that already happened. */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, signed } = NX;
  const LW = () => window.LightweightCharts;
  const KEY = "nx_academy";
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const SYMS = [["BTCUSDT", "BTC"], ["ETHUSDT", "ETH"], ["SOLUSDT", "SOL"]];
  const FREE_TFS = [["15m", "15M"], ["1h", "1H"], ["4h", "4H"], ["1d", "1D"]];
  const SPEEDS = [1, 3, 8, 20];
  const FEE = 0.0004; // per side
  const LABELS = [["climax", "CLIMAX (SC / BC)"], ["ar", "AR"], ["st", "ST"], ["spring", "SPRING / UT"], ["sos", "SOS / SOW"], ["lps", "LPS / LPSY"]];
  const LNAME = Object.fromEntries(LABELS);
  const REF_KEY = { climax: "sc", ar: "ar", st: "st", spring: "spring", sos: "sos", lps: "lps" };
  const HINTS = ["Find the trading range: where did price stop trending and start chopping sideways?",
    "Effort vs result: a heavy-volume bar that stops a decline is the climax; the bounce after it (AR) marks the top of the range.",
    "Mark CLIMAX, AR and ST (a retest of the climax low on lighter volume). Then watch for a poke outside the range that snaps back inside, ideally on light volume.",
    "Textbook entry: after price closes back inside the range following the poke. Stop just beyond the poke extreme, first target the far side of the range. If the range is at the top, mirror everything."];

  const S = { times: [], draw: null, built: false, active: false, chart: null, cs: null, vs: null, symbol: "BTCUSDT", tf: "4h", mode: "free", all: [], n: 0, playing: false, timer: null, speed: 3, pos: null, trades: [], equity: 10000, start: 10000, pxLines: [], userLines: [], marks: [], markLabel: null,
    drill: null, kind: "any", hintN: 0, revealed: false, decided: false, paused_at_decision: false, dirty: { stop: false, target: false }, stats: Object.assign({ drills: 0, scoreSum: 0, trades: 0, wins: 0, sumR: 0, seen: [] }, lget(KEY, {})), loading: false, req: 0, help: { open: false, hints: {} }, others: [], showAll: false, otherLines: [] };
  const saveStats = () => lset(KEY, S.stats);
  const bars = () => S.all.slice(0, S.n);

  // ------------------------------------------------------------------ indicators for the tape-reading coach
  const atr = (i, n = 14) => { let s = 0, c = 0; for (let k = Math.max(1, i - n + 1); k <= i; k++) { const b = S.all[k], p = S.all[k - 1]; s += Math.max(b.high - b.low, Math.abs(b.high - p.close), Math.abs(b.low - p.close)); c++; } return c ? s / c : 0; };
  const vavg = (i, n = 50) => { let s = 0, c = 0; for (let k = Math.max(0, i - n); k < i; k++) { s += S.all[k].volume; c++; } return c ? s / c : 0; };
  function tape() {
    const i = S.n - 1; if (i < 60) return "";
    const b = S.all[i], a = atr(i), va = vavg(i), vr = va ? b.volume / va : 0, sp = a ? (b.high - b.low) / a : 0, cp = b.high > b.low ? (b.close - b.low) / (b.high - b.low) : 0.5, up = b.close >= b.open, tags = [];
    const pl = Math.min(...S.all.slice(i - 30, i).map((x) => x.low)), ph = Math.max(...S.all.slice(i - 30, i).map((x) => x.high));
    if (vr >= 2.2 && sp >= 1.4) tags.push(!up && cp > 0.5 ? ["STOPPING VOLUME", "Heavy selling but the bar closed off its low: supply is being absorbed (a selling climax looks like this).", "up"] : up && cp < 0.5 ? ["POSSIBLE BUYING CLIMAX", "Heavy buying but the bar closed off its high: demand is being absorbed.", "dn"] : ["CLIMACTIC BAR", "Big effort and a wide spread: a turn or a continuation, wait for the next bars.", ""]);
    else if (vr >= 1.8 && sp < 0.7) tags.push(["ABSORPTION", "High effort, little result: someone is absorbing the other side.", ""]);
    if (!up && vr < 0.7 && sp < 0.8) tags.push(["NO SUPPLY", "A down bar on low volume and a narrow spread: sellers are not there (bullish sign inside a range).", "up"]);
    if (up && vr < 0.7 && sp < 0.8) tags.push(["NO DEMAND", "An up bar on low volume and a narrow spread: buyers are not there (bearish sign inside a range).", "dn"]);
    if (b.low < pl && b.close > pl) tags.push(["SPRING-LIKE", `Dipped under the 30-bar low (${px(pl)}) and closed back above it${vr < 1 ? " on light volume" : " on heavy volume: a test is safer"}.`, "up"]);
    if (b.high > ph && b.close < ph) tags.push(["UPTHRUST-LIKE", `Poked over the 30-bar high (${px(ph)}) and closed back under it${vr < 1 ? " on light volume" : ""}.`, "dn"]);
    const hi = Math.max(...S.all.slice(Math.max(0, i - 60), i + 1).map((x) => x.high)), lo = Math.min(...S.all.slice(Math.max(0, i - 60), i + 1).map((x) => x.low));
    return `<div class="ac-tape"><div class="ac-nums"><span>VOLUME <b class="${vr >= 1.8 ? "up" : ""}">${num(vr, 1)}×</b> avg</span><span>SPREAD <b>${num(sp, 1)}×</b> ATR</span><span>CLOSE <b>${Math.round(cp * 100)}%</b> of bar</span><span>60-BAR RANGE <b>${Math.round(((b.close - lo) / (hi - lo || 1)) * 100)}%</b></span></div>${tags.map((t) => `<div class="ac-tag ${t[2]}"><b>${t[0]}</b> ${esc(t[1])}</div>`).join("") || '<div class="dim">Nothing unusual on this bar.</div>'}</div>`;
  }

  // ------------------------------------------------------------------ chart + replay
  const fmtBar = (b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close });
  const volBar = (b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "rgba(43,224,126,0.45)" : "rgba(255,74,74,0.45)" });
  function initChart() {
    const el = $("#ac-chart");
    const ch = LW().createChart(el, { autoSize: true, layout: { background: { type: "solid", color: "#050505" }, textColor: "#8b8b8b", fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 11 }, grid: { vertLines: { color: "#111" }, horzLines: { color: "#111" } },
      crosshair: { mode: LW().CrosshairMode.Normal, vertLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" }, horzLine: { color: "#ffa028", labelBackgroundColor: "#ffa028" } }, rightPriceScale: { borderColor: "#262626" }, timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false, rightOffset: 8 } });
    S.chart = ch;
    S.cs = ch.addCandlestickSeries({ upColor: "#2be07e", downColor: "#ff4a4a", borderVisible: false, wickUpColor: "#2be07e", wickDownColor: "#ff4a4a" });
    S.vs = ch.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    ch.subscribeClick(onChartClick);
  }
  const idxAt = (t) => { let lo = 0, hi = S.n - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; S.all[m].time <= t ? (lo = m) : (hi = m - 1); } return lo; };
  function onChartClick(p) {
    if (!S.markLabel || !p.time || !p.point) return;
    const i = idxAt(p.time), b = S.all[i], price = S.cs.coordinateToPrice(p.point.y);
    S.marks = S.marks.filter((m) => !(m.label === S.markLabel && Math.abs(m.i - i) < 1));
    S.marks.push({ label: S.markLabel, i, above: price > (b.high + b.low) / 2 });
    drawMarkers(); coachSide();
  }
  function drawMarkers() {
    const M = [];
    S.marks.forEach((m) => M.push({ time: S.all[m.i].time, position: m.above ? "aboveBar" : "belowBar", color: "#ffa028", shape: m.above ? "arrowDown" : "arrowUp", text: LNAME[m.label].split(" ")[0] }));
    S.trades.forEach((t) => { M.push({ time: S.all[t.i0].time, position: t.side > 0 ? "belowBar" : "aboveBar", color: "#5aa9ff", shape: t.side > 0 ? "arrowUp" : "arrowDown", text: t.side > 0 ? "L" : "S" }); M.push({ time: S.all[t.i1].time, position: "inBar", color: t.r >= 0 ? "#2be07e" : "#ff4a4a", shape: "circle", text: `${t.r >= 0 ? "+" : ""}${t.r.toFixed(1)}R` }); });
    if (S.pos) M.push({ time: S.all[S.pos.i].time, position: S.pos.side > 0 ? "belowBar" : "aboveBar", color: "#5aa9ff", shape: S.pos.side > 0 ? "arrowUp" : "arrowDown", text: S.pos.side > 0 ? "L" : "S" });
    if (S.revealed && S.drill) Object.entries(S.drill.events).forEach(([k, i]) => { if (i != null && i < S.n) M.push({ time: S.all[i].time, position: "inBar", color: "#e8d44d", shape: "square", text: `★${refName(k)}` }); });
    Object.entries(S.help.hints).forEach(([k, h]) => { if (h < S.n) M.push({ time: S.all[h].time, position: "aboveBar", color: "#c58bff", shape: "circle", text: `? ${refName(k)}` }); });
    if (S.showAll) S.others.forEach((o, j) => {
      const acc = o.kind === "accumulation", col = acc ? "#2be07e" : "#ff6a6a", low = { sc: acc, st: acc, spring: acc, lps: acc, ar: !acc, sos: !acc };
      Object.entries(o.events).forEach(([k, i]) => { if (i != null && i >= 0 && i < S.n) M.push({ time: S.all[i].time, position: low[k] ? "belowBar" : "aboveBar", color: col, shape: low[k] ? "arrowUp" : "arrowDown", text: `${j + 1}·${evName(o.kind, k)}` }); });
      if (o.entry_i >= 0 && o.entry_i < S.n) M.push({ time: S.all[o.entry_i].time, position: acc ? "belowBar" : "aboveBar", color: "#5aa9ff", shape: "square", text: `${j + 1}·ENTRY` });
    });
    M.sort((a, b) => a.time - b.time);
    S.cs.setMarkers(M);
  }
  const evName = (kind, k) => (kind === "distribution" ? { sc: "BC", ar: "AR", st: "ST", spring: "UT", sos: "SOW", lps: "LPSY" }[k] : { sc: "SC", ar: "AR", st: "ST", spring: "SPRING", sos: "SOS", lps: "LPS" }[k]);
  const refName = (k) => (S.drill && S.drill.kind === "distribution" ? { sc: "BC", ar: "AR", st: "ST", spring: "UT", sos: "SOW", lps: "LPSY" }[k] : { sc: "SC", ar: "AR", st: "ST", spring: "SPRING", sos: "SOS", lps: "LPS" }[k]);

  function load(sess, drill) {
    stop(); S.symbol = sess.symbol; S.tf = sess.tf;
    S.all = sess.candles.map((c) => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4], volume: c[5] }));
    S.n = Math.min(sess.reveal, S.all.length - 5); S.pos = null; S.trades = []; S.equity = S.start = 10000; S.marks = []; S.hintN = 0; S.help = { open: false, hints: {} }; S.others = []; S.showAll = false; S.revealed = false; S.decided = false; S.drill = drill || null; S.dirty = { stop: false, target: false };
    clearLines(); S.times = S.all.map((b) => b.time); if (S.draw) S.draw.reset();
    S.cs.setData(S.all.slice(0, S.n).map(fmtBar)); S.vs.setData(S.all.slice(0, S.n).map(volBar)); S.cs.setMarkers([]);
    S.chart.timeScale().setVisibleLogicalRange({ from: S.n - 110, to: S.n + 10 });
    ui(); coachSide(); autofill(true);
  }
  function clearLines() { (S.otherLines || []).forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); S.otherLines = []; S.pxLines.forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); S.pxLines = []; S.userLines.forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); S.userLines = []; }
  function step(k = 1) {
    for (let j = 0; j < k; j++) {
      if (S.n >= S.all.length) { stop(); if (S.drill && !S.revealed) reveal(); return; }
      const b = S.all[S.n]; S.n++;
      S.cs.update(fmtBar(b)); S.vs.update(volBar(b));
      onBar(b, S.n - 1);
      if (S.drill && !S.revealed) {
        if (!S.decided && S.n - 1 >= S.drill.entry_i) { S.decided = true; S.help.open = false; stop(); coachMsg("DECISION POINT", "The replay paused where the textbook entry would have been. Decide: take the trade (set your own stop and target) or skip. Then press PLAY to see what happens."); }
        else if (S.decided && !S.pos && S.n - 1 >= S.drill.entry_i + 70) { stop(); reveal(); }
      }
    }
    ui(); autofill(false); coachSide();
  }
  function play() { if (S.playing) return; S.playing = true; $("#ac-play").textContent = "■ STOP"; S.timer = setInterval(() => { if (!S.active) return stop(); step(1); }, 1000 / S.speed); }
  function stop() { S.playing = false; clearInterval(S.timer); const b = $("#ac-play"); if (b) b.textContent = "▶ PLAY"; }

  // ------------------------------------------------------------------ paper trading
  function autofill(force) {
    if (!S.n || S.n < 20) return;
    const b = S.all[S.n - 1], a = atr(S.n - 1) || b.close * 0.01, long = (S.side || 1) > 0;
    const st = $("#ac-stop"), tp = $("#ac-tp");
    if (st && (force || !S.dirty.stop)) st.value = (long ? b.close - 1.5 * a : b.close + 1.5 * a).toFixed(b.close < 10 ? 4 : 2);
    if (tp && (force || !S.dirty.target)) tp.value = (long ? b.close + 3 * a : b.close - 3 * a).toFixed(b.close < 10 ? 4 : 2);
  }
  function openPos(side) {
    if (S.pos) return showToast("You already have a position: close it first");
    if (S.revealed) return showToast("This drill is over: start a new one");
    const b = S.all[S.n - 1], entry = b.close, stopP = Number($("#ac-stop").value), tgt = Number($("#ac-tp").value), riskPct = Number($("#ac-risk").value) || 1;
    if (!(stopP > 0) || (side > 0 ? stopP >= entry : stopP <= entry)) return showToast(side > 0 ? "A long needs the stop BELOW the entry" : "A short needs the stop ABOVE the entry");
    if (tgt && (side > 0 ? tgt <= entry : tgt >= entry)) return showToast(side > 0 ? "The target must be above the entry" : "The target must be below the entry");
    const risk$ = (S.equity * riskPct) / 100, size = risk$ / Math.abs(entry - stopP);
    S.pos = { side, entry, stop: stopP, target: tgt || null, size, risk$, i: S.n - 1, riskPct };
    drawPos(); drawMarkers(); ui(); coachSide();
  }
  function drawPos() {
    S.pxLines.forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); S.pxLines = [];
    const p = S.pos; if (!p) return;
    S.pxLines.push(S.cs.createPriceLine({ price: p.entry, color: "#5aa9ff", lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: p.side > 0 ? "LONG" : "SHORT" }));
    S.pxLines.push(S.cs.createPriceLine({ price: p.stop, color: "#ff4a4a", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "STOP" }));
    if (p.target) S.pxLines.push(S.cs.createPriceLine({ price: p.target, color: "#2be07e", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "TARGET" }));
  }
  function closePos(exit, reason, idx) {
    const p = S.pos; if (!p) return;
    const gross = (exit - p.entry) * p.size * p.side, fees = p.size * (p.entry + exit) * FEE, pnl = gross - fees, r = pnl / p.risk$;
    S.equity += pnl; S.trades.push({ side: p.side, entry: p.entry, exit, stop: p.stop, target: p.target, r, pnl, reason, i0: p.i, i1: idx, riskPct: p.riskPct });
    S.stats.trades++; S.stats.sumR += r; if (r > 0) S.stats.wins++; saveStats();
    S.pos = null; drawPos(); drawMarkers();
    showToast(`${reason === "stop" ? "Stopped out" : reason === "target" ? "Target hit" : "Closed"}: ${r >= 0 ? "+" : ""}${r.toFixed(2)}R`);
    if (S.drill && S.decided && !S.revealed) { stop(); setTimeout(reveal, 400); }
  }
  function onBar(b, idx) {
    const p = S.pos; if (!p || idx <= p.i) return;
    if (p.side > 0) { if (b.low <= p.stop) return closePos(Math.min(p.stop, b.open), "stop", idx); if (p.target && b.high >= p.target) return closePos(Math.max(p.target, Math.min(b.open, p.target)), "target", idx); }
    else { if (b.high >= p.stop) return closePos(Math.max(p.stop, b.open), "stop", idx); if (p.target && b.low <= p.target) return closePos(Math.min(p.target, Math.max(b.open, p.target)), "target", idx); }
  }

  // ------------------------------------------------------------------ WYCKOFF drill
  async function newDrill() {
    const my = ++S.req; stop();
    const msg = $("#ac-msg"); msg.innerHTML = '<div class="bb-empty bb-load">FINDING A REAL SETUP</div>';
    try {
      let r;
      for (let k = 0; k < 40; k++) {
        r = await api(`/academy/drill?symbol=${S.symbol}&tf=${S.tf}&kind=${S.kind}&seen=${encodeURIComponent(S.stats.seen.slice(-200).join(","))}`);
        if (my !== S.req) return;
        if (!r.building) break;
        msg.innerHTML = `<div class="bb-empty bb-load">${esc(r.message)}</div>`; await new Promise((res) => setTimeout(res, 5000));
        if (!S.active) return;
      }
      if (r.building) throw new Error("The setup library is still building: try again in a minute.");
      if (my !== S.req) return;
      S.mode = "drill"; S.lib = r.library; load(r, r.setup); S.others = r.others || []; modeUi();
      coachMsg("NEW DRILL", `A real ${S.symbol.slice(0, -4)} ${S.tf} chart. Press PLAY (or STEP) and study it. The replay stops at the decision point. Use HELP if you are stuck (it lists what to mark and can point at where), MARK to label Wyckoff events, and trade with a stop and a target.`);
    } catch (e) { if (my === S.req) msg.innerHTML = NX.err(e); }
  }
  function reveal() {
    if (S.revealed || !S.drill) return;
    stop(); S.revealed = true;
    const st = S.drill;
    // show the answer: range lines and the textbook entry / stop / target
    [[st.range_high, "#e8d44d", "RANGE TOP"], [st.range_low, "#e8d44d", "RANGE BOTTOM"]].forEach(([p, c, t]) => S.userLines.push(S.cs.createPriceLine({ price: p, color: c, lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: t })));
    [[st.entry, "#5aa9ff", "TEXTBOOK ENTRY"], [st.stop, "#ff4a4a", "TEXTBOOK STOP"], [st.target, "#2be07e", "TEXTBOOK TARGET"]].forEach(([p, c, t]) => S.userLines.push(S.cs.createPriceLine({ price: p, color: c, lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: t })));
    // let the reference events all show, even if they were already scrolled past
    drawMarkers(); grade(); showAll();
  }
  function grade() {
    const st = S.drill, tol = S.tf === "1d" ? 2 : 4, acc = st.kind === "accumulation", checks = [];
    // events
    const refs = Object.entries(st.events).filter(([, i]) => i != null);
    let hit = 0; const rows = refs.map(([k, i]) => { const lab = Object.keys(REF_KEY).find((x) => REF_KEY[x] === k), m = S.marks.find((x) => x.label === lab && Math.abs(x.i - i) <= tol); if (m) hit += m.assisted ? 0.5 : 1; return `<tr><td class="sym">${refName(k)}</td><td class="dim">${new Date(S.all[i].time * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</td><td class="${m ? "up" : "dn"}">${m ? (m.assisted ? "half credit (used HELP)" : "✓ you marked it") : "✗ not marked"}</td></tr>`; });
    const extra = S.marks.filter((m) => !refs.some(([k, i]) => REF_KEY[m.label] === k && Math.abs(m.i - i) <= tol)).length;
    const evScore = refs.length ? hit / refs.length : 0;
    // decision
    const t = S.trades[S.trades.length - 1], took = !!t, right = took && (t.side > 0) === acc, late = took && t.i0 > st.entry_i + 25, early = took && t.i0 < st.entry_i - 12;
    let dec = 0;
    if (!took) checks.push(["You skipped it", `The textbook trade here was ${acc ? "LONG" : "SHORT"} (${st.outcome.result === "win" ? "it reached the range target" : st.outcome.hit_2r ? "it reached 2R before failing" : "it was stopped out"}). Skipping is a valid decision: most springs fail.`, "dim"]);
    else {
      dec += right ? 20 : 0;
      checks.push([right ? "Direction matches the structure" : "Direction against the structure", right ? `${acc ? "Accumulation" : "Distribution"}: the textbook side is ${acc ? "long" : "short"}.` : `This was ${acc ? "accumulation, so the textbook side was long" : "distribution, so the textbook side was short"}.`, right ? "up" : "dn"]);
      const okStop = acc ? t.stop <= st.range_low * 1.002 && t.stop >= st.stop * 0.97 - (st.range_high - st.range_low) * 0.15 : t.stop >= st.range_high * 0.998 && t.stop <= st.stop * 1.03 + (st.range_high - st.range_low) * 0.15;
      const beyond = acc ? t.stop < Math.min(st.stop * 1.01, st.range_low) : t.stop > Math.max(st.stop * 0.99, st.range_high);
      dec += beyond ? 15 : 0; checks.push([beyond ? "Stop beyond the structure" : "Stop inside the structure", beyond ? "Your stop sat beyond the poke extreme, where the idea is proven wrong." : `A stop inside the range is easily hit by normal noise. Textbook stop: ${px(st.stop)}.`, beyond ? "up" : "dn"]); void okStop;
      const rr = t.target ? Math.abs(t.target - t.entry) / Math.abs(t.entry - t.stop) : null;
      dec += rr && rr >= 2 ? 10 : 0; checks.push([rr ? (rr >= 2 ? `Planned reward:risk ${rr.toFixed(1)}` : `Planned reward:risk only ${rr.toFixed(1)}`) : "No target planned", rr && rr >= 2 ? "At least 2R planned: needed because these setups fail often." : "Plan at least 2R: a spring wins rarely, so it must pay well when it works.", rr && rr >= 2 ? "up" : "dn"]);
      dec += t.riskPct <= 2 ? 5 : 0; checks.push([t.riskPct <= 2 ? `Risk ${t.riskPct}% of the account` : `Risk ${t.riskPct}% is too much`, "Keep each trade at 1–2% so a run of stop-outs is survivable.", t.riskPct <= 2 ? "up" : "dn"]);
      checks.push([late ? "Late entry" : early ? "Early entry" : "Timing was reasonable", late ? "You entered well after the confirmation: the reward shrank." : early ? "You entered before price closed back inside the range: that is a guess, not a confirmation." : "Around the textbook entry bar.", late || early ? "dn" : "up"]);
      dec -= late || early ? 5 : 0;
    }
    const score = Math.max(0, Math.min(100, Math.round(evScore * 50 + dec)));
    S.stats.drills++; S.stats.scoreSum += score; S.stats.seen.push(st.id); S.stats.seen = S.stats.seen.slice(-400); saveStats();
    const lib = S.lib && S.lib[st.kind];
    $("#ac-msg").innerHTML = `<div class="ac-grade"><div class="ac-score ${score >= 70 ? "up" : score >= 40 ? "" : "dn"}"><b>${score}</b>/100<span>${acc ? "ACCUMULATION · SPRING" : "DISTRIBUTION · UPTHRUST"}</span></div>
      <div class="ac-sec">EVENTS · ${hit % 1 ? hit.toFixed(1) : hit}/${refs.length} found${extra ? ` · ${extra} extra mark${extra > 1 ? "s" : ""}` : ""}</div><table class="bb-tbl"><tbody>${rows.join("")}</tbody></table>
      <div class="ac-sec">YOUR DECISION</div>${checks.map((c) => `<div class="ac-tag ${c[2]}"><b>${esc(c[0])}</b> ${esc(c[1])}</div>`).join("")}${took ? `<div class="ac-tag"><b>Your trade</b> ${t.side > 0 ? "LONG" : "SHORT"} ${px(t.entry)} → ${px(t.exit)} (${t.reason}) = <b class="${t.r >= 0 ? "up" : "dn"}">${t.r >= 0 ? "+" : ""}${t.r.toFixed(2)}R</b></div>` : ""}
      <div class="ac-sec">THE TEXTBOOK TRADE</div><div class="ac-tag"><b>${acc ? "LONG" : "SHORT"}</b> ${px(st.entry)}, stop ${px(st.stop)}, target ${px(st.target)} (${st.rr}R). On this chart: <b class="${st.outcome.result === "win" ? "up" : st.outcome.result === "loss" ? "dn" : ""}">${st.outcome.result === "win" ? "target hit" : st.outcome.result === "loss" ? "stopped out" : "unresolved"}</b>, best move ${st.outcome.mfe_r}R.</div>
      ${lib ? `<div class="ac-tag dim">Across ${lib.n} real ${st.kind} setups found in the ${S.symbol.slice(0, -4)} ${S.tf} library: <b>${lib.stopped}%</b> were stopped out, <b>${lib.hit_2r}%</b> reached 2R, average <b>${lib.avg_r}R</b> if held to the range target. Wyckoff entries win rarely: their edge is being wrong cheaply.</div>` : ""}
      <div id="ac-others"></div><button class="btn" id="ac-next">NEXT SETUP ▸</button></div>`;
    $("#ac-next").addEventListener("click", newDrill);
    ui(); statsUi();
  }

  // ------------------------------------------------------------------ HELP: a marking assistant for the drill
  const EV_DESC = {
    accumulation: { sc: "Selling Climax: the widest bar with peak volume that ends the decline.", ar: "Automatic Rally: the sharp bounce right after the climax. Its high is the top of the range.", st: "Secondary Test: price returns to the climax area on lighter volume and holds.", spring: "Spring: a small poke below the range low that snaps back inside, ideally on light volume.", sos: "Sign of Strength: a strong push up out of the range on volume.", lps: "Last Point of Support: a shallow pullback on low volume after the SOS." },
    distribution: { sc: "Buying Climax: the widest bar with peak volume that ends the advance.", ar: "Automatic Reaction: the sharp drop right after the climax. Its low is the bottom of the range.", st: "Secondary Test: price returns to the climax area on lighter volume and fails.", spring: "Upthrust: a small poke above the range high that snaps back inside.", sos: "Sign of Weakness: a strong push down out of the range on volume.", lps: "Last Point of Supply: a weak bounce on low volume after the SOW." },
  };
  const evLabel = (k) => Object.keys(REF_KEY).find((x) => REF_KEY[x] === k);
  const evAbove = (kind, k) => (kind === "accumulation" ? k === "ar" || k === "sos" : !(k === "ar" || k === "sos"));
  function helpRows() {
    const st = S.drill, tol = S.tf === "1d" ? 2 : 4;
    return ["sc", "ar", "st", "spring", "sos", "lps"].filter((k) => st.events[k] != null).map((k) => {
      const idx = st.events[k], lab = evLabel(k), avail = idx < S.n, m = S.marks.find((x) => x.label === lab);
      let state = "todo", note = "not marked yet";
      if (!avail) { state = "later"; note = "not on the chart yet: keep playing"; }
      else if (m) { const d = m.i - idx; if (Math.abs(d) <= tol) { state = "ok"; note = m.assisted ? "marked with help (half credit)" : "correct place"; } else { state = "off"; note = d < 0 ? "too early: look a few bars later" : "too late: look a few bars earlier"; if (Math.abs(d) > 25) note = "far from it: reread the description"; } }
      return { k, idx, lab, avail, m, state, note };
    });
  }
  function helpUi() {
    const box = $("#ac-msg"); if (!box || S.revealed || !S.drill || !S.help.open) return;
    const rows = helpRows(), done = rows.filter((r) => r.state === "ok").length, next = rows.find((r) => r.avail && r.state !== "ok"), desc = EV_DESC[S.drill.kind] || EV_DESC.accumulation;
    box.innerHTML = `<div class="ac-help"><div class="ac-sec" style="margin-top:0">HELP · MARKING ASSISTANT <i class="ac-x" id="ac-help-x" title="Close">✕</i></div>
      <div class="ac-nums"><span>MARKED RIGHT <b>${done}/${rows.length}</b></span><span>${S.drill.kind.toUpperCase()}</span></div>
      ${rows.map((r) => `<div class="ac-hrow ${r.state}${next && next.k === r.k ? " next" : ""}"><div class="h"><b>${refName(r.k)}</b><em>${r.state === "ok" ? "✓ " : r.state === "off" ? "✗ " : ""}${esc(r.note)}</em></div><div class="d">${esc(desc[r.k])}</div>${r.avail && r.state !== "ok" ? `<div class="b"><button class="wd-mini" data-hw="${r.k}">WHERE?</button><button class="wd-mini" data-hm="${r.k}" title="Places the mark for you: worth half credit">MARK IT FOR ME</button></div>` : ""}</div>`).join("")}
      <div class="ac-tag dim">${next ? `Next: find the <b>${refName(next.k)}</b>. Pick it in the MARK row, then click the bar. WHERE? drops a purple "?" within a couple of bars of it.` : done === rows.length ? "Every event on the chart is marked. Now press PLAY to reach the decision point, then take (or skip) the trade." : "Keep pressing PLAY: the remaining events appear as the chart unfolds."}</div></div>`;
    $("#ac-help-x").addEventListener("click", () => { S.help.open = false; coachMsg("NEW DRILL", "Use HELP whenever you want the marking assistant back."); });
    box.querySelectorAll("[data-hw]").forEach((b) => b.addEventListener("click", () => helpWhere(b.dataset.hw)));
    box.querySelectorAll("[data-hm]").forEach((b) => b.addEventListener("click", () => helpMark(b.dataset.hm)));
  }
  function scrollTo(i) { const r = S.chart.timeScale().getVisibleLogicalRange(); if (r && i < r.from + 4) S.chart.timeScale().setVisibleLogicalRange({ from: i - 25, to: Math.max(r.to, i + 90) }); }
  function helpWhere(k) {
    const idx = S.drill.events[k]; if (idx == null || idx >= S.n) return;
    S.help.hints[k] = Math.max(0, Math.min(S.n - 1, idx + ((idx * 7) % 5) - 2)); // near, not exact: you still choose the bar
    scrollTo(S.help.hints[k]); drawMarkers(); helpUi();
  }
  function helpMark(k) {
    const idx = S.drill.events[k]; if (idx == null || idx >= S.n) return;
    const lab = evLabel(k); S.marks = S.marks.filter((m) => m.label !== lab);
    S.marks.push({ label: lab, i: idx, above: evAbove(S.drill.kind, k), assisted: true });
    delete S.help.hints[k]; scrollTo(idx); drawMarkers(); coachSide();
  }

  // ------------------------------------------------------------------ after the drill: every setup the detector found on this chart
  function showAll() {
    if (!S.drill) return;
    S.showAll = true;
    if (S.n < S.all.length) { S.n = S.all.length; S.cs.setData(S.all.map(fmtBar)); S.vs.setData(S.all.map(volBar)); ui(); }
    drawMarkers(); othersUi();
    const first = Math.min(...Object.values(S.drill.events).filter((v) => v != null), S.drill.entry_i); S.chart.timeScale().setVisibleLogicalRange({ from: first - 40, to: S.drill.entry_i + 130 });
  }
  function othersUi() {
    const el = $("#ac-others"); if (!el) return;
    const all = S.others, d = (i) => new Date(S.all[i].time * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
    const res = (o) => (o.result === "win" ? `<b class="up">target ${o.r >= 0 ? "+" : ""}${o.r}R</b>` : o.result === "loss" ? `<b class="dn">stopped ${o.r}R</b>` : `<span class="dim">unresolved</span>`);
    el.innerHTML = `<div class="ac-sec">SETUPS ON THIS CHART · ${all.length + 1}</div><div class="ac-tag dim">The whole stretch is now shown. Green arrows are accumulations, red arrows distributions, blue squares the textbook entries. Click a row to see its range, entry, stop and target.</div>
      <table class="bb-tbl ac-others"><tbody><tr class="cur"><td>★</td><td class="sym">${S.drill.kind === "accumulation" ? "ACC" : "DIST"}</td><td class="dim">${d(S.drill.entry_i)}</td><td>${S.drill.outcome.result === "win" ? `<b class="up">target</b>` : S.drill.outcome.result === "loss" ? `<b class="dn">stopped</b>` : "open"} · this drill</td></tr>
      ${all.map((o, j) => `<tr data-j="${j}"><td>${j + 1}</td><td class="sym">${o.kind === "accumulation" ? "ACC" : "DIST"}</td><td class="dim">${d(o.entry_i)}</td><td>${res(o)}</td></tr>`).join("") || '<tr><td colspan="4" class="dim">No other setups in this stretch: this one was alone.</td></tr>'}</tbody></table>`;
    el.querySelectorAll("tr[data-j]").forEach((tr) => tr.addEventListener("click", () => focusOther(Number(tr.dataset.j))));
  }
  function focusOther(j) {
    const o = S.others[j]; if (!o) return;
    S.otherLines.forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); S.otherLines = [];
    [[o.range_high, "#e8d44d", 1, `#${j + 1} RANGE TOP`], [o.range_low, "#e8d44d", 1, `#${j + 1} RANGE BOTTOM`], [o.entry, "#5aa9ff", 2, `#${j + 1} ENTRY`], [o.stop, "#ff4a4a", 2, `#${j + 1} STOP`], [o.target, "#2be07e", 2, `#${j + 1} TARGET`]]
      .forEach(([p, c, ls, t]) => S.otherLines.push(S.cs.createPriceLine({ price: p, color: c, lineWidth: 1, lineStyle: ls, axisLabelVisible: true, title: t })));
    const first = Math.min(...Object.values(o.events).filter((v) => v != null && v >= 0), o.entry_i);
    S.chart.timeScale().setVisibleLogicalRange({ from: first - 20, to: o.entry_i + 80 });
    document.querySelectorAll("#ac-others tr[data-j]").forEach((tr) => tr.classList.toggle("on", Number(tr.dataset.j) === j));
  }

  // ------------------------------------------------------------------ UI
  function coachMsg(title, text) { const m = $("#ac-msg"); if (m && !S.revealed) m.innerHTML = `<div class="ac-note"><b>${esc(title)}</b>${esc(text)}</div>`; }
  function coachSide() { const t = $("#ac-tape"); if (t) t.innerHTML = tape(); marksUi(); if (S.help.open) helpUi(); }
  function marksUi() {
    const el = $("#ac-marks"); if (!el) return;
    el.innerHTML = S.marks.length ? S.marks.sort((a, b) => a.i - b.i).map((m, k) => `<span class="ac-mk">${LNAME[m.label].split(" ")[0]}${m.assisted ? "*" : ""} <em>${new Date(S.all[m.i].time * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</em><i data-k="${k}">✕</i></span>`).join("") : '<span class="dim">No marks yet: pick an event, then click the bar on the chart.</span>';
    el.querySelectorAll("i[data-k]").forEach((x) => x.addEventListener("click", () => { S.marks.splice(Number(x.dataset.k), 1); drawMarkers(); marksUi(); }));
  }
  function ui() {
    const b = S.all[S.n - 1]; if (!b) return;
    if (S.draw) S.draw.paint();
    $("#ac-prog").textContent = `bar ${S.n}/${S.all.length} · ${new Date(b.time * 1000).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}${S.mode === "drill" ? "" : ""}`;
    const p = S.pos, un = p ? ((b.close - p.entry) * p.size * p.side) / p.risk$ : 0;
    $("#ac-pos").innerHTML = p ? `<b class="${p.side > 0 ? "up" : "dn"}">${p.side > 0 ? "LONG" : "SHORT"}</b> ${px(p.entry)} · stop ${px(p.stop)}${p.target ? ` · target ${px(p.target)}` : ""} · open <b class="${dir(un)}">${un >= 0 ? "+" : ""}${un.toFixed(2)}R</b>` : '<span class="dim">flat</span>';
    const wins = S.trades.filter((t) => t.r > 0).length, sum = S.trades.reduce((a, t) => a + t.r, 0);
    $("#ac-acct").innerHTML = `EQUITY <b class="${dir(S.equity - S.start)}">$${num(S.equity, 0)}</b> · ${S.trades.length} trades · ${wins} wins · <b class="${dir(sum)}">${sum >= 0 ? "+" : ""}${sum.toFixed(2)}R</b>`;
    $("#ac-close").disabled = !p; $("#ac-hint").style.display = S.mode === "drill" && !S.revealed ? "" : "none"; $("#ac-help").style.display = S.mode === "drill" && !S.revealed ? "" : "none"; $("#ac-reveal").style.display = S.mode === "drill" && !S.revealed ? "" : "none";
    $("#ac-legend").innerHTML = `<b>${S.symbol.slice(0, -4)}/USDT</b> ${S.tf} <span>O</span>${px(b.open)} <span>H</span>${px(b.high)} <span>L</span>${px(b.low)} <span>C</span>${px(b.close)} <span>V</span>${num(b.volume, 0)}`;
  }
  function statsUi() {
    const s = S.stats, el = $("#ac-stats"); if (!el) return;
    el.innerHTML = `<div class="ac-nums"><span>DRILLS <b>${s.drills}</b></span><span>AVG SCORE <b>${s.drills ? Math.round(s.scoreSum / s.drills) : "—"}</b></span><span>TRADES <b>${s.trades}</b></span><span>WIN RATE <b>${s.trades ? Math.round((100 * s.wins) / s.trades) : "—"}%</b></span><span>TOTAL <b class="${dir(s.sumR)}">${s.sumR >= 0 ? "+" : ""}${s.sumR.toFixed(1)}R</b></span></div>`;
  }
  function modeUi() {
    document.querySelectorAll("#ac-modes .bb-chip").forEach((c) => c.classList.toggle("on", c.dataset.f === S.mode));
    const learn = S.mode === "learn", cs = S.mode === "case";
    $("#ac-learn").classList.toggle("hidden", !learn); $("#ac-case").classList.toggle("hidden", !cs); $("#ac-play-area").classList.toggle("hidden", learn || cs);
    const shell = document.querySelector(".ac-shell"); if (shell) shell.classList.toggle("case", cs);
    if (cs && window.NXCase) window.NXCase.mount($("#ac-case")); $("#ac-kind").classList.toggle("hidden", S.mode !== "drill");
    document.querySelectorAll("#ac-tfs .bb-chip").forEach((c) => { const off = S.mode === "drill" && !["4h", "1d"].includes(c.dataset.f); c.classList.toggle("dis", off); });
    if (learn) learnPage();
  }
  const lib = () => api("/academy/library");

  // ------------------------------------------------------------------ LEARN
  function schematic(cv, acc) {
    const w = cv.width = cv.clientWidth * 2, h = cv.height = 240 * 2, g = cv.getContext("2d"); g.scale(2, 2);
    const W = w / 2, H = h / 2, X = (x) => x * (W - 30) + 15, Y = (y) => (acc ? y : 1 - y) * (H - 40) + 20;
    // normalised path (accumulation, y: 0 top .. 1 bottom): decline, SC low, AR, ST, chop, spring, test, SOS, LPS, mark-up
    const P = [[0, 0.12], [0.08, 0.3], [0.16, 0.55], [0.2, 0.95], [0.25, 0.55], [0.31, 0.5], [0.36, 0.86], [0.42, 0.55], [0.48, 0.6], [0.54, 0.5], [0.6, 0.93], [0.65, 0.7], [0.7, 0.55], [0.76, 0.35], [0.82, 0.5], [0.9, 0.2], [1, 0.05]];
    g.clearRect(0, 0, W, H); g.strokeStyle = "#ffa028"; g.lineWidth = 1.8; g.beginPath(); P.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1])))); g.stroke();
    g.strokeStyle = "rgba(255,255,255,0.18)"; g.setLineDash([4, 4]); [[0.5, 0.95], [0.5, 0.2]].forEach(() => 0);
    const top = Y(0.5), bot = Y(0.93); g.beginPath(); g.moveTo(X(0.2), top); g.lineTo(X(0.78), top); g.moveTo(X(0.2), bot); g.lineTo(X(0.78), bot); g.stroke(); g.setLineDash([]);
    g.font = "700 10px 'JetBrains Mono', Consolas, monospace";
    const lab = acc ? [[0.2, 0.95, "SC", "below"], [0.25, 0.55, "AR", "above"], [0.36, 0.86, "ST", "below"], [0.6, 0.93, "SPRING", "below"], [0.7, 0.55, "TEST", "above"], [0.76, 0.35, "SOS", "above"], [0.82, 0.5, "LPS", "below"], [0.08, 0.3, "PS", "above"]] : [[0.2, 0.95, "BC", "above"], [0.25, 0.55, "AR", "below"], [0.36, 0.86, "ST", "above"], [0.6, 0.93, "UT / UTAD", "above"], [0.7, 0.55, "TEST", "below"], [0.76, 0.35, "SOW", "below"], [0.82, 0.5, "LPSY", "above"], [0.08, 0.3, "PSY", "below"]];
    lab.forEach(([x, y, t, pos]) => { g.fillStyle = "#f1f1f1"; g.beginPath(); g.arc(X(x), Y(y), 3, 0, 6.3); g.fill(); g.fillStyle = "#ffa028"; g.fillText(t, X(x) - 12, Y(y) + (pos === "above" ? -9 : 17)); });
    g.fillStyle = "#666"; ["A", "B", "C", "D", "E"].forEach((t, i) => g.fillText(`PHASE ${t}`, X([0.12, 0.34, 0.6, 0.76, 0.92][i]) - 20, H - 4));
  }
  async function learnPage() {
    const el = $("#ac-learn"); if (el._built) return; el._built = true;
    el.innerHTML = `<div class="ac-lgrid"><div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>ACCUMULATION · where a decline ends</div><div class="bb-pad"><canvas class="ac-sch" id="ac-sch1"></canvas><p class="ac-p">Big players buy while price is held in a range. <b>SC</b> (selling climax): a wide bar on heavy volume that stops the fall. <b>AR</b>: the sharp bounce that sets the range top. <b>ST</b>: a retest of the low on lighter volume: sellers are running out. <b>SPRING</b>: a poke under the range low that snaps back inside, a last shakeout. <b>SOS</b>: a break up through the range on volume. <b>LPS</b>: a higher pullback on light volume, the safest re-entry.</p></div></div>
      <div class="bb-panel" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>DISTRIBUTION · where an advance ends</div><div class="bb-pad"><canvas class="ac-sch" id="ac-sch2"></canvas><p class="ac-p">The mirror image. <b>BC</b> (buying climax): a wide bar on heavy volume that stalls the rise. <b>AR</b>: the sharp drop that sets the range bottom. <b>ST</b>: a retest of the high on lighter volume. <b>UT / UTAD</b>: a poke above the range high that fails and falls back inside. <b>SOW</b>: a break down on volume. <b>LPSY</b>: a lower rally on light volume, the safest short entry.</p></div></div>
      <div class="bb-panel" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>THE THREE QUESTIONS BEFORE ANY TRADE</div><div class="bb-pad"><ol class="ac-ol"><li><b>Where am I in the structure?</b> Is there a range at all, and is it after a decline (accumulation) or an advance (distribution)?</li><li><b>Who is in control?</b> Compare effort (volume) with result (how far price moved). Big volume with little movement = absorption.</li><li><b>Where is the idea wrong?</b> If you cannot name a stop that is beyond the structure, there is no trade.</li></ol></div></div>
      <div class="bb-panel" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>RULES USED IN THE DRILLS</div><div class="bb-pad"><ol class="ac-ol"><li>Enter only after price has closed back inside the range following the poke (confirmation), not while it is still outside.</li><li>Stop just beyond the extreme of the poke. First target the far side of the range.</li><li>Plan at least 2R. Risk 1–2% of the account per trade.</li><li>Light volume on the poke is better than heavy volume: heavy volume can mean a real breakout, so wait for a retest.</li><li>Skipping a doubtful setup is a decision, and usually the right one.</li></ol></div></div>
      <div class="bb-panel" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>HONEST NUMBERS FROM REAL HISTORY</div><div class="bb-pb" id="ac-libstats">${NX.load()}</div></div>
      <div class="bb-panel" style="--i:6"><div class="bb-ph"><span class="bb-n">6</span>HOW THE TRAINER WORKS</div><div class="bb-pad"><p class="ac-p"><b>FREE REPLAY</b>: a random stretch of real BTC / ETH / SOL history from 15 minutes to daily. PLAY reveals one candle at a time; STOP, STEP or change speed any time. You cannot scroll into the future.</p><p class="ac-p"><b>WYCKOFF DRILL</b>: a real setup found in years of 4h / daily data. The replay pauses at the decision point, you trade it, then it reveals the textbook answer and grades your events, entry, stop, target and risk.</p><p class="ac-p">Fills are at the close of the last visible candle. If a candle touches both your stop and target, the stop is assumed first. Fees of 0.04% per side are included.</p><button class="btn" id="ac-go-drill">START A WYCKOFF DRILL ▸</button></div></div></div>`;
    schematic($("#ac-sch1"), true); schematic($("#ac-sch2"), false);
    $("#ac-go-drill").addEventListener("click", () => { S.mode = "drill"; modeUi(); newDrill(); });
    try {
      const L = await lib(), rows = Object.entries(L).map(([k, v]) => { const [sym, tf] = k.split(":"); return v.building ? `<tr><td class="sym">${sym.slice(0, -4)} ${tf}</td><td colspan="8" class="dim">building…</td></tr>` : ["accumulation", "distribution"].map((kind) => `<tr><td class="sym">${sym.slice(0, -4)} ${tf}</td><td>${kind}</td><td class="r">${v[kind].n}</td><td class="r dn">${v[kind].stopped ?? "—"}%</td><td class="r">${v[kind].hit_2r ?? "—"}%</td><td class="r">${v[kind].target_hit ?? "—"}%</td><td class="r ${dir(v[kind].avg_r)}">${v[kind].avg_r ?? "—"}R</td></tr>`).join(""); });
      $("#ac-libstats").innerHTML = `<table class="bb-tbl"><thead><tr><th>MARKET</th><th>PATTERN</th><th class="r">FOUND</th><th class="r">STOPPED</th><th class="r">REACHED 2R</th><th class="r">REACHED RANGE TARGET</th><th class="r">AVG R (HOLD TO TARGET)</th></tr></thead><tbody>${rows.join("")}</tbody></table><div class="dim" style="padding:6px 10px;font-size:10.5px">Found by an automatic rule-based scan of years of candles (climax, rally, retest, poke and close back inside). It finds textbook-looking examples, not every real one. Most attempts fail: that is why the stop goes beyond the structure and the target must pay well.</div>`;
    } catch (e) { $("#ac-libstats").innerHTML = NX.err(e); }
  }

  // ------------------------------------------------------------------ build
  async function newFree(start) {
    const my = ++S.req; stop(); $("#ac-msg").innerHTML = '<div class="bb-empty bb-load">LOADING REAL CANDLES</div>';
    try { const r = await api(`/academy/session?symbol=${S.symbol}&tf=${S.tf}${start ? `&start=${start}` : ""}`); if (my !== S.req) return; S.mode = "free"; load(r, null); modeUi(); coachMsg("FREE REPLAY", "Press PLAY. The chart reveals one candle at a time: STOP, STEP or change speed whenever you want. Take trades below, and use MARK to label what you see. NEW SESSION jumps to a different stretch of history."); }
    catch (e) { if (my === S.req) $("#ac-msg").innerHTML = NX.err(e); }
  }
  async function build() {
    const el = $("#view-academy");
    el.innerHTML = `<div class="ac-shell"><div class="ac-main">
      <div class="ac-bar">${NX.chipsHtml(SYMS.map(([v, l]) => [v, l]), S.symbol).replace(/class="bb-chips\s*"/, 'class="bb-chips" id="ac-syms"')}<span class="gp-sep"></span>${NX.chipsHtml(FREE_TFS.map(([v, l]) => [v, l]), S.tf).replace(/class="bb-chips\s*"/, 'class="bb-chips" id="ac-tfs"')}<span class="gp-sep"></span>${NX.chipsHtml([["free", "FREE REPLAY"], ["drill", "WYCKOFF DRILL"], ["learn", "LEARN"], ["case", "מקרה בוחן"]], S.mode).replace(/class="bb-chips\s*"/, 'class="bb-chips" id="ac-modes"')}
        <select id="ac-kind" class="hidden"><option value="any">any setup</option><option value="accumulation">accumulation only</option><option value="distribution">distribution only</option></select><span class="sp"></span><button class="bb-chip on" id="ac-new">NEW SESSION</button></div>
      <div id="ac-play-area"><div class="ac-legend" id="ac-legend">&nbsp;</div><div class="ac-draw" id="ac-draw">${NXDraw.toolbarHtml()}</div><div class="ac-chartwrap"><div id="ac-chart"></div></div>
        <div class="ac-ctl"><button class="wd-btn" id="ac-play">▶ PLAY</button><button class="wd-btn on" id="ac-step" title="One candle (→)">STEP ▸</button><button class="wd-mini" id="ac-step10">+10</button>${SPEEDS.map((s) => `<button class="wd-mini ac-sp ${s === S.speed ? "on" : ""}" data-s="${s}">${s}×</button>`).join("")}<span class="ac-prog" id="ac-prog"></span><span class="sp" style="flex:1"></span><label class="dim">start at <input type="date" id="ac-date" class="ctl-in" style="min-width:110px;max-width:130px"/></label><button class="wd-mini" id="ac-go">GO</button></div>
        <div class="ac-trade"><button class="wd-btn ac-long" id="ac-long">▲ LONG</button><button class="wd-btn ac-short" id="ac-shortb">▼ SHORT</button><button class="wd-mini" id="ac-close" disabled>CLOSE</button>
          <label>STOP <input id="ac-stop" class="ctl-in" style="min-width:86px;max-width:96px"/></label><label>TARGET <input id="ac-tp" class="ctl-in" style="min-width:86px;max-width:96px"/></label><label>RISK % <input id="ac-risk" class="ctl-in" value="1" style="min-width:44px;max-width:50px"/></label><span id="ac-pos"></span><span class="sp" style="flex:1"></span><span id="ac-acct"></span></div>
        <div class="ac-tools"><b class="ac-tl">MARK</b>${LABELS.map(([k, l]) => `<button class="wd-mini ac-mk-b" data-l="${k}" title="Pick it, then click the bar">${l.split(" ")[0]}${k === "spring" ? "/UT" : k === "sos" ? "/SOW" : k === "lps" ? "/LPSY" : k === "climax" ? "" : ""}</button>`).join("")}<button class="wd-mini" id="ac-line" title="Then click the chart at a price">LINE</button><button class="wd-mini" id="ac-clr">CLEAR MARKS</button><span id="ac-marks" class="ac-marks"></span><span class="sp" style="flex:1"></span><button class="wd-mini" id="ac-help">HELP</button><button class="wd-mini" id="ac-hint">HINT</button><button class="wd-mini" id="ac-reveal">REVEAL</button></div></div>
      <div id="ac-learn" class="hidden"></div><div id="ac-case" class="hidden"></div></div>
      <div class="ac-side"><div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>COACH</div><div class="bb-pad" id="ac-msg"></div></div><div class="bb-panel" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>TAPE READING · last bar</div><div class="bb-pad" id="ac-tape"></div></div><div class="bb-panel" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>YOUR RECORD</div><div class="bb-pad" id="ac-stats"></div></div></div></div>`;
    initChart(); statsUi(); marksUi();
    S.draw = NXDraw.attach({ host: $(".ac-chartwrap"), chart: S.chart, series: S.cs, times: () => S.times, decimals: (v) => (Math.abs(v) < 10 ? 4 : 2), name: "academy",
      onTool: (t) => { if (t !== "cursor" && t !== "select") { S.markLabel = null; S.lineTool = false; document.querySelectorAll(".ac-mk-b").forEach((x) => x.classList.remove("on")); const l = $("#ac-line"); if (l) l.classList.remove("on"); } } });
    S.draw.bindToolbar($("#ac-draw"));
    const bind = (id, fn) => $(id).addEventListener("click", fn);
    NX.wire($("#ac-syms"), (f) => { S.symbol = f; S.mode === "drill" ? newDrill() : newFree(); });
    NX.wire($("#ac-tfs"), (f, c) => { if (c.classList.contains("dis")) { document.querySelectorAll("#ac-tfs .bb-chip").forEach((x) => x.classList.toggle("on", x.dataset.f === S.tf)); return; } S.tf = f; S.mode === "drill" ? newDrill() : newFree(); });
    NX.wire($("#ac-modes"), (f) => { if (f === "learn") { S.mode = "learn"; stop(); modeUi(); } else if (f === "case") { S.mode = "case"; stop(); modeUi(); } else if (f === "drill") { if (!["4h", "1d"].includes(S.tf)) { S.tf = "4h"; document.querySelectorAll("#ac-tfs .bb-chip").forEach((c) => c.classList.toggle("on", c.dataset.f === "4h")); } S.mode = "drill"; modeUi(); newDrill(); } else { S.mode = "free"; modeUi(); newFree(); } });
    $("#ac-kind").addEventListener("change", (e) => { S.kind = e.target.value; newDrill(); });
    bind("#ac-new", () => (S.mode === "drill" ? newDrill() : S.mode === "learn" || S.mode === "case" ? (S.mode = "free", modeUi(), newFree()) : newFree()));
    bind("#ac-play", () => (S.playing ? stop() : play())); bind("#ac-step", () => { stop(); step(1); }); bind("#ac-step10", () => { stop(); step(10); });
    document.querySelectorAll(".ac-sp").forEach((b) => b.addEventListener("click", () => { S.speed = Number(b.dataset.s); document.querySelectorAll(".ac-sp").forEach((x) => x.classList.toggle("on", x === b)); if (S.playing) { stop(); play(); } }));
    bind("#ac-go", () => { const d = $("#ac-date").value; if (!d) return; S.mode = "free"; newFree(Math.floor(new Date(d).getTime() / 1000)); });
    bind("#ac-long", () => { S.side = 1; openPos(1); }); bind("#ac-shortb", () => { S.side = -1; openPos(-1); });
    bind("#ac-close", () => { if (S.pos) closePos(S.all[S.n - 1].close, "closed", S.n - 1); ui(); });
    ["#ac-stop", "#ac-tp"].forEach((id) => $(id).addEventListener("input", () => { S.dirty[id === "#ac-stop" ? "stop" : "target"] = true; }));
    $("#ac-long").addEventListener("mouseenter", () => { S.side = 1; autofill(!S.dirty.stop); }); $("#ac-shortb").addEventListener("mouseenter", () => { S.side = -1; autofill(!S.dirty.stop); });
    document.querySelectorAll(".ac-mk-b").forEach((b) => b.addEventListener("click", () => { S.draw.setTool("cursor"); S.markLabel = S.markLabel === b.dataset.l ? null : b.dataset.l; document.querySelectorAll(".ac-mk-b").forEach((x) => x.classList.toggle("on", x.dataset.l === S.markLabel)); $("#ac-line").classList.remove("on"); S.lineTool = false; $("#ac-chart").style.cursor = S.markLabel ? "crosshair" : ""; }));
    bind("#ac-line", (e) => { S.draw.setTool("cursor"); S.lineTool = !S.lineTool; e.currentTarget.classList.toggle("on", S.lineTool); S.markLabel = null; document.querySelectorAll(".ac-mk-b").forEach((x) => x.classList.remove("on")); });
    S.chart.subscribeClick((p) => { if (!S.lineTool || !p.point) return; const price = S.cs.coordinateToPrice(p.point.y); S.userLines.push(S.cs.createPriceLine({ price, color: "#c58bff", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "" })); });
    bind("#ac-clr", () => { S.marks = []; S.userLines.filter((l) => !S.revealed).forEach((l) => { try { S.cs.removePriceLine(l); } catch (e) { /* gone */ } }); if (!S.revealed) S.userLines = []; drawMarkers(); marksUi(); });
    bind("#ac-help", () => { if (!S.drill || S.revealed) return; S.help.open = !S.help.open; if (S.help.open) helpUi(); else coachMsg("HELP CLOSED", "Press HELP again for the marking assistant."); });
    bind("#ac-hint", () => { S.help.open = false; const h = HINTS[Math.min(S.hintN++, HINTS.length - 1)]; coachMsg(`HINT ${Math.min(S.hintN, HINTS.length)}/${HINTS.length}`, h); });
    bind("#ac-reveal", () => { if (!S.decided && S.drill && S.n - 1 < S.drill.entry_i) return showToast("Play up to the decision point first"); reveal(); });
    document.addEventListener("keydown", (e) => { if (!S.active || S.mode === "learn" || S.mode === "case" || ["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement || {}).tagName)) return; if (e.code === "Space") { e.preventDefault(); S.playing ? stop() : play(); } else if (e.key === "ArrowRight") { stop(); step(e.shiftKey ? 10 : 1); } });
    new ResizeObserver(() => { /* autoSize handles the chart */ }).observe($("#ac-chart"));
    modeUi(); await newFree();
  }

  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    S.active = tab === "academy";
    if (S.active && !S.started) { S.started = true; build().catch((e) => { const v = $("#view-academy"); if (v) v.innerHTML = NX.err(e); }); }
    if (!S.active) stop();
  };
  NX.academy = { S };
})();
