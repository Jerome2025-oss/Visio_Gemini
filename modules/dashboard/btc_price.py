"""Prix spot BTC/ETH Bitunix Perp — cache court pour l'en-tête dashboard."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("visio_gemini.dashboard.btc_price")

BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
SPOT_SYMBOLS = (BTC_SYMBOL, ETH_SYMBOL)
_SYMBOL_KEYS = {BTC_SYMBOL: "btc", ETH_SYMBOL: "eth"}
BITUNIX_TICKERS_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
_CACHE_TTL_S = 30

_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _empty_pair(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "price": None, "price_display": "—"}


def _format_price_fr(price: float) -> str:
    """Affichage entier FR : ``65 712``."""
    whole = str(int(round(price)))
    grouped: list[str] = []
    while whole:
        grouped.insert(0, whole[-3:])
        whole = whole[:-3]
    return " ".join(grouped)


def _parse_ticker(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = item.get("symbol")
    if not symbol:
        return None
    raw = item.get("lastPrice") or item.get("markPrice") or item.get("last")
    if raw is None:
        return None
    price = float(raw)
    return {
        "symbol": symbol,
        "price": price,
        "price_display": _format_price_fr(price),
    }


def get_market_spot() -> dict[str, Any]:
    """Derniers prix BTCUSDT et ETHUSDT (Bitunix futures), cache 30 s."""
    now = time.time()
    cached = _cache.get("data")
    if cached and now - float(_cache.get("ts") or 0) < _CACHE_TTL_S:
        return dict(cached)

    out: dict[str, Any] = {
        "btc": _empty_pair(BTC_SYMBOL),
        "eth": _empty_pair(ETH_SYMBOL),
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                BITUNIX_TICKERS_URL,
                params={"symbols": ",".join(SPOT_SYMBOLS)},
            )
            resp.raise_for_status()
            payload = resp.json()
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            parsed = _parse_ticker(item)
            if not parsed:
                continue
            key = _SYMBOL_KEYS.get(parsed["symbol"])
            if key:
                out[key] = parsed
        if out["btc"]["price"] is not None or out["eth"]["price"] is not None:
            _cache["ts"] = now
            _cache["data"] = out
            return out
    except Exception as exc:
        logger.debug("Prix marché indisponibles : %s", exc)

    if cached:
        return dict(cached)
    return out


def get_btc_spot() -> dict[str, Any]:
    """Compatibilité — retourne uniquement la paire BTC."""
    return dict(get_market_spot()["btc"])
