from __future__ import annotations


def derivatives_score(
    oi_change_15m_pct: float | None,
    oi_change_1h_pct: float | None,
    open_interest_value: float | None,
    price_change_15m: float | None,
    funding_rate: float | None,
    funding_config: dict,
) -> tuple[int, list[str], list[str], int]:
    score = 0
    penalty = 0
    reasons: list[str] = []
    warnings: list[str] = []
    if oi_change_15m_pct is not None and oi_change_15m_pct >= 3:
        score += 6
        reasons.append(f"OI 15m rising {oi_change_15m_pct:+.1f}%")
    if oi_change_1h_pct is not None and oi_change_1h_pct >= 8:
        score += 8
        reasons.append(f"OI 1h rising {oi_change_1h_pct:+.1f}%")
    if open_interest_value is not None and open_interest_value >= 5_000_000:
        score += 4
        reasons.append("meaningful open interest value")
    if (price_change_15m or 0) > 0 and (oi_change_15m_pct or 0) > 0:
        score += 4
        reasons.append("price and OI rising together")
    if funding_rate is not None:
        if 0 <= funding_rate <= funding_config["good_max"]:
            score += 4
            reasons.append(f"funding acceptable {funding_rate * 100:.3f}%")
        elif funding_rate < 0:
            score += 2
            reasons.append(f"negative funding {funding_rate * 100:.3f}%")
        if funding_rate > funding_config["caution_min"]:
            penalty += 8
            warnings.append(f"funding hot {funding_rate * 100:.3f}%")
        if funding_rate > funding_config["danger_min"]:
            penalty += 15
            warnings.append(f"funding danger {funding_rate * 100:.3f}%")
    else:
        warnings.append("funding data missing")
    return min(score, 20), reasons, warnings, penalty

