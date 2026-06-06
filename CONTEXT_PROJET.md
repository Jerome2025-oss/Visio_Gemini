# CONTEXT_PROJET.md — Visio_Gemini

## 1. INFRA & STACK

- VPS Linux, accès SSH
- Python 3.12+, venv : `~/Visio_Gemini/venv/`
- SQLite : `~/Visio_Gemini/data/visio_gemini.db`
- Playwright Chromium : `~/.cache/ms-playwright` (voir `PLAYWRIGHT_BROWSERS_PATH` dans `.env`)
- API vision : Mammouth (`OPENAI_BASE_URL`, modèle `CHART_VISION_MODEL`)
- Config : `config.yaml` + `.env`
- Projet lié : [Detecte_Pump_Bitunix_P](https://github.com/Jerome2025-oss/detecte_Pump_Bitunix_P) (dashboard `/macro`, port 8002)

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

Le LLM applique la grille standard ; Python convertit : `score_crypto = 10 - score_chart` (`parse_verdict`, symbole USDT.D).

## 4. RÉTENTION DES DONNÉES

- **SQLite** : historique cumulé, **ne jamais DELETE** sauf demande explicite du trader
- **PNG** : purge automatique des fichiers non référencés par `analyses.image_path` après chaque run
- Dashboard Bitunix : affiche le **dernier** verdict par (symbole, TF, agent) via `MAX(timestamp)`

## 5. COMMANDES COURANTES

```bash
cd ~/Visio_Gemini && source venv/bin/activate
./venv/bin/python -m src.main --macro
sudo systemctl restart detecte-pump-api   # rafraîchir /macro
```

## 6. FICHIERS CLÉS

| Fichier | Rôle |
|---------|------|
| `src/prompts.py` | Prompts + grilles /10 + inversion USDT.D |
| `src/main.py` | Orchestration capture → analyse → SQLite |
| `MACRO_AGENTS.md` | Spec notation, consensus, exemples dashboard |
| `secrets/storage_state.json` | Session TradingView (export Windows) |
| `.env` | Clés API Mammouth, modèle vision |

## 7. RÈGLES DE COLLABORATION

- Ne pas purger SQLite sans demande explicite
- Ne pas committer `.env`, secrets, captures, base SQLite
- Présenter les commandes en blocs `bash` prêts à copier
- Préciser VPS vs Windows pour export session TradingView
