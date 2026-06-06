"""Purge des captures PNG orphelines (non référencées en SQLite)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PurgeReport:
    """Résultat d'une purge de captures."""

    protected: int
    scanned: int
    deleted: int
    freed_bytes: int
    deleted_paths: tuple[str, ...]


def collect_protected_capture_paths(db_path: Path) -> set[Path]:
    """Chemins PNG encore référencés par la table analyses."""
    if not db_path.is_file():
        return set()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT image_path FROM analyses WHERE image_path IS NOT NULL AND image_path != ''"
        ).fetchall()

    protected: set[Path] = set()
    for (raw_path,) in rows:
        try:
            protected.add(Path(str(raw_path)).resolve())
        except (OSError, ValueError):
            continue
    return protected


def purge_orphan_captures(
    captures_dir: Path,
    db_path: Path,
    *,
    dry_run: bool = False,
) -> PurgeReport:
    """
    Supprime les PNG absents de analyses.image_path.

    Ne touche jamais à la base SQLite — seuls les fichiers orphelins disparaissent.
    """
    captures_dir = captures_dir.resolve()
    protected = collect_protected_capture_paths(db_path)

    deleted_paths: list[str] = []
    freed_bytes = 0
    scanned = 0

    if not captures_dir.is_dir():
        return PurgeReport(
            protected=len(protected),
            scanned=0,
            deleted=0,
            freed_bytes=0,
            deleted_paths=(),
        )

    for png_path in captures_dir.rglob("*.png"):
        scanned += 1
        resolved = png_path.resolve()
        if resolved in protected:
            continue
        size = png_path.stat().st_size if png_path.is_file() else 0
        if not dry_run:
            png_path.unlink(missing_ok=True)
        deleted_paths.append(str(resolved))
        freed_bytes += size

    return PurgeReport(
        protected=len(protected),
        scanned=scanned,
        deleted=len(deleted_paths),
        freed_bytes=freed_bytes,
        deleted_paths=tuple(deleted_paths),
    )
