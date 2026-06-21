#!/usr/bin/env python3
"""CLI — batterie backtest TEMPO (paramètres TP / SL / levier / période)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.dashboard.backtest_battery import (  # noqa: E402
    BatteryConfig,
    export_battery_files,
    run_battery,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Batterie backtest TEMPO multi-filtres")
    p.add_argument("--date-debut", default="2026-06-14", help="Date début ISO (YYYY-MM-DD)")
    p.add_argument("--date-fin", default="2026-06-21", help="Date fin ISO")
    p.add_argument("--heure-debut", default="00:00")
    p.add_argument("--heure-fin", default="23:59")
    p.add_argument("--leverage", type=float, default=30.0)
    p.add_argument("--tp", type=float, default=1.4)
    p.add_argument("--sl", type=float, default=2.0)
    p.add_argument(
        "--export",
        action="store_true",
        help="Écrit CSV/JSON/MD dans data/backtest_battery/",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    config = BatteryConfig(
        date_debut=args.date_debut,
        date_fin=args.date_fin,
        heure_debut=args.heure_debut,
        heure_fin=args.heure_fin,
        leverage=args.leverage,
        tp=args.tp,
        sl=args.sl,
    )

    def on_progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"  simulation {done}/{total}", flush=True)

    print(
        f"Période {config.date_debut} → {config.date_fin} · "
        f"Levier {config.leverage} · TP {config.tp}% · SL {config.sl}%"
    )
    print("Simulation API bitunix…")
    payload = run_battery(config, on_progress=on_progress)
    n = payload["params"]["n_flashes_total"]
    print(f"Terminé — {n} flash(s) · {len(payload['scenarios'])} scénarios")

    if args.export:
        paths = export_battery_files(payload, ROOT / "data" / "backtest_battery")
        for kind, path in paths.items():
            print(f"  {kind}: {path}")


if __name__ == "__main__":
    main()
