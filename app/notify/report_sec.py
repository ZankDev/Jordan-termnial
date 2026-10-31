"""SEC EDGAR extras for the /report PDF: 10-year annual + 12-quarter XBRL financials, 8-K events (earnings dates), filing tallies."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# key, label, unit, tags in order of preference
LINES: list[tuple[str, str, str, list[str]]] = [
    ("revenue", "Revenue", "USD", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    ("cogs", "Cost of revenue", "USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    ("gross", "Gross profit", "USD", ["GrossProfit"]),
    ("rnd", "R&D expense", "USD", ["ResearchAndDevelopmentExpense"]),
    ("sga", "SG&A expense", "USD", ["SellingGeneralAndAdministrativeExpense"]),
    ("opex", "Total operating expenses", "USD", ["OperatingExpenses", "CostsAndExpenses"]),
    ("opinc", "Operating income", "USD", ["OperatingIncomeLoss"]),
    ("intexp", "Interest expense", "USD", ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]),
    ("pretax", "Pre-tax income", "USD", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("tax", "Income tax expense", "USD", ["IncomeTaxExpenseBenefit"]),
    ("netinc", "Net income", "USD", ["NetIncomeLoss", "ProfitLoss"]),
    ("eps", "Diluted EPS", "USD/shares", ["EarningsPerShareDiluted"]),
    ("shares", "Diluted shares", "shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    ("da", "Depreciation & amortization", "USD", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"]),
    ("sbc", "Stock-based compensation", "USD", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]),
    ("ocf", "Operating cash flow", "USD", ["NetCashProvidedByUsedInOperatingActivities"]),
    ("capex", "Capital expenditure", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("icf", "Investing cash flow", "USD", ["NetCashProvidedByUsedInInvestingActivities"]),
    ("fincf", "Financing cash flow", "USD", ["NetCashProvidedByUsedInFinancingActivities"]),
    ("div", "Dividends paid", "USD", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    ("buyback", "Share repurchases", "USD", ["PaymentsForRepurchaseOfCommonStock"]),
    ("cash", "Cash & equivalents", "USD", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("ar", "Accounts receivable", "USD", ["AccountsReceivableNetCurrent"]),
    ("inv", "Inventory", "USD", ["InventoryNet"]),
    ("curassets", "Current assets", "USD", ["AssetsCurrent"]),
    ("ppe", "PP&E (net)", "USD", ["PropertyPlantAndEquipmentNet"]),
    ("goodwill", "Goodwill", "USD", ["Goodwill"]),
    ("assets", "Total assets", "USD", ["Assets"]),
    ("ap", "Accounts payable", "USD", ["AccountsPayableCurrent"]),
    ("curliab", "Current liabilities", "USD", ["LiabilitiesCurrent"]),
    ("debt", "Long-term debt", "USD", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("liab", "Total liabilities", "USD", ["Liabilities"]),
    ("retained", "Retained earnings", "USD", ["RetainedEarningsAccumulatedDeficit"]),
    ("equity", "Shareholders' equity", "USD", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
]
INSTANT = {"cash", "ar", "inv", "curassets", "ppe", "goodwill", "assets", "ap", "curliab", "debt", "liab", "retained", "equity"}
NOQ4 = {"shares"}  # weighted-average counts cannot be derived as FY minus 9M
LABEL = {k: lab for k, lab, _u, _t in LINES} | {"fcf": "Free cash flow"}
ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "20-F", "40-F")
QUARTER_FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A")

ITEM_TEXT = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy", "2.01": "Acquisition / disposal completed", "2.02": "Earnings results",
    "2.03": "New debt obligation", "2.04": "Debt acceleration", "2.05": "Restructuring / exit costs", "2.06": "Material impairment", "3.01": "Listing notice / delisting",
    "3.02": "Unregistered equity sale", "3.03": "Change in shareholder rights", "4.01": "Auditor change", "4.02": "Financials non-reliance", "5.01": "Change in control",
    "5.02": "Officer / director change", "5.03": "Bylaws amended", "5.04": "Trading suspension", "5.05": "Ethics code change", "5.07": "Shareholder vote", "5.08": "Director nominations",
    "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits / financial statements",
}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _entries(facts: dict[str, Any], tag: str, unit: str) -> list[dict[str, Any]]:
    return ((facts.get(tag) or {}).get("units") or {}).get(unit) or []


def _annual_line(facts: dict[str, Any], tags: list[str], unit: str, instant: bool) -> dict[str, float]:
    out: dict[str, tuple[str, float]] = {}
    for tag in tags:
        for e in _entries(facts, tag, unit):
            if e.get("form") not in ANNUAL_FORMS or e.get("fp") != "FY":
                continue
            end = e.get("end")
            try:
                if not instant and not 340 <= (_d(end) - _d(e["start"])).days <= 380:
                    continue
            except Exception:  # noqa: BLE001
                continue
            if end not in out or e["filed"] >= out[end][0]:
                if end in out and tag != tags[0] and out[end][0] > e["filed"]:
                    continue
                out[end] = (e["filed"], float(e["val"]))
    return {k: v[1] for k, v in out.items()}


def _quarter_raw(facts: dict[str, Any], tags: list[str], unit: str, instant: bool) -> dict[str, float]:
    """Directly reported quarter values (3-month durations, or balance-sheet points), newest filing wins, preferred tag first per period."""
    out: dict[str, float] = {}
    for tag in tags:
        got: dict[str, tuple[str, float]] = {}
        for e in _entries(facts, tag, unit):
            if e.get("form") not in QUARTER_FORMS:
                continue
            end = e.get("end")
            try:
                if not instant and not 75 <= (_d(end) - _d(e["start"])).days <= 105:
                    continue
            except Exception:  # noqa: BLE001
                continue
            if end not in got or e["filed"] >= got[end][0]:
                got[end] = (e["filed"], float(e["val"]))
        for end, (_f, v) in got.items():
            out.setdefault(end, v)
    return out


def _derive_q4(facts: dict[str, Any], tags: list[str], unit: str, quarters: dict[str, float], annual: dict[str, float]) -> None:
    """Q4 = fiscal year - (Q1+Q2+Q3) when the three earlier quarters of that year are all present."""
    qd = sorted(quarters)
    for fy_end, fy_val in annual.items():
        if fy_end in quarters:
            continue
        end = _d(fy_end)
        prev = [q for q in qd if 60 <= (end - _d(q)).days <= 290]
        if len(prev) == 3 and all(q < fy_end for q in prev):
            quarters[fy_end] = fy_val - sum(quarters[q] for q in prev)


def _derived_ytd(facts: dict[str, Any], tags: list[str], unit: str, quarters: dict[str, float]) -> None:
    """Cash-flow items are reported cumulative (3/6/9 months): turn YTD values into single quarters where the 3-month fact is missing."""
    for tag in tags:
        ytd: dict[str, tuple[str, float, int]] = {}
        for e in _entries(facts, tag, unit):
            if e.get("form") not in ("10-Q", "10-Q/A"):
                continue
            try:
                days = (_d(e["end"]) - _d(e["start"])).days
            except Exception:  # noqa: BLE001
                continue
            if 160 <= days <= 290:
                k = e["end"]
                if k not in ytd or e["filed"] >= ytd[k][0]:
                    ytd[k] = (e["filed"], float(e["val"]), days)
        for end, (_f, val, days) in ytd.items():
            if end in quarters:
                continue
            prior = [(k, v) for k, v in ytd.items() if 70 <= (_d(end) - _d(k)).days <= 110 and v[2] < days]
            if prior:
                quarters[end] = val - prior[0][1][1]
            elif days <= 195:  # first half: Q2 = H1 - Q1 where Q1 is a known 3-month fact
                q1 = [k for k in quarters if 70 <= (_d(end) - _d(k)).days <= 110]
                if q1:
                    quarters[end] = val - quarters[q1[0]]


def build(inv: Any, sym: str) -> dict[str, Any] | None:
    ck = inv._cik(sym)
    if not ck:
        return None
    cik, _title = ck

    def go() -> dict[str, Any]:
        facts = inv._sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")["facts"]
        gaap = facts.get("us-gaap") or {}
        ann: dict[str, dict[str, float]] = {}
        qtr: dict[str, dict[str, float]] = {}
        for key, _label, unit, tags in LINES:
            inst = key in INSTANT
            a = _annual_line(gaap, tags, unit, inst)
            q = _quarter_raw(gaap, tags, unit, inst)
            if not inst:
                if key not in NOQ4:
                    _derived_ytd(gaap, tags, unit, q)
                    _derive_q4(gaap, tags, unit, q, a)
            else:
                for end, v in a.items():
                    q.setdefault(end, v)
            if a:
                ann[key] = a
            if q:
                qtr[key] = q
        if "gross" not in ann and "revenue" in ann and "cogs" in ann:
            ann["gross"] = {d: ann["revenue"][d] - ann["cogs"][d] for d in ann["revenue"] if d in ann["cogs"]}
        if "gross" not in qtr and "revenue" in qtr and "cogs" in qtr:
            qtr["gross"] = {d: qtr["revenue"][d] - qtr["cogs"][d] for d in qtr["revenue"] if d in qtr["cogs"]}
        for store in (ann, qtr):
            if "ocf" in store:
                store["fcf"] = {d: store["ocf"][d] - abs(store.get("capex", {}).get(d, 0.0)) for d in store["ocf"]}
        return {"annual": ann, "quarterly": qtr}
    from app.api.capital_routes import cached

    data = cached(f"rptsec:{cik}", 6 * 3600, go)
    try:
        sub = cached(f"rptsub:{cik}", 1800, lambda: inv._sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    except Exception:  # noqa: BLE001
        sub = None
    data = dict(data)
    data.update(_events(sub, cik) if sub else {"events": [], "form_counts": {}, "form4_by_month": []})
    return data


def _events(sub: dict[str, Any], cik: int) -> dict[str, Any]:
    rec = (sub.get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    n = len(forms)
    col = lambda k: rec.get(k) or [""] * n  # noqa: E731
    events, counts = [], Counter()
    f4: Counter = Counter()
    cutoff = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    for i, f in enumerate(forms):
        filed = col("filingDate")[i]
        if filed >= cutoff:
            counts[f] += 1
            if f in ("4", "4/A"):
                f4[filed[:7]] += 1
        if f in ("8-K", "8-K/A") and len(events) < 60:
            items = [x.strip() for x in (col("items")[i] or "").split(",") if x.strip()]
            acc = col("accessionNumber")[i]
            doc = col("primaryDocument")[i]
            events.append({"filed": filed, "accepted": col("acceptanceDateTime")[i], "items": items,
                           "what": "; ".join(ITEM_TEXT.get(x, f"Item {x}") for x in items if x != "9.01") or "Current report",
                           "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else ""})
    months = sorted(f4)[-12:]
    return {"events": events, "form_counts": dict(counts.most_common()), "form4_by_month": [(m, f4[m]) for m in months]}


def earnings_dates(events: list[dict[str, Any]]) -> list[tuple[dt.date, bool]]:
    """(release date, after_close) for 8-Ks carrying Item 2.02, newest first, de-duplicated within 25 days."""
    out: list[tuple[dt.date, bool]] = []
    for e in events:
        if "2.02" not in e["items"]:
            continue
        d = _d(e["filed"])
        after = False
        acc = e.get("accepted") or ""
        if acc:
            try:
                t = dt.datetime.fromisoformat(acc.replace("Z", "+00:00")).astimezone(NY)
                d, after = t.date(), t.hour >= 16
            except ValueError:
                pass
        if any(abs((d - x).days) < 25 for x, _ in out):
            continue
        out.append((d, after))
    return out
