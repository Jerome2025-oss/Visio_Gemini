"""Interface capture TradingView — délègue à tv_capture."""

from __future__ import annotations

from pathlib import Path

from modules.capture.tv_capture import capture_chart
from modules.config.jobs import build_capture_job


def capture(
    symbol_tv: str,
    timeframe: str,
    layout_id: str,
    agent_id: str,
    *,
    wait_ms: int | None = None,
) -> Path:
    """
    Capture un graphique TradingView et retourne le chemin du PNG généré.

    Args:
        symbol_tv: Symbole TradingView déjà résolu (ex. ``BINANCE:BTCUSDT``).
        timeframe: Label timeframe (ex. ``4h``).
        layout_id: ID layout TradingView.
        agent_id: Sous-dossier ``captures/{agent_id}/``.
    """
    job = build_capture_job(
        symbol_tv=symbol_tv,
        timeframe_label=timeframe,
        layout_id=layout_id,
        agent_id=agent_id,
    )
    return capture_chart(job, wait_ms=wait_ms)
