import json

from app.storage.models import BreakoutContext, CandidateDiagnostics, Candle, Metrics, SetupPlan, TickerSnapshot
from app.trading.classifier import DirectionDecision, classify_direction
from app.trading.position_manager import evaluate_position
from app.trading.strategy import build_trade_plan


def diagnostic(**metric_overrides):
    metrics = Metrics(
        exchange="binance",
        symbol="AAAUSDT",
        timestamp_ms=1,
        price_change_1m=0.2,
        price_change_5m=1.2,
        price_change_15m=2.0,
        volume_spike_15m=2.0,
        oi_change_15m_pct=1.0,
        oi_change_1h_pct=1.0,
        funding_rate=0.0001,
        spread_pct=0.05,
        taker_buy_sell_ratio=1.2,
        depth_usdt_20bps=1000,
    )
    for key, value in metric_overrides.items():
        setattr(metrics, key, value)
    ticker = TickerSnapshot(1, "binance", "AAAUSDT", 100, price_24h_pct=5, turnover_24h=20_000_000, bid_price=99.99, ask_price=100.01, spread_pct=0.02)
    breakout = BreakoutContext("FRESH_BREAKOUT", "240", None, 100)
    return CandidateDiagnostics(
        timestamp_ms=1,
        exchange="binance",
        symbol="AAAUSDT",
        ticker=ticker,
        metrics=metrics,
        score=72,
        level="HOT",
        signal_type="BREAKOUT_HOT",
        state="FRESH_BREAKOUT",
        scores={},
        risk_penalty=0,
        reasons=[],
        warnings=[],
        filter_stage_passed="scored",
        breakout=breakout,
    )


def test_classifier_long_and_conflict_paths():
    decision = classify_direction(diagnostic(), {"filters": {"max_spread_pct": 0.20}, "paper": {"max_position_margin_usdt": 2, "default_leverage": 5}})
    assert decision.label == "LONG_CONTINUATION"
    assert decision.direction == "LONG"
    assert decision.execution_score >= 68

    conflict = classify_direction(diagnostic(price_change_5m=-0.5, price_change_15m=0.1, taker_buy_sell_ratio=0.8), {"filters": {"max_spread_pct": 0.20}, "paper": {"max_position_margin_usdt": 2, "default_leverage": 5}})
    assert conflict.direction == "NO_TRADE"
    assert conflict.label == "NO_TRADE_CONFLICT"


def test_classifier_uses_execution_score_for_continuation_not_legacy_alert_score():
    item = diagnostic(
        price_change_1m=0.15,
        price_change_5m=1.4,
        price_change_15m=2.8,
        price_change_1h=4.5,
        volume_spike_15m=2.6,
        taker_buy_sell_ratio=1.24,
        oi_change_15m_pct=3.2,
    )
    item.score = 24
    decision = classify_direction(
        item,
        {
            "filters": {"max_spread_pct": 0.20, "min_volume_spike_for_candidate": 1.4, "min_price_change_15m_pct_for_candidate": 0.8},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {"long_min_score": 68, "short_min_score": 88},
        },
    )
    assert decision.direction == "LONG"
    assert decision.label == "LONG_CONTINUATION"


def test_classifier_can_execute_long_signal_as_inverse_short():
    item = diagnostic(
        price_change_1m=0.25,
        price_change_5m=1.4,
        price_change_15m=2.8,
        volume_spike_15m=2.4,
        taker_buy_sell_ratio=1.24,
        oi_change_15m_pct=2.0,
    )
    decision = classify_direction(
        item,
        {
            "filters": {"max_spread_pct": 0.20, "min_volume_spike_for_candidate": 1.4, "min_price_change_15m_pct_for_candidate": 0.8},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {
                "long_signal_execution": "inverse_short",
                "inverse_short_immediate_entry": True,
                "long_min_score": 64,
                "inverse_long_min_score": 64,
                "short_min_score": 88,
                "short_enabled": True,
                "long_enabled": True,
            },
        },
    )

    assert decision.direction == "SHORT"
    assert decision.label == "SHORT_INVERSE_LONG_SIGNAL"
    assert decision.execution_score >= 64
    assert any("inverse mode" in reason for reason in decision.reasons)


def test_inverse_short_waits_for_1m_pullback_when_immediate_entry_disabled():
    item = diagnostic(
        price_change_1m=0.10,
        price_change_5m=1.4,
        price_change_15m=2.8,
        volume_spike_15m=2.4,
        taker_buy_sell_ratio=1.24,
        oi_change_15m_pct=2.0,
    )
    cfg = {
        "filters": {"max_spread_pct": 0.20, "min_volume_spike_for_candidate": 1.4, "min_price_change_15m_pct_for_candidate": 0.8},
        "entry": {"pullback_confirm_pct": 0.15},
        "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
        "strategy": {
            "long_signal_execution": "inverse_short",
            "inverse_short_immediate_entry": False,
            "long_min_score": 64,
            "inverse_long_min_score": 64,
            "short_min_score": 88,
            "short_enabled": True,
            "long_enabled": True,
        },
    }

    waiting = classify_direction(item, cfg)
    assert waiting.direction == "NO_TRADE"
    assert waiting.label == "NO_TRADE_CONFLICT"
    assert any("waiting for 1m pullback" in warning for warning in waiting.warnings)

    item.metrics.price_change_1m = -0.18
    confirmed = classify_direction(item, cfg)
    assert confirmed.direction == "SHORT"
    assert confirmed.label == "SHORT_INVERSE_LONG_SIGNAL"
    assert any("1m pullback confirmation" in reason for reason in confirmed.reasons)


def test_classifier_allows_high_conviction_continuation_without_volume_spike():
    item = diagnostic(
        price_change_1m=0.2,
        price_change_5m=1.7,
        price_change_15m=4.6,
        price_change_1h=5.5,
        price_change_4h=13.0,
        volume_spike_15m=0.8,
        turnover_spike_15m=0.9,
        taker_buy_sell_ratio=1.17,
        oi_change_15m_pct=0.6,
        funding_rate=-0.0004,
    )
    decision = classify_direction(
        item,
        {
            "filters": {"max_spread_pct": 0.20, "min_volume_spike_for_candidate": 1.4, "min_price_change_15m_pct_for_candidate": 0.8},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {"long_min_score": 68, "long_high_conviction_score": 82, "short_min_score": 88},
        },
    )
    assert decision.direction == "LONG"
    assert any("high-conviction" in warning for warning in decision.warnings)


def test_classifier_allows_fresh_breakout_confirmation_before_price_extension():
    item = diagnostic(
        price_change_1m=0.0,
        price_change_5m=0.0,
        price_change_15m=0.05,
        volume_spike_15m=2.2,
        turnover_spike_15m=2.1,
        taker_buy_sell_ratio=1.22,
        oi_change_15m_pct=0.4,
        funding_rate=0.0001,
    )
    item.setup = SetupPlan(
        exchange="binance",
        symbol="AAAUSDT",
        setup_type="breakout",
        current_price=100,
        entry_context="fresh breakout",
        estimated_rr=4.5,
        chase_risk="LOW",
    )
    decision = classify_direction(
        item,
        {
            "filters": {"max_spread_pct": 0.20, "min_volume_spike_for_candidate": 1.4, "min_price_change_15m_pct_for_candidate": 0.8},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {"long_min_score": 68, "short_min_score": 88},
        },
    )
    assert decision.direction == "LONG"
    assert decision.label == "LONG_CONTINUATION"


def test_classifier_blocks_shorting_strong_long_momentum_and_requires_short_score():
    strong = diagnostic()
    strong.breakout.state = "OVEREXTENDED_AFTER_BREAKOUT"
    strong.metrics.price_change_1m = -0.1
    decision = classify_direction(
        strong,
        {
            "filters": {"max_spread_pct": 0.20},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {"avoid_shorting_strong_momentum": True, "long_min_score": 68, "short_min_score": 88, "short_strict_mode": True},
        },
    )
    assert decision.direction != "SHORT"

    weak_short = diagnostic(price_change_1m=-1, price_change_5m=-1, taker_buy_sell_ratio=0.8, funding_rate=0.001)
    weak_short.breakout.state = "FAILED_BREAKOUT"
    weak_short.score = 80
    decision = classify_direction(
        weak_short,
        {
            "filters": {"max_spread_pct": 0.20},
            "paper": {"max_position_margin_usdt": 2, "default_leverage": 5},
            "strategy": {"long_min_score": 68, "short_min_score": 88, "short_strict_mode": True},
        },
    )
    assert decision.direction == "NO_TRADE"


def test_classifier_bad_liquidity():
    item = diagnostic()
    item.ticker.spread_pct = 0.5
    decision = classify_direction(item, {"filters": {"max_spread_pct": 0.20, "max_spread_pct_absolute_skip": 0.35}, "paper": {"max_position_margin_usdt": 2, "default_leverage": 5}})
    assert decision.label == "NO_TRADE_BAD_LIQUIDITY"


def test_trade_plan_uses_ladder_trigger_for_risk_when_confirmation_required():
    item = diagnostic()
    item.candles["1"] = [
        Candle(timestamp_ms=1, exchange="binance", symbol="AAAUSDT", interval="1", open=100, high=101, low=99.5, close=100.5, volume=1000, turnover=100_000)
    ]
    decision = DirectionDecision("LONG_CONTINUATION", "LONG", ["test"], [], execution_score=82)
    plan = build_trade_plan(
        item,
        decision,
        symbol_info=None,
        balance_usdt=20,
        config={
            "entry": {"mode": "confirmation_ladder", "require_trigger_confirmation": True, "leg_weights": [1.0], "max_legs": 1},
            "paper": {"max_position_margin_usdt": 2, "max_account_fraction_as_margin": 0.2, "default_leverage": 5, "max_leverage": 10, "max_loss_per_trade_usdt": 0.2},
            "exit": {},
        },
    )
    assert plan is not None
    assert plan.entry_price > item.ticker.ask_price
    assert plan.risk.entry_price == plan.entry_price


def test_inverse_short_trade_plan_uses_current_bid_and_short_risk_shape():
    item = diagnostic()
    item.candles["1"] = [
        Candle(timestamp_ms=1, exchange="binance", symbol="AAAUSDT", interval="1", open=100, high=102, low=98, close=101, volume=1000, turnover=100_000)
    ]
    decision = DirectionDecision("SHORT_INVERSE_LONG_SIGNAL", "SHORT", ["inverse"], [], execution_score=82)
    plan = build_trade_plan(
        item,
        decision,
        symbol_info=None,
        balance_usdt=20,
        config={
            "strategy": {"inverse_short_immediate_entry": True},
            "entry": {"mode": "confirmation_ladder", "require_trigger_confirmation": True, "leg_weights": [1.0], "max_legs": 1},
            "paper": {"max_position_margin_usdt": 2, "max_account_fraction_as_margin": 0.2, "default_leverage": 5, "max_leverage": 10, "max_loss_per_trade_usdt": 0.2},
            "exit": {},
        },
    )

    assert plan is not None
    assert plan.direction == "SHORT"
    assert plan.classifier_label == "SHORT_INVERSE_LONG_SIGNAL"
    assert plan.entry_price == item.ticker.bid_price
    assert plan.initial_sl_price > plan.entry_price
    assert plan.tp1_price < plan.entry_price
    assert plan.be_plus_price < plan.entry_price


def base_position():
    return {
        "id": 1,
        "direction": "LONG",
        "entry_price": 100.0,
        "initial_sl_price": 99.0,
        "current_sl_price": 99.0,
        "tp1_price": 101.0,
        "trailing_distance_pct": 0.4,
        "high_watermark": 100.0,
        "low_watermark": 100.0,
        "opened_at_ms": 1_000,
        "details_json": json.dumps(
            {
                "be_plus_price": 100.5,
                "be_plus_move_pct": 0.35,
                "trailing_start_pct": 0.75,
                "trailing_distance_pct": 0.4,
                "tp1_done": False,
            }
        ),
    }


def test_breakeven_plus_waits_for_net_cost_threshold():
    cfg = {"paper": {"time_stop_seconds": 180, "max_hold_seconds": 600}, "exit": {"profit_guard_enabled": False}}
    action = evaluate_position(base_position(), 100.40, 2_000, cfg)
    assert action.action == "HOLD"

    action = evaluate_position(base_position(), 100.55, 2_000, cfg)
    assert action.action == "HOLD"

    after_tp1 = base_position()
    details = json.loads(after_tp1["details_json"])
    details["tp1_done"] = True
    after_tp1["details_json"] = json.dumps(details)
    action = evaluate_position(after_tp1, 100.55, 2_000, cfg)
    assert action.action == "UPDATE"
    assert action.updates["current_sl_price"] == 100.5


def test_tp1_partial_and_trailing_state():
    action = evaluate_position(base_position(), 101.1, 2_000, {"paper": {"time_stop_seconds": 180, "max_hold_seconds": 600}})
    assert action.action == "PARTIAL_CLOSE"
    assert action.reason == "TP1_PARTIAL"
    assert action.close_fraction == 0.5

    trailed = base_position()
    trailed["high_watermark"] = 102
    trailed["details_json"] = json.dumps({"be_plus_price": 100.5, "be_plus_move_pct": 0.35, "trailing_start_pct": 0.75, "tp1_done": True})
    action = evaluate_position(trailed, 102, 2_000, {"paper": {"time_stop_seconds": 180, "max_hold_seconds": 600}})
    assert action.action == "UPDATE"
    assert action.updates["trailing_active"] == 1
    assert action.updates["current_sl_price"] > 101


def test_tp1_can_close_full_position_for_scalp_mode():
    action = evaluate_position(
        base_position(),
        101.1,
        2_000,
        {"exit": {"tp1_close_fraction": 1.0}, "paper": {"time_stop_seconds": 180, "max_hold_seconds": 600}},
    )

    assert action.action == "CLOSE"
    assert action.reason == "SCALP_TAKE_PROFIT"
    assert action.close_fraction == 1.0


def test_profit_guard_closes_giveback_before_full_stop():
    position = base_position()
    details = json.loads(position["details_json"])
    details["profit_started_ms"] = 1_000
    position["details_json"] = json.dumps(details)
    position["notional_usdt"] = 100
    position["mfe_usdt"] = 0.5

    action = evaluate_position(
        position,
        100.05,
        35_000,
        {
            "exit": {
                "profit_guard_enabled": True,
                "profit_guard_trigger_pct": 0.30,
                "profit_guard_floor_pct": 0.08,
                "profit_guard_min_age_seconds": 20,
            },
            "paper": {"time_stop_seconds": 180, "max_hold_seconds": 600},
        },
    )

    assert action.action == "CLOSE"
    assert action.reason == "PROFIT_GIVEBACK_EXIT"


def test_small_profit_time_exit_cashes_slow_positive_scalp():
    position = base_position()
    details = json.loads(position["details_json"])
    details["profit_started_ms"] = 1_000
    position["details_json"] = json.dumps(details)
    position["notional_usdt"] = 100
    position["mfe_usdt"] = 0.35

    action = evaluate_position(
        position,
        100.30,
        35_000,
        {
            "exit": {
                "profit_guard_enabled": True,
                "profit_guard_trigger_pct": 0.30,
                "profit_guard_floor_pct": 0.08,
                "small_profit_time_exit_enabled": True,
                "small_profit_time_exit_seconds": 30,
                "small_profit_time_exit_min_pct": 0.25,
            },
            "paper": {"time_stop_seconds": 180, "max_hold_seconds": 600},
        },
    )

    assert action.action == "CLOSE"
    assert action.reason == "SMALL_PROFIT_TIME_EXIT"


def test_time_stop_holds_when_trade_has_progress():
    position = base_position()
    position["notional_usdt"] = 100
    position["mfe_usdt"] = 0.5
    action = evaluate_position(position, 100.30, 300_000, {"paper": {"time_stop_seconds": 90, "max_hold_seconds": 600}, "exit": {"profit_guard_enabled": False}})
    assert action.action == "HOLD"

    stale = base_position()
    stale["notional_usdt"] = 100
    stale["mfe_usdt"] = 0.01
    action = evaluate_position(stale, 99.95, 300_000, {"paper": {"time_stop_seconds": 90, "max_hold_seconds": 600}, "exit": {"profit_guard_enabled": False}})
    assert action.action == "CLOSE"
    assert action.reason == "TIME_STOP"
