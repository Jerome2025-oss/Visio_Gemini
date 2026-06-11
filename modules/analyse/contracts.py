"""Contrats universels du pipeline d'analyse Visio_Gemini."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

VALID_SOURCES: frozenset[str] = frozenset({
    "macro",
    "manual",
    "telegram",
    "webhook",
})


@dataclass(frozen=True)
class AnalysisRequest:
    """
    Objet universel produit par tout trigger, consommé par l'orchestrateur.

    - token : clé config (ex. "BTCUSDT", "USDT.D"), pas le symbole TradingView.
    - timeframe : label (ex. "4h", "1D").
    - Pas de chemins fichiers ni d'API keys — injectés par les modules à l'exécution.
    - metadata : données optionnelles (provider, request_id, etc.).
    """

    token: str
    timeframe: str
    layout_id: str
    agent_id: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"source invalide : {self.source!r} "
                f"(attendu : {sorted(VALID_SOURCES)})"
            )

    @classmethod
    def create(
        cls,
        *,
        token: str,
        timeframe: str,
        layout_id: str,
        agent_id: str,
        source: str,
        provider: str | None = None,
        request_id: str | None = None,
        **extra: Any,
    ) -> AnalysisRequest:
        """Factory avec request_id auto-généré."""
        meta: dict[str, Any] = dict(extra)
        if provider is not None:
            meta["provider"] = provider
        meta.setdefault("request_id", request_id or str(uuid4()))
        return cls(
            token=token,
            timeframe=timeframe,
            layout_id=layout_id,
            agent_id=agent_id,
            source=source,
            metadata=meta,
        )
