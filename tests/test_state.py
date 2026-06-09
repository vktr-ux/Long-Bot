from app.scanner.state import should_alert, signal_notification_snapshot
from app.storage.models import BreakoutContext, Metrics, SetupPlan, SignalCandidate


POLICY = {
    "min_level_to_send": "WATCH",
    "per_symbol_cooldown_minutes": {"WATCH": 90, "HOT": 45, "BREAKOUT_HOT": 30, "VERY_HOT": 15},
    "repeat_rules": {
        "allow_if_level_upgraded": True,
        "allow_if_score_increased_by": 10,
        "allow_if_breakout_state_changed": True,
        "allow_if_price_moved_pct_since_last_alert": 5.0,
        "allow_if_new_target_or_invalidation": True,
    },
    "global_rate_limit": {"max_alerts_per_hour": 8, "max_alerts_per_10_minutes": 3},
}


def signal(level="WATCH", state="FRESH_BREAKOUT", score=50, ts=1_000_000, price=100.0, target=110.0, invalidation=95.0):
    return SignalCandidate(
        ts,
        "bybit",
        "AAAUSDT",
        score,
        level,
        "BREAKOUT_WATCH",
        state,
        Metrics("bybit", "AAAUSDT", ts),
        BreakoutContext(state, "240", None, price),
        setup=SetupPlan(
            "bybit",
            "AAAUSDT",
            "BREAKOUT_CONTINUATION",
            price,
            "already_above_breakout",
            target_zone_low=target,
            invalidation_price=invalidation,
        ),
    )


def test_legacy_cooldown_allows_state_transition_and_score_jump():
    cfg = {"default_minutes": 30, "very_hot_minutes": 10, "score_increase_to_repeat": 10, "allow_immediate_state_transition_alert": True}
    assert should_alert(signal(level="BREAKOUT_HOT", score=80), None, cfg)[0]
    previous = {"state": "TESTING_RESISTANCE", "score": 70, "timestamp_ms": 999_000}
    assert should_alert(signal(level="BREAKOUT_HOT", score=80), previous, cfg)[0]
    previous = {"state": "FRESH_BREAKOUT", "score": 70, "timestamp_ms": 999_000}
    assert should_alert(signal(level="BREAKOUT_HOT", score=81), previous, cfg)[0]
    previous = {"state": "FRESH_BREAKOUT", "score": 80, "timestamp_ms": 999_000}
    assert not should_alert(signal(level="BREAKOUT_HOT", score=81), previous, cfg)[0]


def test_alert_suppressed_during_cooldown():
    previous = signal_notification_snapshot(signal(score=50, price=100.0), sent_at_ms=900_000)
    allowed, reason = should_alert(signal(score=51, ts=1_000_000, price=100.2), previous, POLICY)
    assert not allowed
    assert reason == "cooldown_active"


def test_alert_allowed_when_level_upgrades():
    previous = signal_notification_snapshot(signal(level="WATCH", score=50), sent_at_ms=990_000)
    allowed, reason = should_alert(signal(level="HOT", score=55, ts=1_000_000), previous, POLICY)
    assert allowed
    assert reason == "level_upgraded"


def test_alert_allowed_when_score_increases_by_configured_amount():
    previous = signal_notification_snapshot(signal(level="WATCH", score=50), sent_at_ms=990_000)
    allowed, reason = should_alert(signal(level="WATCH", score=60, ts=1_000_000, price=101), previous, POLICY)
    assert allowed
    assert reason == "score_increased"


def test_alert_suppressed_by_global_rate_limit():
    current = signal(ts=1_000_000)
    recent = [999_000, 998_000, 997_000]
    allowed, reason = should_alert(current, None, POLICY, recent_alert_timestamps=recent)
    assert not allowed
    assert reason == "global_rate_limit"


def test_duplicate_signal_suppressed():
    current = signal()
    previous = signal_notification_snapshot(current, sent_at_ms=990_000)
    allowed, reason = should_alert(current, previous, POLICY)
    assert not allowed
    assert reason == "duplicate_signal"
