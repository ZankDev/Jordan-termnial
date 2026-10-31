/* DESKTOP SHELL — what makes the terminal behave like an installed app: a full-screen toggle, "open this page in a new window"
   (so one screen can show INVESTING and another a chart), and #win=<tab> deep links so every extra window opens on the right page.
   Works the same in a normal browser tab; the JORDAN TERMINAL.exe launcher opens the app already full screen. */
(function () {
  "use strict";
  window.NX = window.NX || {};
  const IS_PANE = /[?&]pane=1/.test(location.search); // this page is a pane inside another window's split screen: no chrome
  if (IS_PANE) document.documentElement.classList.add("nx-pane");
  const toast = (m) => { if (window.showToast) window.showToast(m); };
  const currentTab = () => document.querySelector(".tab.active")?.dataset.tab || "capital";
  const params = () => new URLSearchParams(location.hash.replace(/^#/, ""));

  // new window, placed on the other monitor when the browser lets us see it (it asks permission once)
  NX.openWindow = async (tab, sym) => {
    tab = tab || currentTab();
    let box = { left: (window.screenX || 0) + 48, top: (window.screenY || 0) + 48, width: 1280, height: 800 };
    try {
      if (window.getScreenDetails && window.screen && window.screen.isExtended) {
        const d = await window.getScreenDetails(), here = d.currentScreen, other = d.screens.find((s) => s !== here) || here;
        box = { left: other.availLeft, top: other.availTop, width: other.availWidth, height: other.availHeight };
      }
    } catch (e) { /* permission refused: fall back to a floating window */ }
    const url = `${location.pathname}#win=${encodeURIComponent(tab)}${sym ? `&sym=${encodeURIComponent(sym)}` : ""}`;
    const w = window.open(url, "_blank", `popup=yes,left=${box.left},top=${box.top},width=${box.width},height=${box.height}`);
    if (!w) toast("The browser blocked the new window: allow pop-ups for this site");
  };

  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  };
  NX.toggleFullscreen = toggleFull;

  function buttons() {
    const bar = document.querySelector(".bb-status"); if (IS_PANE || !bar || document.getElementById("nx-win-btn")) return;
    const mk = (id, txt, title, fn) => { const d = document.createElement("div"); d.className = "bb-chip-s keep"; d.innerHTML = `<button class="nx-shellbtn" id="${id}" title="${title}">${txt}</button>`; d.firstChild.addEventListener("click", fn); return d; };
    bar.appendChild(mk("nx-win-btn", "⧉ WINDOW", "Open the page you are on in a new window (it goes to your other screen when you have one)", () => NX.openWindow()));
    bar.appendChild(mk("nx-split-btn", "▦ SPLIT", "Split screen: several pages side by side in this window, without leaving full screen (Alt+S)", () => NX.toggleSplit()));
    bar.appendChild(mk("nx-fs-btn", "⛶", "Full screen on / off (F11)", toggleFull));
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "F11") { e.preventDefault(); toggleFull(); }
    else if (e.altKey && e.key.toLowerCase() === "n" && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "")) { e.preventDefault(); NX.openWindow(); }
  });

  // deep link: a window opened by NX.openWindow lands on its page; the first click makes it full screen (browsers require a gesture)
  function deepLink() {
    const p = params(), tab = p.get("win"); if (!tab) return;
    document.documentElement.classList.add("nx-popwin");
    const go = () => {
      document.querySelector(`.tab[data-tab="${tab}"]`)?.click();
      const sym = p.get("sym"); if (sym && window.openSecurity) window.openSecurity(sym, "GP");
    };
    window.addEventListener("load", () => setTimeout(go, 500));
    if (IS_PANE) return;
    const hint = document.createElement("div"); hint.className = "nx-pophint"; hint.textContent = "Click anywhere for full screen · Esc leaves";
    window.addEventListener("DOMContentLoaded", () => document.body.appendChild(hint));
    const once = () => { hint.remove(); document.removeEventListener("pointerdown", once, true); if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {}); };
    document.addEventListener("pointerdown", once, true);
    setTimeout(() => hint.remove(), 9000);
  }


  // ------------------------------------------------------------------ SPLIT SCREEN: several terminal pages side by side inside this one window,
  // so adding or removing a pane never leaves full screen. Each pane is the terminal itself (same session) in a frame, chrome hidden.
  const SPK = "nostro.split";
  const SP = { open: false, el: null, panes: [], layout: "auto", max: -1 };
  const spLoad = () => { try { const v = JSON.parse(localStorage.getItem(SPK)); if (v && Array.isArray(v.panes)) { SP.panes = v.panes.slice(0, 6); SP.layout = v.layout || "auto"; } } catch (e) { /* none saved */ } };
  const spSave = () => { try { localStorage.setItem(SPK, JSON.stringify({ panes: SP.panes, layout: SP.layout })); } catch (e) { /* private mode */ } };
  const pageList = () => {
    const seen = new Set(), out = [];
    document.querySelectorAll(".tab[data-tab]").forEach((b) => {
      const t = b.dataset.tab; if (seen.has(t) || t === "wealth") return; seen.add(t);
      const name = (b.querySelector("b") ? b.querySelector("b").textContent : b.textContent.replace(/^\s*\w{1,2}(?=[A-Z])/, "")).trim();
      out.push([t, name || t.toUpperCase()]);
    });
    return out;
  };
  const paneUrl = (p) => `${location.pathname}?pane=1#win=${encodeURIComponent(p.tab)}${p.sym ? `&sym=${encodeURIComponent(p.sym)}` : ""}`;
  const escH = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function spPlace() {
    if (!SP.el) return;
    const tape = document.querySelector(".ticker-tape"), top = tape ? Math.round(tape.getBoundingClientRect().bottom) : 90;
    SP.el.style.top = `${Math.max(0, top / (parseFloat(getComputedStyle(document.body).zoom) || 1))}px`;
  }
  function spGrid() {
    const g = SP.el.querySelector(".sp-grid"), n = SP.panes.length, kids = [...g.children];
    kids.forEach((k, i) => { k.style.gridColumn = k.style.gridRow = ""; k.classList.toggle("hide", SP.max >= 0 && i !== SP.max); k.classList.toggle("max", SP.max === i); });
    if (SP.max >= 0) { g.style.gridTemplateColumns = "1fr"; g.style.gridTemplateRows = "1fr"; return; }
    const L = SP.layout;
    if (L === "main" && n >= 2) { g.style.gridTemplateColumns = "1.7fr 1fr"; g.style.gridTemplateRows = `repeat(${n - 1}, 1fr)`; kids[0].style.gridColumn = "1"; kids[0].style.gridRow = `1 / span ${n - 1}`; kids.slice(1).forEach((k, i) => { k.style.gridColumn = "2"; k.style.gridRow = String(i + 1); }); return; }
    if (L === "rows") { g.style.gridTemplateColumns = "1fr"; g.style.gridTemplateRows = `repeat(${n}, 1fr)`; return; }
    const cols = L === "cols" ? n : Math.ceil(Math.sqrt(n));
    g.style.gridTemplateColumns = `repeat(${cols}, 1fr)`; g.style.gridTemplateRows = `repeat(${Math.ceil(n / cols)}, 1fr)`;
  }
  function spRender() {
    if (!SP.el) return;
    const pages = pageList(), g = SP.el.querySelector(".sp-grid");
    if (SP.max >= SP.panes.length) SP.max = -1;
    g.innerHTML = SP.panes.map((p, i) => `<div class="sp-pane" data-i="${i}"><div class="sp-ph"><select class="sp-sel">${pages.map(([t, n]) => `<option value="${t}" ${t === p.tab ? "selected" : ""}>${escH(n)}</option>`).join("")}</select>
        <input class="sp-sym ${p.tab === "security" ? "" : "hide"}" placeholder="symbol e.g. TSLA" value="${escH(p.sym || "")}" /><span class="sp-sp"></span>
        <button data-a="max" title="${SP.max === i ? "Back to the split view" : "Fill the whole split area"}">${SP.max === i ? "⤡" : "⤢"}</button><button data-a="win" title="Move this pane into its own window">⧉</button><button data-a="x" title="Close this pane">✕</button></div>
        <iframe src="${paneUrl(p)}" title="pane ${i + 1}"></iframe></div>`).join("");
    g.querySelectorAll(".sp-pane").forEach((el) => {
      const i = Number(el.dataset.i), p = SP.panes[i], fr = el.querySelector("iframe"), symEl = el.querySelector(".sp-sym");
      el.querySelector(".sp-sel").addEventListener("change", (e) => {
        p.tab = e.target.value; spSave(); symEl.classList.toggle("hide", p.tab !== "security");
        try { fr.contentDocument.querySelector(`.tab[data-tab="${p.tab}"]`).click(); } catch (err) { fr.src = paneUrl(p); }
      });
      const go = () => { p.sym = symEl.value.trim().toUpperCase(); spSave(); try { fr.contentWindow.openSecurity(p.sym, "GP"); } catch (err) { fr.src = paneUrl(p); } };
      symEl.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      el.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => {
        const a = b.dataset.a;
        if (a === "x") { SP.panes.splice(i, 1); SP.max = -1; spSave(); if (!SP.panes.length) spClose(); else spRender(); }
        else if (a === "max") { SP.max = SP.max === i ? -1 : i; spRender(); }
        else if (a === "win") { NX.openWindow(p.tab, p.sym); }
      }));
    });
    spGrid();
    const n = SP.el.querySelector(".sp-n"); if (n) n.textContent = `${SP.panes.length} / 6 panes`;
    SP.el.querySelectorAll("[data-l]").forEach((b) => b.classList.toggle("on", b.dataset.l === SP.layout));
  }
  function spOpen() {
    if (SP.open) return;
    spLoad();
    if (!SP.panes.length) SP.panes = [{ tab: currentTab() }, { tab: "wall" }];
    const el = document.createElement("div"); el.id = "nx-split"; el.className = "nx-split";
    const pages = pageList();
    el.innerHTML = `<div class="sp-bar"><b>SPLIT SCREEN</b><span class="sp-n"></span><span class="gp-sep"></span>
      ${[["auto", "AUTO GRID"], ["cols", "SIDE BY SIDE"], ["rows", "STACKED"], ["main", "ONE BIG + SIDE"]].map(([l, t]) => `<button class="bb-chip" data-l="${l}">${t}</button>`).join("")}<span class="gp-sep"></span>
      <select class="sp-new">${pages.map(([t, n]) => `<option value="${t}" ${t === "security" ? "selected" : ""}>${escH(n)}</option>`).join("")}</select><button class="bb-chip on" id="sp-add">+ ADD PANE</button><span class="sp-sp"></span>
      <span class="sp-hint">stays in full screen · Alt+S</span><button class="bb-chip" id="sp-close">✕ CLOSE SPLIT</button></div><div class="sp-grid"></div>`;
    document.body.appendChild(el); SP.el = el; SP.open = true; SP.max = -1;
    el.querySelectorAll("[data-l]").forEach((b) => b.addEventListener("click", () => { SP.layout = b.dataset.l; SP.max = -1; spSave(); spRender(); }));
    el.querySelector("#sp-add").addEventListener("click", () => {
      if (SP.panes.length >= 6) return toast("A split holds up to 6 panes");
      SP.panes.push({ tab: el.querySelector(".sp-new").value }); SP.max = -1; spSave(); spRender();
    });
    el.querySelector("#sp-close").addEventListener("click", spClose);
    const btn = document.getElementById("nx-split-btn"); if (btn) btn.classList.add("on");
    spPlace(); spRender();
  }
  function spClose() {
    if (!SP.open) return;
    SP.el.remove(); SP.el = null; SP.open = false;
    const btn = document.getElementById("nx-split-btn"); if (btn) btn.classList.remove("on");
  }
  NX.toggleSplit = () => (SP.open ? spClose() : spOpen());
  window.addEventListener("resize", spPlace);
  document.addEventListener("keydown", (e) => {
    if (IS_PANE) return;
    if (e.altKey && e.key.toLowerCase() === "s" && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "")) { e.preventDefault(); NX.toggleSplit(); }
  });

  window.addEventListener("DOMContentLoaded", buttons);
  if (document.readyState !== "loading") buttons();
  deepLink();
})();
