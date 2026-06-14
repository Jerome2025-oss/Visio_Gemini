"""Scan BTC H4 planifié — script oneshot pour systemd timer.

Usage standalone :
    python -m modules.schedulers.btc_h4_scheduled
"""

from __future__ import annotations

import logging
import sys

from modules.triggers import btc_context

logger = logging.getLogger("visio_gemini.schedulers.btc_h4")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("🚀 Démarrage scan BTC H4 planifié (oneshot)…")
    score = btc_context.run_btc_h4_scheduled_scan()
    if score is None:
        logger.error("❌ Scan BTC H4 planifié terminé sans score.")
        return 1
    logger.info("✅ Scan BTC H4 planifié terminé — score %s/10", score)
    return 0


if __name__ == "__main__":
    sys.exit(main())
