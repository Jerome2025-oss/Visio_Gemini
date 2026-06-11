# TradingView — session & capture (Visio_Gemini)

Les graphiques sont capturés via **votre compte TradingView** (`secrets/storage_state.json`) pour inclure layouts et indicateurs perso (Ichimoku, BB, EMA, etc.).

## Installation Playwright (VPS)

```bash
cd ~/Visio_Gemini
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# Optionnel si libs système manquantes :
# sudo playwright install-deps chromium
```

Dans `.env` :

```env
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
```

## Export session (Windows, 1× / 2–3 mois)

Utiliser le script du projet Bitunix_P (même format Playwright) :

```powershell
cd Detecte_Pump_Bitunix_P
python tools/export_tv_session.py
scp storage_state.json root@VOTRE_IP:~/Visio_Gemini/secrets/storage_state.json
```

Sur le VPS :

```bash
chmod 600 ~/Visio_Gemini/secrets/storage_state.json
chmod 700 ~/Visio_Gemini/secrets
```

## Symptômes session expirée

- Redirection vers `/accounts/signin`
- Layouts personnalisés absents
- Erreur runtime : « Session TradingView expirée »

→ Procédure complète : [renouvellement_session_tradingview.md](renouvellement_session_tradingview.md) (cookies Cookie-Editor ou export Playwright).

## Test capture unitaire

```bash
cd ~/Visio_Gemini && source venv/bin/activate
./venv/bin/python -m src.capture
```

## Layouts par agent (config.yaml)

| Agent | Layout ID | Indicateurs |
|-------|-----------|-------------|
| agent_a | VLmoQO22 | Ichimoku + RSI |
| agent_b | guJhPkAj | BB + Volume |
| agent_c | bl0W3Xyj | EMA50/200 + MACD |
