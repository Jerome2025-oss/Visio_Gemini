"""Batterie backtest TEMPO — synthèse multi-scénarios (voyants BTC)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from modules.dashboard.backtest_temporal import (
    filter_flashes_temporal,
    resolve_temporal_filter,
    temporal_interval_label,
    temporal_period_summary,
)
from modules.triggers import db_manager

VOYANT_LABELS: dict[frozenset[str], str] = {
    frozenset({db_manager.BTC_ETAT_OK}): "🟢 OK seul",
    frozenset({db_manager.BTC_ETAT_REPRISE}): "✅ REPRISE seule",
    frozenset({db_manager.BTC_ETAT_FAIBLE}): "🔴 FAIBLE seul",
    frozenset({db_manager.BTC_ETAT_OK, db_manager.BTC_ETAT_REPRISE}): "🟢 OK + ✅ REPRISE",
    frozenset({db_manager.BTC_ETAT_OK, db_manager.BTC_ETAT_FAIBLE}): "🟢 OK + 🔴 FAIBLE",
    frozenset(
        {db_manager.BTC_ETAT_REPRISE, db_manager.BTC_ETAT_FAIBLE}
    ): "✅ REPRISE + 🔴 FAIBLE",
    frozenset(
        {
            db_manager.BTC_ETAT_OK,
            db_manager.BTC_ETAT_REPRISE,
            db_manager.BTC_ETAT_FAIBLE,
        }
    ): "Tous voyants",
}

@dataclass(frozen=True)
class BatteryConfig:
    date_debut: str | None = None
    date_fin: str | None = None
    heure_debut: str = "00:00"
    heure_fin: str = "23:59"
    leverage: float = 30.0
    tp: float = 1.4
    sl: float = 2.0
    # Conservé pour compat API, mais BTC ON/OFF est retiré.
    regime_overrides: dict[tuple[str, str], str] | None = None


ProgressFn = Callable[[int, int], None]


def _simulate_all(
    flashes: list[dict[str, Any]],
    *,
    leverage: float,
    tp: float,
    sl: float,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    from modules.dashboard.routes import (
        _backtest_row_from_sim,
        _call_simulate_one,
    )

    rows: list[dict[str, Any]] = []
    n = len(flashes)
    for i, flash in enumerate(flashes, 1):
        ts = flash.get("flash_ts")
        if not ts:
            row = _backtest_row_from_sim(flash, None)
        else:
            payload = _call_simulate_one(
                symbol=str(flash["token"]),
                flash_ts=str(ts),
                leverage=leverage,
                tp=tp,
                sl=sl,
            )
            row = _backtest_row_from_sim(flash, payload)
        row["btc_score"] = flash.get("btc_score")
        rows.append(row)
        if on_progress:
            on_progress(i, n)
    return rows


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    etats: frozenset[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("btc_etat") not in etats:
            continue
        out.append(r)
    return out


def _scenario_row(
    *,
    voyant_label: str,
    voyant_key: frozenset[str],
    all_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    from modules.dashboard.routes import _compute_backtest_stats

    subset = _filter_rows(all_rows, etats=voyant_key)
    stats = _compute_backtest_stats(subset)
    return {
        "voyant_label": voyant_label,
        "voyant_filter": sorted(voyant_key),
        "stats": stats,
    }


def _tous_voyants() -> frozenset[str]:
    return frozenset(
        {
            db_manager.BTC_ETAT_OK,
            db_manager.BTC_ETAT_REPRISE,
            db_manager.BTC_ETAT_FAIBLE,
        }
    )


def _pnl_raw(row: dict[str, Any]) -> float | None:
    raw = row.get("_pnl_all")
    if raw is None:
        raw = row.get("_pnl_raw")
    return float(raw) if raw is not None else None


def _format_pnl_sum(pnl_sum: float) -> str:
    return f"{pnl_sum:+.2f}%"


def run_battery(
    config: BatteryConfig,
    *,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Simule tous les flashs de la période puis agrège des scénarios par voyant BTC."""
    from modules.dashboard.routes import (
        _fetch_backtest_flashes,
        _normalize_backtest_etats,
    )

    temporal = resolve_temporal_filter(
        date_debut=config.date_debut,
        date_fin=config.date_fin,
        heure_debut=config.heure_debut,
        heure_fin=config.heure_fin,
    )
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes(
            conn,
            filtres=[],
            etats=_normalize_backtest_etats(),
            regime_etats=None,
            regime_overrides=config.regime_overrides,
        )
    finally:
        conn.close()
    flashes = filter_flashes_temporal(flashes, temporal)
    all_rows = _simulate_all(
        flashes,
        leverage=config.leverage,
        tp=config.tp,
        sl=config.sl,
        on_progress=on_progress,
    )

    scenarios: list[dict[str, Any]] = []
    for voyant_key, voyant_label in VOYANT_LABELS.items():
        scenarios.append(
            _scenario_row(
                voyant_label=voyant_label,
                voyant_key=voyant_key,
                all_rows=all_rows,
            )
        )

    return {
        "ok": True,
        "params": {
            "date_debut": temporal.date_debut.isoformat(),
            "date_fin": temporal.date_fin.isoformat(),
            "heure_debut": temporal.heure_debut,
            "heure_fin": temporal.heure_fin,
            "leverage": config.leverage,
            "tp": config.tp,
            "sl": config.sl,
            "filtres": "Tous",
            "n_flashes_total": len(all_rows),
        },
        "period_summary": temporal_period_summary(temporal, len(all_rows)),
        "interval_label": temporal_interval_label(temporal),
        "scenarios": scenarios,
    }


def export_battery_files(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    stamp: str | None = None,
) -> dict[str, str]:
    """Écrit CSV + JSON + MD dans out_dir. Retourne les chemins."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    params = payload["params"]
    scenarios = payload["scenarios"]

    json_path = out_dir / f"battery_{stamp}.json"
    csv_path = out_dir / f"battery_{stamp}.csv"
    md_path = out_dir / f"battery_{stamp}.md"

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "voyant",
        "n",
        "tp",
        "sl",
        "wr",
        "pnl_total",
        "tp_pct",
        "sl_pct",
        "en_cours",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in scenarios:
            st = s["stats"]
            w.writerow(
                {
                    "voyant": s["voyant_label"],
                    "n": st["total"],
                    "tp": st["tp"],
                    "sl": st["sl"],
                    "wr": st["wr"],
                    "pnl_total": st["pnl_total"],
                    "tp_pct": st["tp_pct"],
                    "sl_pct": st["sl_pct"],
                    "en_cours": st["en_cours"],
                }
            )

    lines = [
        f"# Batterie Backtest — {params['date_debut']} → {params['date_fin']}",
        "",
        f"- **Levier** {params['leverage']} · **TP** {params['tp']}% · **SL** {params['sl']}%",
        f"- **Flashs simulés** {params['n_flashes_total']}",
        "",
        "| Voyant | N | TP | SL | WR | PnL total |",
        "|--------|---:|---:|---:|---:|----------:|",
    ]
    for s in scenarios:
        st = s["stats"]
        lines.append(
            f"| {s['voyant_label']} | {st['total']} | {st['tp']} | {st['sl']} | {st['wr']} | {st['pnl_total']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "md": str(md_path),
    }
