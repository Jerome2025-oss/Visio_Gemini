"""CaptureJob et construction des jobs capture TradingView."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from modules.config.loader import load_app_config


@dataclass(frozen=True)
class CaptureJob:
    """Configuration d'une capture (agent × symbole × timeframe)."""

    root_dir: Path
    storage_state_path: Path
    captures_dir: Path
    logs_dir: Path

    agent_id: str
    agent_name: str
    symbol_key: str
    symbol: str
    timeframe: int
    timeframe_label: str
    layout_id: str

    capture_wait_ms: int
    capture_viewport: dict[str, int]
    capture_headless: bool

    @property
    def chart_url(self) -> str:
        return (
            f"https://www.tradingview.com/chart/{self.layout_id}/"
            f"?symbol={self.symbol}&interval={self.timeframe}"
        )

    @property
    def output_filename_prefix(self) -> str:
        return f"{self.symbol_key}_{self.timeframe_label}"


def _symbol_key_from_tv(symbol_tv: str) -> str:
    if ":" in symbol_tv:
        return symbol_tv.split(":", 1)[1]
    return symbol_tv


def build_capture_job(
    *,
    symbol_tv: str,
    timeframe_label: str,
    layout_id: str,
    agent_id: str,
) -> CaptureJob:
    """Construit un ``CaptureJob`` depuis la config projet."""
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
    )
