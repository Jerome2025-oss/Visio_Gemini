"""
Capture BTC H4 avec fenêtre temporelle via le sélecteur natif TradingView « Date Range ».

Réservé à la page Date ON/OFF (régimes) — n'altère pas capture_btc_h4_chart() flash/dashboard.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout

from modules.capture.tv_capture import capture_chart
from modules.config.jobs import build_capture_job
from modules.selection.resolver import resolve_symbol_tv
from modules.triggers.btc_context import BTC_AGENT_ID, BTC_TIMEFRAME, BTC_TOKEN

# Layout TradingView dédié « Ten Kan » (graphe épuré : ligne cyan + bloc bleu).
BTC_REGIME_LAYOUT_ID = "NGeeKEid"

logger = logging.getLogger("visio_gemini.capture.btc_h4_date_range")

# ── Paramètres configurables ───────────────────────────────────
DAYS_WINDOW = 5        # fenêtre par défaut (mode incrémental / test)
MIN_DAYS = 5
MAX_DAYS = 30
TIMEFRAME = "4h"
WAIT_AFTER_ZOOM = 1.5  # secondes

# ── Sélecteurs TradingView (data-name stables au 2026-06) ─────
GO_TO_DIALOG = '[data-name="go-to-date-dialog"]'
GO_TO_BUTTON = '[data-name="go-to-date"]'
START_DATE_INPUT = '[data-name="start-date-range"]'
END_DATE_INPUT = '[data-name="end-date-range"]'
SUBMIT_BUTTON = '[data-name="submit-button"]'

FALLBACK_WHEEL_REPETITIONS = 12
FALLBACK_WHEEL_DELTA = -150


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _clamp_days(days_window: int) -> int:
    """Force la fenêtre dans [MIN_DAYS, MAX_DAYS] (cadrage de référence)."""
    return max(MIN_DAYS, min(MAX_DAYS, days_window))


def compute_date_window(days_window: int = DAYS_WINDOW) -> tuple[str, str]:
    """Retourne (date_debut, date_fin) au format YYYY-MM-DD."""
    effective = _clamp_days(days_window)
    end = _utc_today()
    start = end - timedelta(days=effective)
    return start.isoformat(), end.isoformat()


def _dismiss_intrusive_popups(page: Page) -> None:
    """Ferme cookies, Symbol search et autres modales parasites."""
    for label in ("Accept all", "Accept", "Accepter", "Don't allow"):
        try:
            page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).click(
                timeout=500
            )
            page.wait_for_timeout(200)
        except PWTimeout:
            continue

    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)

    for sel in (
        'div[data-name="symbol-search-items-dialog"]',
        '[data-name="symbol-search"]',
        'input[data-role="search"]',
        GO_TO_DIALOG,
    ):
        try:
            loc = page.locator(sel).first
            if sel == GO_TO_DIALOG:
                continue
            if loc.is_visible(timeout=200):
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
        except PWTimeout:
            continue


def _dialog_open(page: Page) -> bool:
    try:
        return page.locator(GO_TO_DIALOG).first.is_visible(timeout=500)
    except PWTimeout:
        return False


def _open_date_range_dialog(page: Page) -> bool:
    """Ouvre le dialogue « Go to » (Alt+G ou clic icône calendrier)."""
    _dismiss_intrusive_popups(page)

    page.keyboard.press("Alt+g")
    page.wait_for_timeout(700)
    if _dialog_open(page):
        return True

    try:
        page.locator(GO_TO_BUTTON).click(timeout=5_000, force=True)
        page.wait_for_timeout(700)
        if _dialog_open(page):
            return True
    except PWTimeout:
        pass

    return False


def _select_custom_range_tab(page: Page) -> None:
    """Active l'onglet « Custom range » dans le dialogue Go to."""
    dialog = page.locator(GO_TO_DIALOG)
    dialog.get_by_text("Custom range", exact=True).click(timeout=3_000)
    page.wait_for_timeout(400)


def _fill_date_inputs(page: Page, date_start: str, date_end: str) -> bool:
    """Remplit les champs natifs start-date-range / end-date-range."""
    dialog = page.locator(GO_TO_DIALOG)
    start = dialog.locator(START_DATE_INPUT)
    end = dialog.locator(END_DATE_INPUT)
    try:
        start.wait_for(state="visible", timeout=3_000)
        end.wait_for(state="visible", timeout=3_000)
    except PWTimeout:
        return False

    start.click()
    start.fill("")
    start.fill(date_start)
    end.click()
    end.fill("")
    end.fill(date_end)
    return True


def _submit_date_range(page: Page) -> None:
    page.locator(GO_TO_DIALOG).locator(SUBMIT_BUTTON).click(timeout=3_000)


def _fallback_wheel_zoom(page: Page) -> None:
    """Option B — molette sur le canvas (jamais touches clavier +/-)."""
    logger.warning(
        "[BTC H4] Date Range introuvable — fallback molette (%s répétitions)",
        FALLBACK_WHEEL_REPETITIONS,
    )
    print(
        f"⚠️  [BTC H4] Date Range introuvable — fallback molette "
        f"({FALLBACK_WHEEL_REPETITIONS}×)"
    )
    _dismiss_intrusive_popups(page)
    try:
        canvas = page.locator("canvas").first
        canvas.click(timeout=5_000)
        box = canvas.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    except PWTimeout:
        pass

    for _ in range(FALLBACK_WHEEL_REPETITIONS):
        page.mouse.wheel(0, FALLBACK_WHEEL_DELTA)
        page.wait_for_timeout(90)


def apply_tradingview_date_range(
    page: Page,
    *,
    days_window: int = DAYS_WINDOW,
    wait_after_zoom: float = WAIT_AFTER_ZOOM,
) -> tuple[str, str, bool]:
    """
    Applique la plage [now - days_window, now] via Date Range natif.

    Returns:
        (date_debut, date_fin, used_native) — used_native=False si fallback molette.
    """
    date_start, date_end = compute_date_window(days_window)
    wait_ms = int(wait_after_zoom * 1000)

    _dismiss_intrusive_popups(page)

    if not _open_date_range_dialog(page):
        logger.warning("[BTC H4] Icône / dialogue Date Range introuvable.")
        _fallback_wheel_zoom(page)
        page.wait_for_timeout(wait_ms)
        _dismiss_intrusive_popups(page)
        return date_start, date_end, False

    try:
        _select_custom_range_tab(page)
    except PWTimeout:
        logger.warning("[BTC H4] Onglet Custom range introuvable — fallback molette.")
        page.keyboard.press("Escape")
        _fallback_wheel_zoom(page)
        page.wait_for_timeout(wait_ms)
        _dismiss_intrusive_popups(page)
        return date_start, date_end, False

    if not _fill_date_inputs(page, date_start, date_end):
        logger.warning("[BTC H4] Champs date début/fin introuvables — fallback molette.")
        page.keyboard.press("Escape")
        _fallback_wheel_zoom(page)
        page.wait_for_timeout(wait_ms)
        _dismiss_intrusive_popups(page)
        return date_start, date_end, False

    try:
        _submit_date_range(page)
    except PWTimeout:
        logger.warning("[BTC H4] Bouton Go to introuvable — fallback molette.")
        page.keyboard.press("Escape")
        _fallback_wheel_zoom(page)
        page.wait_for_timeout(wait_ms)
        _dismiss_intrusive_popups(page)
        return date_start, date_end, False

    page.wait_for_timeout(wait_ms)
    _dismiss_intrusive_popups(page)
    return date_start, date_end, True


def _log_range_applied(date_start: str, date_end: str, *, days_window: int) -> None:
    effective = _clamp_days(days_window)
    msg = (
        f"[BTC H4] Range : {date_start} → {date_end} "
        f"({effective}j) — cadrage de référence OK"
    )
    logger.info(msg)
    print(msg)


def capture_btc_h4_regime_chart(
    *,
    days_window: int = DAYS_WINDOW,
    wait_after_zoom: float = WAIT_AFTER_ZOOM,
) -> Path:
    """
    Capture BTC/USDT H4 cadrée sur ~17 jours (Date Range TradingView).

    Cadrage de référence : nuage rouge à gauche, creux bas, range récent à droite.
    """
    effective_days = _clamp_days(days_window)
    if effective_days != days_window:
        logger.warning(
            "[BTC H4] DAYS_WINDOW=%s clampé à %s (bornes %s–%s)",
            days_window,
            effective_days,
            MIN_DAYS,
            MAX_DAYS,
        )

    symbol_tv = resolve_symbol_tv(BTC_TOKEN)
    layout_id = BTC_REGIME_LAYOUT_ID
    job = build_capture_job(
        symbol_tv=symbol_tv,
        timeframe_label=BTC_TIMEFRAME,
        layout_id=layout_id,
        agent_id=BTC_AGENT_ID,
    )

    def _setup(page: Page) -> None:
        start, end, native = apply_tradingview_date_range(
            page,
            days_window=effective_days,
            wait_after_zoom=wait_after_zoom,
        )
        _log_range_applied(start, end, days_window=effective_days)
        if not native:
            logger.warning(
                "[BTC H4] Capture régime sans Date Range natif (%s → %s)",
                start,
                end,
            )

    return capture_chart(job, page_setup=_setup)
