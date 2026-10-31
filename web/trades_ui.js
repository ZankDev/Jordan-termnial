/* NOSTRO › LIVE TRADES › ALL TRADES — redesign.
   Every row carries a WIN / LOSS badge; a scoreboard shows wins vs losses, the win-rate bar and the last 40 results as a
   streak strip; chips filter ALL / WINS / LOSSES / LONG / SHORT plus strategy and free-text search; clicking a row expands
   the full trade (entry → exit, stop, target, hold time, R, equity after) with its chart; the 📷 button opens the trade
   image (same renderer as the Telegram alerts) with DOWNLOAD / COPY / SEND TO TELEGRAM.
   Loaded after dashboard.js and overrides its liveRowHtml / liveInsertRow / liveShowTable (the feed, polling, range
   toggle and load-more logic there stay untouched). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isN = (n) => n !== null && n !== undefined && !Number.isNaN(n);
  const money = (n) => (!isN(n) ? "—" : `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`);
  const T = { trades: new Map(), filter: "all", strat: "", q: "", open: null };
  const nice = (s) => String(s || "—").replace(/_/g, " ");
  const hold = (a, b) => { const m = (new Date(b) - new Date(a)) / 60000; if (!isFinite(m) || m < 0) return "—"; return m >= 60 ? `${Math.floor(m / 60)}h ${Math.round(m % 60)}m` : `${Math.round(m)}m`; };
  const when = (s) => { const d = new Date(s); return isNaN(d) ? esc(s) : `${d.toLocaleDateString(undefined, { day: "2-digit", month: "short" })} <b>${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</b>`; };

  // ------------------------------------------------------------------ rows
  window.liveRowHtml = function (t) {
    const win = t.pnl > 0, r = t.r_multiple;
    return `<td class="tu-res"><span class="tu-badge ${win ? "win" : "loss"}">${win ? "WIN" : "LOSS"}</span></td>
      <td class="tu-when">${when(t.exit_time)}</td>
      <td class="tu-acc"><b>${esc(t.account_name)}</b>${t.archived ? ' <span class="pill pill-idle">archived</span>' : ""}${t.restored ? ' <span class="pill pill-idle" title="restored after the 28-Sep strategy switch reset this account">restored</span>' : ""}<small>${esc(nice(t.strategy))}</small></td>
      <td><span class="tu-sym">${esc(t.symbol || "—")}</span></td>
      <td><span class="tu-side ${t.side === "long" ? "long" : "short"}">${t.side === "long" ? "▲ LONG" : "▼ SHORT"}</span></td>
      <td class="r tu-qty">${t.qty ?? 1}${t.contract === "micro" ? "µ" : ""}</td>
      <td class="tu-px">${isN(t.entry_price) ? t.entry_price : "—"} <i>→</i> ${isN(t.exit_price) ? t.exit_price : "—"}</td>
      <td><span class="tu-reason ${esc(t.exit_reason)}">${esc(t.exit_reason)}</span></td>
      <td class="r tu-pnl ${win ? "up" : "dn"}">${money(t.pnl)}</td>
      <td class="r tu-r ${isN(r) ? (r > 0 ? "up" : "dn") : ""}">${isN(r) ? `${r > 0 ? "+" : ""}${r}R` : "—"}</td>
      <td class="tu-act"><button class="tu-cam" data-img="${t.id}" title="Trade image — view, download, copy, send"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></button></td>`;
  };

  const _insert = window.liveInsertRow;
  window.liveInsertRow = function (t, animate) {
    T.trades.set(t.id, t);
    _insert(t, animate);
    const tr = liveState.rows.get(t.id);
    if (tr) {
      tr.classList.add("tu-row", t.pnl > 0 ? "tu-w" : "tu-l");
      tr.dataset.win = t.pnl > 0 ? "1" : "0";
      tr.dataset.side = t.side;
      tr.dataset.strat = t.strategy || "";
      tr.dataset.text = `${t.account_name} ${t.strategy} ${t.symbol} ${t.exit_reason}`.toLowerCase();
      applyOne(tr);
    }
    scheduleBoard();
  };

  const _show = window.liveShowTable;
  window.liveShowTable = function () { _show(); scheduleBoard(); };

  // ------------------------------------------------------------------ filters + scoreboard
  function visible(tr) {
    if (T.filter === "wins" && tr.dataset.win !== "1") return false;
    if (T.filter === "losses" && tr.dataset.win !== "0") return false;
    if ((T.filter === "long" || T.filter === "short") && tr.dataset.side !== T.filter) return false;
    if (T.strat && tr.dataset.strat !== T.strat) return false;
    if (T.q && !tr.dataset.text.includes(T.q)) return false;
    return true;
  }
  function applyOne(tr) { const v = visible(tr); tr.style.display = v ? "" : "none"; const d = tr.nextElementSibling; if (d && d.classList.contains("tu-detail")) d.style.display = v ? "" : "none"; }
  function applyAll() { liveState.rows.forEach((tr) => applyOne(tr)); scheduleBoard(); }

  let boardTimer = null;
  function scheduleBoard() { clearTimeout(boardTimer); boardTimer = setTimeout(renderBoard, 60); }
  function renderBoard() {
    const all = [...T.trades.values()].filter((t) => liveState.rows.has(t.id)).sort((a, b) => b.id - a.id);
    const shown = all.filter((t) => { const tr = liveState.rows.get(t.id); return tr && tr.style.display !== "none"; });
    const w = shown.filter((t) => t.pnl > 0), l = shown.filter((t) => t.pnl <= 0);
    const net = shown.reduce((s, t) => s + t.pnl, 0), gw = w.reduce((s, t) => s + t.pnl, 0), gl = -l.reduce((s, t) => s + t.pnl, 0);
    const wr = shown.length ? (100 * w.length) / shown.length : 0;
    let streak = 0, sk = null;
    for (const t of shown) { const k = t.pnl > 0; if (sk === null) sk = k; if (k !== sk) break; streak++; }
    const board = $("#tu-board");
    if (!board) return;
    board.innerHTML = `
      <div class="tu-sc win" data-uix-tip="WINS|${w.length} winning trades|gross ${money(gw)}"><span>WINS</span><b>${w.length}</b><small>${money(gw)}</small></div>
      <div class="tu-sc loss" data-uix-tip="LOSSES|${l.length} losing trades|gross ${money(-gl)}"><span>LOSSES</span><b>${l.length}</b><small>${money(-gl)}</small></div>
      <div class="tu-wr"><div class="tu-wr-l"><span>WIN RATE <b>${wr.toFixed(1)}%</b></span><span>NET <b class="${net >= 0 ? "up" : "dn"}">${money(net)}</b></span><span>PF <b>${gl ? (gw / gl).toFixed(2) : "—"}</b></span>
        <span>STREAK <b class="${sk ? "up" : "dn"}">${streak ? `${streak}${sk ? "W" : "L"}` : "—"}</b></span></div>
        <div class="tu-wr-bar"><i class="w" style="width:${wr}%"></i><i class="l" style="width:${shown.length ? 100 - wr : 0}%"></i></div>
        <div class="tu-strip" title="last 40 results, newest on the right">${shown.slice(0, 40).reverse().map((t) => `<i class="${t.pnl > 0 ? "w" : "l"}" data-uix-tip="${t.pnl > 0 ? "WIN" : "LOSS"} ${money(t.pnl)}|${esc(t.account_name)} · ${esc(nice(t.strategy))}|${esc(String(t.exit_time).replace("T", " ").slice(0, 16))}" data-jump="${t.id}"></i>`).join("")}</div></div>`;
    const sel = $("#tu-strat");
    if (sel) {
      const names = [...new Set(all.map((t) => t.strategy).filter(Boolean))].sort();
      const cur = sel.value;
      const opts = `<option value="">ALL STRATEGIES</option>${names.map((n) => `<option value="${esc(n)}">${esc(nice(n)).toUpperCase()}</option>`).join("")}`;
      if (sel.dataset.opts !== opts) { sel.innerHTML = opts; sel.dataset.opts = opts; sel.value = cur; }
    }
    const cnt = { all: all.length, wins: all.filter((t) => t.pnl > 0).length, losses: all.filter((t) => t.pnl <= 0).length, long: all.filter((t) => t.side === "long").length, short: all.filter((t) => t.side === "short").length };
    document.querySelectorAll("#tu-chips [data-f]").forEach((b) => { const n = b.querySelector("em"); if (n) n.textContent = cnt[b.dataset.f]; });
  }

  // ------------------------------------------------------------------ expand row
  function detailHtml(t) {
    const r = t.r_multiple, win = t.pnl > 0;
    const risk = t.risk_dollars;
    const kv = (k, v, c = "") => `<div><span>${k}</span><b class="${c}">${v}</b></div>`;
    return `<td colspan="11"><div class="tu-d">
      <div class="tu-d-l">
        <div class="tu-d-h"><span class="tu-badge ${win ? "win" : "loss"}">${win ? "WIN" : "LOSS"}</span><b>${esc(t.account_name)}</b><span class="dim">${esc(nice(t.strategy))} · ${esc(t.symbol)} · ${t.side.toUpperCase()}</span></div>
        <div class="tu-kv">${kv("ENTRY", `${t.entry_price}`)}${kv("EXIT", `${t.exit_price}`)}${kv("STOP", isN(t.stop_price) ? t.stop_price : "—", "dn")}${kv("TARGET", isN(t.target_price) ? t.target_price : "—", "up")}
          ${kv("OPENED", esc(String(t.entry_time).replace("T", " ").slice(0, 16)))}${kv("CLOSED", esc(String(t.exit_time).replace("T", " ").slice(0, 16)))}${kv("HELD", hold(t.entry_time, t.exit_time))}${kv("EXIT REASON", esc(t.exit_reason))}
          ${kv("QTY", `${t.qty ?? 1} ${t.contract || "mini"}`)}${kv("RISKED", isN(risk) ? money(risk).replace("+", "") : "—")}${kv("RESULT", isN(r) ? `${r > 0 ? "+" : ""}${r}R` : "—", isN(r) ? (r > 0 ? "up" : "dn") : "")}${kv("P&L", money(t.pnl), win ? "up" : "dn")}
          ${kv("EQUITY AFTER", isN(t.equity_after) ? `$${Number(t.equity_after).toLocaleString()}` : "—")}</div>
        <div class="tu-d-btns"><button class="bb-chip" data-img="${t.id}"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> FULL IMAGE</button><a class="bb-chip" href="/api/trades/${t.id}/image.png?download=true" download="trade_${t.id}.png">⬇ DOWNLOAD</a>
          <button class="bb-chip" data-tg="${t.id}">✈ SEND TO TELEGRAM</button><button class="bb-chip" data-acc="${t.account_id}">OPEN ACCOUNT ▸</button></div>
      </div>
      <div class="tu-d-img" data-img="${t.id}"><div class="tu-spin">drawing the chart…</div><img alt="trade ${t.id} chart"/></div>
    </div></td>`;
  }
  function toggleDetail(tr) {
    const id = Number(tr.dataset.id), t = T.trades.get(id);
    const nxt = tr.nextElementSibling;
    document.querySelectorAll("#live-trades-body tr.tu-detail").forEach((d) => { d.previousElementSibling?.classList.remove("tu-open"); d.remove(); });
    if (nxt && nxt.classList.contains("tu-detail")) return;
    if (!t) return;
    const d = document.createElement("tr");
    d.className = "tu-detail";
    d.innerHTML = detailHtml(t);
    tr.after(d);
    tr.classList.add("tu-open");
    const img = d.querySelector("img"), spin = d.querySelector(".tu-spin");
    img.onload = () => { spin.remove(); img.classList.add("ok"); };
    img.onerror = () => { spin.textContent = "No price data around this trade to draw it."; };
    img.src = `/api/trades/${id}/image.png`;
  }

  // ------------------------------------------------------------------ image viewer
  function viewer(id) {
    const t = T.trades.get(id);
    let v = $("#tu-viewer");
    if (!v) {
      v = document.createElement("div");
      v.id = "tu-viewer";
      v.innerHTML = `<div class="tu-v"><header><b id="tu-v-title"></b><span class="sp"></span><a class="bb-chip" id="tu-v-dl" download>⬇ DOWNLOAD</a><button class="bb-chip" id="tu-v-copy">⧉ COPY IMAGE</button>
        <button class="bb-chip" id="tu-v-tg">✈ TELEGRAM</button><button class="bb-chip" id="tu-v-prev" title="older (←)">‹</button><button class="bb-chip" id="tu-v-next" title="newer (→)">›</button><button class="btn btn-small" id="tu-v-x">✕</button></header>
        <div class="tu-v-body"><div class="tu-spin" id="tu-v-spin">drawing…</div><img id="tu-v-img" alt=""/></div><footer id="tu-v-foot"></footer></div>`;
      document.body.appendChild(v);
      v.addEventListener("mousedown", (e) => { if (e.target === v) v.classList.remove("on"); });
      $("#tu-v-x").addEventListener("click", () => v.classList.remove("on"));
      $("#tu-v-copy").addEventListener("click", copyImg);
      $("#tu-v-tg").addEventListener("click", () => sendTg(Number(v.dataset.id)));
      $("#tu-v-prev").addEventListener("click", () => step(-1));
      $("#tu-v-next").addEventListener("click", () => step(1));
      document.addEventListener("keydown", (e) => {
        if (!v.classList.contains("on")) return;
        if (e.key === "Escape") v.classList.remove("on"); else if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1);
      });
    }
    v.dataset.id = id;
    const win = t && t.pnl > 0;
    $("#tu-v-title").innerHTML = t ? `<span class="tu-badge ${win ? "win" : "loss"}">${win ? "WIN" : "LOSS"}</span> ${esc(t.account_name)} · ${esc(nice(t.strategy))} ${esc(t.symbol)} ${t.side.toUpperCase()} <span class="${win ? "up" : "dn"}">${money(t.pnl)}</span>` : `TRADE #${id}`;
    $("#tu-v-foot").textContent = t ? `${t.entry_price} → ${t.exit_price} · ${t.exit_reason} · held ${hold(t.entry_time, t.exit_time)} · closed ${String(t.exit_time).replace("T", " ").slice(0, 16)}` : "";
    $("#tu-v-dl").href = `/api/trades/${id}/image.png?download=true`;
    $("#tu-v-dl").setAttribute("download", `trade_${id}.png`);
    const img = $("#tu-v-img"), spin = $("#tu-v-spin");
    img.classList.remove("ok"); spin.style.display = ""; spin.textContent = "drawing…";
    img.onload = () => { spin.style.display = "none"; img.classList.add("ok"); };
    img.onerror = () => { spin.textContent = "No price data around this trade to draw it."; };
    img.src = `/api/trades/${id}/image.png`;
    v.classList.add("on");
  }
  function step(dir) {
    const v = $("#tu-viewer"), cur = Number(v.dataset.id);
    const ids = [...liveState.rows.entries()].filter(([, tr]) => tr.style.display !== "none").map(([id]) => id).sort((a, b) => a - b);
    const i = ids.indexOf(cur), nx = ids[i + dir];
    if (nx) viewer(nx);
  }
  async function copyImg() {
    const id = $("#tu-viewer").dataset.id;
    try {
      const blob = await fetch(`/api/trades/${id}/image.png`).then((r) => r.blob());
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      showToast("Image copied — paste it anywhere");
    } catch (e) { showToast("Copy not allowed by the browser — use DOWNLOAD"); }
  }
  async function sendTg(id) {
    try { await api(`/trades/${id}/telegram`, { method: "POST" }); showToast("Sent to Telegram"); } catch (e) { showToast(`Telegram: ${e.message}`); }
  }

  // ------------------------------------------------------------------ mount
  function mount() {
    const table = $("#live-trades-table");
    if (!table || table.dataset.tu) return;
    table.dataset.tu = "1";
    table.classList.add("tu-tbl");
    table.querySelector("thead").innerHTML = `<tr><th>RESULT</th><th>CLOSED</th><th>ACCOUNT · STRATEGY</th><th>SYM</th><th>SIDE</th><th class="r">QTY</th><th>ENTRY → EXIT</th><th>EXIT</th><th class="r">P&amp;L</th><th class="r">R</th><th></th></tr>`;
    const bar = document.createElement("div");
    bar.className = "tu-top";
    bar.innerHTML = `<div class="tu-board" id="tu-board"></div>
      <div class="tu-tools"><div class="tu-chips" id="tu-chips">${[["all", "ALL"], ["wins", "WINS"], ["losses", "LOSSES"], ["long", "LONG"], ["short", "SHORT"]].map(([k, l]) => `<button class="bb-chip ${k === "all" ? "on" : ""} tu-f-${k}" data-f="${k}">${l} <em>0</em></button>`).join("")}</div>
        <select id="tu-strat" class="tu-sel"><option value="">ALL STRATEGIES</option></select><input id="tu-q" class="tu-q" placeholder="search account / symbol / reason…"/></div>`;
    table.parentNode.insertBefore(bar, table);
    // repaint rows already rendered by dashboard.js before this script loaded
    liveState.rows.forEach((tr, id) => { const t = T.trades.get(id); if (t) tr.innerHTML = window.liveRowHtml(t); });
    bar.addEventListener("click", (e) => {
      const f = e.target.closest("[data-f]"); if (f) { T.filter = f.dataset.f; bar.querySelectorAll("[data-f]").forEach((b) => b.classList.toggle("on", b === f)); applyAll(); return; }
      const j = e.target.closest("[data-jump]"); if (j) { const tr = liveState.rows.get(Number(j.dataset.jump)); if (tr) { tr.scrollIntoView({ block: "center", behavior: "smooth" }); tr.classList.add("tu-flash"); setTimeout(() => tr.classList.remove("tu-flash"), 1600); } }
    });
    $("#tu-strat").addEventListener("change", (e) => { T.strat = e.target.value; applyAll(); });
    $("#tu-q").addEventListener("input", (e) => { T.q = e.target.value.trim().toLowerCase(); applyAll(); });
    $("#live-trades-body").addEventListener("click", (e) => {
      const img = e.target.closest("[data-img]"); if (img) { e.stopPropagation(); viewer(Number(img.dataset.img)); return; }
      const tg = e.target.closest("[data-tg]"); if (tg) { e.stopPropagation(); sendTg(Number(tg.dataset.tg)); return; }
      const acc = e.target.closest("[data-acc]"); if (acc) { e.stopPropagation(); window.openAccountModal && openAccountModal(Number(acc.dataset.acc)); return; }
      if (e.target.closest("a")) return;
      const tr = e.target.closest("tr.tu-row"); if (tr) toggleDetail(tr);
    });
    scheduleBoard();
  }

  // capture the trades dashboard.js already rendered (its Map only keeps <tr>s), then mount
  window.addEventListener("load", () => {
    mount();
    // the live tab may already be initialised: refill our trade store from the feed so badges/scoreboard are complete
    if (liveState.init) api("/trades/feed?limit=200").then((feed) => { feed.trades.forEach((t) => T.trades.set(t.id, t)); liveState.rows.forEach((tr, id) => { const t = T.trades.get(id); if (t) { tr.innerHTML = window.liveRowHtml(t); tr.classList.add("tu-row", t.pnl > 0 ? "tu-w" : "tu-l"); Object.assign(tr.dataset, { win: t.pnl > 0 ? "1" : "0", side: t.side, strat: t.strategy || "", text: `${t.account_name} ${t.strategy} ${t.symbol} ${t.exit_reason}`.toLowerCase() }); } }); applyAll(); });
  });
})();
