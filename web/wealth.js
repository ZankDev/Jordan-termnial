/* WEALTH — all of the user's money in one place (top-level tab).
   NET WORTH · RISK · PERFORMANCE · GOALS · BUDGET · DEALS · SECURITY. Terminal styling (bb-panel / bb-kpi / acd-*), USD or ILS.
   Depends on dashboard.js (api(), showToast()), ui_kit.js (UIX tooltips, sortable tables, count-up). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const W = { tab: "nw", cur: localStorage.getItem("wealth_cur") || "ILS", fx: 3.7, built: false, cache: {}, timers: [] };
  const TABS = [["nw", "NET WORTH"], ["cash", "CASH FLOW"], ["shop", "SHOPIFY"], ["risk", "RISK"], ["perf", "PERFORMANCE"], ["goals", "GOALS"], ["budget", "BUDGET"], ["deals", "DEALS"], ["sec", "SECURITY"]];
  const CAT_COL = { cash: "#8fd0ff", bank: "#5aa9ff", bonds: "#7fe0c8", pension: "#c792ff", stocks: "#2be07e", crypto: "#ffa028", real_estate: "#e8d44d", business: "#ff8a3d", trading: "#ff4a4a", other: "#9a9a9a", debt: "#ff4a4a" };
  const m = (usd, d = 0) => { if (!isN(usd)) return "—"; const v = W.cur === "ILS" ? usd * W.fx : usd; return `${v < 0 ? "-" : ""}${W.cur === "ILS" ? "₪" : "$"}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })}`; };
  const sm = (usd, d = 0) => (!isN(usd) ? "—" : (usd > 0 ? "+" : "") + m(usd, d));
  const ils = (v, d = 0) => (!isN(v) ? "—" : `${v < 0 ? "-" : ""}₪${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: d })}`);
  const pct = (n, d = 1) => (!isN(n) ? "—" : `${n > 0 ? "+" : ""}${Number(n).toFixed(d)}%`);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const kpi = (l, v, s = "", c = "", tip = "") => `<div class="bb-kpi wl-k" ${tip ? `data-uix-tip="${esc(l)}|${esc(tip)}"` : ""}><span class="l">${l}</span><span class="v uix-count ${c}">${v}</span>${s ? `<span class="s">${s}</span>` : ""}</div>`;
  const P = (n, t, body, meta = "", extra = "") => `<div class="bb-panel ${extra}"><div class="bb-ph"><span class="bb-n">${n}</span>${t}<span class="sp"></span><span class="meta">${meta}</span></div><div class="bb-pad">${body}</div></div>`;
  async function call(path, body, method) {
    const r = await fetch(`/api/wealth${path}`, body === undefined && !method ? {} : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (r.status === 423) { lockScreen(); throw new Error("locked"); }
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    return j;
  }

  // ------------------------------------------------------------------ charts
  function donut(parts, size = 170) {
    const tot = parts.reduce((s, p) => s + Math.max(0, p.v), 0) || 1, r = size / 2 - 12, c = size / 2;
    let a0 = -Math.PI / 2;
    const segs = parts.filter((p) => p.v > 0).map((p) => {
      const a1 = a0 + (2 * Math.PI * p.v) / tot, large = a1 - a0 > Math.PI ? 1 : 0;
      const d = `M${c + r * Math.cos(a0)},${c + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${c + r * Math.cos(a1)},${c + r * Math.sin(a1)}`;
      a0 = a1;
      return `<path d="${d}" stroke="${p.c}" class="wl-seg" data-seg="${esc(p.l)}" data-uix-tip="${esc(p.l)}|${m(p.v)}|${(100 * p.v / tot).toFixed(1)}%"/>`;
    }).join("");
    return `<svg viewBox="0 0 ${size} ${size}" class="wl-donut" width="${size}" height="${size}">${segs}</svg>`;
  }
  function line(series, keys, colors, h = 200) {
    if (!series || series.length < 2) return `<div class="acd-empty">Builds up day by day — a snapshot is stored every day.</div>`;
    const Wd = 760, PADL = 64, all = series.flatMap((s) => keys.map((k) => s[k])).filter(isN);
    let lo = Math.min(...all), hi = Math.max(...all); const pv = (hi - lo) * 0.08 || 1; lo -= pv; hi += pv;
    const x = (i) => PADL + (i / (series.length - 1)) * (Wd - PADL - 10), y = (v) => h - 20 - ((v - lo) / (hi - lo)) * (h - 32);
    const grid = [lo, (lo + hi) / 2, hi].map((v) => `<line x1="${PADL}" x2="${Wd - 10}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${PADL - 6}" y="${y(v) + 3}" class="t" text-anchor="end">${m(v)}</text>`).join("");
    const lines = keys.map((k, j) => `<polyline class="ln" style="stroke:${colors[j]}" points="${series.map((s, i) => (isN(s[k]) ? `${x(i).toFixed(1)},${y(s[k]).toFixed(1)}` : "")).filter(Boolean).join(" ")}"/>`).join("");
    const hits = series.map((s, i) => `<rect class="hit" x="${x(i) - (Wd - PADL) / series.length / 2}" y="0" width="${Math.max(2, (Wd - PADL) / series.length)}" height="${h}" data-uix-tip="${esc(s.day || s.month || "")}|${keys.map((k) => `${k}: ${m(s[k])}`).join("|")}"/>`).join("");
    return `<svg viewBox="0 0 ${Wd} ${h}" class="acd-svg wl-line">${grid}${lines}${hits}</svg>`;
  }

  // ------------------------------------------------------------------ interactive line chart: time ranges, legend toggles, crosshair
  const CH = new Map(); let chN = 0;
  const RANGE_D = { "7D": 7, "30D": 30, "1M": 31, "3M": 92, "90D": 90, "6M": 183, "1Y": 366, ALL: 1e6 };
  const PALETTE = ["#ffa028", "#5aa9ff", "#2be07e", "#c792ff", "#ff4a4a", "#e8d44d", "#7fe0c8", "#ff8a3d"];
  function chart(series, keys, o = {}) {
    const id = `wlch${++chN}`;
    const c = { series: series || [], keys, labels: o.labels || keys, colors: o.colors || PALETTE, fmt: o.fmt || m, h: o.h || 220, range: o.range || "ALL",
      ranges: o.ranges || ["1M", "3M", "6M", "1Y", "ALL"], off: new Set(o.off || []), area: o.area !== false, dk: o.dateKey || "day", zero: !!o.zero, chg: o.change !== false };
    CH.set(id, c);
    const legend = keys.length > 1 ? keys.map((k, j) => `<button class="wl-lg ${c.off.has(k) ? "off" : ""}" data-chk="${id}|${k}" title="show / hide"><i style="background:${c.colors[j]}"></i>${esc(c.labels[j])}</button>`).join("") : "";
    const rng = c.ranges.length > 1 ? c.ranges.map((r) => `<button class="bb-chip ${r === c.range ? "on" : ""}" data-chr="${id}|${r}">${r}</button>`).join("") : "";
    return `<div class="wl-ch" id="${id}">${legend || rng ? `<div class="wl-ch-bar"><div class="wl-ch-lg">${legend}</div><span class="sp"></span>${rng}</div>` : ""}<div class="wl-ch-s">${drawChart(id)}</div></div>`;
  }
  function visible(c) {
    if (c.range === "ALL" || c.series.length < 2) return c.series;
    const last = new Date(c.series[c.series.length - 1][c.dk]);
    if (isNaN(last)) return c.series;
    const cut = new Date(last - RANGE_D[c.range] * 864e5).toISOString().slice(0, 10);
    return c.series.filter((s) => String(s[c.dk]) >= cut);
  }
  function drawChart(id) {
    const c = CH.get(id), S = visible(c), keys = c.keys.filter((k) => !c.off.has(k));
    if (S.length < 2) return `<div class="acd-empty">Builds up day by day — a snapshot is stored every day.</div>`;
    const vals = S.flatMap((s) => keys.map((k) => s[k])).filter(isN);
    if (c.zero) vals.push(0);
    if (!vals.length) return `<div class="acd-empty">Everything is hidden — click a legend item.</div>`;
    const Wd = 760, H = c.h, L = 66, R = 10;
    let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad;
    const x = (i) => L + (i / (S.length - 1)) * (Wd - L - R), y = (v) => H - 20 - ((v - lo) / (hi - lo)) * (H - 34);
    c.view = { S, keys, L, R, Wd, x, y };
    const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * (hi - lo)).map((v) => `<line x1="${L}" x2="${Wd - R}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${L - 6}" y="${y(v) + 3}" class="t" text-anchor="end">${c.fmt(v)}</text>`).join("");
    const xl = [...new Set([0, Math.floor((S.length - 1) / 2), S.length - 1])].map((i) => `<text x="${x(i)}" y="${H - 5}" class="t" text-anchor="${i === 0 ? "start" : i === S.length - 1 ? "end" : "middle"}">${esc(String(S[i][c.dk]))}</text>`).join("");
    const zero = lo < 0 && hi > 0 ? `<line x1="${L}" x2="${Wd - R}" y1="${y(0)}" y2="${y(0)}" class="z"/>` : "";
    const lines = keys.map((k) => {
      const j = c.keys.indexOf(k), col = c.colors[j];
      const pts = S.map((s, i) => (isN(s[k]) ? `${x(i).toFixed(1)},${y(s[k]).toFixed(1)}` : "")).filter(Boolean);
      if (!pts.length) return "";
      const base = y(Math.max(lo, Math.min(hi, 0 > lo ? 0 : lo)));
      const area = c.area && k === keys[0] ? `<polygon class="ar" style="fill:${col}" points="${pts[0].split(",")[0]},${base} ${pts.join(" ")} ${pts[pts.length - 1].split(",")[0]},${base}"/>` : "";
      return `${area}<polyline class="ln" style="stroke:${col}" points="${pts.join(" ")}"/>`;
    }).join("");
    const k0 = keys[0], a = S.find((s) => isN(s[k0]))?.[k0], b = [...S].reverse().find((s) => isN(s[k0]))?.[k0];
    const chg = c.chg && isN(a) && isN(b) ? `<text x="${Wd - R}" y="12" class="t chg ${b >= a ? "up" : "dn"}" text-anchor="end">${esc(c.labels[c.keys.indexOf(k0)])} ${b >= a ? "+" : ""}${c.fmt(b - a)}${a ? ` (${pct(100 * (b / a - 1))})` : ""}</text>` : "";
    return `<svg viewBox="0 0 ${Wd} ${H}" class="acd-svg wl-line wl-ich" data-ch="${id}">${grid}${zero}${lines}${xl}${chg}<line class="wl-x" y1="16" y2="${H - 20}" style="display:none"/><g class="wl-dots"></g><rect class="wl-hit" x="${L}" y="0" width="${Wd - L - R}" height="${H}"/></svg>`;
  }
  function chartHover(e) {
    const svg = e.target.closest && e.target.closest("svg.wl-ich"); if (!svg) return;
    const c = CH.get(svg.dataset.ch); if (!c || !c.view) return;
    const { S, keys, L, R, Wd, x, y } = c.view;
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;   // screen → viewBox (the svg may be letterboxed)
    const vx = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
    const i = Math.max(0, Math.min(S.length - 1, Math.round(((vx - L) / (Wd - L - R)) * (S.length - 1))));
    const ln = svg.querySelector(".wl-x"); ln.setAttribute("x1", x(i)); ln.setAttribute("x2", x(i)); ln.style.display = "";
    svg.querySelector(".wl-dots").innerHTML = keys.filter((k) => isN(S[i][k])).map((k) => `<circle cx="${x(i)}" cy="${y(S[i][k])}" r="3.5" style="fill:${c.colors[c.keys.indexOf(k)]}"/>`).join("");
    const k0 = keys[0], prev = i > 0 ? S[i - 1][k0] : null, cur = S[i][k0];
    // ui_kit shows the tooltip from data-uix-tip on the element under the mouse (its handler runs right after this one)
    svg.querySelector(".wl-hit").dataset.uixTip = [String(S[i][c.dk]), ...keys.map((k) => `${c.labels[c.keys.indexOf(k)]}: ${c.fmt(S[i][k])}`),
      ...(isN(prev) && isN(cur) ? [`change: ${cur - prev >= 0 ? "+" : ""}${c.fmt(cur - prev)}`] : [])].join("|");
  }
  function chartOut(e) {
    const svg = e.target.closest && e.target.closest("svg.wl-ich"); if (!svg || svg.contains(e.relatedTarget)) return;
    svg.querySelector(".wl-x").style.display = "none"; svg.querySelector(".wl-dots").innerHTML = "";
  }
  function chartClick(e) {
    const t = e.target.closest("[data-chk],[data-chr]"); if (!t) return false;
    const [id, v] = (t.dataset.chk || t.dataset.chr).split("|"), c = CH.get(id); if (!c) return true;
    if (t.dataset.chk) { c.off.has(v) ? c.off.delete(v) : c.off.add(v); t.classList.toggle("off", c.off.has(v)); }
    else { c.range = v; t.parentElement.querySelectorAll("[data-chr]").forEach((b) => b.classList.toggle("on", b === t)); }
    const box = $(`#${id} .wl-ch-s`); if (box) box.innerHTML = drawChart(id);
    return true;
  }
  function bars(items, o = {}) {  // [{label, v, tip, cls}] → hoverable vertical bars
    const mx = Math.max(1e-9, ...items.map((i) => Math.abs(i.v)));
    return `<div class="wl-bars wl-vb ${o.cls || ""}">${items.map((i) => `<div class="wl-bm ${i.on ? "on" : ""}" ${i.attr || ""} data-uix-tip="${esc(i.tip || i.label)}"><i class="${i.v < 0 ? "ex" : "in"} ${i.cls || ""}" style="height:${(100 * Math.abs(i.v)) / mx}%"></i><span>${esc(i.label)}</span></div>`).join("")}</div>`;
  }
  function gauge(score, grade) {
    const r = 54, C = 2 * Math.PI * r * 0.75, col = score >= 70 ? "#2be07e" : score >= 45 ? "#ffa028" : "#ff4a4a";
    return `<svg viewBox="0 0 140 124" class="wl-gauge"><circle cx="70" cy="70" r="${r}" class="bg" stroke-dasharray="${C} 999" transform="rotate(135 70 70)"/>
      <circle cx="70" cy="70" r="${r}" class="fg" style="stroke:${col};--len:${(C * score) / 100}px" stroke-dasharray="${(C * score) / 100} 999" transform="rotate(135 70 70)"/>
      <text x="70" y="74" text-anchor="middle" class="sc uix-count">${score}</text><text x="70" y="94" text-anchor="middle" class="gr">GRADE ${grade}</text><text x="70" y="118" text-anchor="middle" class="gr dim">of 100</text></svg>`;
  }
  const CAT_LBL = { cash: "Cash", bank: "Bank", bonds: "Bonds", pension: "Pension", stocks: "Stocks & ETFs", crypto: "Crypto", real_estate: "Real estate", business: "Business", trading: "Trading", other: "Other", debt: "Debt" };
  const badge = (page, html) => { const b = $(`#wl-pb-${page}`); if (b) b.innerHTML = html; };

  // ------------------------------------------------------------------ MONEY › OVERVIEW (forecast, health, events, currency)
  async function secPulse(el) {
    const o = await call("/outlook");
    W.fx = o.usdils || W.fx;
    const h = o.health, last = o.forecast[o.forecast.length - 1];
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("MAIN BALANCE", ils(o.main_balance_ils), "what the bank shows", "", "set it once on CASH FLOW; salary, bills and card answers move it")}
        ${kpi("IN / MONTH", ils(o.income_month_ils), "salary + fixed income", "up")}
        ${kpi("OUT / MONTH", ils(-o.burn_month_ils), esc(o.burn_source), "dn")}
        ${kpi("LEFT EACH MONTH", (o.net_month_ils >= 0 ? "+" : "") + ils(o.net_month_ils), isN(o.savings_rate) ? `savings rate ${o.savings_rate}%` : "", cls(o.net_month_ils))}
        ${kpi("EMERGENCY RUNWAY", isN(o.runway_months) ? `${o.runway_months} mo` : "—", `cash + bank ${ils(o.safe_ils)}`, o.runway_months >= 6 ? "up" : o.runway_months < 3 ? "dn" : "", "months your cash + bank would cover spending if income stopped (6+ is healthy)")}
        ${kpi("IN 12 MONTHS", last ? ils(last.balance_ils) : "—", "main balance forecast", last ? cls(last.balance_ils - o.main_balance_ils) : "")}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "MAIN BALANCE — NEXT 12 MONTHS", chart([{ month: "now", balance_ils: o.main_balance_ils }, ...o.forecast], ["balance_ils"], { labels: ["balance"], fmt: ils, dateKey: "month", ranges: [], zero: true, h: 200 })
          + `<p class="acd-note">Every month: +${ils(o.income_month_ils)} fixed income, −${ils(o.burn_month_ils - o.cc_avg_ils)} fixed bills, −${ils(o.cc_avg_ils)} typical credit-card spend. Hover the line for each month.</p>`, "forecast")}
        ${P(2, "NET WORTH BY CATEGORY OVER TIME", (() => { const ks = [...new Set(o.composition.flatMap((r) => Object.keys(r).filter((k) => k !== "day" && k !== "total")))];
          return chart(o.composition, ["total", ...ks], { labels: ["Total", ...ks.map((k) => CAT_LBL[k] || k)], colors: ["#ffa028", ...ks.map((k) => CAT_COL[k] || "#888")], off: ks }); })(), "click a category to add it")}
      </div><div class="acd-side">
        ${P(3, "FINANCIAL HEALTH", `<div class="wl-health">${gauge(h.score, h.grade)}<div class="wl-hp">${h.parts.map((p) => `<div data-uix-tip="${esc(p.label)}|${esc(p.value)}|${esc(p.tip)}"><span>${esc(p.label)}</span><div class="wl-cb"><i style="width:${p.score * 5}%;background:${p.score >= 15 ? "#2be07e" : p.score >= 8 ? "#ffa028" : "#ff4a4a"}"></i></div><b>${p.score}/20</b><small class="dim">${esc(p.value)}</small></div>`).join("")}</div></div>`, "hover a row")}
        ${P(4, "NEXT 60 DAYS", `<div class="wl-tl">${o.events.map((e) => { const d = new Date(e.day + "T12:00:00"), n = Math.round((d - new Date()) / 864e5);
          return `<div class="wl-ev ${e.kind}"><div class="wl-evd"><b>${d.getDate()}</b><span>${d.toLocaleString("en", { month: "short" }).toUpperCase()}</span></div><div class="wl-evt"><span>${esc(e.title)}</span><small class="dim">${n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`} · ${d.toLocaleString("en", { weekday: "long" })}</small></div>${isN(e.amount_ils) ? `<b class="${cls(e.amount_ils)}">${e.amount_ils > 0 ? "+" : ""}${ils(e.amount_ils)}</b>` : ""}</div>`; }).join("")}</div>`)}
        ${P(5, "CURRENCY EXPOSURE", `<div class="wl-stack">${o.currency.map((c, i) => `<i style="width:${c.pct}%;background:${PALETTE[i % 8]}" data-uix-tip="${esc(c.currency)}|${m(c.usd)}|${c.pct}% of assets"></i>`).join("")}</div>
          <div class="wl-leg">${o.currency.map((c, i) => `<div><i style="background:${PALETTE[i % 8]}"></i><span>${esc(c.currency)}</span><b>${c.pct}%</b></div>`).join("")}</div>
          ${(() => { const usd = o.currency.filter((c) => c.currency !== "ILS").reduce((s, c) => s + c.usd, 0); return usd ? `<p class="acd-note">If the shekel strengthens 10%, your foreign assets lose <b class="dn">${ils(usd * o.usdils * 0.1)}</b> in shekel terms (and gain the same if it weakens).</p>` : ""; })()}`)}
      </div></div>`;
  }

  // ------------------------------------------------------------------ INVESTMENTS › HOLDINGS (returns, 52w range, beta, risk share)
  async function secHold(el) {
    const h = await call("/holdings");
    if (!h.rows.length) { el.innerHTML = P(1, "HOLDINGS", `<div class="acd-empty">Add holdings on CAPITAL › PORTFOLIO.</div>`); return; }
    const R = h.rows, withR = R.filter((r) => isN(r.r1m));
    const best = withR.reduce((a, b) => (b.r1m > (a?.r1m ?? -1e9) ? b : a), null), worst = withR.reduce((a, b) => (b.r1m < (a?.r1m ?? 1e9) ? b : a), null);
    const wb = R.filter((r) => isN(r.beta)).reduce((s, r) => s + r.beta * r.weight / 100, 0);
    const topRisk = R.filter((r) => isN(r.risk_share)).sort((a, b) => b.risk_share - a.risk_share)[0];
    const day = R.reduce((s, r) => s + (isN(r.day_pct) ? r.value * r.day_pct / (100 + r.day_pct) : 0), 0);
    const cell = (v, d = 1) => `<td class="r ${cls(v)}" data-v="${v ?? ""}">${pct(v, d)}</td>`;
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("PORTFOLIO", m(h.total_usd), `${R.length} holdings`)}
        ${kpi("TODAY", sm(day), "price moves", cls(day))}
        ${best ? kpi("BEST 1 MONTH", esc(best.symbol), pct(best.r1m), "up") : ""}
        ${worst ? kpi("WORST 1 MONTH", esc(worst.symbol), pct(worst.r1m), "dn") : ""}
        ${kpi("PORTFOLIO BETA", wb ? wb.toFixed(2) : "—", "vs S&P 500", wb > 1.5 ? "dn" : "", "1.0 = moves like the S&P 500; 2.0 = twice as much, both ways")}
        ${topRisk ? kpi("BIGGEST RISK", esc(topRisk.symbol), `${topRisk.risk_share}% of all the swings`, topRisk.risk_share > 60 ? "dn" : "", "share of the portfolio's day-to-day variance that comes from this holding") : ""}
      </div>
      ${P(1, "EVERY HOLDING — click a row to open its chart", `<div class="wl-tw"><table class="bb-tbl uix-sort wl-tbl wl-hold"><thead><tr><th>SYMBOL</th><th>90 DAYS</th><th class="r">PRICE</th><th class="r">DAY</th><th class="r">1M</th><th class="r">3M</th><th class="r">YTD</th><th class="r">1Y</th><th>52-WEEK RANGE</th><th class="r">VOL</th><th class="r">BETA</th><th class="r">WEIGHT</th><th>RISK SHARE</th></tr></thead><tbody>
        ${R.map((r) => { const pos = isN(r.high) && r.high > r.low ? (100 * (r.price - r.low)) / (r.high - r.low) : null;
          return `<tr class="click" data-sec="${esc(r.symbol)}"><td class="sym">${esc(r.symbol)}</td><td>${window.UIX && r.spark ? UIX.spark(r.spark, { w: 90, h: 22 }) : ""}</td><td class="r" data-v="${r.price}">$${isN(r.price) ? r.price.toLocaleString(undefined, { maximumFractionDigits: r.price < 10 ? 3 : 2 }) : "—"}</td>
            ${cell(r.day_pct, 2)}${cell(r.r1m)}${cell(r.r3m)}${cell(r.ytd)}${cell(r.r1y)}
            <td data-v="${pos ?? ""}">${isN(pos) ? `<div class="wl-rng" data-uix-tip="${esc(r.symbol)} 52 weeks|low $${r.low.toFixed(2)} · high $${r.high.toFixed(2)}|${r.from_high}% from the high · +${r.from_low}% from the low"><i style="left:${pos}%"></i></div>` : ""}</td>
            <td class="r" data-v="${r.vol ?? ""}">${isN(r.vol) ? r.vol + "%" : "—"}</td><td class="r" data-v="${r.beta ?? ""}">${r.beta ?? "—"}</td><td class="r" data-v="${r.weight}">${r.weight}%</td>
            <td data-v="${r.risk_share ?? ""}">${isN(r.risk_share) ? `<div class="wl-cb wl-rs" data-uix-tip="${esc(r.symbol)}|weight ${r.weight}%|risk share ${r.risk_share}%"><i style="width:${Math.min(100, Math.max(0, r.risk_share))}%"></i></div><small>${r.risk_share}%</small>` : ""}</td></tr>`; }).join("")}</tbody></table></div>
        <p class="acd-note">VOL = yearly volatility · BETA vs S&P 500 (1y daily) · RISK SHARE = how much of the portfolio's swings come from it; far above its weight means it dominates your risk. Click a column to sort.</p>`)}`;
    badge("invest", `${sm(day)} today`);
  }

  // ------------------------------------------------------------------ INVESTMENTS › STRESS TEST (live sliders)
  async function secStress(el) {
    const nw = await call("/networth");
    W.fx = nw.usdils || W.fx;
    const SH = W.shock || (W.shock = { stocks: 0, crypto: 0, trading: 0, pension: 0, real_estate: 0, business: 0, fx: 0 });
    const PRE = {
      "2008 CRASH": { stocks: -50, crypto: -75, trading: -40, pension: -25, real_estate: -20, business: -30, fx: 12 },
      "CRYPTO WINTER": { stocks: -10, crypto: -80, trading: -30, pension: -3, real_estate: 0, business: 0, fx: 0 },
      "COVID MARCH": { stocks: -34, crypto: -50, trading: -20, pension: -15, real_estate: -5, business: -25, fx: 8 },
      "SHEKEL +15%": { stocks: 0, crypto: 0, trading: 0, pension: 0, real_estate: 0, business: 0, fx: -15 },
      "BULL YEAR": { stocks: 25, crypto: 90, trading: 10, pension: 12, real_estate: 8, business: 10, fx: -3 },
      RESET: { stocks: 0, crypto: 0, trading: 0, pension: 0, real_estate: 0, business: 0, fx: 0 },
    };
    const SL = [["stocks", "Stocks & ETFs"], ["crypto", "Crypto"], ["trading", "Trading accounts"], ["pension", "Pension"], ["real_estate", "Real estate"], ["business", "Business"], ["fx", "Dollar vs ₪"]];
    el.innerHTML = `<div class="acd-cols"><div class="acd-main">${P(1, "WHAT IF… — drag the sliders or pick a scenario", `<div class="wl-pre-b">${Object.keys(PRE).map((k) => `<button class="bb-chip" data-pre="${k}">${k}</button>`).join("")}</div>
        <div class="wl-sl">${SL.map(([k, l]) => `<label><span>${l}</span><input type="range" min="${k === "fx" ? -30 : -95}" max="${k === "fx" ? 30 : 150}" step="1" value="${SH[k]}" data-shock="${k}"/><b id="shv-${k}" class="${cls(SH[k])}">${SH[k] > 0 ? "+" : ""}${SH[k]}%</b></label>`).join("")}</div>
        <p class="acd-note">Bank, cash and bonds are not shocked. "Dollar vs ₪" changes the shekel value of everything held in dollars.</p>`)}</div>
      <div class="acd-side">${P(2, "RESULT", `<div id="st-out"></div>`)}</div></div>`;
    const calc = () => {
      let before = 0, after = 0; const byCat = {};
      nw.items.forEach((i) => {
        const cur = i.currency || "USD", s = (SH[i.category] || 0) / 100;
        const b0 = i.usd * W.fx, a0 = i.usd * (1 + s) * W.fx * (cur === "ILS" ? 1 : 1 + SH.fx / 100);
        before += b0; after += a0; byCat[i.category] = (byCat[i.category] || 0) + (a0 - b0);
      });
      const d = after - before, rows = Object.entries(byCat).filter(([, v]) => Math.abs(v) >= 1).sort((a, b) => a[1] - b[1]), mx = Math.max(1, ...rows.map(([, v]) => Math.abs(v)));
      $("#st-out").innerHTML = `<div class="wl-stres"><div><span>NOW</span><b>${ils(before)}</b></div><div><span>AFTER</span><b class="${cls(d)}">${ils(after)}</b></div>
          <div class="wl-stbig ${cls(d)}">${d >= 0 ? "+" : ""}${ils(d)}<small>${before ? pct((100 * d) / before) : ""}</small></div></div>
        <div class="wl-cats">${rows.map(([k, v]) => `<div><span>${esc(CAT_LBL[k] || k)}</span><div class="wl-cb wl-bi"><i class="${v < 0 ? "neg" : "pos"}" style="width:${(50 * Math.abs(v)) / mx}%"></i></div><b class="${cls(v)}">${v > 0 ? "+" : ""}${ils(v)}</b></div>`).join("") || '<div class="dim">Move a slider to see the impact.</div>'}</div>`;
    };
    const setV = (k) => { const b = $(`#shv-${k}`); b.textContent = `${SH[k] > 0 ? "+" : ""}${SH[k]}%`; b.className = cls(SH[k]); };
    el.querySelectorAll("[data-shock]").forEach((i) => i.addEventListener("input", () => { SH[i.dataset.shock] = +i.value; setV(i.dataset.shock); calc(); }));
    el.querySelectorAll("[data-pre]").forEach((b) => b.addEventListener("click", () => { Object.assign(SH, PRE[b.dataset.pre]); el.querySelectorAll("[data-shock]").forEach((i) => { i.value = SH[i.dataset.shock]; setV(i.dataset.shock); }); el.querySelectorAll("[data-pre]").forEach((x) => x.classList.toggle("on", x === b)); calc(); }));
    calc();
  }

  // ------------------------------------------------------------------ SPENDING › INSIGHTS (from the imported transactions)
  async function secSpendX(el) {
    const b = await call("/budget");
    const tx = b.transactions || [], ex = tx.filter((t) => t.amount < 0 && !/invest|saving/i.test(t.category || ""));
    if (!ex.length) {
      el.innerHTML = P(1, "SPENDING INSIGHTS", `<div class="acd-empty">No spending data yet. Import a bank or credit-card CSV below (or answer the monthly credit-card question on Telegram) and this fills with: this month's pace, top merchants, weekday patterns and the biggest expenses.</div>`);
      badge("spend", "import a CSV"); return;
    }
    const now = new Date(), ym = now.toISOString().slice(0, 7), dim = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const mtd = -ex.filter((t) => t.day.startsWith(ym)).reduce((s, t) => s + t.amount, 0), proj = (mtd / now.getDate()) * dim;
    const since = new Date(now - 90 * 864e5).toISOString().slice(0, 10), r90 = ex.filter((t) => t.day >= since);
    const mer = {}; r90.forEach((t) => { const k = t.description.trim().slice(0, 40); (mer[k] = mer[k] || { n: 0, v: 0, cat: t.category }); mer[k].n++; mer[k].v -= t.amount; });
    const top = Object.entries(mer).sort((a, b) => b[1].v - a[1].v).slice(0, 12), mxm = top[0]?.[1].v || 1;
    const wd = [0, 0, 0, 0, 0, 0, 0]; r90.forEach((t) => { wd[(new Date(t.day + "T12:00:00").getDay() + 6) % 7] -= t.amount; });
    const cats = {}; ex.filter((t) => t.day.startsWith(ym)).forEach((t) => { cats[t.category || "Other"] = (cats[t.category || "Other"] || 0) - t.amount; });
    const topCat = Object.entries(cats).sort((a, b) => b[1] - a[1])[0];
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("SPENT THIS MONTH", ils(mtd), `day ${now.getDate()} of ${dim}`, "dn")}
        ${kpi("ON PACE FOR", ils(proj), b.avg_expense ? `avg month ${ils(b.avg_expense)}` : "", proj > (b.avg_expense || 1e12) ? "dn" : "up", "this month so far, extended to the full month")}
        ${kpi("VS AVERAGE", b.avg_expense ? pct((100 * proj) / b.avg_expense - 100) : "—", proj > b.avg_expense ? "spending more than usual" : "spending less than usual", proj > b.avg_expense ? "dn" : "up")}
        ${kpi("TOP CATEGORY", topCat ? esc(topCat[0]) : "—", topCat ? ils(topCat[1]) + " this month" : "")}
        ${kpi("TRANSACTIONS (90D)", r90.length, `avg ${ils(r90.length ? r90.reduce((s, t) => s - t.amount, 0) / r90.length : 0)}`)}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "TOP MERCHANTS — last 90 days", `<div class="wl-cats">${top.map(([k, v]) => `<div class="click" data-merch="${esc(k)}" data-uix-tip="${esc(k)}|${v.n} payments · avg ${ils(v.v / v.n)}|${esc(v.cat || "")}|click to filter the transactions"><span dir="auto">${esc(k)}</span><div class="wl-cb"><i style="width:${(100 * v.v) / mxm}%"></i></div><b>${ils(v.v)}</b></div>`).join("")}</div>`)}
      </div><div class="acd-side">
        ${P(2, "WHICH DAYS YOU SPEND", bars(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map((l, i) => ({ label: l, v: wd[i], tip: `${l}|${ils(wd[i])} in 90 days`, cls: "ex" }))), "90 days")}
        ${P(3, "BIGGEST EXPENSES — 90 days", `<table class="bb-tbl wl-tbl"><tbody>${[...r90].sort((a, b) => a.amount - b.amount).slice(0, 8).map((t) => `<tr><td class="dim">${esc(t.day.slice(5))}</td><td dir="auto">${esc(t.description.slice(0, 32))}</td><td class="r dn">${ils(t.amount)}</td></tr>`).join("")}</tbody></table>`)}
      </div></div>`;
    el.querySelectorAll("[data-merch]").forEach((d) => d.addEventListener("click", () => { const q = $("#bt-q"); if (q) { q.value = d.dataset.merch; q.dispatchEvent(new Event("input")); q.scrollIntoView({ behavior: "smooth", block: "center" }); } }));
    badge("spend", `${ils(mtd)} this month`);
  }

  // ------------------------------------------------------------------ INVESTMENTS › GOALS & MISSIONS (every area, with the work to do)
  const fmtU = (v, u) => (!isN(v) ? "—" : u === "₪" ? ils(v) : u === "$" ? `${v < 0 ? "-" : ""}$${Math.abs(Math.round(v)).toLocaleString()}` : u === "%" ? `${(+v).toFixed(1)}%` : u === "count" ? Math.round(v).toLocaleString() : `${(+v).toFixed(1)} ${u || ""}`);
  const AREA_CODE = { nostro: "NF", stocks: "ST", wealth: "WL", business: "BZ", personal: "PS" };
  W.hubArea = W.hubArea || "";
  function hubRing(pct, color, id) {
    const r = 46, C = 2 * Math.PI * r;
    return `<svg viewBox="0 0 110 110" class="wl-hring"><defs><linearGradient id="hg${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="#fff" stop-opacity=".85"/></linearGradient></defs>
      <circle cx="55" cy="55" r="${r}" class="bg"/><circle cx="55" cy="55" r="${r}" class="tk" stroke-dasharray="1 ${(C / 40 - 1).toFixed(2)}"/>
      <circle cx="55" cy="55" r="${r}" class="fg" stroke="url(#hg${id})" style="--len:${((C * pct) / 100).toFixed(1)}px" stroke-dasharray="${((C * pct) / 100).toFixed(1)} 999" transform="rotate(-90 55 55)"/>
      <text x="55" y="60" text-anchor="middle" class="pc">${Math.round(pct)}%</text></svg>`;
  }
  function hubCard(g) {
    const done = g.status === "reached";
    const next = g.next_task;
    const hist = g.history.map((h) => h.value);
    return `<article class="wl-hc ${done ? "done" : ""} st-${g.status.replace(" ", "-")}" style="--c:${g.color}" data-hg="${g.id}">
      <div class="wl-hc-glow"></div>
      <header><span class="wl-hc-e">${esc(AREA_CODE[g.area] || "GO")}</span><div><small style="color:${g.color}">${esc((W.hubAreas[g.area] || {}).label || g.area)}</small><b>${esc(g.title)}</b></div><span class="sp"></span>
        <span class="wl-hc-st">${done ? "✓ REACHED" : g.status.toUpperCase()}</span></header>
      <div class="wl-hc-mid">${hubRing(g.pct, g.color, g.id)}
        <div class="wl-hc-num"><b>${fmtU(g.value, g.unit)}</b><span class="dim">${g.direction === "down" ? "down to" : "of"} ${fmtU(g.target, g.unit)}</span>
          <div class="wl-hc-ms">${[25, 50, 75, 100].map((m) => `<i class="${g.milestones.includes(m) ? "on" : ""}" data-uix-tip="MILESTONE ${m}%|${g.milestones.includes(m) ? "unlocked ✓" : "locked"}">${m === 100 ? "★" : m}</i>`).join("")}</div>
          <small class="dim" data-uix-tip="TRACKED|${esc(g.metric_label || "")}">${g.metric ? "AUTO · " : "MANUAL · "}${esc(g.metric_label || "")}</small></div></div>
      ${g.why ? `<p class="wl-hc-why">“${esc(g.why)}”</p>` : ""}
      ${next ? `<div class="wl-hc-next"><span>▶ NEXT ACTION</span><b>${esc(next.title)}</b><button class="wl-hc-do" data-htk="${next.id}">✓ DONE</button></div>` : done ? `<div class="wl-hc-next win"><b>GOAL REACHED${g.reached_at ? " on " + esc(g.reached_at) : ""}</b></div>` : `<div class="wl-hc-next"><span>▶ NEXT ACTION</span><b class="dim">No tasks left — add the next step below.</b></div>`}
      <div class="wl-hc-meta">
        <div data-uix-tip="DEADLINE|${esc(g.deadline || "none")}"><span>DAYS LEFT</span><b class="${isN(g.days_left) && g.days_left < 30 ? "warn" : ""}">${isN(g.days_left) ? g.days_left : "—"}</b></div>
        <div data-uix-tip="ON SCHEDULE|a straight line from the start to the deadline says ${isN(g.expected_pct) ? g.expected_pct + "%" : "—"} by today"><span>SHOULD BE</span><b>${isN(g.expected_pct) ? g.expected_pct + "%" : "—"}</b></div>
        <div data-uix-tip="PACE|change per day over the last 30 days"><span>PACE / DAY</span><b class="${cls((g.direction === "down" ? -1 : 1) * (g.pace_per_day || 0))}">${isN(g.pace_per_day) ? fmtU(g.pace_per_day, g.unit) : "—"}</b></div>
        <div data-uix-tip="ETA|when you get there at the current pace"><span>ETA</span><b>${done ? "✓" : g.eta || "—"}</b></div></div>
      ${hist.length > 1 && window.UIX ? `<div class="wl-hc-sp">${UIX.spark(hist, { w: 300, h: 26 })}</div>` : ""}
      ${!g.metric ? `<div class="wl-hc-man"><input type="number" step="any" placeholder="new value" class="wl-hc-mv"/><button class="bb-chip" data-hmv="${g.id}">UPDATE VALUE</button></div>` : ""}
      <details class="wl-hc-tasks" ${W.hubOpen && W.hubOpen[g.id] ? "open" : ""} data-hopen="${g.id}"><summary><span>THE WORK</span><em>${g.tasks_done}/${g.tasks.length} done</em><div class="wl-hc-tb"><i style="width:${g.tasks.length ? (100 * g.tasks_done) / g.tasks.length : 0}%"></i></div></summary>
        ${g.tasks.map((t) => `<label class="wl-task ${t.done_at ? "on" : ""}" data-htk="${t.id}"><span class="wl-cbx">${t.done_at ? "✓" : ""}</span><span class="wl-tt">${esc(t.title)}</span>${t.done_at ? `<small class="dim">${esc(t.done_at.slice(5))}</small>` : ""}<button class="wl-star" data-htdel="${t.id}" title="remove">✕</button></label>`).join("")}
        <div class="wl-hc-add"><input placeholder="add the next step…" class="wl-hc-nt"/><button class="bb-chip" data-hadd="${g.id}">+ ADD</button></div></details>
      <footer><button class="wl-star" data-hedit='${esc(JSON.stringify({ id: g.id, area: g.area, title: g.title, emoji: g.emoji, target: g.target, deadline: g.deadline, why: g.why, direction: g.direction, metric: g.metric }))}' title="edit">✎ edit</button><button class="wl-star" data-hdel="${g.id}" title="delete">✕ delete</button></footer>
    </article>`;
  }
  async function secHub(el) {
    const o = await call(`/hub${W.hubArea ? `?area=${W.hubArea}` : ""}`);
    W.hubAreas = o.areas; W.hubOpen = W.hubOpen || {};
    const p = o.player, lvPct = p.level_to > p.level_from ? (100 * (p.xp - p.level_from)) / (p.level_to - p.level_from) : 0;
    const cat = o.metric_catalog;
    el.innerHTML = `<div class="wl-hub">
      <div class="wl-player">
        <div class="wl-lv"><span>LEVEL</span><b class="uix-count">${p.level}</b></div>
        <div class="wl-xp"><div class="wl-xp-t"><b>${p.xp} XP</b><span class="dim">${p.level_to - p.xp} XP to level ${p.level + 1} · 10 XP per task · 100 XP per goal</span></div><div class="wl-xp-b"><i style="width:${lvPct}%"></i></div>
          <div class="wl-xp-s"><span><b>${p.active}</b> active goals</span><span><b>${p.goals_reached}</b> reached</span><span><b>${p.tasks_done}</b> tasks done</span><span><b>${p.tasks_this_week}</b> this week</span></div></div>
        ${o.focus ? `<div class="wl-focus"><span>TODAY'S FOCUS · ${esc(o.focus.title)}</span><b>${esc(o.focus.task.title)}</b><button class="wl-hc-do big" data-htk="${o.focus.task.id}">✓ I DID IT</button></div>` : `<div class="wl-focus"><b>All tasks done — add the next steps to your goals.</b></div>`}
      </div>
      <div class="wl-harea"><button class="bb-chip ${!W.hubArea ? "on" : ""}" data-harea="">ALL</button>${Object.entries(o.areas).map(([k, a]) => `<button class="bb-chip ${W.hubArea === k ? "on" : ""}" data-harea="${k}"><i style="background:${a.color}"></i>${esc(a.label)} <small>${a.n}</small></button>`).join("")}<span class="sp"></span><button class="bb-chip wl-hnew" id="hb-new">+ NEW GOAL</button></div>
      <div class="wl-hbuild" id="hb-form" hidden><input type="hidden" id="hb-id"/>
        <div class="wl-hb-g">
          <label>Area<select id="hb-area">${Object.entries(o.areas).map(([k, a]) => `<option value="${k}">${esc(a.label)}</option>`).join("")}</select></label>
          <label>Track automatically<select id="hb-metric"><option value="">Nothing — I update the value by hand</option>${Object.entries(cat).map(([k, m]) => `<option value="${k}" data-area="${m.area}" data-unit="${m.unit}">${esc(m.label)} (now ${fmtU(m.value, m.unit)})</option>`).join("")}</select></label>
          <label>Goal<input id="hb-title" placeholder="e.g. 3 funded accounts by March"/></label>
          <label>Target<input id="hb-target" type="number" step="any"/></label>
          <label>Direction<select id="hb-dir"><option value="up">reach at least the target</option><option value="down">bring it down to the target</option></select></label>
          <label>Deadline<input id="hb-dl" type="date"/></label>
          <label class="wide">Why it matters<input id="hb-why" placeholder="what changes when you get there"/></label>
          <label class="wide">The work — one task per line<textarea id="hb-tasks" rows="4" placeholder="step 1&#10;step 2&#10;step 3"></textarea></label>
        </div>
        <div class="wl-btns"><button class="btn" id="hb-save">SAVE GOAL</button><button class="btn btn-small" id="hb-cancel">CANCEL</button><span class="dim" id="hb-hint"></span></div></div>
      <div class="wl-hgrid">${o.goals.map(hubCard).join("") || '<div class="acd-empty">No goals in this area yet — press + NEW GOAL.</div>'}</div></div>`;
    // 3D tilt + light that follows the mouse
    el.querySelectorAll(".wl-hc").forEach((c) => {
      c.addEventListener("mousemove", (e) => { const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height; c.style.setProperty("--rx", `${(0.5 - y) * 5}deg`); c.style.setProperty("--ry", `${(x - 0.5) * 7}deg`); c.style.setProperty("--mx", `${x * 100}%`); c.style.setProperty("--my", `${y * 100}%`); });
      c.addEventListener("mouseleave", () => { c.style.setProperty("--rx", "0deg"); c.style.setProperty("--ry", "0deg"); });
    });
    el.querySelectorAll("[data-hopen]").forEach((d) => d.addEventListener("toggle", () => { W.hubOpen[d.dataset.hopen] = d.open; }));
    el.querySelectorAll("[data-harea]").forEach((b) => b.addEventListener("click", () => { W.hubArea = b.dataset.harea; render(); }));
    el.querySelectorAll("[data-htk]").forEach((b) => b.addEventListener("click", async (e) => {
      if (e.target.closest("[data-htdel]")) return;
      e.preventDefault();
      const card = b.closest(".wl-hc"), lab = b.classList.contains("wl-task") ? b : null;
      if (lab) { const on = !lab.classList.contains("on"); lab.classList.toggle("on", on); lab.querySelector(".wl-cbx").textContent = on ? "✓" : ""; }
      else { b.textContent = "✓ +10 XP"; b.classList.add("pop"); }
      try { const r = await call(`/hub/task/${b.dataset.htk}/toggle`, {}); if (r.done) { showToast("+10 XP"); burst(card || b.parentElement); } setTimeout(render, r.done ? 650 : 0); } catch (err) { showToast(err.message); }
    }));
    el.querySelectorAll("[data-htdel]").forEach((b) => b.addEventListener("click", async (e) => { e.preventDefault(); e.stopPropagation(); await call(`/hub/task/${b.dataset.htdel}`, undefined, "DELETE"); render(); }));
    el.querySelectorAll("[data-hadd]").forEach((b) => { const inp = b.parentElement.querySelector(".wl-hc-nt"); const go = async () => { if (!inp.value.trim()) return; W.hubOpen[b.dataset.hadd] = true; await call("/hub/task", { goal_id: +b.dataset.hadd, title: inp.value }); render(); }; b.addEventListener("click", go); inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); });
    el.querySelectorAll("[data-hmv]").forEach((b) => b.addEventListener("click", async () => { const v = b.parentElement.querySelector(".wl-hc-mv").value; if (v === "") return; await call(`/hub/goal/${b.dataset.hmv}/value`, { value: +v }); showToast("Value updated"); render(); }));
    el.querySelectorAll("[data-hdel]").forEach((b) => b.addEventListener("click", async () => { if (confirm("Delete this goal?")) { await call(`/hub/goal/${b.dataset.hdel}`, undefined, "DELETE"); render(); } }));
    // goal builder
    let emo = null;
    const form = $("#hb-form"), pickE = (e) => { emo = e; el.querySelectorAll("[data-hemo]").forEach((x) => x.classList.toggle("on", x.dataset.hemo === e)); };
    const syncMetric = () => { const opt = $("#hb-metric").selectedOptions[0], k = $("#hb-metric").value; if (k) { $("#hb-area").value = opt.dataset.area; $("#hb-dir").value = k === "stocks.top_weight" ? "down" : "up"; $("#hb-hint").textContent = `now ${fmtU(cat[k].value, cat[k].unit)} — progress is measured from here`; } else $("#hb-hint").textContent = "you'll update the value yourself on the card"; };
    const open = (g) => { form.hidden = false; $("#hb-id").value = g.id || ""; $("#hb-title").value = g.title || ""; $("#hb-target").value = g.target ?? ""; $("#hb-dl").value = g.deadline || ""; $("#hb-why").value = g.why || ""; $("#hb-area").value = g.area || W.hubArea || "stocks"; $("#hb-dir").value = g.direction || "up"; $("#hb-metric").value = g.metric || ""; $("#hb-metric").disabled = !!g.id; $("#hb-tasks").value = ""; $("#hb-tasks").disabled = !!g.id; pickE(g.emoji || null); syncMetric(); form.scrollIntoView({ behavior: "smooth", block: "center" }); $("#hb-title").focus(); };
    $("#hb-new").addEventListener("click", () => (form.hidden ? open({}) : (form.hidden = true)));
    $("#hb-cancel").addEventListener("click", () => { form.hidden = true; });
    $("#hb-metric").addEventListener("change", syncMetric);
    el.querySelectorAll("[data-hemo]").forEach((b) => b.addEventListener("click", () => pickE(b.dataset.hemo)));
    el.querySelectorAll("[data-hedit]").forEach((b) => b.addEventListener("click", () => open(JSON.parse(b.dataset.hedit))));
    $("#hb-save").addEventListener("click", async () => {
      try {
        await call("/hub/goal", { id: +$("#hb-id").value || null, area: $("#hb-area").value, title: $("#hb-title").value, emoji: emo, metric: $("#hb-metric").value || null,
          direction: $("#hb-dir").value, target: +$("#hb-target").value, deadline: $("#hb-dl").value || null, why: $("#hb-why").value || null,
          tasks: $("#hb-tasks").value.split("\n").map((s) => s.trim()).filter(Boolean) });
        showToast("Goal saved"); render();
      } catch (e) { showToast(e.message); }
    });
  }

  // ------------------------------------------------------------------ INVESTMENTS › SAVINGS GOALS
  const ST_CLS = { done: "up", ahead: "up", "on track": "", behind: "dn", "no deadline": "dim" };
  function burst(node) {  // small celebration when something hits 100%
    if (!node || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const b = document.createElement("div"); b.className = "wl-burst";
    b.innerHTML = Array.from({ length: 18 }, (_, i) => `<i style="--a:${i * 20}deg;--c:${PALETTE[i % 8]}"></i>`).join("");
    node.appendChild(b); setTimeout(() => b.remove(), 900);
  }
  async function secSavings(el) {
    const G = await call("/goals");
    const gs = G.goals, tot = G.total_target ? (100 * G.total_saved) / G.total_target : 0;
    const card = (g) => `<article class="wl-goalc ${g.status === "done" ? "done" : ""}" data-gid="${g.id}">
        <header><span class="wl-ge">${esc((g.name || "?").trim().slice(0, 2).toUpperCase())}</span><div><b>${esc(g.name)}</b><small class="dim">${g.deadline ? `by ${esc(g.deadline)}${isN(g.months_left) ? ` · ${g.months_left} months left` : ""}` : "no deadline"}</small></div>
          <span class="sp"></span><span class="wl-gst ${ST_CLS[g.status] || ""}">${g.status.toUpperCase()}</span>
          <button class="wl-star" data-gedit='${esc(JSON.stringify({ id: g.id, name: g.name, emoji: g.emoji, target: g.target, deadline: g.deadline }))}' title="edit">✎</button><button class="wl-star" data-gdel="${g.id}" title="delete">✕</button></header>
        <div class="wl-gmain"><b class="wl-gpct uix-count">${g.pct}%</b><div><span>${ils(g.saved)}</span><small class="dim"> of ${ils(g.target)}</small></div></div>
        <div class="wl-gbar" data-uix-tip="${esc(g.name)}|saved ${g.pct}%${isN(g.expected_pct) ? `|should be ${g.expected_pct}% by today to finish on time` : ""}"><i style="width:${Math.min(100, g.pct)}%"></i>${isN(g.expected_pct) ? `<em style="left:${g.expected_pct}%"></em>` : ""}</div>
        <div class="acd-sg wl-gsg">
          <div class="acd-st"><span>Still needed</span><b>${ils(g.remaining)}</b></div>
          <div class="acd-st" data-uix-tip="PER MONTH|what you need to put in every month to make the deadline"><span>Per month</span><b class="${g.monthly_needed > (g.rate_month || 0) && g.status !== "done" ? "warn" : ""}">${isN(g.monthly_needed) ? ils(g.monthly_needed) : "—"}</b></div>
          <div class="acd-st" data-uix-tip="YOUR PACE|average deposit per month over the last 3 months"><span>Your pace</span><b>${g.rate_month ? ils(g.rate_month) + "/mo" : "—"}</b></div>
          <div class="acd-st"><span>Done by</span><b>${g.status === "done" ? "✓ reached" : g.eta || "—"}</b></div></div>
        ${g.curve.length > 1 && window.UIX ? `<div class="wl-gsp">${UIX.spark(g.curve.map((c) => c.saved), { w: 260, h: 26 })}</div>` : ""}
        <div class="wl-gq">${[100, 500, 1000].map((a) => `<button class="bb-chip" data-gadd="${a}">+₪${a.toLocaleString()}</button>`).join("")}<input type="number" step="any" placeholder="₪" class="wl-gamt"/><button class="bb-chip" data-gcustom="1">+ ADD</button><button class="bb-chip" data-gcustom="-1">− TAKE OUT</button></div>
        ${g.moves.length ? `<details class="wl-gh"><summary class="dim">history (${g.moves.length})</summary><table class="bb-tbl wl-tbl"><tbody>${g.moves.map((mv) => `<tr><td class="dim">${esc(mv.day)}</td><td>${esc(mv.note || "")}</td><td class="r ${cls(mv.amount)}">${mv.amount > 0 ? "+" : ""}${ils(mv.amount)}</td></tr>`).join("")}</tbody></table></details>` : ""}
      </article>`;
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("SAVED FOR GOALS", ils(G.total_saved), `${gs.length} goals`, "up")}
        ${kpi("ALL TARGETS", ils(G.total_target), `${tot.toFixed(1)}% there`)}
        ${kpi("NEEDED / MONTH", ils(G.total_monthly_needed), "to hit every deadline", "", "sum of what each goal needs per month")}
        ${kpi("ON TRACK", `${gs.filter((g) => ["done", "ahead", "on track"].includes(g.status)).length}/${gs.length}`, `${gs.filter((g) => g.status === "behind").length} behind`, gs.some((g) => g.status === "behind") ? "dn" : "up")}
      </div>
      <div class="acd-cols"><div class="acd-main"><div class="wl-goals">${gs.map(card).join("") || '<div class="acd-empty">No savings goals yet — add your first one on the right (car, apartment, trip, emergency fund…).</div>'}</div></div>
      <div class="acd-side">${P(1, "NEW SAVINGS GOAL", `<div class="wl-form"><input type="hidden" id="sg-id"/>
          <label>Name<input id="sg-name" placeholder="Car, apartment, trip, emergency fund…"/></label>
          <div class="wl-2"><label>Target ₪<input id="sg-target" type="number" step="any" placeholder="60000"/></label><label>By (month)<input id="sg-dl" type="month"/></label></div>
          <label>Already saved ₪ (optional)<input id="sg-saved" type="number" step="any" placeholder="0"/></label>
          <div id="sg-hint" class="acd-note"></div>
          <div class="wl-btns"><button class="btn" id="sg-save">SAVE GOAL</button><button class="btn btn-small" id="sg-clear">CLEAR</button></div></div>
          <p class="acd-note">Goals are earmarks: money isn't moved anywhere. Put in what you saved and the page tells you if you're on track.</p>`)}</div></div>`;
    let emo = null;
    const pick = (e) => { emo = e; el.querySelectorAll("[data-emo]").forEach((b) => b.classList.toggle("on", b.dataset.emo === e)); };
    const hint = () => {  // live: how much per month this goal needs
      const t = +$("#sg-target").value - (+$("#sg-saved").value || 0), dl = $("#sg-dl").value;
      if (t > 0 && dl) { const mo = Math.max(0.5, (new Date(dl + "-28") - new Date()) / (30.44 * 864e5)); $("#sg-hint").innerHTML = `That's <b class="warn">${ils(t / mo)}/month</b> for ${mo.toFixed(0)} months.`; } else $("#sg-hint").textContent = "";
    };
    const fill = (g) => { $("#sg-id").value = g.id || ""; $("#sg-name").value = g.name || ""; $("#sg-target").value = g.target || ""; $("#sg-dl").value = (g.deadline || "").slice(0, 7); $("#sg-saved").value = ""; $("#sg-saved").disabled = !!g.id; pick(g.emoji || null); $("#sg-save").textContent = g.id ? "UPDATE GOAL" : "SAVE GOAL"; hint(); };
    el.querySelectorAll("[data-emo]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.emo)));
    ["#sg-target", "#sg-dl", "#sg-saved"].forEach((s) => $(s).addEventListener("input", hint));
    $("#sg-clear").addEventListener("click", () => fill({}));
    $("#sg-save").addEventListener("click", async () => {
      const dl = $("#sg-dl").value;
      try { await call("/goals", { id: +$("#sg-id").value || null, name: $("#sg-name").value, emoji: emo, target: +$("#sg-target").value, deadline: dl ? `${dl}-28` : null, saved: +$("#sg-saved").value || null }); showToast("Goal saved"); render(); } catch (e) { showToast(e.message); }
    });
    el.querySelectorAll("[data-gedit]").forEach((b) => b.addEventListener("click", () => { fill(JSON.parse(b.dataset.gedit)); $("#sg-name").focus(); }));
    el.querySelectorAll("[data-gdel]").forEach((b) => b.addEventListener("click", async () => { if (confirm("Delete this goal?")) { await call(`/goals/${b.dataset.gdel}`, undefined, "DELETE"); render(); } }));
    const add = async (card, amount) => {
      if (!amount) return showToast("Enter an amount");
      const before = +card.querySelector(".wl-gpct").textContent.replace(/[^\d.]/g, "");
      const g = await call(`/goals/${card.dataset.gid}/move`, { amount, note: amount > 0 ? "deposit" : "withdrawal" });
      showToast(`${amount > 0 ? "+" : ""}${ils(amount)} → ${g.name}: ${g.pct}%`);
      if (before < 100 && g.pct >= 100) { burst(card); setTimeout(render, 700); } else render();
    };
    el.querySelectorAll("[data-gadd]").forEach((b) => b.addEventListener("click", () => add(b.closest("[data-gid]"), +b.dataset.gadd)));
    el.querySelectorAll("[data-gcustom]").forEach((b) => b.addEventListener("click", () => { const c = b.closest("[data-gid]"); add(c, +b.dataset.gcustom * Math.abs(+c.querySelector(".wl-gamt").value)); }));
  }

  // ------------------------------------------------------------------ BUSINESS › ROAD TO ₪1M (daily plan)
  async function secRoad(el) {
    const o = await call(`/plan${W.planDay ? `?day=${W.planDay}` : ""}`);
    const st = o.settings, R = o.revenue, day = o.day, isToday = day === o.today;
    const dd = new Date(day + "T12:00:00"), dayLbl = isToday ? "TODAY" : dd.toLocaleDateString("en", { weekday: "short", day: "numeric", month: "short" }).toUpperCase();
    const ring = (p) => { const C = 2 * Math.PI * 30; return `<svg viewBox="0 0 76 76" class="wl-ring"><circle cx="38" cy="38" r="30" class="bg"/><circle cx="38" cy="38" r="30" class="fg ${p >= 100 ? "full" : ""}" stroke-dasharray="${(C * p) / 100} 999" transform="rotate(-90 38 38)"/><text x="38" y="43" text-anchor="middle">${p}%</text></svg>`; };
    const box = (t) => `<label class="wl-task ${t.done ? "on" : ""}" data-tick="${t.id}"><span class="wl-cbx">${t.done ? "✓" : ""}</span><span class="wl-tt">${esc(t.title)}</span>${t.minutes ? `<small class="dim">${t.minutes}m</small>` : ""}${t.category ? `<em class="wl-tc c-${esc(t.category)}">${esc(t.category)}</em>` : ""}</label>`;
    const cur = o.phases[o.phase], mins = o.daily.reduce((s, t) => s + (t.minutes || 0), 0);
    const startD = new Date(st.start + "T12:00:00"), endD = new Date(st.deadline + "T12:00:00"), span = Math.max(1, (endD - startD) / 864e5);
    const series = R.series.map((s) => ({ ...s, plan: Math.round((st.target * Math.max(0, (new Date(s.day + "T12:00:00") - startD) / 864e5)) / span) }));
    el.innerHTML = `<div class="wl-road">
        <div class="wl-rhero"><div class="wl-rh-t"><b>ROAD TO ${ils(o.target)}</b><span class="dim">revenue since ${esc(st.start)} · phase ${o.phase + 1}/6 <b class="warn">${esc(cur.name)}</b></span></div>
          <div class="wl-rh-n"><b class="uix-count">${ils(o.total)}</b><span>${o.pct}%</span></div>
          <div class="wl-rbar">${o.phases.map((p) => `<div class="wl-rseg ${p.state}" style="flex:1" data-uix-tip="PHASE ${p.i + 1} · ${esc(p.name)}|${ils(p.from)} → ${ils(p.to)}|${p.state === "done" ? "done ✓" : p.state === "current" ? `${p.pct}% of this phase` : "ahead"}|${p.tasks_done}/${p.tasks.length} milestones"><i style="width:${p.state === "done" ? 100 : p.state === "current" ? p.pct : 0}%"></i><span>${esc(p.name)}</span></div>`).join("")}</div>
          <p class="wl-rfocus">▸ ${esc(cur.focus)}</p></div>
        <div class="acd-kpis wl-kpis">
          ${kpi("STILL TO GO", ils(o.remaining), `${o.days_left} days to ${esc(st.deadline)}`)}
          ${kpi("NEEDED / DAY", ils(o.need_per_day), `${ils(o.need_per_month)} / month`, "warn", "revenue per day from now on to hit the target by the deadline")}
          ${kpi("YOUR PACE", ils(o.pace_per_day) + "/day", "last 30 days", o.pace_per_day >= o.need_per_day ? "up" : "dn")}
          ${kpi("AT THIS PACE", o.eta || "—", o.eta ? "you reach the target" : "no revenue in the last 30 days yet")}
          ${kpi("STREAK", `${o.streak} days`, `best ${o.best_streak} days`, o.streak ? "up" : "", o.streak_rule)}
        </div>
        <div class="acd-cols"><div class="acd-main">
          ${P(1, `${dayLbl} — DAILY TASKS`, `<div class="wl-dnav"><button class="bb-chip" data-pday="-1">◀</button><input type="date" id="rd-day" value="${day}" max="${o.today}"/><button class="bb-chip" data-pday="1" ${isToday ? "disabled" : ""}>▶</button>${isToday ? "" : '<button class="bb-chip" data-pday="0">TODAY</button>'}<span class="sp"></span><span class="dim">≈ ${Math.round(mins / 6) / 10} h of work</span></div>
            <div class="wl-today"><div id="rd-ring">${ring(o.day_stats.pct)}</div><div class="wl-tasks" id="rd-daily">${o.daily.map(box).join("")}</div></div>
            <div class="wl-alldone ${o.day_stats.pct >= 100 ? "show" : ""}" id="rd-done">Every task done${isToday ? " today" : ""} — that's how ${ils(o.target)} gets built.</div>`, `${o.day_stats.done}/${o.day_stats.total}`)}
          ${P(2, `THIS WEEK — from ${esc(o.week_of)}`, `<div class="wl-tasks">${o.weekly.map(box).join("") || '<div class="dim">No weekly tasks.</div>'}</div>`, `${o.weekly.filter((t) => t.done).length}/${o.weekly.length}`)}
          ${P(3, "REVENUE vs THE PLAN", chart(series, ["revenue", "plan"], { labels: ["your revenue", "straight line to the target"], colors: ["#2be07e", "#555"], fmt: ils, h: 190, ranges: ["1M", "3M", "1Y", "ALL"], area: true }) + (R.shopify_error ? `<p class="acd-note dn">Shopify: ${esc(R.shopify_error)}</p>` : ""), `Shopify ${ils(R.shopify)} + manual ${ils(R.manual)}`)}
          ${P(4, "ROADMAP — six phases to the target", `<div class="wl-phases">${o.phases.map((p) => `<details class="wl-phase ${p.state}" ${p.state === "current" ? "open" : ""}><summary><span class="wl-pn">${p.state === "done" ? "✓" : p.i + 1}</span><b>${esc(p.name)}</b><span class="dim">${ils(p.from)} → ${ils(p.to)}</span><span class="sp"></span><small>${p.tasks_done}/${p.tasks.length}</small></summary>
              <p class="dim">${esc(p.focus)}</p><div class="wl-tasks">${p.tasks.map(box).join("")}</div></details>`).join("")}</div>`)}
        </div><div class="acd-side">
          ${P(5, "CONSISTENCY — last 16 weeks", `<div class="wl-heatc">${o.heat.map((h) => `<i class="${h.pct >= 100 ? "l4" : h.pct >= 80 ? "l3" : h.pct >= 50 ? "l2" : h.pct > 0 ? "l1" : ""} ${h.day === day ? "sel" : ""}" data-hday="${h.day}" data-uix-tip="${h.day}|${h.pct}% of the daily tasks|click to open this day"></i>`).join("")}</div><div class="wl-heat-l"><span>16 weeks ago</span><span>today</span></div>`, "click a day")}
          ${P(6, "ADD REVENUE BY HAND", `<div class="wl-form"><div class="wl-2"><label>Date<input id="rv-day" type="date" value="${o.today}"/></label><label>Amount ₪<input id="rv-amt" type="number" step="any"/></label></div><label>Source<input id="rv-src" placeholder="Etsy, wholesale, service, second store…"/></label><button class="btn btn-small" id="rv-go">ADD REVENUE</button></div>
            ${R.entries.length ? `<table class="bb-tbl wl-tbl"><tbody>${R.entries.slice(0, 8).map((e) => `<tr><td class="dim">${esc(e.day)}</td><td>${esc(e.source || "")}</td><td class="r up">${ils(e.amount)}</td><td><button class="tu-cam" data-rvdel="${e.id}">✕</button></td></tr>`).join("")}</tbody></table>` : ""}
            <p class="acd-note">Shopify sales count automatically${st.shopify ? "" : " (switched off below)"}. Add anything else you sell here.</p>`)}
          ${P(7, "MY TASKS", `<div class="wl-form"><label>New task<input id="tk-title" placeholder="e.g. Call 3 suppliers"/></label><div class="wl-2"><label>How often<select id="tk-freq"><option value="daily">every day</option><option value="weekly">every week</option><option value="once">once (milestone)</option></select></label><label>Phase<select id="tk-ph">${o.phases.map((p) => `<option value="${p.i}" ${p.i === o.phase ? "selected" : ""}>${p.i + 1}. ${esc(p.name)}</option>`).join("")}</select></label></div><button class="btn btn-small" id="tk-go">ADD TASK</button></div>
            <details><summary class="dim">remove tasks</summary>${[...o.daily, ...o.weekly].map((t) => `<div class="wl-trm"><span>${esc(t.title)}</span><small class="dim">${t.freq}</small><button class="tu-cam" data-tkoff="${t.id}" title="remove">✕</button></div>`).join("")}</details>`)}
          ${P(8, "PLAN SETTINGS", `<div class="wl-form"><div class="wl-2"><label>Target ₪<input id="ps-t" type="number" value="${st.target}"/></label><label>Deadline<input id="ps-d" type="date" value="${esc(st.deadline)}"/></label></div>
            <label class="wl-chk"><input type="checkbox" id="ps-sh" ${st.shopify ? "checked" : ""}/> count Shopify sales</label>
            <div class="wl-2"><label class="wl-chk"><input type="checkbox" id="ps-tg" ${st.telegram ? "checked" : ""}/> checklist on Telegram</label><label>every day at<select id="ps-h">${[6, 7, 8, 9, 10].map((h) => `<option ${+st.tg_hour === h ? "selected" : ""} value="${h}">${h}:00</option>`).join("")}</select></label></div>
            <div class="wl-btns"><button class="btn btn-small" id="ps-go">SAVE</button><button class="btn btn-small" id="ps-send">SEND TODAY'S CHECKLIST TO TELEGRAM</button></div></div>
            <p class="acd-note">Every morning the bot sends today's tasks with a button for each — tap it when done. At 21:00 it reminds you if less than 80% is done.</p>`)}
        </div></div></div>`;
    // tick a task: instant feedback, then sync the ring / counters with the server
    el.querySelectorAll("[data-tick]").forEach((lab) => lab.addEventListener("click", async (e) => {
      e.preventDefault();
      const on = !lab.classList.contains("on");
      lab.classList.toggle("on", on); lab.querySelector(".wl-cbx").textContent = on ? "✓" : "";
      if (on) lab.classList.add("pop"), setTimeout(() => lab.classList.remove("pop"), 400);
      try {
        const r = await call("/plan/tick", { task_id: +lab.dataset.tick, day, done: on });
        const s = r.today; $("#rd-ring").innerHTML = ring(s.pct);
        if (isToday) badge("biz", `${o.pct}% of ${ils(o.target)} · ${s.done}/${s.total} today · streak ${o.streak}`);
        const cell = el.querySelector(`[data-hday="${s.day}"]`); if (cell) cell.className = `${s.pct >= 100 ? "l4" : s.pct >= 80 ? "l3" : s.pct >= 50 ? "l2" : s.pct > 0 ? "l1" : ""} sel`;
        const done = $("#rd-done"), was = done.classList.contains("show"); done.classList.toggle("show", s.pct >= 100);
        if (!was && s.pct >= 100) burst($("#rd-ring"));
      } catch (err) { lab.classList.toggle("on", !on); showToast(err.message); }
    }));
    const goDay = (d) => { W.planDay = d && d !== o.today ? d : null; W.active = "road"; render(); };
    el.querySelectorAll("[data-pday]").forEach((b) => b.addEventListener("click", () => { const n = +b.dataset.pday; if (!n) return goDay(null); const x = new Date(day + "T12:00:00"); x.setDate(x.getDate() + n); goDay(x.toISOString().slice(0, 10)); }));
    $("#rd-day").addEventListener("change", (e) => goDay(e.target.value));
    el.querySelectorAll("[data-hday]").forEach((c) => c.addEventListener("click", () => goDay(c.dataset.hday)));
    $("#rv-go").addEventListener("click", async () => { try { await call("/plan/revenue", { day: $("#rv-day").value, amount: +$("#rv-amt").value, source: $("#rv-src").value || null }); showToast("Revenue added"); render(); } catch (e2) { showToast(e2.message); } });
    el.querySelectorAll("[data-rvdel]").forEach((b) => b.addEventListener("click", async () => { await call(`/plan/revenue/${b.dataset.rvdel}`, undefined, "DELETE"); render(); }));
    $("#tk-freq").addEventListener("change", () => { $("#tk-ph").disabled = $("#tk-freq").value !== "once"; }); $("#tk-ph").disabled = true;
    $("#tk-go").addEventListener("click", async () => { try { await call("/plan/task", { title: $("#tk-title").value, freq: $("#tk-freq").value, phase: $("#tk-freq").value === "once" ? +$("#tk-ph").value : null }); showToast("Task added"); render(); } catch (e2) { showToast(e2.message); } });
    el.querySelectorAll("[data-tkoff]").forEach((b) => b.addEventListener("click", async () => { await call(`/plan/task/${b.dataset.tkoff}`, { active: false }); render(); }));
    $("#ps-go").addEventListener("click", async () => { await call("/plan/settings", { target: +$("#ps-t").value, deadline: $("#ps-d").value, shopify: $("#ps-sh").checked, telegram: $("#ps-tg").checked, tg_hour: +$("#ps-h").value }); showToast("Plan saved"); render(); });
    $("#ps-send").addEventListener("click", async () => { await call("/plan/telegram", {}); showToast("Checklist sent to Telegram"); });
    badge("biz", `${o.pct}% of ${ils(o.target)} · ${o.day_stats.done}/${o.day_stats.total} today · streak ${o.streak}`);
  }

  // ------------------------------------------------------------------ NET WORTH
  async function tabNW(el) {
    const [nw, hist] = await Promise.all([call("/networth"), call("/history")]);
    W.fx = nw.usdils || W.fx;
    const ch = nw.change || {};
    const parts = nw.by_category.filter((c) => c.usd > 0).map((c) => ({ l: c.label, v: c.usd, c: CAT_COL[c.category] || "#888" }));
    const cats = nw.categories || {};
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("NET WORTH", m(nw.total_usd), W.cur === "ILS" ? `$${Math.round(nw.total_usd).toLocaleString()}` : ils(nw.total_ils), "", "everything you own minus what you owe")}
        ${kpi("TODAY", sm(nw.day_pnl_usd), "market moves of priced assets", cls(nw.day_pnl_usd))}
        ${kpi("7 DAYS", ch["7d"] ? sm(ch["7d"].usd) : "—", ch["7d"] ? pct(ch["7d"].pct, 2) : "needs 7 days of snapshots", ch["7d"] ? cls(ch["7d"].usd) : "")}
        ${kpi("30 DAYS", ch["30d"] ? sm(ch["30d"].usd) : "—", ch["30d"] ? pct(ch["30d"].pct, 2) : "needs 30 days of snapshots", ch["30d"] ? cls(ch["30d"].usd) : "")}
        ${kpi("LIQUID", m(nw.liquid_usd), `${nw.assets_usd ? Math.round(100 * nw.liquid_usd / nw.assets_usd) : 0}% of assets`, "", "money you could reach within days")}
        ${kpi("DEBTS", m(nw.debts_usd), "loans, mortgage, credit", nw.debts_usd ? "dn" : "")}
        ${kpi("USD/ILS", nw.usdils.toFixed(3), "live rate")}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "NET WORTH OVER TIME", chart(hist.history.map((h) => ({ day: h.day, total: h.total_usd })), ["total"], { labels: ["net worth"] }), `${hist.history.length} daily snapshots`)}
        ${P(2, "EVERYTHING YOU OWN", `<table class="bb-tbl uix-sort wl-tbl"><thead><tr><th>NAME</th><th>CATEGORY</th><th>SOURCE</th><th class="r">VALUE</th><th class="r">WEIGHT</th><th class="r">P&amp;L</th><th></th></tr></thead><tbody>
          ${nw.items.map((i) => `<tr><td class="sym">${esc(i.name)}</td><td><i class="wl-dot" style="background:${CAT_COL[i.category] || "#888"}"></i>${esc(cats[i.category] || i.category)}</td><td class="dim">${esc(i.source)}${i.currency && i.currency !== "USD" ? ` · ${esc(i.currency)} ${Number(i.native).toLocaleString()}` : ""}</td>
            <td class="r ${i.usd < 0 ? "dn" : ""}" data-v="${i.usd}">${m(i.usd)}</td><td class="r" data-v="${i.weight}">${i.weight ? i.weight.toFixed(1) + "%" : "—"}</td><td class="r ${cls(i.pnl)}">${isN(i.pnl) ? sm(i.pnl) : ""}</td>
            <td>${i.source === "manual" ? `<button class="bb-chip wl-edit" data-edit='${esc(JSON.stringify({ id: i.id, name: i.name, category: i.category, currency: i.currency, value: i.native, notes: i.notes }))}'>EDIT</button>` : ""}</td></tr>`).join("")}</tbody></table>
          <p class="acd-note">Portfolio holdings, the real Polymarket balance and Tradovate cash update automatically. Add bank accounts, pension, property, a business or debts below.</p>`)}
      </div><div class="acd-side">
        ${P(3, "ALLOCATION", `<div class="wl-alloc">${donut(parts)}<div class="wl-leg">${nw.by_category.map((c) => `<div data-hl="${esc(c.label)}" data-uix-tip="${esc(c.label)}|${m(c.usd)}"><i style="background:${CAT_COL[c.category] || "#888"}"></i><span>${esc(c.label)}</span><b>${c.pct ? c.pct.toFixed(1) + "%" : m(c.usd)}</b></div>`).join("")}</div></div>`)}
        ${P(4, "ADD / EDIT AN ASSET", `<div class="wl-form" id="wl-aform">
          <input type="hidden" id="wa-id"/><label>Name<input id="wa-name" placeholder="Leumi checking, Harel pension…"/></label>
          <label>Category<select id="wa-cat">${Object.entries(cats).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
          <div class="wl-2"><label>Value<input id="wa-val" type="number" step="any" placeholder="0"/></label><label>Currency<select id="wa-cur"><option>ILS</option><option>USD</option><option>EUR</option></select></label></div>
          <label>Notes<input id="wa-notes" placeholder="optional"/></label>
          <div class="wl-btns"><button class="btn" id="wa-save">SAVE</button><button class="btn btn-small" id="wa-del" style="display:none">DELETE</button><button class="btn btn-small" id="wa-clear">CLEAR</button></div></div>`)}
      </div></div>`;
    const fill = (a) => { $("#wa-id").value = a.id || ""; $("#wa-name").value = a.name || ""; $("#wa-cat").value = a.category || "bank"; $("#wa-val").value = isN(a.value) ? Math.abs(a.value) : ""; $("#wa-cur").value = a.currency || "ILS"; $("#wa-notes").value = a.notes || ""; $("#wa-del").style.display = a.id ? "" : "none"; };
    el.querySelectorAll(".wl-edit").forEach((b) => b.addEventListener("click", () => { fill(JSON.parse(b.dataset.edit)); $("#wa-name").focus(); }));
    $("#wa-clear").addEventListener("click", () => fill({}));
    $("#wa-save").addEventListener("click", async () => {
      try { await call("/assets", { id: Number($("#wa-id").value) || null, name: $("#wa-name").value, category: $("#wa-cat").value, value: Number($("#wa-val").value), currency: $("#wa-cur").value, notes: $("#wa-notes").value }); showToast("Saved"); render(); } catch (e) { showToast(e.message); }
    });
    $("#wa-del").addEventListener("click", async () => { if (!confirm("Delete this asset?")) return; await call(`/assets/${$("#wa-id").value}`, undefined, "DELETE"); render(); });
    // hover a legend row → its donut slice pops out (and the other way round)
    const hl = (lbl) => { el.querySelectorAll("[data-seg]").forEach((s) => s.classList.toggle("hot", s.dataset.seg === lbl)); el.querySelectorAll("[data-hl]").forEach((s) => s.classList.toggle("hot", s.dataset.hl === lbl)); el.querySelector(".wl-donut")?.classList.toggle("dimmed", !!lbl); };
    el.querySelectorAll("[data-hl]").forEach((r) => { r.addEventListener("mouseenter", () => hl(r.dataset.hl)); r.addEventListener("mouseleave", () => hl(null)); });
    el.querySelectorAll("[data-seg]").forEach((r) => { r.addEventListener("mouseenter", () => hl(r.dataset.seg)); r.addEventListener("mouseleave", () => hl(null)); });
    badge("money", `${m(nw.total_usd)}`);
  }

  // ------------------------------------------------------------------ CASH FLOW
  async function tabCash(el) {
    const c = await call("/cashflow");
    const mb = c.main_balance, s = c.stocks_mtd;
    const net = c.recurring_net_month;
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("MAIN BALANCE", ils(mb.value), `updated ${esc(String(mb.updated_at || "").slice(0, 16))}`, "", "moved automatically by your salary, card bills and the monthly credit-card answer")}
        ${kpi("MONTHLY FIXED NET", ils(net), `${c.recurring.filter((r) => r.active).length} recurring items`, cls(net))}
        ${kpi("STOCKS THIS MONTH", (s.pnl_ils >= 0 ? "+" : "") + ils(s.pnl_ils), `${s.month} · $${Math.round(s.pnl_usd).toLocaleString()}`, cls(s.pnl_ils), "month-to-date gain of your portfolio holdings")}
        ${kpi("CARD SPEND (LAST ANSWER)", c.cc_history[0] ? ils(-c.cc_history[0].amount) : "—", c.cc_history[0] ? c.cc_history[0].month : "asked on Telegram every 1st", "dn")}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "EVERY MONTH — booked automatically on its day, moves the main balance", `<table class="bb-tbl wl-tbl"><thead><tr><th>ITEM</th><th class="r">AMOUNT</th><th class="r">DAY</th><th>CATEGORY</th><th>LAST BOOKED</th><th></th></tr></thead><tbody>
          ${c.recurring.map((r) => `<tr class="${r.active ? "" : "dim"}"><td class="sym">${esc(r.name)}</td><td class="r ${cls(r.amount)}">${r.amount > 0 ? "+" : ""}${ils(r.amount)}</td><td class="r">${r.day}</td><td class="dim">${esc(r.category || "")}</td><td class="dim">${esc(r.last_posted || "not yet")}</td>
            <td><button class="bb-chip" data-rec='${esc(JSON.stringify(r))}'>EDIT</button> <button class="tu-cam" data-recdel="${r.id}" title="delete">✕</button></td></tr>`).join("")}</tbody></table>
          <div class="wl-form wl-recf"><input type="hidden" id="rc-id"/><div class="wl-2"><label>Name<input id="rc-name" placeholder="Rent, Netflix, bonus…"/></label><label>Amount ₪ (+ income, − expense)<input id="rc-amt" type="number" step="any"/></label></div>
            <div class="wl-2"><label>Day of month<input id="rc-day" type="number" min="1" max="28" value="1"/></label><label>Category<input id="rc-cat" placeholder="Income / Credit card / Housing…"/></label></div>
            <div class="wl-btns"><button class="btn" id="rc-save">SAVE ITEM</button><button class="btn btn-small" id="rc-clear">CLEAR</button></div></div>`)}
        ${P(2, "STOCKS THIS MONTH", `<table class="bb-tbl wl-tbl"><tbody>${s.items.map((i) => `<tr><td class="sym">${esc(i.symbol)}</td><td class="r ${cls(i.pnl_usd)}">${sm(i.pnl_usd)}</td><td class="r ${cls(i.pct)}">${pct(i.pct, 2)}</td></tr>`).join("")}</tbody></table><p class="acd-note">Month-to-date: price now vs the last close of last month (or vs cost for positions opened this month).</p>`)}
      </div><div class="acd-side">
        ${P(3, "MAIN BALANCE", `<div class="wl-form"><label>Set the real balance (₪)<input id="mb-val" type="number" step="any" value="${mb.value}"/></label><button class="btn" id="mb-save">SET BALANCE</button></div>
          <p class="acd-note">Set it once to what your bank shows; after that salary, card bills and credit-card answers move it by themselves.</p>`)}
        ${P(4, "CREDIT CARD — asked on Telegram every 1st", `<p class="acd-note">On the 1st at 09:00 the bot asks how much you spent on credit cards last month. Reply with the number; it is booked as an expense and taken off the main balance. ${c.cc_pending ? `<b class="warn">Waiting for your answer for ${esc(c.cc_pending.month)}.</b>` : ""}</p>
          <div class="wl-form"><label>…or answer here (₪)<input id="cc-amt" type="number" step="any" placeholder="3450"/></label><div class="wl-btns"><button class="btn" id="cc-save">BOOK CARD SPEND</button><button class="btn btn-small" id="cc-ask">ASK ME ON TELEGRAM NOW</button></div></div>
          <table class="bb-tbl wl-tbl"><tbody>${c.cc_history.map((h) => `<tr><td>${esc(h.month)}</td><td class="r dn">${ils(-h.amount)}</td></tr>`).join("")}</tbody></table>
          <p class="acd-note">You can also send <b>/balance</b> to the bot any time.</p>`)}
      </div></div>`;
    const fill = (r) => { $("#rc-id").value = r.id || ""; $("#rc-name").value = r.name || ""; $("#rc-amt").value = r.amount ?? ""; $("#rc-day").value = r.day || 1; $("#rc-cat").value = r.category || ""; };
    el.querySelectorAll("[data-rec]").forEach((b) => b.addEventListener("click", () => fill(JSON.parse(b.dataset.rec))));
    el.querySelectorAll("[data-recdel]").forEach((b) => b.addEventListener("click", async () => { if (confirm("Delete this monthly item?")) { await call(`/cashflow/recurring/${b.dataset.recdel}`, undefined, "DELETE"); render(); } }));
    $("#rc-clear").addEventListener("click", () => fill({}));
    $("#rc-save").addEventListener("click", async () => { try { await call("/cashflow/recurring", { id: Number($("#rc-id").value) || null, name: $("#rc-name").value, amount: +$("#rc-amt").value, day: +$("#rc-day").value, category: $("#rc-cat").value || null }); showToast("Saved"); render(); } catch (e) { showToast(e.message); } });
    $("#mb-save").addEventListener("click", async () => { await call("/cashflow/balance", { value: +$("#mb-val").value }); showToast("Balance set"); render(); });
    $("#cc-save").addEventListener("click", async () => { const r = await call("/cashflow/cc", { amount: +$("#cc-amt").value }); showToast(r.reply || "Booked"); render(); });
    $("#cc-ask").addEventListener("click", async () => { const r = await call("/cashflow/ask-cc", {}); showToast(`Asked on Telegram about ${r.asked}`); render(); });
  }

  // ------------------------------------------------------------------ SHOPIFY
  async function tabShop(el) {
    const d = await call("/shopify?days=90");
    const st = d.status, s = d.summary || {};
    if (!st.connected) {
      el.innerHTML = `<div class="acd-cols"><div class="acd-main">${P(1, "CONNECT YOUR SHOPIFY STORE (read-only)", `<div class="pm-help"><b>The "Jordan Terminal" app is already created and installed on זוהר.</b> Copy its two credentials:<ol>
          <li>Open <a href="https://dev.shopify.com/dashboard" target="_blank" rel="noopener noreferrer">Shopify Dev Dashboard ▸</a> → <b>Jordan Terminal</b> → <b>Overview</b>.</li>
          <li>Under <b>Credentials</b>: copy the <b>Client ID</b> and the <b>Client secret</b> (click the copy icon next to each) and paste them below.</li></ol>
          <span class="dim">Older stores with a legacy custom app can paste an Admin API token (shpat_…) instead.</span></div>
        <div class="wl-form"><label>Store domain<input id="sh-shop" value="a58ptm-rb.myshopify.com"/></label>
          <label>Client ID<input id="sh-cid" autocomplete="off" placeholder="from Dev Dashboard › Credentials"/></label>
          <label>Client secret<input id="sh-sec" type="password" autocomplete="off" placeholder="from Dev Dashboard › Credentials"/></label>
          <details><summary class="dim">…or a legacy Admin API token</summary><label>Admin API access token<input id="sh-tok" type="password" autocomplete="off" placeholder="shpat_…"/></label></details>
          <button class="btn" id="sh-go">CONNECT</button></div>
        <p class="acd-note">Read-only: the terminal only reads orders and products. Credentials are stored encrypted on this computer and never shown again; the access token renews itself every day.</p>`)}</div></div>`;
      $("#sh-go").addEventListener("click", async () => { try { await call("/shopify/connect", { shop: $("#sh-shop").value, client_id: $("#sh-cid").value || null, client_secret: $("#sh-sec").value || null, token: $("#sh-tok").value || null }); showToast("Shopify connected"); render(); } catch (e) { showToast(e.message); } });
      return;
    }
    if (s.error) { el.innerHTML = P(1, `SHOPIFY · ${esc(st.name || st.shop)}`, `<div class="dn">${esc(s.error)}</div><button class="btn btn-small" id="sh-off">DISCONNECT</button>`); $("#sh-off").addEventListener("click", async () => { await call("/shopify/disconnect", {}); render(); }); return; }
    const cur = s.currency || "", money = (v) => (isN(v) ? `${Math.round(v).toLocaleString()} ${cur}` : "—");
    const vs = s.prev_month?.net ? (100 * (s.mtd.net / s.prev_month.net - 1)) : null;
    const now = new Date(), dim = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate(), proj = (s.mtd.net / now.getDate()) * dim;
    // cumulative this month vs last month, day by day
    const ym = now.toISOString().slice(0, 7), pm = new Date(now.getFullYear(), now.getMonth() - 1, 15).toISOString().slice(0, 7);
    const cum = []; let a = 0, b = 0;
    for (let d = 1; d <= 31; d++) {
      const dd = String(d).padStart(2, "0"), t = s.series.find((x) => x.day === `${ym}-${dd}`), p = s.series.find((x) => x.day === `${pm}-${dd}`);
      a += t ? t.net : 0; b += p ? p.net : 0;
      cum.push({ d: `day ${d}`, this: d <= now.getDate() ? Math.round(a) : null, last: p || d <= 28 ? Math.round(b) : null });
    }
    const T = s.totals || {}, WD = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], wd = s.by_weekday || [];
    const bestWd = wd.length ? wd.map((x, i) => [i, x.sales]).sort((x, y) => y[1] - x[1])[0] : null;
    const hr = s.by_hour || [], hmx = Math.max(1, ...hr.map((x) => x.sales));
    const totP = s.top_products.reduce((q, p) => q + p.sales, 0) || 1;
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("TODAY", money(s.today.net), `${s.today.orders} orders`, "up")}
        ${kpi("LAST 7 DAYS", money(s.d7.net), `${s.d7.orders} orders`)}
        ${kpi("THIS MONTH", money(s.mtd.net), `${s.mtd.orders} orders · AOV ${money(s.mtd.aov)}`, "up", "net = paid by customers minus refunds")}
        ${kpi("PROJECTED MONTH", money(proj), `at today's pace${isN(vs) ? ` · MTD vs last month ${pct(vs)}` : ""}`, proj >= (s.prev_month.net || 0) ? "up" : "dn", "this month so far, extended to the full month")}
        ${kpi("LAST MONTH", money(s.prev_month.net), `${s.prev_month.orders} orders`)}
        ${kpi("90 DAYS", money(s.d90?.net), `${s.d90?.orders ?? 0} orders · refunds ${money(s.d90?.refunds)}`)}
        ${kpi("UNITS SOLD", isN(T.units) ? T.units.toLocaleString() : "—", "since " + esc(s.window_start || ""))}
        ${kpi("DISCOUNTS GIVEN", money(T.discounts), `tax collected ${money(T.tax)}`, T.discounts ? "dn" : "")}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, `DAILY SALES · ${esc(s.name || s.shop)}`, chart(s.series.map((x) => ({ ...x, refunds: Math.round((x.sales - x.net) * 100) / 100 })), ["sales", "net", "refunds"], { labels: ["sales", "net", "refunds"], colors: ["#2be07e", "#ffa028", "#ff4a4a"], fmt: money, ranges: ["7D", "30D", "90D"], range: "30D", off: ["refunds"] }), "hover for each day")}
        ${P(2, "THIS MONTH vs LAST MONTH — running total", chart(cum, ["this", "last"], { labels: ["this month", "last month"], colors: ["#ffa028", "#5aa9ff"], fmt: money, dateKey: "d", ranges: [], area: false, h: 180 }))}
      </div><div class="acd-side">
        ${P(3, "BEST DAYS OF THE WEEK", wd.length ? bars(wd.map((x, i) => ({ label: WD[i], v: x.sales, on: bestWd && bestWd[0] === i, tip: `${WD[i]}|${money(x.sales)} · ${x.orders} orders` }))) : '<div class="dim">—</div>', bestWd && bestWd[1] ? `best: ${WD[bestWd[0]]}` : "")}
        ${P(4, "WHEN PEOPLE BUY — hour of day", hr.length ? `<div class="wl-heat">${hr.map((x, i) => `<i style="opacity:${0.08 + (0.92 * x.sales) / hmx}" data-uix-tip="${String(i).padStart(2, "0")}:00–${String(i + 1).padStart(2, "0")}:00|${money(x.sales)} · ${x.orders} orders"></i>`).join("")}</div><div class="wl-heat-l"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>` : '<div class="dim">—</div>')}
        ${P(5, "TOP PRODUCTS (90 DAYS)", s.top_products.length ? `<div class="wl-cats">${s.top_products.map((p) => `<div data-uix-tip="${esc(p.title)}|${p.qty} sold · ${money(p.sales)}|${((100 * p.sales) / totP).toFixed(1)}% of product sales"><span>${esc(p.title)}</span><div class="wl-cb"><i style="width:${(100 * p.sales) / s.top_products[0].sales}%"></i></div><b>${money(p.sales)}</b></div>`).join("")}</div>` : '<div class="dim">No sales yet in the last 90 days.</div>')}
        ${P(6, "CONNECTION", `<div class="acd-sg"><div class="acd-st"><span>Store</span><b>${esc(st.shop)}</b></div><div class="acd-st"><span>Method</span><b>${esc(st.method || "")}</b></div><div class="acd-st"><span>ID</span><b>${esc(st.token_hint)}</b></div></div><div class="wl-btns"><button class="btn btn-small" id="sh-off">DISCONNECT</button></div>`)}
      </div></div>`;
    $("#sh-off").addEventListener("click", async () => { if (confirm("Disconnect Shopify?")) { await call("/shopify/disconnect", {}); render(); } });
  }

  // ------------------------------------------------------------------ RISK
  async function tabRisk(el) {
    const [r, st] = await Promise.all([call("/risk"), call("/settings")]);
    const mk = r.market;
    const L = st.risk;
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${mk ? kpi("BAD MONTH (1 IN 20)", m(-mk.var95_month_usd), `${mk.var95_month_pct}% of the portfolio`, "dn", "95% monthly Value-at-Risk: a month at least this bad happens about once in 20 months") : kpi("BAD MONTH", "—", "add portfolio holdings")}
        ${mk ? kpi("WORST 5% AVERAGE", m(-mk.cvar95_month_usd), "expected loss in those months", "dn", "CVaR 95%") : ""}
        ${mk ? kpi("VOLATILITY", mk.vol_annual_pct + "%", "per year", mk.vol_annual_pct > 40 ? "dn" : "", "how much the portfolio swings; S&P 500 ≈ 15-18%") : ""}
        ${mk ? kpi("MAX DRAWDOWN 1Y", mk.max_drawdown_1y_pct + "%", "today's mix, last year", "dn") : ""}
        ${mk ? kpi("AVG CORRELATION", mk.avg_correlation, mk.avg_correlation > 0.6 ? "moves as one bet" : "reasonably spread", mk.avg_correlation > 0.6 ? "dn" : "up", "1 = everything moves together") : ""}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "WHAT NEEDS ATTENTION", `<div class="acd-fs">${r.warnings.map((w) => `<div class="acd-f ${w.level}"><b>${esc(w.title)}</b><span>${esc(w.text)}</span></div>`).join("")}</div>`)}
        ${r.correlation ? P(2, "CORRELATION — do your holdings move together?", corrTable(r.correlation)) : ""}
      </div><div class="acd-side">
        ${P(3, "KILL SWITCH", `<p class="acd-note">Stops every real-money trading path at once: disarms the Polymarket engine and sells its open positions. Paper accounts keep running.</p>
          <div class="wl-kill"><input id="wl-kill-in" placeholder="type STOP"/><button class="btn wl-killbtn" id="wl-kill">STOP ALL REAL TRADING</button></div>
          <div class="acd-note">Polymarket now: <b class="${r.real?.polymarket_armed ? "dn" : "up"}">${r.real?.polymarket_armed ? "ARMED" : "disarmed"}</b>${r.real?.polymarket_today ? ` · today ${sm(r.real.polymarket_today.pnl, 2)}` : ""}</div>`)}
        ${P(4, "YOUR LIMITS", `<div class="wl-form">
          <label>Max single position (% of assets)<input id="rl-pos" type="number" value="${L.max_position_pct}"/></label>
          <label>Max crypto (% of assets)<input id="rl-cr" type="number" value="${L.max_crypto_pct}"/></label>
          <label>Alert if net worth falls in a day (%)<input id="rl-day" type="number" step="0.5" value="${L.daily_loss_alert_pct}"/></label>
          <label>Alert if real trading loses in a day ($)<input id="rl-real" type="number" value="${L.real_daily_loss_usd}"/></label>
          <button class="btn" id="rl-save">SAVE LIMITS</button></div><p class="acd-note">Breaches send a Telegram alert (once per day).</p>`)}
      </div></div>`;
    $("#wl-kill").addEventListener("click", async () => { try { const r2 = await call("/kill-switch", { confirm: $("#wl-kill-in").value.trim() }); showToast(r2.done.join(" · ")); render(); } catch (e) { showToast(e.message); } });
    $("#rl-save").addEventListener("click", async () => { await call("/settings", { risk: { max_position_pct: +$("#rl-pos").value, max_crypto_pct: +$("#rl-cr").value, daily_loss_alert_pct: +$("#rl-day").value, real_daily_loss_usd: +$("#rl-real").value } }); showToast("Limits saved"); });
  }
  function corrTable(c) {
    const col = (v) => (v >= 0 ? `rgba(255,74,74,${Math.min(0.85, Math.abs(v))})` : `rgba(43,224,126,${Math.min(0.85, Math.abs(v))})`);
    return `<table class="wl-corr"><thead><tr><th></th>${c.names.map((n) => `<th>${esc(n)}</th>`).join("")}</tr></thead><tbody>${c.matrix.map((row, i) => `<tr><th>${esc(c.names[i])}</th>${row.map((v, j) => `<td style="background:${i === j ? "#111" : col(v)}" data-uix-tip="${esc(c.names[i])} × ${esc(c.names[j])}|correlation ${v}|${v > 0.6 ? "move together" : v < 0 ? "offset each other" : "loosely related"}">${i === j ? "" : v.toFixed(2)}</td>`).join("")}</tr>`).join("")}</tbody></table>
      <p class="acd-note">Red = move together (no diversification), green = offset each other. Based on the last year of daily returns.</p>`;
  }

  // ------------------------------------------------------------------ PERFORMANCE
  async function tabPerf(el) {
    const p = await call("/performance");
    if (!p.lots || !p.lots.length) { el.innerHTML = P(1, "PERFORMANCE", `<div class="acd-empty">Add holdings on CAPITAL › PORTFOLIO to track performance.</div>`); return; }
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("INVESTED", m(p.cost), `since ${esc(p.since)}`)}
        ${kpi("WORTH NOW", m(p.value), pct(p.pnl_pct, 2), cls(p.pnl))}
        ${kpi("P&L", sm(p.pnl), "unrealized", cls(p.pnl))}
        ${kpi("VS S&P 500", sm(p.vs_spy), `same dollars in SPY: ${m(p.spy_value)}`, cls(p.vs_spy), "what you have minus what the same money, invested in the S&P 500 on the same days, would be worth")}
        ${kpi("VS BITCOIN", sm(p.vs_btc), `same dollars in BTC: ${m(p.btc_value)}`, cls(p.vs_btc))}
        ${kpi("EST. TAX IF SOLD", m(p.tax.estimated_usd), "25% of net gains (estimate)", "", p.tax.note)}
      </div>
      ${P(1, "YOU vs THE MARKET — same money, same days", chart(p.curve, ["value", "spy", "cost"], { labels: ["your holdings", "S&P 500 (SPY)", "money put in"], colors: ["#ffa028", "#5aa9ff", "#777"], change: false }), "hover · click the legend")}
      ${P(2, "EVERY LOT", `<table class="bb-tbl uix-sort wl-tbl"><thead><tr><th>SYMBOL</th><th>ADDED</th><th class="r">COST</th><th class="r">VALUE</th><th class="r">P&amp;L</th><th class="r">%</th><th class="r">IN SPY</th><th class="r">VS SPY</th></tr></thead><tbody>
        ${p.lots.map((l) => `<tr><td class="sym">${esc(l.symbol)}</td><td class="dim">${esc(l.added)}</td><td class="r">${m(l.cost)}</td><td class="r">${m(l.value)}</td><td class="r ${cls(l.pnl)}" data-v="${l.pnl}">${sm(l.pnl)}</td><td class="r ${cls(l.pnl_pct)}">${pct(l.pnl_pct)}</td><td class="r dim">${m(l.spy_value)}</td><td class="r ${cls(l.vs_spy)}" data-v="${l.vs_spy ?? ""}">${sm(l.vs_spy)}</td></tr>`).join("")}</tbody></table>
        <p class="acd-note">${esc(p.tax.note)}</p>`)}`;
  }

  // ------------------------------------------------------------------ GOALS
  async function tabGoals(el) {
    const [rb, st] = await Promise.all([call("/rebalance"), call("/settings")]);
    const cats = (await call("/networth")).categories;
    const goals = st.goals || [];
    el.innerHTML = `<div class="acd-cols"><div class="acd-main">
        ${P(1, "TARGET MIX vs TODAY", `<table class="bb-tbl wl-tbl"><thead><tr><th>CATEGORY</th><th class="r">NOW</th><th class="r">NOW %</th><th class="r">TARGET %</th><th>DRIFT</th><th class="r">TO REBALANCE</th></tr></thead><tbody>
          ${rb.rows.map((r) => `<tr class="${r.alert ? "wl-alert" : ""}"><td><i class="wl-dot" style="background:${CAT_COL[r.category] || "#888"}"></i>${esc(r.label)}</td><td class="r">${m(r.usd)}</td><td class="r">${r.pct}%</td>
            <td class="r"><input class="wl-tgt" data-cat="${r.category}" type="number" min="0" max="100" value="${r.target_pct}"/></td>
            <td><div class="wl-drift"><i class="${r.drift > 0 ? "over" : "under"}" style="${r.drift > 0 ? "left:50%" : `left:${50 - Math.min(50, Math.abs(r.drift))}%`};width:${Math.min(50, Math.abs(r.drift))}%"></i></div><small class="${r.alert ? "dn" : "dim"}">${r.drift > 0 ? "+" : ""}${r.drift} pts</small></td>
            <td class="r ${cls(r.action_usd)}">${Math.abs(r.action_usd) < 1 ? "—" : (r.action_usd > 0 ? "add " : "reduce ") + m(Math.abs(r.action_usd))}</td></tr>`).join("")}
          <tr><td>add a category</td><td colspan="5"><select id="wl-addcat">${Object.entries(cats).filter(([k]) => k !== "debt" && !rb.rows.some((r) => r.category === k)).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select> <button class="bb-chip" id="wl-addcat-go">+ ADD</button></td></tr></tbody></table>
          <div class="wl-btns"><button class="btn" id="wl-tgt-save">SAVE TARGETS</button><span class="dim">alert when a category drifts more than <input id="wl-drift" type="number" value="${rb.drift_alert_pct}" style="width:48px"/> points</span></div>
          <p class="acd-note">${esc(rb.note)}</p>`)}
      </div><div class="acd-side">
        ${P(2, "WILL I REACH MY GOAL?", `<div class="wl-form">
          <label>Goal<input id="g-name" value="${esc(goals[0]?.name || "Financial freedom")}"/></label>
          <label>Target (${W.cur})<input id="g-target" type="number" value="${goals[0] ? Math.round((W.cur === "ILS" ? W.fx : 1) * goals[0].target_usd) : W.cur === "ILS" ? 2000000 : 500000}"/></label>
          <label class="wl-rl">Years <b id="g-years-v">${goals[0]?.years || 10}</b><input id="g-years" type="range" min="1" max="40" value="${goals[0]?.years || 10}"/></label>
          <label class="wl-rl">Monthly contribution <b id="g-monthly-v"></b><input id="g-monthly" type="range" min="0" max="${W.cur === "ILS" ? 30000 : 8000}" step="${W.cur === "ILS" ? 100 : 25}" value="${goals[0] ? Math.round((W.cur === "ILS" ? W.fx : 1) * goals[0].monthly_usd) : W.cur === "ILS" ? 3000 : 800}"/></label>
          <button class="btn" id="g-run">RUN 4,000 FUTURES</button><span class="dim">drag a slider: it recalculates by itself</span></div><div id="g-out"></div>`)}
      </div></div>`;
    const conv = (v) => (W.cur === "ILS" ? v / W.fx : v);
    const run = async () => {
      const goal = { name: $("#g-name").value, target_usd: conv(+$("#g-target").value), monthly_usd: conv(+$("#g-monthly").value), years: +$("#g-years").value };
      const r = await call("/project", goal);
      await call("/settings", { goals: [goal] });
      const mx = Math.max(...r.yearly.map((y) => y.p90), goal.target_usd);
      $("#g-out").innerHTML = `<div class="wl-goal"><div class="wl-prob ${r.probability >= 70 ? "up" : r.probability >= 40 ? "" : "dn"}"><b class="uix-count">${r.probability}%</b><span>chance to reach ${m(goal.target_usd)} in ${r.years} years${r.median_months ? ` · typically in ${(r.median_months / 12).toFixed(1)} years` : ""}</span></div>
        <div class="wl-fan">${r.yearly.map((y) => `<div data-uix-tip="Year ${y.year}|good (90%) ${m(y.p90)}|median ${m(y.p50)}|bad (10%) ${m(y.p10)}"><i class="b" style="bottom:${(100 * y.p10) / mx}%;height:${(100 * (y.p90 - y.p10)) / mx}%"></i><i class="md" style="bottom:${(100 * y.p50) / mx}%"></i><span>${y.year}</span></div>`).join("")}<em style="bottom:${(100 * goal.target_usd) / mx}%">target</em></div>
        <p class="acd-note">Start ${m(r.start_usd)} + ${m(r.monthly_usd)}/month · assumed return ${r.assumed_return_pct}%/yr, volatility ${r.assumed_vol_pct}% from your current mix. ${esc(r.note)}</p></div>`;
      window.UIX && UIX.countUp($("#g-out"));
    };
    $("#g-run").addEventListener("click", () => run().catch((e) => showToast(e.message)));
    const lbl = () => { $("#g-years-v").textContent = `${$("#g-years").value} years`; $("#g-monthly-v").textContent = `${W.cur === "ILS" ? "₪" : "$"}${(+$("#g-monthly").value).toLocaleString()}/month`; };
    let tmr = null;
    ["#g-years", "#g-monthly", "#g-target"].forEach((s) => $(s).addEventListener("input", () => { lbl(); clearTimeout(tmr); tmr = setTimeout(() => run().catch(() => {}), 450); }));
    lbl();
    run().catch(() => {});
    $("#wl-tgt-save").addEventListener("click", async () => {
      const t = {}; el.querySelectorAll(".wl-tgt").forEach((i) => { if (+i.value > 0) t[i.dataset.cat] = +i.value; });
      await call("/settings", { targets: t, drift_alert_pct: +$("#wl-drift").value }); showToast("Targets saved"); render();
    });
    $("#wl-addcat-go")?.addEventListener("click", async () => { const t = { ...st.targets, [$("#wl-addcat").value]: 5 }; await call("/settings", { targets: t }); render(); });
  }

  // ------------------------------------------------------------------ BUDGET
  async function tabBudget(el) {
    const b = await call("/budget");
    const months = b.months.slice(-12);
    const mx = Math.max(1, ...months.map((x) => Math.max(x.income, x.expense)));
    const catOpts = (sel) => b.category_names.map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("AVG INCOME / MONTH", ils(b.avg_income), "last 6 full months")}
        ${kpi("AVG SPENDING / MONTH", ils(b.avg_expense), "excl. investing & savings", "dn")}
        ${kpi("LEFT OVER / MONTH", ils(b.avg_net), "", cls(b.avg_net))}
        ${kpi("SAVINGS RATE", isN(b.savings_rate) ? b.savings_rate + "%" : "—", "of income", b.savings_rate >= 20 ? "up" : b.savings_rate < 5 ? "dn" : "", "20%+ is healthy")}
        ${kpi("RECURRING / YEAR", ils(b.recurring_yearly), `${b.recurring.length} subscriptions & fixed payments`)}
      </div>
      <div class="acd-cols"><div class="acd-main">
        ${P(1, "INCOME vs SPENDING", months.length ? `<div class="wl-bars">${months.map((x) => `<div class="wl-bm click" data-month="${x.month}" data-uix-tip="${x.month}|income ${ils(x.income)}|spending ${ils(x.expense)}|${x.net >= 0 ? "saved" : "overspent"} ${ils(Math.abs(x.net))}${isN(x.savings_rate) ? ` (${x.savings_rate}%)` : ""}|click: show this month's transactions"><i class="in" style="height:${(100 * x.income) / mx}%"></i><i class="ex" style="height:${(100 * x.expense) / mx}%"></i><span>${x.month.slice(2)}</span></div>`).join("")}</div><div class="acd-legend"><i class="ln" style="border-color:#2be07e"></i>income <i class="ln" style="border-color:#ff4a4a"></i>spending</div>` : `<div class="acd-empty">Import a bank or credit-card CSV to see your cash flow.</div>`)}
        ${P(2, `TRANSACTIONS (${b.count})`, `<div class="wl-dbar"><input id="bt-q" placeholder="search description, e.g. שופרסל, Netflix…"/><select id="bt-fc"><option value="">ALL CATEGORIES</option>${catOpts("")}</select><button class="bb-chip" id="bt-fm" style="display:none"></button><span class="sp"></span><span class="dim" id="bt-sum"></span></div>
          <div class="wl-tw"><table class="bb-tbl uix-sort wl-tbl" id="bt-tbl"><thead><tr><th>DATE</th><th>DESCRIPTION</th><th>CATEGORY</th><th class="r">AMOUNT</th><th></th></tr></thead><tbody>
          ${b.transactions.map((t) => `<tr data-day="${esc(t.day)}" data-cat="${esc(t.category || "")}" data-amt="${t.amount}"><td class="dim">${esc(t.day)}</td><td>${esc(t.description)}${t.account ? ` <small class="dim">${esc(t.account)}</small>` : ""}</td><td><select class="wl-tcat" data-id="${t.id}">${catOpts(t.category)}</select></td><td class="r ${cls(t.amount)}" data-v="${t.amount}">${ils(t.amount, 2)}</td><td><button class="tu-cam wl-tdel" data-id="${t.id}" title="delete">✕</button></td></tr>`).join("")}</tbody></table></div>
          <p class="acd-note">Changing a category applies to every transaction with the same description — the rule is learned.</p>`)}
      </div><div class="acd-side">
        ${P(3, "IMPORT BANK / CARD CSV", `<div class="wl-form"><label>File (CSV — save Excel exports as CSV)<input id="bi-file" type="file" accept=".csv,.txt"/></label>
          <label>Account name<input id="bi-acc" placeholder="Leumi / Isracard / Max…"/></label>
          <label>Charges shown as positive numbers?<select id="bi-pos"><option value="auto">detect automatically</option><option value="yes">yes (credit-card export)</option><option value="no">no (bank export with −/+)</option></select></label>
          <button class="btn" id="bi-go">IMPORT</button></div><div class="acd-note" id="bi-msg">Hebrew headers (תאריך, שם בית העסק, סכום, חובה / זכות) are recognised. Importing the same file twice is safe.</div>
          <details class="wl-add"><summary>+ add one transaction by hand</summary><div class="wl-form"><div class="wl-2"><label>Date<input id="bt-day" type="date" value="${new Date().toISOString().slice(0, 10)}"/></label><label>Amount (₪, − = expense)<input id="bt-amt" type="number" step="any"/></label></div><label>Description<input id="bt-desc"/></label><label>Category<select id="bt-cat">${catOpts("Other")}</select></label><button class="btn btn-small" id="bt-go">ADD</button></div></details>`)}
        ${P(4, "WHERE THE MONEY GOES", b.categories.length ? `<div class="wl-cats">${b.categories.map((c) => { const mxc = b.categories[0].avg_month || 1; return `<div data-uix-tip="${esc(c.category)}|avg ${ils(c.avg_month)}/month|last month ${ils(c.last_month)}"><span>${esc(c.category)}</span><div class="wl-cb"><i style="width:${(100 * c.avg_month) / mxc}%"></i></div><b>${ils(c.avg_month)}</b></div>`; }).join("")}</div>` : `<div class="dim">—</div>`, "avg per month")}
        ${P(5, "RECURRING PAYMENTS", b.recurring.length ? `<table class="bb-tbl wl-tbl"><tbody>${b.recurring.map((r) => `<tr><td>${esc(r.merchant)}</td><td class="r">${ils(r.typical, 2)}/mo</td><td class="r dim">${ils(r.yearly)}/yr</td></tr>`).join("")}</tbody></table>` : `<div class="dim">Needs 3+ months of data.</div>`)}
      </div></div>`;
    $("#bi-go").addEventListener("click", async () => {
      const f = $("#bi-file").files[0]; if (!f) return showToast("Choose a file");
      const buf = new Uint8Array(await f.arrayBuffer()); let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      try { const r = await call("/budget/import", { b64: btoa(bin), account: $("#bi-acc").value, charges_positive: $("#bi-pos").value }); showToast(`Imported ${r.added} (skipped ${r.skipped_duplicates} duplicates)`); render(); } catch (e) { $("#bi-msg").textContent = e.message; }
    });
    $("#bt-go").addEventListener("click", async () => { await call("/budget/tx", { day: $("#bt-day").value, description: $("#bt-desc").value || "manual", amount: +$("#bt-amt").value, category: $("#bt-cat").value }); render(); });
    el.querySelectorAll(".wl-tcat").forEach((s) => s.addEventListener("change", async () => { const r = await call(`/budget/tx/${s.dataset.id}/category`, { category: s.value, apply_to_same: true }); showToast(`Updated ${r.updated} transactions`); render(); }));
    el.querySelectorAll(".wl-tdel").forEach((btn) => btn.addEventListener("click", async () => { await call(`/budget/tx/${btn.dataset.id}`, undefined, "DELETE"); render(); }));
    // live filter: text + category + month (click a bar)
    let month = "";
    const filt = () => {
      const q = $("#bt-q").value.trim().toLowerCase(), c = $("#bt-fc").value; let n = 0, sum = 0;
      el.querySelectorAll("#bt-tbl tbody tr").forEach((tr) => {
        const ok = (!q || tr.textContent.toLowerCase().includes(q)) && (!c || tr.dataset.cat === c) && (!month || tr.dataset.day.startsWith(month));
        tr.style.display = ok ? "" : "none"; if (ok) { n++; sum += +tr.dataset.amt; }
      });
      $("#bt-sum").textContent = q || c || month ? `${n} shown · total ${ils(sum)}` : "";
      $("#bt-fm").style.display = month ? "" : "none"; $("#bt-fm").textContent = `${month} ✕`;
      el.querySelectorAll("[data-month]").forEach((b) => b.classList.toggle("on", b.dataset.month === month));
    };
    $("#bt-q").addEventListener("input", filt); $("#bt-fc").addEventListener("change", filt);
    $("#bt-fm").addEventListener("click", () => { month = ""; filt(); });
    el.querySelectorAll("[data-month]").forEach((b) => b.addEventListener("click", () => { month = month === b.dataset.month ? "" : b.dataset.month; filt(); }));
  }

  // ------------------------------------------------------------------ DEALS
  W.df = { max_price: "", region: "", category: "", q: "", starred: false };
  async function tabDeals(el) {
    const f = W.df, qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "" && v !== false).map(([k, v]) => [k, String(v)])).toString();
    const [d, saved] = await Promise.all([call(`/deals?${qs}`), call("/deals/filter")]);
    const shekel = (v) => (isN(v) ? `₪${Math.round(v).toLocaleString()}` : "—");
    const SORTS = { new: ["NEWEST", (a, b) => String(b.first_seen).localeCompare(String(a.first_seen))], cheap: ["CHEAPEST", (a, b) => (a.price ?? 1e12) - (b.price ?? 1e12)],
      pay: ["FASTEST PAYBACK", (a, b) => (a.payback_years ?? 1e9) - (b.payback_years ?? 1e9)], profit: ["HIGHEST PROFIT", (a, b) => (b.profit_month ?? -1) - (a.profit_month ?? -1)] };
    W.dsort = W.dsort || "new";
    d.deals.sort(SORTS[W.dsort][1]);
    // price vs yearly owner profit (only ads that state both) + price histogram
    const sc = d.deals.filter((x) => x.price && x.profit_month);
    const scatter = (() => {
      if (sc.length < 2) return '<div class="dim">Needs ads that state both price and profit.</div>';
      const Wd = 300, H = 200, px = Math.max(...sc.map((x) => x.price)), py = Math.max(...sc.map((x) => x.profit_month * 12));
      const X = (v) => 30 + (v / px) * (Wd - 40), Y = (v) => H - 20 - (v / py) * (H - 30);
      const lines = [2, 4].map((yr) => { const x2 = Math.min(px, py * yr); return `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(x2)}" y2="${Y(x2 / yr)}" class="pb"/><text x="${X(x2) - 2}" y="${Y(x2 / yr) - 3}" class="t" text-anchor="end">${yr}y payback</text>`; }).join("");
      return `<svg viewBox="0 0 ${Wd} ${H}" class="acd-svg wl-scat">${lines}<text x="4" y="10" class="t">profit/yr</text><text x="${Wd - 4}" y="${H - 6}" class="t" text-anchor="end">price →</text>
        ${sc.map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer"><circle cx="${X(x.price)}" cy="${Y(x.profit_month * 12)}" r="5" class="${x.payback_years <= 3 ? "good" : x.payback_years <= 5 ? "mid" : "bad"}" data-uix-tip="${esc(x.title.slice(0, 60))}|price ${shekel(x.price)}|profit ${shekel(x.profit_month)}/mo|payback ${x.payback_years}y · click to open"/></a>`).join("")}</svg>`;
    })();
    const pr = d.deals.map((x) => x.price).filter((v) => v > 0), edges = [100e3, 250e3, 500e3, 1e6, 2e6, 5e6, 1e12];
    const hist = edges.map((e, i) => ({ e, n: pr.filter((v) => v <= e && v > (edges[i - 1] || 0)).length }));
    const lab = (e) => (e >= 1e12 ? "5M+" : e >= 1e6 ? `${e / 1e6}M` : `${e / 1e3}K`);
    el.innerHTML = `<div class="acd-kpis wl-kpis">
        ${kpi("BUSINESSES FOR SALE", d.count, `updated ${d.refreshed_at ? new Date(d.refreshed_at * 1000).toLocaleString() : "—"}`)}
        ${kpi("MEDIAN ASKING PRICE", shekel(d.median_price), "all listings shown")}
        ${kpi("NEW (48H)", d.deals.filter((x) => x.new).length, "since the last scans", "up")}
        ${kpi("WITH PROFIT FIGURES", d.deals.filter((x) => x.payback_years).length, "payback can be computed")}
      </div>
      <div class="wl-dbar"><input id="df-q" placeholder="search: קפה, סופר, תל אביב…" value="${esc(f.q)}"/>
        <select id="df-reg"><option value="">ALL REGIONS</option>${d.regions.map((r) => `<option ${r === f.region ? "selected" : ""}>${esc(r)}</option>`).join("")}</select>
        <select id="df-cat"><option value="">ALL CATEGORIES</option>${d.categories.map((c) => `<option ${c === f.category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <input id="df-max" type="number" placeholder="max price ₪" value="${esc(f.max_price)}"/>
        <button class="bb-chip ${f.starred ? "on" : ""}" id="df-star">★ SAVED</button><button class="bb-chip" id="df-go">FILTER</button>
        <select id="df-sort">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}" ${k === W.dsort ? "selected" : ""}>SORT: ${l}</option>`).join("")}</select><span class="sp"></span>
        <label class="wl-chk"><input type="checkbox" id="df-alert" ${saved.alert ? "checked" : ""}/> Telegram me new matches</label><button class="bb-chip" id="df-refresh">↻ SCAN NOW</button></div>
      <div class="acd-cols"><div class="acd-main"><div class="wl-deals">${d.deals.map((x) => `<article class="wl-deal ${x.new ? "new" : ""}">
          <header>${x.new ? '<span class="ac-badge passed">NEW</span>' : ""}<span class="ac-tag">${esc(x.category || "Other")}</span><span class="dim">${esc(x.region || x.city || "")}</span><span class="sp"></span><button class="wl-star ${x.starred ? "on" : ""}" data-star="${x.id}" title="save">★</button><button class="wl-star" data-hide="${x.id}" title="hide">✕</button></header>
          <a class="wl-dt" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer" dir="rtl">${esc(x.title)}</a>
          <div class="wl-dp"><b>${shekel(x.price)}</b>${x.payback_years ? `<span class="${x.payback_years <= 3 ? "up" : x.payback_years <= 5 ? "" : "dn"}" data-uix-tip="PAYBACK|price ÷ yearly owner profit, per the ad|${x.payback_years} years">payback ${x.payback_years}y</span>` : ""}${x.profit_month ? `<span data-uix-tip="OWNER PROFIT|per month, as claimed in the ad">₪${Math.round(x.profit_month).toLocaleString()}/mo profit</span>` : ""}${x.turnover_year ? `<span data-uix-tip="TURNOVER|per year, as claimed">₪${(x.turnover_year / 1e6).toFixed(2)}M turnover</span>` : ""}</div>
          <p dir="rtl">${esc((x.description || "").slice(0, 220))}</p>
          <footer><span class="dim">${esc(x.source)} · first seen ${esc(String(x.first_seen).slice(0, 10))}${x.city && x.region ? " · " + esc(x.city) : ""}</span><a class="bb-chip" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">OPEN AD ▸</a></footer></article>`).join("") || '<div class="acd-empty">Nothing matches — widen the filter.</div>'}</div></div>
        <div class="acd-side">${P("◆", "PRICE vs PROFIT", scatter + `<p class="acd-note">Green = pays back within 3 years, per the ad's own numbers. Hover a dot, click to open the ad.</p>`, `${sc.length} ads`)}
          ${P("◆", "ASKING PRICES", bars(hist.map((h, i) => ({ label: lab(h.e), v: h.n, attr: `data-maxp="${h.e >= 1e12 ? "" : h.e}"`, tip: `${i ? lab(edges[i - 1]) : "0"} – ${lab(h.e)}|${h.n} businesses|click: show up to this price` }))), "click a bar")}
          ${P(1, "PRICES BY CATEGORY", `<table class="bb-tbl wl-tbl"><tbody>${d.by_category.map((c) => `<tr class="click" data-cat="${esc(c.category)}"><td>${esc(c.category)}</td><td class="r dim">${c.n}</td><td class="r">${shekel(c.median_price)}</td></tr>`).join("")}</tbody></table>`, "median asking")}
          ${P(2, "MORE SOURCES (browse by hand)", d.manual_sources.map((s) => `<div class="wl-src"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)} ▸</a></div>`).join("") + `<p class="acd-note">Yad2 and Shuk Asakim block automated reading, and Facebook groups need a login — open them here and look manually.</p>`)}
          <p class="acd-note">${esc(d.note)}</p></div></div>`;
    const apply = () => { W.df = { max_price: $("#df-max").value, region: $("#df-reg").value, category: $("#df-cat").value, q: $("#df-q").value.trim(), starred: W.df.starred }; render(); };
    $("#df-go").addEventListener("click", apply);
    $("#df-q").addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
    ["#df-reg", "#df-cat"].forEach((s) => $(s).addEventListener("change", apply));
    $("#df-star").addEventListener("click", () => { W.df.starred = !W.df.starred; render(); });
    $("#df-alert").addEventListener("change", async (e) => { await call("/deals/filter", { ...W.df, max_price: W.df.max_price ? +W.df.max_price : null, alert: e.target.checked }); showToast(e.target.checked ? "You'll get new matching listings on Telegram" : "Alerts off"); });
    $("#df-refresh").addEventListener("click", async () => { showToast("Scanning Monopoli, Brokerli, Menivim…"); const r = await call("/deals/refresh", {}); showToast(`${r.new} new listings`); render(); });
    el.querySelectorAll("[data-star]").forEach((b) => b.addEventListener("click", async () => { await call(`/deals/${b.dataset.star}`, { starred: !b.classList.contains("on") }); b.classList.toggle("on"); }));
    el.querySelectorAll("[data-hide]").forEach((b) => b.addEventListener("click", async () => { await call(`/deals/${b.dataset.hide}`, { hidden: true }); b.closest(".wl-deal").remove(); }));
    el.querySelectorAll("tr[data-cat]").forEach((tr) => tr.addEventListener("click", () => { W.df.category = tr.dataset.cat; render(); }));
    $("#df-sort").addEventListener("change", (e) => { W.dsort = e.target.value; render(); });
    el.querySelectorAll("[data-maxp]").forEach((b) => b.addEventListener("click", () => { W.df.max_price = b.dataset.maxp; render(); }));
  }

  // ------------------------------------------------------------------ SECURITY
  async function tabSec(el) {
    const s = await call("/security/status");
    el.innerHTML = `<div class="acd-grid2">
      ${P(1, "BACKUPS", `<div class="acd-sg"><div class="acd-st"><span>Last backup</span><b>${s.last_backup_at ? new Date(s.last_backup_at * 1000).toLocaleString() : "never"}</b></div><div class="acd-st"><span>Kept</span><b>newest ${s.keep}</b></div></div>
        <div class="wl-btns"><button class="btn" id="sb-now">BACK UP NOW</button></div>
        <table class="bb-tbl wl-tbl"><tbody>${s.backups.map((b) => `<tr><td>${esc(b.file)}</td><td class="r dim">${b.size_mb} MB</td></tr>`).join("")}</tbody></table>
        <p class="acd-note">Automatic every day, consistent copies even while the server runs. Folder: ${esc(s.backup_dir)}</p>`)}
      ${P(2, "WALLET KEY", `<div class="acd-fs"><div class="acd-f ${s.wallet.encrypted ? "good" : s.wallet.exists ? "bad" : "info"}"><b>${s.wallet.exists ? (s.wallet.encrypted ? "Encrypted at rest (Windows DPAPI)" : "Stored in plain text") : "No wallet connected"}</b>
        <span>${s.wallet.encrypted ? "The Polymarket private key file can only be decrypted by your Windows user on this computer — a copied file is useless." : s.wallet.exists ? "Encrypt it now — anyone with the file could empty the wallet." : ""}</span></div></div>
        ${s.wallet.exists && !s.wallet.encrypted ? `<button class="btn" id="sw-enc">ENCRYPT NOW</button>` : ""}`)}
      ${P(3, "PIN LOCK", `<p class="acd-note">When set, WEALTH, the real Polymarket controls and the Tradovate link need the PIN (unlock lasts 12 hours).</p>
        <div class="wl-form">${s.pin_enabled ? `<label>Current PIN<input id="sp-cur" type="password" inputmode="numeric"/></label>` : ""}
        <label>${s.pin_enabled ? "New PIN (empty = remove)" : "New PIN (4-8 digits)"}<input id="sp-new" type="password" inputmode="numeric"/></label>
        <button class="btn" id="sp-go">${s.pin_enabled ? "CHANGE / REMOVE PIN" : "SET PIN"}</button></div><div class="acd-note">Status: <b class="${s.pin_enabled ? "up" : "dim"}">${s.pin_enabled ? "PIN ON" : "PIN OFF"}</b></div>`)}
      ${P(4, "REPORTS", `<p class="acd-note">A weekly report (Sunday 09:00) and a monthly report (1st, 09:00) go to Telegram automatically.</p><div class="wl-btns"><button class="btn btn-small" data-rep="weekly">PREVIEW WEEKLY</button><button class="btn btn-small" data-rep="monthly">PREVIEW MONTHLY</button><button class="btn btn-small" data-send="weekly">SEND WEEKLY NOW</button></div><pre class="wl-pre" id="rep-out"></pre>`)}
      ${P(5, "NETWORK", `<div class="acd-sg"><div class="acd-st"><span>Server</span><b>${esc(s.server_bind)}</b></div></div><p class="acd-note">The terminal only listens on this computer; nothing is exposed to your network or the internet.</p>`)}
    </div>`;
    $("#sb-now").addEventListener("click", async () => { const r = await call("/security/backup", {}); showToast(`Backed up: ${r.file}`); render(); });
    $("#sw-enc")?.addEventListener("click", async () => { const r = await call("/security/encrypt-wallet", {}); showToast(r.ok ? "Wallet encrypted" : r.reason); render(); });
    $("#sp-go").addEventListener("click", async () => { try { await call("/security/pin", { pin: $("#sp-new").value || null, current: $("#sp-cur")?.value || null }); showToast("PIN updated"); render(); } catch (e) { showToast(e.message); } });
    el.querySelectorAll("[data-rep],[data-send]").forEach((b) => b.addEventListener("click", async () => { const k = b.dataset.rep || b.dataset.send; const r = await call(`/report/${k}${b.dataset.send ? "?send=true" : ""}`, {}); $("#rep-out").textContent = r.text; if (r.sent) showToast("Sent to Telegram"); }));
  }

  // ------------------------------------------------------------------ frame
  function lockScreen() {
    const el = $("#wl-body"); if (!el) return;
    el.innerHTML = `<div class="wl-lock"><b>WEALTH IS LOCKED</b><input id="wl-pin" type="password" inputmode="numeric" placeholder="PIN" autofocus/><button class="btn" id="wl-unlock">UNLOCK</button></div>`;
    const go = async () => { try { await call("/security/unlock", { pin: $("#wl-pin").value }); render(); } catch (e) { showToast("Wrong PIN"); } };
    $("#wl-unlock").addEventListener("click", go);
    $("#wl-pin").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }
  const FN = { pulse: secPulse, nw: tabNW, cash: tabCash, hold: secHold, perf: tabPerf, stress: secStress, risk: tabRisk, goals: tabGoals,
    spendx: secSpendX, budget: tabBudget, shop: tabShop, deals: tabDeals, sec: tabSec, savings: secSavings, road: secRoad, hub: secHub };
  // four pages, one topic each (switch with the tabs or keys 1-4); SECURITY sits behind the lock button.
  // Every section loads on its own; an action inside a section re-renders only that section.
  const PAGES = [
    ["money", "1", "MONEY", "forecast · health score · net worth · cash flow", ["pulse", "nw", "cash"]],
    ["invest", "2", "INVESTMENTS", "goals & missions · holdings · savings · returns · what-if · risk", ["hub", "hold", "savings", "perf", "stress", "risk", "goals"]],
    ["spend", "3", "SPENDING", "this month · merchants · budget & transactions", ["spendx", "budget"]],
    ["biz", "4", "BUSINESS", "road to ₪1M · Shopify · businesses for sale", ["road", "shop", "deals"]],
  ];
  const SEC_PAGE = ["sec", "5", "SECURITY", "backups · PIN lock · wallet · reports", ["sec"]];
  const pageOf = (k) => PAGES.find((p) => p[0] === k) || (k === "sec" ? SEC_PAGE : PAGES[0]);
  W.page = (() => { try { return localStorage.getItem("wealth_page") || "money"; } catch (x) { return "money"; } })();
  const NAME = { ...Object.fromEntries(TABS), pulse: "OVERVIEW — FORECAST, HEALTH & WHAT'S COMING", nw: "NET WORTH", cash: "CASH FLOW — MONTHLY ITEMS & CREDIT CARD",
    hold: "HOLDINGS", perf: "PERFORMANCE vs THE MARKET", stress: "STRESS TEST — WHAT IF…", risk: "RISK", goals: "LONG-TERM GOAL & TARGET MIX", savings: "SAVINGS GOALS", hub: "GOALS & MISSIONS — THE WORK", road: "ROAD TO ₪1M — DAILY PLAN",
    spendx: "SPENDING INSIGHTS", budget: "BUDGET & TRANSACTIONS", shop: "SHOPIFY — YOUR STORE", deals: "BUSINESSES FOR SALE IN ISRAEL" };
  const closed = new Set((() => { try { return JSON.parse(localStorage.getItem("wealth_closed") || "[]"); } catch (x) { return []; } })());
  const saveClosed = () => { try { localStorage.setItem("wealth_closed", JSON.stringify([...closed])); } catch (x) { /* ignore */ } };
  async function renderSec(k) {
    const el = $(`#wl-s-${k}`); if (!el) return;
    if (closed.has(k)) { el.innerHTML = ""; return; }
    el.classList.remove("swap"); void el.offsetWidth; el.classList.add("swap");
    if (!el.children.length) el.innerHTML = `<div class="dim acd-load">Loading ${NAME[k]}…</div>`;
    try { await FN[k](el); window.UIX && UIX.countUp(el); } catch (e) { if (e.message !== "locked") el.innerHTML = `<div class="dn">${esc(e.message)}</div>`; }
  }
  function renderAll() {
    const el = $("#wl-body");
    const [pg, icon, title, sub, keys] = pageOf(W.page);
    CH.clear();
    syncMenus(pg, title, sub, keys);
    el.classList.remove("wl-in"); void el.offsetWidth; el.classList.add("wl-in");
    el.innerHTML = `<div class="wl-ph"><div><b>${title}</b><span class="dim">${sub}</span></div><span class="sp"></span>
        ${keys.length > 1 ? `<nav class="wl-jump">${keys.map((k) => `<button class="bb-chip" data-jump="${k}">${NAME[k].split(" — ")[0]}</button>`).join("")}</nav>` : ""}
        <button class="bb-chip" data-reloadall="1" title="refresh this page">↻ REFRESH</button></div>
      ${keys.map((k) => `<div class="wl-sec ${closed.has(k) ? "shut" : ""}" data-wsec="${k}" id="wl-w-${k}"><div class="wl-sec-h" data-fold="${k}"><span class="wl-caret">▾</span>${NAME[k]}<span class="sp"></span><button class="bb-chip" data-reload="${k}" title="refresh this section">↻</button></div><div class="wl-sec-b" id="wl-s-${k}"></div></div>`).join("")}`;
    keys.forEach((k) => renderSec(k));
  }
  // actions inside a section call render(): refresh just that section; otherwise (page switch, unlock, currency) the whole page
  function render() { const k = W.active; W.active = null; return k && $(`#wl-s-${k}`) ? renderSec(k) : renderAll(); }
  function go(page) {
    W.page = page; W.active = null;
    try { localStorage.setItem("wealth_page", page); } catch (x) { /* ignore */ }
    renderAll();
    $("#view-wealth")?.scrollIntoView({ block: "start" });
  }
  async function badges() {  // live numbers on the page tabs, before a page is even opened
    const [nw, b, sh, pl] = await Promise.allSettled([call("/networth"), call("/budget"), call("/shopify?days=90"), call("/plan")]);
    if (nw.status === "fulfilled") { W.fx = nw.value.usdils || W.fx; badge("money", m(nw.value.total_usd)); badge("invest", `<span class="${cls(nw.value.day_pnl_usd)}">${sm(nw.value.day_pnl_usd)}</span> today`); }
    if (b.status === "fulfilled") { const ym = new Date().toISOString().slice(0, 7), s = -(b.value.transactions || []).filter((t) => t.amount < 0 && t.day.startsWith(ym)).reduce((q, t) => q + t.amount, 0); badge("spend", s ? `${ils(s)} this month` : "import a CSV"); }
    if (pl.status === "fulfilled") { const o = pl.value; badge("biz", `${o.pct}% of ${ils(o.target)} · ${o.day_stats.done}/${o.day_stats.total} today · streak ${o.streak}`); }
    else if (sh.status === "fulfilled") { const s = sh.value.summary || {}; badge("biz", s.mtd ? `${Math.round(s.mtd.net).toLocaleString()} ${esc(s.currency || "")} this month` : "connect Shopify"); }
  }
  // top-bar WEALTH menu + the in-page hamburger show where you are
  function syncMenus(pg, title, sub, keys) {
    const viewOn = $("#view-wealth")?.classList.contains("active");
    document.querySelectorAll("#wealth-pop [data-wpage]").forEach((b) => { b.classList.toggle("cur", b.dataset.wpage === pg); b.classList.toggle("active", viewOn && b.dataset.wpage === pg); });
    const crumb = $("#wealth-crumb"); if (crumb && $("#view-wealth")?.classList.contains("active")) crumb.textContent = ` › ${title}`;
    const bc = $("#wl-bc"); if (bc) bc.textContent = title;
    document.querySelectorAll("#wl-drawer [data-page]").forEach((b) => b.classList.toggle("on", b.dataset.page === pg));
    const on = $("#wl-onpage"); if (on) on.innerHTML = keys.length > 1 ? `<div class="wl-dh">ON THIS PAGE</div>${keys.map((k) => `<button class="wl-di sm" data-jump="${k}"><b>${esc(NAME[k].split(" — ")[0])}</b></button>`).join("")}` : "";
  }
  const drawer = (open) => { const d = $("#wl-drawer"); if (!d) return; const o = open ?? d.hidden; d.hidden = !o; $("#wl-burger")?.classList.toggle("on", o); $("#wl-burger")?.setAttribute("aria-expanded", String(o)); };
  // picking a page in the top-bar menu: remember it before the tab switch renders the view
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("#wealth-pop [data-wpage]");
    if (!b) return;
    W.page = b.dataset.wpage; W.active = null;
    try { localStorage.setItem("wealth_page", W.page); } catch (x) { /* ignore */ }
    document.querySelectorAll("#wealth-pop [data-wpage]").forEach((x) => x.classList.toggle("cur", x === b));
  }, true);
  function build() {
    const v = $("#view-wealth");
    v.innerHTML = `<div class="wl-page wl-one"><div class="wl-top">
        <button class="wl-burger" id="wl-burger" aria-label="WEALTH menu" aria-expanded="false"><i></i><i></i><i></i></button>
        <b class="hs-t">WEALTH</b><span class="wl-bcs">›</span><b class="wl-bc" id="wl-bc"></b><span class="sp"></span>
        <div class="wl-cur">${["ILS", "USD"].map((c) => `<button class="bb-chip ${W.cur === c ? "on" : ""}" data-cur="${c}">${c === "ILS" ? "₪ ILS" : "$ USD"}</button>`).join("")}</div>
        <div class="wl-drawer" id="wl-drawer" hidden>
          <div class="wl-dh">WEALTH · ALL YOUR MONEY</div>
          ${[...PAGES, SEC_PAGE].map(([k, n, t, sub]) => `${k === "sec" ? '<div class="wl-dh">SETTINGS</div>' : ""}<button class="wl-di" data-page="${k}"><i>${n}</i><span><b>${t}</b><small>${esc(sub)}</small></span><em id="wl-pb-${k}"></em></button>`).join("")}
          <div id="wl-onpage"></div>
        </div></div>
      <div class="wl-body" id="wl-body"></div></div>`;
    $("#wl-burger").addEventListener("click", (e) => { e.stopPropagation(); drawer(); });
    document.addEventListener("click", (e) => { if (!e.target.closest?.("#wl-drawer") && !e.target.closest?.("#wl-burger")) drawer(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") drawer(false); });
    v.addEventListener("mousemove", chartHover, { passive: true });
    v.addEventListener("mouseout", chartOut, { passive: true });
    document.addEventListener("keydown", (e) => {
      if (!v.offsetParent || e.ctrlKey || e.altKey || e.metaKey || ["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement || {}).tagName)) return;
      const p = PAGES[+e.key - 1]; if (p) { e.preventDefault(); go(p[0]); }
    });
    badges().catch(() => {});
    const tb = document.querySelector("header.topbar");  // keep the menu just under the terminal header (its height changes when the ticker loads)
    if (tb) { const pin = () => { $(".wl-top", v).style.top = `${tb.getBoundingClientRect().height}px`; }; pin(); new ResizeObserver(pin).observe(tb); }
    const mark = (e) => { const s = e.target.closest && e.target.closest("[data-wsec]"); W.active = s ? s.dataset.wsec : null; };
    v.addEventListener("click", mark, true);
    v.addEventListener("focusin", mark, true);
    v.addEventListener("click", (e) => {
      if (chartClick(e)) return;
      const g = e.target.closest("[data-page]"); if (g) { drawer(false); go(g.dataset.page); return; }
      const j = e.target.closest("[data-jump]"); if (j) { drawer(false); $(`#wl-w-${j.dataset.jump}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
      if (e.target.closest("[data-reloadall]")) { W.active = null; renderAll(); badges().catch(() => {}); return; }
      const r = e.target.closest("[data-reload]"); if (r) { e.stopPropagation(); W.active = null; renderSec(r.dataset.reload); return; }
      const f = e.target.closest("[data-fold]"); if (f) {
        const k = f.dataset.fold; W.active = null;
        closed.has(k) ? closed.delete(k) : closed.add(k); saveClosed();
        f.parentElement.classList.toggle("shut", closed.has(k)); renderSec(k); return;
      }
      const c = e.target.closest("[data-cur]"); if (c) { W.cur = c.dataset.cur; W.active = null; try { localStorage.setItem("wealth_cur", W.cur); } catch (x) { /* ignore */ } v.querySelectorAll("[data-cur]").forEach((b) => b.classList.toggle("on", b === c)); renderAll(); }
    });
    W.built = true;
  }
  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    if (tab === "wealth") { if (!W.built) build(); render(); }
    else { const c = $("#wealth-crumb"); if (c) c.textContent = ""; drawer(false); }
  };
})();
