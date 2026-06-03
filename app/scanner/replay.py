from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.exchanges.bybit import BybitPublicConnector
from app.scanner.breakout import build_setup_plan, chart_score, detect_breakout
from app.scanner.metrics import build_metrics
from app.scanner.outcomes import compute_outcome
from app.scanner.rsi import calculate_rsi, rsi_warnings
from app.scanner.scoring import score_signal
from app.storage.models import Candle, CandidateDiagnostics, Metrics, SetupPlan, TickerSnapshot
from app.utils.numbers import pct_change, to_float
from app.utils.time import utc_iso_from_ms


@dataclass(slots=True)
class ReplayReport:
    symbol: str
    exchange: str
    start_ms: int
    end_ms: int
    profile: str
    first_signal: CandidateDiagnostics | None
    first_breakout_time_ms: int | None
    breakout_phase: str
    max_price_after_signal: float | None = None
    max_drawdown_after_signal_pct: float | None = None
    target_reached: bool | None = None
    invalidation_hit: bool | None = None
    steps: list[CandidateDiagnostics] = field(default_factory=list)
    missing_data_notes: list[str] = field(default_factory=list)


def parse_replay_datetime(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1000)


def _slice(candles: list[Candle], timestamp_ms: int) -> list[Candle]:
    return [c for c in candles if c.timestamp_ms <= timestamp_ms and c.is_closed]


def _oi_slice(history: list[tuple[int, float]], timestamp_ms: int) -> list[tuple[int, float]]:
    return [item for item in history if item[0] <= timestamp_ms]


def _funding_at(history: list[dict[str, Any]], timestamp_ms: int) -> float | None:
    best_ts = -1
    best_rate: float | None = None
    for row in history:
        ts = int(row.get("fundingRateTimestamp") or row.get("timestamp") or 0)
        rate = to_float(row.get("fundingRate"))
        if rate is not None and ts <= timestamp_ms and ts > best_ts:
            best_ts = ts
            best_rate = rate
    return best_rate


def _rolling_sum(candles: list[Candle], field: str, limit: int) -> float | None:
    values = [getattr(c, field) for c in candles[-limit:]]
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return sum(numeric)


def build_replay_step(
    symbol: str,
    exchange: str,
    timestamp_ms: int,
    candles_by_interval: dict[str, list[Candle]],
    oi_history: list[tuple[int, float]],
    funding_history: list[dict[str, Any]],
    btc_metrics: tuple[float | None, float | None, float | None],
    config: dict,
) -> CandidateDiagnostics | None:
    candles_15m = _slice(candles_by_interval.get("15", []), timestamp_ms)
    if len(candles_15m) < 20:
        return None
    current = candles_15m[-1]
    candles_1m = _slice(candles_by_interval.get("1", []), timestamp_ms)
    candles_5m = _slice(candles_by_interval.get("5", []), timestamp_ms)
    candles_60m = _slice(candles_by_interval.get("60", []), timestamp_ms)
    candles_240m = _slice(candles_by_interval.get("240", []), timestamp_ms)
    oi = _oi_slice(oi_history, timestamp_ms)
    funding_rate = _funding_at(funding_history, timestamp_ms)
    price_24h = pct_change(current.close, candles_15m[-97].close) if len(candles_15m) >= 97 else None
    turnover_24h = _rolling_sum(candles_15m, "turnover", 96)
    volume_24h = _rolling_sum(candles_15m, "volume", 96)
    ticker = TickerSnapshot(
        timestamp_ms=timestamp_ms,
        exchange=exchange,
        symbol=symbol,
        last_price=current.close,
        price_24h_pct=price_24h,
        turnover_24h=turnover_24h,
        volume_24h=volume_24h,
        open_interest=oi[-1][1] if oi else None,
        open_interest_value=None,
        funding_rate=funding_rate,
        spread_pct=None,
        raw={"source": "replay_derived_public_candles"},
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
        config["metrics"]["volume_spike_lookback_periods"],
    )
    metrics.rsi_15m = calculate_rsi(candles_15m, config["rsi"]["period"])
    metrics.rsi_1h = calculate_rsi(candles_60m, config["rsi"]["period"])
    metrics.rsi_4h = calculate_rsi(candles_240m, config["rsi"]["period"])
    breakout = detect_breakout(candles_240m, current.close, config["breakout"])
    setup = build_setup_plan(exchange, symbol, breakout, candles_240m, config["setup_quality"])
    chart_tuple = chart_score(breakout)
    rsi_tuple = rsi_warnings(metrics.rsi_15m, metrics.rsi_1h, metrics.rsi_4h, config["rsi"])
    total, level, sig_type, scores, risk, reasons, warnings, grade, label = score_signal(
        ticker, metrics, breakout, setup, chart_tuple, rsi_tuple, config
    )
    rejection_reasons: list[str] = []
    if not oi:
        warnings.append("replay OI unavailable - derivatives score reduced")
    if funding_rate is None:
        warnings.append("replay funding unavailable - funding score unavailable")
    if level == "NO_SIGNAL":
        rejection_reasons.append("score below WATCH")
    return CandidateDiagnostics(
        timestamp_ms=timestamp_ms,
        exchange=exchange,
        symbol=symbol,
        ticker=ticker,
        metrics=metrics,
        score=total,
        level=level,
        signal_type=sig_type,
        state=breakout.state,
        scores=scores,
        risk_penalty=risk,
        reasons=reasons,
        warnings=warnings,
        filter_stage_passed="replay_scored" if level != "NO_SIGNAL" else "replay_failed_score",
        rejection_reasons=rejection_reasons,
        breakout=breakout,
        setup=setup,
        grade=grade,
        review_label=label,
        candles=candles_by_interval,
        oi_history=oi,
        btc_metrics=btc_metrics,
    )


def run_replay_on_candles(
    symbol: str,
    exchange: str,
    start_ms: int,
    end_ms: int,
    candles_by_interval: dict[str, list[Candle]],
    oi_history: list[tuple[int, float]] | None,
    funding_history: list[dict[str, Any]] | None,
    config: dict,
) -> ReplayReport:
    steps: list[CandidateDiagnostics] = []
    missing = []
    oi = sorted(oi_history or [], key=lambda item: item[0])
    funding = funding_history or []
    missing.append("Historical all-market activity rank is unavailable from Bybit kline replay; turnover-rank score is not awarded.")
    if not oi:
        missing.append("OI history unavailable; replay used price/volume/breakout layers without OI confirmation.")
    if not funding:
        missing.append("Funding history unavailable; replay did not award funding score.")
    replay_candles = [c for c in candles_by_interval.get("15", []) if start_ms <= c.timestamp_ms <= end_ms and c.is_closed]
    btc_metrics = (None, None, None)
    first_breakout_time: int | None = None
    for candle in replay_candles:
        step = build_replay_step(symbol, exchange, candle.timestamp_ms, candles_by_interval, oi, funding, btc_metrics, config)
        if not step:
            continue
        steps.append(step)
        if first_breakout_time is None and step.state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"}:
            first_breakout_time = step.timestamp_ms
    first_signal = next((step for step in steps if step.level != "NO_SIGNAL"), None)
    phase = "no_signal"
    if first_signal and first_breakout_time:
        if first_signal.timestamp_ms < first_breakout_time:
            phase = "before_breakout"
        elif first_signal.timestamp_ms == first_breakout_time:
            phase = "during_breakout"
        else:
            phase = "after_breakout"
    elif first_signal:
        phase = "signal_without_detected_breakout"
    after_signal = []
    outcome = {}
    if first_signal:
        after_signal = [c for c in candles_by_interval.get("15", []) if first_signal.timestamp_ms <= c.timestamp_ms <= end_ms]
        target = first_signal.setup.target_zone_low if first_signal.setup else None
        invalidation = first_signal.setup.invalidation_price if first_signal.setup else None
        outcome = compute_outcome(after_signal, first_signal.ticker.last_price, target=target, invalidation=invalidation)
    return ReplayReport(
        symbol=symbol,
        exchange=exchange,
        start_ms=start_ms,
        end_ms=end_ms,
        profile=config["app"]["profile"],
        first_signal=first_signal,
        first_breakout_time_ms=first_breakout_time,
        breakout_phase=phase,
        max_price_after_signal=outcome.get("max_price") if outcome else None,
        max_drawdown_after_signal_pct=outcome.get("mae_pct") if outcome else None,
        target_reached=outcome.get("target_touched") if outcome else None,
        invalidation_hit=outcome.get("invalidation_touched") if outcome else None,
        steps=steps,
        missing_data_notes=missing,
    )


async def load_public_replay_data(
    connector: BybitPublicConnector,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, list[Candle]], list[tuple[int, float]], list[dict[str, Any]], list[str]]:
    warmups = {
        "1": timedelta(hours=4),
        "5": timedelta(days=1),
        "15": timedelta(days=2),
        "60": timedelta(days=5),
        "240": timedelta(days=30),
    }
    notes: list[str] = []
    async def fetch_interval(interval: str) -> list[Candle]:
        warm_start = start_ms - int(warmups[interval].total_seconds() * 1000)
        return await connector.get_klines_range(symbol, interval, warm_start, end_ms)

    candles_1, candles_5, candles_15, candles_60, candles_240 = await asyncio.gather(
        fetch_interval("1"),
        fetch_interval("5"),
        fetch_interval("15"),
        fetch_interval("60"),
        fetch_interval("240"),
    )
    oi_history: list[tuple[int, float]] = []
    funding_history: list[dict[str, Any]] = []
    try:
        oi_history = await connector.get_open_interest_history_range(symbol, "5min", start_ms - 4 * 60 * 60 * 1000, end_ms, 200)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"Historical OI unavailable from Bybit public API: {exc}")
    try:
        funding_history = await connector.get_funding_history_range(symbol, start_ms, end_ms, 200)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"Historical funding unavailable from Bybit public API: {exc}")
    return (
        {"1": candles_1, "5": candles_5, "15": candles_15, "60": candles_60, "240": candles_240},
        oi_history,
        funding_history,
        notes,
    )


def format_replay_report(report: ReplayReport) -> str:
    lines = [
        "Replay report",
        f"Symbol: {report.symbol}",
        f"Exchange: {report.exchange}",
        f"Profile: {report.profile}",
        f"Start/end: {utc_iso_from_ms(report.start_ms)} - {utc_iso_from_ms(report.end_ms)}",
    ]
    if report.missing_data_notes:
        lines.append("Missing data notes:")
        lines.extend(f"- {note}" for note in report.missing_data_notes)
    signal = report.first_signal
    if not signal:
        lines.extend(
            [
                "First signal time: n/a",
                "First signal level: NO_SIGNAL",
                f"Breakout phase: {report.breakout_phase}",
                f"Replay steps evaluated: {len(report.steps)}",
            ]
        )
        if report.steps:
            best = max(report.steps, key=lambda step: step.score)
            lines.append(f"Best replay score: {best.symbol} {best.score}/{best.level} state={best.state}")
            lines.append(f"Best reasons: {best.reasons}")
            lines.append(f"Best warnings: {best.warnings}")
        return "\n".join(lines)
    zone = signal.breakout.resistance_zone if signal.breakout else None
    setup: SetupPlan | None = signal.setup
    lines.extend(
        [
            f"First signal time: {signal.timestamp_ms}",
            f"First signal UTC: {utc_iso_from_ms(signal.timestamp_ms)}",
            f"First signal level: {signal.level}",
            f"First signal price: {signal.ticker.last_price:.8g}",
            f"Breakout phase: {report.breakout_phase}",
            f"Breakout zone: {zone.zone_low:.8g}-{zone.zone_high:.8g}" if zone else "Breakout zone: n/a",
            f"Score: {signal.score}",
            f"Reasons: {signal.reasons}",
            f"Warnings: {signal.warnings}",
            f"Max price after signal: {report.max_price_after_signal if report.max_price_after_signal is not None else 'n/a'}",
            f"Max drawdown after signal: {report.max_drawdown_after_signal_pct if report.max_drawdown_after_signal_pct is not None else 'n/a'}",
            f"Target zone reached: {report.target_reached}",
            f"Invalidation hit: {report.invalidation_hit}",
            f"Target reference: {setup.target_zone_low if setup else 'n/a'}",
            f"Invalidation reference: {setup.invalidation_price if setup else 'n/a'}",
            f"Replay steps evaluated: {len(report.steps)}",
        ]
    )
    return "\n".join(lines)
