"""Maps a Polymarket buzzer asset slug (e.g. "btc") to the Binance USDT pair the
momo strategy reads for the underlying spot price / realized volatility — the
exact SPOT_SYMBOLS table from the source bot's src/buzzer.js, kept explicit rather
than derived (a guessed "{ASSET}USDT" pair is wrong for renamed/rebranded coins).

Not every buzzer asset has a Binance USDT pair (e.g. zec, hype seen live). Those
are honestly excluded — surfaced in the momo radar UI as "unsupported", never
silently dropped or backed by a fabricated volatility number.
"""
from __future__ import annotations

ASSET_TO_BINANCE: dict[str, str] = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "xrp": "XRPUSDT",
    "sol": "SOLUSDT",
    "doge": "DOGEUSDT",
    "bnb": "BNBUSDT",
}


def binance_symbol(asset: str) -> str | None:
    return ASSET_TO_BINANCE.get(asset.lower())


def supported_assets(assets: list[str]) -> tuple[list[str], list[str]]:
    """Splits a list of buzzer asset slugs into (supported, unsupported) — the
    latter has no Binance USDT pair to source volatility/spot from."""
    supported, unsupported = [], []
    for a in assets:
        (supported if a.lower() in ASSET_TO_BINANCE else unsupported).append(a)
    return supported, unsupported
