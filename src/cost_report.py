"""
Tableaux de coûts API Mammouth — style Detecte_Pump_Bitunix_P (benchmark_chart_vision).

Référence facturation : dashboard Mammouth juin 2026, gemini-3.1-flash-lite-preview.
"""

from __future__ import annotations

from src.settings import (
    MACRO_AVG_COST_EUR_PER_JOB,
    MACRO_AVG_COST_USD_PER_JOB,
    MACRO_AVG_TOKENS_PER_JOB,
    estimate_macro_cost,
)

# Abonnement Mammouth affiché sur le dashboard utilisateur
MAMMOUTH_MONTHLY_CREDIT_USD: float = 4.00


def _hline(width: int = 72) -> str:
    return "-" * width


def print_cost_per_job_table() -> None:
    """Tableau : coût unitaire par job / passe macro."""
    _, eur, usd = estimate_macro_cost(1)
    print("\n" + "=" * 72)
    print("COÛT UNITAIRE — Visio_Gemini (gemini-3.1-flash-lite-preview)")
    print("=" * 72)
    print(f"{'Élément':<28} {'Valeur':>20} {'Détail':>20}")
    print(_hline())
    rows = [
        ("1 job (1 capture + 1 analyse)", f"~${usd:.5f}", f"~€{eur:.5f}"),
        ("Tokens / job", f"~{MACRO_AVG_TOKENS_PER_JOB:,}", "1317 in + 153 out"),
        ("1 passe macro (24 jobs)", f"~${usd * 24:.4f}", f"~€{eur * 24:.4f}"),
        ("Tokens / passe (24 jobs)", f"~{MACRO_AVG_TOKENS_PER_JOB * 24:,}", "4 sym × 2 TF × 3 agents"),
    ]
    for label, val, detail in rows:
        print(f"{label:<28} {val:>20} {detail:>20}")
    print("=" * 72)


def print_monthly_budget_table() -> None:
    """Tableau : projection mensuelle selon le rythme de passes/jour."""
    passes_options = [1, 5, 10, 15, 20]
    print("\n" + "=" * 72)
    print("BUDGET MENSUEL ESTIMÉ (30 jours)")
    print("=" * 72)
    print(
        f"{'Passes/jour':>12} {'Passes/mois':>12} {'$/mois':>10} "
        f"{'€/mois':>10} {'Dans $4 ?':>12}"
    )
    print(_hline())
    for per_day in passes_options:
        per_month = per_day * 30
        _, eur, usd = estimate_macro_cost(per_month)
        fits = "✅ Oui" if usd <= MAMMOUTH_MONTHLY_CREDIT_USD else "❌ Non"
        print(
            f"{per_day:>12} {per_month:>12} {usd:>10.2f} "
            f"{eur:>10.2f} {fits:>12}"
        )
    print("=" * 72)
    print(f"Crédit abonnement Mammouth : ${MAMMOUTH_MONTHLY_CREDIT_USD:.2f} / mois")


def print_mammouth_subscription_table(
    remaining_usd: float = 3.97,
    used_usd: float = 0.097,
) -> None:
    """Tableau : suivi crédit Mammouth (valeurs dashboard)."""
    print("\n" + "=" * 72)
    print("CRÉDIT MAMMOUTH — ABONNEMENT MENSUEL")
    print("=" * 72)
    print(f"{'Poste':<32} {'Montant':>12} {'Reste':>12} {'Utilisé':>12}")
    print(_hline())
    used_sub = MAMMOUTH_MONTHLY_CREDIT_USD - remaining_usd
    print(
        f"{'Abonnement':<32} "
        f"${MAMMOUTH_MONTHLY_CREDIT_USD:>10.2f} "
        f"${remaining_usd:>10.2f} "
        f"${used_sub:>10.3f}"
    )
    print(
        f"{'Clé API (cumul)':<32} "
        f"{'—':>12} "
        f"{'—':>12} "
        f"${used_usd:>10.3f}"
    )
    passes_left = int(remaining_usd / MACRO_AVG_COST_USD_PER_JOB / 24)
    print(_hline())
    print(f"Passe(s) macro restantes (est.) : ~{passes_left}  (24 jobs/passe)")
    print("=" * 72)


def print_run_summary_table(
    jobs: int,
    tokens: int,
    cost_usd: float,
    cost_eur: float,
) -> None:
    """Tableau récapitulatif après un run."""
    print("\n" + "=" * 72)
    print("RÉSUMÉ COÛT RUN")
    print("=" * 72)
    print(f"{'Métrique':<28} {'Valeur':>20}")
    print(_hline())
    per_job_usd = cost_usd / jobs if jobs else 0.0
    rows = [
        ("Jobs exécutés", f"{jobs}"),
        ("Tokens total", f"{tokens:,}"),
        ("Coût total", f"${cost_usd:.4f} (~€{cost_eur:.4f})"),
        ("Coût / job", f"${per_job_usd:.6f}"),
    ]
    for label, val in rows:
        print(f"{label:<28} {val:>20}")
    print("=" * 72)


def print_all_budget_tables() -> None:
    """Affiche tous les tableaux de budget (CLI --budget)."""
    print_cost_per_job_table()
    print_monthly_budget_table()
    print_mammouth_subscription_table()
