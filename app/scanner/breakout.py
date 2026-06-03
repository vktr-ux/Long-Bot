from __future__ import annotations

from app.storage.models import BreakoutContext, Candle, ResistanceZone, SetupPlan
from app.utils.numbers import pct_change


def atr(candles: list[Candle], period: int = 14) -> float | None:
    ordered = sorted(candles, key=lambda c: c.timestamp_ms)
    if len(ordered) <= period:
        return None
    trs: list[float] = []
    for idx in range(1, len(ordered)):
        current = ordered[idx]
        prev_close = ordered[idx - 1].close
        trs.append(max(current.high - current.low, abs(current.high - prev_close), abs(current.low - prev_close)))
    return sum(trs[-period:]) / period


def candle_shape(candle: Candle) -> tuple[float | None, float | None, float | None]:
    rng = candle.high - candle.low
    if rng <= 0:
        return None, None, None
    body_pct = abs(candle.close - candle.open) / rng
    close_position = (candle.close - candle.low) / rng
    upper_wick_pct = (candle.high - max(candle.open, candle.close)) / rng
    return body_pct, close_position, upper_wick_pct


def find_swing_highs(candles: list[Candle], window: int = 2) -> list[Candle]:
    ordered = sorted(candles, key=lambda c: c.timestamp_ms)
    swings: list[Candle] = []
    for idx in range(window, len(ordered) - window):
        center = ordered[idx]
        left = ordered[idx - window : idx]
        right = ordered[idx + 1 : idx + 1 + window]
        if all(center.high > c.high for c in left + right):
            swings.append(center)
    return swings


def cluster_resistance_zones(candles: list[Candle], config: dict) -> list[ResistanceZone]:
    ordered = sorted(candles, key=lambda c: c.timestamp_ms)
    swings = find_swing_highs(ordered, config["swing_window"])
    atr_value = atr(ordered) or 0
    zones: list[list[Candle]] = []
    for swing in swings:
        placed = False
        for zone in zones:
            avg = sum(c.high for c in zone) / len(zone)
            tolerance = max(avg * config["zone_tolerance_pct"], atr_value * config["atr_tolerance_mult"])
            if abs(swing.high - avg) <= tolerance:
                zone.append(swing)
                placed = True
                break
        if not placed:
            zones.append([swing])
    resistance: list[ResistanceZone] = []
    for zone in zones:
        if len(zone) < config["min_touches"]:
            continue
        highs = [c.high for c in zone]
        mids = sum(highs) / len(highs)
        tolerance = max(mids * config["zone_tolerance_pct"], atr_value * config["atr_tolerance_mult"])
        resistance.append(
            ResistanceZone(
                timeframe=config["timeframe"],
                zone_low=max(min(highs), mids - tolerance),
                zone_high=min(max(highs), mids + tolerance),
                zone_mid=mids,
                touches=len(zone),
                first_touch_ts_ms=zone[0].timestamp_ms,
                last_touch_ts_ms=zone[-1].timestamp_ms,
                strength_score=min(10.0, len(zone) * 2.0),
            )
        )
    return sorted(resistance, key=lambda z: z.zone_high)


def detect_breakout(candles: list[Candle], current_price: float, config: dict) -> BreakoutContext:
    ordered = sorted(candles, key=lambda c: c.timestamp_ms)
    closed = [c for c in ordered if c.is_closed]
    if len(closed) < 20:
        return BreakoutContext("NO_BREAKOUT", config["timeframe"], None, current_price, breakout_buffer_pct=config["breakout_buffer_pct"])
    zones = cluster_resistance_zones(closed[:-1], config)
    eligible = [z for z in zones if z.zone_low <= current_price * (1 + config["max_distance_above_zone_pct"])]
    if not eligible:
        return BreakoutContext("NO_BREAKOUT", config["timeframe"], None, current_price, breakout_buffer_pct=config["breakout_buffer_pct"])
    zone = max(eligible, key=lambda z: z.zone_high)
    buffer_high = zone.zone_high * (1 + config["breakout_buffer_pct"])
    current_candle = ordered[-1]
    latest_closed = closed[-1]
    prev_closed = closed[-2]
    body, close_pos, upper_wick = candle_shape(current_candle)
    volume_baseline = closed[-15:-1]
    volume_confirmed = current_candle.volume > (sum(c.volume for c in volume_baseline) / max(len(volume_baseline), 1))
    distance_to_zone = pct_change(current_price, zone.zone_high)
    distance_above = pct_change(current_price, zone.zone_high) if current_price > zone.zone_high else None
    state = "NO_BREAKOUT"
    if current_price > zone.zone_high * (1 + config["max_distance_above_zone_pct"]):
        state = "OVEREXTENDED_AFTER_BREAKOUT"
    elif latest_closed.close > buffer_high and prev_closed.close > buffer_high:
        state = "CONFIRMED_BREAKOUT"
    elif latest_closed.close > buffer_high and prev_closed.close <= buffer_high:
        state = "FRESH_BREAKOUT"
    elif current_price > buffer_high and latest_closed.close <= buffer_high:
        state = "FRESH_BREAKOUT"
    elif zone.zone_low <= current_price <= zone.zone_high:
        state = "TESTING_RESISTANCE"
    elif current_price < zone.zone_high and current_price >= zone.zone_high * (1 - config["approach_distance_pct"]):
        state = "APPROACHING_RESISTANCE"
    if latest_closed.high > buffer_high and current_price < zone.zone_mid:
        state = "FAILED_BREAKOUT"
    return BreakoutContext(
        state=state,
        timeframe=config["timeframe"],
        resistance_zone=zone,
        current_price=current_price,
        distance_to_zone_pct=distance_to_zone,
        distance_above_zone_pct=distance_above,
        breakout_buffer_pct=config["breakout_buffer_pct"],
        latest_candle_body_pct=body,
        latest_candle_close_position=close_pos,
        latest_candle_upper_wick_pct=upper_wick,
        volume_confirmed=volume_confirmed,
    )


def chart_score(context: BreakoutContext) -> tuple[int, list[str], list[str], int]:
    score_by_state = {
        "APPROACHING_RESISTANCE": 4,
        "TESTING_RESISTANCE": 6,
        "FRESH_BREAKOUT": 12,
        "CONFIRMED_BREAKOUT": 15,
        "RETEST_HELD": 18,
    }
    score = score_by_state.get(context.state, 0)
    penalty = 0
    reasons: list[str] = []
    warnings: list[str] = []
    if score:
        reasons.append(f"4H chart state {context.state}")
    if context.resistance_zone and context.resistance_zone.touches >= 3:
        score += 5
        reasons.append(f"resistance zone has {context.resistance_zone.touches} touches")
    if context.volume_confirmed:
        score += 4
        reasons.append("breakout candle volume confirmed")
    if context.state == "FAILED_BREAKOUT":
        penalty += 8
        warnings.append("failed breakout risk")
    if context.state == "OVEREXTENDED_AFTER_BREAKOUT":
        penalty += 10
        warnings.append("price overextended above breakout zone")
    if context.latest_candle_upper_wick_pct is not None and context.latest_candle_upper_wick_pct > 0.45:
        penalty += 8
        warnings.append("large upper wick after impulse")
    return min(score, 25), reasons, warnings, penalty


def build_setup_plan(exchange: str, symbol: str, context: BreakoutContext, candles_240m: list[Candle], config: dict) -> SetupPlan:
    zone = context.resistance_zone
    atr_value = atr(candles_240m) or context.current_price * 0.03
    if not zone:
        return SetupPlan(exchange, symbol, "MOMENTUM_WATCH", context.current_price, "no_clean_level")
    if context.state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "OVEREXTENDED_AFTER_BREAKOUT"}:
        setup_type = "BREAKOUT_CONTINUATION"
        invalidation = min(zone.zone_low, zone.zone_high - atr_value * config["invalidation_atr_mult_breakout"])
        entry_context = "already_above_breakout"
    elif context.state in {"TESTING_RESISTANCE", "APPROACHING_RESISTANCE"}:
        setup_type = "MOMENTUM_WATCH"
        invalidation = None
        entry_context = "approaching_level"
    else:
        setup_type = "MOMENTUM_WATCH"
        invalidation = None
        entry_context = "no_clean_level"
    target_low = context.current_price + atr_value * config["target_atr_extension_mult"]
    target_high = target_low + atr_value * 0.5
    risk_pct = abs(context.current_price - invalidation) / context.current_price * 100 if invalidation else None
    room_pct = (target_low - context.current_price) / context.current_price * 100
    rr = (room_pct / risk_pct) if risk_pct and risk_pct > 0 else None
    distance_above = context.distance_above_zone_pct or 0
    if context.state in {"TESTING_RESISTANCE", "APPROACHING_RESISTANCE"}:
        chase = "LOW"
    elif distance_above <= 3:
        chase = "LOW"
    elif distance_above <= 8:
        chase = "MEDIUM"
    else:
        chase = "HIGH"
    return SetupPlan(
        exchange=exchange,
        symbol=symbol,
        setup_type=setup_type,
        current_price=context.current_price,
        entry_context=entry_context,
        breakout_zone_low=zone.zone_low,
        breakout_zone_high=zone.zone_high,
        suggested_watch_zone_low=zone.zone_low,
        suggested_watch_zone_high=zone.zone_high,
        invalidation_price=invalidation,
        invalidation_reason="below broken 4H resistance zone" if invalidation else "no clean invalidation level",
        distance_to_invalidation_pct=risk_pct,
        target_zone_low=target_low,
        target_zone_high=target_high,
        target_reason="ATR extension / nearest objective review zone",
        room_to_target_pct=room_pct,
        estimated_rr=rr,
        chase_risk=chase,
    )
