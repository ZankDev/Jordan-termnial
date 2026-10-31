/* DEEP DIVE (INVESTING › SECURITY › DEEP) — everything the /report knows about a stock or coin, as interactive charts you can hover, filter and click:
   fair-value football field (tick methods in or out and the blended value updates), income-statement Sankey, profit funnel, cash waterfall, 40-formula lab,
   peer bubbles and valuation premium, rank radar vs every SEC filer, short interest, CBOE gamma/skew/term structure, price cone, clinical trials, recalls,
   filing-language monitor, attention, government contracts, macro, ARK, insiders; for coins: market data, perps, Deribit options, DeFi/stablecoins, sentiment.
   Sections stream in as each data source answers (/api/invest/deep/poll). Depends on invest.js (NX.*) and api(). */
(function () {
  "use strict";
  const NX = window.NX;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const usd = (n) => (isN(n) ? NX.big(n, "$") : "—");
  const pc = (n, d = 1) => (isN(n) ? `${n > 0 ? "+" : ""}${n.toFixed(d)}%` : "—");
  const pct0 = (n, d = 1) => (isN(n) ? `${(n * 100).toFixed(d)}%` : "—");
  const money = (n) => (isN(n) ? `$${n >= 100 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : n.toFixed(2)}` : "—");
  const G = "#2be07e", R = "#ff4a4a", A = "#ffa028", B = "#5aa9ff", GR = "#8b8b8b";
  const tipEl = () => { let t = document.getElementById("dp-tip"); if (!t) { t = document.createElement("div"); t.id = "dp-tip"; t.className = "dp-tip"; document.body.appendChild(t); } return t; };

  // one tooltip for everything: any element with data-tip="html" shows it on hover
  function wireTips(root) {
    if (root._tips) return; root._tips = true;
    const t = tipEl();
    root.addEventListener("mousemove", (e) => {
      const el = e.target.closest("[data-tip]");
      if (!el) { t.style.display = "none"; return; }
      t.innerHTML = el.dataset.tip; t.style.display = "block";
      const w = t.offsetWidth, h = t.offsetHeight; let x = e.clientX + 14, y = e.clientY + 14;
      if (x + w > innerWidth - 8) x = e.clientX - w - 14; if (y + h > innerHeight - 8) y = e.clientY - h - 14;
      t.style.left = `${Math.max(4, x)}px`; t.style.top = `${Math.max(4, y)}px`;
    });
    root.addEventListener("mouseleave", () => { t.style.display = "none"; });
    window.addEventListener("scroll", () => { t.style.display = "none"; }, true);
  }
  const tp = (html) => `data-tip="${esc(html)}"`;
  const card = (id, n, title, sub, body, cls = "s12") => `<section class="dp-card ${cls}" id="dp-${id}"><div class="dp-h"><span class="dp-n">${n}</span><b>${title}</b><span class="dp-sub">${sub || ""}</span></div><div class="dp-b">${body}</div></section>`;
  const note = (t) => `<div class="dp-note">${t}</div>`;
  const kvs = (pairs) => `<div class="dp-kv">${pairs.filter(Boolean).map(([k, v, c]) => `<div><span>${k}</span><b class="${c || ""}">${v}</b></div>`).join("")}</div>`;
  const empty = (t) => `<div class="dp-empty">${t}</div>`;
  const tone = (t) => (t === "good" ? G : t === "bad" ? R : GR);

  // generic axis helper for line/bar charts in a 720-wide viewBox
  const scale = (lo, hi, a, b) => (v) => a + ((v - lo) / (hi - lo || 1)) * (b - a);

  // ------------------------------------------------------------------ FAIR VALUE
  function fair(el, d) {
    const ms = d.methods.map((m) => ({ ...m, inc: !m.out }));
    const draw = () => {
      const used = ms.filter((m) => m.inc), ws = used.reduce((a, m) => a + m.w, 0) || 1, fv = used.reduce((a, m) => a + m.mid * m.w, 0) / ws, up = (fv / d.price - 1) * 100;
      const all = ms.flatMap((m) => [m.low, m.high]).concat([d.price, fv]); const lo = Math.max(0, Math.min(...all) * 0.85), hi = Math.max(...all) * 1.08, W = 720, rowH = 34, H = 40 + ms.length * rowH + 66, lx = 225, x = scale(lo, hi, lx, W - 24);
      let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`;
      if (d.w52 && d.w52[0]) s += `<rect x="${x(d.w52[0])}" y="14" width="${x(d.w52[1]) - x(d.w52[0])}" height="${ms.length * rowH + 10}" fill="#ffffff" opacity=".05"/><text x="${x(d.w52[0]) + 4}" y="24" fill="${GR}" font-size="9">52-week range</text>`;
      for (let g = 0; g <= 5; g++) { const v = lo + ((hi - lo) * g) / 5; s += `<line x1="${x(v)}" x2="${x(v)}" y1="14" y2="${24 + ms.length * rowH}" stroke="#171717"/><text x="${x(v)}" y="${36 + ms.length * rowH}" fill="${GR}" font-size="9" text-anchor="middle">${money(v)}</text>`; }
      ms.forEach((m, i) => { const y = 30 + i * rowH; s += `<text x="${lx - 8}" y="${y + 5}" fill="${m.inc ? "#ddd" : "#666"}" font-size="10.5" text-anchor="end">${esc(m.name.length > 34 ? m.name.slice(0, 33) + "…" : m.name)}</text>
        <g ${tp(`<b>${esc(m.name)}</b><br>low ${money(m.low)} · middle <b>${money(m.mid)}</b> · high ${money(m.high)}<br>${(m.mid / d.price - 1) * 100 > 0 ? "above" : "below"} the price by ${Math.abs((m.mid / d.price - 1) * 100).toFixed(0)}%<br><span class='dim'>${esc(m.basis)}</span>`)} class="dp-bar" opacity="${m.inc ? 1 : 0.35}"><rect x="${x(m.low)}" y="${y - 9}" width="${Math.max(2, x(m.high) - x(m.low))}" height="18" fill="${A}" opacity=".6" rx="2"/><rect x="${x(m.mid) - 1.5}" y="${y - 12}" width="3" height="24" fill="#fff"/></g>
        ${x(m.low) - lx < 46 ? `<text x="${x(m.low) + 5}" y="${y + 4}" fill="#000" font-size="9" font-weight="700">${money(m.low)}</text>` : `<text x="${x(m.low) - 4}" y="${y + 4}" fill="${GR}" font-size="9" text-anchor="end">${money(m.low)}</text>`}<text x="${x(m.high) + 4}" y="${y + 4}" fill="${GR}" font-size="9">${money(m.high)}</text>`; });
      s += `<line x1="${x(d.price)}" x2="${x(d.price)}" y1="10" y2="${28 + ms.length * rowH}" stroke="${B}" stroke-width="2"/><text x="${x(d.price)}" y="9" fill="${B}" font-size="10" text-anchor="middle" font-weight="700">price ${money(d.price)}</text>
        <line x1="${x(fv)}" x2="${x(fv)}" y1="10" y2="${28 + ms.length * rowH}" stroke="${up >= 0 ? G : R}" stroke-width="2" stroke-dasharray="5 4"/><text x="${x(fv)}" y="${48 + ms.length * rowH}" fill="${up >= 0 ? G : R}" font-size="10.5" text-anchor="middle" font-weight="700">fair value ${money(fv)}</text>`;
      // meter
      const my = H - 22, mx = scale(-60, 60, 70, W - 70), r = Math.max(-60, Math.min(60, (d.price / fv - 1) * 100));
      s += `<rect x="${mx(-60)}" y="${my}" width="${mx(-20) - mx(-60)}" height="10" fill="#14532d"/><rect x="${mx(-20)}" y="${my}" width="${mx(20) - mx(-20)}" height="10" fill="#713f12"/><rect x="${mx(20)}" y="${my}" width="${mx(60) - mx(20)}" height="10" fill="#7f1d1d"/>
        <path d="M ${mx(r)} ${my - 3} l -6 -9 l 12 0 z" fill="#fff"/><text x="${mx(-40)}" y="${my + 22}" fill="${G}" font-size="9" text-anchor="middle">price cheap vs value</text><text x="${mx(0)}" y="${my + 22}" fill="${A}" font-size="9" text-anchor="middle">fair</text><text x="${mx(40)}" y="${my + 22}" fill="${R}" font-size="9" text-anchor="middle">price expensive vs value</text></svg>`;
      el.querySelector(".dp-chart").innerHTML = s;
      el.querySelector(".dp-fv").innerHTML = `<b style="color:${up >= 0 ? G : R}">${money(fv)}</b> blended fair value · price ${money(d.price)} is <b style="color:${up >= 0 ? G : R}">${(d.price / fv - 1) * 100 >= 0 ? "+" : ""}${((d.price / fv - 1) * 100).toFixed(0)}%</b> vs fair value`;
    };
    el.innerHTML = `<div class="dp-fv"></div><div class="dp-chart"></div>
      <table class="bb-tbl dp-t"><thead><tr><th>USE</th><th>METHOD</th><th class="r">LOW</th><th class="r">MIDDLE</th><th class="r">HIGH</th><th class="r">MIDDLE VS PRICE</th><th>BASIS</th></tr></thead><tbody>${ms.map((m, i) => `<tr><td><input type="checkbox" data-i="${i}" ${m.inc ? "checked" : ""}></td><td><b>${esc(m.name)}</b>${m.out ? ' <i class="dim">(outlier)</i>' : ""}</td><td class="r">${money(m.low)}</td><td class="r"><b>${money(m.mid)}</b></td><td class="r">${money(m.high)}</td><td class="r ${m.mid >= d.price ? "up" : "dn"}">${pc((m.mid / d.price - 1) * 100, 0)}</td><td class="dim">${esc(m.basis)}</td></tr>`).join("")}</tbody></table>
      ${note("An estimate, not advice. Tick methods in or out and the blended value, the line and the meter update. The default blend leaves out a method that sits far from the rest (analyst targets are always kept). Peer methods use Yahoo's 'similar companies', not a curated comp set; models cannot see news, management or regulation.")}`;
    el.querySelectorAll("input[data-i]").forEach((c) => c.addEventListener("change", () => { ms[Number(c.dataset.i)].inc = c.checked; if (!ms.some((m) => m.inc)) { c.checked = true; ms[Number(c.dataset.i)].inc = true; } draw(); }));
    draw();
  }

  // ------------------------------------------------------------------ FLOW: Sankey / funnel / cash waterfall
  function sankey(d) {
    const W = 720, H = 340, pad = 10, nw = 14, cols = d.cols, rightRoom = 175;
    const tot = cols.map((c) => c.reduce((a, n) => a + n.v, 0)), mx = Math.max(...tot), gap = mx * 0.05, hgt = tot.map((t, i) => t + gap * (cols[i].length - 1)), top = Math.max(...hgt), sc = (H - 2 * pad) / top;
    const cx = (i) => pad + i * ((W - 2 * pad - nw - rightRoom) / Math.max(1, cols.length - 1)), pos = {}, name = {};
    cols.forEach((c, ci) => { let cur = pad + ((top - hgt[ci]) * sc) / 2; c.forEach((n) => { pos[n.id] = { x: cx(ci), y: cur, h: n.v * sc, n }; name[n.id] = n.label.split("\n")[0]; cur += n.v * sc + gap * sc; }); });
    const so = {}, di = {}; let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`;
    d.links.forEach((l) => {
      if (!pos[l.s] || !pos[l.d] || l.v <= 0) return; const a = pos[l.s], b = pos[l.d], h = l.v * sc, y0 = a.y + (so[l.s] || 0), y1 = b.y + (di[l.d] || 0); so[l.s] = (so[l.s] || 0) + h; di[l.d] = (di[l.d] || 0) + h;
      const x0 = a.x + nw, x1 = b.x, xm = (x0 + x1) / 2;
      s += `<path class="dp-rib" d="M${x0} ${y0} C${xm} ${y0} ${xm} ${y1} ${x1} ${y1} L${x1} ${y1 + h} C${xm} ${y1 + h} ${xm} ${y0 + h} ${x0} ${y0 + h}Z" fill="${l.color}" ${tp(`<b>${esc(name[l.s])} → ${esc(name[l.d])}</b><br>${usd(l.v)} · ${(l.v / d.rev * 100).toFixed(1)}% of revenue`)}/>`;
    });
    cols.forEach((c, ci) => { let prev = null; c.forEach((n) => { const p = pos[n.id]; let ty = p.y + p.h / 2; if (prev !== null && ty - prev < 28) ty = prev + 28; prev = ty; const [l1, l2] = n.label.split("\n");
      s += `<rect x="${p.x}" y="${p.y}" width="${nw}" height="${Math.max(1.5, p.h)}" fill="${n.color}" ${tp(`<b>${esc(l1)}</b><br>${esc(l2 || "")}`)}/><text class="dp-lbl" x="${p.x + nw + 6}" y="${ty - 2}" font-size="10" fill="#e5e5e5" font-weight="700">${esc(l1)}</text><text class="dp-lbl" x="${p.x + nw + 6}" y="${ty + 10}" font-size="9.5" fill="${GR}">${esc(l2 || "")}</text>`; }); });
    return s + "</svg>";
  }
  function funnel(d) {
    const W = 720, rowH = 54, H = d.stages.length * rowH + 10; let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`;
    d.stages.forEach((st, i) => { const w = Math.max(0.012, Math.abs(st.v) / d.rev), nx = d.stages[i + 1] ? Math.max(0.012, Math.abs(d.stages[i + 1].v) / d.rev) : w, y = 5 + i * rowH, cxm = W / 2, col = i === 0 ? B : st.v >= 0 ? G : R, Wd = 640;
      s += `<path class="dp-rib" d="M${cxm - (w * Wd) / 2} ${y} L${cxm + (w * Wd) / 2} ${y} L${cxm + (nx * Wd) / 2} ${y + rowH - 4} L${cxm - (nx * Wd) / 2} ${y + rowH - 4}Z" fill="${col}" opacity=".78" ${tp(`<b>${esc(st.name)}</b><br>${usd(st.v)} = <b>${(st.v / d.rev * 100).toFixed(1)}¢</b> of each $1 of revenue`)}/><text x="${cxm}" y="${y + 22}" fill="#fff" font-size="11.5" font-weight="700" text-anchor="middle" style="pointer-events:none">${esc(st.name)}</text><text x="${cxm}" y="${y + 37}" fill="#fff" font-size="10.5" text-anchor="middle" style="pointer-events:none">${usd(st.v)} = ${(st.v / d.rev * 100).toFixed(0)}¢</text>`; });
    return s + "</svg>";
  }
  function waterfall(d) {
    const W = 720, H = 300, pad = { l: 56, r: 12, t: 20, b: 64 }; let run = 0; const bars = d.steps.map((st) => { const start = st.kind === "total" ? 0 : run, end = st.kind === "total" ? st.v : run + st.v; run = end; return { ...st, start, end }; });
    const lo = Math.min(0, ...bars.map((b) => Math.min(b.start, b.end))), hi = Math.max(...bars.map((b) => Math.max(b.start, b.end))) * 1.1, y = scale(lo, hi, H - pad.b, pad.t), bw = (W - pad.l - pad.r) / bars.length;
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; for (let g = 0; g <= 4; g++) { const v = lo + ((hi - lo) * g) / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#171717"/><text x="${pad.l - 6}" y="${y(v) + 3}" fill="${GR}" font-size="9" text-anchor="end">${usd(v)}</text>`; }
    bars.forEach((b, i) => { const x0 = pad.l + i * bw + bw * 0.15, col = b.kind === "total" ? B : b.v >= 0 ? G : R; s += `<rect x="${x0}" y="${Math.min(y(b.start), y(b.end))}" width="${bw * 0.7}" height="${Math.max(1.5, Math.abs(y(b.start) - y(b.end)))}" fill="${col}" opacity=".85" ${tp(`<b>${esc(b.name)}</b><br>${b.kind === "total" ? "" : b.v >= 0 ? "+" : "-"}${usd(Math.abs(b.v))}${b.kind === "total" ? "" : `<br>balance after: ${usd(b.end)}`}`)}/><text x="${x0 + bw * 0.35}" y="${Math.min(y(b.start), y(b.end)) - 4}" fill="#ddd" font-size="9.5" text-anchor="middle">${b.kind === "total" ? usd(b.v) : (b.v >= 0 ? "+" : "-") + usd(Math.abs(b.v))}</text>`;
      b.name.split(/(?<=,|&) |\s(?=cash|flow)/).slice(0, 3).forEach((ln, k) => { s += `<text x="${x0 + bw * 0.35}" y="${H - pad.b + 14 + k * 11}" fill="${GR}" font-size="9" text-anchor="middle">${esc(ln)}</text>`; }); });
    return s + "</svg>";
  }
  function flow(el, d) {
    const tabs = [["sankey", "INCOME STATEMENT FLOW"], ["funnel", "PROFIT FUNNEL"], ["cash", "CASH WATERFALL"]].filter((t) => d[t[0]]);
    const show = (k) => {
      el.querySelectorAll(".bb-chip").forEach((c) => c.classList.toggle("on", c.dataset.k === k));
      const x = d[k]; el.querySelector(".dp-chart").innerHTML = k === "sankey" ? sankey(x) : k === "funnel" ? funnel(x) : waterfall(x);
      el.querySelector(".dp-fnote").innerHTML = k === "sankey" ? `Fiscal year ending ${esc(x.end)}. Hover a ribbon for the amount and its share of revenue.${x.loss ? " Operating costs exceed gross profit: the red node is the operating loss funded from cash." : ""}` : k === "funnel" ? `Fiscal year ending ${esc(x.end)}. Each band is as wide as that line is relative to revenue; red is negative.` : `From the ${esc(x.prev_end)} balance to the ${esc(x.end)} balance. The last step is the balancing figure (debt, share issuance, acquisitions, currency).`;
    };
    el.innerHTML = `<div class="bb-chips dp-tabs">${tabs.map((t) => `<button class="bb-chip" data-k="${t[0]}">${t[1]}</button>`).join("")}</div><div class="dp-chart"></div><div class="dp-note dp-fnote"></div>`;
    el.querySelectorAll(".bb-chip").forEach((c) => c.addEventListener("click", () => show(c.dataset.k))); show(tabs[0][0]);
  }

  // ------------------------------------------------------------------ FORMULAS
  function formulas(el, d) {
    const groups = [...new Set(d.rows.map((r) => r.group))], st = { g: "ALL", t: "ALL", q: "" };
    const draw = () => {
      const rows = d.rows.filter((r) => (st.g === "ALL" || r.group === st.g) && (st.t === "ALL" || r.tone === st.t) && (!st.q || (r.name + r.eq + r.reading).toLowerCase().includes(st.q)));
      el.querySelector("tbody").innerHTML = rows.map((r) => `<tr ${tp(`<b>${esc(r.name)}</b><br><span class='dim'>${esc(r.eq)}</span><br>${esc(r.value)} — ${esc(r.reading)}`)}><td><i class="dp-dot" style="background:${tone(r.tone)}"></i></td><td><b>${esc(r.name)}</b><div class="dim">${esc(r.group.toLowerCase())}</div></td><td class="dim">${esc(r.eq)}</td><td class="r"><b>${esc(r.value)}</b></td><td style="color:${tone(r.tone)}">${esc(r.reading)}</td></tr>`).join("") || `<tr><td colspan="5" class="dim">nothing matches</td></tr>`;
      const good = d.rows.filter((r) => r.tone === "good").length, bad = d.rows.filter((r) => r.tone === "bad").length; el.querySelector(".dp-score").innerHTML = `<b style="color:${G}">${good}</b> favourable · <b style="color:${R}">${bad}</b> warnings · ${d.rows.length - good - bad} neutral · ${d.rows.length} computed`;
    };
    el.innerHTML = `<div class="dp-fbar"><div class="bb-chips" id="dp-fg">${["ALL", ...groups].map((g) => `<button class="bb-chip ${g === "ALL" ? "on" : ""}" data-g="${esc(g)}">${esc(g === "ALL" ? "ALL" : g.split(" ")[0])}</button>`).join("")}</div>
      <div class="bb-chips" id="dp-ft">${[["ALL", "ALL"], ["good", "GREEN"], ["bad", "RED"]].map(([v, l]) => `<button class="bb-chip ${v === "ALL" ? "on" : ""}" data-t="${v}">${l}</button>`).join("")}</div><input class="ctl-in" id="dp-fq" placeholder="search a formula…"><span class="dp-score"></span></div>
      <table class="bb-tbl dp-t"><thead><tr><th></th><th>FORMULA</th><th>EQUATION</th><th class="r">VALUE</th><th>READING</th></tr></thead><tbody></tbody></table>
      ${note("Rules of thumb, not standards: a formula that suits one industry can mislead in another (banks, insurers and REITs especially). A row is left out when the filings do not report its inputs.")}`;
    el.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => { st.g = b.dataset.g; el.querySelectorAll("[data-g]").forEach((c) => c.classList.toggle("on", c === b)); draw(); }));
    el.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { st.t = b.dataset.t; el.querySelectorAll("[data-t]").forEach((c) => c.classList.toggle("on", c === b)); draw(); }));
    el.querySelector("#dp-fq").addEventListener("input", (e) => { st.q = e.target.value.toLowerCase(); draw(); }); draw();
  }

  // ------------------------------------------------------------------ PEERS / RANK
  const openSym = (sym) => { if (window.openSecurity) window.openSecurity(sym, "DEEP"); };
  function peers(el, d) {
    let html = "";
    if (d.bubble && d.bubble.length >= 3) {
      const W = 720, H = 320, pad = { l: 50, r: 20, t: 20, b: 38 }, xs = d.bubble.map((b) => b.rg), ys = d.bubble.map((b) => b.gm), x = scale(Math.min(...xs) - 3, Math.max(...xs) + 3, pad.l, W - pad.r), y = scale(Math.min(...ys) - 5, Math.max(...ys) + 5, H - pad.b, pad.t), mc = Math.max(...d.bubble.map((b) => b.mcap));
      let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}"><text x="${W / 2}" y="${H - 6}" fill="${GR}" font-size="10" text-anchor="middle">revenue growth, % (year over year)</text><text x="12" y="${H / 2}" fill="${GR}" font-size="10" transform="rotate(-90 12 ${H / 2})" text-anchor="middle">gross margin, %</text>`;
      for (let g = 0; g <= 4; g++) { const gx = Math.min(...xs) - 3 + ((Math.max(...xs) - Math.min(...xs) + 6) * g) / 4, gy = Math.min(...ys) - 5 + ((Math.max(...ys) - Math.min(...ys) + 10) * g) / 4; s += `<line x1="${x(gx)}" x2="${x(gx)}" y1="${pad.t}" y2="${H - pad.b}" stroke="#141414"/><line x1="${pad.l}" x2="${W - pad.r}" y1="${y(gy)}" y2="${y(gy)}" stroke="#141414"/><text x="${x(gx)}" y="${H - pad.b + 14}" fill="${GR}" font-size="9" text-anchor="middle">${gx.toFixed(0)}</text><text x="${pad.l - 6}" y="${y(gy) + 3}" fill="${GR}" font-size="9" text-anchor="end">${gy.toFixed(0)}</text>`; }
      d.bubble.forEach((b) => { const r = 9 + 26 * Math.sqrt(b.mcap / mc); s += `<g class="dp-bub" onclick="openSecurity('${esc(b.symbol)}','DEEP')" ${tp(`<b>${esc(b.symbol)}</b><br>revenue growth ${pc(b.rg)}<br>gross margin ${pc(b.gm).replace("+", "")}<br>market cap ${usd(b.mcap)}<br><span class='dim'>click to open</span>`)}><circle cx="${x(b.rg)}" cy="${y(b.gm)}" r="${r}" fill="${b.me ? A : B}" opacity="${b.me ? 0.8 : 0.4}" stroke="${b.me ? "#fff" : "none"}"/><text x="${x(b.rg)}" y="${y(b.gm) + 3}" fill="#fff" font-size="10" text-anchor="middle" font-weight="700" style="pointer-events:none">${esc(b.symbol)}</text></g>`; });
      html += `<div class="dp-sub2">GROWTH VS MARGIN (BUBBLE = MARKET CAP, CLICK TO OPEN)</div>` + s + "</svg>";
    }
    if (d.premium && d.premium.length) {
      const W = 720, rowH = 28, H = d.premium.length * rowH + 14, x = scale(-100, 320, 90, W - 30); let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}"><line x1="${x(0)}" x2="${x(0)}" y1="4" y2="${H - 4}" stroke="#555"/>`;
      d.premium.forEach((p, i) => { const v = Math.max(-100, Math.min(320, p.prem)), y = 8 + i * rowH; s += `<text x="84" y="${y + 14}" fill="#ccc" font-size="10.5" text-anchor="end">${esc(p.label)}</text><rect x="${Math.min(x(0), x(v))}" y="${y + 3}" width="${Math.abs(x(v) - x(0))}" height="16" fill="${p.prem > 0 ? R : G}" opacity=".8" ${tp(`<b>${esc(p.label)}</b><br>${p.subject.toFixed(1)}x vs peer median ${p.median.toFixed(1)}x<br><b>${pc(p.prem, 0)}</b> ${p.prem > 0 ? "premium (pricier)" : "discount (cheaper)"} · ${p.n} peers`)}/><text x="${v >= 0 ? x(v) + 6 : x(v) - 6}" y="${y + 15}" fill="#ddd" font-size="10" text-anchor="${v >= 0 ? "start" : "end"}">${pc(p.prem, 0)}</text>`; });
      html += `<div class="dp-sub2">MULTIPLE VS PEER MEDIAN (RED = PRICIER THAN PEERS)</div>` + s + "</svg>";
    }
    if (d.matrix && d.matrix.length) html += `<div class="dp-sub2">VALUATION MATRIX</div><div class="dp-scroll"><table class="bb-tbl dp-t"><thead><tr><th>PEER</th><th class="r">P/E FWD</th><th class="r">P/E</th><th class="r">P/S</th><th class="r">P/B</th><th class="r">EV/REV</th><th class="r">EV/EBITDA</th><th class="r">GROSS MGN</th><th class="r">REV GROWTH</th><th class="r">MKT CAP</th></tr></thead><tbody>${d.matrix.map((r) => `<tr class="click ${r.me ? "sel" : ""}" onclick="openSecurity('${esc(r.symbol)}','DEEP')"><td><b>${esc(r.symbol)}</b></td>${["fpe", "pe", "ps", "pb", "evrev", "eveb"].map((k) => `<td class="r">${isN(r[k]) ? r[k].toFixed(1) + "x" : "—"}</td>`).join("")}<td class="r">${isN(r.gm) ? r.gm.toFixed(1) + "%" : "—"}</td><td class="r">${isN(r.rg) ? r.rg.toFixed(1) + "%" : "—"}</td><td class="r">${usd(r.mcap)}</td></tr>`).join("")}</tbody></table></div>`;
    el.innerHTML = html + note("Peers are Yahoo's 'similar companies', not a curated comp set. A premium can be earned by faster growth or better margins.");
  }
  function rank(el, d) {
    const ms = (d.metrics || []).filter((m) => m.metric !== "Revenue per $ of assets"), n = ms.length; if (n < 3) { el.innerHTML = empty("not enough data"); return; }
    const W = 340, H = 300, cx = W / 2, cy = H / 2 + 6, R0 = 100, ang = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n, pt = (i, v) => [cx + (R0 * v / 100) * Math.cos(ang(i)), cy + (R0 * v / 100) * Math.sin(ang(i))], val = (m) => (m.metric === "Debt / equity" ? 100 - m.pct_all : m.pct_all);
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`;
    [25, 50, 75, 100].forEach((r) => { s += `<polygon points="${ms.map((_, i) => pt(i, r).join(",")).join(" ")}" fill="none" stroke="#1d1d1d"/>`; });
    ms.forEach((m, i) => { const [x, y] = pt(i, 100), [lx, ly] = pt(i, 122); s += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#1d1d1d"/><text x="${lx}" y="${ly + 3}" fill="${GR}" font-size="9" text-anchor="middle">${esc(m.metric.replace("Operating cash flow", "Op. cash flow").replace("Return on ", "R. on ").replace(" margin", " mgn").replace("Debt / equity", "Low debt"))}</text>`; });
    s += `<polygon points="${ms.map((m, i) => pt(i, val(m)).join(",")).join(" ")}" fill="${A}" opacity=".28" stroke="${A}" stroke-width="2"/>`;
    ms.forEach((m, i) => { const [x, y] = pt(i, val(m)); s += `<circle cx="${x}" cy="${y}" r="5" fill="${A}" ${tp(`<b>${esc(m.metric)}</b><br>beats <b>${m.pct_all.toFixed(0)}%</b> of ${d.universe.toLocaleString()} SEC filers${m.pct_band !== null && m.pct_band !== undefined ? `<br>${m.pct_band.toFixed(0)}% of ${d.band.toLocaleString()} similar-size filers` : ""}`)}/>`; });
    el.innerHTML = `<div class="dp-two"><div>${s}</svg></div><div><table class="bb-tbl dp-t"><thead><tr><th>METRIC</th><th class="r">VALUE</th><th class="r">VS ALL</th><th class="r">VS SIMILAR</th></tr></thead><tbody>${d.metrics.map((m) => { const f = m.kind === "usd" ? usd(m.value) : m.kind === "pct" ? pct0(m.value) : m.value.toFixed(2) + "x"; const good = m.higher_better ? m.pct_all >= 50 : m.pct_all <= 50;
      return `<tr><td>${esc(m.metric)}</td><td class="r"><b>${f}</b></td><td class="r" style="color:${good ? G : R}">${m.pct_all.toFixed(0)}</td><td class="r">${m.pct_band !== null && m.pct_band !== undefined ? m.pct_band.toFixed(0) : "—"}</td></tr>`; }).join("")}</tbody></table></div></div>${note(`Percentile = share of the ${d.universe.toLocaleString()} US filers with $50M+ revenue (fiscal ${esc(d.period.slice(2))}) that this company beats. For debt/equity a lower percentile is the better position. Hover the radar points.`)}`;
  }

  // ------------------------------------------------------------------ SHORT INTEREST / OPTIONS / CONE
  function shortint(el, d) {
    const rows = d.rows, W = 720, H = 280, pad = { l: 56, r: 50, t: 20, b: 32 }, mxS = Math.max(...rows.map((r) => r.short)) * 1.1, mxD = Math.max(...rows.map((r) => r.dtc)) * 1.15, y = scale(0, mxS, H - pad.b, pad.t), y2 = scale(0, mxD, H - pad.b, pad.t), bw = (W - pad.l - pad.r) / rows.length;
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; for (let g = 0; g <= 4; g++) { const v = (mxS * g) / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#171717"/><text x="${pad.l - 6}" y="${y(v) + 3}" fill="${GR}" font-size="9" text-anchor="end">${NX.big(v)}</text><text x="${W - pad.r + 6}" y="${y2((mxD * g) / 4) + 3}" fill="${B}" font-size="9">${((mxD * g) / 4).toFixed(1)}d</text>`; }
    rows.forEach((r, i) => { const x0 = pad.l + i * bw; s += `<rect x="${x0 + bw * 0.15}" y="${y(r.short)}" width="${bw * 0.7}" height="${H - pad.b - y(r.short)}" fill="${A}" opacity=".75"/><text x="${x0 + bw / 2}" y="${H - 14}" fill="${GR}" font-size="9" text-anchor="middle">${esc(r.date.slice(5))}</text>`; });
    s += `<polyline fill="none" stroke="${B}" stroke-width="2" points="${rows.map((r, i) => `${pad.l + i * bw + bw / 2},${y2(r.dtc)}`).join(" ")}"/>`;
    rows.forEach((r, i) => { const x0 = pad.l + i * bw; s += `<rect class="dp-hit" x="${x0}" y="${pad.t}" width="${bw}" height="${H - pad.t - pad.b}" ${tp(`<b>${esc(r.date)}</b><br>short ${NX.big(r.short)} (${pc(r.chg)} vs previous)<br>days to cover <b>${r.dtc.toFixed(1)}</b><br>avg daily volume ${NX.big(r.adv)}`)}/>`; });
    const last = rows[rows.length - 1], first = rows[0];
    el.innerHTML = kvs([["LATEST", esc(last.date)], ["SHARES SHORT", NX.big(last.short)], ["VS PREVIOUS", pc(last.chg), last.chg > 0 ? "dn" : "up"], ["DAYS TO COVER", last.dtc.toFixed(1), last.dtc > 7 ? "dn" : ""], d.float ? ["% OF FLOAT", pct0(last.short / d.float)] : null, ["SINCE " + esc(first.date), pc((last.short / first.short - 1) * 100, 0)]]) + s + "</svg>" +
      note("Bars: shares sold short at each FINRA settlement. Line: days to cover. A high, rising figure is bearish positioning; a very high figure with a rising price is how squeezes start.");
  }
  function options(el, d) {
    const spot = d.spot, g = d.gex || [], terms = d.terms || []; let html = kvs([["SPOT", money(spot)], ["PUT/CALL OI", isN(d.pc_oi) ? d.pc_oi.toFixed(2) : "—"], ["PUT/CALL VOL", isN(d.pc_vol) ? d.pc_vol.toFixed(2) : "—"], ["NET DEALER GAMMA /1%", usd(d.net_gex), d.net_gex < 0 ? "dn" : "up"], ["GAMMA FLIP", d.flip ? money(d.flip) : "none"], ["30D IV", d.iv30 ? d.iv30.toFixed(1) + "%" : "—"]]);
    if (g.length > 5) {
      const W = 720, H = 280, pad = { l: 60, r: 14, t: 20, b: 30 }, ks = g.map((a) => a[0]), vs = g.map((a) => a[1]), m = Math.max(...vs.map(Math.abs)) * 1.1 || 1, x = scale(Math.min(...ks), Math.max(...ks), pad.l, W - pad.r), y = scale(-m, m, H - pad.b, pad.t), bw = Math.max(2, ((W - pad.l - pad.r) / g.length) * 0.8);
      let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; [-m, 0, m].forEach((v) => { s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#222"/><text x="${pad.l - 6}" y="${y(v) + 3}" fill="${GR}" font-size="9" text-anchor="end">${usd(v)}</text>`; });
      g.forEach(([k, v]) => { s += `<rect x="${x(k) - bw / 2}" y="${Math.min(y(0), y(v))}" width="${bw}" height="${Math.max(1, Math.abs(y(v) - y(0)))}" fill="${v >= 0 ? G : R}" opacity=".85" ${tp(`<b>strike ${money(k)}</b><br>dealer gamma ${usd(v)} per 1% move<br>${k > spot ? "above" : "below"} spot by ${Math.abs((k / spot - 1) * 100).toFixed(1)}%`)}/>`; });
      s += `<line x1="${x(spot)}" x2="${x(spot)}" y1="${pad.t}" y2="${H - pad.b}" stroke="${B}" stroke-width="2" stroke-dasharray="4 3"/><text x="${x(spot)}" y="${pad.t - 5}" fill="${B}" font-size="10" text-anchor="middle">spot ${money(spot)}</text>`;
      if (d.flip) s += `<line x1="${x(d.flip)}" x2="${x(d.flip)}" y1="${pad.t}" y2="${H - pad.b}" stroke="${A}" stroke-width="2" stroke-dasharray="2 3"/><text x="${x(d.flip)}" y="${H - 8}" fill="${A}" font-size="10" text-anchor="middle">flip ${money(d.flip)}</text>`;
      html += `<div class="dp-sub2">ESTIMATED DEALER GAMMA BY STRIKE (options within 60 days)</div>` + s + "</svg>";
    }
    if (terms.length > 1) {
      const T = terms.filter((t) => isN(t.atm_iv)), W = 720, H = 200, pad = { l: 50, r: 20, t: 16, b: 30 }, x = scale(0, Math.max(...T.map((t) => t.dte)) || 1, pad.l, W - pad.r), mi = Math.max(...T.map((t) => t.atm_iv)) * 1.15, y = scale(0, mi, H - pad.b, pad.t);
      if (T.length > 1) { let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; for (let q = 0; q <= 4; q++) { const v = (mi * q) / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#171717"/><text x="${pad.l - 6}" y="${y(v) + 3}" fill="${GR}" font-size="9" text-anchor="end">${v.toFixed(0)}%</text>`; }
        s += `<polyline fill="none" stroke="${A}" stroke-width="2" points="${T.map((t) => `${x(t.dte)},${y(t.atm_iv)}`).join(" ")}"/>`; T.forEach((t) => { s += `<circle cx="${x(t.dte)}" cy="${y(t.atm_iv)}" r="5" fill="${A}" ${tp(`<b>${esc(t.exp)}</b> (${t.dte} days)<br>ATM IV <b>${t.atm_iv.toFixed(0)}%</b>${isN(t.move) ? `<br>expected move ±${t.move.toFixed(1)}%` : ""}${isN(t.skew) ? `<br>25Δ skew ${t.skew.toFixed(1)} pts` : ""}${isN(t.pc_oi) ? `<br>put/call OI ${t.pc_oi.toFixed(2)}` : ""}${isN(t.max_pain) ? `<br>max pain ${money(t.max_pain)}` : ""}`)}/><text x="${x(t.dte)}" y="${H - 12}" fill="${GR}" font-size="9" text-anchor="middle">${t.dte}d</text>`; });
        html += `<div class="dp-sub2">IMPLIED-VOLATILITY TERM STRUCTURE</div>` + s + "</svg>"; }
      html += `<div class="dp-scroll"><table class="bb-tbl dp-t"><thead><tr><th>EXPIRY</th><th class="r">DAYS</th><th class="r">ATM IV</th><th class="r">SKEW</th><th class="r">EXP. MOVE</th><th class="r">P/C OI</th><th class="r">P/C VOL</th><th class="r">MAX PAIN</th></tr></thead><tbody>${terms.slice(0, 10).map((t) => `<tr><td>${esc(t.exp)}</td><td class="r">${t.dte}</td><td class="r">${isN(t.atm_iv) ? t.atm_iv.toFixed(0) + "%" : "—"}</td><td class="r">${isN(t.skew) ? t.skew.toFixed(1) : "—"}</td><td class="r">${isN(t.move) ? "±" + t.move.toFixed(1) + "%" : "—"}</td><td class="r">${isN(t.pc_oi) ? t.pc_oi.toFixed(2) : "—"}</td><td class="r">${isN(t.pc_vol) ? t.pc_vol.toFixed(2) : "—"}</td><td class="r">${isN(t.max_pain) ? money(t.max_pain) : "—"}</td></tr>`).join("")}</tbody></table></div>`;
    }
    el.innerHTML = html + note("CBOE delayed chain. Gamma assumes market makers are short what customers bought (calls positive, puts negative): positive total gamma tends to dampen moves, negative amplifies them. An estimate from open interest, not dealer books.");
  }
  function cone(el, d) {
    const W = 720, H = 300, pad = { l: 62, r: 14, t: 16, b: 28 }, days = d.days, f = d.fan, lo = Math.min(...f.p10) * 0.95, hi = Math.max(...f.p90) * 1.03, x = scale(1, days.length, pad.l, W - pad.r), y = scale(lo, hi, H - pad.b, pad.t);
    const band = (a, b) => `M${days.map((_, i) => `${x(i + 1)},${y(a[i])}`).join(" L")} L${days.map((_, i) => `${x(days.length - i)},${y(b[days.length - 1 - i])}`).join(" L")}Z`;
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; for (let g = 0; g <= 4; g++) { const v = lo + ((hi - lo) * g) / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#171717"/><text x="${pad.l - 6}" y="${y(v) + 3}" fill="${GR}" font-size="9" text-anchor="end">${money(v)}</text>`; }
    s += `<path d="${band(f.p10, f.p90)}" fill="${A}" opacity=".16"/><path d="${band(f.p25, f.p75)}" fill="${A}" opacity=".3"/><polyline fill="none" stroke="${A}" stroke-width="2" points="${days.map((_, i) => `${x(i + 1)},${y(f.p50[i])}`).join(" ")}"/><line x1="${pad.l}" x2="${W - pad.r}" y1="${y(d.price)}" y2="${y(d.price)}" stroke="${B}" stroke-dasharray="4 3"/>`;
    for (let i = 0; i < days.length; i += 2) s += `<rect class="dp-hit" x="${x(i + 1) - 4}" y="${pad.t}" width="8" height="${H - pad.t - pad.b}" ${tp(`<b>day ${i + 1}</b> (${esc(d.unit)})<br>90th pct <b>${money(f.p90[i])}</b> (${pc((f.p90[i] / d.price - 1) * 100, 0)})<br>75th ${money(f.p75[i])}<br>median <b>${money(f.p50[i])}</b> (${pc((f.p50[i] / d.price - 1) * 100, 0)})<br>25th ${money(f.p25[i])}<br>10th <b>${money(f.p10[i])}</b> (${pc((f.p10[i] / d.price - 1) * 100, 0)})`)}/>`;
    s += [0, 30, 60, 90, 120, 150, 179].map((i) => `<text x="${x(i + 1)}" y="${H - 8}" fill="${GR}" font-size="9" text-anchor="middle">${i + 1}</text>`).join("");
    el.innerHTML = kvs([["PRICE", money(d.price)], ["VOLATILITY (1Y)", pct0(d.vol, 0)], ...d.odds.map((o) => [esc(o.label), pct0(o.p, 0)])]) + s + `</svg><div class="dp-scroll"><table class="bb-tbl dp-t"><thead><tr><th>HORIZON</th><th class="r">10TH</th><th class="r">25TH</th><th class="r">MEDIAN</th><th class="r">75TH</th><th class="r">90TH</th><th class="r">CHANCE OF GAIN</th></tr></thead><tbody>${d.horizons.map((h) => `<tr><td>${h.h} ${esc(d.unit)}</td>${["p10", "p25", "p50", "p75", "p90"].map((k) => `<td class="r">${money(h[k])} <span class="${h[k] >= d.price ? "up" : "dn"}">(${pc((h[k] / d.price - 1) * 100, 0)})</span></td>`).join("")}<td class="r">${pct0(h.up, 0)}</td></tr>`).join("")}</tbody></table></div>` +
      note("Bootstrap of this security's own daily returns over the last year (5,000 simulated paths, drift removed so a past rally or crash is not extrapolated). Hover the fan for each day's percentile prices. It shows how wide outcomes can be, not what will happen.");
  }

  // ------------------------------------------------------------------ TRIALS / RECALLS / LANGUAGE / ATTENTION / tables
  function trials(el, d) {
    const st = { phase: "ALL", status: "ACTIVE", q: "" }, all = d.studies, ACT = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING"], phases = ["ALL", ...new Set(all.map((s) => s.phase))];
    const parse = (s) => (s ? new Date(s.length === 7 ? s + "-01" : s) : null), today = new Date();
    const draw = () => {
      const rows = all.filter((s) => (st.phase === "ALL" || s.phase === st.phase) && (st.status === "ALL" || (st.status === "ACTIVE" ? ACT.includes(s.status) : s.status === st.status)) && (!st.q || (s.title + s.cond).toLowerCase().includes(st.q)));
      const up = rows.filter((s) => { const d1 = parse(s.pcd); return d1 && d1 >= new Date(today - 30 * 864e5) && d1 <= new Date(+today + 548 * 864e5); }).sort((a, b) => parse(a.pcd) - parse(b.pcd));
      let tl = ""; if (up.length) { const W = 720, H = 120, t0 = +new Date(today - 30 * 864e5), t1 = +new Date(+today + 548 * 864e5), x = scale(t0, t1, 30, W - 30); tl = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}"><line x1="30" x2="${W - 30}" y1="70" y2="70" stroke="#333"/><line x1="${x(+today)}" x2="${x(+today)}" y1="20" y2="100" stroke="${B}" stroke-dasharray="3 3"/><text x="${x(+today)}" y="14" fill="${B}" font-size="9" text-anchor="middle">today</text>`;
        up.forEach((s, i) => { const cxp = x(+parse(s.pcd)), cyp = 70 + ((i % 5) - 2) * 11; tl += `<circle cx="${cxp}" cy="${cyp}" r="6" fill="${A}" opacity=".85" ${tp(`<b>${esc(s.pcd)}</b> · ${esc(s.phase)}<br>${esc(s.title)}<br>${esc(s.cond)} · ${s.n ? s.n.toLocaleString() + " patients" : ""}<br><span class='dim'>${esc(s.id)} · ${esc(s.status.replace(/_/g, " ").toLowerCase())}</span>`)}/>`; }); tl += "</svg>"; }
      el.querySelector(".dp-tl").innerHTML = (tl ? `<div class="dp-sub2">PRIMARY COMPLETION DATES, NEXT 18 MONTHS (HOVER A DOT)</div>${tl}` : "");
      el.querySelector("tbody").innerHTML = rows.slice(0, 120).map((s) => `<tr><td><a href="https://clinicaltrials.gov/study/${esc(s.id)}" target="_blank" rel="noopener noreferrer">${esc(s.id)}</a></td><td>${esc(s.title)}</td><td>${esc(s.phase)}</td><td>${esc(s.cond)}</td><td>${esc(s.status.replace(/_/g, " ").toLowerCase())}</td><td class="r">${esc(s.pcd || "—")}</td><td class="r">${s.n ? s.n.toLocaleString() : "—"}</td></tr>`).join("") || `<tr><td colspan="7" class="dim">nothing matches</td></tr>`;
      el.querySelector(".dp-count").textContent = `${rows.length} of ${all.length} studies`;
    };
    el.innerHTML = `<div class="dp-fbar"><div class="bb-chips" id="dp-tp">${phases.map((p) => `<button class="bb-chip ${p === "ALL" ? "on" : ""}" data-p="${esc(p)}">${esc(p)}</button>`).join("")}</div><div class="bb-chips" id="dp-ts">${[["ACTIVE", "ACTIVE"], ["ALL", "ALL"], ["COMPLETED", "COMPLETED"], ["TERMINATED", "TERMINATED"]].map(([v, l]) => `<button class="bb-chip ${v === "ACTIVE" ? "on" : ""}" data-s="${v}">${l}</button>`).join("")}</div><input class="ctl-in" id="dp-tq" placeholder="search condition or title…"><span class="dp-count dp-score"></span></div><div class="dp-tl"></div>
      <div class="dp-scroll" style="max-height:340px;overflow:auto"><table class="bb-tbl dp-t"><thead><tr><th>NCT</th><th>TRIAL</th><th>PHASE</th><th>CONDITION</th><th>STATUS</th><th class="r">PRIMARY COMPLETION</th><th class="r">PATIENTS</th></tr></thead><tbody></tbody></table></div>${note("Industry-sponsored studies where the company (or a subsidiary) is lead sponsor. Primary completion is when the last patient is examined for the main endpoint; results follow within months and dates often slip.")}`;
    el.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => { st.phase = b.dataset.p; el.querySelectorAll("[data-p]").forEach((c) => c.classList.toggle("on", c === b)); draw(); }));
    el.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => { st.status = b.dataset.s; el.querySelectorAll("[data-s]").forEach((c) => c.classList.toggle("on", c === b)); draw(); }));
    el.querySelector("#dp-tq").addEventListener("input", (e) => { st.q = e.target.value.toLowerCase(); draw(); }); draw();
  }
  function recalls(el, d) {
    const rs = d.recalls, by = {}; rs.forEach((r) => { const k = r.d.slice(0, 7); by[k] = (by[k] || 0) + 1; }); const keys = Object.keys(by).sort().slice(-24), W = 720, H = 150, mx = Math.max(...keys.map((k) => by[k])), bw = (W - 40) / keys.length, y = scale(0, mx * 1.2, H - 24, 10);
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; keys.forEach((k, i) => { s += `<rect x="${30 + i * bw + 2}" y="${y(by[k])}" width="${bw - 4}" height="${H - 24 - y(by[k])}" fill="${A}" opacity=".8" ${tp(`<b>${k}</b><br>${by[k]} recall campaign${by[k] > 1 ? "s" : ""}`)}/>${i % 3 === 0 ? `<text x="${30 + i * bw + bw / 2}" y="${H - 8}" fill="${GR}" font-size="9" text-anchor="middle">${k}</text>` : ""}`; });
    el.innerHTML = kvs([["MAKE", esc(d.make.toUpperCase())], ["CAMPAIGNS FOUND", rs.length]]) + `<div class="dp-sub2">RECALLS PER MONTH</div>` + s + `</svg><div class="dp-scroll" style="max-height:300px;overflow:auto"><table class="bb-tbl dp-t"><thead><tr><th>DATE</th><th>CAMPAIGN</th><th>MODELS</th><th>COMPONENT</th><th>SUMMARY</th></tr></thead><tbody>${rs.slice(0, 40).map((r) => `<tr><td>${esc(r.d)}</td><td>${esc(r.id)}</td><td>${esc(r.models.join(", "))}</td><td>${esc(r.component)}</td><td class="dim">${esc((r.summary || "").slice(0, 160))}</td></tr>`).join("")}</tbody></table></div>` + note("US NHTSA recall campaigns for this maker, searched by model and model year (last four years). Many are software fixes; the components show where quality problems cluster.");
  }
  function language(el, d) {
    const rows = [...d.rows].sort((a, b) => b.recent - b.prior - (a.recent - a.prior)), W = 720, rowH = 30, H = rows.length * rowH + 10, mx = Math.max(...rows.flatMap((r) => [r.recent, r.prior])) || 1, x = scale(0, mx, 150, W - 60);
    let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`; rows.forEach((r, i) => { const y = 6 + i * rowH; s += `<text x="142" y="${y + 17}" fill="#ccc" font-size="10.5" text-anchor="end">${esc(r.term)}</text><rect x="150" y="${y + 3}" width="${Math.max(1, x(r.recent) - 150)}" height="11" fill="${r.recent > r.prior ? R : A}" opacity=".85" ${tp(`<b>"${esc(r.term)}"</b><br>last 12 months: <b>${r.recent}</b> filings<br>prior 12 months: ${r.prior} filings<br>change ${r.recent - r.prior > 0 ? "+" : ""}${r.recent - r.prior}`)}/><rect x="150" y="${y + 15}" width="${Math.max(1, x(r.prior) - 150)}" height="8" fill="${GR}" opacity=".5"/><text x="${x(r.recent) + 6}" y="${y + 13}" fill="#ddd" font-size="10">${r.recent}</text>`; });
    el.innerHTML = `<div class="dp-leg"><i style="background:${A}"></i> last 12 months <i style="background:${GR}"></i> prior 12 months <i style="background:${R}"></i> rising</div>` + s + `</svg>` + note("How many of the company's 10-K, 10-Q and 8-K filings contain each phrase. A phrase appearing for the first time, or doubling, is an early signal of what management is worried about. A phrase can sit in boilerplate risk factors.");
  }
  function spark(pts, fmtTip, color) {
    const W = 720, H = 150, pad = { l: 44, r: 10, t: 10, b: 20 }, v = pts.map((p) => p[1]), x = scale(0, pts.length - 1, pad.l, W - pad.r), y = scale(0, Math.max(...v) * 1.1, H - pad.b, pad.t);
    return `<svg class="dp-svg" viewBox="0 0 ${W} ${H}"><path d="M${pts.map((p, i) => `${x(i)},${y(p[1])}`).join(" L")} L${x(pts.length - 1)},${H - pad.b} L${x(0)},${H - pad.b}Z" fill="${color}" opacity=".25"/><polyline fill="none" stroke="${color}" stroke-width="1.6" points="${pts.map((p, i) => `${x(i)},${y(p[1])}`).join(" ")}"/>${pts.map((p, i) => `<rect class="dp-hit" x="${x(i) - 3}" y="${pad.t}" width="6" height="${H - pad.t - pad.b}" ${tp(fmtTip(p))}/>`).join("")}</svg>`;
  }
  function attention(el, d) {
    let h = ""; const w = d.wiki, st = d.stwits, hn = d.hn, gd = d.gdelt;
    h += kvs([w ? ["WIKIPEDIA VIEWS (7D AVG)", Math.round(w.last7).toLocaleString()] : null, w ? ["VS 4-MONTH MEDIAN", w.ratio.toFixed(2) + "x", w.ratio > 2 ? "dn" : ""] : null, st ? ["STOCKTWITS WATCHERS", st.watchers ? st.watchers.toLocaleString() : "—"] : null, st ? ["POSTS/HOUR", st.per_hour ? st.per_hour.toFixed(1) : "—"] : null, hn ? ["HACKER NEWS (45D)", `${hn.count} stories`] : null, gd ? ["NEWS TONE 7D / 30D", `${gd.tone7.toFixed(2)} / ${gd.tone30.toFixed(2)}`, gd.tone7 < gd.tone30 - 1.5 ? "dn" : ""] : null]);
    if (st && st.bull + st.bear) { const tot = st.bull + st.bear; h += `<div class="dp-sub2">STOCKTWITS: LATEST 30 POSTS THAT TOOK A SIDE</div><div class="dp-split" ${tp(`${st.bull} bullish · ${st.bear} bearish`)}><i style="width:${st.bull / tot * 100}%;background:${G}">${st.bull} bullish</i><i style="width:${st.bear / tot * 100}%;background:${R}">${st.bear} bearish</i></div>`; }
    if (w) h += `<div class="dp-sub2">WIKIPEDIA PAGE VIEWS: ${esc(w.title.toUpperCase())}</div>` + spark(w.points, (p) => `<b>${p[0].slice(0, 4)}-${p[0].slice(4, 6)}-${p[0].slice(6)}</b><br>${p[1].toLocaleString()} views`, A);
    if (gd) h += `<div class="dp-sub2">NEWS TONE, 30 DAYS (GDELT)</div>` + spark(gd.tone.map((p) => [p[0], p[1] + 10]), (p) => `<b>${p[0]}</b><br>tone ${(p[1] - 10).toFixed(2)}`, B);
    if (hn) h += `<div class="dp-sub2">TOP HACKER NEWS STORIES</div><table class="bb-tbl dp-t"><tbody>${hn.top.map((x) => `<tr><td>${esc(x.date)}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a></td><td class="r">${x.points} pts</td><td class="r">${x.comments} comments</td></tr>`).join("")}</tbody></table>`;
    el.innerHTML = h + note("Attention is a crowd signal, not fundamentals: spikes usually mean the story is already known. Treat extremes as a reason to read the news, not as a trade.");
  }
  const table = (cols, rows, o = {}) => `<div class="dp-scroll" ${o.h ? `style="max-height:${o.h}px;overflow:auto"` : ""}><table class="bb-tbl dp-t"><thead><tr>${cols.map((c) => `<th class="${c[2] ? "r" : ""}">${c[0]}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td class="${c[2] ? "r" : ""}">${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  function contracts(el, d) { el.innerHTML = table([["AGENCY", (r) => esc(r["Awarding Agency"])], ["RECIPIENT", (r) => esc(r["Recipient Name"])], ["AWARD", (r) => usd(r["Award Amount"]), 1], ["START", (r) => esc((r["Start Date"] || "").slice(0, 10))], ["END", (r) => esc((r["End Date"] || "").slice(0, 10))], ["DESCRIPTION", (r) => `<span class="dim">${esc((r.Description || "").slice(0, 90))}</span>`]], d.awards) + note("Federal contract awards started in the last twelve months, matched by recipient name. Amounts are full award ceilings, not revenue booked in the period."); }
  function macro(el, d) {
    let h = "";
    if (d.global && d.global.length) h += `<div class="dp-sub2">GLOBAL RATES AND FX (ECB + BIS)</div>` + table([["INDICATOR", (r) => esc(r.label)], ["LATEST", (r) => `<b>${r.value}${r.unit || ""}</b>`, 1], ["PREVIOUS", (r) => (isN(r.prev) ? r.prev + (r.unit || "") : "—"), 1], ["AS OF", (r) => esc(r.date), 1], ["SOURCE", (r) => esc(r.source)]], d.global);
    if (d.cot && d.cot.length) h += `<div class="dp-sub2">INDEX FUTURES POSITIONING (CFTC COT)</div>` + table([["MARKET", (r) => esc(r.market)], ["DATE", (r) => esc(r.date), 1], ["LEVERAGED FUNDS NET", (r) => `<span class="${r.lev_net < 0 ? "dn" : "up"}">${r.lev_net.toLocaleString()}</span>`, 1], ["ASSET MANAGERS NET", (r) => `<span class="${r.am_net < 0 ? "dn" : "up"}">${r.am_net.toLocaleString()}</span>`, 1], ["OPEN INTEREST", (r) => r.oi.toLocaleString(), 1]], d.cot);
    if (d.imf && d.imf.length) { const ys = Object.keys(d.imf[0].growth); h += `<div class="dp-sub2">IMF OUTLOOK: REAL GDP GROWTH (%) — green = faster growth</div>` + table([["COUNTRY", (r) => esc(r.country)], ...ys.map((y) => [y, (r) => { const v = r.growth[y]; return isN(v) ? `<span style="color:${v >= 3 ? G : v < 1 ? R : "#ddd"}">${v.toFixed(1)}</span>` : "—"; }, 1]), ["INFLATION " + ys[1], (r) => (isN(r.infl[ys[1]]) ? r.infl[ys[1]].toFixed(1) : "—"), 1]], d.imf); }
    if (d.fred && d.fred.length) h += `<div class="dp-sub2">US MACRO (FRED)</div>` + table([["INDICATOR", (r) => esc(r.label)], ["LATEST", (r) => (isN(r.value) ? `<b>${r.mode === "yoy" ? pc(r.value) + " y/y" : r.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>` : "—"), 1], ["AS OF", (r) => esc(String(r.date || "").slice(0, 10)), 1]], d.fred, { h: 260 });
    el.innerHTML = h || empty("no macro data");
  }
  function ark(el, d) { el.innerHTML = table([["ARK FUND", (r) => `<b>${esc(r.fund)}</b>`], ["COMPANY", (r) => esc(r.company)], ["SHARES", (r) => NX.big(r.shares), 1], ["VALUE", (r) => usd(r.value), 1], ["WEIGHT", (r) => (isN(r.weight) ? r.weight.toFixed(2) + "%" : "—"), 1], ["AS OF", (r) => esc(r.date), 1]], d) + note("ARK publishes only its current daily holdings snapshot."); }
  function insiders(el, d) {
    const buys = d.filter((r) => /buy|purchase/i.test(r.text)), sells = d.filter((r) => /sale|sell/i.test(r.text));
    el.innerHTML = kvs([["FILINGS SHOWN", d.length], ["BUYS", buys.length, "up"], ["SELLS", sells.length, "dn"], ["NET SHARES", NX.big(buys.reduce((a, r) => a + r.shares, 0) - sells.reduce((a, r) => a + r.shares, 0))]]) + table([["DATE", (r) => esc(new Date(r.date * 1000).toISOString().slice(0, 10))], ["INSIDER", (r) => `<b>${esc(r.name)}</b>`], ["ROLE", (r) => `<span class="dim">${esc(r.relation)}</span>`], ["TRANSACTION", (r) => `<span class="${/buy|purchase/i.test(r.text) ? "up" : /sale|sell/i.test(r.text) ? "dn" : ""}">${esc(r.text)}</span>`], ["SHARES", (r) => NX.big(r.shares), 1], ["VALUE", (r) => usd(r.value), 1]], d, { h: 340 }) + note("Open-market common-stock transactions from SEC Form 4 filings (derivative awards are left out).");
  }
  // ---- coins
  function coinmkt(el, c) {
    const m = c.market || {}; el.innerHTML = kvs([["RANK", c.rank ? "#" + c.rank : "—"], ["MARKET CAP", usd(m.mcap)], ["FDV / MCAP", m.fdv && m.mcap ? (m.fdv / m.mcap).toFixed(2) + "x" : "—"], ["24H VOLUME", usd(m.vol)], ["CIRCULATING / MAX", m.supply && m.max_supply ? pct0(m.supply / m.max_supply, 0) : "—"], ["FROM ATH", pc(m.ath_pct, 0), m.ath_pct < -50 ? "dn" : ""], ["1H", pc(m.p1h), m.p1h < 0 ? "dn" : "up"], ["24H", pc(m.p24h), m.p24h < 0 ? "dn" : "up"], ["7D", pc(m.p7d), m.p7d < 0 ? "dn" : "up"], ["30D", pc(m.p30d), m.p30d < 0 ? "dn" : "up"], ["1Y", pc(m.p1y), m.p1y < 0 ? "dn" : "up"]]) + (c.categories ? `<div class="dp-note">${c.categories.map(esc).join(" · ")}</div>` : "");
  }
  function perps(el, d) { const s = d.summary; el.innerHTML = kvs([["PERP PRICE", money(s.price)], ["FUNDING (8H)", (s.funding * 100).toFixed(4) + "%", s.funding > 0.0003 ? "dn" : ""], ["ANNUALISED", (s.apr * 100).toFixed(0) + "%"], ["OPEN INTEREST", usd(s.oi_usd)], ["24H TURNOVER", usd(s.turnover)], ["POSITIONING", esc(s.tone)]]) + table([["PERP", (r) => esc(r.symbol)], ["PRICE", (r) => money(r.price), 1], ["24H", (r) => `<span class="${r.pct24 < 0 ? "dn" : "up"}">${pc(r.pct24)}</span>`, 1], ["FUNDING 8H", (r) => (r.funding * 100).toFixed(4) + "%", 1], ["APR", (r) => (r.apr * 100).toFixed(0) + "%", 1], ["OPEN INTEREST", (r) => usd(r.oi_usd), 1]], d.top) + note("Bybit perpetuals. Funding above ~0.03% per 8h means crowded longs are paying shorts."); }
  function deribit(el, d) {
    const ex = d.expiries, W = 720, H = 200, mx = Math.max(...ex.map((e) => e.oi_usd)), bw = (W - 60) / ex.length, y = scale(0, mx * 1.1, H - 30, 10); let s = `<svg class="dp-svg" viewBox="0 0 ${W} ${H}">`;
    ex.forEach((e, i) => { s += `<rect x="${40 + i * bw + 2}" y="${y(e.oi_usd)}" width="${bw - 4}" height="${H - 30 - y(e.oi_usd)}" fill="${A}" opacity=".8" ${tp(`<b>${esc(e.date)}</b> (${e.dte}d)<br>open interest ${usd(e.oi_usd)}<br>put/call OI ${isN(e.pc) ? e.pc.toFixed(2) : "—"}<br>ATM IV ${isN(e.atm_iv) ? e.atm_iv.toFixed(0) + "%" : "—"}<br>max pain ${money(e.max_pain)}`)}/>${i % Math.ceil(ex.length / 10) === 0 ? `<text x="${40 + i * bw + bw / 2}" y="${H - 12}" fill="${GR}" font-size="9" text-anchor="middle">${esc(e.date.slice(5))}</text>` : ""}`; });
    el.innerHTML = kvs([["INDEX", money(d.spot)], ["TOTAL OPTION OI", usd(d.total_oi_usd)], ["PUT/CALL OI", isN(d.pc) ? d.pc.toFixed(2) : "—"]]) + `<div class="dp-sub2">OPEN INTEREST BY EXPIRY (HOVER)</div>` + s + "</svg>" + note("Deribit public data, BTC and ETH only. Max pain is the strike where option holders collectively lose the most at expiry.");
  }
  function llama(el, d) {
    let h = ""; const st = d.stable, ch = d.chain;
    h += kvs([st ? ["STABLECOIN SUPPLY", usd(st.total)] : null, st && isN(st.change_pct) ? ["30-DAY CHANGE", pc(st.change_pct), st.change_pct < 0 ? "dn" : "up"] : null, d.total_tvl ? ["TOTAL DEFI TVL", usd(d.total_tvl)] : null, ch ? [esc(ch.name) + " TVL", usd(ch.tvl)] : null, ch ? ["TVL RANK", `#${ch.rank} of ${ch.n}`] : null]);
    if (d.top && d.top.length) { const W = 720, rowH = 28, H = d.top.length * rowH + 8, mx = Math.max(...d.top.map((c) => c.tvl)), x = scale(0, mx, 120, W - 80); h += `<div class="dp-sub2">LARGEST CHAINS BY TVL</div><svg class="dp-svg" viewBox="0 0 ${W} ${H}">${d.top.map((c, i) => `<text x="112" y="${10 + i * rowH + 14}" fill="#ccc" font-size="10.5" text-anchor="end">${esc(c.name)}</text><rect x="120" y="${10 + i * rowH + 3}" width="${x(c.tvl) - 120}" height="16" fill="${ch && c.name === ch.name ? A : B}" opacity=".8" ${tp(`<b>${esc(c.name)}</b><br>TVL ${usd(c.tvl)}${mx ? `<br>${(c.tvl / d.total_tvl * 100).toFixed(1)}% of all DeFi` : ""}`)}/><text x="${x(c.tvl) + 6}" y="${10 + i * rowH + 15}" fill="#ddd" font-size="10">${usd(c.tvl)}</text>`).join("")}</svg>`; }
    el.innerHTML = h + note("DefiLlama: TVL is the value deposited in DeFi apps on a chain; stablecoin supply is the dry powder available to buy crypto.");
  }
  function sentiment(el, d) {
    let h = ""; const f = d.fng || {}, gl = d.global || {};
    if (f.now) h += kvs([["FEAR & GREED", `${f.now[1] ?? f.now.value ?? ""} ${esc(f.now[2] ?? f.now.label ?? "")}`], gl.btc_dominance ? ["BTC DOMINANCE", gl.btc_dominance.toFixed(1) + "%"] : null]);
    if (f.history && f.history.length > 5) h += `<div class="dp-sub2">CRYPTO FEAR & GREED, LAST 90 DAYS (0 = FEAR, 100 = GREED)</div>` + spark(f.history.map((x) => [new Date(x[0] * 1000).toISOString().slice(0, 10), x[1]]), (p) => `<b>${p[0]}</b><br>index ${p[1]}`, A);
    if (d.trending && d.trending.length) h += `<div class="dp-sub2">TRENDING COINS</div>` + table([["COIN", (r) => `<b>${esc(r.name)}</b> <span class="dim">${esc(r.symbol)}</span>`], ["RANK", (r) => r.rank || "—", 1], ["24H", (r) => `<span class="${r.pct24 < 0 ? "dn" : "up"}">${pc(r.pct24)}</span>`, 1]], d.trending);
    el.innerHTML = h || empty("no sentiment data");
  }

  // ------------------------------------------------------------------ page
  const STOCK = [
    ["fair", "FAIR VALUE ESTIMATE VS PRICE", "tick methods in or out", fair, "s12"], ["flow", "WHERE THE REVENUE AND CASH GO", "Sankey · funnel · waterfall", flow, "s12"], ["formulas", "FORMULA LAB", "40 calculations", formulas, "s12"],
    ["peers", "PEERS: BUBBLES AND PREMIUM", "click a bubble to open it", peers, "s12"], ["rank", "RANK VS EVERY SEC FILER", "radar", rank, "s12"], ["cone", "PRICE SCENARIOS (STATISTICAL RANGE)", "hover the fan", cone, "s12"],
    ["shortint", "SHORT INTEREST (FINRA)", "bi-monthly", shortint, "s6"], ["options", "OPTIONS DEEP DIVE (CBOE)", "gamma · skew · term structure", options, "s12"], ["trials", "CLINICAL TRIAL PIPELINE", "ClinicalTrials.gov", trials, "s12"],
    ["recalls", "VEHICLE RECALLS (NHTSA)", "", recalls, "s12"], ["language", "FILING LANGUAGE MONITOR", "SEC full-text search", language, "s6"], ["attention", "PUBLIC ATTENTION AND SENTIMENT", "Wikipedia · StockTwits · HN · GDELT", attention, "s12"],
    ["contracts", "US GOVERNMENT CONTRACTS", "USAspending", contracts, "s12"], ["insiders", "INSIDER TRANSACTIONS (SEC FORM 4)", "", insiders, "s12"], ["ark", "ARK INVEST HOLDINGS", "", ark, "s12"], ["macro", "MACRO: RATES, POSITIONING, OUTLOOK", "ECB · BIS · CFTC · IMF · FRED", macro, "s12"],
  ];
  const COIN = [
    ["coinmkt", "MARKET DATA", "CoinGecko", coinmkt, "s12"], ["cone", "PRICE SCENARIOS (STATISTICAL RANGE)", "hover the fan", cone, "s12"], ["perps", "DERIVATIVES: BYBIT PERPETUALS", "", perps, "s12"], ["deribit", "OPTIONS MARKET (DERIBIT)", "", deribit, "s12"],
    ["llama", "DEFI AND STABLECOIN LIQUIDITY", "DefiLlama", llama, "s12"], ["sentiment", "SENTIMENT AND BREADTH", "", sentiment, "s12"], ["macro", "MACRO: RATES AND POSITIONING", "ECB · BIS · CFTC · FRED", macro, "s12"],
  ];

  NX.deepDive = async function deepDive(body, sec, tok, alive) {
    const symbol = sec.symbol, defs = ((sec.type || "").toUpperCase() === "CRYPTOCURRENCY" ? COIN : STOCK);
    body.innerHTML = `<div class="dp"><div class="dp-top" id="dp-top"><b>DEEP DIVE · ${esc(sec.bbg || symbol)}</b><span id="dp-prog">starting the data fetch…</span><span class="sp"></span><button class="bb-chip" id="dp-re" title="Discard cached data and fetch everything again">↻ REFRESH</button></div>
      <div class="dp-nav" id="dp-nav">${defs.map((x) => `<a data-k="${x[0]}" class="pending">${x[1].split(" (")[0].split(":")[0]}</a>`).join("")}</div><div class="dp-grid" id="dp-grid">${defs.map((x, i) => card(x[0], i + 1, x[1], x[2], `<div class="dp-wait"><span></span>waiting for data…</div>`, x[4])).join("")}</div></div>`;
    wireTips(body);
    const have = new Set(), nav = (k, state) => { const a = body.querySelector(`#dp-nav a[data-k="${k}"]`); if (a) a.className = state; };
    body.querySelectorAll("#dp-nav a").forEach((a) => a.addEventListener("click", () => { const c = body.querySelector(`#dp-${a.dataset.k}`); if (c) c.scrollIntoView({ behavior: "smooth", block: "start" }); }));
    let live = true; const stillOn = () => live && body.isConnected && alive();
    const render = (k, data) => {
      const def = defs.find((x) => x[0] === k), cardEl = body.querySelector(`#dp-${k}`); if (!def || !cardEl) return; const b = cardEl.querySelector(".dp-b");
      if (!data || data.empty) { cardEl.classList.add("dp-none"); b.innerHTML = empty(data && data.error ? "this section failed to build" : "no data for this security"); nav(k, "none"); return; }
      try { def[3](b, data); nav(k, "ready"); } catch (e) { b.innerHTML = empty("could not draw this section: " + esc(e.message)); nav(k, "none"); }
      if (k === "fair") { const top = body.querySelector("#dp-top"); const f = data; const vtxt = `<span class="dp-chip" style="border-color:${f.up >= 0 ? G : R};color:${f.up >= 0 ? G : R}">FAIR VALUE ${money(f.fair)} (${pc(f.up, 0)})</span>`; if (!top.querySelector(".dp-chip")) top.insertAdjacentHTML("beforeend", vtxt); }
      if (k === "formulas") { const good = data.rows.filter((r) => r.tone === "good").length, bad = data.rows.filter((r) => r.tone === "bad").length; body.querySelector("#dp-top").insertAdjacentHTML("beforeend", `<span class="dp-chip">FORMULAS ${good}▲ ${bad}▼</span>`); }
    };
    const poll = async (first) => {
      if (!stillOn()) return;
      try {
        const r = await api(`/invest/deep/poll?symbol=${encodeURIComponent(symbol)}&have=${[...have].join(",")}`);
        Object.entries(r.sections).forEach(([k, v]) => { have.add(k); render(k, v); });
        const total = defs.length, done = have.size; body.querySelector("#dp-prog").textContent = r.pending.length ? `${done}/${total} sections ready · waiting on ${r.waiting_on.slice(0, 4).join(", ")}${r.waiting_on.length > 4 ? "…" : ""}` : `${done}/${total} sections · data fetched in ${r.age}s`;
        if (r.pending.length && stillOn()) setTimeout(() => poll(false), 1500);
      } catch (e) { body.querySelector("#dp-prog").textContent = "error: " + (e.message || e); }
    };
    body.querySelector("#dp-re").addEventListener("click", async () => { live = false; await api(`/invest/deep/start?symbol=${encodeURIComponent(symbol)}&refresh=true`); NX.deepDive(body, sec, tok, alive); });
    try { await api(`/invest/deep/start?symbol=${encodeURIComponent(symbol)}`); } catch (e) { body.querySelector("#dp-prog").textContent = "could not start: " + (e.message || e); return; }
    poll(true);
  };
})();
