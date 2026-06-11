# Visio_Gemini

Bot macro **TradingView + vision Mammouth** : capture multi-layouts, analyse multi-agents, résultats en mémoire.

Dashboard FastAPI + HTMX sur le port **8004** (ÉTAPE 6) — affichage des derniers `AnalysisResult` sans SQLite.

## Stack

- Python 3.12+, venv local
- Playwright (capture TradingView headless)
- API Mammouth (OpenAI SDK) — modèle vision via `CHART_VISION_MODEL`
- Résultats pipeline : `AnalysisResult` en mémoire

## Installation (VPS)

```bash
cd ~/Visio_Gemini
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Éditer .env : OPENAI_API_KEY, CHART_VISION_MODEL, PLAYWRIGHT_BROWSERS_PATH
```

Session TradingView : [docs/TRADINGVIEW_SESSION.md](docs/TRADINGVIEW_SESSION.md) · renouvellement cookies : [docs/renouvellement_session_tradingview.md](docs/renouvellement_session_tradingview.md).

## Usage

```bash
cd ~/Visio_Gemini && source venv/bin/activate

# Test pipeline (section run de config.yaml)
.venv/bin/python3 -c "
from modules.analyse import run_batch
from modules.selection import build_from_run_section
print(run_batch(build_from_run_section()))
"

# Grille macro complète : 4 symboles × 2 TF × 3 agents = 24 jobs
.venv/bin/python3 -c "
from modules.analyse import run_batch
from modules.selection import build_macro_requests
print(run_batch(build_macro_requests()))
"
```

## Grille macro

| Symboles | Timeframes | Agents |
|----------|------------|--------|
| TOTAL3ES, OTHERS, USDT.D, BTCUSDT | 4h, 1D | A: Ichimoku+RSI · B: BB+Volume · C: EMA+MACD |

Documentation complète : [MACRO_AGENTS.md](MACRO_AGENTS.md).

## Données & rétention

| Élément | Politique |
|---------|-----------|
| **Verdicts** | En mémoire (`AnalysisResult`) — dashboard `:8004` |
| **PNG** | `captures/{agent_id}/` — conservés localement |
| **Logs coût** | `logs/chart_analyses.jsonl` (optionnel) |
| **`.env` / `secrets/`** | Jamais commités |

## Dashboard

- **Port 8004** — FastAPI + HTMX (8003 = bot trading sur ce VPS)
- Affiche les derniers verdicts par (symbole, TF, agent) depuis la mémoire du processus

## Structure

```
Visio_Gemini/
├── config.yaml          # Symboles, agents, layouts TV, providers
├── MACRO_AGENTS.md      # Grilles /10, consensus, USDT.D
├── modules/
│   ├── capture/         # Playwright TradingView
│   ├── agent/           # Provider Mammouth + parsing
│   ├── analyse/         # Orchestrateur run_batch()
│   ├── selection/       # Résolution symboles / macro
│   └── config/          # Loader config.yaml
├── prompts/             # Prompts agents A/B/C + contexte USDT.D
├── captures/            # PNG (gitignored)
├── logs/                # JSONL coûts API (gitignored)
└── secrets/             # storage_state.json (gitignored)
```

## Fichiers sensibles (gitignored)

- `.env`
- `secrets/storage_state.json`
- `captures/`, `logs/`
