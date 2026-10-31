"""SPIDER — the news reader that crawls the NEWS page.

It reads every headline on the market wire (all publishers) plus the critical events, and marks what matters:
  1. rules (instant, always on): source weight + trigger words + tickers + freshness -> CRITICAL / IMPORTANT / normal,
     with the words that triggered it (the page outlines them);
  2. brain (the configured language model, every few minutes): reads the top headlines together, picks what really
     moves markets, says why and which assets, and writes a 3-line brief. Its picks override the rules.
You can also ask it questions: it answers from what it has read (headlines, events, this week's calendar), citing them.
Without a language model it still marks by rules and answers by keyword search.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from app.intel import llm

logger = logging.getLogger(__name__)

# trigger words: (pattern, weight). Tier 1 moves everything; tier 2 moves a sector or an asset.
_T1 = r"fed|fomc|powell|rate (?:cut|hike)s?|cuts? rates|hikes? rates|emergency|cpi|inflation|payrolls|jobs report|nonfarm|gdp|recession|" \
      r"default|halt(?:ed)?|crash(?:es)?|plunge[sd]?|war|missile|invasion|nuclear|ceasefire|sanctions?|tariffs?|ecb|lagarde|boj|pboc|bank of england|" \
      r"circuit breaker|bank run|bailout|shutdown|downgrade[sd]?"
_T2 = r"earnings|guidance|revenue|profit warning|beats?|miss(?:es|ed)?|upgrade[sd]?|opec|oil|crude|gold|bitcoin|ether|etf|merger|acquisitions?|" \
      r"buyout|takeover|ipo|bankrupt(?:cy)?|layoffs?|antitrust|probe|lawsuit|sec |treasury|yields?|bonds?|dollar|yen|yuan|surge[sd]?|soar(?:s|ed)?|" \
      r"tumble[sd]?|slump[sd]?|rall(?:y|ies|ied)|record high|all-time high|selloff|sell-off"
RE_T1 = re.compile(rf"\b({_T1})\b", re.I)
RE_T2 = re.compile(rf"\b({_T2})\b", re.I)
RE_TICK = re.compile(r"\$[A-Z]{1,5}\b|\(([A-Z]{1,5})\)")
SRC_W = {"FED": 3, "ECB": 3, "BLOOMBERG": 1, "FT": 1, "CNBC ECONOMY": 1}

_state: dict[str, Any] = {"brain": None, "brain_at": 0.0, "running": False}
_lock = threading.Lock()
BRAIN_TTL = 600  # one model pass every 10 minutes at most (free-tier friendly)


def _news() -> list[dict[str, Any]]:
    from app.api.capital_routes import _build_news, cached
    return cached("news", 240, _build_news)


def _events() -> list[dict[str, Any]]:
    try:
        from app.intel import events
        return events.recent(48, 2)
    except Exception:  # noqa: BLE001
        return []


def _rules(it: dict[str, Any], now: float) -> dict[str, Any]:
    title = it.get("title", "")
    t1 = {m.group(0) for m in RE_T1.finditer(title)}
    t2 = {m.group(0) for m in RE_T2.finditer(title)}
    ticks = {m.group(0) for m in RE_TICK.finditer(title)}
    age_h = max(0.0, (now - (it.get("ts") or now)) / 3600)
    score = 3 * len(t1) + 1.5 * len(t2) + len(ticks) + SRC_W.get(it.get("source", ""), 0) + (1 if it.get("tag") == "CB" else 0)
    score *= 1.0 if age_h < 2 else 0.8 if age_h < 8 else 0.6
    level = "critical" if score >= 5 else "important" if score >= 2.5 else ""
    return {"score": round(score, 2), "level": level, "words": sorted(t1 | t2 | ticks, key=len, reverse=True)[:6]}


def _brain_pass(items: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    """One language-model read of the top headlines (runs in a background thread, result cached)."""
    try:
        top = sorted(items, key=lambda i: -i["score"])[:70]
        lines = "\n".join(f"{n}. [{i['source']}] {i['title']}" for n, i in enumerate(top))
        ev = "\n".join(f"- {e.get('title', '')}" for e in events[:15])
        system = ("You are a markets news desk editor. From the numbered headlines pick the ones that can move markets "
                  "(index futures, rates, FX, oil, gold, crypto, big single stocks). Ignore lifestyle, opinion, personal finance and "
                  "routine local news. Answer only JSON: {\"brief\": [3 short lines: what matters right now], "
                  "\"picks\": [{\"n\": headline number, \"level\": \"critical\"|\"important\", \"why\": \"<=14 words\", "
                  "\"assets\": [\"tickers or assets affected\"], \"words\": [\"1-3 exact words from the headline that carry the news\"]}]} "
                  "with at most 18 picks, critical only for things that move the whole market today.")
        out = llm.chat_json(system, f"HEADLINES:\n{lines}\n\nCRITICAL EVENTS (48h):\n{ev or '-'}", max_tokens=1600, reasoning="low")
        if out and isinstance(out.get("picks"), list):
            picks = {}
            for p in out["picks"]:
                try:
                    i = top[int(p.get("n"))]
                except (TypeError, ValueError, IndexError):
                    continue
                picks[i["link"]] = {"level": "critical" if p.get("level") == "critical" else "important", "why": str(p.get("why", ""))[:160],
                                    "assets": [str(a)[:16] for a in (p.get("assets") or [])][:5],
                                    "words": [str(w)[:40] for w in (p.get("words") or []) if str(w).lower() in i["title"].lower()][:3]}
            _state["brain"] = {"brief": [str(b)[:200] for b in (out.get("brief") or [])][:4], "picks": picks, "at": time.time()}
    except Exception as e:  # noqa: BLE001
        logger.warning("spider brain pass failed: %s", type(e).__name__)
    finally:
        _state["brain_at"] = time.time()
        _state["running"] = False


def scan() -> dict[str, Any]:
    now = time.time()
    raw = _news()
    items = []
    for it in raw:
        r = _rules(it, now)
        items.append({"title": it["title"], "link": it["link"], "source": it["source"], "tag": it["tag"], "ts": it["ts"], **r})
    events = _events()
    brain = _state["brain"]
    if llm.config() and not _state["running"] and now - _state["brain_at"] > BRAIN_TTL:
        with _lock:
            if not _state["running"]:
                _state["running"] = True
                threading.Thread(target=_brain_pass, args=(items, events), daemon=True, name="spider-brain").start()
    if brain:
        for it in items:
            p = brain["picks"].get(it["link"])
            if p:
                it.update(level=p["level"], why=p["why"], assets=p["assets"], brain=True,
                          words=p["words"] or it["words"], score=it["score"] + (10 if p["level"] == "critical" else 5))
            elif it["level"] and it["score"] < 6:
                it["level"] = ""  # the brain read it and passed: only keep a rules mark when it is very strong
    marked = sorted((i for i in items if i["level"]), key=lambda i: (-i["score"], -i["ts"]))
    return {
        "items": items, "marked": marked[:40], "brief": brain["brief"] if brain else [],
        "read": len(items), "critical": sum(1 for i in items if i["level"] == "critical"),
        "important": sum(1 for i in items if i["level"] == "important"),
        "events": [{"title": e.get("title"), "severity": e.get("severity"), "kind": e.get("kind"), "ts": e.get("ts")} for e in events[:12]],
        "brain": bool(brain), "brain_at": brain["at"] if brain else None, "thinking": _state["running"], "llm": bool(llm.config()), "at": now,
    }


def _wire_item(url: str) -> dict[str, Any] | None:
    return next((i for i in _news() if i["link"] == url), None)


def article(url: str) -> dict[str, Any]:
    """The full text of a news link (see app/intel/article.py), with what the wire already knows about it."""
    from app.intel import article as art
    it = _wire_item(url)
    a = art.fetch(url, it)
    if it:
        r = _rules(it, time.time())
        p = (_state["brain"] or {}).get("picks", {}).get(url)
        a["level"], a["why"] = (p["level"], p["why"]) if p else (r["level"], None)
    return a


_reads: dict[str, tuple[float, dict[str, Any]]] = {}


def _rules_read(paras: list[dict[str, str]]) -> dict[str, Any]:
    """No model: mark sentences that carry trigger words, with numbers counting extra."""
    hl = []
    for n, p in enumerate(paras):
        for sent in re.split(r"(?<=[.!?])\s+", p["t"]):
            t1, t2 = len(RE_T1.findall(sent)), len(RE_T2.findall(sent))
            score = 3 * t1 + 1.5 * t2 + (1 if re.search(r"\d", sent) else 0)
            if score >= 3 and 20 < len(sent) < 400:
                hl.append({"p": n, "text": sent, "level": "critical" if score >= 6 else "important", "why": "trigger words", "score": score})
    hl.sort(key=lambda h: -h["score"])
    return {"summary": None, "points": [], "impact": None, "assets": [], "highlights": hl[:8], "brain": False}


def read_article(url: str, force: bool = False) -> dict[str, Any]:
    """SPIDER reads the whole article: a short summary, the key points, what it could move, and the exact sentences that
    matter (each tied to its paragraph so the page can mark them). Remembered in the database (spider_reads), so an
    article is read by the brain once; `force` (the background reader) skips the in-memory shortcut only."""
    from app.intel import spider_memory as mem
    hit = _reads.get(url)
    if hit and time.time() - hit[0] < 3600 and not force:
        return hit[1]
    old = mem.get_read(url)
    if old and old.get("brain"):
        _reads[url] = (time.time(), old)
        return old
    a = article(url)
    paras = a["paragraphs"]
    if not paras:
        return {"summary": None, "points": [], "impact": None, "assets": [], "highlights": [], "brain": False, "partial": True}
    out = _rules_read(paras)
    if llm.config():
        budget, lines = 11000, []
        for n, p in enumerate(paras):
            t = p["t"][:900]
            if budget - len(t) < 0:
                break
            budget -= len(t)
            lines.append(f"[{n}] {t}")
        system = ("You are SPIDER, the news brain of a trading terminal. Read the whole article (numbered paragraphs) like a markets "
                  "desk analyst. Answer only JSON: {\"summary\": \"2-3 sentences: what happened\", \"points\": [up to 5 short key facts, "
                  "numbers kept exact], \"impact\": \"1-2 sentences: what this could move in markets and which way, or 'little market "
                  "impact' if so\", \"assets\": [tickers/assets affected], \"level\": \"critical\"|\"important\"|\"minor\", "
                  "\"highlights\": [{\"p\": paragraph number, \"text\": \"an EXACT sentence or phrase copied from that paragraph\", "
                  "\"level\": \"critical\"|\"important\", \"why\": \"<=12 words\"}]} with 3 to 8 highlights: the sentences a trader "
                  "must not miss. Facts only from the article; no advice to buy or sell.")
        head = f"TITLE: {a.get('title')}\nSOURCE: {a.get('source')}\n{'(only part of the article was available)' if a.get('partial') else ''}\n\n"
        res = llm.chat_json(system, head + "\n".join(lines), max_tokens=1800, reasoning="low")
        if res:
            hls = []
            for h in res.get("highlights") or []:
                try:
                    n = int(h.get("p"))
                    ptxt = paras[n]["t"]
                except (TypeError, ValueError, IndexError):
                    continue
                txt = str(h.get("text") or "").strip().strip('"')
                i = ptxt.lower().find(txt.lower()) if txt else -1
                if i < 0 and len(txt) > 40:  # the model shortened it: anchor on its first words
                    i = ptxt.lower().find(txt[:40].lower())
                    txt = ptxt[i:i + len(txt)] if i >= 0 else txt
                if i < 0:
                    continue
                hls.append({"p": n, "text": ptxt[i:i + len(txt)], "level": "critical" if h.get("level") == "critical" else "important",
                            "why": str(h.get("why") or "")[:120]})
            out = {"summary": str(res.get("summary") or "")[:600] or None, "points": [str(x)[:220] for x in (res.get("points") or [])][:5],
                   "impact": str(res.get("impact") or "")[:400] or None, "assets": [str(x)[:16] for x in (res.get("assets") or [])][:6],
                   "level": res.get("level") if res.get("level") in ("critical", "important", "minor") else None,
                   "highlights": hls[:8] or out["highlights"], "brain": True}
    out["partial"] = a.get("partial")
    out["words"] = a.get("words")
    _reads[url] = (time.time(), out)
    try:
        mem.save_article(a)
        mem.save_read(url, a, out)
    except Exception:  # noqa: BLE001
        logger.warning("spider: couldn't store the read of %s", url)
    if len(_reads) > 200:
        for k in sorted(_reads, key=lambda k: _reads[k][0])[:60]:
            _reads.pop(k, None)
    return out


RE_STOCK_ASK = re.compile(r"^\s*(?:full\s+)?(?:report|analy[sz]e|analysis|research|deep dive|what do you think (?:of|about)|your view on|view on|thoughts on|should i (?:buy|sell|hold))\s+(?:on\s+|of\s+)?\$?([A-Za-z][A-Za-z.\-]{0,9})\s*\??\s*$", re.I)


def ask(question: str, url: str | None = None) -> dict[str, Any]:
    q = (question or "").strip()[:500]
    if not q:
        raise ValueError("ask me something")
    m = RE_STOCK_ASK.match(q)
    if m and not url:  # "report NVDA", "what do you think about TSLA": the full stock report
        sym = m.group(1).upper()
        return {"answer": f"Building the full SPIDER report on {sym}: price and trend, financials, analysts, the latest filings and the news, read together. It opens in its own window.",
                "refs": [], "brain": True, "report": sym}
    out = _ask(q, url)
    try:
        from app.intel import spider_memory as mem
        mem.save_qa(q, url, out.get("answer", ""), out.get("refs") or [])
    except Exception:  # noqa: BLE001
        pass
    return out


def _ask(q: str, url: str | None) -> dict[str, Any]:
    if url and llm.config():  # a question about the article that is open
        a = article(url)
        text = "\n".join(f"[{n}] {p['t'][:900]}" for n, p in enumerate(a["paragraphs"]))[:11000]
        system = ("You are SPIDER, the news brain of a trading terminal. The user is reading the article below. Answer the question "
                  "from it (add general market knowledge only where needed, and say so). Be direct: at most 6 lines. If the article "
                  "doesn't say, say that. No advice to buy or sell. Answer only JSON: {\"answer\": \"text\"}.")
        out = llm.chat_json(system, f"ARTICLE: {a.get('title')} ({a.get('source')})\n{text}\n\nQUESTION: {q}", max_tokens=800, temperature=0.2, reasoning="low")
        if out and out.get("answer"):
            return {"answer": str(out["answer"]).strip()[:2000], "refs": [], "brain": True}
    s = scan()
    items = sorted(s["items"], key=lambda i: -i["ts"])[:120]
    if llm.config():
        try:
            from app.intel import calendar
            cal = calendar.view()
            cal_lines = "\n".join(f"- {e.get('name')} · {time.strftime('%a %H:%M', time.localtime(e.get('ts', 0)))} · fcst {e.get('forecast')} prev {e.get('previous')} actual {e.get('actual')}"
                                  for e in (cal.get("events") or [])[:25])
        except Exception:  # noqa: BLE001
            cal_lines = "-"
        heads = "\n".join(f"{n}. [{time.strftime('%d %b %H:%M', time.localtime(i['ts']))} · {i['source']}{' · ' + i['level'].upper() if i['level'] else ''}] {i['title']}"
                          for n, i in enumerate(items))
        ev = "\n".join(f"- {e['title']}" for e in s["events"])
        try:
            from app.intel import spider_memory as mem
            past = mem.recall(q, days=14, n=8)
        except Exception:  # noqa: BLE001
            past = []
        memory = "\n".join(f"- [{time.strftime('%d %b', time.localtime(r['read_at']))} · {r['source']}] {r['title']}: {r['summary'] or ''} {('IMPACT: ' + r['impact']) if r['impact'] else ''}"
                           for r in past)
        system = ("You are SPIDER, the news brain of a trading terminal. You have read the headlines below and, under MEMORY, full articles you read earlier. Answer the user's question "
                  "using them (and general market knowledge where needed; say so). Be direct and short: at most 6 lines. If the "
                  "headlines do not cover it, say that. Never give personal investment advice or tell the user to buy or sell; you "
                  "explain what the news says and what it could affect. Do not write headline numbers in the answer text (put them in refs). Answer only JSON: {\"answer\": \"text\", \"refs\": [headline numbers you used]}.")
        out = llm.chat_json(system, f"QUESTION: {q}\n\nHEADLINES (newest first):\n{heads}\n\nCRITICAL EVENTS:\n{ev or '-'}\n\nTHIS WEEK'S CALENDAR:\n{cal_lines}\n\nMEMORY (articles read in full):\n{memory or '-'}",
                            max_tokens=900, temperature=0.2, reasoning="low")
        if out and out.get("answer"):
            refs = []
            for n in out.get("refs") or []:
                try:
                    refs.append(items[int(n)])
                except (TypeError, ValueError, IndexError):
                    pass
            answer = re.sub(r"\s*[\(\[]\s*#?\d+(?:\s*[,;&]\s*#?\d+)*\s*[\)\]]", "", str(out["answer"]))  # sources are listed under it
            return {"answer": answer.strip()[:2000], "refs": refs[:8], "brain": True}
    # no model: keyword search over what was read
    words = [w for w in re.findall(r"[a-z0-9$]{3,}", q.lower()) if w not in {"what", "the", "about", "with", "news", "today", "how", "why", "any", "and"}]
    hits = [i for i in items if any(w in i["title"].lower() for w in words)][:8]
    return {"answer": f"I found {len(hits)} headline{'s' if len(hits) != 1 else ''} about that." if hits else "I haven't read anything about that yet.",
            "refs": hits, "brain": False}
