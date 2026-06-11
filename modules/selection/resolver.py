"""Résolution token / agent / timeframe → paramètres TradingView."""

from __future__ import annotations

from modules.config import load_app_config


class UnknownTokenError(ValueError):
    """Token absent de config.yaml — pas de résolution silencieuse."""


class LayoutNotReadyError(RuntimeError):
    """Layout TradingView absent ou non prêt pour un agent."""


def resolve_symbol_tv(token: str, *, allow_unknown: bool = False) -> str:
    """
    Résout une clé config vers un symbole TradingView.

    Par défaut lève ``UnknownTokenError`` si le token n'est pas dans config.
    ``allow_unknown=True`` (opt-in futur) : ``BINANCE:{token}``.
    """
    app = load_app_config()
    symbol = app.symbols.get(token)
    if symbol is not None:
        return symbol.tv
    if allow_unknown:
        return f"BINANCE:{token}"
    known = ", ".join(sorted(app.symbols))
    raise UnknownTokenError(
        f"Token inconnu : {token!r}. Tokens configurés : {known}. "
        "Pas de résolution silencieuse (évite capture/verdict bidon)."
    )


def resolve_layout(agent_id: str) -> str:
    """Retourne l'ID layout TradingView pour un agent."""
    app = load_app_config()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise ValueError(f"Agent inconnu : {agent_id!r}")

    if not agent.enabled:
        raise LayoutNotReadyError(
            f"Agent « {agent_id} » désactivé (enabled: false)."
        )

    layout_id = agent.layout.id
    status = agent.layout.status
    if status != "ready" or not layout_id:
        raise LayoutNotReadyError(
            f"Layout TradingView non prêt pour « {agent.name} » "
            f"(status={status!r}, id={layout_id!r})."
        )
    return layout_id


def resolve_timeframe_minutes(timeframe_label: str) -> int:
    """Label timeframe → minutes TradingView."""
    app = load_app_config()
    if timeframe_label not in app.timeframes:
        known = ", ".join(sorted(app.timeframes))
        raise ValueError(
            f"Timeframe inconnu : {timeframe_label!r} (connus : {known})"
        )
    return app.timeframes[timeframe_label]


def validate_timeframe_for_token(token: str, timeframe_label: str) -> None:
    """Vérifie que le timeframe est autorisé pour le token (si défini en config)."""
    resolve_timeframe_minutes(timeframe_label)
    app = load_app_config()
    symbol = app.symbols.get(token)
    if symbol is None:
        return
    allowed = symbol.timeframes
    if allowed and timeframe_label not in allowed:
        raise ValueError(
            f"Timeframe {timeframe_label!r} non autorisé pour {token!r} "
            f"(autorisés : {sorted(allowed)})"
        )
