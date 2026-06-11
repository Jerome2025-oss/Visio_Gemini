"""Utilitaires Playwright (chemin binaires Chromium)."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PLAYWRIGHT_BROWSERS_PATH = Path.home() / ".cache" / "ms-playwright"


def _playwright_has_browsers(path: Path) -> bool:
    return path.is_dir() and any(path.glob("chromium*"))


def ensure_playwright_browsers_path() -> Path | None:
    """
    Pointe PLAYWRIGHT_BROWSERS_PATH vers l'install VPS si le chemin courant est absent.
    """
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured:
        configured_path = Path(configured)
        if _playwright_has_browsers(configured_path):
            return configured_path

    fallback = DEFAULT_PLAYWRIGHT_BROWSERS_PATH
    if _playwright_has_browsers(fallback):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(fallback)
        return fallback

    return Path(configured) if configured else None
