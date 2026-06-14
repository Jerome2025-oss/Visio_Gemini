"""Sources d'événements (macro, manual, webhook, telegram) — ÉTAPE 7.

Module ``telegram`` opérationnel : écoute d'un canal Telegram (Telethon) qui
déclenche automatiquement l'entonnoir Ichimoku puis enregistre le résultat.

  - ``auto_listener``   : écoute Telegram + déclenchement de l'analyse
  - ``db_manager``      : persistance SQLite (analyses_ichimoku)
  - ``btc_context``     : contexte macro BTC H4 (post-analyse, non bloquant)
  - ``compare_results`` : rapprochement fin de journée score IA ↔ PnL réel
"""
