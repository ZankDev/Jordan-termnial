"""OPTION / ORDER FLOW ICEBERG — daily study course on Telegram.

Every day (Israel time):
  * 08:00  today's topic, name only
  * 22:00  10 multiple-choice questions on that topic, one at a time with A/B/C/D buttons
A topic is passed with PASS_MARK/10 correct. Until then the same topic comes back the next day with new questions;
an unanswered quiz counts as not passed. Progress (current topic, attempts, scores) lives in meta `iceberg_progress`,
every quiz in table `iceberg_quiz`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from app import db

logger = logging.getLogger(__name__)
IL = ZoneInfo("Asia/Jerusalem")
MORNING_HOUR = 8
QUIZ_HOUR = 22
N_Q = 10
PASS_MARK = 8
LETTERS = "ABCD"
_state: dict[str, float] = {"fail_ts": 0.0}

# the iceberg, top to bottom (layer, topic)
LAYERS = [
    ("Surface", ["0DTE", "Open interest", "Volume", "Gamma exposure (GEX)", "Round strikes", "Put/call skew",
                 "IV / implied volatility", "Max pain", "Options walls", "SPY > SPX", "Term structure"]),
    ("Layer 1", ["Delta hedging", "Dealer positioning", "Vanna / charm", "Volatility surface", "Skew", "IV rank", "Volga",
                 "Dark pools", "Block trades", "Sweeps", "Multi-leg masking", "Synthetic positions", "Cross-asset flow"]),
    ("Layer 2", ["Gamma scalping", "Inventory risk", "Flow toxicity", "Order book imbalance", "Price impact", "Execution algos",
                 "Latency", "Smart money footprints", "Market-on-close flow"]),
    ("Layer 3", ["IV expansion", "IV crush", "Calendar spreads", "Diagonal spreads", "Butterfly / condor", "Ratio spreads",
                 "Iron condors", "Risk reversal", "Straddle / strangle"]),
    ("Layer 4", ["ETF creation/redemption", "Options flow vs. volume", "Reset flows", "Large lot trades", "Dealer gamma",
                 "Dealer delta", "OI changes", "Volume/OI divergence", "Gamma flip"]),
    ("Layer 5", ["Expiration flows", "Pin risk", "Hedging flows", "0DTE gamma", "Vol control", "Momentum ignition",
                 "Sweep to fill", "Spoofing / layering", "Quote stuffing"]),
    ("Layer 6", ["Market microstructure", "Order flow classification", "Footprint charts", "Tick data", "Queue position",
                 "Liquidity dynamics", "Hidden liquidity", "HFT presence", "Fractals", "Regime switching", "Execution variance",
                 "Stat arb"]),
    ("The bottom", ["Quant"]),
]
TOPICS = [(layer, t) for layer, ts in LAYERS for t in ts]

SYSTEM = (
    "You are a strict options & market-microstructure instructor. The student trades NQ/ES futures and wants to really "
    "understand option/order flow. Write exam questions in clear English. Answer ONLY with JSON: {\"questions\":[{\"q\":\"...\","
    "\"options\":[\"...\",\"...\",\"...\",\"...\"],\"answer\":0,\"explain\":\"...\"}]}. Rules: exactly 12 questions; exactly 4 "
    "options each; answer = index 0-3 of the correct option, spread across positions; options under 100 characters; explain = 1-2 "
    "sentences teaching why. Mix: definitions, mechanics (cause -> effect for dealers / liquidity / price) and practical "
    "situations a futures trader meets. ONLY textbook-standard, verifiable facts (Hull, Natenberg, Sinclair, SpotGamma/SqueezeMetrics "
    "conventions, Harris 'Trading and Exchanges'). Never invent rules or link the topic to unrelated indicators. Exactly one option "
    "is correct; the wrong ones must be clearly wrong to an expert, not 'partly true'."
)
REVIEW = (
    "You are a senior options-market-maker reviewing an exam before students see it. For every question check: is the marked "
    "answer unambiguously correct by standard literature? Is every other option clearly wrong? Is the explanation accurate? "
    "Fix the answer index or rewrite the question if needed; DROP any question you are not sure about. Keep the best 10. Then "
    "translate the questions, options and explanations into natural, correctly spelled modern Hebrew; keep technical terms in "
    "English (gamma, delta, open interest, dealer, strike, IV, VWAP, ...). Hebrew glossary: expire = פוקעת (never פוגעת), expiration = פקיעה, "
    "implied volatility = תנודתיות גלומה (IV), underlying = נכס הבסיס, market maker = עושה שוק, premium = פרמיה, spread = מרווח. "
    "Judge correctness against core_facts first. Answer ONLY with JSON in the same shape: "
    "{\"questions\":[{\"q\",\"options\":[4],\"answer\",\"explain\"}]}."
)


def _clean(out: Any) -> list[dict[str, Any]]:
    qs = []
    for q in (out or {}).get("questions", []) if isinstance(out, dict) else []:
        try:
            opts = [str(o).strip()[:180] for o in q["options"]][:4]
            ans = int(q["answer"])
            if len(opts) == 4 and 0 <= ans < 4 and str(q["q"]).strip():
                qs.append({"q": str(q["q"]).strip()[:600], "options": opts, "answer": ans, "explain": str(q.get("explain", "")).strip()[:500]})
        except (KeyError, TypeError, ValueError):
            continue
    return qs


# ------------------------------------------------------------------ storage
def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS iceberg_quiz (
            day TEXT PRIMARY KEY, topic_idx INTEGER, attempt INTEGER, questions TEXT, answers TEXT DEFAULT '{}',
            score INTEGER, passed INTEGER, morning_sent INTEGER DEFAULT 0, quiz_sent INTEGER DEFAULT 0, finished INTEGER DEFAULT 0)""")


def progress() -> dict[str, Any]:
    try:
        p = json.loads(db.meta_get("iceberg_progress") or "{}")
    except ValueError:
        p = {}
    p.setdefault("idx", 0)
    p.setdefault("attempts", {})  # str(idx) -> quizzes taken
    p.setdefault("passed", {})  # str(idx) -> {"day", "score", "attempts"}
    return p


def _save_progress(p: dict[str, Any]) -> None:
    db.meta_set("iceberg_progress", json.dumps(p))


def _row(day: str) -> dict[str, Any] | None:
    ensure()
    with db.read_conn() as conn:
        r = conn.execute("SELECT * FROM iceberg_quiz WHERE day=?", (day,)).fetchone()
    return dict(r) if r else None


def _update(day: str, **kw: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in kw)
    with db.get_conn() as conn:
        conn.execute(f"UPDATE iceberg_quiz SET {cols} WHERE day=?", (*kw.values(), day))


def _today_row(now: dt.datetime) -> dict[str, Any] | None:
    """Today's quiz row, created on first use with the current topic. None when the course is finished."""
    day = now.date().isoformat()
    r = _row(day)
    if r:
        return r
    p = progress()
    if p["idx"] >= len(TOPICS):
        return None
    attempt = int(p["attempts"].get(str(p["idx"]), 0)) + 1
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO iceberg_quiz (day, topic_idx, attempt) VALUES (?,?,?)", (day, p["idx"], attempt))
    return _row(day)


# ------------------------------------------------------------------ questions
def _generate(idx: int, attempt: int) -> list[dict[str, Any]]:
    from app.intel import ai_news

    layer, topic = TOPICS[idx]
    seen: list[str] = []
    with db.read_conn() as conn:
        for r in conn.execute("SELECT questions FROM iceberg_quiz WHERE topic_idx=? AND questions IS NOT NULL", (idx,)).fetchall():
            seen += [q["q"] for q in json.loads(r["questions"] or "[]")]
    done = [t for _, t in TOPICS[:idx]]
    from app.learn.iceberg_syllabus import SYLLABUS

    user = (f"Topic: {topic}  (iceberg level: {layer}, topic {idx + 1} of {len(TOPICS)}).\n"
            f"Topics already learned (you may connect to them): {', '.join(done[-12:]) or 'none'}.\n"
            f"This is attempt {attempt}." + (" The student failed before: cover the core ideas again from new angles." if attempt > 1 else "") + "\n")
    user += ("CORE FACTS (build every question and every correct answer ONLY from these facts; wrong options must contradict them):\n"
             + SYLLABUS.get(topic, "") + "\n")
    if seen:
        user += "Do NOT repeat these earlier questions:\n- " + "\n- ".join(s[:140] for s in seen[-30:]) + "\n"
    import time

    draft = _clean(ai_news._call(ai_news.ANALYSE_MODELS, SYSTEM, user, max_tokens=7000, reasoning="medium")[0])
    if len(draft) < N_Q:
        return []
    time.sleep(30)  # Groq free tier: tokens per minute per model
    for _ in range(2):
        out, _m = ai_news._call(ai_news.ANALYSE_MODELS[:1], REVIEW, json.dumps({"topic": topic, "core_facts": SYLLABUS.get(topic, ""), "questions": draft}, ensure_ascii=False),
                                max_tokens=8000, reasoning="medium")
        qs = _clean(out)
        if len(qs) >= N_Q:
            return qs[:N_Q]
        time.sleep(65)
    return []


# ------------------------------------------------------------------ telegram
def _q_text(row: dict[str, Any], qi: int) -> str:
    q = json.loads(row["questions"])[qi]
    topic = TOPICS[row["topic_idx"]][1]
    head = f"🧊 {topic} · שאלה {qi + 1}/{N_Q}"
    return head + "\n\n" + q["q"] + "\n\n" + "\n".join(f"{LETTERS[i]}. {o}" for i, o in enumerate(q["options"]))


def _send_question(row: dict[str, Any], qi: int) -> None:
    from app.notify import telegram_bot

    kb = [[{"text": LETTERS[i], "callback_data": f"iq:{row['day']}:{qi}:{i}"} for i in range(4)]]
    telegram_bot.send_text_buttons(_q_text(row, qi), kb)


def send_morning(now: dt.datetime | None = None) -> bool:
    from app.notify import telegram_bot

    now = now or dt.datetime.now(IL)
    r = _today_row(now)
    if not r:
        return False
    telegram_bot.send_text(f"🧊 {TOPICS[r['topic_idx']][1]}" + (f"  (#{r['attempt']})" if r["attempt"] > 1 else ""))
    _update(r["day"], morning_sent=1)
    return True


def prepare(r: dict[str, Any]) -> bool:
    qs = _generate(r["topic_idx"], r["attempt"])
    if not qs:
        logger.warning("iceberg: question generation failed for %s", TOPICS[r["topic_idx"]][1])
        return False
    _update(r["day"], questions=json.dumps(qs, ensure_ascii=False))
    return True


def send_quiz(now: dt.datetime | None = None) -> bool:
    from app.notify import telegram_bot

    now = now or dt.datetime.now(IL)
    r = _today_row(now)
    if not r or r["quiz_sent"]:
        return False
    if not r["questions"]:
        if not prepare(r):
            return False
        r = _row(r["day"])
    p = progress()
    p["attempts"][str(r["topic_idx"])] = r["attempt"]
    _save_progress(p)
    _update(r["day"], quiz_sent=1)
    telegram_bot.send_text(f"🌙 מבחן הלילה: {TOPICS[r['topic_idx']][1]}\n{N_Q} שאלות · עוברים עם {PASS_MARK}/{N_Q}. בהצלחה!")
    _send_question(r, 0)
    return True


def handle_callback(data: str, message_id: int, cq_id: str) -> None:
    from app.notify import telegram_bot

    _, day, qi_s, ch_s = data.split(":")
    qi, ch = int(qi_s), int(ch_s)
    r = _row(day)
    if not r or not r["questions"]:
        telegram_bot.answer_callback_query(cq_id, "המבחן לא נמצא")
        return
    answers = json.loads(r["answers"] or "{}")
    if str(qi) in answers or r["finished"]:
        telegram_bot.answer_callback_query(cq_id, "כבר ענית על השאלה הזו")
        return
    q = json.loads(r["questions"])[qi]
    ok = ch == q["answer"]
    answers[str(qi)] = ch
    _update(day, answers=json.dumps(answers))
    telegram_bot.answer_callback_query(cq_id, "✅ נכון!" if ok else f"❌ התשובה: {LETTERS[q['answer']]}")
    verdict = f"✅ נכון ({LETTERS[ch]})" if ok else f"❌ בחרת {LETTERS[ch]} · הנכונה: {LETTERS[q['answer']]}"
    telegram_bot.edit_text_buttons(message_id, _q_text(r, qi) + f"\n\n{verdict}\n💡 {q['explain']}", [])
    if len(answers) < N_Q:
        nxt = next(i for i in range(N_Q) if str(i) not in answers)
        _send_question(_row(day), nxt)
        return
    _finish(day)


def _finish(day: str) -> None:
    from app.notify import telegram_bot

    r = _row(day)
    qs, answers = json.loads(r["questions"]), json.loads(r["answers"] or "{}")
    score = sum(1 for i, q in enumerate(qs) if answers.get(str(i)) == q["answer"])
    passed = score >= PASS_MARK
    _update(day, score=score, passed=int(passed), finished=1)
    p = progress()
    idx = r["topic_idx"]
    topic = TOPICS[idx][1]
    if passed and p["idx"] == idx:
        p["passed"][str(idx)] = {"day": day, "score": score, "attempts": r["attempt"]}
        p["idx"] = idx + 1
        _save_progress(p)
    bar = _bar(p)
    if passed:
        nxt = f"מחר: הנושא הבא 🧊" if p["idx"] < len(TOPICS) else "🏆 סיימת את כל הקרחון — הגעת ל-Quant!"
        telegram_bot.send_text(f"🎉 עברת את {topic}! {score}/{N_Q}" + (f" (בניסיון {r['attempt']})" if r["attempt"] > 1 else "") + f"\n{nxt}\n\n{bar}")
    else:
        telegram_bot.send_text(f"📚 {score}/{N_Q} ב-{topic} — צריך {PASS_MARK}. מחר חוזרים על אותו נושא עם שאלות חדשות.\n"
                               f"תקרא שוב את ההסברים למעלה 👆\n\n{bar}")


def _bar(p: dict[str, Any]) -> str:
    n, total = len(p["passed"]), len(TOPICS)
    filled = round(10 * n / total)
    layer = TOPICS[min(p["idx"], total - 1)][0]
    return f"התקדמות: {'🟦' * filled}{'⬜' * (10 - filled)} {n}/{total} נושאים · שכבה: {layer}"


def status_text() -> str:
    p = progress()
    lines = ["🧊 OPTION / ORDER FLOW ICEBERG", _bar(p)]
    if p["idx"] < len(TOPICS):
        a = int(p["attempts"].get(str(p["idx"]), 0))
        lines.append(f"נושא נוכחי: {TOPICS[p['idx']][1]}" + (f" · {a} ניסיונות עד עכשיו" if a else ""))
    recent = sorted(p["passed"].items(), key=lambda kv: int(kv[0]))[-5:]
    if recent:
        lines.append("\nעברת לאחרונה:")
        lines += [f"✅ {TOPICS[int(k)][1]} — {v['score']}/{N_Q}" + (f" (ניסיון {v['attempts']})" if v["attempts"] > 1 else "") for k, v in recent]
    lines.append(f"\nבוקר {MORNING_HOUR:02d}:00 נושא · לילה {QUIZ_HOUR}:00 מבחן · /quiz = מבחן עכשיו")
    return "\n".join(lines)


def api_state() -> dict[str, Any]:
    p = progress()
    ensure()
    with db.read_conn() as conn:
        hist = [dict(r) for r in conn.execute("SELECT day, topic_idx, attempt, score, passed, finished FROM iceberg_quiz ORDER BY day DESC LIMIT 60").fetchall()]
    for h in hist:
        h["topic"] = TOPICS[h["topic_idx"]][1]
    return {"idx": p["idx"], "total": len(TOPICS), "current": TOPICS[p["idx"]][1] if p["idx"] < len(TOPICS) else None,
            "topics": [{"layer": l, "topic": t, "passed": p["passed"].get(str(i)), "attempts": p["attempts"].get(str(i), 0)} for i, (l, t) in enumerate(TOPICS)],
            "history": hist}


# ------------------------------------------------------------------ loop
def tick(now: dt.datetime | None = None) -> None:
    now = now or dt.datetime.now(IL)
    ensure()
    if now.hour >= MORNING_HOUR:
        r = _today_row(now)
        if r and not r["morning_sent"]:
            send_morning(now)
    if (now.hour == QUIZ_HOUR - 1 and now.minute >= 40) and now.timestamp() - _state["fail_ts"] > 600:
        r = _today_row(now)
        if r and not r["questions"] and not prepare(r):
            _state["fail_ts"] = now.timestamp()
    if now.hour >= QUIZ_HOUR and now.timestamp() - _state["fail_ts"] > 600:
        r = _today_row(now)
        if r and not r["quiz_sent"] and not send_quiz(now):
            _state["fail_ts"] = now.timestamp()  # AI busy: retry in 10 min


async def study_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            logger.exception("iceberg study loop failed")
        await asyncio.sleep(60)
