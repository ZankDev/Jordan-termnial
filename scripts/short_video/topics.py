"""Daily topics for the JORDAN shorts (one per morning, in this order). Each topic is a list of scenes for jordan_short.html.
Facts used: CPI reactions are from the terminal's measured event library (Sep 13 2022 hot CPI: S&P -4.3%; Nov 10 2022 cool CPI: S&P +5.5%)."""
from __future__ import annotations

import numpy as np

ORDER = ["montecarlo", "algo", "propfirm", "footprint", "cpi", "negrr", "quant"]
TITLES = {"montecarlo": "What is Monte Carlo?", "algo": "What is algo trading?", "propfirm": "What is a prop firm?", "footprint": "How to enter a trade with the footprint",
          "cpi": "How to predict CPI with the Jordan Terminal", "negrr": "How to pass a funded account with negative R:R", "quant": "What is quant trading?"}

OUTRO = lambda cta="Follow for more": dict(id="s9", kind="outro", text="Follow for the next one.", p=dict(cta=cta), sfx=[("start", "boom", 0.8), (2.3, "click", 1.0), (2.35, "pop_hi", 0.8)], em=["follow"])  # noqa: E731


def walk(seed: int, n: int, p_win: float, win: float, loss: float, stop_at: tuple[float, float] | None = None) -> list[float]:
    r = np.random.default_rng(seed)
    v, out = 0.0, [0.0]
    for _ in range(n):
        v += win if r.random() < p_win else -loss
        out.append(round(v, 2))
        if stop_at and (v >= stop_at[0] or v <= stop_at[1]):
            break
    return out


def find_walk(want: str, seeds, n, p, w, l, target, dd):
    for s in seeds:
        v = walk(s, n, p, w, l, (target, dd))
        if (want == "pass" and v[-1] >= target) or (want == "fail" and v[-1] <= dd):
            return v
    return walk(seeds[0], n, p, w, l, (target, dd))


def candles_up(n=24, seed=4):
    r = np.random.default_rng(seed)
    out, prev = [], 100.0
    for i in range(n):
        c = prev + 0.45 + r.normal(0, 0.9)
        out.append([round(prev, 2), round(max(prev, c) + abs(r.normal(0, 0.5)), 2), round(min(prev, c) - abs(r.normal(0, 0.5)), 2), round(c, 2)])
        prev = c
    return out


def topics(chart_spec, IMG):
    """chart_spec / IMG come from jordan_short.py."""
    up = candles_up()
    swing = []
    r = np.random.default_rng(9)
    prev = 100.0
    path = 100 + 6 * np.sin(np.linspace(0, 3.3 * np.pi, 34))
    for x in path:
        c = x + r.normal(0, 0.6)
        swing.append([round(prev, 2), round(max(prev, c) + abs(r.normal(0, 0.5)), 2), round(min(prev, c) - abs(r.normal(0, 0.5)), 2), round(c, 2)])
        prev = c
    lows = [i for i in range(2, 32) if swing[i][3] < min(swing[i - 2][3], swing[i + 2][3])][:2]
    highs = [i for i in range(2, 32) if swing[i][3] > max(swing[i - 2][3], swing[i + 2][3])][:2]
    algo_arrows = [{"i": i, "v": swing[i][2], "up": True, "cue": None} for i in lows] + [{"i": i, "v": swing[i][1], "up": False, "cue": None} for i in highs]
    # prop firm: $ thousands on a $50k account, target +3k, max drawdown -2.5k
    pf_pass = find_walk("pass", range(1, 300), 60, 0.55, 0.45, 0.4, 3.0, -2.5)
    pf_fail = find_walk("fail", range(300, 600), 60, 0.45, 0.5, 0.5, 3.0, -2.5)
    # negative R:R: win 1, lose 2, 75% win rate (smooth) vs 2:1 at 35% (jagged)
    nr_smooth = find_walk("pass", range(1, 300), 60, 0.75, 0.25, 0.5, 3.0, -2.5)
    nr_jag = find_walk("fail", range(300, 700), 60, 0.35, 0.8, 0.4, 3.0, -2.5)
    # footprint: 10 price rows, 4 candles; stacked buy imbalances in candle 2 (rows 4-6), pullback entry in candle 4
    rows = [f"{5212 - i * 0.25:.2f}" for i in range(10)]
    fp = [
        {"top": 2, "bot": 8, "up": False, "delta": -310, "cells": [[220, 140], [310, 180], [460, 210], [380, 160], [290, 120], [240, 90], [150, 60]]},
        {"top": 3, "bot": 9, "up": True, "delta": 520, "cells": [[90, 210], [120, 340], [60, 410], [70, 380], [80, 360], [110, 190], [130, 150]]},
        {"top": 1, "bot": 5, "up": True, "delta": 260, "cells": [[140, 230], [160, 310], [120, 280], [150, 200], [170, 190]]},
        {"top": 3, "bot": 7, "up": True, "delta": 340, "cells": [[260, 270], [210, 330], [380, 400], [120, 360], [90, 240]]},
    ]
    return {
        "montecarlo": [
            dict(id="s1", kind="hook", flash=True, text="Your backtest made money. But it only shows you one possible future.",
                 p=dict(line1="Your backtest shows", line2="one future", key2="one", hits=["money"], chart=chart_spec(up, top=470, h=700)),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("one", "glitch", 0.7)], em=["one", "future"]),
            dict(id="s2", kind="paths", text="Monte Carlo fixes that. It replays your strategy thousands of times, with the trades in a random order, to show every future that could have happened.",
                 p=dict(title="10,000 possible futures", n=80, steps=90, drift=0.12, vol=1.2, seed=5, hlCue="every"), sfx=[("start", "swipe", 0.8), ("thousands", "riser", 0.6), ("every", "whoosh", 0.7)], em=["thousands", "random", "every"]),
            dict(id="s3", kind="paths", text="Most paths end in profit. But look at the worst five percent. That is the drawdown you must be able to survive.",
                 p=dict(title="Look at the worst 5%", n=80, steps=90, drift=0.12, vol=1.2, seed=5, hist=True, histCue="most", worst=True, hlCue="worst", worstLabel="worst 5%", medLabel="median"),
                 sfx=[("start", "swipe", 0.7), ("worst", "glitch", 0.7), ("survive", "boom", 0.7)], em=["worst", "survive"]),
            dict(id="s4", kind="statement", text="If your account can't survive the worst path, the position is too big for your account.", p=dict(line1="Size for the", line2="worst path", key2="worst", top=440),
                 sfx=[("start", "whoosh", 0.8), ("worst", "pop", 0.9)], em=["worst", "big"]),
            dict(id="s5", kind="checklist", text="Use it to find your real maximum drawdown, to set your position size, and to check that your edge is not just luck.",
                 p=dict(title="Use it for", items=[{"text": "Your real max drawdown", "ok": True, "cue": "maximum"}, {"text": "Your position size", "ok": True, "cue": "position"}, {"text": "Proof the edge is not luck", "ok": True, "cue": "luck"}]),
                 sfx=[("start", "swipe", 0.8), ("maximum", "pop", 0.9), ("position", "pop", 0.9), ("luck", "ding", 0.8)], em=["drawdown", "size", "luck"]),
            dict(id="s6", kind="shot", flash=True, text="The Jordan Terminal runs a Monte Carlo on any stock, inside the quant lab.", p=dict(title="Monte Carlo · Quant Lab", img=IMG["qnt_mc"], zoom=1.3, panX=-6, top=400, pill="◆ JORDAN TERMINAL"),
                 sfx=[("start", "whoosh", 0.9), ("quant", "ding", 0.8)], em=["monte", "quant"]),
            OUTRO(),
        ],
        "algo": [
            dict(id="s1", kind="hook", flash=True, text="While you sleep, a robot is taking trades.", p=dict(line1="While you sleep", line2="a robot trades", key2="robot", hits=["robot"], chart={**chart_spec(swing, arrows=algo_arrows, top=470, h=700)}),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("robot", "glitch", 0.7)], em=["robot", "sleep"]),
            dict(id="s2", kind="flow", text="Algo trading means turning a strategy into rules a computer can follow. Data comes in, the rules look for a signal, risk gets checked, and the order goes out, in milliseconds.",
                 p=dict(title="How an algo trades", steps=[{"t": "Market data", "s": "prices, volume, order flow", "cue": "data"}, {"t": "Rules find a signal", "s": "your strategy, as code", "cue": "rules#2"}, {"t": "Risk check", "s": "size, stop, daily limit", "cue": "risk"}, {"t": "Order sent", "s": "in milliseconds", "cue": "order"}]),
                 sfx=[("start", "swipe", 0.8), ("data", "pop", 0.8), ("rules#2", "pop", 0.8), ("risk", "pop", 0.8), ("order", "ding", 0.8)], em=["rules", "signal", "milliseconds"]),
            dict(id="s3", kind="chart", text="No fear, no greed, no hesitation. It takes every setup exactly the same way.", p=dict(title="Every setup, the same way", chart=chart_spec(swing, arrows=[a | {"cue": None} for a in algo_arrows]), reveal=[0, 0.9]),
                 sfx=[("start", "swipe", 0.8), ("fear", "tick", 0.7), ("greed", "tick", 0.7), ("hesitation", "tick", 0.7), ("every", "pop", 0.8)], em=["fear", "greed", "every"]),
            dict(id="s4", kind="equity", text="But a robot is only as good as its edge. Most fail because they are fitted to the past, and fall apart on new data.",
                 p=dict(title="Backtest vs live", runs=[{"v": [round(x * 0.9, 2) for x in walk(3, 40, 0.68, 0.5, 0.4)], "color": "#f1f1f4", "label": "BACKTEST"}, {"v": walk(8, 40, 0.42, 0.5, 0.45), "color": "#ff8a1f", "label": "LIVE"}], target=None, dd=None, shake=["apart"]),
                 sfx=[("start", "whoosh", 0.8), ("past", "glitch", 0.6), ("apart", "boom", 0.7)], em=["edge", "past", "apart"]),
            dict(id="s5", kind="checklist", text="So test it on data it has never seen, include fees and slippage, and start small.",
                 p=dict(title="Before you go live", items=[{"text": "Test on unseen data", "ok": True, "cue": "test"}, {"text": "Include fees and slippage", "ok": True, "cue": "fees"}, {"text": "Start small", "ok": True, "cue": "small"}]),
                 sfx=[("start", "swipe", 0.8), ("test", "pop", 0.9), ("fees", "pop", 0.9), ("small", "ding", 0.8)], em=["unseen", "fees", "small"]),
            dict(id="s6", kind="shot", flash=True, text="The Jordan Terminal lets you practise and test strategies on a live simulated market.", p=dict(title="Build it. Test it.", img=IMG["sim"], zoom=1.35, panX=-6, top=400, pill="◆ JORDAN TERMINAL"),
                 sfx=[("start", "whoosh", 0.9), ("jordan", "ding", 0.8)], em=["test", "jordan"]),
            OUTRO(),
        ],
        "propfirm": [
            dict(id="s1", kind="statement", flash=True, text="Trade one hundred thousand dollars without risking your own money.", p=dict(line1="Trade without", line2="your own money", key2="risking", num=100000, prefix="$", numCue="hundred", top=330),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("hundred", "riser", 0.6), ("money", "ding", 0.8)], em=["hundred", "own"]),
            dict(id="s2", kind="flow", text="A prop firm gives you its capital. First you pass an evaluation: hit a profit target without breaking the rules. Pass, and you keep up to ninety percent of the profit.",
                 p=dict(title="How a prop firm works", steps=[{"t": "Pay for an evaluation", "s": "a one-time fee", "cue": "evaluation"}, {"t": "Hit the profit target", "s": "without breaking a rule", "cue": "target"}, {"t": "Get funded", "s": "trade the firm's capital", "cue": "pass#2"}, {"t": "Keep up to 90%", "s": "of the profit", "cue": "ninety"}]),
                 sfx=[("start", "swipe", 0.8), ("evaluation", "pop", 0.8), ("target", "pop", 0.8), ("pass#2", "pop", 0.8), ("ninety", "ding", 0.9)], em=["capital", "target", "ninety"]),
            dict(id="s3", kind="equity", text="The catch is the drawdown limit. Touch it once and the account is gone, even if you were up a minute before.",
                 p=dict(title="One line decides everything", runs=[{"v": pf_pass, "color": "#f1f1f4", "end": "pass"}, {"v": pf_fail, "color": "#ff8a1f", "end": "fail"}], target=3.0, dd=-2.5, targetLabel="TARGET +$3,000", ddLabel="MAX DRAWDOWN −$2,500", shake=["gone"]),
                 sfx=[("start", "whoosh", 0.8), ("touch", "tick", 0.8), ("gone", "boom", 0.9)], em=["drawdown", "gone"]),
            dict(id="s4", kind="checklist", text="Three rules decide everything: the profit target, the maximum drawdown, and the daily loss limit.",
                 p=dict(title="The 3 rules", items=[{"text": "Profit target", "ok": True, "cue": "profit"}, {"text": "Maximum drawdown", "ok": False, "cue": "maximum"}, {"text": "Daily loss limit", "ok": False, "cue": "daily"}]),
                 sfx=[("start", "swipe", 0.8), ("profit", "pop", 0.9), ("maximum", "pop", 0.9), ("daily", "pop", 0.9)], em=["target", "drawdown", "daily"]),
            dict(id="s5", kind="statement", text="Most traders fail because they risk too much, too fast. Small size, every day, is what passes.", p=dict(line1="Most traders fail", line2="risking too much", key2="risk", sub="Small size, every day, passes.", subCue="small", top=430),
                 sfx=[("start", "whoosh", 0.8), ("risk", "glitch", 0.7), ("small", "ding", 0.8)], em=["fail", "risk", "small"]),
            dict(id="s6", kind="shot", flash=True, text="Practise the exact rules risk free in the Jordan Sim, before you pay for an evaluation.", p=dict(title="Practise before you pay", img=IMG["sim"], zoom=1.35, panX=-6, top=400, pill="◆ JORDAN SIM"),
                 sfx=[("start", "whoosh", 0.9), ("sim", "ding", 0.8)], em=["risk", "sim"]),
            OUTRO(),
        ],
        "footprint": [
            dict(id="s1", kind="footprint", flash=True, text="Candles lie. The footprint shows who is really trading inside them.", p=dict(title="Candles lie", titleSize=60, rows=rows, candles=fp[:2]),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("inside", "glitch", 0.6)], em=["lie", "inside"]),
            dict(id="s2", kind="footprint", text="Every price inside the candle shows two numbers. Sellers hitting the bid on the left. Buyers lifting the offer on the right.", p=dict(title="Bid × Ask at every price", rows=rows, candles=fp[:3], deltaCue="buyers"),
                 sfx=[("start", "swipe", 0.8), ("sellers", "pop", 0.8), ("buyers", "pop", 0.8)], em=["sellers", "buyers"]),
            dict(id="s3", kind="footprint", text="When buyers beat sellers three to one on the diagonal, that's an imbalance. Three stacked imbalances means aggressive buyers are in control.",
                 p=dict(title="Stacked imbalances", rows=rows, candles=fp[:3], hot=[[1, 5], [1, 6], [1, 7]], hotCue="stacked", badge={"text": "3 STACKED BUY IMBALANCES", "cls": "g", "cue": "control", "top": 1010}),
                 sfx=[("start", "swipe", 0.8), ("imbalance", "tick", 0.8), ("stacked", "pop_hi", 0.9), ("control", "ding", 0.8)], em=["imbalance", "stacked", "control"]),
            dict(id="s4", kind="footprint", text="The entry: price pulls back into the stacked imbalance, holds, and delta turns positive. Go long, with the stop just below the imbalance.",
                 p=dict(title="The entry", rows=rows, candles=fp, hot=[[1, 5], [1, 6], [1, 7]], hotCue="stacked", entry={"row": 5, "stopRow": 7, "label": "LONG", "cue": "long", "stopCue": "stop"}),
                 sfx=[("start", "swipe", 0.8), ("holds", "tick", 0.8), ("long", "pop", 1.0), ("stop", "pop", 0.8)], em=["holds", "positive", "long", "stop"]),
            dict(id="s5", kind="statement", text="Absorption is the warning sign: huge selling, but price won't drop. Someone big is buying.", p=dict(line1="Huge selling", line2="price won't drop", key2="price", sub="Someone big is buying.", subCue="someone", top=430),
                 sfx=[("start", "whoosh", 0.8), ("price", "glitch", 0.6), ("someone", "boom", 0.7)], em=["absorption", "big"]),
            dict(id="s6", kind="shot", flash=True, text="Practise reading order flow live, in the Jordan Sim.", p=dict(title="Read the tape. Live.", img=IMG["sim"], zoom=1.4, panX=-4, top=400, pill="◆ JORDAN SIM"),
                 sfx=[("start", "whoosh", 0.9), ("sim", "ding", 0.8)], em=["live", "sim"]),
            OUTRO(),
        ],
        "cpi": [
            dict(id="s1", kind="statement", flash=True, text="One number, at eight thirty in the morning, can move the whole market four percent.", p=dict(line1="One number at 8:30", line2="moves the market", key2="move", num=4, prefix="", suffix="%", numCue="four", top=330),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("four", "glitch", 0.7)], em=["one", "four"]),
            dict(id="s2", kind="versus", text="In September twenty twenty two, a hot CPI sent the S and P down four point three percent in one day. Two months later, a cool one sent it up five and a half.",
                 p=dict(title="CPI days, measured", cards=[{"tag": "SEP 13 2022 · HOT CPI", "title": "S&P 500 in one day", "num": -4.3, "dec": 1, "suffix": "%", "color": "#ff4d5e", "bg": "rgba(255,77,94,.16)", "cue": "september"}, {"tag": "NOV 10 2022 · COOL CPI", "title": "S&P 500 in one day", "num": 5.5, "dec": 1, "prefix": "+", "suffix": "%", "color": "#22c08f", "bg": "rgba(34,192,143,.16)", "cue": "later"}]),
                 sfx=[("start", "swipe", 0.8), ("hot", "boom", 0.7), ("cool", "ding", 0.8)], em=["hot", "cool"]),
            dict(id="s3", kind="flow", text="To get ahead of it, watch the inputs that move before the report: gasoline prices, used cars, and rents.",
                 p=dict(title="What moves CPI first", steps=[{"t": "Gasoline prices", "s": "daily, at the pump", "cue": "gasoline"}, {"t": "Used cars", "s": "auction prices lead", "cue": "used"}, {"t": "Rents / shelter", "s": "the biggest weight", "cue": "rents"}, {"t": "CPI report", "s": "8:30 New York", "cue": "report"}]),
                 sfx=[("start", "swipe", 0.8), ("gasoline", "pop", 0.8), ("used", "pop", 0.8), ("rents", "pop", 0.8)], em=["inputs", "gasoline", "rents"]),
            dict(id="s4", kind="versus", text="Then compare the consensus with what the crowd is betting. On Polymarket, traders put real money on every CPI outcome. The gap is the surprise.",
                 p=dict(title="Consensus vs the crowd", cards=[{"tag": "CONSENSUS", "title": "What economists expect", "text": "3.1%", "cue": "consensus"}, {"tag": "POLYMARKET", "title": "Odds of a hotter print", "text": "62%", "color": "#ffa028", "cue": "polymarket"}, {"tag": "THE GAP", "title": "= the surprise to trade", "text": "⚡", "color": "#4fc3f7", "bg": "rgba(79,195,247,.16)", "cue": "gap"}], badge={"text": "example numbers", "cue": "gap", "top": 1140}),
                 sfx=[("start", "swipe", 0.8), ("consensus", "pop", 0.8), ("polymarket", "pop", 0.8), ("gap", "riser", 0.6)], em=["consensus", "crowd", "surprise"]),
            dict(id="s5", kind="shot", flash=True, text="The Jordan Terminal puts it on one screen: the calendar with the consensus, live Polymarket odds, and how the market really reacted to every past surprise.",
                 p=dict(title="All on one screen", img=IMG["hist"], zoom=1.25, panX=-4, top=420, pill="◆ JORDAN TERMINAL"),
                 sfx=[("start", "whoosh", 0.9), ("polymarket", "pop", 0.8), ("surprise", "ding", 0.8)], em=["calendar", "polymarket", "reacted"]),
            dict(id="s6", kind="statement", text="You can't know the number. But you can know the odds, and be ready before it drops.", p=dict(line1="You can't know the number", line2="trade the odds", key2="odds", top=450),
                 sfx=[("start", "whoosh", 0.8), ("odds", "boom", 0.8)], em=["odds", "ready"]),
            OUTRO(),
        ],
        "negrr": [
            dict(id="s1", kind="statement", flash=True, text="Everyone says never risk more than you make. Here's how a negative R:R can pass a funded account.", p=dict(line1="Negative R:R", line2="can pass funded", key2="negative", top=420),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("never", "glitch", 0.6), ("pass", "riser", 0.5)], em=["never", "negative", "pass"]),
            dict(id="s2", kind="versus", text="Risk two dollars to make one. Sounds crazy. But if you win seventy five percent of the time, you still make a quarter of your risk on every trade.",
                 p=dict(title="The math", cards=[{"tag": "RISK : REWARD", "title": "Risk $2 to make $1", "text": "2 : 1", "color": "#ff8a1f", "cue": "risk"}, {"tag": "WIN RATE", "title": "3 wins out of 4", "num": 75, "suffix": "%", "color": "#fff", "cue": "seventy"}, {"tag": "EDGE PER TRADE", "title": "0.75 × 1 − 0.25 × 2", "num": 0.25, "dec": 2, "prefix": "+", "suffix": "R", "color": "#22c08f", "bg": "rgba(34,192,143,.16)", "cue": "quarter"}]),
                 sfx=[("start", "swipe", 0.8), ("risk", "pop", 0.8), ("seventy", "pop", 0.8), ("quarter", "ding", 0.9)], em=["two", "seventy", "quarter"]),
            dict(id="s3", kind="equity", text="And for a funded account, a high win rate means a smoother curve. Fewer losing streaks, so you're less likely to touch the drawdown before the target.",
                 p=dict(title="Smooth beats big", runs=[{"v": nr_smooth, "color": "#f1f1f4", "end": "pass", "label": "75% WIN · 1:2"}, {"v": nr_jag, "color": "#ff8a1f", "end": "fail", "label": "35% WIN · 2:1"}], target=3.0, dd=-2.5, targetLabel="TARGET", ddLabel="DRAWDOWN"),
                 sfx=[("start", "whoosh", 0.8), ("smoother", "pop", 0.8), ("drawdown", "boom", 0.6)], em=["smoother", "streaks", "drawdown"]),
            dict(id="s4", kind="statement", text="The break-even win rate is risk divided by risk plus reward. Risk two to make one, and you need more than sixty seven percent.", p=dict(line1="Break-even win rate", line2="risk ÷ (risk + reward)", size=58, key2="risk", num=67, suffix="%", numCue="sixty", top=330),
                 sfx=[("start", "whoosh", 0.8), ("sixty", "ding", 0.8)], em=["break-even", "sixty"]),
            dict(id="s5", kind="checklist", text="But respect the risk. One loss erases two wins, so never move the stop, cut size after a loss, and prove the win rate first.",
                 p=dict(title="The rules", items=[{"text": "Move the stop", "ok": False, "cue": "never"}, {"text": "Cut size after a loss", "ok": True, "cue": "cut"}, {"text": "Prove the win rate on 100+ trades", "ok": True, "cue": "prove"}]),
                 sfx=[("start", "swipe", 0.8), ("never", "pop", 0.9), ("cut", "pop", 0.9), ("prove", "ding", 0.8)], em=["erases", "never", "prove"]),
            dict(id="s6", kind="shot", flash=True, text="Test your real win rate in the Jordan Sim, and get every trade graded, before you risk an evaluation.", p=dict(title="Prove it first", img=IMG["sim"], zoom=1.35, panX=-6, top=400, pill="◆ JORDAN SIM"),
                 sfx=[("start", "whoosh", 0.9), ("graded", "ding", 0.8)], em=["win", "graded"]),
            OUTRO(),
        ],
        "quant": [
            dict(id="s1", kind="statement", flash=True, text="The richest traders in the world don't guess. They calculate.", p=dict(line1="They don't guess", line2="they calculate", key2="calculate", top=440),
                 sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("calculate", "glitch", 0.6)], em=["guess", "calculate"]),
            dict(id="s2", kind="flow", text="Quant trading means using math and data to find an edge. An idea becomes a hypothesis, the hypothesis is tested on years of data, and only what survives gets traded.",
                 p=dict(title="How quants work", steps=[{"t": "Idea", "s": "a pattern you suspect", "cue": "idea"}, {"t": "Hypothesis", "s": "a rule you can test", "cue": "hypothesis"}, {"t": "Test on years of data", "s": "with costs, out of sample", "cue": "tested"}, {"t": "Trade what survives", "s": "everything else is noise", "cue": "survives"}]),
                 sfx=[("start", "swipe", 0.8), ("idea", "pop", 0.8), ("hypothesis", "pop", 0.8), ("tested", "pop", 0.8), ("survives", "ding", 0.9)], em=["math", "data", "survives"]),
            dict(id="s3", kind="versus", text="Momentum, mean reversion, and pairs trading. Each one is a pattern that history says repeats, measured with statistics.",
                 p=dict(title="Classic quant edges", cards=[{"tag": "MOMENTUM", "title": "Winners keep winning", "text": "↗", "cue": "momentum"}, {"tag": "MEAN REVERSION", "title": "Stretched prices snap back", "text": "↺", "color": "#4fc3f7", "bg": "rgba(79,195,247,.16)", "cue": "mean"}, {"tag": "PAIRS", "title": "Two related stocks drift apart, then converge", "text": "⇄", "color": "#22c08f", "bg": "rgba(34,192,143,.16)", "cue": "pairs"}]),
                 sfx=[("start", "swipe", 0.8), ("momentum", "pop", 0.8), ("mean", "pop", 0.8), ("pairs", "pop", 0.8)], em=["momentum", "reversion", "pairs"]),
            dict(id="s4", kind="statement", text="The real question is always the same: is this edge real, or is it just luck? Statistics answer that, not opinions.", p=dict(line1="Real edge", line2="or just luck?", key2="luck", sub="Statistics answer that. Not opinions.", subCue="statistics", top=430),
                 sfx=[("start", "whoosh", 0.8), ("luck", "glitch", 0.7), ("statistics", "ding", 0.8)], em=["real", "luck", "statistics"]),
            dict(id="s5", kind="shot", flash=True, text="The Jordan Terminal's quant lab runs the regression, the distribution tests, and a Monte Carlo on any stock, in one click.", p=dict(title="The Quant Lab", img=IMG["qnt_top"], zoom=1.3, panX=-4, top=400, pill="◆ JORDAN TERMINAL"),
                 sfx=[("start", "whoosh", 0.9), ("click", "click", 1.0), ("click", "ding", 0.7)], em=["regression", "monte", "click"]),
            OUTRO(),
        ],
    }
