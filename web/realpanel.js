/* REAL ACCOUNT panel on the LIVE TRADES page: what was actually sent to the broker (TradersPost signals) and the real balance.
   The trade table only shows trades the strategy has CLOSED, so a real order that was just sent (or a manual test signal) used to be invisible
   there. This panel shows every signal, with its stop and target, and lets you type the real balance in one line. Self-contained: polls
   /api/connect/status every 8 s while the page is on screen. */
(function () {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (n) => (n === null || n === undefined ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  let box = null, timer = 0, busy = false;

  const ago = (ts) => { // sqlite 'YYYY-MM-DD HH:MM:SS' in UTC
    const t = Date.parse(String(ts).replace(" ", "T") + "Z"); if (!t) return "";
    const m = Math.round((Date.now() - t) / 60000);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  };
  const when = (ts) => { const t = Date.parse(String(ts).replace(" ", "T") + "Z"); return t ? new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : ts; };

  function draw(d) {
    const b = d.balance || {}, acct = b.account, sig = d.signals || [];
    const typed = b.typed, fills = b.fills || {};
    box.innerHTML = `<div class="bb-ph"><span class="bb-n">R</span>REAL ACCOUNT · ORDERS SENT<span class="sp"></span><span class="meta">${d.webhook && d.webhook.configured ? "TradersPost connected" : "TradersPost NOT connected"}</span><button class="bb-chip rp-close" id="rp-close" title="Send a market exit to the broker for this account's ticker and close the terminal's position">CLOSE POSITION</button></div>
      <div class="bb-pb bb-pad">
        <div class="rp-bal">
          <div><span>TERMINAL TRACKED</span><b>${money(acct && acct.balance)}</b><small>${acct ? esc(acct.name) : "no account linked"} · moves with the strategy's closed trades</small></div>
          <div><span>REAL BALANCE (BROKER, AUTO)</span><b>${typed ? money(typed.balance) : "—"}</b><small>${typed ? ago(typed.at) : "type it below, or drop a Tradovate Performance CSV in Downloads"}</small></div>
          ${fills.n ? `<div><span>FROM BROKER FILLS</span><b>${money(b.real_from_fills)}</b><small>${fills.n} fills · ${money(fills.pnl)}</small></div>` : ""}
        </div>
        <div class="rp-in"><input id="rp-bal" class="ctl-in" placeholder="real balance, e.g. 50210.50" inputmode="decimal"/><button class="bb-chip on" id="rp-save">SAVE BALANCE</button><span class="rp-msg" id="rp-msg"></span></div>
        ${d.pa ? `<div class="rp-h">APEX PA RULES · LIVE CHECK <span class="rp-pa-${d.pa.eligible_now ? "go" : "wait"}">${d.pa.eligible_now ? "PAYOUT REQUEST OK" : "NOT YET ELIGIBLE"}</span></div><div class="rp-pa">${d.pa.checks.map((c) => `<div class="rp-pr s-${c.state}"><i>${{ ok: "✓", enforced: "●", pending: "…", violated: "✕" }[c.state] || ""}</i><b>${esc(c.rule)}</b><span>${esc(c.detail)}</span></div>`).join("")}</div><div class="rp-note">The terminal enforces: flat before 16:59 New York, daily target $300, trailing drawdown, a stop + target on every order. Payout rules are measured from the account's own trades; assumed values (${Object.entries(d.pa.assumed || {}).map(([k, v]) => esc(k) + " $" + v).join(", ")}) need Apex's confirmation. Apex's dashboard is the final authority.</div>` : ""}
        ${(d.tracked || []).length ? `<div class="rp-h">TEST SIGNALS · TRACKED AGAINST THE MARKET</div><table class="bb-tbl rp-t"><thead><tr><th>SENT</th><th>ORDER</th><th class="r">ENTRY</th><th class="r">STOP</th><th class="r">TARGET</th><th class="r">NOW</th><th class="r">RESULT</th></tr></thead><tbody>${d.tracked.map((s) =>
          `<tr><td title="${esc(s.ts)} UTC">${esc(when(s.ts))}</td><td><b class="${s.side === "long" ? "up" : "dn"}">${esc(s.side.toUpperCase())}</b> ${esc(s.qty)} ${esc(s.symbol)}</td><td class="r">${s.entry}</td><td class="r">${s.stop}</td><td class="r">${s.target}</td><td class="r">${s.last_price ?? "—"}</td>
          <td class="r ${s.status === "open" ? (s.unrealised > 0 ? "up" : s.unrealised < 0 ? "dn" : "") : (s.pnl > 0 ? "up" : "dn")}">${s.status === "open" ? "OPEN " + (s.unrealised !== undefined ? (s.unrealised >= 0 ? "+" : "") + money(s.unrealised) : "") : (s.status === "target" ? "TARGET " : s.status === "manual" ? "CLOSED " : "STOP ") + (s.pnl >= 0 ? "+" : "") + money(s.pnl)}</td></tr>`).join("")}</tbody></table><div class="rp-note">Estimated from market prices every minute (a bar touching both stop and target counts as the stop). The broker's fill is final.</div>` : ""}
        ${sig.length ? `<table class="bb-tbl rp-t"><thead><tr><th>SENT</th><th>ORDER</th><th class="r">PRICE</th><th class="r">STOP</th><th class="r">TARGET</th><th></th></tr></thead><tbody>${sig.slice(0, 8).map((s) =>
          `<tr><td title="${esc(s.ts)} UTC">${esc(when(s.ts))}</td><td><b class="${s.action === "buy" ? "up" : s.action === "sell" ? "dn" : ""}">${esc(String(s.action || "").toUpperCase())}</b> ${esc(s.qty ?? "")} ${esc(s.ticker || "")}</td><td class="r">${s.price ?? "—"}</td><td class="r">${s.stop ?? "—"}</td><td class="r">${s.target ?? "—"}</td><td class="${s.ok ? "up" : "dn"}">${s.ok ? "OK" : "FAILED"}</td></tr>`).join("")}</tbody></table>
          <div class="rp-note">A strategy trade shows in the table on the left once it closes. Anything sent by hand (test signals) appears only here.</div>`
          : '<div class="bb-empty" style="min-height:60px"><b>NO ORDERS SENT YET</b>When the strategy (or a test signal) sends a real order, it is listed here at once.</div>'}
      </div>`;
    const save = box.querySelector("#rp-save"), inp = box.querySelector("#rp-bal"), msg = box.querySelector("#rp-msg");
    const go = async () => {
      const v = Number(String(inp.value).replace(/[$,\s]/g, "")); if (!(v > 0)) { msg.textContent = "enter a number"; return; }
      try { const r = await fetch("/api/connect/balance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ balance: v, note: "typed on the live page" }) }); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status); msg.textContent = "saved"; load(); } catch (e) { msg.textContent = String(e.message || e); }
    };
    const closeBtn = box.querySelector("#rp-close");
    if (closeBtn) closeBtn.addEventListener("click", async () => {
      if (!acct || !acct.id) { msg.textContent = "no linked account"; return; }
      if (!confirm("CLOSE the real position now? This sends a market EXIT to the broker (TradersPost) for " + (acct.symbol || "this account's ticker") + " and closes the terminal's position. It cannot be undone.")) return;
      closeBtn.disabled = true; closeBtn.textContent = "CLOSING…";
      try {
        const r = await fetch("/api/nostro/account/" + acct.id + "/traderspost/close", { method: "POST" }); const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || r.status);
        msg.textContent = j.sent ? "Exit sent at ~" + j.price + (j.closed_local_position ? " · terminal position closed" : "") + ". Check the broker." : "The exit could NOT be sent. Close it at the broker.";
      } catch (e) { msg.textContent = "Close failed: " + (e.message || e) + ". Close it at the broker."; }
      closeBtn.disabled = false; closeBtn.textContent = "CLOSE POSITION"; setTimeout(load, 1500);
    });
    save.addEventListener("click", go); inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }

  async function load() {
    if (busy || !box) return; busy = true;
    try { const r = await fetch("/api/connect/status"); if (r.ok) { const typing = box.querySelector("#rp-bal"); if (!(typing && document.activeElement === typing && typing.value)) draw(await r.json()); } } catch (e) { /* keep the last picture */ }
    busy = false;
  }

  function start() {
    const view = document.getElementById("view-live"); if (!view) return;
    const col = view.querySelector(".bb-col.s4"); if (!col) return;
    box = document.createElement("div"); box.className = "bb-panel rp-panel"; box.style.setProperty("--i", "0"); col.insertBefore(box, col.firstChild);
    box.innerHTML = '<div class="bb-ph"><span class="bb-n">R</span>REAL ACCOUNT · ORDERS SENT</div><div class="bb-pb bb-pad"><span class="muted">loading…</span></div>';
    const tick = () => { if (view.classList.contains("active")) load(); };
    timer = setInterval(tick, 8000); tick();
    new MutationObserver(tick).observe(view, { attributes: true, attributeFilter: ["class"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
