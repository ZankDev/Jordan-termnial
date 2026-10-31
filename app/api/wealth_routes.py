"""WEALTH API: net worth, manual assets, risk + kill switch, performance, goals & rebalancing, budget, deals, security."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import db
from app.wealth import budget, cashflow, core, deals, security, shopify

router = APIRouter(prefix="/api/wealth")


# ------------------------------------------------------------------ net worth
def _c(key: str, ttl: float, fn):
    from app.api.capital_routes import cached

    return cached(f"wealth:{key}", ttl, fn)


# stale-while-revalidate for the slow views (net worth reads the portfolio, Polymarket and FX: ~15 s cold): a value younger
# than `ttl` is returned as is; an older one (up to 30 min) is returned immediately and refreshed in the background.
_swr: dict[str, list] = {}
_swr_lock = threading.Lock()
SWR_MAX_AGE = 1800


def _swr_refresh(key: str, fn) -> None:
    try:
        v = fn()
        with _swr_lock:
            _swr[key] = [time.time(), v, False]
    except Exception:  # noqa: BLE001 - keep serving the old value
        with _swr_lock:
            if key in _swr:
                _swr[key][2] = False


def _s(key: str, ttl: float, fn):
    now = time.time()
    with _swr_lock:
        hit = _swr.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        if hit and now - hit[0] < SWR_MAX_AGE:
            if not hit[2]:
                hit[2] = True
                threading.Thread(target=_swr_refresh, args=(key, fn), daemon=True).start()
            return hit[1]
    v = fn()
    with _swr_lock:
        _swr[key] = [time.time(), v, False]
    return v


def _bust() -> None:
    from app.api import capital_routes as cr

    with cr._lock:
        for k in [k for k in list(cr._cache) if str(k).startswith("wealth:")]:
            cr._cache.pop(k, None)
    with _swr_lock:
        _swr.clear()
    core._nw_memo.clear()


def warm() -> None:
    """Fill the slow views ahead of time (called by the WEALTH loop) so the pages open instantly."""
    from app.wealth import insights

    for key, fn in (("nw", core.networth), ("cash", cashflow.overview), ("outlook", insights.outlook), ("holdings", insights.holdings),
                    ("risk", core.risk), ("perf", core.performance)):
        try:
            _swr_refresh(key, fn)
        except Exception:  # noqa: BLE001
            pass
    try:
        from app.wealth import goalhub

        goalhub.metric_values(0)
    except Exception:  # noqa: BLE001
        pass
    try:  # prime the Shopify caches the plan / Shopify sections read (network, ~5 s cold)
        from app.wealth import plan, shopify

        plan.revenue()
        if shopify.status()["connected"]:
            shopify.summary(90)
            shopify.summary(30)
    except Exception:  # noqa: BLE001
        pass


@router.get("/networth")
def api_networth(fresh: bool = False):
    if fresh:
        _bust()
    return _s("nw", 60, core.networth)


@router.get("/history")
def api_history(days: int = 730):
    return {"history": core.history(days)}


@router.post("/snapshot")
def api_snapshot():
    return core.snapshot()


class AssetIn(BaseModel):
    id: int | None = None
    name: str
    category: str
    currency: str = "ILS"
    value: float
    notes: str | None = None


@router.post("/assets")
def api_asset(a: AssetIn):
    if not a.name.strip():
        raise HTTPException(status_code=422, detail="name required")
    out = core.upsert_asset(a.model_dump())
    _bust()
    return out


@router.delete("/assets/{aid}")
def api_asset_del(aid: int):
    core.delete_asset(aid)
    _bust()
    return {"ok": True}


@router.get("/settings")
def api_settings():
    return core.settings()


@router.post("/settings")
def api_settings_save(patch: dict[str, Any]):
    return core.save_settings(patch)


# ------------------------------------------------------------------ risk
@router.get("/risk")
def api_risk():
    return _s("risk", 120, core.risk)


class KillReq(BaseModel):
    confirm: str


@router.post("/kill-switch")
def api_kill(req: KillReq):
    if req.confirm != "STOP":
        raise HTTPException(status_code=422, detail="type STOP to confirm")
    return core.kill_switch("pressed on WEALTH › RISK")


# ------------------------------------------------------------------ performance / goals
@router.get("/performance")
def api_performance():
    return _s("perf", 300, core.performance)


@router.get("/rebalance")
def api_rebalance():
    return core.rebalance()


class GoalIn(BaseModel):
    name: str = "Goal"
    target_usd: float
    monthly_usd: float = 0
    years: int = 10
    start_usd: float | None = None


@router.post("/project")
def api_project(g: GoalIn):
    return core.project(g.model_dump())


# ------------------------------------------------------------------ budget
@router.get("/budget")
def api_budget(months: int = 12):
    return budget.summary(max(1, min(36, months)))


class ImportIn(BaseModel):
    b64: str                 # the CSV file's bytes, base64 (the page reads the file locally)
    account: str = ""
    charges_positive: str = "auto"


@router.post("/budget/import")
def api_budget_import(req: ImportIn):
    import base64

    try:
        raw = base64.b64decode(req.b64)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad file data")
    account, charges_positive = req.account, req.charges_positive
    text = None
    for enc in ("utf-8-sig", "cp1255", "iso-8859-8", "utf-16"):
        try:
            text = raw.decode(enc)
            if "\x00" not in text:
                break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(status_code=422, detail="could not read the file encoding")
    try:
        out = budget.import_csv(text, account, None if charges_positive == "auto" else charges_positive == "yes")
        _bust()
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class TxIn(BaseModel):
    day: str
    description: str
    amount: float
    category: str | None = None
    account: str | None = None


@router.post("/budget/tx")
def api_tx(t: TxIn):
    return budget.add_tx(t.day, t.description, t.amount, t.category, t.account)


class CatIn(BaseModel):
    category: str
    apply_to_same: bool = True


@router.post("/budget/tx/{tx_id}/category")
def api_tx_cat(tx_id: int, c: CatIn):
    return {"updated": budget.set_category(tx_id, c.category, c.apply_to_same)}


@router.delete("/budget/tx/{tx_id}")
def api_tx_del(tx_id: int):
    budget.delete_tx(tx_id)
    return {"ok": True}


# ------------------------------------------------------------------ deals
@router.get("/deals")
def api_deals(max_price: float | None = None, region: str | None = None, category: str | None = None, q: str | None = None, starred: bool = False):
    return deals.listing(max_price, region or None, category or None, q or None, starred)


@router.post("/deals/refresh")
def api_deals_refresh():
    return deals.refresh(force=True)


class MarkIn(BaseModel):
    starred: bool | None = None
    hidden: bool | None = None


@router.post("/deals/{deal_id}")
def api_deal_mark(deal_id: int, m: MarkIn):
    deals.mark(deal_id, m.starred, m.hidden)
    return {"ok": True}


@router.get("/deals/filter")
def api_deal_filter():
    return json.loads(db.meta_get("deals_filter") or "{}")


@router.post("/deals/filter")
def api_deal_filter_save(f: dict[str, Any]):
    db.meta_set("deals_filter", json.dumps(f))
    return f


# ------------------------------------------------------------------ security
@router.get("/security/status")
def api_sec_status(request: Request):
    return {**security.status(), "unlocked": security.token_ok(request.cookies.get("wealth_token"))}


@router.post("/security/backup")
def api_sec_backup():
    return security.backup_now("manual")


@router.post("/security/encrypt-wallet")
def api_sec_encrypt():
    return security.encrypt_wallet()


class PinIn(BaseModel):
    pin: str | None = None
    current: str | None = None


@router.post("/security/pin")
def api_sec_pin(p: PinIn):
    try:
        return security.set_pin(p.pin, p.current)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/security/unlock")
def api_sec_unlock(p: PinIn, response: Response):
    t = security.unlock(p.pin or "")
    if not t:
        raise HTTPException(status_code=403, detail="wrong PIN")
    response.set_cookie("wealth_token", t, max_age=security.UNLOCK_HOURS * 3600, httponly=True, samesite="strict")
    return {"ok": True}


@router.post("/report/{kind}")
def api_report(kind: str, send: bool = False):
    from app.wealth import loop

    text = loop.report("monthly" if kind == "monthly" else "weekly")
    if send:
        loop._send(text)
    return {"text": text, "sent": send}


# ------------------------------------------------------------------ cash flow (recurring, main balance, credit-card question)
@router.get("/cashflow")
def api_cashflow():
    return _s("cash", 60, cashflow.overview)


class RecIn(BaseModel):
    id: int | None = None
    name: str
    amount: float
    day: int = 1
    category: str | None = None
    active: int = 1


@router.post("/cashflow/recurring")
def api_rec(r: RecIn):
    out = cashflow.upsert_recurring(r.model_dump())
    _bust()
    return out


@router.delete("/cashflow/recurring/{rid}")
def api_rec_del(rid: int):
    cashflow.delete_recurring(rid)
    _bust()
    return {"ok": True}


class BalIn(BaseModel):
    value: float


@router.post("/cashflow/balance")
def api_balance(b: BalIn):
    out = cashflow.set_balance(b.value)
    _bust()
    return out


@router.post("/cashflow/ask-cc")
def api_ask_cc():
    """Send the credit-card question on Telegram now (for last month) — normally automatic on the 1st."""
    import datetime as _dt

    month = (_dt.date.today().replace(day=1) - _dt.timedelta(days=1)).strftime("%Y-%m")
    db.meta_set("cc_pending", json.dumps({"month": month, "asked": []}))
    db.meta_set(f"cc_answered_{month}", "")
    from app.notify import telegram_bot

    telegram_bot.send_text(f"💳 How much did you spend on credit cards in {month}?\nReply with just the number (e.g. 3450). "
                           "I'll book it as an expense and take it off your main balance.")
    db.meta_set("cc_pending", json.dumps({"month": month, "asked": [_dt.date.today().isoformat()]}))
    return {"asked": month}


class CcIn(BaseModel):
    amount: float


@router.post("/cashflow/cc")
def api_cc(c: CcIn):
    """Answer the credit-card question from the page instead of Telegram."""
    pend = json.loads(db.meta_get("cc_pending") or "null") or {}
    if not pend.get("month"):
        import datetime as _dt

        db.meta_set("cc_pending", json.dumps({"month": (_dt.date.today().replace(day=1) - _dt.timedelta(days=1)).strftime("%Y-%m"), "asked": []}))
    reply = cashflow.handle_telegram_text(str(c.amount))
    _bust()
    return {"reply": reply}


# ------------------------------------------------------------------ insights (extra data for the four pages)
@router.get("/outlook")
def api_outlook():
    from app.wealth import insights

    return _s("outlook", 120, insights.outlook)


@router.get("/holdings")
def api_holdings():
    from app.wealth import insights

    return _s("holdings", 300, insights.holdings)


# ------------------------------------------------------------------ savings goals + road to ₪1M
from app.wealth import plan  # noqa: E402


@router.get("/goals")
def api_goals():
    return plan.goals()


class GoalBox(BaseModel):
    id: int | None = None
    name: str
    emoji: str | None = None
    target: float
    deadline: str | None = None
    color: str | None = None
    saved: float | None = None


@router.post("/goals")
def api_goal_save(g: GoalBox):
    if not g.name.strip() or g.target <= 0:
        raise HTTPException(status_code=422, detail="name and a positive target are required")
    return plan.upsert_goal(g.model_dump())


@router.delete("/goals/{gid}")
def api_goal_del(gid: int):
    plan.delete_goal(gid)
    return {"ok": True}


class MoveIn(BaseModel):
    amount: float
    note: str | None = None


@router.post("/goals/{gid}/move")
def api_goal_move(gid: int, m: MoveIn):
    if not m.amount:
        raise HTTPException(status_code=422, detail="amount required")
    return plan.move(gid, m.amount, m.note)


@router.get("/plan")
def api_plan(day: str | None = None):
    return plan.overview(day)


class TickIn(BaseModel):
    task_id: int
    day: str | None = None
    done: bool | None = None


@router.post("/plan/tick")
def api_plan_tick(t: TickIn):
    try:
        return plan.toggle(t.task_id, t.day, t.done)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class TaskIn(BaseModel):
    title: str
    freq: str = "daily"
    phase: int | None = None
    category: str | None = None
    minutes: int | None = None


@router.post("/plan/task")
def api_plan_task(t: TaskIn):
    if not t.title.strip():
        raise HTTPException(status_code=422, detail="title required")
    try:
        return plan.add_task(t.title, t.freq, t.phase, t.category, t.minutes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class TaskPatch(BaseModel):
    title: str | None = None
    active: bool | None = None


@router.post("/plan/task/{tid}")
def api_plan_task_patch(tid: int, p: TaskPatch):
    plan.update_task(tid, p.title, p.active)
    return {"ok": True}


@router.post("/plan/settings")
def api_plan_settings(patch: dict[str, Any]):
    return plan.save_settings(patch)


class RevIn(BaseModel):
    day: str
    amount: float
    source: str | None = None
    note: str | None = None


@router.post("/plan/revenue")
def api_plan_rev(r: RevIn):
    return plan.add_revenue(r.day, r.amount, r.source, r.note)


@router.delete("/plan/revenue/{rid}")
def api_plan_rev_del(rid: int):
    plan.delete_revenue(rid)
    return {"ok": True}


@router.post("/plan/telegram")
def api_plan_tg():
    plan.send_now()
    return {"sent": True}


# ------------------------------------------------------------------ goals & missions (NOSTRO farm, stocks, wealth, business)
from app.wealth import goalhub  # noqa: E402


@router.get("/hub")
def api_hub(area: str | None = None):
    return goalhub.overview(area or None)


class HubGoalIn(BaseModel):
    id: int | None = None
    area: str
    title: str
    emoji: str | None = None
    metric: str | None = None
    direction: str = "up"
    target: float
    deadline: str | None = None
    why: str | None = None
    start_value: float | None = None
    manual_value: float | None = None
    tasks: list[str] = []


@router.post("/hub/goal")
def api_hub_goal(g: HubGoalIn):
    if not g.title.strip() or g.area not in goalhub.AREAS:
        raise HTTPException(status_code=422, detail="title and a valid area are required")
    try:
        return goalhub.save_goal(g.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/hub/goal/{gid}")
def api_hub_goal_del(gid: int):
    goalhub.delete_goal(gid)
    return {"ok": True}


class HubValueIn(BaseModel):
    value: float


@router.post("/hub/goal/{gid}/value")
def api_hub_value(gid: int, v: HubValueIn):
    goalhub.set_value(gid, v.value)
    return {"ok": True}


class HubTaskIn(BaseModel):
    goal_id: int
    title: str


@router.post("/hub/task")
def api_hub_task(t: HubTaskIn):
    if not t.title.strip():
        raise HTTPException(status_code=422, detail="title required")
    return goalhub.add_task(t.goal_id, t.title)


@router.post("/hub/task/{tid}/toggle")
def api_hub_toggle(tid: int):
    try:
        return goalhub.toggle_task(tid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/hub/task/{tid}")
def api_hub_task_del(tid: int):
    goalhub.delete_task(tid)
    return {"ok": True}


# ------------------------------------------------------------------ Shopify
@router.get("/shopify")
def api_shopify(days: int = 90):
    try:
        return {"status": shopify.status(), "summary": shopify.summary(max(7, min(365, days)))}
    except PermissionError as e:
        return {"status": shopify.status(), "summary": {"connected": True, "error": str(e)}}
    except Exception as e:  # noqa: BLE001
        return {"status": shopify.status(), "summary": {"connected": True, "error": f"Shopify unreachable: {e}"}}


class ShopIn(BaseModel):
    shop: str
    token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


@router.post("/shopify/connect")
def api_shopify_connect(s: ShopIn):
    try:
        return shopify.connect(s.shop, s.token, s.client_id, s.client_secret)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"could not reach the store: {e}")


@router.post("/shopify/disconnect")
def api_shopify_disconnect():
    return shopify.disconnect()
