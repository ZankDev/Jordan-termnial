"""DEEP DIVE API: the interactive security page. /api/invest/deep/start launches the same parallel data fetch the /report uses, in a background thread that records
each source the moment it answers; /api/invest/deep/poll hands the page every section whose sources have arrived (built by app.notify.report_deepdata from the same
context the PDF uses, so the numbers match the report). Sections arrive progressively: fast ones in seconds, the slow ones (filing language, XBRL rank) as they finish."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api import invest_routes as inv
from app.notify import report_data as rd
from app.notify import report_deepdata as dd
from app.notify import report_sections as rs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/invest")
JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
TTL = 600  # a finished job is reused for 10 minutes


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: (None if v == {} else _clean(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def _run(job: dict[str, Any], sym: str, sec: dict[str, Any], is_coin: bool) -> None:
    def on_result(k: str, v: Any) -> None:
        with job["lock"]:
            job["D"][k] = v
            job["done"].add(k)

    try:
        rd.fetch_all(inv, sym, sec, is_coin, deadline=150.0, on_result=on_result)
    except Exception:  # noqa: BLE001
        logger.exception("deep dive fetch failed for %s", sym)
    finally:
        job["finished"] = True
        job["t_done"] = time.time()


def _start(sym: str, force: bool = False) -> dict[str, Any]:
    with LOCK:
        job = JOBS.get(sym)
        if job and not force and time.time() - job["t"] < TTL:
            return job
        try:
            sec = _clean(inv._security(sym))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=f"no data for {sym}") from e
        is_coin = (sec.get("type") or "").upper() == "CRYPTOCURRENCY"
        job = {"t": time.time(), "sym": sym, "sec": sec, "is_coin": is_coin, "D": {}, "done": set(), "finished": False, "built": {}, "lock": threading.Lock(), "t_done": None}
        JOBS[sym] = job
        for k in [k for k, j in JOBS.items() if time.time() - j["t"] > 3600]:
            JOBS.pop(k, None)
        threading.Thread(target=_run, args=(job, sym, sec, is_coin), daemon=True).start()
        return job


def _ctx(job: dict[str, Any]) -> rs.Ctx | None:
    with job["lock"]:
        D = _clean(dict(job["D"]))
    h5 = D.get("hist5") or inv._safe_history(job["sym"], "1y", "1d")
    if not h5 or not h5.get("candles"):
        return None
    all_c = [c for c in h5["candles"] if None not in c[:5]]
    return rs.Ctx(job["sym"], job["sec"], D, job["is_coin"], all_c)


@router.get("/deep/start")
def api_deep_start(symbol: str, refresh: bool = False):
    sym = inv.resolve(symbol)["symbol"] if symbol else None
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    job = _start(sym, refresh)
    s = job["sec"]
    return {"symbol": sym, "name": s.get("name"), "type": s.get("type"), "is_coin": job["is_coin"], "price": s.get("price"), "sections": sorted(dd.COIN_DEPS if job["is_coin"] else dd.STOCK_DEPS)}


@router.get("/deep/poll")
def api_deep_poll(symbol: str, have: str = ""):
    job = JOBS.get(symbol)
    if not job:
        job = _start(symbol)
    deps = dd.COIN_DEPS if job["is_coin"] else dd.STOCK_DEPS
    have_set = {x for x in have.split(",") if x}
    out: dict[str, Any] = {}
    pending: list[str] = []
    X = None
    for name, need in deps.items():
        if name in have_set:
            continue
        if name in job["built"]:
            out[name] = job["built"][name]
            continue
        with job["lock"]:
            ready = need <= job["done"] or job["finished"]
        if not ready:
            pending.append(name)
            continue
        try:
            if X is None:
                X = _ctx(job)
            if X is None:
                job["built"][name] = {"empty": True}
            else:
                data = dd.build(name, X)
                job["built"][name] = data if data is not None else {"empty": True}
        except Exception:  # noqa: BLE001
            logger.exception("deep dive section %s failed for %s", name, symbol)
            job["built"][name] = {"empty": True, "error": True}
        out[name] = job["built"][name]
    with job["lock"]:
        waiting = sorted(set().union(*(deps[n] for n in pending)) - job["done"]) if pending else []
    return {"sections": out, "pending": pending, "waiting_on": waiting, "finished": job["finished"], "age": round(time.time() - job["t"], 1)}
