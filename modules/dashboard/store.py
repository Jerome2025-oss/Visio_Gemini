"""Historique des derniers runs en RAM (pas de persistance disque)."""

from __future__ import annotations

from collections import deque

from modules.analyse.results import AnalysisResult

LAST_RUNS: deque[list[AnalysisResult]] = deque(maxlen=5)


def add_run(results: list[AnalysisResult]) -> None:
    LAST_RUNS.appendleft(results)


def latest() -> list[AnalysisResult]:
    return LAST_RUNS[0] if LAST_RUNS else []


def history() -> list[list[AnalysisResult]]:
    return list(LAST_RUNS)
