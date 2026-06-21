"""Mise à jour Date ON/OFF BTC — script oneshot pour systemd timer.

Équivalent du bouton « Mettre à jour le tableau » (capture Ten Kan + Gemini + DB).

Usage :
    python -m modules.schedulers.btc_regime_scheduled
    python -m modules.schedulers.btc_regime_scheduled --days-window 5
"""

from __future__ import annotations

import argparse
import logging
import sys

from modules.triggers import btc_regime_dates

logger = logging.getLogger("visio_gemini.schedulers.btc_regime")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Date ON/OFF BTC — run planifié")
    parser.add_argument(
        "--days-window",
        type=int,
        default=None,
        help="Fenêtre TradingView (défaut : DAYS_WINDOW du module)",
    )
    args = parser.parse_args(argv)

    days_window = btc_regime_dates.resolve_days_window(args.days_window)
    logger.info(
        "🚀 Démarrage Date ON/OFF planifié (days_window=%s, UTC)…",
        days_window,
    )
    result = btc_regime_dates.run_regime_dates_update(days_window=days_window)
    if not result.get("ok"):
        logger.error(
            "❌ Date ON/OFF planifié échoué : %s",
            result.get("error", "erreur inconnue"),
        )
        return 1
    logger.info(
        "✅ Date ON/OFF planifié terminé — %s nouveau(x) créneau(x), %s au total, run=%s",
        result.get("n_inserted", 0),
        result.get("n_rows", 0),
        result.get("run_id"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
