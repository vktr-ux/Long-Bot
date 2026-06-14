from app.storage.models import SymbolInfo
from app.trading.pnl import candle_exit_reason, closed_trade_from_fills, net_pnl, simulate_fill
from app.trading.risk import build_risk_plan


PAPER_CFG = {
    "max_position_margin_usdt": 2.0,
    "max_account_fraction_as_margin": 0.12,
    "max_leverage": 10,
    "default_leverage": 5,
    "max_loss_per_trade_usdt": 0.20,
    "fee_rate_taker": 0.0004,
    "entry_slippage_bps": 3,
    "exit_slippage_bps": 5,
}


def test_risk_sizing_respects_20_usdt_balance_step_and_min_notional():
    symbol = SymbolInfo("binance", "AAAUSDT", "AAA", "USDT", "TRADING", "PERPETUAL", step_size=0.1, min_notional=5)
    plan = build_risk_plan(
        balance_usdt=20,
        entry_price=1,
        score=70,
        spread_pct=0.10,
        atr_1m_pct=0.7,
        symbol_info=symbol,
        paper_cfg=PAPER_CFG,
    )
    assert plan.allowed
    assert plan.margin_usdt <= 2.0
    assert plan.notional_usdt >= 5 * 1.02
    assert round(plan.qty, 1) == plan.qty
    assert plan.leverage <= 10


def test_risk_sizing_skips_when_symbol_min_notional_is_impossible():
    symbol = SymbolInfo("binance", "BIGUSDT", "BIG", "USDT", "TRADING", "PERPETUAL", step_size=1, min_notional=50)
    plan = build_risk_plan(
        balance_usdt=20,
        entry_price=1,
        score=70,
        spread_pct=0.10,
        atr_1m_pct=0.7,
        symbol_info=symbol,
        paper_cfg=PAPER_CFG,
    )
    assert not plan.allowed
    assert "minNotional" in plan.reason


def test_fee_slippage_net_pnl_exact_for_long_and_short():
    long_entry = simulate_fill(side="BUY", qty=1, reference_price=100, role="ENTRY", fee_rate=0.001, slippage_bps=10)
    long_exit = simulate_fill(side="SELL", qty=1, reference_price=102, role="EXIT", fee_rate=0.001, slippage_bps=10)
    long_net = net_pnl("LONG", long_entry.price, long_exit.price, 1, long_entry.fee_usdt + long_exit.fee_usdt, long_entry.slippage_usdt + long_exit.slippage_usdt)
    assert round(long_entry.price, 2) == 100.10
    assert round(long_exit.price, 3) == 101.898
    assert round(long_net, 6) == round((101.898 - 100.1) - (0.1001 + 0.101898) - (0.1 + 0.102), 6)

    short_entry = simulate_fill(side="SELL", qty=1, reference_price=100, role="ENTRY", fee_rate=0.001, slippage_bps=10)
    short_exit = simulate_fill(side="BUY", qty=1, reference_price=98, role="EXIT", fee_rate=0.001, slippage_bps=10)
    short_net = net_pnl("SHORT", short_entry.price, short_exit.price, 1, short_entry.fee_usdt + short_exit.fee_usdt, short_entry.slippage_usdt + short_exit.slippage_usdt)
    assert round(short_entry.price, 2) == 99.90
    assert round(short_exit.price, 3) == 98.098
    assert short_net > 1.4


def test_closed_trade_history_is_computed_from_fills_and_stop_first_ambiguity():
    position = {
        "id": 7,
        "account_id": 1,
        "symbol": "AAAUSDT",
        "direction": "LONG",
        "leverage": 5,
        "exit_reason": "TRAILING_STOP",
        "mfe_usdt": 0.5,
        "mae_usdt": -0.1,
        "strategy_config_version": 3,
        "settings_hash": "abc123",
        "details_json": '{"strategy_version": "paper_scalper_v3"}',
    }
    fills = [
        {"side": "BUY", "qty": 1, "price": 100, "fee_usdt": 0.04, "slippage_usdt": 0.03, "filled_at_ms": 1000},
        {"side": "SELL", "qty": 1, "price": 101, "fee_usdt": 0.0404, "slippage_usdt": 0.05, "filled_at_ms": 4000},
    ]
    trade = closed_trade_from_fills(position, fills)
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == 101
    assert round(trade["net_pnl_usdt"], 4) == 0.8396
    assert trade["entry_fee_usdt"] == 0.04
    assert trade["exit_fee_usdt"] == 0.0404
    assert trade["strategy_config_version"] == 3
    assert trade["strategy_version"] == "paper_scalper_v3"
    assert trade["settings_hash"] == "abc123"
    assert trade["duration_seconds"] == 3
    assert candle_exit_reason("LONG", high=105, low=95, stop_price=98, tp_price=104) == "STOP_LOSS"
