"""Résolution token / agent / timeframe → paramètres TradingView."""

from __future__ import annotations

from modules.config import load_app_config
from modules.selection.bitunix_symbols import (
    bitunix_to_tv_symbol,
    get_bitunix_perp_symbols,
    is_bitunix_perp,
    normalize_token_key,
)


class UnknownTokenError(ValueError):
    """Token absent de config.yaml et de la liste Bitunix Perp USDT."""


class LayoutNotReadyError(RuntimeError):
    """Layout TradingView absent ou non prêt pour un agent."""


def resolve_symbol_tv(token: str, *, allow_unknown: bool = False) -> str:
    """
    Résout une clé vers un symbole TradingView.

    1. Clés ``config.yaml`` (macro CRYPTOCAP, etc.) → ``symbol.tv`` explicite.
    2. Symboles Bitunix Perp USDT (``bitunix_perps.json``) → ``BITUNIX:XXXUSDT.P``.
    3. ``allow_unknown=True`` (opt-in) : ``BITUNIX:{token}.P`` sans validation liste.
    """
    key = normalize_token_key(token)
    app = load_app_config()

    symbol = app.symbols.get(key)
    if symbol is not None:
        return symbol.tv

    if is_bitunix_perp(key):
        return bitunix_to_tv_symbol(key)

    if allow_unknown:
        return bitunix_to_tv_symbol(key)

    known_config = ", ".join(sorted(app.symbols))
    perp_count = len(get_bitunix_perp_symbols())
    raise UnknownTokenError(
        f"Token inconnu : {key!r}. "
        f"Config : {known_config}. "
        f"Bitunix Perp USDT : {perp_count} symboles (fichier {app.paths.bitunix_perps}). "
        "Pas de résolution silencieuse."
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
    """Vérifie le timeframe (config symbole ou liste Bitunix → timeframes globaux)."""
    resolve_timeframe_minutes(timeframe_label)
    key = normalize_token_key(token)
    app = load_app_config()

    if is_bitunix_perp(key):
        return

    symbol = app.symbols.get(key)
    if symbol is None:
        return
    allowed = symbol.timeframes
    if allowed and timeframe_label not in allowed:
        raise ValueError(
            f"Timeframe {timeframe_label!r} non autorisé pour {key!r} "
            f"(autorisés : {sorted(allowed)})"
        )
