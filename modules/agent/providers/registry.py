"""Registry provider vision — Mammouth uniquement."""

from __future__ import annotations

from pathlib import Path

from modules.agent.providers.base import AnalyzeContext, VisionProvider, VisionResult
from modules.agent.providers.mammouth import MammouthProvider

_PROVIDERS: dict[str, type[VisionProvider]] = {
    "mammouth": MammouthProvider,
}


def get_provider(name: str = "mammouth") -> VisionProvider:
    """Instancie le provider Mammouth."""
    cls = _PROVIDERS.get(name)
    if cls is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Provider inconnu : {name!r} (connus : {known})")
    return cls()


def analyze_with_strategy(
    image_path: Path,
    prompt: str,
    *,
    context: AnalyzeContext | None = None,
    strategy: str | None = None,
    temperature: float | None = None,
) -> VisionResult:
    """Analyse vision via API Mammouth (paramètre strategy ignoré, rétrocompat)."""
    _ = strategy
    return get_provider("mammouth").analyze(
        image_path, prompt, context=context, temperature=temperature
    )


def analyze_multi_with_strategy(
    image_paths: list[Path],
    prompt: str,
    *,
    context: AnalyzeContext | None = None,
) -> VisionResult:
    """Analyse multi-image (1 seul appel) via API Mammouth — entonnoir Ichimoku."""
    provider = get_provider("mammouth")
    return provider.analyze_multi(image_paths, prompt, context=context)
