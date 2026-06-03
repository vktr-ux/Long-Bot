from __future__ import annotations

from app.storage.models import Candle, Metrics, TickerSnapshot
from app.utils.numbers import pct_change


def sort_candles(candles: list[Candle]) -> list[Candle]:
    return sorted(candles, key=lambda c: c.timestamp_ms)


def closed_candles(candles: list[Candle]) -> list[Candle]:
    return [c for c in sort_candles(candles) if c.is_closed]


def price_change_from_candles(candles: list[Candle], periods_back: int) -> float | None:
    ordered = closed_candles(candles)
    if len(ordered) <= periods_back:
        return None
    return pct_change(ordered[-1].close, ordered[-1 - periods_back].close)


def bucket_sum(candles: list[Candle], field: str, size: int) -> list[float]:
    ordered = closed_candles(candles)
    values: list[float] = []
    for idx in range(0, len(ordered), size):
        bucket = ordered[idx : idx + size]
        if len(bucket) == size:
            values.append(sum(float(getattr(c, field) or 0) for c in bucket))
    return values


def spike_ratio(candles: list[Candle], field: str, bucket_size: int, lookback_buckets: int) -> float | None:
    buckets = bucket_sum(candles, field, bucket_size)
    if len(buckets) < lookback_buckets + 1:
        return None
    current = buckets[-1]
    baseline_values = buckets[-1 - lookback_buckets : -1]
    baseline = sum(baseline_values) / len(baseline_values)
    if baseline <= 0:
        return None
    return current / baseline


def oi_change_pct(history: list[tuple[int, float]], periods_back: int) -> float | None:
    ordered = sorted(history, key=lambda item: item[0])
    if len(ordered) <= periods_back:
        return None
    return pct_change(ordered[-1][1], ordered[-1 - periods_back][1])


def build_metrics(
    ticker: TickerSnapshot,
    candles_1m: list[Candle],
    candles_5m: list[Candle],
    candles_15m: list[Candle],
    candles_60m: list[Candle],
    candles_240m: list[Candle],
    oi_history_5m: list[tuple[int, float]],
    btc_metrics: tuple[float | None, float | None, float | None] = (None, None, None),
    lookback_buckets: int = 12,
) -> Metrics:
    return Metrics(
        exchange=ticker.exchange,
        symbol=ticker.symbol,
        timestamp_ms=ticker.timestamp_ms,
        price_change_1m=price_change_from_candles(candles_1m, 1),
        price_change_5m=price_change_from_candles(candles_5m, 1),
        price_change_15m=price_change_from_candles(candles_15m, 1),
        price_change_1h=price_change_from_candles(candles_15m, 4),
        price_change_4h=price_change_from_candles(candles_60m, 4),
        price_change_24h=ticker.price_24h_pct,
        volume_spike_15m=spike_ratio(candles_1m, "volume", 15, lookback_buckets),
        turnover_spike_15m=spike_ratio(candles_1m, "turnover", 15, lookback_buckets),
        volume_spike_1h=spike_ratio(candles_15m, "volume", 4, lookback_buckets),
        turnover_spike_1h=spike_ratio(candles_15m, "turnover", 4, lookback_buckets),
        oi_change_5m_pct=oi_change_pct(oi_history_5m, 1),
        oi_change_15m_pct=oi_change_pct(oi_history_5m, 3),
        oi_change_1h_pct=oi_change_pct(oi_history_5m, 12),
        funding_rate=ticker.funding_rate,
        turnover_24h=ticker.turnover_24h,
        turnover_rank_24h=ticker.turnover_rank_24h,
        volume_rank_24h=ticker.volume_rank_24h,
        spread_pct=ticker.spread_pct,
        btc_change_15m=btc_metrics[0],
        btc_change_1h=btc_metrics[1],
        btc_change_4h=btc_metrics[2],
    )
