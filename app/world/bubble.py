"""BUBBLE RADAR — circular-financing detector (WORLD › BUBBLE RADAR).

A bubble built on circular money: A invests in B, B commits to buy from C, C buys from A. Each hop books revenue or a valuation
mark for someone, and the loop keeps inflating until one link stops paying. This module keeps a curated, sourced graph of those
flows (seeded with the 2023-2025 AI capex loop), finds every loop with networkx.simple_cycles, pulls per-company red flags from
free data, and scores each loop 0-100.

Edge direction = direction the money (or the obligation to pay) moves: investor -> investee, buyer -> seller, backstopper -> beneficiary.
amount_bn = headline USD billions as publicly reported (often "up to" / multi-year); null when no figure was disclosed.
Sources are given as publisher + date + headline; the link opens a news search for that headline (we do not guess article URLs).

Red flags (public companies, each fetched in parallel with a hard per-source budget):
  receivables   accounts receivable YoY growth more than 10 pts above revenue YoY growth (SEC XBRL companyfacts)
  concentration curated customer-concentration disclosures (10-K / S-1 / earnings)
  debt          long-term debt YoY growth > 30% (SEC XBRL)
  capex         capex / revenue > 30% (latest fiscal year, SEC XBRL)
  valuation     price / sales (trailing) > 15 (Yahoo)
  insiders      net insider selling over 6 months (OpenInsider, fallback Yahoo netSharePurchaseActivity)
  credit        ICE BofA US High Yield OAS (FRED BAMLH0A0HYM2): stress when > 4.5% or +0.75 pt in 3 months (market-wide)"""
from __future__ import annotations

import concurrent.futures as cf
import math
import re
import time
import urllib.parse
from typing import Any

import networkx as nx
import requests

_POOL = cf.ThreadPoolExecutor(max_workers=24, thread_name_prefix="bubble")
_INNER = cf.ThreadPoolExecutor(max_workers=48, thread_name_prefix="bubble-src")  # per-source fetches (never share the outer pool: no starvation)
BUDGET = 8.0
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# id, label, ticker (None = private), kind, curated note, customer-concentration (level, text)
NODES: list[tuple[str, str, str | None, str, str, tuple[str, str] | None]] = [
    ("NVDA", "NVIDIA", "NVDA", "chips", "Sells the GPUs at the centre of the loop; also invests in its own customers.", ("high", "10-K: a handful of direct customers each >10% of revenue; hyperscalers dominate data-center sales")),
    ("OPENAI", "OpenAI", None, "lab", "Private. ~$500B valuation (Oct 2025 secondary). Committed well over $1T of compute against far smaller revenue.", None),
    ("ORCL", "Oracle", "ORCL", "cloud", "Q1 FY26 remaining performance obligations of $455B, largely from OpenAI; funded with new debt.", ("high", "RPO jumped to $455B (Sep 2025), mostly one customer (OpenAI)")),
    ("MSFT", "Microsoft", "MSFT", "cloud", "OpenAI's largest early investor and Azure provider; also invests in Anthropic.", ("medium", "OpenAI is a large share of Azure commercial bookings")),
    ("AMD", "AMD", "AMD", "chips", "Pays for its OpenAI order partly in its own equity (warrant).", ("medium", "OpenAI 6 GW deal would be a large share of future data-center GPU revenue")),
    ("CRWV", "CoreWeave", "CRWV", "neocloud", "GPU cloud: Nvidia is investor, supplier and capacity backstop at the same time.", ("high", "S-1: Microsoft was 62% of 2024 revenue; OpenAI contracts $22.4B")),
    ("META", "Meta", "META", "cloud", "Buys capacity from CoreWeave on top of its own capex.", None),
    ("AVGO", "Broadcom", "AVGO", "chips", "Custom accelerators for OpenAI.", ("medium", "a few hyperscaler XPU customers")),
    ("AMZN", "Amazon / AWS", "AMZN", "cloud", "Invests in Anthropic, sells AWS compute to Anthropic and OpenAI.", None),
    ("GOOGL", "Alphabet / Google Cloud", "GOOGL", "cloud", "Invests in Anthropic, sells TPUs / Google Cloud to it.", None),
    ("ANTHROPIC", "Anthropic", None, "lab", "Private. Funded by Amazon, Google, Microsoft and Nvidia - and buys compute from three of them.", None),
    ("SOFTBANK", "SoftBank", "9984.T", "investor", "Led OpenAI's $40B round; sold its Nvidia stake (Nov 2025) to fund it.", None),
    ("XAI", "xAI", None, "lab", "Private. GPU purchases financed through an SPV backed by Nvidia equity.", None),
    ("BANKS", "Bank lenders (JPM, MUFG ...)", "JPM", "lender", "Project debt for the data centres behind the compute contracts.", None),
]

# from, to, type, amount_bn, date, note, source (publisher, headline)
EDGES: list[tuple[str, str, str, float | None, str, str, tuple[str, str]]] = [
    ("NVDA", "OPENAI", "investment", 100, "2025-09-22", "Letter of intent: up to $100B, invested progressively as each gigawatt of Nvidia systems is deployed", ("NVIDIA / OpenAI", "OpenAI and NVIDIA announce strategic partnership to deploy 10 gigawatts of NVIDIA systems")),
    ("OPENAI", "ORCL", "compute contract", 300, "2025-09-10", "~$300B of Oracle cloud compute over ~5 years starting 2027", ("Wall Street Journal", "Oracle, OpenAI Sign $300 Billion Cloud Deal")),
    ("ORCL", "NVDA", "purchase commitment", 40, "2025-05-23", "~400,000 GB200 chips for the Stargate site in Abilene, Texas", ("Financial Times", "Oracle to buy $40bn of Nvidia chips for first Stargate data centre")),
    ("OPENAI", "AMD", "purchase commitment", None, "2025-10-06", "6 GW of AMD Instinct GPUs from 2H 2026 (no $ figure disclosed; reported as tens of billions)", ("AMD / OpenAI", "AMD and OpenAI announce strategic partnership to deploy 6 gigawatts of AMD GPUs")),
    ("AMD", "OPENAI", "vendor financing", None, "2025-10-06", "Warrant for up to 160M AMD shares (~10%) vesting with deployments and AMD share-price milestones", ("AMD / OpenAI", "AMD and OpenAI announce strategic partnership to deploy 6 gigawatts of AMD GPUs")),
    ("MSFT", "OPENAI", "investment", 13, "2023-01-23", "~$13B invested 2019-2023; ~27% stake after the Oct 2025 recapitalization", ("Microsoft", "Microsoft and OpenAI extend partnership")),
    ("OPENAI", "MSFT", "compute contract", 250, "2025-10-28", "Incremental $250B Azure commitment agreed in the recapitalization", ("OpenAI / Microsoft", "The next chapter of the Microsoft-OpenAI partnership")),
    ("MSFT", "NVDA", "purchase commitment", None, "2025-01-29", "One of Nvidia's largest data-center buyers (amount undisclosed)", ("NVIDIA 10-K", "NVIDIA annual report customer concentration")),
    ("OPENAI", "CRWV", "compute contract", 22.4, "2025-09-25", "$11.9B (Mar 2025) + $4B (May 2025) + $6.5B (Sep 2025)", ("CoreWeave", "CoreWeave expands agreement with OpenAI by up to $6.5 billion")),
    ("CRWV", "OPENAI", "investment", 0.35, "2025-03-10", "$350M of CoreWeave stock issued to OpenAI alongside the first contract", ("Reuters", "CoreWeave strikes $11.9 billion contract with OpenAI")),
    ("NVDA", "CRWV", "debt backstop", 6.3, "2025-09-15", "Nvidia must buy CoreWeave's unsold capacity through April 2032", ("CoreWeave 8-K", "CoreWeave 6.3 billion capacity agreement with NVIDIA")),
    ("NVDA", "CRWV", "investment", None, "2023-04-20", "Equity stake (~7% pre-IPO), added at the IPO", ("Reuters", "Nvidia stake in CoreWeave")),
    ("CRWV", "NVDA", "purchase commitment", None, "2024-12-31", "250k+ Nvidia GPUs deployed (financed largely with GPU-backed debt)", ("CoreWeave S-1", "CoreWeave S-1 GPU fleet")),
    ("MSFT", "CRWV", "compute contract", None, "2025-03-03", "Microsoft = 62% of CoreWeave's 2024 revenue", ("CoreWeave S-1", "CoreWeave S-1 Microsoft 62% of revenue")),
    ("META", "CRWV", "compute contract", 14.2, "2025-09-30", "Through 2031", ("CoreWeave", "CoreWeave signs $14.2 billion AI infrastructure deal with Meta")),
    ("OPENAI", "AVGO", "purchase commitment", None, "2025-10-13", "10 GW of OpenAI-designed accelerators, 2026-2029", ("OpenAI / Broadcom", "OpenAI and Broadcom announce strategic collaboration to deploy 10 gigawatts of OpenAI-designed AI accelerators")),
    ("OPENAI", "AMZN", "compute contract", 38, "2025-11-03", "Seven-year AWS compute agreement", ("Amazon / OpenAI", "AWS and OpenAI announce multi-year strategic partnership")),
    ("MSFT", "ANTHROPIC", "investment", 5, "2025-11-18", "Up to $5B", ("Microsoft / NVIDIA / Anthropic", "Microsoft, NVIDIA and Anthropic announce strategic partnerships")),
    ("NVDA", "ANTHROPIC", "investment", 10, "2025-11-18", "Up to $10B", ("Microsoft / NVIDIA / Anthropic", "Microsoft, NVIDIA and Anthropic announce strategic partnerships")),
    ("ANTHROPIC", "MSFT", "compute contract", 30, "2025-11-18", "$30B of Azure compute (running on Nvidia systems)", ("Microsoft / NVIDIA / Anthropic", "Microsoft, NVIDIA and Anthropic announce strategic partnerships")),
    ("AMZN", "ANTHROPIC", "investment", 8, "2024-11-22", "$8B in total", ("Anthropic", "Powering the next generation of AI development with AWS")),
    ("ANTHROPIC", "AMZN", "compute contract", None, "2024-11-22", "AWS primary cloud + training partner (Trainium)", ("Anthropic", "Powering the next generation of AI development with AWS")),
    ("GOOGL", "ANTHROPIC", "investment", None, "2025-01-22", "Equity investments 2023-2025 (reported ~$3B; exact total undisclosed)", ("Reuters", "Google agrees new investment in Anthropic")),
    ("ANTHROPIC", "GOOGL", "compute contract", None, "2025-10-23", "Up to 1 million TPUs ('tens of billions of dollars')", ("Anthropic", "Expanding our use of Google Cloud TPUs and Services")),
    ("SOFTBANK", "OPENAI", "investment", 30, "2025-03-31", "SoftBank's $30B of the $40B round at a $300B post-money valuation", ("OpenAI", "New funding to build towards AGI")),
    ("NVDA", "XAI", "investment", 2, "2025-10-07", "Up to $2B of equity in xAI's $20B raise, via an SPV that buys Nvidia GPUs", ("Bloomberg", "Musk's xAI Raising Up to $20 Billion Backed by Nvidia Chips")),
    ("XAI", "NVDA", "purchase commitment", None, "2025-10-07", "Colossus 2 GPUs bought through the SPV and leased to xAI", ("Bloomberg", "Musk's xAI Raising Up to $20 Billion Backed by Nvidia Chips")),
    ("BANKS", "ORCL", "debt backstop", 38, "2025-10-01", "JPMorgan / MUFG-led loans for Vantage data centers built for Oracle (OpenAI capacity)", ("Reuters", "Banks to lend $38 billion for Oracle-linked data centers")),
    ("ORCL", "AMD", "purchase commitment", None, "2025-10-14", "50,000 MI450 GPUs for Oracle Cloud from Q3 2026", ("Oracle / AMD", "Oracle and AMD expand partnership to help customers achieve next-generation AI scale")),
]

TYPE_W = {"investment": 1.0, "vendor financing": 1.2, "debt backstop": 1.3, "compute contract": 0.9, "purchase commitment": 0.8}


def _src(pub: str, head: str) -> dict[str, str]:
    return {"publisher": pub, "headline": head, "url": "https://news.google.com/search?" + urllib.parse.urlencode({"q": head, "hl": "en-US"})}


def graph_data() -> dict[str, Any]:
    nodes = [{"id": i, "label": lb, "ticker": tk, "kind": k, "note": note, "concentration": {"level": c[0], "text": c[1]} if c else None} for i, lb, tk, k, note, c in NODES]
    edges = [{"id": f"e{n}", "from": a, "to": b, "type": t, "amount_bn": amt, "date": d, "note": note, "source": _src(*src)} for n, (a, b, t, amt, d, note, src) in enumerate(EDGES)]
    return {"nodes": nodes, "edges": edges}


def loops(edges: list[dict[str, Any]], max_len: int = 5) -> list[dict[str, Any]]:
    g = nx.DiGraph()
    pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in edges:
        pair.setdefault((e["from"], e["to"]), []).append(e)
        g.add_edge(e["from"], e["to"])
    out = []
    for cyc in nx.simple_cycles(g, length_bound=max_len):
        if len(cyc) < 2:
            continue
        k = cyc.index(min(cyc))  # canonical rotation
        cyc = cyc[k:] + cyc[:k]
        hops = []
        for i, a in enumerate(cyc):
            b = cyc[(i + 1) % len(cyc)]
            es = pair[(a, b)]
            known = [x["amount_bn"] for x in es if x["amount_bn"] is not None]
            hops.append({"from": a, "to": b, "edges": [x["id"] for x in es], "types": sorted({x["type"] for x in es}), "amount_bn": round(sum(known), 2) if known else None})
        amts = [h["amount_bn"] for h in hops if h["amount_bn"] is not None]
        out.append({"id": "-".join(cyc), "nodes": cyc, "hops": hops, "total_bn": round(sum(amts), 1), "known_hops": len(amts), "len": len(cyc),
                    "min_bn": min(amts) if len(amts) == len(hops) else None, "max_bn": max(amts) if amts else None})
    return out


# ------------------------------------------------------------------ red flags
def _facts(ticker: str) -> dict[str, Any]:
    from app.api import invest_routes as ir

    ck = ir._cik(ticker)  # noqa: SLF001
    if not ck:
        raise ValueError("no SEC filer")
    return ir._sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{ck[0]:010d}.json")["facts"].get("us-gaap", {})  # noqa: SLF001


def _frames(g: dict[str, Any], tags: list[str], kind: str) -> dict[str, float]:
    """frame -> value for the first tag that has recent data. kind: 'q' 3-month duration, 'i' instant, 'y' fiscal year."""
    best: dict[str, float] = {}
    for t in tags:
        units = (g.get(t) or {}).get("units", {}).get("USD") or []
        fr: dict[str, float] = {}
        for u in units:
            f = u.get("frame") or ""
            if kind == "q" and re.fullmatch(r"CY\d{4}Q\d", f) or kind == "i" and re.fullmatch(r"CY\d{4}Q\dI", f) or kind == "y" and re.fullmatch(r"CY\d{4}", f):
                fr[f] = u["val"]
        if fr and (not best or max(fr) > max(best)):
            best = fr
    return best


def _yoy(fr: dict[str, float]) -> tuple[float | None, str | None]:
    if not fr:
        return None, None
    last = max(fr)
    prior = f"CY{int(last[2:6]) - 1}{last[6:]}"
    if prior not in fr or not fr[prior]:
        return None, last
    return (fr[last] / fr[prior] - 1) * 100, last


REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
AR = ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsReceivableGrossCurrent"]
DEBT = ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "DebtInstrumentCarryingAmount"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]


def sec_flags(ticker: str) -> dict[str, Any]:
    g = _facts(ticker)
    rev_g, rq = _yoy(_frames(g, REV, "q"))
    ar_g, aq = _yoy(_frames(g, AR, "i"))
    debt_g, dq = _yoy(_frames(g, DEBT, "i"))
    rev_y, cap_y = _frames(g, REV, "y"), _frames(g, CAPEX, "y")
    yrs = sorted(set(rev_y) & set(cap_y))
    capex_ratio = cap_y[yrs[-1]] / rev_y[yrs[-1]] if yrs and rev_y[yrs[-1]] else None
    return {"rev_yoy": rev_g, "rev_q": rq, "ar_yoy": ar_g, "ar_q": aq, "debt_yoy": debt_g, "debt_q": dq, "capex_rev": capex_ratio, "capex_y": yrs[-1] if yrs else None}


def yahoo_flags(ticker: str) -> dict[str, Any]:
    from app.api.invest_routes import _yget

    j = _yget(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}", {"modules": "summaryDetail,netSharePurchaseActivity,price"}, timeout=7)
    r = ((j.get("quoteSummary") or {}).get("result") or [{}])[0]
    raw = lambda d, k: ((r.get(d) or {}).get(k) or {}).get("raw") if isinstance((r.get(d) or {}).get(k), dict) else (r.get(d) or {}).get(k)  # noqa: E731
    return {"ps": raw("summaryDetail", "priceToSalesTrailing12Months"), "mcap": raw("price", "marketCap"),
            "ins_buy": raw("netSharePurchaseActivity", "buyInfoShares"), "ins_sell": raw("netSharePurchaseActivity", "sellInfoShares"),
            "ins_net": raw("netSharePurchaseActivity", "netInfoShares"), "ins_src": "Yahoo (6 months)"}


def openinsider(ticker: str) -> dict[str, Any]:
    r = requests.get("http://openinsider.com/screener", params={"s": ticker, "fd": 180, "xp": 1, "xs": 1, "cnt": 200}, headers=UA, timeout=6)
    r.raise_for_status()
    sells = buys = 0.0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 12:
            continue
        val = cells[-5].replace("$", "").replace(",", "").replace("+", "")
        try:
            v = abs(float(val))
        except ValueError:
            continue
        if cells[6].startswith("S"):
            sells += v
        elif cells[6].startswith("P"):
            buys += v
    if not sells and not buys:
        raise ValueError("no OpenInsider rows")
    return {"ins_sell_usd": sells, "ins_buy_usd": buys, "ins_src": "OpenInsider (180 d)"}


def credit() -> dict[str, Any]:
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "BAMLH0A0HYM2"}, headers=UA, timeout=7)
    r.raise_for_status()
    rows = []
    for ln in r.text.splitlines()[1:]:
        d, _, v = ln.partition(",")
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    last = rows[-1]
    ago = next((x for x in reversed(rows) if x[0] <= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 91 * 86400))), rows[0])
    chg = last[1] - ago[1]
    return {"series": "BAMLH0A0HYM2", "date": last[0], "hy_oas": last[1], "chg_3m": round(chg, 2), "stress": last[1] > 4.5 or chg > 0.75,
            "spark": [v for _d, v in rows[-90:]]}


def flags_for(ticker: str) -> dict[str, Any]:
    """SEC + Yahoo + OpenInsider for one ticker, each with its own share of the budget; missing parts are reported, never waited on."""
    f_sec = _INNER.submit(sec_flags, ticker) if not ticker.endswith(".T") else None
    f_y, f_oi = _INNER.submit(yahoo_flags, ticker), _INNER.submit(openinsider, ticker)
    futs = [f for f in (f_sec, f_y, f_oi) if f]
    cf.wait(futs, timeout=BUDGET - 1)
    out: dict[str, Any] = {"status": {}}
    for name, f in (("sec", f_sec), ("yahoo", f_y), ("openinsider", f_oi)):
        if f is None:
            out["status"][name] = "n/a (not an SEC filer)"
        elif not f.done():
            out["status"][name] = "timed out"
        elif f.exception():
            out["status"][name] = f"{type(f.exception()).__name__}"
        else:
            out.update(f.result())
            out["status"][name] = "ok"
    return out


def checklist(t: str | None, f: dict[str, Any], conc: dict[str, str] | None) -> list[dict[str, Any]]:
    if not t:
        return [{"key": "private", "label": "Private company", "hit": None, "value": "no public filings / market data"}]
    rv, ar = f.get("rev_yoy"), f.get("ar_yoy")
    items = [
        {"key": "receivables", "label": "Receivables growing faster than revenue", "hit": (ar - rv > 10) if ar is not None and rv is not None else None,
         "value": f"AR {ar:+.0f}% vs revenue {rv:+.0f}% YoY ({f.get('ar_q')})" if ar is not None and rv is not None else "no XBRL data"},
        {"key": "concentration", "label": "Customer / related-party concentration", "hit": conc["level"] == "high" if conc else None, "value": conc["text"] if conc else "nothing curated"},
        {"key": "debt", "label": "Debt growing > 30% a year", "hit": f["debt_yoy"] > 30 if f.get("debt_yoy") is not None else None,
         "value": f"long-term debt {f['debt_yoy']:+.0f}% YoY ({f.get('debt_q')})" if f.get("debt_yoy") is not None else "no XBRL data"},
        {"key": "capex", "label": "Capex > 30% of revenue", "hit": f["capex_rev"] > 0.3 if f.get("capex_rev") is not None else None,
         "value": f"capex {f['capex_rev'] * 100:.0f}% of revenue ({f.get('capex_y')})" if f.get("capex_rev") is not None else "no XBRL data"},
        {"key": "valuation", "label": "Price / sales > 15", "hit": f["ps"] > 15 if f.get("ps") else None, "value": f"P/S {f['ps']:.1f}" if f.get("ps") else "n/a"},
    ]
    if f.get("ins_sell_usd") is not None:
        s, b = f["ins_sell_usd"], f.get("ins_buy_usd", 0)
        items.append({"key": "insiders", "label": "Net insider selling", "hit": s > 5e6 and s > 10 * b, "value": f"sold ${s / 1e6:,.0f}M vs bought ${b / 1e6:,.1f}M ({f['ins_src']})"})
    elif f.get("ins_sell") is not None:
        s, b = f.get("ins_sell") or 0, f.get("ins_buy") or 0
        items.append({"key": "insiders", "label": "Net insider selling", "hit": s > 0 and s > 3 * b, "value": f"{s / 1e6:,.2f}M shares sold vs {b / 1e6:,.2f}M bought ({f['ins_src']})"})
    else:
        items.append({"key": "insiders", "label": "Net insider selling", "hit": None, "value": "insider data unavailable"})
    return items


# ------------------------------------------------------------------ scoring
WEIGHTS = {"circularity": 30, "size": 15, "leverage": 15, "concentration": 10, "accounting": 10, "valuation": 10, "insiders": 5, "credit": 5}


def score(loop: dict[str, Any], comp: dict[str, dict[str, Any]], edges: dict[str, dict[str, Any]], cr: dict[str, Any] | None) -> dict[str, Any]:
    members = [comp[n] for n in loop["nodes"]]
    pub = [m for m in members if m["ticker"]]
    hit = lambda key: [c["hit"] for m in pub for c in m["checks"] if c["key"] == key and c["hit"] is not None]  # noqa: E731
    share = lambda xs: (sum(1 for x in xs if x) / len(xs)) if xs else 0.0  # noqa: E731
    # circularity: how much of the money comes back around (min hop / max hop when all hops are known), plus a premium for short loops
    ret = (loop["min_bn"] / loop["max_bn"]) if loop["min_bn"] is not None and loop["max_bn"] else 0.35
    types = {t for h in loop["hops"] for t in h["types"]}
    circ = min(1.0, ret + (0.25 if loop["len"] == 2 else 0.1 if loop["len"] == 3 else 0) + (0.15 if types & {"vendor financing", "debt backstop"} else 0))
    size = min(1.0, math.log10(loop["total_bn"] + 1) / 3) if loop["total_bn"] else 0.1
    backstop = any("debt backstop" in h["types"] for h in loop["hops"])
    lev = min(1.0, 0.6 * share(hit("debt")) + 0.4 * share(hit("capex")) + (0.3 if backstop else 0))
    conc_v = share([m["concentration"]["level"] == "high" for m in members if m["concentration"]]) if any(m["concentration"] for m in members) else 0.0
    acct = share(hit("receivables"))
    ps = [m["flags"].get("ps") for m in pub if m["flags"].get("ps")]
    val = min(1.0, (sum(ps) / len(ps)) / 30) if ps else 0.0
    ins = share(hit("insiders"))
    crd = min(1.0, max(0.0, (cr["hy_oas"] - 2.5) / 3.5)) if cr else 0.0
    parts = {"circularity": circ, "size": size, "leverage": lev, "concentration": conc_v, "accounting": acct, "valuation": val, "insiders": ins, "credit": crd}
    br = {k: round(WEIGHTS[k] * v, 1) for k, v in parts.items()}
    tot = round(sum(br.values()))
    return {"score": tot, "breakdown": br, "weights": WEIGHTS, "level": "EXTREME" if tot >= 70 else "HIGH" if tot >= 55 else "ELEVATED" if tot >= 40 else "LOW",
            "private": [m["label"] for m in members if not m["ticker"]]}


def build() -> dict[str, Any]:
    gd = graph_data()
    tickers = sorted({n["ticker"] for n in gd["nodes"] if n["ticker"]})
    f_cr = _POOL.submit(credit)
    f_fl = {t: _POOL.submit(flags_for, t) for t in tickers}
    cf.wait([f_cr, *f_fl.values()], timeout=BUDGET + 1)
    cr = f_cr.result() if f_cr.done() and f_cr.exception() is None else None
    comp: dict[str, dict[str, Any]] = {}
    for n in gd["nodes"]:
        t = n["ticker"]
        fl = f_fl[t].result() if t and f_fl[t].done() and f_fl[t].exception() is None else ({"status": {"all": "timed out"}} if t else {})
        n["flags"] = {k: v for k, v in fl.items() if k != "spark"}
        n["checks"] = checklist(t, fl, n["concentration"])
        n["red"] = sum(1 for c in n["checks"] if c["hit"])
        comp[n["id"]] = n
    emap = {e["id"]: e for e in gd["edges"]}
    lp = loops(gd["edges"])
    for l_ in lp:
        l_.update(score(l_, comp, emap, cr))
        l_["sources"] = [emap[eid]["source"] | {"edge": eid} for h in l_["hops"] for eid in h["edges"]]
    lp.sort(key=lambda x: (-x["score"], -x["total_bn"]))
    on_loop = {n for l_ in lp for n in l_["nodes"]}
    for n in gd["nodes"]:
        n["loops"] = sum(1 for l_ in lp if n["id"] in l_["nodes"])
        n["on_loop"] = n["id"] in on_loop
    return {**gd, "loops": lp, "credit": cr, "ts": time.time(), "weights": WEIGHTS,
            "note": "Curated from public announcements; amounts are headline figures (often 'up to' / multi-year). Loops are found with networkx.simple_cycles (length <= 5). A screen, not a forecast."}
