"""Charge config.yaml étendu et variables .env — loader partagé modules/."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from modules.config.models import (
    AgentConfig,
    AgentLayoutConfig,
    AppConfig,
    CaptureConfig,
    DashboardConfig,
    MacroConfig,
    MammouthProviderConfig,
    PathsConfig,
    ProvidersConfig,
    RunConfig,
    SymbolConfig,
    ViewportConfig,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
ENV_PATH = ROOT_DIR / ".env"
DEFAULT_PLAYWRIGHT_BROWSERS_PATH = Path.home() / ".cache" / "ms-playwright"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"config.yaml introuvable : {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config.yaml invalide (dict attendu) : {path}")
    return data


def _resolve_path(root: Path, rel: str) -> Path:
    return (root / rel).resolve()


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item) for item in value)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _parse_paths(root: Path, raw: dict[str, Any]) -> PathsConfig:
    return PathsConfig(
        storage_state=_resolve_path(
            root, str(raw.get("storage_state", "secrets/storage_state.json"))
        ),
        captures=_resolve_path(root, str(raw.get("captures", "captures/"))),
        logs=_resolve_path(root, str(raw.get("logs", "logs/"))),
        bitunix_perps=_resolve_path(
            root,
            str(
                raw.get(
                    "bitunix_perps",
                    "../Detecte_Pump_Bitunix_P/bitunix_perps.json",
                )
            ),
        ),
    )


def _parse_capture(raw: dict[str, Any]) -> CaptureConfig:
    viewport_raw = raw.get("viewport") or {}
    if not isinstance(viewport_raw, dict):
        viewport_raw = {}
    return CaptureConfig(
        viewport=ViewportConfig(
            width=int(viewport_raw.get("width", 1920)),
            height=int(viewport_raw.get("height", 1080)),
        ),
        wait_ms=int(raw.get("wait_ms", 5_000)),
        headless=bool(raw.get("headless", True)),
    )


def _parse_symbols(raw: dict[str, Any]) -> dict[str, SymbolConfig]:
    symbols: dict[str, SymbolConfig] = {}
    for symbol_key, symbol_cfg in (raw or {}).items():
        if not isinstance(symbol_cfg, dict):
            continue
        symbols[str(symbol_key)] = SymbolConfig(
            tv=str(symbol_cfg.get("tv", symbol_key)),
            role=str(symbol_cfg.get("role", "")),
            timeframes=_as_tuple(symbol_cfg.get("timeframes")),
        )
    return symbols


def _parse_agents(raw: dict[str, Any]) -> dict[str, AgentConfig]:
    agents: dict[str, AgentConfig] = {}
    for agent_id, agent_cfg in (raw or {}).items():
        if not isinstance(agent_cfg, dict):
            continue
        layout_raw = agent_cfg.get("layout") or {}
        if not isinstance(layout_raw, dict):
            layout_raw = {}
        agents[str(agent_id)] = AgentConfig(
            name=str(agent_cfg.get("name", agent_id)),
            indicators=_as_tuple(agent_cfg.get("indicators")),
            layout=AgentLayoutConfig(
                id=str(layout_raw.get("id", "")),
                status=str(layout_raw.get("status", "pending")),
            ),
            verdict_scale=_as_tuple(agent_cfg.get("verdict_scale")),
            enabled=bool(agent_cfg.get("enabled", False)),
        )
    return agents


def _parse_run(raw: dict[str, Any]) -> RunConfig:
    return RunConfig(
        agents=_as_tuple(raw.get("agents")),
        symbols=_as_tuple(raw.get("symbols")),
        timeframes=_as_tuple(raw.get("timeframes")),
        priority_timeframes=_as_tuple(raw.get("priority_timeframes")),
    )


def _parse_macro(raw: dict[str, Any] | None) -> MacroConfig:
    data = raw or {}
    return MacroConfig(
        agents=_as_tuple(data.get("agents"))
        or ("agent_Ichimoku", "agent_BB", "agent_EMA"),
        symbols=_as_tuple(data.get("symbols"))
        or ("TOTAL3ES", "OTHERS", "USDT.D", "BTCUSDT"),
        timeframes=_as_tuple(data.get("timeframes")) or ("4h", "1D"),
    )


def _parse_providers(raw: dict[str, Any] | None) -> ProvidersConfig:
    data = raw or {}
    mammouth_raw = data.get("mammouth") or {}
    if not isinstance(mammouth_raw, dict):
        mammouth_raw = {}

    mammouth_key_env = str(mammouth_raw.get("api_key_env", "OPENAI_API_KEY"))
    mammouth_model_env = str(mammouth_raw.get("model_env", "CHART_VISION_MODEL"))
    mammouth_base_env = str(mammouth_raw.get("base_url_env", "OPENAI_BASE_URL"))

    return ProvidersConfig(
        mammouth=MammouthProviderConfig(
            model_env=mammouth_model_env,
            base_url_env=mammouth_base_env,
            api_key_env=mammouth_key_env,
        ),
        openai_api_key=_env(mammouth_key_env, ""),
        openai_base_url=_env(mammouth_base_env, "https://api.mammouth.ai/v1"),
        chart_vision_model=_env(mammouth_model_env, "gemini-3.1-flash-lite-preview"),
    )


def _parse_dashboard(raw: dict[str, Any] | None) -> DashboardConfig:
    data = raw or {}
    return DashboardConfig(
        enabled=bool(data.get("enabled", True)),
        host=str(data.get("host", "0.0.0.0")),
        port=int(data.get("port", 8004)),
    )


def load_app_config(
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> AppConfig:
    """Charge .env puis config.yaml et retourne la configuration typée."""
    root = ROOT_DIR
    cfg_path = config_path or CONFIG_PATH
    dotenv_path = env_path or ENV_PATH

    load_dotenv(dotenv_path)
    yaml_cfg = _load_yaml(cfg_path)

    paths_raw = yaml_cfg.get("paths") or {}
    if not isinstance(paths_raw, dict):
        raise ValueError("config.yaml : section 'paths' invalide")

    timeframes_raw = yaml_cfg.get("timeframes") or {}
    timeframes = {str(k): int(v) for k, v in timeframes_raw.items()}

    return AppConfig(
        root_dir=root,
        paths=_parse_paths(root, paths_raw),
        timeframes=timeframes,
        symbols=_parse_symbols(yaml_cfg.get("symbols") or {}),
        agents=_parse_agents(yaml_cfg.get("agents") or {}),
        run=_parse_run(yaml_cfg.get("run") or {}),
        capture=_parse_capture(yaml_cfg.get("capture") or {}),
        macro=_parse_macro(yaml_cfg.get("macro")),
        providers=_parse_providers(yaml_cfg.get("providers")),
        dashboard=_parse_dashboard(yaml_cfg.get("dashboard")),
        openai_model=_env("OPENAI_MODEL", "gpt-5.4-mini"),
        playwright_browsers_path=_env(
            "PLAYWRIGHT_BROWSERS_PATH", str(DEFAULT_PLAYWRIGHT_BROWSERS_PATH)
        ),
    )
