import sys
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
import research_portfolio as rp
for sym in ("ES", "NQ"):
    df = rp._load(sym)[0]
    d = df.index.to_series().diff()
    same_day = df.index.to_series().dt.date == df.index.to_series().shift(1).dt.date
    tod = df.index.hour * 60 + df.index.minute
    in_sess = (tod >= 9 * 60 + 35) & (tod <= 16 * 60)
    gaps = d[same_day & in_sess & (d > pd.Timedelta(minutes=5))]
    print(f"{sym}: intraday gaps inside 9:35-16:00: {len(gaps)}  (>15min: {(gaps > pd.Timedelta(minutes=15)).sum()}, >60min: {(gaps > pd.Timedelta(minutes=60)).sum()})")
    big = gaps[gaps > pd.Timedelta(minutes=15)]
    print("   by year:", big.groupby(big.index.year).size().to_dict())
    short = df.groupby(df.index.date).size(); short = short[short < 60]
    print("   short days by year:", pd.Series(short.index).map(lambda x: x.year).value_counts().sort_index().to_dict())
    # missing 9:30 open bar (breaks opening range)
    days = df.groupby(df.index.date)
    no_open = sum(1 for _, g in days if not ((g.index.hour == 9) & (g.index.minute == 30)).any())
    print(f"   days without the 9:30 bar: {no_open} of {len(days)}")
