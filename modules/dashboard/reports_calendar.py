"""Calendrier journalier PnL — flashs Visio Gemini (filtres alignés backtest TEMPO)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from modules.dashboard.backtest_temporal import (
    BACKTEST_TEMPO_DEFAULT_BTC_FAIBLE,
    BACKTEST_TEMPO_DEFAULT_BTC_OK,
    BACKTEST_TEMPO_DEFAULT_BTC_REPRISE,
    BACKTEST_TEMPO_DEFAULT_LEVERAGE,
    BACKTEST_TEMPO_DEFAULT_REGIME_NON,
    BACKTEST_TEMPO_DEFAULT_REGIME_OUI,
    BACKTEST_TEMPO_DEFAULT_SL,
    BACKTEST_TEMPO_DEFAULT_TP,
    BACKTEST_TEMPO_DEFAULT_TREND_0,
    BACKTEST_TEMPO_DEFAULT_TREND_5,
    BACKTEST_TEMPO_DEFAULT_TREND_10,
    VISIO_PROJECT_MIN_DATE,
    VISIO_PROJECT_MIN_DATE_STR,
    _utc_today,
    resolve_temporal_filter,
)
from modules.dashboard.btc_regime_filter import normalize_regime_etats
from modules.dashboard.btc_trend_filter import normalize_trend_scores
from modules.triggers import db_manager

CALENDAR_MIN_DATE = VISIO_PROJECT_MIN_DATE
CALENDAR_MIN_DATE_STR = VISIO_PROJECT_MIN_DATE_STR


def _clamp_date_from(date_from: str | None) -> str:
    if not date_from or date_from < CALENDAR_MIN_DATE_STR:
        return CALENDAR_MIN_DATE_STR
    return date_from


def _entry_ts_iso(flash_ts: str) -> str:
    s = str(flash_ts or "").strip()
    if not s:
        return ""
    if "T" in s:
        return s[:19]
    return s.replace(" ", "T", 1)[:19]


def _exit_ts_iso(exit_at_ms: int | float | None) -> str:
    if exit_at_ms is None:
        return ""
    try:
        ms = float(exit_at_ms)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _calendar_exit_reason(row: dict[str, Any]) -> str:
    outcome = str(row.get("outcome") or "").upper()
    if outcome == "LIQUIDATION":
        return "LIQ"
    code = str(row.get("resultat") or "")
    if code == "EN_COURS":
        return "OPEN"
    if code == "TP":
        return "TP"
    if code == "SL":
        return "SL"
    if code == "CLO_24H":
        return "TIMEOUT"
    if code == "ERR":
        return "ERR"
    return code or "—"


def _row_to_calendar_trade(row: dict[str, Any]) -> dict[str, Any]:
    pnl_total = row.get("_pnl_all")
    if pnl_total is None and row.get("resultat") == "EN_COURS":
        pnl_total = row.get("pnl_pct_raw")
    pnl_realise = row.get("_pnl_raw")
    return {
        "symbol": row.get("token") or "",
        "entry_ts": _entry_ts_iso(str(row.get("flash_ts") or "")),
        "exit_ts": _exit_ts_iso(row.get("exit_at_ms")),
        "duration_min": row.get("exit_minutes"),
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "pnl_pct": float(pnl_total) if pnl_total is not None else 0.0,
        "pnl_realise": float(pnl_realise) if pnl_realise is not None else None,
        "exit_reason": _calendar_exit_reason(row),
        "pnl_provisional": bool(row.get("provisional")),
        "trend_h4_score": row.get("trend_h4_score"),
        "trend_h4_badge": row.get("trend_h4_badge"),
    }


def _day_win_rate(trades: list[dict[str, Any]]) -> float:
    tp = sum(1 for t in trades if t["exit_reason"] == "TP")
    sl = sum(1 for t in trades if t["exit_reason"] in ("SL", "LIQ"))
    closed = tp + sl
    if closed == 0:
        return 0.0
    return round(tp / closed * 100, 1)


def _aggregate_days(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        flash_ts = str(row.get("flash_ts") or "")
        day_key = flash_ts[:10] if len(flash_ts) >= 10 else ""
        if not day_key:
            continue
        by_day[day_key].append(_row_to_calendar_trade(row))

    days: list[dict[str, Any]] = []
    for day_key in sorted(by_day.keys()):
        trades = sorted(by_day[day_key], key=lambda t: t.get("entry_ts") or "")
        trade_rows = [t for t in trades if t["exit_reason"] in ("TP", "SL", "OPEN", "TIMEOUT")]
        pnl_realise = round(
            sum(t["pnl_realise"] for t in trades if t["pnl_realise"] is not None),
            2,
        )
        pnl_total = round(sum(t["pnl_pct"] for t in trades), 2)
        open_count = sum(1 for t in trades if t["exit_reason"] == "OPEN")
        has_open = open_count > 0
        days.append(
            {
                "date": day_key,
                "n": len(trade_rows),
                "n_signaux": len(trades),
                "pnl": pnl_total,
                "pnl_realise": pnl_realise,
                "pnl_total": pnl_total,
                "pnl_total_provisional": has_open,
                "win_rate": _day_win_rate(trades),
                "open_count": open_count,
                "trades": trades,
            }
        )
    return days


def _compute_calendar_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from modules.dashboard.routes import is_simulated_trade

    tp = sum(1 for r in rows if r.get("resultat") == "TP")
    sl = sum(1 for r in rows if r.get("resultat") == "SL")
    en_cours = sum(1 for r in rows if r.get("resultat") == "EN_COURS")
    clo = sum(1 for r in rows if r.get("resultat") == "CLO_24H")
    err = sum(1 for r in rows if r.get("resultat") == "ERR")
    trades = sum(1 for r in rows if is_simulated_trade(r))
    n_signaux = len(rows)
    pnls_realise = [r["_pnl_raw"] for r in rows if r.get("_pnl_raw") is not None]
    pnls_total = [r["_pnl_all"] for r in rows if r.get("_pnl_all") is not None]
    pnl_realise = round(sum(pnls_realise), 2) if pnls_realise else 0.0
    pnl_total = round(sum(pnls_total), 2) if pnls_total else 0.0
    has_open = en_cours > 0
    closed = tp + sl
    win_rate = round(tp / closed * 100, 1) if closed else 0.0
    return {
        "trades": trades,
        "total": trades,
        "n_signaux": n_signaux,
        "tp": tp,
        "sl": sl,
        "en_cours": en_cours,
        "clo_24h": clo,
        "err": err,
        "win_rate": win_rate,
        "pnl_realise": pnl_realise,
        "pnl_total": pnl_total,
        "pnl_total_provisional": has_open,
    }


def build_calendar_data(
    *,
    leverage: float,
    tp: float,
    sl: float,
    date_from: str | None = None,
    date_to: str | None = None,
    filtres: list[str] | None = None,
    btc_ok: bool = BACKTEST_TEMPO_DEFAULT_BTC_OK,
    btc_reprise: bool = BACKTEST_TEMPO_DEFAULT_BTC_REPRISE,
    btc_faible: bool = BACKTEST_TEMPO_DEFAULT_BTC_FAIBLE,
    regime_oui: bool = BACKTEST_TEMPO_DEFAULT_REGIME_OUI,
    regime_non: bool = BACKTEST_TEMPO_DEFAULT_REGIME_NON,
    trend_10: bool = BACKTEST_TEMPO_DEFAULT_TREND_10,
    trend_5: bool = BACKTEST_TEMPO_DEFAULT_TREND_5,
    trend_0: bool = BACKTEST_TEMPO_DEFAULT_TREND_0,
    regime_overrides: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """Simule les flashs filtrés et agrège le calendrier journalier."""
    from modules.dashboard.routes import (
        _backtest_row_from_sim,
        _call_simulate_one,
        _fetch_backtest_flashes_temporal,
        _include_flash_in_backtest_sim,
        _normalize_backtest_etats,
        _normalize_backtest_filtres,
        count_simulated_trades,
    )

    effective_from = _clamp_date_from(date_from)
    effective_to = date_to or _utc_today().isoformat()
    filtres_norm = _normalize_backtest_filtres(filtres)
    etats = _normalize_backtest_etats(
        btc_ok=btc_ok,
        btc_reprise=btc_reprise,
        btc_faible=btc_faible,
    )
    regime_etats = normalize_regime_etats(
        regime_oui=regime_oui,
        regime_non=regime_non,
    )
    trend_scores = normalize_trend_scores(
        trend_10=trend_10,
        trend_5=trend_5,
        trend_0=trend_0,
    )
    temporal = resolve_temporal_filter(
        date_debut=effective_from,
        date_fin=effective_to,
        heure_debut="00:00",
        heure_fin="23:59",
    )

    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes_temporal(
            conn,
            filtres=filtres_norm,
            etats=etats,
            temporal=temporal,
            regime_etats=regime_etats,
            regime_overrides=regime_overrides,
            trend_scores=trend_scores,
        )
    finally:
        conn.close()

    sim_rows: list[dict[str, Any]] = []
    for flash in flashes:
        if not _include_flash_in_backtest_sim(flash, etats):
            continue
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
        sim_rows.append(row)

    days = _aggregate_days(sim_rows)
    stats = _compute_calendar_stats(sim_rows)
    return {
        "ok": True,
        "n_trades": count_simulated_trades(sim_rows),
        "n_signaux": len(sim_rows),
        "date_from": temporal.date_debut.isoformat(),
        "date_to": temporal.date_fin.isoformat(),
        "regime_overrides_applied": len(regime_overrides or {}),
        "stats": stats,
        "data": {"days": days},
    }
