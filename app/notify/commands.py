"""Owner-chat Telegram commands: /report /price /chart /news /brief /earnings /ping /help.
`handle(text)` returns True when the text was one of these commands (so other handlers skip it)."""
from __future__ import annotations

import datetime as dt
import io
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.notify import telegram_bot

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

HELP = (
    "JORDAN TERMINAL commands\n\n"
    "/report AAPL - deep PDF + raw-data spreadsheet: quarterly trends, risk, ownership, options term structure, flow, earnings history (stock, ETF or coin)\n"
    "/price AAPL BTC - quick quotes (up to 8)\n"
    "/chart TSLA [1mo|3mo|6mo|1y] - price chart\n"
    "/news NVDA - latest headlines\n"
    "/brief - latest morning brief\n"
    "/study - ICEBERG course progress, /quiz - today's 10 questions now\n"
    "/earnings - today's earnings slides\n"
    "/summary - live trading summary\n"
    "/balance - main balance\n"
    "/status - server, engine, real-money account and trainer at a glance\n"
    "/real - the real account: strategy, balances, open position, last orders sent\n"
    "/realbalance 50210.50 - record the real broker balance (tracked in the terminal)\n"
    "/pre - pre-market / after-hours move of your stocks\n"
    "/realauto on|off|dry|status - let the real account follow a clearly better strategy by itself\n"
    "/ping - is the bot alive\n"
    "/help - this list"
)


def _resolve(q: str) -> str:
    from fastapi import HTTPException

    from app.api import invest_routes as inv

    try:
        return inv.resolve(q)["symbol"]
    except HTTPException as e:
        raise ValueError(str(e.detail)) from e


def _price(args: list[str]) -> str:
    from app.api import invest_routes as inv

    if not args:
        return "Usage: /price AAPL BTC"
    lines = []
    for a in args[:8]:
        try:
            sec = inv._security(_resolve(a))
            px, pct = sec.get("price"), sec.get("pct")
            lines.append(f"{sec['symbol']}  {px:,.2f}" + (f"  ({pct:+.2f}%)" if pct is not None else "") + f"  {sec.get('name') or ''}")
        except Exception:  # noqa: BLE001
            lines.append(f"{a.upper()}: not found")
    return "\n".join(lines)


def _chart(args: list[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from app.api import invest_routes as inv

    if not args:
        telegram_bot.send_text("Usage: /chart TSLA [1mo|3mo|6mo|1y]")
        return
    rng = args[-1].lower() if len(args) > 1 and args[-1].lower() in ("5d", "1mo", "3mo", "6mo", "1y", "2y") else "6mo"
    q = " ".join(args[:-1] if len(args) > 1 and args[-1].lower() == rng else args)
    sym = _resolve(q)
    h = inv._safe_history(sym, rng, "1d")
    if not h or not h["candles"]:
        telegram_bot.send_text(f"No price data for {q.upper()}")
        return
    ts = [dt.datetime.fromtimestamp(c[0]) for c in h["candles"]]
    cl = [c[4] for c in h["candles"] if c[4] is not None]
    ts = ts[-len(cl):]
    col = "#22c55e" if cl[-1] >= cl[0] else "#ef4444"
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150, facecolor="#0b0d12")
    ax.set_facecolor("#0b0d12")
    ax.plot(ts, cl, color=col, lw=1.8)
    ax.fill_between(ts, cl, min(cl), color=col, alpha=0.12)
    ax.set_title(f"{sym}  {cl[-1]:,.2f}  ({(cl[-1] / cl[0] - 1) * 100:+.1f}% over {rng})", color="#f3f4f7", fontsize=11, loc="left")
    ax.tick_params(colors="#8b93a7", labelsize=8)
    ax.grid(alpha=0.15)
    for k, sp in ax.spines.items():
        sp.set_color("#2a2f3d")
        if k in ("top", "right"):
            sp.set_visible(False)
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    telegram_bot.send_photo(b.getvalue(), f"{sym} - {rng}")


def _news(args: list[str]) -> str:
    from app.api import invest_routes as inv

    if not args:
        return "Usage: /news NVDA"
    sym = _resolve(" ".join(args))
    items = inv._news(sym, True)[:6]
    if not items:
        return f"No recent news for {sym}"
    return f"News - {sym}\n\n" + "\n\n".join(f"- {i['title']}\n  {i.get('source') or ''}  {i.get('link') or ''}" for i in items)


class _ReportStatus:
    """One Telegram message that edits itself while /report builds: data sources fetched, sections built, PDF layout, spreadsheet, upload."""

    STEPS = ["Fetching data sources", "Building report sections", "Laying out the PDF", "Building the spreadsheet", "Uploading to Telegram"]

    def __init__(self, query: str) -> None:
        self.title = query.upper()
        self.t0 = time.time()
        self.step = 0
        self.detail = ["starting", "", "", "", ""]
        self.frac = 0.02
        self.late: list[str] = []
        self.last = 0.0
        self.mid: int | None = None
        self.mid = telegram_bot.send_text_id(self._text())

    def _text(self, final: str | None = None) -> str:
        bar = "▓" * round(self.frac * 12) + "░" * (12 - round(self.frac * 12))
        lines = [f"📊 REPORT - {self.title}", f"{bar}  {round(self.frac * 100)}%   {int(time.time() - self.t0)}s", ""]
        for i, name in enumerate(self.STEPS):
            mark = "✅" if i < self.step else ("🔄" if i == self.step and final is None else "⏳")
            d = f" - {self.detail[i]}" if self.detail[i] and i <= self.step else ""
            lines.append(f"{mark} {name}{d}")
        if self.late:
            lines.append(f"\n⚠ slow or missing sources: {', '.join(self.late[:8])}")
        if final:
            lines += ["", final]
        return "\n".join(lines)

    def push(self, force: bool = False, final: str | None = None) -> None:
        now = time.time()
        if not force and now - self.last < 2.5:
            return
        self.last = now
        if self.mid:
            telegram_bot.edit_text(self.mid, self._text(final))

    def on_progress(self, stage: str, **k: Any) -> None:
        if stage == "resolved":
            self.title = f"{k.get('sym')} ({str(k.get('name') or '')[:30]})"
            self.detail[0] = "resolved the symbol"
        elif stage == "fetch":
            self.step, self.frac = 0, 0.05 + 0.40 * k["done"] / max(1, k["total"])
            self.detail[0] = f"{k['done']}/{k['total']} sources answered"
        elif stage == "fetched":
            self.late = k.get("late") or []
            self.step, self.frac = 1, 0.45
        elif stage == "sections":
            self.step, self.frac = 1, 0.45 + 0.40 * k["done"] / max(1, k["total"])
            self.detail[1] = f"{k['done']}/{k['total']} - {str(k.get('name') or '').replace('_', ' ')}"
        elif stage == "layout":
            self.step, self.frac = 2, 0.86
            self.detail[1] = "done"
            self.push(force=True)
            return
        self.push()

    def advance(self, step: int, frac: float, detail: str = "") -> None:
        self.step, self.frac = step, frac
        if detail:
            self.detail[step] = detail
        self.push(force=True)


def _report(args: list[str]) -> None:
    from app.notify import report_pdf, report_xlsx

    if not args:
        telegram_bot.send_text("Usage: /report AAPL   (or a coin: /report BTC)")
        return
    q = " ".join(args)
    st = _ReportStatus(q)
    try:
        pdf, sym, sets = report_pdf.build(q, progress=st.on_progress)
    except ValueError as e:
        st.push(force=True, final=f"❌ Couldn't build a report for '{q}': {e}")
        return
    except Exception:
        logger.exception("report build failed")
        st.push(force=True, final=f"❌ The report for '{q}' failed while building. Try again in a moment.")
        return
    st.detail[2] = "done"
    st.advance(3, 0.92, "raw data file")
    stamp = dt.datetime.now(NY).strftime("%Y-%m-%d")
    xlsx = None
    try:
        xlsx = report_xlsx.build(sym, sets)
    except Exception:
        logger.exception("report data file failed")
    st.detail[3] = "done" if xlsx else "skipped (failed)"
    st.advance(4, 0.96, f"PDF {len(pdf) // 1024} KB")
    if not telegram_bot.send_document(pdf, f"{sym}_report_{stamp}.pdf", f"{sym} report - screening data, not investment advice."):
        st.push(force=True, final="❌ The report was built but the upload failed.")
        return
    if xlsx:
        data, ext, mime = xlsx
        telegram_bot.send_document(data, f"{sym}_data_{stamp}.{ext}", f"{sym} raw data behind the report (one sheet per dataset).", mime)
    st.step, st.frac = len(st.STEPS), 1.0
    st.detail[4] = "sent"
    st.push(force=True, final=f"✅ Done in {int(time.time() - st.t0)}s: PDF{' + data file' if xlsx else ''} sent below.")


def _brief() -> str:
    from app.morning import brief

    b = brief.latest()
    return b["text"] if b and b.get("text") else "No morning brief stored yet."


def _real_text() -> str:
    from app import db
    from app.notify import connections

    acct = next((a for a in db.list_accounts(include_archived=False) if a.get("traderspost_linked")), None)
    if not acct:
        return "No account is linked to TradersPost."
    b = connections.balance_summary()
    typed = b.get("typed")
    pos = acct.get("open_position")
    lines = [f"{acct['name']}: {acct.get('strategy')} {acct.get('symbol')}",
             f"Terminal balance: ${acct['balance']:,.2f}",
             f"Real balance (typed): " + (f"${typed['balance']:,.2f} at {typed['at'][:16]} UTC" if typed else "not recorded - send /realbalance 50210.50"),
             "Open position: " + (f"{pos.get('side')} {pos.get('qty')} @ {pos.get('entry_price')}" if pos else "flat")]
    for s in connections.recent_signals(3):
        lines.append(f"{s['ts'][:16]} UTC {str(s.get('action')).upper()} {s.get('qty') or ''} {s.get('ticker')} @ {s.get('price')} stop {s.get('stop')} target {s.get('target')} {'OK' if s.get('ok') else 'FAILED'}")
    return "\n".join(lines)


def _real_balance(args: list[str]) -> str:
    from app.notify import connections

    if not args:
        return "Send the balance: /realbalance 50210.50"
    try:
        connections.add_balance(float(args[0].replace("$", "").replace(",", "")), "typed in Telegram")
    except ValueError as e:
        return str(e)
    return "Saved.\n" + _real_text()


def _pre_text() -> str:
    """Pre-market / after-hours move of every stock in the portfolio, biggest first (same data as the CAPITAL portfolio panel)."""
    from app.api import capital_routes as cr

    p = cr.get_portfolio()
    rows = [h for h in p["holdings"] if h.get("ext") and h["ext"].get("pct") is not None]
    if not rows:
        return "No pre-market or after-hours moves right now (outside extended hours, or your stocks have not traded there)."
    rows.sort(key=lambda h: -abs(h["ext"]["pct"]))
    return "\n".join(f"{h['symbol']} - {'pre' if h['ext']['state'] == 'PRE' else 'AH'} {h['ext']['pct']:+.1f}%  ({h['ext']['price']:,.2f})" for h in rows)


def handle(text: str) -> bool:
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return False
    cmd, args = parts[0].lower().split("@")[0], parts[1:]
    try:
        if cmd in ("/help", "/start", "/commands"):
            telegram_bot.send_text(HELP)
        elif cmd == "/status":
            from app.notify import ops_alerts

            telegram_bot.send_text(ops_alerts.status_text())
        elif cmd == "/pa":
            from app.accounts import pa_rules

            telegram_bot.send_text(pa_rules.status_text())
        elif cmd == "/realbalance":
            telegram_bot.send_text(_real_balance(args))
        elif cmd == "/real":
            telegram_bot.send_text(_real_text())
        elif cmd == "/pre":
            telegram_bot.send_text(_pre_text())
        elif cmd == "/realauto":
            from app.accounts import real_follower

            if args and args[0].lower() in ("on", "off", "dry"):
                real_follower.set_mode(args[0])
            telegram_bot.send_text(real_follower.status_text())
        elif cmd in ("/study", "/iceberg"):
            from app.learn import iceberg

            telegram_bot.send_text(iceberg.status_text())
        elif cmd == "/bigmoves":
            import os

            from app.intel.bigmoves import OUT

            path = os.path.join(OUT, "BigMovers_research.pdf")
            if "rebuild" in " ".join(args or []).lower() or not os.path.exists(path):
                from app.notify import report_bigmoves

                telegram_bot.send_text("Rendering the BIG MOVERS research PDF...")
                report_bigmoves.build()
            telegram_bot.send_document(open(path, "rb").read(), "BigMovers_research.pdf", "BIG MOVERS - why stocks move 10% in a day. Research, not investment advice.")
        elif cmd == "/quiz":
            from app.learn import iceberg

            if not iceberg.send_quiz():
                telegram_bot.send_text("The quiz for today was already sent (or the AI is busy - try again in a minute).")
        elif cmd == "/ping":
            telegram_bot.send_text("pong " + dt.datetime.now(NY).strftime("%H:%M:%S NY"))
        elif cmd == "/price":
            telegram_bot.send_text(_price(args))
        elif cmd == "/chart":
            _chart(args)
        elif cmd == "/news":
            telegram_bot.send_text(_news(args))
        elif cmd == "/brief":
            telegram_bot.send_text(_brief())
        elif cmd == "/earnings":
            from app.morning import earnings_report

            if not earnings_report.send(force=True).get("sent"):
                telegram_bot.send_text("No earnings slides to send right now.")
        elif cmd == "/report":
            _report(args)
        else:
            return False
    except ValueError as e:
        telegram_bot.send_text(str(e))
    except Exception:
        logger.exception("telegram command %s failed", cmd)
        telegram_bot.send_text(f"{cmd} failed - try again in a moment.")
    return True
