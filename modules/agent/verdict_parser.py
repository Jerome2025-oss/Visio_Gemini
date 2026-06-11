"""Parsing des verdicts vision — grilles /10 et inversion USDT.D."""

from __future__ import annotations

import re

_ABSENT_MARKERS: dict[str, str] = {
    "agent_a": "ICHIMOKU ABSENT",
    "agent_b": "BB ABSENT",
    "agent_c": "EMA50/200 ABSENT",
}


def is_indicator_absent(agent_id: str, verdict: str) -> bool:
    marker = _ABSENT_MARKERS.get(agent_id)
    if not marker:
        return False
    return marker.upper() in verdict.upper()


def extract_verdict_color(verdict: str) -> str | None:
    match = re.search(
        r"\*\*Verdict\s*:\*\*\s*(GREEN|YELLOW|RED)\b",
        verdict,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()
    return None


def extract_score(verdict: str) -> int | None:
    for label in ("Score", "Confiance"):
        match = re.search(
            rf"\*\*{label}\s*:\*\*\s*(\d{{1,2}})\s*/\s*10",
            verdict,
            re.IGNORECASE,
        )
        if match:
            return max(0, min(10, int(match.group(1))))
    match = re.search(
        r"(?:Score|Confiance)\s*[:/]?\s*(\d{1,2})\s*/\s*10",
        verdict,
        re.IGNORECASE,
    )
    if match:
        return max(0, min(10, int(match.group(1))))
    return None


extract_confidence = extract_score


def verdict_from_score(score: int) -> str:
    if score >= 8:
        return "GREEN"
    if score >= 5:
        return "YELLOW"
    return "RED"


def usdt_chart_to_crypto_score(chart_score: int) -> int:
    return max(0, min(10, 10 - chart_score))


def extract_field(verdict: str, field_name: str) -> str | None:
    pattern = rf"\*\*{re.escape(field_name)}\s*:\*\*\s*(.+?)(?=\n\*\*|\Z)"
    match = re.search(pattern, verdict, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _extract_symbol_field(verdict: str) -> str | None:
    return extract_field(verdict, "Indice / Crypto") or extract_field(verdict, "Indice")


def parse_verdict(
    agent_id: str,
    verdict: str,
    symbol_key: str | None = None,
) -> dict[str, str | int | None]:
    """Parse le verdict structuré en champs exploitables."""
    if is_indicator_absent(agent_id, verdict):
        detected = extract_field(verdict, "Indicateurs réellement détectés")
        if detected is None:
            match = re.search(
                r"Indicateurs réellement détectés\s*:\s*(.+)",
                verdict,
                re.IGNORECASE,
            )
            detected = match.group(1).strip() if match else None
        return {
            "verdict": None,
            "confiance": None,
            "raison": _ABSENT_MARKERS.get(agent_id, "INDICATEURS ABSENTS"),
            "observations": detected,
        }

    chart_score = extract_score(verdict)
    score = chart_score
    verdict_color = extract_verdict_color(verdict)

    if score is not None and symbol_key == "USDT.D":
        score = usdt_chart_to_crypto_score(score)
        verdict_color = verdict_from_score(score)
    elif score is not None:
        verdict_color = verdict_from_score(score)

    symbole = _extract_symbol_field(verdict)
    timeframe = extract_field(verdict, "Timeframe")
    raison = extract_field(verdict, "Raison courte")

    observations_parts: list[str] = []
    if symbole:
        observations_parts.append(f"Indice: {symbole}")
    if timeframe:
        observations_parts.append(f"Timeframe: {timeframe}")
    if chart_score is not None and symbol_key == "USDT.D":
        observations_parts.append(f"Score chart: {chart_score}/10")
        observations_parts.append(f"Score crypto: {score}/10")
    elif score is not None:
        observations_parts.append(f"Score grille: {score}/10")

    return {
        "verdict": verdict_color,
        "confiance": score,
        "raison": raison,
        "observations": " | ".join(observations_parts) if observations_parts else None,
    }


def is_valid_verdict(agent_id: str, verdict: str) -> bool:
    if is_indicator_absent(agent_id, verdict):
        return False
    if extract_score(verdict) is None:
        return False
    if "**Verdict :**" not in verdict:
        return False
    if "**Score :**" not in verdict and "**Confiance :**" not in verdict:
        return False
    return "**Raison courte :**" in verdict
