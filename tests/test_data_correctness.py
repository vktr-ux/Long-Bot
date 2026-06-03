from app.exchanges.bybit import normalize_price_24h_pct, recompute_price_24h_pct
from app.scanner.filters import candidate_passes
from app.scanner.metrics import price_change_from_candles, spike_ratio
from app.storage.models import Candle, Metrics, TickerSnapshot


def test_price24h_pcnt_normalization():
    assert normalize_price_24h_pct("0.05") == 5.0
    assert round(recompute_price_24h_pct(105, 100), 4) == 5.0


def test_closed_candle_metrics_exclude_live_candle():
    candles = [
        Candle(1, "bybit", "AAAUSDT", "1", 100, 101, 99, 100, 10, 1000, is_closed=True),
        Candle(2, "bybit", "AAAUSDT", "1", 100, 106, 100, 105, 10, 1050, is_closed=True),
        Candle(3, "bybit", "AAAUSDT", "1", 105, 150, 104, 150, 500, 75000, is_closed=False),
    ]
    assert price_change_from_candles(candles, 1) == 5.0


def test_volume_spike_uses_turnover_when_turnover_requested():
    candles = [Candle(i, "bybit", "AAAUSDT", "1", 1, 1, 1, 1, 100, 100, is_closed=True) for i in range(180)]
    candles += [Candle(i, "bybit", "AAAUSDT", "1", 1, 1, 1, 1, 100, 300, is_closed=True) for i in range(180, 195)]
    assert spike_ratio(candles, "volume", 15, 12) == 1.0
    assert spike_ratio(candles, "turnover", 15, 12) == 3.0


def test_liquidity_filter_uses_turnover_not_base_volume():
    ticker = TickerSnapshot(1, "bybit", "AAAUSDT", 1, turnover_24h=1_000, volume_24h=999_999_999)
    metrics = Metrics("bybit", "AAAUSDT", 1, price_change_15m=5)
    passed, reasons = candidate_passes(
        ticker,
        metrics,
        {
            "min_turnover_24h_usd": 10_000,
            "max_spread_pct": 0.3,
            "min_price_change_15m_pct_for_candidate": 1,
            "min_price_change_1h_pct_for_candidate": 2,
            "top_activity_rank_candidate": 30,
            "min_volume_spike_for_candidate": 2,
        },
    )
    assert not passed
    assert "24h turnover below threshold" in reasons

