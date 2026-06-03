from __future__ import annotations

from app.storage.models import TickerSnapshot


def rank_tickers(tickers: list[TickerSnapshot]) -> list[TickerSnapshot]:
    by_turnover = sorted(
        [t for t in tickers if t.turnover_24h is not None],
        key=lambda t: t.turnover_24h or 0,
        reverse=True,
    )
    for idx, ticker in enumerate(by_turnover, start=1):
        ticker.turnover_rank_24h = idx

    by_volume = sorted(
        [t for t in tickers if t.volume_24h is not None],
        key=lambda t: t.volume_24h or 0,
        reverse=True,
    )
    for idx, ticker in enumerate(by_volume, start=1):
        ticker.volume_rank_24h = idx
    return tickers


def activity_score(turnover_rank: int | None, volume_spike_15m: float | None, turnover_spike_15m: float | None) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if turnover_rank is not None:
        if turnover_rank <= 5:
            score += 10
            reasons.append(f"top Bybit turnover rank #{turnover_rank}")
        elif turnover_rank <= 10:
            score += 8
            reasons.append(f"high Bybit turnover rank #{turnover_rank}")
        elif turnover_rank <= 25:
            score += 5
            reasons.append(f"active Bybit turnover rank #{turnover_rank}")
    if volume_spike_15m is not None:
        if volume_spike_15m >= 3:
            score += 5
            reasons.append(f"15m volume spike x{volume_spike_15m:.1f}")
        elif volume_spike_15m >= 2:
            score += 3
            reasons.append(f"15m volume above baseline x{volume_spike_15m:.1f}")
    if turnover_spike_15m is not None:
        if turnover_spike_15m >= 3:
            score += 5
            reasons.append(f"15m turnover spike x{turnover_spike_15m:.1f}")
        elif turnover_spike_15m >= 2:
            score += 3
            reasons.append(f"15m turnover above baseline x{turnover_spike_15m:.1f}")
    return min(score, 20), reasons

