from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.exchanges.base import ExchangeConnector
from app.storage.models import Candle, SymbolInfo, TickerSnapshot
from app.utils.numbers import to_float, to_int
from app.utils.time import now_ms

LOGGER = logging.getLogger(__name__)

INTERVAL_MAP = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "120": "2h",
    "240": "4h",
    "360": "6h",
    "720": "12h",
    "D": "1d",
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1d",
    "5min": "5m",
    "15min": "15m",
}


def binance_interval(interval: str) -> str:
    return INTERVAL_MAP.get(interval, interval)


def _filter_by_type(filters: list[dict[str, Any]], filter_type: str) -> dict[str, Any]:
    return next((item for item in filters if item.get("filterType") == filter_type), {})


def _min_notional(filters: list[dict[str, Any]]) -> float | None:
    for filter_type in ("MIN_NOTIONAL", "NOTIONAL"):
        item = _filter_by_type(filters, filter_type)
        value = item.get("notional") if "notional" in item else item.get("minNotional")
        parsed = to_float(value)
        if parsed is not None:
            return parsed
    return None


def _kline_weight(limit: int | None) -> int:
    if limit is None or limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


def request_weight(path: str, params: dict[str, Any] | None = None) -> int:
    params = params or {}
    if path == "/fapi/v1/ticker/24hr":
        return 1 if params.get("symbol") else 40
    if path == "/fapi/v1/ticker/bookTicker":
        return 2 if params.get("symbol") else 5
    if path == "/fapi/v1/premiumIndex":
        return 1 if params.get("symbol") else 10
    if path == "/fapi/v1/klines":
        return _kline_weight(to_int(params.get("limit"), 500))
    if path == "/fapi/v1/depth":
        limit = to_int(params.get("limit"), 100) or 100
        if limit <= 50:
            return 2
        if limit <= 100:
            return 5
        if limit <= 500:
            return 10
        return 20
    if path.startswith("/futures/data/"):
        return 1
    return 1


class AsyncWeightLimiter:
    def __init__(self, max_weight_per_minute: int = 900, min_interval_seconds: float = 0.04):
        self.max_weight_per_minute = max_weight_per_minute
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._window_started = time.monotonic()
        self._used_weight = 0
        self._last_request_at = 0.0

    async def acquire(self, weight: int) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._window_started
            if elapsed >= 60:
                self._window_started = now
                self._used_weight = 0
                elapsed = 0
            if self._used_weight + weight > self.max_weight_per_minute:
                sleep_for = max(0.1, 60 - elapsed)
                LOGGER.info("Binance REST weight budget reached; sleeping %.1fs", sleep_for)
                await asyncio.sleep(sleep_for)
                self._window_started = time.monotonic()
                self._used_weight = 0
            spacing = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
            if spacing > 0:
                await asyncio.sleep(spacing)
            self._used_weight += weight
            self._last_request_at = time.monotonic()


class BinanceFuturesPublicConnector(ExchangeConnector):
    name = "binance"

    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 12,
        max_retries: int = 3,
        backoff_seconds: list[float] | None = None,
        max_concurrent_requests: int = 8,
        max_weight_per_minute: int = 900,
        min_request_interval_seconds: float = 0.04,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds or [0.5, 1.0, 2.0]
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._limiter = AsyncWeightLimiter(max_weight_per_minute, min_request_interval_seconds)
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        weight = request_weight(path, params)
        async with self._semaphore:
            for attempt in range(self.max_retries):
                try:
                    await self._limiter.acquire(weight)
                    response = await self._client.get(url, params=params or {})
                    if response.status_code in {418, 429}:
                        retry_after = to_float(response.headers.get("Retry-After"), 10.0) or 10.0
                        sleep_for = max(retry_after, self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)])
                        LOGGER.warning("Binance rate limit status=%s path=%s sleeping %.1fs", response.status_code, path, sleep_for)
                        await asyncio.sleep(sleep_for)
                        response.raise_for_status()
                    response.raise_for_status()
                    return response.json()
                except Exception as exc:  # noqa: BLE001 - keep scanning when one public endpoint is flaky.
                    last_exc = exc
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)])
            raise RuntimeError(f"Binance public request failed: {path}: {last_exc}") from last_exc

    async def get_symbols(self) -> list[SymbolInfo]:
        payload = await self._get("/fapi/v1/exchangeInfo")
        symbols: list[SymbolInfo] = []
        for row in payload.get("symbols", []):
            if row.get("quoteAsset") != "USDT":
                continue
            if row.get("contractType") != "PERPETUAL":
                continue
            if row.get("status") != "TRADING":
                continue
            filters = row.get("filters") or []
            price_filter = _filter_by_type(filters, "PRICE_FILTER")
            lot_filter = _filter_by_type(filters, "LOT_SIZE")
            market_lot_filter = _filter_by_type(filters, "MARKET_LOT_SIZE")
            symbols.append(
                SymbolInfo(
                    exchange=self.name,
                    symbol=row.get("symbol", ""),
                    base_asset=row.get("baseAsset"),
                    quote_asset=row.get("quoteAsset") or "",
                    status=row.get("status", ""),
                    contract_type=row.get("contractType"),
                    tick_size=to_float(price_filter.get("tickSize")),
                    step_size=to_float(lot_filter.get("stepSize")),
                    min_qty=to_float(lot_filter.get("minQty")),
                    max_qty=to_float(lot_filter.get("maxQty")),
                    market_step_size=to_float(market_lot_filter.get("stepSize")),
                    market_min_qty=to_float(market_lot_filter.get("minQty")),
                    market_max_qty=to_float(market_lot_filter.get("maxQty")),
                    min_notional=_min_notional(filters),
                    price_precision=to_int(row.get("pricePrecision")),
                    quantity_precision=to_int(row.get("quantityPrecision")),
                    trigger_protect=to_float(row.get("triggerProtect")),
                )
            )
        return [symbol for symbol in symbols if symbol.symbol]

    async def get_tickers(self) -> list[TickerSnapshot]:
        ticker_rows, book_rows, premium_rows = await asyncio.gather(
            self._get("/fapi/v1/ticker/24hr"),
            self._get("/fapi/v1/ticker/bookTicker"),
            self._get("/fapi/v1/premiumIndex"),
        )
        book_by_symbol = {row.get("symbol"): row for row in book_rows if row.get("symbol")}
        premium_by_symbol = {row.get("symbol"): row for row in premium_rows if row.get("symbol")}
        ts = now_ms()
        snapshots: list[TickerSnapshot] = []
        for row in ticker_rows:
            symbol = row.get("symbol", "")
            last = to_float(row.get("lastPrice"))
            if not symbol or last is None:
                continue
            book = book_by_symbol.get(symbol, {})
            premium = premium_by_symbol.get(symbol, {})
            bid = to_float(book.get("bidPrice"))
            ask = to_float(book.get("askPrice"))
            mid = (bid + ask) / 2 if bid and ask else last
            spread = ((ask - bid) / mid * 100) if bid and ask and mid else None
            snapshots.append(
                TickerSnapshot(
                    timestamp_ms=ts,
                    exchange=self.name,
                    symbol=symbol,
                    last_price=last,
                    price_24h_pct=to_float(row.get("priceChangePercent")),
                    turnover_24h=to_float(row.get("quoteVolume")),
                    volume_24h=to_float(row.get("volume")),
                    funding_rate=to_float(premium.get("lastFundingRate")),
                    next_funding_time_ms=to_int(premium.get("nextFundingTime")),
                    bid_price=bid,
                    ask_price=ask,
                    spread_pct=spread,
                    mark_price=to_float(premium.get("markPrice")),
                    index_price=to_float(premium.get("indexPrice")),
                    trade_count_24h=to_int(row.get("count")),
                    raw={"ticker": dict(row), "bookTicker": dict(book), "premiumIndex": dict(premium)},
                )
            )
        return snapshots

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        payload = await self._get(
            "/fapi/v1/klines",
            {"symbol": symbol.upper(), "interval": binance_interval(interval), "limit": limit},
        )
        candles: list[Candle] = []
        current_ms = now_ms()
        for row in payload:
            if len(row) < 11:
                continue
            open_time = int(row[0])
            close_time = int(row[6])
            candles.append(
                Candle(
                    timestamp_ms=open_time,
                    exchange=self.name,
                    symbol=symbol.upper(),
                    interval=interval,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    turnover=float(row[7]) if row[7] is not None else None,
                    is_closed=close_time <= current_ms,
                )
            )
        candles.sort(key=lambda candle: candle.timestamp_ms)
        return candles

    async def get_open_interest_history(self, symbol: str, interval: str = "5m", limit: int = 50) -> list[tuple[int, float]]:
        payload = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol.upper(), "period": binance_interval(interval), "limit": limit},
        )
        history: list[tuple[int, float]] = []
        for row in payload:
            ts = to_int(row.get("timestamp"))
            oi = to_float(row.get("sumOpenInterest"))
            if ts is not None and oi is not None:
                history.append((ts, oi))
        history.sort(key=lambda item: item[0])
        return history

    async def get_taker_buy_sell_volume(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict[str, float | int]]:
        payload = await self._get(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol.upper(), "period": binance_interval(period), "limit": limit},
        )
        rows: list[dict[str, float | int]] = []
        for row in payload:
            ts = to_int(row.get("timestamp"))
            buy = to_float(row.get("buyVol"))
            sell = to_float(row.get("sellVol"))
            ratio = to_float(row.get("buySellRatio"))
            if ts is None:
                continue
            rows.append({"timestamp": ts, "buyVol": buy or 0.0, "sellVol": sell or 0.0, "buySellRatio": ratio or 0.0})
        rows.sort(key=lambda item: int(item["timestamp"]))
        return rows

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        payload = await self._get("/fapi/v1/depth", {"symbol": symbol.upper(), "limit": limit})
        bids = [(float(price), float(qty)) for price, qty in payload.get("bids", [])]
        asks = [(float(price), float(qty)) for price, qty in payload.get("asks", [])]
        return {
            "last_update_id": payload.get("lastUpdateId"),
            "bids": bids,
            "asks": asks,
        }


# Backwards-compatible import name for older code/tests that only checked a placeholder.
BinanceConnector = BinanceFuturesPublicConnector
