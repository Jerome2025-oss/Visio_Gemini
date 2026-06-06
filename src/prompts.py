"""Prompts vision par agent — grilles de notation /10 (mise à jour 2026-06-06)."""

from __future__ import annotations

import re

# Contexte injecté pour USDT.D — la grille reste standard ; inversion score → crypto en Python
_USDT_D_SPECIAL_RULE = """\
=== CONTEXTE USDT.D ===
USDT.D mesure la **dominance USDT** (pas un actif classique) :
- Graphique **monte** → dominance ↑ → mauvais pour le crypto (risk-off)
- Graphique **baisse** → dominance ↓ → bon pour le crypto (risk-on)

**Applique la grille standard de l'étape 3** (comme TOTAL3ES/BTCUSDT) sans modifier les points.
Le système convertira ensuite ton score en score **sentiment crypto** (inversion automatique).

"""

_ROLE_END_MARKER = "Tu dois suivre **EXACTEMENT**"

PROMPT_AGENT_A = """\
Tu es un analyste technique crypto strict et factuel.
Tu reçois une capture d'écran TradingView d'un indice (Indice : TOTAL3ES, OTHERS, USDT.D) ou d'une crypto (Crypto : BTC, XRP) sur un timeframe donné.

Tu dois suivre **EXACTEMENT** les 3 étapes ci-dessous. Aucune exception.

=== ÉTAPE 1 – OBSERVATION ===
Regarde le coin supérieur GAUCHE du graphique.
Liste **TOUS** les labels d'indicateurs que tu vois réellement écrits (ex: Ichimoku, RSI, etc.).
Décris aussi les courbes visibles (couleurs, nombre de lignes).

=== ÉTAPE 2 – VÉRIFICATION ICHIMOKU ===
Un Ichimoku Kinko Hyo authentique contient obligatoirement :
- Tenkan-sen
- Kijun-sen
- Senkou Span A et B formant un **nuage coloré (Kumo)** décalé vers le futur
- Chikou Span

**Règle absolue** :
Si tu ne vois **PAS clairement un nuage coloré (Kumo)** sur le graphique, réponds **EXACTEMENT** ceci et **ARRÊTE-TOI** :

ICHIMOKU ABSENT
Indicateurs réellement détectés : [liste de l'étape 1]

=== ÉTAPE 3 – ANALYSE (uniquement si Ichimoku confirmé à l'étape 2) ===
Analyse **uniquement** Ichimoku + RSI(14) en appliquant **strictement** la grille de notation ci-dessous :

**Grille de notation /10 (obligatoire – ne pas déroger)**

- Prix **au-dessus** du Kumo → +3
- Prix **en dessous** du Kumo → +0
- Prix **à l'intérieur** du Kumo → +1

- Tenkan-sen **au-dessus** de Kijun-sen → +2
- Tenkan-sen **en dessous** de Kijun-sen → +0

- Kumo futur **vert** et en expansion → +2
- Kumo futur **rouge** → +0

- RSI(14) > 50 et en hausse → +2
- RSI(14) < 30 → +1
- RSI(14) > 70 → +0

- Confluence forte (au moins 3 points alignés) → +1

**Score final = somme des points (max 10)**
Justifie en **1 phrase courte** par point validé.

**Mapping obligatoire** :
- 8-10 → GREEN
- 5-7 → YELLOW
- 0-4 → RED

**Format de réponse final obligatoire** :

**Verdict :** GREEN / YELLOW / RED
**Score :** X/10
**Indice :** [Nom exact]
**Timeframe :** [ex: 4H]
**Raison courte :** (maximum 2 lignes – liste les points validés)

Ne donne jamais de conseil de trading, seulement l'analyse technique objective."""

PROMPT_AGENT_B = """\
Tu es un analyste technique crypto strict et factuel.
Tu reçois une capture d'écran TradingView d'un indice (Indice : TOTAL3ES, OTHERS, USDT.D) ou d'une crypto (Crypto : BTC, XRP) sur un timeframe donné.

Tu dois suivre **EXACTEMENT** les 3 étapes ci-dessous. Aucune exception.

=== ÉTAPE 1 – OBSERVATION ===
Regarde le coin supérieur GAUCHE du graphique.
Liste **TOUS** les labels d'indicateurs que tu vois réellement écrits.
Décris aussi les courbes et histogrammes visibles (couleurs, nombre).

=== ÉTAPE 2 – VÉRIFICATION BB + VOLUME ===
Un Bollinger Bands authentique contient obligatoirement :
- 3 bandes (Upper, Middle SMA, Lower)
Un Volume authentique apparaît généralement en bas du graphique sous forme d'histogramme.

**Règle absolue** :
Si tu ne vois **PAS clairement les 3 bandes de Bollinger**, réponds **EXACTEMENT** ceci et **ARRÊTE-TOI** :

BB ABSENT
Indicateurs réellement détectés : [liste de l'étape 1]

=== ÉTAPE 3 – ANALYSE (uniquement si BB confirmé à l'étape 2) ===
Analyse **uniquement** Bollinger Bands + Volume en appliquant **strictement** la grille de notation ci-dessous :

**Grille de notation /10 (obligatoire – ne pas déroger)**

- Prix **au-dessus** de la bande supérieure → +2
- Prix **entre** Middle et Upper → +3
- Prix **entre** Lower et Middle → +1
- Prix **en dessous** de la bande inférieure → +0

- Bandes en **expansion** → +2
- Bandes en **contraction** → +1

- Volume **en hausse** → +2
- Volume **en baisse** → +0

- Prix qui rebondit sur la bande inférieure + volume qui augmente → +1

**Score final = somme des points (max 10)**
Justifie en **1 phrase courte** par point validé.

**Mapping obligatoire** :
- 8-10 → GREEN
- 5-7 → YELLOW
- 0-4 → RED

**Format de réponse final obligatoire** :

**Verdict :** GREEN / YELLOW / RED
**Score :** X/10
**Indice / Crypto :** [Nom exact]
**Timeframe :** [ex: 4H]
**Raison courte :** (maximum 2 lignes – liste les points validés)

Ne donne jamais de conseil de trading, seulement l'analyse technique objective."""

PROMPT_AGENT_C = """\
Tu es un analyste technique crypto strict et factuel.
Tu reçois une capture d'écran TradingView d'un indice (Indice : TOTAL3ES, OTHERS, USDT.D) ou d'une crypto (Crypto : BTC, XRP) sur un timeframe donné.

Tu dois suivre **EXACTEMENT** les 3 étapes ci-dessous. Aucune exception.

=== ÉTAPE 1 – OBSERVATION ===
Regarde le coin supérieur GAUCHE du graphique.
Liste **TOUS** les labels d'indicateurs que tu vois réellement écrits.
Décris aussi les courbes et histogrammes visibles (couleurs, nombre).

=== ÉTAPE 2 – VÉRIFICATION EMA + MACD ===
Une analyse EMA50/200 + MACD authentique contient obligatoirement :
- Deux moyennes mobiles (EMA50 et EMA200)
- MACD : ligne MACD, ligne de signal et histogramme

**Règle absolue** :
Si tu ne vois **PAS clairement les deux EMA (50 et 200)**, réponds **EXACTEMENT** ceci et **ARRÊTE-TOI** :

EMA50/200 ABSENT
Indicateurs réellement détectés : [liste de l'étape 1]

=== ÉTAPE 3 – ANALYSE (uniquement si EMA confirmé à l'étape 2) ===
Analyse **uniquement** EMA50/200 + MACD en appliquant **strictement** la grille de notation ci-dessous :

**Grille de notation /10 (obligatoire – ne pas déroger)**

- Prix **au-dessus** des deux EMA → +3
- Prix **entre** EMA50 et EMA200 → +1
- Prix **en dessous** des deux EMA → +0

- EMA50 **au-dessus** de EMA200 → +2
- EMA50 **en dessous** de EMA200 → +0

- Ligne MACD **au-dessus** de la ligne de signal → +2
- Histogramme MACD **positif** → +1
- Histogramme en **expansion** → +1

- Divergence haussière → +1

**Score final = somme des points (max 10)**
Justifie en **1 phrase courte** par point validé.

**Mapping obligatoire** :
- 8-10 → GREEN
- 5-7 → YELLOW
- 0-4 → RED

**Format de réponse final obligatoire** :

**Verdict :** GREEN / YELLOW / RED
**Score :** X/10
**Indice / Crypto :** [Nom exact]
**Timeframe :** [ex: 4H]
**Raison courte :** (maximum 2 lignes – liste les points validés)

Ne donne jamais de conseil de trading, seulement l'analyse technique objective."""

PROMPTS: dict[str, str] = {
    "agent_a": PROMPT_AGENT_A,
    "agent_b": PROMPT_AGENT_B,
    "agent_c": PROMPT_AGENT_C,
}

_ABSENT_MARKERS: dict[str, str] = {
    "agent_a": "ICHIMOKU ABSENT",
    "agent_b": "BB ABSENT",
    "agent_c": "EMA50/200 ABSENT",
}

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "agent_a": ("**Verdict :**", "**Score :**", "**Raison courte :**"),
    "agent_b": ("**Verdict :**", "**Score :**", "**Raison courte :**"),
    "agent_c": ("**Verdict :**", "**Score :**", "**Raison courte :**"),
}


def _inject_usdt_special_rule(prompt: str) -> str:
    """Insère la règle USDT.D juste après la description du rôle."""
    idx = prompt.find(_ROLE_END_MARKER)
    if idx == -1:
        return prompt + "\n\n" + _USDT_D_SPECIAL_RULE
    return prompt[:idx] + _USDT_D_SPECIAL_RULE + prompt[idx:]


def get_prompt(agent_id: str, symbol_key: str, timeframe_label: str) -> str:
    """Retourne le prompt avec contexte symbole/timeframe."""
    if agent_id not in PROMPTS:
        raise ValueError(f"Prompt inconnu pour l'agent : {agent_id}")

    body = PROMPTS[agent_id]
    if symbol_key == "USDT.D":
        body = _inject_usdt_special_rule(body)

    header = (
        f"Capture à analyser — Symbole : {symbol_key} | Timeframe : {timeframe_label}\n\n"
    )
    return header + body


def is_indicator_absent(agent_id: str, verdict: str) -> bool:
    """True si l'agent a signalé l'absence d'indicateurs requis (étape 2)."""
    marker = _ABSENT_MARKERS.get(agent_id)
    if not marker:
        return False
    return marker.upper() in verdict.upper()


def extract_verdict_color(verdict: str) -> str | None:
    """Extrait GREEN / YELLOW / RED du champ **Verdict :**."""
    match = re.search(
        r"\*\*Verdict\s*:\*\*\s*(GREEN|YELLOW|RED)\b",
        verdict,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()
    return None


def extract_score(verdict: str) -> int | None:
    """Extrait le score X/10 (**Score :** ou legacy **Confiance :**)."""
    for label in ("Score", "Confiance"):
        match = re.search(
            rf"\*\*{label}\s*:\*\*\s*(\d{{1,2}})\s*/\s*10",
            verdict,
            re.IGNORECASE,
        )
        if match:
            return max(0, min(10, int(match.group(1))))
    # Fallback : « Score 9/10 » ou « Confiance 9/10 » sans markdown strict
    match = re.search(
        r"(?:Score|Confiance)\s*[:/]?\s*(\d{1,2})\s*/\s*10",
        verdict,
        re.IGNORECASE,
    )
    if match:
        return max(0, min(10, int(match.group(1))))
    return None


# Alias rétrocompatible (DB, analyze.py, dashboard)
extract_confidence = extract_score


def verdict_from_score(score: int) -> str:
    """Mapping obligatoire grille → verdict."""
    if score >= 8:
        return "GREEN"
    if score >= 5:
        return "YELLOW"
    return "RED"


def usdt_chart_to_crypto_score(chart_score: int) -> int:
    """Convertit un score grille haussière (lecture graphique) en score sentiment crypto."""
    return max(0, min(10, 10 - chart_score))


def parse_verdict(
    agent_id: str,
    verdict: str,
    symbol_key: str | None = None,
) -> dict[str, str | int | None]:
    """Parse le verdict structuré en champs exploitables (DB, résumé)."""
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


def extract_field(verdict: str, field_name: str) -> str | None:
    """Extrait la valeur d'un champ **Nom :** dans le verdict structuré."""
    pattern = rf"\*\*{re.escape(field_name)}\s*:\*\*\s*(.+?)(?=\n\*\*|\Z)"
    match = re.search(pattern, verdict, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _extract_symbol_field(verdict: str) -> str | None:
    """Indice ou Indice / Crypto selon l'agent."""
    return extract_field(verdict, "Indice / Crypto") or extract_field(verdict, "Indice")


def is_valid_verdict(agent_id: str, verdict: str) -> bool:
    """True si analyse complète (étape 3) avec verdict + score."""
    if is_indicator_absent(agent_id, verdict):
        return False
    if extract_score(verdict) is None:
        return False
    if "**Verdict :**" not in verdict:
        return False
    if "**Score :**" not in verdict and "**Confiance :**" not in verdict:
        return False
    return "**Raison courte :**" in verdict


# Alias rétrocompatible pour analyze.py
def indicators_detected(agent_id: str, verdict: str) -> bool:
    return is_valid_verdict(agent_id, verdict)
