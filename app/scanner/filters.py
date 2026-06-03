from __future__ import annotations

from app.storage.models import Metrics, TickerSnapshot


def ticker_universe_filter(ticker: TickerSnapshot, symbols_config: dict) -> bool:
    quote = symbols_config["quote_asset"]
    if not ticker.symbol.endswith(quote):
        return False
    if symbols_config.get("include_symbols") and ticker.symbol not in symbols_config["include_symbols"]:
        return False
    if ticker.symbol in set(symbols_config.get("blacklist") or []):
        return False
    if symbols_config.get("exclude_major_symbols") and ticker.symbol in set(symbols_config.get("major_symbols") or []):
        return False
    return True


def candidate_passes(ticker: TickerSnapshot, metrics: Metrics, config: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if (ticker.turnover_24h or 0) < config["min_turnover_24h_usd"]:
        reasons.append("24h turnover below threshold")
    if ticker.spread_pct is not None and ticker.spread_pct > config["max_spread_pct"]:
        reasons.append("spread too wide")
    trigger = any(
        [
            (metrics.price_change_15m or 0) >= config["min_price_change_15m_pct_for_candidate"],
            (metrics.price_change_1h or 0) >= config["min_price_change_1h_pct_for_candidate"],
            (ticker.turnover_rank_24h or 9999) <= config["top_activity_rank_candidate"],
            (metrics.volume_spike_15m or 0) >= config["min_volume_spike_for_candidate"],
        ]
    )
    if not trigger:
        reasons.append("no activity/momentum trigger")
    return not reasons, reasons

