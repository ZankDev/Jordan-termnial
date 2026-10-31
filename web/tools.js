/* TOOLS: OVDV/OVME/UOA options lab, PORT portfolio risk, ALRT alerts (+toasts), WKSP multi-panel workspace,
   NOSTRO research pages (WFO walk-forward, REGIME, JRNL journal, EXEC execution stats) and the HELP overlay.
   Depends on NX (invest.js), window.NXR renderers (analytics.js) and api() (dashboard.js). */
(function () {
  "use strict";
  const NX = window.NX, R = window.NXR;
  const { $, esc, dir, num, px, pct, big, signed } = NX;
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const q = encodeURIComponent;
  const { chipsHtml, wire } = NX;
  const pane = (n, title, meta, body, cls = "", extra = "") => `<div class="bb-panel ${cls}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${meta || ""}</span></div>${extra}<div class="pn-body" ${body.id ? `id="${body.id}"` : ""}>${body.html || ""}</div></div>`;
  const kpi = (l, v, s = "", c = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
  const usd = (v) => `${v < 0 ? "−" : ""}$${num(Math.abs(v), 0)}`;
  const cell = (v, max) => { const t = Math.max(-1, Math.min(1, v / (max || 1))); return t >= 0 ? `rgba(43,224,126,${0.08 + 0.55 * t})` : `rgba(255,74,74,${0.08 + 0.55 * -t})`; };

  // ================================================================== tabbed shell (same pattern as analytics.js)
  const SUB = (NX.SUB = NX.SUB || {});
  function tabbed(viewId, title, tabs, first) {
    const el = $(`#view-${viewId}`);
    el.innerHTML = `<div class="bb-panel tabbed" style="--i:0"><div class="bb-ph"><span class="bb-n">&#9656;</span>${title}<span class="sp"></span>${chipsHtml(tabs.map((t) => [t.id, t.label]), first)}</div><div class="tabbed-body" id="${viewId}-body"></div></div>`;
    const body = $(`#${viewId}-body`);
    const select = (id) => { el.querySelectorAll(".tabbed > .bb-ph .bb-chip").forEach((c) => c.classList.toggle("on", c.dataset.f === id)); body.innerHTML = ""; tabs.find((x) => x.id === id).render(body); };
    wire(el.querySelector(".tabbed > .bb-ph"), select);
    SUB[viewId] = select;
    select(first);
  }
  NX.openSub2 = NX.openSub;

  // ================================================================== OVME: Black-Scholes
  const ncdf = (x) => { const t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp((-x * x) / 2); const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; };
  const npdf = (x) => 0.3989423 * Math.exp((-x * x) / 2);
  function bs(S, K, T, sig, r, qd, call) {
    if (T <= 0 || sig <= 0) { const v = call ? Math.max(0, S - K) : Math.max(0, K - S); return { price: v, delta: call ? (S > K ? 1 : 0) : (S < K ? -1 : 0), gamma: 0, theta: 0, vega: 0, rho: 0 }; }
    const sq = Math.sqrt(T), d1 = (Math.log(S / K) + (r - qd + 0.5 * sig * sig) * T) / (sig * sq), d2 = d1 - sig * sq;
    const eq = Math.exp(-qd * T), er = Math.exp(-r * T);
    const price = call ? S * eq * ncdf(d1) - K * er * ncdf(d2) : K * er * ncdf(-d2) - S * eq * ncdf(-d1);
    const decay = -(S * eq * npdf(d1) * sig) / (2 * sq);
    const theta = (call ? decay - r * K * er * ncdf(d2) + qd * S * eq * ncdf(d1) : decay + r * K * er * ncdf(-d2) - qd * S * eq * ncdf(-d1)) / 365;
    return { price, delta: call ? eq * ncdf(d1) : -eq * ncdf(-d1), gamma: (eq * npdf(d1)) / (S * sig * sq), theta, vega: (S * eq * npdf(d1) * sq) / 100, rho: ((call ? 1 : -1) * K * T * er * ncdf(call ? d2 : -d2)) / 100 };
  }
  NX.bs = bs;

  const STRATS = {
    "Long call": (S) => [{ k: "call", s: 1, K: S, n: 1 }],
    "Long put": (S) => [{ k: "put", s: 1, K: S, n: 1 }],
    "Covered call": (S) => [{ k: "stock", s: 1, K: S, n: 100 }, { k: "call", s: -1, K: S * 1.05, n: 1 }],
    "Protective put": (S) => [{ k: "stock", s: 1, K: S, n: 100 }, { k: "put", s: 1, K: S * 0.95, n: 1 }],
    "Bull call spread": (S) => [{ k: "call", s: 1, K: S, n: 1 }, { k: "call", s: -1, K: S * 1.05, n: 1 }],
    "Bear put spread": (S) => [{ k: "put", s: 1, K: S, n: 1 }, { k: "put", s: -1, K: S * 0.95, n: 1 }],
    "Long straddle": (S) => [{ k: "call", s: 1, K: S, n: 1 }, { k: "put", s: 1, K: S, n: 1 }],
    "Long strangle": (S) => [{ k: "call", s: 1, K: S * 1.05, n: 1 }, { k: "put", s: 1, K: S * 0.95, n: 1 }],
    "Iron condor": (S) => [{ k: "put", s: 1, K: S * 0.9, n: 1 }, { k: "put", s: -1, K: S * 0.95, n: 1 }, { k: "call", s: -1, K: S * 1.05, n: 1 }, { k: "call", s: 1, K: S * 1.1, n: 1 }],
    "Butterfly": (S) => [{ k: "call", s: 1, K: S * 0.95, n: 1 }, { k: "call", s: -2, K: S, n: 1 }, { k: "call", s: 1, K: S * 1.05, n: 1 }],
  };
  const round5 = (K, S) => { const st = S > 500 ? 5 : S > 100 ? 2.5 : S > 25 ? 1 : 0.5; return Math.round(K / st) * st; };

  R.ovme = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { sym: p.symbol || "SPY", S: 100, iv: 25, days: 30, r: 4.0, qd: 0, legs: [] });
    el.innerHTML = `<div class="bb-grid"><div class="bb-panel s4" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>OPTION PRICER<span class="sp"></span><span class="meta">Black-Scholes</span></div><div class="bb-pad">
      <div class="form-row"><label>Underlying</label><span><input id="om-sym" value="${esc(st.sym)}" style="width:90px"/> <button class="btn btn-small" id="om-load" style="margin:0">LOAD</button></span></div>
      <div class="form-row"><label>Spot</label><input id="om-S" type="number" step="any" value="${st.S}"/></div>
      <div class="form-row"><label>Strike</label><input id="om-K" type="number" step="any" value=""/></div>
      <div class="form-row"><label>Days to expiry</label><input id="om-T" type="number" step="any" value="${st.days}"/></div>
      <div class="form-row"><label>Implied vol %</label><input id="om-iv" type="number" step="any" value="${st.iv}"/></div>
      <div class="form-row"><label>Risk-free rate %</label><input id="om-r" type="number" step="any" value="${st.r}"/></div>
      <div class="form-row"><label>Dividend yield %</label><input id="om-q" type="number" step="any" value="${st.qd}"/></div>
      <div id="om-src" class="dim" style="font-size:10.5px;margin:4px 0"></div></div></div>
      <div class="bb-panel s8" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>PRICE &amp; GREEKS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th></th><th class="r">PRICE</th><th class="r">DELTA</th><th class="r">GAMMA</th><th class="r">THETA / DAY</th><th class="r">VEGA / 1%</th><th class="r">RHO / 1%</th></tr></thead><tbody id="om-g"></tbody></table><div class="dim" style="padding:6px 10px;font-size:10.5px">Per share. Multiply by 100 for one contract. Theta is the daily decay; vega the change per one volatility point.</div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>PAYOFF BUILDER<span class="sp"></span>${chipsHtml(Object.keys(STRATS).map((k) => [k, k]), "")}</div><div class="bb-grid" style="padding:8px"><div class="s5"><table class="bb-tbl"><thead><tr><th>LEG</th><th>TYPE</th><th class="r">STRIKE</th><th class="r">QTY</th><th class="r">PREMIUM</th><th></th></tr></thead><tbody id="pb-legs"></tbody></table>
        <div style="display:flex;gap:4px;margin-top:6px"><button class="bb-chip on" id="pb-add">+ LEG</button><button class="bb-chip" id="pb-clear">CLEAR</button></div><div id="pb-stats" class="pb-stats"></div></div><div class="s7"><div id="pb-chart"></div></div></div></div></div>`;
    const v = (id) => Number($(id, el).value);
    const read = () => { st.S = v("#om-S"); st.iv = v("#om-iv"); st.days = v("#om-T"); st.r = v("#om-r"); st.qd = v("#om-q"); };
    const priceRow = () => {
      read(); const K = v("#om-K") || round5(st.S, st.S), T = st.days / 365;
      $("#om-g", el).innerHTML = [["CALL", true], ["PUT", false]].map(([l, c]) => { const g = bs(st.S, K, T, st.iv / 100, st.r / 100, st.qd / 100, c); return `<tr><td class="sym">${l} ${num(K, 2)}</td><td class="r"><b>${num(g.price, 3)}</b></td><td class="r">${num(g.delta, 3)}</td><td class="r">${num(g.gamma, 4)}</td><td class="r dn">${num(g.theta, 3)}</td><td class="r">${num(g.vega, 3)}</td><td class="r">${num(g.rho, 3)}</td></tr>`; }).join("");
    };
    const legPrem = (l) => (l.k === "stock" ? st.S : bs(st.S, l.K, st.days / 365, st.iv / 100, st.r / 100, st.qd / 100, l.k === "call").price);
    const payoff = () => {
      read();
      const legs = st.legs;
      if (!legs.length) { $("#pb-chart", el).innerHTML = '<div class="bb-empty"><b>PICK A STRATEGY</b>or add legs to see the payoff at expiry.</div>'; $("#pb-stats", el).innerHTML = ""; return; }
      const lo = st.S * 0.6, hi = st.S * 1.4, N = 120, xs = [], exp = [], now = [];
      for (let i = 0; i <= N; i++) {
        const S = lo + ((hi - lo) * i) / N; xs.push(S);
        let e = 0, n0 = 0;
        legs.forEach((l) => {
          const m = l.k === "stock" ? 1 : 100, prem = l.prem ?? legPrem(l);
          if (l.k === "stock") { e += l.s * l.n * (S - prem); n0 += l.s * l.n * (S - prem); return; }
          const iv = l.k === "call" ? Math.max(0, S - l.K) : Math.max(0, l.K - S);
          e += l.s * l.n * m * (iv - prem);
          n0 += l.s * l.n * m * (bs(S, l.K, st.days / 365, st.iv / 100, st.r / 100, st.qd / 100, l.k === "call").price - prem);
        });
        exp.push(e); now.push(n0);
      }
      const be = []; for (let i = 1; i <= N; i++) if ((exp[i - 1] < 0) !== (exp[i] < 0)) be.push(xs[i - 1] + ((xs[i] - xs[i - 1]) * (0 - exp[i - 1])) / (exp[i] - exp[i - 1]));
      const mx = Math.max(...exp), mn = Math.min(...exp), sl0 = exp[1] - exp[0], sl1 = exp[N] - exp[N - 1];
      const cost = legs.reduce((s, l) => s + (l.k === "stock" ? 0 : l.s * l.n * 100 * (l.prem ?? legPrem(l))), 0);
      $("#pb-stats", el).innerHTML = `<div class="bb-kpis six">${kpi("NET " + (cost >= 0 ? "DEBIT" : "CREDIT"), "$" + num(Math.abs(cost), 0))}${kpi("MAX PROFIT", sl1 > 1e-6 || sl0 < -1e-6 ? "UNLIMITED" : usd(mx), "", "up")}${kpi("MAX LOSS", sl0 > 1e-6 || sl1 < -1e-6 ? "UNLIMITED" : usd(mn), "", "dn")}${kpi("BREAKEVEN", be.length ? be.map((b) => num(b, 2)).join(" / ") : "—")}</div>`;
      NX.multiLine($("#pb-chart", el), xs.map((x) => num(x, 0)), [{ name: "AT EXPIRY", color: "#ffa028", values: exp, width: 2.4 }, { name: `TODAY (${st.days}d left, IV ${st.iv}%)`, color: "#5aa9ff", values: now, dash: [4, 3] }], { height: 300, fmt: usd, baseline: 0, ticks: 8 });
    };
    const drawLegs = () => {
      $("#pb-legs", el).innerHTML = st.legs.map((l, i) => `<tr data-i="${i}"><td class="dim">${i + 1}</td><td><select class="l-s"><option value="1" ${l.s > 0 ? "selected" : ""}>BUY</option><option value="-1" ${l.s < 0 ? "selected" : ""}>SELL</option></select> <select class="l-k"><option ${l.k === "call" ? "selected" : ""} value="call">CALL</option><option ${l.k === "put" ? "selected" : ""} value="put">PUT</option><option ${l.k === "stock" ? "selected" : ""} value="stock">STOCK</option></select></td>
        <td class="r"><input class="l-K" type="number" step="any" value="${num(l.K, 2).replace(/,/g, "")}" style="width:72px"/></td><td class="r"><input class="l-n" type="number" step="1" value="${Math.abs(l.n)}" style="width:52px"/></td><td class="r"><input class="l-p" type="number" step="any" value="${num(l.prem ?? legPrem(l), 2).replace(/,/g, "")}" style="width:64px"/></td><td><span class="bb-x">✕</span></td></tr>`).join("");
      $("#pb-legs", el).querySelectorAll("tr").forEach((tr) => {
        const i = Number(tr.dataset.i), l = st.legs[i];
        tr.querySelector(".l-s").addEventListener("change", (e) => { l.s = Number(e.target.value); payoff(); });
        tr.querySelector(".l-k").addEventListener("change", (e) => { l.k = e.target.value; l.prem = undefined; drawLegs(); payoff(); });
        tr.querySelector(".l-K").addEventListener("change", (e) => { l.K = Number(e.target.value); l.prem = undefined; drawLegs(); payoff(); });
        tr.querySelector(".l-n").addEventListener("change", (e) => { l.n = Math.max(1, Number(e.target.value)); payoff(); });
        tr.querySelector(".l-p").addEventListener("change", (e) => { l.prem = Number(e.target.value); payoff(); });
        tr.querySelector(".bb-x").addEventListener("click", () => { st.legs.splice(i, 1); drawLegs(); payoff(); });
      });
    };
    const recompute = () => { priceRow(); st.legs.forEach((l) => (l.prem = undefined)); drawLegs(); payoff(); };
    ["#om-S", "#om-K", "#om-T", "#om-iv", "#om-r", "#om-q"].forEach((id) => $(id, el).addEventListener("change", recompute));
    const load = async () => {
      st.sym = $("#om-sym", el).value.trim().toUpperCase() || "SPY";
      $("#om-src", el).textContent = "loading spot and implied vol…";
      try {
        const [qt, ov] = await Promise.all([api(`/invest/quotes?symbols=${q(st.sym)}`), api(`/market/options/vol?symbol=${q(st.sym)}`).catch(() => null)]);
        const row = qt.rows[0]; if (row) $("#om-S", el).value = row.price;
        if (ov && ov.iv30) $("#om-iv", el).value = ov.iv30.toFixed(1);
        $("#om-K", el).value = round5(row ? row.price : st.S, row ? row.price : st.S);
        $("#om-src", el).textContent = ov && ov.iv30 ? `${st.sym}: spot ${num(row.price)}, 30-day implied vol ${num(ov.iv30, 1)}%` : `${st.sym}: spot loaded, no listed options, using the IV you entered`;
      } catch (e) { $("#om-src", el).textContent = "could not load the quote: enter values by hand"; }
      recompute();
    };
    $("#om-load", el).addEventListener("click", load);
    $("#om-sym", el).addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
    wire(el.querySelector(".s12 .bb-ph"), (name) => { read(); st.legs = STRATS[name](st.S).map((l) => ({ ...l, K: round5(l.K, st.S) })); drawLegs(); payoff(); });
    $("#pb-add", el).addEventListener("click", () => { read(); st.legs.push({ k: "call", s: 1, K: round5(st.S, st.S), n: 1 }); drawLegs(); payoff(); });
    $("#pb-clear", el).addEventListener("click", () => { st.legs = []; drawLegs(); payoff(); });
    await load();
  };

  // ================================================================== OVDV: vol surface, skew, IV rank
  const volColor = (v, lo, hi) => { const t = (v - lo) / (hi - lo || 1); const r = Math.round(30 + 225 * t), g = Math.round(60 + 90 * (1 - Math.abs(t - 0.5) * 2)), b = Math.round(200 - 170 * t); return `rgb(${r},${g},${b})`; };
  R.ovdv = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { sym: p.symbol || "SPY", exp: 0 });
    el.innerHTML = `<div class="ctl"><input class="ctl-in" id="ov-s" value="${esc(st.sym)}" style="max-width:150px"/><button class="bb-chip on" id="ov-go">GO</button><span class="sp"></span><span class="dim" id="ov-note"></span></div><div id="ov-b">${NX.load()}</div>`;
    const run = async () => {
      st.sym = $("#ov-s", el).value.trim().toUpperCase();
      const box = $("#ov-b", el); box.innerHTML = NX.load("BUILDING SURFACE");
      try {
        const d = await api(`/market/options/vol?symbol=${q(st.sym)}`);
        $("#ov-note", el).textContent = d.note;
        const m = d.moneyness.map((x) => `${Math.round(x * 100)}%`), all = d.surface.flatMap((r) => r.iv.filter(isNum)), lo = Math.min(...all), hi = Math.max(...all);
        const exps = d.expiries;
        const rank = d.iv_rank;
        box.innerHTML = `<div class="bb-kpis six">${kpi("SPOT", px(d.spot))}${kpi("30D IMPLIED VOL", isNum(d.iv30) ? num(d.iv30, 1) + "%" : "—", "interpolated ATM")}${kpi("30D REALISED VOL", isNum(d.hv["30d"]) ? num(d.hv["30d"], 1) + "%" : "—", `10d ${isNum(d.hv["10d"]) ? num(d.hv["10d"], 1) : "—"}% · 60d ${isNum(d.hv["60d"]) ? num(d.hv["60d"], 1) : "—"}%`)}${kpi("IV RANK", isNum(rank) ? num(rank, 0) : "—", "vs 1-year realised range", rank > 70 ? "dn" : rank < 30 ? "up" : "")}${kpi("IV PERCENTILE", isNum(d.iv_percentile) ? num(d.iv_percentile, 0) : "—", "share of days below")}${kpi("PUT / CALL (VOL)", isNum(d.put_call_volume) ? num(d.put_call_volume, 2) : "—", `open interest ${isNum(d.put_call_oi) ? num(d.put_call_oi, 2) : "—"}`)}</div>
        <div class="bb-grid"><div class="bb-panel s7" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>IMPLIED VOL SURFACE<span class="sp"></span><span class="meta">expiry × moneyness (strike / spot), OTM options</span></div><div class="bb-pad"><div class="vs" style="grid-template-columns:70px repeat(${m.length},1fr)"><div></div>${m.map((x) => `<div class="ch">${x}</div>`).join("")}${d.surface.map((r, ri) => `<div class="ch rh ${ri === st.exp ? "on" : ""}" data-e="${ri}">${new Date(r.expiry * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} <em>${Math.round(r.days)}d</em></div>${r.iv.map((v2) => `<div class="cc" style="background:${isNum(v2) ? volColor(v2, lo, hi) : "#111"}" title="${isNum(v2) ? num(v2, 1) + "%" : "n/a"}">${isNum(v2) ? num(v2, 0) : ""}</div>`).join("")}`).join("")}</div></div></div>
        <div class="bb-panel s5" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>SKEW · <span id="ov-skl"></span></div><div class="bb-pad"><div id="ov-sk"></div></div></div>
        <div class="bb-panel s6" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>TERM STRUCTURE (ATM IV)</div><div class="bb-pad"><div id="ov-ts"></div></div></div>
        <div class="bb-panel s6" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>EXPIRIES</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>EXPIRY</th><th class="r">DAYS</th><th class="r">ATM IV</th><th class="r">SKEW (95−105)</th><th class="r">EXPECTED MOVE</th></tr></thead><tbody>${exps.map((e) => `<tr><td class="sym">${new Date(e.expiry * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" })}</td><td class="r dim">${Math.round(e.days)}</td><td class="r">${isNum(e.atm_iv) ? num(e.atm_iv, 1) + "%" : "—"}</td><td class="r ${dir(e.skew)}">${isNum(e.skew) ? signed(e.skew, 1) : "—"}</td><td class="r">${isNum(e.exp_move_pct) ? "±" + num(e.exp_move_pct, 1) + "%" : "—"}</td></tr>`).join("")}</tbody></table></div></div></div>`;
        const skew = () => { $("#ov-skl", box).textContent = new Date(d.surface[st.exp].expiry * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }).toUpperCase(); NX.multiLine($("#ov-sk", box), m, [{ name: "IV %", color: "#ffa028", values: d.surface[st.exp].iv, width: 2.4 }], { height: 250, dots: true, fmt: (v2) => num(v2, 1) + "%", ticks: 8 }); };
        skew();
        NX.multiLine($("#ov-ts", box), exps.map((e) => `${Math.round(e.days)}d`), [{ name: "ATM IV %", color: "#5aa9ff", values: exps.map((e) => e.atm_iv), width: 2.4 }, { name: "30d realised vol", color: "#666", values: exps.map(() => d.hv["30d"]), dash: [4, 3] }], { height: 250, dots: true, fmt: (v2) => num(v2, 1) + "%", ticks: exps.length - 1 });
        box.querySelectorAll(".rh[data-e]").forEach((c) => c.addEventListener("click", () => { st.exp = Number(c.dataset.e); box.querySelectorAll(".rh").forEach((x) => x.classList.toggle("on", x === c)); skew(); }));
      } catch (e) { box.innerHTML = NX.err(e); }
    };
    $("#ov-go", el).addEventListener("click", run);
    $("#ov-s", el).addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    await run();
  };

  R.uoa = async (el, p = {}, o = {}) => {
    el.innerHTML = `<div class="ctl">${chipsHtml([["all", "ALL"], ["CALL", "CALLS"], ["PUT", "PUTS"]], "all")}<span class="sp"></span><span class="dim">volume ≥ 500, premium ≥ $75k, volume ≥ 1.5× open interest · 46 most liquid optionable names</span></div><div class="bb-pb eco-b" id="ua-b">${NX.load("SCANNING OPTION CHAINS")}</div>`;
    let rows = [], side = "all";
    const draw = () => {
      const r = rows.filter((x) => side === "all" || x.type === side);
      $("#ua-b", el).innerHTML = `<table class="bb-tbl"><thead><tr><th>SYMBOL</th><th>TYPE</th><th class="r">STRIKE</th><th>EXPIRY</th><th class="r">OTM</th><th class="r">VOLUME</th><th class="r">OPEN INT</th><th class="r">VOL / OI</th><th class="r">LAST</th><th class="r">IV</th><th class="r">PREMIUM</th></tr></thead><tbody>${r.map((x) => `<tr class="click" data-sec="${esc(x.symbol)}" data-fn="OMON"><td class="sym">${esc(x.symbol)}</td><td class="${x.type === "CALL" ? "up" : "dn"}">${x.type}</td><td class="r">${num(x.strike, 2)}</td><td class="dim">${new Date(x.expiry * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</td><td class="r dim">${isNum(x.otm_pct) ? signed(x.otm_pct, 1) + "%" : ""}</td><td class="r">${num(x.vol, 0)}</td><td class="r dim">${num(x.oi, 0)}</td><td class="r">${isNum(x.ratio) ? num(x.ratio, 1) + "x" : "new"}</td><td class="r">${num(x.last, 2)}</td><td class="r dim">${num(x.iv, 0)}%</td><td class="r"><b>${big(x.premium, "$")}</b></td></tr>`).join("") || '<tr><td colspan="11" class="dim">nothing unusual right now</td></tr>'}</tbody></table>`;
      $("#ua-b", el).querySelectorAll("tr[data-fn]").forEach((tr) => tr.addEventListener("click", () => openSecurity(tr.dataset.sec, "OMON")));
    };
    wire(el.querySelector(".ctl"), (f) => { side = f; draw(); });
    try { rows = (await api("/market/options/unusual")).rows; draw(); } catch (e) { $("#ua-b", el).innerHTML = NX.err(e); }
  };

  // ================================================================== PORT: portfolio risk
  R.port = async (el, p = {}, o = {}) => {
    el.innerHTML = NX.load("COMPUTING RISK");
    try {
      const d = await api(`/market/portfolio/risk?benchmark=${q(p.benchmark || "SPY")}`), m = d.metrics;
      const bmin = Math.min(...Object.values(m.betas).filter(isNum), 0);
      el.innerHTML = `<div class="bb-kpis six">${kpi("PORTFOLIO VALUE", "$" + num(d.value, 0), `${d.positions.length} priced positions`)}${kpi("ANN. RETURN", pct(m.return_ann, true, 1), "1y, current weights", dir(m.return_ann))}${kpi("ANN. VOLATILITY", isNum(m.vol_ann) ? num(m.vol_ann, 1) + "%" : "—")}${kpi("SHARPE", isNum(m.sharpe) ? num(m.sharpe, 2) : "—", `rf ${num(m.rf, 2)}%`, dir(m.sharpe))}${kpi("MAX DRAWDOWN", pct(m.max_drawdown, false, 1), "", "dn")}${kpi("1-DAY VaR 95%", isNum(m.var95_pct) ? num(m.var95_pct, 1) + "%" : "—", isNum(m.var95_usd) ? "≈ $" + num(m.var95_usd, 0) : "")}</div>
        <div class="bb-grid"><div class="bb-panel s8" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>PORTFOLIO vs BENCHMARKS<span class="sp"></span><span class="meta">indexed to 100, current weights over 1 year</span></div><div class="bb-pad"><div id="pr-c"></div></div></div>
        <div class="bb-panel s4" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>RISK MEASURES</div><div class="bb-pb"><table class="bb-tbl"><tbody><tr><td>VaR 99% (1 day)</td><td class="r">${isNum(m.var99_pct) ? num(m.var99_pct, 1) + "%" : "—"}</td></tr><tr><td>CVaR 95% (expected shortfall)</td><td class="r">${isNum(m.cvar95_pct) ? num(m.cvar95_pct, 1) + "%" : "—"}</td></tr>${Object.entries(m.betas).map(([k, b]) => `<tr><td>Beta vs ${esc(k)}</td><td class="r"><b>${isNum(b) ? num(b, 2) : "—"}</b></td></tr>`).join("")}<tr><td>Observations</td><td class="r dim">${m.days} days</td></tr></tbody></table></div></div>
        <div class="bb-panel s5" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>EXPOSURE BY SECTOR</div><div class="bb-pad">${d.exposure.map((x) => `<div class="hbar"><span>${esc(x.sector)}</span><i style="width:${Math.max(2, x.pct)}%"></i><b>${num(x.pct, 1)}%</b></div>`).join("")}</div></div>
        <div class="bb-panel s7" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>SCENARIO SHOCKS<span class="sp"></span><span class="meta">each position's 1-year beta to the shocked factor</span></div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>SCENARIO</th><th class="r">PORTFOLIO P&amp;L</th><th class="r">%</th><th>WORST HIT</th></tr></thead><tbody>${d.scenarios.map((s) => { const w = [...s.positions].sort((a, b) => a.impact_usd - b.impact_usd)[s.shock < 0 ? 0 : s.positions.length - 1]; return `<tr><td class="sym">${esc(s.label)}</td><td class="r ${dir(s.total_usd)}">${signed(s.total_usd, 0)}</td><td class="r ${dir(s.total_pct)}">${pct(s.total_pct, true, 1)}</td><td class="dim">${w ? `${esc(w.symbol)} ${signed(w.impact_usd, 0)}` : ""}</td></tr>`; }).join("")}</tbody></table></div></div>
        <div class="bb-panel s12" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>POSITIONS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>SYMBOL</th><th>TYPE</th><th class="r">WEIGHT</th><th class="r">VALUE</th><th class="r">P&amp;L</th></tr></thead><tbody>${d.positions.map((x) => `<tr class="click" data-sec="${esc(x.symbol)}"><td class="sym">${esc(x.symbol)}</td><td class="dim">${x.kind}</td><td class="r">${num(x.weight, 1)}%</td><td class="r">$${num(x.value, 0)}</td><td class="r ${dir(x.pnl_pct)}">${pct(x.pnl_pct, true, 1)}</td></tr>`).join("")}</tbody></table><div class="dim" style="padding:6px 10px;font-size:10.5px">${esc(d.note)}</div></div></div></div>`;
      const ks = Object.keys(d.benchmarks);
      NX.multiLine($("#pr-c", el), d.dates.map((x) => String(x).slice(0, 10)), [{ name: "MY PORTFOLIO", color: "#ffa028", values: d.portfolio, width: 2.4 }, ...ks.map((k, i) => ({ name: k, color: ["#5aa9ff", "#2be07e"][i], values: d.benchmarks[k] }))], { height: o.compact ? 200 : 300, fmt: (v) => num(v, 0), baseline: 100 });
    } catch (e) { el.innerHTML = NX.err(e); }
  };

  // ================================================================== ALRT: alerts
  const ALERT_KINDS = { price: "Price crosses a level", pct_move: "Daily % move", volume: "Volume spike", news: "News keyword", econ: "Economic release" };
  async function alertsPage(el) {
    el.innerHTML = `<div class="bb-grid"><div class="bb-panel s4" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>NEW ALERT</div><div class="bb-pad">
      <div class="form-row"><label>Type</label><select id="al-k">${Object.entries(ALERT_KINDS).map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></div><div id="al-f"></div>
      <div class="form-row"><label>Note (optional)</label><input id="al-note" style="width:170px"/></div>
      <div class="form-row"><label>Fire once, then disarm</label><input id="al-once" type="checkbox" checked style="width:auto"/></div>
      <button class="btn" id="al-add">CREATE ALERT</button>
      <div style="display:flex;gap:4px;margin-top:6px"><button class="btn btn-small" id="al-test" style="flex:1;margin:0">TEST TELEGRAM</button><button class="btn btn-small" id="al-run" style="flex:1;margin:0">CHECK NOW</button></div>
      <div class="dim" style="font-size:10.5px;margin-top:8px">Checked every 60 s while the server runs. Delivery: Telegram (if the bot is set up) and a toast in this app.</div></div></div>
      <div class="bb-panel s8" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>ACTIVE ALERTS</div><div class="bb-pb" id="al-list"></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>HISTORY</div><div class="bb-pb" id="al-log"></div></div></div>`;
    const form = () => {
      const k = $("#al-k", el).value;
      $("#al-f", el).innerHTML = { price: `<div class="form-row"><label>Symbol</label><input id="a-sym" placeholder="AAPL" style="width:100px"/></div><div class="form-row"><label>Goes</label><select id="a-op"><option value="above">above</option><option value="below">below</option></select></div><div class="form-row"><label>Price</label><input id="a-val" type="number" step="any" style="width:100px"/></div>`,
        pct_move: `<div class="form-row"><label>Symbol</label><input id="a-sym" style="width:100px"/></div><div class="form-row"><label>Direction</label><select id="a-op"><option value="any">up or down</option><option value="up">up</option><option value="down">down</option></select></div><div class="form-row"><label>Move at least %</label><input id="a-val" type="number" step="any" value="3" style="width:100px"/></div>`,
        volume: `<div class="form-row"><label>Symbol</label><input id="a-sym" style="width:100px"/></div><div class="form-row"><label>Volume ≥ × average</label><input id="a-val" type="number" step="any" value="2" style="width:100px"/></div>`,
        news: `<div class="form-row"><label>Symbol (or leave blank and search)</label><input id="a-sym" style="width:100px"/></div><div class="form-row"><label>Search query</label><input id="a-query" placeholder="only if no symbol" style="width:130px"/></div><div class="form-row"><label>Headline contains</label><input id="a-kw" placeholder="earnings" style="width:130px"/></div>`,
        econ: `<div class="form-row"><label>Event contains</label><input id="a-kw" value="CPI" style="width:130px"/></div><div class="form-row"><label>Country</label><input id="a-sym" value="USD" style="width:70px"/></div><div class="form-row"><label>Minutes before</label><input id="a-val" type="number" value="30" style="width:80px"/></div>` }[k];
    };
    $("#al-k", el).addEventListener("change", form); form();
    const list = async () => {
      const [a, l] = await Promise.all([api("/alerts"), api("/alerts/log?limit=40")]);
      $("#al-list", el).innerHTML = `<table class="bb-tbl"><thead><tr><th>ON</th><th>TYPE</th><th>WHAT</th><th>NOTE</th><th class="r">FIRED</th><th></th></tr></thead><tbody>${a.alerts.map((x) => { const e = x.extra || {}; const what = { price: `${x.symbol} ${x.op} ${num(x.value, 2)}`, pct_move: `${x.symbol} moves ${x.op === "any" ? "±" : x.op === "up" ? "+" : "−"}${x.value}% today`, volume: `${x.symbol} volume ≥ ${x.value}× avg`, news: `${x.symbol || e.query}: “${esc(e.keyword)}”`, econ: `${e.keyword} (${x.symbol || "any"}) ${e.minutes_before} min before` }[x.kind]; return `<tr class="${x.active ? "" : "past"}"><td><input type="checkbox" data-t="${x.id}" ${x.active ? "checked" : ""}/></td><td class="dim">${ALERT_KINDS[x.kind]}</td><td class="sym">${what}</td><td class="dim">${esc(x.note || "")}</td><td class="r dim">${x.fired_count}${x.last_fired ? " · " + NX.ago(x.last_fired) : ""}</td><td><span class="bb-x" data-d="${x.id}">✕</span></td></tr>`; }).join("") || '<tr><td colspan="6" class="dim">No alerts yet.</td></tr>'}</tbody></table>`;
      $("#al-list", el).querySelectorAll("[data-t]").forEach((c) => c.addEventListener("change", async () => { await api(`/alerts/${c.dataset.t}/toggle`, { method: "POST" }); list(); }));
      $("#al-list", el).querySelectorAll("[data-d]").forEach((c) => c.addEventListener("click", async () => { await api(`/alerts/${c.dataset.d}`, { method: "DELETE" }); list(); }));
      $("#al-log", el).innerHTML = l.log.length ? `<table class="bb-tbl"><tbody>${l.log.map((x) => `<tr><td class="dim" style="width:150px">${new Date(x.ts * 1000).toLocaleString("en-GB")}</td><td>${esc(x.message).replace(/\n/g, "<br>")}</td><td class="dim r">${x.delivered ? "Telegram ✓" : "in-app"}</td></tr>`).join("")}</tbody></table>` : '<div class="bb-empty">Nothing has fired yet.</div>';
    };
    $("#al-add", el).addEventListener("click", async () => {
      const k = $("#al-k", el).value, g = (id) => ($(id, el) ? $(id, el).value : ""), body = { kind: k, symbol: g("#a-sym") || null, op: g("#a-op") || null, value: g("#a-val") ? Number(g("#a-val")) : null, keyword: g("#a-kw") || null, query: g("#a-query") || null, minutes_before: k === "econ" ? Number(g("#a-val")) : null, note: g("#al-note") || null, once: $("#al-once", el).checked };
      if (k === "econ") body.value = null;
      try { await api("/alerts", { method: "POST", body: JSON.stringify(body) }); showToast("Alert created"); list(); } catch (e) { showToast(e.message); }
    });
    $("#al-test", el).addEventListener("click", async () => { try { await api("/alerts/test", { method: "POST" }); showToast("Test sent to Telegram"); } catch (e) { showToast(e.message); } });
    $("#al-run", el).addEventListener("click", async () => { const r = await api("/alerts/run-now", { method: "POST" }); showToast(`${r.fired} alert(s) fired`); list(); });
    await list();
  }
  // in-app toasts: poll the alert log, show anything newer than the last id seen
  (function alertToasts() {
    const KEY = "nx_alert_seen";
    const get = () => { try { return Number(localStorage.getItem(KEY)); } catch (e) { return NaN; } };
    const set = (v) => { try { localStorage.setItem(KEY, String(v)); } catch (e) { /* private mode */ } };
    const poll = async () => {
      try {
        const seen = get(), r = await api(`/alerts/log?since_id=${Number.isNaN(seen) ? 0 : seen}&limit=10`);
        if (!r.log.length) return;
        const top = Math.max(...r.log.map((x) => x.id));
        if (!Number.isNaN(seen) && seen > 0) r.log.slice().reverse().forEach((x) => showToast(x.message.split("\n")[0]));
        set(top);
      } catch (e) { /* quiet */ }
    };
    setTimeout(async () => { if (Number.isNaN(get())) { try { const r = await api("/alerts/log?limit=1"); set(r.log.length ? r.log[0].id : 0); } catch (e) { set(0); } } setInterval(poll, 30000); }, 4000);
  })();

  // ================================================================== WKSP: multi-panel workspace
  const WIDGETS = {
    WATCH: { label: "Watchlist", render: async (el) => { const list = NX.getList(NX.WATCH_KEY); const names = list.length ? list : ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "TSLA", "BTC-USD"]; try { const r = await api(`/invest/quotes?symbols=${q(names.join(","))}`); NX.monitorTable(el, r.rows, { cols: ["sym", "last", "pct", "p1m", "spark"] }); } catch (e) { el.innerHTML = NX.err(e); } } },
    CHART: { label: "Price chart", render: async (el, s) => { s.sym = s.sym || "SPY"; el.innerHTML = `<div class="ctl"><input class="ctl-in" value="${esc(s.sym)}" style="max-width:110px"/>${chipsHtml(["1M", "3M", "6M", "1Y", "5Y"].map((r) => [r, r]), s.rng || "6M")}</div><div class="bb-pad"><div class="wk-c"></div></div>`;
      const go = async () => { const box = el.querySelector(".wk-c"); box.innerHTML = NX.load(); try { const d = await api(`/invest/chart?symbol=${q(s.sym)}&range=${s.rng || "6M"}`); NX.lineChart(box, d.candles.map((c) => [c[0], c[4]]), { height: Math.max(120, (el.clientHeight || 240) - 60) }); } catch (e) { box.innerHTML = NX.err(e); } };
      el.querySelector("input").addEventListener("keydown", (e) => { if (e.key === "Enter") { s.sym = e.target.value.trim().toUpperCase(); go(); } });
      wire(el.querySelector(".ctl"), (f) => { s.rng = f; go(); }); await go(); } },
    NEWS: { label: "News", render: async (el) => { try { const r = await api("/capital/news?limit=40"); el.innerHTML = NX.newsHtml(r.items, 40); } catch (e) { el.innerHTML = NX.err(e); } } },
    ECO: { label: "Economic calendar", render: (el, s) => R.eco(el, { impact: "high" }, {}) },
    EVTS: { label: "Earnings calendar", render: (el, s) => R.evts(el, {}, {}) },
    GC: { label: "Yield curve", render: (el, s) => R.curve(el, {}, { compact: true }) },
    WIRP: { label: "Fed path (WIRP)", render: (el, s) => R.wirp(el, {}, {}) },
    HEAT: { label: "Heatmap", render: (el, s) => R.heat(el, {}, { compact: true }) },
    COMP: { label: "Relative performance", render: (el, s) => R.comp(el, {}, { compact: true }) },
    CORR: { label: "Correlation", render: (el, s) => R.corr(el, {}, { compact: true }) },
    SEAS: { label: "Seasonality", render: (el, s) => R.seas(el, {}, { compact: true }) },
    COT: { label: "COT positioning", render: (el, s) => R.cot(el, {}, { compact: true }) },
    PORT: { label: "Portfolio risk", render: (el, s) => R.port(el, {}, { compact: true }) },
    OVDV: { label: "Vol surface", render: (el, s) => R.ovdv(el, {}, {}) },
    UOA: { label: "Unusual options", render: (el, s) => R.uoa(el, {}, {}) },
  };
  const L = (w) => ({ w });
  const H = (a, b, r = 0.5) => ({ t: "h", r, c: [a, b] }), V = (a, b, r = 0.5) => ({ t: "v", r, c: [a, b] });
  const TEMPLATES = { "1 panel": () => L("WATCH"), "2 columns": () => H(L("WATCH"), L("CHART")), "2 rows": () => V(L("CHART"), L("NEWS")), "2 × 2": () => V(H(L("WATCH"), L("CHART")), H(L("NEWS"), L("ECO"))), "1 | 2": () => H(L("CHART"), V(L("WATCH"), L("NEWS"))), "2 | 1": () => H(V(L("WATCH"), L("NEWS")), L("CHART")), "3 columns": () => H(L("WATCH"), H(L("CHART"), L("NEWS"), 0.5), 0.34), "1 + 3": () => V(L("CHART"), H(L("WATCH"), H(L("NEWS"), L("ECO"), 0.5), 0.34), 0.55) };
  const PRESET_LAYOUTS = {
    "Macro desk": V(H(L("GC"), L("WIRP"), 0.5), H(L("ECO"), L("COT"), 0.55), 0.42),
    "Equity desk": H(V(L("WATCH"), L("NEWS"), 0.5), V(L("CHART"), H(L("EVTS"), L("SEAS"), 0.55), 0.5), 0.34),
    "Risk desk": V(H(L("PORT"), L("CORR"), 0.55), H(L("COMP"), L("HEAT"), 0.5), 0.5),
    "Options desk": V(L("OVDV"), L("UOA"), 0.55),
  };
  const LKEY = "nx_layouts", CKEY = "nx_layout_current";
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };

  function workspacePage() {
    const el = $("#view-wksp");
    let tree = lsGet(CKEY, null) || clone(TEMPLATES["2 × 2"]());
    el.innerHTML = `<div class="bb-panel wk-shell" style="--i:0"><div class="bb-ph"><span class="bb-n">&#9656;</span>WORKSPACE<span class="sp"></span>
      <select id="wk-tpl" title="Layout template"><option value="">TEMPLATE…</option>${Object.keys(TEMPLATES).map((k) => `<option>${k}</option>`).join("")}</select>
      <select id="wk-pre" title="Preset workspaces"><option value="">PRESET…</option>${Object.keys(PRESET_LAYOUTS).map((k) => `<option>${k}</option>`).join("")}</select>
      <select id="wk-saved" title="Your saved layouts"><option value="">MY LAYOUTS…</option></select>
      <button class="bb-chip" id="wk-save">SAVE AS…</button><button class="bb-chip" id="wk-del">DELETE</button></div><div class="wk-root" id="wk-root"></div></div>`;
    const root = $("#wk-root", el);
    const persist = () => lsSet(CKEY, tree);
    const opts = (w) => Object.entries(WIDGETS).map(([k, v]) => `<option value="${k}" ${k === w ? "selected" : ""}>${v.label}</option>`).join("");
    const build = (node, parent) => {
      if (!node.t) {
        const leaf = document.createElement("div"); leaf.className = "wk-leaf";
        leaf.innerHTML = `<div class="wk-h"><select>${opts(node.w)}</select><span class="sp"></span><button class="wk-b" title="Split side by side">▥</button><button class="wk-b" title="Split top / bottom">▤</button><button class="wk-b" title="Close panel">✕</button></div><div class="wk-body"></div>`;
        const body = leaf.querySelector(".wk-body"); node.s = node.s || {};
        let timer = null, lastW = 0, lastH = 0;
        const paint = () => { lastW = body.clientWidth; lastH = body.clientHeight; body.innerHTML = ""; const b = document.createElement("div"); b.className = "wk-in"; body.appendChild(b); WIDGETS[node.w].render(b, node.s); };
        leaf.querySelector("select").addEventListener("change", (e) => { node.w = e.target.value; node.s = {}; persist(); paint(); });
        const [bh, bv, bx] = leaf.querySelectorAll(".wk-b");
        const splitInto = (t) => { const old = { w: node.w, s: node.s }; delete node.w; delete node.s; node.t = t; node.r = 0.5; node.c = [old, L("NEWS")]; persist(); mount(); };
        bh.addEventListener("click", () => splitInto("h")); bv.addEventListener("click", () => splitInto("v"));
        bx.addEventListener("click", () => { if (!parent) return showToast("A workspace needs at least one panel"); const i = parent.c.indexOf(node), other = parent.c[1 - i]; Object.keys(parent).forEach((k) => delete parent[k]); Object.assign(parent, other); persist(); mount(); });
        new ResizeObserver(() => { clearTimeout(timer); timer = setTimeout(() => { if (Math.abs(body.clientWidth - lastW) > 40 || Math.abs(body.clientHeight - lastH) > 40) paint(); }, 400); }).observe(body);
        setTimeout(paint, 0);
        return leaf;
      }
      const box = document.createElement("div"); box.className = `wk-split ${node.t}`;
      const a = build(node.c[0], node), b = build(node.c[1], node), gut = document.createElement("div"); gut.className = "wk-gut";
      a.style.flex = `${node.r} 1 0`; b.style.flex = `${1 - node.r} 1 0`;
      gut.addEventListener("mousedown", (e) => {
        e.preventDefault(); const rect = box.getBoundingClientRect();
        const move = (ev) => { const r = node.t === "h" ? (ev.clientX - rect.left) / rect.width : (ev.clientY - rect.top) / rect.height; node.r = Math.max(0.12, Math.min(0.88, r)); a.style.flex = `${node.r} 1 0`; b.style.flex = `${1 - node.r} 1 0`; };
        const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); persist(); };
        document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
      });
      box.append(a, gut, b);
      return box;
    };
    const mount = () => { root.innerHTML = ""; root.appendChild(build(tree, null)); };
    const saved = () => { const s = lsGet(LKEY, {}); $("#wk-saved", el).innerHTML = `<option value="">MY LAYOUTS…</option>${Object.keys(s).map((k) => `<option>${esc(k)}</option>`).join("")}`; };
    $("#wk-tpl", el).addEventListener("change", (e) => { if (!e.target.value) return; tree = clone(TEMPLATES[e.target.value]()); persist(); mount(); e.target.value = ""; });
    $("#wk-pre", el).addEventListener("change", (e) => { if (!e.target.value) return; tree = clone(PRESET_LAYOUTS[e.target.value]); persist(); mount(); e.target.value = ""; });
    $("#wk-saved", el).addEventListener("change", (e) => { const s = lsGet(LKEY, {}); if (s[e.target.value]) { tree = clone(s[e.target.value]); persist(); mount(); } });
    $("#wk-save", el).addEventListener("click", () => { const n = prompt("Name this workspace:"); if (!n) return; const s = lsGet(LKEY, {}); s[n] = clone(tree); lsSet(LKEY, s); saved(); showToast(`Saved “${n}”`); });
    $("#wk-del", el).addEventListener("click", () => { const n = $("#wk-saved", el).value; if (!n) return; const s = lsGet(LKEY, {}); delete s[n]; lsSet(LKEY, s); saved(); showToast(`Deleted “${n}”`); });
    saved(); mount();
  }

  // ================================================================== NOSTRO research: WFO / REGIME
  async function jobPage(el, { title, runPath, getPath, form, onRun, render, after }) {
    el.innerHTML = `<div class="bb-grid"><div class="bb-panel s3" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>${title}</div><div class="bb-pad">${form}<button class="btn" id="jb-run">RUN</button><div id="jb-msg" class="dim" style="font-size:11px;margin-top:8px"></div><div class="jb-bar hidden" id="jb-bar"><i></i></div></div></div><div class="s9" id="jb-out"></div></div>`;
    let timer = null;
    const show = (j) => {
      const msg = $("#jb-msg", el), bar = $("#jb-bar", el);
      if (j.state === "running") { bar.classList.remove("hidden"); bar.firstChild.style.width = `${Math.round((j.progress || 0) * 100)}%`; msg.textContent = j.message || "running…"; $("#jb-run", el).disabled = true; }
      else { bar.classList.add("hidden"); $("#jb-run", el).disabled = false; clearInterval(timer); if (j.state === "error") { msg.textContent = `Failed: ${j.error || j.message}`; } else if (j.state === "done") { msg.textContent = `Finished ${NX.ago(j.finished || j.started)} ago`; $("#jb-out", el).innerHTML = render(j.result); if (after) after(j.result, $("#jb-out", el)); } }
    };
    const poll = async () => { try { show(await api(getPath)); } catch (e) { /* retry */ } };
    $("#jb-run", el).addEventListener("click", async () => {
      try { await api(runPath, { method: "POST", body: JSON.stringify(onRun(el)) }); clearInterval(timer); timer = setInterval(() => { if (!document.body.contains(el)) return clearInterval(timer); poll(); }, 3000); poll(); } catch (e) { showToast(e.message); }
    });
    const j = await api(getPath); show(j);
    if (j.state === "running") timer = setInterval(poll, 3000);
    if (j.state === "idle") $("#jb-out", el).innerHTML = '<div class="bb-empty"><b>NOT RUN YET</b>Choose the settings and press RUN. It runs in the background on real Dukascopy history.</div>';
    return j;
  }

  async function wfoPage() {
    const el = $("#view-wfo"), strats = (await api("/strategies")).strategies.map((s) => s.name);
    el.innerHTML = '<div id="wfo-in"></div>';
    await jobPage($("#wfo-in"), {
      title: "WALK-FORWARD", runPath: "/nostro/walkforward/run", getPath: "/nostro/walkforward",
      form: `<div class="form-row"><label>Symbol</label><select id="wf-sym"><option>NQ</option><option>ES</option></select></div><div class="form-row"><label>Strategy</label><select id="wf-st">${strats.map((s) => `<option>${s}</option>`).join("")}</select></div><div class="form-row"><label>Train months</label><input id="wf-tr" type="number" value="9" style="width:70px"/></div><div class="form-row"><label>Test months</label><input id="wf-te" type="number" value="3" style="width:70px"/></div><div class="form-row"><label>Search iterations</label><input id="wf-it" type="number" value="20" style="width:70px"/></div><div class="dim" style="font-size:10.5px;margin-bottom:6px">Re-optimises on each train window, then judges only on the next unseen test window. More iterations = slower.</div>`,
      after: (r, out) => NX.lineChart($("#wf-c", out), r.oos_curve, { height: 260, fmt: (v) => "$" + num(v, 0) }),
      onRun: (e) => ({ symbol: $("#wf-sym", e).value, strategy: $("#wf-st", e).value, train_months: Number($("#wf-tr", e).value), test_months: Number($("#wf-te", e).value), iterations: Number($("#wf-it", e).value) }),
      render: (r) => { const s = r.summary, eff = s.median_efficiency; return `<div class="bb-kpis six">${kpi("STRATEGY", esc(r.strategy), `${r.symbol} · ${r.months_available} months of history`)}${kpi("WINDOWS", s.windows, `${r.train_months}m train / ${r.test_months}m test`)}${kpi("PROFITABLE WINDOWS", `${s.profitable_windows}/${s.windows}`, "out of sample", s.profitable_windows / (s.windows || 1) >= 0.6 ? "up" : "dn")}${kpi("OUT-OF-SAMPLE NET", "$" + num(s.oos_net, 0), `${s.oos_trades} trades`, dir(s.oos_net))}${kpi("MEDIAN EFFICIENCY", isNum(eff) ? num(eff * 100, 0) + "%" : "—", "OOS ÷ in-sample P&L per month", eff >= 0.5 ? "up" : "dn")}</div>
        <div class="bb-grid"><div class="bb-panel s7" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>STITCHED OUT-OF-SAMPLE EQUITY</div><div class="bb-pad"><div id="wf-c"></div></div></div>
        <div class="bb-panel s5" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>WINDOWS</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>TEST WINDOW</th><th class="r">IN-SAMPLE</th><th class="r">OUT OF SAMPLE</th><th class="r">TRADES</th><th class="r">EFF.</th></tr></thead><tbody>${r.windows.map((w) => `<tr><td class="dim">${w.test[0]} → ${w.test[1]}</td><td class="r dim">${isNum(w.is_net) ? signed(w.is_net, 0) : "—"}</td><td class="r ${dir(w.oos_net)}">${isNum(w.oos_net) ? signed(w.oos_net, 0) : "—"}</td><td class="r dim">${w.oos_trades ?? ""}</td><td class="r">${isNum(w.efficiency) ? num(w.efficiency * 100, 0) + "%" : "—"}</td></tr>`).join("")}</tbody></table></div></div></div><div class="dim" style="padding:6px 2px;font-size:10.5px">${esc(r.note)}</div>`; },
    });
  }

  async function regimePage() {
    const el = $("#view-regime");
    el.innerHTML = '<div id="rg-in"></div>';
    await jobPage($("#rg-in"), {
      title: "REGIME DETECTION", runPath: "/nostro/regimes/run", getPath: "/nostro/regimes",
      form: `<div class="form-row"><label>Symbol</label><select id="rg-sym"><option>NQ</option><option>ES</option></select></div><div class="form-row"><label>Months of history</label><input id="rg-m" type="number" value="24" style="width:70px"/></div><div class="dim" style="font-size:10.5px;margin-bottom:6px">Runs every strategy at its default settings and splits the results by market regime: trending vs choppy days (intraday efficiency ratio) and low / mid / high volatility (daily range terciles).</div>`,
      onRun: (e) => ({ symbol: $("#rg-sym", e).value, months: Number($("#rg-m", e).value) }),
      render: (r) => { const cols = ["trending", "choppy", "low vol", "mid vol", "high vol"], mx = Math.max(1, ...r.strategies.flatMap((s) => Object.values(s.cells).map((c) => Math.abs(c.net))));
        const best = (s) => cols.map((c) => [c, (s.cells[c] || {}).net]).filter((x) => isNum(x[1])).sort((a, b) => b[1] - a[1])[0];
        return `<div class="bb-kpis six">${cols.map((c) => kpi(c.toUpperCase(), r.day_counts[c] || 0, "days")).join("")}${kpi("SAMPLE", r.months + "m", `${r.symbol}, ${r.days} sessions`)}</div>
        <div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>NET P&amp;L BY STRATEGY AND REGIME<span class="sp"></span><span class="meta">default parameters · $50k account · green = made money</span></div><div class="bb-pb"><table class="bb-tbl rg"><thead><tr><th>STRATEGY</th>${cols.map((c) => `<th class="r">${c.toUpperCase()}</th>`).join("")}<th>BEST IN</th></tr></thead><tbody>${r.strategies.map((s) => { const b = best(s); return `<tr><td class="sym">${esc(s.strategy)}</td>${cols.map((c) => { const x = s.cells[c]; return x ? `<td class="r" style="background:${cell(x.net, mx)}" title="${x.trades} trades · win ${x.win_rate}% · PF ${x.pf ?? "—"}">${signed(x.net, 0)}<em>${x.trades}t</em></td>` : '<td class="r dim">—</td>'; }).join("")}<td class="up">${b ? b[0].toUpperCase() : ""}</td></tr>`; }).join("")}</tbody></table></div></div><div class="dim" style="padding:6px 2px;font-size:10.5px">${esc(r.note)} Cells are in-sample at default settings: use them to choose WHEN a strategy should be trusted, not as proof it will make money.</div>`; },
    });
  }

  // ================================================================== JRNL: trade journal
  async function journalPage() {
    const el = $("#view-jrnl");
    el.innerHTML = `<div class="bb-grid"><div class="bb-panel s7" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>LIVE TRADES<span class="sp"></span><span class="meta">click a trade to add notes, tags and screenshots</span></div><div class="bb-pb eco-b" id="jr-list">${NX.load()}</div></div>
      <div class="bb-panel s5" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>ENTRY<span class="sp"></span><span class="meta" id="jr-t"></span></div><div class="bb-pad" id="jr-ed"><div class="bb-empty">Select a trade.</div></div></div>
      <div class="bb-panel s12" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>MISTAKE ANALYSIS<span class="sp"></span><span class="meta">what each tag has cost or made you</span></div><div class="bb-pb" id="jr-an"></div></div></div>`;
    let data = null, cur = null;
    const load = async () => {
      data = await api("/journal?limit=120");
      $("#jr-list", el).innerHTML = `<table class="bb-tbl"><thead><tr><th>EXIT</th><th>ACCOUNT</th><th>STRATEGY</th><th>SIDE</th><th class="r">P&amp;L</th><th class="r">R</th><th>TAGS</th></tr></thead><tbody>${data.trades.map((t) => `<tr class="click ${cur === t.id ? "next" : ""}" data-id="${t.id}"><td class="dim">${t.exit_time ? new Date(t.exit_time).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</td><td class="dim">${esc(t.account_name)}</td><td>${esc(t.strategy || "")}</td><td class="${t.side === "long" || t.side === "buy" ? "up" : "dn"}">${esc(t.side || "")}</td><td class="r ${dir(t.pnl)}">${signed(t.pnl, 0)}</td><td class="r dim">${isNum(t.r) ? num(t.r, 2) : ""}</td><td>${t.tags.map((g) => `<span class="tg">${esc(g)}</span>`).join("")}${t.images.length ? " 🖼" : ""}${t.note ? " ✎" : ""}</td></tr>`).join("") || '<tr><td colspan="7" class="dim">No live trades yet.</td></tr>'}</tbody></table>`;
      $("#jr-list", el).querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => edit(Number(tr.dataset.id))));
      $("#jr-an", el).innerHTML = data.analysis.length ? `<table class="bb-tbl"><thead><tr><th>TAG</th><th class="r">TRADES</th><th class="r">NET P&amp;L</th><th class="r">WIN RATE</th><th class="r">AVG R</th><th class="r">AVG P&amp;L</th></tr></thead><tbody>${data.analysis.map((a) => `<tr><td><span class="tg">${esc(a.tag)}</span></td><td class="r">${a.trades}</td><td class="r ${dir(a.net)}"><b>${signed(a.net, 0)}</b></td><td class="r">${num(a.win_rate, 0)}%</td><td class="r">${isNum(a.avg_r) ? num(a.avg_r, 2) : "—"}</td><td class="r ${dir(a.avg_pnl)}">${signed(a.avg_pnl, 0)}</td></tr>`).join("")}</tbody></table>` : '<div class="bb-empty"><b>NO TAGGED TRADES YET</b>Tag trades (fomo, moved stop, oversized…) and this table shows which habits cost you money.</div>';
    };
    const edit = async (id) => {
      cur = id;
      const d = await api(`/journal/${id}`), t = d.trade;
      $("#jr-t", el).textContent = `#${id} · ${t.strategy || ""} · ${signed(t.pnl, 0)}`;
      const tags = new Set(d.tags), sugg = data.suggested_tags;
      const ed = $("#jr-ed", el);
      const draw = () => {
        ed.innerHTML = `<textarea id="jr-note" rows="6" placeholder="What was the setup? What did you do? What would you change?" style="width:100%;box-sizing:border-box">${esc(d.note)}</textarea>
          <div class="tagrow">${[...new Set([...sugg, ...tags])].map((g) => `<button class="bb-chip ${tags.has(g) ? "on" : ""}" data-g="${esc(g)}">${esc(g)}</button>`).join("")}</div>
          <div class="form-row"><label>Custom tag</label><input id="jr-ct" style="width:120px"/></div>
          <div class="jr-drop" id="jr-drop" tabindex="0">Click here and paste a screenshot (Ctrl+V), or drop an image file</div>
          <div class="jr-imgs">${d.images.map((n) => `<div class="jr-img"><img src="/api/journal-image/${n}" data-full="/api/journal-image/${n}"/><span class="bb-x" data-n="${n}">✕</span></div>`).join("")}</div>
          <button class="btn" id="jr-save">SAVE ENTRY</button>`;
        ed.querySelectorAll(".tagrow .bb-chip").forEach((c) => c.addEventListener("click", () => { d.note = $("#jr-note", ed).value; tags.has(c.dataset.g) ? tags.delete(c.dataset.g) : tags.add(c.dataset.g); draw(); }));
        $("#jr-ct", ed).addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.value.trim()) { d.note = $("#jr-note", ed).value; tags.add(e.target.value.trim().toLowerCase()); draw(); } });
        $("#jr-save", ed).addEventListener("click", async () => { try { await api(`/journal/${id}`, { method: "PUT", body: JSON.stringify({ note: $("#jr-note", ed).value, tags: [...tags] }) }); showToast("Journal entry saved"); await load(); } catch (e) { showToast(e.message); } });
        const addImg = async (file) => {
          if (!file || !file.type.startsWith("image/")) return;
          const url = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file); });
          try { const r = await api(`/journal/${id}/image`, { method: "POST", body: JSON.stringify({ data_url: url }) }); d.note = $("#jr-note", ed).value; d.images.push(r.name); draw(); load(); } catch (e) { showToast(e.message); }
        };
        const drop = $("#jr-drop", ed);
        drop.addEventListener("paste", (e) => { const it = [...(e.clipboardData || {}).items || []].find((x) => x.type.startsWith("image/")); if (it) { e.preventDefault(); addImg(it.getAsFile()); } });
        drop.addEventListener("dragover", (e) => e.preventDefault());
        drop.addEventListener("drop", (e) => { e.preventDefault(); addImg(e.dataTransfer.files[0]); });
        ed.querySelectorAll(".jr-img .bb-x").forEach((x) => x.addEventListener("click", async () => { await api(`/journal/${id}/image/${x.dataset.n}`, { method: "DELETE" }); d.images = d.images.filter((n) => n !== x.dataset.n); draw(); load(); }));
        ed.querySelectorAll(".jr-img img").forEach((im) => im.addEventListener("click", () => window.open(im.dataset.full, "_blank")));
      };
      draw();
      $("#jr-list", el).querySelectorAll("tr[data-id]").forEach((tr) => tr.classList.toggle("next", Number(tr.dataset.id) === id));
    };
    try { await load(); } catch (e) { $("#jr-list", el).innerHTML = NX.err(e); }
  }

  // ================================================================== EXEC: execution statistics
  async function execPage() {
    const el = $("#view-exec");
    let since = "all";
    el.innerHTML = `<div class="bb-panel" style="--i:0"><div class="bb-ph"><span class="bb-n">&#9656;</span>EXECUTION<span class="sp"></span>${chipsHtml([["7d", "7D"], ["30d", "30D"], ["90d", "90D"], ["all", "ALL"]], since)}</div><div id="ex-b" class="pn-body">${NX.load()}</div></div><div class="bb-grid"><div class="bb-panel s6" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>TRADING BLACKOUT<span class="sp"></span><span class="meta">pause new entries around CPI · FOMC · NFP</span></div><div id="ex-bk"></div></div><div class="bb-panel s6" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>UPCOMING BLACKOUT EVENTS</div><div id="ex-up" class="bb-pb"></div></div></div>`;
    const draw = async () => {
      const box = $("#ex-b", el); box.innerHTML = NX.load();
      try {
        const d = await api(`/nostro/exec?since=${since}`);
        if (!d.n) { box.innerHTML = '<div class="bb-empty"><b>NO LIVE TRADES IN THIS WINDOW</b></div>'; return; }
        const hrs = d.by_hour, brow = (rows, label) => `<table class="bb-tbl"><thead><tr><th>${label}</th><th class="r">TRADES</th><th class="r">NET</th><th class="r">WIN %</th><th class="r">AVG R</th></tr></thead><tbody>${rows.map((x) => `<tr><td class="sym">${esc(String(x.key))}</td><td class="r dim">${x.trades}</td><td class="r ${dir(x.net)}">${signed(x.net, 0)}</td><td class="r">${num(x.win_rate, 0)}%</td><td class="r dim">${isNum(x.avg_r) ? num(x.avg_r, 2) : "—"}</td></tr>`).join("")}</tbody></table>`;
        box.innerHTML = `<div class="bb-kpis six">${kpi("TRADES", d.n)}${kpi("NET P&L", "$" + num(d.net, 0), "after costs", dir(d.net))}${kpi("COMMISSIONS", "$" + num(d.commission, 0))}${kpi("MODELED SLIPPAGE", "$" + num(d.modeled_slippage, 0), "on stop exits")}${kpi("COSTS / GROSS", isNum(d.cost_pct_of_gross) ? num(d.cost_pct_of_gross, 1) + "%" : "—")}${kpi("AVG HOLD", isNum(d.avg_hold_min) ? num(d.avg_hold_min, 0) + " min" : "—")}</div>
          <div class="bb-grid" style="margin-top:10px"><div class="bb-panel s7" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>NET P&amp;L BY ENTRY HOUR (NEW YORK)</div><div class="bb-pad"><div id="ex-h"></div></div></div><div class="bb-panel s5" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>BY WEEKDAY</div><div class="bb-pad"><div id="ex-w"></div></div></div>
          <div class="bb-panel s4" style="--i:3"><div class="bb-ph"><span class="bb-n">3</span>BY EXIT REASON</div><div class="bb-pb">${brow(d.by_exit, "EXIT")}</div></div><div class="bb-panel s4" style="--i:4"><div class="bb-ph"><span class="bb-n">4</span>BY STRATEGY</div><div class="bb-pb">${brow(d.by_strategy, "STRATEGY")}</div></div><div class="bb-panel s4" style="--i:5"><div class="bb-ph"><span class="bb-n">5</span>BY SIDE &amp; SYMBOL</div><div class="bb-pb">${brow([...d.by_side, ...d.by_symbol], "GROUP")}</div></div></div><div class="dim" style="padding:6px 2px;font-size:10.5px">${esc(d.note)}</div>`;
        NX.barChart($("#ex-h", box), hrs.map((h) => `${h.key}:00`), [{ name: "net", color: "#2be07e", values: hrs.map((h) => h.net) }], { height: 210, fmt: (v) => big(v) });
        NX.barChart($("#ex-w", box), d.by_weekday.map((h) => h.key), [{ name: "net", color: "#5aa9ff", values: d.by_weekday.map((h) => h.net) }], { height: 210, fmt: (v) => big(v) });
      } catch (e) { box.innerHTML = NX.err(e); }
    };
    wire(el.querySelector(".bb-ph"), (f) => { since = f; draw(); });
    NX.blackoutPanel($("#ex-bk", el));
    api("/nostro/blackout").then((b) => { $("#ex-up", el).innerHTML = `<table class="bb-tbl"><tbody>${b.upcoming.map((e) => `<tr><td class="dim">${new Date(e.ts * 1000).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</td><td class="sym">${esc(e.country)}</td><td>${esc(e.title)}</td></tr>`).join("") || '<tr><td class="dim">No matching event in the next 4 days.</td></tr>'}</tbody></table>`; }).catch(() => {});
    await draw();
  }

  // ================================================================== HELP + function-key bar
  const HELP_ROWS = [
    ["NAVIGATE", [["CAP", "CAPITAL home"], ["MKT · STK · CRYPTO · ETF · FX", "market monitors"], ["ECST", "macro dashboard (FRED)"], ["SEC / any ticker", "security deep dive: AAPL US, VOD LN, TEVA IT, BTC, EURUSD Curncy"], ["SEC AAPL · GP TSLA · FA MSFT · N BTC", "command first, ticker (or name) after — jumps straight to that tab"], ["AI <question>", "ask in plain English: “AI take me to the stock screener”, “AI show apple's chart”, “AI find me a leveraged crypto ETF” (searches real securities, never guesses tickers), “AI mark high and lows on the graph”, “AI mark a Wyckoff setup for a long trade” (scans the open chart, 15M/1H/4H/1D only), or any question — needs an OpenAI key on the server"], ["WKSP", "multi-panel workspace"]]],
    ["CALENDARS & RATES", [["ECO", "economic calendar + trading blackout"], ["EVTS", "earnings calendar"], ["GC", "US yield curve"], ["WIRP", "Fed rate path from futures"]]],
    ["ANALYTICS", [["EQS", "equity screener"], ["IMAP", "market heatmap"], ["COMP", "relative performance"], ["CORR", "correlation matrix"], ["SEAS", "seasonality"], ["COT", "futures positioning"], ["PORT", "portfolio risk"]]],
    ["OPTIONS", [["OVDV", "vol surface, skew, IV rank"], ["OVME", "pricer, Greeks, payoff builder"], ["UOA", "unusual options activity"]]],
    ["NOSTRO", [["LIVE · STRAT · TRAIN · FIELD", "the algo terminal"], ["WFO", "walk-forward"], ["REGIME", "regime performance"], ["JRNL", "trade journal"], ["EXEC", "execution stats"], ["ALRT", "alerts to Telegram"]]],
    ["WORLD", [["WORLD", "3D globe, click a country for its dashboard"], ["RADIO", "scan live radio stations worldwide"], ["PLANES", "live aircraft + military"], ["CAM", "public traffic cameras"], ["QUAKE", "earthquakes + natural events"]]],
    ["MORE MENUS", [["ACADEMY", "replay trainer + Wyckoff drills on BTC / ETH / SOL"], ["TV", "live news channels + geo-tagged headlines"], ["ONCHAIN", "fear and greed, funding, liquidations, Bitcoin network"], ["PMOMO", "Polymarket momo — paper accounts, trainer, live radar, session chart"]]],
    ["INSIDE A SECURITY", [["DES GP FA N ANR HDS", "type after the ticker (NVDA GP) or before it (GP NVDA) — either order works"], ["FIL DVD OMON RV DATA", "filings, dividends, options, peers, all fields"], ["LP", "launchpad (multi-panel)"]]],
  ];
  const KEYS = [["F1", "HELP", "HELP", "#ffa028"], ["F2", "CAP", "CAP", "#2be07e"], ["F3", "MKT", "MKT", "#5aa9ff"], ["F4", "EQS", "EQS", "#5aa9ff"], ["F6", "ECO", "ECO", "#ff6b9a"], ["F7", "GC", "GC", "#ff6b9a"], ["F8", "WKSP", "WKSP", "#c58bff"], ["F9", "PORT", "PORT", "#c58bff"], ["F10", "ALRT", "ALRT", "#e8d44d"]];
  function helpOverlay() {
    if ($("#nx-help")) return $("#nx-help").remove();
    const d = document.createElement("div"); d.id = "nx-help"; d.className = "nx-help";
    d.innerHTML = `<div class="nx-help-box"><div class="bb-ph"><span class="bb-n">?</span>NOSTRO TERMINAL · HELP<span class="sp"></span><span class="meta">type a code in the command line, then Enter (&lt;GO&gt;) · press Esc to close</span></div><div class="nx-help-g">${HELP_ROWS.map(([h, rows]) => `<div><h4>${h}</h4>${rows.map(([c, t]) => `<div class="hr"><b>${c}</b><span>${t}</span></div>`).join("")}</div>`).join("")}</div>
      <div class="dim" style="padding:8px 12px;font-size:10.5px">Shortcuts: <b>/</b> or <b>Ctrl+K</b> focus the command line · <b>F1</b> this help · <b>F2–F4, F6–F10</b> jump to the coloured function keys along the bottom (F5 stays the browser refresh) · quotes are delayed about 15 minutes (free feeds).</div></div>`;
    d.addEventListener("click", (e) => { if (e.target === d) d.remove(); });
    document.body.appendChild(d);
  }
  window.showHelp = helpOverlay;
  function keyBar() {
    const sb = document.querySelector(".statusbar"); if (!sb || $("#nx-keys")) return;
    const bar = document.createElement("div"); bar.id = "nx-keys"; bar.className = "nx-keys";
    bar.innerHTML = KEYS.map(([k, l, c, col]) => `<button data-c="${c}" style="--k:${col}"><i>${k}</i>${l}</button>`).join("");
    sb.parentNode.insertBefore(bar, sb);
    bar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => runCode(b.dataset.c)));
    document.addEventListener("keydown", (e) => {
      const tag = (document.activeElement || {}).tagName;
      if (e.key === "Escape" && $("#nx-help")) { $("#nx-help").remove(); return; }
      if ((e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) && !["INPUT", "TEXTAREA", "SELECT"].includes(tag) && !document.activeElement?.isContentEditable) { e.preventDefault(); const c = $("#bb-cmd"); if (c) c.focus(); return; }
      if (e.key === "?" && !["INPUT", "TEXTAREA", "SELECT"].includes(tag) && !document.activeElement?.isContentEditable) { helpOverlay(); return; }
      const m = /^F(\d{1,2})$/.exec(e.key);
      if (m) { const k = KEYS.find((x) => x[0] === `F${m[1]}`); if (k) { e.preventDefault(); runCode(k[2]); } }
    });
  }
  const runCode = (c) => { if (c === "HELP") return helpOverlay(); const inp = $("#bb-cmd"); if (window.runFunction) window.runFunction(c); else if (inp) { inp.value = c; inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); } };

  // ================================================================== header: blackout chip
  function blackoutChip() {
    const host = document.querySelector(".bb-status"); if (!host || $("#bb-blk")) return;
    const chip = document.createElement("div"); chip.className = "bb-chip-s keep"; chip.id = "bb-blk"; chip.style.cursor = "pointer"; chip.title = "Trading blackout around high-impact releases";
    host.appendChild(chip);
    chip.addEventListener("click", () => NX.openSub2("cal", "eco"));
    const upd = async () => {
      try {
        const b = await api("/nostro/blackout"), s = b.settings;
        if (!s.enabled) { chip.innerHTML = '<span class="bb-clk"><em>BLACKOUT</em><span class="dim">OFF</span></span>'; return; }
        if (b.active) { chip.innerHTML = `<span class="bb-clk"><em style="color:#ff4a4a">BLACKOUT</em><span style="color:#ff4a4a">${esc(b.active.title.slice(0, 16))}</span></span>`; return; }
        const n = b.next, m = n ? Math.round((n.ts - b.now) / 60) : null;
        chip.innerHTML = `<span class="bb-clk"><em>NEXT HI-IMPACT</em><span>${n ? (m >= 60 ? Math.floor(m / 60) + "h" + String(m % 60).padStart(2, "0") : m + "m") : "—"}</span></span>`;
      } catch (e) { /* quiet */ }
    };
    upd(); setInterval(upd, 60000);
  }

  // ================================================================== registration
  const builders = {
    opts: () => tabbed("opts", "OPTIONS LAB", [
      { id: "ovdv", label: "OVDV · VOL SURFACE", render: (b) => { b.innerHTML = '<div class="pn-body" id="op-el"></div>'; R.ovdv($("#op-el", b)); } },
      { id: "ovme", label: "OVME · PRICER & PAYOFF", render: (b) => { b.innerHTML = '<div class="pn-body" id="op-el"></div>'; R.ovme($("#op-el", b)); } },
      { id: "uoa", label: "UOA · UNUSUAL OPTIONS", render: (b) => { b.innerHTML = '<div class="pn-body" id="op-el"></div>'; R.uoa($("#op-el", b)); } },
    ], "ovdv"),
    port: () => { $("#view-port").innerHTML = '<div class="bb-panel" style="--i:0"><div class="bb-ph"><span class="bb-n">&#9656;</span>PORT · PORTFOLIO RISK<span class="sp"></span><span class="meta">your CAPITAL holdings</span></div><div class="pn-body" id="port-el"></div></div>'; R.port($("#port-el")); },
    alrt: () => { $("#view-alrt").innerHTML = '<div id="alrt-el"></div>'; alertsPage($("#alrt-el")); },
    wksp: workspacePage, wfo: wfoPage, regime: regimePage, jrnl: journalPage, exec: execPage,
  };
  const built = {};
  const prev = window.onExtraTab;
  window.onToolsTab = (tab) => { if (builders[tab] && !built[tab]) { built[tab] = true; Promise.resolve(builders[tab]()).catch((e) => { const v = $(`#view-${tab}`); if (v) v.innerHTML = NX.err(e); }); } };
  window.addEventListener("DOMContentLoaded", () => { keyBar(); blackoutChip(); });
  if (document.readyState !== "loading") { keyBar(); blackoutChip(); }
})();
