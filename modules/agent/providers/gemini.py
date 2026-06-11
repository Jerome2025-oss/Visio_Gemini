"""Provider vision Google Gemini (SDK google-generativeai)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

from modules.agent.providers.base import (
    GEMINI_API_TIMEOUT_SECONDS,
    ProviderError,
    VisionProvider,
    VisionResult,
)
from modules.config import load_app_config

logger = logging.getLogger("visio_gemini.providers.gemini")

_EUR_RATE = 0.92
# Tarifs indicatifs Gemini ($/M tokens) — calibrage ÉTAPE 3
_GEMINI_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash-preview": (0.15, 0.60),
}


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _GEMINI_PRICING.get(model, (0.15, 0.60))
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000


def _estimate_cost_eur(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    return round(_estimate_cost_usd(model, prompt_tokens, completion_tokens) * _EUR_RATE, 6)


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    completion_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
    return prompt_tokens, completion_tokens


def _map_google_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, google_exceptions.DeadlineExceeded):
        return ProviderError(
            f"Timeout Gemini ({GEMINI_API_TIMEOUT_SECONDS}s dépassé)",
            reason="timeout",
        )
    if isinstance(exc, google_exceptions.Unauthenticated):
        return ProviderError(str(exc), reason="auth")
    if isinstance(exc, google_exceptions.PermissionDenied):
        return ProviderError(str(exc), reason="auth")
    if isinstance(exc, google_exceptions.ResourceExhausted):
        return ProviderError(str(exc), reason="quota")
    if isinstance(exc, google_exceptions.NotFound):
        return ProviderError(str(exc), reason="model_not_found")
    if isinstance(exc, google_exceptions.ServiceUnavailable):
        return ProviderError(str(exc), reason="server_error")
    if isinstance(exc, google_exceptions.InternalServerError):
        return ProviderError(str(exc), reason="server_error")
    if isinstance(exc, google_exceptions.TooManyRequests):
        return ProviderError(str(exc), reason="quota")
    code = getattr(exc, "code", None)
    if code in {429, 500, 502, 503, 504}:
        return ProviderError(str(exc), reason="http_error")
    return ProviderError(str(exc), reason="api_error")


class GeminiProvider(VisionProvider):
    """Analyse vision via SDK Google Generative AI."""

    def __init__(self, model: str | None = None) -> None:
        app = load_app_config()
        self._model_name = model or app.providers.gemini.model
        self._api_key = app.providers.google_api_key

    @property
    def name(self) -> str:
        return "gemini"

    def analyze(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: AnalyzeContext | None = None,
    ) -> VisionResult:
        del context  # non requis pour Gemini
        if not self._api_key:
            raise ProviderError(
                "GOOGLE_API_KEY absente du .env",
                reason="missing_api_key",
            )
        if not image_path.is_file():
            raise ProviderError(f"Image introuvable : {image_path}", reason="invalid_input")

        print(f"🧠 Provider Gemini   : {self._model_name}")
        print(f"⏱️  Timeout           : {GEMINI_API_TIMEOUT_SECONDS}s")

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model_name)
        image_bytes = image_path.read_bytes()

        try:
            print("📡 Appel Gemini en cours...")
            response = model.generate_content(
                [
                    prompt,
                    {"mime_type": "image/png", "data": image_bytes},
                ],
                request_options={"timeout": GEMINI_API_TIMEOUT_SECONDS},
            )
        except Exception as exc:
            raise _map_google_exception(exc) from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ProviderError("Réponse Gemini vide", reason="empty_response")

        prompt_tokens, completion_tokens = _usage_tokens(response)
        cost_usd = round(
            _estimate_cost_usd(self._model_name, prompt_tokens, completion_tokens), 6
        )
        cost_eur = _estimate_cost_eur(self._model_name, prompt_tokens, completion_tokens)

        print(
            f"💸 Coût estimé      : {cost_eur:.6f} € "
            f"({prompt_tokens} in + {completion_tokens} out)"
        )

        return VisionResult(
            text=text,
            provider="gemini",
            model=self._model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
            raw_meta={"timeout_seconds": GEMINI_API_TIMEOUT_SECONDS},
        )
