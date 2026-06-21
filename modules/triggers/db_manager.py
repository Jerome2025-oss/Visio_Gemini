"""Gestion de la base SQLite des analyses Ichimoku automatiques.

╔══════════════════════════════════════════════════════════════════════════╗
║ FLUX COMPLET (module auto_listener)                                        ║
║                                                                           ║
║  1. auto_listener.py  →  écoute le canal Telegram de Jérôme (Telethon)    ║
║  2. message « FLASH … réveil confirmé »  →  extraction du TOKEN           ║
║  3. déclenchement de l'entonnoir Ichimoku 3 TF (run_funnel)              ║
║  4. db_manager.py     →  enregistre score IA + décision + recap complet   ║
║  5. compare_results.py →  en fin de journée, rapproche score IA et PnL    ║
║                                                                           ║
║  Ce fichier (db_manager) ne contient QUE la couche persistance SQLite.    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.analyse.funnel import is_tradable_score, parse_confiance_from_recap

logger = logging.getLogger("visio_gemini.triggers.db")

# Racine projet : modules/triggers/db_manager.py → remonter de 3 niveaux.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "analyses.db"

# Schéma de la table principale (voir spécifications).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses_ichimoku (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token             TEXT    NOT NULL,
    signal_time_utc   TEXT,            -- heure du message de Jérôme (UTC)
    analysis_time_utc TEXT    NOT NULL,-- heure de l'analyse IA (UTC)
    score_ia          REAL,            -- 0.0 à 10.0 (NULL si non parsé)
    decision_ia       TEXT,            -- TRADE LONG / TRADE SHORT / PAS DE TRADE
    recap_complet     TEXT,            -- réponse intégrale de Gemini
    pnl_final         REAL,            -- NULL au départ, rempli en fin de journée
    exit_type         TEXT,            -- NULL au départ (TP / SL / OPEN)
    date_jour         TEXT    NOT NULL,-- YYYY-MM-DD (groupement journalier)
    chart_paths       TEXT             -- JSON : chemins PNG des 3 captures (H4,H1,M15)
);
"""

# Index pour accélérer les rapprochements token + jour (compare_results).
_INDEX = """
CREATE INDEX IF NOT EXISTS idx_analyses_token_jour
    ON analyses_ichimoku (token, date_jour);
"""

# Points historiques BTC H4 pour le graphique tendance (backfill visuel, etc.).
_BTC_SCANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS btc_scans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    btc_context_score INTEGER NOT NULL,
    btc_h4_snapshot   TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    note              TEXT,
    created_at        TEXT    NOT NULL
);
"""

_BTC_SCANS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_btc_scans_snapshot
    ON btc_scans (btc_h4_snapshot);
"""

# Régimes macro BTC H4 par plages de dates (page Date ON/OFF).
_BTC_REGIME_DATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS btc_regime_dates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_range      TEXT    NOT NULL,
    avg_price       TEXT,
    above_tenkan    INTEGER,
    position_label  TEXT,
    context_score   INTEGER NOT NULL,
    verdict         TEXT    NOT NULL,
    justification   TEXT,
    run_id          TEXT    NOT NULL,
    chart_path      TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);
"""

_BTC_REGIME_DATES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_btc_regime_dates_run
    ON btc_regime_dates (run_id, sort_order);
"""

_BTC_REGIME_DATES_SLOT_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_btc_regime_dates_date_heure_unique
    ON btc_regime_dates (date_range, heure);
"""

_BTC_REGIME_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS btc_regime_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    chart_path  TEXT,
    last_run_at TEXT,
    last_error  TEXT
);
"""

_BTC_REGIME_MIGRATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("etat", "TEXT"),
    ("transition", "TEXT"),
    ("transition_note", "INTEGER"),
    ("zone", "TEXT"),
    ("heure", "TEXT"),
)


@dataclass(frozen=True)
class AnalyseRow:
    """Représentation typée d'une ligne de ``analyses_ichimoku``."""

    id: int
    token: str
    signal_time_utc: str | None
    analysis_time_utc: str
    score_ia: float | None
    decision_ia: str | None
    recap_complet: str | None
    pnl_final: float | None
    exit_type: str | None
    date_jour: str
    chart_paths: str | None = None
    btc_change_1h: float | None = None
    btc_change_5m: float | None = None
    btc_etat: str | None = None

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> "AnalyseRow":
        keys = row.keys()
        return cls(
            id=row["id"],
            token=row["token"],
            signal_time_utc=row["signal_time_utc"],
            analysis_time_utc=row["analysis_time_utc"],
            score_ia=row["score_ia"],
            decision_ia=row["decision_ia"],
            recap_complet=row["recap_complet"],
            pnl_final=row["pnl_final"],
            exit_type=row["exit_type"],
            date_jour=row["date_jour"],
            chart_paths=row["chart_paths"] if "chart_paths" in keys else None,
            btc_change_1h=row["btc_change_1h"] if "btc_change_1h" in keys else None,
            btc_change_5m=row["btc_change_5m"] if "btc_change_5m" in keys else None,
            btc_etat=row["btc_etat"] if "btc_etat" in keys else None,
        )

    @property
    def charts(self) -> list[str]:
        """Liste des chemins PNG (décodés depuis le JSON ``chart_paths``)."""
        if not self.chart_paths:
            return []
        try:
            data = json.loads(self.chart_paths)
        except (ValueError, TypeError):
            return []
        return [str(p) for p in data] if isinstance(data, list) else []


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Ouvre (et initialise si besoin) la base SQLite.

    La connexion renvoyée utilise ``sqlite3.Row`` pour un accès par nom de colonne.
    À fermer par l'appelant (``conn.close()``) ou via ``with connect() as conn``.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    """Migrations non destructives (ALTER TABLE ADD COLUMN si manquant).

    Ne supprime ni ne réécrit jamais de données existantes.
    """
    if not _column_exists(conn, "analyses_ichimoku", "chart_paths"):
        conn.execute("ALTER TABLE analyses_ichimoku ADD COLUMN chart_paths TEXT")
        conn.commit()
        logger.info("🧱 Migration : colonne chart_paths ajoutée (données préservées).")
    for name, col_type in _BTC_FLASH_COLUMNS:
        if not _column_exists(conn, "analyses_ichimoku", name):
            conn.execute(f"ALTER TABLE analyses_ichimoku ADD COLUMN {name} {col_type}")
            conn.commit()
            logger.info("🧱 Migration : colonne %s ajoutée (données préservées).", name)
    for name, col_type in _BTC_REGIME_MIGRATE_COLUMNS:
        if not _column_exists(conn, "btc_regime_dates", name):
            conn.execute(f"ALTER TABLE btc_regime_dates ADD COLUMN {name} {col_type}")
            conn.commit()
            logger.info("🧱 Migration btc_regime_dates : colonne %s ajoutée.", name)
    _drop_legacy_usdtd_regime_tables(conn)
    _migrate_btc_regime_dates_slot_unique(conn)
    _migrate_scores_from_recap(conn)


def _migrate_btc_regime_dates_slot_unique(conn: sqlite3.Connection) -> None:
    """Colonne heure + index UNIQUE (date_range, heure) pour UPSERT par créneau 4h."""
    has_slot_unique = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='index' AND name='idx_btc_regime_dates_date_heure_unique'
        """
    ).fetchone()
    if has_slot_unique:
        return

    if not _column_exists(conn, "btc_regime_dates", "heure"):
        conn.execute(
            "ALTER TABLE btc_regime_dates ADD COLUMN heure TEXT NOT NULL DEFAULT '00H00'"
        )
        conn.commit()
        logger.info("🧱 Migration btc_regime_dates : colonne heure ajoutée.")

    conn.execute("DROP INDEX IF EXISTS idx_btc_regime_dates_date_unique")
    conn.commit()

    rows = conn.execute(
        "SELECT id, date_range, zone, heure FROM btc_regime_dates"
    ).fetchall()
    zone_to_heure = {
        "debut": "00H00",
        "midi": "12H00",
        "milieu": "12H00",
        "fin": "20H00",
    }
    for row in rows:
        date_raw = str(row["date_range"] or "").strip()
        date_norm = re.sub(r"^(\d{1,2})\s+", r"\1-", date_raw, count=1)
        zone = str(row["zone"] or "").strip().lower()
        heure_raw = str(row["heure"] or "").strip()
        if heure_raw and heure_raw != "00H00":
            heure_norm = heure_raw.upper().replace(" ", "")
            m = re.match(r"^(\d{1,2})H(\d{2})$", heure_norm)
            heure_norm = f"{int(m.group(1)):02d}H{m.group(2)}" if m else "00H00"
        else:
            heure_norm = zone_to_heure.get(zone, "00H00")
        conn.execute(
            "UPDATE btc_regime_dates SET date_range = ?, heure = ? WHERE id = ?",
            (date_norm, heure_norm, row["id"]),
        )
    conn.commit()

    conn.execute(
        """
        DELETE FROM btc_regime_dates
        WHERE id NOT IN (
            SELECT MAX(id) FROM btc_regime_dates GROUP BY date_range, heure
        )
        """
    )
    conn.executescript(_BTC_REGIME_DATES_SLOT_UNIQUE)
    conn.commit()
    logger.info(
        "🧱 Migration btc_regime_dates : index UNIQUE (date_range, heure) pour créneaux 4h."
    )


def _drop_legacy_usdtd_regime_tables(conn: sqlite3.Connection) -> None:
    """Supprime les tables USDT.D Date ON/OFF (feature retirée)."""
    for table in ("analyses_usdtd", "analyses_usdtd_state"):
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone():
            conn.execute(f"DROP TABLE {table}")
            conn.commit()
            logger.info("🧹 Table legacy supprimée : %s", table)


def _migrate_scores_from_recap(conn: sqlite3.Connection) -> None:
    """Re-parse les scores décimaux depuis ``recap_complet`` (idempotent)."""
    rows = conn.execute(
        """
        SELECT id, recap_complet FROM analyses_ichimoku
        WHERE recap_complet IS NOT NULL AND recap_complet != ''
        """
    ).fetchall()
    updated = 0
    for row in rows:
        parsed = parse_confiance_from_recap(row["recap_complet"])
        if parsed is None:
            continue
        conn.execute(
            "UPDATE analyses_ichimoku SET score_ia = ? WHERE id = ?",
            (parsed, row["id"]),
        )
        updated += 1
    if updated:
        conn.commit()
        logger.info("🧱 Migration : %s score(s) Ichimoku re-parsés (décimaux).", updated)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.executescript(_INDEX)
    conn.executescript(_BTC_SCANS_SCHEMA)
    conn.executescript(_BTC_SCANS_INDEX)
    conn.executescript(_BTC_REGIME_DATES_SCHEMA)
    conn.executescript(_BTC_REGIME_DATES_INDEX)
    conn.executescript(_BTC_REGIME_STATE_SCHEMA)
    conn.commit()
    _migrate(conn)


BACKFILL_VISUEL_SOURCE = "backfill_visuel"

BTC_ETAT_OK = "OK"
BTC_ETAT_REPRISE = "REPRISE"
BTC_ETAT_FAIBLE = "FAIBLE"
BTC_ETAT_UNKNOWN = "UNKNOWN"
BTC_ETATS_TRADABLE = frozenset({BTC_ETAT_OK, BTC_ETAT_REPRISE, BTC_ETAT_FAIBLE})

_BTC_FLASH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("btc_change_1h", "REAL"),
    ("btc_change_5m", "REAL"),
    ("btc_etat", "TEXT"),
)


def insert_btc_scan(
    conn: sqlite3.Connection,
    *,
    score: int,
    snapshot_iso: str,
    source: str,
    note: str | None = None,
) -> int:
    """Insère un point dans ``btc_scans`` et retourne l'id créé."""
    now = _utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """
        INSERT INTO btc_scans (btc_context_score, btc_h4_snapshot, source, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (score, snapshot_iso, source, note, now),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def fetch_btc_scans(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
) -> list[sqlite3.Row]:
    """Retourne les scans BTC triés par snapshot (lecture seule)."""
    if source:
        return conn.execute(
            """
            SELECT id, btc_context_score, btc_h4_snapshot, source, note, created_at
            FROM btc_scans
            WHERE source = ?
            ORDER BY btc_h4_snapshot ASC
            """,
            (source,),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, btc_context_score, btc_h4_snapshot, source, note, created_at
        FROM btc_scans
        ORDER BY btc_h4_snapshot ASC
        """
    ).fetchall()


def normalize_btc_etat(value: str | None) -> str:
    """Normalise ``btc_etat`` — anciens flashs sans ligne → ``UNKNOWN``."""
    if value in BTC_ETATS_TRADABLE:
        return value
    return BTC_ETAT_UNKNOWN


def infer_btc_etat_from_changes(
    change_1h: float | None,
    change_5m: float | None,
) -> str:
    """Déduit l'état BTC flash quand le message Telegram n'a pas de pastille.

    Règles alignées sur ``Detecte_Pump`` (``flash_alerts._btc_voyant``) :
    - Δ1h ≤ -0,5 % → FAIBLE (chute nette)
    - Δ1h < 0 et Δ5m > 0 → REPRISE (rebond court)
    - sinon → OK
    """
    if change_1h is None:
        return BTC_ETAT_UNKNOWN
    if change_1h <= -0.5:
        return BTC_ETAT_FAIBLE
    if change_1h < 0 and (change_5m or 0) > 0:
        return BTC_ETAT_REPRISE
    return BTC_ETAT_OK


def btc_etat_voyant(etat: str | None) -> str:
    """Emoji voyant pour l'affichage backtest."""
    mapping = {
        BTC_ETAT_OK: "🟢",
        BTC_ETAT_REPRISE: "✅",
        BTC_ETAT_FAIBLE: "🔴",
    }
    return mapping.get(normalize_btc_etat(etat), "—")


def btc_etat_badge_label(etat: str | None) -> str:
    """Libellé pastille voyant BTC flash (Telegram)."""
    labels = {
        BTC_ETAT_OK: "🟢 BTC OK",
        BTC_ETAT_REPRISE: "✅ BTC REPRISE",
        BTC_ETAT_FAIBLE: "🔴 BTC FAIBLE",
    }
    return labels.get(normalize_btc_etat(etat), "BTC voyant —")


def btc_etat_badge_color(etat: str | None) -> str:
    """Couleur pastille voyant BTC flash."""
    colors = {
        BTC_ETAT_OK: "green",
        BTC_ETAT_REPRISE: "yellow",
        BTC_ETAT_FAIBLE: "red",
    }
    return colors.get(normalize_btc_etat(etat), "muted")


def insert_analyse(
    conn: sqlite3.Connection,
    *,
    token: str,
    signal_time_utc: str | None,
    score_ia: float | None,
    decision_ia: str | None,
    recap_complet: str | None,
    chart_paths: list[str] | None = None,
    analysis_time_utc: str | None = None,
    date_jour: str | None = None,
    btc_change_1h: float | None = None,
    btc_change_5m: float | None = None,
    btc_etat: str | None = None,
) -> int:
    """Enregistre une nouvelle analyse IA et retourne l'``id`` créé.

    ``pnl_final`` et ``exit_type`` restent NULL (remplis plus tard par compare_results).
    ``chart_paths`` : liste des chemins PNG (encodée en JSON).
    """
    now = _utcnow()
    analysis_ts = analysis_time_utc or now.strftime("%Y-%m-%d %H:%M:%S")
    day = date_jour or now.strftime("%Y-%m-%d")
    charts_json = json.dumps(chart_paths) if chart_paths else None

    cur = conn.execute(
        """
        INSERT INTO analyses_ichimoku
            (token, signal_time_utc, analysis_time_utc, score_ia,
             decision_ia, recap_complet, pnl_final, exit_type, date_jour, chart_paths,
             btc_change_1h, btc_change_5m, btc_etat)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
        """,
        (
            token,
            signal_time_utc,
            analysis_ts,
            score_ia,
            decision_ia,
            recap_complet,
            day,
            charts_json,
            btc_change_1h,
            btc_change_5m,
            btc_etat,
        ),
    )
    conn.commit()
    new_id = int(cur.lastrowid or 0)
    logger.info(
        "💾 Analyse enregistrée (id=%s, token=%s, score=%s, décision=%s)",
        new_id,
        token,
        score_ia,
        decision_ia,
    )
    return new_id


def recently_analyzed(
    conn: sqlite3.Connection,
    token: str,
    *,
    within_minutes: int = 30,
    now: datetime | None = None,
) -> bool:
    """Anti-doublon : True si ``token`` a déjà été analysé il y a < ``within_minutes``.

    Compare ``analysis_time_utc`` (stocké en ``YYYY-MM-DD HH:MM:SS`` UTC) à maintenant.
    """
    reference = now or _utcnow()
    threshold = (reference - timedelta(minutes=within_minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    row = conn.execute(
        """
        SELECT 1 FROM analyses_ichimoku
        WHERE token = ? AND analysis_time_utc >= ?
        ORDER BY analysis_time_utc DESC
        LIMIT 1
        """,
        (token, threshold),
    ).fetchone()
    return row is not None


def is_accepted(score_ia: float | int | None) -> bool:
    """Accepté Ichimoku = confiance ≥ 6/10 (seuil impartial, dashboard = backtest)."""
    return is_tradable_score(score_ia)


def fetch_recent(
    conn: sqlite3.Connection,
    limit: int = 50,
    *,
    accepted_only: bool = False,
) -> list[AnalyseRow]:
    """Retourne les dernières analyses (plus récentes d'abord) — lecture seule.

    ``accepted_only`` : ne garde que les flashs avec score ≥ 6/10 (vue « Ichimoku »).
    """
    rows = conn.execute(
        "SELECT * FROM analyses_ichimoku ORDER BY id DESC",
    ).fetchall()
    analyses = [AnalyseRow.from_sqlite(r) for r in rows]
    if accepted_only:
        analyses = [a for a in analyses if is_accepted(a.score_ia)]
    return analyses[:limit]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Compteurs d'affichage : total des flashs depuis la bascule et nb acceptés."""
    rows = conn.execute("SELECT score_ia FROM analyses_ichimoku").fetchall()
    total = len(rows)
    accepted = sum(1 for r in rows if is_accepted(r["score_ia"]))
    return {"total": total, "accepted": accepted}


def counts_flash_only(conn: sqlite3.Connection) -> dict[str, int]:
    """Compteurs FLASH (exclut les scans BTC H4 planifiés systemd)."""
    rows = conn.execute(
        """
        SELECT score_ia FROM analyses_ichimoku
        WHERE decision_ia IS NULL OR decision_ia != 'BTC H4 SCAN'
        """
    ).fetchall()
    total = len(rows)
    accepted = sum(1 for r in rows if is_accepted(r["score_ia"]))
    return {"total": total, "accepted": accepted}


def fetch_by_day(conn: sqlite3.Connection, date_jour: str) -> list[AnalyseRow]:
    """Retourne toutes les analyses d'un jour donné (``YYYY-MM-DD``)."""
    rows = conn.execute(
        "SELECT * FROM analyses_ichimoku WHERE date_jour = ? ORDER BY id",
        (date_jour,),
    ).fetchall()
    return [AnalyseRow.from_sqlite(r) for r in rows]


def fetch_latest_for_token(
    conn: sqlite3.Connection,
    token: str,
    *,
    date_jour: str | None = None,
) -> AnalyseRow | None:
    """Dernière analyse pour un token (optionnellement restreinte à un jour)."""
    if date_jour:
        row = conn.execute(
            """
            SELECT * FROM analyses_ichimoku
            WHERE token = ? AND date_jour = ?
            ORDER BY id DESC LIMIT 1
            """,
            (token, date_jour),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM analyses_ichimoku WHERE token = ? ORDER BY id DESC LIMIT 1",
            (token,),
        ).fetchone()
    return AnalyseRow.from_sqlite(row) if row else None


def update_trade_result(
    conn: sqlite3.Connection,
    *,
    analyse_id: int,
    pnl_final: float | None,
    exit_type: str | None,
) -> bool:
    """Renseigne le PnL réel et le type de sortie d'une analyse (par ``id``)."""
    cur = conn.execute(
        """
        UPDATE analyses_ichimoku
        SET pnl_final = ?, exit_type = ?
        WHERE id = ?
        """,
        (pnl_final, exit_type, analyse_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    if updated:
        logger.info(
            "🔄 Trade mis à jour (id=%s, pnl=%s, exit=%s)",
            analyse_id,
            pnl_final,
            exit_type,
        )
    return updated


def fetch_btc_regime_dates(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
) -> list[sqlite3.Row]:
    """Toutes les lignes Date ON/OFF accumulées (historique), tri chronologique."""
    if run_id is not None:
        return conn.execute(
            """
            SELECT id, date_range, heure, etat, zone, transition, transition_note,
                   avg_price, above_tenkan, position_label,
                   context_score, verdict, justification, run_id, chart_path,
                   sort_order, created_at
            FROM btc_regime_dates
            WHERE run_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (run_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, date_range, heure, etat, zone, transition, transition_note,
               avg_price, above_tenkan, position_label,
               context_score, verdict, justification, run_id, chart_path,
               sort_order, created_at
        FROM btc_regime_dates
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()


def fetch_latest_btc_regime_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT run_id FROM btc_regime_dates
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def fetch_btc_regime_meta(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Métadonnées du dernier run (horodatage, chart)."""
    state = conn.execute(
        "SELECT chart_path, last_run_at, last_error FROM btc_regime_state WHERE id = 1"
    ).fetchone()
    row = conn.execute(
        """
        SELECT run_id, chart_path, created_at
        FROM btc_regime_dates
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    total_row = conn.execute("SELECT COUNT(*) AS n FROM btc_regime_dates").fetchone()
    n_total = str(total_row["n"]) if total_row else "0"
    chart_path = (state["chart_path"] if state else None) or (row["chart_path"] if row else None)
    if not row and not state:
        return {"run_id": None, "chart_path": None, "created_at": None, "n_rows": "0", "last_error": None}
    return {
        "run_id": str(row["run_id"]) if row else None,
        "chart_path": chart_path,
        "created_at": (row["created_at"] if row else None) or (state["last_run_at"] if state else None),
        "n_rows": n_total,
        "last_error": state["last_error"] if state else None,
    }


def save_btc_regime_state(
    conn: sqlite3.Connection,
    *,
    chart_path: str | None,
    last_error: str | None = None,
) -> None:
    """Persiste la dernière capture / erreur (même si le parse Gemini échoue)."""
    now = _utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO btc_regime_state (id, chart_path, last_run_at, last_error)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            chart_path = excluded.chart_path,
            last_run_at = excluded.last_run_at,
            last_error = excluded.last_error
        """,
        (chart_path, now, last_error),
    )
    conn.commit()


def prune_future_btc_regime_slots(
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> int:
    """Supprime les créneaux H4 du jour courant (ou futurs) pas encore commencés (UTC)."""
    from modules.triggers.btc_regime_dates import is_slot_persistable

    now = now or _utcnow()
    rows = conn.execute(
        "SELECT id, date_range, heure FROM btc_regime_dates"
    ).fetchall()
    removed = 0
    for row in rows:
        if not is_slot_persistable(str(row["date_range"]), str(row["heure"]), now):
            conn.execute("DELETE FROM btc_regime_dates WHERE id = ?", (row["id"],))
            removed += 1
    if removed:
        conn.commit()
        logger.info("🧹 Date ON/OFF : %s créneau(x) futur(s) retiré(s).", removed)
    return removed


def upsert_btc_regime_dates(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    chart_path: str | None,
    rows: list[dict],
) -> tuple[int, int]:
    """INSERT incrémental par (date_range, heure) — ignore les clés déjà présentes."""
    now = _utcnow().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    for row in rows:
        date_label = str(row["date"]).strip()
        heure = str(row.get("heure") or "00H00").strip()
        sort_order = row.get("sort_order")
        if sort_order is None:
            sort_order = 0
        cur = conn.execute(
            """
            INSERT INTO btc_regime_dates (
                date_range, heure, etat, zone, transition, transition_note,
                avg_price, above_tenkan, position_label,
                context_score, verdict, justification,
                run_id, chart_path, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 'OFF', NULL, ?, ?, ?, ?)
            ON CONFLICT(date_range, heure) DO NOTHING
            """,
            (
                date_label,
                heure,
                row.get("etat"),
                row.get("zone"),
                row.get("transition"),
                row.get("transition_note"),
                run_id,
                chart_path,
                int(sort_order),
                now,
            ),
        )
        if cur.rowcount > 0:
            inserted += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS n FROM btc_regime_dates").fetchone()
    return inserted, int(total["n"]) if total else 0


def replace_btc_regime_dates(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    chart_path: str | None,
    rows: list[dict],
) -> int:
    """Délègue à upsert (compat) — ne remplace plus tout le tableau."""
    touched, _total = upsert_btc_regime_dates(
        conn, run_id=run_id, chart_path=chart_path, rows=rows
    )
    return touched
