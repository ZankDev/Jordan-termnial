"""JSON builders for the interactive DEEP DIVE page (security › DEEP). Each builder takes the report context X (the same object the PDF sections use, so the
numbers match the report exactly) and returns plain data for the browser to draw, or None when the source had nothing. SECTION_DEPS says which fetched
sources a section needs; the page asks for a section as soon as those sources have answered."""
from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np

from app.notify import report_calc as rc
from app.notify import report_formulas as rfm
from app.notify import report_visual as rv
from app.notify.report_sections import Ctx

STOCK_DEPS: dict[str, set[str]] = {
    "fair": {"peer_extra", "subj_val", "analysts", "h_tnx", "raw", "sec"}, "flow": {"sec"}, "formulas": {"sec", "h_tnx", "bench5", "peer_extra", "subj_val"}, "rank": {"xrank"},
    "peers": {"peer_extra", "subj_val"}, "shortint": {"finra_si"}, "options": {"cboe"}, "trials": {"trials"}, "recalls": {"nhtsa"}, "language": {"sec_lang"},
    "attention": {"wiki", "stwits", "hn", "gdelt"}, "contracts": {"usasp"}, "macro": {"global_macro", "cot", "imf"}, "ark": {"ark"}, "insiders": {"form4_tx"}, "cone": {"hist5"},
}
COIN_DEPS: dict[str, set[str]] = {
    "coinmkt": {"coin"}, "perps": {"perps"}, "deribit": {"deribit"}, "llama": {"llama"}, "sentiment": {"fng", "trending", "cglobal"}, "macro": {"global_macro", "cot", "macro"}, "cone": {"hist5"},
}


def clean(x: Any) -> Any:
    """Make anything JSON-safe: numpy numbers, dates, sets, NaN and infinity."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    if isinstance(x, (list, tuple, set)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, np.integer):
        return int(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return x


def _fair(X: Ctx) -> dict[str, Any] | None:
    f = rv.fair_value(X)
    if not f:
        return None
    return {"methods": [{k: m.get(k) for k in ("name", "low", "mid", "high", "w", "basis", "out")} for m in f["ms"]], "fair": f["fair"], "up": f["up"], "cv": f["cv"],
            "price": f["price"], "w52": X.sec.get("week52"), "verdict": f["verdict"], "tone": f["tone"]}


def _flow(X: Ctx) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    s = rv.sankey_model(X)
    if s:
        out["sankey"] = {"end": s["end"], "loss": s["loss"], "rev": s["rev"], "cols": s["cols"], "links": [{"s": a, "d": b, "v": v, "color": c} for a, b, v, c in s["links"]]}
    f = rv.funnel_stages(X)
    if f:
        out["funnel"] = {"end": f["end"], "rev": f["rev"], "stages": [{"name": n, "v": v} for n, v in f["stages"]]}
    c = rv.cash_steps(X)
    if c:
        out["cash"] = {"end": c["end"], "prev_end": c["prev_end"], "steps": [{"name": n, "v": v, "kind": k} for n, v, k in c["steps"]]}
    return out or None


def _formulas(X: Ctx) -> dict[str, Any] | None:
    rows = rfm.compute_rows(X)
    return {"rows": [{"group": g, "name": n, "eq": e, "value": v, "reading": r, "tone": t} for g, n, e, v, r, t in rows]} if rows else None


def _peers(X: Ctx) -> dict[str, Any] | None:
    px = X.D.get("peer_extra") or {}
    plist = px.get("peers") or []
    subj = rc.val_row(X.D.get("subj_val"))
    pv = {s: rc.val_row(q) for s, q in (px.get("val") or {}).items() if q}
    if not plist and not pv:
        return None
    rows = []
    for p in plist[:10]:
        sp = p.get("spark") or []
        m1 = (sp[-1] / sp[-21] - 1) * 100 if len(sp) >= 21 and sp[-21] else None
        w = p.get("week52") or [None, None]
        pos = (p["price"] - w[0]) / (w[1] - w[0]) * 100 if p.get("price") and w[0] and w[1] and w[1] > w[0] else None
        rows.append({"symbol": p["symbol"], "name": p.get("name"), "price": p.get("price"), "pct": p.get("pct"), "m1": m1, "pos": pos})
    bubble = [{"symbol": s, "rg": v["rg"], "gm": v["gm"], "mcap": v["mcap"], "me": s == X.sym} for s, v in [(X.sym, subj)] + list(pv.items()) if v and v.get("gm") is not None and v.get("rg") is not None and v.get("mcap")]
    prem = [{"label": r["label"], "subject": r["subject"], "median": r["median"], "prem": r["prem"], "n": r["n"]} for r in (rc.valuation_compare(subj, pv) or []) if r.get("fmt") == "x" and r.get("prem") is not None] if subj and pv else []
    matrix = [{"symbol": s, **{k: v.get(k) for k in ("fpe", "pe", "ps", "pb", "evrev", "eveb", "gm", "rg", "mcap")}, "me": s == X.sym} for s, v in [(X.sym, subj)] + list(pv.items()) if v]
    return {"rows": rows, "bubble": bubble, "premium": prem, "matrix": matrix}


def _shortint(X: Ctx) -> dict[str, Any] | None:
    rows = X.D.get("finra_si")
    if not rows:
        return None
    fl = None
    try:
        fl = float(((X.D.get("keystats") or {}).get("defaultKeyStatistics") or {}).get("floatShares", {}).get("raw") or 0) or None
    except Exception:  # noqa: BLE001
        pass
    return {"rows": rows, "float": fl}


def _cone(X: Ctx) -> dict[str, Any] | None:
    closes = [c[4] for c in X.c if c[4]]
    if len(closes) < 120:
        return None
    yr = X.yr
    c = np.array(closes[-(yr + 1):], dtype=float)
    r = np.diff(np.log(c))
    rng = np.random.default_rng(7)
    H, N = 180, 5000
    paths = np.exp(np.cumsum(rng.choice(r, size=(N, H), replace=True) - r.mean(), axis=1)) * c[-1]
    now = float(c[-1])
    qs = np.percentile(paths, [10, 25, 50, 75, 90], axis=0)
    days = list(range(1, H + 1))
    hz = []
    for h in (7, 30, 90, 180):
        q = np.percentile(paths[:, h - 1], [10, 25, 50, 75, 90])
        hz.append({"h": h, "p10": q[0], "p25": q[1], "p50": q[2], "p75": q[3], "p90": q[4], "up": float((paths[:, h - 1] > now).mean())})
    p90 = paths[:, 89]
    return {"price": now, "unit": "days" if X.is_coin else "trading days", "vol": float(r.std() * np.sqrt(yr)), "horizons": hz, "days": days,
            "fan": {"p10": qs[0], "p25": qs[1], "p50": qs[2], "p75": qs[3], "p90": qs[4]},
            "odds": [{"label": "Up more than 20% in 90 days", "p": float((p90 > now * 1.2).mean())}, {"label": "Up more than 50% in 90 days", "p": float((p90 > now * 1.5).mean())},
                     {"label": "Down more than 20% in 90 days", "p": float((p90 < now * 0.8).mean())}, {"label": "Down more than 40% in 90 days", "p": float((p90 < now * 0.6).mean())}]}


def _perps(X: Ctx) -> dict[str, Any] | None:
    perps = (X.D.get("perps") or {}).get("rows") or []
    fs = rc.funding_summary(perps, X.sym)
    if not fs:
        return None
    top = sorted(perps, key=lambda r: -(r.get("oi_usd") or 0))[:12]
    return {"summary": fs, "top": top}


BUILDERS = {
    "fair": _fair, "flow": _flow, "formulas": _formulas, "rank": lambda X: X.D.get("xrank"), "peers": _peers, "shortint": _shortint, "options": lambda X: X.D.get("cboe"),
    "trials": lambda X: X.D.get("trials"), "recalls": lambda X: X.D.get("nhtsa"), "language": lambda X: X.D.get("sec_lang"), "contracts": lambda X: X.D.get("usasp"),
    "attention": lambda X: ({k: X.D.get(k) for k in ("wiki", "stwits", "hn", "gdelt") if X.D.get(k)} or None),
    "macro": lambda X: ({"global": X.D.get("global_macro"), "cot": X.D.get("cot"), "imf": X.D.get("imf"), "fred": ((X.D.get("macro") or {}).get("rows"))} if any(X.D.get(k) for k in ("global_macro", "cot", "imf")) else None),
    "ark": lambda X: X.D.get("ark"), "insiders": lambda X: (sorted(X.D.get("form4_tx") or [], key=lambda r: -r["date"])[:60] or None), "cone": _cone,
    "coinmkt": lambda X: X.D.get("coin"), "perps": _perps, "deribit": lambda X: X.D.get("deribit"), "llama": lambda X: X.D.get("llama"),
    "sentiment": lambda X: ({"fng": X.D.get("fng"), "trending": (X.D.get("trending") or {}).get("coins"), "global": X.D.get("cglobal")} if X.D.get("fng") or X.D.get("cglobal") else None),
}


def build(name: str, X: Ctx) -> Any:
    fn = BUILDERS[name]
    return clean(fn(X))
