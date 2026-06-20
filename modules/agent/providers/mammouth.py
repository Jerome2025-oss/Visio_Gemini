"""Provider vision Mammouth — appel OpenAI inline (sans écriture verdict .txt)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from modules.agent.providers.base import AnalyzeContext, ProviderError, VisionProvider, VisionResult
from modules.agent.verdict_parser import extract_confidence, extract_verdict_color, is_valid_verdict
from modules.config import load_app_config

_CHART_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite-preview": (0.37, 0.43),
    "gemini-3-flash-preview": (0.3, 1.5),
    "gemini-3.1-pro-preview": (2.5, 15.0),
    "mistral-small-2603": (0.15, 0.6),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
}
_EUR_RATE = 0.92


def _encode_image(image_path: Path) -> str:
    with image_path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _CHART_PRICING.get(model, (0.25, 0.4))
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000


def _estimate_cost_eur(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    return round(_estimate_cost_usd(model, prompt_tokens, completion_tokens) * _EUR_RATE, 6)


def _append_cost_log(logs_dir: Path, entry: dict[str, Any]) -> Path:
    log_path = logs_dir / "chart_analyses.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return log_path


class MammouthProvider(VisionProvider):
    """Analyse vision via API Mammouth (format OpenAI)."""

    @property
    def name(self) -> str:
        return "mammouth"

    def analyze(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: AnalyzeContext | None = None,
        temperature: float | None = None,
    ) -> VisionResult:
        if not prompt.strip():
            raise ProviderError("Prompt vide", reason="invalid_input")
        if context is None:
            raise ProviderError(
                "AnalyzeContext requis pour Mammouth",
                reason="missing_context",
            )
        if not image_path.is_file():
            raise ProviderError(f"Image introuvable : {image_path}", reason="invalid_input")

        app = load_app_config()
        api_key = app.providers.openai_api_key
        base_url = app.providers.openai_base_url
        model = app.providers.chart_vision_model

        if not api_key:
            raise ProviderError("OPENAI_API_KEY absente du .env", reason="missing_api_key")

        print(f"🧠 Modèle vision    : {model}")
        print(f"🌐 Endpoint         : {base_url}")
        print(f"📋 Agent            : {context.agent_id}")

        client = OpenAI(api_key=api_key, base_url=base_url)
        data_url = f"data:image/png;base64,{_encode_image(image_path)}"

        try:
            print("📡 Appel API en cours...")
            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            response = client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            raise ProviderError(str(exc), reason="api_error") from exc

        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ProviderError("Réponse Mammouth vide", reason="empty_response")

        usage = response.usage
        prompt_tokens = int(usage.prompt_tokens if usage else 0)
        completion_tokens = int(usage.completion_tokens if usage else 0)
        cost_usd = round(_estimate_cost_usd(model, prompt_tokens, completion_tokens), 6)
        cost_eur = _estimate_cost_eur(model, prompt_tokens, completion_tokens)

        verdict_color = extract_verdict_color(text)
        confidence = extract_confidence(text)
        verdict_ok = is_valid_verdict(context.agent_id, text)

        meta: dict[str, Any] = {
            "model": model,
            "base_url": base_url,
            "image_path": str(image_path.resolve()),
            "image_size_kb": round(image_path.stat().st_size / 1024, 1),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": cost_usd,
            "cost_eur": cost_eur,
            "analyzed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }

        log_entry = {
            "agent_id": context.agent_id,
            "symbol_key": context.symbol_key,
            "timeframe": context.timeframe_label,
            "layout_id": context.layout_id,
            "verdict_ok": verdict_ok,
            "verdict_color": verdict_color,
            "confidence": confidence,
            "verdict_preview": text[:200].replace("\n", " "),
            "_meta": meta,
        }
        log_path = _append_cost_log(app.paths.logs, log_entry)
        meta["log_path"] = str(log_path.resolve())

        print(
            f"💸 Coût estimé      : {cost_eur:.6f} € "
            f"({prompt_tokens} in + {completion_tokens} out = {prompt_tokens + completion_tokens} tokens)"
        )
        if verdict_color:
            conf_label = f" ({confidence}/10)" if confidence is not None else ""
            print(f"🚦 Verdict          : {verdict_color}{conf_label}")

        return VisionResult(
            text=text,
            provider="mammouth",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
            verdict_path=None,
            raw_meta=meta,
        )

    def analyze_multi(
        self,
        image_paths: list[Path],
        prompt: str,
        *,
        context: AnalyzeContext | None = None,
    ) -> VisionResult:
        """Analyse plusieurs images dans UN SEUL appel (ordre préservé).

        Utilisé par l'entonnoir Ichimoku (H4 → H1 → M15). Réutilise les helpers
        de coût/log du provider ; aucun verdict GREEN/YELLOW/RED n'est forcé
        (le format de sortie est libre, parsé en aval par l'appelant).
        """
        if not prompt.strip():
            raise ProviderError("Prompt vide", reason="invalid_input")
        if context is None:
            raise ProviderError(
                "AnalyzeContext requis pour Mammouth",
                reason="missing_context",
            )
        paths = [Path(p) for p in image_paths]
        if not paths:
            raise ProviderError("Aucune image fournie", reason="invalid_input")
        for path in paths:
            if not path.is_file():
                raise ProviderError(f"Image introuvable : {path}", reason="invalid_input")

        app = load_app_config()
        api_key = app.providers.openai_api_key
        base_url = app.providers.openai_base_url
        model = app.providers.chart_vision_model

        if not api_key:
            raise ProviderError("OPENAI_API_KEY absente du .env", reason="missing_api_key")

        print(f"🧠 Modèle vision    : {model}")
        print(f"🌐 Endpoint         : {base_url}")
        print(f"📋 Agent            : {context.agent_id} (multi-image x{len(paths)})")

        client = OpenAI(api_key=api_key, base_url=base_url)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in paths:
            data_url = f"data:image/png;base64,{_encode_image(path)}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})

        try:
            print("📡 Appel API multi-image en cours...")
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise ProviderError(str(exc), reason="api_error") from exc

        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ProviderError("Réponse Mammouth vide", reason="empty_response")

        usage = response.usage
        prompt_tokens = int(usage.prompt_tokens if usage else 0)
        completion_tokens = int(usage.completion_tokens if usage else 0)
        cost_usd = round(_estimate_cost_usd(model, prompt_tokens, completion_tokens), 6)
        cost_eur = _estimate_cost_eur(model, prompt_tokens, completion_tokens)

        meta: dict[str, Any] = {
            "model": model,
            "base_url": base_url,
            "image_paths": [str(p.resolve()) for p in paths],
            "image_count": len(paths),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": cost_usd,
            "cost_eur": cost_eur,
            "analyzed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }

        log_entry = {
            "agent_id": context.agent_id,
            "symbol_key": context.symbol_key,
            "timeframe": context.timeframe_label,
            "layout_id": context.layout_id,
            "multi_image": True,
            "image_count": len(paths),
            "verdict_preview": text[:200].replace("\n", " "),
            "_meta": meta,
        }
        log_path = _append_cost_log(app.paths.logs, log_entry)
        meta["log_path"] = str(log_path.resolve())

        print(
            f"💸 Coût estimé      : {cost_eur:.6f} € "
            f"({prompt_tokens} in + {completion_tokens} out = {prompt_tokens + completion_tokens} tokens)"
        )

        return VisionResult(
            text=text,
            provider="mammouth",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
            verdict_path=None,
            raw_meta=meta,
        )
