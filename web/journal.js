/* LEARNING JOURNAL: a long, Google-Docs-like page to write what you learn. Rich text (fonts incl. handwriting / marker fonts, sizes, colours,
   highlighter), headings, lists and checklists, callouts (lesson / mistake / rule / setup), tables, links, screenshots (paste, drop, upload,
   screen capture), charts from your own numbers, live market charts from the terminal, and a drawing board. Entries autosave to the server
   (/api/journal). Depends on api() from dashboard.js. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };
  const J = { built: false, docs: [], cur: null, dirty: false, saveT: 0, saving: false, q: "", tag: "", pref: lget("nx_lj_pref", { paper: "light", last: null }) };
  const req = (path, method = "GET", body) => fetch("/api/journal" + path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })
    .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`); return j; });
  const toast = (m) => { if (window.showToast) window.showToast(m); else console.log(m); };

  const FONTS = [["Inter", "Inter"], ["Georgia", "Georgia"], ["Merriweather", "Merriweather"], ["Heebo", "Heebo (עברית)"], ["Rubik", "Rubik"], ["JetBrains Mono", "Mono"],
    ["Caveat", "Caveat ✍"], ["Permanent Marker", "Permanent Marker"], ["Patrick Hand", "Patrick Hand"], ["Kalam", "Kalam"], ["Amatic SC", "Amatic SC"]];
  const SIZES = [10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64];
  const COLORS = ["#111111", "#5f6368", "#d93025", "#e8710a", "#f9ab00", "#188038", "#1a73e8", "#9334e6", "#ffffff", "#ff4a4a", "#ffa028", "#2be07e", "#4aa8ff", "#c58bff"];
  const MARKERS = [["#fff475", "yellow"], ["#ccff90", "green"], ["#a7ffeb", "mint"], ["#aecbfa", "blue"], ["#fdcfe8", "pink"], ["#ffd8a8", "orange"]];
  const CALLOUTS = { lesson: ["💡", "LESSON"], mistake: ["⚠️", "MISTAKE"], rule: ["✅", "RULE"], setup: ["📌", "SETUP"] };
  const SV = (p) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  const IC = {
    undo: SV('<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'), redo: SV('<path d="m15 14 5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>'),
    b: "<b>B</b>", i: "<i style='font-family:Georgia'>I</i>", u: "<u>U</u>", s: "<s>S</s>",
    color: SV('<path d="M5 20h14"/><path d="m8 16 4-11 4 11"/><path d="M9.5 12h5"/>'), mark: SV('<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>'),
    left: SV('<path d="M3 6h18M3 12h12M3 18h16"/>'), center: SV('<path d="M3 6h18M6 12h12M4 18h16"/>'), right: SV('<path d="M3 6h18M9 12h12M5 18h16"/>'), justify: SV('<path d="M3 6h18M3 12h18M3 18h18"/>'),
    rtl: "<b style='font-size:11px'>RTL</b>", ul: SV('<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
    ol: SV('<path d="M10 6h11M10 12h11M10 18h11"/><path d="M4 6h1v4M4 10h2M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>'), check: SV('<rect x="3" y="4" width="6" height="6" rx="1"/><path d="m4.5 7 1 1 2-2"/><path d="M13 7h8"/><rect x="3" y="14" width="6" height="6" rx="1"/><path d="M13 17h8"/>'),
    link: SV('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
    img: SV('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>'),
    shot: SV('<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/>'),
    table: SV('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'), hr: SV('<path d="M3 12h18"/>'),
    code: SV('<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>'), chart: SV('<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>'),
    candle: SV('<path d="M7 3v4M7 15v6M17 3v6M17 17v4"/><rect x="5" y="7" width="4" height="8" rx="1"/><rect x="15" y="9" width="4" height="8" rx="1"/>'),
    draw: SV('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'), date: SV('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
    pin: SV('<path d="M12 17v5"/><path d="M9 10.8V4h6v6.8l3 3.2H6Z"/>'), trash: SV('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>'), print: SV('<path d="M6 9V2h12v7"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v8H6Z"/>'),
    dl: SV('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>'), moon: SV('<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10Z"/>'), clear: SV('<path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13"/><path d="m3 3 18 18"/>'),
  };

  // ---------------------------------------------------------------- page
  function build() {
    const v = $("#view-journal");
    v.innerHTML = `
    <div class="lj ${J.pref.paper === "dark" ? "lj-darkpaper" : ""}">
      <aside class="lj-side">
        <div class="lj-brand"><span class="lj-logo">✎</span><div><b>LEARNING JOURNAL</b><i>write what you learn · every lesson in one place</i></div></div>
        <button class="lj-new" id="lj-new">＋ NEW ENTRY</button>
        <div class="lj-search"><input id="lj-q" placeholder="Search everything you wrote…"></div>
        <div class="lj-tagbar" id="lj-tagbar"></div>
        <div class="lj-list" id="lj-list"></div>
        <div class="lj-outh">OUTLINE</div>
        <div class="lj-outline" id="lj-outline"><div class="lj-dim">Headings appear here.</div></div>
      </aside>
      <main class="lj-main">
        <div class="lj-bar" id="lj-bar">
          <button data-cmd="undo" title="Undo (Ctrl+Z)">${IC.undo}</button><button data-cmd="redo" title="Redo (Ctrl+Y)">${IC.redo}</button><span class="lj-sep"></span>
          <select id="lj-block" title="Paragraph style"><option value="p">Normal text</option><option value="title">Title</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option><option value="blockquote">Quote</option><option value="pre">Code</option></select>
          <select id="lj-font" title="Font">${FONTS.map(([f, n]) => `<option value="${f}" style="font-family:'${f}'">${n}</option>`).join("")}</select>
          <select id="lj-size" title="Size">${SIZES.map((s) => `<option value="${s}" ${s === 16 ? "selected" : ""}>${s}</option>`).join("")}</select><span class="lj-sep"></span>
          <button data-cmd="bold" title="Bold (Ctrl+B)">${IC.b}</button><button data-cmd="italic" title="Italic (Ctrl+I)">${IC.i}</button><button data-cmd="underline" title="Underline (Ctrl+U)">${IC.u}</button><button data-cmd="strikeThrough" title="Strikethrough">${IC.s}</button>
          <span class="lj-dd"><button data-dd="color" title="Text colour">${IC.color}<i class="lj-swatch" id="lj-cur-color" style="background:#111"></i></button><div class="lj-pop" data-pop="color">${COLORS.map((c) => `<button data-color="${c}" style="background:${c}"></button>`).join("")}<button data-color="" class="lj-none" title="Default">⌫</button></div></span>
          <span class="lj-dd"><button data-dd="mark" title="Highlighter (marker)">${IC.mark}<i class="lj-swatch" style="background:#fff475"></i></button><div class="lj-pop lj-pop-mk" data-pop="mark">${MARKERS.map(([c, n]) => `<button data-mark="${c}" title="${n} marker" style="background:${c}"></button>`).join("")}<button data-mark="transparent" class="lj-none" title="Remove highlight">⌫</button><div class="lj-popnote">Marker styles</div><button data-mstyle="under" class="lj-mstyle">underline marker</button><button data-mstyle="circle" class="lj-mstyle">box it</button></div></span>
          <span class="lj-sep"></span>
          <button data-cmd="justifyLeft" title="Align left">${IC.left}</button><button data-cmd="justifyCenter" title="Centre">${IC.center}</button><button data-cmd="justifyRight" title="Align right">${IC.right}</button><button data-cmd="justifyFull" title="Justify">${IC.justify}</button><button data-act="rtl" title="Right-to-left paragraph (Hebrew)">${IC.rtl}</button><span class="lj-sep"></span>
          <button data-cmd="insertUnorderedList" title="Bullets">${IC.ul}</button><button data-cmd="insertOrderedList" title="Numbered list">${IC.ol}</button><button data-act="check" title="Checklist">${IC.check}</button><span class="lj-sep"></span>
          <span class="lj-dd"><button data-dd="callout" title="Callout box" class="lj-txtbtn">💡 BOX</button><div class="lj-pop lj-pop-list" data-pop="callout">${Object.entries(CALLOUTS).map(([k, [e, n]]) => `<button data-callout="${k}">${e} ${n}</button>`).join("")}</div></span>
          <button data-act="link" title="Link (Ctrl+K)">${IC.link}</button><button data-act="image" title="Image / screenshot from a file (or just paste with Ctrl+V)">${IC.img}</button><button data-act="capture" title="Capture a screenshot of a window or screen">${IC.shot}</button>
          <span class="lj-dd"><button data-dd="table" title="Table">${IC.table}</button><div class="lj-pop lj-pop-grid" data-pop="table"><div class="lj-tgrid" id="lj-tgrid"></div><div class="lj-popnote" id="lj-tsize">Table</div></div></span>
          <button data-act="hr" title="Divider">${IC.hr}</button><button data-act="code" title="Code block">${IC.code}</button><button data-act="date" title="Insert today's date">${IC.date}</button><span class="lj-sep"></span>
          <button data-act="chart" class="lj-txtbtn lj-acc" title="Chart from your own numbers">${IC.chart} CHART</button><button data-act="mchart" class="lj-txtbtn lj-acc" title="Market chart from the terminal (any symbol)">${IC.candle} MARKET</button><button data-act="draw" class="lj-txtbtn lj-acc" title="Drawing board">${IC.draw} DRAW</button>
          <span class="lj-sp"></span>
          <button data-act="clearfmt" title="Clear formatting">${IC.clear}</button><button data-act="paper" title="Light / dark paper">${IC.moon}</button><button data-act="export" title="Download as HTML">${IC.dl}</button><button data-act="print" title="Print / save as PDF">${IC.print}</button>
        </div>
        <div class="lj-scroll" id="lj-scroll">
          <div class="lj-paper">
            <div class="lj-meta"><span id="lj-date"></span><button class="lj-pinb" id="lj-pin" title="Pin to the top">${IC.pin}</button><button class="lj-pinb" id="lj-del" title="Delete this entry">${IC.trash}</button></div>
            <input class="lj-title" id="lj-title" placeholder="Title of what you learned">
            <div class="lj-tags"><span>#</span><input id="lj-tags" placeholder="tags, comma separated (e.g. gamma, order flow, mistakes)"></div>
            <div class="lj-doc" id="lj-doc" contenteditable="true" spellcheck="true" data-ph="Start writing… paste screenshots with Ctrl+V, type # for a heading, - for bullets, [] for a checklist."></div>
          </div>
        </div>
        <div class="lj-status"><span id="lj-words">0 words</span><span id="lj-tblops" class="lj-tblops" hidden><button data-tbl="row">+ row</button><button data-tbl="col">+ column</button><button data-tbl="delrow">− row</button><button data-tbl="delcol">− column</button></span><span class="lj-sp"></span><span id="lj-saved" class="lj-saved">—</span></div>
      </main>
      <input type="file" id="lj-file" accept="image/*" multiple hidden>
      <div class="lj-imgbar" id="lj-imgbar" hidden><button data-w="25">25%</button><button data-w="50">50%</button><button data-w="75">75%</button><button data-w="100">100%</button><span class="lj-sep"></span><button data-al="left">⇤</button><button data-al="center">↔</button><button data-al="right">⇥</button><span class="lj-sep"></span><button data-ia="edit" title="Edit this chart / drawing">✎ EDIT</button><button data-ia="open" title="Open full size">⤢</button><button data-ia="del" title="Remove">✕</button></div>
      <div class="lj-modal" id="lj-modal" hidden><div class="lj-mbox" id="lj-mbox"></div></div>
    </div>`;
    wire();
  }

  // ---------------------------------------------------------------- editor helpers
  const doc = () => $("#lj-doc");
  let savedRange = null;
  const keepSel = () => { const s = getSelection(); if (s.rangeCount && doc().contains(s.anchorNode)) savedRange = s.getRangeAt(0).cloneRange(); };
  const restoreSel = () => { doc().focus({ preventScroll: true }); if (savedRange) { const s = getSelection(); s.removeAllRanges(); s.addRange(savedRange); } };
  const exec = (cmd, val = null) => { restoreSel(); document.execCommand("styleWithCSS", false, true); document.execCommand(cmd, false, val); changed(); keepSel(); };
  const blockOf = (n) => { while (n && n !== doc()) { if (n.nodeType === 1 && /^(P|H1|H2|H3|BLOCKQUOTE|PRE|LI|DIV)$/.test(n.nodeName)) return n; n = n.parentNode; } return null; };
  // a block insert never lands inside a picture's caption, a callout label or a table cell: move the caret to a fresh line after it
  function caretOutside(blocky) {
    const s = getSelection(); if (!s.rangeCount) return;
    const n = s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentNode);
    const host = el && (el.closest("figure.lj-fig") || el.closest(".lj-ce") || (blocky ? el.closest("table.lj-table") : null));
    if (!host || !doc().contains(host)) return;
    const after = host.closest(".lj-callout") && host.classList.contains("lj-ce") ? host.nextElementSibling : host.nextElementSibling;
    let p = after && after.nodeName === "P" && !after.textContent.trim() ? after : null;
    if (!p) { p = document.createElement("p"); p.innerHTML = "<br>"; host.after(p); }
    const r = document.createRange(); r.setStart(p, 0); r.collapse(true); s.removeAllRanges(); s.addRange(r);
  }
  function insertHTML(html, blocky = /^<(figure|table|div|hr)/.test(html)) { restoreSel(); caretOutside(blocky); document.execCommand("insertHTML", false, html); changed(); keepSel(); }
  function wrapSel(styleFn) {
    restoreSel();
    const s = getSelection(); if (!s.rangeCount) return;
    const r = s.getRangeAt(0); if (r.collapsed) return toast("Select some text first");
    const span = document.createElement("span"); styleFn(span);
    try { span.appendChild(r.extractContents()); r.insertNode(span); } catch (e) { return; }
    s.removeAllRanges(); const nr = document.createRange(); nr.selectNodeContents(span); s.addRange(nr); changed(); keepSel();
  }
  const figHTML = (url, o = {}) => `<figure class="lj-fig" contenteditable="false" style="width:${o.w || 100}%" data-align="${o.al || "center"}"${o.kind ? ` data-kind="${o.kind}"` : ""}${o.spec ? ` data-spec="${esc(o.spec)}"` : ""}><img src="${esc(url)}" alt=""><figcaption contenteditable="true" data-ph="Add a caption…">${esc(o.cap || "")}</figcaption></figure><p><br></p>`;
  async function upload(dataUrl) { const r = await req("/upload", "POST", { data: dataUrl }); return r.url; }
  const fileToData = (f) => new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(f); });
  async function shrink(dataUrl, max = 2400) {
    const im = new Image(); im.src = dataUrl; await im.decode();
    if (Math.max(im.width, im.height) <= max && dataUrl.length < 3e6) return dataUrl;
    const s = max / Math.max(im.width, im.height), c = document.createElement("canvas"); c.width = Math.round(im.width * Math.min(1, s)); c.height = Math.round(im.height * Math.min(1, s));
    c.getContext("2d").drawImage(im, 0, 0, c.width, c.height); return c.toDataURL("image/jpeg", 0.9);
  }
  async function insertImageData(dataUrl, o = {}) {
    try { setSaved("uploading…"); const url = await upload(await shrink(dataUrl)); insertHTML(figHTML(url, o)); } catch (e) { toast("Image upload failed: " + e.message); setSaved("upload failed"); }
  }
  function sanitize(html) {
    const t = document.createElement("template"); t.innerHTML = html;
    t.content.querySelectorAll("script,style,iframe,object,embed,link,meta").forEach((n) => n.remove());
    t.content.querySelectorAll("*").forEach((n) => { [...n.attributes].forEach((a) => { if (/^on/i.test(a.name) || (/^(href|src)$/i.test(a.name) && /^\s*javascript:/i.test(a.value))) n.removeAttribute(a.name); }); });
    return t.innerHTML;
  }

  // ---------------------------------------------------------------- save / load
  function setSaved(t) { const el = $("#lj-saved"); if (el) el.textContent = t; }
  function changed() {
    if (!J.cur) return;
    J.dirty = true; setSaved("editing…"); clearTimeout(J.saveT); J.saveT = setTimeout(save, 900);
    stats(); outline();
  }
  async function save() {
    if (!J.cur || !J.dirty || J.saving) return;
    J.saving = true; J.dirty = false;
    const body = { title: $("#lj-title").value.trim() || "Untitled", html: doc().innerHTML, tags: $("#lj-tags").value, pinned: !!J.cur.pinned };
    try { await req(`/docs/${J.cur.id}`, "PUT", body); Object.assign(J.cur, body); setSaved("✓ saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })); refreshListItem(); }
    catch (e) { J.dirty = true; setSaved("⚠ not saved: " + e.message); }
    finally { J.saving = false; if (J.dirty) { clearTimeout(J.saveT); J.saveT = setTimeout(save, 1500); } }
  }
  async function flush() { clearTimeout(J.saveT); if (J.dirty) await save(); }
  async function loadList() {
    try { J.docs = (await req(`/docs?q=${encodeURIComponent(J.q)}&tag=${encodeURIComponent(J.tag)}`)).docs; } catch (e) { $("#lj-list").innerHTML = `<div class="lj-dim">${esc(e.message)}</div>`; return; }
    renderList();
  }
  function renderList() {
    const tags = {}; J.docs.forEach((d) => (d.tags || "").split(",").map((t) => t.trim()).filter(Boolean).forEach((t) => (tags[t] = (tags[t] || 0) + 1)));
    $("#lj-tagbar").innerHTML = Object.keys(tags).length || J.tag ? [`<button data-tag="" class="${J.tag ? "" : "on"}">all</button>`, ...Object.entries(tags).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([t, n]) => `<button data-tag="${esc(t)}" class="${J.tag === t ? "on" : ""}">#${esc(t)} <i>${n}</i></button>`)].join("") : "";
    $("#lj-list").innerHTML = J.docs.length ? J.docs.map((d) => `<div class="lj-item ${J.cur && J.cur.id === d.id ? "on" : ""}" data-id="${d.id}">
        <div class="lj-it">${d.pinned ? "📌 " : ""}${esc(d.title || "Untitled")}</div><div class="lj-is">${esc(d.snippet || "Empty entry")}</div>
        <div class="lj-id">${new Date(d.updated * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })} · ${d.words} words${d.tags ? " · " + esc(d.tags.split(",").slice(0, 3).map((t) => "#" + t.trim()).join(" ")) : ""}</div></div>`).join("")
      : `<div class="lj-dim" style="padding:14px">${J.q || J.tag ? "Nothing matches." : "No entries yet. Press ＋ NEW ENTRY."}</div>`;
  }
  function refreshListItem() { const d = J.docs.find((x) => x.id === J.cur.id); if (d) { d.title = J.cur.title; d.tags = J.cur.tags; d.pinned = J.cur.pinned; d.updated = Date.now() / 1000; d.snippet = doc().innerText.replace(/\s+/g, " ").slice(0, 160); d.words = countWords(); } renderList(); }
  async function open(id) {
    await flush();
    let d; try { d = await req(`/docs/${id}`); } catch (e) { toast(e.message); return; }
    J.cur = d; J.pref.last = id; lset("nx_lj_pref", J.pref);
    $("#lj-title").value = d.title === "Untitled" && !d.html ? "" : d.title; $("#lj-tags").value = d.tags || ""; doc().innerHTML = sanitize(d.html || "") || "<p><br></p>"; $$("h1,h2,h3", doc()).forEach((h) => { if (!h.textContent.trim() && h !== doc().lastElementChild) h.remove(); });
    $("#lj-date").textContent = `Created ${new Date(d.created * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}`;
    $("#lj-pin").classList.toggle("on", !!d.pinned);
    J.dirty = false; setSaved("✓ saved"); stats(); outline(); renderList(); $("#lj-scroll").scrollTop = 0;
    if (!d.html) $("#lj-title").focus();
  }
  async function newDoc() {
    await flush();
    const today = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
    const tpl = `<h2>What I learned</h2><p><br></p><h2>Screenshots</h2><p><br></p><div class="lj-callout" data-kind="mistake"><span class="lj-ce" contenteditable="false">⚠️ MISTAKE</span><p>What went wrong, and why…</p></div><div class="lj-callout" data-kind="rule"><span class="lj-ce" contenteditable="false">✅ RULE</span><p>The rule I take from it…</p></div><p><br></p>`;
    const r = await req("/docs", "POST", { title: "Untitled", html: tpl, tags: "" });
    await loadList(); await open(r.id);
    $("#lj-title").placeholder = `What did you learn today? (${today})`;
  }
  const countWords = () => (doc().innerText.match(/\S+/g) || []).length;
  function stats() { const w = countWords(); $("#lj-words").textContent = `${w.toLocaleString()} words · ${Math.max(1, Math.round(w / 220))} min read · ${$$("figure", doc()).length} images`; }
  function outline() {
    const hs = $$("h1,h2,h3", doc()).filter((h) => h.innerText.trim());
    $("#lj-outline").innerHTML = hs.length ? hs.map((h, i) => `<button data-out="${i}" class="lj-o-${h.classList.contains("lj-titleblk") ? "t" : h.nodeName.toLowerCase()}">${esc(h.innerText.trim() || "…").slice(0, 60)}</button>`).join("") : `<div class="lj-dim">Headings appear here.</div>`;
  }

  // ---------------------------------------------------------------- toolbar actions
  function setBlock(v) {
    restoreSel();
    if (v === "title") { document.execCommand("formatBlock", false, "h1"); const b = blockOf(getSelection().anchorNode); if (b) b.classList.add("lj-titleblk"); }
    else { document.execCommand("formatBlock", false, v); const b = blockOf(getSelection().anchorNode); if (b) b.classList.remove("lj-titleblk"); }
    changed();
  }
  function checklist() {
    restoreSel();
    if (document.activeElement !== doc()) doc().focus();
    const b = blockOf(getSelection().anchorNode);
    const li = b && b.nodeName === "LI" ? b : null;
    if (li && li.parentNode.classList.contains("lj-check")) { document.execCommand("insertUnorderedList"); changed(); return; }  // toggle off
    if (!li) document.execCommand("insertUnorderedList");
    const nb = blockOf(getSelection().anchorNode); if (nb && nb.parentNode && nb.parentNode.nodeName === "UL") nb.parentNode.classList.add("lj-check");
    changed();
  }
  function callout(kind) {
    const [e, n] = CALLOUTS[kind];
    const sel = getSelection().toString(), id = "lj-c" + Date.now();
    insertHTML(`<div class="lj-callout" data-kind="${kind}"><span class="lj-ce" contenteditable="false">${e} ${n}</span><p id="${id}">${esc(sel) || "Write it here…"}</p></div><p><br></p>`);
    const pEl = document.getElementById(id); if (pEl) { pEl.removeAttribute("id"); const r = document.createRange(); r.selectNodeContents(pEl); const s = getSelection(); s.removeAllRanges(); s.addRange(r); keepSel(); }
  }
  function rtl() { restoreSel(); const b = blockOf(getSelection().anchorNode); if (!b) return; const d = b.getAttribute("dir") === "rtl" ? "ltr" : "rtl"; b.setAttribute("dir", d); b.style.textAlign = d === "rtl" ? "right" : ""; changed(); }
  function table(r, c) {
    const row = (th) => `<tr>${Array.from({ length: c }, () => (th ? "<th><br></th>" : "<td><br></td>")).join("")}</tr>`;
    const id = "lj-t" + Date.now();
    insertHTML(`<table class="lj-table" id="${id}"><thead>${row(true)}</thead><tbody>${Array.from({ length: Math.max(1, r - 1) }, () => row(false)).join("")}</tbody></table><p><br></p>`);
    const t = document.getElementById(id); if (t) { t.removeAttribute("id"); const c0 = t.querySelector("th,td"), rg = document.createRange(); rg.setStart(c0, 0); rg.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(rg); keepSel(); }
  }
  function tableOp(op) {
    restoreSel();
    const cell = getSelection().anchorNode && (getSelection().anchorNode.nodeType === 1 ? getSelection().anchorNode : getSelection().anchorNode.parentNode).closest("td,th"); if (!cell) return;
    const tr = cell.parentNode, tbl = cell.closest("table"), idx = [...tr.children].indexOf(cell);
    if (op === "row") { const n = tr.cloneNode(true); n.querySelectorAll("th,td").forEach((x) => { const td = document.createElement("td"); td.innerHTML = "<br>"; x.replaceWith(td); }); (tr.parentNode.nodeName === "THEAD" ? tbl.tBodies[0] : tr.parentNode).insertBefore(n, tr.parentNode.nodeName === "THEAD" ? tbl.tBodies[0].firstChild : tr.nextSibling); }
    if (op === "col") $$("tr", tbl).forEach((r) => { const ref = r.children[idx]; const n = document.createElement(ref.nodeName); n.innerHTML = "<br>"; ref.after(n); });
    if (op === "delrow" && $$("tr", tbl).length > 1) tr.remove();
    if (op === "delcol" && tr.children.length > 1) $$("tr", tbl).forEach((r) => r.children[idx] && r.children[idx].remove());
    changed();
  }
  function link() {
    keepSel();
    const sel = getSelection().toString();
    modal(`<div class="lj-mh"><b>🔗 Insert link</b><button data-x>✕</button></div><div class="lj-mb">
      <label>Text</label><input id="lj-lt" value="${esc(sel)}" placeholder="What to show"><label>Link</label><input id="lj-lu" placeholder="https://…">
      <div class="lj-mf"><button class="lj-btn" data-x>Cancel</button><button class="lj-btn lj-pri" id="lj-lok">Insert</button></div></div>`, "sm");
    $("#lj-lu").focus();
    $("#lj-lok").onclick = () => {
      let u = $("#lj-lu").value.trim(); if (!u) return; if (!/^(https?:|mailto:|\/)/i.test(u)) u = "https://" + u;
      const t = $("#lj-lt").value.trim() || u; closeModal();
      insertHTML(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(t)}</a>&nbsp;`);
    };
  }
  async function capture() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) return toast("Screen capture is not available in this browser; use Win+Shift+S and paste.");
    keepSel();
    let stream;
    try { stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }); } catch (e) { return; }
    try {
      const v = document.createElement("video"); v.srcObject = stream; v.muted = true; await v.play(); await new Promise((r) => setTimeout(r, 350));
      const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext("2d").drawImage(v, 0, 0);
      stream.getTracks().forEach((t) => t.stop());
      cropper(c.toDataURL("image/png"));
    } catch (e) { stream.getTracks().forEach((t) => t.stop()); toast("Capture failed: " + e.message); }
  }
  function cropper(dataUrl) {
    modal(`<div class="lj-mh"><b>📸 Crop the screenshot</b><span class="lj-dim">drag a box, or insert the whole picture</span><button data-x>✕</button></div>
      <div class="lj-crop" id="lj-crop"><img src="${dataUrl}" id="lj-cimg"><div class="lj-cbox" id="lj-cbox" hidden></div></div>
      <div class="lj-mf"><button class="lj-btn" data-x>Cancel</button><button class="lj-btn" id="lj-cwhole">Insert whole</button><button class="lj-btn lj-pri" id="lj-cok">Insert crop</button></div>`, "xl");
    const box = $("#lj-cbox"), wrap = $("#lj-crop"), im = $("#lj-cimg"); let a = null, rect = null;
    wrap.onmousedown = (e) => { const r = im.getBoundingClientRect(); a = [e.clientX - r.left, e.clientY - r.top]; box.hidden = false; e.preventDefault(); };
    window.onmousemove = (e) => { if (!a) return; const r = im.getBoundingClientRect(), b = [Math.max(0, Math.min(r.width, e.clientX - r.left)), Math.max(0, Math.min(r.height, e.clientY - r.top))];
      rect = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])]; Object.assign(box.style, { left: rect[0] + im.offsetLeft + "px", top: rect[1] + im.offsetTop + "px", width: rect[2] + "px", height: rect[3] + "px" }); };
    window.onmouseup = () => { a = null; };
    $("#lj-cwhole").onclick = () => { closeModal(); insertImageData(dataUrl, { cap: "Screenshot · " + new Date().toLocaleString("en-GB") }); };
    $("#lj-cok").onclick = () => {
      if (!rect || rect[2] < 8) { closeModal(); insertImageData(dataUrl); return; }
      const s = im.naturalWidth / im.clientWidth, c = document.createElement("canvas"); c.width = Math.round(rect[2] * s); c.height = Math.round(rect[3] * s);
      c.getContext("2d").drawImage(im, rect[0] * s, rect[1] * s, c.width, c.height, 0, 0, c.width, c.height); closeModal();
      insertImageData(c.toDataURL("image/png"), { cap: "Screenshot · " + new Date().toLocaleString("en-GB") });
    };
  }

  // ---------------------------------------------------------------- modal
  function modal(html, size = "md") { const m = $("#lj-modal"); $("#lj-mbox").className = "lj-mbox lj-m-" + size; $("#lj-mbox").innerHTML = html; m.hidden = false; $$("[data-x]", m).forEach((b) => (b.onclick = closeModal)); return m; }
  function closeModal() { const m = $("#lj-modal"); if (window.Plotly) $$(".js-plotly-plot", m).forEach((p) => window.Plotly.purge(p)); m.hidden = true; $("#lj-mbox").innerHTML = ""; window.onmousemove = window.onmouseup = null; }
  const plotly = () => window.Plotly ? Promise.resolve(window.Plotly) : (window.__plotlyP = window.__plotlyP || new Promise((res, rej) => { const sc = document.createElement("script"); sc.src = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"; sc.onload = () => res(window.Plotly); sc.onerror = () => { window.__plotlyP = null; rej(new Error("could not load the chart library")); }; document.head.appendChild(sc); }));

  // ---------------------------------------------------------------- chart from your own numbers
  const CHART_THEMES = { jordan: { bg: "#0b0b0d", fg: "#e6e6ea", grid: "#222228", pal: ["#ffa028", "#2be07e", "#4aa8ff", "#ff4a4a", "#c58bff", "#f2f2f5"] }, paper: { bg: "#ffffff", fg: "#202124", grid: "#e8eaed", pal: ["#1a73e8", "#188038", "#d93025", "#f9ab00", "#9334e6", "#5f6368"] } };
  function chartModal(fig) {
    keepSel();
    const sp = fig && fig.dataset.spec ? JSON.parse(fig.dataset.spec) : { type: "line", title: "My chart", theme: "jordan", data: "Week,Win rate %,Avg R\nW1,38,0.2\nW2,44,0.4\nW3,41,0.3\nW4,52,0.7", smooth: true };
    modal(`<div class="lj-mh"><b>📊 Chart from your numbers</b><span class="lj-dim">first row = names, first column = labels</span><button data-x>✕</button></div>
      <div class="lj-chartgrid"><div class="lj-cform">
        <label>Type</label><div class="lj-seg" id="lj-ctype">${[["line", "Line"], ["area", "Area"], ["bar", "Bars"], ["hbar", "H-bars"], ["pie", "Pie"], ["scatter", "Dots"]].map(([k, n]) => `<button data-v="${k}" class="${sp.type === k ? "on" : ""}">${n}</button>`).join("")}</div>
        <label>Title</label><input id="lj-ctitle" value="${esc(sp.title)}">
        <label>Data</label><textarea id="lj-cdata" rows="10" spellcheck="false">${esc(sp.data)}</textarea>
        <label>Style</label><div class="lj-seg" id="lj-ctheme"><button data-v="jordan" class="${sp.theme === "jordan" ? "on" : ""}">Terminal dark</button><button data-v="paper" class="${sp.theme === "paper" ? "on" : ""}">Paper light</button></div>
        <label class="lj-chk"><input type="checkbox" id="lj-csmooth" ${sp.smooth ? "checked" : ""}> smooth lines</label>
      </div><div class="lj-cprev" id="lj-cprev"></div></div>
      <div class="lj-mf"><button class="lj-btn" data-x>Cancel</button><button class="lj-btn lj-pri" id="lj-cins">${fig ? "Update chart" : "Insert chart"}</button></div>`, "xl");
    const read = () => ({ type: $("#lj-ctype .on").dataset.v, title: $("#lj-ctitle").value, data: $("#lj-cdata").value, theme: $("#lj-ctheme .on").dataset.v, smooth: $("#lj-csmooth").checked });
    const seg = (id) => $$(`#${id} button`).forEach((b) => (b.onclick = () => { $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
    seg("lj-ctype"); seg("lj-ctheme");
    let tm = 0; ["lj-ctitle", "lj-cdata", "lj-csmooth"].forEach((id) => ($(`#${id}`).oninput = () => { clearTimeout(tm); tm = setTimeout(draw, 250); }));
    const build_ = (s) => {
      const rows = s.data.trim().split(/\r?\n/).map((l) => l.split(/[,\t;]/).map((x) => x.trim())).filter((r) => r.length && r[0] !== "");
      const head = rows[0] || [], body = rows.slice(1), th = CHART_THEMES[s.theme];
      const series = head.slice(1).map((name, j) => ({ name, y: body.map((r) => parseFloat(r[j + 1])) }));
      const x = body.map((r) => r[0]);
      let traces;
      if (s.type === "pie") traces = [{ type: "pie", labels: x, values: series[0] ? series[0].y : [], hole: 0.45, marker: { colors: th.pal, line: { color: th.bg, width: 2 } }, textfont: { color: "#fff" } }];
      else traces = series.map((se, j) => {
        const col = th.pal[j % th.pal.length];
        if (s.type === "bar") return { type: "bar", x, y: se.y, name: se.name, marker: { color: col } };
        if (s.type === "hbar") return { type: "bar", orientation: "h", y: x, x: se.y, name: se.name, marker: { color: col } };
        return { type: "scatter", mode: s.type === "scatter" ? "markers" : "lines+markers", x, y: se.y, name: se.name, line: { color: col, width: 3, shape: s.smooth ? "spline" : "linear" }, marker: { size: s.type === "scatter" ? 10 : 6, color: col }, fill: s.type === "area" ? (j ? "tonexty" : "tozeroy") : "none" };
      });
      const layout = { title: { text: s.title, font: { size: 20, color: th.fg, family: "Inter, sans-serif" }, x: 0.03 }, paper_bgcolor: th.bg, plot_bgcolor: th.bg, font: { color: th.fg, family: "Inter, sans-serif" },
        margin: { l: 56, r: 24, t: 64, b: 48 }, legend: { orientation: "h", y: -0.15 }, xaxis: { gridcolor: th.grid, zeroline: false }, yaxis: { gridcolor: th.grid, zeroline: false }, barmode: "group" };
      return { traces, layout };
    };
    const draw = async () => { const Pl = await plotly(); const { traces, layout } = build_(read()); Pl.react($("#lj-cprev"), traces, layout, { displayModeBar: false, responsive: true }); };
    draw().catch((e) => ($("#lj-cprev").innerHTML = `<div class="lj-dim">${esc(e.message)}</div>`));
    $("#lj-cins").onclick = async () => {
      const s = read(), Pl = await plotly(), { traces, layout } = build_(s);
      const tmp = document.createElement("div"); tmp.style.cssText = "position:fixed;left:-9999px;width:1100px;height:560px"; document.body.appendChild(tmp);
      await Pl.newPlot(tmp, traces, layout, { staticPlot: true }); const png = await Pl.toImage(tmp, { format: "png", width: 1100, height: 560, scale: 2 }); Pl.purge(tmp); tmp.remove();
      closeModal();
      const url = await upload(png);
      if (fig) { fig.querySelector("img").src = url; fig.dataset.spec = JSON.stringify(s); changed(); }
      else insertHTML(figHTML(url, { kind: "chart", spec: JSON.stringify(s), cap: s.title }));
    };
  }

  // ---------------------------------------------------------------- market chart (terminal data)
  function marketModal(fig) {
    keepSel();
    const sp = fig && fig.dataset.spec ? JSON.parse(fig.dataset.spec) : { symbol: "NQ=F", range: "5D", interval: "15m", style: "candles" };
    modal(`<div class="lj-mh"><b>📈 Market chart</b><span class="lj-dim">live data from the terminal · any stock, future, index or crypto</span><button data-x>✕</button></div>
      <div class="lj-chartgrid"><div class="lj-cform">
        <label>Symbol</label><input id="lj-ms" value="${esc(sp.symbol)}" placeholder="NQ=F, ES=F, AAPL, BTC-USD…">
        <label>Range</label><div class="lj-seg" id="lj-mr">${["1D", "5D", "1M", "3M", "6M", "1Y", "5Y"].map((r) => `<button data-v="${r}" class="${sp.range === r ? "on" : ""}">${r}</button>`).join("")}</div>
        <label>Candle</label><div class="lj-seg" id="lj-mi">${["1m", "5m", "15m", "60m", "1d", "1wk"].map((r) => `<button data-v="${r}" class="${sp.interval === r ? "on" : ""}">${r}</button>`).join("")}</div>
        <label>Style</label><div class="lj-seg" id="lj-mst"><button data-v="candles" class="${sp.style === "candles" ? "on" : ""}">Candles</button><button data-v="line" class="${sp.style === "line" ? "on" : ""}">Line</button></div>
        <div class="lj-dim" id="lj-mnote" style="margin-top:8px">After inserting, you can draw on it: select the picture → ✎ EDIT.</div>
      </div><div class="lj-cprev" id="lj-mprev"></div></div>
      <div class="lj-mf"><button class="lj-btn" data-x>Cancel</button><button class="lj-btn lj-pri" id="lj-mins">${fig ? "Update" : "Insert chart"}</button></div>`, "xl");
    const read = () => ({ symbol: $("#lj-ms").value.trim().toUpperCase() || "NQ=F", range: $("#lj-mr .on").dataset.v, interval: $("#lj-mi .on").dataset.v, style: $("#lj-mst .on").dataset.v });
    let data = null;
    const fig_ = (s) => {
      const c = data.candles, x = c.map((r) => new Date(r[0] * 1000)), intr = !/d|wk/.test(s.interval);
      const tr = s.style === "line" ? [{ type: "scatter", mode: "lines", x, y: c.map((r) => r[4]), line: { color: "#ffa028", width: 2.2 }, fill: "tozeroy", fillcolor: "rgba(255,160,40,0.08)", name: s.symbol }]
        : [{ type: "candlestick", x, open: c.map((r) => r[1]), high: c.map((r) => r[2]), low: c.map((r) => r[3]), close: c.map((r) => r[4]), increasing: { line: { color: "#2be07e" }, fillcolor: "#2be07e" }, decreasing: { line: { color: "#ff4a4a" }, fillcolor: "#ff4a4a" }, name: s.symbol }];
      const last = c.length ? c[c.length - 1][4] : null, first = c.length ? c[0][4] : null;
      return { tr, layout: { title: { text: `${s.symbol}  ${last !== null ? last.toLocaleString(undefined, { maximumFractionDigits: 2 }) : ""}  <span style="color:${last >= first ? "#2be07e" : "#ff4a4a"}">${first ? ((last / first - 1) * 100).toFixed(2) + "%" : ""}</span>  · ${s.range} · ${s.interval}`, font: { size: 18, color: "#e6e6ea", family: "JetBrains Mono, monospace" }, x: 0.02 },
        paper_bgcolor: "#0b0b0d", plot_bgcolor: "#0b0b0d", font: { color: "#b9b9c4", family: "JetBrains Mono, monospace", size: 11 }, margin: { l: 20, r: 64, t: 56, b: 36 }, showlegend: false,
        xaxis: { gridcolor: "#1c1c22", rangeslider: { visible: false }, rangebreaks: intr ? [{ bounds: ["sat", "mon"] }] : [] }, yaxis: { gridcolor: "#1c1c22", side: "right" } } };
    };
    const load = async () => {
      const s = read(); $("#lj-mprev").innerHTML = `<div class="lj-dim" style="padding:40px">Loading ${esc(s.symbol)}…</div>`;
      try { data = await api(`/invest/chart?symbol=${encodeURIComponent(s.symbol)}&range=${s.range}&interval=${s.interval}`); } catch (e) { $("#lj-mprev").innerHTML = `<div class="lj-dim" style="padding:40px">${esc(e.message)}</div>`; data = null; return; }
      const Pl = await plotly(); $("#lj-mprev").innerHTML = ""; const { tr, layout } = fig_(s); Pl.react($("#lj-mprev"), tr, layout, { displayModeBar: false, responsive: true });
    };
    ["lj-mr", "lj-mi", "lj-mst"].forEach((id) => $$(`#${id} button`).forEach((b) => (b.onclick = () => { $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b)); load(); })));
    $("#lj-ms").onkeydown = (e) => { if (e.key === "Enter") load(); };
    $("#lj-ms").onchange = load;
    load();
    $("#lj-mins").onclick = async () => {
      if (!data) return; const s = read(), Pl = await plotly(), { tr, layout } = fig_(s);
      const tmp = document.createElement("div"); tmp.style.cssText = "position:fixed;left:-9999px;width:1200px;height:620px"; document.body.appendChild(tmp);
      await Pl.newPlot(tmp, tr, layout, { staticPlot: true }); const png = await Pl.toImage(tmp, { format: "png", width: 1200, height: 620, scale: 2 }); Pl.purge(tmp); tmp.remove(); closeModal();
      const url = await upload(png), cap = `${s.symbol} · ${s.range} · ${s.interval} candles · ${new Date().toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}`;
      if (fig) { fig.querySelector("img").src = url; fig.dataset.spec = JSON.stringify(s); changed(); }
      else insertHTML(figHTML(url, { kind: "market", spec: JSON.stringify(s), cap }));
    };
  }

  // ---------------------------------------------------------------- drawing board
  function drawModal(fig) {
    keepSel();
    const prior = fig && fig.dataset.kind === "draw" && fig.dataset.spec ? JSON.parse(fig.dataset.spec) : null;
    const baseImg = fig && fig.dataset.kind !== "draw" ? fig.querySelector("img").src : (prior && prior.base) || null;   // draw on top of a screenshot / chart
    const W = 1600, H = 900;
    modal(`<div class="lj-mh"><b>✎ Drawing board</b><span class="lj-dim">${baseImg ? "drawing on top of your picture" : "sketch setups, levels, ideas"} · Ctrl+Z undo</span><button data-x>✕</button></div>
      <div class="lj-dtools" id="lj-dtools">
        <div class="lj-seg" id="lj-dtool">${[["pen", "✎ Pen"], ["marker", "▮ Marker"], ["line", "╱ Line"], ["arrow", "➚ Arrow"], ["rect", "▭ Box"], ["ellipse", "◯ Circle"], ["text", "T Text"], ["eraser", "⌫ Eraser"]].map(([k, n], i) => `<button data-v="${k}" class="${i ? "" : "on"}">${n}</button>`).join("")}</div>
        <span class="lj-dcols">${["#111111", "#ffffff", "#ff4a4a", "#ffa028", "#f9e04b", "#2be07e", "#4aa8ff", "#c58bff"].map((c, i) => `<button data-c="${c}" class="${i === (baseImg ? 2 : 0) ? "on" : ""}" style="background:${c}"></button>`).join("")}<input type="color" id="lj-dcc" value="#ff4a4a"></span>
        <div class="lj-seg" id="lj-dw">${[2, 4, 8, 14].map((w, i) => `<button data-v="${w}" class="${i === 1 ? "on" : ""}"><i style="height:${Math.min(10, w)}px"></i></button>`).join("")}</div>
        <div class="lj-seg" id="lj-dbg">${[["white", "White"], ["grid", "Grid"], ["dark", "Dark"], ["chart", "Chart paper"]].map(([k, n]) => `<button data-v="${k}" class="${(prior ? prior.bg : "white") === k ? "on" : ""}" ${baseImg ? "disabled" : ""}>${n}</button>`).join("")}</div>
        <button class="lj-btn" id="lj-dundo">↶ Undo</button><button class="lj-btn" id="lj-dclear">Clear</button>
      </div>
      <div class="lj-dwrap"><canvas id="lj-dcv" width="${W}" height="${H}"></canvas></div>
      <div class="lj-mf"><button class="lj-btn" data-x>Cancel</button><button class="lj-btn lj-pri" id="lj-dins">${fig ? "Update picture" : "Insert drawing"}</button></div>`, "xl");
    const cv = $("#lj-dcv"), ctx = cv.getContext("2d");
    let tool = "pen", color = baseImg ? "#ff4a4a" : "#111111", width = 4, bg = prior ? prior.bg : "white", strokes = prior ? prior.strokes : [], cur = null, base = null;
    if (baseImg) { base = new Image(); base.crossOrigin = "anonymous"; base.onload = () => { const s = Math.min(W / base.width, H / base.height); base._r = [(W - base.width * s) / 2, (H - base.height * s) / 2, base.width * s, base.height * s]; render(); }; base.src = baseImg; }
    const paintBg = () => {
      ctx.fillStyle = bg === "dark" || bg === "chart" ? "#0b0b0d" : "#ffffff"; ctx.fillRect(0, 0, W, H);
      if (base && base.complete && base._r) { ctx.drawImage(base, ...base._r); return; }
      if (bg === "grid" || bg === "chart") { ctx.strokeStyle = bg === "chart" ? "#1d1d24" : "#e8eaed"; ctx.lineWidth = 1; for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, H); ctx.stroke(); } for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(W, y + 0.5); ctx.stroke(); } }
    };
    const stroke = (s, ctx) => {
      ctx.save(); ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = s.c; ctx.fillStyle = s.c; ctx.lineWidth = s.w;
      if (s.t === "marker") { ctx.globalAlpha = 0.35; ctx.lineWidth = s.w * 4; ctx.lineCap = "square"; }
      if (s.t === "eraser") { ctx.globalCompositeOperation = "destination-out"; ctx.lineWidth = s.w * 4; }
      const p = s.p;
      if (s.t === "pen" || s.t === "marker" || s.t === "eraser") { ctx.beginPath(); p.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); if (p.length === 1) ctx.lineTo(p[0][0] + 0.1, p[0][1]); ctx.stroke(); }
      else if (p.length >= 2) {
        const [a, b] = [p[0], p[p.length - 1]];
        if (s.t === "line" || s.t === "arrow") { ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); if (s.t === "arrow") { const an = Math.atan2(b[1] - a[1], b[0] - a[0]), L = 14 + s.w * 3; ctx.beginPath(); ctx.moveTo(b[0] - L * Math.cos(an - 0.45), b[1] - L * Math.sin(an - 0.45)); ctx.lineTo(...b); ctx.lineTo(b[0] - L * Math.cos(an + 0.45), b[1] - L * Math.sin(an + 0.45)); ctx.stroke(); } }
        if (s.t === "rect") ctx.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
        if (s.t === "ellipse") { ctx.beginPath(); ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2); ctx.stroke(); }
      }
      if (s.t === "text" && s.txt) { ctx.font = `700 ${12 + s.w * 4}px Caveat, 'Patrick Hand', sans-serif`; ctx.textBaseline = "top"; s.txt.split("\n").forEach((l, i) => ctx.fillText(l, p[0][0], p[0][1] + i * (14 + s.w * 4))); }
      ctx.restore();
    };
    // strokes go on their own layer so the eraser never removes the background or the picture underneath
    const layer = document.createElement("canvas"); layer.width = W; layer.height = H; const lctx = layer.getContext("2d");
    const render = () => { paintBg(); lctx.clearRect(0, 0, W, H); [...strokes, ...(cur ? [cur] : [])].forEach((s) => stroke(s, lctx)); ctx.drawImage(layer, 0, 0); };
    const P = (e) => { const r = cv.getBoundingClientRect(); return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H]; };
    cv.onpointerdown = (e) => {
      const p = P(e);
      if (tool === "text") { const t = prompt("Text"); if (t) { strokes.push({ t: "text", c: color, w: width, p: [p], txt: t }); render(); } return; }
      cur = { t: tool, c: color, w: width, p: [p] }; cv.setPointerCapture(e.pointerId);
    };
    cv.onpointermove = (e) => { if (!cur) return; const p = P(e); if (["pen", "marker", "eraser"].includes(cur.t)) cur.p.push(p); else cur.p[1] = p; render(); };
    cv.onpointerup = () => { if (cur) { strokes.push(cur); cur = null; render(); } };
    const seg = (id, fn) => $$(`#${id} button`).forEach((b) => (b.onclick = () => { $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b)); fn(b.dataset.v); }));
    seg("lj-dtool", (v) => (tool = v)); seg("lj-dw", (v) => (width = +v)); seg("lj-dbg", (v) => { bg = v; render(); });
    $$(".lj-dcols button").forEach((b) => (b.onclick = () => { $$(".lj-dcols button").forEach((x) => x.classList.toggle("on", x === b)); color = b.dataset.c; }));
    $("#lj-dcc").oninput = (e) => { color = e.target.value; $$(".lj-dcols button").forEach((x) => x.classList.remove("on")); };
    $("#lj-dundo").onclick = () => { strokes.pop(); render(); };
    $("#lj-dclear").onclick = () => { strokes = []; render(); };
    const undoKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !$("#lj-modal").hidden && $("#lj-dcv")) { e.preventDefault(); strokes.pop(); render(); } };
    document.addEventListener("keydown", undoKey);
    render();
    $("#lj-dins").onclick = async () => {
      document.removeEventListener("keydown", undoKey);
      render(); let png;
      try { png = cv.toDataURL("image/png"); } catch (e) { toast("Could not export this picture"); return; }
      closeModal();
      const url = await upload(png), spec = JSON.stringify({ strokes, bg, base: baseImg || null });
      if (fig) { fig.querySelector("img").src = url; fig.dataset.kind = "draw"; fig.dataset.spec = spec; changed(); }
      else insertHTML(figHTML(url, { kind: "draw", spec, cap: "" }));
    };
  }

  // ---------------------------------------------------------------- image toolbar
  let selFig = null;
  function showImgBar(fig) {
    selFig = fig; $$("figure.lj-sel", doc()).forEach((f) => f.classList.remove("lj-sel")); const bar = $("#lj-imgbar");
    if (!fig) { bar.hidden = true; return; }
    fig.classList.add("lj-sel");
    const r = fig.getBoundingClientRect(), host = $(".lj").getBoundingClientRect();
    bar.hidden = false; bar.style.left = Math.max(8, r.left - host.left + r.width / 2 - bar.offsetWidth / 2) + "px"; bar.style.top = Math.max(60, r.top - host.top - 44) + "px";
    bar.querySelector('[data-ia="edit"]').textContent = fig.dataset.kind === "chart" ? "✎ EDIT CHART" : fig.dataset.kind === "market" ? "✎ CHANGE CHART" : "✎ DRAW ON IT";
  }

  // ---------------------------------------------------------------- markdown-style shortcuts
  function shortcuts(e) {
    if (e.key !== " ") return;
    const s = getSelection(); if (!s.rangeCount) return;
    const node = s.anchorNode; if (!node || node.nodeType !== 3) return;
    const b = blockOf(node); if (!b || b.nodeName === "LI" || b.nodeName === "PRE") return;
    const before = node.textContent.slice(0, s.anchorOffset);
    const map = { "#": "h1", "##": "h2", "###": "h3", ">": "blockquote", "-": "ul", "*": "ul", "1.": "ol", "[]": "check", "```": "pre" };
    const k = map[before.trim()]; if (!k || b.innerText.trim() !== before.trim()) return;
    e.preventDefault();
    node.textContent = node.textContent.slice(s.anchorOffset);
    if (!b.textContent) b.innerHTML = "<br>";   // keep an empty line the caret can live in
    const r = document.createRange(); r.setStart(b.firstChild || b, 0); r.collapse(true); s.removeAllRanges(); s.addRange(r); savedRange = r.cloneRange();
    if (k === "ul") exec("insertUnorderedList"); else if (k === "ol") exec("insertOrderedList"); else if (k === "check") checklist(); else setBlock(k);
    if (document.activeElement !== doc()) doc().focus();
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    const d = doc();
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) { /* older browsers */ }
    d.addEventListener("input", () => { if (!d.firstElementChild || d.innerHTML === "<br>") { d.innerHTML = "<p><br></p>"; const r = document.createRange(); r.setStart(d.firstChild, 0); const s = getSelection(); s.removeAllRanges(); s.addRange(r); } const last = d.lastElementChild; if (last && /^(FIGURE|TABLE|DIV|HR|PRE|BLOCKQUOTE)$/.test(last.nodeName)) d.insertAdjacentHTML("beforeend", "<p><br></p>"); });
    // table size picker
    $("#lj-tgrid").innerHTML = Array.from({ length: 64 }, (_, i) => `<i data-r="${Math.floor(i / 8) + 1}" data-c="${(i % 8) + 1}"></i>`).join("");
    $("#lj-tgrid").onmouseover = (e) => { const t = e.target.closest("i"); if (!t) return; const r = +t.dataset.r, c = +t.dataset.c; $$("#lj-tgrid i").forEach((x) => x.classList.toggle("on", +x.dataset.r <= r && +x.dataset.c <= c)); $("#lj-tsize").textContent = `${r} × ${c}`; };
    $("#lj-tgrid").onclick = (e) => { const t = e.target.closest("i"); if (!t) return; closePops(); table(+t.dataset.r, +t.dataset.c); };
    const bar = $("#lj-bar");
    bar.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });   // keep the text selection
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.dd) { const p = bar.querySelector(`[data-pop="${b.dataset.dd}"]`), was = p.classList.contains("open"); closePops(); if (!was) p.classList.add("open"); return; }
      if (b.dataset.cmd) return exec(b.dataset.cmd);
      if (b.dataset.color !== undefined) { closePops(); if (b.dataset.color) { exec("foreColor", b.dataset.color); $("#lj-cur-color").style.background = b.dataset.color; } else exec("removeFormat"); return; }
      if (b.dataset.mark) { closePops(); return exec("hiliteColor", b.dataset.mark); }
      if (b.dataset.mstyle) { closePops(); return wrapSel((sp) => (sp.className = b.dataset.mstyle === "under" ? "lj-munder" : "lj-mbox")); }
      if (b.dataset.callout) { closePops(); return callout(b.dataset.callout); }
      const a = b.dataset.act;
      if (a === "rtl") rtl(); else if (a === "check") checklist(); else if (a === "link") link(); else if (a === "image") { keepSel(); $("#lj-file").click(); } else if (a === "capture") capture();
      else if (a === "hr") insertHTML("<hr><p><br></p>"); else if (a === "code") setBlock("pre");
      else if (a === "date") insertHTML(`<span class="lj-datechip">📅 ${new Date().toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</span>&nbsp;`);
      else if (a === "chart") chartModal(); else if (a === "mchart") marketModal(); else if (a === "draw") drawModal();
      else if (a === "clearfmt") exec("removeFormat");
      else if (a === "paper") { J.pref.paper = J.pref.paper === "dark" ? "light" : "dark"; lset("nx_lj_pref", J.pref); $(".lj").classList.toggle("lj-darkpaper", J.pref.paper === "dark"); }
      else if (a === "export") exportHtml(); else if (a === "print") printDoc();
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".lj-dd")) closePops(); });
    $("#lj-block").onchange = (e) => { setBlock(e.target.value); };
    $("#lj-font").onchange = (e) => exec("fontName", e.target.value);
    $("#lj-size").onchange = (e) => { const px = e.target.value; wrapSel((sp) => (sp.style.fontSize = px + "px")); };
    ["lj-block", "lj-font", "lj-size"].forEach((id) => $(`#${id}`).addEventListener("mousedown", keepSel));
    // editor events
    d.addEventListener("input", changed);
    d.addEventListener("keyup", () => { keepSel(); syncBar(); });
    d.addEventListener("mouseup", () => { keepSel(); syncBar(); });
    d.addEventListener("keydown", (e) => {
      shortcuts(e);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); link(); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); J.dirty = true; save(); }
      if (e.key === "Tab") {
        e.preventDefault();
        const n = getSelection().anchorNode, cell = n && (n.nodeType === 1 ? n : n.parentNode).closest("td,th");
        if (cell) {
          const cells = $$("th,td", cell.closest("table")); let i = cells.indexOf(cell) + (e.shiftKey ? -1 : 1);
          if (i >= cells.length) { keepSel(); tableOp("row"); i = cells.length; }
          const all = $$("th,td", cell.closest("table")), tgt = all[Math.max(0, Math.min(all.length - 1, i))], r = document.createRange(); r.selectNodeContents(tgt); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); keepSel();
        } else exec(e.shiftKey ? "outdent" : "indent");
      }
    });
    d.addEventListener("click", (e) => {
      const li = e.target.closest(".lj-check > li"); if (li && e.offsetX < 4 && e.target === li) { li.classList.toggle("done"); changed(); }
      const fig = e.target.closest("figure.lj-fig"); if (fig && e.target.nodeName === "IMG") { showImgBar(fig); return; }
      if (!e.target.closest("#lj-imgbar")) showImgBar(null);
      const a = e.target.closest("a"); if (a && (e.ctrlKey || e.metaKey)) window.open(a.href, "_blank", "noopener");
    });
    d.addEventListener("dblclick", (e) => { const fig = e.target.closest("figure.lj-fig"); if (!fig) return; editFig(fig); });
    d.addEventListener("paste", async (e) => {
      const items = [...(e.clipboardData || {}).items || []], img = items.find((i) => i.type.startsWith("image/"));
      if (img) { e.preventDefault(); keepSel(); insertImageData(await fileToData(img.getAsFile()), { cap: "Screenshot · " + new Date().toLocaleString("en-GB") }); return; }
      const html = e.clipboardData.getData("text/html");
      if (html) { e.preventDefault(); keepSel(); insertHTML(sanitize(html.replace(/<!--[\s\S]*?-->/g, ""))); }
    });
    d.addEventListener("dragover", (e) => { if ([...e.dataTransfer.items].some((i) => i.kind === "file")) { e.preventDefault(); d.classList.add("lj-drop"); } });
    d.addEventListener("dragleave", () => d.classList.remove("lj-drop"));
    d.addEventListener("drop", async (e) => {
      const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")); d.classList.remove("lj-drop"); if (!files.length) return;
      e.preventDefault();
      const r = document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null; if (r) { const s = getSelection(); s.removeAllRanges(); s.addRange(r); keepSel(); }
      for (const f of files) await insertImageData(await fileToData(f), { cap: f.name.replace(/\.[^.]+$/, "") });
    });
    $("#lj-file").onchange = async (e) => { for (const f of e.target.files) await insertImageData(await fileToData(f), { cap: f.name.replace(/\.[^.]+$/, "") }); e.target.value = ""; };
    $("#lj-title").addEventListener("input", changed); $("#lj-tags").addEventListener("input", changed);
    $("#lj-title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); d.focus(); } });
    $("#lj-new").onclick = newDoc;
    let qt = 0; $("#lj-q").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { J.q = e.target.value; loadList(); }, 250); };
    $("#lj-tagbar").onclick = (e) => { const b = e.target.closest("[data-tag]"); if (!b) return; J.tag = b.dataset.tag; loadList(); };
    $("#lj-list").onclick = (e) => { const it = e.target.closest(".lj-item"); if (it) open(+it.dataset.id); };
    $("#lj-outline").onclick = (e) => { const b = e.target.closest("[data-out]"); if (!b) return; const h = $$("h1,h2,h3", d).filter((x) => x.innerText.trim())[+b.dataset.out]; if (h) h.scrollIntoView({ behavior: "smooth", block: "start" }); };
    $("#lj-pin").onclick = () => { if (!J.cur) return; J.cur.pinned = !J.cur.pinned; $("#lj-pin").classList.toggle("on", J.cur.pinned); J.dirty = true; save(); };
    $("#lj-del").onclick = async () => {
      if (!J.cur) return;
      modal(`<div class="lj-mh"><b>Delete “${esc(J.cur.title || "Untitled")}”?</b><button data-x>✕</button></div><div class="lj-mb"><p>This removes the entry for good.</p><div class="lj-mf"><button class="lj-btn" data-x>Keep it</button><button class="lj-btn lj-danger" id="lj-dok">Delete</button></div></div>`, "sm");
      $("#lj-dok").onclick = async () => { closeModal(); await req(`/docs/${J.cur.id}`, "DELETE"); J.cur = null; J.dirty = false; await loadList(); if (J.docs[0]) open(J.docs[0].id); else newDoc(); };
    };
    $("#lj-imgbar").addEventListener("mousedown", (e) => e.preventDefault());
    $("#lj-imgbar").onclick = (e) => {
      const b = e.target.closest("button"); if (!b || !selFig) return;
      if (b.dataset.w) selFig.style.width = b.dataset.w + "%";
      if (b.dataset.al) selFig.dataset.align = b.dataset.al;
      if (b.dataset.ia === "del") { selFig.remove(); showImgBar(null); changed(); return; }
      if (b.dataset.ia === "open") { window.open(selFig.querySelector("img").src, "_blank", "noopener"); return; }
      if (b.dataset.ia === "edit") { editFig(selFig); return; }
      changed(); showImgBar(selFig);
    };
    $("#lj-scroll").addEventListener("scroll", () => { if (selFig) showImgBar(selFig); });
    $("#lj-tblops").addEventListener("mousedown", (e) => e.preventDefault());
    $("#lj-tblops").onclick = (e) => { const b = e.target.closest("[data-tbl]"); if (b) tableOp(b.dataset.tbl); };
    $("#lj-modal").addEventListener("mousedown", (e) => { if (e.target.id === "lj-modal") closeModal(); });
    window.addEventListener("beforeunload", () => { if (J.dirty && J.cur) navigator.sendBeacon && fetch(`/api/journal/docs/${J.cur.id}`, { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: $("#lj-title").value || "Untitled", html: doc().innerHTML, tags: $("#lj-tags").value, pinned: !!J.cur.pinned }) }); });
  }
  function editFig(fig) { showImgBar(null); if (fig.dataset.kind === "chart") chartModal(fig); else if (fig.dataset.kind === "market") marketModal(fig); else drawModal(fig); }
  function closePops() { $$(".lj-pop.open").forEach((p) => p.classList.remove("open")); }
  function syncBar() {
    const s = getSelection(); if (!s.rangeCount || !doc().contains(s.anchorNode)) return;
    ["bold", "italic", "underline", "strikeThrough", "insertUnorderedList", "insertOrderedList", "justifyLeft", "justifyCenter", "justifyRight", "justifyFull"].forEach((c) => { const b = $(`#lj-bar [data-cmd="${c}"]`); if (b) b.classList.toggle("on", document.queryCommandState(c)); });
    const b = blockOf(s.anchorNode); if (b) { const v = b.classList.contains("lj-titleblk") ? "title" : { H1: "h1", H2: "h2", H3: "h3", BLOCKQUOTE: "blockquote", PRE: "pre" }[b.nodeName] || "p"; $("#lj-block").value = v; }
    const el = s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentNode, cs = getComputedStyle(el);
    const fam = cs.fontFamily.split(",")[0].replace(/["']/g, "").trim(); if (FONTS.some(([f]) => f === fam)) $("#lj-font").value = fam;
    const px = Math.round(parseFloat(cs.fontSize)); if (SIZES.includes(px)) $("#lj-size").value = px;
    $("#lj-tblops").hidden = !el.closest("td,th");
  }

  function exportHtml() {
    if (!J.cur) return;
    const css = [...document.styleSheets].flatMap((sh) => { try { return [...sh.cssRules].map((r) => r.cssText).filter((t) => t.includes(".lj-")); } catch (e) { return []; } }).join("\n");
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc($("#lj-title").value || "Journal")}</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Merriweather:wght@400;700&family=Heebo:wght@400;700&family=Caveat:wght@500;700&family=Permanent+Marker&family=Patrick+Hand&family=Kalam:wght@400;700&family=Amatic+SC:wght@700&family=Rubik:wght@400;700&family=JetBrains+Mono&display=swap" rel="stylesheet"><style>${css} body{background:#f1f3f4;margin:0} .lj-paper{margin:24px auto}</style></head><body><div class="lj"><div class="lj-paper"><h1 class="lj-title" style="border:0">${esc($("#lj-title").value)}</h1><div class="lj-doc">${doc().innerHTML.replace(/src="\/api/g, `src="${location.origin}/api`)}</div></div></div></body></html>`;
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([html], { type: "text/html" })); a.download = `${($("#lj-title").value || "journal").replace(/[^\w֐-׿ -]+/g, "").slice(0, 60)}.html`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function printDoc() { document.body.classList.add("lj-printing"); setTimeout(() => { window.print(); document.body.classList.remove("lj-printing"); }, 50); }

  async function activate() {
    if (!J.built) { J.built = true; build(); }
    await loadList();
    if (!J.cur) { const want = J.docs.find((d) => d.id === J.pref.last) || J.docs[0]; if (want) await open(want.id); else await newDoc(); }
  }
  const prev = window.onInvestTab;
  window.onInvestTab = function (tab) { if (prev) prev(tab); if (tab === "journal") activate(); else if (J.cur) flush(); };
})();
