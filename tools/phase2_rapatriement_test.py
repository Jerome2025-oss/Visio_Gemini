#!/usr/bin/env python3
"""PHASE 2 — test pipeline après rapatriement (src/ intact)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.analyse.orchestrator import run_batch
from modules.selection.builders import build_from_run_section, build_manual_requests

# Comparaison parse_verdict legacy vs modules
from modules.agent.verdict_parser import parse_verdict as parse_new
from src.prompts import parse_verdict as parse_legacy

SAMPLE_USDT_VERDICT = """
**Indice / Crypto :** USDT.D
**Timeframe :** 4h
**Verdict :** GREEN
**Score :** 7/10
**Raison courte :** Dominance stables en hausse modérée.
"""


def test_usdt_parsing_parity() -> None:
    print("=" * 60)
    print("TEST USDT.D — parité parse_verdict legacy vs modules")
    print("=" * 60)
    legacy = parse_legacy("agent_a", SAMPLE_USDT_VERDICT, "USDT.D")
    new = parse_new("agent_a", SAMPLE_USDT_VERDICT, "USDT.D")
    print("Legacy:", json.dumps(legacy, ensure_ascii=False, indent=2))
    print("Modules:", json.dumps(new, ensure_ascii=False, indent=2))
    assert legacy == new, f"DIVERGENCE: {legacy} != {new}"
    assert new["confiance"] == 3, f"Score crypto attendu 3 (10-7), got {new['confiance']}"
    assert new["verdict"] == "RED"
    print("✅ USDT.D inversion identique\n")


def test_run_batch_btc() -> list:
    print("=" * 60)
    print("TEST run_batch — BTCUSDT 4h × 3 agents (capture + vision + parse)")
    print("=" * 60)
    requests = build_from_run_section()
    print(f"Jobs: {len(requests)}")
    results = run_batch(requests)
    print()
    for r in results:
        print(f"--- {r.request.token} {r.request.timeframe} {r.request.agent_id} ---")
        print(f"  success      : {r.success}")
        if r.error:
            print(f"  error        : {r.error}")
        if r.png_path:
            size_kb = r.png_path.stat().st_size / 1024
            print(f"  png          : {r.png_path} ({size_kb:.1f} Ko)")
        if r.parsed:
            print(f"  verdict      : {r.parsed.get('verdict')}")
            print(f"  confiance    : {r.parsed.get('confiance')}")
            print(f"  raison       : {r.parsed.get('raison', '')[:80]}")
            print(f"  observations : {r.parsed.get('observations', '')[:100]}")
        if r.vision:
            print(f"  provider     : {r.vision.provider} / {r.vision.model}")
            print(f"  cost_eur     : {r.vision.cost_eur}")
        print(f"  has_valid    : {r.has_valid_format()}")
        print()
    ok = sum(1 for r in results if r.success)
    print(f"Résumé BTC: {ok}/{len(results)} OK")
    return results


def test_usdt_live() -> None:
    print("=" * 60)
    print("TEST live USDT.D 4h agent_a (capture + vision + inversion)")
    print("=" * 60)
    requests = build_manual_requests("USDT.D", "4h", agents=["agent_a"])
    results = run_batch(requests)
    r = results[0]
    print(f"success: {r.success}")
    if r.parsed:
        print(f"parsed: {json.dumps(r.parsed, ensure_ascii=False, indent=2)}")
    if r.vision and r.vision.text:
        chart_score_line = [ln for ln in r.vision.text.splitlines() if "Score" in ln][:2]
        print("Lignes score brutes:", chart_score_line)


if __name__ == "__main__":
    test_usdt_parsing_parity()
    btc_results = test_run_batch_btc()
    test_usdt_live()
    failed = [r for r in btc_results if not r.success]
    if failed:
        sys.exit(1)
