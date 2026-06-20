"""TP optimal = MFE favorable max avant toucher le SL (long, bougies 1m)."""

from __future__ import annotations

from typing import Any

CLOSED_BACKTEST_RESULTATS = frozenset({"TP", "SL", "CLO_24H"})


def compute_mfe_pct_before_sl(
    *,
    entry: float,
    sl_price: float,
    flash_at_ms: int,
    candles: list[dict[str, Any]],
) -> float:
    """MFE % long : plus haut atteint avant la première bougie qui touche le SL."""
    if entry <= 0 or sl_price <= 0:
        return 0.0

    max_favorable = float(entry)
    post = [c for c in candles if int(c.get("t") or 0) >= int(flash_at_ms) + 60_000]

    for candle in post:
        low = float(candle.get("l") or candle.get("low") or 0)
        high = float(candle.get("h") or candle.get("high") or 0)
        if low <= sl_price:
            break
        if high > max_favorable:
            max_favorable = high

    return max(0.0, (max_favorable - entry) / entry * 100.0)


def format_optimal_tp_display(
    mfe_pct: float,
    *,
    leverage: float,
    sl_direct: bool = False,
) -> str:
    pnl_lev = mfe_pct * leverage
    if sl_direct and mfe_pct < 0.05:
        return "0%  SL direct"
    return f"{mfe_pct:.1f}%  (+{pnl_lev:.0f}% levier)"


def optimal_tp_css_class(mfe_pct: float, *, tp_pct: float) -> str:
    """Vert si le TP réglé laisse de l'argent sur la table."""
    if mfe_pct > tp_pct + 1e-9:
        return "tp-opt-better"
    return "tp-opt-ok"


def build_optimal_tp_fields(
    *,
    resultat: str,
    entry_price: Any,
    sl_price: Any,
    flash_at_ms: int | None,
    candles: list[dict[str, Any]] | None,
    leverage: float,
    tp_pct: float,
) -> dict[str, Any]:
    """Champs affichage / CSV pour la colonne TP OPTIMAL."""
    empty = {
        "tp_optimal_pct": None,
        "tp_optimal_pnl_leveraged": None,
        "tp_optimal_display": "—",
        "tp_optimal_class": "",
    }
    if resultat not in CLOSED_BACKTEST_RESULTATS:
        return empty
    if (
        entry_price is None
        or sl_price is None
        or flash_at_ms is None
        or not candles
    ):
        return empty

    try:
        entry = float(entry_price)
        sl = float(sl_price)
    except (TypeError, ValueError):
        return empty

    mfe_pct = compute_mfe_pct_before_sl(
        entry=entry,
        sl_price=sl,
        flash_at_ms=int(flash_at_ms),
        candles=candles,
    )
    sl_direct = resultat == "SL" and mfe_pct < 0.05
    display = format_optimal_tp_display(
        mfe_pct, leverage=leverage, sl_direct=sl_direct
    )
    return {
        "tp_optimal_pct": round(mfe_pct, 3),
        "tp_optimal_pnl_leveraged": round(mfe_pct * leverage, 2),
        "tp_optimal_display": display,
        "tp_optimal_class": optimal_tp_css_class(mfe_pct, tp_pct=tp_pct),
    }
