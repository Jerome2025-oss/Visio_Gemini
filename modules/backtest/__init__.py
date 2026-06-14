"""Backtests complémentaires (sans modifier le backtest historique existant)."""

from modules.backtest.latency_sim import (
    DEFAULT_LATENCY_SECONDS,
    PRICE_GRANULARITY,
    compare_latency_modes,
)

__all__ = [
    "DEFAULT_LATENCY_SECONDS",
    "PRICE_GRANULARITY",
    "compare_latency_modes",
]
