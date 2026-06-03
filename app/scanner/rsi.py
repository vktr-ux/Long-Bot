from __future__ import annotations

from app.storage.models import Candle


def calculate_rsi(candles: list[Candle], period: int = 14) -> float | None:
    ordered = [c for c in sorted(candles, key=lambda c: c.timestamp_ms) if c.is_closed]
    if len(ordered) <= period:
        return None
    closes = [c.close for c in ordered]
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for idx in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_warnings(rsi_15m: float | None, rsi_1h: float | None, rsi_4h: float | None, config: dict) -> tuple[list[str], int]:
    warnings: list[str] = []
    penalty = 0
    if rsi_15m is not None and rsi_15m > config["warning_15m"]:
        warnings.append(f"RSI 15m elevated: {rsi_15m:.1f}")
    if rsi_1h is not None and rsi_1h > config["warning_1h"]:
        warnings.append(f"RSI 1h elevated: {rsi_1h:.1f}")
    if rsi_4h is not None and rsi_4h > config["warning_4h"]:
        warnings.append(f"RSI 4h elevated: {rsi_4h:.1f}")
    if rsi_15m is not None and rsi_15m > config["danger_15m"]:
        penalty += 4
    if rsi_1h is not None and rsi_1h > config["danger_1h"]:
        penalty += 6
    if rsi_4h is not None and rsi_4h > config["danger_4h"]:
        penalty += 8
    return warnings, penalty
