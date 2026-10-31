"""BIG MOVERS research PDF: every +-10% stock day in 10 years, why it happened, the distributions behind it, what flagged it the day before, and today's watchlist.

Built from data_cache/bigmoves/{results,live}.json + events.parquet (app/intel/bigmoves*.py). Same look as the /report PDF (report_ui).
Telegram: /bigmoves. API: GET /api/journal/research/bigmoves.pdf. Research only, not investment advice.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle  # noqa: E402
from reportlab.platypus.tableofcontents import TableOfContents  # noqa: E402

from app.intel.bigmoves import OUT, load  # noqa: E402
from app.notify.report_pdf import _Doc, _kpi_tiles  # noqa: E402
from app.notify.report_ui import BODY, H1, H2, INK, MUTED, SMALL, SUB, W, _dtable, _png, _s, _style_ax  # noqa: E402

H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=8, spaceAfter=3, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=10, bulletIndent=2)
CAP = ParagraphStyle("cap", parent=SMALL, alignment=0, spaceBefore=2, spaceAfter=6)
GRN, RED, AMB, BLU, GRY = "#15803d", "#b91c1c", "#d9830c", "#2563eb", "#6b7285"


def P(t: str, st: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(t, st)


def bullets(items: list[str]) -> list[Any]:
    return [Paragraph(x, BUL, bulletText="•") for x in items]


def callout(text: str, tone: str = "amber", title: str | None = None) -> Table:
    """Highlighted key-finding box (the 'marker')."""
    bg, edge = {"amber": ("#fff6e5", AMB), "green": ("#ecfdf3", GRN), "red": ("#fef2f2", RED), "blue": ("#eff6ff", BLU)}[tone]
    body = (f"<b>{title}</b><br/>" if title else "") + text
    t = Table([[P(body, ParagraphStyle("co", parent=BODY, fontSize=9.6, leading=13.6))]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(edge)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def img(png: bytes, h: float | None = None) -> Image:
    """Full-width image at its own aspect ratio (h is ignored unless the picture would be taller than 230 mm)."""
    from PIL import Image as PImage

    w_px, h_px = PImage.open(io.BytesIO(png)).size
    hh = W * h_px / w_px
    ww = W
    if hh > 230 * mm:
        ww, hh = ww * 230 * mm / hh, 230 * mm
    return Image(io.BytesIO(png), width=ww, height=hh)


def pc(v: float | None, d: int = 1, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def mark(t: str, c: str = AMB) -> str:
    return f"<font color='{c}'><b>{t}</b></font>"


def hl(t: str) -> str:
    """Inline highlighter: yellow background."""
    return f"<span backColor='#fff1b8'>{t}</span>"


# ------------------------------------------------------------------------------------------------ charts
def ch_timeline(E: pd.DataFrame) -> bytes:
    m = E.groupby([E["d"].dt.to_period("M"), "dir"]).size().unstack(fill_value=0)
    x = m.index.to_timestamp()
    vix = load("^VIX")
    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=150)
    ax.bar(x, m.get("UP", 0), width=25, color=GRN, label="+10% days")
    ax.bar(x, -m.get("DOWN", 0), width=25, color=RED, label="-10% days")
    ax.set_ylabel("events per month (down shown negative)", fontsize=7)
    a2 = ax.twinx()
    if vix is not None:
        v = vix["c"].resample("ME").mean()
        v = v[v.index >= x.min()]
        a2.plot(v.index, v.values, color=AMB, lw=1.2, label="VIX (monthly avg)")
        a2.set_ylabel("VIX", fontsize=7, color=AMB)
    ax.legend(loc="upper left", fontsize=7, frameon=False)
    a2.legend(loc="upper right", fontsize=7, frameon=False)
    _style_ax(ax)
    a2.tick_params(labelsize=7)
    ax.set_title("Every +-10% stock day, per month (2,400 liquid US stocks) vs the VIX", fontsize=9, loc="left")
    return _png(fig)


def ch_bars(labels: list[str], vals: list[float], title: str, color: str = BLU, fmt: str = "{:.1f}", h: float = 3.0, horiz: bool = True) -> bytes:
    fig, ax = plt.subplots(figsize=(10, h), dpi=150)
    if horiz:
        ax.barh(range(len(vals)), vals, color=color)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.invert_yaxis()
        for i, v in enumerate(vals):
            ax.text(v, i, " " + fmt.format(v), va="center", fontsize=6.5)
    else:
        ax.bar(range(len(vals)), vals, color=color)
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels(labels, fontsize=7)
        for i, v in enumerate(vals):
            ax.text(i, v, fmt.format(v), ha="center", va="bottom", fontsize=6.5)
    _style_ax(ax)
    ax.set_title(title, fontsize=9, loc="left")
    return _png(fig)


def ch_dist(R: dict[str, Any]) -> bytes:
    from scipy import stats

    D = pd.read_parquet(os.path.join(OUT, "dist.parquet"), columns=["z", "r"])
    D = D[np.isfinite(D["z"])]
    D = D[(D["r"].abs() / D["z"].abs().clip(lower=1e-9) >= 0.003) | (D["r"] == 0)]
    z = D["z"].to_numpy()
    z = z[np.random.default_rng(1).random(len(z)) < 1_500_000 / len(z)]
    fits = {f["name"]: f["params"] for f in R["dist"]["fits"]}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.9), dpi=150)
    bins = np.linspace(-12, 12, 241)
    hgt, e = np.histogram(z, bins=bins, density=True)
    mid = (e[1:] + e[:-1]) / 2
    a1.semilogy(mid, np.where(hgt > 0, hgt, np.nan), ".", ms=2.5, color="#111", label="actual (all stock-days)")
    xs = np.linspace(-12, 12, 600)
    nm = fits["Normal"]
    a1.semilogy(xs, stats.norm.pdf(xs, nm["mu"], nm["sigma"]), color=RED, lw=1.3, label="Normal (Gaussian)")
    t = fits["Student-t"]
    a1.semilogy(xs, stats.t.pdf(xs, t["df"], t["loc"], t["scale"]), color=GRN, lw=1.3, label=f"Student-t (df {t['df']:.1f})")
    lp = fits["Laplace"]
    a1.semilogy(xs, stats.laplace.pdf(xs, lp["loc"], lp["scale"]), color=BLU, lw=1, ls="--", label="Laplace")
    if "NIG" in fits:
        n = fits["NIG"]
        a1.semilogy(xs, stats.norminvgauss.pdf(xs, n["a"], n["b"], n["loc"], n["scale"]), color=AMB, lw=1, ls="-.", label="NIG")
    cy = fits["Cauchy"]
    a1.semilogy(xs, stats.cauchy.pdf(xs, cy["loc"], cy["scale"]), color="#9333ea", lw=0.9, ls=":", label="Cauchy")
    a1.set_ylim(1e-7, 1)
    a1.set_xlabel("daily return in units of the stock's own 20-day volatility (z)", fontsize=7)
    a1.legend(fontsize=6.3, frameon=False)
    a1.set_title("Density, log scale: the tails are where the normal model fails", fontsize=8.5, loc="left")
    qs = np.linspace(0.0005, 0.9995, 400)
    a2.plot(stats.norm.ppf(qs), np.quantile(z, qs) / np.std(z[np.abs(z) < 20]), ".", ms=2.5, color="#111")
    a2.plot([-4, 4], [-4, 4], color=RED, lw=1)
    a2.set_xlabel("normal quantile", fontsize=7)
    a2.set_ylabel("actual quantile (standardised)", fontsize=7)
    a2.set_title("Q-Q plot vs normal: an S-curve = fat tails", fontsize=8.5, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


def ch_ccdf(R: dict[str, Any]) -> bytes:
    D = pd.read_parquet(os.path.join(OUT, "dist.parquet"), columns=["r"])
    r = D["r"].to_numpy()
    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=150)
    for side, col, lab in ((1, GRN, "up moves"), (-1, RED, "down moves")):
        x = np.sort(side * r[side * r > 0.005])
        p = 1 - np.arange(len(x)) / len(x)
        sel = np.unique(len(x) - np.geomspace(1, len(x) - 1, 500).astype(int))
        ax.loglog(x[sel], p[sel] * (len(x) / len(r)), ".", ms=3, color=col, label=lab)
        h = R["dist"]["hill"]["up" if side > 0 else "down"]
        xm = h["x_min"]
        pm = (x >= xm).mean() * len(x) / len(r)
        xx = np.geomspace(xm, 2.5, 50)
        ax.loglog(xx, pm * (xx / xm) ** (-h["alpha"]), color=col, lw=1, ls="--", label=f"power law, alpha = {h['alpha']:.2f}")
    ax.axvline(0.10, color=AMB, lw=1)
    ax.text(0.102, 1e-5, "10%", color=AMB, fontsize=7)
    ax.set_xlabel("size of the daily move (log)", fontsize=7)
    ax.set_ylabel("P(move > x) per stock-day", fontsize=7)
    ax.legend(fontsize=7, frameon=False)
    _style_ax(ax)
    ax.set_title("Tail of daily returns: a straight line on log-log = a power law ('inverse cubic law')", fontsize=8.5, loc="left")
    return _png(fig)


def ch_vol_ratio(R: dict[str, Any]) -> bytes:
    bv = R["dist"]["ten_pct"]["by_vol"]
    fig, ax = plt.subplots(figsize=(10, 3.0), dpi=150)
    xs = np.arange(len(bv))
    ax.bar(xs - 0.2, [b["actual"] for b in bv], 0.4, color="#111", label="actual +-10% days")
    ax.bar(xs + 0.2, [b["normal"] for b in bv], 0.4, color=RED, label="expected if returns were normal")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{b['vol']}\n(" + (f"x{b['ratio']:,.1f}" if b["ratio"] < 1000 else "x1,000+") + ")" for b in bv], fontsize=7)
    ax.set_xlabel("stock's own annualised volatility over the previous 20 days  (x = actual / normal)", fontsize=7)
    ax.legend(fontsize=7, frameon=False)
    _style_ax(ax)
    ax.set_title("How many +-10% days happened vs how many a normal distribution allows", fontsize=8.5, loc="left")
    return _png(fig)


def ch_earn(R: dict[str, Any]) -> bytes:
    D = pd.read_parquet(os.path.join(OUT, "dist.parquet"), columns=["r", "earn"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.2), dpi=150)
    bins = np.linspace(-0.4, 0.4, 161)
    for m, col, lab in ((~D["earn"], GRY, "ordinary days"), (D["earn"], AMB, "earnings reaction days")):
        x = D.loc[m, "r"]
        a1.hist(x[x.abs() < 0.4], bins=bins, density=True, histtype="step", color=col, lw=1.4, label=lab)
    a1.set_yscale("log")
    a1.legend(fontsize=7, frameon=False)
    a1.set_title("Earnings days: a different, much wider distribution", fontsize=8.5, loc="left")
    a1.set_xlabel("daily return", fontsize=7)
    acf_a, acf_r = R["dist"]["acf_abs"], R["dist"]["acf_raw"]
    a2.bar(np.arange(1, 21) - 0.2, acf_a, 0.4, color=AMB, label="|return| (size of moves)")
    a2.bar(np.arange(1, 21) + 0.2, acf_r, 0.4, color=BLU, label="return (direction)")
    a2.axhline(0, color="#111", lw=0.6)
    a2.legend(fontsize=7, frameon=False)
    a2.set_xlabel("lag (sessions)", fontsize=7)
    a2.set_title("Volatility clusters, direction does not", fontsize=8.5, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


def ch_lift(R: dict[str, Any], feats: list[tuple[str, str]]) -> bytes:
    n = len(feats)
    cols = 3
    rows = math.ceil(n / cols)
    fig, axs = plt.subplots(rows, cols, figsize=(10, 2.25 * rows), dpi=150)
    base = R["lift"]["base"]
    for ax, (f, lab) in zip(np.array(axs).ravel(), feats):
        rr = R["lift"]["feats"].get(f, {}).get("rows", [])
        if not rr:
            ax.axis("off")
            continue
        xs = np.arange(len(rr))
        ax.bar(xs, [r["up"] * 100 for r in rr], color=GRN, label="up 10%+")
        ax.bar(xs, [r["dn"] * 100 for r in rr], bottom=[r["up"] * 100 for r in rr], color=RED, label="down 10%+")
        ax.axhline(base * 100, color=AMB, lw=1, ls="--")
        ax.set_xticks(xs)
        ax.set_xticklabels([("lo" if i == 0 else ("hi" if i == len(rr) - 1 else "")) for i in xs] if len(rr) > 3 else [str(r["bin"]) for r in rr], fontsize=6)
        ax.set_title(lab, fontsize=7.5, loc="left")
        ax.tick_params(labelsize=6)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in np.array(axs).ravel()[n:]:
        ax.axis("off")
    fig.suptitle("Chance of a +-10% day TOMORROW (%) by decile of each day-before feature (dashed = base rate)", fontsize=8.5, x=0.01, ha="left")
    return _png(fig)


def ch_model(R: dict[str, Any]) -> bytes:
    m = R["model"]["all days|y"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.2), dpi=150)
    d = m["deciles"]
    a1.bar(range(len(d)), [x["rate"] * 100 for x in d], color=[RED, AMB, AMB, BLU, GRY, GRY])
    a1.axhline(m["base_test"] * 100, color="#111", ls="--", lw=0.8)
    a1.set_xticks(range(len(d)))
    a1.set_xticklabels([x["bucket"].replace("top ", "") for x in d], fontsize=6.5)
    for i, x in enumerate(d):
        a1.text(i, x["rate"] * 100, f"{x['rate'] * 100:.1f}%", ha="center", va="bottom", fontsize=6.5)
    a1.set_title(f"Out-of-sample 2022-26: hit rate by model score bucket (AUC {m['auc_test']:.2f})", fontsize=8, loc="left")
    co = m["coef"][:12][::-1]
    a2.barh(range(len(co)), [c["b"] for c in co], color=[GRN if c["b"] > 0 else RED for c in co])
    a2.set_yticks(range(len(co)))
    a2.set_yticklabels([LABELS.get(c["f"], c["f"]) for c in co], fontsize=6.5)
    a2.set_title("What the model leans on (standardised weights)", fontsize=8, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


def ch_after(E: pd.DataFrame, groups: list[tuple[str, Any, str]], title: str) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 3.3), dpi=150)
    xs = [0, 1, 2, 5, 20, 60]
    for lab, m, col in groups:
        g = E[m]
        if len(g) < 20:
            continue
        y = [0] + [float((g[f"fwd{k}"] - g[f"spy_fwd{k}"]).mean() * 100) for k in (1, 2, 5, 20, 60)]
        ax.plot(range(len(xs)), y, marker="o", ms=3.5, color=col, lw=1.4, label=f"{lab} (n={len(g):,})")
    ax.axhline(0, color="#111", lw=0.6)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(["close of move day", "+1", "+2", "+5", "+20", "+60 sessions"], fontsize=7)
    ax.set_ylabel("avg return minus SPY (%)", fontsize=7)
    ax.legend(fontsize=6.5, frameon=False)
    _style_ax(ax)
    ax.set_title(title, fontsize=8.5, loc="left")
    return _png(fig)


def ch_case(c: dict[str, Any]) -> bytes | None:
    df = load(c["sym"])
    if df is None:
        return None
    d = pd.Timestamp(c["d"])
    i = int(df.index.searchsorted(d))
    w = df.iloc[max(0, i - 70): i + 25]
    if len(w) < 20:
        return None
    fig, (ax, av) = plt.subplots(2, 1, figsize=(10, 3.3), dpi=150, gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    xs = np.arange(len(w))
    up = w["c"] >= w["o"]
    ax.vlines(xs, w["l"], w["h"], color=np.where(up, GRN, RED), lw=0.7)
    ax.bar(xs, (w["c"] - w["o"]).abs().clip(lower=w["c"] * 0.002), bottom=np.minimum(w["o"], w["c"]), color=np.where(up, GRN, RED), width=0.65)
    j = int(np.searchsorted(w.index, d))
    if j < len(w):
        ax.axvspan(j - 0.5, j + 0.5, color=AMB, alpha=0.25)
        ax.annotate(f"{c['r'] * 100:+.1f}%", (j, w["h"].iloc[j]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8, color=AMB, fontweight="bold")
    av.bar(xs, w["v"] / 1e6, color=np.where(up, GRN, RED), width=0.65)
    av.set_ylabel("vol (M)", fontsize=6.5)
    ticks = xs[::max(1, len(xs) // 8)]
    av.set_xticks(ticks)
    av.set_xticklabels([w.index[k].strftime("%d %b %y") for k in ticks], fontsize=6.5)
    _style_ax(ax, av)
    ax.set_title(f"{c['sym']}  {c.get('name') or ''}  -  daily, the move day shaded", fontsize=8.5, loc="left")
    return _png(fig)


def ch_gex(sym: str) -> bytes | None:
    from app.invest import gamma

    g = gamma.compute(sym)
    if not g.get("ok"):
        return None
    pr = g["profile"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.0), dpi=150)
    st = [s for s in g["strikes"] if 0.85 * g["spot"] <= s["strike"] <= 1.15 * g["spot"]]
    a1.bar([s["strike"] for s in st], [s["call"] / 1e6 for s in st], color=GRN, width=(st[1]["strike"] - st[0]["strike"]) * 0.8 if len(st) > 1 else 1, label="call gamma")
    a1.bar([s["strike"] for s in st], [-s["put"] / 1e6 for s in st], color=RED, width=(st[1]["strike"] - st[0]["strike"]) * 0.8 if len(st) > 1 else 1, label="put gamma")
    a1.axvline(g["spot"], color="#111", lw=1)
    a1.set_title(f"{sym}: dealer gamma by strike ($M per 1% move)", fontsize=8, loc="left")
    a1.legend(fontsize=6.5, frameon=False)
    a2.plot([p["spot"] for p in pr], [p["gex"] / 1e6 for p in pr], color=BLU)
    a2.axhline(0, color="#111", lw=0.6)
    a2.axvline(g["spot"], color="#111", lw=1, ls=":")
    if g.get("zero_gamma"):
        a2.axvline(g["zero_gamma"], color=AMB, lw=1, ls="--")
        a2.text(g["zero_gamma"], a2.get_ylim()[1] * 0.85, " zero gamma", color=AMB, fontsize=7)
    a2.set_title("Total dealer gamma if the price moved to x", fontsize=8, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


LABELS = {"rv20": "20-day realised volatility", "rv_ratio": "Volatility now vs 6 months (compression)", "atr_pct": "Average true range % of price",
          "rng_ratio": "10-day range / 60-day range", "bbw_pct": "Bollinger width percentile (squeeze)", "vol_ratio": "Volume 5d / 50d", "ret5": "Return last 5 days",
          "ret20": "Return last 20 days", "ret60": "Return last 60 days", "from_hi": "Distance from 52-week high", "from_lo": "Distance above 52-week low",
          "n_big": "Number of +-10% days in past year", "n_big_up": "Number of +10% days in past year", "n_big_dn": "Number of -10% days in past year",
          "clv1": "Where yesterday closed in its range", "dollar_vol": "Dollar volume (log)", "logcap": "Market cap (log)", "price": "Share price (log)", "vix": "VIX level",
          "vix_chg": "VIX change yesterday", "spy1": "SPY return yesterday", "earn_next": "Earnings report tomorrow", "days_since_earn": "Days since last earnings",
          "shelf30": "S-1/S-3 shelf filed in last 30 days", "obv_div": "OBV vs price divergence (accumulation)", "wy_acc": "Wyckoff accumulation proxy",
          "wy_dist": "Wyckoff distribution proxy", "spring": "Wyckoff spring (last 3 days)", "upthrust": "Wyckoff upthrust (last 3 days)", "beta": "Beta to S&P 500"}


def ch_size_by_cause(E: pd.DataFrame) -> bytes:
    order = E.groupby("cause")["r"].size().sort_values(ascending=False).index.tolist()
    order = [c for c in order if (E["cause"] == c).sum() >= 40]
    fig, ax = plt.subplots(figsize=(10, 3.3), dpi=150)
    data_u = [E.loc[(E["cause"] == c) & (E["r"] > 0), "r"].clip(upper=1.0).to_numpy() * 100 for c in order]
    data_d = [-E.loc[(E["cause"] == c) & (E["r"] < 0), "r"].to_numpy() * 100 for c in order]
    pu = np.arange(len(order)) * 2.6
    b1 = ax.boxplot(data_u, positions=pu - 0.5, widths=0.8, showfliers=False, patch_artist=True)
    b2 = ax.boxplot(data_d, positions=pu + 0.5, widths=0.8, showfliers=False, patch_artist=True)
    for b, col in ((b1, GRN), (b2, RED)):
        for x in b["boxes"]:
            x.set_facecolor(col)
            x.set_alpha(0.55)
        for x in b["medians"]:
            x.set_color("#111")
    ax.set_xticks(pu)
    ax.set_xticklabels([c.replace(" (", "\n(").replace(" / ", "/\n", 1) if len(c) > 18 else c for c in order], fontsize=6)
    ax.set_ylabel("size of move (%)", fontsize=7)
    _style_ax(ax)
    ax.set_title("How big: size of the move by cause (green = up, red = down; box = middle 50%, whiskers = 5-95%)", fontsize=8.5, loc="left")
    return _png(fig)


# ------------------------------------------------------------------------------------------------ build
def build() -> bytes:
    from app.intel.bigmoves_study import load_events

    R = json.load(open(os.path.join(OUT, "results.json")))
    L = json.load(open(os.path.join(OUT, "live.json"))) if os.path.exists(os.path.join(OUT, "live.json")) else {}
    E = load_events()
    o, Dd, mo = R["overview"], R["dist"], R["model"]
    s: list[Any] = []
    # ---------------------------------------------------------------- cover
    s.append(P("BIG MOVERS", H1))
    s.append(P(_s(f"Why stocks move 10% in a day - and what you could have seen the day before  |  {o['first']} to {o['last']}  |  {dt.datetime.now().strftime('%d %b %Y')}"), SUB))
    s.append(Spacer(1, 8))
    s.append(_kpi_tiles([("+-10% days found", f"{o['n_events']:,}", None), ("Stocks", f"{o['n_stocks']:,}", None), ("Stock-days scanned", f"{o['stock_days'] / 1e6:.2f}M", None),
                         ("Base rate per stock-day", pc(o["base_rate"], 2, False), None), ("Up / down", f"{o['n_up']:,} / {o['n_down']:,}", None),
                         ("Median size", pc(o["median_abs"], 1, False), None), ("Biggest up", pc(o["max_up"], 0), "good"), ("Biggest down", pc(o["max_dn"], 0), "bad"),
                         ("Fat-tail factor at 5 sigma", f"x{Dd['actual']['5'] / max(next(f for f in Dd['fits'] if f['name'] == 'Normal')['p5'], 1e-12):,.0f}", "bad"),
                         ("Tail exponent (alpha)", f"{Dd['hill']['up']['alpha']:.2f} / {Dd['hill']['down']['alpha']:.2f}", None),
                         ("Model AUC out-of-sample", f"{mo['all days|y']['auc_test']:.2f}", "good"),
                         ("Top-1% bucket hit rate", pc(mo['all days|y']['deciles'][0]['rate'], 1, False), "good")]))
    s.append(Spacer(1, 8))
    s.append(img(ch_timeline(E), 70))
    s.append(Spacer(1, 6))
    earn_rule = next((r for r in R["rules"] if r["rule"] == "Earnings tomorrow"), None)
    sq_rule = next((r for r in R["rules"] if r["rule"].startswith("Volatility squeeze")), None)
    top = mo["all days|y"]["deciles"][0]
    s.append(callout(
        f"1. <b>Returns are not normal.</b> A 5-sigma day should happen once in ~3.5 million stock-days under a bell curve; it happened {hl(pc(Dd['actual']['5'], 2, False))} of the time "
        f"(about 1 in {1 / Dd['actual']['5']:,.0f}). The best fit is a {mark('Student-t with ~' + str(round(next(f for f in Dd['fits'] if f['name'] == 'Student-t')['params']['df'], 1)) + ' degrees of freedom')}; "
        f"the far tail follows a power law with alpha ~{Dd['hill']['up']['alpha']:.1f} (the 'inverse cubic law').<br/>"
        f"2. <b>Most +-10% days are NOT random.</b> The single strongest day-before flag is a scheduled catalyst: on the session after an earnings report the chance of a +-10% day is "
        f"{hl(pc(earn_rule['rate'], 1, False) if earn_rule else 'n/a')} vs {pc(R['lift']['base'], 2, False)} on a normal day ({earn_rule['lift'] if earn_rule else ''}x).<br/>"
        f"3. <b>The rest is 'personality + regime'.</b> Stocks that already had many +-10% days, high ATR, and a high VIX keep producing them. A simple model built on that, trained 2016-21, "
        f"ranked 2022-26 days with AUC {mo['all days|y']['auc_test']:.2f}; its top 1% of stock-days had a {hl(pc(top['rate'], 1, False))} hit rate.<br/>"
        f"4. <b>Direction is the hard part.</b> The same features predict up and down almost equally. Size is forecastable; sign mostly is not - that is the job of news, positioning and gamma.<br/>"
        f"5. <b>Popular chart lore mostly fails.</b> A Bollinger squeeze makes a +-10% day LESS likely tomorrow ({sq_rule['lift'] if sq_rule else ''}x), and the Wyckoff proxies are close to the base rate.",
        "amber", "THE FIVE THINGS THIS RESEARCH FOUND"))
    s.append(PageBreak())
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("toc0", fontName="Helvetica", fontSize=9, leading=13, textColor=INK, leftIndent=2, rightIndent=8)]
    s.append(P("CONTENTS", ParagraphStyle("toc_title", parent=H2)))
    s.append(toc)
    s.append(PageBreak())

    # ---------------------------------------------------------------- 1. data & method
    s.append(P("1. DATA AND METHOD", H2))
    s.append(P(f"<b>Universe.</b> Every US-listed common stock with a market cap above $300M, a price above $3 and an average volume above 400k shares "
               f"(Yahoo screener, {o['n_stocks']:,} names with at least one event). Days only count when the prior close was at least $3 and the stock traded at least $3M a day "
               f"over the previous 20 sessions, so penny-stock noise is excluded."))
    s.append(P(f"<b>Event.</b> Close-to-close return of at least +10% or at most -10%. That gives <b>{o['n_events']:,}</b> events out of {o['stock_days']:,} stock-days "
               f"({pc(o['base_rate'], 2, False)}, about 1 in {1 / o['base_rate']:.0f})."))
    s.append(P("<b>Why it moved.</b> Every company's SEC EDGAR filing history with exact acceptance times: 8-K item 2.02 = earnings, 1.01 = material agreement, 2.01 = completed "
               "acquisition, 5.02 = management change, 1.03 = bankruptcy, 3.01 = delisting notice, 4.02 = restatement, 7.01/8.01 = other news; 424B prospectus = share offering; "
               "SC TO / DEFM14A = takeover; SC 13D = activist. A filing accepted after 16:00 New York time counts for the next session. Then the day's market breadth "
               "(how many of the 2,400 stocks moved 5%+ the same way) and the stock's industry median separate market-wide and sector days from stock-specific ones. "
               "For recent case studies, the actual headlines were pulled from Google News for that date."))
    s.append(P("<b>The day before.</b> 30 features known at the close of the previous session (volatility, ranges, volume, distance from highs and lows, past big-move count, VIX, "
               "Wyckoff-style patterns, filings, beta, size). The only exception is 'earnings tomorrow': report dates are scheduled weeks in advance, so this is legitimately known. "
               "The base-rate study compares event days with a random 6% sample of all other stock-days (case-control design; rates are re-weighted)."))
    s.append(P("<b>Out-of-sample.</b> Models are fitted on 2016-2021 only and judged on 2022-2026, a period they never saw."))
    s.append(callout("Limits: today's listed stocks only (delisted / bankrupt names are missing, so true down-move frequency is understated); foreign ADRs file 6-K/20-F "
                     "instead of 8-K so their cause is often unknown; option-chain history (gamma, implied volatility) is not free, so gamma is shown live, not backtested.", "blue"))

    # ---------------------------------------------------------------- 2. overview
    s.append(P("2. WHO MOVES 10% AND WHEN", H2))
    cap = o["cap"]
    s.append(img(ch_bars(list(cap), [v["rate"] for v in cap.values()], "+-10% days per stock per year, by market cap", AMB, "{:.1f}", 2.2, horiz=False), 42))
    s.append(P(f"Size is the first filter. A small cap (&lt;$2B) produces about {mark(str(cap['<$2B']['rate']))} +-10% days a year; a mega cap (&gt;$50B) about "
               f"{mark(str(cap['>$50B']['rate']))}. Big companies move 10% almost only on earnings or in a market crash."))
    secs = o["sectors"][:11]
    s.append(img(ch_bars([x["sector"] for x in secs], [x["per_stock_year"] for x in secs], "+-10% days per stock per year, by sector", BLU, "{:.1f}", 3.0), 55))
    yrs = o["by_year"]
    s.append(P("<b>By year.</b> " + ", ".join(f"{y}: {v['UP'] + v['DOWN']:,}" for y, v in yrs.items()) + ". "
               f"2020 alone holds {(yrs.get('2020', {}).get('UP', 0) + yrs.get('2020', {}).get('DOWN', 0)) / o['n_events'] * 100:.0f}% of all events: volatility clusters in time, "
               "which is why the VIX is one of the strongest predictors later in this report. "
               f"Up moves outnumber down moves ({o['n_up']:,} vs {o['n_down']:,}) - but part of that is survivorship (the stocks that crashed to zero are not in today's list)."))
    td = [["Date", "Stocks +-10%", "Of which up", "SPY that day", "What happened"]]
    why = {"2020-03-16": "COVID crash, Fed emergency cut to zero", "2020-03-12": "COVID: Europe travel ban, circuit breaker", "2025-04-09": "Tariff pause (90 days) - biggest rally since 2008",
           "2020-03-24": "Fiscal stimulus deal rally", "2020-03-18": "COVID crash continues", "2020-03-09": "Oil price war + COVID, first circuit breaker",
           "2020-03-13": "National emergency declared, rebound", "2020-03-17": "Rebound after Fed", "2020-03-26": "Stimulus bill passes Senate", "2025-04-04": "China retaliates on tariffs",
           "2025-04-03": "'Liberation Day' tariffs announced", "2020-04-06": "Peak-virus hopes rally", "2020-03-11": "WHO declares a pandemic", "2020-03-20": "Quad-witching in the crash",
           "2020-11-09": "Pfizer vaccine efficacy news (value/reopening surge)", "2025-04-07": "Tariff turmoil, whipsaw"}
    for d in o["top_days"][:12]:
        td.append([d["d"], f"{d['n']:,}", f"{d['up']:,}", pc(d["spy"]), why.get(d["d"], "")])
    s.append(P("<b>The days with the most +-10% movers</b> (each one is a macro shock, not company news):", BODY))
    s.append(_dtable(td, [22 * mm, 22 * mm, 20 * mm, 20 * mm, W - 84 * mm], right=1))
    s.append(Spacer(1, 4))
    ts = [["Stock", "Name", "+-10% days", "of which up", "Biggest up", "Biggest down"]]
    for x in o["top_stocks"][:15]:
        ts.append([x["sym"], (x["name"] or "")[:30], str(x["n"]), str(x["up"]), pc(x["max"], 0), pc(x["min"], 0)])
    s.append(P("<b>Repeat offenders</b> - the stocks with the most +-10% days. They share a profile: small or speculative, high short interest or retail following, "
               "binary businesses (biotech, crypto miners, EV, quantum, AI small caps).", BODY))
    s.append(_dtable(ts, [16 * mm, 62 * mm, 22 * mm, 22 * mm, 26 * mm, 26 * mm], right=2))

    # ---------------------------------------------------------------- 3. distributions
    s.append(PageBreak())
    s.append(P("3. THE MATHEMATICS: WHICH DISTRIBUTION DO STOCK RETURNS FOLLOW?", H2))
    s.append(P("Almost every textbook model of prices - Black-Scholes, Value-at-Risk, Sharpe ratios - starts by assuming that daily returns follow a <b>normal (Gaussian) "
               "distribution</b>, or equivalently that prices are <b>log-normal</b>. If that were true, 10% days would be vanishingly rare for most stocks. "
               f"To test it, every one of the {Dd['n']:,} stock-days was divided by that stock's own volatility over the previous 20 days (z = return / sigma). "
               "If returns were normal, z would be a standard bell curve. It is not."))
    s.append(img(ch_dist(R), 76))
    act = Dd["actual"]
    nrow = next(f for f in Dd["fits"] if f["name"] == "Normal")
    trow = next(f for f in Dd["fits"] if f["name"] == "Student-t")
    tail = [["|z| beyond", "Actual frequency", "Normal says", "Student-t says", "Actual / Normal"]]
    for k in ("3", "4", "5", "6", "8", "10"):
        nk = nrow.get("p" + k) or 0
        tail.append([f"{k} sigma", f"{act[k] * 100:.3f}%  (1 in {1 / act[k]:,.0f})", f"1 in {1 / nk:,.0f}" if nk > 0 else "~never",
                     f"1 in {1 / trow['p' + k]:,.0f}" if trow.get("p" + k) else "n/a", f"x{act[k] / nk:,.0f}" if nk > 1e-300 else "infinite"])
    s.append(_dtable(tail, [24 * mm, 46 * mm, 36 * mm, 34 * mm, W - 140 * mm], right=1))
    s.append(Spacer(1, 4))
    s.append(callout(f"A move of 5 standard deviations should happen once in ~{1 / nrow['p5']:,.0f} days if returns were normal. In real data it happens once every "
                     f"{1 / act['5']:,.0f} stock-days - {hl('about x' + format(act['5'] / nrow['p5'], ',.0f') + ' more often')}. At 8 sigma the normal model says 'never in the "
                     f"life of the universe'; the market delivers it once every {1 / act['8']:,.0f} stock-days. Excess kurtosis of z is {Dd['kurtosis_z']} (normal = 0), skew {Dd['skew_z']}.",
                     "red", "FAT TAILS - THE CORE FACT"))
    s.append(P("3.1 Every candidate distribution, fitted to the same data", H3))
    ft = [["Distribution", "dAIC (0 = best)", "Tail error", "Parameters", "P(|z|>5)", "P(|z|>8)"]]
    for f in Dd["fits"]:
        par = ", ".join(f"{k} {v}" for k, v in list(f["params"].items())[:4])
        te = f.get("tail_err")
        ft.append([f["name"], f"{f['daic']:,.0f}", "n/a" if te is None else f"{te:.2f}", par, f"{(f.get('p5') or 0) * 100:.3f}%", f"{(f.get('p8') or 0) * 100:.4f}%"])
    ft.append(["ACTUAL", "", "", "", f"{act['5'] * 100:.3f}%", f"{act['8'] * 100:.4f}%"])
    s.append(_dtable(ft, [34 * mm, 21 * mm, 17 * mm, W - 122 * mm, 25 * mm, 25 * mm], right=4))
    s.append(P("dAIC: fit of the WHOLE distribution (mostly the centre), all models scored on the same 20,000-day sample; lower is better. Tail error: average |log10(model / actual)| "
               "frequency of moves beyond 3, 4, 5, 6, 8 and 10 sigma - 0 = perfect, 1 = off by 10x. A model can win one and lose the other.", CAP))
    best_c = min(Dd["fits"], key=lambda f: f["daic"])["name"]
    best_t = min([f for f in Dd["fits"] if f.get("tail_err") is not None], key=lambda f: f["tail_err"])["name"]
    s.append(callout(f"Best fit of the whole shape: {hl(best_c)}. Best at the size of the big days: {hl(best_t)}. The normal distribution is last on both.", "green"))
    s.append(P("What each distribution means, and what it says about 10% days:", H3))
    s.extend(bullets([
        "<b>Normal (Gaussian).</b> The bell curve: many small independent shocks adding up (central limit theorem). Its tails fall like exp(-x^2/2), so big moves are essentially impossible. "
        f"{mark('Worst fit here, by far.', RED)} It is still useful for the middle of the distribution - most days really are small.",
        "<b>Log-normal.</b> If log-returns are normal, prices are log-normal: they can't go below zero and up moves can be larger than down moves in % terms. It is the 'rational' price model "
        "behind Black-Scholes. Because it inherits the normal tail, it fails in the same way; the z-test above is exactly a test of the log-normal model.",
        f"<b>Student-t.</b> A normal whose variance is itself random. With few degrees of freedom the tails fall like a power x^-(df+1). Fitted df = {trow['params']['df']} - "
        f"{mark('the best single-shape fit', GRN)}. A df between 3 and 5 is the classic finding for stock returns and means the variance exists but the 4th moment (kurtosis) barely does.",
        "<b>Laplace (double exponential).</b> A sharp peak and straight-line tails on a log plot. It fits the centre well (many days are almost flat) but its exponential tail is too thin for the biggest moves.",
        "<b>Logistic.</b> Similar to the normal with slightly fatter tails; fails beyond 4 sigma.",
        "<b>Normal-Inverse-Gaussian (NIG) / generalised hyperbolic.</b> Used by quants for options and risk: a normal mixed over an inverse-Gaussian variance, with separate control of skew and tails. "
        "Fits well, second only to Student-t.",
        "<b>Stable (Levy) distributions.</b> Mandelbrot's 1963 proposal: sums of heavy-tailed shocks converge to a stable law with index alpha &lt; 2 (alpha = 2 is the normal). "
        f"Fitted alpha = {next((f['params'].get('alpha') for f in Dd['fits'] if f['name'].startswith('Stable')), 'n/a')}. Stable laws have infinite variance, so they "
        f"{mark('over-predict extreme days', AMB)} - real tails are fat but not that fat.",
        "<b>Cauchy.</b> The stable law with alpha = 1: so fat that the mean doesn't exist. Massively over-predicts big days. A useful bound, not a model.",
        f"<b>Normal mixture / jump-diffusion (Merton).</b> Two regimes: a calm normal day ({next(f for f in Dd['fits'] if f['name'].startswith('Normal mix'))['params']['w_calm'] * 100:.0f}% of days) and a "
        f"'jump' day with ~{next(f for f in Dd['fits'] if f['name'].startswith('Normal mix'))['params']['s2'] / next(f for f in Dd['fits'] if f['name'].startswith('Normal mix'))['params']['s1']:.1f}x the volatility. "
        f"This is the most {mark('intuitive', GRN)} picture: on most days information trickles in; on a few days a lump of news (earnings, a deal, a shock) arrives at once.",
        "<b>Power law (Pareto tail).</b> Far in the tail, P(move &gt; x) ~ x^-alpha. See the next chart.",
    ]))
    s.append(img(ch_ccdf(R), 68))
    s.append(callout(f"The tail exponent is alpha = {Dd['hill']['up']['alpha']} for up moves and {Dd['hill']['down']['alpha']} for down moves (Hill estimator, top 1% of days). That is the "
                     f"well-known {mark('inverse cubic law')} (alpha ~ 3) found in stock markets worldwide. Practical meaning: a move twice as big is only ~2^3 = 8 times rarer, "
                     "not millions of times rarer as the normal model says. A 20% day is ~8x rarer than a 10% day; a 40% day ~64x rarer.", "amber"))
    s.append(P("3.2 'It shouldn't happen' - how many 10% days the normal model allows", H3))
    s.append(img(ch_vol_ratio(R), 56))
    s.append(P("For each stock-day, the chance of a +-10% move was computed from a normal distribution with that stock's own recent volatility, and summed. "
               f"For calm stocks the gap is enormous: a stock with 20-30% annual volatility should almost never move 10% if returns were normal, yet it does - "
               f"{hl('those are exactly the events caused by news')}: earnings, takeovers, crashes. For very volatile stocks (&gt;100% a year) the normal model is roughly right "
               "or even too high, because their daily noise is already large; for them a 10% day is just an ordinary day."))
    s.append(P("3.3 Why the tails are fat - the mechanisms", H3))
    s.append(img(ch_earn(R), 58))
    e1, e0 = Dd["earn"], Dd["ordinary"]
    s.extend(bullets([
        f"<b>Information arrives in lumps.</b> On earnings-reaction days the spread of returns (in sigma units) is {e1['sd']} vs {e0['sd']} on ordinary days and the chance of a 10% move is "
        f"{pc(e1['p10'], 1, False)} vs {pc(e0['p10'], 2, False)} - {mark('x' + format(e1['p10'] / max(e0['p10'], 1e-9), '.0f'))}. Earnings, FDA decisions, trial results, court rulings, takeover bids: scheduled or "
        "unscheduled, they are discrete jumps on top of the daily diffusion.",
        f"<b>Volatility clusters (GARCH effect).</b> The size of today's move predicts tomorrow's (autocorrelation of |return| at lag 1 = {Dd['acf_abs'][0]}, still {Dd['acf_abs'][19]} after 20 days) "
        f"while the direction doesn't ({Dd['acf_raw'][0]}). Mixing calm and wild periods produces fat tails even if each day were normal. After a +-10% day there is a "
        f"{pc(R['repeat']['DOWN']['within5'], 0, False)} chance (after a drop) / {pc(R['repeat']['UP']['within5'], 0, False)} (after a jump) of another one within 5 sessions.",
        "<b>Leverage and fear asymmetry.</b> Falling prices raise leverage and fear, so volatility rises on the way down (negative skew in z). Crash days are fewer than jump days, but they hit the whole market at once.",
        "<b>Liquidity holes and dealer gamma.</b> When market makers are short options (negative gamma) they must sell into falls and buy into rallies, which amplifies moves (section 7).",
        "<b>Crowding, leverage and forced flows.</b> Short squeezes, margin calls, ETF/index rebalances, momentum funds de-grossing - all are feedback loops: price change causes more price change.",
        "<b>Reflexivity and retail attention.</b> Social-media and options-driven names (meme stocks, quantum, AI small caps) show clusters of +-10% days without any filing.",
    ]))

    # ---------------------------------------------------------------- 4. causes
    s.append(PageBreak())
    s.append(P("4. WHY THEY MOVED - THE CAUSES", H2))
    sh = R["causes"]["share"]
    s.append(img(ch_bars(list(sh), [v * 100 for v in sh.values()], "Share of all +-10% days by primary cause (%)", AMB, "{:.1f}%", 2.7), 50))
    cr = [["Cause", "Dir", "Events", "Avg move", "Gap share", "Rel. volume", "Next day vs SPY", "+5d", "+20d", "+60d"]]
    for r in sorted(R["causes"]["rows"], key=lambda x: -x["n"]):
        if r["n"] < 25:
            continue
        cr.append([r["cause"][:30], r["dir"], f"{r['n']:,}", pc(r["move"]), pc(r["gap_share"], 0, False), f"{r['vrel']}x", pc(r["ab1"]), pc(r["ab5"]), pc(r["ab20"]), pc(r["ab60"])])
    s.append(_dtable(cr, [46 * mm, 11 * mm, 14 * mm, 15 * mm, 15 * mm, 15 * mm, 18 * mm, 13 * mm, 13 * mm, 14 * mm], right=2))
    s.append(P("Gap share = part of the day's move that already happened at the open (100% = all overnight). Relative volume = that day's volume / 50-day average. "
               "Returns after the event are averages minus SPY over the same days.", CAP))
    s.append(P("What each cause looks like - the playbook of reasons a stock moves 10%:", H3))
    s.extend(bullets([
        "<b>Earnings / guidance</b> (8-K 2.02). The biggest single stock-specific cause. The move is overnight (gap) because results come out before the open or after the close. "
        "Guidance and margins matter more than the EPS beat. High-multiple growth stocks move most: they are valued on far-away cash flows, so any change in growth expectations is levered.",
        "<b>Market-wide shock.</b> A macro event - COVID, tariff announcements, the 2025-04-09 tariff pause, CPI shocks, Fed surprises - moves hundreds of stocks at once. "
        "High-beta stocks (beta 2-3) move 10% when the index moves 3-5%.",
        "<b>Sector / sympathy.</b> One company's news re-prices its peers: a chip maker's guide moves the whole semis group; one drug trial moves the class; oil and metal prices "
        "move miners and energy names; bitcoin moves crypto miners and MSTR/COIN.",
        "<b>Takeover / M&amp;A.</b> The target jumps to just under the offer (often +20% to +60%) and then stops moving - the 'merger-arb' flat line. Acquirers often fall.",
        "<b>Share offerings (dilution).</b> Small and mid caps that need cash file a 424B prospectus; the stock usually drops 8-25% overnight, priced at a discount.",
        "<b>Distress.</b> Bankruptcy filings, going-concern warnings, delisting notices, restatements: rare but severe and usually down.",
        "<b>No filing - news, analysts and flows.</b> Analyst up/downgrades, short-seller reports, FDA/clinical news from partners, government contracts, AI partnerships, "
        "index inclusion (S&amp;P 500 adds jump ~5-10%), social-media hype, short and gamma squeezes. Most of these leave no 8-K, so they land in this bucket.",
        "<b>Foreign ADRs.</b> Chinese and other foreign names file 6-K/20-F rather than 8-K, so the filing doesn't say why; they often move on home-market policy news (e.g., China stimulus).",
    ]))
    s.append(img(ch_size_by_cause(E)))
    na_path = os.path.join(OUT, "news_audit.json")
    if os.path.exists(na_path):
        NA = json.load(open(na_path))
        s.append(P(f"4.1 With news vs without news - headline audit of {NA['n']} recent stock-specific moves (since {NA['since']}, market cap above $2B, no SEC filing)", H3))
        s.append(P("For each move the headlines published from the day before to two days after were pulled and sorted by what they talk about. This fills the gap that SEC filings leave: "
                   "analyst calls, contracts, partner news, social media and sympathy moves never produce an 8-K."))
        nt = [["What the news was", "Moves", "Share", "Of which up", "Avg size", "Next 5d vs SPY"]]
        for r in NA["summary"]:
            nt.append([r["type"], str(r["n"]), pc(r["share"], 0, False), str(r["up"]), pc(r["avg_abs"], 1, False), pc(r["ab5"])])
        s.append(_dtable(nt, [62 * mm, 18 * mm, 18 * mm, 22 * mm, 22 * mm, W - 142 * mm], right=1))
        nn = next((r for r in NA["summary"] if r["type"] == "No headline found"), None)
        s.append(callout(("Even among liquid $2B+ companies, " + hl(f"{nn['share'] * 100:.0f}% of the moves had no dated headline at all") + " - the move came from flows: sympathy with peers, "
                          "options/gamma, social media, or news that only appeared after the price moved. " if nn else "") +
                         "The most common 'invisible' catalysts (no 8-K filed) were: "
                         + ", ".join(f"{hl(r['type'])} ({r['share'] * 100:.0f}%)" for r in [x for x in NA["summary"] if x["type"] not in ("No headline found", "Other news")][:3])
                         + ". Earnings show up here because peers' results, pre-announcements and guidance at conferences move a stock without its own 8-K. "
                         "Where no real news exists, positioning (short covering, dealer hedging, momentum funds) is doing the work.", "amber", "NEWS VS NO NEWS"))
        seen: dict[str, int] = {}
        ex = []
        for r in NA["rows"]:
            if r["head"] and seen.get(r["sym"], 0) < 1:
                seen[r["sym"]] = seen.get(r["sym"], 0) + 1
                ex.append(r)
        ex = ex[:12]
        if ex:
            et = [["Stock", "Date", "Move", "Type", "Headline"]]
            for r in ex:
                et.append([r["sym"], r["d"], pc(r["r"]), r["type"][:22], r["head"][:95]])
            s.append(_dtable(et, [14 * mm, 19 * mm, 15 * mm, 34 * mm, W - 82 * mm], right=9))

    # ---------------------------------------------------------------- 5. after
    s.append(PageBreak())
    s.append(P("5. WHAT HAPPENS AFTER A 10% DAY", H2))
    s.append(P("This matters for trading: should you chase the move, fade it, or wait? Each line is the average path of the stock minus SPY, starting from the close of the move day "
               "(so the move itself is not counted)."))
    up, dn = E["dir"] == "UP", E["dir"] == "DOWN"
    ea = E["cause"] == "Earnings"
    mk = E["cause"] == "Market-wide day"
    nf = E["cause"].str.startswith("No filing")
    s.append(img(ch_after(E, [("Earnings UP, closed near high", up & ea & (E["clv"] >= 0.75), GRN), ("Earnings UP, faded", up & ea & (E["clv"] <= 0.4), "#86efac"),
                              ("Earnings DOWN, closed near low", dn & ea & (E["clv"] <= 0.25), RED), ("Earnings DOWN, bounced", dn & ea & (E["clv"] >= 0.6), "#fca5a5")],
                          "Earnings moves split by where the move day closed (strong close vs faded)"), 62))
    s.append(img(ch_after(E, [("No filing UP", up & nf, GRN), ("No filing DOWN", dn & nf, RED), ("Market-wide UP day", up & mk, BLU), ("Market-wide DOWN day", dn & mk, AMB)],
                          "No-news moves and market-wide days"), 62))
    A = R["after"]
    at = [["Group", "Dir", "n", "+1d", "+5d", "+20d", "+60d", "% continuing (20d)", "t-stat 20d"]]
    for key, nm in (("close_q", "Close location"), ("shape", "Shape"), ("size", "Size"), ("vol_q", "Volume"), ("idio", "Type")):
        for r in A.get(key, []):
            at.append([f"{nm}: {r['k']}", r["dir"], f"{r['n']:,}", pc(r["ab1"]), pc(r["ab5"]), pc(r["ab20"]), pc(r["ab60"]), pc(r["cont20"], 0, False), str(r["t20"])])
    s.append(_dtable(at, [52 * mm, 11 * mm, 14 * mm, 15 * mm, 15 * mm, 15 * mm, 15 * mm, 22 * mm, W - 159 * mm], right=2))
    s.append(Spacer(1, 4))
    s.append(callout("Read with care: averages of +-10% movers are dominated by a few huge winners and losers, and t-stats under 2 are noise.<br/>"
                     f"<b>Robust:</b> market-wide crash days are followed by strong rebounds ({hl('+3.6% vs SPY after 20 sessions, t = 12.9')}; 2020 and 2025 dominate) - a market effect, not a stock one.<br/>"
                     "<b>Weak:</b> after stock-specific moves, both up and down movers drift about +1% vs SPY over 20 sessions (t 4-6). That is mostly the beta of these volatile stocks in a "
                     "rising market, not a tradable continuation or reversal - the share that keeps moving the same way is close to 50%.<br/>"
                     "<b>Not supported:</b> the popular 'strong close = post-earnings drift' rule. Earnings jumps that closed near the high did no better over 20 days than those that faded "
                     "(differences under t = 1). Over 2016-2026, in liquid US stocks, the earnings gap itself is the move.", "blue", "HOW TO USE IT"))

    # ---------------------------------------------------------------- 6. before
    s.append(PageBreak())
    s.append(P("6. THE DAY BEFORE - WHAT FLAGS A 10% MOVE IN ADVANCE", H2))
    s.append(P(f"For each of 30 features known the evening before, all stock-days were split into ten equal groups (deciles) and the chance of a +-10% day the next session was measured "
               f"in each group. The dashed line is the base rate ({pc(R['lift']['base'], 2, False)}). Tall bars on one side = the feature matters."))
    feats = [("earn_next", LABELS["earn_next"]), ("n_big", LABELS["n_big"]), ("atr_pct", LABELS["atr_pct"]), ("rv20", LABELS["rv20"]), ("vix", LABELS["vix"]),
             ("from_hi", LABELS["from_hi"]), ("ret5", LABELS["ret5"]), ("vol_ratio", LABELS["vol_ratio"]), ("logcap", LABELS["logcap"]), ("bbw_pct", LABELS["bbw_pct"]),
             ("rv_ratio", LABELS["rv_ratio"]), ("rng_ratio", LABELS["rng_ratio"]), ("obv_div", LABELS["obv_div"]), ("beta", LABELS["beta"]), ("days_since_earn", LABELS["days_since_earn"])]
    s.append(img(ch_lift(R, feats), 125))
    s.append(P("6.1 Rules - plain conditions and their hit rates", H3))
    rt = [["Condition (known the evening before)", "P(+-10% next day)", "x base", "P(up 10%)", "P(down 10%)", "Events", "% of days"]]
    for r in R["rules"]:
        rt.append([r["rule"], pc(r["rate"], 1, False), f"x{r['lift']}", pc(r["up"], 1, False), pc(r["dn"], 1, False), f"{r['events']:,}", pc(r["share_days"], 2, False)])
    s.append(_dtable(rt, [70 * mm, 22 * mm, 14 * mm, 18 * mm, 20 * mm, 15 * mm, W - 159 * mm], right=1))
    s.append(Spacer(1, 4))
    r_e3 = next((r for r in R["rules"] if r["rule"].startswith("Earnings tomorrow + had 3+")), {})
    r_ev = next((r for r in R["rules"] if r["rule"].startswith("Earnings tomorrow + 20d")), {})
    s.append(callout(f"{hl('Earnings tomorrow + a stock with a history of big moves')} is the strongest simple setup found: {pc(r_ev.get('rate'), 0, False)} chance of a +-10% day "
                     f"when 20-day volatility is in the top 20%, {pc(r_e3.get('rate'), 0, False)} when the stock had 3+ big days in the past year - about {r_ev.get('lift')}x the base rate. "
                     "Without a catalyst, the best flags are a stock's own history (many past 10% days, high ATR), a recent crash or melt-up (+-25-30% in 5 days), being 50% below the 52-week high, "
                     "high beta and VIX above 30.", "green", "THE RULES THAT WORK"))
    s.append(callout(f"Bollinger 'squeeze' (tightest 10% of the year): x{sq_rule['lift'] if sq_rule else ''} - a squeeze makes a 10% day <b>less</b> likely tomorrow, because low volatility "
                     "persists (volatility clustering beats 'coiled spring' stories). Volume dry-up: below base. Wyckoff accumulation / distribution proxies: about the base rate. "
                     "Spring: ~2x but that is mostly because it fires after sharp drops in volatile stocks. These patterns may still help with direction or timing over weeks, "
                     "but they do not flag a one-day 10% move.", "red", "WHAT DOESN'T WORK (FOR 1-DAY 10% MOVES)"))
    s.append(P("6.2 One score from all features - logistic model, trained 2016-2021, tested 2022-2026", H3))
    s.append(img(ch_model(R), 62))
    mt = [["Model", "Target", "AUC train", "AUC test", "Top 1% hit rate", "Top 1-5%", "Base"]]
    for k, v in mo.items():
        if k.startswith("_"):
            continue
        nm, tg = k.split("|")
        mt.append([nm, {"y": "+-10%", "up": "+10%", "dn": "-10%"}[tg], str(v["auc_train"]), str(v["auc_test"]), pc(v["deciles"][0]["rate"], 1, False), pc(v["deciles"][1]["rate"], 1, False),
                   pc(v["base_test"], 2, False)])
    s.append(_dtable(mt, [40 * mm, 18 * mm, 20 * mm, 20 * mm, 28 * mm, 22 * mm, W - 148 * mm], right=2))
    s.append(P("AUC 0.5 = coin flip, 1.0 = perfect. 0.80+ out-of-sample is strong for daily stock data. The up and down models lean on the same features, so the model forecasts "
               "the SIZE of tomorrow's move well, but not its sign.", CAP))

    # ---------------------------------------------------------------- 7. gamma & options
    s.append(PageBreak())
    s.append(P("7. GAMMA, OPTIONS AND SHORT INTEREST - THE AMPLIFIERS", H2))
    s.append(P("A catalyst decides <i>whether</i> a stock moves; positioning decides <i>how far</i>. Three amplifiers matter most:"))
    s.extend(bullets([
        "<b>Dealer gamma.</b> Market makers who sold options hedge with stock. If they are <b>long gamma</b> (customers sold them options) they sell rallies and buy dips - moves are damped and "
        "price tends to pin to big strikes (call wall = ceiling, put wall = floor). If they are <b>short gamma</b> (customers bought options, typical in meme / AI / small-cap names) they must "
        "buy as price rises and sell as it falls - the move feeds itself. Below the <b>zero-gamma level</b> moves get bigger; a break through a large call wall with short gamma is how "
        "'gamma squeezes' happen (GME, AMC 2021).",
        "<b>Implied move.</b> The at-the-money straddle for the first expiry after an event is the options market's price for the move. If a stock's history of earnings moves is "
        "larger than the implied move, options are cheap relative to its personality, and vice versa. Below is today's live board.",
        "<b>Short interest and days-to-cover.</b> When many shares are sold short and it takes days of volume to buy them back, good news forces covering on top of real buying.",
    ]))
    eb = L.get("earnings") or []
    if eb:
        s.append(P("7.1 Live: upcoming earnings - priced move vs the stock's own history", H3))
        t = [["Stock", "Reports", "Straddle to expiry", "Implied earnings move", "Hist. avg earnings move", "Hist. P(+-10%)", "n", "Gamma", "Zero gamma", "Call / put wall", "Read"]]
        today = np.datetime64(dt.date.today())
        for x in eb[:22]:
            im, h, g = x.get("implied") or {}, x.get("hist") or {}, x.get("gamma") or {}
            mv, ie = im.get("move"), None
            if mv and im.get("expiry"):
                try:
                    exd = dt.datetime.strptime(im["expiry"] + f" {dt.date.today().year}", "%d %b %Y").date()
                    if exd < dt.date.today():
                        exd = exd.replace(year=exd.year + 1)
                    n_days = max(1, int(np.busday_count(today, np.datetime64(exd))) + 1)
                    # strip the ordinary days to expiry: E|move| of n-1 normal days ~ 0.8 * sigma * sqrt(n-1), combined in quadrature
                    sd = 0.8 * (x.get("rv20") or 0) * math.sqrt(max(n_days - 1, 0))
                    ie = math.sqrt(max(mv * mv - sd * sd, 0.0)) or None
                except Exception:  # noqa: BLE001
                    ie = mv
            read = ""
            if ie and h.get("mean"):
                read = "options CHEAP vs history" if h["mean"] > ie * 1.2 else ("options RICH vs history" if h["mean"] < ie * 0.8 else "fairly priced")
            t.append([x["sym"], x.get("earn_date") or "", pc(mv, 1, False) if mv else "n/a", pc(ie, 1, False) if ie else "n/a", pc(h.get("mean"), 1, False), pc(h.get("p10"), 0, False),
                      str(h.get("n", "")), (g.get("regime") or "n/a").lower()[:3], f"{g['zero_gamma']:.2f}" if g.get("zero_gamma") else "n/a",
                      f"{g.get('call_wall') or '-'} / {g.get('put_wall') or '-'}", read])
        s.append(_dtable(t, [12 * mm, 17 * mm, 15 * mm, 15 * mm, 15 * mm, 13 * mm, 7 * mm, 10 * mm, 15 * mm, 20 * mm, W - 139 * mm], right=2))
        ch = sum(1 for r in t[1:] if "CHEAP" in r[-1])
        ri = sum(1 for r in t[1:] if "RICH" in r[-1])
        s.append(callout(f"Of {len(t) - 1} names reporting in the next days, {hl(f'{ch} look cheap')} and {ri} look rich relative to their own earnings history; the rest are fairly priced. "
                         "Cheap = the stock's average earnings-day move is at least 20% bigger than the move the straddle implies for the report itself. "
                         "Negative gamma + cheap options is the most explosive combination; positive gamma near a big wall tends to pin.", "blue"))
        s.append(P("Straddle = ATM call + put for the first expiry after the report, / price (Yahoo). Implied earnings move = the straddle with the ordinary days to expiry removed "
                   "(0.8 x 20-day volatility x sqrt(days), in quadrature). History = average absolute move on this stock's own 8-K-dated earnings days since 2016. "
                   "Gamma: pos/neg dealer regime, standard convention (dealers long calls, short puts), end-of-day open interest.", CAP))
    gt = L.get("gamma_top") or []
    big_eb = sorted([x for x in eb if (x.get("cap") or 0) > 2e10], key=lambda x: -(x.get("cap") or 0))
    big_gt = [x for x in gt if (x.get("cap") or 0) > 5e9] or [{"sym": "NVDA"}]
    for x in (big_eb[:1] + big_gt[:1]):
        png = None
        try:
            png = ch_gex(x["sym"])
        except Exception:  # noqa: BLE001
            png = None
        if png:
            s.append(img(png, 56))
    si = L.get("short") or {}
    if si.get("rows"):
        s.append(P("7.2 Short interest test (FINRA, " + si["from"] + " onward)", H3))
        st = [["Days to cover", "P(+-10% next day)", "P(up 10%)", "Events"]]
        for r in si["rows"]:
            st.append([r["dtc"], pc(r["rate"], 2, False), pc(r["up"], 2, False), f"{r['events']:,}"])
        s.append(_dtable(st, [40 * mm, 40 * mm, 40 * mm, W - 120 * mm], right=1))
        s.append(P(f"Base rate in the same period {pc(si['base'], 2, False)}. FINRA short interest is published twice a month; only 2026 files are available locally, so this is a short sample.", CAP))
    # ---------------------------------------------------------------- 8. cases
    cases = L.get("cases") or []
    if cases:
        s.append(PageBreak())
        s.append(P("8. CASE STUDIES - REAL STOCKS, REAL HEADLINES", H2))
        s.append(P("One clear recent example for each cause and direction, with the chart, the numbers, the filing evidence and the headlines published around that day."))
        for c in cases:
            png = ch_case(c)
            blk: list[Any] = [P(f"{c['sym']} - {_s(c.get('name') or '')}  |  {str(c['d'])[:10]}  |  {mark(pc(c['r']), GRN if c['r'] > 0 else RED)}  |  cause: <b>{_s(c['cause'])}</b>", H3)]
            if png:
                blk.append(img(png, 52))
            facts = (f"Gap at open {pc(c.get('gap'))}, then {pc(c.get('intra'))} during the session; closed at {c.get('clv', 0) * 100:.0f}% of the day's range; volume "
                     f"{c.get('vrel') or 0:.1f}x normal. Filings that day: {_s(c.get('tags') or c.get('tags_prev') or 'none')}. "
                     f"Industry ({_s(c.get('industry') or '')}) median that day {pc(c.get('ind_med'))}; SPY {pc(c.get('spy_r'))}. "
                     f"After: +5d {pc((c.get('fwd5') or 0) - (c.get('spy_fwd5') or 0)) if c.get('fwd5') is not None else 'n/a'} vs SPY, "
                     f"+20d {pc((c.get('fwd20') or 0) - (c.get('spy_fwd20') or 0)) if c.get('fwd20') is not None and not (isinstance(c.get('fwd20'), float) and math.isnan(c.get('fwd20'))) else 'n/a'}.")
            blk.append(P(facts))
            nw = c.get("news") or []
            if nw:
                blk.extend(bullets([f"<i>{_s(n['title'])}</i> <font color='#6b7285'>- {_s(n['source'])}, {dt.datetime.fromtimestamp(n['ts']).strftime('%d %b %H:%M')}</font>" for n in nw]))
            else:
                blk.append(P("<font color='#6b7285'>No dated headline found - a 'no-news' move (flows, sympathy, social media or an unindexed source).</font>"))
            s.append(CondPageBreak(95 * mm))
            s.append(KeepTogether(blk))
            s.append(Spacer(1, 6))
    # ---------------------------------------------------------------- 9. watchlist
    wl = L.get("watch") or []
    if wl:
        s.append(PageBreak())
        s.append(P("9. TODAY'S WATCHLIST - HIGHEST MODEL RISK OF A +-10% DAY NEXT SESSION", H2))
        s.append(P("Scored with the model from section 6 (trained only on 2016-2021) on the latest close. This ranks <b>size risk, not direction</b>. Names reporting earnings are flagged."))
        t = [["#", "Stock", "Name", "Sector", "Model P", "Earnings", "20d vol (daily)", "Big days 1y", "Last"]]
        for i, x in enumerate(wl[:30], 1):
            t.append([str(i), x["sym"], (x.get("name") or "")[:24], (x.get("sector") or "")[:16], pc(x["p"], 1, False), x.get("earn_date") if x.get("earn_days") is not None and -0.3 <= x["earn_days"] <= 7 else "",
                      pc(x.get("rv20"), 1, False), str(int(x.get("n_big") or 0)), pc((x.get("chg") or 0) / 100)])
        s.append(_dtable(t, [7 * mm, 14 * mm, 44 * mm, 28 * mm, 16 * mm, 18 * mm, 18 * mm, 15 * mm, W - 160 * mm], right=4))
        s.append(P(f"{L.get('n_scored', 0):,} stocks scored. Model P is calibrated to the 2016-21 base rate; treat it as a ranking.", CAP))
    # ---------------------------------------------------------------- 10. playbook
    s.append(PageBreak())
    s.append(P("10. THE PLAYBOOK - HOW TO DETECT POTENTIAL 10% SETUPS", H2))
    s.append(callout(f"<b>Step 1 - Is there a catalyst tomorrow?</b> Earnings calendar first ({earn_rule['lift'] if earn_rule else ''}x the base rate alone, {r_ev.get('lift')}x when combined with a volatile stock). Then known binary dates: FDA PDUFA dates, "
                     "trial read-outs, court rulings, index rebalances, product events, lock-up expiries, macro (CPI, FOMC) for high-beta names.<br/>"
                     "<b>Step 2 - Does the stock have a 10%-move personality?</b> Count of +-10% days in the past year (3+), ATR above ~5% of price, 20-day volatility in the top 20%, "
                     "market cap under $10B, beta above 2. Size is forecastable - this is where the model gets its AUC.<br/>"
                     "<b>Step 3 - What regime is the market in?</b> VIX above 30 roughly quadruples 10% days for everybody; below 15 halves them. A market-wide shock moves high-beta names "
                     "10% without any company news.<br/>"
                     "<b>Step 4 - What will amplify it?</b> Options: implied move vs the stock's own history (cheap/rich), dealer gamma regime (short gamma = bigger moves), distance to the "
                     "zero-gamma level and walls. Short interest / days-to-cover for squeeze fuel. Recent shelf (S-3) = a cash-hungry company that can sell stock into a rally.<br/>"
                     "<b>Step 5 - Direction (the hard part).</b> No day-before price feature predicts the sign well. Direction comes from the content of the news versus expectations "
                     "(whisper numbers, guidance), from positioning (crowded shorts squeeze, crowded longs dump) and from options flow into the event.<br/>"
                     "<b>Step 6 - After the move.</b> Don't assume continuation or reversal for a single stock - follow-through is close to a coin flip. Market-wide crash days mean-revert strongly. Expect more big days: "
                     "volatility clusters (30-40% chance of another +-10% within 5 sessions).", "amber", "SIX-STEP CHECKLIST"))
    s.append(Spacer(1, 4))
    s.append(P("What the evidence says about popular ideas", H3))
    s.append(_dtable([["Idea", "Verdict from this data"],
                      ["'Normal distribution' risk models", "Badly wrong in the tails - x100 to x1,000,000 too few big days. Use Student-t / jump models, or empirical tails."],
                      ["Volatility squeeze -> explosion tomorrow", "False for 1-day 10% moves: less likely than average. Low volatility persists."],
                      ["Wyckoff accumulation / distribution", "No 1-day edge (near base rate). May still describe multi-week structure."],
                      ["Spring / upthrust", "Spring ~2x base, mainly because it fires in volatile stocks after drops; upthrust ~base."],
                      ["Earnings in volatile names", f"The strongest effect found ({r_ev.get('lift')}x base)."],
                      ["High VIX regime", "x4 for everyone; clusters of events."],
                      ["Big moves are random", f"Size is forecastable (AUC {mo['all days|y']['auc_test']:.2f} out-of-sample); direction mostly is not."],
                      ["Chase strong earnings closes", "Not supported 2016-26: strong-close and faded earnings jumps performed the same over 20 days."],
                      ["Buy market-wide crash days", "Supported: strong average rebound (t ~13), but the sample is dominated by 2020 and April 2025."]],
                     [60 * mm, W - 60 * mm], right=9))
    s.append(Spacer(1, 6))
    s.append(P("Disclaimer: research generated by JORDAN TERMINAL from public data (Yahoo Finance, SEC EDGAR, FINRA, Google News). Past frequencies are not promises; survivorship bias and "
               "data errors are possible. Not investment advice; nothing here places orders.", SMALL))
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title="Big Movers research", author="Jordan Terminal")
    doc.multiBuild(s)
    pdf = buf.getvalue()
    open(os.path.join(OUT, "BigMovers_research.pdf"), "wb").write(pdf)
    return pdf


if __name__ == "__main__":
    b = build()
    print(len(b))
