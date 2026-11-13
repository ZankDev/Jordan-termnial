"""Market-impact rule table for the EVENTS MAP: which tradeable assets sit near / are exposed to a natural event.

A rule = a region (one or more lat/lon boxes), the event kinds it reacts to, a minimum severity, and the assets with a
one-line reason. Symbols are Yahoo Finance tickers so the sidebar can quote them. `bias` is the usual first reaction
(+ up, - down, ~ volatile/either way) — a historical tendency, not a forecast.

Severity is normalised 0..1 by `severity()` so a rule can say "only M6.0+ quakes" or "only hurricanes" uniformly."""
from __future__ import annotations

from typing import Any

A = dict  # short alias for the asset rows below


def _a(sym: str, name: str, why: str, bias: str = "~") -> dict[str, str]:
    return A(sym=sym, name=name, why=why, bias=bias)


# (region id, label, boxes [(lat0, lat1, lon0, lon1)], kinds, min severity, assets)
RULES: list[tuple[str, str, list[tuple[float, float, float, float]], set[str], float, list[dict[str, str]]]] = [
    ("gulf", "US Gulf of Mexico", [(18, 31.5, -98, -80.5)], {"cyclone", "storm", "flood"}, 0.25, [
        _a("CL=F", "WTI crude", "~15% of US crude output is offshore Gulf; platforms evacuate", "+"),
        _a("NG=F", "Natural gas", "Gulf production + LNG export terminals (Sabine Pass, Cameron)", "~"),
        _a("RB=F", "RBOB gasoline", "~50% of US refining capacity sits on the Gulf coast", "+"),
        _a("VLO", "Valero", "Gulf coast refineries at risk of shutdown", "-"),
        _a("MPC", "Marathon Petroleum", "Galveston Bay / Garyville refineries", "-"),
        _a("XLE", "Energy sector ETF", "broad energy exposure to the Gulf", "~"),
        _a("ALL", "Allstate", "property-catastrophe claims", "-"),
    ]),
    ("florida", "Florida", [(24.3, 31, -87.7, -79.8)], {"cyclone", "storm", "flood"}, 0.25, [
        _a("OJ=F", "Orange juice", "Florida grows most US oranges; groves flood / trees fall", "+"),
        _a("UVE", "Universal Insurance", "Florida homeowners insurer", "-"),
        _a("HCI", "HCI Group", "Florida homeowners insurer", "-"),
        _a("RNR", "RenaissanceRe", "property-cat reinsurer", "-"),
        _a("HD", "Home Depot", "rebuild demand after landfall", "+"),
    ]),
    ("useast", "US East coast", [(31, 45, -82, -66)], {"cyclone", "storm"}, 0.3, [
        _a("TRV", "Travelers", "east-coast property claims", "-"),
        _a("ED", "Con Edison", "grid outages, storm costs", "-"),
        _a("GNRC", "Generac", "generator demand spikes before/after storms", "+"),
    ]),
    ("caribbean", "Caribbean", [(10, 24, -86, -59)], {"cyclone", "storm"}, 0.3, [
        _a("RCL", "Royal Caribbean", "itineraries re-routed, port damage", "-"),
        _a("CCL", "Carnival", "itineraries re-routed, port damage", "-"),
        _a("RNR", "RenaissanceRe", "island property-cat losses", "-"),
    ]),
    ("japan", "Japan", [(24, 46, 122.5, 149)], {"quake", "tsunami", "cyclone", "storm", "volcano"}, 0.45, [
        _a("^N225", "Nikkei 225", "domestic equities sell off on major quakes", "-"),
        _a("JPY=X", "USD/JPY", "yen often strengthens: repatriation to pay for rebuilding (2011)", "-"),
        _a("EWJ", "iShares Japan", "US-listed Japan exposure", "-"),
        _a("TM", "Toyota", "plant shutdowns, supply-chain stoppages", "-"),
        _a("SOXX", "Semiconductor ETF", "Japanese wafers / chemicals / equipment supply", "~"),
    ]),
    ("taiwan", "Taiwan", [(21.5, 25.6, 119, 122.5)], {"quake", "cyclone", "storm"}, 0.4, [
        _a("TSM", "TSMC", "fabs pause for inspection; wafers in process scrapped", "-"),
        _a("^TWII", "Taiwan TAIEX", "local market reaction", "-"),
        _a("SOXX", "Semiconductor ETF", "global chip supply concentrated in Taiwan", "-"),
        _a("NVDA", "NVIDIA", "depends on TSMC advanced nodes", "-"),
    ]),
    ("chile", "Chile", [(-56, -17.5, -76, -66.5)], {"quake", "tsunami", "volcano", "wildfire", "drought"}, 0.45, [
        _a("HG=F", "Copper", "Chile mines ~25% of world copper (Escondida, Collahuasi)", "+"),
        _a("FCX", "Freeport-McMoRan", "copper miner (El Abra)", "~"),
        _a("SQM", "SQM", "Atacama lithium", "~"),
        _a("ECH", "iShares Chile", "local equities", "-"),
        _a("CLP=X", "USD/CLP", "peso weakens on disaster risk", "+"),
    ]),
    ("peru", "Peru", [(-18.5, 0, -81.5, -68.5)], {"quake", "tsunami", "volcano", "flood"}, 0.45, [
        _a("HG=F", "Copper", "Peru is the #2-3 copper producer (Las Bambas, Antamina)", "+"),
        _a("SCCO", "Southern Copper", "Peruvian copper mines", "~"),
        _a("SI=F", "Silver", "Peru is a top-3 silver producer", "+"),
        _a("EPU", "iShares Peru", "local equities", "-"),
    ]),
    ("drc_zambia", "Copperbelt (DRC / Zambia)", [(-15, -5, 22, 32)], {"flood", "drought", "quake"}, 0.4, [
        _a("HG=F", "Copper", "Copperbelt output + hydropower-dependent smelters", "+"),
        _a("GLNCY", "Glencore", "cobalt / copper operations", "~"),
    ]),
    ("indonesia", "Indonesia", [(-11, 6, 95, 141)], {"quake", "tsunami", "volcano", "flood", "wildfire"}, 0.45, [
        _a("EIDO", "iShares Indonesia", "local equities", "-"),
        _a("IDR=X", "USD/IDR", "rupiah risk-off", "+"),
        _a("MTF=F", "Coal (Newcastle)", "Indonesia is the largest thermal-coal exporter", "+"),
        _a("JETS", "Airlines ETF", "volcanic ash closes airspace", "-"),
    ]),
    ("philippines", "Philippines", [(4.5, 21, 116, 127)], {"cyclone", "storm", "quake", "volcano", "flood"}, 0.35, [
        _a("EPHE", "iShares Philippines", "local equities", "-"),
        _a("PHP=X", "USD/PHP", "peso risk-off", "+"),
        _a("ZR=F", "Rough rice", "typhoon damage to the rice crop", "+"),
    ]),
    ("southchina", "South China / Hong Kong", [(18, 26, 105, 122)], {"cyclone", "storm", "flood"}, 0.4, [
        _a("^HSI", "Hang Seng", "HK exchange closes on Signal 8+", "-"),
        _a("ZIM", "ZIM Shipping", "Yantian / Shenzhen / HK port closures tighten freight", "+"),
        _a("FXI", "China large-cap ETF", "local equities", "-"),
    ]),
    ("korea", "Korea", [(33, 39, 124, 131)], {"cyclone", "storm", "quake"}, 0.4, [
        _a("EWY", "iShares South Korea", "local equities", "-"),
        _a("005930.KS", "Samsung", "fab / port disruption", "-"),
    ]),
    ("india_bob", "Bay of Bengal (India / Bangladesh)", [(8, 24, 79, 95)], {"cyclone", "storm", "flood"}, 0.35, [
        _a("INR=X", "USD/INR", "rupee risk-off", "+"),
        _a("ZR=F", "Rough rice", "east-India / Bangladesh rice crop", "+"),
        _a("CT=F", "Cotton", "Indian cotton belt rains", "~"),
        _a("INDA", "iShares India", "local equities", "-"),
    ]),
    ("india_west", "Arabian Sea (Gujarat / Mumbai)", [(8, 25, 62, 76)], {"cyclone", "storm", "flood"}, 0.35, [
        _a("RELIANCE.NS", "Reliance Industries", "Jamnagar, the world's largest refinery complex", "-"),
        _a("BZ=F", "Brent crude", "Gulf of Oman / Arabian Sea tanker routes", "+"),
        _a("INR=X", "USD/INR", "rupee risk-off", "+"),
    ]),
    ("pilbara", "Western Australia (Pilbara)", [(-26, -15, 112, 125)], {"cyclone", "storm", "flood"}, 0.3, [
        _a("TIO=F", "Iron ore", "Port Hedland / Dampier close: ~40% of seaborne iron ore", "+"),
        _a("BHP", "BHP", "Pilbara iron-ore operations", "-"),
        _a("RIO", "Rio Tinto", "Pilbara iron-ore operations", "-"),
        _a("AUDUSD=X", "AUD/USD", "terms-of-trade hit", "-"),
    ]),
    ("queensland", "Queensland", [(-29, -10, 138, 154)], {"cyclone", "storm", "flood"}, 0.3, [
        _a("MTF=F", "Coal", "Bowen Basin met-coal mines flood (2011)", "+"),
        _a("SB=F", "Sugar", "Queensland grows most Australian cane", "+"),
        _a("AUDUSD=X", "AUD/USD", "terms-of-trade hit", "-"),
    ]),
    ("california", "California", [(32.5, 42, -124.5, -114)], {"quake", "wildfire", "flood", "heat"}, 0.35, [
        _a("PCG", "PG&E", "wildfire liability (2018 Camp Fire bankruptcy)", "-"),
        _a("EIX", "Edison International", "SoCal wildfire liability", "-"),
        _a("ALL", "Allstate", "property claims", "-"),
        _a("AAPL", "Apple", "Bay Area HQ / suppliers on major quakes", "~"),
    ]),
    ("pnw", "Pacific Northwest", [(42, 49.5, -125, -116)], {"quake", "tsunami", "wildfire", "volcano"}, 0.45, [
        _a("LBS=F", "Lumber", "timberland wildfires / quake rebuild", "+"),
        _a("BA", "Boeing", "Puget Sound plants", "-"),
    ]),
    ("alaska", "Alaska / Aleutians", [(50, 72, -180, -129)], {"quake", "tsunami", "volcano"}, 0.55, [
        _a("JETS", "Airlines ETF", "ash on North Pacific air routes", "-"),
        _a("CL=F", "WTI crude", "Trans-Alaska Pipeline shuts for inspection after big quakes", "+"),
    ]),
    ("canada_west", "Western Canada (Alberta / BC)", [(49, 60, -125, -110)], {"wildfire", "flood"}, 0.25, [
        _a("CL=F", "WTI crude", "oil-sands output curtailed (Fort McMurray 2016)", "+"),
        _a("SU", "Suncor", "oil-sands operations", "-"),
        _a("CNQ", "Canadian Natural", "oil-sands operations", "-"),
        _a("CAD=X", "USD/CAD", "loonie on lost output", "+"),
    ]),
    ("mexico", "Mexico", [(14.5, 32.7, -118, -86.7)], {"quake", "cyclone", "storm", "volcano"}, 0.45, [
        _a("MXN=X", "USD/MXN", "peso risk-off", "+"),
        _a("EWW", "iShares Mexico", "local equities", "-"),
        _a("SI=F", "Silver", "Mexico is the #1 silver producer", "+"),
    ]),
    ("turkey", "Turkey / East Med", [(35.5, 42.2, 25.5, 45)], {"quake", "wildfire", "flood"}, 0.45, [
        _a("TRY=X", "USD/TRY", "lira weakens on reconstruction costs (2023)", "+"),
        _a("TUR", "iShares Turkey", "local equities", "-"),
    ]),
    ("iran_gulf", "Iran / Persian Gulf", [(24, 40, 44, 63.5)], {"quake", "flood", "dust", "storm"}, 0.5, [
        _a("BZ=F", "Brent crude", "Gulf supply / Strait of Hormuz", "+"),
        _a("CL=F", "WTI crude", "Gulf supply risk", "+"),
    ]),
    ("italy_med", "Italy / Greece", [(35, 47, 6, 28.5)], {"quake", "volcano", "wildfire", "flood"}, 0.45, [
        _a("JETS", "Airlines ETF", "Etna / Stromboli ash closes airports", "-"),
        _a("EWI", "iShares Italy", "local equities", "-"),
        _a("EURUSD=X", "EUR/USD", "risk-off", "-"),
    ]),
    ("iceland", "Iceland", [(63, 67, -25, -13)], {"volcano", "quake"}, 0.3, [
        _a("JETS", "Airlines ETF", "North-Atlantic airspace closures (Eyjafjallajökull 2010)", "-"),
        _a("AAL", "Alcoa", "Icelandic smelter / grid", "~"),
    ]),
    ("europe", "Europe (Rhine / Danube / France)", [(43, 55, -5, 25)], {"drought", "flood", "heat"}, 0.3, [
        _a("ZW=F", "Wheat", "EU crop stress", "+"),
        _a("TTF=F", "Dutch TTF gas", "power demand / low river cooling water for nuclear", "+"),
        _a("BAS.DE", "BASF", "Rhine shipping drops at low water (Kaub gauge)", "-"),
    ]),
    ("brazil", "Brazil (coffee / sugar / soy belt)", [(-33, -5, -60, -35)], {"drought", "flood", "wildfire", "heat"}, 0.3, [
        _a("KC=F", "Coffee", "Minas Gerais is the world's arabica heartland", "+"),
        _a("SB=F", "Sugar", "Centre-South cane crop", "+"),
        _a("ZS=F", "Soybeans", "Brazil is the #1 soy exporter", "+"),
        _a("BRL=X", "USD/BRL", "terms-of-trade", "~"),
    ]),
    ("argentina", "Argentina (Pampas)", [(-40, -26, -66, -56)], {"drought", "flood", "heat"}, 0.3, [
        _a("ZS=F", "Soybeans", "soymeal / oil exports", "+"),
        _a("ZC=F", "Corn", "#3 corn exporter", "+"),
        _a("ARGT", "Global X Argentina", "local equities", "-"),
    ]),
    ("usmidwest", "US Corn Belt", [(36, 49, -104, -80.5)], {"drought", "flood", "heat", "storm"}, 0.3, [
        _a("ZC=F", "Corn", "Corn Belt yields", "+"),
        _a("ZS=F", "Soybeans", "Corn Belt yields", "+"),
        _a("ZW=F", "Wheat", "Plains winter wheat", "+"),
        _a("DE", "Deere", "farm income", "~"),
    ]),
    ("westafrica", "West Africa (Ivory Coast / Ghana)", [(3, 11, -9, 3)], {"drought", "flood", "heat"}, 0.25, [
        _a("CC=F", "Cocoa", "~60% of world cocoa comes from Ivory Coast + Ghana", "+"),
    ]),
    ("eastafrica", "East Africa (Ethiopia / Kenya)", [(-5, 15, 32, 48)], {"drought", "flood"}, 0.3, [
        _a("KC=F", "Coffee", "Ethiopian / Kenyan arabica", "+"),
    ]),
    ("seasia_palm", "Malaysia / Sumatra / Thailand", [(-6, 14, 95, 106)], {"flood", "drought", "wildfire", "storm", "cyclone"}, 0.3, [
        _a("FCPO", "Palm oil (Bursa)", "Malaysia + Indonesia grow 85% of palm oil", "+"),
        _a("ZL=F", "Soybean oil", "substitute for palm oil", "+"),
        _a("THD", "iShares Thailand", "local equities", "-"),
    ]),
    ("vietnam", "Vietnam", [(8, 23.5, 102, 110)], {"cyclone", "storm", "flood", "drought"}, 0.3, [
        _a("RC=F", "Robusta coffee", "Vietnam is the #1 robusta producer", "+"),
        _a("VNM", "VanEck Vietnam", "local equities", "-"),
    ]),
    ("nz", "New Zealand", [(-48, -34, 165, 179.9)], {"quake", "volcano", "cyclone", "storm"}, 0.45, [
        _a("NZDUSD=X", "NZD/USD", "kiwi on reconstruction (Christchurch 2011)", "-"),
        _a("ENZL", "iShares New Zealand", "local equities", "-"),
    ]),
    ("northsea", "North Sea", [(51, 62, -4, 9)], {"storm"}, 0.3, [
        _a("BZ=F", "Brent crude", "platform evacuations", "+"),
        _a("NG=F", "Natural gas", "Norwegian gas flows", "+"),
    ]),
    ("hawaii", "Hawaii", [(18, 23, -161, -154)], {"volcano", "quake", "cyclone", "storm", "wildfire"}, 0.3, [
        _a("ALK", "Alaska Air (Hawaiian)", "inter-island / tourism traffic", "-"),
    ]),
]

GLOBAL = [
    ("tsunami", lambda e: e.get("tsunami"), [_a("RNR", "RenaissanceRe", "tsunami flag: coastal property-cat exposure", "-"), _a("GC=F", "Gold", "risk-off haven bid on major disasters", "+")]),
    ("major_quake", lambda e: e["kind"] == "quake" and (e.get("mag") or 0) >= 7.0, [_a("GC=F", "Gold", "risk-off haven bid on M7+ quakes", "+"), _a("RNR", "RenaissanceRe", "reinsurance losses", "-")]),
    ("volcano_ash", lambda e: e["kind"] == "volcano", [_a("JETS", "Airlines ETF", "ash clouds close airspace", "-")]),
]


def severity(e: dict[str, Any]) -> float:
    """0..1, comparable across kinds."""
    return round(_severity(e), 3)


def _severity(e: dict[str, Any]) -> float:
    k = e.get("kind")
    if k == "quake":
        m = e.get("mag") or 0
        return max(0.0, min(1.0, (m - 4.0) / 4.0))  # M4 = 0, M6 = 0.5, M8 = 1
    if k in ("cyclone", "storm"):
        kt = e.get("kt")
        if kt:
            return max(0.2, min(1.0, kt / 140))  # TS 34 kt = 0.24, C3 96 kt = 0.69, C5 = 1
        return {"Red": 0.9, "Orange": 0.6}.get(e.get("alert") or "", 0.35)
    al = {"Red": 0.9, "Orange": 0.6, "Green": 0.3}.get(e.get("alert") or "", 0.3)
    if k == "volcano":
        al = max(al, 0.45)
    return al


def _in(lat: float, lon: float, boxes: list[tuple[float, float, float, float]]) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in boxes)


def assess(e: dict[str, Any]) -> dict[str, Any] | None:
    """Rule matches for one event; for storms the whole track (observed + forecast) is checked, so a storm heading for
    the Gulf flags Gulf assets before landfall."""
    sev = severity(e)
    pts = [(e["lat"], e["lon"])] + [(p["lat"], p["lon"]) for p in (e.get("track") or [])]
    regions, assets, seen = [], [], set()
    for rid, label, boxes, kinds, min_sev, rows in RULES:
        if e["kind"] not in kinds or sev < min_sev:
            continue
        if not any(_in(la, lo, boxes) for la, lo in pts):
            continue
        regions.append(label)
        for r in rows:
            if r["sym"] not in seen:
                seen.add(r["sym"])
                assets.append({**r, "region": label})
    for _gid, test, rows in GLOBAL:
        try:
            hit = test(e)
        except Exception:  # noqa: BLE001
            hit = False
        if hit:
            for r in rows:
                if r["sym"] not in seen:
                    seen.add(r["sym"])
                    assets.append({**r, "region": "global"})
    if not assets:
        return None
    return {"regions": regions, "assets": assets[:8], "sev": round(sev, 2)}


def rule_table() -> list[dict[str, Any]]:
    return [{"id": rid, "region": label, "boxes": boxes, "kinds": sorted(kinds), "min_sev": ms, "assets": rows} for rid, label, boxes, kinds, ms, rows in RULES]
