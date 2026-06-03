from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SymbolInfo:
    exchange: str
    symbol: str
    base_asset: str | None
    quote_asset: str
    status: str
    contract_type: str | None = None
    launch_time_ms: int | None = None


@dataclass(slots=True)
class TickerSnapshot:
    timestamp_ms: int
    exchange: str
    symbol: str
    last_price: float
    price_24h_pct: float | None = None
    turnover_24h: float | None = None
    volume_24h: float | None = None
    turnover_rank_24h: int | None = None
    volume_rank_24h: int | None = None
    open_interest: float | None = None
    open_interest_value: float | None = None
    funding_rate: float | None = None
    next_funding_time_ms: int | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    spread_pct: float | None = None
    prev_price_24h: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Candle:
    timestamp_ms: int
    exchange: str
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float | None = None
    is_closed: bool = True


@dataclass(slots=True)
class Metrics:
    exchange: str
    symbol: str
    timestamp_ms: int
    price_change_1m: float | None = None
    price_change_5m: float | None = None
    price_change_15m: float | None = None
    price_change_1h: float | None = None
    price_change_4h: float | None = None
    price_change_24h: float | None = None
    volume_spike_15m: float | None = None
    turnover_spike_15m: float | None = None
    volume_spike_1h: float | None = None
    turnover_spike_1h: float | None = None
    oi_change_5m_pct: float | None = None
    oi_change_15m_pct: float | None = None
    oi_change_1h_pct: float | None = None
    funding_rate: float | None = None
    turnover_24h: float | None = None
    turnover_rank_24h: int | None = None
    volume_rank_24h: int | None = None
    spread_pct: float | None = None
    btc_change_15m: float | None = None
    btc_change_1h: float | None = None
    btc_change_4h: float | None = None
    rsi_15m: float | None = None
    rsi_1h: float | None = None
    rsi_4h: float | None = None


@dataclass(slots=True)
class ResistanceZone:
    timeframe: str
    zone_low: float
    zone_high: float
    zone_mid: float
    touches: int
    first_touch_ts_ms: int
    last_touch_ts_ms: int
    strength_score: float


@dataclass(slots=True)
class BreakoutContext:
    state: str
    timeframe: str
    resistance_zone: ResistanceZone | None
    current_price: float
    distance_to_zone_pct: float | None = None
    distance_above_zone_pct: float | None = None
    breakout_buffer_pct: float = 0.006
    latest_candle_body_pct: float | None = None
    latest_candle_close_position: float | None = None
    latest_candle_upper_wick_pct: float | None = None
    volume_confirmed: bool = False


@dataclass(slots=True)
class SetupPlan:
    exchange: str
    symbol: str
    setup_type: str
    current_price: float
    entry_context: str
    breakout_zone_low: float | None = None
    breakout_zone_high: float | None = None
    suggested_watch_zone_low: float | None = None
    suggested_watch_zone_high: float | None = None
    invalidation_price: float | None = None
    invalidation_reason: str | None = None
    distance_to_invalidation_pct: float | None = None
    target_zone_low: float | None = None
    target_zone_high: float | None = None
    target_reason: str | None = None
    room_to_target_pct: float | None = None
    estimated_rr: float | None = None
    chase_risk: str = "UNKNOWN"


@dataclass(slots=True)
class SignalCandidate:
    timestamp_ms: int
    exchange: str
    symbol: str
    score: int
    level: str
    signal_type: str
    state: str
    metrics: Metrics
    breakout: BreakoutContext | None
    setup: SetupPlan | None = None
    scores: dict[str, int] = field(default_factory=dict)
    risk_penalty: int = 0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    grade: str = "C"
    review_label: str = "NO_CLEAN_SETUP"

    def to_jsonable(self) -> dict:
        data = asdict(self)
        return data


@dataclass(slots=True)
class CandidateDiagnostics:
    timestamp_ms: int
    exchange: str
    symbol: str
    ticker: TickerSnapshot
    metrics: Metrics
    score: int
    level: str
    signal_type: str
    state: str
    scores: dict[str, int]
    risk_penalty: int
    reasons: list[str]
    warnings: list[str]
    filter_stage_passed: str
    rejection_reasons: list[str] = field(default_factory=list)
    breakout: BreakoutContext | None = None
    setup: SetupPlan | None = None
    grade: str = "C"
    review_label: str = "NO_CLEAN_SETUP"
    candles: dict[str, list[Candle]] = field(default_factory=dict)
    oi_history: list[tuple[int, float]] = field(default_factory=list)
    btc_metrics: tuple[float | None, float | None, float | None] = (None, None, None)

    def to_signal(self) -> SignalCandidate:
        return SignalCandidate(
            timestamp_ms=self.timestamp_ms,
            exchange=self.exchange,
            symbol=self.symbol,
            score=self.score,
            level=self.level,
            signal_type=self.signal_type,
            state=self.state,
            metrics=self.metrics,
            breakout=self.breakout,
            setup=self.setup,
            scores=self.scores,
            risk_penalty=self.risk_penalty,
            reasons=self.reasons,
            warnings=self.warnings,
            grade=self.grade,
            review_label=self.review_label,
        )

    def to_jsonable(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("candles", None)
        data.pop("ticker", None)
        data["metrics"] = asdict(self.metrics)
        data["ticker"] = asdict(self.ticker)
        return data


@dataclass(slots=True)
class ScanResult:
    symbols_total: int
    tickers_total: int
    symbols_scanned: int
    enriched_count: int
    symbols: list[SymbolInfo]
    tickers: list[TickerSnapshot]
    enriched_tickers: list[TickerSnapshot]
    diagnostics: list[CandidateDiagnostics]
    signals: list[SignalCandidate]
    rejected: dict[str, list[str]]
    stage_counts: dict[str, int]
