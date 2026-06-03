from app.scanner.metrics import oi_change_pct, price_change_from_candles, sort_candles, spike_ratio
from app.storage.models import Candle


def candle(ts, close=1, volume=1, turnover=1):
    return Candle(ts, "bybit", "AAAUSDT", "1", close, close, close, close, volume, turnover)


def test_candle_sorting_price_change_and_spike():
    candles = [candle(3, 130), candle(1, 100), candle(2, 110)]
    assert [c.timestamp_ms for c in sort_candles(candles)] == [1, 2, 3]
    assert round(price_change_from_candles(candles, 2), 2) == 30.0
    spike_candles = [candle(i, volume=10, turnover=10) for i in range(180)]
    spike_candles += [candle(i, volume=30, turnover=30) for i in range(180, 195)]
    assert round(spike_ratio(spike_candles, "volume", 15, 12), 2) == 3.0


def test_oi_change():
    history = [(1, 100), (2, 103), (3, 110)]
    assert round(oi_change_pct(history, 2), 2) == 10.0

