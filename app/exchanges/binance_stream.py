from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

import websockets

from app.exchanges.base import ExchangeConnector
from app.exchanges.binance import BinanceFuturesPublicConnector
from app.storage.models import Candle, SymbolInfo, TickerSnapshot
from app.utils.numbers import to_float, to_int
from app.utils.time import now_ms

LOGGER = logging.getLogger(__name__)

DEFAULT_MARKET_STREAM_URL = "wss://fstream.binance.com/market/stream?streams=!ticker@arr/!markPrice@arr"
DEFAULT_PUBLIC_STREAM_URL = "wss://fstream.binance.com/public/ws/!bookTicker"


def _is_um_row(row: dict[str, Any]) -> bool:
    symbol_type = row.get("st")
    return symbol_type in (None, 1, "1")


class BinanceWebSocketMarketCache(ExchangeConnector):
    """Binance connector wrapper that serves broad market state from WebSocket cache.

    REST remains the source for symbols, candles and low-frequency enrichment endpoints.
    """

    name = "binance"

    def __init__(
        self,
        base: BinanceFuturesPublicConnector,
        market_stream_url: str = DEFAULT_MARKET_STREAM_URL,
        public_stream_url: str = DEFAULT_PUBLIC_STREAM_URL,
        reconnect_delay_seconds: float = 5.0,
        fallback_to_rest_after_seconds: float = 45.0,
    ):
        self.base = base
        self.market_stream_url = market_stream_url
        self.public_stream_url = public_stream_url
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.fallback_to_rest_after_seconds = fallback_to_rest_after_seconds
        self._ticker_rows: dict[str, dict[str, Any]] = {}
        self._book_rows: dict[str, dict[str, Any]] = {}
        self._premium_rows: dict[str, dict[str, Any]] = {}
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._last_message_ms = 0
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self._seed_from_rest()
        self._tasks = [
            asyncio.create_task(self._stream_forever(self.market_stream_url, "market")),
            asyncio.create_task(self._stream_forever(self.public_stream_url, "public")),
        ]

    async def close(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.base.close()

    async def _seed_from_rest(self) -> None:
        try:
            for snapshot in await self.base.get_tickers():
                symbol = snapshot.symbol.upper()
                self._ticker_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "c": snapshot.last_price,
                    "P": snapshot.price_24h_pct,
                    "q": snapshot.turnover_24h,
                    "v": snapshot.volume_24h,
                    "n": snapshot.trade_count_24h,
                }
                self._book_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "b": snapshot.bid_price,
                    "a": snapshot.ask_price,
                }
                self._premium_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "p": snapshot.mark_price,
                    "i": snapshot.index_price,
                    "r": snapshot.funding_rate,
                    "T": snapshot.next_funding_time_ms,
                }
            self._last_message_ms = now_ms()
            LOGGER.info("seeded Binance WebSocket cache from REST symbols=%s", len(self._ticker_rows))
        except Exception:  # noqa: BLE001 - cache can still warm up from WebSocket.
            LOGGER.exception("failed to seed Binance WebSocket cache from REST")

    async def _stream_forever(self, url: str, label: str) -> None:
        while not self._stop_event.is_set():
            try:
                LOGGER.info("connecting Binance %s WebSocket: %s", label, url)
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=2048,
                    max_size=4 * 1024 * 1024,
                ) as websocket:
                    LOGGER.info("connected Binance %s WebSocket", label)
                    async for raw_message in websocket:
                        self.apply_message(raw_message)
                        if self._stop_event.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect loop keeps market cache alive.
                LOGGER.exception("Binance %s WebSocket disconnected", label)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.reconnect_delay_seconds)
            except asyncio.TimeoutError:
                pass

    def apply_message(self, raw_message: str | bytes | dict[str, Any] | list[Any]) -> None:
        payload: Any
        if isinstance(raw_message, bytes):
            payload = json.loads(raw_message.decode("utf-8"))
        elif isinstance(raw_message, str):
            payload = json.loads(raw_message)
        else:
            payload = raw_message

        stream = ""
        data = payload
        if isinstance(payload, dict) and "data" in payload:
            stream = str(payload.get("stream") or "")
            data = payload.get("data")

        self.apply_payload(data, stream)
        self._last_message_ms = now_ms()

    def apply_payload(self, data: Any, stream: str = "") -> None:
        rows = data if isinstance(data, list) else [data]
        valid_rows = [row for row in rows if isinstance(row, dict) and row.get("s") and _is_um_row(row)]
        if not valid_rows:
            return

        stream_lower = stream.lower()
        event_type = str(valid_rows[0].get("e") or "").lower()
        if "bookticker" in stream_lower or event_type == "bookticker":
            self.apply_book_rows(valid_rows)
            return
        if "markprice" in stream_lower or event_type == "markpriceupdate":
            self.apply_mark_rows(valid_rows)
            return
        if "ticker" in stream_lower or event_type == "24hrticker":
            self.apply_ticker_rows(valid_rows)
            return

        for row in valid_rows:
            if {"b", "a"} <= set(row):
                self.apply_book_rows([row])
            elif "p" in row and "r" in row:
                self.apply_mark_rows([row])
            elif "c" in row and "q" in row:
                self.apply_ticker_rows([row])

    def apply_ticker_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            symbol = str(row.get("s") or "").upper()
            last = to_float(row.get("c"))
            if symbol and last is not None:
                self._ticker_rows[symbol] = dict(row)

    def apply_book_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            symbol = str(row.get("s") or "").upper()
            if symbol:
                self._book_rows[symbol] = dict(row)

    def apply_mark_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            symbol = str(row.get("s") or "").upper()
            if symbol:
                self._premium_rows[symbol] = dict(row)

    def cache_age_seconds(self) -> float | None:
        if not self._last_message_ms:
            return None
        return max(0.0, (now_ms() - self._last_message_ms) / 1000)

    def _snapshot_for_symbol(self, symbol: str, timestamp_ms: int) -> TickerSnapshot | None:
        symbol = symbol.upper()
        ticker = self._ticker_rows.get(symbol, {})
        book = self._book_rows.get(symbol, {})
        premium = self._premium_rows.get(symbol, {})
        bid = to_float(book.get("b") if "b" in book else book.get("bidPrice"))
        ask = to_float(book.get("a") if "a" in book else book.get("askPrice"))
        last = to_float(ticker.get("c") if "c" in ticker else ticker.get("lastPrice"))
        if last is None and bid is not None and ask is not None:
            last = (bid + ask) / 2
        if not symbol or last is None:
            return None

        mid = (bid + ask) / 2 if bid is not None and ask is not None else last
        spread = ((ask - bid) / mid * 100) if bid is not None and ask is not None and mid else None
        event_time = to_int(ticker.get("E") or book.get("E") or premium.get("E")) or timestamp_ms
        return TickerSnapshot(
            timestamp_ms=event_time,
            exchange=self.name,
            symbol=symbol,
            last_price=last,
            price_24h_pct=to_float(ticker.get("P") if "P" in ticker else ticker.get("priceChangePercent")),
            turnover_24h=to_float(ticker.get("q") if "q" in ticker else ticker.get("quoteVolume")),
            volume_24h=to_float(ticker.get("v") if "v" in ticker else ticker.get("volume")),
            funding_rate=to_float(premium.get("r") if "r" in premium else premium.get("lastFundingRate")),
            next_funding_time_ms=to_int(premium.get("T") if "T" in premium else premium.get("nextFundingTime")),
            bid_price=bid,
            ask_price=ask,
            spread_pct=spread,
            mark_price=to_float(premium.get("p") if "p" in premium else premium.get("markPrice")),
            index_price=to_float(premium.get("i") if "i" in premium else premium.get("indexPrice")),
            trade_count_24h=to_int(ticker.get("n") if "n" in ticker else ticker.get("count")),
            raw={"ticker": dict(ticker), "bookTicker": dict(book), "premiumIndex": dict(premium), "source": "websocket_cache"},
        )

    def get_cached_ticker(self, symbol: str) -> TickerSnapshot | None:
        return self._snapshot_for_symbol(symbol, now_ms())

    async def get_tickers(self) -> list[TickerSnapshot]:
        age = self.cache_age_seconds()
        if not self._ticker_rows or (age is not None and age > self.fallback_to_rest_after_seconds):
            LOGGER.warning("Binance WebSocket cache stale; falling back to REST tickers")
            snapshots = await self.base.get_tickers()
            for snapshot in snapshots:
                symbol = snapshot.symbol.upper()
                self._ticker_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "c": snapshot.last_price,
                    "P": snapshot.price_24h_pct,
                    "q": snapshot.turnover_24h,
                    "v": snapshot.volume_24h,
                    "n": snapshot.trade_count_24h,
                }
                self._book_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "b": snapshot.bid_price,
                    "a": snapshot.ask_price,
                }
                self._premium_rows[symbol] = {
                    "s": symbol,
                    "E": snapshot.timestamp_ms,
                    "p": snapshot.mark_price,
                    "i": snapshot.index_price,
                    "r": snapshot.funding_rate,
                    "T": snapshot.next_funding_time_ms,
                }
            self._last_message_ms = now_ms()
            return snapshots

        timestamp = now_ms()
        snapshots = [
            snapshot
            for symbol in sorted(self._ticker_rows)
            if (snapshot := self._snapshot_for_symbol(symbol, timestamp)) is not None
        ]
        return snapshots

    async def get_symbols(self) -> list[SymbolInfo]:
        return await self.base.get_symbols()

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        return await self.base.get_klines(symbol, interval, limit)

    async def get_open_interest_history(self, symbol: str, interval: str = "5m", limit: int = 50) -> list[tuple[int, float]]:
        return await self.base.get_open_interest_history(symbol, interval, limit)

    async def get_taker_buy_sell_volume(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict[str, float | int]]:
        return await self.base.get_taker_buy_sell_volume(symbol, period, limit)

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        return await self.base.get_orderbook(symbol, limit)
