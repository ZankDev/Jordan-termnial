"""Shared styles, formatters and table/chart helpers for the /report PDF (report_pdf.py and report_sections.py)."""
from __future__ import annotations

import datetime as dt
import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle  # noqa: E402

AMBER, INK, MUTED, LINE = colors.HexColor("#d9830c"), colors.HexColor("#14161d"), colors.HexColor("#6b7285"), colors.HexColor("#e3e6ee")
GREEN, RED = colors.HexColor("#15803d"), colors.HexColor("#b91c1c")

H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=INK)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=14, textColor=MUTED)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=AMBER, spaceBefore=12, spaceAfter=5, keepWithNext=1)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=11.5, textColor=INK)



def _s(x: Any) -> str:
    """Latin-1-safe, XML-escaped text for reportlab's built-in fonts."""
    t = "" if x is None else str(x)
    t = t.encode("cp1252", "replace").decode("cp1252")
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _n(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{d}f}"


def _big(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:,.2f}{suf}"
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def _usd(v: float | None) -> str:
    return "n/a" if v is None else ("-" if v < 0 else "") + "$" + _big(abs(v))


def _p(v: float | None, d: int = 1, scale: float = 1.0) -> str:
    return "n/a" if v is None else f"{v * scale:+.{d}f}%"


def _pct_plain(v: float | None, scale: float = 100.0) -> str:
    return "n/a" if v is None else f"{v * scale:.1f}%"


def _price(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:,.6f}".rstrip("0").rstrip(".")


def _table(rows: list[list[str]], widths: list[float], header: bool = False, esc: bool = True) -> KeepTogether:
    data = [[Paragraph(_s(c) if esc else c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f5f8"))]
    t.setStyle(TableStyle(st))
    return KeepTogether([t])



def _kv(pairs: list[tuple[str, str]], cols: int = 2, width: float = 174 * mm) -> KeepTogether:
    """Label/value grid with `cols` pairs per row. Pairs with no data (n/a) are left out; a grid with nothing left is a zero-height spacer."""
    pairs = [(k, v) for k, v in pairs if str(v).strip().lower() not in ("n/a", "", "-", "n/a / n/a", "none")]
    if not pairs:
        return Spacer(1, 0)
    rows = []
    for i in range(0, len(pairs), cols):
        row: list[str] = []
        for k, v in pairs[i:i + cols]:
            row += [f"<font color='#6b7285'>{_s(k)}</font>", f"<b>{_s(v)}</b>"]
        row += [""] * (cols * 2 - len(row))
        rows.append(row)
    lw, vw = width / cols * 0.42, width / cols * 0.58
    return _table(rows, [lw, vw] * cols, esc=False)


def _colored(v: float | None, text: str) -> str:
    c = "#6b7285" if v is None else ("#15803d" if v >= 0 else "#b91c1c")
    return f"<font color='{c}'><b>{_s(text)}</b></font>"



def _png(fig) -> bytes:
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png", pil_kwargs={"compress_level": 1})
    plt.close(fig)
    return b.getvalue()


def _style_ax(*axes) -> None:
    for a in axes:
        a.spines[["top", "right"]].set_visible(False)
        a.tick_params(labelsize=7)
        a.grid(alpha=0.25, lw=0.5)


def _d(v: Any, fmt: str = "%d %b %Y") -> str:
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        try:
            return dt.datetime.fromtimestamp(v).strftime(fmt)
        except (OverflowError, OSError, ValueError):
            return ""
    return str(v)[:10]


def _dot(tone: str) -> str:
    return {"good": "<font color='#15803d'>&#9679;</font>", "bad": "<font color='#b91c1c'>&#9679;</font>"}.get(tone, "<font color='#d9830c'>&#9679;</font>")



def _sub(text: str) -> Paragraph:
    return Paragraph(_s(text), SMALL)


W = 174 * mm


def _h(f: Any) -> float:
    inner = getattr(f, "_content", None)
    if inner:
        return sum(_h(x) for x in inner)
    try:
        return f.wrap(W, 1000 * mm)[1]
    except Exception:  # noqa: BLE001
        return 60 * mm


CELL_S = ParagraphStyle("cell_s", fontName="Helvetica", fontSize=7.6, leading=9.4, textColor=INK)
CELL_SR = ParagraphStyle("cell_sr", parent=CELL_S, alignment=2)


def _dtable(rows: list[list[str]], widths: list[float], right: int = 1, esc: bool = True, bold_first: bool = False, repeat: bool = True) -> Table:
    """Dense, splittable data table: small font, numbers right-aligned from column `right`, header row repeated, zebra stripes."""
    data = []
    for ri, r in enumerate(rows):
        line = []
        for ci, c in enumerate(r):
            t = _s(c) if esc else str(c)
            if ri == 0:
                t = f"<b>{t}</b>"
            elif bold_first and ci == 0:
                t = f"<b>{t}</b>"
            line.append(Paragraph(t, CELL_SR if ci >= right else CELL_S))
        data.append(line)
    t = Table(data, colWidths=widths, repeatRows=1 if repeat else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceef4")),
          ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#c9cedb"))]
    for i in range(2, len(rows), 2):
        st.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8f9fb")))
    t.setStyle(TableStyle(st))
    return t
