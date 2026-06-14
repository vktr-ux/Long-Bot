import pytest

from app.exchanges.binance import BinanceFuturesPublicConnector, request_weight


class FakeBinance(BinanceFuturesPublicConnector):
    def __init__(self):
        self.calls = []
        self.name = "binance"

    async def _get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/fapi/v1/exchangeInfo":
            return {
                "symbols": [
                    {
                        "symbol": "AAAUSDT",
                        "baseAsset": "AAA",
                        "quoteAsset": "USDT",
                        "contractType": "PERPETUAL",
                        "status": "TRADING",
                        "pricePrecision": 5,
                        "quantityPrecision": 1,
                        "triggerProtect": "0.0500",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.00010"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "1000"},
                            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "2000"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        ],
                    },
                    {"symbol": "OLDUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "BREAK"},
                    {"symbol": "AAABUSD", "quoteAsset": "BUSD", "contractType": "PERPETUAL", "status": "TRADING"},
                    {"symbol": "AAAUSDT_240628", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
                ]
            }
        if path == "/fapi/v1/ticker/24hr":
            return [{"symbol": "AAAUSDT", "lastPrice": "10", "priceChangePercent": "2.5", "quoteVolume": "1000000", "volume": "10000", "count": "42"}]
        if path == "/fapi/v1/ticker/bookTicker":
            return [{"symbol": "AAAUSDT", "bidPrice": "9.99", "askPrice": "10.01"}]
        if path == "/fapi/v1/premiumIndex":
            return [{"symbol": "AAAUSDT", "markPrice": "10.02", "indexPrice": "10.00", "lastFundingRate": "0.0001", "nextFundingTime": "123"}]
        if path == "/fapi/v1/klines":
            return [
                [3, "10", "12", "9", "11", "100", 4, "1100", 1, "0", "0", "0"],
                [1, "9", "10", "8", "10", "90", 2, "900", 1, "0", "0", "0"],
            ]
        if path == "/futures/data/openInterestHist":
            return [
                {"timestamp": "2", "sumOpenInterest": "110"},
                {"timestamp": "1", "sumOpenInterest": "100"},
            ]
        if path == "/futures/data/takerlongshortRatio":
            return [{"timestamp": "1", "buyVol": "60", "sellVol": "40", "buySellRatio": "1.5"}]
        if path == "/fapi/v1/depth":
            return {"lastUpdateId": 1, "bids": [["9.99", "10"]], "asks": [["10.01", "10"]]}
        raise AssertionError(path)


@pytest.mark.asyncio
async def test_exchange_info_filters_only_trading_usdt_perpetuals():
    symbols = await FakeBinance().get_symbols()
    assert [s.symbol for s in symbols] == ["AAAUSDT"]
    symbol = symbols[0]
    assert symbol.status == "TRADING"
    assert symbol.tick_size == 0.00010
    assert symbol.step_size == 0.1
    assert symbol.market_max_qty == 2000
    assert symbol.min_notional == 5
    assert symbol.price_precision == 5
    assert symbol.quantity_precision == 1
    assert symbol.trigger_protect == 0.05


@pytest.mark.asyncio
async def test_ticker_book_and_premium_merge_spread():
    ticker = (await FakeBinance().get_tickers())[0]
    assert ticker.symbol == "AAAUSDT"
    assert ticker.price_24h_pct == 2.5
    assert ticker.turnover_24h == 1000000
    assert ticker.trade_count_24h == 42
    assert ticker.mark_price == 10.02
    assert ticker.funding_rate == 0.0001
    assert round(ticker.spread_pct, 3) == 0.2


@pytest.mark.asyncio
async def test_klines_oi_taker_and_depth_parsing():
    connector = FakeBinance()
    candles = await connector.get_klines("AAAUSDT", "1", 2)
    assert [c.timestamp_ms for c in candles] == [1, 3]
    assert all(c.is_closed for c in candles)
    assert candles[0].turnover == 900
    oi = await connector.get_open_interest_history("AAAUSDT")
    assert oi == [(1, 100.0), (2, 110.0)]
    taker = await connector.get_taker_buy_sell_volume("AAAUSDT")
    assert taker[0]["buySellRatio"] == 1.5
    depth = await connector.get_orderbook("AAAUSDT")
    assert depth["bids"] == [(9.99, 10.0)]


def test_binance_rest_request_weight_table():
    assert request_weight("/fapi/v1/ticker/24hr") == 40
    assert request_weight("/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"}) == 1
    assert request_weight("/fapi/v1/premiumIndex") == 10
    assert request_weight("/fapi/v1/ticker/bookTicker") == 5
    assert request_weight("/fapi/v1/klines", {"limit": 200}) == 2
    assert request_weight("/fapi/v1/klines", {"limit": 1000}) == 5
    assert request_weight("/fapi/v1/depth", {"limit": 20}) == 2
    assert request_weight("/futures/data/openInterestHist") == 1
