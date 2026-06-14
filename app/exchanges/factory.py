from __future__ import annotations

from app.exchanges.base import ExchangeConnector
from app.exchanges.binance import BinanceFuturesPublicConnector
from app.exchanges.bybit import BybitPublicConnector


def selected_exchange_name(config: dict) -> str:
    explicit = (config.get("app", {}).get("exchange") or config.get("exchange") or "").lower()
    if explicit:
        return explicit
    exchanges = config.get("exchanges") or {}
    if exchanges.get("binance", {}).get("enabled", False):
        return "binance"
    return "bybit"


def build_connector(config: dict) -> ExchangeConnector:
    exchange = selected_exchange_name(config)
    performance = config.get("performance") or {}
    max_concurrent = int(performance.get("max_concurrent_requests", 10))
    if exchange == "binance":
        cfg = config["exchanges"]["binance"]
        return BinanceFuturesPublicConnector(
            base_url=cfg.get("base_url", "https://fapi.binance.com"),
            max_concurrent_requests=max_concurrent,
            max_weight_per_minute=int(performance.get("binance_request_weight_limit_per_minute", 900)),
            min_request_interval_seconds=float(performance.get("binance_min_request_interval_seconds", 0.04)),
        )
    if exchange == "bybit":
        cfg = config["exchanges"]["bybit"]
        return BybitPublicConnector(
            base_url=cfg.get("base_url", "https://api.bybit.com"),
            category=cfg.get("category", "linear"),
            max_concurrent_requests=max_concurrent,
        )
    raise ValueError(f"Unsupported exchange: {exchange}")
