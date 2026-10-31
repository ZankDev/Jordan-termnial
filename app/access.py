"""ACCESS — one shared password gating the whole terminal, for when it's reachable from outside this machine
(a Cloudflare tunnel, for the Telegram Mini App). Off by default: with no password set, every page and API call
passes straight through, exactly like before this existed — a password only has to be set once you're about to
expose the app. Once set, every request needs a signed session cookie obtained by logging in at /login.

Mirrors the PBKDF2 + in-memory-token pattern already used for the WEALTH PIN (app/wealth/security.py): a session
token list that resets on restart is an accepted tradeoff there too — logging in again after a restart is cheap,
and it avoids ever persisting a live credential to disk.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app import db

SESSION_DAYS = 30
COOKIE = "nx_session"
OPEN_PATHS = {"/login", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"}  # the install manifest and its icons are fetched without the session cookie
_tokens: dict[str, float] = {}


def _hash(password: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)).decode()


def enabled() -> bool:
    return bool(db.meta_get("access_password"))


def check(password: str) -> bool:
    raw = db.meta_get("access_password")
    if not raw:
        return True
    d = json.loads(raw)
    return hmac.compare_digest(_hash(password, base64.b64decode(d["salt"])), d["hash"])


def set_password(password: str, current: str | None = None) -> None:
    if enabled() and not check(current or ""):
        raise PermissionError("current password is wrong")
    if not password or len(password) < 6:
        raise ValueError("password must be at least 6 characters")
    salt = secrets.token_bytes(16)
    db.meta_set("access_password", json.dumps({"salt": base64.b64encode(salt).decode(), "hash": _hash(password, salt)}))
    _tokens.clear()  # changing the password logs every open session out


def clear_password(current: str) -> None:
    if not check(current):
        raise PermissionError("current password is wrong")
    db.meta_set("access_password", "")
    _tokens.clear()


def login(password: str) -> str | None:
    if not check(password) or not enabled():
        return None
    t = secrets.token_urlsafe(32)
    _tokens[t] = time.time() + SESSION_DAYS * 86400
    return t


def logout(token: str | None) -> None:
    _tokens.pop(token or "", None)


def token_ok(token: str | None) -> bool:
    if not enabled():
        return True
    return _tokens.get(token or "", 0) > time.time()


def is_open(path: str) -> bool:
    return path in OPEN_PATHS


_LOGO = ('<svg viewBox="0 0 32 32" width="46" height="46"><defs><linearGradient id="g" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#ff7a00"/><stop offset="1" stop-color="#ffd27a"/></linearGradient></defs>'
         '<rect width="32" height="32" rx="8" fill="#0a0600" stroke="#3a2406"/>'
         '<path d="M7 22 L13 12 L18 19 L25 8" stroke="url(#g)" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

_STYLE = """
  :root { color-scheme: dark; --amber: #ffa028; --amber2: #ffd27a; --line: rgba(255,160,40,.22); }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: #030303; overflow: hidden; }
  body { font-family: 'Segoe UI', -apple-system, Roboto, sans-serif; color: #e9e9e9; }
  #bg { position: fixed; inset: 0; width: 100%; height: 100%; }
  .vig { position: fixed; inset: 0; pointer-events: none; background: radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 0%, rgba(0,0,0,.55) 60%, rgba(0,0,0,.92) 100%); }
  .wrap { position: relative; z-index: 2; min-height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; }
  .top { position: fixed; top: 0; left: 0; right: 0; display: flex; justify-content: space-between; padding: 16px 26px; font: 600 11px 'JetBrains Mono', Consolas, monospace; letter-spacing: .14em; color: #8a6a3a; z-index: 3; }
  .top b { color: var(--amber); font-weight: 700; }
  .card { width: 100%; max-width: 410px; padding: 38px 36px 30px; background: linear-gradient(180deg, rgba(18,12,4,.82), rgba(8,6,3,.88)); border: 1px solid var(--line); border-radius: 14px;
          backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); box-shadow: 0 0 0 1px rgba(255,160,40,.05), 0 30px 90px rgba(0,0,0,.75), 0 0 80px rgba(255,140,0,.10); animation: rise .7s cubic-bezier(.2,.8,.2,1) both; position: relative; }
  .card::before { content: ""; position: absolute; left: 12%; right: 12%; top: -1px; height: 2px; background: linear-gradient(90deg, transparent, var(--amber), transparent); border-radius: 2px; }
  .card.shake { animation: shake .45s; }
  @keyframes rise { from { opacity: 0; transform: translateY(18px) scale(.985); } to { opacity: 1; transform: none; } }
  @keyframes shake { 20%, 60% { transform: translateX(-9px); } 40%, 80% { transform: translateX(9px); } }
  .brand { text-align: center; }
  .brand svg { filter: drop-shadow(0 0 14px rgba(255,150,20,.45)); }
  .brand h1 { margin: 14px 0 2px; font: 800 25px 'Segoe UI', sans-serif; letter-spacing: .34em; color: #fff; padding-left: .34em; }
  .brand h1 span { background: linear-gradient(90deg, var(--amber), var(--amber2)); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .sub { text-align: center; color: #8a7a62; font: 600 10.5px 'JetBrains Mono', Consolas, monospace; letter-spacing: .3em; margin: 0 0 30px; padding-left: .3em; }
  label { display: block; color: #a58a5e; font: 700 10px 'JetBrains Mono', Consolas, monospace; letter-spacing: .16em; margin: 16px 0 7px; }
  .field { position: relative; }
  input { width: 100%; padding: 13px 44px 13px 14px; background: rgba(0,0,0,.55); border: 1px solid #2d2418; color: #fff; font-size: 15px; border-radius: 8px; transition: border-color .2s, box-shadow .2s; letter-spacing: .04em; }
  input:focus { outline: none; border-color: var(--amber); box-shadow: 0 0 0 3px rgba(255,160,40,.16); }
  .eye { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); width: 34px; height: 34px; margin: 0; padding: 0; background: transparent; border: 0; color: #8a7a62; cursor: pointer; border-radius: 6px; font-size: 15px; }
  .eye:hover { color: var(--amber); background: rgba(255,160,40,.08); }
  .go { width: 100%; margin-top: 24px; padding: 14px; background: linear-gradient(90deg, #ff8a00, #ffb347); border: 0; color: #1a0f00; font: 800 13px 'Segoe UI', sans-serif; letter-spacing: .22em; border-radius: 8px; cursor: pointer; position: relative; overflow: hidden; transition: transform .12s, box-shadow .2s; box-shadow: 0 6px 26px rgba(255,140,0,.28); }
  .go:hover { transform: translateY(-1px); box-shadow: 0 10px 34px rgba(255,140,0,.42); }
  .go:active { transform: translateY(0); }
  .go.busy { pointer-events: none; opacity: .85; }
  .go.busy::after { content: ""; position: absolute; inset: 0; background: linear-gradient(100deg, transparent 30%, rgba(255,255,255,.45) 50%, transparent 70%); animation: sweep 1s infinite; }
  @keyframes sweep { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
  .err { margin-top: 16px; padding: 10px 12px; color: #ff6b6b; background: rgba(255,74,74,.08); border: 1px solid rgba(255,74,74,.3); border-radius: 8px; font: 600 12px 'JetBrains Mono', Consolas, monospace; text-align: center; }
  .caps { display: none; margin-top: 8px; color: #ffcf6b; font: 700 10.5px 'JetBrains Mono', Consolas, monospace; letter-spacing: .06em; }
  .hint { color: #6a604f; font-size: 11px; margin-top: 20px; line-height: 1.6; text-align: center; }
  .foot { position: fixed; bottom: 0; left: 0; right: 0; display: flex; justify-content: center; gap: 22px; padding: 14px; font: 600 10px 'JetBrains Mono', Consolas, monospace; letter-spacing: .2em; color: #5a4d38; z-index: 3; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #2be07e; margin-right: 7px; box-shadow: 0 0 9px #2be07e; animation: pulse 2s infinite; }
  @keyframes pulse { 50% { opacity: .35; } }
  @media (max-width: 520px) { .card { padding: 30px 22px 24px; } .top span:last-child { display: none; } }
"""

_SCRIPT = """
(function () {
  var c = document.getElementById('bg'), g = c.getContext('2d'), W = 0, H = 0, cols = [], dpr = Math.min(2, window.devicePixelRatio || 1);
  function fit() { W = innerWidth; H = innerHeight; c.width = W * dpr; c.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0); var n = Math.ceil(W / 26); cols = []; for (var i = 0; i < n; i++) cols.push(mk(i, true)); }
  function mk(i, init) { var p = 100 + Math.random() * 60, a = []; for (var k = 0; k < 36; k++) { var o = p, d = (Math.random() - .47) * 3.4; p += d; a.push([o, Math.max(o, p) + Math.random() * 2, Math.min(o, p) - Math.random() * 2, p]); }
    return { x: i * 26 + 13, y: init ? Math.random() * H : -Math.random() * H, v: .12 + Math.random() * .38, a: a, s: .55 + Math.random() * .9, al: .06 + Math.random() * .16 }; }
  function frame() {
    g.clearRect(0, 0, W, H);
    g.strokeStyle = 'rgba(255,160,40,.035)'; g.lineWidth = 1; for (var x = 0; x < W; x += 52) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); } for (var y = 0; y < H; y += 52) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
    cols.forEach(function (col, i) {
      col.y += col.v; if (col.y > H + 40) cols[i] = col = mk(i, false);
      var lo = 1e9, hi = -1e9; col.a.forEach(function (b) { if (b[2] < lo) lo = b[2]; if (b[1] > hi) hi = b[1]; });
      var sc = 190 / (hi - lo || 1);
      col.a.forEach(function (b, k) {
        var up = b[3] >= b[0], yy = col.y + k * 7 * col.s, f = function (v) { return yy + (hi - v) * sc * .035 * 8; };
        g.globalAlpha = col.al * (1 - Math.abs((yy - H / 2) / H)) ; g.strokeStyle = g.fillStyle = up ? '#2be07e' : '#ff4a4a';
        g.beginPath(); g.moveTo(col.x, f(b[1])); g.lineTo(col.x, f(b[2])); g.stroke(); g.fillRect(col.x - 3, Math.min(f(b[0]), f(b[3])), 6, Math.max(1.5, Math.abs(f(b[3]) - f(b[0]))));
      });
    });
    g.globalAlpha = 1; requestAnimationFrame(frame);
  }
  fit(); addEventListener('resize', fit); frame();
  var tk = document.getElementById('clk'); function tick() { var d = new Date(); tk.textContent = d.toLocaleTimeString([], { hour12: false }); } tick(); setInterval(tick, 1000);
  var pw = document.querySelector('input[name=password]'), eye = document.getElementById('eye'), caps = document.getElementById('caps'), form = document.getElementById('f'), btn = document.getElementById('go');
  eye.addEventListener('click', function () { var s = pw.type === 'password'; pw.type = s ? 'text' : 'password'; eye.textContent = s ? '🙈' : '👁'; pw.focus(); });
  function ck(e) { if (e.getModifierState) caps.style.display = e.getModifierState('CapsLock') ? 'block' : 'none'; }
  pw.addEventListener('keydown', ck); pw.addEventListener('keyup', ck);
  form.addEventListener('submit', function () { btn.classList.add('busy'); btn.textContent = 'VERIFYING...'; });
  if (document.querySelector('.err')) { var cd = document.querySelector('.card'); cd.classList.add('shake'); setTimeout(function () { cd.classList.remove('shake'); }, 600); }
})();
"""


def login_page(*, error: str | None = None) -> str:
    setup = not enabled()
    title = "CREATE YOUR ACCESS PASSWORD" if setup else "SECURE ACCESS"
    confirm_field = (
        '<label>CONFIRM PASSWORD</label><div class="field"><input type="password" name="confirm" required minlength="6" autocomplete="new-password"></div>'
        if setup else ""
    )
    hint = (
        "This password will be required on every device that opens this terminal from now on, including "
        "through a tunnel URL. Pick something you can remember. There is no recovery flow, only resetting "
        "it from this computer."
        if setup else "Your session stays signed in for 30 days on this browser."
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JORDAN TERMINAL — Access</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>{_STYLE}</style></head><body>
<canvas id="bg"></canvas><div class="vig"></div>
<div class="top"><span><b>◢</b> JORDAN TERMINAL</span><span>ENCRYPTED SESSION</span></div>
<div class="wrap">
<div class="card">
  <div class="brand">{_LOGO}<h1>JORDAN <span>TERMINAL</span></h1></div>
  <div class="sub">{title}</div>
  <form method="post" action="/login" id="f">
    <label>{"NEW " if setup else ""}PASSWORD</label>
    <div class="field"><input type="password" name="password" required minlength="6" autocomplete="{"new-password" if setup else "current-password"}" autofocus placeholder="••••••••">
      <button type="button" class="eye" id="eye" tabindex="-1" aria-label="Show password">👁</button></div>
    <div class="caps" id="caps">⇪ CAPS LOCK IS ON</div>
    {confirm_field}
    <button type="submit" class="go" id="go">{"SET PASSWORD &amp; ENTER" if setup else "ENTER TERMINAL"}</button>
  </form>
  {f'<div class="err">{error}</div>' if error else ""}
  <div class="hint">{hint}</div>
</div>
</div>
<div class="foot"><span><i class="dot"></i>SYSTEM ONLINE</span><span>MARKETS · ALGOS · WORLD</span><span id="clk">--:--:--</span></div>
<script>{_SCRIPT}</script>
</body></html>"""
