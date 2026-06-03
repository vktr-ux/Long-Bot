from app.scanner.state import should_alert
from app.storage.models import Metrics, SignalCandidate


def signal(state="FRESH_BREAKOUT", score=80, ts=1_000_000):
    return SignalCandidate(ts, "bybit", "AAAUSDT", score, "BREAKOUT_HOT", "BREAKOUT_HOT", state, Metrics("bybit", "AAAUSDT", ts), None)


def test_cooldown_allows_state_transition_and_score_jump():
    cfg = {"default_minutes": 30, "very_hot_minutes": 10, "score_increase_to_repeat": 10, "allow_immediate_state_transition_alert": True}
    assert should_alert(signal(), None, cfg)[0]
    previous = {"state": "TESTING_RESISTANCE", "score": 70, "timestamp_ms": 999_000}
    assert should_alert(signal(), previous, cfg)[0]
    previous = {"state": "FRESH_BREAKOUT", "score": 70, "timestamp_ms": 999_000}
    assert should_alert(signal(score=81), previous, cfg)[0]
    previous = {"state": "FRESH_BREAKOUT", "score": 80, "timestamp_ms": 999_000}
    assert not should_alert(signal(score=81), previous, cfg)[0]

