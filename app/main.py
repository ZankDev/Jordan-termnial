import asyncio
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import access, db
from app.accounts import live_engine
from app.api.field_routes import router as field_router
from app.api.capital_routes import router as capital_router, warm as capital_warm
from app.api.invest_routes import router as invest_router, warm as invest_warm
from app.api.deep_routes import router as deep_router
from app.api.connect_routes import router as connect_router
from app.api.ai_routes import router as ai_router
from app.api.snap_routes import router as snap_router
from app.api.world_routes import router as world_router, warm as world_warm
from app.api.onchain_routes import router as onchain_router
from app.api.academy_routes import router as academy_router, warm as academy_warm
from app.api.sim_routes import router as sim_router
from app.api.polymarket_routes import router as polymarket_router
from app.api.pm_market_routes import router as pm_market_router
from app.api.spider_routes import router as spider_router
from app.api.ntlab_routes import router as ntlab_router
from app.api.pm_field_routes import router as pm_field_router
from app.api.lab_routes import router as lab_router
from app.api.market_routes import router as market_router, warm as market_warm
from app.api.live_routes import router as live_router
from app.api.routes import router as api_router
from app import alerts
from app.notify import telegram_listener
from app.optimizer import trainer_service
from app.polymarket import store as pm_store
from app.polymarket import trainer_service as pm_trainer_service
from app.polymarket import live_engine as pm_live_engine
from app.polymarket import copytrade_loop as pm_copytrade_loop

db.init_db()
pm_store.init_db()

# The trainer's backtests are CPU-bound Python and share this process with the API and the live engine.
# sqlite3 gives the GIL up on every row it steps, and with the default 5 ms switch interval a 10,000-row
# response (The Field) waited 12-30 s for its turns. A 1 ms interval makes those hand-offs 5x cheaper.
sys.setswitchinterval(0.001)

app = FastAPI(title="Algo Trading Terminal")
app.add_middleware(GZipMiddleware, minimum_size=1200)
app.include_router(api_router)
app.include_router(live_router)
app.include_router(field_router)
app.include_router(capital_router)
app.include_router(invest_router)
from app.api.intel_routes import router as intel_router  # noqa: E402

app.include_router(intel_router)
from app.api.fund_routes import router as fund_router  # noqa: E402

app.include_router(fund_router)
from app.api.gamma_routes import router as gamma_vwap_router  # noqa: E402

app.include_router(gamma_vwap_router)
from app.api.journal_routes import router as journal_router  # noqa: E402

app.include_router(journal_router)
app.include_router(deep_router)
app.include_router(connect_router)
app.include_router(market_router)
app.include_router(lab_router)
app.include_router(ai_router)
app.include_router(snap_router)
app.include_router(world_router)
app.include_router(onchain_router)
app.include_router(academy_router)
app.include_router(sim_router)
app.include_router(polymarket_router)
app.include_router(pm_market_router)
app.include_router(spider_router)
app.include_router(ntlab_router)
app.include_router(pm_field_router)
from app.api.polymarket_real_routes import router as pm_real_router  # noqa: E402

app.include_router(pm_real_router)
from app.api.nostro_accounts_routes import router as nostro_accounts_router  # noqa: E402

app.include_router(nostro_accounts_router)
from app.api.morning_routes import router as morning_router  # noqa: E402

app.include_router(morning_router)
from app.api.flow_routes import router as flow_router  # noqa: E402
from app.api.bmap_routes import router as bmap_router, warm as bmap_warm  # noqa: E402

app.include_router(flow_router)
app.include_router(bmap_router)
from app.api.wealth_routes import router as wealth_router  # noqa: E402

app.include_router(wealth_router)


@app.middleware("http")
async def _wealth_pin_guard(request, call_next):
    """When a WEALTH PIN is set, the money APIs need the unlock cookie (see app/wealth/security.py)."""
    from fastapi.responses import JSONResponse

    from app.wealth import security as _sec

    p = request.url.path
    if p.startswith(_sec.PROTECTED) and not p.startswith(_sec.OPEN) and not _sec.token_ok(request.cookies.get("wealth_token")):
        return JSONResponse({"detail": "locked — enter your PIN on the WEALTH page"}, status_code=423)
    return await call_next(request)


@app.middleware("http")
async def _revalidate_pages(request: Request, call_next):
    """The page itself (index.html) is always re-checked with the server (cheap, it answers 304 when unchanged), so a new script or
    stylesheet version link is picked up on the next load instead of after the browser's heuristic cache expires."""
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith(".html") or p.endswith(".webmanifest"):
        resp.headers["Cache-Control"] = "no-cache"
    elif "v=" in request.url.query and p.endswith((".js", ".css")) and resp.status_code == 200:
        # versioned script/stylesheet (index.html bumps ?v= on every change): safe to keep for a year, no revalidation
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.middleware("http")
async def _access_gate(request: Request, call_next):
    """Whole-app login gate (see app/access.py). Before a password is ever set, page loads are forced to /login
    to set one up — a bare GET / silently showing the dashboard is exactly how this stayed wide open the first
    time someone opened the tunnel URL. /api/* calls are left alone pre-setup since nothing reaches them until
    the page itself has loaded past /login anyway."""
    p = request.url.path
    if access.is_open(p):
        return await call_next(request)
    if not access.enabled():
        if p.startswith("/api/"):
            return await call_next(request)
        return RedirectResponse("/login")
    if access.token_ok(request.cookies.get(access.COOKIE)):
        return await call_next(request)
    if p.startswith("/api/"):
        return JSONResponse({"detail": "login required"}, status_code=401)
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
async def login_get():
    return access.login_page()


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request):
    from urllib.parse import parse_qs

    form = parse_qs((await request.body()).decode())  # avoids the python-multipart dependency for this 2-field form
    password, confirm = (form.get("password") or [""])[0], (form.get("confirm") or [None])[0]
    try:
        if not access.enabled():
            if password != confirm:
                return HTMLResponse(access.login_page(error="Passwords don't match."), status_code=400)
            access.set_password(password)
        elif not access.check(password):
            return HTMLResponse(access.login_page(error="Wrong password."), status_code=401)
        token = access.login(password)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(access.COOKIE, token, max_age=access.SESSION_DAYS * 86400, httponly=True, samesite="lax")
        return resp
    except ValueError as e:
        return HTMLResponse(access.login_page(error=str(e)), status_code=400)


if os.environ.get("NOSTRO_LOOPS", "all") != "":
    capital_warm()
    invest_warm()
    market_warm()


_WORKER: dict = {"proc": None}


def _start_worker(loops: list[str]) -> None:
    """Launch app/worker.py (trainers) as a child process at below-normal priority; a stale one from a crashed run is stopped first."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    pid_file = root / "data_cache" / "worker.pid"
    try:
        old = int(pid_file.read_text(encoding="utf-8").strip())
        if old and os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {old}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
            if "python" in out.lower():
                subprocess.run(["taskkill", "/PID", str(old), "/T", "/F"], capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001 - no pid file / not running
        pass
    env = {**os.environ, "NOSTRO_WORKER_PARENT": str(os.getpid()), "NOSTRO_WORKER_LOOPS": ",".join(loops)}
    log = open(root / "data_cache" / "worker.log", "a", encoding="utf-8")
    flags = (0x00004000 | 0x08000000) if os.name == "nt" else 0  # BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    _WORKER["proc"] = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=str(root), env=env, stdout=log, stderr=log, creationflags=flags)
    os.environ["NOSTRO_TRAINER_IN_WORKER"] = "1"


@app.on_event("shutdown")
async def _stop_worker() -> None:
    p = _WORKER.get("proc")
    if p and p.poll() is None:
        p.terminate()


@app.on_event("startup")
async def _launch_background_loops() -> None:
    from app.notify import connections

    connections.load_into_env()  # webhook / Telegram set up in the REAL ACCOUNT page become visible to the code that reads the environment
    # NOSTRO_LOOPS (comma list of trainer,live,telegram) lets a staged server on another
    # port run only some loops, so it never runs a second live engine or duplicates
    # Telegram messages. Unset = all loops, the normal production behaviour.
    raw = os.environ.get("NOSTRO_LOOPS")
    # "world" / "academy" (globe samplers, BMAP ship stream, Wyckoff library build) are off by default since those pages were
    # hidden (2026-10-21); add them to NOSTRO_LOOPS to bring them back.
    enabled = {"trainer", "live", "telegram", "alerts", "polymarket", "morning", "wealth", "intel", "gamma"} if raw is None else {s.strip() for s in raw.split(",") if s.strip()}
    heavy = sorted({"trainer", "polymarket"} & enabled)
    if heavy and os.environ.get("NOSTRO_WORKER", "1") != "0":
        _start_worker(heavy)  # CPU-heavy training in its own low-priority process (app/worker.py) so pages stay fast
    else:
        if "trainer" in enabled:
            asyncio.create_task(trainer_service.scheduler_loop())
        if "polymarket" in enabled:
            asyncio.create_task(pm_trainer_service.scheduler_loop())
            asyncio.create_task(pm_trainer_service.backfill_loop())
    if "polymarket" in enabled:
        asyncio.create_task(pm_trainer_service.digest_loop())
        asyncio.create_task(pm_live_engine.live_loop())
        asyncio.create_task(pm_copytrade_loop.copytrade_loop())
    if "live" in enabled:
        asyncio.create_task(live_engine.live_loop())
    if "telegram" in enabled and "live" in enabled:
        from app.notify import ops_alerts

        ops_alerts.startup_notice()
        asyncio.create_task(ops_alerts.monitor_loop())
        asyncio.create_task(connections.inbox_loop())  # picks up Tradovate Performance CSVs and nudges when the real balance is stale
    if "morning" in enabled:
        from app.morning import loop as morning_loop

        asyncio.create_task(morning_loop.morning_loop())
    if "wealth" in enabled:
        from app.wealth import loop as wealth_loop

        asyncio.create_task(wealth_loop.wealth_loop())
    if "telegram" in enabled:
        asyncio.create_task(telegram_listener.listener_loop())
        from app.notify import live_summary

        asyncio.create_task(live_summary.summary_loop())  # today's summary message, edited every 20 s
        from app.learn import iceberg

        asyncio.create_task(iceberg.study_loop())  # ICEBERG course: 08:00 topic name, 22:00 10-question quiz
    if "alerts" in enabled:
        asyncio.create_task(alerts.alerts_loop())
    if "intel" in enabled:
        from app.intel import events as intel_events

        asyncio.create_task(intel_events.intel_loop())  # critical-event detection + Telegram pushes, every 5 min
        asyncio.create_task(intel_events.news_loop())  # fast sources (Telegram channels, Truth Social) + news impact engine, every 60 s
        from app.intel import spider_memory

        asyncio.create_task(spider_memory.spider_loop())  # SPIDER: stores every headline/event, reads one marked article in full every 2.5 min
    if "gamma" in enabled:
        from app.invest import gamma_worker

        asyncio.create_task(gamma_worker.worker_loop())  # NQ vs VWAP vs gamma walls: Telegram alerts + PAPER 1:2 entries, every 30 s
    if "academy" in enabled:
        academy_warm()  # download history + build the Wyckoff setup library in a thread
    if "world" in enabled:
        world_warm()  # OpenSky / adsb.lol samplers + cache prefetch (threads)
        bmap_warm()  # AIS ship stream (when a key is configured) + power-plant file


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
