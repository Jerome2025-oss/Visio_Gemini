"""Filtre backtest par note Tendance BTC H4 (pastilles 10 / 5 / 0)."""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from datetime import datetime, timezone

from modules.dashboard.backtest_temporal import _parse_flash_ts
from modules.triggers import btc_context, db_manager


def build_trend_score_timeline(
    conn: sqlite3.Connection,
) -> list[tuple[datetime, int]]:
    """Timeline chronologique (snapshot UTC, score) depuis ``btc_trend_points``."""
    btc_context.sync_btc_trend_points(conn)
    timeline: list[tuple[datetime, int]] = []
    for row in db_manager.fetch_btc_trend_points(conn):
        dt = btc_context._parse_snapshot_iso(str(row["snapshot_utc"]))
        if dt is None:
            continue
        timeline.append((dt, int(row["score"])))
    timeline.sort(key=lambda item: item[0])
    return timeline


def trend_score_for_flash(
    flash_ts: str | None,
    timeline: list[tuple[datetime, int]],
) -> int | None:
    """Dernière note Tendance H4 connue au moment du signal (≤ flash_ts)."""
    dt = _parse_flash_ts(flash_ts)
    if dt is None or not timeline:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    times = [item[0] for item in timeline]
    idx = bisect_right(times, dt) - 1
    if idx < 0:
        return None
    return timeline[idx][1]


def normalize_trend_scores(
    *,
    trend_10: bool = True,
    trend_5: bool = True,
    trend_0: bool = True,
) -> frozenset[int] | None:
    """``None`` = pas de filtre (les 3 pastilles cochées)."""
    if trend_10 and trend_5 and trend_0:
        return None
    allowed: set[int] = set()
    if trend_10:
        allowed.add(10)
    if trend_5:
        allowed.add(5)
    if trend_0:
        allowed.add(0)
    return frozenset(allowed)


def passes_trend_filter(
    flash_ts: str | None,
    timeline: list[tuple[datetime, int]],
    allowed: frozenset[int] | None,
) -> bool:
    if allowed is None:
        return True
    if not allowed:
        return False
    score = trend_score_for_flash(flash_ts, timeline)
    if score is None:
        return False
    return score in allowed
