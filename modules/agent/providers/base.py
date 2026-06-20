"""Contrat commun des providers vision."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class AnalyzeContext:
    """Contexte métier requis par Mammouth (wrapper analyze_capture)."""

    agent_id: str
    symbol_key: str
    symbol_tv: str
    timeframe_label: str
    layout_id: str


@dataclass(frozen=True)
class VisionResult:
    """Réponse brute d'un provider — parsing via modules.agent.verdict_parser."""

    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_eur: float
    verdict_path: Path | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Erreur provider vision (API Mammouth)."""

    def __init__(self, message: str, *, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


class VisionProvider(ABC):
    """Interface provider vision."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifiant court du provider (``mammouth``)."""

    @abstractmethod
    def analyze(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: AnalyzeContext | None = None,
        temperature: float | None = None,
    ) -> VisionResult:
        """Analyse une image avec le prompt fourni."""
