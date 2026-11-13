"""Supply-chain cause graph (WORLD › SUPPLY CHAIN). The graph itself is data: app/world/supply_graph.json (nodes, edges with
approximate flow shares, chains). This module adds the live layer:

  status   chokepoint nodes: BMAP risk screen (headlines + nearby disasters) -> OPEN / ELEVATED / RESTRICTED, plus vessels
           waiting (stopped AIS ships) when AISSTREAM_API_KEY is set; producer / port nodes get ALERT when an EVENTS MAP impact
           event (storm, quake, hazard) touches their region
  moves    % change over N days for every node ticker (Yahoo daily closes)

Every live lookup runs in parallel with a hard timeout, so a slow feed only blanks its own part of the graph."""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
from pathlib import Path
from typing import Any

from app.api.capital_routes import _chart, cached

CONFIG = Path(__file__).with_name("supply_graph.json")
_POOL = cf.ThreadPoolExecutor(max_workers=24, thread_name_prefix="supply")
BUDGET = 8.0
# which EVENTS MAP impact regions put a node on ALERT
NODE_REGIONS = {"usgulf": ["US Gulf of Mexico", "Florida"], "sabine": ["US Gulf of Mexico"], "cushing": ["US Corn Belt"], "chile": ["Chile"], "peru": ["Peru"],
                "antofagasta": ["Chile", "Peru"], "tsmc": ["Taiwan"], "taiwanstrait": ["Taiwan"], "brazil": ["Brazil (coffee / sugar / soy belt)"],
                "usfarm": ["US Corn Belt"], "blacksea": ["Turkey / East Med"], "drc": ["Copperbelt (DRC / Zambia)"], "packaging": ["Korea"],
                "ningbo": ["South China / Hong Kong"], "raslaffan": ["Iran / Persian Gulf"], "rastanura": ["Iran / Persian Gulf"], "fujairah": ["Iran / Persian Gulf"],
                "ara": ["Europe (Rhine / Danube / France)"], "industry_eu": ["Europe (Rhine / Danube / France)"]}


def config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _move(sym: str, days: int) -> dict[str, Any] | None:
    q = cached(f"supply:q:{sym}", 300, lambda: _chart(sym, "6mo", "1d") or {})
    sp = q.get("spark") if q else None
    if not q or not sp:
        return None
    closes = sp  # last 24 daily closes; enough for 1-20 day moves
    n = min(days, len(closes) - 1)
    base = closes[-1 - n] if n > 0 else q.get("prev")
    return {"price": q["price"], "pct": (q["price"] / base - 1) * 100 if base else None, "d1": (q["price"] / q["prev"] - 1) * 100 if q.get("prev") else None}


def _choke() -> dict[str, Any]:
    from app.api import world_gx_routes as gx

    return {r["id"]: r for r in gx.api_chokepoints()["rows"]}


def _impact() -> list[dict[str, Any]]:
    from app.api import world_live_routes as wl

    return wl.api_all("day")["impact"]


def build(chain: str = "crude", days: int = 5) -> dict[str, Any]:
    cfg = config()
    if chain not in cfg["chains"]:
        raise KeyError(chain)
    nodes = [dict(n) for n in cfg["nodes"] if chain in n["chain"]]
    ids = {n["id"] for n in nodes}
    edges = [dict(e) for e in cfg["edges"] if e["chain"] == chain and e["from"] in ids and e["to"] in ids]
    syms = sorted({s for n in nodes for s in n["tickers"]})
    f_choke, f_imp = _POOL.submit(_choke), _POOL.submit(_impact)
    f_moves = {s: _POOL.submit(_move, s, days) for s in syms}
    cf.wait([f_choke, f_imp, *f_moves.values()], timeout=BUDGET)
    get = lambda f, d: f.result() if f.done() and f.exception() is None else d  # noqa: E731
    choke, impact = get(f_choke, {}), get(f_imp, [])
    moves = {s: get(f, None) for s, f in f_moves.items()}
    live = {"chokepoints": f_choke.done() and f_choke.exception() is None, "events": f_imp.done() and f_imp.exception() is None,
            "prices": sum(1 for v in moves.values() if v), "prices_total": len(syms), "risk": any(c.get("level") for c in choke.values())}
    for n in nodes:
        n["moves"] = [{"sym": s, **(moves.get(s) or {})} for s in n["tickers"]]
        prim = next((m for m in n["moves"] if m.get("pct") is not None), None)
        n["pct"] = round(prim["pct"], 2) if prim else None
        n["status"], n["why"], n["waiting"] = "OPEN", [], None
        c = choke.get(n.get("chokepoint") or "")
        if c:
            lvl = c.get("level")
            n["status"] = {"HIGH": "RESTRICTED", "ELEVATED": "ELEVATED"}.get(lvl or "", "OPEN" if lvl else "UNKNOWN")
            n["risk"] = c.get("risk")
            if c.get("ships"):
                n["waiting"] = c["ships"]["stopped"]
            n["why"] += [t["title"] for t in (c.get("top") or [])[:2]] + [f"⚠ {h}" for h in (c.get("hazards") or [])[:2]]
        regs = NODE_REGIONS.get(n["id"], [])
        hits = [r for r in impact if set(r.get("regions") or []) & set(regs)]
        if hits:
            if n["status"] == "OPEN":
                n["status"] = "ALERT"
            n["why"] += [f"{h['title']}" for h in hits[:2]]
    status = {n["id"]: n["status"] for n in nodes}
    for e in edges:
        e["status"] = status[e["from"]] if status[e["from"]] != "OPEN" else status[e["to"]] if status[e["to"]] in ("RESTRICTED",) else "OPEN"
    return {"chain": chain, "title": cfg["chains"][chain]["title"], "unit": cfg["chains"][chain]["unit"], "days": days, "nodes": nodes, "edges": edges,
            "chains": [{"id": k, "title": v["title"]} for k, v in cfg["chains"].items()], "live": live, "ts": time.time()}
