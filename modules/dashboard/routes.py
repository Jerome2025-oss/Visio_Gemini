"""Routes HTTP du dashboard Visio Gemini."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from modules.analyse.funnel import _decision_color, format_score_fr, run_funnel
from modules.analyse.orchestrator import run_batch
from modules.analyse.results import AnalysisResult
from modules.config import load_app_config
from modules.dashboard.backtest_optimal_tp import build_optimal_tp_fields
from modules.dashboard.backtest_temporal import (
    TemporalFilter,
    filter_flashes_temporal,
    resolve_temporal_filter,
    temporal_interval_label,
    temporal_period_summary,
)
from modules.dashboard.btc_price import get_market_spot
from modules.dashboard.store import add_run, latest
from modules.selection.bitunix_symbols import normalize_token_key
from modules.selection.builders import build_manual_requests
from modules.triggers import btc_context, db_manager
from modules.triggers import btc_regime_dates

router = APIRouter()

# API backtest du projet Detecte_Pump_Bitunix_P (simulation TP/SL/levier par FLASH).
BITUNIX_API_URL = os.environ.get("BITUNIX_API_URL", "http://localhost:8002").rstrip("/")

# Routes en ``def`` sync (pas ``async def``) : FastAPI les exécute dans un thread pool.
# ``run_batch()`` appelle Playwright sync ; dans une coroutine asyncio cela lève
# « Sync API inside the asyncio loop ». Ne pas convertir en async sans migrer la capture.


_TIMEFRAME_ALIASES: dict[str, str] = {
    "5mn": "5m",
    "5m": "5m",
    "15mn": "15m",
    "15m": "15m",
    "30mn": "30m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "1j": "1D",
    "1D": "1D",
}

_DASHBOARD_TF_ORDER: tuple[str, ...] = ("5m", "15m", "30m", "1h", "4h", "1D")

_DASHBOARD_TF_LABELS: dict[str, str] = {
    "5m": "5mn",
    "15m": "15mn",
    "30m": "30mn",
    "1h": "1h",
    "4h": "4h",
    "1D": "1j",
}


def _normalize_timeframe(timeframe: str) -> str:
    key = timeframe.strip().lower().replace(" ", "")
    if key in _TIMEFRAME_ALIASES:
        return _TIMEFRAME_ALIASES[key]
    return timeframe.strip()


def _dashboard_timeframe_options() -> list[dict[str, str]]:
    """Options du select dashboard (libellés FR → clés config.yaml)."""
    app_config = load_app_config()
    options: list[dict[str, str]] = []
    for key in _DASHBOARD_TF_ORDER:
        if key not in app_config.timeframes:
            continue
        label = _DASHBOARD_TF_LABELS.get(key, key)
        options.append({"key": key, "value": label, "label": label})
    return options


def _results_context(
    request: Request,
    *,
    results: list[AnalysisResult] | None = None,
    run_error: str | None = None,
) -> dict:
    app_config = load_app_config()
    return {
        "request": request,
        "results": results if results is not None else latest(),
        "run_error": run_error,
        "captures_dir": app_config.paths.captures,
        "agents_config": app_config.agents,
    }


def _render_results(
    request: Request,
    *,
    results: list[AnalysisResult] | None = None,
    run_error: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_results.html",
        _results_context(request, results=results, run_error=run_error),
    )


@router.get("/api/market-spot")
def api_market_spot() -> JSONResponse:
    """Prix BTCUSDT et ETHUSDT Bitunix Perp (cache 30 s) — en-tête dashboard."""
    return JSONResponse(get_market_spot())


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.get("/manu", response_class=HTMLResponse)
def manu_page(request: Request) -> HTMLResponse:
    """Page analyse manuelle (agents + entonnoir Ichimoku)."""
    templates = request.app.state.templates
    app_config = load_app_config()
    return templates.TemplateResponse(
        request,
        "manu.html",
        {
            "request": request,
            "results": latest(),
            "run_error": None,
            "captures_dir": app_config.paths.captures,
            "agents_config": app_config.agents,
            "timeframes": _dashboard_timeframe_options(),
        },
    )


@router.get("/results", response_class=HTMLResponse)
def results_fragment(request: Request) -> HTMLResponse:
    return _render_results(request)


@router.post("/run", response_class=HTMLResponse)
def run_analysis(
    request: Request,
    symbol: Annotated[str, Form()],
    timeframe: Annotated[str, Form()],
    agents: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse:
    token = normalize_token_key(symbol)
    tf = _normalize_timeframe(timeframe)
    if not agents:
        selected_agents: list[str] = []
    elif isinstance(agents, str):
        selected_agents = [agents]
    else:
        selected_agents = list(agents)

    if not token:
        return _render_results(request, results=[], run_error="Symbole requis.")
    if not selected_agents:
        return _render_results(
            request,
            results=[],
            run_error="Sélectionnez au moins un agent.",
        )

    try:
        requests = build_manual_requests(token, tf, agents=selected_agents)
        results = run_batch(requests)
        add_run(results)
        return _render_results(request, results=results)
    except Exception as exc:
        return _render_results(request, results=[], run_error=str(exc))


@router.post("/run_funnel", response_class=HTMLResponse)
def run_funnel_route(
    request: Request,
    symbol: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Entonnoir Ichimoku 3TF (H4 → H1 → M15) — capture x3 + 1 appel vision.

    Indépendant de ``/run`` : n'altère jamais le pipeline standard.
    """
    templates = request.app.state.templates
    token = (symbol or "").strip()
    if not token:
        return templates.TemplateResponse(
            request,
            "_funnel_result.html",
            {"request": request, "funnel": None, "funnel_error": "Symbole requis."},
        )

    try:
        result = run_funnel(token)
        funnel_error = result.error
    except Exception as exc:  # garde-fou ultime — ne casse pas le dashboard
        result = None
        funnel_error = str(exc)

    return templates.TemplateResponse(
        request,
        "_funnel_result.html",
        {"request": request, "funnel": result, "funnel_error": funnel_error},
    )


def _score_color(score: float | int | None) -> str:
    """Couleur du badge de confiance : rouge ≤4, jaune 5-6, vert ≥7."""
    if score is None:
        return "muted"
    value = float(score)
    if value <= 4:
        return "red"
    if value <= 6:
        return "yellow"
    return "green"


def _pnl_color(pnl: float | None) -> str:
    """Couleur du badge PnL : vert si gain, rouge si perte, gris si nul/absent."""
    if pnl is None or pnl == 0:
        return "muted"
    return "green" if pnl > 0 else "red"


def _flash_view(row: db_manager.AnalyseRow) -> dict:
    """Transforme une ligne SQLite en modèle d'affichage (lecture seule)."""
    return {
        "id": row.id,
        "token": row.token,
        "signal_time": row.signal_time_utc,
        "analysis_time": row.analysis_time_utc,
        "score": row.score_ia,
        "score_display": format_score_fr(row.score_ia),
        "score_color": _score_color(row.score_ia),
        "decision": row.decision_ia,
        "decision_color": _decision_color(row.decision_ia, row.score_ia),
        "charts": row.charts,
        "report_text": row.recap_complet,
        "pnl": row.pnl_final,
        "pnl_color": _pnl_color(row.pnl_final),
        "exit_type": row.exit_type,
    }


def _flash_view_with_btc(row: db_manager.AnalyseRow, raw: sqlite3.Row) -> dict:
    """Modèle d'affichage FLASH enrichi du contexte BTC (fonction nouvelle)."""
    view = _flash_view(row)
    btc = btc_context.read_btc_fields_from_row(raw)
    flash_btc = _read_btc_flash_fields(raw)
    score = btc.get("btc_context_score")
    captures_dir = load_app_config().paths.captures
    view.update(
        {
            "btc_above_tenkan": btc.get("btc_above_tenkan"),
            "btc_tenkan_slope": btc.get("btc_tenkan_slope"),
            "btc_context_score": score,
            "btc_h4_snapshot": btc.get("btc_h4_snapshot"),
            "btc_chart": btc_context.resolve_btc_chart_path(raw, captures_dir=captures_dir),
            "btc_badge_color": btc_context.btc_badge_color(score),
            "btc_badge_label": btc_context.btc_badge_label(score),
            "btc_etat": flash_btc["btc_etat"],
            "btc_etat_voyant": flash_btc["btc_etat_voyant"],
            "btc_etat_badge_label": db_manager.btc_etat_badge_label(flash_btc["btc_etat"]),
            "btc_etat_badge_color": db_manager.btc_etat_badge_color(flash_btc["btc_etat"]),
            "btc_change_1h": flash_btc["btc_change_1h"],
            "btc_change_5m": flash_btc["btc_change_5m"],
        }
    )
    return view


def _fetch_flash_analyses(
    conn: sqlite3.Connection,
    *,
    view: str,
    btc_filter: bool,
    limit: int = 50,
) -> list[tuple[db_manager.AnalyseRow, sqlite3.Row]]:
    """Charge les analyses FLASH avec filtres combinables (fonction nouvelle)."""
    btc_context.ensure_btc_columns(conn)
    rows = conn.execute(
        "SELECT * FROM analyses_ichimoku ORDER BY id DESC",
    ).fetchall()
    rows = [
        r for r in rows
        if r["decision_ia"] != btc_context.BTC_SCHEDULED_DECISION
    ]
    if view == "ichimoku":
        rows = [r for r in rows if db_manager.is_accepted(r["score_ia"])]
    if btc_filter:
        rows = btc_context.filter_rows_btc_tradable(rows)
    rows = rows[:limit]
    return [(db_manager.AnalyseRow.from_sqlite(r), r) for r in rows]


@router.get("/flash", response_class=HTMLResponse)
def flash_analyses(
    request: Request,
    view: str = "all",
    btc: int = 0,
) -> HTMLResponse:
    """Analyses automatiques déclenchées par les FLASH Telegram.

    LECTURE SEULE de SQLite (``data/analyses.db``) — ne touche jamais
    au store RAM, au pipeline standard, ni à l'historique bitunix (450 flashs).

    ``view`` : ``all`` = tous les flashs depuis la bascule (acceptés + rejetés),
    ``ichimoku`` = uniquement ceux avec confiance ≥ 6/10.
    ``btc`` : ``1`` = ne garde que les flashs avec contexte BTC tradable (score ≥ 5).
    """
    view_norm = (view or "all").strip().lower()
    if view_norm not in ("all", "ichimoku"):
        view_norm = "all"
    btc_filter = int(btc or 0) == 1

    templates = request.app.state.templates
    conn = db_manager.connect()
    try:
        paired = _fetch_flash_analyses(conn, view=view_norm, btc_filter=btc_filter)
        stats = db_manager.counts_flash_only(conn)
        btc_context.ensure_btc_columns(conn)
        btc_rows = conn.execute(
            """
            SELECT btc_context_score FROM analyses_ichimoku
            WHERE decision_ia IS NULL OR decision_ia != ?
            """,
            (btc_context.BTC_SCHEDULED_DECISION,),
        ).fetchall()
        n_btc_tradable = sum(
            1
            for r in btc_rows
            if btc_context.is_btc_tradable(r["btc_context_score"])
        )
    finally:
        conn.close()
    analyses = [_flash_view_with_btc(row, raw) for row, raw in paired]
    return templates.TemplateResponse(
        request,
        "_flash_analyses.html",
        {
            "request": request,
            "analyses": analyses,
            "view": view_norm,
            "btc_filter": btc_filter,
            "n_total": stats["total"],
            "n_accepted": stats["accepted"],
            "n_btc_tradable": n_btc_tradable,
        },
    )


# Vocabulaire des résultats renvoyés par bitunix → (libellé badge, couleur badge).
_OUTCOME_BADGE: dict[str, tuple[str, str]] = {
    "TP": ("🟢 TP", "green"),
    "SL": ("🔴 SL", "red"),
    "LIQUIDATION": ("💥 LIQ", "red"),
    "TIMEOUT": ("⏱ Clôture 24h", "muted"),
    "OPEN": ("⏳ EN COURS", "yellow"),
}


def _sim_view(payload: dict) -> dict:
    """Transforme la réponse JSON bitunix en modèle d'affichage.

    ``pnl_provisional`` pilote l'affichage gris/italique avec préfixe « ~ ».
    """
    outcome = str(payload.get("outcome") or "?")
    provisional = bool(payload.get("pnl_provisional"))
    pnl = payload.get("pnl_pct")
    label, badge_color = _OUTCOME_BADGE.get(outcome, (outcome, "muted"))

    if pnl is None:
        pnl_text = "—"
    elif provisional:
        pnl_text = f"~{pnl:+.2f}%"
    else:
        pnl_text = f"{pnl:+.2f}%"

    if provisional:
        pnl_class = "pnl-provisional"
    elif pnl is None or pnl == 0:
        pnl_class = "pnl-neutral"
    else:
        pnl_class = "pnl-up" if pnl > 0 else "pnl-down"

    params = payload.get("params") or {}
    return {
        "outcome": outcome,
        "provisional": provisional,
        "badge_label": label,
        "badge_color": badge_color,
        "pnl_text": pnl_text,
        "pnl_class": pnl_class,
        "exit_minutes": payload.get("exit_minutes"),
        "leverage": params.get("leverage"),
        "tp": params.get("tp"),
        "sl": params.get("sl"),
        "ret_15m_pct": payload.get("ret_15m_pct"),
        "max_gain_24h_pct": payload.get("max_gain_24h_pct"),
    }


def _render_sim(
    request: Request,
    *,
    sim: dict | None = None,
    sim_error: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_flash_sim_result.html",
        {"request": request, "sim": sim, "sim_error": sim_error},
    )


@router.get("/flash/simulate", response_class=HTMLResponse)
def flash_simulate(
    request: Request,
    symbol: str = "",
    flash_ts: str = "",
    leverage: float = 10.0,
    tp: float = 8.0,
    sl: float = 2.0,
    tolerance_s: int = 60,
) -> HTMLResponse:
    """Simulation TP/SL/levier d'UN flash via l'API bitunix (proxy lecture seule).

    Matching côté bitunix par ``symbol + flash_ts`` (±``tolerance_s``).
    """
    token = (symbol or "").strip()
    ts = (flash_ts or "").strip()
    if not token or not ts:
        return _render_sim(
            request, sim_error="Token ou horodatage du signal manquant."
        )

    params = {
        "symbol": token,
        "flash_ts": ts,
        "leverage": leverage,
        "tp": tp,
        "sl": sl,
        "tolerance_s": tolerance_s,
    }
    url = f"{BITUNIX_API_URL}/api/backtest/simulate_one"
    try:
        resp = httpx.get(url, params=params, timeout=30.0)
    except httpx.HTTPError as exc:
        return _render_sim(
            request,
            sim_error=f"API bitunix injoignable ({BITUNIX_API_URL}) : {exc}",
        )

    if resp.status_code == 404:
        return _render_sim(
            request,
            sim_error=f"FLASH introuvable côté bitunix (±{tolerance_s}s autour de {ts} UTC).",
        )
    if resp.status_code != 200:
        return _render_sim(
            request, sim_error=f"Erreur API bitunix : HTTP {resp.status_code}"
        )
    try:
        payload = resp.json()
    except ValueError:
        return _render_sim(request, sim_error="Réponse bitunix illisible (JSON invalide).")

    if not payload.get("ok"):
        return _render_sim(
            request, sim_error=str(payload.get("error", "Simulation impossible."))
        )
    return _render_sim(request, sim=_sim_view(payload))


@router.get("/flash/ping")
def flash_ping() -> JSONResponse:
    """Sonde légère pour l'auto-refresh : dernier id + compteurs (lecture seule)."""
    conn = db_manager.connect()
    try:
        stats = db_manager.counts_flash_only(conn)
        row = conn.execute(
            """
            SELECT MAX(id) AS m FROM analyses_ichimoku
            WHERE decision_ia IS NULL OR decision_ia != ?
            """,
            (btc_context.BTC_SCHEDULED_DECISION,),
        ).fetchone()
    finally:
        conn.close()
    latest_id = int(row["m"]) if row and row["m"] is not None else 0
    return JSONResponse(
        {
            "latest_id": latest_id,
            "total": stats["total"],
            "accepted": stats["accepted"],
        }
    )


def _normalize_btc_period(period: str) -> str:
    p = (period or "30d").strip().lower()
    if p in ("2d", "7d", "30d", "all", "tout"):
        return "all" if p == "tout" else p
    return "30d"


def _btc_trend_payload(period: str) -> dict:
    conn = db_manager.connect()
    try:
        points = btc_context.fetch_btc_trend_history(conn, period=period)
    finally:
        conn.close()

    point_meta = [
        {
            "date": p["date_display"],
            "score": p["score"],
            "scoreLabel": p["score_label"],
            "source": p["source_label"],
            "note": p.get("note"),
        }
        for p in points
    ]

    return {
        "period": period,
        "points": points,
        "labels": [p["snapshot"] for p in points],
        "scores": [p["score"] for p in points],
        "point_colors": [
            btc_context.trend_point_color(p["score"], backfill=p.get("backfill", False))
            for p in points
        ],
        "point_backfill": [bool(p.get("backfill")) for p in points],
        "point_meta": point_meta,
        "chart_initial": {
            "period": period,
            "labels": [p["snapshot"] for p in points],
            "scores": [p["score"] for p in points],
            "pointColors": [
                btc_context.trend_point_color(p["score"], backfill=p.get("backfill", False))
                for p in points
            ],
            "pointBackfill": [bool(p.get("backfill")) for p in points],
            "pointMeta": point_meta,
        },
    }


@router.get("/btc-trend", response_class=HTMLResponse)
def btc_trend_page(
    request: Request,
    period: str = "30d",
) -> HTMLResponse:
    """Page tendance contexte macro BTC H4."""
    period_norm = _normalize_btc_period(period)
    templates = request.app.state.templates
    payload = _btc_trend_payload(period_norm)
    return templates.TemplateResponse(
        request,
        "btc_trend.html",
        {"request": request, **payload},
    )


@router.get("/btc-trend/data")
def btc_trend_data(period: str = "30d") -> JSONResponse:
    """Données JSON pour la courbe BTC H4 (sélecteur période)."""
    period_norm = _normalize_btc_period(period)
    return JSONResponse(_btc_trend_payload(period_norm))


def _btc_regime_chart_url(chart_path: str | None) -> str:
    if not chart_path:
        return ""
    try:
        captures = load_app_config().paths.captures
        rel = Path(chart_path).resolve().relative_to(captures.resolve())
        return f"/captures/{rel.as_posix()}"
    except ValueError:
        return ""


def _btc_regime_page_context() -> dict[str, Any]:
    conn = db_manager.connect()
    try:
        rows, meta, fixed_days = btc_regime_dates.fetch_regime_table(conn)
    finally:
        conn.close()
    chart_url = _btc_regime_chart_url(meta.get("chart_path"))
    return {
        "rows": rows,
        "meta": meta,
        "chart_url": chart_url,
        "fixed_days": list(fixed_days),
        "n_oui": sum(1 for r in rows if r.get("etat") == "OUI"),
        "n_non": sum(1 for r in rows if r.get("etat") == "NON"),
        "n_limite": sum(1 for r in rows if r.get("etat") == "LIMITE"),
    }


def _btc_regime_run_response(result: dict[str, Any]) -> dict[str, Any]:
    ctx = _btc_regime_page_context()
    chart_url = _btc_regime_chart_url(result.get("chart_path")) or ctx.get("chart_url", "")
    return {**ctx, **result, "chart_url": chart_url}


class BtcRegimeRunRequest(BaseModel):
    days_window: int | None = None


@router.post("/btc-dates-onoff/run")
def btc_dates_onoff_run(body: BtcRegimeRunRequest | None = None) -> JSONResponse:
    """Relance capture + analyse Gemini et upsert incrémental du tableau."""
    days_window = btc_regime_dates.resolve_days_window(
        body.days_window if body else None
    )
    result = btc_regime_dates.run_regime_dates_update(days_window=days_window)
    payload = _btc_regime_run_response(result)
    if not result.get("ok"):
        return JSONResponse(payload, status_code=502)
    return JSONResponse(payload)


@router.get("/btc-dates-onoff", response_class=HTMLResponse)
def btc_dates_onoff_page(request: Request) -> HTMLResponse:
    """Page Date ON/OFF — régimes macro BTC H4 par plages."""
    templates = request.app.state.templates
    ctx = _btc_regime_page_context()
    return templates.TemplateResponse(
        request,
        "btc_dates_onoff.html",
        {
            "request": request,
            **ctx,
            "days_window_default": btc_regime_dates.DAYS_WINDOW,
            "days_window_min": btc_regime_dates.MIN_DAYS,
            "days_window_max": btc_regime_dates.MAX_DAYS,
        },
    )


@router.get("/btc-dates-onoff/data")
def btc_dates_onoff_data() -> JSONResponse:
    """Données JSON du tableau (historique accumulé)."""
    return JSONResponse(_btc_regime_page_context())


def _normalize_backtest_etats(
    *,
    btc_ok: bool = True,
    btc_reprise: bool = True,
    btc_faible: bool = True,
) -> set[str]:
    """États BTC inclus dans le backtest (cases cochées)."""
    out: set[str] = set()
    if btc_ok:
        out.add(db_manager.BTC_ETAT_OK)
    if btc_reprise:
        out.add(db_manager.BTC_ETAT_REPRISE)
    if btc_faible:
        out.add(db_manager.BTC_ETAT_FAIBLE)
    return out


def _read_btc_flash_fields(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    etat_raw = row["btc_etat"] if "btc_etat" in keys else None
    etat = db_manager.normalize_btc_etat(etat_raw)
    return {
        "btc_change_1h": row["btc_change_1h"] if "btc_change_1h" in keys else None,
        "btc_change_5m": row["btc_change_5m"] if "btc_change_5m" in keys else None,
        "btc_etat": etat,
        "btc_etat_voyant": db_manager.btc_etat_voyant(etat_raw),
    }


def _passes_btc_etat_backtest(row: sqlite3.Row, etats: set[str]) -> bool:
    """Filtre voyant BTC flash pour les états connus (OK/REPRISE/FAIBLE)."""
    if not etats:
        return False
    fields = _read_btc_flash_fields(row)
    return fields["btc_etat"] in etats


def _include_flash_in_backtest_list(row: sqlite3.Row, etats: set[str]) -> bool:
    """Liste historique : filtre strict sur les états cochés.

    Depuis le nettoyage DB, les flashs sans pastille (ancien format) sont supprimés.
    """
    fields = _read_btc_flash_fields(row)
    return _passes_btc_etat_backtest(row, etats)


def _include_flash_in_backtest_sim(flash: dict[str, Any], etats: set[str]) -> bool:
    """Simulation batch : filtre voyant optionnel.

    - ``etats`` vide (aucune case cochée) → tous les flashs visibles simulés.
    - ``etats`` non vide → filtre strict : uniquement les états cochés.
    """
    if not etats:
        return True
    btc_etat = flash.get("btc_etat")
    return btc_etat in etats


def _normalize_backtest_filtres(filtres: list[str] | str | None) -> list[str]:
    """Normalise les filtres backtest : [] = tous, sinon sous-ensemble ichimoku/btc/btc10."""
    if filtres is None:
        return []
    if isinstance(filtres, str):
        key = filtres.strip().lower()
        if key in ("", "tous", "all"):
            return []
        if key in ("ichimoku", "btc", "btc10"):
            return [key]
        return []
    out: list[str] = []
    for raw in filtres:
        key = str(raw).strip().lower()
        if key in ("ichimoku", "btc", "btc10") and key not in out:
            out.append(key)
    return out


def _passes_ichimoku_backtest(row: sqlite3.Row) -> bool:
    """Filtre Ichimoku backtest : confiance ≥ 6/10 (aligné dashboard)."""
    return db_manager.is_accepted(row["score_ia"])


def _passes_btc_backtest(row: sqlite3.Row) -> bool:
    """Filtre BTC H4 backtest : btc_context_score ≥ 5."""
    btc = btc_context.read_btc_fields_from_row(row)
    score = btc.get("btc_context_score")
    return score is not None and int(score) >= 5


def _passes_btc10_backtest(row: sqlite3.Row) -> bool:
    """Filtre BTC H4 strict : btc_context_score = 10."""
    btc = btc_context.read_btc_fields_from_row(row)
    score = btc.get("btc_context_score")
    return score is not None and int(score) == 10


def _fetch_backtest_flashes(
    conn: sqlite3.Connection,
    *,
    filtres: list[str] | None = None,
    etats: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Liste brute des flashs Ichimoku pour le backtest (sans simulation)."""
    btc_context.ensure_btc_columns(conn)
    active_etats = etats if etats is not None else _normalize_backtest_etats()
    rows = conn.execute(
        """
        SELECT * FROM analyses_ichimoku
        WHERE decision_ia IS NULL OR decision_ia != ?
        ORDER BY id ASC
        """,
        (btc_context.BTC_SCHEDULED_DECISION,),
    ).fetchall()
    active = _normalize_backtest_filtres(filtres)
    if "ichimoku" in active:
        rows = [r for r in rows if _passes_ichimoku_backtest(r)]
    if "btc" in active:
        rows = [r for r in rows if _passes_btc_backtest(r)]
    if "btc10" in active:
        rows = [r for r in rows if _passes_btc10_backtest(r)]
    rows = [r for r in rows if _include_flash_in_backtest_list(r, active_etats)]
    flashes: list[dict[str, Any]] = []
    for row in rows:
        btc = btc_context.read_btc_fields_from_row(row)
        flash_btc = _read_btc_flash_fields(row)
        flashes.append(
            {
                "id": int(row["id"]),
                "token": str(row["token"]),
                "flash_ts": row["signal_time_utc"],
                "signal_time_utc": row["signal_time_utc"],
                "analysis_time_utc": row["analysis_time_utc"],
                "score": row["score_ia"],
                "score_display": format_score_fr(row["score_ia"]),
                "decision": row["decision_ia"],
                "btc_score": btc.get("btc_context_score"),
                "btc_badge": btc_context.btc_badge_color(btc.get("btc_context_score")),
                **flash_btc,
            }
        )
    return flashes


def _enrich_row_optimal_tp(
    row: dict[str, Any],
    *,
    leverage: float,
    tp: float,
) -> None:
    """Ajoute tp_optimal_* via klines API (même source que le graphique)."""
    resultat = str(row.get("resultat") or "")
    flash_id = row.get("flash_id")
    klines_payload: dict[str, Any] | None = None
    if flash_id is not None:
        klines_payload = _call_backtest_klines(int(flash_id), hours_after=24)

    fields = build_optimal_tp_fields(
        resultat=resultat,
        entry_price=row.get("entry_price"),
        sl_price=row.get("sl_price"),
        flash_at_ms=klines_payload.get("flash_at_ms") if klines_payload else None,
        candles=(klines_payload or {}).get("candles") or [],
        leverage=leverage,
        tp_pct=tp,
    )
    row.update(fields)


def _run_backtest_simulation(
    *,
    filtres: list[str],
    etats: set[str],
    leverage: float,
    tp: float,
    sl: float,
) -> list[dict[str, Any]]:
    """Exécute la simulation batch (même logique que ``POST /backtest/run``)."""
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes(conn, filtres=filtres, etats=etats)
    finally:
        conn.close()

    raw_rows: list[dict[str, Any]] = []
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
        row.update(
            {
                "analysis_time_utc": flash.get("analysis_time_utc"),
                "score_display": flash.get("score_display"),
                "decision": flash.get("decision"),
                "btc_score": flash.get("btc_score"),
                "btc_change_1h": flash.get("btc_change_1h"),
                "btc_change_5m": flash.get("btc_change_5m"),
            }
        )
        _enrich_row_optimal_tp(row, leverage=leverage, tp=tp)
        raw_rows.append(row)
    return raw_rows


# Colonnes alignées sur le tableau backtest (aucune métadonnée IA / delta BTC / prix).
_BACKTEST_TABLE_CSV_FIELDS: tuple[str, ...] = (
    "token",
    "date_flash",
    "score_ia",
    "voyant_btc",
    "btc_score",
    "resultat",
    "duree",
    "pnl_pct",
    "tp_optimal",
)


def _csv_score_ia(sim_row: dict[str, Any]) -> str:
    display = sim_row.get("score_display")
    if display is not None and str(display).strip():
        return f"{display}/10"
    score = sim_row.get("score")
    if score is None:
        return ""
    return f"{format_score_fr(score)}/10"


def _csv_btc_score(sim_row: dict[str, Any]) -> str:
    score = sim_row.get("btc_score")
    if score is None:
        return ""
    return f"{score}/10"


def _backtest_table_csv_row(sim_row: dict[str, Any]) -> dict[str, Any]:
    """Une ligne CSV = colonnes visibles du tableau après simulation."""
    return {
        "token": sim_row.get("token") or "",
        "date_flash": sim_row.get("flash_ts") or "",
        "score_ia": _csv_score_ia(sim_row),
        "voyant_btc": sim_row.get("btc_etat_voyant") or "",
        "btc_score": _csv_btc_score(sim_row),
        "resultat": sim_row.get("resultat") or "",
        "duree": sim_row.get("duree") or "",
        "pnl_pct": sim_row.get("pnl_pct") or "",
        "tp_optimal": sim_row.get("tp_optimal_display") or "",
    }


def _build_backtest_csv(
    rows: list[dict[str, Any]],
    *,
    fieldnames: tuple[str, ...],
) -> str:
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _backtest_csv_filename() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"backtest_simulation_{stamp}.csv"


def _call_simulate_one(
    *,
    symbol: str,
    flash_ts: str,
    leverage: float,
    tp: float,
    sl: float,
    tolerance_s: int = 60,
) -> dict[str, Any] | None:
    """Proxy vers bitunix ``/api/backtest/simulate_one`` (même logique que /flash/simulate)."""
    params = {
        "symbol": symbol,
        "flash_ts": flash_ts,
        "leverage": leverage,
        "tp": tp,
        "sl": sl,
        "tolerance_s": tolerance_s,
    }
    url = f"{BITUNIX_API_URL}/api/backtest/simulate_one"
    try:
        resp = httpx.get(url, params=params, timeout=45.0)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not payload.get("ok"):
        return None
    return payload


def _call_backtest_klines(
    flash_id: int,
    *,
    hours_after: int = 24,
) -> dict[str, Any] | None:
    """Proxy vers bitunix ``/api/backtest/klines/{flash_id}``."""
    url = f"{BITUNIX_API_URL}/api/backtest/klines/{flash_id}"
    try:
        resp = httpx.get(url, params={"hours_after": hours_after}, timeout=45.0)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not payload.get("ok"):
        return None
    return payload


def _format_duration_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(int(minutes), 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}j {h}h {m}m"
    return f"{h}h {m}m"


def _format_pnl_pct(pnl: float | None, *, provisional: bool = False) -> str:
    if pnl is None:
        return "—"
    text = f"{pnl:+.2f}%"
    return f"~{text}" if provisional else text


def _map_backtest_resultat(payload: dict[str, Any]) -> tuple[str, bool]:
    """Retourne (code résultat, provisional)."""
    outcome = str(payload.get("outcome") or "").upper()
    provisional = bool(payload.get("pnl_provisional"))
    if outcome == "TP":
        return "TP", False
    if outcome in ("SL", "LIQUIDATION"):
        return "SL", False
    if outcome == "TIMEOUT" or payload.get("status") == "closed":
        return "CLO_24H", False
    if outcome == "OPEN" or payload.get("status") == "in_progress":
        return "EN_COURS", provisional
    return "CLO_24H", False


def _backtest_row_from_sim(
    flash: dict[str, Any],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if payload is None:
        return {
            "token": flash["token"],
            "flash_ts": flash.get("flash_ts") or "",
            "score": flash.get("score"),
            "btc_etat": flash.get("btc_etat"),
            "btc_etat_voyant": flash.get("btc_etat_voyant", "—"),
            "resultat": "ERR",
            "pnl_pct": "—",
            "duree": "—",
            "provisional": False,
        }
    code, provisional = _map_backtest_resultat(payload)
    pnl = payload.get("pnl_pct")
    pnl_float = float(pnl) if pnl is not None else None
    row: dict[str, Any] = {
        "token": flash["token"],
        "flash_ts": flash.get("flash_ts") or payload.get("flash_ts") or "",
        "score": flash.get("score"),
        "btc_etat": flash.get("btc_etat"),
        "btc_etat_voyant": flash.get("btc_etat_voyant", "—"),
        "resultat": code,
        "pnl_pct": _format_pnl_pct(
            pnl_float,
            provisional=provisional,
        ),
        "duree": _format_duration_minutes(payload.get("exit_minutes")),
        "provisional": provisional,
        "_pnl_raw": pnl_float
        if pnl_float is not None and code in ("TP", "SL", "CLO_24H")
        else None,
        "_pnl_all": pnl_float
        if pnl_float is not None and code in ("TP", "SL", "CLO_24H", "EN_COURS")
        else None,
    }
    for key in (
        "flash_id",
        "tp_price",
        "sl_price",
        "liq_price",
        "exit_price",
        "exit_at_ms",
        "exit_minutes",
        "outcome",
        "status",
        "entry_price",
        "pnl_pct_raw",
    ):
        if key in payload and payload[key] is not None:
            row[key] = payload[key]
    if pnl_float is not None:
        row["pnl_pct_raw"] = pnl_float
    return row


def _win_rate_pct(tp: int, sl: int) -> str:
    closed = tp + sl
    if closed == 0:
        return "—"
    return f"{round(tp / closed * 100)}%"


def _compute_backtest_stats(resultats: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(resultats)
    tp = sum(1 for r in resultats if r["resultat"] == "TP")
    sl = sum(1 for r in resultats if r["resultat"] == "SL")
    en_cours = sum(1 for r in resultats if r["resultat"] == "EN_COURS")
    clo = sum(1 for r in resultats if r["resultat"] == "CLO_24H")

    def _pct(n: int) -> str:
        if total == 0:
            return "0%"
        return f"{round(n / total * 100)}%"

    pnls_realized = [r["_pnl_raw"] for r in resultats if r.get("_pnl_raw") is not None]
    pnls_total = [r["_pnl_all"] for r in resultats if r.get("_pnl_all") is not None]
    pnl_realise_sum = sum(pnls_realized) if pnls_realized else None
    pnl_total_sum = sum(pnls_total) if pnls_total else None
    has_en_cours_pnl = any(
        r["resultat"] == "EN_COURS" and r.get("_pnl_all") is not None for r in resultats
    )

    par_etat: dict[str, dict[str, Any]] = {}
    for etat, emoji, label in (
        (db_manager.BTC_ETAT_OK, "🟢", "OK"),
        (db_manager.BTC_ETAT_REPRISE, "✅", "REPRISE"),
        (db_manager.BTC_ETAT_FAIBLE, "🔴", "FAIBLE"),
    ):
        subset = [r for r in resultats if r.get("btc_etat") == etat]
        sub_tp = sum(1 for r in subset if r["resultat"] == "TP")
        sub_sl = sum(1 for r in subset if r["resultat"] == "SL")
        sub_pnls = [r["_pnl_all"] for r in subset if r.get("_pnl_all") is not None]
        sub_pnl_sum = sum(sub_pnls) if sub_pnls else None
        sub_has_live = any(
            r["resultat"] == "EN_COURS" and r.get("_pnl_all") is not None for r in subset
        )
        par_etat[etat] = {
            "emoji": emoji,
            "label": label,
            "n": len(subset),
            "wr": _win_rate_pct(sub_tp, sub_sl),
            "pnl": _format_pnl_pct(sub_pnl_sum, provisional=sub_has_live)
            if sub_pnl_sum is not None
            else "—",
        }

    return {
        "total": total,
        "tp": tp,
        "sl": sl,
        "tp_pct": _pct(tp),
        "sl_pct": _pct(sl),
        "en_cours": en_cours,
        "clo_24h": clo,
        "wr": _win_rate_pct(tp, sl),
        "pnl_realise": _format_pnl_pct(pnl_realise_sum)
        if pnl_realise_sum is not None
        else "—",
        "pnl_total": _format_pnl_pct(
            pnl_total_sum,
            provisional=has_en_cours_pnl,
        )
        if pnl_total_sum is not None
        else "—",
        "par_etat": par_etat,
    }


class BacktestRunRequest(BaseModel):
    leverage: float = Field(default=30.0, ge=1.0, le=50.0)
    tp: float = Field(default=1.4, ge=0.1, le=50.0)
    sl: float = Field(default=2.0, ge=0.1, le=20.0)
    filtres: list[str] = Field(default_factory=list)
    btc_ok: bool = True
    btc_reprise: bool = True
    btc_faible: bool = True


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(
    request: Request,
    filtres: list[str] = Query(default=[]),
    leverage: float = 30.0,
    tp: float = 1.4,
    sl: float = 2.0,
    btc_ok: bool = Query(default=True),
    btc_reprise: bool = Query(default=True),
    btc_faible: bool = Query(default=True),
) -> HTMLResponse:
    """Page backtest historique — liste brute sans simulation au chargement."""
    filtres_norm = _normalize_backtest_filtres(filtres)
    etats = _normalize_backtest_etats(
        btc_ok=btc_ok,
        btc_reprise=btc_reprise,
        btc_faible=btc_faible,
    )
    templates = request.app.state.templates
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes(conn, filtres=filtres_norm, etats=etats)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "backtest.html",
        {
            "request": request,
            "flashes": flashes,
            "filtres": filtres_norm,
            "leverage": leverage,
            "tp": tp,
            "sl": sl,
            "btc_ok": btc_ok,
            "btc_reprise": btc_reprise,
            "btc_faible": btc_faible,
        },
    )


@router.post("/backtest/run")
def backtest_run(body: BacktestRunRequest) -> JSONResponse:
    """Simule tous les flashs filtrés via bitunix simulate_one (batch)."""
    filtres_norm = _normalize_backtest_filtres(body.filtres)
    etats = _normalize_backtest_etats(
        btc_ok=body.btc_ok,
        btc_reprise=body.btc_reprise,
        btc_faible=body.btc_faible,
    )
    raw_rows = _run_backtest_simulation(
        filtres=filtres_norm,
        etats=etats,
        leverage=body.leverage,
        tp=body.tp,
        sl=body.sl,
    )

    stats = _compute_backtest_stats(raw_rows)
    resultats = []
    for r in raw_rows:
        c = dict(r)
        c.pop("_pnl_raw", None)
        c.pop("_pnl_all", None)
        resultats.append(c)

    return JSONResponse({"resultats": resultats, "stats": stats})


@router.get("/backtest/export.csv")
def backtest_export_csv(
    filtres: list[str] = Query(default=[]),
    leverage: float = Query(30.0, ge=1, le=50),
    tp: float = Query(1.4, ge=0.1, le=50),
    sl: float = Query(2.0, ge=0.1, le=20),
    btc_ok: bool = Query(default=True),
    btc_reprise: bool = Query(default=True),
    btc_faible: bool = Query(default=True),
) -> Response:
    """Export CSV backtest : simulation batch, colonnes du tableau uniquement."""
    filtres_norm = _normalize_backtest_filtres(filtres)
    etats = _normalize_backtest_etats(
        btc_ok=btc_ok,
        btc_reprise=btc_reprise,
        btc_faible=btc_faible,
    )
    sim_rows = _run_backtest_simulation(
        filtres=filtres_norm,
        etats=etats,
        leverage=leverage,
        tp=tp,
        sl=sl,
    )
    csv_rows = [_backtest_table_csv_row(r) for r in sim_rows]
    content = _build_backtest_csv(csv_rows, fieldnames=_BACKTEST_TABLE_CSV_FIELDS)
    filename = _backtest_csv_filename()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/backtest/chart")
def backtest_chart_data(
    symbol: Annotated[str, Query(min_length=1)],
    flash_ts: Annotated[str, Query(min_length=1)],
    leverage: float = Query(20.0, ge=1, le=50),
    tp: float = Query(2.5, ge=0.1, le=50),
    sl: float = Query(1.5, ge=0.1, le=20),
    hours_after: int = Query(24, ge=1, le=48),
) -> JSONResponse:
    """Klines + niveaux TP/SL pour le graphique backtest (proxy API bitunix)."""
    sim = _call_simulate_one(
        symbol=symbol,
        flash_ts=flash_ts,
        leverage=leverage,
        tp=tp,
        sl=sl,
    )
    if sim is None:
        return JSONResponse(
            {"ok": False, "error": "Simulation indisponible (API bitunix)"},
            status_code=502,
        )
    flash_id = sim.get("flash_id")
    if flash_id is None:
        return JSONResponse(
            {"ok": False, "error": "FLASH introuvable sur l'API bitunix"},
            status_code=404,
        )
    klines = _call_backtest_klines(int(flash_id), hours_after=hours_after)
    if klines is None:
        return JSONResponse(
            {"ok": False, "error": "Klines indisponibles"},
            status_code=502,
        )
    return JSONResponse({"ok": True, "sim": sim, "klines": klines})


def _fetch_backtest_flashes_temporal(
    conn: sqlite3.Connection,
    *,
    filtres: list[str] | None,
    etats: set[str],
    temporal: TemporalFilter,
) -> list[dict[str, Any]]:
    flashes = _fetch_backtest_flashes(conn, filtres=filtres, etats=etats)
    return filter_flashes_temporal(flashes, temporal)


def _run_backtest_temporal_simulation(
    *,
    filtres: list[str],
    etats: set[str],
    leverage: float,
    tp: float,
    sl: float,
    temporal: TemporalFilter,
) -> list[dict[str, Any]]:
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes_temporal(
            conn, filtres=filtres, etats=etats, temporal=temporal
        )
    finally:
        conn.close()

    raw_rows: list[dict[str, Any]] = []
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
        row.update(
            {
                "analysis_time_utc": flash.get("analysis_time_utc"),
                "score_display": flash.get("score_display"),
                "decision": flash.get("decision"),
                "btc_score": flash.get("btc_score"),
                "btc_change_1h": flash.get("btc_change_1h"),
                "btc_change_5m": flash.get("btc_change_5m"),
            }
        )
        _enrich_row_optimal_tp(row, leverage=leverage, tp=tp)
        raw_rows.append(row)
    return raw_rows


def _backtest_tempo_csv_filename() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"backtest_tempo_simulation_{stamp}.csv"


class BacktestTemporalRunRequest(BacktestRunRequest):
    date_debut: str | None = None
    date_fin: str | None = None
    heure_debut: str = "00:00"
    heure_fin: str = "23:59"


@router.get("/backtest/tempo", response_class=HTMLResponse)
def backtest_temporal_page(
    request: Request,
    filtres: list[str] = Query(default=[]),
    leverage: float = 30.0,
    tp: float = 1.4,
    sl: float = 2.0,
    date_debut: str | None = None,
    date_fin: str | None = None,
    heure_debut: str = "00:00",
    heure_fin: str = "23:59",
    btc_ok: bool = Query(default=True),
    btc_reprise: bool = Query(default=True),
    btc_faible: bool = Query(default=True),
) -> HTMLResponse:
    """Backtest temporel — même vue que l'historique + filtres date/heure."""
    filtres_norm = _normalize_backtest_filtres(filtres)
    etats = _normalize_backtest_etats(
        btc_ok=btc_ok,
        btc_reprise=btc_reprise,
        btc_faible=btc_faible,
    )
    temporal = resolve_temporal_filter(
        date_debut=date_debut,
        date_fin=date_fin,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
    )
    templates = request.app.state.templates
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes_temporal(
            conn, filtres=filtres_norm, etats=etats, temporal=temporal
        )
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "backtest_temporal.html",
        {
            "request": request,
            "flashes": flashes,
            "filtres": filtres_norm,
            "leverage": leverage,
            "tp": tp,
            "sl": sl,
            "btc_ok": btc_ok,
            "btc_reprise": btc_reprise,
            "btc_faible": btc_faible,
            "date_debut": temporal.date_debut.isoformat(),
            "date_fin": temporal.date_fin.isoformat(),
            "heure_debut": temporal.heure_debut,
            "heure_fin": temporal.heure_fin,
            "period_summary": temporal_period_summary(temporal, len(flashes)),
            "interval_label": temporal_interval_label(temporal),
        },
    )


@router.post("/backtest/tempo/run")
def backtest_temporal_run(body: BacktestTemporalRunRequest) -> JSONResponse:
    """Simulation batch sur le sous-ensemble temporel filtré."""
    filtres_norm = _normalize_backtest_filtres(body.filtres)
    etats = _normalize_backtest_etats(
        btc_ok=body.btc_ok,
        btc_reprise=body.btc_reprise,
        btc_faible=body.btc_faible,
    )
    temporal = resolve_temporal_filter(
        date_debut=body.date_debut,
        date_fin=body.date_fin,
        heure_debut=body.heure_debut,
        heure_fin=body.heure_fin,
    )
    raw_rows = _run_backtest_temporal_simulation(
        filtres=filtres_norm,
        etats=etats,
        leverage=body.leverage,
        tp=body.tp,
        sl=body.sl,
        temporal=temporal,
    )
    stats = _compute_backtest_stats(raw_rows)
    resultats = []
    for r in raw_rows:
        c = dict(r)
        c.pop("_pnl_raw", None)
        c.pop("_pnl_all", None)
        resultats.append(c)
    return JSONResponse(
        {
            "resultats": resultats,
            "stats": stats,
            "period_summary": temporal_period_summary(temporal, len(resultats)),
            "interval_label": temporal_interval_label(temporal),
        }
    )


@router.get("/backtest/tempo/export.csv")
def backtest_temporal_export_csv(
    filtres: list[str] = Query(default=[]),
    leverage: float = Query(30.0, ge=1, le=50),
    tp: float = Query(1.4, ge=0.1, le=50),
    sl: float = Query(2.0, ge=0.1, le=20),
    date_debut: str | None = None,
    date_fin: str | None = None,
    heure_debut: str = "00:00",
    heure_fin: str = "23:59",
    btc_ok: bool = Query(default=True),
    btc_reprise: bool = Query(default=True),
    btc_faible: bool = Query(default=True),
) -> Response:
    """Export CSV backtest temporel : simulation batch, colonnes du tableau uniquement."""
    filtres_norm = _normalize_backtest_filtres(filtres)
    etats = _normalize_backtest_etats(
        btc_ok=btc_ok,
        btc_reprise=btc_reprise,
        btc_faible=btc_faible,
    )
    temporal = resolve_temporal_filter(
        date_debut=date_debut,
        date_fin=date_fin,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
    )
    sim_rows = _run_backtest_temporal_simulation(
        filtres=filtres_norm,
        etats=etats,
        leverage=leverage,
        tp=tp,
        sl=sl,
        temporal=temporal,
    )
    csv_rows = [_backtest_table_csv_row(r) for r in sim_rows]
    content = _build_backtest_csv(csv_rows, fieldnames=_BACKTEST_TABLE_CSV_FIELDS)
    filename = _backtest_tempo_csv_filename()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register_png_url_filter(templates, captures_dir: Path) -> None:
    """Filtre Jinja2 : chemin PNG absolu → URL /captures/..."""

    def png_url(path: Path | str | None) -> str:
        if not path:
            return ""
        try:
            rel = Path(path).resolve().relative_to(captures_dir.resolve())
            return f"/captures/{rel.as_posix()}"
        except (ValueError, TypeError, OSError):
            return ""

    templates.env.filters["png_url"] = png_url
