"""WHY panel: for one security, the reasons TO buy, NOT to buy and TO hold - every reason carries the numbers it rests on.

Nothing here is invented: each rule reads a field the terminal already fetched (Yahoo stats, analyst consensus, price history, headlines) and, when the
field is missing, the rule is skipped and listed under `missing`. The score is a transparent weighted vote of the rules (every weight is visible in the
output) - a research aid, not advice, and not a forecast. Rules are the classic quality / growth / value / trend / sentiment / risk checks.
"""
from __future__ import annotations

import time
from typing import Any

POS = {"beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "record", "upgrade", "upgrades", "raises", "raised", "outperform",
       "strong", "growth", "profit", "wins", "approval", "approved", "buyback", "partnership", "expands", "boost", "bullish", "tops", "higher"}
NEG = {"miss", "misses", "plunge", "plunges", "tumble", "tumbles", "slump", "falls", "fall", "drop", "drops", "downgrade", "downgrades", "cuts", "cut",
       "lawsuit", "probe", "investigation", "recall", "warns", "warning", "weak", "loss", "losses", "layoffs", "fraud", "bankruptcy", "default", "bearish", "lower"}


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 2:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _sentiment(items: list[dict[str, Any]]) -> dict[str, Any]:
    now, rows, score, recent = time.time(), [], 0, 0
    for it in items[:25]:
        words = {w.strip(".,:;!?()'\"").lower() for w in (it.get("title") or "").split()}
        p, n = len(words & POS), len(words & NEG)
        tone = (p > n) - (p < n)
        ts = it.get("ts")
        if ts and (now - ts) / 86400 <= 7:
            score += tone
            recent += 1
        rows.append({"title": it.get("title"), "link": it.get("link"), "source": it.get("source"), "ts": ts, "tone": tone})
    return {"score": score, "n": recent, "items": rows[:8]}


def build(sec: dict[str, Any], closes: list[float], analysts: dict[str, Any] | None, news: list[dict[str, Any]]) -> dict[str, Any]:
    def clean(d: dict[str, Any], keep: tuple[str, ...] = ()) -> dict[str, Any]:
        # Yahoo returns {} for a missing number: treat anything that is not a real number as missing
        return {k: (v if k in keep or (isinstance(v, (int, float)) and not isinstance(v, bool)) else None) for k, v in (d or {}).items()}

    st, tg, pf = clean(sec.get("stats")), clean(sec.get("target"), keep=("rating",)), clean(sec.get("perf"))
    price = sec.get("price") if isinstance(sec.get("price"), (int, float)) else None
    reasons: list[dict[str, Any]] = []
    missing: list[str] = []

    def add(side: str, w: int, title: str, detail: str, ev: list[tuple[str, str]]) -> None:
        reasons.append({"side": side, "w": w, "title": title, "detail": detail, "evidence": [[k, v] for k, v in ev]})

    def have(name: str, v: Any) -> bool:
        if v is None:
            missing.append(name)
            return False
        return True

    def pc(v: float) -> str:
        return f"{v * 100:.1f}%"

    def bn(v: float | None) -> str:
        return f"${(v or 0) / 1e9:.2f}B"

    # ---- quality ---------------------------------------------------------------------------------------------------------------
    if have("return on equity", st.get("roe")):
        if st["roe"] >= 0.15:
            add("buy", 2, "High return on equity", "The business earns a high return on shareholders' money.", [("ROE", pc(st["roe"]))])
        elif st["roe"] < 0.05:
            add("not", 1, "Low return on equity", "Shareholders' money earns little.", [("ROE", pc(st["roe"]))])
    if have("operating margin", st.get("op_margin")):
        if st["op_margin"] >= 0.20:
            add("buy", 2, "Strong operating margin", "Pricing power / cost control: a fifth or more of revenue is operating profit.", [("Operating margin", pc(st["op_margin"]))])
        elif st["op_margin"] < 0.05:
            add("not", 2, "Thin or negative operating margin", "Little room for error if costs rise or demand slips.", [("Operating margin", pc(st["op_margin"]))])
    if have("free cash flow", st.get("fcf")):
        if st["fcf"] > 0:
            add("buy", 2, "Positive free cash flow", "The company funds itself: it generates cash after capital spending.", [("Free cash flow", bn(st["fcf"]))])
        else:
            add("not", 3, "Burning cash", "Negative free cash flow: it depends on cash reserves or new financing.", [("Free cash flow", "-" + bn(abs(st["fcf"]))), ("Cash", bn(st.get("total_cash")))])
    if st.get("debt_equity") is not None:
        de = st["debt_equity"]
        if de > 150:
            add("not", 2, "Heavy debt load", "Debt is large relative to equity; rising rates or a downturn hurt more.", [("Debt / equity", f"{de:.0f}%")])
        elif de < 50 and (st.get("total_cash") or 0) >= (st.get("total_debt") or 0):
            add("buy", 1, "Cash covers debt", "Balance sheet is a cushion, not a risk.", [("Debt / equity", f"{de:.0f}%"), ("Cash", bn(st.get("total_cash"))), ("Debt", bn(st.get("total_debt")))])
    if st.get("current_ratio") is not None and st["current_ratio"] < 1:
        add("not", 1, "Short-term liquidity is tight", "Current liabilities exceed current assets.", [("Current ratio", f"{st['current_ratio']:.2f}")])

    # ---- growth ----------------------------------------------------------------------------------------------------------------
    if have("revenue growth", st.get("rev_growth")):
        if st["rev_growth"] >= 0.10:
            add("buy", 2, "Revenue is growing fast", "Sales are expanding at a double-digit rate.", [("Revenue growth", pc(st["rev_growth"]))])
        elif st["rev_growth"] < 0:
            add("not", 2, "Revenue is shrinking", "A falling top line is the hardest problem to fix.", [("Revenue growth", pc(st["rev_growth"]))])
    if st.get("earn_growth") is not None:
        if st["earn_growth"] >= 0.15:
            add("buy", 1, "Earnings are accelerating", "Profit is growing faster than most of the market.", [("Earnings growth", pc(st["earn_growth"]))])
        elif st["earn_growth"] < -0.10:
            add("not", 1, "Earnings are falling", "Profit is going the wrong way.", [("Earnings growth", pc(st["earn_growth"]))])

    # ---- valuation -------------------------------------------------------------------------------------------------------------
    fpe, pe, peg = st.get("fwd_pe"), st.get("pe"), st.get("peg")
    if have("forward P/E", fpe):
        if 0 < fpe < 15:
            add("buy", 2, "Cheap on forward earnings", "You pay under 15x next year's expected profit.", [("Forward P/E", f"{fpe:.1f}")])
        elif fpe > 35:
            add("not", 2, "Expensive on forward earnings", "The price already assumes years of strong growth; any disappointment is punished.", [("Forward P/E", f"{fpe:.1f}")])
    if peg is not None and 0 < peg < 1.2:
        add("buy", 1, "Growth is cheap (PEG)", "Price/earnings is low relative to the growth rate.", [("PEG", f"{peg:.2f}")])
    elif peg is not None and peg > 2.5:
        add("not", 1, "Growth is expensive (PEG)", "You are paying a lot for each point of growth.", [("PEG", f"{peg:.2f}")])
    if pe is not None and pe < 0:
        add("not", 1, "No trailing profit", "Earnings per share are negative.", [("EPS", str(st.get("eps")))])

    # ---- analysts --------------------------------------------------------------------------------------------------------------
    upside = None
    if have("analyst target", tg.get("mean")) and price:
        upside = (tg["mean"] / price - 1) * 100
        n = tg.get("n") or 0
        ev = [("Mean target", f"${tg['mean']:.2f}"), ("Price", f"${price:.2f}"), ("Analysts", str(n)), ("Upside", f"{upside:.0f}%")]
        if upside >= 15 and n >= 5:
            add("buy", 2, "Analysts see clear upside", "The average price target is well above the current price.", ev)
        elif upside <= -5:
            add("not", 2, "Price is above analyst targets", "The market already trades above the average target.", ev)
    if tg.get("rating") in ("strong_buy", "buy") and (tg.get("n") or 0) >= 5:
        add("buy", 1, "Consensus rating is Buy", "Most covering analysts recommend buying.", [("Rating", tg["rating"].replace("_", " ").upper()), ("Score (1 = strong buy)", str(tg.get("score")))])
    elif tg.get("rating") in ("sell", "strong_sell"):
        add("not", 2, "Consensus rating is Sell", "Covering analysts lean negative.", [("Rating", tg["rating"].replace("_", " ").upper())])
    if analysts:
        chg = (analysts.get("changes") or [])[:10]
        ups = sum(1 for c in chg if (c.get("action") or "").lower() in ("up", "upgrade"))
        downs = sum(1 for c in chg if (c.get("action") or "").lower() in ("down", "downgrade"))
        if downs >= 3 and downs > ups:
            add("not", 1, "Recent analyst downgrades", "More downgrades than upgrades among the last rating changes.", [("Downgrades", str(downs)), ("Upgrades", str(ups))])
        elif ups >= 3 and ups > downs:
            add("buy", 1, "Recent analyst upgrades", "More upgrades than downgrades among the last rating changes.", [("Upgrades", str(ups)), ("Downgrades", str(downs))])

    # ---- trend / momentum ------------------------------------------------------------------------------------------------------
    sma50, sma200 = st.get("sma50"), st.get("sma200")
    trend_up = None
    if price and sma200:
        trend_up = price > sma200 and (not sma50 or sma50 > sma200 * 0.98)
        if sma50 and price > sma200 and price > sma50:
            add("buy", 2, "Uptrend intact", "Price is above both its 50- and 200-day averages.", [("Price", f"${price:.2f}"), ("50-day", f"${sma50:.2f}"), ("200-day", f"${sma200:.2f}")])
        elif price < sma200:
            add("not", 2, "Below the 200-day average", "The long-term trend is down; buying here is catching a falling knife until it recovers.", [("Price", f"${price:.2f}"), ("200-day", f"${sma200:.2f}"), ("Gap", f"{(price / sma200 - 1) * 100:.1f}%")])
    else:
        missing.append("moving averages")
    rsi = _rsi(closes[-200:]) if closes else None
    if rsi is not None:
        if rsi >= 75:
            add("not", 1, "Overbought short-term", "RSI is stretched; pullbacks are common after readings like this.", [("RSI(14)", f"{rsi:.0f}")])
        elif rsi <= 30:
            add("buy", 1, "Oversold short-term", "RSI is washed out; bounces are common, but only if the business is intact.", [("RSI(14)", f"{rsi:.0f}")])
    w52 = sec.get("week52") or [None, None]
    if price and w52[0] and w52[1] and w52[1] > w52[0]:
        pos = (price - w52[0]) / (w52[1] - w52[0])
        if pos >= 0.95:
            add("hold", 1, "At the 52-week high", "Strength attracts buyers and breakouts to new highs often continue, but chasing here has a worse risk/reward.", [("52w range", f"${w52[0]:.2f} - ${w52[1]:.2f}"), ("Position", f"{pos * 100:.0f}% of range")])
        elif pos <= 0.15:
            add("not", 1, "Near the 52-week low", "The market has been selling it hard; find out why before buying.", [("52w range", f"${w52[0]:.2f} - ${w52[1]:.2f}"), ("Position", f"{pos * 100:.0f}% of range")])
    if pf.get("6M") is not None and pf["6M"] > 25:
        add("hold", 1, "Strong 6-month momentum", "Winners over 6 months statistically keep outperforming for a while.", [("6M", f"{pf['6M']:+.0f}%")])
    if pf.get("3M") is not None and pf["3M"] < -20:
        add("not", 1, "Sharp 3-month drop", "Heavy recent selling.", [("3M", f"{pf['3M']:+.0f}%")])

    # ---- risk ------------------------------------------------------------------------------------------------------------------
    if (st.get("short_pct_float") or 0) >= 0.10:
        add("not", 1, "Heavily shorted", "Many traders bet against it (also a squeeze risk upward).", [("Short % of float", pc(st["short_pct_float"]))])
    if (st.get("beta") or 0) >= 1.6:
        add("not", 1, "Very volatile (high beta)", "It moves much more than the market in both directions.", [("Beta", f"{st['beta']:.2f}")])
    days_to_earn = None
    ne = sec.get("next_earnings") or []
    if ne:
        days_to_earn = (ne[0] - time.time()) / 86400
        if 0 <= days_to_earn <= 10:
            add("not", 1, "Earnings are days away", "Binary event risk: the stock can gap 5-15% either way overnight.", [("Earnings in", f"{days_to_earn:.0f} days")])

    # ---- income / hold reasons -------------------------------------------------------------------------------------------------
    if (st.get("div_yield") or 0) >= 0.02 and (st.get("payout") or 1) < 0.7:
        add("hold", 2, "Paid to wait (dividend)", "The dividend is meaningful and well covered by earnings.", [("Yield", pc(st["div_yield"])), ("Payout ratio", pc(st.get("payout") or 0))])
    if trend_up and (st.get("fcf") or 0) > 0:
        add("hold", 2, "Trend and cash flow both support holding", "No technical break and the business funds itself: nothing forces you out.", [("Price vs 200-day", f"{(price / sma200 - 1) * 100:+.1f}%"), ("FCF", bn(st["fcf"]))])
    if upside is not None and upside >= 10:
        add("hold", 1, "Analyst upside still remaining", "The average target is above the price: the story is not fully priced.", [("Upside to mean target", f"{upside:.0f}%")])

    # ---- news tone -------------------------------------------------------------------------------------------------------------
    sent = _sentiment(news)
    if sent["n"] >= 5:
        if sent["score"] >= 3:
            add("buy", 1, "Positive news flow", "Headlines this week skew positive.", [("Net tone (7d)", f"{sent['score']:+d}"), ("Headlines", str(sent["n"]))])
        elif sent["score"] <= -3:
            add("not", 2, "Negative news flow", "Headlines this week skew negative: read them before buying.", [("Net tone (7d)", f"{sent['score']:+d}"), ("Headlines", str(sent["n"]))])

    buy = sum(r["w"] for r in reasons if r["side"] == "buy")
    against = sum(r["w"] for r in reasons if r["side"] == "not")
    total = buy + against
    score = round(100 * (buy - against) / total) if total else 0
    if total < 4:
        score = 0
        stance, why = "INSUFFICIENT DATA", "Too few measurable signals for this security (often ETFs, crypto, foreign listings)."
    elif score >= 35:
        stance, why = "BUY CANDIDATE", "Quality, valuation and trend line up in favour of buying."
    elif score >= 10:
        stance, why = "LEAN BUY / ACCUMULATE", "More reasons for than against, but not a clean setup: size small, add on strength."
    elif score > -10:
        stance, why = "WAIT / NEUTRAL", "Reasons for and against are balanced. No edge to act on yet."
    elif score > -35:
        stance, why = "LEAN AVOID", "More reasons against than for."
    else:
        stance, why = "AVOID", "The case against clearly outweighs the case for."

    levels = {"price": price, "sma50": sma50, "sma200": sma200, "low52": w52[0], "high52": w52[1],
              "target_mean": tg.get("mean"), "target_low": tg.get("low"), "target_high": tg.get("high")}
    support = max([v for v in (sma200, w52[0]) if v and price and v < price], default=None)
    rr = None
    if upside is not None and support and price:
        down = (price / support - 1) * 100
        rr = {"upside_pct": round(upside, 1), "downside_pct": round(down, 1), "ratio": round(upside / down, 2) if down > 0 else None, "support": round(support, 2)}
    watch = []
    if sma200:
        watch.append(f"A close below the 200-day average (${sma200:.2f}) would break the long-term trend.")
    if days_to_earn is not None and days_to_earn >= 0:
        watch.append(f"Earnings in about {days_to_earn:.0f} days: the next real catalyst.")
    if tg.get("mean"):
        watch.append(f"The mean analyst target is ${tg['mean']:.2f}; cuts to it are an early warning.")
    return {
        "symbol": sec.get("symbol"), "name": sec.get("name"), "score": score, "stance": stance, "stance_why": why,
        "votes": {"for": buy, "against": against},
        "why_buy": sorted((r for r in reasons if r["side"] == "buy"), key=lambda r: -r["w"]),
        "why_not": sorted((r for r in reasons if r["side"] == "not"), key=lambda r: -r["w"]),
        "why_hold": sorted((r for r in reasons if r["side"] == "hold"), key=lambda r: -r["w"]),
        "levels": levels, "risk_reward": rr, "watch": watch, "news": sent, "rsi": None if rsi is None else round(rsi, 1),
        "missing": sorted(set(missing)),
        "method": "Weighted vote of quality, growth, valuation, analyst, trend, risk and news rules. Weights are shown on every reason. A research aid, not advice, not a forecast.",
    }
