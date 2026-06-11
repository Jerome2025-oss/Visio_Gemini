"""
Wrapper capture TradingView — délègue à src/capture.capture_chart sans réécrire Playwright.

Dette technique : importe ``CaptureJob`` depuis ``src/settings.py`` (couplage legacy
temporaire, à découpler post-refonte).
"""

from __future__ import annotations

from pathlib import Path

from modules.config import load_app_config
from src.capture import capture_chart
from src.settings import CaptureJob


def _symbol_key_from_tv(symbol_tv: str) -> str:
    """Dérive une clé fichier depuis un symbole TV (ex. BINANCE:BTCUSDT → BTCUSDT)."""
    if ":" in symbol_tv:
        return symbol_tv.split(":", 1)[1]
    return symbol_tv


def _build_capture_job(
    *,
    symbol_tv: str,
    timeframe_label: str,
    layout_id: str,
    agent_id: str,
) -> CaptureJob:
    app = load_app_config()

    if timeframe_label not in app.timeframes:
        known = ", ".join(sorted(app.timeframes))
        raise ValueError(
            f"timeframe inconnu : {timeframe_label!r} (connus : {known})"
        )

    agent_cfg = app.agents.get(agent_id)
    agent_name = agent_cfg.name if agent_cfg else agent_id
    viewport = app.capture.viewport

    return CaptureJob(
        root_dir=app.root_dir,
        storage_state_path=app.paths.storage_state,
        captures_dir=app.paths.captures / agent_id,
        verdicts_dir=app.paths.verdicts / agent_id,
        logs_dir=app.paths.logs,
        agent_id=agent_id,
        agent_name=agent_name,
        symbol_key=_symbol_key_from_tv(symbol_tv),
        symbol=symbol_tv,
        timeframe=app.timeframes[timeframe_label],
        timeframe_label=timeframe_label,
        layout_id=layout_id,
        capture_wait_ms=app.capture.wait_ms,
        capture_viewport={"width": viewport.width, "height": viewport.height},
        capture_headless=app.capture.headless,
        openai_api_key=app.providers.openai_api_key,
        openai_base_url=app.providers.openai_base_url,
        openai_model=app.openai_model,
        chart_vision_model=app.providers.chart_vision_model,
    )


def capture(
    symbol_tv: str,
    timeframe: str,
    layout_id: str,
    agent_id: str,
    *,
    wait_ms: int | None = None,
) -> Path:
    """
    Capture un graphique TradingView et retourne le chemin du PNG.

    Args:
        symbol_tv: Symbole TradingView déjà résolu (ex. ``BINANCE:BTCUSDT``).
        timeframe: Label timeframe (ex. ``4h``) — résolu en minutes via config.
        layout_id: ID layout TradingView (ex. ``VLmoQO22``).
        agent_id: Identifiant agent (sous-dossier ``captures/{agent_id}/``).

    Returns:
        Chemin absolu du PNG généré.

    Note:
        La résolution token config → symbole TV relève de ``modules/selection/`` (ÉTAPE 4).
    """
    job = _build_capture_job(
        symbol_tv=symbol_tv,
        timeframe_label=timeframe,
        layout_id=layout_id,
        agent_id=agent_id,
    )
    return capture_chart(job, wait_ms=wait_ms)
