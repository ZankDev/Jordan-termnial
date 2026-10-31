/* SPIDER — the news brain that lives on the terminal.

   On the NEWS page it crawls the MARKET WIRE headline by headline: walks to each one, reads it (a scan box sweeps the
   title), and marks what matters — cyan for IMPORTANT, pink for CRITICAL — outlining the words that carry the news, with
   the reason on hover. Marks come from /api/spider/scan: trigger-word rules at once, then the language model ("brain")
   that reads all the headlines together every few minutes. If you are using the wire (scrolling or pointing at it) it
   doesn't scroll it for you.
   On every other page it perches in the bottom-right corner. Click it (or type SPIDER in the command bar) to open its
   panel: the brief, everything it marked, and a box to ask it anything about the news.

   The creature is drawn on one fixed, click-through canvas: a body of linked nodes and 8 two-segment legs with a
   tetrapod gait (feet stay planted and step when the body moves away; on the NEWS page feet grip the headline rows).
   Pauses when the tab is hidden; honours prefers-reduced-motion. */
(function () {
  "use strict";
  window.NX = window.NX || {};
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const LS_OFF = "nx.spider.off";
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const CY = "#39d0ff", PK = "#ff3d7f";

  const S = {
    off: store.get(LS_OFF) === "1", scan: null, byLink: new Map(), read: new Set(), lastScan: 0, seenCrit: 0,
    x: window.innerWidth - 80, y: window.innerHeight - 80, vx: 0, vy: 0, heading: -Math.PI / 2,
    tx: null, ty: null, mode: "perch", reading: null, readT: 0, readDur: 0, pulses: [], userAt: 0, nextIdle: 0, rows: [], rowsAt: 0,
  };

  const A = { open: false, url: null, info: null, data: null, read: null, hl: [], paras: [], done: new Set(), userAt: 0, chat: [], scroll: null };

  // ------------------------------------------------------------------ DOM: canvas, click target, panel
  const cv = document.createElement("canvas"); cv.id = "sp-cv";
  const hit = document.createElement("button"); hit.id = "sp-hit"; hit.type = "button"; hit.title = "SPIDER — news brain. Click to see what it marked and ask it anything.";
  hit.setAttribute("aria-label", "Open SPIDER, the news brain");
  const panel = document.createElement("div"); panel.id = "sp-panel"; panel.className = "hidden"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "SPIDER news brain");
  let ctx = null, W = 0, H = 0, dpr = 1;
  function size() {
    dpr = Math.min(2, window.devicePixelRatio || 1); W = window.innerWidth; H = window.innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); cv.style.width = `${W}px`; cv.style.height = `${H}px`;
    ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // ------------------------------------------------------------------ the body and its legs
  const R = 40;                        // leg reach (px)
  const ANG = [-0.45, -1.15, -1.95, -2.6, 0.45, 1.15, 1.95, 2.6];  // around the heading: front pair first
  const GROUP = [0, 1, 1, 0, 1, 0, 0, 1]; // alternating tetrapod gait
  const legs = ANG.map((a, i) => ({ a, g: GROUP[i], fx: S.x + Math.cos(a) * R, fy: S.y + Math.sin(a) * R, step: null, len: R * (i % 4 === 0 || i % 4 === 3 ? 1.12 : 0.95) }));
  const rest = (l, lead = 0) => {
    const a = S.heading + l.a, ahead = lead ? 0.18 : 0;
    return [S.x + Math.cos(a) * l.len + S.vx * ahead, S.y + Math.sin(a) * l.len + S.vy * ahead];
  };
  function snap(px, py) {
    // on the NEWS page feet grip the nearest headline row
    let best = null, bd = 46;
    for (const r of S.rows) {
      const cx = Math.max(r.left + 2, Math.min(r.right - 2, px)), cy = Math.max(r.top + 3, Math.min(r.bottom - 3, py));
      const d = Math.hypot(cx - px, cy - py);
      if (d < bd) { bd = d; best = [cx, cy]; }
    }
    return best || [px, py];
  }
  function updateLegs(dt) {
    const stepping = [0, 0];
    legs.forEach((l) => { if (l.step) stepping[l.g]++; });
    for (const l of legs) {
      if (l.step) {
        l.step.t += dt / 0.11;
        const t = Math.min(1, l.step.t), e = t * t * (3 - 2 * t);
        l.fx = l.step.x0 + (l.step.x1 - l.step.x0) * e; l.fy = l.step.y0 + (l.step.y1 - l.step.y0) * e;
        if (t >= 1) l.step = null;
        continue;
      }
      const [rx, ry] = rest(l);
      const far = Math.hypot(l.fx - rx, l.fy - ry) > l.len * 0.62;
      if (far && !stepping[1 - l.g]) {
        let [nx, ny] = rest(l, 1);
        if (S.mode === "news") [nx, ny] = snap(nx, ny);
        l.step = { t: 0, x0: l.fx, y0: l.fy, x1: nx, y1: ny }; stepping[l.g]++;
      }
    }
  }

  function update(dt) {
    if (S.reading && S.reading.art) {
      const r = S.reading.el.getBoundingClientRect(), k = Math.min(1, S.readT / Math.max(0.2, S.readDur - 0.1));
      S.tx = r.left - 26; S.ty = r.top + 6 + Math.max(0, r.height - 12) * k;
    }
    if (S.tx != null) {
      const dx = S.tx - S.x, dy = S.ty - S.y, d = Math.hypot(dx, dy);
      const sp = (S.mode === "perch" ? 140 : 260) * Math.min(1, d / 60 + 0.25);
      if (d < 2) { S.vx = S.vy = 0; S.x = S.tx; S.y = S.ty; S.tx = S.ty = null; arrived(); }
      else { S.vx = (dx / d) * sp; S.vy = (dy / d) * sp; S.x += S.vx * dt; S.y += S.vy * dt; }
      if (d > 3) { const want = Math.atan2(dy, dx); let dh = want - S.heading; dh = Math.atan2(Math.sin(dh), Math.cos(dh)); S.heading += dh * Math.min(1, dt * 7); }
    } else { S.vx *= 0.8; S.vy *= 0.8; }
    updateLegs(dt);
    if (S.reading) { S.readT += dt; if (S.readT >= S.readDur) doneReading(); }
    S.pulses = S.pulses.filter((p) => (p.t += dt) < 0.9);
    hit.style.transform = `translate(${Math.round(S.x - 22)}px, ${Math.round(S.y - 22)}px)`;
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    // reading: a thread to the headline and a scan box sweeping across it
    if (S.reading) {
      const r = S.reading.el.getBoundingClientRect(), k = Math.min(1, S.readT / Math.max(0.2, S.readDur - 0.15));
      ctx.strokeStyle = "rgba(57,208,255,.35)"; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(S.x, S.y); ctx.lineTo(r.left, r.top + r.height / 2); ctx.stroke(); ctx.setLineDash([]);
      const lvl = S.reading.item && S.reading.item.level;
      ctx.strokeStyle = lvl === "critical" ? PK : CY; ctx.lineWidth = 1.2;
      ctx.fillStyle = lvl === "critical" ? "rgba(255,61,127,.10)" : "rgba(57,208,255,.07)";
      if (S.reading.art) {
        const hh = (r.height + 6) * k;
        ctx.strokeRect(r.left - 6, r.top - 3, r.width + 12, hh); ctx.fillRect(r.left - 6, r.top - 3, r.width + 12, hh);
        ctx.fillStyle = CY; ctx.fillRect(r.left - 6, r.top - 3 + hh - 1, r.width + 12, 1.5);  // the scan line
      } else {
        ctx.strokeRect(r.left - 2, r.top - 1, (r.width + 4) * k, r.height + 2);
        ctx.fillRect(r.left - 2, r.top - 1, (r.width + 4) * k, r.height + 2);
      }
    }
    for (const p of S.pulses) {
      const a = 1 - p.t / 0.9;
      ctx.strokeStyle = p.c === "critical" ? `rgba(255,61,127,${a})` : `rgba(57,208,255,${a})`; ctx.lineWidth = 1.5;
      ctx.strokeRect(p.r.left - 4 - p.t * 10, p.r.top - 3 - p.t * 6, p.r.width + 8 + p.t * 20, p.r.height + 6 + p.t * 12);
    }
    // legs: body -> knee -> foot, knees bent outward; faint links between neighbouring knees (the "network" look)
    const knees = [];
    ctx.lineWidth = 1.15; ctx.strokeStyle = CY; ctx.shadowColor = CY; ctx.shadowBlur = 4;
    for (const l of legs) {
      const mx = (S.x + l.fx) / 2, my = (S.y + l.fy) / 2, dx = l.fx - S.x, dy = l.fy - S.y, d = Math.hypot(dx, dy) || 1;
      let nx = -dy / d, ny = dx / d;
      const side = Math.sign(Math.sin(l.a)) || 1; nx *= side; ny *= side;
      const lift = l.step ? Math.sin(Math.min(1, l.step.t) * Math.PI) * 6 : 0;
      const bend = Math.sqrt(Math.max(0, l.len * l.len * 0.55 - (d * d) / 4)) * 0.75 + lift;
      const kx = mx + nx * bend - (S.x - mx) * 0.15, ky = my + ny * bend - (S.y - my) * 0.15 - lift;
      knees.push([kx, ky]);
      ctx.beginPath(); ctx.moveTo(S.x, S.y); ctx.lineTo(kx, ky); ctx.lineTo(l.fx, l.fy); ctx.stroke();
    }
    ctx.shadowBlur = 0; ctx.strokeStyle = "rgba(57,208,255,.28)"; ctx.lineWidth = 0.8;
    ctx.beginPath();
    for (let i = 0; i < 4; i++) { const a = knees[i], b = knees[i + 4]; ctx.moveTo(a[0], a[1]); ctx.lineTo(knees[(i + 1) % 4][0], knees[(i + 1) % 4][1]); ctx.moveTo(b[0], b[1]); ctx.lineTo(knees[4 + ((i + 1) % 4)][0], knees[4 + ((i + 1) % 4)][1]); }
    ctx.stroke();
    ctx.fillStyle = CY;
    for (const [kx, ky] of knees) { ctx.beginPath(); ctx.arc(kx, ky, 1.7, 0, 6.3); ctx.fill(); }
    for (const l of legs) { ctx.beginPath(); ctx.arc(l.fx, l.fy, 1.4, 0, 6.3); ctx.fill(); }
    // body: a small cluster of linked nodes; pink pulse while the brain is thinking or a new critical item waits
    const hot = S.scan && (S.scan.thinking || (S.scan.critical > S.seenCrit));
    const ch = Math.cos(S.heading), sh = Math.sin(S.heading);
    const nodes = [[9, 0], [0, 6], [-8, 0], [0, -6], [-15, 0]].map(([a, b]) => [S.x + a * ch - b * sh, S.y + a * sh + b * ch]);
    ctx.strokeStyle = CY; ctx.lineWidth = 1;
    ctx.beginPath(); nodes.forEach(([x, y], i) => { const [x2, y2] = nodes[(i + 1) % 4]; if (i < 4) { ctx.moveTo(x, y); ctx.lineTo(x2, y2); } }); ctx.moveTo(nodes[2][0], nodes[2][1]); ctx.lineTo(nodes[4][0], nodes[4][1]); ctx.moveTo(nodes[0][0], nodes[0][1]); ctx.lineTo(nodes[2][0], nodes[2][1]); ctx.stroke();
    ctx.shadowColor = hot ? PK : CY; ctx.shadowBlur = hot ? 10 + 6 * Math.sin(t / 180) : 8;
    ctx.fillStyle = hot ? PK : CY;
    nodes.forEach(([x, y], i) => { ctx.beginPath(); ctx.arc(x, y, i === 4 ? 4.6 : i === 0 ? 3.2 : 2.4, 0, 6.3); ctx.fill(); });
    ctx.shadowBlur = 0;
    if (S.scan && S.scan.critical > S.seenCrit && S.mode === "perch") {
      ctx.fillStyle = PK; ctx.font = "700 10px JetBrains Mono, Consolas, monospace"; ctx.textAlign = "center";
      ctx.fillText(String(S.scan.critical - S.seenCrit), S.x, S.y - 24);
    }
  }

  // ------------------------------------------------------------------ behaviour
  const wire = () => document.getElementById("mk-news");
  const onNews = () => document.getElementById("view-news")?.classList.contains("active");
  function refreshRows() {
    S.rows = [];
    if (A.open) {
      const sc = A.scroll.getBoundingClientRect();
      A.paras.forEach((p) => { const r = p.getBoundingClientRect(); if (r.bottom > sc.top && r.top < sc.bottom && r.height) S.rows.push(r); });
      S.rowsAt = performance.now();
      return [];
    }
    const box = wire();
    if (!box || !onNews()) return [];
    const br = box.getBoundingClientRect(), out = [];
    box.querySelectorAll("li").forEach((li) => {
      const r = li.getBoundingClientRect();
      if (r.bottom > br.top + 30 && r.top < Math.min(br.bottom, H) - 4 && r.height) { out.push(li); S.rows.push(r); }
    });
    S.rowsAt = performance.now();
    return out;
  }
  function think(now) {
    const news = onNews() && wire() && wire().querySelector("li");
    const want = A.open && A.paras.length ? "article" : news && !RP.sym ? "news" : "perch";
    if (want !== S.mode) { S.mode = want; S.reading = null; S.tx = S.ty = null; }
    if (now - S.rowsAt > 400) refreshRows();
    if (S.reading || S.tx != null) return;
    if (S.mode === "article") return thinkArticle(now);
    if (S.mode === "perch") {
      if (now < S.nextIdle) return;
      S.nextIdle = now + 3500 + Math.random() * 5000;
      // perch in the bottom-right corner; with the panel open, just left of it (never on its ASK button)
      const pr = panel.classList.contains("hidden") ? null : panel.getBoundingClientRect();
      const hx = pr ? pr.left - 50 : W - 70, hy = H - 70;
      S.tx = hx - Math.random() * 60; S.ty = hy - Math.random() * 40;
      return;
    }
    // NEWS: next unread visible headline, top to bottom
    if (!S.scan) return;
    const box = wire(), rows = [...box.querySelectorAll("li")];
    const next = rows.find((li) => !S.read.has(link(li)) && visible(li, box));
    if (next) {
      const a = next.querySelector("a"), r = a.getBoundingClientRect();
      S.reading = null; S.next = next;
      S.tx = r.left - 22; S.ty = r.top + r.height / 2;
      return;
    }
    const unread = rows.find((li) => !S.read.has(link(li)));
    if (unread && performance.now() - S.userAt > 8000) {
      const bar = box.querySelector(".nw-bar")?.offsetHeight || 0;
      const room = Math.min(box.clientHeight, H - box.getBoundingClientRect().top) - bar;  // the part of the box the window shows
      box.scrollTo({ top: Math.max(0, unread.offsetTop - bar - Math.min(40, room / 4)), behavior: "smooth" });
      S.nextIdle = now + 700; S.tx = S.x; S.ty = S.y + 0.1;
      return;
    }
    if (now > S.nextIdle) {  // everything on screen is read: wander between the marked ones
      S.nextIdle = now + 2500 + Math.random() * 3000;
      const marked = rows.filter((li) => li.classList.contains("sp-crit") || li.classList.contains("sp-imp")).filter((li) => visible(li, box));
      const pick = marked[Math.floor(Math.random() * marked.length)];
      if (pick) { const r = pick.querySelector("a").getBoundingClientRect(); S.tx = r.right - 10 - Math.random() * Math.min(200, r.width); S.ty = r.top - 4; }
    }
  }
  const link = (li) => li.querySelector("a")?.getAttribute("href") || "";
  // on screen: inside the wire's scroll box (below its filter bar) and inside the window
  const visible = (li, box) => { const r = li.getBoundingClientRect(), b = box.getBoundingClientRect(); return r.top > Math.max(b.top + 28, 0) && r.bottom < Math.min(b.bottom, H) - 4; };
  function arrived() {
    if (!S.next) return;
    if (S.mode === "article") {
      const p = S.next; S.next = null;
      if (!document.contains(p)) return;
      S.reading = { el: p, art: true, n: +p.dataset.i, item: { level: A.hl.some((h) => h.p === +p.dataset.i && h.level === "critical") ? "critical" : "" } };
      S.readT = 0;
      S.readDur = reduce ? 0.05 : Math.min(2.6, 0.3 + p.textContent.length * 0.0016);
      return;
    }
    if (S.mode !== "news") return;
    const li = S.next; S.next = null;
    const a = li.querySelector("a"); if (!a || !document.contains(a)) return;
    const item = S.byLink.get(link(li));
    S.reading = { li, el: a, item };
    S.readT = 0;
    S.readDur = reduce ? 0.05 : Math.min(0.9, 0.18 + a.textContent.length * 0.004) + (item && item.level ? 0.45 : 0);
  }
  function doneReading() {
    if (S.reading.art) { const n = S.reading.n; S.reading = null; A.done.add(n); markPara(n, true); progress(); return; }
    const { li, el, item } = S.reading; S.reading = null;
    S.read.add(link(li));
    if (item && item.level) { mark(li, item); S.pulses.push({ r: el.getBoundingClientRect(), c: item.level, t: 0 }); }
  }

  // ------------------------------------------------------------------ marks (DOM): outline the words that carry the news
  function mark(li, item) {
    const a = li.querySelector("a"); if (!a) return;
    const sig = `${item.level}|${(item.words || []).join(",")}`;
    if (a.dataset.spm === sig) return;
    const title = a.dataset.spt || a.textContent;
    a.dataset.spt = title; a.dataset.spm = sig;
    const words = (item.words || []).filter(Boolean);
    let html = esc(title);
    if (words.length) {
      const re = new RegExp(`(${words.map(escRe).join("|")})`, "gi");
      html = title.split(re).map((p, i) => (i % 2 ? `<mark class="sp-k">${esc(p)}</mark>` : esc(p))).join("");
    }
    a.innerHTML = `<b class="sp-b">${item.level === "critical" ? "CRITICAL" : "IMPORTANT"}</b>${html}`;
    li.classList.remove("sp-crit", "sp-imp"); li.classList.add(item.level === "critical" ? "sp-crit" : "sp-imp");
    li.title = [item.why, item.assets && item.assets.length ? `affects: ${item.assets.join(", ")}` : ""].filter(Boolean).join(" · ") || `marked by ${item.brain ? "the brain" : "trigger words"}`;
  }
  // the wire re-renders on every filter / refresh: put back the marks of what was already read
  function remark(box) {
    box.querySelectorAll("li").forEach((li) => { const l = link(li); const it = S.byLink.get(l); if (it && it.level && (S.read.has(l) || box.id !== "mk-news")) mark(li, it); });
  }
  const observed = new WeakSet();
  function watchLists() {
    for (const id of ["mk-news", "news-body"]) {
      const box = document.getElementById(id);
      if (!box || observed.has(box)) continue;
      observed.add(box);
      new MutationObserver(() => { clearTimeout(box._spT); box._spT = setTimeout(() => remark(box), 30); }).observe(box, { childList: true });
      if (id === "mk-news") ["wheel", "pointerdown", "touchstart", "keydown"].forEach((ev) => box.addEventListener(ev, () => (S.userAt = performance.now()), { passive: true }));
      remark(box);
    }
  }

  // ------------------------------------------------------------------ ARTICLE READER: the full story in-app, read by the spider
  // Any headline link (NEWS wire, HOME top news, the dock, the spider's lists) opens here instead of a new tab
  // (ctrl / cmd / middle-click still opens the publisher). The text comes in first (/api/spider/article); meanwhile the spider
  // walks down the article paragraph by paragraph, and /api/spider/read (the brain reading the whole text) adds the summary,
  // key points, market impact and the exact sentences that matter, which the spider marks as it reaches them.
  const reader = document.createElement("div"); reader.id = "sp-reader"; reader.className = "hidden";
  reader.setAttribute("role", "dialog"); reader.setAttribute("aria-label", "Article reader");
  const ago = (ts) => { if (!ts) return ""; const s = Math.max(0, Date.now() / 1000 - ts); return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`; };
  const pubTime = (iso) => { try { const d = new Date(iso); return Number.isNaN(+d) ? "" : d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return ""; } };

  function openArticle(info) {
    const url = info.link;
    Object.assign(A, { open: true, url, info, data: null, read: null, hl: [], paras: [], done: new Set(), userAt: 0, chat: [] });
    S.reading = null; S.next = null; S.tx = S.ty = null;
    reader.classList.remove("hidden"); document.documentElement.classList.add("sp-reading");
    reader.innerHTML = `<div class="spr-box">
      <header class="spr-h"><span class="spr-src">${esc(info.source || "")}${info.ts ? ` · ${ago(info.ts)}` : ""}</span><span class="sp"></span>
        <a class="spr-btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">ORIGINAL ↗</a><button type="button" class="spr-btn" data-spr="close" aria-label="Close">CLOSE ✕</button></header>
      <div class="spr-scroll"><h1 class="spr-title">${esc(info.title || "")}</h1><div class="spr-meta"></div><div class="spr-body">${NX.load ? NX.load("LOADING THE FULL ARTICLE") : "Loading…"}</div></div>
      <aside class="spr-side">
        <div class="spr-brain"><div class="spr-sh"><b>SPIDER READ</b><span id="spr-prog">waiting for the text…</span></div><div id="spr-read" class="spr-dim">The spider reads the whole article and marks the sentences that matter.</div></div>
        <div class="spr-chat" id="spr-chat"></div>
        <form class="spr-ask"><input name="q" placeholder="ask the spider about this article…" autocomplete="off" maxlength="500"/><button type="submit">ASK</button></form>
      </aside></div>`;
    A.scroll = reader.querySelector(".spr-scroll");
    ["wheel", "pointerdown", "touchstart", "keydown"].forEach((ev) => A.scroll.addEventListener(ev, () => (A.userAt = performance.now()), { passive: true }));
    api(`/spider/article?url=${encodeURIComponent(url)}`).then((a) => {
      if (A.url !== url) return;
      A.data = a;
      if (a.title) reader.querySelector(".spr-title").textContent = a.title;
      const meta = [a.author ? `by ${a.author}` : "", pubTime(a.published), a.words ? `${a.words.toLocaleString()} words · ${Math.max(1, Math.round(a.words / 230))} min read` : ""].filter(Boolean);
      reader.querySelector(".spr-meta").innerHTML = meta.map(esc).join(" · ");
      const body = reader.querySelector(".spr-body");
      if (!a.paragraphs.length) {
        body.innerHTML = `<div class="spr-note"><b>${esc(a.error || "No article text found on this page.")}</b><br/>Use ORIGINAL ↗ to read it on the publisher's site.</div>`;
        progress(); return;
      }
      body.innerHTML = `${a.image ? `<img class="spr-img" src="${esc(a.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"/>` : ""}
        ${a.paragraphs.map((p, i) => `<${p.k === "h" ? "h3" : p.k === "q" ? "blockquote" : "p"} class="spr-p ${p.k === "li" ? "li" : ""}" data-i="${i}">${esc(p.t)}</${p.k === "h" ? "h3" : p.k === "q" ? "blockquote" : "p"}>`).join("")}
        ${a.partial ? `<div class="spr-note">${esc(a.error || "The publisher only shows part of this article to outside readers")} — the spider read what is available. ORIGINAL ↗ has the rest.</div>` : ""}`;
      A.paras = [...body.querySelectorAll(".spr-p")];
      A.paras.forEach((p) => (p.dataset.t = p.textContent));
      progress();
    }).catch((e) => { if (A.url === url) reader.querySelector(".spr-body").innerHTML = `<div class="spr-note"><b>Couldn't load the article.</b> ${esc(e.message || e)}</div>`; });
    api(`/spider/read?url=${encodeURIComponent(url)}`).then((r) => {
      if (A.url !== url) return;
      A.read = r; A.hl = r.highlights || [];
      renderRead();
      A.done.forEach((n) => markPara(n, true)); // what the spider already passed gets its marks now
    }).catch((e) => { if (A.url === url) reader.querySelector("#spr-read").innerHTML = `<span class="spr-dim">The brain couldn't read it: ${esc(e.message || e)}</span>`; });
  }
  function closeArticle() {
    A.open = false; A.url = null; S.reading = null; S.next = null; S.tx = S.ty = null;
    reader.classList.add("hidden"); reader.innerHTML = ""; if (!RP.sym) document.documentElement.classList.remove("sp-reading");
  }
  function renderRead() {
    const r = A.read, box = reader.querySelector("#spr-read"); if (!box || !r) return;
    if (!r.summary && !r.points.length && !r.highlights.length) { box.innerHTML = '<span class="spr-dim">Nothing to read here.</span>'; return; }
    const lvl = r.level === "critical" ? '<span class="spr-lv crit">CRITICAL</span>' : r.level === "important" ? '<span class="spr-lv imp">IMPORTANT</span>' : r.level === "minor" ? '<span class="spr-lv">MINOR</span>' : "";
    box.innerHTML = `${lvl}${r.summary ? `<p class="spr-sum">${esc(r.summary)}</p>` : ""}
      ${r.points.length ? `<div class="spr-sub">KEY POINTS</div><ul class="spr-pts">${r.points.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${r.impact ? `<div class="spr-sub">MARKET IMPACT</div><p class="spr-imp">${esc(r.impact)}</p>` : ""}
      ${r.assets.length ? `<div class="spr-assets">${r.assets.map((x) => `<button type="button" data-sec="${esc(x)}" title="open ${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}
      ${r.highlights.length ? `<div class="spr-sub">MARKED IN THE TEXT · ${r.highlights.length}</div>${r.highlights.map((h, i) => `<button type="button" class="spr-hl ${h.level}" data-hl="${i}"><b>${h.level === "critical" ? "CRIT" : "IMP"}</b><span>${esc(h.why || h.text.slice(0, 80))}</span></button>`).join("")}` : ""}
      ${!r.brain ? '<p class="spr-dim">No language model connected — marked by trigger words only.</p>' : ""}`;
    progress();
  }
  function progress() {
    const el = reader.querySelector("#spr-prog"); if (!el) return;
    const n = A.paras.length, d = A.done.size;
    el.textContent = !A.data ? "waiting for the text…" : !n ? "nothing to read" : d < n ? `reading ${d}/${n} paragraphs${A.read ? "" : " · brain thinking…"}` : `read all ${n} paragraphs${A.read ? ` · ${A.hl.length} marked` : " · brain thinking…"}`;
  }
  // wrap the brain's sentences for paragraph n in marks (and flash them when the spider has just read them)
  function markPara(n, pulse) {
    const p = A.paras[n]; if (!p) return;
    const hls = A.hl.filter((h) => h.p === n); if (!hls.length || p.dataset.marked === String(hls.length)) return;
    const t = p.dataset.t, low = t.toLowerCase(), ranges = [];
    hls.forEach((h) => { const i = low.indexOf(h.text.toLowerCase()); if (i >= 0 && !ranges.some((r) => i < r.e && i + h.text.length > r.s)) ranges.push({ s: i, e: i + h.text.length, h }); });
    if (!ranges.length) return;
    ranges.sort((a, b) => a.s - b.s);
    let html = "", at = 0;
    ranges.forEach((r) => { html += esc(t.slice(at, r.s)) + `<mark class="spr-k ${r.h.level}" title="${esc(r.h.why || "")}">${esc(t.slice(r.s, r.e))}</mark>`; at = r.e; });
    p.innerHTML = html + esc(t.slice(at)); p.dataset.marked = String(hls.length);
    if (pulse) p.querySelectorAll("mark").forEach((m) => S.pulses.push({ r: m.getBoundingClientRect(), c: m.classList.contains("critical") ? "critical" : "important", t: 0 }));
  }
  function thinkArticle(now) {
    const sc = A.scroll, b = sc.getBoundingClientRect();
    const vis = (p) => { const r = p.getBoundingClientRect(); return r.top >= b.top - 2 && r.top < Math.min(b.bottom, H) - 30; };
    const next = A.paras.find((p) => !A.done.has(+p.dataset.i) && vis(p));
    if (next) { const r = next.getBoundingClientRect(); S.next = next; S.tx = r.left - 26; S.ty = r.top + 6; return; }
    const unread = A.paras.find((p) => !A.done.has(+p.dataset.i));
    if (unread && performance.now() - A.userAt > 6000) {
      sc.scrollTo({ top: Math.max(0, unread.offsetTop - 60), behavior: "smooth" });
      S.nextIdle = now + 600; S.tx = S.x; S.ty = S.y + 0.1;
      return;
    }
    if (!unread && now > S.nextIdle) {  // finished: patrol the marked sentences
      S.nextIdle = now + 2500 + Math.random() * 2500;
      const ms = [...reader.querySelectorAll("mark.spr-k")].filter((m) => { const r = m.getBoundingClientRect(); return r.top > b.top && r.bottom < Math.min(b.bottom, H); });
      const m = ms[Math.floor(Math.random() * ms.length)];
      if (m) { const r = m.getBoundingClientRect(); S.tx = r.left + Math.random() * Math.min(r.width, 160); S.ty = r.top - 6; }
    }
  }
  async function askArticle(q) {
    q = q.trim(); if (!q || !A.url) return;
    const url = A.url, c = { q, a: null }; A.chat.push(c); renderChat();
    try { c.a = (await api("/spider/ask", { method: "POST", body: JSON.stringify({ q, url }) })).answer; } catch (e) { c.a = `I couldn't answer: ${e.message || e}`; }
    if (A.url === url) renderChat();
  }
  function renderChat() {
    const box = reader.querySelector("#spr-chat"); if (!box) return;
    box.innerHTML = A.chat.map((c) => `<div class="sp-q">${esc(c.q)}</div><div class="sp-a">${c.a == null ? '<span class="spr-dim">reading…</span>' : esc(c.a)}</div>`).join("");
    reader.querySelector(".spr-side").scrollTop = 1e6;
  }
  reader.addEventListener("click", (e) => {
    if (e.target === reader || e.target.closest("[data-spr='close']")) return closeArticle();
    if (e.target.closest("[data-sec]")) return closeArticle();  // an asset chip: SECURITY opens it (invest.js) behind the reader
    const h = e.target.closest("[data-hl]");
    if (h) { const hl = A.hl[+h.dataset.hl], p = hl && A.paras[hl.p]; if (p) { A.userAt = performance.now(); p.scrollIntoView({ block: "center", behavior: "smooth" }); p.classList.add("spr-flash"); setTimeout(() => p.classList.remove("spr-flash"), 1500); } }
  });
  reader.addEventListener("submit", (e) => { e.preventDefault(); const i = reader.querySelector(".spr-ask input"); const q = i.value; i.value = ""; askArticle(q); });
  // headline links anywhere in the terminal open in the reader
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.closest("#sp-reader")) return;
    if (!a.closest(".bb-news, .sd-news, .spw-m, #sp-panel .sp-m, #sp-panel .sp-refs")) return;
    const href = a.getAttribute("href");
    if (!/^https?:\/\//i.test(href)) return;
    e.preventDefault(); e.stopPropagation();
    const it = S.byLink.get(href) || {};
    const li = a.closest("li");
    openArticle({ link: href, title: it.title || a.dataset.spt || a.textContent.replace(/^(IMPORTANT|CRITICAL)/, ""), source: it.source || li?.querySelector(".src")?.textContent || "", ts: it.ts });
  }, true);
  NX.openArticle = openArticle;

  // ------------------------------------------------------------------ STOCK REPORT: the spider's full research report on one stock
  // Opened from SECURITY (SPIDER REPORT), by asking the spider "report NVDA" / "what do you think about TSLA", or from the dock.
  // /api/spider/report builds it in the background (~1 min: data, filings, the latest results release and news read in full,
  // then the brain writes it); this window shows the progress, then the report. Stored reports open instantly.
  const rpt = document.createElement("div"); rpt.id = "sp-report"; rpt.className = "hidden";
  rpt.setAttribute("role", "dialog"); rpt.setAttribute("aria-label", "SPIDER stock report");
  const RP = { sym: null, timer: 0, chat: [] };
  const num = (x, d = 2) => (typeof x === "number" && Number.isFinite(x) ? x.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
  const pctTxt = (x, d = 1) => (typeof x === "number" && Number.isFinite(x) ? `${x > 0 ? "+" : ""}${x.toFixed(d)}%` : "—");
  const bigTxt = (x) => (typeof x === "number" ? (Math.abs(x) >= 1e12 ? `${(x / 1e12).toFixed(2)}T` : Math.abs(x) >= 1e9 ? `${(x / 1e9).toFixed(1)}B` : Math.abs(x) >= 1e6 ? `${(x / 1e6).toFixed(0)}M` : num(x, 0)) : "—");
  const STANCE = { BULLISH: "bull2", "LEANING BULLISH": "bull1", NEUTRAL: "neu", "LEANING BEARISH": "bear1", BEARISH: "bear2" };
  const STEPS = ["starting", "collecting price, financials, analysts, filings and news", "reading the newest articles in full", "the brain is reading it all and writing the report"];

  function openReport(symbol, fresh = false) {
    symbol = String(symbol || "").trim().toUpperCase(); if (!symbol) return;
    Object.assign(RP, { sym: symbol, chat: [] });
    rpt.classList.remove("hidden"); document.documentElement.classList.add("sp-reading");
    rpt.innerHTML = `<div class="srp-box"><header class="spr-h"><span class="spr-src">SPIDER REPORT · ${esc(symbol)}</span><span class="sp"></span>
      <button type="button" class="spr-btn" data-srp="sec">OPEN IN SECURITY</button><button type="button" class="spr-btn" data-srp="fresh" title="read everything again now">REFRESH</button>
      <button type="button" class="spr-btn" data-srp="close" aria-label="Close">CLOSE ✕</button></header><div class="srp-scroll" id="srp-body"></div></div>`;
    poll(fresh);
  }
  function closeReport() { clearTimeout(RP.timer); RP.sym = null; rpt.classList.add("hidden"); rpt.innerHTML = ""; if (!A.open) document.documentElement.classList.remove("sp-reading"); }
  async function poll(fresh) {
    const sym = RP.sym; clearTimeout(RP.timer);
    let r;
    try { r = await api(`/spider/report?symbol=${encodeURIComponent(sym)}${fresh ? "&fresh=true" : ""}`); }
    catch (e) { if (RP.sym === sym) $b().innerHTML = `<div class="spr-note"><b>Couldn't build the report.</b> ${esc(e.message || e)}</div>`; return; }
    if (RP.sym !== sym) return;
    if (r.status === "running") {
      const i = Math.max(0, STEPS.indexOf(r.step));
      $b().innerHTML = `<div class="srp-wait"><div class="srp-wt">THE SPIDER IS RESEARCHING ${esc(sym)}</div>
        ${STEPS.slice(1).map((s, k) => `<div class="srp-st ${k + 1 < i ? "done" : k + 1 === i ? "on" : ""}"><i></i>${esc(s)}</div>`).join("")}
        <div class="spr-dim">${r.elapsed || 0}s · usually about a minute — filings, the latest results release and the news are read in full</div></div>`;
      RP.timer = setTimeout(() => poll(false), 2500);
      return;
    }
    if (r.status === "error") { $b().innerHTML = `<div class="spr-note"><b>${esc(r.error || "The report failed.")}</b></div>`; return; }
    renderReport(r.report);
  }
  const $b = () => rpt.querySelector("#srp-body") || document.createElement("div");
  function renderReport(p) {
    const v = p.view || {}, t = p.tech || {}, st = p.stats || {}, tg = p.target || {};
    const cls = STANCE[v.stance] || "neu", price = p.price;
    const sc = v.scenarios || {}, pts = ["bear", "base", "bull"].map((k) => sc[k] && typeof sc[k].price === "number" ? sc[k].price : null);
    const all = [price, ...pts].filter((x) => typeof x === "number"), lo = Math.min(...all), hi = Math.max(...all), pos = (x) => (hi > lo ? ((x - lo) / (hi - lo)) * 100 : 50);
    const list = (a) => ({ html: Array.isArray(a) && a.length ? `<ul>${a.map((x) => `<li>${esc(typeof x === "string" ? x : `${x.what || ""}${x.when ? ` — ${x.when}` : ""}`)}</li>`).join("")}</ul>` : '<p class="spr-dim">—</p>' });
    const sect = (title, body) => (body ? `<section class="srp-sec"><h4>${title}</h4>${body.html != null ? body.html : `<p>${esc(body)}</p>`}</section>` : "");
    const when = (x) => { if (x == null || x === "") return ""; const ts = Array.isArray(x) ? x[0] : x; const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts); return Number.isNaN(+d) ? String(ts) : d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }); };
    const scores = v.scores || {};
    $b().innerHTML = `
      <div class="srp-top">
        <div class="srp-id"><div class="srp-sym">${esc(p.symbol)}</div><div class="srp-name">${esc(p.name || "")}</div><div class="spr-dim">${esc([p.sector, p.industry].filter(Boolean).join(" · "))}</div></div>
        <div class="srp-px"><b>${num(price)}</b> <span class="${p.pct > 0 ? "up" : p.pct < 0 ? "dn" : ""}">${pctTxt(p.pct, 2)}</span><div class="spr-dim">${esc(p.currency || "")} · cap ${bigTxt(p.market_cap)}${p.next_earnings ? ` · next earnings ${esc(when(p.next_earnings))}` : ""}</div></div>
        <div class="srp-stance ${cls}"><span>SPIDER'S VIEW</span><b>${esc(v.stance || p.rules_verdict?.stance || "NO VIEW")}</b>
          <div class="srp-conv">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= (v.conviction || 0) ? "on" : ""}"></i>`).join("")}<em>conviction ${v.conviction || "—"}/5 · ${esc(v.horizon || "")}</em></div></div>
      </div>
      ${v.headline ? `<div class="srp-head">${esc(v.headline)}</div>` : ""}
      ${p.note ? `<div class="spr-note">${esc(p.note)}</div>` : ""}
      <div class="srp-grid">
        <div class="srp-main">
          ${sect("THESIS", v.thesis)}
          <div class="srp-two">${sect('<span class="up">BULL CASE</span>', list(v.bull_case))}${sect('<span class="dn">BEAR CASE</span>', list(v.bear_case))}</div>
          ${pts.some((x) => x != null) ? `<section class="srp-sec"><h4>SCENARIOS</h4><div class="srp-scn">${["bear", "base", "bull"].map((k, i) => (pts[i] == null ? "" : `<div class="srp-scn-m ${k}" style="left:${pos(pts[i])}%"><b>${k.toUpperCase()} ${num(pts[i])}</b><span>${pctTxt(price ? (pts[i] / price - 1) * 100 : null)}</span></div>`)).join("")}
            ${typeof price === "number" ? `<div class="srp-scn-now" style="left:${pos(price)}%"><b>NOW ${num(price)}</b></div>` : ""}<i class="srp-scn-line"></i></div>
            <div class="srp-scn-why">${["bear", "base", "bull"].map((k) => (sc[k] && sc[k].why ? `<p><b class="${k}">${k.toUpperCase()}:</b> ${esc(sc[k].why)}</p>` : "")).join("")}</div></section>` : ""}
          ${sect("LATEST RESULTS", v.latest_results)}
          ${sect("FUNDAMENTALS", v.fundamentals)}
          ${sect("VALUATION", v.valuation)}
          ${sect("BALANCE SHEET & CASH", v.balance_sheet)}
          ${sect("TECHNICALS", { html: `${v.technicals ? `<p>${esc(v.technicals)}</p>` : ""}<div class="srp-lv">${(v.levels?.resistance || []).map((x) => `<span class="dn">R ${num(x)}</span>`).join("")}${(v.levels?.support || []).map((x) => `<span class="up">S ${num(x)}</span>`).join("")}</div>` })}
          ${sect("NEWS FLOW", v.news_flow)}
          ${sect("ANALYSTS", v.analysts)}
          ${sect("OWNERSHIP & INSIDERS", v.ownership)}
        </div>
        <aside class="srp-side">
          ${Object.keys(scores).length ? `<section class="srp-sec"><h4>SCORECARD</h4>${Object.entries(scores).map(([k, x]) => `<div class="srp-sc"><span>${esc(k.replace("_", " ").toUpperCase())}</span><i><b style="width:${Math.max(0, Math.min(10, +x || 0)) * 10}%" class="${x >= 7 ? "hi" : x <= 3 ? "lo" : ""}"></b></i><em>${esc(x)}</em></div>`).join("")}</section>` : ""}
          <section class="srp-sec"><h4>KEY NUMBERS</h4><table class="srp-kv"><tbody>
            ${[["P/E", num(st.pe, 1)], ["Fwd P/E", num(st.fwd_pe, 1)], ["PEG", num(st.peg, 2)], ["P/S", num(st.ps, 1)], ["EV/EBITDA", num(st.ev_ebitda, 1)], ["Gross margin", pctTxt(st.gross_margin != null ? st.gross_margin * 100 : null)], ["Op margin", pctTxt(st.op_margin != null ? st.op_margin * 100 : null)], ["Rev growth", pctTxt(st.rev_growth != null ? st.rev_growth * 100 : null)], ["ROE", pctTxt(st.roe != null ? st.roe * 100 : null)], ["Debt/Equity", num(st.debt_equity, 1)], ["Free cash flow", bigTxt(st.fcf)], ["Beta", num(st.beta, 2)], ["RSI 14", num(t.rsi14, 1)], ["Trend", esc(t.trend || "—")], ["vs 52w high", pctTxt(t.from_52w_high_pct)], ["1y volatility", pctTxt(t.vol_1y_pct)], ["Target (mean)", `${num(tg.mean)} · ${pctTxt(tg.mean && price ? (tg.mean / price - 1) * 100 : null)}`], ["Analysts", `${esc(tg.rating || "—")} · ${tg.n || "—"}`]].map(([k, x]) => `<tr><td>${k}</td><td>${x}</td></tr>`).join("")}
          </tbody></table></section>
          ${sect("CATALYSTS", list(v.catalysts))}
          ${sect("RISKS", list(v.risks))}
          ${sect("WHAT WOULD CHANGE MY MIND", list(v.change_my_mind))}
          ${sect("WATCH NEXT", list(v.watch))}
          <section class="srp-sec"><h4>WHAT THE SPIDER READ</h4>
            <p class="spr-dim">${p.read ? `${p.read.articles} articles in full · ${p.read.headlines} headlines · ${p.read.filings} filings${p.read.results ? " · the latest results release" : ""}${p.read.memory ? ` · ${p.read.memory} earlier reads` : ""}` : ""}</p>
            <div class="srp-src">${p.results ? `<a href="${esc(p.results.url)}" data-srp-read="1"><b>RESULTS</b> ${esc(p.results.form)} filed ${esc(p.results.filed)}</a>` : ""}
              ${(p.articles || []).map((a) => `<a href="${esc(a.url)}" data-srp-read="1"><b>READ</b> ${esc(a.source || "")} · ${esc(a.title)}</a>`).join("")}
              ${(p.news || []).slice(0, 8).map((a) => `<a href="${esc(a.link)}" data-srp-read="1">${esc(a.source || "")} · ${esc(a.title)}</a>`).join("")}
              ${(p.filings || []).slice(0, 5).map((f) => `<a href="${esc(f.index || f.url)}" data-srp-read="1"><b>${esc(f.form)}</b> ${esc(f.filed)} ${esc(f.note || "")}</a>`).join("")}</div></section>
          <section class="srp-sec"><h4>ASK ABOUT ${esc(p.symbol)}</h4><div class="spr-chat" id="srp-chat"></div>
            <form class="spr-ask" id="srp-ask"><input name="q" placeholder="e.g. what are the biggest risks?" autocomplete="off" maxlength="400"/><button type="submit">ASK</button></form></section>
        </aside>
      </div>
      <div class="srp-foot">Generated ${esc(new Date((p.generated_at || p.created_at) * 1000).toLocaleString())} by SPIDER${p.brain ? "" : " (data only — no language model)"} · rules verdict: ${esc(p.rules_verdict?.stance || "—")} · Research from public data, not personal financial advice; check the sources before acting.</div>`;
  }
  async function askReport(q) {
    q = q.trim(); const sym = RP.sym; if (!q || !sym) return;
    const c = { q, a: null }; RP.chat.push(c); drawRChat();
    try { c.a = (await api("/spider/ask", { method: "POST", body: JSON.stringify({ q: `About ${sym}: ${q}` }) })).answer; } catch (e) { c.a = `I couldn't answer: ${e.message || e}`; }
    if (RP.sym === sym) drawRChat();
  }
  function drawRChat() { const b = rpt.querySelector("#srp-chat"); if (b) b.innerHTML = RP.chat.map((c) => `<div class="sp-q">${esc(c.q)}</div><div class="sp-a">${c.a == null ? '<span class="spr-dim">reading…</span>' : esc(c.a)}</div>`).join(""); }
  rpt.addEventListener("click", (e) => {
    const b = e.target.closest("[data-srp]");
    if (e.target === rpt || (b && b.dataset.srp === "close")) return closeReport();
    if (b && b.dataset.srp === "fresh") return openReport(RP.sym, true);
    if (b && b.dataset.srp === "sec") { const s = RP.sym; closeReport(); return window.openSecurity && window.openSecurity(s); }
    const a = e.target.closest("[data-srp-read]");
    if (a && !(e.ctrlKey || e.metaKey || e.shiftKey)) { e.preventDefault(); openArticle({ link: a.getAttribute("href"), title: a.textContent.replace(/^(READ|RESULTS)\s*/, ""), source: "" }); }
  });
  rpt.addEventListener("submit", (e) => { e.preventDefault(); const i = rpt.querySelector("#srp-ask input"); const q = i.value; i.value = ""; askReport(q); });
  NX.spiderReport = openReport;

  // ------------------------------------------------------------------ data
  async function loadScan() {
    try {
      const s = await api("/spider/scan");
      S.scan = s; S.lastScan = Date.now();
      S.byLink = new Map(s.items.map((i) => [i.link, i]));
      ["mk-news", "news-body"].forEach((id) => { const b = document.getElementById(id); if (b) remark(b); });
      if (!panel.classList.contains("hidden")) renderPanel();
      if (s.thinking) setTimeout(loadScan, 15000); // the brain is reading: come back for its picks
    } catch (e) { /* keep the last scan */ }
  }

  // ------------------------------------------------------------------ panel: brief, marked list, ask
  const chat = [];
  function renderPanel() {
    const s = S.scan;
    const status = !s ? "reading the wire…" : `${s.read} headlines read · <span class="pk">${s.critical} critical</span> · <span class="cy">${s.important} important</span> · brain ${s.thinking ? '<span class="pk">thinking…</span>' : s.brain ? "on" : s.llm ? "starting" : "off (rules only)"}`;
    panel.innerHTML = `<div class="sp-h"><b>SPIDER</b><span>news brain</span><span class="sp"></span><button type="button" data-sp="close" aria-label="Close">✕</button></div>
      <div class="sp-st">${status}</div>
      <div class="sp-body">
        ${s && s.brief && s.brief.length ? `<div class="sp-sub">WHAT MATTERS RIGHT NOW</div><ul class="sp-brief">${s.brief.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
        <div class="sp-sub">MARKED <span class="dim">· click to find it on the wire</span></div>
        <div class="sp-marks">${s && s.marked.length ? s.marked.slice(0, 14).map((m) => `<div class="sp-m ${m.level}" data-spl="${esc(m.link)}"><span class="sp-lv">${m.level === "critical" ? "CRIT" : "IMP"}</span><div><a href="${esc(m.link)}" target="_blank" rel="noopener noreferrer">${esc(m.title)}</a>${m.why ? `<small>${esc(m.why)}${m.assets && m.assets.length ? ` · <b>${m.assets.map(esc).join(" ")}</b>` : ""}</small>` : `<small>${esc(m.source)}</small>`}</div></div>`).join("") : '<div class="dim">nothing marked yet</div>'}</div>
        <div class="sp-sub">ASK ME</div>
        <div class="sp-chat">${chat.map((c) => `<div class="sp-q">${esc(c.q)}</div><div class="sp-a">${c.a == null ? '<span class="dim">thinking…</span>' : esc(c.a)}${c.refs && c.refs.length ? `<div class="sp-refs">${c.refs.map((r) => `<a href="${esc(r.link)}" target="_blank" rel="noopener noreferrer">${esc(r.source)} · ${esc(r.title)}</a>`).join("")}</div>` : ""}</div>`).join("")}</div>
        ${chat.length ? "" : `<div class="sp-sugg">${["What matters right now?", "Anything on the Fed or rates?", "What could move NQ today?", "Which companies reported results today?", "Report NVDA"].map((q) => `<button type="button" data-spq="${esc(q)}">${esc(q)}</button>`).join("")}</div>`}
      </div>
      <form class="sp-ask"><input name="q" placeholder="ask about the news…" autocomplete="off" maxlength="500"/><button type="submit">ASK</button></form>
      <div class="sp-f"><span class="dim">${S.mem ? `memory: ${S.mem.headlines.toLocaleString()} headlines · ${S.mem.reads.toLocaleString()} articles read in full · ${S.mem.reports} stock reports` : `I read ${s ? s.read : "—"} headlines + the critical events`} · type "report TSLA" for a full stock report. Not personal advice.</span><button type="button" data-sp="hide">${S.off ? "SHOW SPIDER" : "HIDE SPIDER"}</button></div>`;
    const body = panel.querySelector(".sp-body"); if (body && chat.length) body.scrollTop = body.scrollHeight;
  }
  async function ask(q) {
    q = q.trim(); if (!q) return;
    const c = { q, a: null }; chat.push(c); renderPanel();
    try { const r = await api("/spider/ask", { method: "POST", body: JSON.stringify({ q }) }); c.a = r.answer; c.refs = r.refs; if (r.report) openReport(r.report); }
    catch (e) { c.a = `I couldn't answer: ${e.message || e}`; }
    renderPanel();
    panel.querySelector(".sp-ask input")?.focus();
  }
  function open() {
    if (!S.scan || Date.now() - S.lastScan > 60000) loadScan();
    if (S.scan) S.seenCrit = S.scan.critical;
    api("/spider/memory?n=1").then((m) => { S.mem = m.stats; if (!panel.classList.contains("hidden")) renderPanel(); }).catch(() => {});
    panel.classList.remove("hidden"); renderPanel();
    S.nextIdle = 0; S.tx = S.ty = null;  // step aside from the panel now
    setTimeout(() => panel.querySelector(".sp-ask input")?.focus(), 30);
  }
  const close = () => panel.classList.add("hidden");
  panel.addEventListener("click", (e) => {
    const b = e.target.closest("[data-sp]");
    if (b && b.dataset.sp === "close") return close();
    if (b && b.dataset.sp === "hide") { S.off = !S.off; store.set(LS_OFF, S.off ? "1" : "0"); applyOff(); renderPanel(); return; }
    const q = e.target.closest("[data-spq]"); if (q) return ask(q.dataset.spq);
    const m = e.target.closest("[data-spl]");
    if (m && !e.target.closest("a")) {
      const l = m.dataset.spl;
      if (!onNews()) document.querySelector('.tab[data-tab="news"]')?.click();
      setTimeout(() => {
        const li = [...(wire()?.querySelectorAll("li") || [])].find((x) => link(x) === l);
        if (li) { li.scrollIntoView({ block: "center", behavior: "smooth" }); li.classList.add("sp-flash"); setTimeout(() => li.classList.remove("sp-flash"), 1600); }
      }, onNews() ? 0 : 1200);
    }
  });
  panel.addEventListener("submit", (e) => { e.preventDefault(); const i = panel.querySelector(".sp-ask input"); const q = i.value; i.value = ""; ask(q); });
  document.addEventListener("keydown", (e) => { if (e.key !== "Escape") return; if (A.open) closeArticle(); else if (RP.sym) closeReport(); else if (!panel.classList.contains("hidden")) close(); });
  hit.addEventListener("click", () => (panel.classList.contains("hidden") ? open() : close()));

  function applyOff() { cv.style.display = hit.style.display = S.off ? "none" : ""; }

  // ------------------------------------------------------------------ loop
  let last = 0, raf = 0;
  function frame(t) {
    raf = requestAnimationFrame(frame);
    if (S.off || document.hidden || !ctx) return;
    const dt = Math.min(0.05, (t - (last || t)) / 1000); last = t;
    think(t); update(dt); draw(t);
  }
  NX.spider = { open, close, scan: () => S.scan, report: (s, f) => openReport(s, f), ask: (q) => api("/spider/ask", { method: "POST", body: JSON.stringify({ q }) }) };
  function boot() {
    // canvas + click target live on <html>, outside the zoomed <body> (responsive.css zooms body on wide screens), so
    // their pixels are plain viewport pixels, the same space getBoundingClientRect() reports headline positions in
    document.documentElement.append(cv, hit); document.body.append(panel, rpt, reader);
    size(); applyOff();
    window.addEventListener("resize", () => { size(); if (S.mode === "perch") { S.x = Math.min(S.x, W - 40); S.y = Math.min(S.y, H - 40); } });
    loadScan();
    setInterval(() => { if (!document.hidden && (onNews() || !panel.classList.contains("hidden") || Date.now() - S.lastScan > 300000)) loadScan(); }, 60000);
    setInterval(watchLists, 1500); watchLists();
    raf = requestAnimationFrame(frame);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
