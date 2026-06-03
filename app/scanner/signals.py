from __future__ import annotations

import asyncio
import logging

from app.exchanges.bybit import BybitPublicConnector
from app.scanner.activity import rank_tickers
from app.scanner.breakout import build_setup_plan, chart_score, detect_breakout
from app.scanner.filters import ticker_universe_filter
from app.scanner.metrics import build_metrics, price_change_from_candles
from app.scanner.rsi import calculate_rsi, rsi_warnings
from app.scanner.scoring import score_signal
from app.storage.models import Candle, CandidateDiagnostics, Metrics, ScanResult, SignalCandidate, TickerSnapshot

LOGGER = logging.getLogger(__name__)


class ScanEngine:
    def __init__(self, connector: BybitPublicConnector, config: dict):
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
        allowed = {s.symbol for s in symbols if s.quote_asset == self.config["symbols"]["quote_asset"] and s.status == "Trading"}
        filtered = [
            t
            for t in tickers
            if t.symbol in allowed and ticker_universe_filter(t, self.config["symbols"])
        ]
        return symbols, filtered

    def _filter_reasons(self, ticker: TickerSnapshot, metrics: Metrics, breakout_state: str | None = None) -> tuple[list[str], dict[str, bool]]:
        cfg = self.config
        checks = {
            "liquidity": (ticker.turnover_24h or 0) >= cfg["filters"]["min_turnover_24h_usd"],
            "spread": not (ticker.spread_pct is not None and ticker.spread_pct > cfg["filters"]["max_spread_pct"]),
            "trigger": any(
                [
                    (metrics.price_change_15m or 0) >= cfg["filters"]["min_price_change_15m_pct_for_candidate"],
                    (metrics.price_change_1h or 0) >= cfg["filters"]["min_price_change_1h_pct_for_candidate"],
                    (ticker.turnover_rank_24h or 9999) <= cfg["filters"]["top_activity_rank_candidate"],
                    (metrics.volume_spike_15m or 0) >= cfg["filters"]["min_volume_spike_for_candidate"],
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

    async def enrich_one(self, ticker: TickerSnapshot, btc_metrics: tuple[float | None, float | None, float | None]) -> CandidateDiagnostics:
        cfg = self.config
        mcfg = cfg["metrics"]
        candles_1m, candles_5m, candles_15m, candles_60m, candles_240m, oi = await asyncio.gather(
            self._safe_klines(ticker.symbol, "1", mcfg["candle_limit_1m"]),
            self._safe_klines(ticker.symbol, "5", mcfg["candle_limit_5m"]),
            self._safe_klines(ticker.symbol, "15", mcfg["candle_limit_15m"]),
            self._safe_klines(ticker.symbol, "60", mcfg["candle_limit_60m"]),
            self._safe_klines(ticker.symbol, "240", mcfg["candle_limit_240m"]),
            self._safe_oi(ticker.symbol),
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

    async def explain_symbol(self, symbol: str) -> CandidateDiagnostics | None:
        _symbols, tickers = await self.load_market()
        ticker_map = {ticker.symbol.upper(): ticker for ticker in tickers}
        ticker = ticker_map.get(symbol.upper())
        if ticker is None:
            return None
        btc_metrics = await self._btc_background()
        return await self.enrich_one(ticker, btc_metrics)

    async def scan_once(self, explain_symbol: str | None = None) -> ScanResult:
        symbols, tickers = await self.load_market()
        max_candidates = self.config["performance"]["max_enriched_candidates_per_cycle"]
        broad_candidates = sorted(
            tickers,
            key=lambda t: ((t.turnover_rank_24h or 9999), -(t.price_24h_pct or 0)),
        )[:max_candidates]
        if explain_symbol:
            explain_upper = explain_symbol.upper()
            if all(t.symbol.upper() != explain_upper for t in broad_candidates):
                match = next((t for t in tickers if t.symbol.upper() == explain_upper), None)
                if match:
                    broad_candidates.append(match)
        btc_metrics = await self._btc_background()
        diagnostics = list(await asyncio.gather(*(self.enrich_one(ticker, btc_metrics) for ticker in broad_candidates)))
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
            "selected_for_enrichment": len(broad_candidates),
            "enriched": len(diagnostics),
            "passed_liquidity": sum(d.filter_stage_passed not in {"failed_liquidity"} for d in diagnostics),
            "passed_spread": sum(d.filter_stage_passed not in {"failed_liquidity", "failed_spread"} for d in diagnostics),
            "passed_trigger_or_chart": sum(d.filter_stage_passed not in {"failed_liquidity", "failed_spread", "failed_trigger"} for d in diagnostics),
            "passed_score": sum(d.level != "NO_SIGNAL" for d in diagnostics),
            "emitted_candidates": len(signals),
        }
        return ScanResult(
            symbols_total=len(symbols),
            tickers_total=len(tickers),
            symbols_scanned=len(tickers),
            enriched_count=len(diagnostics),
            symbols=symbols,
            tickers=tickers,
            enriched_tickers=broad_candidates,
            diagnostics=sorted(diagnostics, key=lambda d: d.score, reverse=True),
            signals=signals,
            rejected=rejected,
            stage_counts=stage_counts,
        )
