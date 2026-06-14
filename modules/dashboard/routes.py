"""Routes HTTP du dashboard Visio Gemini."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from modules.analyse.funnel import _decision_color, format_score_fr, run_funnel
from modules.analyse.orchestrator import run_batch
from modules.analyse.results import AnalysisResult
from modules.config import load_app_config
from modules.dashboard.store import add_run, latest
from modules.selection.bitunix_symbols import normalize_token_key
from modules.selection.builders import build_manual_requests
from modules.triggers import btc_context, db_manager

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
    score = btc.get("btc_context_score")
    view.update(
        {
            "btc_above_tenkan": btc.get("btc_above_tenkan"),
            "btc_tenkan_slope": btc.get("btc_tenkan_slope"),
            "btc_context_score": score,
            "btc_h4_snapshot": btc.get("btc_h4_snapshot"),
            "btc_badge_color": btc_context.btc_badge_color(score),
            "btc_badge_label": btc_context.btc_badge_label(score),
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


def _normalize_backtest_filtres(filtres: list[str] | str | None) -> list[str]:
    """Normalise les filtres backtest : [] = tous, sinon sous-ensemble de ichimoku/btc."""
    if filtres is None:
        return []
    if isinstance(filtres, str):
        key = filtres.strip().lower()
        if key in ("", "tous", "all"):
            return []
        if key in ("ichimoku", "btc"):
            return [key]
        return []
    out: list[str] = []
    for raw in filtres:
        key = str(raw).strip().lower()
        if key in ("ichimoku", "btc") and key not in out:
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


def _fetch_backtest_flashes(
    conn: sqlite3.Connection,
    *,
    filtres: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Liste brute des flashs Ichimoku pour le backtest (sans simulation)."""
    btc_context.ensure_btc_columns(conn)
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
    flashes: list[dict[str, Any]] = []
    for row in rows:
        btc = btc_context.read_btc_fields_from_row(row)
        flashes.append(
            {
                "id": int(row["id"]),
                "token": str(row["token"]),
                "flash_ts": row["signal_time_utc"],
                "score": row["score_ia"],
                "score_display": format_score_fr(row["score_ia"]),
                "decision": row["decision_ia"],
                "btc_score": btc.get("btc_context_score"),
                "btc_badge": btc_context.btc_badge_color(btc.get("btc_context_score")),
            }
        )
    return flashes


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
            "resultat": "ERR",
            "pnl_pct": "—",
            "duree": "—",
            "provisional": False,
        }
    code, provisional = _map_backtest_resultat(payload)
    pnl = payload.get("pnl_pct")
    pnl_float = float(pnl) if pnl is not None else None
    return {
        "token": flash["token"],
        "flash_ts": flash.get("flash_ts") or payload.get("flash_ts") or "",
        "score": flash.get("score"),
        "resultat": code,
        "pnl_pct": _format_pnl_pct(
            pnl_float,
            provisional=provisional,
        ),
        "duree": _format_duration_minutes(payload.get("exit_minutes")),
        "provisional": provisional,
        "_pnl_raw": pnl_float if pnl_float is not None and code in ("TP", "SL") else None,
        "_pnl_all": pnl_float
        if pnl_float is not None and code in ("TP", "SL", "EN_COURS")
        else None,
    }


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

    return {
        "total": total,
        "tp": tp,
        "tp_pct": _pct(tp),
        "sl": sl,
        "sl_pct": _pct(sl),
        "en_cours": en_cours,
        "clo_24h": clo,
        "pnl_realise": _format_pnl_pct(pnl_realise_sum)
        if pnl_realise_sum is not None
        else "—",
        "pnl_total": _format_pnl_pct(
            pnl_total_sum,
            provisional=has_en_cours_pnl,
        )
        if pnl_total_sum is not None
        else "—",
    }


class BacktestRunRequest(BaseModel):
    leverage: float = Field(default=30.0, ge=1.0, le=50.0)
    tp: float = Field(default=1.4, ge=0.1, le=50.0)
    sl: float = Field(default=2.0, ge=0.1, le=20.0)
    filtres: list[str] = Field(default_factory=list)


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(
    request: Request,
    filtres: list[str] = Query(default=[]),
    leverage: float = 30.0,
    tp: float = 1.4,
    sl: float = 2.0,
) -> HTMLResponse:
    """Page backtest historique — liste brute sans simulation au chargement."""
    filtres_norm = _normalize_backtest_filtres(filtres)
    templates = request.app.state.templates
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes(conn, filtres=filtres_norm)
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
        },
    )


@router.post("/backtest/run")
def backtest_run(body: BacktestRunRequest) -> JSONResponse:
    """Simule tous les flashs filtrés via bitunix simulate_one (batch)."""
    filtres_norm = _normalize_backtest_filtres(body.filtres)
    conn = db_manager.connect()
    try:
        flashes = _fetch_backtest_flashes(conn, filtres=filtres_norm)
    finally:
        conn.close()

    raw_rows: list[dict[str, Any]] = []
    for flash in flashes:
        ts = flash.get("flash_ts")
        if not ts:
            raw_rows.append(_backtest_row_from_sim(flash, None))
            continue
        payload = _call_simulate_one(
            symbol=str(flash["token"]),
            flash_ts=str(ts),
            leverage=body.leverage,
            tp=body.tp,
            sl=body.sl,
        )
        raw_rows.append(_backtest_row_from_sim(flash, payload))

    stats = _compute_backtest_stats(raw_rows)
    resultats = []
    for r in raw_rows:
        c = dict(r)
        c.pop("_pnl_raw", None)
        resultats.append(c)

    return JSONResponse({"resultats": resultats, "stats": stats})


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
