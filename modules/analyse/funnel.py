"""Entonnoir Ichimoku 3 timeframes (H4 → H1 → M15) — capture x3 + 1 appel vision.

Indépendant du pipeline standard (`run_batch`). Réutilise capture, prompt_loader
et le provider Mammouth (`analyze_multi`). Le prompt agent_Ichimoku.txt attend
3 images dans l'ordre H4, H1, M15 et produit un rapport « entonnoir » terminé par
« 🎯 CONFIANCE : X/10 » et « 📌 DÉCISION : ... ».
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from modules.agent import load_prompt
from modules.agent.providers import AnalyzeContext, analyze_multi_with_strategy
from modules.capture import capture
from modules.selection.bitunix_symbols import normalize_token_key
from modules.selection.resolver import resolve_layout, resolve_symbol_tv

logger = logging.getLogger("visio_gemini.funnel")

FUNNEL_AGENT_ID = "agent_Ichimoku"
# Ordre IMPÉRATIF : H4 (contexte) → H1 (confirmation) → M15 (exécution)
FUNNEL_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "15m")
FUNNEL_TF_LABELS: dict[str, str] = {"4h": "H4", "1h": "H1", "15m": "M15"}

# Seuil unique Ichimoku (dashboard + backtest) : confiance ≥ 6/10.
ICHIMOKU_TRADE_MIN_SCORE = 6.0

_CONFIANCE_RE = re.compile(
    r"CONFIANCE\s*[:：]\s*(\d{1,2}(?:[.,]\d+)?)\s*/\s*10",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(r"D[ÉE]CISION\s*[:：]\s*(.+)", re.IGNORECASE)


@dataclass(frozen=True)
class FunnelCapture:
    """Une capture timeframe de l'entonnoir."""

    timeframe: str
    tf_label: str
    png_path: Path | None
    error: str | None = None


@dataclass(frozen=True)
class FunnelResult:
    """Résultat complet de l'entonnoir Ichimoku 3TF."""

    symbol_key: str
    symbol_tv: str
    captures: list[FunnelCapture] = field(default_factory=list)
    report_text: str | None = None
    confiance: float | None = None
    decision: str | None = None
    decision_color: str | None = None
    cost_eur: float | None = None
    model: str | None = None
    error: str | None = None


def _parse_confiance(text: str) -> float | None:
    match = _CONFIANCE_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(",", ".")
    try:
        value = round(float(raw), 1)
    except ValueError:
        return None
    return max(0.0, min(10.0, value))


def parse_confiance_from_recap(text: str | None) -> float | None:
    """Parse la note /10 depuis un rapport entonnoir (public pour backfill)."""
    if not text:
        return None
    return _parse_confiance(text)


def format_score_fr(score: float | int | None) -> str | None:
    """Affichage FR du score : ``5,5`` ou ``6`` (sans suffixe /10)."""
    if score is None:
        return None
    value = round(float(score), 1)
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def is_tradable_score(score: float | int | None) -> bool:
    """Accepté Ichimoku = confiance ≥ ``ICHIMOKU_TRADE_MIN_SCORE`` (seuil impartial)."""
    if score is None:
        return False
    return float(score) >= ICHIMOKU_TRADE_MIN_SCORE


def _parse_decision(text: str) -> str | None:
    match = _DECISION_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _decision_color(decision: str | None, confiance: float | None) -> str:
    """Couleur de badge : green=LONG, red=SHORT, muted=pas de trade/inconnu."""
    if decision:
        upper = decision.upper()
        if "PAS DE TRADE" in upper or "RIEN" in upper or "ATTENDRE" in upper:
            return "muted"
        if "LONG" in upper:
            return "green"
        if "SHORT" in upper:
            return "red"
    return "muted"


def run_funnel(symbol: str) -> FunnelResult:
    """Lance l'entonnoir Ichimoku 3TF pour un symbole.

    Ne lève jamais d'exception : toute erreur est encapsulée dans
    ``FunnelResult.error`` (le dashboard reste stable).
    """
    token = normalize_token_key(symbol)

    try:
        symbol_tv = resolve_symbol_tv(token)
    except Exception as exc:  # token inconnu / config
        return FunnelResult(symbol_key=token, symbol_tv="", error=str(exc))

    try:
        layout_id = resolve_layout(FUNNEL_AGENT_ID)
    except Exception as exc:
        return FunnelResult(symbol_key=token, symbol_tv=symbol_tv, error=str(exc))

    captures: list[FunnelCapture] = []
    image_paths: list[Path] = []
    for tf in FUNNEL_TIMEFRAMES:
        label = FUNNEL_TF_LABELS[tf]
        try:
            png = capture(symbol_tv, tf, layout_id, FUNNEL_AGENT_ID)
            captures.append(FunnelCapture(timeframe=tf, tf_label=label, png_path=png))
            image_paths.append(png)
        except Exception as exc:
            logger.warning("Capture %s échouée (%s) : %s", label, token, exc)
            captures.append(
                FunnelCapture(timeframe=tf, tf_label=label, png_path=None, error=str(exc))
            )

    if len(image_paths) < len(FUNNEL_TIMEFRAMES):
        missing = [c.tf_label for c in captures if c.png_path is None]
        return FunnelResult(
            symbol_key=token,
            symbol_tv=symbol_tv,
            captures=captures,
            error=f"Captures incomplètes — manquant : {', '.join(missing)}",
        )

    prompt = load_prompt(FUNNEL_AGENT_ID, token, "H4+H1+M15")
    context = AnalyzeContext(
        agent_id=FUNNEL_AGENT_ID,
        symbol_key=token,
        symbol_tv=symbol_tv,
        timeframe_label="H4+H1+M15",
        layout_id=layout_id,
    )

    try:
        vision = analyze_multi_with_strategy(image_paths, prompt, context=context)
    except Exception as exc:
        logger.warning("LLM entonnoir %s échoué : %s", token, exc)
        return FunnelResult(
            symbol_key=token,
            symbol_tv=symbol_tv,
            captures=captures,
            error=f"Timeout / erreur LLM : {exc}",
        )

    text = vision.text
    confiance = _parse_confiance(text)
    decision = _parse_decision(text)

    return FunnelResult(
        symbol_key=token,
        symbol_tv=symbol_tv,
        captures=captures,
        report_text=text,
        confiance=confiance,
        decision=decision,
        decision_color=_decision_color(decision, confiance),
        cost_eur=vision.cost_eur,
        model=vision.model,
    )
