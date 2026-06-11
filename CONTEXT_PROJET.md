# CONTEXT_PROJET.md — Visio_Gemini

## 1. INFRA & STACK

- VPS Linux, accès SSH
- Python 3.12+, venv : `~/Visio_Gemini/venv/` ou `.venv/`
- Playwright Chromium : `~/.cache/ms-playwright` (voir `PLAYWRIGHT_BROWSERS_PATH` dans `.env`)
- API vision : Gemini (primaire) + Mammouth fallback (`OPENAI_BASE_URL`, `CHART_VISION_MODEL`)
- Config : `config.yaml` + `.env`
- Dashboard : FastAPI + HTMX sur port **8003** (résultats en mémoire)

## 2. RÔLE DU PROJET

Analyser le **contexte macro crypto** via captures TradingView et 3 agents spécialisés :

- **Agent A** — Ichimoku + RSI  
- **Agent B** — Bollinger Bands + Volume  
- **Agent C** — EMA50/200 + MACD  

Chaque agent produit **Verdict + Score /10** (GREEN 8–10, YELLOW 5–7, RED 0–4).

Run macro standard : **24 analyses** (4 symboles × 2 TF × 3 agents).

## 3. USDT.D — RÈGLE SPÉCIALE

USDT.D mesure la dominance USDT :

- Graphique **monte** → dominance ↑ → **note crypto basse** (risk-off)
- Graphique **baisse** → dominance ↓ → **note crypto élevée** (risk-on)

Le LLM applique la grille standard ; Python convertit : `score_crypto = 10 - score_chart` (`modules/agent/verdict_parser.py`, symbole USDT.D).

## 4. RÉTENTION DES DONNÉES

- **Verdicts** : `AnalysisResult` en mémoire — pas de SQLite, pas d'archives `.txt`
- **PNG** : `captures/{agent_id}/` — conservés localement
- **Dashboard :8003** : affiche les derniers verdicts du processus en cours

## 5. COMMANDES COURANTES

```bash
cd ~/Visio_Gemini && source venv/bin/activate

# Test rapide (section run)
.venv/bin/python3 tools/phase2_rapatriement_test.py

# Grille macro
.venv/bin/python3 -c "
from modules.analyse import run_batch
from modules.selection import build_macro_requests
run_batch(build_macro_requests())
"
```

## 6. FICHIERS CLÉS

| Fichier | Rôle |
|---------|------|
| `prompts/agent_*.txt` | Prompts + grilles /10 |
| `modules/agent/verdict_parser.py` | Parsing score/verdict + inversion USDT.D |
| `modules/analyse/orchestrator.py` | Orchestration capture → vision → parse |
| `MACRO_AGENTS.md` | Spec notation, consensus, exemples dashboard |
| `secrets/storage_state.json` | Session TradingView (export Windows) |
| `.env` | Clés API Gemini/Mammouth, modèle vision |

## 7. RÈGLES DE COLLABORATION

- Ne pas committer `.env`, secrets, captures
- Présenter les commandes en blocs `bash` prêts à copier
- Préciser VPS vs Windows pour export session TradingView
