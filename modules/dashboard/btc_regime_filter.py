"""Filtre backtest par état BTC ON/OFF (régime H4 OUI/NON par créneau)."""

from __future__ import annotations

import sqlite3

from modules.dashboard.backtest_temporal import _parse_flash_ts
from modules.triggers import db_manager
from modules.triggers.btc_regime_dates import (
    _h4_floor,
    format_day_label,
    normalize_date_label,
    normalize_heure,
)


def flash_ts_to_regime_key(flash_ts: str | None) -> tuple[str, str] | None:
    """Mappe un horodatage flash → (date « 15-juin », créneau « 04H00 ») UTC."""
    dt = _parse_flash_ts(flash_ts)
    if dt is None:
        return None
    floor = _h4_floor(dt)
    return format_day_label(floor.date()), f"{floor.hour:02d}H00"


def build_regime_lookup(
    conn: sqlite3.Connection,
    *,
    overrides: dict[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], str]:
    """Index (date, heure) → OUI/NON depuis btc_regime_dates (+ surcharges simulation)."""
    lookup: dict[tuple[str, str], str] = {}
    for row in db_manager.fetch_btc_regime_dates(conn):
        date_label = normalize_date_label(str(row["date_range"]))
        heure = normalize_heure(str(row["heure"]))
        if not heure:
            continue
        etat = str(row["etat"] or "").strip().upper()
        if etat in ("OUI", "NON"):
            lookup[(date_label, heure)] = etat
    if overrides:
        lookup.update(overrides)
    return lookup


def parse_regime_overrides(
    raw: dict[str, str] | None,
) -> dict[tuple[str, str], str] | None:
    """Convertit ``{\"15-juin|04H00\": \"NON\", ...}`` en clés internes."""
    if not raw:
        return None
    out: dict[tuple[str, str], str] = {}
    for key, etat in raw.items():
        etat_u = str(etat or "").strip().upper()
        if etat_u not in ("OUI", "NON"):
            continue
        parts = str(key).split("|", 1)
        if len(parts) != 2:
            continue
        day = normalize_date_label(parts[0].strip())
        heure = normalize_heure(parts[1].strip())
        if day and heure:
            out[(day, heure)] = etat_u
    return out or None


def normalize_regime_etats(
    *,
    regime_oui: bool = True,
    regime_non: bool = True,
) -> frozenset[str] | None:
    """``None`` = filtre BTC ON/OFF désactivé (les deux cochés ou aucun coché)."""
    if not regime_oui and not regime_non:
        return None
    if regime_oui and regime_non:
        return None
    allowed: set[str] = set()
    if regime_oui:
        allowed.add("OUI")
    if regime_non:
        allowed.add("NON")
    return frozenset(allowed)


def regime_etat_for_flash(
    flash_ts: str | None,
    lookup: dict[tuple[str, str], str],
) -> str | None:
    key = flash_ts_to_regime_key(flash_ts)
    if key is None:
        return None
    return lookup.get(key)


def passes_regime_filter(
    flash_ts: str | None,
    lookup: dict[tuple[str, str], str],
    allowed: frozenset[str] | None,
) -> bool:
    if allowed is None:
        return True
    if not allowed:
        return False
    etat = regime_etat_for_flash(flash_ts, lookup)
    if etat is None:
        return False
    return etat in allowed
