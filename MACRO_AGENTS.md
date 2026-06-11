# Visio_Gemini — Grille macro multi-agents

Référence pour l’analyse macro du bot : symboles, timeframes, indicateurs, agents spécialisés et **système de notation**.

---

## 0. Système de notation (définitif — juin 2026)

### Ancien vs nouveau

| | Ancien système | Nouveau système (actif) |
|---|----------------|-------------------------|
| **Logique** | Verdict et note `/10` **indépendants** (ex. `RED 9/10` = très bearish *avec forte confiance*) | Le **score `/10` mesure uniquement la force haussière** (grille de points sur conditions bullish) |
| **Verdict** | Choisi librement par le LLM | **Dérivé obligatoirement du score** |
| **Interprétation** | Confiance subjective | Score **bas** → signal **rouge** ; score **élevé** → signal **vert** |

Chaque agent retourne **Verdict + Score /10** selon la grille de notation haussière définie dans `prompts/agent_*.txt`.  
Le verdict affiché est recalculé côté Python si le modèle diverge :

| Score /10 | Verdict |
|-----------|---------|
| **8 – 10** | GREEN |
| **5 – 7** | YELLOW |
| **0 – 4** | RED |

**Format d’affichage dashboard** : `RED 3/10`, `YELLOW 6/10`, `GREEN 8/10` (verdict puis score).

> **Idéal pour :** décision macro rapide en un coup d’œil — plus le score est **bas**, plus le signal est **rouge** ; plus il est **haut**, plus le contexte est **haussier**.

### Consensus

Le **consensus** par symbole × timeframe combine les 3 agents :

1. **Verdict consensus** = majorité des 3 verdicts (`GREEN` / `YELLOW` / `RED`) ; égalité → `YELLOW`.
2. **Score consensus** = moyenne arrondie des scores `/10` des agents alignés sur le verdict majoritaire (fallback : moyenne des 3 scores).

Exemple : `RED 3/10` + `RED 2/10` + `RED 4/10` → consensus **`RED 3/10`**.

### USDT.D (risk-on / risk-off)

Pour **USDT.D** uniquement, une **règle spéciale** est injectée dans les prompts des 3 agents (`prompts/usdt_d_context.txt`) :

- Graphique **monte** → dominance ↑ → **note basse** (conditions haussières = 0 pt)
- Graphique **baisse** → dominance ↓ → **note élevée** (conditions baissières = points)

Le LLM applique la **grille standard** (lecture haussière du graphique).
Python convertit ensuite : **score crypto = 10 − score chart** (`parse_verdict`, symbole USDT.D).
Score et verdict reflètent le **sentiment crypto** :

| Score /10 | Verdict | Signification crypto |
|-----------|---------|----------------------|
| 8–10 | GREEN | Dominance en forte baisse (risk-on) |
| 5–7 | YELLOW | Neutre |
| 0–4 | RED | Dominance en hausse (risk-off) |

Légende dashboard : « USDT.D : GREEN = dominance en baisse (risk-on) | RED = dominance en hausse (risk-off) ».

Pas de recalibrage dashboard — inversion unique à l'enregistrement (évite la double conversion).

---

## 1. Symboles et timeframes

| Symbole    | 15 min | 30 min | 1H (optionnel) | 4H      | 1D      | Utilisation dans le bot        |
|------------|--------|--------|----------------|---------|---------|--------------------------------|
| TOTAL3ES   | Non    | Non    | Oui            | **Oui** | **Oui** | Filtre macro principal         |
| OTHERS     | Non    | Non    | Oui            | **Oui** | **Oui** | Filtre mid/small caps          |
| USDT.D     | Non    | Non    | Oui            | **Oui** | **Oui** | Filtre sentiment stables       |
| BTCUSDT    | Non    | Oui    | **Oui**        | **Oui** | **Oui** | Corrélation / confirmation     |

**Timeframes prioritaires** : 4H et 1D pour la macro.  
**1H** : optionnel (contexte intermédiaire).  
**BTCUSDT** : seul symbole avec analyse **30 min** en plus.

**Run macro complet** : 4 symboles × 2 TF (4H, 1D) × 3 agents = **24 analyses**.

---

## 2. Indicateurs et intégration

| Indicateur               | Peut-on l’intégrer ? | Recommandation pour macro 4H/1D | Commentaire                                              |
|--------------------------|----------------------|---------------------------------|----------------------------------------------------------|
| **Ichimoku**             | Oui                  | Très recommandé                 | Prix > Cloud + Tenkan > Kijun                            |
| **RSI(14)**              | Oui                  | Très recommandé                 | > 50 (idéal > 55)                                        |
| **EMA50 vs EMA200**      | Oui                  | Recommandé                      | EMA50 > EMA200 ou Prix > EMA200                          |
| **MACD (12,26,9)**       | Oui                  | Recommandé                      | MACD > Signal + Histogramme > 0                          |
| **Volume**               | Oui                  | Utile en complément             | Volume > moyenne 20 périodes ou en hausse                |
| **BB (Bollinger Bands)** | Oui                  | Optionnel / bon complément      | Prix > Middle Band ou squeeze (volatilité faible)        |

---

## 3. Agents spécialisés

Chaque agent analyse les **mêmes indices** (TOTAL3ES, OTHERS, USDT.D, BTCUSDT) sur les timeframes définis, avec sa paire d’indicateurs et **sa propre grille de notation /10** (prompts 3 étapes dans `src/prompts.py`).

| Agent | Indicateurs | Grille (résumé) | Rôle macro |
|-------|-------------|-----------------|------------|
| **A** | Ichimoku + RSI(14) | Kumo, Tenkan/Kijun, RSI, confluence | Tendance + momentum haussier |
| **B** | Bollinger Bands + Volume | Position vs bandes, expansion, volume | Volatilité + flux haussier |
| **C** | EMA50/200 + MACD | Prix vs EMA, croisement, MACD, divergence | Tendance structurelle haussière |

Les colonnes du dashboard utilisent les **noms d’indicateurs** (pas « Agent A/B/C ») :

- Ichimoku + RSI  
- BB + Volume  
- EMA50/200 + MACD  

### Verdicts (dérivés du score)

| Code    | Score | Signification                                      |
|---------|-------|----------------------------------------------------|
| GREEN   | 8–10  | Contexte haussier fort (grille)                    |
| YELLOW  | 5–7   | Neutre / mitigé — prudence                         |
| RED     | 0–4   | Contexte défavorable — peu ou pas de signal haussier |

---

## 4. Exemples de tableaux (format dashboard)

Légende des colonnes : **Score** = points grille `/10` (force haussière), **pas** une note de confiance indépendante.

### Option A — Deux tableaux séparés (recommandé, page `/macro`)

**Timeframe 4H** — Confiance sur 10 = score grille haussier

| Symbole  | Ichimoku + RSI | BB + Volume | EMA50/200 + MACD | Consensus |
|----------|----------------|-------------|------------------|-----------|
| TOTAL3ES | RED 3/10       | RED 2/10    | RED 1/10         | RED 2/10  |
| OTHERS   | RED 4/10       | YELLOW 6/10 | RED 3/10         | RED 4/10  |
| USDT.D   | YELLOW 5/10    | GREEN 8/10  | RED 2/10         | YELLOW 5/10 |
| BTCUSDT  | RED 3/10       | RED 4/10    | RED 2/10         | RED 3/10  |

**Timeframe 1D**

| Symbole  | Ichimoku + RSI | BB + Volume | EMA50/200 + MACD | Consensus |
|----------|----------------|-------------|------------------|-----------|
| TOTAL3ES | RED 2/10       | RED 3/10    | RED 2/10         | RED 2/10  |
| OTHERS   | RED 3/10       | RED 4/10    | YELLOW 5/10      | RED 4/10  |
| USDT.D   | RED 4/10       | RED 3/10    | RED 2/10         | RED 3/10  |
| BTCUSDT  | RED 2/10       | RED 3/10    | RED 1/10         | RED 2/10  |

### Option B — Un seul tableau combiné (vue synthétique)

| Symbole  | Agent A (Score) | Agent B (Score) | Agent C (Score) | Consensus |
|----------|-----------------|-----------------|-----------------|-----------|
| TOTAL3ES | RED 3/10        | RED 2/10        | RED 1/10        | RED       |
| OTHERS   | RED 4/10        | YELLOW 6/10     | RED 3/10        | RED       |
| USDT.D   | YELLOW 5/10     | GREEN 8/10      | RED 2/10        | YELLOW    |
| BTCUSDT  | RED 3/10        | RED 4/10        | RED 2/10        | RED       |

*(Exemple illustratif 4H ; les valeurs réelles proviennent du dernier `run_batch()` en mémoire.)*

---

## 5. Synthèse macro (exemple)

| Symbole  | Signal GREEN (4H+1D) | Verdict global | Détail |
|----------|----------------------|----------------|--------|
| TOTAL3ES | 0/2                  | RED 2/10       | Dominant RED 2/2 — Signal GREEN 0/2 |
| OTHERS   | 0/2                  | RED 4/10       | Dominant RED 2/2 — Signal GREEN 0/2 |
| USDT.D   | 0/2                  | RED 3/10       | Dominant RED 2/2 — Signal GREEN 0/2 |
| BTCUSDT  | 0/2                  | RED 2/10       | Dominant RED 2/2 — Signal GREEN 0/2 |

**Totaux consensus** (8 cellules max) : GREEN 0 · YELLOW 1 · RED 7  
**Commentaire auto** : aucun signal GREEN — contexte macro défavorable.

---

## 6. Rôles par symbole

| Symbole  | Rôle dans la décision macro                          |
|----------|------------------------------------------------------|
| TOTAL3ES | Filtre macro principal (marché crypto global hors BTC) |
| OTHERS   | Filtre mid/small caps (appétit pour les alts)        |
| USDT.D   | Sentiment stables (USDT dominance — risk-on / off)   |
| BTCUSDT  | Corrélation et confirmation (référence du marché)    |

---

## 7. Lien avec le projet Visio_Gemini

| Élément              | Fichier / config                                     |
|----------------------|------------------------------------------------------|
| Prompts actifs       | `prompts/agent_*.txt` + `prompts/usdt_d_context.txt` |
| Capture TradingView  | `modules/capture/tv_capture.py` — layouts par agent  |
| Config symboles / TF | `config.yaml`                                        |
| Parsing score/verdict| `modules/agent/verdict_parser.py` → `parse_verdict()` |
| Résultats pipeline   | `AnalysisResult` en mémoire (`modules/analyse/`)     |
| Lancement macro      | `run_batch(build_macro_requests())` (24 jobs)        |
| Dashboard web        | FastAPI + HTMX port **8003** (mémoire, ÉTAPE 6)      |

**État actuel** : les **3 agents** (A, B, C) sont actifs sur la grille 4H + 1D × 4 symboles via `modules/`.

---

## 8. Grilles de notation (référence rapide)

Détail complet dans `prompts/agent_*.txt`. Résumé :

### Agent A — Ichimoku + RSI (max 10 pts)

| Condition | Points |
|-----------|--------|
| Prix au-dessus du Kumo | +3 |
| Prix dans le Kumo | +1 |
| Prix sous le Kumo | +0 |
| Tenkan > Kijun | +2 |
| Kumo futur vert / expansion | +2 |
| RSI > 50 en hausse | +2 |
| Confluence (≥ 3 pts alignés) | +1 |

### Agent B — BB + Volume (max 10 pts)

| Condition | Points |
|-----------|--------|
| Prix entre Middle et Upper | +3 |
| Bandes en expansion | +2 |
| Volume en hausse | +2 |
| … | (voir prompt) |

### Agent C — EMA + MACD (max 10 pts)

| Condition | Points |
|-----------|--------|
| Prix au-dessus des deux EMA | +3 |
| EMA50 > EMA200 | +2 |
| MACD > signal | +2 |
| … | (voir prompt) |
