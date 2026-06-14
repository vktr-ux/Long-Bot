import pytest

from app.exchanges.binance_stream import BinanceWebSocketMarketCache
from app.storage.models import TickerSnapshot
from app.trading.runner import open_position_symbol_set, open_position_tickers


class FakeBase:
    name = "binance"

    async def close(self):
        pass


def test_binance_stream_cache_merges_ticker_book_and_mark_payloads():
    cache = BinanceWebSocketMarketCache(FakeBase())
    cache.apply_message(
        {
            "stream": "!ticker@arr",
            "data": [
                {"e": "24hrTicker", "E": 10, "s": "AAAUSDT", "c": "10", "P": "2.5", "q": "1000000", "v": "10000", "n": 42, "st": 1},
                {"e": "24hrTicker", "E": 10, "s": "BBBUSD_PERP", "c": "1", "q": "1", "st": 2},
            ],
        }
    )
    cache.apply_message({"stream": "!bookTicker", "data": {"e": "bookTicker", "E": 11, "s": "AAAUSDT", "b": "9.99", "a": "10.01", "st": 1}})
    cache.apply_message(
        {
            "stream": "!markPrice@arr",
            "data": [{"e": "markPriceUpdate", "E": 12, "s": "AAAUSDT", "p": "10.02", "i": "10.00", "r": "0.0001", "T": 123, "st": 1}],
        }
    )

    snapshot = cache.get_cached_ticker("AAAUSDT")
    assert snapshot is not None
    assert snapshot.symbol == "AAAUSDT"
    assert snapshot.last_price == 10
    assert snapshot.price_24h_pct == 2.5
    assert snapshot.turnover_24h == 1000000
    assert snapshot.trade_count_24h == 42
    assert snapshot.mark_price == 10.02
    assert snapshot.funding_rate == 0.0001
    assert round(snapshot.spread_pct, 3) == 0.2
    assert cache.get_cached_ticker("BBBUSD_PERP") is None


@pytest.mark.asyncio
async def test_open_position_tickers_prefers_websocket_cache():
    class Connector:
        name = "binance"

        def __init__(self):
            self.orderbook_calls = 0

        def get_cached_ticker(self, symbol):
            return TickerSnapshot(1, "binance", symbol, 100, bid_price=99.9, ask_price=100.1, spread_pct=0.2)

        async def get_orderbook(self, symbol, limit):
            self.orderbook_calls += 1
            return {"bids": [(99.8, 1)], "asks": [(100.2, 1)]}

    connector = Connector()
    tickers = await open_position_tickers(connector, [{"symbol": "AAAUSDT"}])
    assert tickers["AAAUSDT"].last_price == 100
    assert connector.orderbook_calls == 0


def test_open_position_symbol_set_normalizes_symbols():
    assert open_position_symbol_set([{"symbol": "aaausdt"}, {"symbol": "BBBUSDT"}, {"symbol": ""}]) == {"AAAUSDT", "BBBUSDT"}
