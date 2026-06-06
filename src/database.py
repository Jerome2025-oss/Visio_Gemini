"""Persistance SQLite des analyses Visio_Gemini."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalysisRecord:
    """Enregistrement prêt à insérer dans la table analyses."""

    timestamp: str
    date: str
    symbole: str
    timeframe: str
    agent: str
    verdict: str | None
    confiance: int | None
    raison: str | None
    observations: str | None
    layout: str
    image_path: str
    tokens_in: int
    tokens_out: int
    cout_eur: float


class AnalysisStore:
    """Gestionnaire SQLite pour les verdicts d'analyse."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        date TEXT NOT NULL,
        symbole TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        agent TEXT NOT NULL,
        verdict TEXT,
        confiance INTEGER,
        raison TEXT,
        observations TEXT,
        layout TEXT NOT NULL,
        image_path TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        cout_eur REAL NOT NULL DEFAULT 0.0
    );
    CREATE INDEX IF NOT EXISTS idx_analyses_date ON analyses(date);
    CREATE INDEX IF NOT EXISTS idx_analyses_symbole ON analyses(symbole);
    CREATE INDEX IF NOT EXISTS idx_analyses_agent ON analyses(agent);
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(self._SCHEMA)
            conn.commit()

    def insert(self, record: AnalysisRecord) -> int:
        """Insère une analyse et retourne l'id généré."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analyses (
                    timestamp, date, symbole, timeframe, agent,
                    verdict, confiance, raison, observations,
                    layout, image_path, tokens_in, tokens_out, cout_eur
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.timestamp,
                    record.date,
                    record.symbole,
                    record.timeframe,
                    record.agent,
                    record.verdict,
                    record.confiance,
                    record.raison,
                    record.observations,
                    record.layout,
                    record.image_path,
                    record.tokens_in,
                    record.tokens_out,
                    record.cout_eur,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def fetch_session_summary(self, since_timestamp: str) -> list[dict[str, Any]]:
        """Résumé des verdicts de la session courante, groupés par symbole."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbole, verdict, COUNT(*) AS count
                FROM analyses
                WHERE timestamp >= ? AND verdict IS NOT NULL
                GROUP BY symbole, verdict
                ORDER BY symbole, verdict
                """,
                (since_timestamp,),
            ).fetchall()
        return [dict(row) for row in rows]
