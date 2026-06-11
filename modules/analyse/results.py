"""Résultats structurés du pipeline d'orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.agent.providers.base import VisionResult
from modules.analyse.contracts import AnalysisRequest


@dataclass
class AnalysisResult:
    """Sortie d'un job orchestrateur (succès ou échec isolé)."""

    request: AnalysisRequest
    success: bool
    png_path: Path | None = None
    vision: VisionResult | None = None
    parsed: dict[str, Any] | None = None
    verdict_text: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_valid_format(self) -> bool:
        """True si le résultat est exploitable : verdict parsé OU erreur documentée."""
        if self.error:
            return True
        if self.parsed is not None:
            return True
        return False
