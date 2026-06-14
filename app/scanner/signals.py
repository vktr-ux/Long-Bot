from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from app.exchanges.base import ExchangeConnector
from app.scanner.attention import select_attention_candidates, update_attention_state_from_diagnostics
from app.scanner.activity import rank_tickers
from app.scanner.breakout import build_setup_plan, chart_score, detect_breakout
from app.scanner.filters import ticker_universe_filter
from app.scanner.metrics import build_metrics, price_change_from_candles
from app.scanner.rsi import calculate_rsi, rsi_warnings
from app.scanner.scoring import score_signal
from app.storage.models import Candle, CandidateDiagnostics, Metrics, ScanResult, SignalCandidate, TickerSnapshot

LOGGER = logging.getLogger(__name__)


class ScanEngine:
    def __init__(self, connector: ExchangeConnector, config: dict):
        self.connector = connector
        self.config = config

    async def _safe_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        try:
            return await self.connector.get_klines(symbol, interval, limit)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("kline fetch failed for %s %s: %s", symbol, interval, exc)
            return []

    async def _safe_oi(self, symbol: str) -> list[tuple[int, float]]:
        try:
            return await self.connector.get_open_interest_history(symbol, "5min", 50)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("OI fetch failed for %s: %s", symbol, exc)
            return []

    async def _safe_taker(self, symbol: str) -> list[dict[str, float | int]]:
        fetcher = getattr(self.connector, "get_taker_buy_sell_volume", None)
        if fetcher is None:
            return []
        try:
            return await fetcher(symbol, "5m", 30)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("taker volume fetch failed for %s: %s", symbol, exc)
            return []

    async def _safe_orderbook(self, symbol: str) -> dict[str, Any]:
        fetcher = getattr(self.connector, "get_orderbook", None)
        if fetcher is None:
            return {}
        try:
            return await fetcher(symbol, 20)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("orderbook fetch failed for %s: %s", symbol, exc)
            return {}

    async def _btc_background(self) -> tuple[float | None, float | None, float | None]:
        symbol = self.config["btc_filter"]["symbol"]
        candles_15m, candles_60m = await asyncio.gather(
            self._safe_klines(symbol, "15", 20),
            self._safe_klines(symbol, "60", 20),
        )
        return (
            price_change_from_candles(candles_15m, 1),
            price_change_from_candles(candles_15m, 4),
            price_change_from_candles(candles_60m, 4),
        )

    async def load_market(self) -> tuple[list, list[TickerSnapshot]]:
        symbols, tickers = await asyncio.gather(self.connector.get_symbols(), self.connector.get_tickers())
        tickers = rank_tickers(tickers)
        allowed_statuses = {"Trading", "TRADING"}
        allowed = {
            s.symbol
            for s in symbols
            if s.quote_asset == self.config["symbols"]["quote_asset"] and s.status in allowed_statuses
        }
        filtered = [
            t
            for t in tickers
            if t.symbol in allowed and ticker_universe_filter(t, self.config["symbols"])
        ]
        return symbols, filtered

    def _filter_reasons(self, ticker: TickerSnapshot, metrics: Metrics, breakout_state: str | None = None) -> tuple[list[str], dict[str, bool]]:
        cfg = self.config
        min_turnover = cfg["filters"].get("min_quote_volume_24h_usd", cfg["filters"].get("min_turnover_24h_usd", 0))
        min_change_5m = cfg["filters"].get("min_5m_change_abs_pct")
        min_volume_spike = cfg["filters"].get("min_15m_volume_spike", cfg["filters"].get("min_volume_spike_for_candidate", 0))
        max_spread = cfg["filters"].get("max_spread_pct", 0.30)
        checks = {
            "liquidity": (ticker.turnover_24h or 0) >= min_turnover,
            "spread": not (ticker.spread_pct is not None and ticker.spread_pct > max_spread),
            "trigger": any(
                [
                    (metrics.price_change_15m or 0) >= cfg["filters"].get("min_price_change_15m_pct_for_candidate", 999),
                    (metrics.price_change_1h or 0) >= cfg["filters"]["min_price_change_1h_pct_for_candidate"],
                    (ticker.turnover_rank_24h or 9999) <= cfg["filters"]["top_activity_rank_candidate"],
                    (metrics.volume_spike_15m or 0) >= min_volume_spike,
                    min_change_5m is not None and abs(metrics.price_change_5m or 0) >= min_change_5m,
                ]
            ),
        }
        reasons: list[str] = []
        if not checks["liquidity"]:
            reasons.append("24h turnover below threshold")
        if not checks["spread"]:
            reasons.append("spread too wide")
        if not checks["trigger"]:
            reasons.append("no activity/momentum trigger")
        if breakout_state in {"APPROACHING_RESISTANCE", "TESTING_RESISTANCE", "FRESH_BREAKOUT", "CONFIRMED_BREAKOUT"}:
            reasons = [item for item in reasons if item != "no activity/momentum trigger"]
            checks["trigger"] = True
        return reasons, checks

    def _taker_buy_sell_ratio(self, rows: list[dict[str, float | int]]) -> float | None:
        if not rows:
            return None
        latest = rows[-3:] if len(rows) >= 3 else rows
        buy = sum(float(row.get("buyVol") or 0) for row in latest)
        sell = sum(float(row.get("sellVol") or 0) for row in latest)
        if sell <= 0:
            return None
        return buy / sell

    def _depth_usdt_20bps(self, ticker: TickerSnapshot, orderbook: dict[str, Any]) -> float | None:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        bid = ticker.bid_price
        ask = ticker.ask_price
        mid = (bid + ask) / 2 if bid and ask else ticker.last_price
        if not mid:
            return None
        lower = mid * 0.998
        upper = mid * 1.002
        bid_depth = sum(price * qty for price, qty in bids if price >= lower)
        ask_depth = sum(price * qty for price, qty in asks if price <= upper)
        return bid_depth + ask_depth

    async def enrich_one(self, ticker: TickerSnapshot, btc_metrics: tuple[float | None, float | None, float | None]) -> CandidateDiagnostics:
        cfg = self.config
        mcfg = cfg["metrics"]
        candles_1m, candles_5m, candles_15m, candles_60m, candles_240m, oi, taker_rows, orderbook = await asyncio.gather(
            self._safe_klines(ticker.symbol, "1", mcfg["candle_limit_1m"]),
            self._safe_klines(ticker.symbol, "5", mcfg["candle_limit_5m"]),
            self._safe_klines(ticker.symbol, "15", mcfg["candle_limit_15m"]),
            self._safe_klines(ticker.symbol, "60", mcfg["candle_limit_60m"]),
            self._safe_klines(ticker.symbol, "240", mcfg["candle_limit_240m"]),
            self._safe_oi(ticker.symbol),
            self._safe_taker(ticker.symbol),
            self._safe_orderbook(ticker.symbol),
        )
        metrics = build_metrics(
            ticker,
            candles_1m,
            candles_5m,
            candles_15m,
            candles_60m,
            candles_240m,
            oi,
            btc_metrics,
            mcfg["volume_spike_lookback_periods"],
        )
        metrics.rsi_15m = calculate_rsi(candles_15m, cfg["rsi"]["period"])
        metrics.rsi_1h = calculate_rsi(candles_60m, cfg["rsi"]["period"])
        metrics.rsi_4h = calculate_rsi(candles_240m, cfg["rsi"]["period"])
        metrics.taker_buy_sell_ratio = self._taker_buy_sell_ratio(taker_rows)
        metrics.depth_usdt_20bps = self._depth_usdt_20bps(ticker, orderbook)
        breakout = detect_breakout(candles_240m, ticker.last_price, cfg["breakout"])
        reject, checks = self._filter_reasons(ticker, metrics, breakout.state)
        setup = build_setup_plan(ticker.exchange, ticker.symbol, breakout, candles_240m, cfg["setup_quality"])
        chart_tuple = chart_score(breakout)
        rsi_tuple = rsi_warnings(metrics.rsi_15m, metrics.rsi_1h, metrics.rsi_4h, cfg["rsi"])
        total, level, sig_type, scores, risk, reasons, warnings, grade, label = score_signal(
            ticker, metrics, breakout, setup, chart_tuple, rsi_tuple, cfg
        )
        rejection_reasons = list(reject)
        if level == "NO_SIGNAL":
            rejection_reasons.append("score below WATCH")
        filter_stage = "scored"
        if not checks["liquidity"]:
            filter_stage = "failed_liquidity"
        elif not checks["spread"]:
            filter_stage = "failed_spread"
        elif not checks["trigger"]:
            filter_stage = "failed_trigger"
        elif level == "NO_SIGNAL":
            filter_stage = "failed_score"
        return CandidateDiagnostics(
            timestamp_ms=ticker.timestamp_ms,
            exchange=ticker.exchange,
            symbol=ticker.symbol,
            ticker=ticker,
            score=total,
            level=level,
            signal_type=sig_type,
            state=breakout.state,
            metrics=metrics,
            breakout=breakout,
            setup=setup,
            scores=scores,
            risk_penalty=risk,
            reasons=reasons,
            warnings=warnings,
            grade=grade,
            review_label=label,
            filter_stage_passed=filter_stage,
            rejection_reasons=rejection_reasons,
            candles={"1": candles_1m, "5": candles_5m, "15": candles_15m, "60": candles_60m, "240": candles_240m},
            oi_history=oi,
            btc_metrics=btc_metrics,
        )

    async def enrich_candidates(
        self,
        tickers: list[TickerSnapshot],
        btc_metrics: tuple[float | None, float | None, float | None],
    ) -> list[CandidateDiagnostics]:
        if not tickers:
            return []
        performance = self.config.get("performance", {})
        candidate_limit = int(performance.get("max_concurrent_enrich_candidates", performance.get("max_concurrent_requests", 3)) or 1)
        semaphore = asyncio.Semaphore(max(1, candidate_limit))

        async def enrich_guarded(ticker: TickerSnapshot) -> CandidateDiagnostics:
            async with semaphore:
                return await self.enrich_one(ticker, btc_metrics)

        return list(await asyncio.gather(*(enrich_guarded(ticker) for ticker in tickers)))

    async def explain_symbol(self, symbol: str) -> CandidateDiagnostics | None:
        _symbols, tickers = await self.load_market()
        ticker_map = {ticker.symbol.upper(): ticker for ticker in tickers}
        ticker = ticker_map.get(symbol.upper())
        if ticker is None:
            return None
        btc_metrics = await self._btc_background()
        return await self.enrich_one(ticker, btc_metrics)

    async def scan_once(
        self,
        explain_symbol: str | None = None,
        *,
        snapshot_enricher: Any | None = None,
        attention_state: dict[str, Any] | None = None,
        recent_trade_plans: list[dict[str, Any]] | None = None,
        recent_trades: list[dict[str, Any]] | None = None,
    ) -> ScanResult:
        symbols, tickers = await self.load_market()
        max_candidates = self.config["performance"]["max_enriched_candidates_per_cycle"]
        active_limit = int(self.config.get("filters", {}).get("top_activity_rank_candidate", max_candidates) or max_candidates)
        ranked_candidates = sorted(
            tickers,
            key=lambda t: (
                (t.turnover_rank_24h or 9999),
                -max(abs(t.price_change_5m_pct or 0), abs(t.price_change_15m_pct or 0), abs(t.price_24h_pct or 0) / 24),
                (t.spread_pct if t.spread_pct is not None else 999),
                -(t.trade_count_24h or 0),
            ),
        )
        active_watchlist = ranked_candidates[:active_limit]
        if snapshot_enricher is not None:
            maybe_awaitable = snapshot_enricher(active_watchlist)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        attention_stats: dict[str, Any] = {"enabled": False}
        next_attention_state = attention_state or {}
        if self.config.get("performance", {}).get("attention_scheduler_enabled", True):
            selection = select_attention_candidates(
                active_watchlist,
                max_candidates,
                self.config,
                state=attention_state,
                recent_trade_plans=recent_trade_plans,
                recent_trades=recent_trades,
            )
            broad_candidates = selection.selected
            next_attention_state = selection.next_state
            attention_stats = selection.stats
        else:
            broad_candidates = active_watchlist[:max_candidates]
        if explain_symbol:
            explain_upper = explain_symbol.upper()
            if all(t.symbol.upper() != explain_upper for t in broad_candidates):
                match = next((t for t in tickers if t.symbol.upper() == explain_upper), None)
                if match:
                    broad_candidates.append(match)
        btc_metrics = await self._btc_background()
        diagnostics = await self.enrich_candidates(broad_candidates, btc_metrics)
        signals: list[SignalCandidate] = []
        rejected: dict[str, list[str]] = {}
        for diagnostic in diagnostics:
            rejected_by_filter = diagnostic.rejection_reasons and diagnostic.level in {"NO_SIGNAL", "WATCH"}
            if diagnostic.level != "NO_SIGNAL" and not rejected_by_filter:
                signals.append(diagnostic.to_signal())
            if diagnostic.rejection_reasons:
                rejected[diagnostic.symbol] = diagnostic.rejection_reasons
        signals.sort(key=lambda s: s.score, reverse=True)
        stage_counts = {
            "symbols_total": len(symbols),
            "tickers_total": len(tickers),
            "after_universe_filter": len(tickers),
            "active_watchlist": len(active_watchlist),
            "selected_for_enrichment": len(broad_candidates),
            "enriched": len(diagnostics),
            "passed_liquidity": sum(d.filter_stage_passed not in {"failed_liquidity"} for d in diagnostics),
            "passed_spread": sum(d.filter_stage_passed not in {"failed_liquidity", "failed_spread"} for d in diagnostics),
            "passed_trigger_or_chart": sum(d.filter_stage_passed not in {"failed_liquidity", "failed_spread", "failed_trigger"} for d in diagnostics),
            "passed_score": sum(d.level != "NO_SIGNAL" for d in diagnostics),
            "emitted_candidates": len(signals),
            "attention_waiting_selected": int(attention_stats.get("bucket_counts", {}).get("waiting", 0)),
            "attention_hot_selected": int(attention_stats.get("bucket_counts", {}).get("hot", 0)),
            "attention_recent_selected": int(attention_stats.get("bucket_counts", {}).get("recent", 0)),
            "attention_reversal_selected": int(attention_stats.get("bucket_counts", {}).get("reversal", 0)),
            "attention_rotation_selected": int(attention_stats.get("bucket_counts", {}).get("rotation", 0)),
            "attention_loss_blocked": int(attention_stats.get("loss_blocked", 0)),
        }
        next_attention_state = update_attention_state_from_diagnostics(next_attention_state, diagnostics, self.config)
        return ScanResult(
            symbols_total=len(symbols),
            tickers_total=len(tickers),
            symbols_scanned=len(active_watchlist),
            enriched_count=len(diagnostics),
            symbols=symbols,
            tickers=active_watchlist,
            enriched_tickers=broad_candidates,
            diagnostics=sorted(diagnostics, key=lambda d: d.score, reverse=True),
            signals=signals,
            rejected=rejected,
            stage_counts=stage_counts,
            attention_state=next_attention_state,
            attention_stats=attention_stats,
        )
