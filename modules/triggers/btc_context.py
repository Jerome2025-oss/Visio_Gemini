"""Contexte macro BTC H4 — capture + analyse Gemini (module séparé, non-bloquant).

Ne modifie aucune fonction de capture/analyse Ichimoku existante.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.agent.providers.base import AnalyzeContext
from modules.agent.providers.registry import analyze_with_strategy
from modules.capture.service import capture
from modules.selection.resolver import resolve_layout, resolve_symbol_tv
from modules.triggers import db_manager

logger = logging.getLogger("visio_gemini.triggers.btc_context")

BTC_AGENT_ID = "agent_Ichimoku"
BTC_TIMEFRAME = "4h"
BTC_TOKEN = "BTCUSDT"
BTC_SCHEDULED_DECISION = "BTC H4 SCAN"
# Créneaux H4 UTC — timer systemd : 6 scans/jour (une fois par bougie H4).
BTC_SCHEDULED_HOURS_UTC: tuple[int, ...] = (0, 4, 8, 12, 16, 20)
BACKFILL_VISUEL_SOURCE = db_manager.BACKFILL_VISUEL_SOURCE
# Écart max entre btc_h4_snapshot (fin analyse) et timestamp du PNG (début capture).
BTC_CHART_MATCH_MAX_SECONDS = 180

_MOIS_FR = (
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_PNG_TS_RE = re.compile(r"BTCUSDT\.P_4h_(\d{8})_(\d{6})\.png$", re.IGNORECASE)

_BTC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("btc_above_tenkan", "INTEGER"),
    ("btc_tenkan_slope", "TEXT"),
    ("btc_context_score", "INTEGER"),
    ("btc_h4_snapshot", "TEXT"),
    ("btc_chart_path", "TEXT"),
)

_BTC_PROMPT = """Tu es un analyste technique expert Ichimoku, extrêmement rigoureux et pessimiste.
Tu préfères rater un trade plutôt que de valider un faux signal.
Analyse ce graphique BTC/USDT H4 avec la plus grande sévérité visuelle.

Critère 1 — Position du Close :
- Le prix (clôture de la dernière bougie) est-il STRICTEMENT au-dessus de la Tenkan-sen (ligne bleue) ?
- Si le prix touche la ligne, hésite sur le fil, ou la chevauche visuellement -> NON.

Critère 2 — Orientation de la Tenkan-sen (Zéro Tolérance pour le Flat) :
- Sur les 3-4 dernières bougies, la Tenkan monte-t-elle de façon NETTE, CONTINUE et INCESSANTE ?
- RÈGLE ABSOLUE : Si la ligne forme un palier horizontal (plateau), même léger, ou s'aplatit sur la dernière bougie -> "flat".

Scoring drastique :
- Critère 1 validé -> +5 pts (sinon 0)
- Critère 2 validé (hausse claire, aucun plat) -> +5 pts (sinon 0)

Règle impérative pour context_score :
- 10 : UNIQUEMENT si le prix pousse ET la Tenkan monte (momentum).
- 5  : si le prix est au-dessus MAIS la Tenkan est plate, neutre ou baissière.
- 0  : si le prix est sous ou sur la Tenkan.

Réponds UNIQUEMENT sous forme de bloc JSON valide, sans markdown autour (pas de ```json), respectant strictement ce format :
{
  "resume": "Analyse visuelle obligatoire de la forme exacte de la Tenkan (ex: plate, en escalier stable, ou inclinée vers le haut). Mentionner si le prix chevauche la ligne.",
  "above_tenkan": true,
  "tenkan_slope": "up",
  "context_score": 10
}

Valeurs autorisées :
- above_tenkan : true | false
- tenkan_slope : "up" | "down" | "flat"
- context_score : 0 | 5 | 10
"""


@dataclass(frozen=True)
class BtcAnalysisResult:
    """Résultat parsé de l'analyse BTC H4."""

    above_tenkan: bool | None
    tenkan_slope: str | None
    context_score: int | None
    resume: str | None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def ensure_btc_columns(conn: sqlite3.Connection) -> None:
    """Migration non destructive : ajoute les colonnes BTC si absentes."""
    for name, col_type in _BTC_COLUMNS:
        if not _column_exists(conn, "analyses_ichimoku", name):
            conn.execute(f"ALTER TABLE analyses_ichimoku ADD COLUMN {name} {col_type}")
            conn.commit()
            logger.info("🧱 Migration BTC : colonne %s ajoutée.", name)


def capture_btc_h4_chart() -> Path:
    """Capture dédiée BTC/USDT H4 Ichimoku (n'altère pas la capture flash existante)."""
    symbol_tv = resolve_symbol_tv(BTC_TOKEN)
    layout_id = resolve_layout(BTC_AGENT_ID)
    return capture(symbol_tv, BTC_TIMEFRAME, layout_id, BTC_AGENT_ID)


def _empty_btc_result() -> BtcAnalysisResult:
    return BtcAnalysisResult(
        above_tenkan=None,
        tenkan_slope=None,
        context_score=None,
        resume=None,
    )


def _strip_markdown_fence(text: str) -> str:
    """Retire un éventuel bloc ```json ... ``` si le modèle l'ajoute malgré le prompt."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _extract_json_block(text: str) -> dict | None:
    """Extrait un objet JSON depuis la réponse Gemini."""
    raw = _strip_markdown_fence(text)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_slope(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("up", "down", "flat"):
        return s
    return None


def _normalize_context_score(value: object) -> int | None:
    if value is None:
        return None
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    if score in (0, 5, 10):
        return score
    return None


def _normalize_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "oui"):
        return True
    if s in ("false", "0", "no", "non"):
        return False
    return None


def parse_btc_h4_response(text: str) -> BtcAnalysisResult:
    """Parse la réponse Gemini. Ne lève jamais."""
    data = _extract_json_block(text)
    if not data:
        return _empty_btc_result()
    return BtcAnalysisResult(
        above_tenkan=_normalize_bool(data.get("above_tenkan")),
        tenkan_slope=_normalize_slope(data.get("tenkan_slope")),
        context_score=_normalize_context_score(data.get("context_score")),
        resume=str(data["resume"]).strip() if data.get("resume") else None,
    )


def analyze_btc_h4(image_path: Path) -> BtcAnalysisResult:
    """Analyse vision BTC H4 via Gemini. Ne lève jamais (retourne null si échec)."""
    text = analyze_btc_h4_prompt(image_path, _BTC_PROMPT)
    if not text:
        return _empty_btc_result()
    return parse_btc_h4_response(text)


def analyze_btc_h4_prompt(image_path: Path, prompt: str) -> str | None:
    """Pipeline vision identique au dashboard BTC H4 — prompt libre, texte brut Gemini."""
    try:
        symbol_tv = resolve_symbol_tv(BTC_TOKEN)
        layout_id = resolve_layout(BTC_AGENT_ID)
        context = AnalyzeContext(
            agent_id=BTC_AGENT_ID,
            symbol_key=BTC_TOKEN,
            symbol_tv=symbol_tv,
            timeframe_label="H4",
            layout_id=layout_id,
        )
        vision = analyze_with_strategy(image_path, prompt, context=context)
        return vision.text
    except Exception as exc:
        logger.error("❌ Analyse BTC H4 Gemini échouée (non bloquant) : %s", exc)
        return None


def _parse_snapshot_iso(value: str) -> datetime | None:
    try:
        snap = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if snap.tzinfo is None:
            snap = snap.replace(tzinfo=timezone.utc)
        return snap
    except ValueError:
        return None


def _png_path_to_dt(path: Path) -> datetime | None:
    """Extrait le timestamp UTC du nom ``BTCUSDT.P_4h_YYYYMMDD_HHMMSS.png``."""
    match = _PNG_TS_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(
            match.group(1) + match.group(2),
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _snapshot_iso_from_png(png: Path) -> str:
    """Horodatage aligné sur la capture PNG (pas la fin de l'analyse Gemini)."""
    dt = _png_path_to_dt(png)
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat(timespec="seconds")


def _find_nearest_btc_png(agent_dir: Path, target: datetime) -> Path | None:
    """PNG BTC H4 le plus proche de ``target`` (tolérance ``BTC_CHART_MATCH_MAX_SECONDS``)."""
    best: Path | None = None
    best_delta = BTC_CHART_MATCH_MAX_SECONDS + 1.0
    for png in agent_dir.glob("BTCUSDT.P_4h_*.png"):
        png_dt = _png_path_to_dt(png)
        if png_dt is None:
            continue
        delta = abs((png_dt - target).total_seconds())
        if delta <= BTC_CHART_MATCH_MAX_SECONDS and delta < best_delta:
            best_delta = delta
            best = png
    return best


def update_btc_context(
    conn: sqlite3.Connection,
    *,
    analyse_id: int,
    result: BtcAnalysisResult,
    snapshot_iso: str,
    chart_path: str | None = None,
) -> bool:
    """Enregistre le contexte BTC sur une analyse existante."""
    above = None if result.above_tenkan is None else int(result.above_tenkan)
    cur = conn.execute(
        """
        UPDATE analyses_ichimoku
        SET btc_above_tenkan = ?,
            btc_tenkan_slope = ?,
            btc_context_score = ?,
            btc_h4_snapshot = ?,
            btc_chart_path = ?
        WHERE id = ?
        """,
        (
            above,
            result.tenkan_slope,
            result.context_score,
            snapshot_iso,
            chart_path,
            analyse_id,
        ),
    )
    conn.commit()
    if cur.rowcount > 0:
        sync_btc_trend_points(conn)
    return cur.rowcount > 0


def run_btc_h4_context(analyse_id: int) -> int | None:
    """Orchestre capture + analyse BTC H4 pour une analyse Ichimoku déjà sauvegardée.

    Non-bloquant : toute erreur est journalisée, retourne ``None``.
    """
    try:
        conn = db_manager.connect()
        try:
            ensure_btc_columns(conn)
        finally:
            conn.close()

        png = capture_btc_h4_chart()
        result = analyze_btc_h4(png)
        snapshot = _snapshot_iso_from_png(png)
        chart_path = str(png.resolve())

        conn = db_manager.connect()
        try:
            updated = update_btc_context(
                conn,
                analyse_id=analyse_id,
                result=result,
                snapshot_iso=snapshot,
                chart_path=chart_path,
            )
        finally:
            conn.close()

        if not updated:
            logger.warning("⚠ Analyse id=%s introuvable pour contexte BTC.", analyse_id)
            return None

        if result.context_score is not None:
            logger.info("✅ Analyse BTC H4 : score %s/10", result.context_score)
        else:
            logger.warning("⚠ Analyse BTC H4 : score indéterminé (id=%s)", analyse_id)
        return result.context_score
    except Exception as exc:
        logger.error("❌ Contexte BTC H4 échoué (non bloquant, id=%s) : %s", analyse_id, exc)
        return None


def run_btc_h4_scheduled_scan() -> int | None:
    """Scan BTC H4 planifié (systemd timer, 6×/jour aux clôtures H4 UTC).

    Indépendant du listener FLASH. Ne modifie aucune analyse Ichimoku existante.
    """
    try:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        conn = db_manager.connect()
        try:
            ensure_btc_columns(conn)
            analyse_id = db_manager.insert_analyse(
                conn,
                token=BTC_TOKEN,
                signal_time_utc=ts,
                score_ia=None,
                decision_ia=BTC_SCHEDULED_DECISION,
                recap_complet=None,
                chart_paths=None,
                analysis_time_utc=ts,
            )
        finally:
            conn.close()
        logger.info("📅 Scan BTC H4 planifié — nouvelle entrée id=%s", analyse_id)
        return run_btc_h4_context(analyse_id)
    except Exception as exc:
        logger.error("❌ Scan BTC H4 planifié échoué : %s", exc)
        return None


def format_trend_jour_fr(snapshot: str) -> str:
    """Libellé jour pour tableau tendance : ``13 juin``."""
    parsed = _parse_snapshot_iso(snapshot)
    if parsed is None:
        return snapshot[:10]
    mois = _MOIS_FR[parsed.month] if 1 <= parsed.month <= 12 else str(parsed.month)
    return f"{parsed.day} {mois}"


def format_trend_heure(snapshot: str) -> str:
    """Libellé heure UTC pour tableau tendance : ``05:00``."""
    parsed = _parse_snapshot_iso(snapshot)
    if parsed is None:
        return ""
    return parsed.strftime("%H:%M")


def _collect_raw_trend_points(conn: sqlite3.Connection) -> list[dict]:
    """Agrège les sources brutes (Gemini + backfill) avant matérialisation."""
    ensure_btc_columns(conn)
    points: list[dict] = []
    rows = conn.execute(
        """
        SELECT id, btc_context_score, btc_h4_snapshot, btc_tenkan_slope,
               btc_above_tenkan, decision_ia
        FROM analyses_ichimoku
        WHERE btc_context_score IS NOT NULL
          AND btc_h4_snapshot IS NOT NULL
        ORDER BY btc_h4_snapshot ASC
        """
    ).fetchall()
    for row in rows:
        points.append(
            {
                "id": int(row["id"]),
                "score": int(row["btc_context_score"]),
                "snapshot": str(row["btc_h4_snapshot"]),
                "tenkan_slope": row["btc_tenkan_slope"],
                "above_tenkan": bool(row["btc_above_tenkan"])
                if row["btc_above_tenkan"] is not None
                else None,
                "scheduled": row["decision_ia"] == BTC_SCHEDULED_DECISION,
                "backfill": False,
                "source": "gemini",
                "analyse_id": int(row["id"]),
            }
        )

    for row in db_manager.fetch_btc_scans(conn, source=BACKFILL_VISUEL_SOURCE):
        points.append(
            {
                "id": int(row["id"]),
                "score": int(row["btc_context_score"]),
                "snapshot": str(row["btc_h4_snapshot"]),
                "tenkan_slope": None,
                "above_tenkan": None,
                "scheduled": False,
                "backfill": True,
                "source": str(row["source"]),
                "analyse_id": None,
                "note": row["note"],
            }
        )

    points.sort(key=lambda p: p["snapshot"])
    return points


def sync_btc_trend_points(conn: sqlite3.Connection) -> int:
    """Matérialise ``btc_trend_points`` depuis analyses_ichimoku + btc_scans."""
    materialized: list[dict[str, object]] = []
    for point in _collect_raw_trend_points(conn):
        snap = str(point["snapshot"])
        materialized.append(
            {
                "snapshot_utc": snap,
                "jour": format_trend_jour_fr(snap),
                "heure": format_trend_heure(snap),
                "score": int(point["score"]),
                "source": str(point["source"]),
                "analyse_id": point.get("analyse_id"),
                "tenkan_slope": point.get("tenkan_slope"),
                "above_tenkan": (
                    int(point["above_tenkan"])
                    if point.get("above_tenkan") is not None
                    else None
                ),
                "scheduled": int(bool(point.get("scheduled"))),
                "backfill": int(bool(point.get("backfill"))),
                "note": point.get("note"),
            }
        )
    return db_manager.replace_btc_trend_points(conn, materialized)


def _trend_point_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "score": int(row["score"]),
        "snapshot": str(row["snapshot_utc"]),
        "jour": str(row["jour"]),
        "heure": str(row["heure"]),
        "tenkan_slope": row["tenkan_slope"],
        "above_tenkan": bool(row["above_tenkan"])
        if row["above_tenkan"] is not None
        else None,
        "scheduled": bool(row["scheduled"]),
        "backfill": bool(row["backfill"]),
        "source": str(row["source"]),
        "analyse_id": row["analyse_id"],
        "note": row["note"],
    }


def fetch_btc_trend_history(
    conn: sqlite3.Connection,
    *,
    period: str = "7d",
) -> list[dict]:
    """Historique des scores BTC H4 (table matérialisée + filtre période)."""
    sync_btc_trend_points(conn)
    points = [_trend_point_from_row(row) for row in db_manager.fetch_btc_trend_points(conn)]
    points = _filter_btc_trend_period(points, period)
    return [_enrich_trend_point(p) for p in points]


def _enrich_trend_point(p: dict) -> dict:
    """Métadonnées affichage graphique / tooltip / tableau."""
    score = p.get("score")
    note = p.get("note")
    snap = str(p["snapshot"])
    return {
        **p,
        "jour": p.get("jour") or format_trend_jour_fr(snap),
        "heure": p.get("heure") or format_trend_heure(snap),
        "score_label": score_trend_label(score),
        "source_label": "historique manuel" if p.get("backfill") else "Gemini",
        "date_display": format_trend_datetime(snap),
        "note": str(note).strip() if note else None,
    }


def score_trend_label(score: int | None) -> str:
    """Libellé tooltip : Haussier / Neutre / Baissier."""
    if score is None:
        return "—"
    if score >= 10:
        return "Haussier"
    if score >= 5:
        return "Neutre"
    return "Baissier"


def format_trend_datetime(snapshot: str) -> str:
    """Format lisible pour tooltip : ``2026-05-22 12:54``."""
    parsed = _parse_snapshot_iso(snapshot)
    if parsed is None:
        return snapshot.replace("T", " ")[:16]
    return parsed.strftime("%Y-%m-%d %H:%M")


def _filter_btc_trend_period(points: list[dict], period: str) -> list[dict]:
    if not points or period in ("all", "tout"):
        return points
    now = datetime.now(timezone.utc)
    days_map = {"2d": 2, "7d": 7, "30d": 30}
    days = days_map.get(period.strip().lower())
    if days is None:
        return points
    cutoff = now - timedelta(days=days)
    kept: list[dict] = []
    for p in points:
        try:
            snap = datetime.fromisoformat(p["snapshot"].replace("Z", "+00:00"))
            if snap.tzinfo is None:
                snap = snap.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if snap >= cutoff:
            kept.append(p)
    return kept


def compute_btc_streak(points: list[dict]) -> int:
    """Streak de scans tradables consécutifs (score ≥ 5), du plus récent au passé.

    Exclut les points historiques manuels (approximatifs).
    """
    real = [p for p in points if not p.get("backfill")]
    if not real:
        return 0
    streak = 0
    for p in reversed(real):
        if is_btc_tradable(p.get("score")):
            streak += 1
        else:
            break
    return streak


def trend_point_color(score: int, *, backfill: bool = False) -> str:
    """Couleur point graphique — estompée si point historique manuel."""
    if backfill:
        if score >= 10:
            return "rgba(34, 197, 94, 0.45)"
        if score >= 5:
            return "rgba(234, 179, 8, 0.45)"
        return "rgba(239, 68, 68, 0.45)"
    if score >= 10:
        return "#22c55e"
    if score >= 5:
        return "#eab308"
    return "#ef4444"


def current_trend_point(points: list[dict]) -> dict | None:
    """Dernier point « réel » (Gemini) ; fallback dernier point quelconque."""
    real = [p for p in points if not p.get("backfill")]
    if real:
        return real[-1]
    return points[-1] if points else None


def score_zone_color(score: int | None) -> str:
    """Couleur zone : green / yellow / red / muted."""
    return btc_badge_color(score)


def read_btc_fields_from_row(row: sqlite3.Row) -> dict:
    """Lit les champs BTC depuis une ligne SQLite (sans modifier ``AnalyseRow``)."""
    keys = row.keys()
    above = row["btc_above_tenkan"] if "btc_above_tenkan" in keys else None
    if above is not None:
        above = bool(above)
    return {
        "btc_above_tenkan": above,
        "btc_tenkan_slope": row["btc_tenkan_slope"] if "btc_tenkan_slope" in keys else None,
        "btc_context_score": row["btc_context_score"] if "btc_context_score" in keys else None,
        "btc_h4_snapshot": row["btc_h4_snapshot"] if "btc_h4_snapshot" in keys else None,
        "btc_chart_path": row["btc_chart_path"] if "btc_chart_path" in keys else None,
    }


def resolve_btc_chart_path(
    row: sqlite3.Row,
    *,
    captures_dir: Path,
) -> str | None:
    """Chemin PNG BTC H4 lié à une analyse (DB ou recherche par snapshot)."""
    fields = read_btc_fields_from_row(row)
    stored = fields.get("btc_chart_path")
    if stored:
        path = Path(str(stored))
        if path.is_file():
            return str(path.resolve())

    snapshot = fields.get("btc_h4_snapshot")
    if not snapshot:
        return None
    dt = _parse_snapshot_iso(str(snapshot))
    if dt is None:
        return None

    agent_dir = captures_dir / BTC_AGENT_ID
    if not agent_dir.is_dir():
        return None

    exact = agent_dir / f"BTCUSDT.P_4h_{dt.strftime('%Y%m%d_%H%M%S')}.png"
    if exact.is_file():
        return str(exact.resolve())

    minute_prefix = f"BTCUSDT.P_4h_{dt.strftime('%Y%m%d_%H%M')}"
    matches = sorted(agent_dir.glob(f"{minute_prefix}*.png"), reverse=True)
    if matches:
        return str(matches[0].resolve())

    nearest = _find_nearest_btc_png(agent_dir, dt)
    if nearest is not None:
        return str(nearest.resolve())
    return None


def is_btc_tradable(context_score: int | None) -> bool:
    """Contexte tradable = score ≥ 5 (0 = SKIP obligatoire)."""
    return context_score is not None and context_score >= 5


def filter_rows_btc_tradable(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Filtre les lignes : ne garde que btc_context_score >= 5."""
    kept: list[sqlite3.Row] = []
    for row in rows:
        fields = read_btc_fields_from_row(row)
        if is_btc_tradable(fields.get("btc_context_score")):
            kept.append(row)
    return kept


def btc_badge_color(context_score: int | None) -> str:
    """Couleur badge BTC : 10=vert, 5=orange, 0=rouge, absent=gris."""
    if context_score is None:
        return "muted"
    if context_score >= 10:
        return "green"
    if context_score >= 5:
        return "yellow"
    return "red"


def btc_badge_label(context_score: int | None) -> str:
    if context_score is None:
        return "BTC H4 —"
    if context_score >= 10:
        return f"✅ BTC {context_score}/10"
    if context_score >= 5:
        return f"⚠️ BTC {context_score}/10"
    return f"❌ BTC {context_score}/10"
