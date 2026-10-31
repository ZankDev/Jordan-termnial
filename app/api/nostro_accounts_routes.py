"""ACCOUNTS page API: every account's card data + the read-only Tradovate link."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import db
from app.accounts import insights, page, simulate, tradovate
from app.notify import traderspost

router = APIRouter(prefix="/api/nostro")


@router.get("/accounts-page")
def accounts_page(include_archived: bool = False):
    return page.build(include_archived=include_archived)


@router.get("/account/{account_id}/insights")
def account_insights(account_id: int, paths: int = 4000):
    try:
        out = dict(insights.build(account_id, paths=max(500, min(20000, paths))))
        # the open trade marked at the last price (fresh on every call, outside the insights cache)
        acct = db.get_account(account_id) or {}
        pos = acct.get("open_position")
        out["open"] = None
        if pos and acct.get("symbol"):
            px = page._last_prices({acct["symbol"]}).get(acct["symbol"])
            out["open"] = {"side": pos.get("side"), "qty": pos.get("qty", 1), "entry_price": pos.get("entry_price"), "entry_time": pos.get("entry_time"),
                           "stop": pos.get("stop_price"), "target": pos.get("target_price"), "last": px[0] if px else None,
                           "last_time": px[1] if px else None, "pnl": page._open_pnl(acct["symbol"], pos, px[0] if px else None)}
        return out
    except KeyError:
        raise HTTPException(status_code=404, detail="Account not found")


@router.post("/simulate-all")
def simulate_all():
    """Replay every account's strategy over the recent history in the background; poll /simulate-status."""
    return simulate.start_all()


@router.get("/simulate-status")
def simulate_status():
    return simulate.status()


@router.post("/account/{account_id}/simulate")
def simulate_one(account_id: int):
    acct = db.get_account(account_id)
    if acct and acct.get("traderspost_linked"):
        raise HTTPException(status_code=409, detail="This account is linked to TradersPost for real execution - simulating it would wipe its real trade history. Unlink it first if you need to simulate.")
    try:
        return simulate.simulate_account(account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Account not found")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/strategy-board")
def strategy_board():
    from app.api.capital_routes import cached

    return {"rows": cached("strategy-board", 120, insights.strategy_board)}


@router.get("/strategy-insights")
def strategy_insights(strategy: str, symbol: str, paths: int = 4000):
    return insights.strategy_insights(strategy, symbol.upper(), max(500, min(20000, paths)))


@router.get("/combos")
def combos(force: bool = False):
    """Multi-year long-history check of every parameter set in use + which 2-3 strategy combos pass the evaluation most."""
    from app.accounts import portfolio

    return portfolio.analyse_long(force=force)


@router.get("/combo-evals")
def combo_evals():
    from app.accounts import combo_eval

    return {"combos": combo_eval.list_all()}


class ComboRequest(BaseModel):
    rank: int = 0  # which combo of the long-history ranking to launch (0 = best)


@router.post("/combo-evals/launch")
def combo_launch(req: ComboRequest):
    """Launch a combo evaluation from the long-history ranking: one live paper account per member at the combo's size."""
    from app.accounts import combo_eval, portfolio

    d = portfolio.analyse_long()
    combos = [c for c in d.get("combos", []) if c["size"] >= 2]
    if req.rank >= len(combos):
        raise HTTPException(status_code=404, detail="No such combo in the ranking.")
    c = combos[req.rank]
    members = [{"strategy": m["strategy"], "symbol": m["symbol"], "params": m["params"], "risk": round(500 * c["best_scale"])} for m in c["members"]]
    name = " + ".join(f"{m['strategy']} {m['symbol']}" for m in c["members"])
    return combo_eval.create(name, members, research={k: c[k] for k in ("pass", "breach", "edge", "best_scale", "days", "median_days_to_pass", "by_year")})


@router.get("/risk")
def risk_recommendations():
    from app.accounts import risk_sync

    return {"recommendations": risk_sync.recommendations(), "clamp": [risk_sync.MIN_RISK, risk_sync.MAX_RISK]}


@router.post("/risk-sync")
def risk_sync_now():
    from app.accounts import risk_sync

    return {"changes": risk_sync.sync()}


# ---------------------------------------------------------------- Tradovate (read-only)
@router.get("/tradovate/status")
def tv_status():
    return tradovate.status()


@router.get("/tradovate/balances")
def tv_balances(force: bool = False):
    return {**tradovate.balances(force=force), "link": tradovate.status()}


class ConnectRequest(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str | None = None
    env: str = "live"


@router.post("/tradovate/connect")
def tv_connect(req: ConnectRequest):
    try:
        return tradovate.connect(req.client_id, req.client_secret, req.redirect_uri, req.env)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class TokenRequest(BaseModel):
    token: str
    env: str = "live"


@router.post("/tradovate/token")
def tv_token(req: TokenRequest):
    try:
        st = tradovate.set_token(req.token, req.env)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"link": st, **tradovate.balances(force=True)}


@router.post("/tradovate/disconnect")
def tv_disconnect():
    return tradovate.disconnect()


@router.get("/tradovate/callback", response_class=HTMLResponse)
def tv_callback(code: str | None = None, error: str | None = None, error_description: str | None = None):
    """Tradovate redirects here after the user approves. Trades the code for a token, then bounces to the page."""
    msg, ok = "", False
    if error or not code:
        msg = error_description or error or "No authorization code was returned."
    else:
        try:
            tradovate.exchange_code(code)
            ok = True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
    safe = msg.replace("<", "&lt;").replace(">", "&gt;")
    body = ("Connected. Taking you back to your accounts…" if ok else f"Could not connect: {safe}")
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Tradovate</title>"
        "<body style='background:#000;color:#ffa028;font:14px monospace;padding:40px'>"
        f"{body}<script>setTimeout(()=>location.replace('/#acct'),{900 if ok else 4000})</script>"
    )


# ---------------------------------------------------------------- TradersPost real-order link
# Links exactly one account to real execution: live_engine fires a TradersPost webhook
# (-> Tradovate) only for the account with traderspost_linked = 1. Every other account's
# local simulation is unaffected regardless of this link's state.
class TraderspostLinkRequest(BaseModel):
    # Optional: TradersPost connects to the broker entirely on its own site (its docs say
    # the Tradovate API Access add-on isn't even required for that), so real order
    # execution needs no Tradovate credentials here at all. tradovate_account_id is only
    # for the optional real-balance/fills display, and only usable once the separate
    # read-only OAuth link above has been connected.
    tradovate_account_id: str | None = None
    tradovate_account_name: str | None = None


@router.post("/account/{account_id}/traderspost/link")
def traderspost_link(account_id: int, req: TraderspostLinkRequest):
    acct = db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if acct.get("is_system"):
        raise HTTPException(status_code=409, detail="The AUTO account cannot be linked to real execution.")
    others = [a for a in db.list_accounts(include_archived=True) if a.get("traderspost_linked") and a["id"] != account_id]
    if others:
        raise HTTPException(status_code=409, detail=f"'{others[0]['name']}' is already linked to TradersPost. Unlink it first.")
    db.set_traderspost_link(account_id, True, req.tradovate_account_id, req.tradovate_account_name)
    return db.get_account(account_id)


@router.post("/account/{account_id}/traderspost/unlink")
def traderspost_unlink(account_id: int):
    if not db.get_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    db.set_traderspost_link(account_id, False)
    return db.get_account(account_id)


@router.get("/account/{account_id}/traderspost/real")
def traderspost_real(account_id: int, force: bool = False):
    """The linked account's real Tradovate balance + fill history, for display only —
    this never feeds back into the strategy's own decisions (see traderspost.py's docstring)."""
    acct = db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not acct.get("traderspost_linked"):
        return {"linked": False}
    webhook_configured = traderspost.is_configured()
    if not acct.get("tradovate_account_id"):
        # Linked for real execution, but no Tradovate OAuth sub-account chosen - normal when
        # that separate read-only link isn't set up (e.g. the broker doesn't expose it, like
        # Apex Trader Funding's Tradovate Prop accounts). Not an error.
        return {"linked": True, "webhook_configured": webhook_configured, "tradovate_account_name": None,
                "state": None, "balance": None, "fills": [], "error": None}
    bal = tradovate.balances(force=force)
    real_acct = next((a for a in bal.get("accounts", []) if str(a["id"]) == str(acct["tradovate_account_id"])), None)
    fl = tradovate.fills(acct["tradovate_account_id"], force=force)
    return {
        "linked": True, "webhook_configured": webhook_configured,
        "tradovate_account_name": acct.get("tradovate_account_name"),
        "state": bal.get("state"), "balance": real_acct, "fills": fl.get("fills", []), "error": bal.get("error") or fl.get("error"),
    }


class PayoutTargetRequest(BaseModel):
    target: float | None = None


@router.post("/account/{account_id}/payout-target")
def set_payout_target(account_id: int, req: PayoutTargetRequest):
    """Stop live trading for this account for good once equity reaches `target`, flagging it
    for payout (separate from the daily-reset circuit breaker). None clears the target."""
    if not db.get_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    db.set_payout_target(account_id, req.target)
    return db.get_account(account_id)


class TraderspostTestRequest(BaseModel):
    side: str = "long"  # "long" or "short"
    take_profit: float = 100.0  # dollars
    stop_loss: float = 50.0  # dollars
    quantity: float = 1


@router.post("/account/{account_id}/traderspost/close")
def traderspost_close(account_id: int):
    """CLOSE button: flatten the real position now. Sends an 'exit' signal for the account's ticker (the broker closes whatever is
    open on it, including a test order), and closes the terminal's own position at the last price if the strategy has one open.
    This sends a real order the instant the button is pressed - it is the user's own action."""
    from app.accounts import eod_flatten
    from app.notify import signal_tracker

    acct = db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not acct.get("traderspost_linked"):
        raise HTTPException(status_code=409, detail="This account is not linked to TradersPost.")
    if not traderspost.is_configured():
        raise HTTPException(status_code=409, detail="TRADERSPOST_WEBHOOK_URL is not configured in run.bat.")
    symbol = acct.get("symbol")
    if not symbol:
        raise HTTPException(status_code=422, detail="This account has no symbol assigned.")
    local = None
    contract = (acct.get("open_position") or {}).get("contract") or "micro"
    px = eod_flatten._price(symbol, {})
    price = round(float(px[0]), 2) if px else float((acct.get("open_position") or {}).get("entry_price") or 0)
    if acct.get("open_position"):
        local = eod_flatten._flatten_one(acct, {}, reason="manual_close")
    sent = traderspost.send_exit(symbol, contract, price)
    tracked = signal_tracker.close_open(symbol, price) if price else 0
    return {"sent": sent, "price": price, "symbol": symbol, "closed_local_position": bool(local), "local_pnl": (local or {}).get("pnl"), "closed_tracked_tests": tracked}


@router.post("/account/{account_id}/traderspost/test-signal")
def traderspost_test_signal(account_id: int, req: TraderspostTestRequest):
    """Fires one real entry signal for the linked account using the current market price,
    for manually verifying the webhook end to end. This sends a real order the instant the
    button is clicked - the button click is the user's own action, not something triggered
    automatically by anything in this terminal."""
    from app.backtest.sizing import point_value
    from app.data import yfinance_source

    acct = db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not acct.get("traderspost_linked"):
        raise HTTPException(status_code=409, detail="This account is not linked to TradersPost.")
    if not traderspost.is_configured():
        raise HTTPException(status_code=409, detail="TRADERSPOST_WEBHOOK_URL is not configured in run.bat.")
    symbol = acct.get("symbol")
    if not symbol:
        raise HTTPException(status_code=422, detail="This account has no symbol assigned.")
    if req.side not in ("long", "short"):
        raise HTTPException(status_code=422, detail="side must be 'long' or 'short'.")

    df = yfinance_source.fetch(symbol, "5m", max_age_seconds=300)
    if df.empty:
        raise HTTPException(status_code=502, detail=f"Could not fetch a current price for {symbol}.")
    price = round(float(df["close"].iloc[-1]), 2)
    contract = (acct.get("open_position") or {}).get("contract") or "micro"
    pv = point_value(symbol, contract) * req.quantity
    direction = 1 if req.side == "long" else -1
    stop_price = round(price - direction * (req.stop_loss / pv), 2)
    target_price = round(price + direction * (req.take_profit / pv), 2)
    sent = traderspost.send_entry(symbol, contract, req.side, req.quantity, price, stop_price, target_price)
    if sent:  # follow this test order against the market so the terminal shows it open / closed and what it made
        try:
            from app.notify import signal_tracker

            signal_tracker.backfill_from_log()
            signal_tracker.open_test(symbol, contract, req.side, req.quantity, price, stop_price, target_price)
        except Exception:  # noqa: BLE001 - tracking is an extra; the order has already been sent
            pass
    return {"sent": sent, "symbol": symbol, "contract": contract, "side": req.side, "price": price,
            "stop_price": stop_price, "target_price": target_price, "quantity": req.quantity}
