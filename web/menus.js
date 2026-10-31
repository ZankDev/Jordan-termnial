/* NEWS TV (live news wall + geo-tagged headlines), SENTIMENT & ONCHAIN (fear/greed, funding, liquidations, Bitcoin network, whales). Depends on NX (invest.js) and api() (dashboard.js). */
(function () {
  "use strict";
  const NX = window.NX;
  const { $, esc, dir, num, px, pct, big, signed } = NX;
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const alive = (sel) => document.body.contains($(sel));
  const flagImg = (cc) => (cc && /^[A-Za-z]{2}$/.test(cc) ? `<img class="wd-fl" src="https://flagcdn.com/w40/${cc.toLowerCase()}.png" alt="" onerror="this.style.visibility='hidden'"/>` : "");
  const kpi = (l, v, s = "", c = "") => `<div class="bb-kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
  const usd = (n) => (n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}k` : `$${n.toFixed(0)}`);

  // ================================================================== NEWS TV
  const TVK = "nx_tv";
  const LAYOUTS = { "1": 1, "2 × 2": 4, "3 × 2": 6, "3 × 3": 9, "1 + 3": 4 };
  const yt = (id, mute) => (/^UC[\w-]{22}$/.test(id) ? `https://www.youtube.com/embed/live_stream?channel=${id}&autoplay=1&mute=${mute ? 1 : 0}&enablejsapi=1&playsinline=1&rel=0` : `https://www.youtube.com/embed/${id}?autoplay=1&mute=${mute ? 1 : 0}&enablejsapi=1&playsinline=1&rel=0`);
  const ytLink = (id) => (/^UC[\w-]{22}$/.test(id) ? `https://www.youtube.com/channel/${id}/live` : `https://www.youtube.com/watch?v=${id}`);
  const cmd = (fr, func) => { try { fr.contentWindow.postMessage(JSON.stringify({ event: "command", func, args: "" }), "*"); } catch (e) { /* not loaded */ } };
  const TV = { built: false, catalog: [], st: null, active: false, ok: lget("nx_tv_ok", { ts: 0, res: {} }), probing: false };

  // Many channels refuse to be embedded or are blocked in some countries. Load each one in a hidden muted player and read what YouTube reports:
  // player state 1/3 = it plays here, error 100/101/150 = it will not play here. The result is cached for 12 h.
  async function probeChannels(force) {
    if (TV.probing || (!force && Date.now() - TV.ok.ts < 12 * 3600 * 1000 && Object.keys(TV.ok.res).length)) return;
    TV.probing = true; const note = $("#tv-note"), res = {}, list = TV.catalog, holder = document.createElement("div");
    holder.style.cssText = "position:fixed;left:-9999px;top:0;width:320px;height:180px"; document.body.appendChild(holder);
    const onMsg = (e) => {
      if (!String(e.origin).includes("youtube")) return; let d; try { d = JSON.parse(e.data); } catch (x) { return; }
      const fr = [...holder.querySelectorAll("iframe")].find((f) => f.contentWindow === e.source); if (!fr) return; const id = fr.dataset.id;
      if (d.event === "onError") res[id] = false; else if (res[id] === undefined && ((d.event === "onStateChange" && [1, 3].includes(d.info)) || (d.event === "infoDelivery" && d.info && [1, 3].includes(d.info.playerState)))) res[id] = true;
    };
    window.addEventListener("message", onMsg);
    for (let i = 0; i < list.length && alive("#tv-grid"); i += 8) {
      const batch = list.slice(i, i + 8);
      if (note) note.textContent = `Checking which channels play on this network… ${Math.min(i + 8, list.length)}/${list.length}`;
      batch.forEach((c) => { const f = document.createElement("iframe"); f.dataset.id = c.id; f.allow = "autoplay"; f.src = yt(c.id, true).replace("enablejsapi=1", `enablejsapi=1&origin=${encodeURIComponent(location.origin)}`); f.onload = () => { try { f.contentWindow.postMessage(JSON.stringify({ event: "listening", id: c.id }), "*"); } catch (x) { /* ignore */ } }; holder.appendChild(f); });
      await new Promise((r) => setTimeout(r, 13000));
      holder.innerHTML = "";
    }
    window.removeEventListener("message", onMsg); holder.remove(); TV.probing = false;
    TV.ok = { ts: Date.now(), res }; lset("nx_tv_ok", TV.ok);
    // swap any tile that shows a channel which will not play for the next working channel that is not on screen
    const good = list.filter((c) => res[c.id] === true).map((c) => c.id), shown = new Set(TV.st.tiles);
    TV.st.tiles = TV.st.tiles.map((id) => { if (res[id] !== false) return id; const nx = good.find((g) => !shown.has(g)); if (nx) { shown.add(nx); return nx; } return id; });
    lset(TVK, TV.st);
    if (alive("#tv-grid")) { drawTiles(); const n = $("#tv-note"); if (n) n.textContent = `${good.length} of ${list.length} channels play on this network; the rest are blocked for embedding or in your country (marked ✗).`; }
  }

  async function buildTV() {
    const el = $("#view-newstv");
    TV.catalog = (await api("/world/tv")).channels;
    const def = ["UCIALMKvObZNtJ6AmdCLP7Lg", "UCoMdktPbSTixAyNGwb-UYkQ", "UCNye-wNBqNL5ZzHSJj3l8Bg", "UCvHDpsWKADrDia0c99X37vg", "UCrp_UI8XtuYfpiqluWLD7Lw", "UCQfwfsi5VrQ8yKZ-UWmAEFg", "UCknLrEdhRCp1aegoMqRaCZg", "UCKqFqiCe1dCUxRe0_YNZ6gg", "UCLA_DiR1FfKNvjuUpBHmylQ"];
    TV.st = Object.assign({ layout: "2 × 2", tiles: def, audio: -1, custom: [] }, lget(TVK, {}));
    el.innerHTML = `<div class="tv-shell"><div class="tv-main"><div class="tv-bar"><span class="tv-t">NEWS TV</span>${NX.chipsHtml(Object.keys(LAYOUTS).map((k) => [k, k]), TV.st.layout)}<button class="bb-chip" id="tv-check" title="Test which channels play here">CHECK CHANNELS</button><span class="sp"></span><span class="dim" id="tv-note">Streams are YouTube embeds: a channel that is not live right now shows YouTube's own message.</span></div><div class="tv-grid" id="tv-grid"></div></div>
      <div class="tv-side"><div class="bb-panel" style="--i:1"><div class="bb-ph"><span class="bb-n">1</span>WORLD HEADLINES<span class="sp"></span><span class="meta">click: fly there</span></div><div class="bb-pb tv-heads" id="tv-heads">${NX.load()}</div></div>
        <div class="bb-panel" style="--i:2"><div class="bb-ph"><span class="bb-n">2</span>ADD A STREAM</div><div class="bb-pad"><div class="form-row"><label>Name</label><input id="tv-n" style="width:150px"/></div><div class="form-row"><label>Channel ID (UC…) or video ID</label><input id="tv-i" style="width:150px" placeholder="UCxxxxxxxxxxxxxxxxxxxxxx"/></div><button class="btn" id="tv-add">ADD</button></div></div></div></div>`;
    NX.wire($(".tv-bar", el), (f) => { TV.st.layout = f; lset(TVK, TV.st); drawTiles(); });
    $("#tv-add").addEventListener("click", () => { const n = $("#tv-n").value.trim(), i = $("#tv-i").value.trim(); if (!n || !/^(UC[\w-]{22}|[\w-]{11})$/.test(i)) return showToast("Give a name and a channel ID (UC…, 24 chars) or a video ID (11 chars)"); TV.st.custom.push({ name: n, id: i, group: "MINE" }); lset(TVK, TV.st); $("#tv-n").value = $("#tv-i").value = ""; drawTiles(); });
    $("#tv-check").addEventListener("click", () => probeChannels(true));
    drawTiles(); loadHeads(); setTimeout(() => probeChannels(false), 2500);
    setInterval(() => { if (alive("#tv-heads") && TV.active) loadHeads(); }, 600000);
  }
  const all = () => [...TV.catalog, ...TV.st.custom];
  function drawTiles() {
    const g = $("#tv-grid"), lay = TV.st.layout, n = LAYOUTS[lay], cat = all();
    g.className = `tv-grid l${lay.replace(/\W/g, "")}`;
    while (TV.st.tiles.length < n) TV.st.tiles.push(cat[TV.st.tiles.length % cat.length].id);
    g.innerHTML = TV.st.tiles.slice(0, n).map((id, i) => {
      const c = cat.find((x) => x.id === id) || cat[0], groups = [...new Set(cat.map((x) => x.group))];
      return `<div class="tv-tile ${TV.st.audio === i ? "aud" : ""}" data-i="${i}"><div class="tv-h"><select>${groups.map((gr) => `<optgroup label="${gr}">${cat.filter((x) => x.group === gr).map((x) => `<option value="${x.id}" ${x.id === id ? "selected" : ""}>${TV.ok.res[x.id] === true ? "✓ " : TV.ok.res[x.id] === false ? "✗ " : ""}${esc(x.name)}</option>`).join("")}</optgroup>`).join("")}</select><span class="sp"></span><button class="tv-b" data-a="snd" title="Sound on this tile">${TV.st.audio === i ? "🔊" : "🔈"}</button><a class="tv-b" href="${ytLink(id)}" target="_blank" rel="noopener" title="Open on YouTube">↗</a></div><iframe src="${yt(id, TV.st.audio !== i)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe></div>`;
    }).join("");
    g.querySelectorAll(".tv-tile").forEach((t) => {
      const i = Number(t.dataset.i);
      t.querySelector("select").addEventListener("change", (e) => { TV.st.tiles[i] = e.target.value; lset(TVK, TV.st); t.querySelector("iframe").src = yt(e.target.value, TV.st.audio !== i); t.querySelector("a").href = ytLink(e.target.value); });
      t.querySelector('[data-a="snd"]').addEventListener("click", () => {
        const on = TV.st.audio === i; TV.st.audio = on ? -1 : i; lset(TVK, TV.st);
        g.querySelectorAll(".tv-tile").forEach((x, j) => { const fr = x.querySelector("iframe"), me = TV.st.audio === j; x.classList.toggle("aud", me); x.querySelector('[data-a="snd"]').textContent = me ? "🔊" : "🔈"; cmd(fr, me ? "unMute" : "mute"); if (me) cmd(fr, "setVolume"); });
      });
    });
  }
  async function loadHeads() {
    const box = $("#tv-heads"); if (!box) return;
    try {
      const r = await api("/world/newsheat"), rows = [];
      Object.entries(r.heads || {}).forEach(([cc, list]) => list.forEach((h) => rows.push({ ...h, cc })));
      rows.sort((a, b) => b.ts + b.sev * 2100 - (a.ts + a.sev * 2100));
      box.innerHTML = rows.slice(0, 70).map((h) => `<div class="tv-head ${h.sev ? "sev" : ""}" data-cc="${h.cc}"><span>${flagImg(h.cc)} ${esc(h.title)}</span><em>${esc(h.source || "")} · ${NX.ago(h.ts)} <a href="${esc(h.link)}" target="_blank" rel="noopener" title="Open the article">↗</a></em></div>`).join("") || '<div class="bb-empty">The first news sample is still building (about 2 minutes after the server starts).</div>';
      box.querySelectorAll(".tv-head").forEach((d) => d.addEventListener("click", (e) => { if (e.target.closest("a")) return; flyTo(d.dataset.cc); }));
    } catch (e) { box.innerHTML = NX.err(e); }
  }
  function flyTo(cc) { const b = document.querySelector('.tab[data-tab="world"]'); if (b) b.click(); if (NX.world) NX.world.focus(cc); }
  function tvSuspend(on) { document.querySelectorAll("#tv-grid iframe").forEach((f) => { if (on) { if (!f.dataset.src) { f.dataset.src = f.src; f.src = "about:blank"; } } else if (f.dataset.src) { f.src = f.dataset.src; delete f.dataset.src; } }); }

  // ================================================================== SENTIMENT & ONCHAIN
  const OC = { built: false, liq: { ws: null, rows: [], long: [], short: [] }, whales: [], seen: new Set(), price: null, sort: "turnover", desc: true, perps: [] };
  const panel = (n, title, meta, id, cls = "s4") => `<div class="bb-panel ${cls}" style="--i:${n}"><div class="bb-ph"><span class="bb-n">${n}</span>${title}<span class="sp"></span><span class="meta">${meta}</span></div><div class="bb-pb" id="${id}">${NX.load()}</div></div>`;
  async function buildOnchain() {
    const el = $("#view-onchain");
    el.innerHTML = `<div class="bb-grid oc-grid">${panel(1, "FEAR &amp; GREED", "crypto · alternative.me", "oc-fng")}${panel(2, "STOCK SENTIMENT", "VIX · SPY options", "oc-stock")}${panel(3, "TRENDING COINS", "CoinGecko searches, 24 h", "oc-trend")}
      ${panel(4, "PERPETUAL FUTURES · FUNDING &amp; OPEN INTEREST", "Bybit USDT perps · click a header to sort, a symbol for its chart", "oc-perps", "s8 tall-oc")}${panel(5, "LIQUIDATIONS · LIVE", "Binance futures stream", "oc-liq", "s4 tall-oc")}
      ${panel(6, "BITCOIN NETWORK", "blockstream.info", "oc-net", "s6")}${panel(7, "BITCOIN WHALES · UNCONFIRMED", "moves of 25 BTC or more, as they are broadcast", "oc-whale", "s6")}</div>
      <div class="dim" style="padding:6px 2px;font-size:10.5px">Not included because they are paid data: exchange inflows/outflows and named-wallet tracking. Funding shown is per funding period and annualised; liquidations and whale lists only fill while this page is open.</div>`;
    fng(); stocks(); trending(); perps(); btcNet(); liqStart(); whaleLoop();
    setInterval(() => { if (alive("#oc-perps") && OC.active) { perps(); btcNet(); } }, 30000); setInterval(() => { if (alive("#oc-fng") && OC.active) { fng(); trending(); } }, 600000);
  }
  async function fng() {
    const box = $("#oc-fng");
    try {
      const r = await api("/onchain/fng"), n = r.now, col = n[1] < 25 ? "dn" : n[1] > 75 ? "up" : "";
      box.innerHTML = `<div class="bb-pad"><div class="oc-fng"><b class="${col}">${n[1]}</b><span>${esc(n[2].toUpperCase())}</span></div><div class="oc-gauge"><i style="left:${n[1]}%"></i></div><div class="oc-scale"><span>EXTREME FEAR</span><span>NEUTRAL</span><span>EXTREME GREED</span></div><div id="oc-fng-c" style="margin-top:6px"></div></div>`;
      NX.lineChart($("#oc-fng-c"), r.history.map((x) => [x[0], x[1]]), { height: 130, fmt: (v) => num(v, 0), color: "#ffa028" });
    } catch (e) { box.innerHTML = NX.err(e); }
  }
  async function stocks() {
    const box = $("#oc-stock");
    try {
      const q = await api("/invest/quotes?symbols=%5EVIX,SPY,QQQ"), row = (s) => q.rows.find((r) => r.symbol === s) || {};
      const vix = row("^VIX");
      box.innerHTML = `<div class="bb-kpis six" style="grid-template-columns:1fr 1fr;margin:0">${kpi("VIX", num(vix.price, 2), pct(vix.pct), vix.price > 25 ? "dn" : vix.price < 15 ? "up" : "")}${kpi("SPY", num(row("SPY").price, 2), pct(row("SPY").pct), dir(row("SPY").pct))}</div><div class="bb-pad" id="oc-pc"><span class="dim">Loading put/call…</span></div>`;
      api("/market/options/vol?symbol=SPY").then((o) => { const pc = o.put_call_volume; $("#oc-pc").innerHTML = `<div class="oc-line"><span>SPY PUT / CALL (volume)</span><b class="${pc > 1.1 ? "dn" : pc < 0.7 ? "up" : ""}">${num(pc, 2)}</b></div><div class="oc-line"><span>SPY 30D IMPLIED VOL</span><b>${o.iv30 ? num(o.iv30, 1) + "%" : "—"}</b></div><div class="oc-line"><span>IV RANK</span><b>${o.iv_rank != null ? num(o.iv_rank, 0) : "—"}</b></div><div class="dim" style="font-size:10px;margin-top:4px">put/call above ~1.1 = hedging; below ~0.7 = complacency (rules of thumb, not signals)</div>`; }).catch(() => { const p = $("#oc-pc"); if (p) p.innerHTML = '<span class="dim">Options data unavailable.</span>'; });
    } catch (e) { box.innerHTML = NX.err(e); }
  }
  async function trending() {
    const box = $("#oc-trend");
    try {
      const r = await api("/onchain/trending");
      box.innerHTML = `<table class="bb-tbl"><tbody>${r.coins.map((c, i) => `<tr class="click" data-sym="${esc(c.symbol)}"><td class="dim">${i + 1}</td><td>${c.thumb ? `<img src="${esc(c.thumb)}" style="height:15px;vertical-align:-3px;margin-right:6px" alt=""/>` : ""}<span class="sym">${esc(c.symbol)}</span> <span class="dim">${esc(c.name)}</span></td><td class="r dim">#${c.rank || "—"}</td><td class="r ${dir(c.pct24)}">${c.pct24 != null ? pct(c.pct24, true, 1) : "—"}</td></tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("tr[data-sym]").forEach((tr) => tr.addEventListener("click", () => window.openSecurity && openSecurity(`${tr.dataset.sym.toUpperCase()}-USD`, "GP")));
    } catch (e) { box.innerHTML = NX.err(e); }
  }
  async function perps() {
    const box = $("#oc-perps"); if (!box) return;
    try { OC.perps = (await api("/onchain/perps?top=60")).rows; drawPerps(); } catch (e) { if (!OC.perps.length) box.innerHTML = NX.err(e); }
  }
  function drawPerps() {
    const box = $("#oc-perps"), k = OC.sort, rows = [...OC.perps].sort((a, b) => (OC.desc ? -1 : 1) * ((a[k] ?? 0) - (b[k] ?? 0))).slice(0, 40);
    const cols = [["symbol", "PERP"], ["price", "PRICE"], ["pct24", "24H"], ["funding", "FUNDING"], ["apr", "ANNUALISED"], ["oi_usd", "OPEN INTEREST"], ["turnover", "24H VOLUME"]];
    const fc = (v) => (v > 0.03 ? "dn" : v < -0.01 ? "up" : "");
    box.innerHTML = `<table class="bb-tbl"><thead><tr>${cols.map(([c, l]) => `<th class="${c === "symbol" ? "" : "r"} sortable ${k === c ? "sorted" : ""}" data-k="${c}">${l}${k === c ? (OC.desc ? " ▼" : " ▲") : ""}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr class="click" data-s="${esc(r.symbol)}"><td class="sym">${esc(r.symbol.replace("USDT", ""))}</td><td class="r">${px(r.price)}</td><td class="r ${dir(r.pct24)}">${pct(r.pct24, true, 2)}</td><td class="r ${fc(r.funding)}">${num(r.funding, 4)}%</td><td class="r ${fc(r.funding)}">${num(r.apr, 1)}%</td><td class="r">${usd(r.oi_usd)}</td><td class="r dim">${usd(r.turnover)}</td></tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => { OC.desc = OC.sort === th.dataset.k ? !OC.desc : true; OC.sort = th.dataset.k; drawPerps(); }));
    box.querySelectorAll("tr[data-s]").forEach((tr) => tr.addEventListener("click", () => window.openSecurity && openSecurity(`${tr.dataset.s.replace("USDT", "")}-USD`, "GP")));
  }
  async function btcNet() {
    const box = $("#oc-net"); if (!box) return;
    try {
      const r = await api("/onchain/btcnet"), m = r.mempool, f = r.fees;
      box.innerHTML = `<div class="bb-kpis six" style="margin:0">${kpi("MEMPOOL TX", num(m.count, 0), `${num((m.vsize || 0) / 1e6, 1)} MvB waiting`)}${kpi("FEE · NEXT BLOCK", `${num(f.fast, 0)} sat/vB`, "fast")}${kpi("FEE · ~30 MIN", `${num(f.medium, 0)} sat/vB`, "medium")}${kpi("FEE · ~1 H", `${num(f.slow, 0)} sat/vB`, "slow")}</div>
        <table class="bb-tbl"><thead><tr><th>BLOCK</th><th class="r">TXS</th><th class="r">SIZE</th><th class="r">MINED</th></tr></thead><tbody>${r.blocks.map((b) => `<tr><td class="sym">${num(b.height, 0)}</td><td class="r">${num(b.tx, 0)}</td><td class="r dim">${num(b.size / 1e6, 2)} MB</td><td class="r dim">${NX.ago(b.t)} ago</td></tr>`).join("")}</tbody></table>`;
    } catch (e) { if (!box.querySelector("table")) box.innerHTML = NX.err(e); }
  }
  // liquidations: forced closes on Binance futures (SELL = a long was liquidated, BUY = a short was)
  function liqStart() {
    const box = $("#oc-liq"), L = OC.liq;
    const draw = () => {
      const now = Date.now(), win = (a) => a.filter((x) => now - x[0] < 300000), lo = win(L.long), sh = win(L.short), sum = (a) => a.reduce((s, x) => s + x[1], 0);
      box.innerHTML = `<div class="bb-kpis six" style="grid-template-columns:1fr 1fr;margin:0">${kpi("LONGS LIQUIDATED · 5 MIN", usd(sum(lo)), `${lo.length} orders`, "dn")}${kpi("SHORTS LIQUIDATED · 5 MIN", usd(sum(sh)), `${sh.length} orders`, "up")}</div>
        <table class="bb-tbl"><tbody>${L.rows.map((r) => `<tr><td class="dim">${new Date(r.t).toLocaleTimeString("en-GB")}</td><td class="sym">${esc(r.s.replace("USDT", ""))}</td><td class="${r.long ? "dn" : "up"}">${r.long ? "LONG" : "SHORT"} liq</td><td class="r">${usd(r.v)}</td></tr>`).join("") || '<tr><td class="dim" colspan="4">Waiting for forced liquidations…</td></tr>'}</tbody></table>`;
    };
    try {
      L.ws = new WebSocket("wss://fstream.binance.com/ws/!forceOrder@arr");
      L.ws.onmessage = (m) => { try { const o = JSON.parse(m.data).o, v = Number(o.ap || o.p) * Number(o.q), long = o.S === "SELL"; L.rows.unshift({ t: Date.now(), s: o.s, long, v }); L.rows.length = Math.min(L.rows.length, 40); (long ? L.long : L.short).push([Date.now(), v]); draw(); } catch (e) { /* ignore */ } };
      L.ws.onerror = () => { box.innerHTML = '<div class="bb-empty"><b>STREAM UNREACHABLE</b>Binance\'s liquidation stream could not be opened from this network.</div>'; };
      L.ws.onopen = draw;
    } catch (e) { box.innerHTML = NX.err(e); }
    setInterval(() => { if (alive("#oc-liq") && OC.active && L.ws && L.ws.readyState === 1) draw(); }, 5000);
  }
  function whaleLoop() {
    const box = $("#oc-whale");
    const poll = async () => {
      try {
        const r = await api("/world/btc"); OC.price = r.price;
        r.txs.forEach((t) => { if (t.btc >= 25 && !OC.seen.has(t.h)) { OC.seen.add(t.h); OC.whales.unshift({ ...t, seen: Date.now() }); } });
        OC.whales = OC.whales.slice(0, 40);
        box.innerHTML = `<table class="bb-tbl"><thead><tr><th>SEEN</th><th class="r">BTC</th><th class="r">USD</th><th>TRANSACTION</th></tr></thead><tbody>${OC.whales.map((w) => `<tr><td class="dim">${new Date(w.seen).toLocaleTimeString("en-GB")}</td><td class="r"><b>${num(w.btc, 1)}</b></td><td class="r">${OC.price ? usd(w.btc * OC.price) : "—"}</td><td><a href="https://blockstream.info/tx/${w.h}" target="_blank" rel="noopener">${w.h.slice(0, 18)}…</a></td></tr>`).join("") || '<tr><td colspan="4" class="dim">No 25+ BTC transaction seen yet: they appear as they are broadcast.</td></tr>'}</tbody></table>`;
      } catch (e) { if (!box.querySelector("table")) box.innerHTML = NX.err(e); }
    };
    poll(); setInterval(() => { if (alive("#oc-whale") && OC.active) poll(); }, 10000);
  }

  // ================================================================== registration
  const built = {};
  const prev = window.onToolsTab;
  window.onToolsTab = (tab) => {
    if (prev) prev(tab);
    TV.active = tab === "newstv"; OC.active = tab === "onchain";
    if (TV.built) tvSuspend(tab !== "newstv");
    const run = (key, fn, view) => { if (tab === key && !built[key]) { built[key] = true; Promise.resolve(fn()).catch((e) => { const v = $(view); if (v) v.innerHTML = NX.err(e); }); } };
    run("newstv", async () => { await buildTV(); TV.built = true; }, "#view-newstv"); run("onchain", buildOnchain, "#view-onchain");
  };
})();
