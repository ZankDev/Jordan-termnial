"""Companion raw-data file for /report: one sheet per dataset (xlsx, or a zip of CSVs if openpyxl is missing)."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from typing import Any

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _cell(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, int, float, bool)) or v is None:
        return v
    s = re.sub(r"<[^>]+>", "", str(v)).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return s[:32000]


def _name(n: str, used: set[str]) -> str:
    base = re.sub(r"[\/*?:\[\]]", " ", n).strip()[:28] or "Sheet"
    out, i = base, 2
    while out.lower() in used:
        out = f"{base[:25]}_{i}"
        i += 1
    used.add(out.lower())
    return out


def build(sym: str, sets: dict[str, tuple[list[str], list[list[Any]]]]) -> tuple[bytes, str, str]:
    """(bytes, extension, mime)."""
    sets = {k: v for k, v in sets.items() if v[1]}
    used: set[str] = set()
    names = {k: _name(k, used) for k in sets}
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for k, (hdr, rows) in sets.items():
                s = io.StringIO()
                w = csv.writer(s)
                w.writerow(hdr)
                w.writerows([[_cell(v) for v in r] for r in rows])
                z.writestr(f"{sym}_{names[k]}.csv", s.getvalue())
        return buf.getvalue(), "zip", "application/zip"

    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Index"
    ws0.append([f"{sym} - raw data behind the PDF report ({dt.datetime.now():%Y-%m-%d %H:%M})"])
    ws0["A1"].font = Font(bold=True, size=12)
    ws0.append(["Screening data, not investment advice."])
    ws0.append([])
    ws0.append(["Sheet", "Rows"])
    for c in ws0[4]:
        c.font = Font(bold=True)
    fill = PatternFill("solid", fgColor="F3E3C7")
    for k, (hdr, rows) in sets.items():
        ws = wb.create_sheet(names[k])
        ws.append([_cell(h) for h in hdr])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = fill
        for r in rows:
            ws.append([_cell(v) for v in r])
        ws.freeze_panes = "A2"
        for i, h in enumerate(hdr, 1):
            w = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:200] if i - 1 < len(r) and r[i - 1] is not None])
            ws.column_dimensions[get_column_letter(i)].width = min(max(9, w + 2), 60)
        ws0.append([names[k], len(rows)])
    ws0.column_dimensions["A"].width = 34
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), "xlsx", XLSX_MIME
