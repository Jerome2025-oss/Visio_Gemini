"""Batterie backtest TEMPO — synthèse multi-scénarios (voyants + tendance BTC H4)."""

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

TREND_LABELS: dict[frozenset[int], str] = {
    frozenset({10}): "🟢 10 seul",
    frozenset({5}): "🟡 5 seul",
    frozenset({0}): "🔴 0 seul",
    frozenset({10, 5}): "🟢 10 + 🟡 5",
    frozenset({10, 0}): "🟢 10 + 🔴 0",
    frozenset({5, 0}): "🟡 5 + 🔴 0",
    frozenset({10, 5, 0}): "Toutes tendances",
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
        row["trend_h4_score"] = flash.get("trend_h4_score")
        row["trend_h4_badge"] = flash.get("trend_h4_badge")
        rows.append(row)
        if on_progress:
            on_progress(i, n)
    return rows


def _filter_rows_combined(
    rows: list[dict[str, Any]],
    *,
    etats: frozenset[str],
    scores: frozenset[int],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("btc_etat") not in etats:
            continue
        score = r.get("trend_h4_score")
        if score is None:
            continue
        if int(score) in scores:
            out.append(r)
    return out


def _scenario_matrix_row(
    *,
    voyant_label: str,
    voyant_key: frozenset[str],
    trend_label: str,
    trend_key: frozenset[int],
    all_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    from modules.dashboard.routes import _compute_backtest_stats

    subset = _filter_rows_combined(all_rows, etats=voyant_key, scores=trend_key)
    stats = _compute_backtest_stats(subset)
    return {
        "dimension": "matrix",
        "label": f"{trend_label} × {voyant_label}",
        "voyant_label": voyant_label,
        "trend_label": trend_label,
        "voyant_filter": sorted(voyant_key),
        "trend_filter": sorted(trend_key),
        "stats": stats,
    }


def run_battery(
    config: BatteryConfig,
    *,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Simule tous les flashs puis agrège scénarios voyant × tendance H4 (49 combinaisons)."""
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

    scenarios_matrix: list[dict[str, Any]] = []
    for trend_key, trend_label in TREND_LABELS.items():
        for voyant_key, voyant_label in VOYANT_LABELS.items():
            scenarios_matrix.append(
                _scenario_matrix_row(
                    voyant_label=voyant_label,
                    voyant_key=voyant_key,
                    trend_label=trend_label,
                    trend_key=trend_key,
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
        "scenarios_matrix": scenarios_matrix,
        "n_scenarios": len(scenarios_matrix),
        # Rétrocompat : alias principal.
        "scenarios": scenarios_matrix,
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
    scenarios_matrix = payload.get("scenarios_matrix") or payload.get("scenarios") or []

    json_path = out_dir / f"battery_{stamp}.json"
    csv_path = out_dir / f"battery_{stamp}.csv"
    md_path = out_dir / f"battery_{stamp}.md"

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "tendance_h4",
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
        for s in scenarios_matrix:
            st = s["stats"]
            w.writerow(
                {
                    "tendance_h4": s.get("trend_label"),
                    "voyant": s.get("voyant_label"),
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
        f"- **Scénarios** {len(scenarios_matrix)} (tendance H4 × voyant)",
        "",
        "| Tendance H4 | Voyant | N | TP | SL | WR | PnL total |",
        "|-------------|--------|---:|---:|---:|---:|----------:|",
    ]
    current_trend = None
    for s in scenarios_matrix:
        trend = s.get("trend_label") or ""
        if trend != current_trend:
            current_trend = trend
        st = s["stats"]
        lines.append(
            f"| {s.get('trend_label')} | {s.get('voyant_label')} | {st['total']} | {st['tp']} | {st['sl']} | {st['wr']} | {st['pnl_total']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "md": str(md_path),
    }
