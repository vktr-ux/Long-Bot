from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Iterable

from app.storage.models import CandidateDiagnostics, ScanResult
from app.utils.numbers import fmt_money, fmt_pct

LOGGER = logging.getLogger(__name__)

LEVELS = ["NO_SIGNAL", "WATCH", "HOT", "BREAKOUT_HOT", "VERY_HOT"]


def safe_float(value: float | None) -> float:
    return float("-inf") if value is None else value


def level_counts(diagnostics: Iterable[CandidateDiagnostics]) -> dict[str, int]:
    counts = {level: 0 for level in LEVELS}
    for diagnostic in diagnostics:
        counts[diagnostic.level] = counts.get(diagnostic.level, 0) + 1
    return counts


def score_distribution(diagnostics: list[CandidateDiagnostics]) -> dict:
    if not diagnostics:
        return {"min": None, "median": None, "max": None, "counts": level_counts([])}
    scores = [d.score for d in diagnostics]
    return {
        "min": min(scores),
        "median": median(scores),
        "max": max(scores),
        "counts": level_counts(diagnostics),
    }


def print_ranked(title: str, diagnostics: list[CandidateDiagnostics], key_name: str, top: int) -> None:
    def key_func(item: CandidateDiagnostics) -> float:
        if key_name == "activity":
            rank = item.metrics.turnover_rank_24h
            return float("-inf") if rank is None else -rank
        return safe_float(getattr(item.metrics, key_name))

    print(f"\n{title}:")
    for idx, item in enumerate(sorted(diagnostics, key=key_func, reverse=True)[:top], start=1):
        value = item.metrics.turnover_rank_24h if key_name == "activity" else getattr(item.metrics, key_name)
        print(
            f"  {idx:>2}. {item.symbol:<14} score={item.score:>3} level={item.level:<13} "
            f"value={value if value is not None else 'n/a'}"
        )


def print_score_visibility(result: ScanResult) -> None:
    diagnostics = result.diagnostics
    if not diagnostics:
        print("Score visibility: no enriched candidates")
        LOGGER.info("Score visibility: no enriched candidates")
        return
    best = max(diagnostics, key=lambda item: item.score)
    counts = level_counts(diagnostics)
    top_scores = ", ".join(f"{d.symbol}:{d.score}/{d.level}" for d in sorted(diagnostics, key=lambda d: d.score, reverse=True)[:10])
    failed = [
        f"{d.symbol}: {', '.join(d.rejection_reasons or ['no rejection, below alert/cooldown'])}"
        for d in sorted(diagnostics, key=lambda d: d.score, reverse=True)[:5]
        if d.level == "NO_SIGNAL" or d.rejection_reasons
    ]
    print("\nScore visibility:")
    print(f"  Highest score: {best.symbol} {best.score}/{best.level}")
    print(f"  Top 10 scores: {top_scores}")
    print(f"  Level counts: {counts}")
    if failed:
        print("  Best candidate failures:")
        for line in failed:
            print(f"    {line}")
    LOGGER.info("Highest score candidate: %s %s/%s", best.symbol, best.score, best.level)
    LOGGER.info("Top 10 candidate scores: %s", top_scores)
    LOGGER.info("Level counts: %s", counts)
    if failed:
        LOGGER.info("Best candidate failures: %s", "; ".join(failed))


def print_diagnostic_report(result: ScanResult, top: int) -> None:
    diagnostics = result.diagnostics
    print("\nDiagnostic summary:")
    print(f"  Symbols scanned: {result.symbols_scanned}")
    print(f"  Symbols enriched: {result.enriched_count}")
    print("  Filter stages:")
    for name, count in result.stage_counts.items():
        print(f"    {name}: {count}")
    distribution = score_distribution(diagnostics)
    print(
        "  Score distribution: "
        f"min={distribution['min']} median={distribution['median']} max={distribution['max']} counts={distribution['counts']}"
    )
    print_ranked("Top by activity rank", diagnostics, "activity", top)
    print_ranked("Top by 15m price change", diagnostics, "price_change_15m", top)
    print_ranked("Top by 1h price change", diagnostics, "price_change_1h", top)
    print_ranked("Top by volume_spike_15m", diagnostics, "volume_spike_15m", top)
    print_ranked("Top by OI 15m change", diagnostics, "oi_change_15m_pct", top)
    rejected = [d for d in diagnostics if d.rejection_reasons]
    print("\nTop rejected candidates:")
    for item in sorted(rejected, key=lambda d: d.score, reverse=True)[:top]:
        print(f"  {item.symbol:<14} score={item.score:>3} level={item.level:<13} reasons={'; '.join(item.rejection_reasons)}")


def export_diagnostics_jsonl(result: ScanResult, path: str | Path = "data/diagnostics/latest_candidates.jsonl") -> Path:
    export_path = Path(path)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with export_path.open("w", encoding="utf-8") as fh:
        for item in result.diagnostics:
            payload = {
                "timestamp": item.timestamp_ms,
                "exchange": item.exchange,
                "symbol": item.symbol,
                "score": item.score,
                "level": item.level,
                "metrics": asdict(item.metrics),
                "reasons": item.reasons,
                "warnings": item.warnings,
                "filter_stage_passed": item.filter_stage_passed,
                "rejection_reason": "; ".join(item.rejection_reasons) if item.rejection_reasons else None,
            }
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return export_path


def _print_candle_rows(title: str, candles) -> None:
    print(f"\n{title}:")
    for candle in candles[-8:]:
        status = "closed" if candle.is_closed else "live"
        print(
            f"  {candle.timestamp_ms} {status:<6} "
            f"O={candle.open:.8g} H={candle.high:.8g} L={candle.low:.8g} C={candle.close:.8g} "
            f"V={candle.volume:.8g} T={candle.turnover if candle.turnover is not None else 'n/a'}"
        )


def print_explain(diagnostic: CandidateDiagnostics) -> None:
    ticker = diagnostic.ticker
    metrics = diagnostic.metrics
    print(f"Explain: {diagnostic.symbol}")
    print("\nRaw ticker fields:")
    print(json.dumps(ticker.raw or {}, ensure_ascii=False, indent=2, sort_keys=True))
    normalized = asdict(ticker)
    normalized.pop("raw", None)
    print("\nNormalized ticker fields:")
    print(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True))
    for interval in ["1", "5", "15", "60", "240"]:
        _print_candle_rows(f"Recent {interval}m candles used for metrics" if interval != "240" else "Recent 4H candles used for breakout", diagnostic.candles.get(interval, []))
    oi_current = diagnostic.oi_history[-1][1] if diagnostic.oi_history else None
    oi_previous = diagnostic.oi_history[-4][1] if len(diagnostic.oi_history) >= 4 else None
    print("\nComputed metrics:")
    print(f"  price_change_5m: {fmt_pct(metrics.price_change_5m)}")
    print(f"  price_change_15m: {fmt_pct(metrics.price_change_15m)}")
    print(f"  price_change_1h: {fmt_pct(metrics.price_change_1h)}")
    print(f"  price_change_24h: {fmt_pct(metrics.price_change_24h)}")
    print(f"  volume_spike_15m: {metrics.volume_spike_15m if metrics.volume_spike_15m is not None else 'n/a'}")
    print(f"  OI current: {oi_current if oi_current is not None else 'n/a'}")
    print(f"  OI previous(15m): {oi_previous if oi_previous is not None else 'n/a'}")
    print(f"  OI 15m change: {fmt_pct(metrics.oi_change_15m_pct)}")
    print(f"  funding: {ticker.funding_rate * 100:.4f}%" if ticker.funding_rate is not None else "  funding: n/a")
    print(f"  turnover_24h USD liquidity: {fmt_money(ticker.turnover_24h)}")
    print(f"  base volume_24h: {ticker.volume_24h if ticker.volume_24h is not None else 'n/a'}")
    print("\nBTC filter values:")
    print(f"  BTC 15m: {fmt_pct(metrics.btc_change_15m)}")
    print(f"  BTC 1h: {fmt_pct(metrics.btc_change_1h)}")
    print(f"  BTC 4h: {fmt_pct(metrics.btc_change_4h)}")
    print("\n4H breakout:")
    breakout = diagnostic.breakout
    if breakout and breakout.resistance_zone:
        zone = breakout.resistance_zone
        print(f"  resistance zone: {zone.zone_low:.8g}-{zone.zone_high:.8g} touches={zone.touches}")
    else:
        print("  resistance zone: n/a")
    print(f"  breakout status: {diagnostic.state}")
    print("\nRSI:")
    print(f"  15m: {metrics.rsi_15m if metrics.rsi_15m is not None else 'n/a'}")
    print(f"  1h: {metrics.rsi_1h if metrics.rsi_1h is not None else 'n/a'}")
    print(f"  4h: {metrics.rsi_4h if metrics.rsi_4h is not None else 'n/a'}")
    print("\nScore components:")
    for name, value in diagnostic.scores.items():
        print(f"  {name}: {value}")
    print(f"  risk_penalty: {diagnostic.risk_penalty}")
    print(f"  total_score: {diagnostic.score}")
    if diagnostic.setup:
        setup = diagnostic.setup
        print("\nSetup quality:")
        print(f"  room_to_target_pct: {fmt_pct(setup.room_to_target_pct)}")
        print(f"  estimated_rr: {setup.estimated_rr if setup.estimated_rr is not None else 'n/a'}")
        print(f"  target_zone: {setup.target_zone_low if setup.target_zone_low is not None else 'n/a'}-{setup.target_zone_high if setup.target_zone_high is not None else 'n/a'}")
        print(f"  invalidation_reference: {setup.invalidation_price if setup.invalidation_price is not None else 'n/a'}")
        print(f"  chase_risk: {setup.chase_risk}")
    print(f"\nReasons: {diagnostic.reasons or ['none']}")
    print(f"Warnings: {diagnostic.warnings or ['none']}")
    print(f"Final level: {diagnostic.level}")
    if diagnostic.level == "NO_SIGNAL" or diagnostic.rejection_reasons:
        print(f"Exact no-signal/rejection reason: {'; '.join(diagnostic.rejection_reasons or ['score below WATCH'])}")
