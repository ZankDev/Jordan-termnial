"""Telegram notifications for the Polymarket vertical — the same look as the futures
trade alerts (dark chart image + HTML caption) so they read as one product:

  * entry   — chart of the held side's share price (with the entry marker and the
              take-profit / stop levels) over the underlying spot vs the strike, plus
              the reasoning: lead, momentum, Monte-Carlo flip probability;
  * exit    — the same chart extended to the exit with a WIN/LOSS title and P&L;
  * champion change and an hourly TRAINING DIGEST (corpus, cycles, best score, the
    champion, and every paper account's equity — an image, so progress is visible
    at a glance rather than a wall of numbers).

Everything is best-effort and runs off the trading threads: a Telegram or matplotlib
failure is logged and dropped, never allowed to affect a trade or the trainer.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import datetime as dt  # noqa: E402

from app.notify import telegram_bot  # noqa: E402

logger = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pm-notify")
_render_lock = threading.Lock()  # pyplot keeps global state: render one figure at a time

BG, PANEL, GRID, TXT = "#0c1118", "#0c1118", "#161d26", "#8993a1"
UP, DN, CYAN, AMBER, VIOLET = "#21c97a", "#ff4d5e", "#3fd1ff", "#ffa028", "#c58bff"


# Paper-account alerts are OFF: Telegram is reserved for real-money trades. Set
# POLYMARKET_PAPER_ALERTS=1 in the environment to bring them back. A digest requested by
# hand (POST /telegram/digest) still goes out.
PAPER_ALERTS = os.environ.get("POLYMARKET_PAPER_ALERTS", "0") == "1"


def _fire(fn, *a, force: bool = False, **kw) -> None:
    if not PAPER_ALERTS and not force:
        return

    def run() -> None:
        try:
            if telegram_bot.is_configured():
                fn(*a, **kw)
        except Exception:  # noqa: BLE001
            logger.exception("polymarket telegram notification failed")

    _pool.submit(run)


def _ts(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t)


def _style(ax) -> None:
    ax.set_facecolor(PANEL)
    ax.grid(True, color=GRID, linewidth=0.7)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=8)


def session_chart(
    title: str, side: str, ticks: list[tuple[float, float, float]], spot_trace: list[tuple[float, float]],
    strike: float | None, start_ts: float, end_ts: float, entry: tuple[float, float] | None,
    exit_: tuple[float, float, bool] | None, levels: dict[str, float] | None,
) -> bytes:
    """ticks = [(t, up_price, down_price)]; entry = (t, price); exit_ = (t, price, won)."""
    with _render_lock:
        return _session_chart(title, side, ticks, spot_trace, strike, start_ts, end_ts, entry, exit_, levels)


def _session_chart(title, side, ticks, spot_trace, strike, start_ts, end_ts, entry, exit_, levels) -> bytes:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.6), dpi=120, sharex=True,
                                 gridspec_kw={"height_ratios": [3, 2], "hspace": 0.08})
    fig.patch.set_facecolor(BG)
    for ax in (a1, a2):
        _style(ax)

    held = [(t, (u if side == "up" else d)) for t, u, d in ticks]
    if held:
        a1.plot([_ts(t) for t, _ in held], [p for _, p in held], color=AMBER, linewidth=1.8, label=f"{side.upper()} share price")
    for name, val, col in ((("take-profit", (levels or {}).get("tp"), UP), ("stop", (levels or {}).get("sl"), DN))):
        if val:
            a1.axhline(val, color=col, linestyle="--", linewidth=1.0, alpha=0.8)
            a1.text(_ts(end_ts), val, f"{name} {val:.2f} ", color=col, fontsize=7, va="bottom", ha="right")
    if entry:
        a1.scatter([_ts(entry[0])], [entry[1]], color=CYAN, s=90, marker="^", zorder=5, label="entry")
    if exit_:
        a1.scatter([_ts(exit_[0])], [exit_[1]], color=UP if exit_[2] else DN, s=140, marker="*", zorder=5, label="exit")
    a1.set_ylim(0, 1.02)
    a1.set_ylabel("share price", color=TXT, fontsize=8)
    a1.set_title(title, color="#e6edf5", fontsize=10, loc="left")
    a1.legend(loc="lower left", fontsize=7, facecolor=BG, edgecolor=GRID, labelcolor=TXT)

    if spot_trace:
        a2.plot([_ts(t) for t, _ in spot_trace], [p for _, p in spot_trace], color=CYAN, linewidth=1.2)
    if strike:
        a2.axhline(strike, color=VIOLET, linestyle=":", linewidth=1.1)
        a2.text(_ts(start_ts), strike, " strike", color=VIOLET, fontsize=7, va="bottom")
    a2.set_ylabel("spot", color=TXT, fontsize=8)
    for ax in (a1, a2):
        ax.axvline(_ts(end_ts), color=GRID, linewidth=1.2)
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    a2.set_xlim(_ts(start_ts - 5), _ts(end_ts + 8))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _levels(entry_price: float, params: dict[str, Any]) -> dict[str, float]:
    tp = min(params.get("tp_px", 0.9), entry_price * (1 + params.get("tp_pct", 40) / 100))
    sl = entry_price * (1 - params.get("sl_pct", 40) / 100)
    return {"tp": round(tp, 3), "sl": round(max(0.01, sl), 3)}


def notify_entry(account: dict[str, Any], sess: dict[str, Any], position: dict[str, Any], features: dict[str, Any],
                 ticks: list, spot_trace: list, strike: float | None, params: dict[str, Any]) -> None:
    def send() -> None:
        lv = _levels(position["entry_price"], params)
        img = session_chart(
            f"{sess['asset'].upper()} · {position['side'].upper()} · entry {position['entry_price']:.3f}",
            position["side"], ticks, spot_trace, strike, sess["start_ts"], sess["end_ts"],
            (time.time(), position["entry_price"]), None, lv,
        )
        flip = features.get("mc_flip")
        stake = position["shares"] * position["entry_price"]
        lines = [
            "\U0001F4C8 <b>POLYMARKET MOMO — ENTRY</b>",
            f"<b>{sess['asset'].upper()} {position['side'].upper()}</b> @ <b>{position['entry_price']:.3f}</b> · stake ${stake:.2f}",
        ]
        if features.get("fav_lead") is not None:
            row = f"lead <b>{features['fav_lead']:+.3f}%</b> · momentum {features.get('drift_fav', 0):+.3f}%"
            if flip is not None:
                row += f" · flip risk <b>{flip * 100:.0f}%</b>"
            lines.append(row)
        lines.append(f"TP {lv['tp']:.2f} · SL {lv['sl']:.2f} · {int(max(0, sess['end_ts'] - time.time()))}s left")
        lines.append(f"acct: {account['name']}")
        caption = "\n".join(lines)
        telegram_bot.send_trade_open(caption, img)

    _fire(send)


def notify_exit(account: dict[str, Any], sess_slug: str, asset: str, trade: dict[str, Any], ticks: list, spot_trace: list,
                strike: float | None, start_ts: float, end_ts: float, params: dict[str, Any]) -> None:
    def send() -> None:
        won = trade["pnl"] >= 0
        img = session_chart(
            f"{asset.upper()} · {trade['side'].upper()} · {'WIN' if won else 'LOSS'} — {trade['exit_reason']} ({trade['pnl']:+.2f})",
            trade["side"], ticks, spot_trace, strike, start_ts, end_ts,
            (trade.get("entry_ts") or start_ts, trade["entry_price"]), (trade.get("exit_ts") or time.time(), trade["exit_price"], won),
            _levels(trade["entry_price"], params),
        )
        pct = (trade["exit_price"] / trade["entry_price"] - 1) * 100 if trade["entry_price"] else 0
        caption = (
            f"{'🟢' if won else '🔴'} <b>POLYMARKET MOMO — {'WIN' if won else 'LOSS'}</b>\n"
            f"<b>{asset.upper()} {trade['side'].upper()}</b> {trade['entry_price']:.3f} → <b>{trade['exit_price']:.3f}</b> "
            f"({pct:+.1f}%)\n"
            f"P&amp;L <b>{trade['pnl']:+.2f}$</b> · {trade['exit_reason']}\n"
            f"acct: {account['name']} · balance ${account['balance'] + trade['pnl']:,.2f}"
        )
        telegram_bot.send_trade_close(caption, img)

    _fire(send)


def notify_champion(champion: dict[str, Any], outcome: str, reason: str, corpus: int) -> None:
    def send() -> None:
        tm, ho = champion.get("test_metrics") or {}, champion.get("holdout") or {}
        telegram_bot.send_text(
            f"\U0001F3C6 POLYMARKET MOMO — {'NEW CHAMPION' if outcome == 'promoted' else outcome.upper()} v{champion.get('version')}\n"
            f"score {champion.get('score', 0):.2f} · OOS {tm.get('total_trades', 0)} trades, {tm.get('win_rate_pct', 0):.0f}% win, "
            f"PF {tm.get('profit_factor')}, net ${tm.get('net_pnl', 0):+.2f}\n"
            f"holdout: {ho.get('n', 0)} trades, net ${ho.get('net_pnl', 0):+.2f}\n"
            f"trained on {corpus:,} sessions · {reason}"
        )

    _fire(send)


def digest_image(state: dict[str, Any], accounts: list[dict[str, Any]], curves: dict[int, list[tuple[float, float]]],
                 history: list[dict[str, Any]]) -> bytes:
    with _render_lock:
        return _digest_image(state, accounts, curves, history)


def _digest_image(state, accounts, curves, history) -> bytes:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), dpi=120)
    fig.patch.set_facecolor(BG)
    for ax in (a1, a2):
        _style(ax)
    pal = [AMBER, CYAN, UP, VIOLET, DN, "#e8d44d", "#5aa9ff"]
    for i, a in enumerate(accounts):
        pts = curves.get(a["id"]) or []
        if len(pts) > 1:
            a1.plot([_ts(t) for t, _ in pts], [e for _, e in pts], color=pal[i % len(pal)], linewidth=1.5, label=a["name"].replace("AUTO · ", ""))
    a1.set_title("PAPER ACCOUNT EQUITY", color="#e6edf5", fontsize=9, loc="left")
    a1.legend(fontsize=6, facecolor=BG, edgecolor=GRID, labelcolor=TXT, loc="best")
    a1.xaxis.set_major_formatter(mdates.DateFormatter("%d %H:%M"))
    a1.tick_params(axis="x", rotation=25)
    if history:
        a2.plot([_ts(h["ts"]) for h in history], [h.get("best_score") or 0 for h in history], color=AMBER, linewidth=1.6, label="best score (cycle)")
        a2.plot([_ts(h["ts"]) for h in history], [h.get("champion_score") or 0 for h in history], color=UP, linewidth=1.4, label="champion")
        a2.legend(fontsize=6, facecolor=BG, edgecolor=GRID, labelcolor=TXT, loc="best")
    a2.set_title("TRAINING PROGRESS (score)", color="#e6edf5", fontsize=9, loc="left")
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%d %H:%M"))
    a2.tick_params(axis="x", rotation=25)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def notify_digest(state: dict[str, Any], accounts: list[dict[str, Any]], curves: dict, history: list[dict[str, Any]],
                  force: bool = False) -> None:
    def send() -> None:
        img = digest_image(state, accounts, curves, history)
        lines = []
        for a in accounts:
            pnl = a["balance"] - a["starting_balance"]
            lines.append(f"{'🟢' if pnl >= 0 else '🔴'} {a['name'].replace('AUTO · ', '')}: ${a['balance']:,.2f} ({pnl:+.2f}) · "
                         f"{a.get('n_trades', 0)} trades · {a.get('win_rate', 0):.0f}% win")
        champ = state.get("champion_score")
        caption = (
            f"\U0001F9EA <b>POLYMARKET MOMO — TRAINING DIGEST</b>\n"
            f"corpus <b>{state.get('n_sessions', 0):,}</b> sessions (search {state.get('n_search', 0):,} / holdout {state.get('n_holdout', 0):,})\n"
            f"cycles <b>{state.get('cycle', 0)}</b> · candidates tested <b>{state.get('total_evaluated', 0):,}</b> · "
            f"best {state.get('best_score_all') or 0:.2f}{' · champion ' + format(champ, '.2f') if champ is not None else ' · no champion yet'}\n\n"
            + "\n".join(lines)
        )
        telegram_bot.send_trade_open(caption[:1000], img)

    _fire(send, force=force)


# --------------------------------------------------------------------------- REAL trades
# The only Telegram traffic this vertical sends by default: trades on the user's real account.
def notify_real_entry(sess: dict[str, Any], trade: dict[str, Any], features: dict[str, Any], ticks: list, spot_trace: list,
                      strike: float | None, params: dict[str, Any]) -> None:
    def send() -> None:
        lv = _levels(trade["entry_price"], params)
        img = session_chart(
            f"REAL · {sess['asset'].upper()} · {trade['side'].upper()} · entry {trade['entry_price']:.3f}",
            trade["side"], ticks, spot_trace, strike, sess["start_ts"], sess["end_ts"],
            (time.time(), trade["entry_price"]), None, lv,
        )
        flip = features.get("mc_flip")
        lines = [
            "\U0001F4B5 <b>REAL TRADE — ENTRY</b>",
            f"<b>{sess['asset'].upper()} {trade['side'].upper()}</b> @ <b>{trade['entry_price']:.3f}</b> · "
            f"spent <b>${trade['cost']:.2f}</b> ({trade['shares']:.2f} shares)",
        ]
        if features.get("fav_lead") is not None:
            row = f"lead <b>{features['fav_lead']:+.3f}%</b> · momentum {features.get('drift_fav', 0):+.3f}%"
            if flip is not None:
                row += f" · flip risk <b>{flip * 100:.0f}%</b>"
            lines.append(row)
        lines.append(f"target {lv['tp']:.2f} · {int(max(0, sess['end_ts'] - time.time()))}s left")
        telegram_bot.send_trade_open("\n".join(lines), img)

    _fire(send, force=True)


def notify_real_exit(trade: dict[str, Any], ticks: list, spot_trace: list, params: dict[str, Any]) -> None:
    def send() -> None:
        won = (trade["pnl"] or 0) >= 0
        img = session_chart(
            f"REAL · {trade['asset'].upper()} · {trade['side'].upper()} · {'WIN' if won else 'LOSS'} — {trade['exit_reason']} ({trade['pnl']:+.2f})",
            trade["side"], ticks, spot_trace, trade.get("strike"), trade["start_ts"], trade["end_ts"],
            (trade["start_ts"] + 60, trade["entry_price"]), (time.time(), trade["exit_price"] or 0, won),
            _levels(trade["entry_price"], params),
        )
        pct = (trade["proceeds"] / trade["cost"] - 1) * 100 if trade.get("cost") else 0
        est = " (settlement — estimated from the resolved outcome)" if trade.get("settled_estimate") else ""
        emoji = "\U0001F7E2" if won else "\U0001F534"
        caption = (
            f"{emoji} <b>REAL TRADE — {'WIN' if won else 'LOSS'}</b>\n"
            f"<b>{trade['asset'].upper()} {trade['side'].upper()}</b> {trade['entry_price']:.3f} → <b>{(trade['exit_price'] or 0):.3f}</b> ({pct:+.1f}%)\n"
            f"P&amp;L <b>${trade['pnl']:+.2f}</b> on ${trade['cost']:.2f} · {trade['exit_reason']}{est}"
        )
        telegram_bot.send_trade_close(caption, img)

    _fire(send, force=True)


def notify_real_event(text: str) -> None:
    """Safety events on the real account (disarmed, daily limit, panic) — these matter more than any P&L line."""
    _fire(lambda: telegram_bot.send_text("⚠️ " + text), force=True)
