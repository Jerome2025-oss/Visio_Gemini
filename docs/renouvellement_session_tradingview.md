# 🔄 RENOUVELLEMENT SESSION TRADINGVIEW — Visio_Gemini

## Contexte

Le bot macro capture des charts TradingView via une session authentifiée (cookies)
exportée depuis ton navigateur Windows et déployée sur le VPS sous
`secrets/storage_state.json`.

Quand la session TradingView expire (abonnement ou cookie invalide), les captures
échouent : page « Sign in », layouts personnalisés absents, erreur
« Session TradingView expirée ».

---

## 1. SYMPTÔMES D'UNE SESSION EXPIRÉE

Tu sauras que la session a expiré si :

- Les captures montrent « Sign in » ou « Create account »
- Le tooltip « Logged in as ggsirius » a disparu en haut à gauche
- Les layouts personnalisés (Ichimoku, BB, EMA…) ne sont plus chargés
- Les captures montrent un chart générique sans tes indicateurs
- Les logs affichent une redirection vers `/accounts/signin`

---

## 2. CE QU'IL FAUT FAIRE — ÉTAPE PAR ÉTAPE

### ÉTAPE 1 — Renouveler ton abonnement TradingView (si nécessaire)

- Connecte-toi sur https://www.tradingview.com
- Menu : Profil → Abonnement → Renouveler
- Compte concerné : **ggsirius**
- Vérifie que l'abonnement est actif avant de continuer

---

### ÉTAPE 2 — Exporter les nouveaux cookies depuis ton navigateur Windows

#### Sur Chrome

1. Installe l'extension **Cookie-Editor** (icône bleue, Chrome Web Store)
2. Va sur https://www.tradingview.com et connecte-toi avec **ggsirius**
3. Attends d'être bien connecté (avatar « G » visible en haut à droite)
4. Coche **« Remember me »** / **« Stay signed in »** si proposé
5. Clique sur l'icône Cookie-Editor → **Export** → format **JSON**
6. Sauvegarde le fichier sous `tv_cookies_raw.json`

#### Sur Firefox

1. Installe **Cookie Quick Manager**
2. Va sur https://www.tradingview.com connecté
3. Export → JSON → sauvegarde sous `tv_cookies_raw.json`

#### Alternative — export Playwright (recommandé)

Sans extension, depuis le projet Bitunix_P sur Windows :

```powershell
cd Detecte_Pump_Bitunix_P
python tools/export_tv_session.py
scp storage_state.json root@TON_VPS_IP:~/Visio_Gemini/secrets/storage_state.json
```

→ Passe directement aux étapes 4 et 5 (permissions + test), sans conversion cookies.

---

### ÉTAPE 3 — Transférer les cookies sur le VPS

Depuis Windows CMD ou PowerShell :

```bash
scp C:\Users\TON_USER\Desktop\tv_cookies_raw.json root@TON_VPS_IP:~/Visio_Gemini/secrets/tv_cookies_raw.json
```

Remplace `TON_USER` et `TON_VPS_IP`.

---

### ÉTAPE 3b — Convertir les cookies en `storage_state.json` (VPS)

Si tu as utilisé Cookie-Editor (pas l'export Playwright) :

```bash
cd ~/Visio_Gemini && source venv/bin/activate
python tools/tv_convert_cookies.py
```

Le script lit `secrets/tv_cookies_raw.json` et produit `secrets/storage_state.json`.

---

### ÉTAPE 4 — Vérifier les permissions sur le VPS

```bash
chmod 600 ~/Visio_Gemini/secrets/storage_state.json
chmod 700 ~/Visio_Gemini/secrets
```

---

### ÉTAPE 5 — Tester la capture immédiatement

```bash
cd ~/Visio_Gemini && source venv/bin/activate
./venv/bin/python -m src.capture
```

Vérifie le PNG généré dans `captures/` :

- ✅ Tooltip « Logged in as ggsirius » visible
- ✅ Layout agent chargé (Ichimoku / BB / EMA selon l'agent testé)
- ✅ Données live présentes

---

### ÉTAPE 6 — Relancer un scan macro (optionnel)

```bash
cd ~/Visio_Gemini && ./venv/bin/python -m src.main --macro
```

Pour rafraîchir le dashboard Bitunix :

```bash
sudo systemctl restart detecte-pump-api
```

---

## 3. COOKIES IMPORTANTS À VÉRIFIER

Dans `tv_cookies_raw.json`, les cookies critiques :

| Nom du cookie    | Rôle                                      |
|------------------|-------------------------------------------|
| `sessionid`      | 🔑 Session principale (le plus important) |
| `sessionid_sign` | Signature cryptographique de la session   |
| `tv_ecuid`       | Identifiant unique navigateur             |
| `tv_gpxuid`      | Tracking session                          |
| `_sp_ses.*`      | Session Snowplow analytics                |

> ⚠️ Si `sessionid` est absent ou expiré, rien ne marchera.

---

## 4. DURÉE DE VIE DES COOKIES

| Élément                  | Durée estimée                          |
|--------------------------|----------------------------------------|
| Abonnement TradingView   | Selon ton plan (mensuel/annuel)        |
| Cookie `sessionid`       | ~1 an si « Remember me » coché         |
| Cookie de session simple | ~30 jours sans « Remember me »         |

> 💡 Coche toujours **« Remember me »** à la connexion pour maximiser la durée.

---

## 5. CHECKLIST RAPIDE

```
□ 1. Abonnement TradingView renouvelé sur ggsirius
□ 2. Connecté sur tradingview.com avec « Remember me » coché
□ 3. Cookies exportés (Cookie-Editor) OU storage_state via export_tv_session.py
□ 4. Fichier transféré sur VPS via scp
□ 5. Conversion cookies → storage_state si méthode Cookie-Editor
□ 6. chmod 600 appliqué sur storage_state.json
□ 7. Test capture (python -m src.capture) → PNG vérifié
□ 8. « Logged in as ggsirius » visible sur le PNG de test
```

---

## 6. EN CAS DE PROBLÈME

### « sessionid non trouvé dans le JSON »

→ Réexporte les cookies **après** t'être reconnecté sur tradingview.com  
→ Vérifie que tu es sur le domaine `tradingview.com`

### « layout non chargé, chart générique »

→ Le sessionid est bon mais le layout_id est peut-être périmé  
→ Vérifie les IDs dans `config.yaml` :

| Agent   | Layout ID  |
|---------|------------|
| agent_a | VLmoQO22   |
| agent_b | guJhPkAj   |
| agent_c | bl0W3Xyj   |

### « Playwright timeout / page ne charge pas »

```bash
ping tradingview.com
cd ~/Visio_Gemini && source venv/bin/activate && playwright install chromium
```

Vérifie aussi `.env` :

```env
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
```

### « scp refusé depuis Windows »

→ Utilise WinSCP ou FileZilla (SFTP, port 22)

---

## 7. RESSOURCES

- TradingView support : https://www.tradingview.com/support/
- Cookie-Editor Chrome : https://chrome.google.com/webstore/detail/cookie-editor/
- Playwright Python : https://playwright.dev/python/
- Doc complémentaire : [TRADINGVIEW_SESSION.md](TRADINGVIEW_SESSION.md)

---

*Document adapté depuis Detecte_Pump_Bitunix_P — Projet Visio_Gemini*  
*Compte TradingView : ggsirius*
