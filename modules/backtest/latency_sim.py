"""Backtest comparatif latence : entrée à ``analysis_time`` vs ``analysis_time + LATENCY``.

Source de prix
--------------
Bougies **1 minute** (``flash_klines`` via API bitunix / Detecte_Pump).

Granularité ``PRICE_GRANULARITY`` = ``"1m"`` :
- Le prix d'entrée est le **close** de la dernière bougie 1m dont ``open_time <= entry_ms``
  (même convention que le backtest bitunix existant).
- Une latence de 30 s **à l'intérieur de la même minute** ne change pas le prix d'entrée ;
  l'écart optimiste/réaliste apparaît surtout quand ``analysis_time + 30s`` franchit une
  nouvelle bougie.
- Les mèches éphémères **< 1 minute** ne sont pas détectables avec certitude ; on signale
  les TP où ``high >= tp`` mais ``close < tp`` sur la bougie de sortie (proxy « mèche fragile »).

Règle intra-bougie (pessimiste)
-------------------------------
Si ``low <= SL`` **et** ``high >= TP`` sur la même bougie → **SL en premier**.

Le backtest historique ``/backtest`` (signal_time) n'est **pas** modifié par ce module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

UTC = timezone.utc
MS_PER_MINUTE = 60_000
MS_PER_HOUR = 3_600_000
HOURS_AFTER = 24

# Paramètres modifiables
DEFAULT_LATENCY_SECONDS = 30
PRICE_GRANULARITY = "1m"

Outcome = Literal["TP", "SL", "LIQUIDATION", "OPEN", "TIMEOUT", "ERR"]


@dataclass(frozen=True)
class KlineRow:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class TradeSimResult:
    mode: Literal["optimistic", "realistic"]
    entry_ms: int
    entry_price: float | None
    tp_price: float | None
    sl_price: float | None
    outcome: Outcome
    pnl_pct: float | None
    exit_minutes: int | None
    exit_price: float | None
    fragile_wick_tp: bool = False
    provisional: bool = False
    error: str | None = None


@dataclass
class SignalComparison:
    token: str
    signal_time_utc: str
    analysis_time_utc: str
    score: float | None
    decision: str | None
    optimistic: TradeSimResult
    realistic: TradeSimResult
    flipped_win_to_loss: bool = False


def ts_to_ms(ts: str) -> int:
    """``YYYY-MM-DD HH:MM:SS`` (UTC) → timestamp ms."""
    normalized = ts.strip().replace("T", " ").replace("Z", "")[:19]
    dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def klines_from_api(candles: list[dict[str, Any]]) -> list[KlineRow]:
    return [
        KlineRow(
            open_time=int(c["t"]),
            open=float(c["o"]),
            high=float(c["h"]),
            low=float(c["l"]),
            close=float(c["c"]),
            volume=float(c.get("v") or 0),
        )
        for c in candles
    ]


def _close_at_or_before(klines: list[KlineRow], target_ms: int) -> float | None:
    last: float | None = None
    for row in klines:
        if row.open_time <= target_ms:
            last = row.close
        else:
            break
    return last


def entry_price_at_ms(klines: list[KlineRow], entry_ms: int) -> float | None:
    """Prix d'entrée LONG sur bougies 1m (close à ``entry_ms`` ou première bougie suivante)."""
    entry = _close_at_or_before(klines, entry_ms)
    if entry is not None and entry > 0:
        return entry
    for row in klines:
        if row.open_time >= entry_ms:
            return row.close
    return klines[0].close if klines else None


def _is_24h_complete(flash_at_utc: str, last_open_ms: int | None) -> bool:
    if last_open_ms is None:
        return False
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    flash_ms = ts_to_ms(flash_at_utc)
    end_ms = flash_ms + HOURS_AFTER * MS_PER_HOUR
    expected_last = end_ms - MS_PER_MINUTE
    if now_ms >= end_ms:
        return last_open_ms >= expected_last
    latest_closed = ((now_ms // MS_PER_MINUTE) - 1) * MS_PER_MINUTE
    return last_open_ms >= latest_closed


def _post_candles(klines: list[KlineRow], entry_ms: int) -> list[KlineRow]:
    """Bougies dont la fenêtre chevauche la période après ``entry_ms``."""
    return [k for k in klines if k.open_time + MS_PER_MINUTE > entry_ms]


def simulate_long_from_entry(
    klines: list[KlineRow],
    *,
    entry_ms: int,
    leverage: float,
    tp_pct: float,
    sl_pct: float,
    flash_at_utc: str,
    mode: Literal["optimistic", "realistic"],
    origin_ms: int | None = None,
) -> TradeSimResult:
    """Simule un LONG futures à partir d'un horodatage d'entrée explicite."""
    origin = origin_ms if origin_ms is not None else entry_ms
    if not klines:
        return TradeSimResult(
            mode=mode,
            entry_ms=entry_ms,
            entry_price=None,
            tp_price=None,
            sl_price=None,
            outcome="ERR",
            pnl_pct=None,
            exit_minutes=None,
            exit_price=None,
            error="Aucune bougie",
        )

    entry = entry_price_at_ms(klines, entry_ms)
    if entry is None or entry <= 0:
        return TradeSimResult(
            mode=mode,
            entry_ms=entry_ms,
            entry_price=None,
            tp_price=None,
            sl_price=None,
            outcome="ERR",
            pnl_pct=None,
            exit_minutes=None,
            exit_price=None,
            error="Prix d'entrée indisponible",
        )

    tp_price = entry * (1.0 + tp_pct / 100.0)
    sl_price = entry * (1.0 - sl_pct / 100.0)
    liq_price = entry * (1.0 - (1.0 / leverage) * 0.95)
    sl_above_liq = sl_price >= liq_price

    post = _post_candles(klines, entry_ms)
    if not post:
        return _timeout_or_open(
            klines=klines,
            post=post,
            entry=entry,
            entry_ms=entry_ms,
            origin_ms=origin,
            leverage=leverage,
            tp_price=tp_price,
            sl_price=sl_price,
            liq_price=liq_price,
            flash_at_utc=flash_at_utc,
            mode=mode,
        )

    for candle in post:
        exit_min = max(0, int((candle.open_time - origin) / MS_PER_MINUTE))
        tp_touched = candle.high >= tp_price
        sl_touched = candle.low <= sl_price

        # Règle pessimiste : TP et SL sur la même bougie → SL d'abord.
        if tp_touched and sl_touched:
            return TradeSimResult(
                mode=mode,
                entry_ms=entry_ms,
                entry_price=round(entry, 8),
                tp_price=round(tp_price, 8),
                sl_price=round(sl_price, 8),
                outcome="SL",
                pnl_pct=round(-sl_pct * leverage, 3),
                exit_minutes=exit_min,
                exit_price=round(sl_price, 8),
                fragile_wick_tp=False,
            )

        if sl_above_liq and sl_touched:
            return TradeSimResult(
                mode=mode,
                entry_ms=entry_ms,
                entry_price=round(entry, 8),
                tp_price=round(tp_price, 8),
                sl_price=round(sl_price, 8),
                outcome="SL",
                pnl_pct=round(-sl_pct * leverage, 3),
                exit_minutes=exit_min,
                exit_price=round(sl_price, 8),
            )

        if candle.low <= liq_price:
            return TradeSimResult(
                mode=mode,
                entry_ms=entry_ms,
                entry_price=round(entry, 8),
                tp_price=round(tp_price, 8),
                sl_price=round(sl_price, 8),
                outcome="LIQUIDATION",
                pnl_pct=-100.0,
                exit_minutes=exit_min,
                exit_price=round(liq_price, 8),
            )

        if sl_touched:
            return TradeSimResult(
                mode=mode,
                entry_ms=entry_ms,
                entry_price=round(entry, 8),
                tp_price=round(tp_price, 8),
                sl_price=round(sl_price, 8),
                outcome="SL",
                pnl_pct=round(-sl_pct * leverage, 3),
                exit_minutes=exit_min,
                exit_price=round(sl_price, 8),
            )

        if tp_touched:
            fragile = candle.close < tp_price
            return TradeSimResult(
                mode=mode,
                entry_ms=entry_ms,
                entry_price=round(entry, 8),
                tp_price=round(tp_price, 8),
                sl_price=round(sl_price, 8),
                outcome="TP",
                pnl_pct=round(tp_pct * leverage, 3),
                exit_minutes=exit_min,
                exit_price=round(tp_price, 8),
                fragile_wick_tp=fragile,
            )

    return _timeout_or_open(
        klines=klines,
        post=post,
        entry=entry,
        entry_ms=entry_ms,
        origin_ms=origin,
        leverage=leverage,
        tp_price=tp_price,
        sl_price=sl_price,
        liq_price=liq_price,
        flash_at_utc=flash_at_utc,
        mode=mode,
    )


def _timeout_or_open(
    *,
    klines: list[KlineRow],
    post: list[KlineRow],
    entry: float,
    entry_ms: int,
    origin_ms: int,
    leverage: float,
    tp_price: float,
    sl_price: float,
    liq_price: float,
    flash_at_utc: str,
    mode: Literal["optimistic", "realistic"],
) -> TradeSimResult:
    last = post[-1] if post else klines[-1]
    final = last.close
    exit_min = max(0, int((last.open_time - origin_ms) / MS_PER_MINUTE))
    pnl = ((final - entry) / entry) * leverage * 100.0
    complete = _is_24h_complete(flash_at_utc, last.open_time)
    return TradeSimResult(
        mode=mode,
        entry_ms=entry_ms,
        entry_price=round(entry, 8),
        tp_price=round(tp_price, 8),
        sl_price=round(sl_price, 8),
        outcome="TIMEOUT" if complete else "OPEN",
        pnl_pct=round(pnl, 3),
        exit_minutes=exit_min,
        exit_price=round(final, 8),
        provisional=not complete,
    )


def _is_win(outcome: Outcome) -> bool:
    return outcome == "TP"


def _is_loss(outcome: Outcome) -> bool:
    return outcome in ("SL", "LIQUIDATION")


def _sim_to_dict(sim: TradeSimResult) -> dict[str, Any]:
    return {
        "mode": sim.mode,
        "entry_ms": sim.entry_ms,
        "entry_price": sim.entry_price,
        "tp_price": sim.tp_price,
        "sl_price": sim.sl_price,
        "outcome": sim.outcome,
        "pnl_pct": sim.pnl_pct,
        "exit_minutes": sim.exit_minutes,
        "exit_price": sim.exit_price,
        "fragile_wick_tp": sim.fragile_wick_tp,
        "provisional": sim.provisional,
        "error": sim.error,
    }


def _aggregate_mode(results: list[TradeSimResult]) -> dict[str, Any]:
    valid = [r for r in results if r.error is None]
    tp = sum(1 for r in valid if r.outcome == "TP")
    sl = sum(1 for r in valid if r.outcome == "SL")
    liq = sum(1 for r in valid if r.outcome == "LIQUIDATION")
    open_n = sum(1 for r in valid if r.outcome == "OPEN")
    timeout = sum(1 for r in valid if r.outcome == "TIMEOUT")
    closed = tp + sl + liq + timeout
    wr = f"{round(tp / (tp + sl + liq) * 100)}%" if (tp + sl + liq) else "—"
    pnls = [
        r.pnl_pct
        for r in valid
        if r.pnl_pct is not None and r.outcome in ("TP", "SL", "LIQUIDATION", "TIMEOUT")
    ]
    cum = round(sum(pnls), 2) if pnls else None
    fragile = sum(1 for r in valid if r.outcome == "TP" and r.fragile_wick_tp)
    return {
        "total": len(valid),
        "tp": tp,
        "sl": sl + liq,
        "sl_raw": sl,
        "liquidations": liq,
        "open": open_n,
        "timeout": timeout,
        "closed": closed,
        "win_rate": wr,
        "cum_pnl_pct": cum,
        "fragile_wick_tp": fragile,
        "errors": sum(1 for r in results if r.error),
    }


def compare_signal(
    *,
    token: str,
    signal_time_utc: str,
    analysis_time_utc: str,
    klines: list[KlineRow],
    leverage: float,
    tp_pct: float,
    sl_pct: float,
    latency_seconds: int = DEFAULT_LATENCY_SECONDS,
    score: float | None = None,
    decision: str | None = None,
) -> SignalComparison:
    """Double simulation optimiste (analysis_time) vs réaliste (+latence)."""
    analysis_ms = ts_to_ms(analysis_time_utc)
    realistic_ms = analysis_ms + latency_seconds * 1000
    origin_ms = analysis_ms

    optimistic = simulate_long_from_entry(
        klines,
        entry_ms=analysis_ms,
        leverage=leverage,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        flash_at_utc=signal_time_utc,
        mode="optimistic",
        origin_ms=origin_ms,
    )
    realistic = simulate_long_from_entry(
        klines,
        entry_ms=realistic_ms,
        leverage=leverage,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        flash_at_utc=signal_time_utc,
        mode="realistic",
        origin_ms=origin_ms,
    )
    flipped = _is_win(optimistic.outcome) and _is_loss(realistic.outcome)
    return SignalComparison(
        token=token,
        signal_time_utc=signal_time_utc,
        analysis_time_utc=analysis_time_utc,
        score=score,
        decision=decision,
        optimistic=optimistic,
        realistic=realistic,
        flipped_win_to_loss=flipped,
    )


def compare_latency_modes(
    signals: list[dict[str, Any]],
    klines_by_key: dict[str, list[KlineRow]],
    *,
    leverage: float,
    tp_pct: float,
    sl_pct: float,
    latency_seconds: int = DEFAULT_LATENCY_SECONDS,
) -> dict[str, Any]:
    """Compare un lot de signaux ; ``klines_by_key`` indexé par ``token|signal_time_utc``."""
    comparisons: list[SignalComparison] = []
    skipped: list[dict[str, str]] = []

    for sig in signals:
        token = str(sig["token"])
        signal_ts = str(sig.get("signal_time_utc") or sig.get("flash_ts") or "")
        analysis_ts = str(sig.get("analysis_time_utc") or "")
        if not signal_ts or not analysis_ts:
            skipped.append({"token": token, "reason": "analysis_time ou signal_time manquant"})
            continue
        key = f"{token}|{signal_ts}"
        klines = klines_by_key.get(key)
        if not klines:
            skipped.append({"token": token, "reason": "klines indisponibles"})
            continue

        comparisons.append(
            compare_signal(
                token=token,
                signal_time_utc=signal_ts,
                analysis_time_utc=analysis_ts,
                klines=klines,
                leverage=leverage,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                latency_seconds=latency_seconds,
                score=sig.get("score"),
                decision=sig.get("decision"),
            )
        )

    opt_results = [c.optimistic for c in comparisons]
    real_results = [c.realistic for c in comparisons]
    flipped = [c for c in comparisons if c.flipped_win_to_loss]

    return {
        "meta": {
            "price_granularity": PRICE_GRANULARITY,
            "latency_seconds": latency_seconds,
            "leverage": leverage,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "data_note": (
                f"Prix via bougies {PRICE_GRANULARITY} (API bitunix). "
                f"Entrée optimiste à analysis_time ; réaliste à analysis_time + {latency_seconds}s. "
                "La latence intra-minute (< 60s) peut être invisible sur bougies 1m."
            ),
        },
        "optimistic": _aggregate_mode(opt_results),
        "realistic": _aggregate_mode(real_results),
        "latency_impact": {
            "flipped_win_to_loss": len(flipped),
            "flipped_trades": [
                {
                    "token": c.token,
                    "analysis_time_utc": c.analysis_time_utc,
                    "signal_time_utc": c.signal_time_utc,
                    "score": c.score,
                    "decision": c.decision,
                    "entry_optimistic": c.optimistic.entry_price,
                    "entry_realistic": c.realistic.entry_price,
                    "tp_price_optimistic": c.optimistic.tp_price,
                    "sl_price_optimistic": c.optimistic.sl_price,
                    "optimistic_outcome": c.optimistic.outcome,
                    "realistic_outcome": c.realistic.outcome,
                    "optimistic_pnl_pct": c.optimistic.pnl_pct,
                    "realistic_pnl_pct": c.realistic.pnl_pct,
                }
                for c in flipped
            ],
        },
        "trades": [
            {
                "token": c.token,
                "signal_time_utc": c.signal_time_utc,
                "analysis_time_utc": c.analysis_time_utc,
                "score": c.score,
                "decision": c.decision,
                "flipped_win_to_loss": c.flipped_win_to_loss,
                "optimistic": _sim_to_dict(c.optimistic),
                "realistic": _sim_to_dict(c.realistic),
            }
            for c in comparisons
        ],
        "skipped": skipped,
    }
