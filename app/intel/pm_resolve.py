"""Resolution-lag finder: Polymarket markets whose question is about something that ALREADY happened (or not), where the price has not caught up.

Scope (where evidence can be read reliably without a language model): the common date-specific templates
  "<A> military action against <B> on <date>?"   (one market per day; A = actor, B = target)
  "<A> x <B> ceasefire ..."  is skipped (needs judgement)
For each open market whose day has passed:
  1. Evidence: our own news stream (Telegram squawk/geo/Israel channels + RSS) and the GDELT news archive for that day (plus a 30 h reporting lag),
     filtered to headlines that name both sides.
  2. Each headline is read against the market's rules with simple, explicit patterns:
       strong   "<A> jets / warplanes / air force / airstrikes ... <B>", "<A> strikes / bombs / launched missiles ..."      +2
       weak     "<A>-led coalition ..."  (only counts if A's own forces struck)                                              +1
       against  "<A>-backed / allied / proxy forces"  (proxies do not count),  "<B> attacks <A>" (wrong direction)            -2 / 0
  3. Verdict: YES-evidence (>= 2 strong headlines from different outlets, on that day in the rule's time zone) vs the market's YES price.
     NO is only suggested weakly (absence of evidence is not evidence) when coverage of the conflict that day is good and nothing qualifies.
Every flag is paper-ledgered (pm_opps, kind 'resolution') and settled when the market resolves, so the hit rate is measured, not assumed.
Nothing is ever sent to the real Polymarket trader.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
TEMPLATE = re.compile(r"^(?P<a>.+?)\s+military action against\s+(?P<b>.+?)\s+on", re.I)
TZ = {"yemen": 3, "iran": 3.5, "israel": 3, "lebanon": 3, "syria": 3, "iraq": 3, "russia": 3, "ukraine": 3, "eastern": -4, " et": -4, "utc": 0, "gmt": 0}
ALIASES = {"saudi arabia": ["saudi", "saudis", "riyadh", "royal saudi"], "yemen": ["yemen", "houthi", "houthis", "sanaa", "sana'a", "hodeidah", "aden", "marib", "saada", "amran"],
           "israel": ["israel", "israeli", "idf"], "iran": ["iran", "iranian", "tehran"], "lebanon": ["lebanon", "lebanese", "hezbollah", "beirut"],
           "united states": ["us ", "u.s.", "american", "pentagon", "centcom"], "us": ["us ", "u.s.", "american", "pentagon", "centcom"], "syria": ["syria", "syrian", "damascus"],
           "iraq": ["iraq", "iraqi", "baghdad"], "russia": ["russia", "russian", "moscow"], "ukraine": ["ukraine", "ukrainian", "kyiv"]}
STRIKE = r"(air ?strikes?|strikes?|struck|bomb(s|ed|ing)?|raid(s|ed)?|missiles?|drones?|warplanes?|jets?|fighter|air force|pound(s|ed)?|target(s|ed)?|hit|attack(s|ed)?)"
SCHEMA = ("CREATE TABLE IF NOT EXISTS pm_evidence (market_id TEXT, day TEXT, source TEXT, ts INTEGER, title TEXT, url TEXT, score INTEGER, why TEXT, "
          "PRIMARY KEY (market_id, url))",
          "CREATE TABLE IF NOT EXISTS pm_gdelt_done (market_id TEXT PRIMARY KEY, at INTEGER)")


def ensure() -> None:
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)


def names(side: str) -> list[str]:
    s = side.strip().lower()
    return ALIASES.get(s, [s.split()[0]])


def tz_offset(rules: str) -> float:
    r = (rules or "").lower()
    return next((v for k, v in TZ.items() if f"{k} standard time" in r or f"{k} time" in r or (k.strip() in ("et",) and " et" in r)), 0.0)


def day_of(label: str, end_iso: str) -> dt.date | None:
    """'October 6' + the market's end date year -> date."""
    try:
        end = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        d = dt.datetime.strptime(f"{label.strip()} {end.year}", "%B %d %Y").date()
        return d if d <= end.date() else d.replace(year=end.year - 1)
    except (ValueError, AttributeError):
        return None


def score_headline(text: str, a_names: list[str], b_names: list[str]) -> tuple[int, str]:
    t = " " + text.lower() + " "
    has_a = any(n in t for n in a_names)
    has_b = any(n in t for n in b_names)
    if not (has_a and has_b) or not re.search(STRIKE, t):
        return 0, ""
    a_alt = "|".join(re.escape(n.strip()) for n in a_names)
    b_alt = "|".join(re.escape(n.strip()) for n in b_names)
    if re.search(rf"({a_alt})[- ](backed|allied|aligned|supported)|proxy|proxies|government forces|pro-government", t):
        return -2, "proxy / backed forces (do not count)"
    # wrong direction: B is the one striking A ("Houthis attack Riyadh airport")
    if re.search(rf"({b_alt})\b[^.]{{0,40}}\b(attack|strike|struck|missile|drone|target|hit)[^.]{{0,40}}({a_alt})", t) and not re.search(rf"({a_alt})\b[^.]{{0,30}}(jets|warplanes|air force|airstrikes|bombed|strikes on|struck)", t):
        return 0, "wrong direction (target attacking the actor)"
    if re.search(rf"({a_alt})[- ]led coalition", t):
        return 1, "coalition strike (counts only if the actor's own forces struck)"
    if re.search(rf"({a_alt})\b[^.]{{0,25}}(jets|warplanes|fighter|air force|aircraft|airstrikes?|air strikes?|bombed|bombs|bombing|pounded|raids?|launched (missiles|strikes|airstrikes))", t) \
            or re.search(rf"(airstrikes?|air strikes?|bombing|raids?)[^.]{{0,25}}\bby\b[^.]{{0,10}}({a_alt})", t):
        return 2, "actor's own air / missile strike"
    return 1, "mentions both sides and a strike (direction unclear)"


def our_news(a: list[str], b: list[str], start: int, end: int) -> list[dict[str, Any]]:
    with db.read_conn() as conn:
        rows = conn.execute("SELECT ts, source, text, link FROM news_items WHERE ts BETWEEN ? AND ?", (start, end)).fetchall()
    out = []
    for r in rows:
        sc, why = score_headline(r["text"], a, b)
        if sc or why:
            out.append({"source": r["source"].split(":")[-1], "ts": r["ts"], "title": r["text"][:300], "url": r["link"] or f"news:{r['ts']}:{hash(r['text']) % 10**8}", "score": sc, "why": why})
    return out


def gdelt(a_side: str, b_side: str, a: list[str], b: list[str], start: int, end: int) -> list[dict[str, Any]]:
    q = f'({" OR ".join(set(x.strip() for x in a[:2]))}) ({" OR ".join(set(x.strip() for x in b[:3]))}) (airstrike OR airstrikes OR strike OR strikes OR bombed OR raids OR jets) sourcelang:english'
    fmt = lambda t: time.strftime("%Y%m%d%H%M%S", time.gmtime(t))  # noqa: E731
    for attempt in range(3):
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params={"query": q, "mode": "artlist", "format": "json", "maxrecords": 60, "startdatetime": fmt(start),
                                                                                   "enddatetime": fmt(end), "sort": "datedesc"}, headers=UA, timeout=40)
        if r.status_code == 429 or not r.text.startswith("{"):
            time.sleep(8 + attempt * 6)
            continue
        out = []
        for art in r.json().get("articles", []):
            sc, why = score_headline(art.get("title", ""), a, b)
            try:
                ts = int(dt.datetime.strptime(art["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
            except (KeyError, ValueError):
                ts = start
            out.append({"source": art.get("domain", "gdelt"), "ts": ts, "title": art.get("title", "")[:300], "url": art.get("url", ""), "score": sc, "why": why or "no qualifying wording"})
        return out
    return []


def candidates(min_vol: float = 1000) -> list[dict[str, Any]]:
    """Open markets of the 'A military action against B on <date>' template whose day is over (in the rule's time zone)."""
    r = requests.get("https://gamma-api.polymarket.com/events", params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": 500},
                     headers=UA, timeout=30)
    r.raise_for_status()
    now = time.time()
    out = []
    for ev in r.json():
        m0 = TEMPLATE.match(str(ev.get("title", "")))
        if not m0:
            continue
        rules = ev.get("description") or ""
        off = tz_offset(rules)
        for m in ev.get("markets") or []:
            if m.get("closed"):
                continue
            day = day_of(m.get("groupItemTitle") or "", m.get("endDate") or ev.get("endDate") or "")
            if not day:
                continue
            day_start = dt.datetime.combine(day, dt.time()).replace(tzinfo=dt.timezone.utc).timestamp() - off * 3600
            day_end = day_start + 86400
            if now < day_end + 2 * 3600:  # the day must be over (+2 h for first reports)
                continue
            try:
                p = m.get("outcomePrices")
                yes = float((json.loads(p) if isinstance(p, str) else p)[0])
            except Exception:  # noqa: BLE001
                continue
            if float(m.get("volumeNum") or 0) < min_vol or not 0.03 < yes < 0.97:
                continue
            out.append({"event": ev["title"], "slug": ev["slug"], "market_id": str(m["id"]), "label": m.get("groupItemTitle"), "day": day.isoformat(), "start": int(day_start), "end": int(day_end),
                        "a": m0.group("a").strip(), "b": m0.group("b").strip(), "yes": yes, "bid": m.get("bestBid"), "ask": m.get("bestAsk"), "volume": round(float(m.get("volumeNum") or 0)),
                        "rules_excerpt": rules[:400], "rules": rules[:4000], "tz": off})
    return out


def judge(c: dict[str, Any], use_gdelt: bool = True) -> dict[str, Any]:
    ensure()
    a, b = names(c["a"]), names(c["b"])
    # reports of a day's strikes often appear hours later: read up to 30 h after the day ends, but count a headline for THIS day only if it was
    # published inside the day or names the weekday/date (kept simple: headlines after the day are 'late reports', shown but weighted 0.5)
    ev = our_news(a, b, c["start"], c["end"] + 30 * 3600)
    with db.read_conn() as conn:
        done = conn.execute("SELECT 1 FROM pm_gdelt_done WHERE market_id=?", (c["market_id"],)).fetchone()
    if use_gdelt and not done:
        ev += gdelt(c["a"], c["b"], a, b, c["start"], c["end"] + 30 * 3600)
        with db.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO pm_gdelt_done (market_id, at) VALUES (?,?)", (c["market_id"], int(time.time())))
            conn.executemany("INSERT OR IGNORE INTO pm_evidence (market_id, day, source, ts, title, url, score, why) VALUES (?,?,?,?,?,?,?,?)",
                             [(c["market_id"], c["day"], e["source"], e["ts"], e["title"], e["url"], e["score"], e["why"]) for e in ev])
    else:
        with db.read_conn() as conn:
            ev += [dict(r) for r in conn.execute("SELECT source, ts, title, url, score, why FROM pm_evidence WHERE market_id=?", (c["market_id"],)).fetchall()]
    seen, items = set(), []
    for e in sorted(ev, key=lambda x: (-x["score"], x["ts"])):
        k = re.sub(r"\W+", "", e["title"].lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        e["in_day"] = c["start"] <= e["ts"] < c["end"]
        items.append(e)
    strong_day = {e["source"] for e in items if e["score"] >= 2 and e["in_day"]}
    strong_late = {e["source"] for e in items if e["score"] >= 2 and not e["in_day"]}
    relevant = [e for e in items if e["score"] != 0 or "direction" in (e["why"] or "")]
    if len(strong_day) >= 2:
        verdict, conf = "YES likely", "strong" if len(strong_day) >= 3 else "moderate"
    elif strong_day or strong_late:
        verdict, conf = "YES possible", "weak"
    elif len(relevant) >= 6 and not any(e["score"] >= 1 and e["in_day"] for e in items):
        verdict, conf = "NO possible", "weak (no qualifying report despite coverage)"
    else:
        verdict, conf = "UNCLEAR", "little coverage"
    yes = c["yes"]
    edge = None
    ai = ai_judge(c, items)
    if ai:
        v, p = ai.get("verdict"), float(ai.get("confidence") or 0)
        # absence of evidence: unless our own coverage of that day is broad, a NO can never be confident enough to count as an opportunity
        day_items = [e for e in items if e["in_day"]]
        broad = len(day_items) >= 15 and len({e["source"] for e in day_items}) >= 4
        if v == "NO" and not broad:
            p = min(p, 0.6)
            ai["confidence"] = p
            ai["reason"] = f"{ai.get('reason', '')} [capped: our coverage of that day is too thin to prove nothing happened]"
        verdict = {"YES": "YES likely", "NO": "NO likely"}.get(v, "UNCLEAR") if p >= 0.6 else "UNCLEAR"
        conf = f"AI {p:.0%}"
        if v == "YES" and p >= 0.8 and c.get("ask") is not None and float(c["ask"]) < p - 0.1:
            edge = {"side": "YES", "price": float(c["ask"]), "note": f"AI reads a qualifying strike ({p:.0%}): {str(ai.get('reason', ''))[:220]}"}
        elif v == "NO" and p >= 0.8 and c.get("bid") is not None and 1 - float(c["bid"]) < p - 0.1:
            edge = {"side": "NO", "price": round(1 - float(c["bid"]), 3), "note": f"AI finds no qualifying strike ({p:.0%}): {str(ai.get('reason', ''))[:220]}"}
        return {**c, "verdict": verdict, "confidence": conf, "strong_outlets": sorted(strong_day), "late_outlets": sorted(strong_late), "n_relevant": len(relevant),
                "evidence": items[:12], "edge": edge, "ai": ai}
    if verdict == "YES likely" and c.get("ask") is not None and float(c["ask"]) < 0.85:
        edge = {"side": "YES", "price": float(c["ask"]), "note": f"{len(strong_day)} outlets report an own-forces strike that day; YES costs {float(c['ask']) * 100:.0f}¢"}
    elif verdict == "NO possible" and c.get("bid") is not None and float(c["bid"]) > 0.35:
        edge = {"side": "NO", "price": round(1 - float(c["bid"]), 3), "note": "coverage of the conflict that day but no qualifying strike report (weak: absence of evidence)"}
    return {**c, "verdict": verdict, "confidence": conf, "strong_outlets": sorted(strong_day), "late_outlets": sorted(strong_late), "n_relevant": len(relevant),
            "evidence": items[:12], "edge": edge}


AI_SYSTEM = ("You judge prediction-market resolution from news evidence. Apply the market rules literally. Answer ONLY a JSON object: "
             '{"verdict": "YES" | "NO" | "UNCLEAR", "confidence": 0.0-1.0, "qualifying": [indices of headlines that satisfy the rules], "reason": "one or two sentences"}. '
             "Rules of thumb: a strike claimed only by the other side with no corroboration is weak; proxy/backed forces, interceptions, artillery and announcements do not "
             "count unless the rules say so; a coalition strike counts only if the rules allow it (e.g. the actor's own forces conducted it). Mind the date and the time zone: "
             "the event must fall inside the stated day. If evidence is thin or conflicting, say UNCLEAR with low confidence. Never invent facts beyond the headlines. "
             "IMPORTANT: the headline set is INCOMPLETE (a handful of English squawk/Telegram feeds plus a news archive), so the absence of a report is weak evidence. "
             "Only answer NO with confidence above 0.7 if the headlines positively indicate that no qualifying strike happened that day (for example a pause, a truce, or "
             "explicit statements that strikes stopped).")


def ai_judge(c: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    from app.intel import llm

    if not llm.config():
        return None
    off = c.get("tz", 0)
    lines = []
    for i, e in enumerate(items[:40]):
        local = time.strftime("%m-%d %H:%M", time.gmtime(e["ts"] + off * 3600))
        lines.append(f"[{i}] {local} local ({'inside the day' if e['in_day'] else 'after the day'}) · {e['source']}: {e['title'][:260]}")
    user = (f"MARKET: {c['event']} - {c['label']} (day {c['day']}, time zone UTC{off:+g})\n\nRULES:\n{c.get('rules') or c.get('rules_excerpt')}\n\n"
            "HEADLINES (published times in the market's local time):\n" + ("\n".join(lines) or "(none found)"))
    out = llm.chat_json(AI_SYSTEM, user)
    if out:
        out["used"] = [items[i]["title"][:160] for i in out.get("qualifying", []) if isinstance(i, int) and i < len(items)][:5]
    return out


def scan(use_gdelt: bool = True, push: bool = True, max_markets: int = 25) -> dict[str, Any]:
    """Judge every candidate (GDELT at most once per market, spaced for its rate limit), paper-ledger the edges, push new strong ones."""
    from app.intel import pm_edge
    from app.notify import telegram_bot

    res = []
    for i, c in enumerate(candidates()[:max_markets]):
        try:
            res.append(judge(c, use_gdelt))
        except Exception:  # noqa: BLE001
            logger.exception("resolution judge failed for %s", c.get("market_id"))
        if use_gdelt:
            time.sleep(6)
    pm_edge.ensure()
    pushed = 0
    for r in res:
        e = r.get("edge")
        if not e:
            continue
        with db.get_conn() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO pm_opps (ts, market_id, slug, title, question, side, price, fair, edge, kind, underlying, spot, strike, sigma, end_ts, liquidity) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (int(time.time()), r["market_id"], r["slug"], r["event"], f"{r['event']} — {r['label']}", e["side"], e["price"],
                                                                            None, None, "resolution", None, None, r["label"], None, r["end"], r["volume"]))
            new = cur.rowcount
        if new and push and r["confidence"] in ("strong", "moderate"):
            telegram_bot.send_rich(card(r), [[{"text": "🎲 Open market", "url": f"https://polymarket.com/event/{r['slug']}"}]])
            pushed += 1
    db.meta_set("pm_resolve_latest", json.dumps({"at": time.time(), "results": res}, default=str))
    return {"judged": len(res), "edges": sum(1 for r in res if r.get("edge")), "pushed": pushed}


def card(r: dict[str, Any]) -> str:
    from html import escape

    e = r["edge"]
    L = [f"🔍 <b>RESOLUTION CHECK · {escape(r['verdict'])}</b>   <code>{escape(r['confidence'])}</code>", "", f"<b>{escape(r['event'])} — {escape(str(r['label']))}</b>",
         f"<code>YES price {r['yes'] * 100:.0f}%   · vol ${r['volume']:,}</code>", ""]
    for ev in [x for x in r["evidence"] if x["score"] >= 2][:4]:
        L.append(f"• {escape(ev['source'])} {time.strftime('%m-%d %H:%M', time.gmtime(ev['ts']))}Z: {escape(ev['title'][:120])}")
    if e:
        L += ["", f"📌 {escape(e['note'])}"]
    L += ["", "<i>Evidence read with rules, not certainty: the resolver (UMA) decides, and proxies / intercepted strikes do not count. Paper-tracked.</i>"]
    return "\n".join(L)
