"""
Charge config.yaml et variables .env du projet Visio_Gemini.

Expose la grille macro (symboles, agents, timeframes) et résout les jobs
d'exécution à partir de la section ``run``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
ENV_PATH = ROOT_DIR / ".env"
DEFAULT_PLAYWRIGHT_BROWSERS_PATH = Path.home() / ".cache" / "ms-playwright"

# Grille macro complète : 4 symboles × 2 TF × 3 agents = 24 jobs max
MACRO_RUN: dict[str, list[str]] = {
    "agents": ["agent_a", "agent_b", "agent_c"],
    "symbols": ["TOTAL3ES", "OTHERS", "USDT.D", "BTCUSDT"],
    "timeframes": ["4h", "1D"],
}

# Estimation coût run macro — calibrée sur facturation Mammouth réelle (juin 2026)
# ~1460 tokens/job (1317 in + 153 out), ~0.00055 $/job (~0.00051 €/job)
MACRO_AVG_TOKENS_PER_JOB: int = 1465
MACRO_AVG_COST_USD_PER_JOB: float = 0.00055
MACRO_AVG_COST_EUR_PER_JOB: float = 0.00051


class LayoutNotReadyError(RuntimeError):
    """Layout TradingView absent ou non prêt pour un agent."""


@dataclass(frozen=True)
class CaptureJob:
    """Configuration d'une capture + analyse (un agent × symbole × timeframe)."""

    root_dir: Path
    storage_state_path: Path
    captures_dir: Path
    verdicts_dir: Path
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

    openai_api_key: str
    openai_base_url: str
    openai_model: str
    chart_vision_model: str

    @property
    def chart_url(self) -> str:
        return (
            f"https://www.tradingview.com/chart/{self.layout_id}/"
            f"?symbol={self.symbol}&interval={self.timeframe}"
        )

    @property
    def output_filename_prefix(self) -> str:
        return f"{self.symbol_key}_{self.timeframe_label}"


# Alias rétrocompatible pour capture.py / analyze.py
AppConfig = CaptureJob


@dataclass(frozen=True)
class ProjectConfig:
    """Configuration complète du projet (YAML + environnement)."""

    root_dir: Path
    storage_state_path: Path
    captures_dir: Path
    verdicts_dir: Path
    logs_dir: Path
    database_path: Path
    timeframes: dict[str, int]
    agents: dict[str, dict[str, Any]]
    symbols: dict[str, dict[str, Any]]
    run: dict[str, Any]
    capture: dict[str, Any]
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    chart_vision_model: str


def _timeframe_label(minutes: int) -> str:
    mapping = {
        1: "1m",
        5: "5m",
        15: "15m",
        30: "30m",
        60: "1h",
        240: "4h",
        1440: "1D",
        10080: "1W",
    }
    return mapping.get(minutes, f"{minutes}m")


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


def _playwright_has_browsers(path: Path) -> bool:
    """True si le dossier contient au moins un runtime Chromium Playwright."""
    return path.is_dir() and any(path.glob("chromium*"))


def ensure_playwright_browsers_path() -> Path | None:
    """
    Pointe PLAYWRIGHT_BROWSERS_PATH vers l'install VPS si le chemin courant est absent.

    Utile quand Cursor/sandbox impose un cache vide dans /tmp/cursor-sandbox-cache/...
    alors que Chromium est déjà installé dans ~/.cache/ms-playwright.
    """
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured:
        configured_path = Path(configured)
        if _playwright_has_browsers(configured_path):
            return configured_path

    fallback = DEFAULT_PLAYWRIGHT_BROWSERS_PATH
    if _playwright_has_browsers(fallback):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(fallback)
        return fallback

    return Path(configured) if configured else None


def load_project() -> ProjectConfig:
    """Charge .env puis config.yaml et retourne la configuration projet."""
    load_dotenv(ENV_PATH)
    ensure_playwright_browsers_path()
    yaml_cfg = _load_yaml(CONFIG_PATH)

    paths = yaml_cfg.get("paths") or {}
    if not isinstance(paths, dict):
        raise ValueError("config.yaml : section 'paths' invalide")

    return ProjectConfig(
        root_dir=ROOT_DIR,
        storage_state_path=_resolve_path(
            ROOT_DIR, str(paths.get("storage_state", "secrets/storage_state.json"))
        ),
        captures_dir=_resolve_path(ROOT_DIR, str(paths.get("captures", "captures/"))),
        verdicts_dir=_resolve_path(ROOT_DIR, str(paths.get("verdicts", "verdicts/"))),
        logs_dir=_resolve_path(ROOT_DIR, str(paths.get("logs", "logs/"))),
        database_path=_resolve_path(ROOT_DIR, str(paths.get("database", "data/visio_gemini.db"))),
        timeframes={
            str(k): int(v) for k, v in (yaml_cfg.get("timeframes") or {}).items()
        },
        agents=yaml_cfg.get("agents") or {},
        symbols=yaml_cfg.get("symbols") or {},
        run=yaml_cfg.get("run") or {},
        capture=yaml_cfg.get("capture") or {},
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.mammouth.ai/v1"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        chart_vision_model=os.getenv(
            "CHART_VISION_MODEL", "gemini-3.1-flash-lite-preview"
        ),
    )


def _get_agent_layout_id(agent_cfg: dict[str, Any]) -> str:
    layout = agent_cfg.get("layout") or {}
    if not isinstance(layout, dict):
        raise ValueError("config.yaml : section agent.layout invalide")

    status = str(layout.get("status", "pending"))
    layout_id = layout.get("id")

    if status != "ready" or not layout_id:
        name = agent_cfg.get("name", "agent")
        raise LayoutNotReadyError(
            f"Layout TradingView non prêt pour « {name} » "
            f"(status={status!r}, id={layout_id!r}). "
            "Créez le layout sur TradingView puis renseignez agents.<id>.layout.id"
        )
    return str(layout_id)


def estimate_macro_cost(job_count: int | None = None) -> tuple[int, float, float]:
    """
    Estime tokens, coût EUR et coût USD pour un run macro complet.

    Calibré sur facturation Mammouth réelle : ~1465 tokens/job, ~0.00055 $/job.
    """
    count = job_count if job_count is not None else _macro_job_count()
    total_tokens = count * MACRO_AVG_TOKENS_PER_JOB
    total_usd = round(count * MACRO_AVG_COST_USD_PER_JOB, 4)
    total_eur = round(count * MACRO_AVG_COST_EUR_PER_JOB, 4)
    return total_tokens, total_eur, total_usd


def _macro_job_count() -> int:
    """Calcule le nombre de jobs pour la grille MACRO_RUN."""
    return (
        len(MACRO_RUN["agents"])
        * len(MACRO_RUN["symbols"])
        * len(MACRO_RUN["timeframes"])
    )


def list_jobs(
    project: ProjectConfig | None = None,
    run_override: dict[str, list[str]] | None = None,
) -> list[CaptureJob]:
    """Résout une section run en liste de jobs exécutables."""
    project = project or load_project()

    run = run_override if run_override is not None else project.run
    agent_ids = list(run.get("agents") or [])
    symbol_keys = list(run.get("symbols") or [])
    tf_labels = list(run.get("timeframes") or [])

    if not agent_ids or not symbol_keys or not tf_labels:
        raise ValueError(
            "config.yaml : section run incomplète (agents, symbols, timeframes requis)"
        )

    capture_cfg = project.capture
    wait_ms = int(capture_cfg.get("wait_ms", 5_000))
    viewport = capture_cfg.get("viewport") or {"width": 1920, "height": 1080}
    headless = bool(capture_cfg.get("headless", True))
    if not isinstance(viewport, dict):
        viewport = {"width": 1920, "height": 1080}

    jobs: list[CaptureJob] = []

    for agent_id in agent_ids:
        agent_cfg = project.agents.get(agent_id)
        if not agent_cfg:
            raise ValueError(f"config.yaml : agent inconnu : {agent_id}")
        if not agent_cfg.get("enabled", False):
            raise LayoutNotReadyError(
                f"Agent « {agent_id} » désactivé (enabled: false). "
                "Activez-le après création du layout TradingView."
            )

        layout_id = _get_agent_layout_id(agent_cfg)
        agent_name = str(agent_cfg.get("name", agent_id))

        for symbol_key in symbol_keys:
            symbol_cfg = project.symbols.get(symbol_key)
            if not symbol_cfg:
                raise ValueError(f"config.yaml : symbole inconnu : {symbol_key}")

            allowed_tfs = set(symbol_cfg.get("timeframes") or [])
            tv_symbol = str(symbol_cfg.get("tv", symbol_key))

            for tf_label in tf_labels:
                if allowed_tfs and tf_label not in allowed_tfs:
                    raise ValueError(
                        f"config.yaml : timeframe {tf_label!r} non autorisé "
                        f"pour {symbol_key} (autorisés : {sorted(allowed_tfs)})"
                    )
                if tf_label not in project.timeframes:
                    raise ValueError(
                        f"config.yaml : timeframe inconnu : {tf_label!r}"
                    )

                tf_minutes = project.timeframes[tf_label]
                jobs.append(
                    CaptureJob(
                        root_dir=project.root_dir,
                        storage_state_path=project.storage_state_path,
                        captures_dir=project.captures_dir / agent_id,
                        verdicts_dir=project.verdicts_dir / agent_id,
                        logs_dir=project.logs_dir,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        symbol_key=symbol_key,
                        symbol=tv_symbol,
                        timeframe=tf_minutes,
                        timeframe_label=tf_label,
                        layout_id=layout_id,
                        capture_wait_ms=wait_ms,
                        capture_viewport={
                            "width": int(viewport.get("width", 1920)),
                            "height": int(viewport.get("height", 1080)),
                        },
                        capture_headless=headless,
                        openai_api_key=project.openai_api_key,
                        openai_base_url=project.openai_base_url,
                        openai_model=project.openai_model,
                        chart_vision_model=project.chart_vision_model,
                    )
                )

    return jobs


def load_settings() -> CaptureJob:
    """Retourne le premier job de la section run (compatibilité étape 2)."""
    jobs = list_jobs()
    if not jobs:
        raise ValueError("Aucun job résolu depuis config.yaml")
    return jobs[0]
