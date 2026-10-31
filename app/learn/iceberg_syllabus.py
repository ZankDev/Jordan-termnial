"""Core facts per ICEBERG topic. Quiz questions are built ONLY from these facts so the free LLM cannot invent rules.
Plain textbook material (Hull, Natenberg, Sinclair, Harris, SpotGamma/SqueezeMetrics conventions)."""

SYLLABUS: dict[str, str] = {
    "0DTE": """0DTE = zero days to expiration: options that expire the same trading day (SPX/XSP/QQQ/SPY now list daily expiries; NQ/ES have daily weeklies too).
They have almost no time value left, so their price is mostly intrinsic value plus a small premium.
ATM 0DTE options have very high gamma and very high theta (fast decay); vega is tiny because there is almost no time left.
Small moves in the underlying change a 0DTE option's delta a lot; an OTM 0DTE option can go from near-worthless to deep ITM in minutes.
Buyers need a fast, large move; sellers collect theta but carry large gamma (tail) risk.
0DTE volume is now a large share (often 40%+) of SPX option volume; most of it is opened and closed the same day, so it barely shows up in open interest.
Dealer hedging of 0DTE positions can amplify (short gamma) or dampen (long gamma) intraday moves, and the effect fades as expiry approaches and contracts settle at 16:00 ET (SPX PM-settled).""",
    "Open interest": """Open interest (OI) = number of option contracts outstanding (opened and not yet closed or exercised).
OI is updated once per day by OCC (overnight), not in real time; intraday you only see volume.
A trade raises OI only if both sides open new positions; it lowers OI if both sides close; it leaves OI unchanged if one opens and one closes.
OI does not tell you who is long or short, or whether a trade was a buy or a sell.
High OI at a strike marks where many positions sit; it is the raw input to gamma-exposure and option-wall estimates.
OI drops sharply after an expiry as those contracts disappear.""",
    "Volume": """Option volume = number of contracts traded during the session; every trade counts, opening or closing.
Volume is real time; open interest is only end-of-day.
Volume greater than existing OI at a strike suggests new positions are being opened (confirmed the next day by the OI change).
Volume alone does not reveal direction: a trade can be a buy or a sell, opening or closing.
Unusual volume (many times normal for that contract) is what 'unusual options activity' scanners flag.
In 0DTE, huge volume can coexist with small OI because positions open and close the same day.""",
    "Gamma exposure (GEX)": """Gamma = rate of change of delta per 1-point move in the underlying; highest for ATM options and near expiry.
GEX estimates the total gamma dealers (market makers) hold, usually in dollars per 1% move, summed across strikes using OI x gamma x contract multiplier x spot.
Standard convention (SqueezeMetrics/SpotGamma): assume dealers are long calls and short puts that customers trade, so call gamma counts positive and put gamma negative. It is an assumption, not observed positioning.
Positive (long) dealer gamma: dealers sell rallies and buy dips to stay hedged -> dampens volatility, mean-reverting, range-bound tape.
Negative (short) dealer gamma: dealers buy rallies and sell dips -> amplifies moves, trending and volatile tape.
GEX changes with price, time and new OI, so levels are re-estimated daily.""",
    "Round strikes": """Option open interest clusters on round strikes (e.g. SPX 5000, 5050, 5100; QQQ 450, 455) because traders and funds prefer round numbers.
Large OI on a round strike creates large gamma there, so price often reacts (stalls, pins, or accelerates through) near those levels.
Round strikes are also psychological levels where limit orders and stops cluster.
Bigger round numbers (100s) usually hold more OI than minor ones (5s, 10s).
Into expiration, price can 'pin' near a high-OI round strike when dealers are long gamma there.""",
    "Put/call skew": """Skew = implied volatility differs by strike. In equity indexes, OTM puts trade at higher IV than equidistant OTM calls ('put skew', 'smirk').
Reason: demand for downside protection, and markets historically fall faster than they rise (volatility rises when prices fall).
Skew is often measured as the IV of a 25-delta put minus the IV of a 25-delta call (risk reversal) or with the CBOE SKEW index.
Steepening put skew = protection getting more expensive (fear of a drop); flattening = complacency or call demand.
Some assets (e.g. meme stocks, sometimes commodities in a squeeze) show call skew, where upside calls are bid.
The put/call ratio (volume or OI of puts vs calls) is a different measure from skew.""",
    "IV / implied volatility": """Implied volatility = the volatility input that makes an option-pricing model (Black-Scholes) match the option's market price.
It is the market's forecast of future volatility, quoted annualized; it is not a forecast of direction.
Rule of thumb: expected 1-day move ~= IV / sqrt(252) (about IV/16) as a percent of price.
Higher IV = more expensive options (higher premium for both calls and puts).
VIX = 30-day implied volatility of SPX options; VXN for Nasdaq-100.
IV usually rises when equities fall and drops when they rally (spot-vol correlation is negative for indexes).
Realized (historical) volatility is what actually happened; IV minus realized is the volatility risk premium, usually positive.""",
    "Max pain": """Max pain = the strike at which the total value of all outstanding options (calls + puts) would be lowest at expiration, i.e. where option buyers collectively lose the most.
It is computed from open interest at each strike.
The theory claims price drifts toward max pain into expiry; evidence is weak and it is at best a mild tendency, mostly for single stocks with big OI on expiration day.
Max pain changes as OI changes, so it is recalculated daily.
It is not a dealer-hedging model like GEX; it only looks at option payoff values.""",
    "Options walls": """An options wall = a strike with very large open interest (call wall above price, put wall below).
Call wall: big call OI/gamma above spot often acts as resistance, because dealers long those calls sell the underlying as price rises toward it (in positive gamma).
Put wall: big put OI below spot often acts as support in a similar way, but if broken, dealer hedging of short puts can accelerate the selling.
Walls move as OI rolls and expiries pass; they are levels to watch, not guaranteed reversals.
Wall strength depends on gamma, which is largest near expiry and near the money.""",
    "SPY > SPX": """SPX = the S&P 500 index option (cash-settled, European exercise, 100x the index, Section 1256 tax treatment, AM and PM settlement series).
SPY = the ETF option (physically settled in shares, American exercise, 100 shares, about 1/10 of SPX in price).
Retail traders favour SPY (smaller size); institutions favour SPX (larger notional, cash settlement, no early-assignment risk).
'SPY > SPX' in flow analysis means watching SPY flow and translating levels: SPX ~= SPY x 10 (approximately, minus the dividend difference).
SPY options can be exercised early (dividend-related early exercise of ITM calls); SPX cannot.
Total gamma/positioning is usually estimated across SPX, SPY and ES options together.""",
    "Term structure": """Volatility term structure = implied volatility plotted by expiration date.
Contango (normal): longer expiries have higher IV than short ones; typical in calm markets.
Backwardation (inverted): short-term IV above longer-term IV; typical during stress and sell-offs (e.g. VIX above VIX3M).
Event expiries (FOMC, CPI, earnings) show a bump in IV for the expiry that contains the event.
VIX futures curve is the tradable version: contango causes roll-down losses for long VIX products like VXX.
A flip from contango to backwardation is a classic stress signal.""",
    "Delta hedging": """Delta = how much an option's price changes per 1-point move in the underlying (calls 0 to 1, puts -1 to 0); also a rough probability of finishing ITM.
Delta hedging = trading the underlying to make the total position delta neutral (no first-order exposure to direction).
A market maker who sells a call (short delta) buys the underlying to hedge; one who sells a put (long delta) sells the underlying.
Because delta changes with price (gamma), time (charm) and volatility (vanna), hedges must be adjusted continuously.
Short-gamma hedgers must buy after rallies and sell after drops (pro-cyclical); long-gamma hedgers do the opposite.
Aggregate dealer hedging flows are why options positioning can move the underlying index.""",
    "Dealer positioning": """Dealers = market makers and banks who take the other side of customer option trades and hedge the risk.
Their positioning is inferred, not observed: standard models assume customers buy puts (dealers short puts) and that dealers are long calls sold by overwriters; refined models use trade-side data.
Dealer net gamma tells whether their hedging dampens (long gamma) or amplifies (short gamma) moves.
Dealer vanna and charm exposure tell how their hedges change with volatility and with time.
Positioning resets around big expirations (monthly OPEX, quarterly), when large OI disappears.
Customer behaviour (overwriting calls, buying protective puts, selling puts for income) shapes what dealers hold.""",
    "Vanna / charm": """Vanna = change in delta for a change in implied volatility (equivalently change in vega for a move in spot).
For OTM options, falling IV lowers the absolute delta (toward 0); rising IV raises it. ITM options behave the other way (absolute delta moves away from 1 as IV rises).
Charm = change in delta as time passes (delta decay): OTM options' delta drifts toward 0 and ITM options' delta drifts toward 1 (or -1) as expiry nears.
Vanna/charm flows: when dealers are short puts that customers bought, falling IV and passing time shrink the put deltas, so dealers buy back short hedges -> supportive 'vanna rally' / OPEX-week drift.
Charm effects get strongest in the last days before expiration.
They are second-order Greeks, but with huge index OI the hedging flows they create are large.""",
    "Volatility surface": """The volatility surface = implied volatility as a function of both strike (or delta/moneyness) and expiration.
Slicing by strike at one expiry gives the smile/skew; slicing by expiry at one moneyness gives the term structure.
Black-Scholes assumes one constant volatility; the surface exists because real markets have fat tails, skew and changing volatility.
Traders mark the surface to price options consistently and to spot relatively cheap or expensive options.
Surface dynamics: in indexes, a sell-off usually lifts the whole surface and steepens short-dated skew.
Models like local volatility and stochastic volatility (Heston, SABR) are built to fit the surface.""",
    "Skew": """Skew = the slope of implied volatility across strikes for one expiry.
Equity index skew is negative: lower strikes (puts) have higher IV than higher strikes (calls).
It reflects crash protection demand and the negative correlation between price and volatility.
Measured by 25-delta risk reversal, the 90%-110% moneyness IV spread, or the CBOE SKEW index.
Short-dated skew is usually steeper than long-dated skew.
Skew trades: put spreads, risk reversals and ratio spreads are ways to buy or sell skew.""",
    "IV rank": """IV rank = where current IV sits within its past-year range: (IV - 52w low) / (52w high - 52w low) x 100.
IV percentile = the percent of days in the past year when IV was below today's level (a different, related measure).
High IV rank (e.g. above 50) = options are expensive relative to their own history; premium-selling strategies are favoured by those who use it.
Low IV rank = options are cheap relative to history; buying premium is relatively cheaper.
One extreme spike in the past year can keep IV rank low for months, which is why many prefer IV percentile.
IV rank compares a stock to itself, not to other stocks.""",
    "Volga": """Volga (vomma) = change in vega for a change in implied volatility; the convexity of an option's value to volatility.
Volga is largest for OTM (wing) options and near zero for ATM options.
Long OTM options are long volga: their vega grows when volatility rises, so they gain more and more in a vol spike.
Wing options are priced with extra IV partly because of volga (and vanna) - the 'smile' compensates for this convexity.
Selling wings (e.g. short strangles, iron condors) is short volga: losses accelerate if volatility explodes.
Volga matters most for volatility traders and tail-hedging books.""",
    "Dark pools": """Dark pools = private trading venues (ATSs) where orders are not displayed before execution; trades are reported to the tape after the fact (via FINRA TRF).
They let institutions trade large blocks without revealing intent and with less market impact.
Roughly 10-15% of US equity volume executes in dark pools; including internalisation, about 40%+ of volume is off-exchange.
Dark prints show up on the tape with a delay flag; large dark prints at a price can mark institutional interest levels.
They trade stocks/ETFs, not futures; for index traders, SPY/QQQ dark-pool levels are the common read-through.
Dark-pool data alone does not show whether a print was a buy or a sell.""",
    "Block trades": """A block trade = a single very large trade (equities: commonly 10,000+ shares or $200k+; options: large contract counts) usually negotiated privately.
In futures (CME), block trades are privately negotiated above a minimum size and reported within minutes, outside the central limit order book.
In options flow, blocks often signal institutional hedges, rolls or large directional bets.
A block can be one side of a spread or a hedge, so its direction is not obvious on its own.
Blocks are usually printed at or between the bid/ask; trading at the ask suggests buyer-initiated.
Flow services highlight blocks separately from sweeps.""",
    "Sweeps": """A sweep = an order that is split and executed across multiple exchanges at once to fill quickly, taking all available liquidity at the best prices.
It shows urgency: the buyer or seller cares more about speed than price.
Sweeps at the ask (calls bought, puts bought) are read as aggressive bullish/bearish bets; at the bid as aggressive selling.
Options flow scanners flag sweeps, especially short-dated OTM sweeps, as 'smart money' signals, but many are hedges or closing trades.
Intermarket sweep orders (ISOs) are the equity-market mechanism that lets the order hit several venues simultaneously under Reg NMS.
Repeated sweeps in the same direction carry more information than a single print.""",
    "Multi-leg masking": """Multi-leg masking = hiding the true intent of a trade by executing it as several legs (spreads, combos) or across strikes/expiries/venues.
A single leg seen in flow (e.g. a big call buy) may actually be one side of a spread, a collar or a risk reversal, so reading it alone misleads.
Complex orders trade on complex order books (COB) and print each leg with a spread flag.
Institutions also split large orders over time and venues to avoid detection.
Good flow analysis checks for matching legs at the same timestamp before concluding direction.""",
    "Synthetic positions": """Put-call parity: Call - Put = Spot - PV(Strike) (minus dividends), for European options with the same strike and expiry.
Synthetic long stock = long call + short put at the same strike and expiry.
Synthetic short stock = short call + long put.
Synthetic long call = long stock + long put (a protective put); synthetic long put = short stock + long call.
Synthetics let traders replicate exposure with different capital, borrow or tax treatment; arbitrage keeps parity prices aligned.
A conversion (long stock + synthetic short) and a reversal (short stock + synthetic long) are parity arbitrage trades.""",
    "Cross-asset flow": """Cross-asset flow = how moves and positioning in one market spill into others (rates, dollar, oil, credit, crypto, equities).
Typical links: rising real yields pressure growth/tech (Nasdaq); a strong dollar tightens global financial conditions; credit spreads widening leads equity stress.
Risk-parity and volatility-targeting funds rebalance across stocks and bonds when volatility or correlations change.
Stock-bond correlation can flip: negative in disinflationary regimes (bonds hedge stocks), positive when inflation is the main risk.
Index traders watch ZN/ZB, DXY, CL, VIX and credit (HYG) as context for ES/NQ moves.""",
    "Gamma scalping": """Gamma scalping = holding a long-gamma option position (e.g. long straddle) delta-hedged, and re-hedging as price moves: selling the underlying after rallies and buying after dips.
Each re-hedge locks in small profits from movement; the cost is the theta paid for the options.
It profits when realized volatility exceeds the implied volatility paid; it loses when the market is quieter than implied.
Dealers who are long gamma effectively gamma scalp, which is why long dealer gamma dampens moves.
The opposite (short gamma, hedging) loses on movement and earns theta.""",
    "Inventory risk": """Inventory risk = the risk a market maker carries from positions accumulated while providing liquidity.
Market makers widen spreads or skew their quotes (lower bids and offers when long, raise them when short) to shed unwanted inventory.
In options, inventory risk is the residual Greeks (gamma, vega, skew) left after delta hedging.
When dealers hold large unwanted inventory, they may pay up to hedge, adding pressure in the direction of the hedge.
Inventory models (e.g. Avellaneda-Stoikov) balance spread capture against inventory risk.""",
    "Flow toxicity": """Toxic flow = order flow from better-informed traders that makes liquidity providers lose money (adverse selection).
Market makers measure markouts: how price moves after they trade with a counterparty; consistently adverse markouts mean toxic flow.
VPIN (volume-synchronized probability of informed trading) is a well-known toxicity measure based on buy/sell volume imbalance.
When flow turns toxic, market makers widen spreads and reduce size, so liquidity thins - often just before big moves.
Retail flow is generally considered less toxic, which is why wholesalers pay for it (payment for order flow).""",
    "Order book imbalance": """Order book imbalance = the difference between resting bid size and ask size near the best prices, often (bid - ask)/(bid + ask).
A heavier bid than ask predicts a small short-term up-tick probability, and vice versa; the signal decays within seconds to minutes.
Displayed size can be pulled or spoofed, so imbalance is noisy; it works best in liquid instruments with real size.
Queue depletion (one side being eaten) is a stronger signal than static imbalance.
DOM/depth ladders and heatmaps visualise it for futures traders.""",
    "Price impact": """Price impact = how much a trade moves the price against the trader.
Temporary impact fades after the trade; permanent impact reflects information and remains.
The square-root law: impact roughly scales with sqrt(order size / daily volume) x volatility.
Large orders are split over time (VWAP, TWAP, POV algos) to reduce impact.
Kyle's lambda measures price change per unit of order flow; thinner markets have larger lambda.
Impact is higher when liquidity is thin (overnight, around news, in small caps).""",
    "Execution algos": """Execution algorithms split large parent orders into small child orders to reduce impact and information leakage.
VWAP: trades in proportion to the typical volume curve to match the day's volume-weighted average price.
TWAP: trades evenly over time. POV/participation: trades a fixed percentage of market volume.
Implementation shortfall (arrival price) algos trade faster early to limit drift risk versus the decision price.
Algo footprints: repetitive same-size prints at regular intervals, and volume clustered near open and close.
Benchmarks (VWAP, arrival, close) define how execution quality is measured.""",
    "Latency": """Latency = the delay between an event and a trader's ability to act on it (data arrival, decision, order reaching the exchange).
HFT firms co-locate servers in exchange data centers and use microwave links to cut latency to microseconds.
Latency arbitrage = trading on a price change seen on one venue before slower participants update quotes elsewhere.
For a retail futures trader, latency is milliseconds to seconds and cannot compete with HFT on speed; edges must come from longer horizons.
Stale quotes during fast markets are a main cost of high latency.""",
    "Smart money footprints": """'Smart money footprints' = traces of informed or institutional activity: large blocks, repeated sweeps, unusual options volume vs OI, dark-pool prints, absorption on the tape.
Absorption: heavy aggressive selling hits a level but price does not fall - large passive buyers are absorbing.
Opening OI in far OTM short-dated options before news can signal informed bets (and is investigated by regulators when it precedes announcements).
Institutional orders are split, so footprints show as persistence (the same side again and again), not one print.
None of these is certain: hedges and closing trades look similar to directional bets.""",
    "Market-on-close flow": """Market-on-close (MOC) orders execute at the official closing price in the closing auction (NYSE/Nasdaq, 16:00 ET).
Index funds, ETFs and benchmarked funds trade at the close to match NAV/benchmark prices; the close is now the single largest liquidity event of the day.
Exchanges publish MOC imbalance information (around 15:50 ET); large buy or sell imbalances can push prices in the last minutes.
Index rebalances and quarter-end rebalancing make MOC flows especially large.
Futures traders watch the imbalance announcement for late-day direction.""",
    "IV expansion": """IV expansion = implied volatility rising.
Causes: falling markets, upcoming events (earnings, FOMC, CPI), uncertainty, or heavy option buying.
Long options (long vega) gain from IV expansion even if the underlying does not move; short options lose.
IV usually expands into scheduled events and spikes in sell-offs (VIX jumps).
Buying options before IV expands and selling after is a volatility trade, distinct from a directional trade.""",
    "IV crush": """IV crush = a sharp drop in implied volatility, usually right after a scheduled event (earnings, FDA, FOMC) once the uncertainty is resolved.
Options lose value from the vega drop even if the price moves; a buyer can be right on direction and still lose.
The market prices an expected move before the event (roughly the ATM straddle price); if the actual move is smaller, option buyers lose.
Short premium strategies (straddles, iron condors) are used to sell the expected IV crush, risking a larger-than-implied move.""",
    "Calendar spreads": """Calendar (time) spread = sell a near-term option and buy a longer-term option at the same strike.
It profits from the faster time decay of the short-dated option and is long vega (gains if longer-dated IV rises).
Maximum profit when price is near the strike at the near-term expiry.
Risk: a big move away from the strike, or a fall in back-month IV.
Used to trade term structure, e.g. selling an event-inflated front expiry against a cheaper back month.""",
    "Diagonal spreads": """Diagonal spread = buy a longer-dated option and sell a shorter-dated option at different strikes (a calendar plus a vertical).
Example: long a deep ITM 60-day call, short an OTM 7-day call (a 'poor man's covered call').
It combines directional bias (from the strike difference) with time-decay income (from the short near-term leg).
Long vega overall in most setups; risk is a sharp move against the bias or a collapse in back-month IV.
The short leg can be rolled repeatedly to collect premium.""",
    "Butterfly / condor": """Long butterfly = buy 1 lower strike, sell 2 middle strikes, buy 1 upper strike (equal spacing), same expiry.
It has limited risk and limited profit; maximum profit when price expires exactly at the middle strike.
Condor = same idea with the two short strikes different (a wider profit zone, lower maximum profit).
Long butterflies/condors are short volatility (profit from price staying in a range) and cheap ways to target a price.
Broken-wing butterfly = unequal wing widths to reduce cost or remove risk on one side.""",
    "Ratio spreads": """Ratio spread = buy some options and sell a larger number of further OTM options (e.g. buy 1 call, sell 2 higher calls).
It can be entered for small debit, zero cost or a credit.
Profit is greatest if price ends near the short strike; above that, the extra short calls create unlimited risk (call ratio) or large risk (put ratio).
Ratio spreads trade skew: selling the richer-IV options against cheaper ones.
Backspread = the reverse (sell fewer, buy more), which profits from a big move.""",
    "Iron condors": """Iron condor = short OTM put spread + short OTM call spread in the same expiry; collects a net credit.
Maximum profit = the credit, if price stays between the short strikes at expiry.
Maximum loss = width of a spread minus the credit; risk is defined by the long wings.
It is short gamma and short vega: profits from time decay and falling IV, loses on big moves.
Popular when IV is high and a range is expected; break-evens = short strikes +/- credit.""",
    "Risk reversal": """Risk reversal = buy an OTM call and sell an OTM put (bullish), or buy an OTM put and sell an OTM call (bearish), same expiry.
It creates synthetic directional exposure, often for zero or low cost.
The price of a 25-delta risk reversal is a standard measure of skew (put IV minus call IV).
A collar (long stock + long put + short call) uses the bearish risk reversal to hedge a stock position.
Risk: selling the put exposes the trader to large losses if price collapses.""",
    "Straddle / strangle": """Long straddle = buy a call and a put at the same strike (usually ATM) and expiry; long strangle = buy an OTM call and an OTM put.
Both profit from a large move in either direction or a rise in IV; they lose to time decay if price stays still.
Strangles are cheaper but need a bigger move to profit.
The ATM straddle price is the market's expected move to expiry (about 0.8 x one standard deviation).
Short straddles/strangles collect premium and are short gamma and vega, with large tail risk.""",
    "ETF creation/redemption": """Authorized participants (APs) create new ETF shares by delivering the underlying basket to the issuer, and redeem ETF shares for the basket.
This arbitrage keeps the ETF price close to its net asset value (NAV): if the ETF trades at a premium, APs create and sell; at a discount, they buy and redeem.
Large creations/redemptions show up as changes in shares outstanding and signal big investor inflows/outflows.
For SPY/QQQ, creation/redemption activity can lead to buying/selling of the underlying stocks.
In-kind redemptions help ETFs avoid capital-gains distributions (a tax advantage).""",
    "Options flow vs. volume": """Volume counts every contract traded; flow analysis tries to classify trades by side (bought at ask vs sold at bid), size, urgency (sweep vs block) and opening/closing.
High volume alone is not bullish or bearish; the side and whether positions are opening matters.
Premium (dollar value) is often a better measure of conviction than contract count.
Next-day OI change confirms whether the flow opened new positions.
Hedging, spreads and closing trades make raw flow noisy, so it is a clue, not a signal by itself.""",
    "Reset flows": """Reset flows = hedging and rebalancing flows that occur when positions are reset on a schedule: option expirations, index/ETF rebalances, structured-product resets, leveraged ETF daily rebalancing, and futures rolls.
Leveraged and inverse ETFs must rebalance near the close every day: buy after up days and sell after down days, adding to the move.
Quarterly futures roll (the week before expiry) shifts open interest from the front to the next contract.
Large monthly OPEX resets dealer positioning, often changing market behaviour after expiration.""",
    "Large lot trades": """Large lot trades = unusually large single prints in contracts or shares, far above the average trade size.
They often come from institutions: hedges, rolls, overwriting programs or directional bets.
Comparing trade size to average trade size and to open interest helps judge significance.
They are often negotiated (blocks) or crossed, not necessarily aggressive.
A single large lot is less informative than consistent large lots on the same side.""",
    "Dealer gamma": """Dealer gamma = the aggregate gamma held by market makers after taking the other side of customer option trades.
Long dealer gamma -> hedging against the move (sell up, buy down) -> lower realized volatility, pinning near big strikes.
Short dealer gamma -> hedging with the move (buy up, sell down) -> higher realized volatility, larger trends and gaps.
Dealer gamma is largest near the money and near expiry, and it shifts as price moves across strikes.
It is an estimate built from OI and assumptions about who is long and short.""",
    "Dealer delta": """Dealer delta = the net directional exposure of market makers from their options book before hedging; they hedge it with futures/stock to stay neutral.
Dealers' hedge (opposite of their option delta) is effectively a large position in the underlying held by the market.
When option deltas change (from price, time or volatility), dealers must trade the underlying to stay neutral - that hedging is the flow that moves price.
Expiration removes option deltas, so dealers unwind hedges, which can cause moves after OPEX.
Delta-adjusted OI estimates the share/futures equivalent of options positioning.""",
    "OI changes": """OI change = today's open interest minus yesterday's for a contract.
Rising OI with rising price = new longs opening (trend supported, classic futures reading); rising OI with falling price = new shorts.
Falling OI = positions being closed (short covering in a rally, long liquidation in a decline).
In options, a strike's OI jump after big volume confirms new positioning there; it updates the GEX/wall map.
OI is published once a day, so changes are known only the next morning.""",
    "Volume/OI divergence": """Volume/OI divergence = volume behaving differently from open interest.
Volume much larger than OI at a strike = mostly new activity (opening) or heavy day trading; check tomorrow's OI to know which.
High volume with unchanged OI = positions changing hands (one side opening, one side closing) or same-day round trips.
In futures, price rising on rising volume and rising OI = strong trend; price rising on falling OI = short covering (weaker).
The volume/OI ratio is a common unusual-activity filter.""",
    "Gamma flip": """Gamma flip (zero gamma level) = the price where estimated dealer gamma changes from positive to negative.
Above the flip: dealers long gamma -> they dampen moves -> calmer, mean-reverting market.
Below the flip: dealers short gamma -> they amplify moves -> faster, trending, more volatile market.
The flip level moves daily with price, OI and time to expiry.
Losing the gamma flip is watched as a warning of a volatility regime change.""",
    "Expiration flows": """Expiration flows = trading caused by options expiring: closing/rolling positions, exercise/assignment, and dealers unwinding hedges.
Monthly OPEX = third Friday; quarterly 'triple witching' (March/June/September/December) also expires futures and is very high-volume.
Before expiry, large gamma near the money can pin price; after expiry, the removal of that gamma often frees the market to move ('OPEX week vol unclenching').
SPX AM-settled monthlies settle on the Friday open (SOQ); PM-settled and 0DTE series settle at the 16:00 close.
Vanna/charm-driven buying often supports prices into OPEX and fades after.""",
    "Pin risk": """Pin risk = the risk for an option seller when the underlying closes very near the strike at expiration: it is unclear whether the option will be exercised.
The seller does not know whether they will be assigned and end up with an unwanted position over the weekend.
Pinning = price gravitating to and closing near a high-OI strike at expiry, often attributed to dealer long-gamma hedging.
Cash-settled index options (SPX) have no physical assignment, but settlement-price uncertainty still matters.
Traders close or roll near-the-money short options before expiry to avoid pin risk.""",
    "Hedging flows": """Hedging flows = trades done to offset risk, not to express a view: dealers delta-hedging options, funds buying puts, corporates hedging FX/commodities.
Dealer hedging is mechanical, so its direction can be predicted from positioning (gamma, vanna, charm).
Portfolio insurance-style hedging (selling into declines) can amplify crashes, e.g. 1987.
Hedging flows often concentrate at the open, the close and around expirations.
Mistaking hedging prints for directional bets is a common flow-reading error.""",
    "0DTE gamma": """0DTE gamma = gamma from options expiring today. Because time to expiry is tiny, ATM 0DTE gamma is far larger per contract than longer-dated gamma.
It changes very fast: as price moves, the strikes with big gamma shift, and the effect collapses toward zero at the close.
If dealers are short 0DTE gamma, their hedging can create sharp intraday moves; if long, intraday pinning to big strikes.
Standard daily GEX built on OI misses most 0DTE positions because they open and close intraday; volume-based estimates are used.
The impact on the index is debated; it is largest near big 0DTE strikes in the last hours of the session.""",
    "Vol control": """Volatility-control (vol-target) funds adjust their equity exposure so that portfolio volatility stays near a target (e.g. 10%).
When realized volatility rises, they sell equities; when volatility falls, they buy, with a lag (they use trailing 1-3 month windows).
This makes them pro-cyclical: selling into turbulence, slowly re-leveraging during calm rallies.
CTAs (trend followers) and risk-parity funds have similar volatility-scaling behaviour.
A volatility spike can trigger systematic selling estimated in tens of billions over following days.""",
    "Momentum ignition": """Momentum ignition = a manipulative strategy: a trader sends aggressive orders to trigger a fast price move, hoping to set off other traders' stops and algorithms, then trades against the move.
It is illegal market manipulation in the US/EU (regulators prosecute it alongside spoofing).
Signs: a burst of aggressive orders in a thin market followed by a quick reversal.
Retail stops clustered at obvious levels are a common target.
Distinguish it from genuine news-driven momentum, which persists.""",
    "Sweep to fill": """Sweep-to-fill = an order type that takes liquidity at successive price levels on one venue until the full size is filled (walking the book).
It prioritises getting filled over price; the average fill can be worse than the best quote.
In futures (CME), aggressive market and limit orders effectively sweep the book through multiple price levels.
On the tape, a sweep-to-fill shows as several prints at increasing (buy) or decreasing (sell) prices at the same timestamp.
Different from an intermarket sweep, which splits across several exchanges.""",
    "Spoofing / layering": """Spoofing = placing orders you intend to cancel before execution to create a false impression of supply or demand, then trading on the other side.
Layering = spoofing with several orders at different price levels to make the book look deep on one side.
Illegal in the US since Dodd-Frank (2010); notable cases include Navinder Sarao (2010 flash crash) and big bank fines in metals futures.
Signs: big resting orders that disappear as price approaches, repeated pattern of pull and trade on the opposite side.
For a DOM trader, large displayed size is not trustworthy until it actually trades.""",
    "Quote stuffing": """Quote stuffing = flooding the market with huge numbers of orders and cancellations in a very short time.
It can slow competitors' systems and data feeds (creating latency) and confuse the market's view of prices.
It is considered manipulative/disruptive and exchanges limit message-to-trade ratios and fine excessive messaging.
It is mostly an HFT phenomenon at microsecond scale, invisible on normal charts.
Signs in data: bursts of thousands of quote updates per second with almost no trades.""",
    "Market microstructure": """Market microstructure = the study of how trading actually happens: order types, order books, matching rules, liquidity, spreads, and how information gets into prices.
Bid-ask spread = cost of immediacy; it compensates market makers for order processing, inventory risk and adverse selection.
Limit orders provide liquidity (makers); market orders take liquidity (takers).
Price discovery happens through informed traders' orders moving quotes.
Key texts: Larry Harris 'Trading and Exchanges', O'Hara 'Market Microstructure Theory'.""",
    "Order flow classification": """Order flow classification = deciding whether each trade was buyer- or seller-initiated.
Quote rule: trade at the ask = buy, at the bid = sell. Tick rule: price up from the last trade = buy, down = sell.
Lee-Ready algorithm combines the quote rule with the tick rule for trades between the quotes.
Bulk volume classification (BVC) assigns buy/sell fractions from price changes over bars.
Classified flow builds delta (buy volume - sell volume) and cumulative volume delta (CVD) used by futures traders.""",
    "Footprint charts": """A footprint chart shows, inside each candle, the volume traded at each price split into bid volume (sells) and ask volume (buys).
It reveals delta per price and per bar, imbalances (diagonal ask vs bid ratios like 3:1), and points of control (price with the most volume).
Stacked imbalances suggest aggressive initiative; large volume with little price progress suggests absorption.
Unfinished auctions (volume on both sides at the bar extreme) hint price may revisit that level.
Requires tick-level data with bid/ask classification (common in futures platforms).""",
    "Tick data": """Tick data = every individual trade (and/or quote update) with timestamp, price and size.
It is the raw material for footprint charts, delta, order-flow analysis and accurate backtests of short-term strategies.
Bars (1-minute, 5-minute) are aggregated from ticks and lose information about order and sequence within the bar.
Tick data is huge, noisy and needs cleaning (bad prints, out-of-sequence trades).
Tick charts (bars every N trades) adapt to activity: more bars in busy periods.""",
    "Queue position": """Queue position = where your limit order sits in the line at a price level. Most futures (e.g. ES) use price-time priority (FIFO): earlier orders at the same price fill first.
Good queue position means your order fills before price moves through the level, reducing adverse selection.
Late orders at a level fill mostly when price is about to trade through it (the worst fills).
Some products use pro-rata matching (e.g. some interest-rate futures), where size matters more than time.
HFT market makers invest heavily to be first in queue.""",
    "Liquidity dynamics": """Liquidity = the ability to trade size quickly at low cost; measured by spread, depth (size at each level) and resilience (how fast the book refills).
Liquidity varies by time of day: thickest after the open and before the close, thinnest overnight and at lunch.
Liquidity withdraws before news and during stress, so the same order moves price more then (liquidity spirals).
Market depth for ES top-of-book shrinks sharply when VIX is high.
Liquidity providers pull quotes when flow becomes toxic, which can cause gaps and flash moves.""",
    "Hidden liquidity": """Hidden liquidity = orders not visible in the displayed order book: fully hidden orders, the hidden part of iceberg orders, dark pools.
Iceberg orders show a small 'tip' and refill from a larger hidden quantity as the tip trades.
Signs of an iceberg: a price level keeps trading far more volume than was displayed and keeps refilling.
Hidden orders usually lose time priority versus displayed orders at the same price on many exchanges.
Hidden liquidity means the visible book understates true supply/demand.""",
    "HFT presence": """High-frequency trading (HFT) = automated firms trading at microsecond speeds, mostly market making and arbitrage.
HFT supplies a large share of displayed liquidity in futures and equities and has narrowed spreads in normal conditions.
In stress they can withdraw quickly, removing liquidity (e.g. the 2010 flash crash).
Signs: very high order-to-trade ratios, fleeting quotes, fast cancellations.
For discretionary traders, HFT presence means very short-term edges are competed away; longer holding periods avoid that race.""",
    "Fractals": """Fractal = a pattern that looks similar at different scales; markets show self-similarity across timeframes (Mandelbrot).
Mandelbrot showed price returns have fat tails and volatility clustering, unlike the normal distribution.
Hurst exponent: H > 0.5 trending (persistent), H < 0.5 mean-reverting, H = 0.5 random walk.
In trading jargon, a Bill Williams 'fractal' is a 5-bar pattern with a high (or low) in the middle bar.
Multi-timeframe analysis is a practical use: structure on 15m often mirrors structure on daily.""",
    "Regime switching": """Market regimes = periods with different behaviour (low-vol trending up, high-vol falling, range-bound).
Regime-switching models (e.g. Hamilton Markov-switching, hidden Markov models) estimate which regime the market is in and the probability of switching.
Strategies that work in one regime (trend-following in trends, mean reversion in ranges) often fail in another.
Common regime signals: realized volatility, VIX level and term structure, gamma regime, trend filters (price vs 200-day average).
Regime detection is always lagging; the switch is known with confidence only after it happened.""",
    "Execution variance": """Execution variance = the spread of outcomes from executing the same strategy because of slippage, partial fills, latency and changing liquidity.
Backtests assume clean fills; live trading pays spread, slippage and fees, which can erase small edges.
Variance is larger in fast markets, at news, and with market orders and stops.
Measuring slippage per trade (fill vs signal price) is essential to know the real edge.
Limit orders reduce slippage but add fill uncertainty (missed trades, adverse selection).""",
    "Stat arb": """Statistical arbitrage = market-neutral strategies trading many small, statistically predicted mispricings (pairs, baskets, factor residuals).
Pairs trading: two cointegrated assets; when their spread deviates (z-score), short the rich one and buy the cheap one, expecting reversion.
Cointegration (not just correlation) is required for a stable spread.
Edges are small per trade, so stat arb needs many trades, low costs and strict risk control.
Risks: relationships break (regime change), crowded trades unwind together (Aug 2007 quant quake).""",
    "Quant": """Quantitative trading = building, testing and running rules-based strategies using data and statistics.
Workflow: hypothesis -> data -> backtest -> out-of-sample/walk-forward test -> paper trade -> small live size -> scale.
Overfitting (data snooping) is the main danger: too many parameters fit noise; out-of-sample testing and few parameters fight it.
Key metrics: expectancy, Sharpe ratio, maximum drawdown, win rate x payoff ratio, number of trades (statistical significance).
Costs, slippage and capacity must be included; a strategy with no edge after costs is not a strategy.
Risk management (position sizing, e.g. fixed fractional or Kelly-fraction) matters as much as the signal.""",
}
