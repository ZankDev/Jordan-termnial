"""ORDER-FLOW SIM API (prefix /api/sim): the AI coach review of a practice session, and saved sessions for progress over time.
The market itself is generated in the browser (web/sim_engine.js, seeded); nothing here touches a real account."""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app import db

router = APIRouter(prefix="/api/sim", tags=["sim"])

COACH_SYSTEM = (
    "You are a strict, encouraging futures trading coach who specialises in order flow (tape, DOM, delta, CVD, absorption, stop runs, liquidity), "
    "dealer gamma (call wall, put wall, zero gamma, positive vs negative gamma regimes), VWAP and ICT-style structure (liquidity sweeps, FVG). "
    "You are given a practice session on a simulated E-mini S&P market: the trader's stats, every trade with its context and the mistakes the grader "
    "found, the setups detected, what the market was really doing (the hidden script) and the opportunities taken or missed.\n"
    "Write a review that a trader can act on tomorrow. Be specific: cite trade numbers, times, prices and R. Judge PROCESS over P&L: a losing trade "
    "with a good plan is fine, a winner from a bad process is a warning. Name the trader's real setup (what they keep doing) and whether it has an edge "
    "in this sample (say when the sample is too small). Never give financial advice about real markets.\n"
    'Answer JSON only: {"headline":"one sentence verdict","summary":"3-4 sentences","wrong":["specific mistake with trade numbers", "..."] (max 6),'
    '"right":["specific thing done well", "..."] (max 4),"setup":"the trader\'s setup and whether it worked here","rules":["a concrete rule for the next session", "..."] (max 5),'
    '"drills":["a drill to practise in the simulator", "..."] (max 3),"focus":"the single thing to focus on next session"}'
)


def _ensure() -> None:
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sim_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, seed INTEGER, scenario TEXT, "
                     "minutes INTEGER, net REAL, trades INTEGER, win REAL, grade REAL, exp_r REAL, data TEXT)")


@router.post("/coach")
def api_coach(body: dict[str, Any] = Body(...)):
    from app.intel import llm

    if not llm.config():
        raise HTTPException(status_code=503, detail="no AI model configured")
    lang = body.get("lang") or "en"
    payload = json.dumps({k: v for k, v in body.items() if k != "lang"}, ensure_ascii=False)[:14000]
    user = ("Write the review in Hebrew (natural trader Hebrew; keep terms like VWAP, CVD, delta, FVG, gamma in English).\n" if lang == "he" else "") + "SESSION:\n" + payload
    out = llm.chat_json(COACH_SYSTEM, user, max_tokens=3000, temperature=0.2, model="openai/gpt-oss-120b", reasoning="medium")
    if not isinstance(out, dict):
        raise HTTPException(status_code=502, detail="the coach did not answer")
    clean = lambda xs, n: [str(x)[:400] for x in (xs or []) if x][:n]  # noqa: E731
    return {"headline": str(out.get("headline", ""))[:300], "summary": str(out.get("summary", ""))[:1200], "wrong": clean(out.get("wrong"), 6),
            "right": clean(out.get("right"), 4), "setup": str(out.get("setup", ""))[:600], "rules": clean(out.get("rules"), 5), "drills": clean(out.get("drills"), 3),
            "focus": str(out.get("focus", ""))[:300]}


@router.post("/sessions")
def api_save(body: dict[str, Any] = Body(...)):
    _ensure()
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO sim_sessions (ts, seed, scenario, minutes, net, trades, win, grade, exp_r, data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (int(time.time()), body.get("seed"), body.get("scenario"), body.get("minutes"), body.get("net"), body.get("trades"), body.get("win"),
                            body.get("grade"), body.get("expR"), json.dumps({"mistakes": body.get("mistakes"), "setups": body.get("setups")})[:20000]))
        sid = cur.lastrowid
    return {"ok": True, "id": sid}


@router.get("/sessions")
def api_sessions(limit: int = 50):
    _ensure()
    with db.read_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM sim_sessions ORDER BY ts DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()]
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
    return {"sessions": rows}
