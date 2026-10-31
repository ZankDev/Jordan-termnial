"""Historical market-moving events, with the market's reaction MEASURED from daily prices (not remembered).

Each event: (date, after_close, type, label, title, extra assets). `date` is the calendar day the news broke (exchange-local);
after_close=1 means it broke after the US close, so the first reacting session is the next one. Weekend news reacts on the next
session automatically (first bar on/after the date, per asset, so Tel Aviv can react on a different day from New York).
Reactions per asset: d0 (first reacting session vs the last close before the news), d1 (two sessions), d5, d20, and z0 = d0 / that
asset's daily volatility over the prior year. Live stories that the AI analysed and that MOVED the market are added automatically.
Used by: the AI analysis prompt (similar past events with real numbers), the alert card, the chart markers and the EVENT HISTORY panel.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parents[2] / "data_cache" / "history_reactions.json"
_lock = threading.Lock()
_mem: dict[str, Any] = {}

BASE = ["^GSPC", "^IXIC", "^VIX", "^TNX", "DX-Y.NYB", "GC=F", "CL=F"]
BY_TYPE = {
    "war_escalation": ["BZ=F", "LMT", "NOC", "RTX", "ITA", "ESLT", "TA35.TA", "ILS=X", "CCJ", "XLE", "JETS"],
    "deescalation": ["BZ=F", "LMT", "ESLT", "TA35.TA", "ILS=X", "JETS"],
    "israel_macro": ["TA35.TA", "^TA125.TA", "ILS=X", "EIS"],
    "fed": ["TLT", "QQQ", "IWM", "KRE", "BTC-USD"], "inflation_data": ["TLT", "QQQ", "IWM", "KRE"], "jobs_data": ["TLT", "IWM", "KRE"],
    "us_data": ["TLT", "IWM"], "banking": ["XLF", "KRE", "TLT"],
    "energy": ["BZ=F", "XLE", "XOM", "CVX", "OXY", "JETS"], "trade_policy": ["FXI", "KWEB", "CNY=X", "SMH", "AAPL"],
    "crypto": ["BTC-USD", "ETH-USD", "COIN", "MSTR"], "politics": ["IWM", "TLT"], "disaster": ["JETS"], "flows": ["QQQ", "IWM"],
    "company": [], "regulatory": [],
}

# fmt: off
EVENTS: list[tuple[str, int, str, str, str, str]] = [
    # ---- crashes, crises, banking
    ("1987-10-19", 0, "flows", "BLACK MONDAY", "Black Monday: Dow falls 22.6% in one day", ""),
    ("1998-08-31", 0, "banking", "LTCM/RUSSIA", "Russia default and LTCM fears: S&P -6.8%", ""),
    ("2001-09-17", 0, "war_escalation", "9/11", "US markets reopen after the 9/11 attacks", "LMT,NOC,GD"),
    ("2003-03-20", 0, "war_escalation", "IRAQ WAR", "US-led invasion of Iraq begins", "LMT,NOC"),
    ("2008-09-15", 0, "banking", "LEHMAN", "Lehman Brothers files for bankruptcy", "JPM,BAC,GS"),
    ("2008-09-29", 0, "banking", "TARP FAILS", "House rejects TARP bailout: S&P -8.8%", "JPM,BAC"),
    ("2008-10-13", 0, "fed", "BANK RESCUES", "Coordinated global bank rescues: S&P +11.6%", "XLF"),
    ("2008-11-25", 0, "fed", "QE1", "Fed announces QE1 mortgage-bond purchases", ""),
    ("2009-03-10", 0, "banking", "CITI MEMO", "Citigroup says it is profitable: start of the 2009 bull market", "C,XLF"),
    ("2010-05-06", 0, "flows", "FLASH CRASH", "Flash crash: Dow drops ~1,000 points in minutes", ""),
    ("2010-11-03", 0, "fed", "QE2", "Fed announces QE2 ($600bn)", "GLD"),
    ("2011-03-14", 0, "disaster", "FUKUSHIMA", "Japan earthquake and Fukushima nuclear crisis", "CCJ,^N225,EWJ"),
    ("2011-08-08", 0, "us_data", "US DOWNGRADE", "S&P downgrades the US credit rating to AA+", "GLD"),
    ("2012-07-26", 0, "fed", "WHATEVER IT TAKES", "Draghi: the ECB will do whatever it takes to save the euro", "EURUSD=X,EWI"),
    ("2013-04-23", 0, "flows", "FAKE AP TWEET", "Hacked AP tweet of White House explosion: flash drop", ""),
    ("2013-05-22", 0, "fed", "TAPER TALK", "Bernanke hints at tapering QE (taper tantrum)", ""),
    ("2013-06-19", 0, "fed", "TAPER PLAN", "Fed lays out the taper timeline; yields jump", "GLD"),
    ("2014-03-03", 0, "war_escalation", "CRIMEA", "Russia seizes Crimea", "ZW=F,EURUSD=X"),
    ("2014-11-28", 0, "energy", "OPEC NO CUT", "OPEC declines to cut output; oil -10%", ""),
    ("2015-01-15", 0, "fed", "SNB UNPEG", "Swiss National Bank scraps the franc cap", "EURCHF=X"),
    ("2015-08-11", 0, "trade_policy", "YUAN DEVAL", "China devalues the yuan", ""),
    ("2015-08-24", 0, "flows", "CHINA CRASH", "China-led global selloff: Dow -1,000 at the open", "FXI"),
    ("2015-12-16", 0, "fed", "FIRST HIKE", "Fed hikes for the first time since 2006", ""),
    ("2016-01-29", 0, "fed", "BOJ NEGATIVE", "Bank of Japan adopts negative rates", "JPY=X"),
    ("2016-06-24", 0, "politics", "BREXIT", "UK votes to leave the EU", "GBPUSD=X,EWU,XLF"),
    ("2016-11-09", 0, "politics", "TRUMP WINS", "Trump wins the 2016 election", "XLF,LMT,KRE"),
    ("2016-11-30", 0, "energy", "OPEC CUT", "OPEC agrees its first cut since 2008", ""),
    ("2017-04-07", 0, "war_escalation", "SYRIA STRIKE", "US Tomahawk strike on Syria", ""),
    ("2017-08-08", 1, "war_escalation", "FIRE & FURY", "Trump threatens North Korea with 'fire and fury'", "EWY"),
    ("2017-08-28", 0, "disaster", "HARVEY", "Hurricane Harvey floods Houston refineries", "RB=F,XLE"),
    ("2017-12-20", 0, "politics", "TAX CUTS", "Tax Cuts and Jobs Act passes Congress", "IWM"),
    ("2018-02-05", 0, "flows", "VOLMAGEDDON", "VIX spike and XIV collapse: Dow -4.6%", ""),
    ("2018-03-01", 0, "trade_policy", "STEEL TARIFFS", "Trump announces steel and aluminium tariffs", "NUE,AA,CAT"),
    ("2018-03-22", 0, "trade_policy", "CHINA TARIFFS", "Trump announces tariffs on $50bn of Chinese goods", "CAT,BA"),
    ("2018-04-14", 0, "war_escalation", "SYRIA STRIKES", "US, UK and France strike Syria", ""),
    ("2018-07-25", 1, "company", "FACEBOOK 18", "Facebook -19% after a user-growth miss", "META"),
    ("2018-10-10", 0, "fed", "OCT 2018 DROP", "Rate fears selloff: S&P -3.3%", ""),
    ("2018-12-24", 0, "fed", "XMAS EVE", "Christmas Eve selloff on Fed and shutdown fears", ""),
    ("2018-12-26", 0, "flows", "XMAS RALLY", "Biggest point gain ever: Dow +1,086", ""),
    ("2019-01-02", 1, "company", "APPLE WARNING", "Apple cuts revenue guidance on China weakness", "AAPL"),
    ("2019-01-04", 0, "fed", "POWELL PIVOT", "Powell signals patience plus a strong jobs report", ""),
    ("2019-03-10", 0, "company", "737 MAX", "Second 737 MAX crash (Ethiopian Airlines)", "BA"),
    ("2019-05-05", 0, "trade_policy", "TARIFF TWEET", "Trump threatens a tariff hike on China", "CAT"),
    ("2019-06-13", 0, "war_escalation", "GULF TANKERS", "Tankers attacked in the Gulf of Oman", "FRO"),
    ("2019-06-20", 0, "war_escalation", "DRONE DOWNED", "Iran shoots down a US drone", ""),
    ("2019-07-31", 0, "fed", "FIRST CUT", "Fed cuts for the first time since 2008 ('mid-cycle')", ""),
    ("2019-08-05", 0, "trade_policy", "YUAN 7", "Yuan breaks 7; US labels China a currency manipulator", ""),
    ("2019-09-14", 0, "war_escalation", "ABQAIQ", "Drone attack on Saudi Aramco's Abqaiq facility", ""),
    ("2019-10-11", 0, "trade_policy", "PHASE ONE", "US-China 'phase one' trade deal outlined", ""),
    ("2020-01-03", 0, "war_escalation", "SOLEIMANI", "US kills Iranian general Soleimani", ""),
    ("2020-01-08", 0, "deescalation", "IRAN HOLDS", "Iran's limited missile retaliation; de-escalation", ""),
    ("2020-02-24", 0, "disaster", "COVID ITALY", "COVID spreads to Italy and Korea: Dow -1,031", "CCL"),
    ("2020-03-08", 0, "energy", "OIL WAR", "Saudi-Russia oil price war: oil -25%, S&P -7.6%", ""),
    ("2020-03-12", 0, "disaster", "TRAVEL BAN", "Europe travel ban after WHO pandemic call: S&P -9.5%", "CCL,BA"),
    ("2020-03-15", 0, "fed", "FED TO ZERO", "Emergency Fed cut to zero; S&P -12%", ""),
    ("2020-03-23", 0, "fed", "UNLIMITED QE", "Fed announces unlimited QE: COVID market bottom", ""),
    ("2020-03-24", 0, "us_data", "STIMULUS", "Stimulus deal nears: Dow +11.4%", ""),
    ("2020-04-12", 0, "energy", "OPEC+ 9.7M", "OPEC+ record 9.7m barrels/day cut", ""),
    ("2020-04-20", 0, "energy", "WTI NEGATIVE", "WTI futures settle at -$37", "USO"),
    ("2020-06-11", 0, "disaster", "SECOND WAVE", "Second-wave fears: S&P -5.9%", ""),
    ("2020-08-27", 0, "fed", "AVG INFLATION", "Fed adopts average inflation targeting", "GLD"),
    ("2020-11-04", 0, "politics", "ELECTION 2020", "US election: Biden leads, split Congress expected", ""),
    ("2020-11-09", 0, "regulatory", "PFIZER VACCINE", "Pfizer: COVID vaccine 90% effective", "PFE,CCL,ZM,XLE,IWM"),
    ("2020-11-16", 0, "regulatory", "MODERNA VACCINE", "Moderna vaccine 94.5% effective", "MRNA"),
    ("2020-11-16", 1, "company", "TSLA S&P 500", "Tesla to join the S&P 500", "TSLA"),
    ("2021-01-06", 0, "politics", "GEORGIA", "Democrats win the Georgia runoffs", "IWM,TAN"),
    ("2021-01-27", 0, "flows", "GME SQUEEZE", "GameStop short squeeze peaks", "GME,AMC"),
    ("2021-02-25", 0, "fed", "BOND TANTRUM", "Treasury yields spike: Nasdaq -3.5%", ""),
    ("2021-03-23", 0, "disaster", "SUEZ", "Ever Given blocks the Suez Canal", "ZIM"),
    ("2021-05-08", 0, "disaster", "COLONIAL", "Colonial Pipeline shut by ransomware", "RB=F,CRWD"),
    ("2021-05-12", 0, "inflation_data", "CPI 4.2%", "April CPI 4.2%: biggest jump since 2008", ""),
    ("2021-06-07", 0, "regulatory", "ADUHELM", "FDA approves Biogen's Alzheimer's drug", "BIIB"),
    ("2021-06-16", 0, "fed", "HAWKISH DOTS", "Fed dots pull rate hikes forward", "GLD"),
    ("2021-09-20", 0, "banking", "EVERGRANDE", "Evergrande default fears", "FXI"),
    ("2021-10-28", 0, "company", "META RENAME", "Facebook renames itself Meta", "META"),
    ("2021-11-10", 0, "inflation_data", "CPI 6.2%", "October 2021 CPI 6.2%", ""),
    ("2021-11-26", 0, "disaster", "OMICRON", "Omicron variant: S&P -2.3%, oil -13%", "CCL,MRNA"),
    ("2021-11-30", 0, "fed", "TRANSITORY", "Powell retires the word 'transitory'", ""),
    ("2022-01-05", 0, "fed", "QT MINUTES", "Fed minutes signal balance-sheet runoff", "ARKK"),
    ("2022-01-20", 1, "company", "NFLX JAN22", "Netflix -22% on weak subscriber guidance", "NFLX"),
    ("2022-02-01", 1, "company", "PYPL FEB22", "PayPal -25% on weak guidance", "PYPL"),
    ("2022-02-02", 1, "company", "META -26%", "Meta -26%: first-ever decline in daily users", "META,SNAP"),
    ("2022-02-03", 1, "company", "AMZN/SNAP", "Amazon +13.5% and Snap +59% after earnings", "AMZN,SNAP"),
    ("2022-02-24", 0, "war_escalation", "UKRAINE", "Russia invades Ukraine", "ZW=F,NG=F"),
    ("2022-03-08", 0, "energy", "RUSSIA OIL BAN", "US bans Russian oil; Brent near $130", ""),
    ("2022-03-16", 0, "fed", "HIKES BEGIN", "Fed starts the hiking cycle (+25bp)", ""),
    ("2022-04-14", 0, "company", "MUSK BID", "Musk bids $43bn for Twitter", "TSLA"),
    ("2022-04-19", 1, "company", "NFLX -35%", "Netflix -35%: first subscriber loss in a decade", "NFLX"),
    ("2022-04-28", 1, "company", "AMZN -14%", "Amazon -14% on a surprise loss", "AMZN"),
    ("2022-05-05", 0, "fed", "POST-FOMC DUMP", "Post-FOMC reversal: Nasdaq -5%", ""),
    ("2022-05-09", 0, "crypto", "LUNA", "TerraUSD/Luna collapse", ""),
    ("2022-05-23", 1, "company", "SNAP WARNING", "Snap profit warning: -43%", "SNAP,META,GOOGL"),
    ("2022-06-08", 0, "energy", "FREEPORT LNG", "Freeport LNG explosion: US gas plunges", "NG=F,LNG"),
    ("2022-06-10", 0, "inflation_data", "CPI 8.6%", "May CPI 8.6%: 40-year high", ""),
    ("2022-06-13", 0, "crypto", "CELSIUS", "Celsius freezes withdrawals; S&P enters a bear market", ""),
    ("2022-06-15", 0, "fed", "75BP HIKE", "Fed hikes 75bp, the largest since 1994", ""),
    ("2022-07-13", 0, "inflation_data", "CPI 9.1%", "June CPI 9.1%: the peak", ""),
    ("2022-08-02", 0, "war_escalation", "PELOSI TAIWAN", "Pelosi visits Taiwan; China drills", "TSM,FXI"),
    ("2022-08-26", 0, "fed", "JACKSON HOLE 22", "Powell at Jackson Hole: 'some pain' ahead", ""),
    ("2022-09-13", 0, "inflation_data", "CPI HOT SEP22", "August CPI hotter than expected: S&P -4.3%", ""),
    ("2022-09-23", 0, "politics", "UK MINI-BUDGET", "UK mini-budget triggers the gilt crisis", "GBPUSD=X"),
    ("2022-09-26", 0, "war_escalation", "NORD STREAM", "Nord Stream pipelines sabotaged", "NG=F,EURUSD=X"),
    ("2022-10-13", 0, "inflation_data", "CPI REVERSAL", "Hot CPI, then a huge intraday reversal", ""),
    ("2022-10-26", 1, "company", "META -25%", "Meta -25% on metaverse spending", "META"),
    ("2022-11-08", 0, "crypto", "FTX", "FTX collapses", ""),
    ("2022-11-10", 0, "inflation_data", "CPI COOL NOV22", "October CPI cooler: S&P +5.5%", ""),
    ("2022-11-30", 0, "company", "CHATGPT", "OpenAI launches ChatGPT", "MSFT,NVDA,GOOGL"),
    ("2022-12-20", 0, "fed", "BOJ YCC", "Bank of Japan widens its yield band; yen jumps", "JPY=X"),
    ("2023-01-23", 0, "company", "MSFT-OPENAI", "Microsoft's $10bn OpenAI investment", "MSFT"),
    ("2023-02-01", 1, "company", "META +23%", "Meta +23%: 'year of efficiency'", "META"),
    ("2023-02-03", 0, "jobs_data", "517K JOBS", "January jobs +517k blowout", ""),
    ("2023-02-08", 0, "company", "BARD ERROR", "Google Bard demo error: -7.7%", "GOOGL"),
    ("2023-03-09", 0, "banking", "SVB RUN", "SVB capital raise; the bank run begins", "SCHW"),
    ("2023-03-10", 0, "banking", "SVB FAILS", "SVB seized by regulators", "SCHW"),
    ("2023-03-13", 0, "banking", "SIGNATURE", "Signature Bank fails; Fed backstop", "SCHW"),
    ("2023-03-19", 0, "banking", "CREDIT SUISSE", "UBS rescues Credit Suisse", "UBS"),
    ("2023-03-22", 0, "fed", "HIKE IN STRESS", "Fed hikes 25bp amid bank stress", ""),
    ("2023-03-26", 1, "israel_macro", "GALLANT FIRED", "Netanyahu fires Gallant; mass protests and general strike", ""),
    ("2023-04-02", 0, "energy", "OPEC SURPRISE", "OPEC+ surprise output cut", ""),
    ("2023-05-01", 0, "banking", "FIRST REPUBLIC", "First Republic seized and sold to JPMorgan", "JPM"),
    ("2023-05-24", 1, "company", "NVDA +24%", "Nvidia +24%: AI guidance shock", "NVDA,AMD,SMH"),
    ("2023-05-27", 0, "politics", "DEBT DEAL", "US debt-ceiling deal reached", ""),
    ("2023-06-24", 0, "war_escalation", "WAGNER", "Wagner mutiny in Russia", "ZW=F"),
    ("2023-07-12", 0, "inflation_data", "CPI 3%", "June 2023 CPI falls to 3%", ""),
    ("2023-07-24", 0, "israel_macro", "REASONABLENESS", "Knesset passes the reasonableness law", ""),
    ("2023-07-25", 0, "company", "RTX ENGINES", "RTX -10% on a Pratt & Whitney engine recall", "RTX"),
    ("2023-07-26", 0, "fed", "LAST HIKE", "Fed's last hike of the cycle", ""),
    ("2023-08-01", 1, "us_data", "FITCH", "Fitch downgrades the US to AA+", ""),
    ("2023-09-05", 0, "energy", "SAUDI EXTENDS", "Saudi Arabia and Russia extend cuts to year-end", ""),
    ("2023-10-07", 0, "war_escalation", "OCT 7", "Hamas attacks Israel; Israel declares war", "ELAL.TA,NXSN.TA"),
    ("2023-10-19", 0, "fed", "10Y AT 5%", "The 10-year yield hits 5%", ""),
    ("2023-11-14", 0, "inflation_data", "CPI COOL NOV23", "October CPI cooler; end of hikes priced", ""),
    ("2023-11-17", 1, "company", "ALTMAN FIRED", "OpenAI board fires Sam Altman", "MSFT"),
    ("2023-12-13", 0, "fed", "FED PIVOT", "Fed dots signal 2024 cuts", ""),
    ("2023-12-18", 0, "war_escalation", "RED SEA", "Shipping majors halt Red Sea transits after Houthi attacks", "ZIM,FRO"),
    ("2024-01-01", 0, "israel_macro", "BOI CUT + COURT", "Bank of Israel's first cut since 2020; court strikes the reasonableness law", ""),
    ("2024-01-05", 1, "company", "DOOR PLUG", "Alaska Airlines 737 MAX 9 door plug blows out", "BA"),
    ("2024-01-10", 1, "crypto", "BTC ETFS", "SEC approves spot bitcoin ETFs", ""),
    ("2024-01-11", 1, "war_escalation", "HOUTHI STRIKES", "US and UK strike Houthi targets in Yemen", "ZIM"),
    ("2024-02-09", 1, "israel_macro", "MOODY'S ISRAEL", "Moody's downgrades Israel to A2", ""),
    ("2024-02-13", 0, "inflation_data", "CPI HOT FEB24", "January 2024 CPI hot: small caps -4%", ""),
    ("2024-02-21", 1, "company", "NVDA FEB24", "Nvidia +16% on data-centre revenue", "NVDA,SMH"),
    ("2024-04-01", 0, "war_escalation", "DAMASCUS", "Israeli strike on Iran's consulate in Damascus", ""),
    ("2024-04-03", 0, "disaster", "TAIWAN QUAKE", "Taiwan 7.4 earthquake hits chipmakers", "TSM,SMH"),
    ("2024-04-10", 0, "inflation_data", "CPI HOT APR24", "March CPI hot for a third month", ""),
    ("2024-04-13", 0, "war_escalation", "IRAN DRONES", "Iran's first direct drone and missile attack on Israel", ""),
    ("2024-04-19", 0, "war_escalation", "ISFAHAN", "Israel's limited strike near Isfahan", ""),
    ("2024-04-23", 1, "company", "TSLA APR24", "Tesla +12% on cheaper models", "TSLA"),
    ("2024-04-24", 1, "company", "META CAPEX", "Meta -10.5% on AI spending plans", "META"),
    ("2024-04-25", 1, "company", "GOOGL DIVIDEND", "Alphabet +10%: first dividend", "GOOGL"),
    ("2024-05-15", 0, "inflation_data", "CPI COOL MAY24", "April CPI cools: record highs", ""),
    ("2024-06-11", 0, "company", "APPLE AI", "Apple Intelligence rally: AAPL +7% to a record", "AAPL"),
    ("2024-06-27", 1, "company", "NIKE -20%", "Nike -20% on weak guidance", "NKE"),
    ("2024-07-11", 0, "inflation_data", "CPI JUN24", "June CPI falls: rotation to small caps", "NVDA"),
    ("2024-07-13", 0, "politics", "TRUMP SHOT", "Assassination attempt on Trump", "DJT"),
    ("2024-07-19", 0, "company", "CROWDSTRIKE", "CrowdStrike update crashes Windows worldwide", "CRWD,MSFT,DAL"),
    ("2024-07-21", 0, "politics", "BIDEN OUT", "Biden drops out of the race", "DJT"),
    ("2024-07-31", 0, "war_escalation", "HANIYEH", "Hamas leader Haniyeh killed in Tehran", ""),
    ("2024-07-31", 0, "fed", "BOJ HIKE", "Bank of Japan hikes; yen surges", "JPY=X"),
    ("2024-08-01", 1, "company", "INTEL -26%", "Intel -26%: dividend suspended, mass layoffs", "INTC"),
    ("2024-08-02", 0, "jobs_data", "SAHM RULE", "Weak July jobs report triggers the Sahm rule", ""),
    ("2024-08-05", 0, "flows", "YEN UNWIND", "Yen carry-trade unwind: Nikkei -12%, VIX 65", "^N225,JPY=X,NVDA"),
    ("2024-09-17", 1, "war_escalation", "PAGERS", "Hezbollah pager explosions", ""),
    ("2024-09-18", 0, "fed", "50BP CUT", "Fed cuts 50bp to start easing", ""),
    ("2024-09-27", 1, "war_escalation", "NASRALLAH", "Israel kills Hezbollah chief Nasrallah", ""),
    ("2024-10-01", 0, "war_escalation", "IRAN 180 MISSILES", "Iran fires ~180 ballistic missiles at Israel", ""),
    ("2024-10-04", 0, "jobs_data", "254K JOBS", "September jobs +254k", ""),
    ("2024-10-22", 0, "company", "LMT MISS", "Lockheed Martin -6% on an earnings miss", "LMT"),
    ("2024-10-23", 1, "company", "TSLA +22%", "Tesla +22% on margins and growth outlook", "TSLA"),
    ("2024-10-26", 0, "war_escalation", "ISRAEL HITS IRAN", "Israel's limited strikes on Iran spare oil sites", ""),
    ("2024-10-30", 0, "company", "SMCI AUDITOR", "Super Micro -33% as auditor EY resigns", "SMCI"),
    ("2024-11-04", 1, "company", "PLTR +23%", "Palantir +23% on AI demand", "PLTR"),
    ("2024-11-05", 1, "politics", "TRUMP 2024", "Trump wins the 2024 election", "TSLA,DJT,IWM,KRE,BTC-USD,COIN"),
    ("2024-11-26", 0, "deescalation", "LEBANON TRUCE", "Israel-Hezbollah ceasefire agreed", ""),
    ("2024-12-04", 1, "crypto", "BTC $100K", "Bitcoin tops $100,000", ""),
    ("2024-12-12", 1, "company", "AVGO +24%", "Broadcom +24% on AI revenue outlook", "AVGO"),
    ("2024-12-18", 0, "fed", "HAWKISH CUT", "Fed hawkish cut: S&P -2.9%", ""),
    ("2025-01-15", 0, "deescalation", "GAZA DEAL", "Gaza ceasefire and hostage deal announced", ""),
    ("2025-01-21", 1, "company", "STARGATE", "Stargate $500bn AI infrastructure announced", "ORCL,NVDA,SMH"),
    ("2025-01-27", 0, "company", "DEEPSEEK", "DeepSeek shock: Nvidia -17%", "NVDA,AVGO,SMH,CEG,VST"),
    ("2025-01-29", 1, "company", "MSFT AZURE", "Microsoft -6% on Azure growth", "MSFT"),
    ("2025-02-01", 0, "trade_policy", "CANADA/MEXICO", "Tariffs on Canada, Mexico and China announced", "CAD=X,MXN=X,GM,F"),
    ("2025-02-26", 1, "company", "NVDA FEB25", "Nvidia beats; stock falls 8.5%", "NVDA"),
    ("2025-03-10", 0, "flows", "GROWTH SCARE", "Recession fears: Nasdaq -4%, Tesla -15%", "TSLA"),
    ("2025-04-02", 1, "trade_policy", "LIBERATION DAY", "Trump's 'Liberation Day' reciprocal tariffs", "NKE,BZ=F"),
    ("2025-04-04", 0, "trade_policy", "CHINA RETALIATES", "China retaliates with 34% tariffs", ""),
    ("2025-04-09", 0, "trade_policy", "TARIFF PAUSE", "90-day tariff pause: S&P +9.5%", "NVDA"),
    ("2025-04-17", 0, "company", "UNH -22%", "UnitedHealth -22% on an earnings miss", "UNH"),
    ("2025-04-21", 0, "fed", "TRUMP VS POWELL", "Trump attacks Powell: dollar slides, gold at a record", ""),
    ("2025-05-07", 0, "war_escalation", "INDIA-PAKISTAN", "India strikes Pakistan (Operation Sindoor)", "INDA"),
    ("2025-05-12", 0, "trade_policy", "GENEVA TRUCE", "US-China Geneva deal cuts tariffs", ""),
    ("2025-05-13", 0, "company", "UNH CEO OUT", "UnitedHealth CEO exits, guidance suspended", "UNH"),
    ("2025-05-16", 1, "us_data", "MOODY'S US", "Moody's strips the US of its last AAA", ""),
    ("2025-06-05", 0, "company", "MUSK VS TRUMP", "Musk-Trump feud: Tesla -14%", "TSLA"),
    ("2025-06-13", 0, "war_escalation", "ISRAEL STRIKES IRAN", "Israel strikes Iran's nuclear and military sites", "NXSN.TA"),
    ("2025-06-21", 0, "war_escalation", "FORDOW", "US bombs Iran's nuclear sites (Fordow, Natanz)", "NXSN.TA"),
    ("2025-06-23", 1, "deescalation", "IRAN CEASEFIRE", "Israel-Iran ceasefire announced", ""),
    ("2025-07-29", 0, "company", "NOVO WARNING", "Novo Nordisk -22% profit warning", "NVO,LLY"),
    ("2025-07-30", 1, "company", "MSFT/META Q2", "Microsoft and Meta crush estimates", "MSFT,META"),
    ("2025-08-01", 0, "jobs_data", "JOBS REVISIONS", "Weak July jobs and huge downward revisions", ""),
    ("2025-08-14", 1, "flows", "BERKSHIRE UNH", "Berkshire discloses a UnitedHealth stake", "UNH"),
    ("2025-08-22", 0, "fed", "JACKSON HOLE 25", "Powell opens the door to a September cut", ""),
    ("2025-09-02", 1, "regulatory", "GOOGLE RULING", "Judge rejects a Chrome breakup in the Google case", "GOOGL,AAPL"),
    ("2025-09-09", 1, "company", "ORCL +36%", "Oracle +36% on its cloud backlog", "ORCL"),
    ("2025-09-17", 0, "fed", "CUTS RESUME", "Fed resumes cuts (25bp)", ""),
    ("2025-09-18", 0, "company", "NVDA-INTEL", "Nvidia invests $5bn in Intel", "INTC,NVDA,AMD"),
    ("2025-10-01", 0, "politics", "SHUTDOWN", "US government shuts down", ""),
    ("2025-10-06", 0, "company", "AMD-OPENAI", "AMD-OpenAI chip deal: AMD +24%", "AMD,NVDA"),
    ("2025-10-08", 1, "deescalation", "GAZA PEACE", "Israel and Hamas agree the first phase of the Trump peace plan", ""),
    ("2025-10-10", 0, "trade_policy", "CHINA 100%", "Trump threatens 100% tariffs on China; crypto liquidation cascade", "BTC-USD,NVDA"),
    # ---- 2026 (dates from news reports: CNN, CNBC, NPR, Al Jazeera, EY, Fox Business)
    ("2026-02-20", 0, "trade_policy", "SCOTUS TARIFFS", "Supreme Court strikes down IEEPA tariffs; Trump announces a 10% global tariff", "AMZN,AAPL,XRT"),
    ("2026-02-28", 0, "war_escalation", "EPIC FURY", "US and Israel strike Iran; war spreads across the Middle East", "NXSN.TA,ELAL.TA"),
    ("2026-03-04", 0, "war_escalation", "HORMUZ CLOSED", "Iran declares the Strait of Hormuz closed and attacks ships", "FRO,STNG,INSW,ZIM"),
    ("2026-04-07", 1, "deescalation", "US-IRAN TRUCE", "US and Iran agree a two-week ceasefire; oil plunges", "FRO"),
    ("2026-06-14", 0, "deescalation", "US-IRAN DEAL", "US and Iran announce a deal (memorandum of understanding)", ""),
    ("2026-07-08", 0, "war_escalation", "WAR RESUMES", "US strikes Iran again; oil reverses its fall", "FRO"),
    ("2026-07-29", 0, "fed", "FED HOLD JUL26", "Fed holds rates; stocks sell off", ""),
    ("2026-08-28", 0, "fed", "WARSH INFLATION", "Fed chair Warsh highlights inflation worries", ""),
    ("2026-09-16", 0, "fed", "FED HIKES", "Fed raises rates to 3.75-4%, first hike since 2023", ""),
]
# fmt: on


def _assets(ev: tuple) -> list[str]:
    extra = [x.strip() for x in ev[5].split(",") if x.strip()]
    out: list[str] = []
    for s in BASE + BY_TYPE.get(ev[2], []) + extra:
        if s not in out:
            out.append(s)
    return out


def _daily(sym: str) -> list[tuple[str, float]] | None:
    """[(exchange-local date, close)] for the whole history."""
    from app.api import invest_routes as ir

    try:
        h = ir._history(sym, "39y", "1d")  # "max" returns quarterly bars; 39y is the longest daily range (back to 1987)
    except Exception:  # noqa: BLE001
        return None
    off = int(h["meta"].get("gmtoffset") or 0)
    out, seen = [], set()
    for c in h["candles"]:
        d = time.strftime("%Y-%m-%d", time.gmtime(c[0] + off))
        if d not in seen and c[4]:
            seen.add(d)
            out.append((d, float(c[4])))
    return out


def react(bars: list[tuple[str, float]], date: str, after_close: int) -> dict[str, Any] | None:
    """Reaction of one asset to an event on `date` from its daily bars."""
    i = next((k for k, (d, _) in enumerate(bars) if (d > date if after_close else d >= date)), None)
    if i is None or i == 0 or i + 1 >= len(bars) and i >= len(bars):
        return None
    if (time.mktime(time.strptime(bars[i][0], "%Y-%m-%d")) - time.mktime(time.strptime(date, "%Y-%m-%d"))) > 6 * 86400:
        return None  # no trading near the date (asset did not exist yet)
    p0 = bars[i - 1][1]
    rets = [bars[k][1] / bars[k - 1][1] - 1 for k in range(max(1, i - 250), i)]
    sd = (sum(r * r for r in rets) / len(rets)) ** 0.5 if len(rets) > 20 else None
    out = {"day": bars[i][0], "d0": round((bars[i][1] / p0 - 1) * 100, 2)}
    for name, n in (("d1", 1), ("d5", 4), ("d20", 19)):
        if i + n < len(bars):
            out[name] = round((bars[i + n][1] / p0 - 1) * 100, 2)
    out["z0"] = round((bars[i][1] / p0 - 1) / sd, 1) if sd else None
    return out


def _live_events() -> list[tuple[str, int, str, str, str, str]]:
    """Stories from the live engine that the AI analysed and that really MOVED the market: they join the library."""
    from app import db

    try:
        with db.read_conn() as conn:
            rows = conn.execute("SELECT ts, ntype, title, ai FROM news_stories WHERE verdict='MOVED' AND ai LIKE '%\"analysis\": {%' ORDER BY ts").fetchall()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        an = (json.loads(r["ai"] or "{}") or {}).get("analysis") or {}
        if not an.get("market_moving"):
            continue
        t = time.gmtime(r["ts"] - 4 * 3600)  # New York date
        ac = 1 if t.tm_hour >= 16 else 0
        extra = ",".join(a["symbol"] for a in an.get("affected", [])[:6])
        out.append((time.strftime("%Y-%m-%d", t), ac, r["ntype"] or "other", "LIVE", (an.get("event") or r["title"])[:140], extra))
    return out


def build(force: bool = False) -> dict[str, Any]:
    """Measure every event (cached on disk; rebuilt weekly or when the event list changes)."""
    with _lock:
        if _mem and not force and time.time() - _mem.get("built", 0) < 7 * 86400:
            return _mem
        evs = EVENTS + _live_events()
        sig = str(len(evs)) + ":" + str(hash(tuple(e[0] + e[3] for e in evs)) % 10**9)
        if not force and CACHE.exists():
            try:
                d = json.loads(CACHE.read_text(encoding="utf-8"))
                if d.get("sig") == sig and time.time() - d.get("built", 0) < 7 * 86400:
                    _mem.clear()
                    _mem.update(d)
                    return _mem
            except ValueError:
                pass
        syms = sorted({s for e in evs for s in _assets(e)})
        with ThreadPoolExecutor(6) as ex:
            series = dict(zip(syms, ex.map(_daily, syms)))
        rows = []
        for k, e in enumerate(evs):
            r = {"id": k, "date": e[0], "after_close": e[1], "type": e[2], "label": e[3], "title": e[4], "live": e[3] == "LIVE", "react": {}}
            for s in _assets(e):
                if series.get(s):
                    x = react(series[s], e[0], e[1])
                    if x:
                        r["react"][s] = x
            rows.append(r)
        d = {"sig": sig, "built": time.time(), "events": rows, "missing": [s for s in syms if not series.get(s)]}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(d), encoding="utf-8")
        _mem.clear()
        _mem.update(d)
        return _mem


def events() -> list[dict[str, Any]]:
    return build()["events"]


def playbook(ntype: str) -> dict[str, Any]:
    """Average first-session reaction per asset across all past events of this type, with how often the sign agreed."""
    evs = [e for e in events() if e["type"] == ntype]
    by: dict[str, list[float]] = {}
    for e in evs:
        for s, x in e["react"].items():
            by.setdefault(s, []).append(x["d0"])
    rows = []
    for s, v in by.items():
        if len(v) >= 3:
            avg = sum(v) / len(v)
            same = sum(1 for x in v if (x > 0) == (avg > 0)) / len(v)
            rows.append({"symbol": s, "n": len(v), "avg_d0": round(avg, 2), "med_d0": round(sorted(v)[len(v) // 2], 2), "same_sign": round(same, 2)})
    rows.sort(key=lambda r: -abs(r["avg_d0"]) * r["same_sign"])
    return {"type": ntype, "events": len(evs), "assets": rows}


_W = re.compile(r"[a-z0-9']{3,}")
STOPW = {"the", "and", "for", "with", "after", "from", "into", "over", "its", "his", "says", "first", "new", "amid", "than"}


def _words(t: str) -> set[str]:
    return {w for w in _W.findall((t or "").lower()) if w not in STOPW}


def analogues(text: str, ntype: str | None = None, k: int = 4) -> list[dict[str, Any]]:
    """Most similar past events: same type first, then word overlap with the story (English event summary works best)."""
    w = _words(text)
    scored = []
    for e in events():
        ov = len(w & _words(e["title"] + " " + e["label"]))
        same = 1 if ntype and e["type"] == ntype else 0
        if ov or same:
            scored.append((same * 2 + ov, e["date"], e))
    scored.sort(key=lambda x: x[1], reverse=True)  # newest first among equals
    scored.sort(key=lambda x: -x[0])
    return [e for _, _, e in scored[:k]]


LABELS = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^VIX": "VIX", "^TNX": "10y yield", "^N225": "Nikkei", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "KRE": "Regional banks",
          "XLF": "Financials", "ITA": "US defence ETF", "TLT": "Long bonds", "EIS": "Israel ETF"}


def name(s: str) -> str:
    from app.intel.ai_news import NAMES

    return LABELS.get(s) or NAMES.get(s, s).split(" (")[0]


def brief(e: dict[str, Any], assets: list[str] | None = None, n: int = 6) -> str:
    """One line: date, event, first-session moves of the most relevant assets."""

    keys = [s for s in (assets or []) if s in e["react"]] or sorted(e["react"], key=lambda s: -abs(e["react"][s].get("z0") or 0))
    parts = [f"{name(s)} {e['react'][s]['d0']:+.1f}%" for s in keys[:n]]
    return f"{e['date']} {e['title']}: " + ", ".join(parts)


def for_symbol(symbol: str, bars: list[tuple[str, float]] | None = None) -> list[dict[str, Any]]:
    """How one symbol reacted to every event in the library (for chart markers)."""
    key = f"sym:{symbol}"
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    bars = bars or _daily(symbol) or []
    out = []
    for e in events():
        x = react(bars, e["date"], e["after_close"]) if bars else None
        if not x:
            continue
        spx = e["react"].get("^GSPC", {}).get("d0")
        out.append({"id": e["id"], "date": e["date"], "day": x["day"], "label": e["label"], "title": e["title"], "type": e["type"], "d0": x["d0"],
                    "d5": x.get("d5"), "z0": x.get("z0"), "spx": spx, "live": e["live"]})
    _mem[key] = (time.time(), out)
    return out


def safe_pct(v: Any) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f}%"
