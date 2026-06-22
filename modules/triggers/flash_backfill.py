"""Rescan de l'historique Telegram — archive les FLASH manquants en base.

Parcourt le canal configuré (``TELEGRAM_CHANNEL_ID``) et enregistre chaque
message « réveil confirmé » absent de ``analyses.db`` (clé token + heure signal).

Ne relance pas l'entonnoir Ichimoku sur l'historique (archivage seul).

Lancement :
  python -m modules.triggers.flash_backfill
  python -m modules.triggers.flash_backfill --since 2026-06-01
  python -m modules.triggers.flash_backfill --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from modules.triggers import db_manager
from modules.triggers.auto_listener import (
    _read_env,
    archive_flash_from_text,
    extract_token,
    is_flash_signal,
    resolve_signal_time,
)

logger = logging.getLogger("visio_gemini.triggers.flash_backfill")


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Date invalide : {value!r} (attendu YYYY-MM-DD)")


async def backfill_telegram_history(
    *,
    since: datetime | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    from telethon import TelegramClient

    api_id, api_hash, session_path, channel = _read_env()
    client = TelegramClient(session_path, api_id, api_hash)

    stats = {
        "scanned": 0,
        "flash_messages": 0,
        "inserted": 0,
        "already_present": 0,
        "skipped": 0,
    }

    await client.start()
    logger.info(
        "📡 Rescan Telegram canal %s%s…",
        channel,
        f" depuis {since.date()}" if since else " (historique complet)",
    )

    conn = db_manager.connect()
    try:
        count = 0
        async for message in client.iter_messages(channel, reverse=True):
            if limit is not None and count >= limit:
                break
            if since and message.date:
                msg_dt = message.date
                if msg_dt.tzinfo is None:
                    msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                if msg_dt < since:
                    continue

            stats["scanned"] += 1
            text = message.raw_text or message.text or ""
            if not is_flash_signal(text):
                continue

            stats["flash_messages"] += 1
            signal_time = resolve_signal_time(text, fallback=message.date)
            token_line = text[:80].replace("\n", " ")

            if dry_run:
                token = extract_token(text)
                existing = (
                    db_manager.find_flash_by_signal(conn, token, signal_time)
                    if token and signal_time
                    else None
                )
                if existing:
                    stats["already_present"] += 1
                else:
                    stats["inserted"] += 1
                logger.info(
                    "[dry-run] %s @ %s → %s",
                    token,
                    signal_time,
                    "déjà en base" if existing else "à ajouter",
                )
                count += 1
                continue

            analyse_id, created = archive_flash_from_text(
                conn, text, received_at=message.date
            )
            if analyse_id is None:
                stats["skipped"] += 1
                logger.warning("⚠ Non archivé : %s", token_line)
            elif created:
                stats["inserted"] += 1
                logger.info(
                    "💾 Ajouté id=%s — %s @ %s",
                    analyse_id,
                    signal_time,
                    token_line,
                )
            else:
                stats["already_present"] += 1

            count += 1
    finally:
        conn.close()
        await client.disconnect()

    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Archive les FLASH Telegram manquants dans analyses.db"
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Ne traiter que les messages à partir de cette date (UTC)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Nombre max de messages parcourus (défaut : tout)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Liste les FLASH sans écrire en base",
    )
    args = parser.parse_args()

    since = _parse_since(args.since)
    stats = asyncio.run(
        backfill_telegram_history(
            since=since,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )
    logger.info(
        "✅ Terminé — messages=%s flash=%s ajoutés=%s déjà présents=%s ignorés=%s",
        stats["scanned"],
        stats["flash_messages"],
        stats["inserted"],
        stats["already_present"],
        stats["skipped"],
    )


if __name__ == "__main__":
    main()
