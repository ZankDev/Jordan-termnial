"""Renders an annotated candlestick PNG for a just-opened trade, for Telegram.

Panel 0 (main) always shows candles + an entry marker + stop/target lines, plus
whatever `overlay["lines"]` / `overlay["hlines"]` the strategy contributed (see
chart_overlays.py) — the actual indicator values that triggered the entry, not a
generic decoration. A second panel is added only when `overlay["panel"]` is set
(the two oscillator strategies, RSI/MACD).
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch

LOOKBACK_BARS = 100

UP_COLOR, DOWN_COLOR, ENTRY_COLOR = "#21c97a", "#ff4d5e", "#3fd1ff"
WIN_COLOR, LOSS_COLOR = "#21c97a", "#ff4d5e"


def render_setup_chart(
    df: pd.DataFrame,
    entry_time: pd.Timestamp,
    entry_price: float,
    stop_price: float,
    target_price: float,
    side: str,
    overlay: dict | None = None,
    exit_time: pd.Timestamp | None = None,
    exit_price: float | None = None,
    exit_reason: str | None = None,
    pnl: float | None = None,
) -> bytes:
    """The entry-setup chart (Telegram's "first image"). Passing exit_time/exit_price/
    exit_reason/pnl turns it into the matching "second image" sent when the trade closes
    (see live_engine.py's _send_telegram_close) - same candles, same style, same stop/
    target lines, just extended to the exit bar with a second marker and a WIN/LOSS title,
    so the two messages read as one visual story instead of two different chart styles."""
    overlay = overlay or {"lines": [], "hlines": [], "panel": None}
    window = df[df.index <= (exit_time or entry_time)].tail(LOOKBACK_BARS)
    if window.empty:
        window = df.tail(LOOKBACK_BARS)
    plot_df = window.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )

    mc = mpf.make_marketcolors(up=UP_COLOR, down=DOWN_COLOR, edge="inherit", wick="inherit")
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        facecolor="#0c1118",
        edgecolor="#1c2530",
        gridcolor="#161d26",
        figcolor="#0c1118",
        rc={"axes.labelcolor": "#8993a1", "xtick.color": "#8993a1", "ytick.color": "#8993a1"},
    )

    def _nearest_bar(ts: pd.Timestamp) -> pd.Timestamp:
        pos = plot_df.index.get_indexer([ts], method="nearest")[0]
        return plot_df.index[pos]

    entry_marker = pd.Series(np.nan, index=plot_df.index)
    entry_marker.loc[_nearest_bar(entry_time)] = entry_price
    marker_shape = "^" if side == "long" else "v"
    addplots = [
        mpf.make_addplot(
            entry_marker, type="scatter", markersize=140, marker=marker_shape, color=ENTRY_COLOR, secondary_y=False
        )
    ]
    if exit_time is not None and exit_price is not None:
        exit_marker = pd.Series(np.nan, index=plot_df.index)
        exit_marker.loc[_nearest_bar(exit_time)] = exit_price
        win = pnl is None or pnl >= 0
        addplots.append(
            mpf.make_addplot(
                exit_marker, type="scatter", markersize=170, marker="*",
                color=(WIN_COLOR if win else LOSS_COLOR), secondary_y=False,
            )
        )

    legend_labels: list[str] = []
    for label, series, color in overlay.get("lines", []):
        addplots.append(
            mpf.make_addplot(series.reindex(plot_df.index), color=color, width=1.1, panel=0, secondary_y=False)
        )
        legend_labels.append(label)

    hline_values = [stop_price, target_price]
    hline_colors = [DOWN_COLOR, UP_COLOR]
    hline_styles = ["--", "--"]
    for label, value, color in overlay.get("hlines", []):
        hline_values.append(value)
        hline_colors.append(color)
        hline_styles.append(":")
        legend_labels.append(label)

    panel = overlay.get("panel")
    panel_ratios = None
    if panel:
        panel_ratios = (3, 1)
        for label, series, color in panel.get("lines", []):
            addplots.append(
                mpf.make_addplot(series.reindex(plot_df.index), color=color, width=1.1, panel=1, secondary_y=False)
            )
            legend_labels.append(label)
        for label, value, color in panel.get("hlines", []):
            const = pd.Series(value, index=plot_df.index)
            addplots.append(
                mpf.make_addplot(const, color=color, width=0.8, linestyle="--", panel=1, secondary_y=False)
            )

    if exit_time is not None:
        win = pnl is None or pnl >= 0
        title = f"{side.upper()} {'WIN' if win else 'LOSS'} — {exit_reason or 'closed'} ({pnl:+.2f})" if pnl is not None else f"{side.upper()} closed — {exit_reason or ''}"
    else:
        title = f"{side.upper()} setup"
    if legend_labels:
        title += "  —  " + ", ".join(legend_labels)

    buf = io.BytesIO()
    kwargs = dict(
        type="candle",
        style=style,
        addplot=addplots,
        hlines=dict(hlines=hline_values, colors=hline_colors, linestyle=hline_styles, linewidths=[1.2] * len(hline_values)),
        volume=False,
        figsize=(9, 6 if panel else 5),
        title=title,
        savefig=dict(fname=buf, dpi=120, bbox_inches="tight"),
    )
    if panel_ratios:
        kwargs["panel_ratios"] = panel_ratios
        kwargs["ylabel_lower"] = panel.get("ylabel", "")
    mpf.plot(plot_df, **kwargs)
    buf.seek(0)
    return buf.getvalue()


def render_circuit_card(
    account_name: str,
    event: str,  # "profit" or "loss"
    limit_value: float,
    daily_pnl: float,
    trades_count: int,
    wins: int,
    losses: int,
    balance_before: float,
    balance_after: float,
    date_str: str,
) -> bytes:
    """Renders the daily circuit-breaker hit as a clean white summary card (styled after
    an "end of day" agent-status card) instead of the dark candlestick chart used for
    individual trade alerts — this event is about the account's whole day, not one trade."""
    is_profit = event == "profit"
    accent = "#16a34a" if is_profit else "#dc2626"
    badge_label = "PROFIT TARGET HIT" if is_profit else "LOSS LIMIT HIT"
    sign = "+" if daily_pnl >= 0 else "-"
    display_name = account_name if len(account_name) <= 28 else account_name[:26] + "…"
    initials = "".join(w[0] for w in account_name.replace("-", " ").replace("_", " ").split()[:2]).upper() or "AI"

    fig, ax = plt.subplots(figsize=(11.2, 6.2), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    avatar = FancyBboxPatch(
        (0.03, 0.855), 0.058, 0.09, boxstyle="round,pad=0,rounding_size=0.014",
        linewidth=0, facecolor=accent, transform=ax.transAxes,
    )
    ax.add_patch(avatar)
    ax.text(0.059, 0.90, initials, ha="center", va="center", fontsize=15, fontweight="bold", color="white")
    ax.text(0.105, 0.925, display_name, fontsize=17, fontweight="bold", color="#111827", va="center")
    ax.text(0.105, 0.878, "Autonomous · NOSTRO Terminal", fontsize=10.5, color="#6b7280", va="center")

    badge_w = 0.05 + len(badge_label) * 0.0135
    badge = FancyBboxPatch(
        (0.03, 0.715), badge_w, 0.062, boxstyle="round,pad=0,rounding_size=0.031",
        linewidth=0, facecolor="#111827", transform=ax.transAxes,
    )
    ax.add_patch(badge)
    ax.text(0.03 + badge_w / 2, 0.746, badge_label, ha="center", va="center", fontsize=10.5, fontweight="bold", color="white")
    ax.text(0.03, 0.665, f"Daily {event} limit: ${limit_value:,.2f}", fontsize=10, color="#6b7280", va="center")

    ax.text(0.03, 0.585, date_str, fontsize=16, color="#6b7280", fontweight="bold", va="center")
    ax.text(0.03, 0.505, "TODAY'S P&L", fontsize=11, color="#6b7280", fontweight="bold", va="center")
    ax.text(0.03, 0.36, f"{sign}${abs(daily_pnl):,.2f}", fontsize=50, fontweight="bold", color=accent, va="center")

    def stat(y: float, label: str, value: str) -> None:
        ax.text(0.60, y, label, fontsize=10.5, color="#6b7280", fontweight="bold", va="center")
        ax.text(0.60, y - 0.058, value, fontsize=17, color="#111827", fontweight="bold", va="center")

    stat(0.83, "TRADES TODAY", str(trades_count))
    stat(0.68, "WINS / LOSSES", f"{wins} / {losses}")
    # Escaped $ signs: a matched "$...$" pair would otherwise be parsed as matplotlib
    # mathtext, which silently collapses the spacing around the arrow between them.
    stat(0.53, "ACCOUNT", f"\\${balance_before:,.2f}   →   \\${balance_after:,.2f}")
    stat(0.38, "KILL-SWITCH", "TRIGGERED — new entries paused")

    ax.axhline(0.09, color="#e5e7eb", linewidth=1, xmin=0.03, xmax=0.97)
    ax.text(0.03, 0.045, f"Autonomous account · NFA · {date_str} ET", fontsize=9.5, color="#9ca3af", va="center")
    ax.text(0.97, 0.045, "Built on NOSTRO · live data", fontsize=9.5, color="#9ca3af", va="center", ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


BRAND_AMBER, BRAND_DARK = "#c9820a", "#0b0d12"
_GREEN, _RED, _INK, _MUTED, _FAINT = "#16a34a", "#dc2626", "#111827", "#6b7280", "#9ca3af"


def _fmt_price(v: float) -> str:
    return f"{v:,.2f}"


def _draw_logo(ax, x: float, y: float, size: float) -> None:
    """The JORDAN TERMINAL mark (dark rounded square + amber zig-zag), same as the report masthead."""
    ax.add_patch(FancyBboxPatch((x, y), size * 0.62, size, boxstyle="round,pad=0,rounding_size=0.012",
                                linewidth=0, facecolor=BRAND_DARK, transform=ax.transAxes))
    pts = [(0.25, 0.31), (0.41, 0.63), (0.56, 0.41), (0.75, 0.75)]
    w = size * 0.62
    ax.plot([x + px * w for px, _ in pts], [y + py * size for _, py in pts], color=BRAND_AMBER, linewidth=3.2,
            solid_capstyle="round", solid_joinstyle="round", transform=ax.transAxes)


def _mini_chart(ax, window: pd.DataFrame, entry_i: int, exit_i: int | None, entry: float, stop: float, target: float,
                side: str, exit_price: float | None, win: bool | None) -> None:
    n = len(window)
    xs = np.arange(n)
    o, h, l, c = (window[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up = c >= o
    cols = np.where(up, _GREEN, _RED)
    ax.vlines(xs, l, h, colors=cols, linewidth=1.2, zorder=3)
    ax.bar(xs, np.where(up, c - o, o - c) + 1e-9, bottom=np.minimum(o, c), width=0.66, color=cols, zorder=4)

    pad = 4 if exit_i is not None else 9
    x_left = entry_i - 0.5
    x_right = (exit_i + 0.5) if exit_i is not None else (n - 1 + pad - 0.5)
    ax.fill_between([x_left, x_right], entry, target, color=_GREEN, alpha=0.13, zorder=1, linewidth=0)
    ax.fill_between([x_left, x_right], entry, stop, color=_RED, alpha=0.13, zorder=1, linewidth=0)
    ax.hlines(target, x_left, x_right, colors=_GREEN, linestyles="--", linewidth=1.4, zorder=5)
    ax.hlines(stop, x_left, x_right, colors=_RED, linestyles="--", linewidth=1.4, zorder=5)
    ax.hlines(entry, x_left, x_right, colors="#2563eb", linestyles="-", linewidth=1.6, zorder=5)
    ax.vlines(x_left, min(stop, target), max(stop, target), colors="#94a3b8", linewidth=0.8, linestyles=":", zorder=2)
    ax.scatter([entry_i], [entry], marker="^" if side == "long" else "v", s=120, color="#2563eb", zorder=7,
               edgecolors="white", linewidths=1.1)
    if exit_i is not None and exit_price is not None:
        ax.scatter([exit_i], [exit_price], marker="*", s=300, color=_GREEN if win else _RED, zorder=8,
                   edgecolors="white", linewidths=1.0)

    lo, hi = min(l.min(), stop, target), max(h.max(), stop, target)
    span = (hi - lo) or 1.0
    ax.set_ylim(lo - span * 0.06, hi + span * 0.06)
    ax.set_xlim(-1, n - 1 + pad)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=8, colors=_FAINT, length=0)
    ax.grid(axis="y", color="#f1f5f9", linewidth=1)
    for sp in ax.spines.values():
        sp.set_visible(False)

    # price tags on the right edge, nudged apart so TP / ENTRY / SL never overlap
    y0, y1 = ax.get_ylim()
    tags = sorted([(target, _GREEN, "TP"), (entry, "#2563eb", "ENTRY"), (stop, _RED, "SL")], key=lambda t: t[0])
    ys = [(t[0] - y0) / (y1 - y0) for t in tags]
    gap = 0.085
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - gap) if ys[i + 1] > 1 else ys[i]
    for (val, col, lab), yy in zip(tags, ys):
        ax.annotate(f"{lab}  {_fmt_price(val)}", xy=(1.0, (val - y0) / (y1 - y0)), xycoords="axes fraction",
                    xytext=(1.03, yy), textcoords="axes fraction", fontsize=9, fontweight="bold", color="white",
                    va="center", ha="left", annotation_clip=False,
                    bbox=dict(boxstyle="round,pad=0.3", fc=col, ec="none"),
                    arrowprops=dict(arrowstyle="-", color=col, linewidth=1.0, shrinkA=0, shrinkB=0))


def render_trade_card(
    df: pd.DataFrame,
    account_name: str,
    symbol: str,
    timeframe: str,
    side: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    entry_time: pd.Timestamp,
    description: str = "",
    exit_time: pd.Timestamp | None = None,
    exit_price: float | None = None,
    exit_reason: str | None = None,
    pnl: float | None = None,
) -> bytes:
    """Jordan Terminal trade alert as a white card (styled after the end-of-day agent card): one card while the
    trade is open, a second WIN/LOSS card with the realised P&L once it closes."""
    closed = exit_time is not None and pnl is not None
    win = bool(closed and pnl >= 0)
    accent = (_GREEN if win else _RED) if closed else BRAND_AMBER
    risk, reward = abs(entry_price - stop_price), abs(target_price - entry_price)
    rr = f"{reward / risk:.2f}" if risk else "-"
    badge = (f"CLOSED · {'WIN' if win else 'LOSS'}" + (f" ({exit_reason})" if exit_reason else "")) if closed else f"TRADE OPEN · {side.upper()}"
    when = pd.Timestamp(exit_time if closed else entry_time)
    display_name = account_name if len(account_name) <= 26 else account_name[:24] + "…"

    fig = plt.figure(figsize=(11.2, 6.2), dpi=150)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    _draw_logo(ax, 0.03, 0.855, 0.09)
    ax.text(0.10, 0.925, "JORDAN TERMINAL", fontsize=15, fontweight="bold", color=_INK, va="center")
    ax.text(0.10, 0.878, f"Autonomous · {display_name}", fontsize=10.5, color=_MUTED, va="center")

    badge_w = 0.05 + len(badge) * 0.0128
    ax.add_patch(FancyBboxPatch((0.03, 0.72), badge_w, 0.062, boxstyle="round,pad=0,rounding_size=0.031",
                                linewidth=0, facecolor=_INK, transform=ax.transAxes))
    ax.text(0.03 + badge_w / 2, 0.751, badge, ha="center", va="center", fontsize=10.5, fontweight="bold", color="white")

    ax.text(0.03, 0.655, f"{symbol} · {timeframe}", fontsize=17, color=_MUTED, fontweight="bold", va="center")
    ax.text(0.03, 0.610, when.strftime("%Y-%m-%d  %H:%M"), fontsize=10.5, color=_FAINT, va="center")

    if closed:
        ax.text(0.03, 0.545, "REALIZED P&L", fontsize=11, color=_MUTED, fontweight="bold", va="center")
        ax.text(0.03, 0.43, f"{'+' if pnl >= 0 else '-'}\${abs(pnl):,.2f}", fontsize=44, fontweight="bold", color=accent, va="center")
    else:
        ax.text(0.03, 0.545, f"{side.upper()} ENTRY", fontsize=11, color=_MUTED, fontweight="bold", va="center")
        ax.text(0.03, 0.43, _fmt_price(entry_price), fontsize=44, fontweight="bold", color=_INK, va="center")

    def stat(x: float, y: float, label: str, value: str, color: str = _INK) -> None:
        ax.text(x, y, label, fontsize=10, color=_MUTED, fontweight="bold", va="center")
        ax.text(x, y - 0.052, value, fontsize=16, color=color, fontweight="bold", va="center")

    if closed:
        stat(0.03, 0.30, "ENTRY", _fmt_price(entry_price))
        stat(0.20, 0.30, "EXIT", _fmt_price(float(exit_price)))
        stat(0.37, 0.30, "R:R", rr)
        stat(0.03, 0.17, "TARGET", _fmt_price(target_price), _GREEN)
        stat(0.20, 0.17, "STOP", _fmt_price(stop_price), _RED)
    else:
        stat(0.03, 0.30, "TARGET (TP)", _fmt_price(target_price), _GREEN)
        stat(0.22, 0.30, "STOP (SL)", _fmt_price(stop_price), _RED)
        stat(0.40, 0.30, "R:R", rr)
        stat(0.03, 0.17, "RISK", f"{risk:,.2f} pts")
        stat(0.22, 0.17, "REWARD", f"{reward:,.2f} pts")

    sel = df[df.index <= (exit_time if closed else entry_time)]
    if sel.empty:
        sel = df
    e_pos = int(sel.index.get_indexer([pd.Timestamp(entry_time)], method="nearest")[0])
    window = sel.iloc[max(0, e_pos - 28):]
    pos = lambda ts: int(window.index.get_indexer([pd.Timestamp(ts)], method="nearest")[0])  # noqa: E731
    cax = fig.add_axes([0.53, 0.20, 0.29, 0.64])
    cax.set_facecolor("white")
    _mini_chart(cax, window, pos(entry_time), pos(exit_time) if closed else None, entry_price, stop_price, target_price,
                side, exit_price, win if closed else None)

    if description:
        ax.text(0.56, 0.875, description[:70], fontsize=9.5, color=_MUTED, va="center", style="italic")

    ax.axhline(0.09, color="#e5e7eb", linewidth=1, xmin=0.03, xmax=0.97)
    ax.text(0.03, 0.045, f"Autonomous account · NFA · {when.strftime('%Y-%m-%d')} ET", fontsize=9.5, color=_FAINT, va="center")
    ax.text(0.97, 0.045, "Built on Jordan Terminal · live data", fontsize=9.5, color=_FAINT, va="center", ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
