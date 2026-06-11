"""Construction de listes ``AnalysisRequest`` (macro, manual, run)."""

from __future__ import annotations

from itertools import product

from modules.analyse.contracts import AnalysisRequest
from modules.config import load_app_config
from modules.selection.resolver import (
    resolve_layout,
    resolve_symbol_tv,
    validate_timeframe_for_token,
)


def build_analysis_request(
    token: str,
    timeframe: str,
    agent_id: str,
    source: str,
    *,
    layout_id: str | None = None,
    allow_unknown_token: bool = False,
    **metadata: object,
) -> AnalysisRequest:
    """Construit une requête unique avec résolution layout + validation token/TF."""
    resolve_symbol_tv(token, allow_unknown=allow_unknown_token)
    validate_timeframe_for_token(token, timeframe)
    resolved_layout = layout_id or resolve_layout(agent_id)
    return AnalysisRequest.create(
        token=token,
        timeframe=timeframe,
        layout_id=resolved_layout,
        agent_id=agent_id,
        source=source,
        **metadata,
    )


def build_manual_requests(
    token: str,
    timeframe: str,
    *,
    agents: list[str] | None = None,
    source: str = "manual",
    allow_unknown_token: bool = False,
) -> list[AnalysisRequest]:
    """Requêtes manuelles : 1 token × 1 TF × N agents."""
    app = load_app_config()
    agent_ids = agents if agents is not None else list(app.agents.keys())
    return [
        build_analysis_request(
            token,
            timeframe,
            agent_id,
            source,
            allow_unknown_token=allow_unknown_token,
        )
        for agent_id in agent_ids
    ]


def build_macro_requests() -> list[AnalysisRequest]:
    """Grille macro : config.yaml → macro (4 symboles × 2 TF × 3 agents)."""
    app = load_app_config()
    macro = app.macro
    requests: list[AnalysisRequest] = []
    for token, timeframe, agent_id in product(
        macro.symbols, macro.timeframes, macro.agents
    ):
        requests.append(
            build_analysis_request(
                token,
                timeframe,
                agent_id,
                source="macro",
            )
        )
    return requests


def build_from_run_section() -> list[AnalysisRequest]:
    """Requêtes depuis la section ``run`` de config.yaml (test rapide)."""
    app = load_app_config()
    run = app.run
    requests: list[AnalysisRequest] = []
    for token, timeframe, agent_id in product(
        run.symbols, run.timeframes, run.agents
    ):
        requests.append(
            build_analysis_request(
                token,
                timeframe,
                agent_id,
                source="manual",
            )
        )
    return requests
