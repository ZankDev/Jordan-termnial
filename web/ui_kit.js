/* UIX — small interaction kit shared by every page (loaded after dashboard.js).
   - Tooltips:  any element with data-uix-tip="Title|line|line" gets a hover tooltip (the account detail keeps its own).
   - Sortable:  any <table class="uix-sort"> sorts by a clicked header (numbers understood, $ , % + stripped).
   - Count-up:  UIX.countUp(root) animates numbers in elements with class "uix-count".
   - Sparkline: UIX.spark(values, {w,h}) → inline SVG string.
   - Draggable panels: UIX.draggable(gridEl, key) lets the user drag .bb-panel boxes by their header to reorder them;
     the order is remembered per page in localStorage. */
(function () {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let tip = null;
  function showTip(raw, x, y) {
    if (!tip) { tip = document.createElement("div"); tip.id = "uix-tip"; document.body.appendChild(tip); }
    tip.innerHTML = raw.split("|").map((l, i) => (i ? `<span>${esc(l)}</span>` : `<b>${esc(l)}</b>`)).join("");
    tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(6, Math.min(innerWidth - w - 6, x + 14)) + "px";
    tip.style.top = Math.max(6, Math.min(innerHeight - h - 6, y + 14)) + "px";
  }
  const hideTip = () => { if (tip) tip.style.display = "none"; };
  document.addEventListener("mousemove", (e) => {
    const t = e.target.closest?.("[data-uix-tip]");
    if (t) showTip(t.dataset.uixTip, e.clientX, e.clientY); else hideTip();
  }, { passive: true });
  document.addEventListener("scroll", hideTip, { passive: true, capture: true });

  const cellVal = (td) => {
    const raw = (td?.dataset.v ?? td?.textContent ?? "").trim();
    const n = parseFloat(raw.replace(/[$,%+×x\s]/g, "").replace(/^—$/, ""));
    return Number.isFinite(n) && /^[-+$\d.,%×x\s]+$/.test(raw) ? n : raw.toLowerCase();
  };
  document.addEventListener("click", (e) => {
    const th = e.target.closest?.("table.uix-sort th");
    if (!th) return;
    const table = th.closest("table"), idx = [...th.parentNode.children].indexOf(th), body = table.tBodies[0];
    if (!body) return;
    const dir = th.dataset.dir === "desc" ? "asc" : "desc";
    table.querySelectorAll("th").forEach((h) => { delete h.dataset.dir; h.classList.remove("uix-srt"); });
    th.dataset.dir = dir; th.classList.add("uix-srt");
    const rows = [...body.rows];
    rows.sort((a, b) => {
      const x = cellVal(a.cells[idx]), y = cellVal(b.cells[idx]);
      const c = typeof x === "number" && typeof y === "number" ? x - y : typeof x === "number" ? -1 : typeof y === "number" ? 1 : String(x).localeCompare(String(y));
      return dir === "desc" ? -c : c;
    });
    rows.forEach((r) => body.appendChild(r));
  });

  function countUp(root) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    (root || document).querySelectorAll(".uix-count").forEach((el) => {
      const txt = el.textContent, m = txt.match(/-?[\d,]*\.?\d+/);
      if (!m || el.dataset.uixDone === txt) return;
      el.dataset.uixDone = txt;
      const target = parseFloat(m[0].replace(/,/g, "")), dec = (m[0].split(".")[1] || "").length;
      if (!isFinite(target) || target === 0) return;
      const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / 650), v = target * (1 - Math.pow(1 - k, 3));
        el.textContent = txt.replace(m[0], (target < 0 ? "-" : "") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }));
        if (k < 1) requestAnimationFrame(step); else el.textContent = txt;
      };
      requestAnimationFrame(step);
    });
  }

  function spark(vals, o = {}) {
    const w = o.w || 90, h = o.h || 24;
    if (!vals || vals.length < 2) return `<svg class="uix-spark" width="${w}" height="${h}"></svg>`;
    const lo = Math.min(...vals), hi = Math.max(...vals), s = hi - lo || 1;
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / s) * (h - 4)).toFixed(1)}`).join(" ");
    const up = vals[vals.length - 1] >= vals[0];
    return `<svg class="uix-spark ${up ? "up" : "dn"}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none"/></svg>`;
  }

  function draggable(grid, key) {
    if (!grid || grid.dataset.uixDrag) return;
    grid.dataset.uixDrag = "1";
    const lsKey = `uix_order_${key}`;
    const idOf = (p) => p.dataset.uixId || (p.dataset.uixId = (p.querySelector(".bb-ph")?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40));
    const panels = () => [...grid.children].filter((c) => c.classList.contains("bb-panel"));
    try {
      const saved = JSON.parse(localStorage.getItem(lsKey) || "[]");
      const map = new Map(panels().map((p) => [idOf(p), p]));
      saved.forEach((id) => { const p = map.get(id); if (p) grid.appendChild(p); });
      panels().filter((p) => !saved.includes(idOf(p))).forEach((p) => grid.appendChild(p));
    } catch (e) { /* storage unavailable */ }
    let drag = null;
    panels().forEach((p) => {
      const h = p.querySelector(".bb-ph");
      if (!h) return;
      h.classList.add("uix-handle");
      h.title = "Drag to move this panel";
      h.setAttribute("draggable", "true");
      h.addEventListener("dragstart", (e) => { drag = p; p.classList.add("uix-dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", idOf(p)); });
      h.addEventListener("dragend", () => { p.classList.remove("uix-dragging"); drag = null; grid.querySelectorAll(".uix-over").forEach((x) => x.classList.remove("uix-over")); });
      p.addEventListener("dragover", (e) => { if (drag && drag !== p) { e.preventDefault(); p.classList.add("uix-over"); } });
      p.addEventListener("dragleave", () => p.classList.remove("uix-over"));
      p.addEventListener("drop", (e) => {
        e.preventDefault(); p.classList.remove("uix-over");
        if (!drag || drag === p) return;
        const list = panels();
        grid.insertBefore(drag, list.indexOf(drag) < list.indexOf(p) ? p.nextSibling : p);
        try { localStorage.setItem(lsKey, JSON.stringify(panels().map(idOf))); } catch (err) { /* ignore */ }
      });
    });
  }

  window.UIX = { esc, countUp, spark, draggable, tip: showTip, hideTip };
})();
