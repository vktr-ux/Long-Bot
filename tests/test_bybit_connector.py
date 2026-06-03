import pytest

from app.exchanges.bybit import BybitPublicConnector


class FakeBybit(BybitPublicConnector):
    def __init__(self):
        self.calls = []
        self.name = "bybit"
        self.category = "linear"

    async def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if path.endswith("instruments-info"):
            if not params.get("cursor"):
                return {"retCode": 0, "result": {"list": [{"symbol": "AAAUSDT", "baseCoin": "AAA", "quoteCoin": "USDT", "status": "Trading", "contractType": "LinearPerpetual"}], "nextPageCursor": "next"}}
            return {"retCode": 0, "result": {"list": [{"symbol": "BBBUSDT", "baseCoin": "BBB", "quoteCoin": "USDT", "status": "Trading", "contractType": "LinearPerpetual"}], "nextPageCursor": ""}}
        if path.endswith("tickers"):
            return {"retCode": 0, "result": {"list": [{"symbol": "AAAUSDT", "lastPrice": "1", "prevPrice24h": "0.95238095", "turnover24h": "100", "volume24h": "20", "price24hPcnt": "0.05", "bid1Price": "0.99", "ask1Price": "1.01"}]}}
        if path.endswith("kline"):
            return {"retCode": 0, "result": {"list": [["3", "1", "2", "1", "2", "10", "20"], ["1", "1", "2", "1", "1", "10", "10"]]}}
        if path.endswith("open-interest"):
            return {"retCode": 0, "result": {"list": [{"timestamp": "2", "openInterest": "110"}, {"timestamp": "1", "openInterest": "100"}]}}
        raise AssertionError(path)


@pytest.mark.asyncio
async def test_instruments_pagination_tickers_klines_and_oi():
    connector = FakeBybit()
    symbols = await connector.get_symbols()
    assert [s.symbol for s in symbols] == ["AAAUSDT", "BBBUSDT"]
    tickers = await connector.get_tickers()
    assert tickers[0].price_24h_pct == 5
    assert tickers[0].prev_price_24h == 0.95238095
    assert tickers[0].raw["price24hPcnt"] == "0.05"
    assert round(tickers[0].spread_pct, 2) == 2.0
    klines = await connector.get_klines("AAAUSDT", "1", 2)
    assert [c.timestamp_ms for c in klines] == [1, 3]
    oi = await connector.get_open_interest_history("AAAUSDT")
    assert oi == [(1, 100.0), (2, 110.0)]
