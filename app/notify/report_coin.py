"""Coin-only /report sections: market data, derivatives, sentiment, network."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Paragraph  # noqa: E402

from app.notify import report_calc as rc  # noqa: E402
from app.notify.report_sections import Ctx, pc  # noqa: E402
from app.notify.report_ui import BODY, _big, _d, _dtable, _kv, _n, _p, _pct_plain, _price, _s, _style_ax, _usd  # noqa: E402


def coin_data(X: Ctx) -> None:
    cg = X.D.get("coin") or {}
    m = cg.get("market") or {}
    if not m:
        return
    X.title("COIN DATA (COINGECKO)")
    sup, mx, tot = m.get("supply"), m.get("max_supply"), m.get("total_supply")
    mc, fdv, vol = m.get("mcap"), m.get("fdv"), m.get("vol")
    kv = [("Rank", f"#{cg['rank']}" if cg.get("rank") else "n/a"), ("Market cap", _usd(mc)), ("Fully diluted valuation", _usd(fdv)),
          ("FDV / market cap", f"{fdv / mc:.2f}x" if fdv and mc else "n/a"), ("24h volume", _usd(vol)), ("Volume / market cap (turnover)", _pct_plain(vol / mc, 100) if vol and mc else "n/a"),
          ("24h high / low", f"{_price(m.get('high24'))} / {_price(m.get('low24'))}"), ("Position in 24h range", _pct_plain((m["price"] - m["low24"]) / (m["high24"] - m["low24"]), 100)
                                                                                         if m.get("price") and m.get("high24") and m.get("low24") and m["high24"] > m["low24"] else "n/a"),
          ("Circulating supply", _big(sup)), ("Total supply", _big(tot)), ("Max supply", _big(mx) if mx else "none / n/a"),
          ("Circulating / max", _pct_plain(sup / mx, 100) if sup and mx else "n/a"), ("Unissued vs max (future supply)", _pct_plain(1 - sup / mx, 100) if sup and mx else "n/a"),
          ("ATH", f"${_price(m.get('ath'))} ({_p(m.get('ath_pct'))}) {_s((m.get('ath_date') or '')[:10])}"), ("ATL", f"${_price(m.get('atl'))} ({_p(m.get('atl_pct'), 0)})"),
          ("1h / 24h", f"{_p(m.get('p1h'))} / {_p(m.get('p24h'))}"), ("7d / 30d", f"{_p(m.get('p7d'))} / {_p(m.get('p30d'))}"), ("1 year", _p(m.get("p1y"))),
          ("Genesis", cg.get("genesis") or "n/a"), ("Hashing", cg.get("hashing") or "n/a")]
    X.add(_kv(kv))
    X.data("Coin data", ["Metric", "Value"], [[k, v] for k, v in kv])
    if cg.get("categories"):
        X.sp(3)
        X.note("Categories: " + ", ".join(cg["categories"]))
    links = cg.get("links") or {}
    if links:
        X.note("Links: " + "  |  ".join(f"{k}: {v}" for k, v in links.items() if v))
    X.kpi("Market cap", _usd(mc))
    if cg.get("rank"):
        X.kpi("CoinGecko rank", f"#{cg['rank']}")
    if m.get("ath_pct") is not None:
        X.kpi("From all-time high", _p(m["ath_pct"], 0), "bad" if m["ath_pct"] < -50 else None)
        if m["ath_pct"] < -70:
            X.flag("bad", f"Trading {abs(m['ath_pct']):.0f}% below its all-time high", "Trend")
    if fdv and mc and fdv / mc > 1.5:
        X.flag("bad", f"FDV is {fdv / mc:.1f}x market cap: large future supply overhang", "Valuation")
    if sup and mx and sup / mx < 0.5:
        X.flag("info", f"Only {sup / mx * 100:.0f}% of max supply is circulating", "Valuation")
    if vol and mc and vol / mc > 0.3:
        X.flag("info", f"24h volume is {vol / mc * 100:.0f}% of market cap (very high turnover)", "Sentiment")
    desc = cg.get("description")
    if desc:
        X.sp(4)
        txt = re.sub(r"<[^>]+>", "", re.sub(r"\s+", " ", desc))
        if len(txt) > 1800:
            txt = txt[:1800].rsplit(". ", 1)[0] + "."
        X.text(_s(txt))


def derivatives(X: Ctx) -> None:
    perps = (X.D.get("perps") or {}).get("rows") or []
    fs = rc.funding_summary(perps, X.sym)
    if not fs:
        return
    X.title("DERIVATIVES: BYBIT PERPETUALS")
    X.add(_kv([("Perp price", _price(fs["price"])), ("24h change (perp)", _p(fs.get("pct24"), 2)), ("Funding rate (8h)", f"{fs['funding'] * 100:.4f}%"), ("Annualised funding", f"{fs['apr'] * 100:.1f}%"),
               ("Open interest", _usd(fs.get("oi_usd"))), ("24h turnover", _usd(fs.get("turnover"))), ("OI / turnover", f"{fs['oi_usd'] / fs['turnover']:.2f}x" if fs.get("turnover") else "n/a"),
               ("Next funding", _d(fs.get("next"), "%d %b %H:%M")), ("Positioning", fs["tone"])]))
    X.data("Perp funding", ["Metric", "Value"], [["Funding 8h %", fs["funding"] * 100], ["APR %", fs["apr"] * 100], ["OI USD", fs.get("oi_usd")], ["Turnover USD", fs.get("turnover")]])
    X.kpi("Funding APR", f"{fs['apr'] * 100:.0f}%", "bad" if fs["apr"] > 0.3 else None)
    if fs["funding"] > 0.03:
        X.flag("bad", f"Funding {fs['funding'] * 100:.3f}% per 8h ({fs['apr'] * 100:.0f}% APR): crowded longs pay shorts", "Sentiment")
    elif fs["funding"] < -0.01:
        X.flag("info", f"Negative funding {fs['funding'] * 100:.3f}%: shorts are paying longs", "Sentiment")
    X.sp(6)
    top = sorted(perps, key=lambda r: -(r.get("oi_usd") or 0))[:12]
    t = [["Perp", "Price", "24h", "Funding 8h", "APR", "Open interest"]] + [[_s(r["symbol"]), _price(r["price"]), pc(r.get("pct24"), 1), f"{r['funding'] * 100:.4f}%", f"{r['apr'] * 100:.0f}%", _usd(r.get("oi_usd"))] for r in top]
    X.add(Paragraph("<b>Largest perpetual markets by open interest (context)</b>", BODY))
    X.sp(2)
    X.add(_dtable(t, [34 * mm, 30 * mm, 24 * mm, 30 * mm, 26 * mm, 30 * mm], esc=False))
    X.data("Perp markets", ["Symbol", "Price", "24h %", "Funding 8h", "APR", "OI USD"], [[r["symbol"], r["price"], r.get("pct24"), r["funding"], r["apr"], r.get("oi_usd")] for r in top])


def sentiment(X: Ctx) -> None:
    D = X.D
    fng, tr, gl = D.get("fng") or {}, (D.get("trending") or {}).get("coins") or [], D.get("cglobal") or {}
    if not (fng.get("history") or tr or gl):
        return
    X.title("MARKET SENTIMENT AND BREADTH")
    kv = []
    now = fng.get("now")
    if now:
        kv.append(("Fear & Greed index (crypto-wide)", f"{now[1]} ({now[2]})"))
    hist = fng.get("history") or []
    if len(hist) > 30:
        kv += [("30 days ago", str(hist[-31][1])), ("90-day average", f"{sum(h[1] for h in hist) / len(hist):.0f}")]
    if gl:
        kv += [("Total crypto market cap", _usd(gl.get("mcap"))), ("24h change", _p(gl.get("chg24"), 2)), ("BTC dominance", f"{gl.get('btc_dom'):.1f}%" if gl.get("btc_dom") is not None else "n/a"),
               ("ETH dominance", f"{gl.get('eth_dom'):.1f}%" if gl.get("eth_dom") is not None else "n/a"), ("24h total volume", _usd(gl.get("vol"))), ("Coins tracked", f"{gl.get('coins'):,}" if gl.get("coins") else "n/a")]
    sym = X.sym.upper().replace("-USD", "")
    t_hit = next((c for c in tr if (c.get("symbol") or "").upper() == sym), None)
    kv.append(("On CoinGecko trending list", f"yes (#{tr.index(t_hit) + 1})" if t_hit else "no"))
    X.add(_kv(kv))
    X.data("Sentiment", ["Metric", "Value"], [[k, v] for k, v in kv])
    if now:
        X.kpi("Fear & Greed", f"{now[1]} {now[2]}", "bad" if now[1] >= 75 or now[1] <= 25 else None)
        if now[1] >= 75:
            X.flag("bad", f"Fear & Greed {now[1]} ({now[2]}): crowd is euphoric", "Sentiment")
        elif now[1] <= 25:
            X.flag("info", f"Fear & Greed {now[1]} ({now[2]}): crowd is fearful", "Sentiment")
    if hist:
        fig, ax = plt.subplots(figsize=(8.6, 2.2), dpi=150)
        ds = [dt.datetime.fromtimestamp(h[0]) for h in hist]
        vs = [h[1] for h in hist]
        ax.axhspan(0, 25, color="#b91c1c", alpha=0.08)
        ax.axhspan(75, 100, color="#15803d", alpha=0.08)
        ax.plot(ds, vs, color="#d9830c", lw=1.3)
        ax.set_ylim(0, 100)
        ax.set_title("Crypto Fear & Greed Index, last 90 days (0 = extreme fear, 100 = extreme greed)", fontsize=8.5, loc="left")
        _style_ax(ax)
        X.sp(4)
        X.fig(fig)
        X.data("Fear and Greed history", ["Date", "Value", "Label"], [[dt.datetime.fromtimestamp(h[0]).date().isoformat(), h[1], h[2]] for h in hist])
    if tr:
        X.sp(5)
        t = [["#", "Trending coin", "Symbol", "Rank", "Price", "24h"]] + [[str(i + 1), _s(c.get("name")), _s(c.get("symbol")), str(c.get("rank") or ""), _price(c.get("price")), pc(c.get("pct24"), 1)] for i, c in enumerate(tr)]
        X.add(_dtable(t, [10 * mm, 62 * mm, 26 * mm, 20 * mm, 28 * mm, 28 * mm], right=3, esc=False))
        X.data("Trending coins", ["#", "Name", "Symbol", "Rank", "Price", "24h %"], [[i + 1, c.get("name"), c.get("symbol"), c.get("rank"), c.get("price"), c.get("pct24")] for i, c in enumerate(tr)])


def network(X: Ctx) -> None:
    bn = X.D.get("btcnet") or {}
    if not bn:
        return
    X.title("BITCOIN NETWORK (MEMPOOL.SPACE)")
    mp, fees, blocks = bn.get("mempool") or {}, bn.get("fees") or {}, bn.get("blocks") or []
    kv = [("Unconfirmed transactions", f"{mp.get('count'):,}" if mp.get("count") else "n/a"), ("Mempool size", f"{mp['vsize'] / 1e6:.1f} MvB" if mp.get("vsize") else "n/a"),
          ("Fees waiting", f"{mp['total_fee'] / 1e8:.2f} BTC" if mp.get("total_fee") else "n/a"), ("Fee: next block / 30 min", f"{_n(fees.get('fast'), 1)} / {_n(fees.get('medium'), 1)} sat/vB"),
          ("Fee: 1 hour / economy", f"{_n(fees.get('slow'), 1)} / {_n(fees.get('economy'), 1)} sat/vB")]
    if blocks:
        kv.append(("Latest block", f"#{blocks[0]['height']:,} ({blocks[0]['tx']:,} tx)"))
        if len(blocks) > 1:
            gaps = [blocks[i]["t"] - blocks[i + 1]["t"] for i in range(len(blocks) - 1)]
            kv.append((f"Average block interval ({len(gaps)} blocks)", f"{sum(gaps) / len(gaps) / 60:.1f} min"))
    X.add(_kv(kv))
    X.data("Bitcoin network", ["Metric", "Value"], [[k, v] for k, v in kv])
    if blocks:
        X.sp(4)
        t = [["Height", "Transactions", "Size (MB)", "Time"]] + [[f"{b['height']:,}", f"{b['tx']:,}", f"{b['size'] / 1e6:.2f}", _d(b["t"], "%d %b %H:%M")] for b in blocks]
        X.add(_dtable(t, [30 * mm, 36 * mm, 30 * mm, 40 * mm]))


def options_crypto(X: Ctx) -> None:
    o = X.D.get("deribit")
    if not o:
        return
    X.title(f"OPTIONS MARKET: DERIBIT ({o['base']})")
    ex = o["expiries"]
    X.add(_kv([("Index price", _usd(o["spot"])), ("Total option open interest", _usd(o["total_oi_usd"])), ("Put / call open interest", f"{o['pc']:.2f}" if o.get("pc") else "n/a"),
               ("Listed expiries with open interest", str(len(ex)))]))
    X.sp(5)
    pick = sorted(ex, key=lambda e: -e["oi_usd"])[:8]
    pick.sort(key=lambda e: e["date"])
    t = [["Expiry", "Days", "Open interest", "Put / call OI", "Put / call volume", "ATM IV", "Max pain", "Max pain vs spot"]]
    for e in pick:
        t.append([_s(e["date"]), str(e["dte"]), _usd(e["oi_usd"]), f"{e['pc']:.2f}" if e.get("pc") else "-", f"{e['pc_vol']:.2f}" if e.get("pc_vol") else "-",
                  f"{e['atm_iv']:.0f}%" if e.get("atm_iv") else "-", _usd(e["max_pain"]), _p((e["max_pain"] / o["spot"] - 1) * 100, 1)])
    X.add(_dtable(t, [24 * mm, 12 * mm, 28 * mm, 24 * mm, 26 * mm, 16 * mm, 22 * mm, 24 * mm], esc=False))
    X.note("Deribit public API (the largest crypto options venue), BTC and ETH only. Open interest is in coin units converted to USD at the index price. Max pain is the strike where option holders collectively lose the most at expiry; price is not forced to go there. ATM IV is the implied volatility of the strike nearest the index.")
    X.data("Deribit options by expiry", ["Expiry", "Days", "Call OI", "Put OI", "OI USD", "Put/call OI", "Put/call vol", "ATM IV %", "Max pain"],
           [[e["date"], e["dte"], e["call_oi"], e["put_oi"], e["oi_usd"], e["pc"], e["pc_vol"], e["atm_iv"], e["max_pain"]] for e in ex])
    X.kpi("Options OI", _usd(o["total_oi_usd"]))
    near = next((e for e in ex if e["dte"] >= 7 and e.get("atm_iv")), None)
    if near:
        X.kpi("ATM IV (1 week+)", f"{near['atm_iv']:.0f}%")
    if o.get("pc") and o["pc"] > 0.9:
        X.flag("info", f"Put/call open interest {o['pc']:.2f}: heavy hedging or bearish positioning", "Sentiment")
    elif o.get("pc") and o["pc"] < 0.5:
        X.flag("info", f"Put/call open interest {o['pc']:.2f}: call-heavy, optimistic positioning", "Sentiment")
    X.source("Deribit options", "deribit.com public API, live at build time")


def defi(X: Ctx) -> None:
    d = X.D.get("llama")
    if not d:
        return
    X.title("DEFI AND STABLECOIN LIQUIDITY (DEFILLAMA)")
    st, ch = d.get("stable"), d.get("chain")
    kv = []
    if st:
        kv += [("Global stablecoin supply", _usd(st["total"])), ("Change over 30 days", _p(st["change_pct"], 2) if st.get("change_pct") is not None else "n/a"),
               ("Largest stablecoins", ", ".join(f"{a['symbol']} {_usd(a['supply'])}" for a in st["top"][:3]))]
    if d.get("total_tvl"):
        kv.append(("Total value locked, all chains", _usd(d["total_tvl"])))
    if ch:
        kv += [(f"{ch['name']} chain TVL", _usd(ch["tvl"])), ("Chain rank by TVL", f"#{ch['rank']} of {ch['n']}"), ("Share of all DeFi TVL", _pct_plain(ch["share"], 100))]
    elif d.get("top"):
        kv.append(("This coin as a chain", "not a tracked chain (no on-chain DeFi TVL of its own)"))
    X.add(_kv(kv))
    X.data("DeFi and stablecoins", ["Metric", "Value"], [[k, v] for k, v in kv])
    if d.get("top"):
        X.sp(5)
        t = [["#", "Chain", "Native token", "TVL"]] + [[str(i + 1), _s(c["name"]), _s(c.get("symbol") or "-"), _usd(c["tvl"])] for i, c in enumerate(d["top"])]
        X.add(_dtable(t, [10 * mm, 60 * mm, 40 * mm, 40 * mm], right=3, esc=False))
        X.data("Largest chains by TVL", ["Chain", "Token", "TVL USD"], [[c["name"], c.get("symbol"), c["tvl"]] for c in d["top"]])
    X.note("Stablecoin supply is the dry powder available to buy crypto: a growing supply is generally supportive, a shrinking one is a headwind. TVL is the value deposited in DeFi apps on a chain. Both are market-wide or chain-level, not specific to this token's price.")
    if st and st.get("change_pct") is not None:
        X.kpi("Stablecoin supply 30d", _p(st["change_pct"], 1))
        if st["change_pct"] < -1:
            X.flag("bad", f"Global stablecoin supply shrank {abs(st['change_pct']):.1f}% in 30 days (liquidity leaving crypto)", "Liquidity")
        elif st["change_pct"] > 2:
            X.flag("info", f"Global stablecoin supply grew {st['change_pct']:.1f}% in 30 days (fresh liquidity)", "Liquidity")
    X.source("DefiLlama", "api.llama.fi and stablecoins.llama.fi, live at build time")


def price_cone(X: Ctx) -> None:
    """Where price could plausibly be: a bootstrap of this coin's own daily returns over the last year, as percentile ranges and odds."""
    import numpy as np

    closes = [c[4] for c in X.c if c[4]]
    if len(closes) < 120:
        return
    yr = X.yr
    unit = "days" if X.is_coin else "trading days"
    c = np.array(closes[-(yr + 1):], dtype=float)
    r = np.diff(np.log(c))
    rng = np.random.default_rng(7)
    H, N = 180, 6000
    paths = np.exp(np.cumsum(rng.choice(r, size=(N, H), replace=True) - r.mean(), axis=1)) * c[-1]  # drift removed: a neutral baseline that does not extrapolate a past rally or crash
    X.title("PRICE SCENARIOS (STATISTICAL RANGE)")
    now = c[-1]
    rows, raw = [["Horizon", "10th pct", "25th pct", "Median", "75th pct", "90th pct", "Chance of a gain"]], []
    for h in (7, 30, 90, 180):
        q = np.percentile(paths[:, h - 1], [10, 25, 50, 75, 90])
        up = float((paths[:, h - 1] > now).mean())
        rows.append([f"{h} {unit}"] + [f"{_price(v)} ({_p((v / now - 1) * 100, 0)})" for v in q] + [_pct_plain(up, 100)])
        raw.append([h, *[float(v) for v in q], up])
    X.add(_dtable(rows, [20 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm, 22 * mm], esc=False, bold_first=True))
    p90 = paths[:, 89]
    odds = [("Up more than 20% in 90 days", float((p90 > now * 1.2).mean())), ("Up more than 50% in 90 days", float((p90 > now * 1.5).mean())),
            ("Down more than 20% in 90 days", float((p90 < now * 0.8).mean())), ("Down more than 40% in 90 days", float((p90 < now * 0.6).mean()))]
    dd = (paths[:, :90] / np.maximum.accumulate(paths[:, :90], axis=1) - 1).min(axis=1)
    X.sp(5)
    X.add(_kv([(k, _pct_plain(v, 100)) for k, v in odds] + [("Median worst drawdown in 90 days", _p(float(np.median(dd)) * 100, 0)), ("Annualised volatility (last year)", _pct_plain(float(r.std() * np.sqrt(yr)), 100))]))
    fig, ax = plt.subplots(figsize=(7.4, 2.8))
    days = np.arange(1, H + 1)
    qs = np.percentile(paths, [10, 25, 50, 75, 90], axis=0)
    ax.fill_between(days, qs[0], qs[4], color="#d9830c", alpha=0.14, label="10th-90th percentile")
    ax.fill_between(days, qs[1], qs[3], color="#d9830c", alpha=0.28, label="25th-75th percentile")
    ax.plot(days, qs[2], color="#b45309", lw=1.5, label="median")
    ax.axhline(now, color="#6b7285", lw=0.8, ls="--")
    ax.set_title(f"{X.sym}: range of simulated prices over the next 180 {unit}", fontsize=8.5, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    _style_ax(ax)
    X.sp(4)
    X.fig(fig)
    X.note("Bootstrap of this security's own daily returns over the last year (6,000 simulated paths, historical drift removed, so a past rally or crash is not extrapolated and the median starts near today's price). It shows how wide the range of outcomes is, not what will happen: it ignores news, regime changes and anything outside the last year.")
    X.data("Price scenarios", ["Horizon days", "P10", "P25", "Median", "P75", "P90", "Chance of gain"], raw)
    X.kpi("90d odds up >20%", _pct_plain(odds[0][1], 100))
    X.source("Price scenarios", "computed here from the last year of daily prices")
