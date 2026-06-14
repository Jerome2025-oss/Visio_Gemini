"""Liste Bitunix Perp USDT — source Detecte_Pump_Bitunix_P/bitunix_perps.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from modules.config import load_app_config

TV_EXCHANGE = "BITUNIX"
TV_PERP_SUFFIX = ".P"


def normalize_token_key(raw: str) -> str:
    """
    Normalise une saisie dashboard : ``RENDER`` → ``RENDERUSDT``, ``BTC/USDT`` → ``BTCUSDT``.
    Laisse intactes les clés ``config.yaml`` (macro CRYPTOCAP, etc.).
    """
    key = raw.strip().upper().replace("/", "")
    if not key:
        return key
    if key in {"USDT.D", "USDTD"}:
        return "USDT.D"
    app = load_app_config()
    if key in app.symbols:
        return key
    if key.endswith(".D") or key.endswith("USDT"):
        return key
    return f"{key}USDT"


def bitunix_perps_path() -> Path:
    """Chemin du JSON Bitunix (refresh : Detecte_Pump_Bitunix_P/refresh_bitunix_perps.py)."""
    app = load_app_config()
    return app.paths.bitunix_perps


@lru_cache(maxsize=1)
def _load_symbols_cached(resolved_path: str, mtime_ns: int) -> frozenset[str]:
    _ = mtime_ns
    path = Path(resolved_path)
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    raw = payload.get("symbols") or []
    return frozenset(
        str(item).upper().strip()
        for item in raw
        if str(item).upper().strip().endswith("USDT")
    )


def get_bitunix_perp_symbols() -> frozenset[str]:
    """Ensemble des symboles Bitunix Futures USDT (ex. ``RENDERUSDT``)."""
    path = bitunix_perps_path()
    if not path.is_file():
        return frozenset()
    mtime_ns = path.stat().st_mtime_ns
    return _load_symbols_cached(str(path.resolve()), mtime_ns)


def is_bitunix_perp(token: str) -> bool:
    return normalize_token_key(token) in get_bitunix_perp_symbols()


def bitunix_to_tv_symbol(token: str) -> str:
    """``RENDERUSDT`` → ``BITUNIX:RENDERUSDT.P`` (perpétuel USDT sur TradingView)."""
    key = normalize_token_key(token)
    return f"{TV_EXCHANGE}:{key}{TV_PERP_SUFFIX}"
