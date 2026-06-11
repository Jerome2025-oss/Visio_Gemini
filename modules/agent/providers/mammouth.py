"""
Provider vision Mammouth — wrapper mince autour de src/analyze.analyze_capture.

Dette technique : importe ``CaptureJob`` depuis ``src/settings.py`` et patche
temporairement ``src.analyze.get_prompt`` (référence utilisée par ``analyze_capture``)
pour injecter le prompt reçu en argument, sans modifier src/analyze.py.

``src/main.py`` appelle ``analyze_capture`` directement (``get_prompt`` non patché).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import src.analyze as legacy_analyze
from modules.agent.providers.base import AnalyzeContext, ProviderError, VisionProvider, VisionResult
from modules.capture.service import _build_capture_job
from modules.config import load_app_config
from src.analyze import analyze_capture


@contextmanager
def _inject_prompt(prompt: str) -> Iterator[None]:
    """
    Force analyze_capture à utiliser le prompt fourni par l'appelant.

    ``analyze_capture`` appelle ``get_prompt`` via l'import module-level de
    ``src.analyze`` — il faut patcher ``src.analyze.get_prompt``, pas ``src.prompts``.
    """
    original = legacy_analyze.get_prompt

    def _patched(_agent_id: str, _symbol_key: str, _timeframe_label: str) -> str:
        return prompt

    legacy_analyze.get_prompt = _patched
    try:
        yield
    finally:
        legacy_analyze.get_prompt = original


class MammouthProvider(VisionProvider):
    """Délègue à analyze_capture — logique OpenAI/Mammouth inchangée."""

    @property
    def name(self) -> str:
        return "mammouth"

    def analyze(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: AnalyzeContext | None = None,
    ) -> VisionResult:
        if not prompt.strip():
            raise ProviderError("Prompt vide", reason="invalid_input")
        if context is None:
            raise ProviderError(
                "AnalyzeContext requis pour Mammouth (agent_id, symbol_key, layout_id…)",
                reason="missing_context",
            )
        if not image_path.is_file():
            raise ProviderError(f"Image introuvable : {image_path}", reason="invalid_input")

        app = load_app_config()
        if not app.providers.openai_api_key:
            raise ProviderError("OPENAI_API_KEY absente du .env", reason="missing_api_key")

        job = _build_capture_job(
            symbol_tv=context.symbol_tv,
            timeframe_label=context.timeframe_label,
            layout_id=context.layout_id,
            agent_id=context.agent_id,
        )
        verdicts_dir = app.paths.verdicts / context.agent_id

        with _inject_prompt(prompt):
            try:
                verdict_path, meta = analyze_capture(job, image_path, verdicts_dir)
            except Exception as exc:
                raise ProviderError(str(exc), reason="api_error") from exc

        text = verdict_path.read_text(encoding="utf-8").strip()
        if not text:
            raise ProviderError("Réponse Mammouth vide", reason="empty_response")

        return VisionResult(
            text=text,
            provider="mammouth",
            model=str(meta.get("model", app.providers.chart_vision_model)),
            prompt_tokens=int(meta.get("prompt_tokens", 0)),
            completion_tokens=int(meta.get("completion_tokens", 0)),
            cost_usd=float(meta.get("cost_usd", 0.0)),
            cost_eur=float(meta.get("cost_eur", 0.0)),
            verdict_path=verdict_path,
            raw_meta=meta,
        )
