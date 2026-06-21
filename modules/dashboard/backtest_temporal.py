"""Filtres date + heure pour le backtest temporel (intervalle datetime continu)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Début historique Visio Gemini (figé).
VISIO_PROJECT_MIN_DATE = date(2026, 6, 14)
VISIO_PROJECT_MIN_DATE_STR = VISIO_PROJECT_MIN_DATE.isoformat()

# Réglages par défaut page Backtest TEMPO (figés).
BACKTEST_TEMPO_DEFAULT_LEVERAGE = 30.0
BACKTEST_TEMPO_DEFAULT_TP = 1.4
BACKTEST_TEMPO_DEFAULT_SL = 2.0
BACKTEST_TEMPO_DEFAULT_BTC_OK = True
BACKTEST_TEMPO_DEFAULT_BTC_REPRISE = False
BACKTEST_TEMPO_DEFAULT_BTC_FAIBLE = False
BACKTEST_TEMPO_DEFAULT_REGIME_OUI = True
BACKTEST_TEMPO_DEFAULT_REGIME_NON = False


def clamp_project_date_debut(value: str | None) -> str | None:
    """Date début ≥ 14/06/2026 (début projet)."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()[:10]
    return raw if raw >= VISIO_PROJECT_MIN_DATE_STR else VISIO_PROJECT_MIN_DATE_STR


@dataclass(frozen=True)
class TemporalFilter:
    date_debut: date
    date_fin: date
    heure_debut: str = "00:00"
    heure_fin: str = "23:59"

    @property
    def borne_debut(self) -> datetime:
        return _combine_date_time(self.date_debut, self.heure_debut, is_end=False)

    @property
    def borne_fin(self) -> datetime:
        return _combine_date_time(self.date_fin, self.heure_fin, is_end=True)


def _hhmm_to_parts(hhmm: str) -> tuple[int, int]:
    raw = (hhmm or "00:00").strip()
    parts = raw.split(":")
    hour = max(0, min(23, int(parts[0])))
    minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
    return hour, minute


def _combine_date_time(d: date, hhmm: str, *, is_end: bool) -> datetime:
    hour, minute = _hhmm_to_parts(hhmm)
    second = 59 if is_end else 0
    return datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=timezone.utc)


def _parse_iso_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _normalize_time_param(value: str | None, *, default: str) -> str:
    raw = (value or default).strip()
    if not raw:
        return default
    if len(raw) == 5 and ":" in raw:
        return raw
    if len(raw) >= 5:
        return raw[:5]
    return default


def resolve_temporal_filter(
    *,
    date_debut: str | None = None,
    date_fin: str | None = None,
    heure_debut: str = "00:00",
    heure_fin: str = "23:59",
) -> TemporalFilter:
    """Plage continue : borne_debut = date début + heure début, borne_fin = date fin + heure fin."""
    d0 = _parse_iso_date(date_debut)
    d1 = _parse_iso_date(date_fin)
    today = _utc_today()
    h0 = _normalize_time_param(heure_debut, default="00:00")
    h1 = _normalize_time_param(heure_fin, default="23:59")

    if d0 is None and d1 is None:
        d0 = VISIO_PROJECT_MIN_DATE
        d1 = today
    elif d0 is not None and d1 is None:
        if d0 < VISIO_PROJECT_MIN_DATE:
            d0 = VISIO_PROJECT_MIN_DATE
        d1 = d0
    elif d0 is None and d1 is not None:
        d0 = d1 if d1 >= VISIO_PROJECT_MIN_DATE else VISIO_PROJECT_MIN_DATE
    else:
        assert d0 is not None and d1 is not None
        if d0 < VISIO_PROJECT_MIN_DATE:
            d0 = VISIO_PROJECT_MIN_DATE
        if d1 < d0:
            d0, d1 = d1, d0

    temporal = TemporalFilter(
        date_debut=d0,
        date_fin=d1,
        heure_debut=h0,
        heure_fin=h1,
    )
    if temporal.borne_fin < temporal.borne_debut:
        temporal = TemporalFilter(
            date_debut=temporal.date_fin,
            date_fin=temporal.date_debut,
            heure_debut=temporal.heure_fin,
            heure_fin=temporal.heure_debut,
        )
    return temporal


def _parse_flash_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def flash_passes_temporal(flash_ts: str | None, temporal: TemporalFilter) -> bool:
    dt = _parse_flash_ts(flash_ts)
    if dt is None:
        return False
    return temporal.borne_debut <= dt <= temporal.borne_fin


def filter_flashes_temporal(
    flashes: list[dict[str, Any]],
    temporal: TemporalFilter,
) -> list[dict[str, Any]]:
    return [
        f
        for f in flashes
        if flash_passes_temporal(f.get("flash_ts") or f.get("signal_time_utc"), temporal)
    ]


def format_datetime_fr(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M")


def temporal_interval_label(temporal: TemporalFilter) -> str:
    return (
        f"Période : du {format_datetime_fr(temporal.borne_debut)} "
        f"au {format_datetime_fr(temporal.borne_fin)} (intervalle continu)"
    )


def temporal_period_summary(
    temporal: TemporalFilter,
    count: int,
    *,
    label: str = "signaux",
) -> str:
    return f"{count} {label} · {temporal_interval_label(temporal)}"
