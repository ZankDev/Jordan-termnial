"""Country reference table for the WORLD map: display name, a fallback centre, the main stock index (Yahoo symbol) and the
currency pair against USD. Symbols that Yahoo does not serve simply show no market data in the dashboard."""
from __future__ import annotations

# cc: (name, lat, lon, index symbol, fx symbol vs USD)
COUNTRIES: dict[str, tuple[str, float, float, str | None, str | None]] = {
    "US": ("United States", 38.0, -97.0, "^GSPC", None), "CA": ("Canada", 56.0, -106.0, "^GSPTSE", "USDCAD=X"), "MX": ("Mexico", 23.0, -102.0, "^MXX", "USDMXN=X"),
    "BR": ("Brazil", -10.0, -52.0, "^BVSP", "USDBRL=X"), "AR": ("Argentina", -34.0, -64.0, "^MERV", "USDARS=X"), "CL": ("Chile", -33.0, -71.0, "^IPSA", "USDCLP=X"),
    "GB": ("United Kingdom", 54.0, -2.0, "^FTSE", "USDGBP=X"), "IE": ("Ireland", 53.0, -8.0, "^ISEQ", "USDEUR=X"), "FR": ("France", 46.0, 2.0, "^FCHI", "USDEUR=X"),
    "DE": ("Germany", 51.0, 10.0, "^GDAXI", "USDEUR=X"), "IT": ("Italy", 42.5, 12.5, "FTSEMIB.MI", "USDEUR=X"), "ES": ("Spain", 40.0, -4.0, "^IBEX", "USDEUR=X"),
    "PT": ("Portugal", 39.5, -8.0, "PSI20.LS", "USDEUR=X"), "NL": ("Netherlands", 52.3, 5.3, "^AEX", "USDEUR=X"), "BE": ("Belgium", 50.6, 4.7, "^BFX", "USDEUR=X"),
    "CH": ("Switzerland", 46.8, 8.2, "^SSMI", "USDCHF=X"), "AT": ("Austria", 47.5, 14.5, "^ATX", "USDEUR=X"), "SE": ("Sweden", 62.0, 15.0, "^OMXS30", "USDSEK=X"),
    "NO": ("Norway", 61.0, 9.0, "OSEBX.OL", "USDNOK=X"), "DK": ("Denmark", 56.0, 10.0, "^OMXC25", "USDDKK=X"), "FI": ("Finland", 64.0, 26.0, "^OMXH25", "USDEUR=X"),
    "PL": ("Poland", 52.0, 19.0, "^WIG20", "USDPLN=X"), "CZ": ("Czechia", 49.8, 15.5, "^PX", "USDCZK=X"), "GR": ("Greece", 39.0, 22.0, "GD.AT", "USDEUR=X"),
    "TR": ("Turkey", 39.0, 35.0, "XU100.IS", "USDTRY=X"), "RU": ("Russia", 61.0, 100.0, "IMOEX.ME", "USDRUB=X"), "UA": ("Ukraine", 49.0, 32.0, None, "USDUAH=X"),
    "IL": ("Israel", 31.5, 34.9, "^TA125.TA", "USDILS=X"), "SA": ("Saudi Arabia", 24.0, 45.0, "^TASI.SR", "USDSAR=X"), "AE": ("United Arab Emirates", 24.0, 54.0, "^DFMGI", "USDAED=X"),
    "EG": ("Egypt", 26.5, 30.0, "^CASE30", "USDEGP=X"), "ZA": ("South Africa", -29.0, 24.0, "^J203.JO", "USDZAR=X"), "NG": ("Nigeria", 9.0, 8.0, None, "USDNGN=X"),
    "IN": ("India", 21.0, 78.0, "^BSESN", "USDINR=X"), "PK": ("Pakistan", 30.0, 70.0, "^KSE", "USDPKR=X"), "CN": ("China", 35.0, 104.0, "000001.SS", "USDCNY=X"),
    "HK": ("Hong Kong", 22.3, 114.2, "^HSI", "USDHKD=X"), "JP": ("Japan", 36.0, 138.0, "^N225", "USDJPY=X"), "KR": ("South Korea", 36.5, 127.8, "^KS11", "USDKRW=X"),
    "TW": ("Taiwan", 23.7, 121.0, "^TWII", "USDTWD=X"), "SG": ("Singapore", 1.35, 103.8, "^STI", "USDSGD=X"), "MY": ("Malaysia", 4.0, 102.0, "^KLSE", "USDMYR=X"),
    "ID": ("Indonesia", -2.0, 118.0, "^JKSE", "USDIDR=X"), "TH": ("Thailand", 15.0, 101.0, "^SET.BK", "USDTHB=X"), "PH": ("Philippines", 13.0, 122.0, "PSEI.PS", "USDPHP=X"),
    "VN": ("Vietnam", 16.0, 107.0, None, "USDVND=X"), "AU": ("Australia", -25.0, 134.0, "^AXJO", "USDAUD=X"), "NZ": ("New Zealand", -41.0, 174.0, "^NZ50", "USDNZD=X"),
}

# Natural Earth flags a few countries with ISO_A2 = -99; patch them by name
NAME_TO_CC = {"France": "FR", "Norway": "NO", "N. Cyprus": "CY", "Somaliland": "SO", "Kosovo": "XK"}

# cc: (capital, IANA time zone). Other countries fall back to an approximate offset from their longitude on the client.
CAPITALS: dict[str, tuple[str, str]] = {
    "US": ("Washington, D.C.", "America/New_York"), "CA": ("Ottawa", "America/Toronto"), "MX": ("Mexico City", "America/Mexico_City"), "BR": ("Brasilia", "America/Sao_Paulo"),
    "AR": ("Buenos Aires", "America/Argentina/Buenos_Aires"), "CL": ("Santiago", "America/Santiago"), "GB": ("London", "Europe/London"), "IE": ("Dublin", "Europe/Dublin"),
    "FR": ("Paris", "Europe/Paris"), "DE": ("Berlin", "Europe/Berlin"), "IT": ("Rome", "Europe/Rome"), "ES": ("Madrid", "Europe/Madrid"), "PT": ("Lisbon", "Europe/Lisbon"),
    "NL": ("Amsterdam", "Europe/Amsterdam"), "BE": ("Brussels", "Europe/Brussels"), "CH": ("Bern", "Europe/Zurich"), "AT": ("Vienna", "Europe/Vienna"), "SE": ("Stockholm", "Europe/Stockholm"),
    "NO": ("Oslo", "Europe/Oslo"), "DK": ("Copenhagen", "Europe/Copenhagen"), "FI": ("Helsinki", "Europe/Helsinki"), "PL": ("Warsaw", "Europe/Warsaw"), "CZ": ("Prague", "Europe/Prague"),
    "GR": ("Athens", "Europe/Athens"), "TR": ("Ankara", "Europe/Istanbul"), "RU": ("Moscow", "Europe/Moscow"), "UA": ("Kyiv", "Europe/Kyiv"), "IL": ("Jerusalem", "Asia/Jerusalem"),
    "SA": ("Riyadh", "Asia/Riyadh"), "AE": ("Abu Dhabi", "Asia/Dubai"), "EG": ("Cairo", "Africa/Cairo"), "ZA": ("Pretoria", "Africa/Johannesburg"), "NG": ("Abuja", "Africa/Lagos"),
    "IN": ("New Delhi", "Asia/Kolkata"), "PK": ("Islamabad", "Asia/Karachi"), "CN": ("Beijing", "Asia/Shanghai"), "HK": ("Hong Kong", "Asia/Hong_Kong"), "JP": ("Tokyo", "Asia/Tokyo"),
    "KR": ("Seoul", "Asia/Seoul"), "TW": ("Taipei", "Asia/Taipei"), "SG": ("Singapore", "Asia/Singapore"), "MY": ("Kuala Lumpur", "Asia/Kuala_Lumpur"), "ID": ("Jakarta", "Asia/Jakarta"),
    "TH": ("Bangkok", "Asia/Bangkok"), "PH": ("Manila", "Asia/Manila"), "VN": ("Hanoi", "Asia/Ho_Chi_Minh"), "AU": ("Canberra", "Australia/Sydney"), "NZ": ("Wellington", "Pacific/Auckland"),
}
