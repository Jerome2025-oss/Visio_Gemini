"""Charge les prompts vision depuis prompts/*.txt (miroir de src/prompts.get_prompt)."""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_ROLE_END_MARKER = "Tu dois suivre **EXACTEMENT**"
_AGENT_FILES = {
    "agent_Ichimoku": "agent_Ichimoku.txt",
    "agent_BB": "agent_BB.txt",
    "agent_EMA": "agent_EMA.txt",
}


def _read_prompt_file(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Prompt introuvable : {path}")
    return path.read_text(encoding="utf-8")


def _inject_usdt_special_rule(prompt: str) -> str:
    usdt_rule = _read_prompt_file("usdt_d_context.txt")
    idx = prompt.find(_ROLE_END_MARKER)
    if idx == -1:
        return prompt + "\n\n" + usdt_rule
    return prompt[:idx] + usdt_rule + prompt[idx:]


def load_prompt(agent_id: str, symbol_key: str, timeframe_label: str) -> str:
    """
    Retourne le prompt complet (en-tête + corps + règle USDT.D si applicable).

    Contenu aligné sur ``src/prompts.get_prompt`` — les fichiers ``prompts/*.txt``
    sont extraits des constantes ``PROMPT_AGENT_*``.
    """
    filename = _AGENT_FILES.get(agent_id)
    if not filename:
        raise ValueError(f"Prompt inconnu pour l'agent : {agent_id}")

    body = _read_prompt_file(filename)
    if symbol_key == "USDT.D":
        body = _inject_usdt_special_rule(body)

    header = (
        f"Capture à analyser — Symbole : {symbol_key} | Timeframe : {timeframe_label}\n\n"
    )
    return header + body
