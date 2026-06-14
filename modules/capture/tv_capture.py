"""
Capture screenshot TradingView via Playwright.

Rapatrié depuis src/capture.py — comportement identique.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from modules.config.jobs import CaptureJob
from modules.config.playwright import ensure_playwright_browsers_path

LOGIN_URL_FRAGMENT = "/accounts/signin"
DEFAULT_WAIT_MS = 5_000

STORAGE_STATE_HELP = """
❌ Fichier de session TradingView introuvable.

Chemin attendu : {path}

Ce fichier n'est PAS généré sur le VPS. Procédure :

  1. Sur Windows (PC déjà connecté à TradingView), exporter la session
     Playwright vers storage_state.json (script export_tv_session ou équivalent).
  2. Transférer le fichier sur le VPS :
       scp storage_state.json USER@VPS:~/Visio_Gemini/secrets/storage_state.json
  3. Sécuriser les permissions sur le VPS :
       chmod 600 ~/Visio_Gemini/secrets/storage_state.json
       chmod 700 ~/Visio_Gemini/secrets
""".strip()

SESSION_EXPIRED_HELP = """
❌ Session TradingView EXPIRÉE — redirection vers la page de connexion.

Le fichier storage_state.json est présent mais les cookies ne sont plus valides.

  → Reconnectez-vous à TradingView sur Windows.
  → Ré-exportez storage_state.json et re-transférez-le sur le VPS.
  → chmod 600 ~/Visio_Gemini/secrets/storage_state.json
""".strip()


def _check_storage_state(path: Path) -> None:
    if path.is_file():
        return
    print(STORAGE_STATE_HELP.format(path=path), file=sys.stderr)
    raise FileNotFoundError(f"storage_state.json introuvable : {path}")


def _build_output_path(cfg: CaptureJob) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{cfg.output_filename_prefix}_{ts}.png"
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    return cfg.captures_dir / filename


def capture_chart(
    cfg: CaptureJob,
    wait_ms: int | None = None,
    *,
    viewport: dict[str, int] | None = None,
    zoom_out_steps: int = 0,
) -> Path:
    """
    Capture le graphique configuré et retourne le chemin du PNG généré.

    Raises:
        FileNotFoundError: storage_state.json absent.
        RuntimeError: session expirée ou échec Playwright.
    """
    _check_storage_state(cfg.storage_state_path)

    browsers_path = ensure_playwright_browsers_path()
    if browsers_path:
        print(f"🎭 Playwright       : {browsers_path}")

    effective_wait_ms = wait_ms if wait_ms is not None else cfg.capture_wait_ms
    out_path = _build_output_path(cfg)
    url = cfg.chart_url

    print(f"🔐 Session          : {cfg.storage_state_path}")
    print(f"🎯 URL              : {url}")
    print(f"📸 Sortie           : {out_path}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.capture_headless)
        effective_viewport = viewport or cfg.capture_viewport
        context = browser.new_context(
            storage_state=str(cfg.storage_state_path),
            viewport=effective_viewport,
        )
        page = context.new_page()
        try:
            print("🌐 Navigation...")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(effective_wait_ms)

            if zoom_out_steps > 0:
                print(f"🔭 Zoom arrière ({zoom_out_steps} étapes)…")
                try:
                    page.locator("canvas").first.click(timeout=5_000)
                except PWTimeout:
                    pass
                for _ in range(zoom_out_steps):
                    page.keyboard.press("-")
                    page.wait_for_timeout(120)
                page.wait_for_timeout(800)

            final_url = page.url
            print(f"📍 URL finale       : {final_url}")

            if LOGIN_URL_FRAGMENT in final_url:
                print(SESSION_EXPIRED_HELP, file=sys.stderr)
                raise RuntimeError("Session TradingView expirée (redirection login).")

            page.screenshot(path=str(out_path), full_page=False)
            print(f"✅ Capture sauvegardée : {out_path}")
            return out_path

        except PWTimeout as exc:
            print("❌ Timeout pendant le chargement de la page.", file=sys.stderr)
            raise RuntimeError("Timeout pendant le chargement de la page.") from exc
        finally:
            context.close()
            browser.close()
