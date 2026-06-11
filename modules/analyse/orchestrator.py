"""
Orchestrateur pipeline : AnalysisRequest → capture → vision → parse_verdict.

Séquentiel, jobs isolés (un échec n'arrête pas le batch).
Résultats en mémoire (AnalysisResult) — pas de persistance disque.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from modules.agent import load_prompt
from modules.agent.providers import AnalyzeContext, analyze_with_strategy
from modules.analyse.contracts import AnalysisRequest
from modules.analyse.results import AnalysisResult
from modules.capture import capture
from modules.selection import resolve_symbol_tv
from modules.agent.verdict_parser import parse_verdict

logger = logging.getLogger("visio_gemini.orchestrator")


def _enrich_metadata(request: AnalysisRequest, vision_meta: dict) -> dict:
    return {
        **request.metadata,
        **vision_meta,
    }


def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    """
    Exécute un job complet pour une ``AnalysisRequest``.

    Retourne toujours un ``AnalysisResult`` (ne propage pas les exceptions).
    """
    try:
        symbol_tv = resolve_symbol_tv(request.token)
        png_path = capture(
            symbol_tv,
            request.timeframe,
            request.layout_id,
            request.agent_id,
        )
        prompt = load_prompt(request.agent_id, request.token, request.timeframe)
        context = AnalyzeContext(
            agent_id=request.agent_id,
            symbol_key=request.token,
            symbol_tv=symbol_tv,
            timeframe_label=request.timeframe,
            layout_id=request.layout_id,
        )
        vision = analyze_with_strategy(png_path, prompt, context=context)
        parsed = parse_verdict(request.agent_id, vision.text, request.token)

        meta = _enrich_metadata(
            request,
            {
                "provider": vision.provider,
                "model": vision.model,
                "cost_eur": vision.cost_eur,
                "cost_usd": vision.cost_usd,
                "fallback_used": vision.raw_meta.get("fallback_used", False),
                "primary_error": vision.raw_meta.get("primary_error"),
            },
        )
        enriched_request = replace(request, metadata=meta)

        return AnalysisResult(
            request=enriched_request,
            success=True,
            png_path=png_path,
            vision=vision,
            parsed=dict(parsed),
            verdict_text=vision.text,
            metadata=meta,
        )
    except Exception as exc:
        logger.warning(
            "Job échoué %s/%s/%s : %s",
            request.token,
            request.timeframe,
            request.agent_id,
            exc,
        )
        return AnalysisResult(
            request=request,
            success=False,
            error=str(exc),
            metadata=dict(request.metadata),
        )


def run_batch(requests: list[AnalysisRequest]) -> list[AnalysisResult]:
    """
    Exécute une liste de requêtes séquentiellement.

    Chaque job est isolé : un échec n'interrompt jamais les suivants.
    """
    results: list[AnalysisResult] = []
    for index, request in enumerate(requests, start=1):
        total = len(requests)
        print(f"─── Job {index}/{total} : {request.token} {request.timeframe} {request.agent_id} ───")
        try:
            result = run_analysis(request)
        except Exception as exc:
            logger.exception("Erreur inattendue hors run_analysis (job %s)", index)
            result = AnalysisResult(
                request=request,
                success=False,
                error=f"erreur inattendue orchestrateur : {exc}",
            )
        results.append(result)
        status = "OK" if result.success else f"FAIL — {result.error}"
        verdict = result.parsed.get("verdict") if result.parsed else "—"
        print(f"   → {status} | verdict={verdict}")
    return results
