"""
Point d'entrée Visio_Gemini — capture TradingView + analyse Mammouth + stockage SQLite.

Modes :
  - défaut     : section ``run`` de config.yaml (test rapide)
  - --macro    : grille macro complète (24 jobs)
  - --full     : alias de --macro
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from src.analyze import analyze_capture
from src.capture import capture_chart
from src.capture_cleanup import purge_orphan_captures
from src.cost_report import print_all_budget_tables, print_run_summary_table
from src.database import AnalysisRecord, AnalysisStore
from src.prompts import parse_verdict
from src.settings import (
    MACRO_RUN,
    LayoutNotReadyError,
    estimate_macro_cost,
    list_jobs,
    load_project,
)


@dataclass
class JobResult:
    """Résultat d'exécution d'un job (succès ou échec)."""

    symbol_key: str
    timeframe_label: str
    agent_id: str
    success: bool
    error: str | None = None
    verdict_color: str | None = None
    confidence: int | None = None
    cost_eur: float = 0.0
    tokens_total: int = 0
    db_id: int | None = None


@dataclass
class RunSummary:
    """Agrégats de fin de run."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    total_tokens: int = 0
    total_cost_eur: float = 0.0
    by_symbol: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    errors: list[str] = field(default_factory=list)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visio_Gemini — analyse macro multi-agents TradingView + Gemini Vision"
    )
    parser.add_argument(
        "--macro",
        action="store_true",
        help="Lance la grille macro complète (4 symboles × 4h/1D × 3 agents = 24 jobs)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Alias de --macro",
    )
    parser.add_argument(
        "--budget",
        action="store_true",
        help="Affiche les tableaux de coût/budget Mammouth (style Bitunix_P)",
    )
    return parser.parse_args()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _print_cost_estimate(job_count: int) -> None:
    """Affiche l'estimation de coût avant un run macro (calibrée Mammouth)."""
    est_tokens, est_eur, est_usd = estimate_macro_cost(job_count)
    _, per_job_eur, per_job_usd = estimate_macro_cost(1)
    print("── Estimation coût run macro (facturation Mammouth) ──")
    print(f"Jobs prévus       : {job_count}")
    print(
        f"Tokens estimés    : ~{est_tokens:,} "
        f"(~{estimate_macro_cost(1)[0]:,} tokens/job)"
    )
    print(
        f"Coût estimé       : ~${est_usd:.4f} (~€{est_eur:.4f}) "
        f"| ~${per_job_usd:.5f}/job (~€{per_job_eur:.5f}/job)"
    )
    print()


def _run_job(job, store: AnalysisStore) -> JobResult:
    """Exécute un job capture + analyse + insertion SQLite."""
    base = JobResult(
        symbol_key=job.symbol_key,
        timeframe_label=job.timeframe_label,
        agent_id=job.agent_id,
        success=False,
    )

    print(f"Agent      : {job.agent_id} ({job.agent_name})")
    print(f"Symbole    : {job.symbol}")
    print(f"Timeframe  : {job.timeframe} ({job.timeframe_label})")
    print(f"Layout     : {job.layout_id}")
    print()

    try:
        png_path = capture_chart(job)
    except (FileNotFoundError, RuntimeError) as exc:
        base.error = str(exc)
        print(f"❌ Capture échouée : {exc}", file=sys.stderr)
        return base

    if not png_path.is_file():
        base.error = f"PNG non généré : {png_path}"
        print(f"❌ {base.error}", file=sys.stderr)
        return base

    size_kb = png_path.stat().st_size / 1024
    if size_kb < 1:
        base.error = f"PNG suspect ({size_kb:.1f} Ko)"
        print(f"❌ {base.error}", file=sys.stderr)
        return base

    print(f"✅ Capture OK ({size_kb:.1f} Ko) : {png_path}")
    print()

    try:
        verdict_path, meta = analyze_capture(job, png_path, job.verdicts_dir)
    except Exception as exc:
        base.error = str(exc)
        print(f"❌ Analyse échouée : {exc}", file=sys.stderr)
        return base

    verdict_text = verdict_path.read_text(encoding="utf-8")
    parsed = parse_verdict(job.agent_id, verdict_text, job.symbol_key)
    now = _utc_now()

    record = AnalysisRecord(
        timestamp=now.isoformat().replace("+00:00", "Z"),
        date=now.strftime("%Y-%m-%d"),
        symbole=job.symbol_key,
        timeframe=job.timeframe_label,
        agent=job.agent_id,
        verdict=parsed["verdict"],
        confiance=parsed["confiance"],
        raison=parsed["raison"],
        observations=parsed["observations"],
        layout=job.layout_id,
        image_path=str(png_path.resolve()),
        tokens_in=int(meta.get("prompt_tokens", 0)),
        tokens_out=int(meta.get("completion_tokens", 0)),
        cout_eur=float(meta.get("cost_eur", 0.0)),
    )
    db_id = store.insert(record)

    tokens_total = record.tokens_in + record.tokens_out
    base.success = True
    base.verdict_color = parsed["verdict"]
    base.confidence = parsed["confiance"]
    base.cost_eur = record.cout_eur
    base.tokens_total = tokens_total
    base.db_id = db_id

    print(f"✅ Verdict sauvegardé : {verdict_path}")
    print(f"💾 SQLite id={db_id}   : {store.db_path}")
    print()
    print("───────── VERDICT ─────────")
    print(verdict_text)
    print("───────────────────────────")

    return base


def _update_summary(summary: RunSummary, result: JobResult) -> None:
    summary.total += 1
    if result.success:
        summary.succeeded += 1
        summary.total_tokens += result.tokens_total
        summary.total_cost_eur += result.cost_eur
        if result.verdict_color:
            counts = summary.by_symbol[result.symbol_key]
            counts[result.verdict_color] = counts.get(result.verdict_color, 0) + 1
    else:
        summary.failed += 1
        label = f"{result.symbol_key}/{result.timeframe_label}/{result.agent_id}"
        summary.errors.append(f"{label} — {result.error or 'erreur inconnue'}")


def _print_summary(summary: RunSummary, macro_mode: bool) -> None:
    print()
    print("=" * 50)
    print("RÉSUMÉ DU RUN")
    print("=" * 50)
    print(f"Jobs exécutés     : {summary.total}")
    print(f"Succès            : {summary.succeeded}")
    print(f"Échecs            : {summary.failed}")
    print(f"Tokens consommés  : {summary.total_tokens:,}")
    print(f"Coût réel         : {summary.total_cost_eur:.6f} €")

    if macro_mode and summary.succeeded == 0:
        est_tokens, est_eur, est_usd = estimate_macro_cost()
        print(
            f"Coût estimé (ref) : ~${est_usd:.4f} (~€{est_eur:.4f}) "
            f"/ ~{est_tokens:,} tokens pour 24 jobs"
        )

    if summary.by_symbol:
        print()
        print("Verdicts par symbole (GREEN / YELLOW / RED) :")
        for symbol in sorted(summary.by_symbol):
            counts = summary.by_symbol[symbol]
            parts = [
                f"{color}={counts.get(color, 0)}"
                for color in ("GREEN", "YELLOW", "RED")
            ]
            print(f"  {symbol:<12} {' | '.join(parts)}")

    if summary.errors:
        print()
        print("Erreurs :")
        for err in summary.errors:
            print(f"  • {err}")

    print("=" * 50)


def _purge_stale_captures(project, summary: RunSummary) -> None:
    """Supprime les PNG non référencés en SQLite après un run réussi."""
    if summary.succeeded == 0:
        return
    report = purge_orphan_captures(project.captures_dir, project.database_path)
    if report.deleted == 0:
        return
    freed_kb = report.freed_bytes / 1024
    print()
    print("🧹 Purge PNG orphelins")
    print(f"   Supprimés           : {report.deleted} (~{freed_kb:.0f} Ko)")
    print(f"   Conservés (SQLite)  : {report.protected}")
    print(f"   Scannés             : {report.scanned}")


def main() -> int:
    args = _parse_args()
    macro_mode = args.macro or args.full

    if args.budget:
        print_all_budget_tables()
        return 0

    try:
        project = load_project()
        run_override = MACRO_RUN if macro_mode else None
        jobs = list_jobs(project, run_override=run_override)
    except (ValueError, LayoutNotReadyError) as exc:
        print(f"❌ Configuration invalide : {exc}", file=sys.stderr)
        return 1

    mode_label = "MACRO COMPLÈTE" if macro_mode else "TEST RAPIDE (config run)"
    print("=== Visio_Gemini — capture + analyse + SQLite ===")
    print(f"Mode       : {mode_label}")
    print(f"Jobs       : {len(jobs)}")
    print()

    if macro_mode:
        _print_cost_estimate(len(jobs))

    store = AnalysisStore(project.database_path)
    summary = RunSummary()

    for index, job in enumerate(jobs, start=1):
        if len(jobs) > 1:
            print(f"─── Job {index}/{len(jobs)} ───")
        result = _run_job(job, store)
        _update_summary(summary, result)
        if index < len(jobs):
            print()

    _print_summary(summary, macro_mode)
    _purge_stale_captures(project, summary)
    return 1 if summary.failed and summary.succeeded == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
