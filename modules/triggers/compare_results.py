"""Comparaison fin de journée : score IA Ichimoku vs PnL réel des trades.

╔══════════════════════════════════════════════════════════════════════════╗
║ FLUX COMPLET                                                               ║
║                                                                           ║
║  En fin de journée, on fournit la liste des trades clôturés (token, PnL,  ║
║  type de sortie). Ce module :                                             ║
║    1. lit les trades (fichier CSV  OU  copier-coller manuel via stdin)    ║
║    2. rapproche chaque trade de l'analyse Ichimoku du même token/jour     ║
║    3. met à jour pnl_final + exit_type dans la base (db_manager)          ║
║    4. génère un rapport de corrélation (score IA gagnants vs perdants)    ║
║                                                                           ║
║  Exemples :                                                               ║
║    python -m modules.triggers.compare_results --csv trades.csv           ║
║    python -m modules.triggers.compare_results --paste   (puis Ctrl+D)    ║
║    python -m modules.triggers.compare_results --csv t.csv --date 2026-06-13║
║                                                                           ║
║  Format attendu des trades (séparateur , ; tab ou espaces) :             ║
║    TOKEN, PNL, EXIT          ex.  XTZUSDT, +3.5, TP                       ║
║    (une ligne d'en-tête « token,pnl,exit » est tolérée et ignorée)       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from modules.triggers import db_manager
from modules.triggers.db_manager import AnalyseRow

logger = logging.getLogger("visio_gemini.triggers.compare")

_HEADER_TOKENS = {"token", "symbol", "symbole", "paire"}
_SPLIT_RE = re.compile(r"[,;\t]+|\s{2,}")


@dataclass(frozen=True)
class TradeInput:
    """Un trade clôturé fourni par l'utilisateur."""

    token: str
    pnl: float | None
    exit_type: str | None


def _normalize_token(raw: str) -> str:
    key = raw.strip().upper().replace("/", "")
    if key and not key.endswith("USDT") and not key.endswith(".D"):
        key = f"{key}USDT"
    return key


def _parse_pnl(raw: str) -> float | None:
    """Parse un PnL : ``+3.5``, ``-2``, ``3,5``, ``1.2%`` → float (None si vide)."""
    cleaned = raw.strip().replace("%", "").replace(" ", "")
    if not cleaned:
        return None
    # Décimale française « 3,5 » → « 3.5 » (uniquement si pas déjà un point).
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_exit(raw: str) -> str | None:
    val = raw.strip().upper()
    if not val:
        return None
    if val in {"TP", "TAKEPROFIT", "TAKE-PROFIT", "WIN", "G", "GAGNANT"}:
        return "TP"
    if val in {"SL", "STOPLOSS", "STOP-LOSS", "LOSS", "P", "PERDANT"}:
        return "SL"
    if val in {"OPEN", "OUVERT", "ENCOURS", "RUNNING"}:
        return "OPEN"
    return val


def parse_trades_text(text: str) -> list[TradeInput]:
    """Parse un bloc texte (CSV ou copier-coller) en liste de ``TradeInput``."""
    trades: list[TradeInput] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in _SPLIT_RE.split(line) if p != ""]
        if not parts:
            continue
        # Ignore une éventuelle ligne d'en-tête.
        if parts[0].strip().lower() in _HEADER_TOKENS:
            continue
        token = _normalize_token(parts[0])
        if not token:
            continue
        pnl = _parse_pnl(parts[1]) if len(parts) > 1 else None
        exit_type = _normalize_exit(parts[2]) if len(parts) > 2 else None
        trades.append(TradeInput(token=token, pnl=pnl, exit_type=exit_type))
    return trades


def _is_winner(pnl: float | None, exit_type: str | None) -> bool | None:
    """Gagnant/perdant : priorité au PnL, fallback sur le type de sortie."""
    if pnl is not None and pnl != 0:
        return pnl > 0
    if exit_type == "TP":
        return True
    if exit_type == "SL":
        return False
    return None  # indéterminé (OPEN / PnL nul)


def _decision_is_trade(decision: str | None) -> bool:
    return bool(decision) and "PAS DE TRADE" not in (decision or "").upper()


def _match_ok(decision: str | None, pnl: float | None, exit_type: str | None) -> bool:
    """✅ si (Décision = TRADE et gagnant) ou (PAS DE TRADE et perdant)."""
    winner = _is_winner(pnl, exit_type)
    if winner is None:
        return False
    is_trade = _decision_is_trade(decision)
    return (is_trade and winner) or (not is_trade and not winner)


@dataclass
class MatchedRow:
    trade: TradeInput
    analyse: AnalyseRow


def apply_and_report(
    trades: list[TradeInput],
    *,
    date_jour: str,
    db_path: Path | str | None = None,
) -> str:
    """Met à jour la base puis retourne le rapport de corrélation formaté."""
    conn = db_manager.connect(db_path)
    matched: list[MatchedRow] = []
    unmatched: list[TradeInput] = []
    try:
        for trade in trades:
            analyse = db_manager.fetch_latest_for_token(
                conn, trade.token, date_jour=date_jour
            )
            if analyse is None:
                unmatched.append(trade)
                logger.warning(
                    "❓ Aucune analyse %s pour le %s — trade ignoré.",
                    trade.token,
                    date_jour,
                )
                continue
            db_manager.update_trade_result(
                conn,
                analyse_id=analyse.id,
                pnl_final=trade.pnl,
                exit_type=trade.exit_type,
            )
            matched.append(MatchedRow(trade=trade, analyse=analyse))
    finally:
        conn.close()

    return _format_report(matched, unmatched, date_jour=date_jour)


def _avg_score(rows: list[MatchedRow], *, winners: bool) -> tuple[float | None, int]:
    scores: list[int] = []
    for m in rows:
        win = _is_winner(m.trade.pnl, m.trade.exit_type)
        if win is None or win != winners:
            continue
        if m.analyse.score_ia is not None:
            scores.append(m.analyse.score_ia)
    if not scores:
        return None, 0
    return sum(scores) / len(scores), len(scores)


def _format_report(
    matched: list[MatchedRow],
    unmatched: list[TradeInput],
    *,
    date_jour: str,
) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"  RAPPORT DE CORRÉLATION IA ↔ PnL — {date_jour}")
    lines.append("=" * 78)

    avg_win, n_win = _avg_score(matched, winners=True)
    avg_lose, n_lose = _avg_score(matched, winners=False)
    win_str = f"{avg_win:.1f}/10" if avg_win is not None else "n/a"
    lose_str = f"{avg_lose:.1f}/10" if avg_lose is not None else "n/a"
    lines.append("")
    lines.append(f"  Score IA moyen — trades GAGNANTS (TP / PnL+) : {win_str}  ({n_win})")
    lines.append(f"  Score IA moyen — trades PERDANTS (SL / PnL-) : {lose_str}  ({n_lose})")
    lines.append("")

    # Tableau détaillé.
    header = (
        f"  {'Token':<12} {'Score IA':>8} {'Décision IA':<14} "
        f"{'PnL réel':>9} {'Exit':<6} {'Match?':<6}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    n_match = 0
    for m in matched:
        a, t = m.analyse, m.trade
        score = (
            f"{a.score_ia}/10".replace(".", ",")
            if a.score_ia is not None
            else "n/a"
        )
        decision = (a.decision_ia or "n/a")[:14]
        pnl = f"{t.pnl:+.2f}" if t.pnl is not None else "n/a"
        exit_t = t.exit_type or "-"
        ok = _match_ok(a.decision_ia, t.pnl, t.exit_type)
        if ok:
            n_match += 1
        mark = "✅" if ok else "❌"
        lines.append(
            f"  {t.token:<12} {score:>8} {decision:<14} {pnl:>9} {exit_t:<6} {mark:<6}"
        )

    lines.append("")
    total = len(matched)
    if total:
        pct = 100.0 * n_match / total
        lines.append(f"  Cohérence IA : {n_match}/{total} trades ({pct:.0f}%)")
    if unmatched:
        tokens = ", ".join(t.token for t in unmatched)
        lines.append(f"  ⚠ Trades sans analyse IA ({len(unmatched)}) : {tokens}")
    lines.append("=" * 78)
    return "\n".join(lines)


def _read_input(args: argparse.Namespace) -> str:
    if args.csv:
        path = Path(args.csv)
        if not path.is_file():
            raise FileNotFoundError(f"Fichier CSV introuvable : {path}")
        return path.read_text(encoding="utf-8")
    # Mode paste : lecture sur stdin jusqu'à EOF (Ctrl+D).
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print(
        "Collez les trades (TOKEN, PNL, EXIT), une ligne par trade, "
        "puis Ctrl+D :",
        file=sys.stderr,
    )
    return sys.stdin.read()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Compare le score IA Ichimoku au PnL réel des trades du jour."
    )
    parser.add_argument("--csv", help="Chemin d'un fichier CSV (token,pnl,exit).")
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Saisie manuelle des trades via stdin (copier-coller).",
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Jour des analyses à rapprocher (YYYY-MM-DD, défaut = aujourd'hui UTC).",
    )
    args = parser.parse_args()

    raw = _read_input(args)
    trades = parse_trades_text(raw)
    if not trades:
        logger.error("Aucun trade valide détecté dans l'entrée.")
        sys.exit(1)

    logger.info("%s trade(s) à rapprocher pour le %s.", len(trades), args.date)
    report = apply_and_report(trades, date_jour=args.date)
    print("\n" + report)


if __name__ == "__main__":
    main()
