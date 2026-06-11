"""Registry providers vision + stratégie gemini_first avec fallback loggé."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from modules.agent.providers.base import (
    AnalyzeContext,
    ProviderError,
    VisionProvider,
    VisionResult,
)
from modules.agent.providers.gemini import GeminiProvider
from modules.agent.providers.mammouth import MammouthProvider
from modules.config import load_app_config

logger = logging.getLogger("visio_gemini.providers.registry")

_PROVIDERS: dict[str, type[VisionProvider]] = {
    "gemini": GeminiProvider,
    "mammouth": MammouthProvider,
}


def get_provider(name: str) -> VisionProvider:
    """Instancie un provider par nom (``gemini`` | ``mammouth``)."""
    cls = _PROVIDERS.get(name)
    if cls is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Provider inconnu : {name!r} (connus : {known})")
    return cls()


def _try_gemini(
    image_path: Path,
    prompt: str,
    *,
    context: AnalyzeContext | None,
    primary_model: str,
    fallback_model: str,
) -> tuple[VisionResult | None, str | None]:
    """Essaie primary_model puis fallback_model. Retourne (result, error_summary)."""
    last_error: str | None = None
    for model in (primary_model, fallback_model):
        if model == fallback_model and model == primary_model:
            continue
        try:
            provider = GeminiProvider(model=model)
            result = provider.analyze(image_path, prompt, context=context)
            if model == fallback_model and model != primary_model:
                result = replace(
                    result,
                    raw_meta={**result.raw_meta, "gemini_model_fallback": True},
                )
                logger.warning(
                    "Gemini modèle principal indisponible — utilisé fallback_model=%s",
                    model,
                )
            return result, None
        except ProviderError as exc:
            last_error = f"{model}: [{exc.reason}] {exc}"
            logger.warning("Échec Gemini (%s) : %s", model, exc)
    return None, last_error


def analyze_with_strategy(
    image_path: Path,
    prompt: str,
    *,
    context: AnalyzeContext | None = None,
    strategy: str | None = None,
) -> VisionResult:
    """
    Analyse avec stratégie configurée (gemini_first | gemini_only | mammouth_only).

    Fallback Gemini → Mammouth loggé en WARNING avec raison explicite.
    """
    app = load_app_config()
    effective = strategy or app.providers.strategy

    if effective == "mammouth_only":
        return get_provider("mammouth").analyze(image_path, prompt, context=context)

    if effective == "gemini_only":
        primary = app.providers.gemini.model
        fallback = app.providers.gemini.fallback_model
        result, err = _try_gemini(
            image_path,
            prompt,
            context=context,
            primary_model=primary,
            fallback_model=fallback,
        )
        if result is None:
            raise ProviderError(
                f"Gemini only — tous les modèles ont échoué : {err}",
                reason="gemini_failed",
            )
        return result

    # gemini_first (défaut)
    primary = app.providers.gemini.model
    fallback = app.providers.gemini.fallback_model
    gemini_result, gemini_error = _try_gemini(
        image_path,
        prompt,
        context=context,
        primary_model=primary,
        fallback_model=fallback,
    )
    if gemini_result is not None:
        return gemini_result

    logger.warning(
        "⚠️  FALLBACK Gemini → Mammouth déclenché | cause=%s",
        gemini_error or "erreur inconnue",
    )
    mammouth_result = get_provider("mammouth").analyze(
        image_path, prompt, context=context
    )
    return replace(
        mammouth_result,
        raw_meta={
            **mammouth_result.raw_meta,
            "fallback_used": True,
            "primary_error": gemini_error,
            "primary_provider": "gemini",
        },
    )
