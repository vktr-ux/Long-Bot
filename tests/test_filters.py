from app.scanner.filters import ticker_universe_filter
from app.storage.models import TickerSnapshot


def test_universe_filter_excludes_major_and_blacklist():
    cfg = {"quote_asset": "USDT", "exclude_major_symbols": True, "major_symbols": ["BTCUSDT"], "blacklist": ["BADUSDT"], "include_symbols": []}
    assert not ticker_universe_filter(TickerSnapshot(1, "bybit", "BTCUSDT", 1), cfg)
    assert not ticker_universe_filter(TickerSnapshot(1, "bybit", "BADUSDT", 1), cfg)
    assert ticker_universe_filter(TickerSnapshot(1, "bybit", "AAAUSDT", 1), cfg)

