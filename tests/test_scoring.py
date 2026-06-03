from app.config import DEFAULT_CONFIG
from app.scanner.scoring import classify_level, grade_and_label, score_signal
from app.storage.models import BreakoutContext, Metrics, ResistanceZone, SetupPlan, TickerSnapshot


def breakout_context():
    zone = ResistanceZone("240", 99, 100, 99.5, 3, 1, 10, 6)
    return BreakoutContext("FRESH_BREAKOUT", "240", zone, 103, distance_above_zone_pct=3)


def ticker_snapshot(funding_rate=0.0001):
    return TickerSnapshot(
        timestamp_ms=1,
        exchange="bybit",
        symbol="AAAUSDT",
        last_price=103,
        funding_rate=funding_rate,
    )


def upgrade_metrics(funding_rate=0.0001):
    return Metrics(
        exchange="bybit",
        symbol="AAAUSDT",
        timestamp_ms=1,
        price_change_5m=2.1,
        price_change_15m=4.2,
        price_change_1h=4.0,
        price_change_4h=7.0,
        volume_spike_15m=3.0,
        turnover_spike_15m=3.0,
        oi_change_15m_pct=0.1,
        oi_change_1h_pct=0.0,
        funding_rate=funding_rate,
    )


def setup_plan(chase_risk="MEDIUM", rr=1.6):
    return SetupPlan(
        "bybit",
        "AAAUSDT",
        "BREAKOUT_CONTINUATION",
        103,
        "already_above_breakout",
        target_zone_low=112,
        room_to_target_pct=8,
        estimated_rr=rr,
        chase_risk=chase_risk,
    )


def score_upgrade_candidate(funding_rate=0.0001, chase_risk="MEDIUM", rr=1.6):
    return score_signal(
        ticker_snapshot(funding_rate=funding_rate),
        upgrade_metrics(funding_rate=funding_rate),
        breakout_context(),
        setup_plan(chase_risk=chase_risk, rr=rr),
        (12, ["4H chart state FRESH_BREAKOUT"], [], 0),
        ([], 0),
        DEFAULT_CONFIG,
    )


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


def test_breakout_upgrade_emits_watch_when_score_just_below_watch():
    score, level, sig_type, _scores, _risk, reasons, warnings, _grade, _label = score_upgrade_candidate()
    assert score < DEFAULT_CONFIG["scoring"]["levels"]["watch"]
    assert level == "WATCH"
    assert sig_type == "BREAKOUT_WATCH"
    assert "upgraded to WATCH by breakout rule" in reasons
    assert "score below regular WATCH threshold, upgraded by breakout rule" in warnings


def test_breakout_upgrade_blocks_high_chase_risk():
    _score, level, *_rest = score_upgrade_candidate(chase_risk="HIGH")
    assert level == "NO_SIGNAL"


def test_breakout_upgrade_blocks_hot_funding():
    _score, level, *_rest = score_upgrade_candidate(funding_rate=0.0007)
    assert level == "NO_SIGNAL"


def test_breakout_upgrade_blocks_weak_rr():
    _score, level, *_rest = score_upgrade_candidate(rr=1.1)
    assert level == "NO_SIGNAL"
