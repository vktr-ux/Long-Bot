from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.exchanges.base import ExchangeConnector
from app.storage.models import Candle, SymbolInfo, TickerSnapshot
from app.utils.numbers import to_float, to_int
from app.utils.time import now_ms

LOGGER = logging.getLogger(__name__)


def interval_to_ms(interval: str) -> int | None:
    if interval == "D":
        return 24 * 60 * 60 * 1000
    try:
        return int(interval) * 60 * 1000
    except ValueError:
        return None


def normalize_price_24h_pct(price24h_pcnt: object) -> float | None:
    value = to_float(price24h_pcnt)
    return value * 100 if value is not None else None


def recompute_price_24h_pct(last_price: float | None, prev_price_24h: float | None) -> float | None:
    if last_price is None or prev_price_24h is None or prev_price_24h == 0:
        return None
    return (last_price - prev_price_24h) / prev_price_24h * 100


class BybitPublicConnector(ExchangeConnector):
    name = "bybit"

    def __init__(
        self,
        base_url: str = "https://api.bybit.com",
        category: str = "linear",
        timeout_seconds: float = 12,
        max_retries: int = 3,
        backoff_seconds: list[float] | None = None,
        max_concurrent_requests: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.category = category
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds or [0.5, 1.0, 2.0]
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        async with self._semaphore:
            for attempt in range(self.max_retries):
                try:
                    response = await self._client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("retCode") != 0:
                        raise RuntimeError(f"Bybit retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}")
                    return payload
                except Exception as exc:  # noqa: BLE001 - log endpoint failures without killing whole scanner.
                    last_exc = exc
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)])
            raise RuntimeError(f"Bybit request failed: {path} {params}: {last_exc}") from last_exc

    async def get_symbols(self) -> list[SymbolInfo]:
        symbols: list[SymbolInfo] = []
        cursor = ""
        while True:
            params = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/v5/market/instruments-info", params)
            result = payload.get("result", {})
            for row in result.get("list", []):
                symbols.append(
                    SymbolInfo(
                        exchange=self.name,
                        symbol=row.get("symbol", ""),
                        base_asset=row.get("baseCoin"),
                        quote_asset=row.get("quoteCoin") or "",
                        status=row.get("status", ""),
                        contract_type=row.get("contractType"),
                        launch_time_ms=to_int(row.get("launchTime")),
                    )
                )
            next_cursor = result.get("nextPageCursor") or ""
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return [s for s in symbols if s.symbol]

    async def get_tickers(self) -> list[TickerSnapshot]:
        payload = await self._get("/v5/market/tickers", {"category": self.category})
        ts = now_ms()
        tickers: list[TickerSnapshot] = []
        for row in payload.get("result", {}).get("list", []):
            last = to_float(row.get("lastPrice"))
            if last is None:
                continue
            prev_price = to_float(row.get("prevPrice24h"))
            bid = to_float(row.get("bid1Price"))
            ask = to_float(row.get("ask1Price"))
            mid = (bid + ask) / 2 if bid and ask else last
            spread = ((ask - bid) / mid * 100) if bid and ask and mid else None
            tickers.append(
                TickerSnapshot(
                    timestamp_ms=ts,
                    exchange=self.name,
                    symbol=row.get("symbol", ""),
                    last_price=last,
                    price_24h_pct=normalize_price_24h_pct(row.get("price24hPcnt")),
                    turnover_24h=to_float(row.get("turnover24h")),
                    volume_24h=to_float(row.get("volume24h")),
                    open_interest=to_float(row.get("openInterest")),
                    open_interest_value=to_float(row.get("openInterestValue")),
                    funding_rate=to_float(row.get("fundingRate")),
                    next_funding_time_ms=to_int(row.get("nextFundingTime")),
                    bid_price=bid,
                    ask_price=ask,
                    spread_pct=spread,
                    prev_price_24h=prev_price,
                    raw=dict(row),
                )
            )
        return tickers

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        payload = await self._get(
            "/v5/market/kline",
            {"category": self.category, "symbol": symbol, "interval": interval, "limit": limit},
        )
        return self._parse_klines(symbol, interval, payload)

    def _parse_klines(self, symbol: str, interval: str, payload: dict[str, Any]) -> list[Candle]:
        candles: list[Candle] = []
        current_ms = now_ms()
        interval_ms = interval_to_ms(interval)
        for row in payload.get("result", {}).get("list", []):
            if len(row) < 6:
                continue
            timestamp_ms = int(row[0])
            is_closed = True if interval_ms is None else timestamp_ms + interval_ms <= current_ms
            candles.append(
                Candle(
                    timestamp_ms=timestamp_ms,
                    exchange=self.name,
                    symbol=symbol,
                    interval=interval,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    turnover=float(row[6]) if len(row) > 6 else None,
                    is_closed=is_closed,
                )
            )
        candles.sort(key=lambda candle: candle.timestamp_ms)
        return candles

    async def get_klines_range(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> list[Candle]:
        all_candles: dict[int, Candle] = {}
        cursor_end = end_ms
        while cursor_end >= start_ms:
            payload = await self._get(
                "/v5/market/kline",
                {
                    "category": self.category,
                    "symbol": symbol,
                    "interval": interval,
                    "start": start_ms,
                    "end": cursor_end,
                    "limit": min(limit, 1000),
                },
            )
            candles = self._parse_klines(symbol, interval, payload)
            if not candles:
                break
            for candle in candles:
                if start_ms <= candle.timestamp_ms <= end_ms:
                    all_candles[candle.timestamp_ms] = candle
            earliest = min(c.timestamp_ms for c in candles)
            if earliest <= start_ms or len(candles) < min(limit, 1000):
                break
            cursor_end = earliest - 1
        return [all_candles[ts] for ts in sorted(all_candles)]

    async def get_open_interest_history(self, symbol: str, interval: str = "5min", limit: int = 50) -> list[tuple[int, float]]:
        payload = await self._get(
            "/v5/market/open-interest",
            {"category": self.category, "symbol": symbol, "intervalTime": interval, "limit": limit},
        )
        return self._parse_open_interest(payload)

    def _parse_open_interest(self, payload: dict[str, Any]) -> list[tuple[int, float]]:
        history: list[tuple[int, float]] = []
        for row in payload.get("result", {}).get("list", []):
            ts = to_int(row.get("timestamp"))
            oi = to_float(row.get("openInterest"))
            if ts is not None and oi is not None:
                history.append((ts, oi))
        history.sort(key=lambda item: item[0])
        return history

    async def get_open_interest_history_range(
        self,
        symbol: str,
        interval: str = "15min",
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 200,
    ) -> list[tuple[int, float]]:
        params: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "intervalTime": interval,
            "limit": min(limit, 200),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        payload = await self._get("/v5/market/open-interest", params)
        return self._parse_open_interest(payload)

    async def get_funding_history(self, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
        payload = await self._get(
            "/v5/market/funding/history",
            {"category": self.category, "symbol": symbol, "limit": limit},
        )
        return payload.get("result", {}).get("list", [])

    async def get_funding_history_range(
        self,
        symbol: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"category": self.category, "symbol": symbol, "limit": min(limit, 200)}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        payload = await self._get("/v5/market/funding/history", params)
        return payload.get("result", {}).get("list", [])
