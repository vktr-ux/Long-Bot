from app.scanner.derivatives import derivatives_score


def test_derivatives_score_and_funding_penalty():
    score, reasons, warnings, penalty = derivatives_score(
        oi_change_15m_pct=4,
        oi_change_1h_pct=9,
        open_interest_value=6_000_000,
        price_change_15m=5,
        funding_rate=0.0012,
        funding_config={"good_max": 0.0003, "caution_min": 0.0005, "danger_min": 0.0010},
    )
    assert score == 20
    assert penalty == 23
    assert any("funding danger" in warning for warning in warnings)
    assert any("OI 1h" in reason for reason in reasons)

