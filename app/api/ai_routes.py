"""News summaries for a security.

Two modes, always labelled in the response so the UI never passes one off as the other:
  * "llm": when AI_API_KEY (and optionally AI_BASE_URL / AI_MODEL) are set, the headlines and price move are sent to an
    OpenAI-compatible /chat/completions endpoint and the model writes the summary.
  * "extractive": no key configured. A rule-based digest: price move, headline tone counted from keywords, the most
    recent headlines grouped by theme. It reads the headlines; it does not understand them.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

from app.api import invest_routes as inv
from app.api.capital_routes import _chart, _yahoo_symbol, cached

router = APIRouter(prefix="/api/ai", tags=["ai"])

POS = ("beat", "beats", "surge", "soar", "jump", "rally", "record", "upgrade", "raises", "raised", "strong", "growth", "buy", "outperform", "approval", "wins", "gain")
NEG = ("miss", "misses", "plunge", "tumble", "fall", "drop", "slump", "downgrade", "cuts", "cut", "weak", "lawsuit", "probe", "recall", "warning", "sell", "layoff", "loss", "fraud")
THEMES = {
    "Earnings & guidance": ("earnings", "eps", "revenue", "guidance", "quarter", "profit", "forecast"),
    "Analysts & targets": ("analyst", "price target", "upgrade", "downgrade", "rating", "buy", "sell", "overweight"),
    "Products & deals": ("launch", "deal", "acquire", "acquisition", "partnership", "contract", "merger", "unveil"),
    "Legal & regulatory": ("lawsuit", "sec ", "probe", "regulator", "antitrust", "fine", "ban", "tariff"),
    "Macro & sector": ("fed", "rate", "inflation", "tariff", "sector", "market", "economy"),
}


def _llm_configured() -> bool:
    return bool(os.environ.get("AI_API_KEY"))


def _extractive(symbol: str, items: list[dict[str, Any]], px: dict[str, Any] | None) -> dict[str, Any]:
    heads = [i["title"] for i in items[:40]]
    pos = sum(any(w in h.lower() for w in POS) for h in heads)
    neg = sum(any(w in h.lower() for w in NEG) for h in heads)
    tone = "mostly positive" if pos > neg * 1.5 else "mostly negative" if neg > pos * 1.5 else "mixed"
    lines = []
    if px and px.get("price"):
        lines.append(f"{symbol} is at {px['price']:.2f} ({(px.get('pct') or 0):+.2f}% today).")
    lines.append(f"Of the {len(heads)} most recent headlines, {pos} read positive and {neg} negative: the tone is {tone}.")
    groups: dict[str, list[str]] = {}
    for h in heads:
        low = h.lower()
        for name, keys in THEMES.items():
            if any(k in low for k in keys):
                groups.setdefault(name, []).append(h)
                break
    bullets = [{"theme": k, "count": len(v), "examples": v[:2]} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:4]]
    return {"mode": "extractive", "text": " ".join(lines), "themes": bullets, "headlines_used": len(heads),
            "note": "Rule-based digest of headline keywords (no AI model configured). Set AI_API_KEY (and AI_BASE_URL / AI_MODEL for a non-OpenAI gateway) to get a written summary."}


def _llm(symbol: str, items: list[dict[str, Any]], px: dict[str, Any] | None) -> dict[str, Any]:
    base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")
    heads = "\n".join(f"- {i['title']} ({i.get('source', '')})" for i in items[:30])
    move = f"Price {px['price']:.2f}, {(px.get('pct') or 0):+.2f}% today." if px and px.get("price") else ""
    prompt = (f"You are a markets analyst. Summarise what is driving {symbol} from these headlines in 5 short bullets, then one line on the "
              f"main risk. Use only the headlines; say when they do not explain the price move. Do not give investment advice.\n{move}\n{heads}")
    r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {os.environ['AI_API_KEY']}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 450}, timeout=45)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AI gateway returned {r.status_code}: {r.text[:160]}")
    return {"mode": "llm", "model": model, "text": r.json()["choices"][0]["message"]["content"].strip(), "themes": [], "headlines_used": min(30, len(items)),
            "note": f"Written by {model} from the headlines above. It can be wrong: check the sources."}


@router.get("/summary")
def api_summary(symbol: str):
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-=^]{1,15}", sym):
        raise HTTPException(status_code=400, detail="bad symbol")

    def go() -> dict[str, Any]:
        items = inv._news(sym, True)
        if not items:
            raise HTTPException(status_code=404, detail="no recent headlines to summarise")
        try:
            c = _chart(_yahoo_symbol("stock", sym))
            px = {"price": c.get("price"), "pct": ((c["price"] / c["prev"] - 1) * 100) if c.get("prev") else None}
        except Exception:  # noqa: BLE001
            px = None
        if _llm_configured():
            try:
                return _llm(sym, items, px)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                out = _extractive(sym, items, px)
                out["note"] = f"AI gateway failed ({type(e).__name__}); showing the rule-based digest instead."
                return out
        return _extractive(sym, items, px)

    return cached(f"aisum:{sym}:{'llm' if _llm_configured() else 'x'}", 900, go)


@router.get("/status")
def api_status():
    return {"llm": _llm_configured(), "model": os.environ.get("AI_MODEL", "gpt-4o-mini") if _llm_configured() else None}


# ------------------------------------------------------------------ "AI <question>" command bar: navigate or answer
# Mirrors the codes in web/capital.js FUNCTIONS. Kept as a short list (not the full descriptions) since the model
# only needs enough to pick a destination, not to explain it to the user.
NAV_CODES = {
    "CAP": "capital home: accounts, portfolio, news, movers", "MKT": "world markets monitor", "STK": "stock monitor", "CRYPTO": "crypto monitor",
    "ETF": "ETFs and funds", "FX": "FX and commodities", "ECST": "macro dashboard (FRED)", "ECO": "economic calendar + blackout",
    "EVTS": "earnings calendar", "GC": "US Treasury yield curve", "WIRP": "Fed rate path", "EQS": "equity screener",
    "IMAP": "S&P 500 / Nasdaq heatmap", "COMP": "relative performance chart", "CORR": "correlation matrix", "SEAS": "seasonality",
    "COT": "CFTC futures positioning", "OVDV": "options vol surface / skew", "OVME": "options pricer, Greeks, payoff", "UOA": "unusual options activity",
    "PORT": "portfolio risk: beta, VaR, Sharpe", "ALRT": "price/news alerts to Telegram", "WKSP": "saved multi-panel workspace",
    "WFO": "walk-forward optimisation", "REGIME": "strategy results by market regime", "JRNL": "trade journal", "EXEC": "execution costs/blackout",
    "WORLD": "world globe: radio, planes, CCTV, quakes", "RADIO": "live radio stations", "PLANES": "live aircraft + military", "CAM": "public CCTV",
    "QUAKE": "earthquakes + hazards", "TV": "live news channels + geo headlines", "ONCHAIN": "fear/greed, funding, liquidations, BTC network",
    "ACADEMY": "Wyckoff replay trainer on BTC/ETH/SOL", "LIVE": "every live trade", "STRAT": "strategy library", "TRAIN": "champion training pipeline",
    "FIELD": "every candidate on one chart", "BT": "run a strategy backtest",
}
SEC_FNS = {"DES": "company description", "GP": "price chart", "FA": "financials", "N": "news", "ANR": "analyst ratings",
           "HDS": "holders", "FIL": "SEC filings", "DVD": "dividends", "OMON": "options", "RV": "peers", "DATA": "all data fields"}
# Actions the AI can perform ON the chart the user currently has open (web/gp.js's window.NX.chartAI), rather than
# navigate or answer in text. Deliberately a fixed, deterministic catalog: the AI's job is only to recognise which
# one the user wants (and, for mark_highs_lows, how sensitive) - the actual points are found by a plain swing-pivot
# algorithm in the browser from the real loaded candles, never guessed by the model from a wall of numbers.
CHART_ACTIONS = {
    "mark_highs_lows": "marks the swing highs and lows on the chart currently open",
    "mark_wyckoff": "finds and marks a Wyckoff accumulation (long) or distribution (short) setup on the chart "
                    "currently open: SC/BC, AR, ST, Spring/UT, SOS/SOW, LPS/LPSY, plus entry, stop and target",
    "clear_ai_marks": "removes marks the AI placed on the chart (never the user's own drawings)",
}
CHART_SENSITIVITY = ("tight", "normal", "wide")  # tight = more, smaller swings marked; wide = fewer, bigger ones
CHART_SIDE = ("long", "short", "either")  # which Wyckoff setup kind mark_wyckoff should look for


def _ai_command_prompt(question: str) -> str:
    nav = "\n".join(f"{c} — {d}" for c, d in NAV_CODES.items())
    fns = "\n".join(f"{c} — {d}" for c, d in SEC_FNS.items())
    return (
        "You are the command interpreter for a trading terminal called JORDAN TERMINAL. The user typed a request "
        "into the command line after \"AI\". Decide whether it is a request to go somewhere in the terminal, or a "
        "question you should just answer in text.\n\n"
        "If it is a navigation request, reply with exactly this JSON shape: "
        '{"type":"command","cmd":"<COMMAND>","note":"<one short sentence, said to the user, on what you are opening>"}\n'
        "<COMMAND> must be either one of these page codes on its own:\n" + nav + "\n\n"
        "...or, to open a specific security, the word SEC (or one of these function words) followed by a SPACE and "
        "then a ticker or company name — the ticker is REQUIRED, never send the function word alone, e.g. "
        "\"SEC AAPL\", \"GP TSLA\", \"FA MSFT\", \"N BTC\", \"OMON NVDA\":\n" + fns + "\n\n"
        "If the user asks you to mark, annotate or highlight something ON THE CHART that is currently open - "
        "rather than go somewhere else or answer in words - reply with exactly this JSON shape: "
        '{"type":"chart_action","action":"<ACTION>","sensitivity":"<tight|normal|wide, only for mark_highs_lows, '
        'default normal>","side":"<long|short|either, only for mark_wyckoff, default either>",'
        '"note":"<one short sentence on what you did>"}\n'
        "<ACTION> must be one of:\n" + "\n".join(f"{k} — {v}" for k, v in CHART_ACTIONS.items()) + "\n"
        "Use tight when the user says every/small/minor swing, wide when they say major/big/significant swings, "
        "otherwise normal. For mark_wyckoff, use side=long when the user asks for a long setup specifically, "
        "short for a short setup, else either. There is no chart action for drawing trendlines, support/resistance "
        "zones or anything not in that list yet - treat those as a general question instead.\n\n"
        "If the user asks you to recommend, find, or name a SPECIFIC tradable product matching some criteria you "
        "cannot verify live — \"give me a 3x leveraged crypto ETF\", \"find a cheap semiconductor ETF\", \"what's a "
        "good dividend stock\", \"a coin like X\" — do NOT name tickers from memory. You cannot confirm they still "
        "exist, trade under that name, or still match the criteria (leverage multiples and fund lineups change), and "
        "a wrong ticker or leverage figure on a real trading terminal is dangerous. Instead reply with exactly this "
        'JSON shape: {"type":"search","query":"<1-2 words to type into this terminal\'s own live security search>",'
        '"note":"<one short sentence: what you searched for and to check the real numbers before trading>"}\n'
        "The search matches a security's name against ALL the words you give it (not any of them), so pick only the "
        "one or two most essential words - one word is often best. \"bitcoin bull\", \"leveraged\", \"semiconductor\" "
        "and \"dividend\" all match real funds; \"3x leveraged crypto ETF\" matches nothing because no single fund "
        "name contains every one of those words.\n"
        "If it is a general question (market concept, what something means, opinion, anything that is not clearly "
        '\"take me to X\" and not a product recommendation), reply with exactly this JSON shape instead: '
        '{"type":"answer","text":"<your answer, a few sentences, plain text>"}\n'
        "Never invent a page code that is not in the lists above. Never give personalised investment advice — say so "
        "briefly and stick to explaining concepts if asked for a recommendation. Reply with JSON only, nothing else.\n\n"
        f"User request: {question}"
    )


@router.get("/command")
def api_ai_command(q: str):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty question")
    if not _llm_configured():
        return {"type": "answer", "text": "AI is not connected yet: no AI_API_KEY is set on the server.", "note": "not configured"}
    base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")
    try:
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {os.environ['AI_API_KEY']}"},
                          json={"model": model, "messages": [{"role": "user", "content": _ai_command_prompt(q)}],
                                "temperature": 0.1, "max_tokens": 350, "response_format": {"type": "json_object"}}, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI gateway unreachable: {type(e).__name__}") from e
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AI gateway returned {r.status_code}: {r.text[:160]}")
    try:
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        return {"type": "answer", "text": r.json()["choices"][0]["message"]["content"].strip()[:1200]}
    if out.get("type") == "command":
        cmd = str(out.get("cmd", "")).strip().upper()
        note = str(out.get("note", ""))[:200]
        parts = cmd.split(" ", 1)
        head, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if head in NAV_CODES:
            return {"type": "command", "cmd": head, "note": note}  # a page code takes no argument: drop anything stray after it
        if head in SEC_FNS or head == "SEC":
            rest_tokens = rest.split()
            # the model sometimes tacks a second, more specific function word onto the end ("SEC AAPL GP"): prefer it
            if rest_tokens and rest_tokens[-1] in SEC_FNS and rest_tokens[-1] != head:
                if head == "SEC":
                    head = rest_tokens[-1]
                rest_tokens = rest_tokens[:-1]
            rest = " ".join(rest_tokens)
            if not rest:
                return {"type": "answer", "text": "Which security did you mean? Try again with a ticker or company name, e.g. \"AI show apple's chart\"."}
            return {"type": "command", "cmd": f"{head} {rest}", "note": note}
        return {"type": "answer", "text": f"I'd want to open \"{cmd}\", but that isn't a page this terminal has.", "note": "invalid command"}
    if out.get("type") == "search":
        query = str(out.get("query", "")).strip()[:80]
        if not query:
            return {"type": "answer", "text": "I couldn't tell what to search for — try rephrasing."}
        return {"type": "search", "query": query, "note": str(out.get("note", ""))[:200]}
    if out.get("type") == "chart_action":
        action = str(out.get("action", "")).strip().lower()
        if action not in CHART_ACTIONS:
            return {"type": "answer", "text": f"I'd want to \"{action or 'do that'}\" on the chart, but that isn't something I can do there yet."}
        sensitivity = str(out.get("sensitivity", "normal")).strip().lower()
        if sensitivity not in CHART_SENSITIVITY:
            sensitivity = "normal"
        side = str(out.get("side", "either")).strip().lower()
        if side not in CHART_SIDE:
            side = "either"
        return {"type": "chart_action", "action": action, "sensitivity": sensitivity, "side": side, "note": str(out.get("note", ""))[:200]}
    return {"type": "answer", "text": str(out.get("text", "")).strip()[:1200] or "No answer."}
