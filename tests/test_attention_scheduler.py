from copy import deepcopy

from app.config import DEFAULT_CONFIG
from app.scanner.attention import market_activity_score, select_attention_candidates
from app.storage.models import TickerSnapshot


def cfg(**performance_overrides):
    config = deepcopy(DEFAULT_CONFIG)
    config["performance"].update(
        {
            "attention_waiting_slots": 2,
            "attention_hot_slots": 3,
            "attention_recent_slots": 2,
            "attention_reversal_slots": 1,
            "attention_rotation_slots": 2,
            "attention_low_score_cooldown_minutes": 60,
            "attention_mid_score_cooldown_minutes": 30,
            "attention_near_score_cooldown_minutes": 10,
            "attention_hot_score_cooldown_minutes": 2,
            **performance_overrides,
        }
    )
    return config


def ticker(symbol: str, rank: int, *, change_1m: float = 0.0, change_5m: float = 0.0, volume_delta: float = 0.0) -> TickerSnapshot:
    return TickerSnapshot(
        timestamp_ms=1_000_000,
        exchange="binance",
        symbol=symbol,
        last_price=1.0,
        price_24h_pct=0.0,
        turnover_24h=100_000_000 - rank,
        volume_24h=1_000_000,
        turnover_rank_24h=rank,
        spread_pct=0.02,
        trade_count_24h=100_000,
        price_change_1m_pct=change_1m,
        price_change_5m_pct=change_5m,
        quote_volume_delta_5m=volume_delta,
        trade_count_delta_5m=100,
    )


def test_waiting_entry_gets_reserved_slot_even_if_not_top_hot():
    tickers = [ticker(f"S{i}USDT", i, change_1m=1.0 if i <= 8 else 0.0) for i in range(1, 31)]
    waiting = tickers[-1].symbol
    plans = [{"symbol": waiting, "status": "waiting_entry", "score": 82, "created_at_ms": 995_000, "classifier_label": "LONG_CONTINUATION"}]

    result = select_attention_candidates(tickers, 10, cfg(), recent_trade_plans=plans, now_ms=1_000_000)

    assert waiting in [item.symbol for item in result.selected]
    assert result.stats["bucket_counts"]["waiting"] == 1


def test_low_score_recent_checks_are_cooled_and_replaced_by_fresh_symbols():
    tickers = [ticker(f"S{i}USDT", i, change_1m=0.2) for i in range(1, 16)]
    state = {
        "symbols": {
            f"S{i}USDT": {"last_enriched_ms": 990_000, "last_score": 10}
            for i in range(1, 6)
        }
    }
    config = cfg(
        attention_waiting_slots=0,
        attention_hot_slots=5,
        attention_recent_slots=0,
        attention_reversal_slots=0,
        attention_rotation_slots=0,
    )

    result = select_attention_candidates(tickers, 5, config, state=state, now_ms=1_000_000)

    selected = {item.symbol for item in result.selected}
    assert selected.isdisjoint({f"S{i}USDT" for i in range(1, 6)})
    assert len(selected) == 5


def test_selection_is_capped_and_deduplicated_across_buckets():
    tickers = [ticker(f"S{i}USDT", i, change_1m=-1.0 if i <= 5 else 0.5, volume_delta=1_000_000) for i in range(1, 20)]
    plans = [
        {"symbol": "S1USDT", "status": "waiting_entry", "score": 90, "created_at_ms": 990_000},
        {"symbol": "S1USDT", "status": "rejected", "score": 95, "created_at_ms": 991_000},
        {"symbol": "S2USDT", "status": "rejected", "score": 80, "created_at_ms": 992_000},
    ]

    result = select_attention_candidates(tickers, 7, cfg(), recent_trade_plans=plans, now_ms=1_000_000)
    selected = [item.symbol for item in result.selected]

    assert len(selected) == 7
    assert len(selected) == len(set(selected))
    assert market_activity_score(tickers[0]) > 0


def test_loss_cooldown_symbol_is_removed_from_attention_slots():
    tickers = [ticker(f"S{i}USDT", i, change_1m=1.0, volume_delta=1_000_000) for i in range(1, 12)]
    plans = [{"symbol": "S1USDT", "status": "waiting_entry", "score": 95, "created_at_ms": 995_000}]
    trades = [{"symbol": "S1USDT", "net_pnl_usdt": -0.20, "exit_reason": "STOP_LOSS", "exit_time_ms": 990_000}]
    config = cfg()
    config["paper"].update({"stop_loss_symbol_cooldown_minutes": 90, "repeat_loss_symbol_count": 2})

    result = select_attention_candidates(tickers, 8, config, recent_trade_plans=plans, recent_trades=trades, now_ms=1_000_000)

    assert "S1USDT" not in {item.symbol for item in result.selected}
    assert result.stats["loss_blocked"] == 1
