"""Dataclasses typées pour config.yaml étendu."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PathsConfig:
    storage_state: Path
    captures: Path
    logs: Path


@dataclass(frozen=True)
class ViewportConfig:
    width: int
    height: int


@dataclass(frozen=True)
class CaptureConfig:
    viewport: ViewportConfig
    wait_ms: int
    headless: bool


@dataclass(frozen=True)
class SymbolConfig:
    tv: str
    role: str
    timeframes: tuple[str, ...]


@dataclass(frozen=True)
class AgentLayoutConfig:
    id: str
    status: str


@dataclass(frozen=True)
class AgentConfig:
    name: str
    indicators: tuple[str, ...]
    layout: AgentLayoutConfig
    verdict_scale: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class RunConfig:
    agents: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    priority_timeframes: tuple[str, ...]


@dataclass(frozen=True)
class MacroConfig:
    """Grille du run automatique macro uniquement (pas la sélection manuelle dashboard)."""

    agents: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]


@dataclass(frozen=True)
class MammouthProviderConfig:
    model_env: str
    base_url_env: str
    api_key_env: str


@dataclass(frozen=True)
class ProvidersConfig:
    mammouth: MammouthProviderConfig
    openai_api_key: str
    openai_base_url: str
    chart_vision_model: str


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool
    host: str
    port: int


@dataclass(frozen=True)
class AppConfig:
    """Configuration complète projet (YAML + variables d'environnement résolues)."""

    root_dir: Path
    paths: PathsConfig
    timeframes: dict[str, int]
    symbols: dict[str, SymbolConfig]
    agents: dict[str, AgentConfig]
    run: RunConfig
    capture: CaptureConfig
    macro: MacroConfig
    providers: ProvidersConfig
    dashboard: DashboardConfig
    openai_model: str
    playwright_browsers_path: str

    def raw_agents(self) -> dict[str, dict[str, Any]]:
        """Dict agents brut (rétrocompat lecture seule)."""
        return {
            agent_id: {
                "name": agent.name,
                "indicators": list(agent.indicators),
                "layout": {
                    "id": agent.layout.id,
                    "status": agent.layout.status,
                },
                "verdict_scale": list(agent.verdict_scale),
                "enabled": agent.enabled,
            }
            for agent_id, agent in self.agents.items()
        }

    def raw_symbols(self) -> dict[str, dict[str, Any]]:
        """Dict symboles brut (rétrocompat lecture seule)."""
        return {
            symbol_key: {
                "tv": symbol.tv,
                "role": symbol.role,
                "timeframes": list(symbol.timeframes),
            }
            for symbol_key, symbol in self.symbols.items()
        }
