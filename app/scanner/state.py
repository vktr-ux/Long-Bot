from __future__ import annotations

from app.storage.models import SignalCandidate

LEVEL_RANK = {"NO_SIGNAL": 0, "WATCH": 1, "HOT": 2, "BREAKOUT_HOT": 3, "VERY_HOT": 4}


def signal_notification_snapshot(signal: SignalCandidate, sent_at_ms: int | None = None) -> dict:
    setup = signal.setup
    metrics = signal.metrics
    price = None
    if signal.breakout:
        price = signal.breakout.current_price
    elif setup:
        price = setup.current_price
    return {
        "symbol": signal.symbol,
        "level": signal.level,
        "score": signal.score,
        "price": price,
        "breakout_state": signal.state,
        "target_reference": setup.target_zone_low if setup else None,
        "invalidation_reference": setup.invalidation_price if setup else None,
        "turnover_24h": metrics.turnover_24h,
        "turnover_rank_24h": metrics.turnover_rank_24h,
        "volume_spike_15m": metrics.volume_spike_15m,
        "turnover_spike_15m": metrics.turnover_spike_15m,
        "oi_change_15m_pct": metrics.oi_change_15m_pct,
        "oi_change_1h_pct": metrics.oi_change_1h_pct,
        "funding_rate": metrics.funding_rate,
        "price_change_5m": metrics.price_change_5m,
        "price_change_15m": metrics.price_change_15m,
        "price_change_1h": metrics.price_change_1h,
        "price_change_4h": metrics.price_change_4h,
        "sent_at_ms": sent_at_ms if sent_at_ms is not None else signal.timestamp_ms,
    }


def _notification_policy(config: dict) -> dict:
    if "per_symbol_cooldown_minutes" in config:
        return config
    return {
        "min_level_to_send": "WATCH",
        "per_symbol_cooldown_minutes": {
            "WATCH": config.get("default_minutes", 30),
            "HOT": config.get("default_minutes", 30),
            "BREAKOUT_HOT": config.get("default_minutes", 30),
            "VERY_HOT": config.get("very_hot_minutes", 10),
        },
        "repeat_rules": {
            "allow_if_level_upgraded": True,
            "allow_if_score_increased_by": config.get("score_increase_to_repeat", 10),
            "allow_if_breakout_state_changed": config.get("allow_immediate_state_transition_alert", True),
            "allow_if_price_moved_pct_since_last_alert": 0,
            "allow_if_new_target_or_invalidation": False,
        },
        "global_rate_limit": {},
    }


def _previous_value(previous_state: dict, *names: str):
    for name in names:
        if name in previous_state and previous_state[name] is not None:
            return previous_state[name]
    return None


def _price_move_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return abs(current - previous) / abs(previous) * 100


def _is_duplicate(current: dict, previous_state: dict) -> bool:
    previous_price = _previous_value(previous_state, "price", "last_price")
    current_price = current["price"]
    if current_price is None or previous_price is None:
        return False
    return all(
        [
            current["level"] == _previous_value(previous_state, "level", "last_level"),
            int(current["score"]) == int(_previous_value(previous_state, "score", "last_score") or -1),
            current["breakout_state"] == _previous_value(previous_state, "breakout_state", "state"),
            current["target_reference"] == _previous_value(previous_state, "target_reference"),
            current["invalidation_reference"] == _previous_value(previous_state, "invalidation_reference"),
            abs(float(current_price) - float(previous_price)) < 1e-12,
        ]
    )


def _global_rate_limited(signal_ts_ms: int, recent_alert_timestamps: list[int], policy: dict) -> bool:
    limits = policy.get("global_rate_limit") or {}
    max_hour = limits.get("max_alerts_per_hour")
    max_10m = limits.get("max_alerts_per_10_minutes")
    hour_count = sum(1 for ts in recent_alert_timestamps if 0 <= signal_ts_ms - ts <= 60 * 60_000)
    ten_min_count = sum(1 for ts in recent_alert_timestamps if 0 <= signal_ts_ms - ts <= 10 * 60_000)
    if max_hour is not None and hour_count >= int(max_hour):
        return True
    if max_10m is not None and ten_min_count >= int(max_10m):
        return True
    return False


def should_alert(
    signal: SignalCandidate,
    previous_state: dict | None,
    notification_config: dict,
    recent_alert_timestamps: list[int] | None = None,
) -> tuple[bool, str]:
    policy = _notification_policy(notification_config)
    if signal.level == "NO_SIGNAL":
        return False, "level_below_min"
    min_level = policy.get("min_level_to_send", "WATCH")
    if LEVEL_RANK.get(signal.level, 0) < LEVEL_RANK.get(min_level, 1):
        return False, "level_below_min"
    if _global_rate_limited(signal.timestamp_ms, recent_alert_timestamps or [], policy):
        return False, "global_rate_limit"
    current = signal_notification_snapshot(signal)
    if previous_state is None:
        return True, "first signal"
    if _is_duplicate(current, previous_state):
        return False, "duplicate_signal"

    repeat_rules = policy.get("repeat_rules") or {}
    previous_level = _previous_value(previous_state, "level", "last_level")
    previous_score = int(_previous_value(previous_state, "score", "last_score") or 0)
    previous_state_name = _previous_value(previous_state, "breakout_state", "state")
    previous_ts = int(_previous_value(previous_state, "sent_at_ms", "last_sent_ms", "timestamp_ms") or 0)
    elapsed_minutes = (signal.timestamp_ms - previous_ts) / 60_000 if previous_ts else 9999

    if (
        previous_level is not None
        and repeat_rules.get("allow_if_level_upgraded", True)
        and LEVEL_RANK.get(signal.level, 0) > LEVEL_RANK.get(str(previous_level), 0)
    ):
        return True, "level_upgraded"
    score_increase = int(repeat_rules.get("allow_if_score_increased_by", 10))
    if signal.score >= previous_score + score_increase:
        return True, "score_increased"
    if repeat_rules.get("allow_if_breakout_state_changed", True) and signal.state != previous_state_name:
        return True, "breakout_state_changed"
    price_move_threshold = repeat_rules.get("allow_if_price_moved_pct_since_last_alert")
    price_move = _price_move_pct(current["price"], _previous_value(previous_state, "price", "last_price"))
    if price_move_threshold is not None and price_move is not None and price_move >= float(price_move_threshold):
        return True, "price_moved"
    if repeat_rules.get("allow_if_new_target_or_invalidation", True):
        target_changed = current["target_reference"] != _previous_value(previous_state, "target_reference")
        invalidation_changed = current["invalidation_reference"] != _previous_value(previous_state, "invalidation_reference")
        if target_changed or invalidation_changed:
            return True, "new_target_or_invalidation"

    cooldowns = policy.get("per_symbol_cooldown_minutes") or {}
    cooldown_minutes = float(cooldowns.get(signal.level, cooldowns.get("WATCH", 90)))
    if elapsed_minutes < cooldown_minutes:
        return False, "cooldown_active"
    return False, "no_material_change"
