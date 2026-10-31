/* MARKET ANALYTICS pages: ECO/EVTS calendars, GC/WIRP rates, COMP/CORR/SEAS/COT analytics, IMAP heatmap, EQS screener.
   Every renderer is R.name(el, params, opts) so the WORKSPACE can host it as a widget (workspace.js). Depends on NX (invest.js). */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, big, signed } = NX;
  const R = (window.NXR = {});
  const isNum = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const q = encodeURIComponent;

  // ------------------------------------------------------------------ shared UI helpers
  const chipsHtml = (items, on, cls = "") => `<div class="bb-chips ${cls}">${items.map(([v, l]) => `<button class="bb-chip ${v === on ? "on" : ""}" data-f="${v}">${l}</button>`).join("")}</div>`;
  function wire(root, cb) {
    root.querySelectorAll(".bb-chip[data-f]").forEach((c) => c.addEventListener("click", () => {
      c.parentElement.querySelectorAll(".bb-chip").forEach((x) => x.classList.remove("on"));
      c.classList.add("on");
      cb(c.dataset.f, c);
    }));
  }
  NX.chipsHtml = chipsHtml; NX.wire = wire;
  const compactH = (el, fallback, reserve = 0) => Math.max(140, (el.clientHeight || fallback + reserve) - reserve);

  NX.multiLine = (box, labels, series, o = {}) => {
    if (NX.defer(box, () => NX.multiLine(box, labels, series, o))) return;
    const h = o.height || 300;
    const { cv, ctx, w } = NX.fitCanvas(box, h);
    const pad = { l: 8, r: 58, t: 24, b: 20 };
    const all = series.flatMap((s) => s.values.filter(isNum));
    if (!all.length) { box.innerHTML = '<div class="bb-empty"><b>NO DATA</b></div>'; return; }
    let lo = Math.min(...all), hi = Math.max(...all);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pv = (hi - lo) * 0.06; lo -= pv; hi += pv;
    const n = labels.length;
    const x = (i) => pad.l + (n > 1 ? (i / (n - 1)) * (w - pad.l - pad.r) : 0);
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
    const fmt = o.fmt || ((v) => num(v, 2));
    const draw = (hover) => {
      ctx.clearRect(0, 0, w, h);
      ctx.font = "10px 'JetBrains Mono', Consolas, monospace";
      ctx.strokeStyle = "#171717"; ctx.fillStyle = "#8b8b8b"; ctx.lineWidth = 1;
      for (let g = 0; g <= 4; g++) { const v = lo + ((hi - lo) * g) / 4, yy = y(v); ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke(); ctx.textAlign = "left"; ctx.fillText(fmt(v), w - pad.r + 6, yy + 3); }
      const ticks = Math.min(o.ticks || 6, n - 1);
      for (let g = 0; g <= ticks; g++) { const i = Math.round((g / ticks) * (n - 1)); ctx.textAlign = g === 0 ? "left" : g === ticks ? "right" : "center"; ctx.fillText(String(labels[i]).slice(0, o.labelLen || 10), x(i), h - 5); }
      if (o.baseline !== undefined && lo < o.baseline && hi > o.baseline) { ctx.strokeStyle = "#3a3a3a"; ctx.beginPath(); ctx.moveTo(pad.l, y(o.baseline)); ctx.lineTo(w - pad.r, y(o.baseline)); ctx.stroke(); }
      series.forEach((s) => {
        ctx.beginPath(); let started = false;
        s.values.forEach((v, i) => { if (!isNum(v)) return; started ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); started = true; });
        ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.6; ctx.setLineDash(s.dash || []); ctx.stroke(); ctx.setLineDash([]);
        if (o.dots) s.values.forEach((v, i) => { if (isNum(v)) { ctx.fillStyle = s.color; ctx.beginPath(); ctx.arc(x(i), y(v), 2.6, 0, 6.3); ctx.fill(); } });
        const lv = [...s.values].reverse().find(isNum);
        if (isNum(lv)) { ctx.fillStyle = s.color; ctx.textAlign = "left"; ctx.fillText(fmt(lv), w - pad.r + 6, y(lv) + 3); }
      });
      let lx = pad.l;
      series.forEach((s) => { ctx.fillStyle = s.color; ctx.fillRect(lx, 8, 9, 3); ctx.fillStyle = "#cfcfcf"; ctx.textAlign = "left"; ctx.fillText(s.name, lx + 13, 12); lx += ctx.measureText(s.name).width + 30; });
      if (hover != null) {
        const xx = x(hover);
        ctx.strokeStyle = "#ffa028"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, h - pad.b); ctx.stroke(); ctx.setLineDash([]);
        const lines = [String(labels[hover]), ...series.filter((s) => isNum(s.values[hover])).map((s) => `${s.name}  ${fmt(s.values[hover])}`)];
        const tw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16, th = lines.length * 13 + 8;
        const tx = xx + 12 + tw > w - pad.r ? xx - tw - 12 : xx + 12;
        ctx.fillStyle = "#000"; ctx.strokeStyle = "#ffa028"; ctx.fillRect(tx, pad.t + 4, tw, th); ctx.strokeRect(tx + 0.5, pad.t + 4.5, tw - 1, th - 1);
        ctx.textAlign = "left"; lines.forEach((l, i) => { ctx.fillStyle = i ? (series[i - 1] ? "#f1f1f1" : "#f1f1f1") : "#ffa028"; ctx.fillText(l, tx + 8, pad.t + 17 + i * 13); });
      }
    };
    draw();
    cv.addEventListener("mousemove", (e) => { const r = cv.getBoundingClientRect(); draw(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * (n - 1))))); });
    cv.addEventListener("mouseleave", () => draw());
  };

  // tabbed page shell
  const SUB = (NX.SUB = {});
  function tabbed(viewId, title, tabs, first) {
    const el = $(`#view-${viewId}`);
    el.innerHTML = `<div class="bb-panel tabbed" style="--i:0"><div class="bb-ph"><span class="bb-n">&#9656;</span>${title}<span class="sp"></span>${chipsHtml(tabs.map((t) => [t.id, t.label]), first)}</div><div class="tabbed-body" id="${viewId}-body"></div></div>`;
    const body = $(`#${viewId}-body`);
    const select = (id) => {
      el.querySelectorAll(".tabbed > .bb-ph .bb-chip").forEach((c) => c.classList.toggle("on", c.dataset.f === id));
      body.innerHTML = ""; body.className = "tabbed-body";
      const t = tabs.find((x) => x.id === id);
      body._cleanup && body._cleanup();
      t.render(body);
    };
    wire(el.querySelector(".tabbed > .bb-ph"), select);
    SUB[viewId] = select;
    select(first);
  }
  NX.openSub = (tab, sub) => {
    const btn = document.querySelector(`.tab[data-tab="${tab}"]`);
    if (btn) btn.click();
    if (sub && SUB[tab]) SUB[tab](sub);
  };

  const myNames = async () => {
    const w = NX.getList(NX.WATCH_KEY);
    try { const p = await api("/capital/portfolio"); return [...new Set([...w, ...p.holdings.map((h) => (h.kind === "crypto" ? `${h.symbol}-USD` : h.symbol))])]; } catch (e) { return w; }
  };
  NX.myNames = myNames;

  // ------------------------------------------------------------------ ECO: economic calendar
  const IMPACT = { High: "#ff4a4a", Medium: "#ffa028", Low: "#e8d44d", Holiday: "#6b6b6b" };
  R.eco = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { impact: p.impact || "med", ccy: p.ccy || "USD" });
    el.innerHTML = `<div class="ctl">${chipsHtml([["high", "HIGH"], ["med", "MEDIUM+"], ["all", "ALL"]], st.impact)}<span class="sp"></span>${chipsHtml(["ALL", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY"].map((c) => [c, c]), st.ccy, "ccy")}</div><div class="bb-pb eco-b" id="eco-b">${NX.load()}</div>`;
    const draw = (data) => {
      const now = data.now;
      let ev = data.events.filter((e) => (st.impact === "high" ? e.impact === "High" : st.impact === "med" ? ["High", "Medium"].includes(e.impact) : true) && (st.ccy === "ALL" || e.country === st.ccy));
      const next = ev.find((e) => e.ts > now && e.impact === "High");
      const days = {};
      ev.forEach((e) => { const d = new Date(e.ts * 1000).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" }); (days[d] = days[d] || []).push(e); });
      let nextMarked = false;
      $("#eco-b", el).innerHTML = `<table class="bb-tbl eco"><thead><tr><th>TIME</th><th>CCY</th><th></th><th>EVENT</th><th class="r">FORECAST</th><th class="r">PREVIOUS</th><th class="r">ACTUAL</th></tr></thead><tbody>${Object.entries(days).map(([d, rows]) => `<tr class="sec"><td colspan="7">${d.toUpperCase()}</td></tr>${rows.map((e) => {
        const past = e.ts < now, mark = !nextMarked && !past ? ((nextMarked = true), " next") : "";
        return `<tr class="${past ? "past" : ""}${mark}"><td class="dim">${new Date(e.ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</td><td class="sym">${esc(e.country)}</td><td><i class="imp" style="background:${IMPACT[e.impact] || "#555"}" title="${esc(e.impact)}"></i></td><td>${esc(e.title)}</td><td class="r">${esc(e.forecast || "")}</td><td class="r dim">${esc(e.previous || "")}</td><td class="r ${e.actual ? "act" : ""}" ${e.actual_src ? `title="from ${e.actual_src}"` : ""}>${esc(e.actual || "")}</td></tr>`;
      }).join("")}`).join("") || `<tr><td colspan="7" class="dim">no events in this filter</td></tr>`}</tbody></table>`;
      el._next = next;
      if (o.onNext) o.onNext(next, now);
    };
    const load = async () => { try { el._data = await api("/market/calendar?back=2&ahead=10"); draw(el._data); } catch (e) { $("#eco-b", el).innerHTML = NX.err(e); } };
    wire(el.querySelector(".ctl"), (f, c) => { if (c.parentElement.classList.contains("ccy")) st.ccy = f; else st.impact = f; el._data && draw(el._data); });
    await load();
  };

  async function blackoutPanel(el) {
    const draw = (b) => {
      const s = b.settings, a = b.active, n = b.next;
      const cd = n ? Math.max(0, Math.round((n.ts - b.now) / 60)) : null;
      el.innerHTML = `<div class="bb-pad">
        <div class="blk ${a ? "on" : s.enabled ? "armed" : "off"}"><b>${a ? "BLACKOUT ACTIVE" : s.enabled ? "ARMED" : "DISABLED"}</b>
          <span>${a ? `New entries are paused: ${esc(a.title)}` : n ? `Next: ${esc(n.title)} in ${cd >= 60 ? Math.floor(cd / 60) + "h " + (cd % 60) + "m" : cd + "m"}` : "No matching release in the next 4 days"}</span></div>
        <div class="form-row"><label>Pause new entries automatically</label><input id="bk-on" type="checkbox" ${s.enabled ? "checked" : ""} style="width:auto"/></div>
        <div class="form-row"><label>Minutes before the release</label><input id="bk-b" type="number" value="${s.before}" min="0" max="240"/></div>
        <div class="form-row"><label>Minutes after the release</label><input id="bk-a" type="number" value="${s.after}" min="0" max="480"/></div>
        <div class="form-row"><label>Event keywords (USD, high impact)</label><input id="bk-k" value="${esc(s.keywords.join(","))}" style="width:190px"/></div>
        <button class="btn" id="bk-save">SAVE BLACKOUT SETTINGS</button>
        <div class="dim" style="font-size:10.5px;margin-top:6px">Only NEW entries are blocked. Open positions keep being managed (stops, targets, end-of-day flatten).</div></div>`;
      $("#bk-save", el).addEventListener("click", async () => {
        const body = { enabled: $("#bk-on", el).checked, before: Number($("#bk-b", el).value), after: Number($("#bk-a", el).value), keywords: $("#bk-k", el).value };
        try { draw(await api("/nostro/blackout", { method: "PUT", body: JSON.stringify(body) })); showToast("Blackout settings saved"); } catch (e) { showToast(e.message); }
      });
    };
    try { draw(await api("/nostro/blackout")); } catch (e) { el.innerHTML = NX.err(e); }
  }
  NX.blackoutPanel = blackoutPanel;

  // ------------------------------------------------------------------ EVTS: earnings calendar
  const monday = (d) => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
  const iso = (d) => d.toISOString().slice(0, 10);
  R.evts = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { start: monday(new Date()), mcap: p.mcap || 3e9, mine: false });
    el.innerHTML = `<div class="ctl"><button class="bb-chip" id="ev-prev">◀ WEEK</button><b class="ev-range" id="ev-range"></b><button class="bb-chip" id="ev-next">WEEK ▶</button><span class="sp"></span>
      ${chipsHtml([["3e9", ">$3B"], ["1e10", ">$10B"], ["5e10", ">$50B"], ["0", "ALL"]], String(st.mcap === 3e9 ? "3e9" : st.mcap === 1e10 ? "1e10" : st.mcap === 5e10 ? "5e10" : "0"))}<button class="bb-chip ${st.mine ? "on" : ""}" id="ev-mine">MY NAMES</button></div><div class="bb-pb eco-b" id="ev-b">${NX.load()}</div>`;
    const load = async () => {
      const a = st.start;
      $("#ev-range", el).textContent = `${a.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} – ${new Date(a.getTime() + 4 * 86400000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}`;
      $("#ev-b", el).innerHTML = NX.load();
      try {
        const [d, mine] = await Promise.all([api(`/market/earnings?start=${iso(a)}&days=5&min_mcap=${st.mine ? 0 : st.mcap}`), st.mine ? myNames() : Promise.resolve(null)]);
        const today = iso(new Date());
        $("#ev-b", el).innerHTML = `<table class="bb-tbl eco"><thead><tr><th>COMPANY</th><th>WHEN</th><th>QTR</th><th class="r">EPS EST</th><th class="r"># EST</th><th class="r">LAST YR</th><th class="r">ACTUAL</th><th class="r">SURPRISE</th><th class="r">MKT CAP</th></tr></thead><tbody>${d.days.map((day) => {
          const rows = (mine ? day.rows.filter((r) => mine.includes(r.symbol)) : day.rows);
          return `<tr class="sec ${day.date === today ? "today" : ""}"><td colspan="9">${new Date(day.date + "T12:00").toLocaleDateString("en-GB", { weekday: "long", day: "2-digit", month: "short" }).toUpperCase()} <span class="dim">· ${day.total} companies reporting, ${rows.length} shown</span></td></tr>${rows.map((r) => `<tr class="click" data-sec="${esc(r.symbol)}" data-fn="ANR"><td><span class="sym">${esc(r.symbol)}</span> <span class="dim">${esc((r.name || "").slice(0, 32))}</span></td><td class="dim">${r.when}</td><td class="dim">${esc(r.fq || "")}</td><td class="r">${px(r.eps_est)}</td><td class="r dim">${r.n_est || ""}</td><td class="r dim">${px(r.eps_last_year)}</td><td class="r ${r.eps !== null && r.eps_est !== null ? dir(r.eps - r.eps_est) : ""}">${r.eps !== null ? px(r.eps) : ""}</td><td class="r ${dir(r.surprise)}">${r.surprise !== null ? pct(r.surprise) : ""}</td><td class="r dim">${big(r.mcap, "$")}</td></tr>`).join("")}`;
        }).join("")}</tbody></table>`;
        $("#ev-b", el).querySelectorAll("tr[data-fn]").forEach((tr) => tr.addEventListener("click", (e) => { e.stopPropagation(); openSecurity(tr.dataset.sec, tr.dataset.fn); }));
      } catch (e) { $("#ev-b", el).innerHTML = NX.err(e); }
    };
    $("#ev-prev", el).addEventListener("click", () => { st.start = new Date(st.start.getTime() - 7 * 86400000); load(); });
    $("#ev-next", el).addEventListener("click", () => { st.start = new Date(st.start.getTime() + 7 * 86400000); load(); });
    $("#ev-mine", el).addEventListener("click", (e) => { st.mine = !st.mine; e.currentTarget.classList.toggle("on", st.mine); load(); });
    wire(el.querySelector(".ctl"), (f, c) => { if (c.dataset.f !== undefined && c.id === "") { st.mcap = Number(f); load(); } });
    await load();
  };

  // ------------------------------------------------------------------ GC: yield curve, WIRP: implied Fed path
  R.curve = async (el, p = {}, o = {}) => {
    el.innerHTML = NX.load();
    try {
      const d = await api("/market/curve");
      const sp = d.spreads, cur = d.curves.now, nowV = (i) => cur[i];
      const kpi = (l, v, sub, c = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${sub}</div></div>`;
      const inv = sp["2s10s"].now < 0;
      const H = o.compact ? compactH(el, 260, 8) : 330;
      el.innerHTML = `${o.compact ? "" : `<div class="bb-kpis six">${kpi("2Y", num(nowV(4)) + "%", `${signed(nowV(4) - d.curves["1m"][4])} vs 1M ago`)}${kpi("10Y", num(nowV(8)) + "%", `${signed(nowV(8) - d.curves["1m"][8])} vs 1M ago`)}${kpi("30Y", num(nowV(10)) + "%", `${signed(nowV(10) - d.curves["1m"][10])} vs 1M ago`)}${kpi("2s10s SPREAD", signed(sp["2s10s"].now) + "%", inv ? "INVERTED" : "positive slope", inv ? "dn" : "up")}${kpi("3M–10Y SPREAD", signed(sp["3m10y"].now) + "%", sp["3m10y"].now < 0 ? "INVERTED" : "positive slope", sp["3m10y"].now < 0 ? "dn" : "up")}${kpi("CURVE DATE", d.marks.now, "US Treasury constant maturity (FRED)")}</div>`}
        <div class="bb-pad"><div id="gc-c"></div></div>${o.compact ? "" : `<div class="bb-grid"><div class="bb-panel s7 h330" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>2s10s SPREAD · LAST 2 YEARS</div><div class="bb-pad"><div id="gc-s"></div></div></div>
        <div class="bb-panel s5 h330" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>CURVE BY TENOR</div><div class="bb-pb"><table class="bb-tbl"><thead><tr><th>TENOR</th><th class="r">NOW</th><th class="r">1W</th><th class="r">1M</th><th class="r">1Y</th><th class="r">Δ 1M</th></tr></thead><tbody>${d.tenors.map((t, i) => `<tr><td class="sym">${t}</td><td class="r">${num(cur[i], 2)}</td><td class="r dim">${num(d.curves["1w"][i], 2)}</td><td class="r dim">${num(d.curves["1m"][i], 2)}</td><td class="r dim">${num(d.curves["1y"][i], 2)}</td><td class="r ${dir(cur[i] - d.curves["1m"][i])}">${signed((cur[i] - d.curves["1m"][i]) * 100, 0)}bp</td></tr>`).join("")}</tbody></table></div></div></div>`}`;
      NX.multiLine($("#gc-c", el), d.tenors, [
        { name: `NOW ${d.marks.now}`, color: "#ffa028", values: d.curves.now, width: 2.6 }, { name: "1 WEEK AGO", color: "#5aa9ff", values: d.curves["1w"], dash: [4, 3] },
        { name: "1 MONTH AGO", color: "#2be07e", values: d.curves["1m"], dash: [4, 3] }, { name: "1 YEAR AGO", color: "#c58bff", values: d.curves["1y"], dash: [2, 3] }], { height: H, dots: true, fmt: (v) => num(v, 2) + "%", ticks: 10 });
      if (!o.compact) NX.lineChart($("#gc-s", el), sp["2s10s"].series.slice(-104).map((r) => [r[0], r[1]]), { height: 250, fmt: (v) => num(v, 2) + "%", color: "#5aa9ff" });
    } catch (e) { el.innerHTML = NX.err(e); }
  };

  R.wirp = async (el, p = {}, o = {}) => {
    el.innerHTML = NX.load();
    try {
      const d = await api("/market/wirp");
      el.innerHTML = `<div class="bb-pad"><div class="wirp-h">EFFECTIVE FED FUNDS RATE <b>${num(d.effective, 2)}%</b> · implied path from the futures curve</div></div>
        <div class="bb-pb"><table class="bb-tbl"><thead><tr><th>FOMC MEETING</th><th class="r">IMPLIED RATE AFTER</th><th class="r">CHANGE</th><th style="width:46%">PROBABILITY (25 bp STEPS)</th><th class="r">EXPECTED MOVES</th></tr></thead><tbody>${d.meetings.map((m) => `<tr><td class="sym">${m.date}</td><td class="r">${num(m.post_rate, 3)}%</td><td class="r ${dir(m.delta_bp)}">${signed(m.delta_bp, 1)} bp</td>
          <td><div class="pbar"><i class="ph" style="width:${m.p_hike * 100}%" title="hike ${(m.p_hike * 100).toFixed(0)}%"></i><i class="pn" style="width:${m.p_hold * 100}%" title="hold ${(m.p_hold * 100).toFixed(0)}%"></i><i class="pc" style="width:${m.p_cut * 100}%" title="cut ${(m.p_cut * 100).toFixed(0)}%"></i></div><span class="pl"><b class="dn">HIKE ${(m.p_hike * 100).toFixed(0)}%</b> · HOLD ${(m.p_hold * 100).toFixed(0)}% · <b class="up">CUT ${(m.p_cut * 100).toFixed(0)}%</b></span></td>
          <td class="r ${dir(m.exp_moves)}">${signed(m.exp_moves, 2)}</td></tr>`).join("") || `<tr><td colspan="5" class="dim">no futures data</td></tr>`}</tbody></table></div>
        <div class="bb-pad dim" style="font-size:10.5px">${esc(d.note)}</div>`;
    } catch (e) { el.innerHTML = NX.err(e); }
  };

  // ------------------------------------------------------------------ COMP / CORR / SEAS / COT
  const PALETTE = ["#ffa028", "#5aa9ff", "#2be07e", "#c58bff", "#ff6b9a", "#f2e34d", "#3fd1c9", "#ff7a3d"];
  R.comp = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { syms: p.symbols || "SPY,QQQ,AAPL,NVDA,BTC-USD", range: p.range || "1Y" });
    el.innerHTML = `<div class="ctl"><input class="ctl-in" id="cp-s" value="${esc(st.syms)}" placeholder="Symbols, comma separated"/><button class="bb-chip on" id="cp-go">COMPARE</button><span class="sp"></span>${chipsHtml(["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"].map((r) => [r, r]), st.range)}</div><div class="bb-pad"><div id="cp-c"></div></div><div class="bb-pb" id="cp-t"></div>`;
    const run = async () => {
      st.syms = $("#cp-s", el).value.trim();
      $("#cp-c", el).innerHTML = NX.load();
      try {
        const d = await api(`/market/compare?symbols=${q(st.syms)}&range=${st.range}`);
        const keys = Object.keys(d.series);
        NX.multiLine($("#cp-c", el), d.dates, keys.map((k, i) => ({ name: k, color: PALETTE[i % 8], values: d.series[k].series, width: i ? 1.5 : 2 })), { height: o.compact ? compactH(el, 260, 60) : 360, fmt: (v) => num(v, 1), baseline: 100 });
        if (!o.compact) $("#cp-t", el).innerHTML = `<table class="bb-tbl"><thead><tr><th>SECURITY</th><th class="r">TOTAL RETURN</th><th class="r">ANN. VOLATILITY</th><th class="r">MAX DRAWDOWN</th></tr></thead><tbody>${keys.map((k, i) => `<tr class="click" data-sec="${esc(k)}"><td><i class="dot" style="background:${PALETTE[i % 8]}"></i><span class="sym">${esc(k)}</span></td><td class="r ${dir(d.series[k].total)}">${pct(d.series[k].total)}</td><td class="r">${d.series[k].vol ? num(d.series[k].vol, 1) + "%" : "—"}</td><td class="r dn">${pct(d.series[k].maxdd, false)}</td></tr>`).join("")}</tbody></table>`;
      } catch (e) { $("#cp-c", el).innerHTML = NX.err(e); }
    };
    wire(el.querySelector(".ctl"), (f, c) => { if (c.id !== "cp-go") { st.range = f; run(); } });
    $("#cp-go", el).addEventListener("click", run);
    $("#cp-s", el).addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    await run();
  };

  const corrColor = (v) => (v === null ? "#111" : v >= 0 ? `rgba(43,224,126,${0.12 + v * 0.7})` : `rgba(255,74,74,${0.12 + -v * 0.7})`);
  R.corr = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { syms: p.symbols || null, range: p.range || "1Y" });
    if (!st.syms) { const mine = await myNames(); st.syms = [...new Set([...mine.slice(0, 6), "SPY", "QQQ", "TLT", "GLD", "BTC-USD", "DX-Y.NYB"])].join(","); }
    el.innerHTML = `<div class="ctl"><input class="ctl-in" id="co-s" value="${esc(st.syms)}"/><button class="bb-chip on" id="co-go">CALCULATE</button><span class="sp"></span>${chipsHtml(["3M", "6M", "1Y", "3Y"].map((r) => [r, r]), st.range)}</div><div class="bb-pad" id="co-c">${NX.load()}</div>`;
    const run = async () => {
      st.syms = $("#co-s", el).value.trim();
      $("#co-c", el).innerHTML = NX.load();
      try {
        const d = await api(`/market/correlation?symbols=${q(st.syms)}&range=${st.range}`);
        const n = d.symbols.length;
        $("#co-c", el).innerHTML = `<div class="corr" style="grid-template-columns:70px repeat(${n},minmax(46px,1fr))"><div></div>${d.symbols.map((s) => `<div class="ch" title="${esc(s)}">${esc(s.replace("-USD", "").slice(0, 7))}</div>`).join("")}${d.matrix.map((row, i) => `<div class="ch rh">${esc(d.symbols[i].replace("-USD", "").slice(0, 8))}</div>${row.map((v, j) => `<div class="cc ${i === j ? "diag" : ""}" style="background:${corrColor(v)}" title="${esc(d.symbols[i])} vs ${esc(d.symbols[j])}: ${v === null ? "n/a" : v.toFixed(3)}">${v === null ? "" : v.toFixed(2)}</div>`).join("")}`).join("")}</div>
          ${o.compact ? "" : `<div class="dim" style="margin-top:10px;font-size:11px">${d.observations} daily observations · most correlated: ${d.highest.map((h) => `<b>${esc(h[1])}/${esc(h[2])} ${h[0].toFixed(2)}</b>`).join(", ")} · best diversifiers: ${d.lowest.map((h) => `<b>${esc(h[1])}/${esc(h[2])} ${h[0].toFixed(2)}</b>`).join(", ")}</div>`}`;
      } catch (e) { $("#co-c", el).innerHTML = NX.err(e); }
    };
    wire(el.querySelector(".ctl"), (f, c) => { if (c.id !== "co-go") { st.range = f; run(); } });
    $("#co-go", el).addEventListener("click", run);
    $("#co-s", el).addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    await run();
  };

  R.seas = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { sym: p.symbol || "SPY" });
    el.innerHTML = `<div class="ctl"><input class="ctl-in" id="se-s" value="${esc(st.sym)}" style="max-width:180px"/><button class="bb-chip on" id="se-go">GO</button><span class="sp"></span><span class="dim" id="se-meta"></span></div><div class="bb-pad"><div id="se-c"></div></div><div class="bb-pad"><div id="se-w"></div></div><div class="bb-pb" id="se-t"></div>`;
    const run = async () => {
      st.sym = $("#se-s", el).value.trim().toUpperCase();
      $("#se-c", el).innerHTML = NX.load();
      try {
        const d = await api(`/market/seasonality?symbol=${q(st.sym)}`);
        $("#se-meta", el).textContent = `${d.years} years of monthly history`;
        const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        NX.barChart($("#se-c", el), M, [{ name: "Average monthly return %", color: "#ffa028", values: d.months.map((m) => m.avg) }], { height: o.compact ? 150 : 200, fmt: (v) => num(v, 1) + "%" });
        NX.barChart($("#se-w", el), d.weekdays.map((w) => w.d), [{ name: "Average daily return % (5y)", color: "#5aa9ff", values: d.weekdays.map((w) => w.avg) }], { height: o.compact ? 110 : 160, fmt: (v) => num(v, 3) + "%" });
        if (!o.compact) $("#se-t", el).innerHTML = `<table class="bb-tbl"><thead><tr><th>MONTH</th><th class="r">AVG</th><th class="r">MEDIAN</th><th class="r">HIT RATE</th><th class="r">BEST</th><th class="r">WORST</th><th class="r">YEARS</th></tr></thead><tbody>${d.months.map((m, i) => `<tr><td class="sym">${M[i]}</td><td class="r ${dir(m.avg)}">${pct(m.avg)}</td><td class="r dim">${pct(m.median)}</td><td class="r">${m.hit === null ? "—" : num(m.hit, 0) + "%"}</td><td class="r up">${pct(m.best)}</td><td class="r dn">${pct(m.worst, false)}</td><td class="r dim">${m.n}</td></tr>`).join("")}</tbody></table>`;
      } catch (e) { $("#se-c", el).innerHTML = NX.err(e); }
    };
    $("#se-go", el).addEventListener("click", run);
    $("#se-s", el).addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    await run();
  };

  R.cot = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { m: p.market || "NQ" });
    el.innerHTML = `<div class="ctl">${chipsHtml([["NQ", "NASDAQ-100"], ["ES", "S&P 500"], ["GOLD", "GOLD"], ["OIL", "WTI CRUDE"], ["SILVER", "SILVER"], ["10Y", "10Y NOTE"]], st.m)}</div><div class="bb-pad"><div id="ct-c"></div></div><div class="bb-pb" id="ct-t"></div>`;
    const run = async () => {
      $("#ct-c", el).innerHTML = NX.load();
      try {
        const d = await api(`/market/cot?market=${st.m}`);
        const spec = d.kind === "tff" ? "Leveraged funds" : "Managed money", W = d.weeks;
        const g = (w, k) => (w.groups[k] ? w.groups[k].net : null);
        NX.multiLine($("#ct-c", el), W.map((w) => w.date), [{ name: `${spec} (speculators) net`, color: "#ffa028", values: W.map((w) => g(w, spec)), width: 2 }, { name: d.kind === "tff" ? "Asset managers net" : "Producers net", color: "#5aa9ff", values: W.map((w) => g(w, d.kind === "tff" ? "Asset managers" : "Producers/merchants")) },
          { name: d.kind === "tff" ? "Dealers net" : "Swap dealers net", color: "#2be07e", values: W.map((w) => g(w, d.kind === "tff" ? "Dealers" : "Swap dealers")) }], { height: o.compact ? compactH(el, 260, 200) : 300, fmt: (v) => big(v), baseline: 0 });
        const last = W[W.length - 1], prev = W[W.length - 2] || last, sv = W.map((w) => g(w, spec)).filter(isNum);
        const rank = sv.length ? (sv.filter((v) => v <= g(last, spec)).length / sv.length) * 100 : null;
        $("#ct-t", el).innerHTML = `<div class="dim" style="padding:6px 10px;font-size:11px">${esc(d.label)} · report of ${last.date} · open interest ${big(last.oi)} · ${spec} net position is at the <b>${rank === null ? "—" : num(rank, 0) + "th"}</b> percentile of the last ${sv.length} weeks</div><table class="bb-tbl"><thead><tr><th>TRADER GROUP</th><th class="r">LONG</th><th class="r">SHORT</th><th class="r">NET</th><th class="r">Δ WEEK</th><th class="r">% OF OI (NET)</th></tr></thead><tbody>${Object.entries(last.groups).map(([k, v]) => `<tr><td class="sym">${esc(k)}</td><td class="r">${big(v.long)}</td><td class="r">${big(v.short)}</td><td class="r ${dir(v.net)}">${signed(v.net, 0)}</td><td class="r ${dir(v.net - ((prev.groups[k] || {}).net || 0))}">${signed(v.net - ((prev.groups[k] || {}).net || 0), 0)}</td><td class="r dim">${last.oi ? num((v.net / last.oi) * 100, 1) + "%" : ""}</td></tr>`).join("")}</tbody></table><div class="dim" style="padding:6px 10px;font-size:10.5px">${esc(d.source)}. Published Fridays for the prior Tuesday.</div>`;
      } catch (e) { $("#ct-c", el).innerHTML = NX.err(e); }
    };
    wire(el.querySelector(".ctl"), (f) => { st.m = f; run(); });
    await run();
  };

  // ------------------------------------------------------------------ IMAP: heatmap (squarified treemap)
  function squarify(items, X, Y, W, H) {
    const nodes = items.map((i) => ({ ...i, a: i.v }));
    const total = nodes.reduce((s, n) => s + n.a, 0), k = (W * H) / total;
    nodes.forEach((n) => (n.a *= k));
    const out = [];
    let x = X, y = Y, w = W, h = H, row = [];
    const lay = (r) => {
      const sum = r.reduce((s, n) => s + n.a, 0);
      if (w >= h) { const cw = sum / h; let yy = y; r.forEach((n) => { const nh = n.a / cw; out.push({ ...n, x, y: yy, w: cw, h: nh }); yy += nh; }); x += cw; w -= cw; }
      else { const rh = sum / w; let xx = x; r.forEach((n) => { const nw = n.a / rh; out.push({ ...n, x: xx, y, w: nw, h: rh }); xx += nw; }); y += rh; h -= rh; }
    };
    const worst = (r, side) => { const s = r.reduce((a, n) => a + n.a, 0), mx = Math.max(...r.map((n) => n.a)), mn = Math.min(...r.map((n) => n.a)); return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn)); };
    for (const n of nodes) {
      const side = Math.min(w, h);
      if (!row.length || worst([...row, n], side) <= worst(row, side)) row.push(n);
      else { lay(row); row = [n]; }
    }
    if (row.length) lay(row);
    return out;
  }
  const heatColor = (p) => { const t = Math.max(-1, Math.min(1, p / 3)); return t >= 0 ? `rgb(${Math.round(38 - 12 * t)},${Math.round(38 + 122 * t)},${Math.round(44 + 30 * t)})` : `rgb(${Math.round(38 - 38 * t)},${Math.round(38 + 4 * t)},${Math.round(44 + 8 * t)})`; };
  R.heat = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { idx: p.index || "sp500" });
    el.innerHTML = `<div class="ctl">${chipsHtml([["sp500", "S&P 500 (large caps)"], ["ndx", "NASDAQ-100"]], st.idx)}<span class="sp"></span><span class="dim" id="hm-meta"></span></div><div class="hm" id="hm"></div>`;
    const run = async () => {
      const box = $("#hm", el);
      box.innerHTML = NX.load();
      try {
        const d = await api(`/market/heatmap?index=${st.idx}`);
        if (!d.items.length) { box.innerHTML = `<div class="bb-empty"><b>SNAPSHOT BUILDING</b>The market snapshot is still loading (first run takes a couple of minutes).</div>`; return; }
        $("#hm-meta", el).textContent = `${d.coverage} stocks · sized by market cap · colour = change today`;
        if (NX.defer(box, run)) return;
        const W = box.clientWidth || 900, H = o.compact ? Math.max(200, (el.clientHeight || 420) - 44) : 600;
        box.style.height = `${H}px`;
        const sectors = {};
        d.items.forEach((i) => (sectors[i.sector] = sectors[i.sector] || []).push(i));
        const sec = squarify(Object.entries(sectors).map(([name, items]) => ({ name, items, v: items.reduce((s, i) => s + i.mcap, 0) })).sort((a, b) => b.v - a.v), 0, 0, W, H);
        const tiles = [];
        sec.forEach((s) => {
          const hh = s.h > 40 && s.w > 70 ? 15 : 0;
          tiles.push(`<div class="hm-sec" style="left:${s.x}px;top:${s.y}px;width:${s.w}px;height:${s.h}px"><span>${hh ? esc(s.name.toUpperCase()) : ""}</span></div>`);
          squarify(s.items.map((i) => ({ ...i, v: i.mcap })).sort((a, b) => b.v - a.v), s.x + 1, s.y + hh + 1, s.w - 2, s.h - hh - 2).forEach((t) => {
            const fs = Math.max(8, Math.min(20, Math.sqrt(t.w * t.h) / 4.6));
            tiles.push(`<div class="hm-t" data-sec="${esc(t.symbol)}" style="left:${t.x}px;top:${t.y}px;width:${t.w - 1}px;height:${t.h - 1}px;background:${heatColor(t.pct)}" title="${esc(t.name)}  ${pct(t.pct)}  ${big(t.mcap, "$")}">${t.w > 34 && t.h > 22 ? `<b style="font-size:${fs}px">${esc(t.symbol)}</b>${t.h > 34 ? `<em style="font-size:${Math.max(8, fs * 0.72)}px">${pct(t.pct)}</em>` : ""}` : ""}</div>`);
          });
        });
        box.innerHTML = tiles.join("");
      } catch (e) { box.innerHTML = NX.err(e); }
    };
    wire(el.querySelector(".ctl"), (f) => { st.idx = f; run(); });
    await run();
  };

  // ------------------------------------------------------------------ EQS: equity screener
  const PRESETS = {
    "Quality at a fair price": { filters: [{ field: "roe", op: ">", value: 15 }, { field: "pe", op: "between", value: 5, value2: 25 }, { field: "net_margin", op: ">", value: 12 }], sort: "mcap" },
    "Oversold large caps": { filters: [{ field: "rsi14", op: "<", value: 35 }, { field: "mcap", op: ">", value: 5e10 }], sort: "rsi14", desc: false },
    "Momentum leaders": { filters: [{ field: "p3m", op: ">", value: 15 }, { field: "vs_sma50", op: ">", value: 0 }, { field: "from_high", op: ">", value: -8 }], sort: "p3m" },
    "High dividend": { filters: [{ field: "div_yield", op: ">", value: 3 }, { field: "debt_equity", op: "<", value: 200 }], sort: "div_yield" },
    "Growth": { filters: [{ field: "rev_growth", op: ">", value: 15 }, { field: "earn_growth", op: ">", value: 10 }], sort: "rev_growth" },
    "Analyst upside": { filters: [{ field: "target_upside", op: ">", value: 15 }, { field: "n_analysts", op: ">", value: 10 }], sort: "target_upside" },
    "Volume breakouts": { filters: [{ field: "rel_volume", op: ">", value: 1.8 }, { field: "pct", op: ">", value: 2 }], sort: "rel_volume" },
  };
  const EQS_COLS = [["symbol", "SYMBOL"], ["sector", "SECTOR"], ["price", "PRICE"], ["mcap", "MKT CAP"], ["pct", "TODAY"], ["p1m", "1M"], ["ytd", "YTD"], ["pe", "P/E"], ["fwd_pe", "FWD P/E"], ["div_yield", "YIELD"], ["roe", "ROE"], ["rev_growth", "REV GR"],
    ["rsi14", "RSI"], ["from_high", "VS 52W HI"], ["target_upside", "TGT UPSIDE"], ["rel_volume", "REL VOL"]];
  const fmtCell = (k, v) => (v === null || v === undefined ? "—" : k === "mcap" ? big(v, "$") : ["pct", "p1m", "ytd", "from_high", "target_upside", "rev_growth"].includes(k) ? pct(v, true, 1) : k === "div_yield" || k === "roe" ? num(v, 1) + "%" : k === "rel_volume" ? num(v, 1) + "x" : typeof v === "number" ? num(v, k === "price" ? 2 : 1) : esc(v));
  R.eqs = async (el, p = {}, o = {}) => {
    const st = (el._st = el._st || { filters: [{ field: "mcap", op: ">", value: 1e11 }], sort: "mcap", desc: true, universe: "all", fields: null });
    if (!st.fields) st.fields = (await api("/market/screener/fields")).fields;
    el.innerHTML = `<div class="bb-grid"><div class="bb-panel s4" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>CRITERIA<span class="sp"></span><span class="meta" id="eq-status"></span></div><div class="bb-pad">
        <div class="form-row"><label>Preset</label><select id="eq-pre"><option value="">— choose —</option>${Object.keys(PRESETS).map((k) => `<option>${esc(k)}</option>`).join("")}</select></div>
        <div class="form-row"><label>Saved screens</label><select id="eq-saved"><option value="">— none —</option></select></div>
        <div class="form-row" id="eq-uni"><label>Universe</label>${chipsHtml([["all", "ALL"], ["ndx", "NASDAQ-100"]], st.universe)}</div>
        <div id="eq-rows"></div><button class="bb-chip on" id="eq-add" style="margin:4px 0">+ ADD FILTER</button>
        <div class="form-row"><label>Sort by</label><select id="eq-sort">${st.fields.filter((f) => !f.text).map((f) => `<option value="${f.key}" ${f.key === st.sort ? "selected" : ""}>${esc(f.label)}</option>`).join("")}</select></div>
        <button class="btn" id="eq-run">RUN SCREEN</button>
        <div style="display:flex;gap:4px;margin-top:6px"><button class="btn btn-small" id="eq-save" style="flex:1;margin:0">SAVE</button><button class="btn btn-small" id="eq-del" style="flex:1;margin:0">DELETE</button><button class="btn btn-small" id="eq-csv" style="flex:1;margin:0">EXPORT CSV</button><button class="btn btn-small" id="eq-ref" style="flex:1;margin:0" title="Rebuild the market snapshot now">REFRESH DATA</button></div></div></div>
      <div class="bb-panel s8" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>RESULTS<span class="sp"></span><span class="meta" id="eq-meta"></span></div><div class="bb-pb eq-res" id="eq-out">${NX.load()}</div></div></div>`;
    const OPS = [[">", ">"], [">=", "≥"], ["<", "<"], ["<=", "≤"], ["=", "="], ["between", "between"]];
    const rowsEl = $("#eq-rows", el);
    const drawRows = () => {
      rowsEl.innerHTML = st.filters.map((f, i) => {
        const text = (st.fields.find((x) => x.key === f.field) || {}).text;
        return `<div class="eq-row" data-i="${i}"><select class="f-field">${st.fields.map((x) => `<option value="${x.key}" ${x.key === f.field ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select>
          <select class="f-op" ${text ? "disabled" : ""}>${(text ? [["=", "contains"]] : OPS).map(([v, l]) => `<option value="${v}" ${v === f.op ? "selected" : ""}>${l}</option>`).join("")}</select>
          <input class="f-v" value="${f.value ?? ""}" ${text ? "" : 'type="number" step="any"'}/>${f.op === "between" && !text ? `<input class="f-v2" type="number" step="any" value="${f.value2 ?? ""}"/>` : ""}<span class="bb-x">✕</span></div>`;
      }).join("") || '<div class="dim" style="padding:4px 0">No filters: shows the whole universe.</div>';
      rowsEl.querySelectorAll(".eq-row").forEach((r) => {
        const i = Number(r.dataset.i);
        r.querySelector(".f-field").addEventListener("change", (e) => { st.filters[i].field = e.target.value; const t = (st.fields.find((x) => x.key === e.target.value) || {}).text; st.filters[i].op = t ? "=" : ">"; st.filters[i].value = ""; drawRows(); });
        r.querySelector(".f-op").addEventListener("change", (e) => { st.filters[i].op = e.target.value; drawRows(); });
        r.querySelector(".f-v").addEventListener("input", (e) => (st.filters[i].value = e.target.value));
        const v2 = r.querySelector(".f-v2"); if (v2) v2.addEventListener("input", (e) => (st.filters[i].value2 = e.target.value));
        r.querySelector(".bb-x").addEventListener("click", () => { st.filters.splice(i, 1); drawRows(); });
      });
    };
    const body = () => ({ filters: st.filters.filter((f) => f.value !== "" && f.value !== null), sort: st.sort, desc: st.desc, limit: 200, universe: st.universe });
    const run = async () => {
      $("#eq-out", el).innerHTML = NX.load("SCREENING");
      try {
        const r = await api("/market/screener/run", { method: "POST", body: JSON.stringify(body()) });
        $("#eq-meta", el).textContent = `${r.matched} of ${r.universe} securities match`;
        $("#eq-out", el).innerHTML = r.rows.length ? `<table class="bb-tbl"><thead><tr>${EQS_COLS.map(([k, l]) => `<th class="${k === "symbol" || k === "sector" ? "" : "r"} sortable ${st.sort === k ? "sorted" : ""}" data-k="${k}">${l}${st.sort === k ? (st.desc ? " ▼" : " ▲") : ""}</th>`).join("")}</tr></thead><tbody>${r.rows.map((x) => `<tr class="click" data-sec="${esc(x.symbol)}">${EQS_COLS.map(([k]) => (k === "symbol" ? `<td><span class="sym">${esc(x.symbol)}</span> <span class="dim">${esc((x.name || "").slice(0, 22))}</span></td>` : k === "sector" ? `<td class="dim">${esc(x.sector || "")}</td>` : `<td class="r ${["pct", "p1m", "ytd", "from_high", "target_upside"].includes(k) ? dir(x[k]) : ""}">${fmtCell(k, x[k])}</td>`)).join("")}</tr>`).join("")}</tbody></table>` : `<div class="bb-empty"><b>NO MATCHES</b>Loosen a filter.</div>`;
        $("#eq-out", el).querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => { st.desc = st.sort === th.dataset.k ? !st.desc : true; st.sort = th.dataset.k; if (!st.fields.find((f) => f.key === st.sort)) st.sort = "mcap"; run(); }));
      } catch (e) { $("#eq-out", el).innerHTML = NX.err(e); }
    };
    const status = async () => {
      try {
        const s = await api("/market/screener/status");
        $("#eq-status", el).textContent = s.running ? `updating ${s.progress}/${s.total}…` : `${s.count} stocks · ${s.updated ? NX.ago(s.updated) + " old" : "building…"}`;
        return s;
      } catch (e) { return null; }
    };
    const loadSaved = async () => {
      const r = await api("/market/screener/saved");
      el._saved = r.screens;
      $("#eq-saved", el).innerHTML = `<option value="">— ${r.screens.length ? "load" : "none"} —</option>${r.screens.map((s) => `<option>${esc(s.name)}</option>`).join("")}`;
    };
    $("#eq-pre", el).addEventListener("change", (e) => { const pr = PRESETS[e.target.value]; if (!pr) return; st.filters = JSON.parse(JSON.stringify(pr.filters)); st.sort = pr.sort; st.desc = pr.desc !== false; $("#eq-sort", el).value = st.sort; drawRows(); run(); });
    $("#eq-saved", el).addEventListener("change", (e) => { const s = (el._saved || []).find((x) => x.name === e.target.value); if (!s) return; st.filters = s.req.filters; st.sort = s.req.sort || "mcap"; st.desc = s.req.desc !== false; st.universe = s.req.universe || "all"; drawRows(); run(); });
    $("#eq-sort", el).addEventListener("change", (e) => { st.sort = e.target.value; });
    $("#eq-add", el).addEventListener("click", () => { st.filters.push({ field: "pe", op: "<", value: "" }); drawRows(); });
    $("#eq-run", el).addEventListener("click", run);
    $("#eq-save", el).addEventListener("click", async () => { const name = prompt("Name this screen:"); if (!name) return; try { await api(`/market/screener/saved/${q(name)}`, { method: "POST", body: JSON.stringify(body()) }); await loadSaved(); showToast(`Saved “${name}”`); } catch (e) { showToast(e.message); } });
    $("#eq-del", el).addEventListener("click", async () => { const n = $("#eq-saved", el).value; if (!n) return; await api(`/market/screener/saved/${q(n)}`, { method: "DELETE" }); await loadSaved(); showToast(`Deleted “${n}”`); });
    $("#eq-csv", el).addEventListener("click", async () => {
      const r = await fetch("/api/market/screener/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body()) });
      const blob = await r.blob(), a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "nostro_screen.csv"; a.click(); URL.revokeObjectURL(a.href);
    });
    $("#eq-ref", el).addEventListener("click", async () => { await api("/market/screener/refresh", { method: "POST" }); showToast("Refreshing the market snapshot in the background"); });
    wire($("#eq-uni", el), (f) => { st.universe = f; run(); });
    drawRows(); await loadSaved();
    const s = await status();
    if (s && s.count === 0) $("#eq-out", el).innerHTML = `<div class="bb-empty bb-load">BUILDING THE MARKET SNAPSHOT (first run takes about two minutes)</div>`;
    else await run();
    const t = setInterval(async () => { if (!document.body.contains(el)) return clearInterval(t); const s2 = await status(); if (s2 && el._waiting && s2.count > 0 && !s2.running) { el._waiting = false; run(); } }, 5000);
    el._waiting = s && s.count === 0;
  };

  // ------------------------------------------------------------------ pages
  const builders = {
    cal: () => tabbed("cal", "CALENDARS", [
      { id: "eco", label: "ECO · ECONOMIC", render: (b) => { b.innerHTML = `<div class="bb-grid"><div class="bb-panel s8 tall2" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>ECONOMIC CALENDAR<span class="sp"></span><span class="meta" id="eco-next"></span></div><div class="pn-body" id="eco-el"></div></div><div class="bb-panel s4" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>TRADING BLACKOUT<span class="sp"></span><span class="meta">CPI · FOMC · NFP</span></div><div id="eco-bk">${NX.load()}</div></div></div>`;
        R.eco($("#eco-el", b), {}, { onNext: (n, now) => { const m = n ? Math.round((n.ts - now) / 60) : null; $("#eco-next", b).textContent = n ? `NEXT HIGH IMPACT: ${n.country} ${n.title} in ${m >= 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m + "m"}` : ""; } }); blackoutPanel($("#eco-bk", b)); } },
      { id: "evts", label: "EVTS · EARNINGS", render: (b) => { b.innerHTML = `<div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>EARNINGS CALENDAR<span class="sp"></span><span class="meta">estimates before, actual EPS and surprise after · click a name for its analyst history</span></div><div class="pn-body" id="ev-el"></div></div>`; R.evts($("#ev-el", b)); } },
    ], "eco"),
    rates: () => tabbed("rates", "RATES", [
      { id: "gc", label: "GC · YIELD CURVE", render: (b) => { b.innerHTML = `<div class="pn-body" id="gc-el"></div>`; R.curve($("#gc-el", b)); } },
      { id: "wirp", label: "WIRP · FED PATH", render: (b) => { b.innerHTML = `<div class="pn-body" id="wp-el"></div>`; R.wirp($("#wp-el", b)); } },
    ], "gc"),
    ana: () => tabbed("ana", "ANALYTICS", [
      { id: "comp", label: "COMP · RELATIVE", render: (b) => { b.innerHTML = `<div class="pn-body" id="an-el"></div>`; R.comp($("#an-el", b)); } },
      { id: "corr", label: "CORR · CORRELATION", render: (b) => { b.innerHTML = `<div class="pn-body" id="an-el"></div>`; R.corr($("#an-el", b)); } },
      { id: "seas", label: "SEAS · SEASONALITY", render: (b) => { b.innerHTML = `<div class="pn-body" id="an-el"></div>`; R.seas($("#an-el", b)); } },
      { id: "cot", label: "COT · POSITIONING", render: (b) => { b.innerHTML = `<div class="pn-body" id="an-el"></div>`; R.cot($("#an-el", b)); } },
    ], "comp"),
    heat: () => { $("#view-heat").innerHTML = `<div class="bb-panel" style="--i:0"><div class="bb-ph"><span class="bb-n">1</span>IMAP · MARKET HEATMAP<span class="sp"></span><span class="meta">click a tile to open the security</span></div><div class="pn-body" id="heat-el"></div></div>`; R.heat($("#heat-el")); },
    eqs: () => { $("#view-eqs").innerHTML = `<div id="eqs-el"></div>`; R.eqs($("#eqs-el")); },
  };
  const built = {};
  window.onExtraTab = (tab) => { if (builders[tab] && !built[tab]) { built[tab] = true; builders[tab](); } if (window.onToolsTab) window.onToolsTab(tab); };
})();
