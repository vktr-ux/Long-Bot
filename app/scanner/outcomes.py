from __future__ import annotations

from app.storage.models import Candle


def compute_outcome(candles: list[Candle], entry_price: float, target: float | None = None, invalidation: float | None = None) -> dict:
    ordered = sorted(candles, key=lambda c: c.timestamp_ms)
    if not ordered or entry_price <= 0:
        return {}
    max_price = max(c.high for c in ordered)
    min_price = min(c.low for c in ordered)
    close_price = ordered[-1].close
    details = {
        "max_price": max_price,
        "min_price": min_price,
        "close_price": close_price,
        "mfe_pct": (max_price - entry_price) / entry_price * 100,
        "mae_pct": (min_price - entry_price) / entry_price * 100,
        "close_return_pct": (close_price - entry_price) / entry_price * 100,
        "target_touched": bool(target is not None and max_price >= target),
        "invalidation_touched": bool(invalidation is not None and min_price <= invalidation),
    }
    plus3_ts = minus3_ts = plus5_ts = minus5_ts = None
    for candle in ordered:
        if plus3_ts is None and candle.high >= entry_price * 1.03:
            plus3_ts = candle.timestamp_ms
        if minus3_ts is None and candle.low <= entry_price * 0.97:
            minus3_ts = candle.timestamp_ms
        if plus5_ts is None and candle.high >= entry_price * 1.05:
            plus5_ts = candle.timestamp_ms
        if minus5_ts is None and candle.low <= entry_price * 0.95:
            minus5_ts = candle.timestamp_ms
    details["plus_3_before_minus_3"] = plus3_ts is not None and (minus3_ts is None or plus3_ts <= minus3_ts)
    details["plus_5_before_minus_5"] = plus5_ts is not None and (minus5_ts is None or plus5_ts <= minus5_ts)
    return details

