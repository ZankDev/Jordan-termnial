/* SIDE DOCK — a panel fixed to the right edge on every page, holding 1–3 widgets the user picks:
   LIVE TRADES · NOSTRO ACCOUNTS · NEWS · SPIDER (news brain) · X / TWITTER TRACKER · SPOTIFY · WATCHLIST · TRADINGVIEW CHART · TODAY'S PLAN ·
   ECON CALENDAR · NOTES.
   Choice, order, width and open/closed state are remembered in localStorage. Each widget refreshes on its own timer and
   pauses while the dock is collapsed or the browser tab is hidden. The page content is narrowed so nothing is covered. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n, d = 0) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "");
  const MIN = 1, MAX = 3;
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(`sd_${k}`); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(`sd_${k}`, JSON.stringify(v)); } catch (e) { /* storage blocked */ } },
  };
  // on a phone the dock's expanded width is nearly the whole screen, so it starts collapsed there regardless of
  // the desktop preference — the toggle still opens it on demand, it just doesn't cover everything by default.
  const S = { open: window.innerWidth > 860 && ls.get("open", true), width: ls.get("width", 340), picked: ls.get("picked", ["live", "news"]), timers: {}, lastTrade: 0 };
  async function j(url, opt) { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status === 423 ? "WEALTH is locked (PIN)" : `HTTP ${r.status}`); return r.json(); }
  const ago = (ts) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`; };
  const hhmm = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; } };
  const shortName = (n) => String(n || "").split(" (was")[0].replace(/^combo·/, "").slice(0, 28);

  // ------------------------------------------------------------------ widgets
  const W = {
    live: {
      title: "LIVE TRADES", sub: "today's P&L, open positions and every closed trade", every: 8000,
      async render(el) {
        const [s, f] = await Promise.all([j("/api/trades/summary?since=today"), j("/api/trades/feed?limit=10")]);
        const newest = f.trades[0]?.id || 0, fresh = S.lastTrade && newest > S.lastTrade;
        S.lastTrade = Math.max(S.lastTrade, newest);
        el.innerHTML = `<div class="sd-kp"><div><span>${s.fallback ? "LAST SESSION" : "TODAY"}</span><b class="${cls(s.net_pnl)}">${money(s.net_pnl)}</b></div>
            <div><span>OPEN P&amp;L</span><b class="${cls(s.open_pnl)}">${money(s.open_pnl)}</b></div><div><span>TRADES</span><b>${s.trades}</b></div><div><span>WIN</span><b>${s.trades ? s.win_rate + "%" : "—"}</b></div></div>
          <div class="sd-h">OPEN · ${f.open_positions.length}</div>
          ${f.open_positions.map((p) => `<div class="sd-row" title="${esc(p.account_name)}"><span class="${p.side === "long" ? "up" : "dn"}">${p.side === "long" ? "LONG" : "SHORT"}</span><em>${esc(p.symbol)}</em><small>${esc(shortName(p.account_name))}</small><b class="${cls(p.unrealized_pnl)}">${money(p.unrealized_pnl, 2)}</b></div>`).join("") || '<div class="sd-dim">flat — no open positions</div>'}
          <div class="sd-h">CLOSED</div>
          ${f.trades.slice(0, 8).map((t, i) => `<div class="sd-row ${fresh && i === 0 ? "sd-new" : ""}" title="${esc(t.account_name)} · ${esc(t.exit_reason)}"><span class="sd-dim">${hhmm(t.exit_time)}</span><em>${esc(t.symbol || "")}</em><small>${esc(shortName(t.account_name))}</small><b class="${cls(t.pnl)}">${money(t.pnl)}</b></div>`).join("")}
          <a class="sd-more" data-go-tab="live">OPEN LIVE TRADES ›</a>`;
      },
    },
    nostro: {
      title: "NOSTRO ACCOUNTS", sub: "equity, net P&L and the leaders", every: 15000,
      async render(el) {
        const d = await j("/api/nostro/accounts-page"), t = d.totals;
        const acc = [...d.accounts].sort((a, b) => (b.net_pnl ?? 0) - (a.net_pnl ?? 0));
        const mx = Math.max(1, ...acc.map((a) => Math.abs(a.net_pnl || 0)));
        el.innerHTML = `<div class="sd-kp"><div><span>EQUITY</span><b>${money(t.equity).replace("+", "")}</b></div><div><span>NET</span><b class="${cls(t.net_pnl)}">${money(t.net_pnl)}</b></div>
            <div><span>TODAY</span><b class="${cls(t.today_net)}">${money(t.today_net)}</b></div><div><span>PASSED</span><b>${t.passed}/${t.in_eval + t.passed}</b></div></div>
          ${[...acc.slice(0, 5), ...(acc.length > 8 ? acc.slice(-3) : [])].map((a) => `<div class="sd-row sd-bar" title="${esc(a.name)}"><small>${esc(shortName(a.name))}</small><i class="${cls(a.net_pnl)}" style="width:${(45 * Math.abs(a.net_pnl || 0)) / mx}%"></i><b class="${cls(a.net_pnl)}">${money(a.net_pnl)}</b></div>`).join("")}
          <a class="sd-more" data-go-tab="acct">OPEN ACCOUNTS ›</a>`;
      },
    },
    news: {
      title: "NEWS", sub: "market headlines as they land", every: 120000,
      async render(el) {
        const { items } = await j("/api/capital/news");
        const tag = S.newsTag || "ALL", tags = ["ALL", ...new Set(items.map((i) => i.tag).filter(Boolean))].slice(0, 5);
        el.innerHTML = `<div class="sd-chips">${tags.map((t) => `<button class="${t === tag ? "on" : ""}" data-ntag="${esc(t)}">${esc(t)}</button>`).join("")}</div>
          ${items.filter((i) => tag === "ALL" || i.tag === tag).slice(0, 25).map((i) => `<a class="sd-news" href="${esc(i.link)}" target="_blank" rel="noopener noreferrer"><span>${esc(i.source)} · ${ago(i.ts)}</span>${esc(i.title)}</a>`).join("")}`;
        el.querySelectorAll("[data-ntag]").forEach((b) => b.addEventListener("click", () => { S.newsTag = b.dataset.ntag; W.news.render(el); }));
      },
    },
    spider: {
      // the news brain (spider.js): its brief, what it marked (click = full article in the reader), and a box to ask it
      title: "SPIDER · NEWS BRAIN", sub: "what the spider marked, its brief, and ask it anything", every: 60000,
      async render(el) {
        const s = await j("/api/spider/scan");
        const lv = S.spLevel || "all";
        const rows = s.marked.filter((m) => lv === "all" || m.level === lv).slice(0, 12);
        el.innerHTML = `<div class="sd-kp"><div><span>READ</span><b>${s.read}</b></div><div><span>CRITICAL</span><b class="spw-pk">${s.critical}</b></div>
            <div><span>IMPORTANT</span><b class="spw-cy">${s.important}</b></div><div><span>BRAIN</span><b>${s.thinking ? "…" : s.brain ? "ON" : "RULES"}</b></div></div>
          ${s.brief && s.brief.length ? `<div class="sd-h">WHAT MATTERS RIGHT NOW</div><ul class="spw-brief">${s.brief.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
          <div class="sd-chips">${[["all", "ALL MARKED"], ["critical", "CRITICAL"], ["important", "IMPORTANT"]].map(([k, t]) => `<button class="${k === lv ? "on" : ""}" data-splv="${k}">${t}</button>`).join("")}</div>
          ${rows.map((m) => `<div class="spw-m ${m.level}"><span class="spw-lv">${m.level === "critical" ? "CRIT" : "IMP"}</span><div><a href="${esc(m.link)}" target="_blank" rel="noopener noreferrer">${esc(m.title)}</a>
            <small>${esc(m.source)} · ${ago(m.ts)}${m.why ? ` · ${esc(m.why)}` : ""}${m.assets && m.assets.length ? ` · <b>${m.assets.map(esc).join(" ")}</b>` : ""}</small></div></div>`).join("") || '<div class="sd-dim">nothing marked yet</div>'}
          <div class="sd-edit"><input id="sd-spw-q" placeholder="ask the spider… or: report NVDA" maxlength="500"/><button id="sd-spw-go">ASK</button></div>
          ${S.spAsk ? `<div class="spw-q">${esc(S.spAsk.q)}</div><div class="spw-a">${S.spAsk.a == null ? '<span class="sd-dim">thinking…</span>' : esc(S.spAsk.a)}</div>` : ""}
          <a class="sd-more" data-spw-open="1">OPEN THE SPIDER PANEL ›</a>`;
        el.querySelectorAll("[data-splv]").forEach((b) => b.addEventListener("click", () => { S.spLevel = b.dataset.splv; W.spider.render(el); }));
        el.querySelector("[data-spw-open]").addEventListener("click", () => window.NX && NX.spider && NX.spider.open());
        const ask = async () => {
          const q = $("#sd-spw-q", el).value.trim(); if (!q) return;
          S.spAsk = { q, a: null }; W.spider.render(el);
          try { const r = await j("/api/spider/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ q }) }); S.spAsk.a = r.answer; if (r.report && window.NX && NX.spider) NX.spider.report(r.report); }
          catch (e) { S.spAsk.a = `I couldn't answer: ${e.message || e}`; }
          W.spider.render(el);
        };
        $("#sd-spw-go", el).addEventListener("click", ask);
        $("#sd-spw-q", el).addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
      },
    },
    x: {
      title: "X / TWITTER TRACKER", sub: "timelines of the accounts you follow", every: 0,
      render(el) {
        const handles = ls.get("x_handles", ["DeItaone", "unusual_whales", "zerohedge", "elonmusk"]);
        const cur = ls.get("x_cur", handles[0]);
        el.innerHTML = `<div class="sd-chips">${handles.map((h) => `<button class="${h === cur ? "on" : ""}" data-xh="${esc(h)}">@${esc(h)}</button>`).join("")}<button data-xedit="1" title="edit the list">EDIT</button></div>
          <div class="sd-x" id="sd-x-${Math.random().toString(36).slice(2)}"><a class="twitter-timeline" data-theme="dark" data-chrome="noheader nofooter transparent" data-height="1200" href="https://twitter.com/${encodeURIComponent(cur)}">Loading @${esc(cur)}…</a>
            <p class="sd-dim">X shows timelines only when you're logged in to X in this browser. <a href="https://x.com/${encodeURIComponent(cur)}" target="_blank" rel="noopener noreferrer">Open @${esc(cur)} on X ›</a></p></div>`;
        const load = () => { if (window.twttr && window.twttr.widgets) window.twttr.widgets.load(el); };
        if (!window.twttr) { const s = document.createElement("script"); s.src = "https://platform.twitter.com/widgets.js"; s.async = true; s.onload = load; document.head.appendChild(s); window.twttr = window.twttr || {}; } else load();
        el.querySelectorAll("[data-xh]").forEach((b) => b.addEventListener("click", () => { ls.set("x_cur", b.dataset.xh); W.x.render(el); }));
        el.querySelector("[data-xedit]").addEventListener("click", () => {
          const box = document.createElement("div"); box.className = "sd-edit";
          box.innerHTML = `<input value="${esc(handles.join(", "))}" placeholder="handles, comma separated"/><button>SAVE</button>`;
          el.prepend(box); box.querySelector("input").focus();
          box.querySelector("button").addEventListener("click", () => { const v = box.querySelector("input").value.split(/[,\s]+/).map((h) => h.replace(/^@/, "").trim()).filter(Boolean).slice(0, 8); if (v.length) { ls.set("x_handles", v); ls.set("x_cur", v[0]); } W.x.render(el); });
        });
      },
    },
    spotify: {
      title: "SPOTIFY", sub: "music while you trade", every: 0,
      render(el) {
        const PRESETS = [["LOFI BEATS", "playlist/37i9dQZF1DWWQRwui0ExPn"], ["DEEP FOCUS", "playlist/37i9dQZF1DWZeKCadgRdKQ"], ["TECHNO", "playlist/37i9dQZF1DX6J5NfMJS675"], ["PEACEFUL PIANO", "playlist/37i9dQZF1DX4sWSpwq3LiO"]];
        const cur = ls.get("sp_uri", PRESETS[0][1]);
        el.innerHTML = `<div class="sd-chips">${PRESETS.map(([n, u]) => `<button class="${u === cur ? "on" : ""}" data-sp="${u}">${n}</button>`).join("")}</div>
          <div class="sd-edit"><input id="sd-sp-in" placeholder="paste a Spotify playlist / album / track link"/><button id="sd-sp-go">PLAY</button></div>
          <iframe class="sd-sp" src="https://open.spotify.com/embed/${esc(cur)}?utm_source=generator&theme=0" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>
          <p class="sd-dim">Log in to Spotify in this browser for full tracks (otherwise 30-second previews).</p>`;
        el.querySelectorAll("[data-sp]").forEach((b) => b.addEventListener("click", () => { ls.set("sp_uri", b.dataset.sp); W.spotify.render(el); }));
        $("#sd-sp-go", el).addEventListener("click", () => {
          const m = $("#sd-sp-in", el).value.match(/(playlist|album|track|artist|show|episode)[/:]([A-Za-z0-9]+)/);
          if (!m) return window.showToast && showToast("That doesn't look like a Spotify link");
          ls.set("sp_uri", `${m[1]}/${m[2]}`); W.spotify.render(el);
        });
      },
    },
    watch: {
      // the terminal's own watchlist (INVESTING › SECURITY ☆ WATCH, key "nostro.watch"): same list everywhere
      title: "WATCHLIST", sub: "your ☆ WATCH list from the terminal, live", every: 30000,
      async render(el) {
        const KEY = "nostro.watch";
        const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } };
        const write = (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* storage blocked */ } };
        const syms = read();
        const sort = ls.get("wl_sort", "list");
        const addBox = `<div class="sd-edit"><input id="sd-wl-in" placeholder="add symbol: AAPL, ETH-USD, ES=F…"/><button id="sd-wl-add">+ ADD</button></div>`;
        if (!syms.length) {
          el.innerHTML = `<div class="sd-dim">Your watchlist is empty. Add a symbol here, or open any security and press ☆ WATCH.</div>${addBox}`;
        } else {
          const { rows } = await j(`/api/invest/quotes?symbols=${encodeURIComponent(syms.join(","))}`);
          const by = Object.fromEntries(rows.map((r) => [r.symbol, r]));
          let list = syms.map((s) => by[s] || { symbol: s });
          if (sort === "pct") list = [...list].sort((a, b) => (b.pct ?? -1e9) - (a.pct ?? -1e9));
          else if (sort === "az") list = [...list].sort((a, b) => a.symbol.localeCompare(b.symbol));
          const spark = (v) => { if (!v || v.length < 2) return "<span></span>"; const lo = Math.min(...v), hi = Math.max(...v), s = hi - lo || 1; return `<svg viewBox="0 0 60 18" class="sd-spk ${v[v.length - 1] >= v[0] ? "up" : "dn"}"><polyline points="${v.map((x, i) => `${((i / (v.length - 1)) * 60).toFixed(1)},${(17 - ((x - lo) / s) * 16).toFixed(1)}`).join(" ")}"/></svg>`; };
          const up = list.filter((r) => r.pct > 0).length, dn = list.filter((r) => r.pct < 0).length;
          el.innerHTML = `<div class="sd-chips"><span class="sd-dim">${syms.length} symbols · <span class="up">${up} up</span> · <span class="dn">${dn} down</span></span><span class="sd-sp"></span>${[["list", "MY ORDER"], ["pct", "% CHG"], ["az", "A–Z"]].map(([k, t]) => `<button class="${k === sort ? "on" : ""}" data-wsort="${k}">${t}</button>`).join("")}</div>
            ${list.map((r) => `<div class="sd-row sd-q" data-sym="${esc(r.symbol)}" title="${esc(r.name || r.symbol)} — click to open"><em>${esc(r.symbol)}</em>${spark((r.spark || []).slice(-30))}<b>${isN(r.price) ? r.price.toLocaleString(undefined, { maximumFractionDigits: r.price < 1 ? 4 : 2 }) : "—"}</b><span class="${cls(r.pct)}">${isN(r.pct) ? (r.pct > 0 ? "+" : "") + r.pct.toFixed(2) + "%" : "no quote"}</span><button class="sd-x-btn" data-wdel="${esc(r.symbol)}" title="remove from the watchlist">✕</button></div>`).join("")}
            ${addBox}`;
        }
        el.querySelectorAll("[data-sym]").forEach((r) => r.addEventListener("click", (e) => { if (!e.target.closest("[data-wdel]") && window.openSecurity) window.openSecurity(r.dataset.sym); }));
        el.querySelectorAll("[data-wdel]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); write(read().filter((s) => s !== b.dataset.wdel)); W.watch.render(el); }));
        el.querySelectorAll("[data-wsort]").forEach((b) => b.addEventListener("click", () => { ls.set("wl_sort", b.dataset.wsort); W.watch.render(el); }));
        const add = () => { const v = $("#sd-wl-in", el).value.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean); if (!v.length) return; const cur = read(); write([...cur, ...v.filter((s) => !cur.includes(s))]); W.watch.render(el); };
        $("#sd-wl-add", el).addEventListener("click", add);
        $("#sd-wl-in", el).addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
      },
    },
    tv: {
      title: "TRADINGVIEW CHART", sub: "a live chart that stays next to every page", every: 0,
      render(el) {
        const PRE = [["ES", "CME_MINI:ES1!"], ["NQ", "CME_MINI:NQ1!"], ["SPY", "AMEX:SPY"], ["BTC", "BINANCE:BTCUSDT"], ["GOLD", "COMEX:GC1!"]];
        const cur = ls.get("tv_sym", PRE[0][1]), iv = ls.get("tv_iv", "5");
        el.innerHTML = `<div class="sd-chips">${PRE.map(([n, s]) => `<button class="${s === cur ? "on" : ""}" data-tvs="${s}">${n}</button>`).join("")}<span class="sd-sp"></span>${["1", "5", "15", "60", "D"].map((x) => `<button class="${x === iv ? "on" : ""}" data-tvi="${x}">${x === "D" ? "1D" : x + "m"}</button>`).join("")}</div>
          <iframe class="sd-tv" src="https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(cur)}&interval=${iv}&theme=dark&style=1&timezone=America%2FNew_York&hide_side_toolbar=1&hide_top_toolbar=0&saveimage=0&toolbarbg=000000" loading="lazy"></iframe>`;
        el.querySelectorAll("[data-tvs]").forEach((b) => b.addEventListener("click", () => { ls.set("tv_sym", b.dataset.tvs); W.tv.render(el); }));
        el.querySelectorAll("[data-tvi]").forEach((b) => b.addEventListener("click", () => { ls.set("tv_iv", b.dataset.tvi); W.tv.render(el); }));
      },
    },
    plan: {
      title: "TODAY'S PLAN", sub: "tick the road-to-₪1M tasks from anywhere", every: 60000,
      async render(el) {
        const o = await j("/api/wealth/plan"), ds = o.day_stats;
        el.innerHTML = `<div class="sd-prog"><i style="width:${ds.pct}%"></i><span>${ds.done}/${ds.total} tasks · ${ds.pct}% · streak ${o.streak}</span></div>
          ${o.daily.map((t) => `<label class="sd-task ${t.done ? "on" : ""}" data-tk="${t.id}"><span>${t.done ? "✓" : ""}</span>${esc(t.title)}</label>`).join("")}
          ${o.weekly.filter((t) => !t.done).length ? `<div class="sd-h">THIS WEEK</div>${o.weekly.filter((t) => !t.done).map((t) => `<label class="sd-task" data-tk="${t.id}"><span></span>${esc(t.title)}</label>`).join("")}` : ""}`;
        el.querySelectorAll("[data-tk]").forEach((lab) => lab.addEventListener("click", async (e) => {
          e.preventDefault(); lab.classList.toggle("on"); lab.querySelector("span").textContent = lab.classList.contains("on") ? "✓" : "";
          try { await j("/api/wealth/plan/tick", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task_id: +lab.dataset.tk, done: lab.classList.contains("on") }) }); } catch (err) { /* next refresh corrects it */ }
          W.plan.render(el);
        }));
      },
    },
    cal: {
      title: "ECON CALENDAR", sub: "the next market-moving releases", every: 300000,
      async render(el) {
        const { events } = await j("/api/market/calendar?back=0&ahead=7");
        const now = Date.now() / 1000, next = events.filter((e) => e.ts >= now - 3600 && (e.impact === "High" || e.impact === "Medium")).slice(0, 18);
        el.innerHTML = next.map((e) => { const d = new Date(e.ts * 1000), soon = e.ts - now < 3600 && e.ts > now;
          return `<div class="sd-ev ${e.impact === "High" ? "hi" : ""} ${soon ? "soon" : ""}"><span>${d.toLocaleDateString([], { weekday: "short" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><em>${esc(e.country)}</em><b>${esc(e.title)}</b><small>${e.actual ? `A ${esc(e.actual)}` : e.forecast ? `F ${esc(e.forecast)}` : ""}${e.previous ? ` · P ${esc(e.previous)}` : ""}</small></div>`; }).join("") || '<div class="sd-dim">No high / medium impact events in the next 7 days.</div>';
      },
    },
    notes: {
      title: "NOTES", sub: "a scratchpad that stays on this computer", every: 0,
      render(el) {
        el.innerHTML = `<textarea class="sd-notes" placeholder="ideas, levels, reminders…">${esc(ls.get("notes", ""))}</textarea><div class="sd-dim" id="sd-notes-st">saved on this computer</div>`;
        let t = null;
        $("textarea", el).addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { ls.set("notes", e.target.value); const st = $("#sd-notes-st", el); if (st) st.textContent = `saved ${new Date().toLocaleTimeString()}`; }, 400); });
      },
    },
  };

  // ------------------------------------------------------------------ frame
  function stopAll() { Object.values(S.timers).forEach(clearInterval); S.timers = {}; }
  async function paint(k) {
    const body = $(`#sd-b-${k}`); if (!body) return;
    try { await W[k].render(body); } catch (e) { body.innerHTML = `<div class="sd-dim">${esc(e.message || e)}</div>`; }
  }
  function renderSlots() {
    stopAll();
    const slots = $("#sd-slots");
    slots.innerHTML = S.picked.map((k, i) => `<section class="sd-slot" data-k="${k}"><header><b>${W[k].title}</b><span class="sd-sp"></span>
        ${S.picked.length > 1 ? `<button data-mv="${k}|-1" title="move up" ${i === 0 ? "disabled" : ""}>▲</button><button data-mv="${k}|1" title="move down" ${i === S.picked.length - 1 ? "disabled" : ""}>▼</button>` : ""}
        <button data-rf="${k}" title="refresh">↻</button></header><div class="sd-body" id="sd-b-${k}"><div class="sd-dim">Loading…</div></div></section>`).join("");
    S.picked.forEach((k) => {
      paint(k);
      if (W[k].every) S.timers[k] = setInterval(() => { if (S.open && !document.hidden) paint(k); }, W[k].every);
    });
  }
  function renderPicker() {
    const p = $("#sd-pick");
    p.innerHTML = `<div class="sd-ph"><b>CHOOSE WIDGETS</b><span>${S.picked.length}/${MAX} · minimum ${MIN}, maximum ${MAX}</span></div>
      ${Object.entries(W).map(([k, w]) => { const on = S.picked.includes(k), dis = (!on && S.picked.length >= MAX) || (on && S.picked.length <= MIN);
        return `<button class="sd-opt ${on ? "on" : ""}" data-pick="${k}" ${dis ? "disabled" : ""}><i>${on ? "✓" : ""}</i><span><b>${w.title}</b><small>${w.sub}</small></span></button>`; }).join("")}
      <p class="sd-dim">${S.picked.length >= MAX ? "Maximum 3 — remove one to add another." : S.picked.length <= MIN ? "At least 1 widget stays on." : ""}</p>`;
  }
  function applyLayout() {
    const dock = $("#sd"), top = document.querySelector("header.topbar");
    const h = top ? top.getBoundingClientRect().height : 60;
    dock.style.top = `${h}px`;
    dock.style.width = S.open ? `${S.width}px` : "26px";
    document.documentElement.style.setProperty("--sd-w", S.open ? `${S.width}px` : "26px");
    document.body.classList.toggle("sd-open", S.open);
    document.body.classList.add("sd-on");
    $("#sd-toggle").textContent = S.open ? "›" : "‹";
    $("#sd-toggle").title = S.open ? "hide the side dock" : "show the side dock";
  }
  function build() {
    const d = document.createElement("aside");
    d.id = "sd";
    d.innerHTML = `<div class="sd-grip" id="sd-grip" title="drag to resize"></div>
      <button class="sd-toggle" id="sd-toggle"></button>
      <div class="sd-in"><div class="sd-top"><b>DOCK</b><span class="sd-dim">${S.picked.length} of ${MAX}</span><span class="sd-sp"></span><button id="sd-cfg" title="choose widgets">WIDGETS</button></div>
        <div class="sd-pick" id="sd-pick" hidden></div><div class="sd-slots" id="sd-slots"></div></div>`;
    document.body.appendChild(d);
    $("#sd-toggle").addEventListener("click", () => { S.open = !S.open; ls.set("open", S.open); applyLayout(); if (S.open) S.picked.forEach((k) => W[k].every && paint(k)); });  // never reload the music / chart iframes
    $("#sd-cfg").addEventListener("click", () => { const p = $("#sd-pick"); p.hidden = !p.hidden; if (!p.hidden) renderPicker(); });
    d.addEventListener("click", (e) => {
      const pk = e.target.closest("[data-pick]");
      if (pk && !pk.disabled) {
        const k = pk.dataset.pick;
        if (S.picked.includes(k)) { if (S.picked.length > MIN) S.picked = S.picked.filter((x) => x !== k); }
        else if (S.picked.length < MAX) S.picked = [...S.picked, k];
        ls.set("picked", S.picked); renderPicker(); renderSlots();
        $(".sd-top .sd-dim").textContent = `${S.picked.length} of ${MAX}`;
        return;
      }
      const mv = e.target.closest("[data-mv]");
      if (mv) { const [k, dir] = mv.dataset.mv.split("|"), i = S.picked.indexOf(k), n = i + +dir; if (n >= 0 && n < S.picked.length) { const a = [...S.picked]; [a[i], a[n]] = [a[n], a[i]]; S.picked = a; ls.set("picked", a); renderSlots(); } return; }
      const rf = e.target.closest("[data-rf]"); if (rf) { paint(rf.dataset.rf); return; }
      const go = e.target.closest("[data-go-tab]"); if (go) { e.preventDefault(); document.querySelector(`.tab[data-tab="${go.dataset.goTab}"]`)?.click(); }
    });
    // resize by dragging the left edge
    let drag = null;
    $("#sd-grip").addEventListener("pointerdown", (e) => { if (!S.open) return; drag = { x: e.clientX, w: S.width }; e.target.setPointerCapture(e.pointerId); document.body.classList.add("sd-drag"); });
    $("#sd-grip").addEventListener("pointermove", (e) => { if (!drag) return; S.width = Math.max(260, Math.min(640, drag.w + (drag.x - e.clientX))); applyLayout(); });
    $("#sd-grip").addEventListener("pointerup", () => { if (!drag) return; drag = null; ls.set("width", S.width); document.body.classList.remove("sd-drag"); });
    const top = document.querySelector("header.topbar");
    if (top && window.ResizeObserver) new ResizeObserver(applyLayout).observe(top);
    // starring a security (☆ WATCH) updates the watchlist widget right away — here and in other tabs
    window.addEventListener("storage", (e) => { if (e.key === "nostro.watch" && S.picked.includes("watch")) paint("watch"); });
    document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("#sx-watch") && S.picked.includes("watch")) setTimeout(() => paint("watch"), 150); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && S.open) S.picked.forEach((k) => W[k].every && paint(k)); });
    S.picked = S.picked.filter((k) => W[k]).slice(0, MAX);
    if (!S.picked.length) S.picked = ["live"];
    applyLayout();
    renderSlots();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build); else build();
})();
