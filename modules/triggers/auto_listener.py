"""Listener Telegram (Telethon) → déclenchement automatique de l'entonnoir Ichimoku.

╔══════════════════════════════════════════════════════════════════════════╗
║ FLUX COMPLET                                                               ║
║                                                                           ║
║  1. Connexion Telethon au canal de Jérôme (id depuis .env)                ║
║  2. Réception d'un message « ⚡ FLASH — XXXUSDT … réveil confirmé »       ║
║  3. Extraction du TOKEN + de l'heure du signal (ligne « 🕐 … UTC »)       ║
║  4. Anti-doublon : on ignore un token déjà analysé il y a < 30 min        ║
║  5. File d'attente séquentielle (1 entonnoir à la fois — Playwright)     ║
║  6. Déclenchement de l'entonnoir Ichimoku 3 TF (H4/H1/M15) via run_funnel ║
║  7. Parsing du rapport Gemini (score /10 + décision)                      ║
║  8. Sauvegarde en base SQLite (db_manager)                                ║
║                                                                           ║
║  ⚠ run_funnel utilise Playwright en API SYNC : il est exécuté dans un     ║
║    thread (asyncio.to_thread) pour ne pas bloquer/casser la boucle async. ║
║                                                                           ║
║  Lancement :  python -m modules.triggers.auto_listener                    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

from modules.analyse.funnel import run_funnel
from modules.triggers import btc_context, db_manager

logger = logging.getLogger("visio_gemini.triggers.listener")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
SECRETS_DIR = ROOT_DIR / "secrets"

# Délai anti-doublon (minutes) : un même token n'est pas ré-analysé en deçà.
DEDUPE_MINUTES = 30

# File d'attente FLASH — sérialise entonnoir + BTC H4 (Playwright non concurrent).
_flash_queue: asyncio.Queue[str] | None = None
_queued_or_running: set[str] = set()

# ── Expressions régulières d'extraction ───────────────────────────────────
# « ⚡ FLASH — XTZUSDT »  →  capture « XTZUSDT » (tirets longs/normaux acceptés).
_TOKEN_RE = re.compile(r"FLASH\s*[—–\-:]\s*([A-Z0-9]{2,20}USDT)", re.IGNORECASE)
# « 🕐 2026-06-13 10:22:47 UTC »  →  capture « 2026-06-13 10:22:47 ».
_SIGNAL_TIME_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*UTC", re.IGNORECASE
)
# « 📊 BTC Δ1h +1.2% | Δ5m -0.3% 🟢 »
_BTC_FLASH_LINE_RE = re.compile(
    r"BTC\s*[ΔD]1h\s*([+-]?\d+(?:[.,]\d+)?)\s*%\s*\|\s*[ΔD]5m\s*([+-]?\d+(?:[.,]\d+)?)\s*%\s*([🟢✅🔴])",
    re.IGNORECASE,
)
_VOYANT_TO_ETAT: dict[str, str] = {
    "🟢": "OK",
    "✅": "REPRISE",
    "🔴": "FAIBLE",
}
BTC_ETAT_UNKNOWN = "UNKNOWN"
# Décision normalisée à partir du texte Gemini.
_DECISION_RE = re.compile(
    r"D[ÉE]CISION\s*[:：]\s*.*?(TRADE\s+LONG|TRADE\s+SHORT|PAS\s+DE\s+TRADE)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_accents(text: str) -> str:
    """Supprime les accents pour des comparaisons tolérantes (« réveil » → « reveil »)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def is_flash_signal(text: str) -> bool:
    """True si le message est un signal FLASH « réveil confirmé » exploitable."""
    if not text:
        return False
    flat = _strip_accents(text).lower()
    return "flash" in flat and "reveil confirme" in flat


def extract_token(text: str) -> str | None:
    """Extrait le token (ex. ``XTZUSDT``) d'un message FLASH, sinon None."""
    match = _TOKEN_RE.search(text or "")
    if not match:
        return None
    return match.group(1).upper()


def extract_signal_time(text: str) -> str | None:
    """Extrait l'heure UTC du signal (``YYYY-MM-DD HH:MM:SS``), sinon None."""
    match = _SIGNAL_TIME_RE.search(text or "")
    if not match:
        return None
    return match.group(1).strip()


def parse_btc_flash_metrics(
    text: str | None,
) -> tuple[float | None, float | None, str]:
    """Parse la ligne BTC du flash Telegram.

    Retourne ``(btc_change_1h, btc_change_5m, btc_etat)``.
    Si la ligne est absente → ``(None, None, UNKNOWN)``.
    """
    if not text:
        return None, None, BTC_ETAT_UNKNOWN
    match = _BTC_FLASH_LINE_RE.search(text)
    if not match:
        return None, None, BTC_ETAT_UNKNOWN
    try:
        change_1h = float(match.group(1).replace(",", "."))
        change_5m = float(match.group(2).replace(",", "."))
    except ValueError:
        return None, None, BTC_ETAT_UNKNOWN
    voyant = match.group(3)
    etat = _VOYANT_TO_ETAT.get(voyant, BTC_ETAT_UNKNOWN)
    return change_1h, change_5m, etat


def normalize_decision(text: str | None) -> str | None:
    """Normalise la décision Gemini en ``TRADE LONG`` / ``TRADE SHORT`` / ``PAS DE TRADE``."""
    if not text:
        return None
    match = _DECISION_RE.search(text)
    if not match:
        return None
    raw = re.sub(r"\s+", " ", match.group(1).upper().strip())
    return raw


def _run_funnel_blocking(
    token: str,
) -> tuple[float | None, str | None, str | None, list[str]]:
    """Exécute l'entonnoir (Playwright SYNC) — à appeler dans un thread.

    Retourne ``(score_ia, decision_ia, recap_complet, chart_paths)``.
    """
    result = run_funnel(token)
    if result.error:
        raise RuntimeError(result.error)
    decision = normalize_decision(result.report_text) or result.decision
    charts = [str(c.png_path) for c in result.captures if c.png_path]
    return result.confiance, decision, result.report_text, charts


async def process_signal(text: str) -> None:
    """Traite un message : extraction → anti-doublon → entonnoir → sauvegarde.

    Appelé par le worker de file (un flash à la fois). Ne lève jamais.
    """
    if not is_flash_signal(text):
        return

    token = extract_token(text)
    if not token:
        logger.warning("⚠ Signal FLASH détecté mais token introuvable — ignoré.")
        return

    signal_time = extract_signal_time(text)
    btc_change_1h, btc_change_5m, btc_etat = parse_btc_flash_metrics(text)
    logger.info("▶ Traitement file : token=%s, heure_signal=%s", token, signal_time)

    conn = db_manager.connect()
    try:
        if db_manager.recently_analyzed(conn, token, within_minutes=DEDUPE_MINUTES):
            logger.info(
                "⏭ %s déjà analysé il y a < %s min — entonnoir ignoré (anti-doublon).",
                token,
                DEDUPE_MINUTES,
            )
            return

        logger.info("🚀 Lancement de l'entonnoir Ichimoku 3 TF pour %s…", token)
        try:
            score, decision, recap, charts = await asyncio.to_thread(
                _run_funnel_blocking, token
            )
        except Exception as exc:  # échec capture / LLM : on log et on continue
            logger.error("❌ Analyse %s échouée : %s", token, exc)
            return

        analyse_id = db_manager.insert_analyse(
            conn,
            token=token,
            signal_time_utc=signal_time,
            score_ia=score,
            decision_ia=decision,
            recap_complet=recap,
            chart_paths=charts,
            btc_change_1h=btc_change_1h,
            btc_change_5m=btc_change_5m,
            btc_etat=btc_etat,
        )
        logger.info(
            "✅ %s analysé (id=%s) → score=%s/10, décision=%s, btc_etat=%s",
            token,
            analyse_id,
            score,
            decision,
            btc_etat,
        )
        try:
            await asyncio.to_thread(btc_context.run_btc_h4_context, analyse_id)
        except Exception as exc:
            logger.error(
                "❌ Contexte BTC H4 échoué (non bloquant, id=%s) : %s",
                analyse_id,
                exc,
            )
    finally:
        conn.close()


async def enqueue_flash_signal(text: str) -> None:
    """Met un FLASH en file d'attente (traitement séquentiel par le worker)."""
    if not is_flash_signal(text):
        return

    token = extract_token(text)
    if not token:
        logger.warning("⚠ Signal FLASH détecté mais token introuvable — ignoré.")
        return

    signal_time = extract_signal_time(text)
    if token in _queued_or_running:
        logger.info(
            "⏭ %s déjà en file ou en cours d'analyse — doublon simultané ignoré.",
            token,
        )
        return

    conn = db_manager.connect()
    try:
        if db_manager.recently_analyzed(conn, token, within_minutes=DEDUPE_MINUTES):
            logger.info(
                "⏭ %s déjà analysé il y a < %s min — ignoré avant mise en file.",
                token,
                DEDUPE_MINUTES,
            )
            return
    finally:
        conn.close()

    queue = _flash_queue
    if queue is None:
        logger.error("❌ File FLASH non initialisée — %s perdu.", token)
        return

    _queued_or_running.add(token)
    waiting = queue.qsize()
    await queue.put(text)
    if waiting:
        logger.info(
            "📥 FLASH %s en file (heure=%s) — %s en attente devant",
            token,
            signal_time,
            waiting,
        )
    else:
        logger.info("⚡ FLASH %s reçu (heure=%s) — traitement immédiat", token, signal_time)


async def _flash_worker() -> None:
    """Worker unique : entonnoir + BTC H4 un par un."""
    queue = _flash_queue
    assert queue is not None
    while True:
        text = await queue.get()
        token = extract_token(text)
        try:
            await process_signal(text)
        except Exception as exc:
            logger.exception("❌ Erreur worker file FLASH (%s) : %s", token, exc)
        finally:
            if token:
                _queued_or_running.discard(token)
            queue.task_done()
            remaining = queue.qsize()
            if remaining:
                logger.info("📋 File FLASH : %s signal(s) restant(s)", remaining)


def _init_flash_queue() -> asyncio.Queue[str]:
    global _flash_queue
    _flash_queue = asyncio.Queue()
    return _flash_queue


def _read_env() -> tuple[int, str, str, int | str]:
    """Lit et valide les variables Telegram du .env."""
    load_dotenv(ENV_PATH)

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "visio_gemini").strip()
    channel_raw = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    missing = [
        name
        for name, val in (
            ("TELEGRAM_API_ID", api_id_raw),
            ("TELEGRAM_API_HASH", api_hash),
            ("TELEGRAM_CHANNEL_ID", channel_raw),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Variables Telegram manquantes dans .env : " + ", ".join(missing)
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID doit être un entier.") from exc

    # Le canal peut être un id numérique (ex. -1001234567890) ou un @username.
    channel: int | str
    try:
        channel = int(channel_raw)
    except ValueError:
        channel = channel_raw

    # La session Telethon est stockée dans secrets/ (hors git).
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    session_path = str(SECRETS_DIR / session_name)
    return api_id, api_hash, session_path, channel


async def _amain() -> None:
    # Import tardif : Telethon n'est requis que pour le listener live.
    from telethon import TelegramClient, events

    api_id, api_hash, session_path, channel = _read_env()

    client = TelegramClient(session_path, api_id, api_hash)

    _init_flash_queue()
    worker_task = asyncio.create_task(_flash_worker(), name="flash-worker")

    @client.on(events.NewMessage(chats=channel))
    async def _handler(event) -> None:  # noqa: ANN001 (type Telethon interne)
        try:
            await enqueue_flash_signal(event.raw_text or "")
        except Exception as exc:  # garde-fou ultime : ne jamais tuer le listener
            logger.exception("❌ Erreur inattendue dans le handler : %s", exc)

    await client.start()
    me = await client.get_me()
    logger.info(
        "📡 Listener connecté (compte=%s) — écoute du canal %s…",
        getattr(me, "username", None) or getattr(me, "id", "?"),
        channel,
    )
    logger.info("📋 File FLASH séquentielle active (1 entonnoir à la fois)")
    logger.info("⏳ En attente des signaux FLASH (Ctrl+C pour arrêter)…")
    try:
        await client.run_until_disconnected()
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


async def _list_dialogs() -> None:
    """Liste toutes les conversations/canaux accessibles (id + nom + type).

    Utile pour trouver l'``id`` exact à mettre dans TELEGRAM_CHANNEL_ID
    (la source des FLASH est la conversation avec le bot Detecte_Pump).
    """
    from telethon import TelegramClient

    load_dotenv(ENV_PATH)
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH requis dans .env.")

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "visio_gemini").strip()
    client = TelegramClient(str(SECRETS_DIR / session_name), api_id, api_hash)
    await client.start()
    print(f"\n{'ID':>16}  {'TYPE':<8}  NOM")
    print("-" * 60)
    async for dialog in client.iter_dialogs():
        kind = "canal" if dialog.is_channel else ("groupe" if dialog.is_group else "user/bot")
        print(f"{dialog.id:>16}  {kind:<8}  {dialog.name}")
    print("-" * 60)
    print("→ Reportez l'ID voulu dans TELEGRAM_CHANNEL_ID (.env).\n")
    await client.disconnect()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Listener Telegram → entonnoir Ichimoku automatique."
    )
    parser.add_argument(
        "--list-dialogs",
        action="store_true",
        help="Liste les conversations/canaux (id + nom) puis quitte.",
    )
    args = parser.parse_args()

    try:
        if args.list_dialogs:
            asyncio.run(_list_dialogs())
        else:
            asyncio.run(_amain())
    except KeyboardInterrupt:
        logger.info("👋 Arrêt du listener (interruption clavier).")


if __name__ == "__main__":
    main()
