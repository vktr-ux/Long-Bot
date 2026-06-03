from __future__ import annotations

from abc import ABC, abstractmethod

from app.storage.models import Candle, SymbolInfo, TickerSnapshot


class ExchangeConnector(ABC):
    name: str

    @abstractmethod
    async def get_symbols(self) -> list[SymbolInfo]:
        raise NotImplementedError

    @abstractmethod
    async def get_tickers(self) -> list[TickerSnapshot]:
        raise NotImplementedError

    @abstractmethod
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        raise NotImplementedError

    @abstractmethod
    async def get_open_interest_history(self, symbol: str, interval: str, limit: int) -> list[tuple[int, float]]:
        raise NotImplementedError

