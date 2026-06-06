"""Analyse d'une capture TradingView via Mammouth (Gemini Vision)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.prompts import (
    extract_confidence,
    extract_verdict_color,
    get_prompt,
    is_valid_verdict,
)
from src.settings import AppConfig

# Tarifs Mammouth ($/M tokens) — calibrés sur facturation réelle juin 2026
# (dashboard Mammouth ~0.00055 $/job pour ~1318 in + 150 out)
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


def _append_cost_log(cfg: AppConfig, entry: dict[str, Any]) -> Path:
    log_path = cfg.logs_dir / "chart_analyses.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return log_path


def analyze_capture(
    cfg: AppConfig, image_path: Path, verdicts_dir: Path
) -> tuple[Path, dict[str, Any]]:
    """Envoie l'image à Mammouth, sauvegarde le verdict et journalise le coût."""
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY absent du .env")

    prompt = get_prompt(cfg.agent_id, cfg.symbol_key, cfg.timeframe_label)

    print(f"🧠 Modèle vision    : {cfg.chart_vision_model}")
    print(f"🌐 Endpoint         : {cfg.openai_base_url}")
    print(f"📋 Agent            : {cfg.agent_id} ({cfg.agent_name})")

    client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)
    data_url = f"data:image/png;base64,{_encode_image(image_path)}"

    print("📡 Appel API en cours...")
    response = client.chat.completions.create(
        model=cfg.chart_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )

    verdict = response.choices[0].message.content or "(verdict vide)"

    verdicts_dir.mkdir(parents=True, exist_ok=True)
    verdict_path = verdicts_dir / f"{image_path.stem}.txt"
    verdict_path.write_text(verdict, encoding="utf-8")

    usage = response.usage
    prompt_tokens = int(usage.prompt_tokens if usage else 0)
    completion_tokens = int(usage.completion_tokens if usage else 0)
    cost_usd = _estimate_cost_usd(cfg.chart_vision_model, prompt_tokens, completion_tokens)
    cost_eur = _estimate_cost_eur(cfg.chart_vision_model, prompt_tokens, completion_tokens)

    verdict_color = extract_verdict_color(verdict)
    confidence = extract_confidence(verdict)
    verdict_ok = is_valid_verdict(cfg.agent_id, verdict)

    meta: dict[str, Any] = {
        "model": cfg.chart_vision_model,
        "base_url": cfg.openai_base_url,
        "image_path": str(image_path.resolve()),
        "image_size_kb": round(image_path.stat().st_size / 1024, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(cost_usd, 6),
        "cost_eur": cost_eur,
        "analyzed_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }

    log_entry = {
        "agent_id": cfg.agent_id,
        "agent_name": cfg.agent_name,
        "symbol": cfg.symbol,
        "symbol_key": cfg.symbol_key,
        "timeframe": cfg.timeframe_label,
        "layout_id": cfg.layout_id,
        "verdict_ok": verdict_ok,
        "verdict_color": verdict_color,
        "confidence": confidence,
        "verdict_path": str(verdict_path.resolve()),
        "verdict_preview": verdict[:200].replace("\n", " "),
        "_meta": meta,
    }
    log_path = _append_cost_log(cfg, log_entry)
    meta["log_path"] = str(log_path.resolve())

    print(
        f"💸 Coût estimé      : {cost_eur:.6f} € "
        f"({prompt_tokens} in + {completion_tokens} out = {prompt_tokens + completion_tokens} tokens)"
    )
    if verdict_color:
        conf_label = f" ({confidence}/10)" if confidence is not None else ""
        print(f"🚦 Verdict          : {verdict_color}{conf_label}")
    print(f"📝 Log coût         : {log_path}")

    return verdict_path, meta
