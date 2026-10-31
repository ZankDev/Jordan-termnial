# NT LAB — strategy spec (shared by NinjaTrader `NostroLab` and the Python search loop)

A candidate strategy is one JSON object. A **batch** is a JSON file
`<NT>/nostro/jobs/<batch>.json` = `{"batch": "...", "specs": [spec, spec, ...]}`.
In NinjaTrader, Strategy Analyzer runs `NostroLab` with `BatchId = <batch>` and **optimizes `JobIndex` from 0 to N-1**, so
one Optimize click backtests the whole batch on NinjaTrader's data, fill engine, commission and slippage (all cores).
Each run writes `<NT>/nostro/results/<batch>/<JobIndex>.json` with every trade.

```json
{
  "id": "c_7f3a91",
  "family": "orb | ema_cross | donchian | rsi_revert | bb_revert | vwap_revert",
  "dir": "both | long | short",
  "p": { "...family parameters..." },
  "session": { "open": "09:30", "entry_from": "09:35", "entry_to": "11:30", "flat": "15:55" },
  "exit": { "stop_atr": 1.5, "target_atr": 3.0, "trail_atr": 0, "max_bars": 0 },
  "atr_len": 14,
  "max_trades_day": 2,
  "qty": 1
}
```

Times are New York time. `NostroLab` converts bar times from NinjaTrader's time zone (PC time zone by default; override
with the `NtTimeZoneId` property).

| family | params `p` | entry |
|---|---|---|
| `orb` | `range_min` (5–60), `buffer_ticks` | close breaks the opening range (first `range_min` minutes after `open`) |
| `ema_cross` | `fast`, `slow`, `trend_len` (0 = off) | fast EMA crosses slow EMA; trend filter = close vs SMA(trend_len) |
| `donchian` | `len`, `trend_len` | close breaks the highest high / lowest low of the previous `len` bars |
| `rsi_revert` | `len`, `lo`, `hi`, `trend_len` | RSI crosses back above `lo` (long) / below `hi` (short) |
| `bb_revert` | `len`, `k` | close back inside the Bollinger band after closing outside |
| `vwap_revert` | `dev_atr` | price stretched `dev_atr` ATRs from session VWAP turns back toward it |

Exits: stop and target in ATR multiples (converted to ticks at entry), optional trailing stop (`trail_atr` > 0 replaces
the fixed stop), optional time stop (`max_bars`), always flat at `flat`, at most `max_trades_day` entries per day.

Result file (`results/<batch>/<i>.json`):
```json
{"batch":"...","index":3,"spec_id":"c_7f3a91","instrument":"ES 12-26","bars":51234,"engine":"ninjatrader",
 "trades":[{"et":"2026-03-02T09:41:00","xt":"2026-03-02T10:12:00","side":"long","ep":5012.25,"xp":5019.5,"qty":1,
            "pnl":357.5,"comm":4.3,"mae":-112.5,"mfe":400.0}]}
```
