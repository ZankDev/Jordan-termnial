# NT LAB — NinjaTrader 8 as the backtest provider

```
 search loop (app/ntlab/search.py)          NinjaTrader 8
 ───────────────────────────────            ─────────────────────────────────────────────
 generate 150 candidates  ──jobs\<batch>.json──▶  Strategy Analyzer · NostroLab · Optimize JobIndex 0..N-1
                                                   (NinjaTrader data, fill engine, commission, slippage, all cores)
 score IS / OOS / folds / holdout / Apex ◀──results\<batch>\<i>.json──  one file per backtest, every trade
 keep robust, test neighbours, breed ──▶ next generation …
                       NostroBridge add-on (127.0.0.1:8099): status, connections, historical bars
 MCP server (app/ntlab/mcp_server.py, registered in .mcp.json) drives all of it as tools
```

## One-time setup

1. Install **NinjaTrader 8** (ninjatrader.com, free license) and log in.
2. Connect a data feed that has futures history: NinjaTrader's own (sim/demo account), Tradovate, Rithmic, or Kinetick.
3. Install the NinjaScript — any one of these:
   - in the terminal: NOSTRO › NT LAB › **INSTALL NINJASCRIPT**, or
   - the MCP tool `nt_install`, or
   - copy `Strategies\NostroLab.cs` → `Documents\NinjaTrader 8\bin\Custom\Strategies\` and
     `AddOns\NostroBridge.cs` → `Documents\NinjaTrader 8\bin\Custom\AddOns\`.
4. In NinjaTrader: **New › NinjaScript Editor**, press **F5** (compile). Restart NinjaTrader once — the bridge starts with
   it (Output window: `NostroBridge listening on 127.0.0.1:8099`).

## Each generation (when the search uses `provider = ninjatrader`)

The search log / NT LAB page shows the exact settings. In **New › Strategy Analyzer**:

| setting | value |
|---|---|
| Strategy | NostroLab |
| Instrument | `ES ##-##` (continuous) — or `NQ ##-##` |
| Type / Value | Minute / 5 |
| Trading hours | CME US Index Futures ETH |
| Start / End | as shown in the log |
| Backtest type | **Optimization**, optimizer **Default** |
| BatchId | the batch id from the log |
| JobIndex | tick *optimize*: Min 0, Max N-1, Increment 1 |
| Commission / Slippage | your commission template / 1 tick |

Press **Run**. Every backtest writes its trades to `Documents\NinjaTrader 8\nostro\results\<batch>\`; the loop picks them
up, scores them and writes the next batch.

## Direct mode

Leave **BatchId empty** and NostroLab runs from its own parameters — use NinjaTrader's genetic optimizer or
**Walk-Forward Optimization** natively. NT LAB shows the exact parameters of every candidate (candidate detail ›
"RUN IT IN NINJATRADER").

## Checking the C# without NinjaTrader

`check.ps1` compiles both files against `stubs\NtStubs.cs` with the .NET Framework compiler (syntax and API-shape check).
