"""Build three deliverables from live data (nothing invented):
  1. gamma_levels_explainer.mp4  - narrated video explaining gamma levels (examples use META's real option chain)
  2. META_report.pdf             - full report: verdict with evidence, levels, fundamentals, gamma levels with formulas, charts
  3. META_video_report.mp4       - narrated video report on META
Narration uses the Windows built-in English voice (System.Speech); video is assembled with ffmpeg.
Usage: .venv\\Scripts\\python.exe scripts\\make_meta_media.py [OUTDIR]
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.api import invest_routes as ir  # noqa: E402
from app.invest import gamma  # noqa: E402

SYM = "META"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join("reports", time.strftime("%Y-%m-%d"))
WORK = os.path.join(OUT, "_work")
os.makedirs(WORK, exist_ok=True)
BG, FG, DIM, AMBER, GREEN, RED, BLUE = "#0b111c", "#e8e8e8", "#8b93a3", "#ffa028", "#2be07e", "#ff4a4a", "#5aa9ff"
plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": FG, "axes.labelcolor": DIM, "xtick.color": DIM, "ytick.color": DIM,
                     "axes.edgecolor": "#2a2f3a", "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG})

# ------------------------------------------------------------------ data
print("loading data ...", flush=True)
V = ir.api_verdict(SYM)
G = gamma.compute(SYM)
SF = gamma.surface(SYM)
SEC = ir._security(SYM)
H = ir._history(SYM, "1y", "1d")
ST = SEC.get("stats") or {}
C = [c for c in H["candles"] if c[4]]
T = np.array([c[0] for c in C])
PX = np.array([c[4] for c in C])
LV = V["levels"]
RR = V.get("risk_reward") or {}
NE = (SEC.get("next_earnings") or [None])[0]
NEXT_EARN = time.strftime("%B %d, %Y", time.gmtime(NE)) if NE else "not published"
TODAY = time.strftime("%B %d, %Y")


def money(x, d=2):
    return f"${x:,.{d}f}"


def big(x):
    a = abs(x)
    s = f"${a / 1e12:.2f}T" if a >= 1e12 else f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M"
    return ("-" if x < 0 else "") + s


# ------------------------------------------------------------------ charts (shared by the PDF and the videos)
def chart_price(path, w=12, h=5.2):
    fig, ax = plt.subplots(figsize=(w, h))
    x = np.arange(len(PX))
    ax.plot(x, PX, color=AMBER, lw=2, label=f"{SYM} close")
    for n, col in ((50, BLUE), (200, "#b48cff")):
        if len(PX) > n:
            sma = np.convolve(PX, np.ones(n) / n, mode="valid")
            ax.plot(x[n - 1:], sma, color=col, lw=1.3, label=f"{n}-day average")
    for lvl, col, name in ((G.get("call_wall"), GREEN, "call wall"), (G.get("zero_gamma"), AMBER, "zero gamma"), (G.get("put_wall"), RED, "put wall")):
        if lvl:
            ax.axhline(lvl, color=col, ls="--", lw=1)
            ax.text(len(PX) + 1, lvl, f" {name} {lvl:,.0f}", color=col, va="center", fontsize=9)
    ticks = np.linspace(0, len(PX) - 1, 6).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([time.strftime("%b %y", time.gmtime(T[i])) for i in ticks])
    ax.set_xlim(0, len(PX) + 28)
    ax.grid(color="#1c222e")
    ax.legend(loc="upper left", frameon=False, labelcolor=FG, fontsize=9)
    ax.set_title(f"{SYM} - 1 year, with today's gamma levels", color=FG, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_strikes(path, w=12, h=5.2):
    rows = [r for r in G["strikes"] if abs(r["strike"] / G["spot"] - 1) <= 0.1 and (r.get("wall_call") or r.get("wall_put"))]
    rows = sorted(sorted(rows, key=lambda r: -max(r["wall_call"], r["wall_put"]))[:30], key=lambda r: r["strike"])
    fig, ax = plt.subplots(figsize=(w, h))
    ks = [r["strike"] for r in rows]
    ax.bar(range(len(ks)), [r["wall_call"] / 1e6 for r in rows], color=GREEN, label="calls (dealers long gamma)")
    ax.bar(range(len(ks)), [-r["wall_put"] / 1e6 for r in rows], color=RED, label="puts (dealers short gamma)")
    ax.set_xticks(range(len(ks)))
    ax.set_xticklabels([f"{k:g}" for k in ks], rotation=60, fontsize=8)
    for lvl, col, name in ((G["call_wall"], GREEN, "CALL WALL"), (G["put_wall"], RED, "PUT WALL")):
        if lvl in ks:
            i = ks.index(lvl)
            ax.annotate(name, (i, 0), xytext=(i, ax.get_ylim()[1] * 0.85 if col == GREEN else ax.get_ylim()[0] * 0.85), color=col, ha="center", fontsize=9,
                        arrowprops={"arrowstyle": "->", "color": col})
    ax.axhline(0, color="#444")
    ax.set_ylabel("$M of hedging per 1% move")
    ax.grid(axis="y", color="#1c222e")
    ax.legend(frameon=False, labelcolor=FG, fontsize=9, loc="lower left")
    ax.set_title(f"{SYM} - hedging concentration by strike (top 30 within 10%)  ·  price {G['spot']:,.2f}", color=FG, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_profile(path, w=12, h=5.2):
    p = G["profile"]
    xs = [q["spot"] for q in p]
    ys = [q["gex"] / 1e6 for q in p]
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(xs, ys, color=AMBER, lw=2.4)
    ax.fill_between(xs, ys, 0, where=[y > 0 for y in ys], color=GREEN, alpha=0.15)
    ax.fill_between(xs, ys, 0, where=[y < 0 for y in ys], color=RED, alpha=0.15)
    ax.axhline(0, color="#555", ls="--")
    ax.axvline(G["spot"], color=BLUE)
    ax.text(G["spot"], max(ys) * 0.95, f" price {G['spot']:,.2f}", color=BLUE, fontsize=9)
    if G.get("zero_gamma"):
        ax.axvline(G["zero_gamma"], color=AMBER, ls=":")
        ax.text(G["zero_gamma"], min(ys) * 0.9, f" zero gamma {G['zero_gamma']:,.2f}", color=AMBER, fontsize=9)
    ax.set_xlabel("hypothetical price")
    ax.set_ylabel("total GEX, $M per 1% move")
    ax.grid(color="#1c222e")
    ax.set_title(f"{SYM} - total dealer gamma if the price moved (+-15%)", color=FG, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_surface(path, w=12, h=6.4, elev=24, azim=-58):
    Z = np.array(SF["z"], dtype=float)
    Zt = np.sign(Z) * np.sqrt(np.abs(Z))
    X, Y = np.meshgrid(SF["strikes"], np.arange(len(SF["expirations"])))
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(BG)
    m = np.abs(Zt).max() or 1
    ax.plot_surface(X, Y, Zt, cmap="jet", vmin=-m, vmax=m, linewidth=0, antialiased=True, rcount=len(SF["expirations"]), ccount=len(SF["strikes"]))
    ax.plot([G["spot"]] * 2, [0, len(SF["expirations"]) - 1], [0, 0], color="white", lw=2)
    ax.set_xlabel("strike", color=DIM)
    ax.set_ylabel("expiration", color=DIM)
    ax.set_zlabel("net gamma (sqrt scale)", color=DIM)
    step = max(1, len(SF["expirations"]) // 6)
    ax.set_yticks(range(0, len(SF["expirations"]), step))
    ax.set_yticklabels([SF["expirations"][i][5:] for i in range(0, len(SF["expirations"]), step)], fontsize=7)
    zt = [-m, -m / 2, 0, m / 2, m]
    ax.set_zticks(zt)
    ax.set_zticklabels([big(np.sign(v) * v * v) for v in zt], fontsize=7)
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.set_pane_color((0.05, 0.08, 0.13, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(f"{SYM} - 3D gamma surface: strike x expiration (white line = price)", color=FG, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_gamma_curve(path, w=12, h=5.2):
    S = 100.0
    K = np.linspace(80, 120, 400)
    fig, ax = plt.subplots(figsize=(w, h))
    for days, col in ((2, RED), (14, AMBER), (45, GREEN), (120, BLUE)):
        Tt = days / 365
        iv = 0.30
        d1 = (np.log(S / K) + 0.5 * iv * iv * Tt) / (iv * np.sqrt(Tt))
        g = np.exp(-0.5 * d1 ** 2) / (math.sqrt(2 * math.pi) * S * iv * np.sqrt(Tt))
        ax.plot(K, g, color=col, lw=2, label=f"{days} days to expiry")
    ax.axvline(S, color="#555", ls="--")
    ax.text(S, ax.get_ylim()[1] * 0.92, "  stock price", color=DIM)
    ax.set_xlabel("strike")
    ax.set_ylabel("gamma (delta change per $1)")
    ax.legend(frameon=False, labelcolor=FG)
    ax.grid(color="#1c222e")
    ax.set_title("Gamma peaks at the money and gets sharper as expiry gets closer (30% volatility)", color=FG, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_regimes(path, w=12, h=5.2):
    rng = np.random.default_rng(7)
    shocks = rng.normal(0, 1, 160)
    pos, neg = [100.0], [100.0]
    for e in shocks:  # illustration only: the same shocks, damped vs amplified by hedging flows
        pos.append(pos[-1] + 0.6 * e - 0.08 * (pos[-1] - 100))
        neg.append(neg[-1] + 0.6 * e + 0.05 * (neg[-1] - 100))
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(pos, color=GREEN, lw=2, label="positive gamma: dealers sell rallies, buy dips -> calmer")
    ax.plot(neg, color=RED, lw=2, label="negative gamma: dealers chase the move -> bigger swings")
    ax.axhline(100, color="#444", ls="--")
    ax.legend(frameon=False, labelcolor=FG, loc="upper left")
    ax.grid(color="#1c222e")
    ax.set_title("Illustration (simulated, same random shocks): how dealer hedging changes the path", color=FG, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


print("charts ...", flush=True)
P = {k: os.path.join(WORK, f"{k}.png") for k in ("price", "strikes", "profile", "surface", "surface2", "curve", "regimes")}
chart_price(P["price"])
chart_strikes(P["strikes"])
chart_profile(P["profile"])
chart_surface(P["surface"])
chart_surface(P["surface2"], elev=35, azim=-125)
chart_gamma_curve(P["curve"])
chart_regimes(P["regimes"])


# ------------------------------------------------------------------ slides + narration -> mp4
def slide(path, title, lines=None, image=None, foot=None):
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
    fig.text(0.04, 0.93, title, fontsize=34, color=AMBER, weight="bold", va="top")
    fig.add_artist(plt.Line2D([0.04, 0.96], [0.86, 0.86], color="#2a2f3a", lw=2))
    if image:
        ax = fig.add_axes([0.04, 0.08, 0.92 if not lines else 0.58, 0.75])
        ax.imshow(plt.imread(image))
        ax.axis("off")
    if lines:
        x0 = 0.65 if image else 0.05
        width = 34 if image else 70
        y = 0.80
        for ln in lines:
            for i, part in enumerate(textwrap.wrap(ln, width) or [""]):
                fig.text(x0, y, ("• " if i == 0 and not ln.startswith(" ") else "  ") + part, fontsize=21 if image else 26, color=FG, va="top",
                         family="DejaVu Sans Mono" if any(ch in ln for ch in "=×Σ√") else "DejaVu Sans")
                y -= 0.048 if image else 0.058
            y -= 0.012
    fig.text(0.04, 0.03, foot or f"JORDAN TERMINAL · data as of {TODAY} · educational, not investment advice", fontsize=13, color=DIM)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def speak(text, wav):
    ps = os.path.join(WORK, "speak.ps1")
    txt = wav + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text)
    with open(ps, "w", encoding="utf-8") as f:
        f.write("param($t,$o)\nAdd-Type -AssemblyName System.Speech\n$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                "try { $s.SelectVoice('Microsoft David Desktop') } catch {}\n$s.Rate = 0\n$s.SetOutputToWaveFile($o)\n$s.Speak([IO.File]::ReadAllText($t))\n$s.Dispose()\n")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, txt, wav], check=True, capture_output=True)


def build_video(name, scenes):
    segs = []
    for i, (img, narration) in enumerate(scenes):
        wav = os.path.join(WORK, f"{name}_{i:02d}.wav")
        seg = os.path.join(WORK, f"{name}_{i:02d}.mp4")
        speak(narration, wav)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", img, "-i", wav, "-af", "apad=pad_dur=0.8", "-c:v", "libx264", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "128k", "-shortest", seg], check=True)
        segs.append(seg)
        print(f"  {name}: scene {i + 1}/{len(scenes)}", flush=True)
    lst = os.path.join(WORK, f"{name}_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{os.path.abspath(s)}'\n" for s in segs)
    out = os.path.join(OUT, f"{name}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
    return out


S = lambda n: os.path.join(WORK, f"s_{n}.png")  # noqa: E731
gw, pw_, zg, sp = G["call_wall"], G["put_wall"], G["zero_gamma"], G["spot"]

print("explainer video ...", flush=True)
slide(S("e1"), "Gamma levels, explained", ["What they are, the formulas behind them, and how to read them on a chart.",
                                           f"Every example uses the real {SYM} option chain from {TODAY}."])
slide(S("e2"), "Who hedges, and why it moves the stock", ["Traders buy calls and puts. Market makers (dealers) sell them.",
      "Dealers do not want to bet on direction, so they hedge by buying or selling the stock.",
      "How much stock they must trade when the price moves is set by GAMMA.",
      "Big open interest = big hedging flows = levels where price often reacts."])
slide(S("e3"), "Gamma: the formula", ["Delta = how much an option moves per $1 in the stock.", "Gamma = how fast delta changes.",
      "Γ = N'(d1) / (S × σ × √T)", "d1 = [ln(S/K) + (r + σ²/2)·T] / (σ√T)", "S price, K strike, σ implied vol, T years to expiry",
      "Highest at the money, sharper near expiry."], image=P["curve"])
slide(S("e4"), "From gamma to GEX (dollar gamma exposure)", ["GEX per strike = Γ × open interest × 100 × S² × 1%", "Calls count +, puts count -",
      "(convention: dealers long calls, short puts)", "Total GEX = sum over all strikes and expiries",
      f"{SYM} today: {big(G['net_gex'])} per 1% move"], image=P["strikes"])
slide(S("e5"), "Positive vs negative gamma", ["Positive: dealers sell into rallies and buy dips. Moves get absorbed - calmer, mean-reverting.",
      "Negative: dealers must sell when price falls and buy when it rises. Moves feed on themselves - bigger, trending."], image=P["regimes"])
slide(S("e6"), "Zero gamma - the flip level", ["Recompute total GEX at many hypothetical prices.", "Where it crosses zero = zero gamma.",
      f"{SYM}: {money(zg)}", "Above it: calmer market. Below it: moves get bigger."], image=P["profile"])
slide(S("e7"), "Call wall and put wall", [f"Call wall = strike with the most call hedging above price: {money(gw, 0)}",
      f"Put wall = most put hedging below price: {money(pw_, 0)}", "Call wall often acts as resistance or a magnet into expiry.",
      "Put wall often acts as support.", "Measured as gamma at each strike if price got there; expiries of 2+ days."], image=P["strikes"])
slide(S("e8"), "The 3D gamma surface", ["Strike x expiration x net gamma.", "Red = dealers long gamma, blue = short gamma.",
      "Near expiries are sharp spikes; far expiries are smooth.", "In the terminal: drag to rotate, scroll to zoom, hover for values."], image=P["surface"])
slide(S("e9"), "How to use it - and its limits", ["Context, not a trade signal. Combine with trend, news and risk.",
      "Open interest is end of day; it changes overnight.", "The dealer side is a convention, not a known fact.", "Same-day (0DTE) flows are not visible.",
      "Levels shift as price moves and options expire."])
slide(S("e10"), f"{SYM} right now", [f"Price {money(sp)}", f"Net gamma {G['regime'].lower()} ({big(G['net_gex'])} per 1%)", f"Call wall {money(gw, 0)} · Put wall {money(pw_, 0)}",
      f"Zero gamma {money(zg)}", "Open GEX GAMMA LEVELS in the terminal for any optionable stock."], image=P["price"])
ex = [
    (S("e1"), f"This video explains gamma levels: what they are, the formulas behind them, and how to read them. Every example uses the real option chain of {SYM} from today."),
    (S("e2"), "Traders buy calls and puts, and market makers sell them. Market makers do not want to bet on direction, so they hedge by buying or selling the stock itself. How much stock they must trade when the price moves is decided by gamma. Where there is a lot of open interest, hedging flows are large, and that is where the price often reacts."),
    (S("e3"), "Delta tells you how much an option moves for a one dollar move in the stock. Gamma tells you how fast delta changes. In the Black Scholes model, gamma equals the normal density of d one, divided by the stock price, times volatility, times the square root of time. As the chart shows, gamma is highest at the money, and it gets much sharper as expiry gets closer."),
    (S("e4"), f"To turn gamma into dollars, multiply it by open interest, by one hundred shares per contract, by the stock price squared, and by one percent. That is the dollar amount dealers must hedge for each one percent move. Calls count as positive and puts as negative, because by convention dealers are long the calls and short the puts that customers buy. Summed over every strike and expiry, {SYM} has about {big(G['net_gex']).replace('$', '')} dollars of gamma per one percent move today."),
    (S("e5"), "When total gamma is positive, dealers sell into rallies and buy into dips, so moves get absorbed and the market tends to be calmer. When gamma is negative, dealers must sell when the price falls and buy when it rises, so moves feed on themselves and swings get bigger. The chart is a simulation with the same random shocks, to show the difference."),
    (S("e6"), f"If we recompute total gamma at many hypothetical prices, we get this curve. The point where it crosses zero is the zero gamma level, or the flip. For {SYM} it is about {zg:,.0f} dollars. Above it, the market tends to be calmer. Below it, moves tend to get bigger."),
    (S("e7"), f"The call wall is the strike above the price with the most call hedging. For {SYM} it is {gw:,.0f}. It often acts as resistance, or as a magnet into expiry. The put wall is the strike below the price with the most put hedging, here {pw_:,.0f}, and it often acts as support."),
    (S("e8"), "The three dimensional surface shows net gamma for every strike and every expiration. Red means dealers are long gamma, blue means they are short. Near expiries show sharp spikes, far expiries are smoother. In the terminal you can rotate it, zoom in, and hover over any point to see the exact value."),
    (S("e9"), "Remember the limits. This is context, not a trade signal. Open interest is updated at the end of the day. The dealer side is a convention, not a known fact. Same day option flows are not visible, and the levels move as the price moves and options expire."),
    (S("e10"), f"Right now {SYM} trades at {sp:,.0f} dollars with positive gamma. The call wall is {gw:,.0f}, the put wall is {pw_:,.0f}, and the zero gamma level is about {zg:,.0f}. You can open the gamma levels page in the terminal for any stock with listed options."),
]
v1 = build_video("gamma_levels_explainer", ex)

print("META video report ...", flush=True)
buy = [f"{r['title']} ({'; '.join(f'{a} {b}' for a, b in r['evidence'])})" for r in V["why_buy"]][:6]
nots = [f"{r['title']} ({'; '.join(f'{a} {b}' for a, b in r['evidence'])})" for r in V["why_not"]]
slide(S("m1"), f"{SYM} · video report · {TODAY}", [f"Price {money(LV['price'])}", f"Verdict: {V['stance']} (score {V['score']:+d}, {V['votes']['for']} for / {V['votes']['against']} against)",
      f"Market cap {big(SEC.get('market_cap') or 0)} · next earnings {NEXT_EARN}"])
slide(S("m2"), "Price and trend", [f"Price {money(LV['price'])}", f"50-day {money(LV['sma50'])} · 200-day {money(LV['sma200'])}", f"52-week range {money(LV['low52'])} - {money(LV['high52'])}",
      f"RSI(14) {V['rsi']}"], image=P["price"])
slide(S("m3"), "Why buy", buy)
slide(S("m4"), "Why not - and reward vs risk", nots + [f"Analyst mean target {money(LV['target_mean'])} (+{RR.get('upside_pct')}%)", f"Support {money(RR.get('support') or 0)} (-{RR.get('downside_pct')}%)",
      f"Reward : risk {RR.get('ratio')} : 1"])
slide(S("m5"), "Valuation and quality", [f"P/E {ST.get('pe'):.1f} · forward P/E {ST.get('fwd_pe'):.1f} · PEG {ST.get('peg')}", f"Revenue {big(ST.get('revenue') or 0)} · growth {ST.get('rev_growth') * 100:.0f}%",
      f"Operating margin {ST.get('op_margin') * 100:.1f}% · ROE {ST.get('roe') * 100:.1f}%", f"Free cash flow {big(ST.get('fcf') or 0)}", f"Cash {big(ST.get('total_cash') or 0)} · debt {big(ST.get('total_debt') or 0)}",
      f"Earnings growth {ST.get('earn_growth') * 100:.1f}% (the one red flag)"])
slide(S("m6"), "Gamma levels", [f"Net gamma {G['regime'].lower()}: {big(G['net_gex'])} per 1%", f"Call wall {money(gw, 0)}", f"Put wall {money(pw_, 0)}", f"Zero gamma {money(zg)}",
      "Price above zero gamma: calmer, mean-reverting tape expected."], image=P["strikes"])
slide(S("m7"), "Gamma surface", [f"{len(SF['expirations'])} expiries · {len(SF['strikes'])} strikes", "Red = dealers long gamma", "Blue = dealers short gamma"], image=P["surface2"])
slide(S("m8"), "Bottom line", [f"Model verdict: {V['stance']} - quality, growth, valuation and trend line up.", f"Main risk: earnings growth {ST.get('earn_growth') * 100:.1f}%; reward:risk to target only {RR.get('ratio')}:1.",
      f"Options: {money(pw_, 0)} put wall below, {money(gw, 0)} call wall above, flip near {money(zg, 0)}.", f"Next catalyst: earnings {NEXT_EARN}.", "Not investment advice."])
mr = [
    (S("m1"), f"This is the video report on {SYM}, Meta Platforms, for {TODAY}. The stock trades at {LV['price']:,.0f} dollars. The terminal's model rates it a {V['stance'].lower()}, with a score of {V['score']} out of one hundred, and {V['votes']['for']} reasons for against {V['votes']['against']} against. The next earnings report is expected on {NEXT_EARN}."),
    (S("m2"), f"The trend is up. The price is above both the fifty day average at {LV['sma50']:,.0f} and the two hundred day average at {LV['sma200']:,.0f}. The fifty two week range is {LV['low52']:,.0f} to {LV['high52']:,.0f}, and the R S I is {V['rsi']}, which is neutral. The dashed lines are today's gamma levels."),
    (S("m3"), f"Why buy. Return on equity is {ST.get('roe') * 100:.0f} percent. The operating margin is {ST.get('op_margin') * 100:.0f} percent. Free cash flow is about {(ST.get('fcf') or 0) / 1e9:.1f} billion dollars, and revenue is growing {ST.get('rev_growth') * 100:.0f} percent a year. Growth looks cheap, with a P E G of {ST.get('peg')}, and the analyst consensus is a strong buy."),
    (S("m4"), f"Why not. Earnings fell {abs(ST.get('earn_growth') * 100):.0f} percent compared with a year ago. The mean analyst target is {LV['target_mean']:,.0f} dollars, about {RR.get('upside_pct')} percent above the price, while support near {(RR.get('support') or 0):,.0f} is {RR.get('downside_pct')} percent below. That is a reward to risk of only {RR.get('ratio')} to one at this price."),
    (S("m5"), f"On valuation, the price to earnings ratio is {ST.get('pe'):.0f}, and the forward ratio is {ST.get('fwd_pe'):.0f}. Revenue is about {(ST.get('revenue') or 0) / 1e9:.0f} billion dollars. The company holds about {(ST.get('total_cash') or 0) / 1e9:.0f} billion in cash against {(ST.get('total_debt') or 0) / 1e9:.0f} billion of debt."),
    (S("m6"), f"In options, net gamma is positive, so dealers tend to dampen moves. The call wall is {gw:,.0f}, the put wall is {pw_:,.0f}, and the zero gamma level is about {zg:,.0f}. With the price above the flip, a calmer, range bound tape is more likely, unless the price breaks below about {zg:,.0f}."),
    (S("m7"), f"This is the gamma surface across {len(SF['expirations'])} expirations. Red areas are where dealers are long gamma, blue where they are short."),
    (S("m8"), f"Bottom line. Quality, growth, valuation and trend all line up, which is why the model says {V['stance'].lower()}. The main risks are falling earnings and a modest reward to risk at today's price. Watch the put wall at {pw_:,.0f} and the call wall at {gw:,.0f}, and the earnings report on {NEXT_EARN}. This is not investment advice."),
]
v2 = build_video(f"{SYM}_video_report", mr)

# ------------------------------------------------------------------ PDF report
print("PDF report ...", flush=True)
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

pdf = os.path.join(OUT, f"{SYM}_report.pdf")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=colors.HexColor("#c26a00"), fontSize=20)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#c26a00"), fontSize=14, spaceBefore=10)
BD = ParagraphStyle("bd", parent=ss["BodyText"], fontSize=10, leading=14)
MONO = ParagraphStyle("mono", parent=BD, fontName="Courier", fontSize=9.5, leading=13, backColor=colors.HexColor("#f2f2f2"), borderPadding=6)
SM = ParagraphStyle("sm", parent=BD, fontSize=8, textColor=colors.grey)


def tbl(rows, widths=None):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b2230")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f6f6")])]))
    return t


def img(path, w=17):
    from PIL import Image as PI
    W_, H_ = PI.open(path).size
    return Image(path, width=w * cm, height=w * cm * H_ / W_)


story = [Paragraph(f"{SYM} · Meta Platforms · full report", H1), Paragraph(f"Data as of {TODAY}. Built by JORDAN TERMINAL from Yahoo Finance quotes, fundamentals and the listed option chain. Educational, not investment advice.", SM), Spacer(1, 8)]
story += [Paragraph("1. Summary", H2), tbl([["Item", "Value"], ["Price", money(LV["price"])], ["Verdict", f"{V['stance']} (score {V['score']:+d}; {V['votes']['for']} for / {V['votes']['against']} against)"],
                                            ["Market cap", big(SEC.get("market_cap") or 0)], ["52-week range", f"{money(LV['low52'])} - {money(LV['high52'])}"],
                                            ["Analyst targets (low / mean / high)", f"{money(LV['target_low'])} / {money(LV['target_mean'])} / {money(LV['target_high'])} ({(SEC.get('target') or {}).get('n')} analysts)"],
                                            ["Reward : risk", f"+{RR.get('upside_pct')}% to mean target vs -{RR.get('downside_pct')}% to support {money(RR.get('support') or 0)} = {RR.get('ratio')} : 1"],
                                            ["Next earnings", NEXT_EARN], ["Gamma", f"{G['regime']} net GEX {big(G['net_gex'])} per 1%; call wall {money(gw, 0)}, put wall {money(pw_, 0)}, zero gamma {money(zg)}"]],
                                           [5.5 * cm, 11.5 * cm])]
story += [Paragraph("Key points", H2)] + [Paragraph("• " + t, BD) for t in [
    f"Trend: price {money(LV['price'])} is above the 50-day ({money(LV['sma50'])}) and 200-day ({money(LV['sma200'])}) averages.",
    f"Quality: operating margin {ST.get('op_margin') * 100:.1f}%, ROE {ST.get('roe') * 100:.1f}%, free cash flow {big(ST.get('fcf') or 0)}.",
    f"Growth: revenue +{ST.get('rev_growth') * 100:.0f}%, but earnings {ST.get('earn_growth') * 100:.1f}% year on year - the one red flag.",
    f"Valuation: P/E {ST.get('pe'):.1f}, forward P/E {ST.get('fwd_pe'):.1f}, PEG {ST.get('peg')} (growth priced reasonably).",
    f"Options: positive gamma above {money(zg, 0)} - calmer tape expected; put wall {money(pw_, 0)} below, call wall {money(gw, 0)} above.",
    f"Reward:risk to the mean target is only {RR.get('ratio')}:1 at today's price; the next catalyst is earnings on {NEXT_EARN}."]]
story += [Spacer(1, 6), img(P["price"])]
story += [PageBreak(), Paragraph("2. Why buy / why not / why hold (model evidence)", H2),
          tbl([["Side", "Reason", "Evidence"]] + [["BUY", r["title"], "; ".join(f"{a} {b}" for a, b in r["evidence"])] for r in V["why_buy"]]
              + [["NOT", r["title"], "; ".join(f"{a} {b}" for a, b in r["evidence"])] for r in V["why_not"]]
              + [["HOLD", r["title"], "; ".join(f"{a} {b}" for a, b in r["evidence"])] for r in V["why_hold"]], [1.4 * cm, 5.6 * cm, 10 * cm]),
          Paragraph(V["method"], SM)]
story += [Paragraph("3. Fundamentals", H2), tbl([["Metric", "Value", "Metric", "Value"],
          ["P/E (TTM)", f"{ST.get('pe'):.1f}", "Revenue (TTM)", big(ST.get("revenue") or 0)], ["Forward P/E", f"{ST.get('fwd_pe'):.1f}", "Revenue growth", f"{ST.get('rev_growth') * 100:.1f}%"],
          ["PEG", f"{ST.get('peg')}", "Earnings growth", f"{ST.get('earn_growth') * 100:.1f}%"], ["EV / EBITDA", f"{ST.get('ev_ebitda'):.1f}", "Gross margin", f"{ST.get('gross_margin') * 100:.1f}%"],
          ["Price / book", f"{ST.get('pb'):.1f}", "Operating margin", f"{ST.get('op_margin') * 100:.1f}%"], ["Beta", f"{ST.get('beta')}", "ROE", f"{ST.get('roe') * 100:.1f}%"],
          ["Dividend yield", f"{ST.get('div_yield') * 100:.2f}%", "Free cash flow", big(ST.get("fcf") or 0)], ["Short % of float", f"{ST.get('short_pct_float') * 100:.2f}%", "Cash / debt", f"{big(ST.get('total_cash') or 0)} / {big(ST.get('total_debt') or 0)}"]],
          [3.5 * cm, 3 * cm, 4 * cm, 6.5 * cm])]
story += [Paragraph("4. News tone (last 7 days)", H2), Paragraph(f"{V['news']['n']} headlines, net tone {V['news']['score']:+d}.", BD)] + [Paragraph(("▲ " if n["tone"] > 0 else "▼ " if n["tone"] < 0 else "• ") + n["title"], BD) for n in V["news"]["items"][:8]]
story += [PageBreak(), Paragraph("5. Gamma levels - formulas and method", H2),
          Paragraph("Black-Scholes gamma for each listed option (from its own implied volatility):", BD),
          Paragraph("d1 = [ ln(S/K) + (r + σ²/2)·T ] / (σ·√T)<br/>Γ = N'(d1) / (S·σ·√T),   N'(x) = e^(-x²/2) / √(2π)", MONO),
          Paragraph("Dollar gamma exposure per strike, per 1% move in the stock:", BD),
          Paragraph("GEX(K) = Γ × OI × 100 × S² × 0.01 × sign,   sign = +1 calls, -1 puts<br/>Net GEX = Σ over strikes and expiries of GEX(K)", MONO),
          Paragraph("Zero gamma: total GEX recomputed at 61 hypothetical prices from -15% to +15%; the price where it crosses zero (linear interpolation). "
                    "Call wall / put wall: the strike above / below the price with the largest hedging concentration, measured as Γ evaluated at S = K × OI × 100 × K² × 1%, "
                    "using expiries 2 days or more away so a single same-day strike does not dominate.", BD),
          Paragraph(f"Inputs: {G['expirations']} expiries within 45 days, {G['n_options']} options with open interest; r = 4%. Sign convention: customers are net buyers of calls and puts, "
                    "so dealers are long call gamma and short put gamma (the standard convention, not an observed fact).", BD),
          Paragraph("6. Gamma levels - META today", H2),
          tbl([["Level", "Value", "How to read it"], ["Price", money(sp), ""], ["Net GEX", f"{big(G['net_gex'])} per 1% ({G['regime']})",
               "Positive: dealers sell rallies and buy dips, moves tend to be damped" if G["net_gex"] > 0 else "Negative: dealers chase moves, swings tend to be larger"],
               ["Zero gamma", money(zg), "Above: calmer; below: moves get bigger"], ["Call wall", money(gw, 0), "Often resistance / magnet into expiry"], ["Put wall", money(pw_, 0), "Often support"]],
              [3 * cm, 5 * cm, 9 * cm]), Spacer(1, 6), img(P["strikes"]), img(P["profile"]), img(P["surface"]),
          Paragraph("Limits: open interest is end-of-day, dealer positioning is assumed, 0DTE flows are invisible, and levels move with price and time. Use as context with trend, news and risk - not as a standalone signal.", SM)]
SimpleDocTemplate(pdf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm, title=f"{SYM} full report").build(story)
print("DONE", v1, v2, pdf, flush=True)
