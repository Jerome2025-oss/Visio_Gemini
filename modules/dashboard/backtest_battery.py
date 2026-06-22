"""Batterie backtest TEMPO — synthèse multi-filtres BTC ON/OFF × voyants."""

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
from modules.dashboard.btc_regime_filter import normalize_regime_etats
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

REGIME_LABELS: dict[frozenset[str], str] = {
    frozenset({"OUI"}): "OUI seul",
    frozenset({"NON"}): "NON seul (BTC OFF)",
    frozenset({"OUI", "NON"}): "OUI + NON",
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
        row["regime_onoff"] = flash.get("regime_onoff")
        row["btc_score"] = flash.get("btc_score")
        rows.append(row)
        if on_progress:
            on_progress(i, n)
    return rows


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    etats: frozenset[str],
    regime_etats: frozenset[str] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("btc_etat") not in etats:
            continue
        regime = r.get("regime_onoff")
        if regime_etats is not None and regime not in regime_etats:
            continue
        out.append(r)
    return out


def _scenario_row(
    *,
    regime_label: str,
    voyant_label: str,
    regime_key: frozenset[str],
    voyant_key: frozenset[str],
    all_rows: list[dict[str, Any]],
    regime_filter: frozenset[str] | None,
) -> dict[str, Any]:
    from modules.dashboard.routes import _compute_backtest_stats

    subset = _filter_rows(all_rows, etats=voyant_key, regime_etats=regime_filter)
    stats = _compute_backtest_stats(subset)
    return {
        "regime_label": regime_label,
        "voyant_label": voyant_label,
        "regime_filter": sorted(regime_key),
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


def _build_non_regime_bilan(
    all_rows: list[dict[str, Any]],
    *,
    resultat: str,
) -> dict[str, Any]:
    """Flashs tous voyants · créneau NON · outcome TP ou SL."""
    non_filter = normalize_regime_etats(regime_oui=False, regime_non=True)
    subset = _filter_rows(all_rows, etats=_tous_voyants(), regime_etats=non_filter)
    trades: list[dict[str, Any]] = []
    pnl_sum = 0.0
    for r in subset:
        if r.get("resultat") != resultat:
            continue
        trades.append(
            {
                "token": r["token"],
                "flash_ts": r["flash_ts"],
                "pnl_pct": r.get("pnl_pct") or "—",
            }
        )
        pnl = _pnl_raw(r)
        if pnl is not None:
            pnl_sum += pnl
    return {
        "count": len(trades),
        "trades": trades,
        "pnl_total": _format_pnl_sum(pnl_sum) if trades else "—",
        "pnl_total_raw": round(pnl_sum, 2),
    }


def _build_tp_rates_btc_non(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    bilan = _build_non_regime_bilan(all_rows, resultat="TP")
    return {**bilan, "tps": bilan["trades"]}


def _build_sl_encaisse_btc_non(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    bilan = _build_non_regime_bilan(all_rows, resultat="SL")
    return {**bilan, "sls": bilan["trades"]}


def _build_regle_off_synthese(
    tp_bilan: dict[str, Any],
    sl_bilan: dict[str, Any],
) -> dict[str, Any]:
    """Compare gains TP ratés vs pertes SL si tradé en créneau NON."""
    tp_raw = float(tp_bilan.get("pnl_total_raw") or 0)
    sl_raw = float(sl_bilan.get("pnl_total_raw") or 0)
    net = tp_raw + sl_raw
    if tp_bilan.get("count", 0) or sl_bilan.get("count", 0):
        net_label = _format_pnl_sum(net)
    else:
        net_label = "—"
    if net > 0:
        verdict = "Si tu avais tradé en OFF : bilan positif → la règle « ne pas trader » t'a fait manquer du gain net."
    elif net < 0:
        verdict = "Si tu avais tradé en OFF : bilan négatif → la règle « ne pas trader » t'a évité des pertes nettes."
    else:
        verdict = "TP ratés et SL si tradé se compensent sur cette période."
    return {
        "tp_count": tp_bilan.get("count", 0),
        "tp_pnl_total": tp_bilan.get("pnl_total", "—"),
        "tp_pnl_raw": tp_raw,
        "sl_count": sl_bilan.get("count", 0),
        "sl_pnl_total": sl_bilan.get("pnl_total", "—"),
        "sl_pnl_raw": sl_raw,
        "sl_evite_total": _format_pnl_sum(abs(sl_raw)) if sl_raw else "—",
        "net_si_trade_off": net_label,
        "net_raw": round(net, 2),
        "verdict": verdict,
    }


def run_battery(
    config: BatteryConfig,
    *,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Simule tous les flashs de la période puis agrège 21 scénarios filtres."""
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
    for regime_key, regime_label in REGIME_LABELS.items():
        regime_filter = normalize_regime_etats(
            regime_oui="OUI" in regime_key,
            regime_non="NON" in regime_key,
        )
        for voyant_key, voyant_label in VOYANT_LABELS.items():
            scenarios.append(
                _scenario_row(
                    regime_label=regime_label,
                    voyant_label=voyant_label,
                    regime_key=regime_key,
                    voyant_key=voyant_key,
                    all_rows=all_rows,
                    regime_filter=regime_filter,
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
        "tp_rates_btc_non": (tp_b := _build_tp_rates_btc_non(all_rows)),
        "sl_encaisse_btc_non": (sl_b := _build_sl_encaisse_btc_non(all_rows)),
        "regle_off_synthese": _build_regle_off_synthese(tp_b, sl_b),
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
        "regime",
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
                    "regime": s["regime_label"],
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
        "| Régime | Voyant | N | TP | SL | WR | PnL total |",
        "|--------|--------|---:|---:|---:|---:|----------:|",
    ]
    for s in scenarios:
        st = s["stats"]
        lines.append(
            f"| {s['regime_label']} | {s['voyant_label']} | {st['total']} | {st['tp']} | {st['sl']} | {st['wr']} | {st['pnl_total']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "md": str(md_path),
    }
