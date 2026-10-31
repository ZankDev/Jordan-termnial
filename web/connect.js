/* REAL ACCOUNT (NOSTRO › REAL ACCOUNT): connect TradersPost and Telegram without editing files, see every signal sent, and record the real balance.
   Secrets are saved encrypted on this PC (Windows DPAPI) and never shown again, only masked. Depends on dashboard.js (api(), showToast()). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const usd = (n) => (n === null || n === undefined ? "—" : `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`);
  const toast = (m) => (window.showToast ? window.showToast(m) : alert(m));
  const S = { d: null, started: false };
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const copy = async (text) => { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Copy is blocked by the browser"); } };

  const led = (on, warn) => `<i class="cn-led ${on ? "on" : warn ? "warn" : ""}"></i>`;
  const step = (n, html, done) => `<li class="${done ? "done" : ""}"><b>${n}</b><span>${html}</span></li>`;

  function health(d) {
    const acc = d.balance.account, tg = d.telegram, wh = d.webhook;
    const chips = [[wh.configured, "TRADERSPOST WEBHOOK", wh.configured ? `saved (${wh.source})` : "not connected"], [tg.configured, "TELEGRAM", tg.configured ? `@${esc(tg.username || "bot")}` : "not connected"],
      [!!acc, "REAL-MONEY ACCOUNT", acc ? esc(acc.name) : "none linked"], [d.balance.typed || d.balance.fills.n > 0, "REAL BALANCE", d.balance.typed ? `${usd(d.balance.typed.balance)} typed` : d.balance.fills.n ? `${d.balance.fills.n} fills imported` : "not recorded"]];
    return `<div class="cn-health">${chips.map(([ok, t, s]) => `<div class="cn-chip">${led(ok, !ok)}<div><b>${t}</b><span>${s}</span></div></div>`).join("")}</div>`;
  }

  function webhookCard(d) {
    const wh = d.webhook, sig = d.signals;
    const example = JSON.stringify({ ticker: d.ticker_example, action: "buy", orderType: "market", price: 31000.0, quantity: 2, stopLoss: { type: "stop", stopPrice: 30915.0 }, takeProfit: { limitPrice: 31085.0 } }, null, 2);
    return `<section class="bb-panel cn-card"><div class="bb-ph"><span class="bb-n">1</span>TRADERSPOST WEBHOOK<span class="sp"></span><span class="meta">${led(wh.configured)} ${wh.configured ? "connected" : "not connected"}</span></div><div class="cn-b">
      <ol class="cn-steps">${step(1, 'In <b>TradersPost</b> open your <b>Strategy</b> and go to the <b>Webhook</b> section. Copy the long URL.', wh.configured)}${step(2, "Paste it here and press <b>SAVE</b>. It is stored encrypted on this PC; you will not need to edit run.bat.", wh.configured)}${step(3, 'Press <b>TEST LONG</b> on your real-money account (ACCOUNTS › the account › CONTROLS) once to check the whole path, and confirm the order shows in TradersPost.', sig.length > 0)}</ol>
      ${wh.configured ? `<div class="cn-saved">${led(true)} <b>Saved:</b> <code>${esc(wh.masked)}</code> <span class="dim">(from ${esc(wh.source)})</span><button class="bb-chip" id="cn-wh-clear">REMOVE</button></div>` : ""}
      <div class="cn-row"><input class="ctl-in" id="cn-wh" type="password" autocomplete="off" spellcheck="false" placeholder="https://webhooks.traderspost.io/trading/webhook/…/…" /><button class="bb-chip on" id="cn-wh-save">SAVE WEBHOOK</button></div>
      <div class="cn-sub">SET THESE IN YOUR TRADERSPOST STRATEGY</div>
      <ul class="cn-list"><li>Ticker: the terminal sends <code>${esc(d.ticker_example)}</code> for micro NQ (and <code>MES</code> for micro ES). Map it to the matching Tradovate contract.</li><li>Turn <b>Auto submit</b> ON, otherwise orders only wait for you to approve them.</li><li>Allow signals to carry their own <b>stop loss and take profit</b> (the terminal sends both in every entry).</li><li>The terminal sends a limit-style entry with a <code>price</code> because Apex gives TradersPost no live quote.</li></ul>
      <div class="cn-sub">THE SIGNAL THE TERMINAL SENDS <button class="bb-chip" id="cn-copy-ex">COPY</button></div><pre class="cn-pre">${esc(example)}</pre>
      <div class="cn-sub">SIGNALS SENT (LATEST 15)</div>
      ${sig.length ? `<div class="cn-scroll"><table class="bb-tbl"><thead><tr><th>TIME (UTC)</th><th>ACTION</th><th>TICKER</th><th class="r">PRICE</th><th class="r">QTY</th><th class="r">STOP</th><th class="r">TARGET</th><th>RESULT</th></tr></thead><tbody>${sig.map((s) => `<tr><td>${esc(s.ts)}</td><td><b>${esc(String(s.action || "").toUpperCase())}</b></td><td>${esc(s.ticker)}</td><td class="r">${s.price ?? "—"}</td><td class="r">${s.qty ?? "—"}</td><td class="r">${s.stop ?? "—"}</td><td class="r">${s.target ?? "—"}</td><td class="${s.ok ? "up" : "dn"}">${s.ok ? "HTTP " + s.http_status : esc(s.detail || "failed")}</td></tr>`).join("")}</tbody></table></div>
      <div class="dp-note">A row means TradersPost accepted the signal. The STOP and TARGET columns show what was sent; check in TradersPost or Tradovate that both exist as working orders.</div>` : '<div class="cn-empty">No signal sent yet. After the first TEST LONG or live trade, every signal appears here with the response.</div>'}
    </div></section>`;
  }

  function telegramCard(d) {
    const tg = d.telegram;
    return `<section class="bb-panel cn-card"><div class="bb-ph"><span class="bb-n">2</span>TELEGRAM<span class="sp"></span><span class="meta">${led(tg.configured)} ${tg.configured ? "connected" : "not connected"}</span></div><div class="cn-b">
      ${tg.configured ? `<div class="cn-saved">${led(true)} <b>Connected</b> to <code>@${esc(tg.username || "your bot")}</code> <span class="dim">(from ${esc(tg.source)})</span><button class="bb-chip on" id="cn-tg-test">SEND TEST MESSAGE</button><button class="bb-chip" id="cn-tg-clear">DISCONNECT</button></div>
        <div class="cn-sub">WHAT YOU GET</div><ul class="cn-list"><li>A message for every <b>real order</b> sent to TradersPost, with the stop and target, and an alert if it <b>fails</b>.</li><li>Alerts if the live engine stops ticking, or a real position is still open after the close.</li><li>Live <b>/report</b> progress, and the commands: <code>/status</code> <code>/report AAPL</code> <code>/price</code> <code>/chart</code> <code>/news</code> <code>/brief</code> <code>/help</code>.</li></ul>`
      : `<ol class="cn-steps">${step(1, 'In Telegram open <b>@BotFather</b>, send <code>/newbot</code>, choose a name, and copy the <b>token</b> it gives you.', !!tg.pending)}${step(2, "Paste the token here and press <b>VERIFY</b>.", !!tg.pending)}${step(3, 'Open your new bot in Telegram and press <b>START</b> (or send it any message).', false)}${step(4, "Come back and press <b>DETECT MY CHAT</b>. The terminal finds your chat and saves everything.", false)}</ol>
        <div class="cn-row"><input class="ctl-in" id="cn-tg" type="password" autocomplete="off" spellcheck="false" placeholder="123456789:AAH…" /><button class="bb-chip on" id="cn-tg-verify">VERIFY TOKEN</button></div>
        ${tg.pending ? `<div class="cn-saved">${led(true)} Token verified for <code>@${esc(tg.pending.username)}</code>. Now open it in Telegram, press START, then:<button class="bb-chip on" id="cn-tg-detect">DETECT MY CHAT</button><a class="bb-chip" href="https://t.me/${esc(tg.pending.username)}" target="_blank" rel="noopener noreferrer">OPEN THE BOT</a></div>` : ""}
        <div class="dp-note">Use a bot made only for this terminal. If another program already reads that bot's messages, Telegram will refuse the detection.</div>`}
    </div></section>`;
  }

  function balanceCard(d) {
    const b = d.balance, a = b.account;
    const tile = (l, v, sub, c) => `<div class="cn-k"><span>${l}</span><b class="${c || ""}">${v}</b><em>${sub || ""}</em></div>`;
    return `<section class="bb-panel cn-card"><div class="bb-ph"><span class="bb-n">3</span>REAL BALANCE<span class="sp"></span><span class="meta">${a ? esc(a.name) : "no account linked"}</span></div><div class="cn-b">
      <div class="cn-kg">${tile("SIMULATED (TERMINAL)", usd(a ? a.balance : null), "what the local engine believes")}${tile("REAL, FROM IMPORTED FILLS", usd(b.real_from_fills), b.fills.n ? `${b.fills.n} round trips, ${usd(b.fills.pnl)} net` : "import a Tradovate file")}${tile("REAL, AS YOU TYPED IT", usd(b.typed ? b.typed.balance : null), b.typed ? esc(b.typed.at) : "type it below")}
        ${tile("DIFFERENCE", b.drift === null || b.drift === undefined ? "—" : usd(b.drift), "real minus simulated", b.drift > 0 ? "up" : b.drift < 0 ? "dn" : "")}</div>
      <div class="cn-sub">1 · TYPE YOUR CURRENT BALANCE (10 SECONDS)</div>
      <div class="cn-row"><input class="ctl-in" id="cn-bal" inputmode="decimal" placeholder="e.g. 25312.50" /><input class="ctl-in" id="cn-note" placeholder="note (optional)" /><button class="bb-chip on" id="cn-bal-save">SAVE BALANCE</button></div>
      <div class="cn-sub">2 · IMPORT YOUR TRADES FROM TRADOVATE (BEST: EXACT P&L)</div>
      <ol class="cn-steps">${step("a", "In Tradovate open <b>Account › Reports › Performance</b> (the trade list) for your Apex account.", false)}${step("b", "Export it as <b>CSV</b>, then choose the file below. Re-importing the same file never double counts.", false)}</ol>
      <div class="cn-row"><input type="file" id="cn-csv" accept=".csv,text/csv" class="cn-file" /><button class="bb-chip on" id="cn-csv-go">IMPORT FILE</button></div>
      <div class="cn-auto">${led(true)} <b>Automatic:</b> save the export in your <code>Downloads</code> folder with <i>Performance</i>, <i>Tradovate</i>, <i>trade log</i> or <i>fills</i> in its name (or drop any CSV into <code>data_cache\tradovate_inbox</code>) and the terminal imports it within a minute and tells you on Telegram. If the balance has not been recorded for 3 days it reminds you.</div>
      <div class="cn-note2"><b>Why not TradingView?</b> TradingView offers no way for another program to read a broker balance, and signing the terminal into your TradingView account would hand a third party your login for no reliable gain. TradersPost's balance feed needs their paid API plan. If you ever get Tradovate API access, a live feed can replace this page.</div>
      ${b.log.length ? `<div class="cn-sub">BALANCES YOU TYPED</div><table class="bb-tbl"><tbody>${b.log.slice(0, 6).map((r) => `<tr><td>${esc(r.ts)}</td><td class="r"><b>${usd(r.balance)}</b></td><td class="dim">${esc(r.note || "")}</td></tr>`).join("")}</tbody></table>` : ""}
    </div></section>`;
  }

  function draw() {
    const d = S.d, v = $("#view-connect"); if (!v || !d) return;
    v.innerHTML = `<div class="cn">${health(d)}<div class="cn-cols">${webhookCard(d)}<div class="cn-stack">${telegramCard(d)}${balanceCard(d)}</div></div></div>`;
    const on = (id, fn) => { const el = $(id, v); if (el) el.addEventListener("click", fn); };
    const run = async (fn, ok) => { try { const r = await fn(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); await load(); } catch (e) { toast(e.message || String(e)); } };
    on("#cn-wh-save", () => run(() => post("/connect/traderspost", { url: $("#cn-wh", v).value }), "Webhook saved"));
    on("#cn-wh-clear", () => { if (confirm("Remove the saved TradersPost webhook? Real orders stop being sent.")) run(() => api("/connect/traderspost", { method: "DELETE" }), "Webhook removed"); });
    on("#cn-copy-ex", () => copy($(".cn-pre", v).textContent));
    on("#cn-tg-verify", () => run(() => post("/connect/telegram/token", { token: $("#cn-tg", v).value }), (r) => `Token verified for @${r.username}`));
    on("#cn-tg-detect", () => run(async () => { const r = await post("/connect/telegram/detect"); if (!r.found) throw new Error("No message found yet. Open the bot in Telegram, press START, then try again."); return r; }, (r) => `Connected to ${r.chat_name}`));
    on("#cn-tg-test", () => run(() => post("/connect/telegram/test"), "Test message sent: check Telegram"));
    on("#cn-tg-clear", () => { if (confirm("Disconnect Telegram? You stop getting alerts.")) run(() => api("/connect/telegram", { method: "DELETE" }), "Telegram disconnected"); });
    on("#cn-bal-save", () => run(() => post("/connect/balance", { balance: Number(String($("#cn-bal", v).value).replace(/[$,\s]/g, "")), note: $("#cn-note", v).value }), "Balance saved"));
    on("#cn-csv-go", () => run(async () => {
      const f = $("#cn-csv", v).files[0]; if (!f) throw new Error("Choose the exported CSV file first.");
      const r = await fetch("/api/connect/balance/csv", { method: "POST", body: await f.text(), headers: { "Content-Type": "text/csv" } });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || "Import failed"); return j;
    }, (r) => `Imported ${r.added} trades (${usd(r.added_pnl)}), ${r.skipped} skipped`));
  }

  async function load() {
    try { S.d = await api("/connect/status"); draw(); } catch (e) { const v = $("#view-connect"); if (v && !S.d) v.innerHTML = `<div class="bb-empty"><b>COULD NOT LOAD</b>${esc(e.message || e)}</div>`; }
  }
  const prev = window.onExtraTab;
  window.onExtraTab = (tab) => { if (prev) prev(tab); if (tab === "connect") { if (!S.started) { S.started = true; const v = $("#view-connect"); if (v) v.innerHTML = '<div class="bb-empty bb-load">LOADING</div>'; } load(); } };
})();
