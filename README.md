# Visio_Gemini

Bot macro **TradingView + Gemini Vision (Mammouth)** : capture multi-layouts, analyse multi-agents, stockage SQLite.

Projet compagnon de [Detecte_Pump_Bitunix_P](https://github.com/Jerome2025-oss/detecte_Pump_Bitunix_P) — le dashboard `/macro` (port 8002) lit `data/visio_gemini.db`.

## Stack

- Python 3.12+, venv local
- Playwright (capture TradingView headless)
- OpenAI SDK → API Mammouth (Gemini vision)
- SQLite (`data/visio_gemini.db`)

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

# Test rapide (section run de config.yaml)
./venv/bin/python -m src.main

# Grille macro complète : 4 symboles × 2 TF × 3 agents = 24 jobs
./venv/bin/python -m src.main --macro

# Estimation coût Mammouth
./venv/bin/python -m src.main --budget
```

## Grille macro

| Symboles | Timeframes | Agents |
|----------|------------|--------|
| TOTAL3ES, OTHERS, USDT.D, BTCUSDT | 4h, 1D | A: Ichimoku+RSI · B: BB+Volume · C: EMA+MACD |

Documentation complète : [MACRO_AGENTS.md](MACRO_AGENTS.md).

## Données & rétention

| Élément | Politique |
|---------|-----------|
| **SQLite** | Append-only — jamais purgé sauf demande explicite |
| **PNG** | Purge auto des orphelins après chaque run (non référencés en base) |
| **`.env` / `secrets/`** | Jamais commités |

## Intégration Bitunix_P

Dans `Detecte_Pump_Bitunix_P/config.yaml` :

```yaml
visio_gemini:
  database_path: /root/Visio_Gemini/data/visio_gemini.db
  reports_dir: /root/Visio_Gemini/reports
```

Dashboard : `http://VPS:8002/macro`

## Structure

```
Visio_Gemini/
├── config.yaml          # Symboles, agents, layouts TV
├── MACRO_AGENTS.md      # Grilles /10, consensus, USDT.D
├── src/
│   ├── main.py          # Point d'entrée --macro
│   ├── capture.py       # Playwright TradingView
│   ├── analyze.py       # API vision Mammouth
│   ├── prompts.py       # Grilles agents A/B/C
│   ├── database.py      # SQLite
│   └── capture_cleanup.py
├── captures/            # PNG (gitignored)
├── verdicts/            # Texte brut IA (gitignored)
├── data/                # visio_gemini.db (gitignored)
└── secrets/             # storage_state.json (gitignored)
```

## Fichiers sensibles (gitignored)

- `.env`
- `secrets/storage_state.json`
- `data/*.db`, `captures/`, `logs/`, `verdicts/`
