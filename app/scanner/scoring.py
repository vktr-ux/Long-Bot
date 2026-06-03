from __future__ import annotations

from app.scanner.activity import activity_score
from app.scanner.derivatives import derivatives_score
from app.storage.models import BreakoutContext, Metrics, SetupPlan, TickerSnapshot
from app.utils.numbers import clamp


def momentum_score(metrics: Metrics) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if (metrics.price_change_5m or 0) >= 2:
        score += 4
        reasons.append(f"5m momentum {metrics.price_change_5m:+.1f}%")
    if (metrics.price_change_15m or 0) >= 4:
        score += 7
        reasons.append(f"15m momentum {metrics.price_change_15m:+.1f}%")
    if (metrics.price_change_1h or 0) >= 8:
        score += 7
        reasons.append(f"1h momentum {metrics.price_change_1h:+.1f}%")
    if (metrics.price_change_4h or 0) >= 12:
        score += 4
        reasons.append(f"4h momentum {metrics.price_change_4h:+.1f}%")
    return min(score, 20), reasons


def setup_quality_adjustment(setup: SetupPlan | None, config: dict) -> tuple[int, list[str], list[str], int]:
    if not setup:
        return 0, [], ["setup quality unavailable"], 0
    score = 0
    penalty = 0
    reasons: list[str] = []
    warnings: list[str] = []
    if setup.estimated_rr is not None:
        if setup.estimated_rr >= 2:
            score += 8
            reasons.append(f"estimated R/R {setup.estimated_rr:.2f}")
        elif setup.estimated_rr >= 1.5:
            score += 5
            reasons.append(f"estimated R/R {setup.estimated_rr:.2f}")
        elif setup.estimated_rr < 1.2:
            penalty += 8
            warnings.append(f"weak estimated R/R {setup.estimated_rr:.2f}")
    if setup.room_to_target_pct is not None and setup.room_to_target_pct < config["min_room_to_target_pct"]:
        penalty += 12
        warnings.append("little room to next target/reference zone")
    if setup.chase_risk == "HIGH":
        penalty += 10
        warnings.append("HIGH chase risk - open chart / check setup, do not chase")
    elif setup.chase_risk == "MEDIUM":
        warnings.append("medium chase risk - wait for clean level/retest if needed")
    return score, reasons, warnings, penalty


def risk_penalties(metrics: Metrics, max_spread_pct: float, btc_config: dict) -> tuple[int, list[str]]:
    penalty = 0
    warnings: list[str] = []
    if metrics.price_change_24h is not None:
        if metrics.price_change_24h > 150:
            penalty += 25
            warnings.append("24h move above +150%")
        elif metrics.price_change_24h > 100:
            penalty += 15
            warnings.append("24h move above +100%")
        elif metrics.price_change_24h > 60:
            penalty += 8
            warnings.append("24h move above +60%")
    if metrics.spread_pct is not None and metrics.spread_pct > max_spread_pct:
        penalty += 12
        warnings.append(f"spread too wide {metrics.spread_pct:.3f}%")
    if metrics.btc_change_15m is not None and metrics.btc_change_15m <= btc_config["bad_15m_pct"]:
        penalty += 10
        warnings.append("BTC 15m background hostile")
    if metrics.btc_change_1h is not None and metrics.btc_change_1h <= btc_config["bad_1h_pct"]:
        penalty += 12
        warnings.append("BTC 1h background hostile")
    return penalty, warnings


def classify_level(score: int, breakout: BreakoutContext | None, scores: dict[str, int], warnings: list[str], config: dict) -> str:
    levels = config["levels"]
    if score < levels["watch"]:
        return "NO_SIGNAL"
    fatal = any("funding danger" in w or "failed breakout" in w or "hostile" in w for w in warnings)
    breakout_states = {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"}
    breakout_state = breakout and breakout.state in breakout_states
    confirmations = sum(
        [
            scores.get("activity", 0) >= 12,
            scores.get("momentum", 0) >= 12,
            scores.get("derivatives", 0) >= 10,
            scores.get("chart", 0) >= 12,
        ]
    )
    if score >= levels["very_hot"] and breakout_state and scores.get("activity", 0) >= 12 and scores.get("momentum", 0) >= 12 and not fatal:
        return "VERY_HOT"
    if score >= levels["breakout_hot"] and confirmations >= 3 and breakout_state:
        return "BREAKOUT_HOT"
    if score >= levels["hot"] and confirmations >= 2:
        return "HOT"
    return "WATCH"


def signal_type_for(level: str, breakout: BreakoutContext | None) -> str:
    state = breakout.state if breakout else "NO_BREAKOUT"
    if state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT"} and level in {"BREAKOUT_HOT", "VERY_HOT"}:
        return "BREAKOUT_HOT"
    if state in {"APPROACHING_RESISTANCE", "TESTING_RESISTANCE"}:
        return "BREAKOUT_WATCH"
    if state == "OVEREXTENDED_AFTER_BREAKOUT":
        return "OVEREXTENDED_WARNING"
    if state == "FAILED_BREAKOUT":
        return "FAILED_BREAKOUT_WARNING"
    return "MOMENTUM_WATCH"


def grade_and_label(level: str, scores: dict[str, int], breakout: BreakoutContext | None, warnings: list[str], setup: SetupPlan | None) -> tuple[str, str]:
    state = breakout.state if breakout else "NO_BREAKOUT"
    no_danger = not any("danger" in w.lower() or "wide" in w.lower() for w in warnings)
    if level in {"BREAKOUT_HOT", "VERY_HOT"} and state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"} and scores.get("activity", 0) >= 12 and scores.get("derivatives", 0) >= 8 and no_danger:
        grade = "A"
    elif level in {"HOT", "BREAKOUT_HOT", "VERY_HOT"} and scores.get("activity", 0) >= 8 and scores.get("momentum", 0) >= 8:
        grade = "B"
    else:
        grade = "C"
    if setup and setup.chase_risk == "HIGH":
        label = "TOO_LATE_CHASE_WARNING"
    elif grade == "A":
        label = "HIGH_QUALITY_REVIEW"
    elif state in {"APPROACHING_RESISTANCE", "TESTING_RESISTANCE"}:
        label = "WAIT_FOR_RETEST"
    elif level in {"HOT", "BREAKOUT_HOT", "VERY_HOT"}:
        label = "AGGRESSIVE_MOMENTUM_REVIEW"
    else:
        label = "NO_CLEAN_SETUP"
    return grade, label


def score_signal(
    ticker: TickerSnapshot,
    metrics: Metrics,
    breakout: BreakoutContext | None,
    setup: SetupPlan | None,
    chart_score_tuple: tuple[int, list[str], list[str], int],
    rsi_tuple: tuple[list[str], int],
    config: dict,
) -> tuple[int, str, str, dict[str, int], int, list[str], list[str], str, str]:
    reasons: list[str] = []
    warnings: list[str] = []
    activity, activity_reasons = activity_score(metrics.turnover_rank_24h, metrics.volume_spike_15m, metrics.turnover_spike_15m)
    momentum, momentum_reasons = momentum_score(metrics)
    derivatives, der_reasons, der_warnings, der_penalty = derivatives_score(
        metrics.oi_change_15m_pct,
        metrics.oi_change_1h_pct,
        ticker.open_interest_value,
        metrics.price_change_15m,
        metrics.funding_rate,
        config["funding"],
    )
    chart, chart_reasons, chart_warnings, chart_penalty = chart_score_tuple
    setup_bonus, setup_reasons, setup_warnings, setup_penalty = setup_quality_adjustment(setup, config["setup_quality"])
    base_risk, base_warnings = risk_penalties(metrics, config["filters"]["max_spread_pct"], config["btc_filter"])
    rsi_warnings_list, rsi_penalty = rsi_tuple
    scores = {"activity": activity, "momentum": momentum, "derivatives": derivatives, "chart": chart, "narrative": 0, "setup_quality": setup_bonus}
    reasons.extend(activity_reasons + momentum_reasons + der_reasons + chart_reasons + setup_reasons)
    warnings.extend(der_warnings + chart_warnings + setup_warnings + base_warnings + rsi_warnings_list)
    risk = der_penalty + chart_penalty + setup_penalty + base_risk + rsi_penalty
    total = int(clamp(activity + momentum + derivatives + chart + setup_bonus - risk, 0, 100))
    level = classify_level(total, breakout, scores, warnings, config["scoring"])
    sig_type = signal_type_for(level, breakout)
    grade, label = grade_and_label(level, scores, breakout, warnings, setup)
    return total, level, sig_type, scores, risk, reasons, warnings, grade, label

