from app.config import DEFAULT_CONFIG
from app.scanner.scoring import classify_level, grade_and_label
from app.storage.models import BreakoutContext, Metrics, ResistanceZone, SetupPlan


def breakout_context():
    zone = ResistanceZone("240", 99, 100, 99.5, 3, 1, 10, 6)
    return BreakoutContext("FRESH_BREAKOUT", "240", zone, 103, distance_above_zone_pct=3)


def test_level_requires_breakout_and_confirmations():
    scores = {"activity": 12, "momentum": 12, "derivatives": 10, "chart": 12}
    level = classify_level(85, breakout_context(), scores, [], DEFAULT_CONFIG["scoring"])
    assert level == "BREAKOUT_HOT"
    no_breakout = BreakoutContext("NO_BREAKOUT", "240", None, 103)
    assert classify_level(85, no_breakout, scores, [], DEFAULT_CONFIG["scoring"]) == "HOT"


def test_grade_and_label_chase_warning():
    setup = SetupPlan("bybit", "AAAUSDT", "BREAKOUT_CONTINUATION", 110, "already_above_breakout", chase_risk="HIGH")
    grade, label = grade_and_label("BREAKOUT_HOT", {"activity": 12, "derivatives": 10}, breakout_context(), [], setup)
    assert grade == "A"
    assert label == "TOO_LATE_CHASE_WARNING"

