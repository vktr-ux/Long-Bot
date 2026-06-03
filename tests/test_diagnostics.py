from app.config import DEFAULT_CONFIG
from app.scanner.diagnostics import print_diagnostic_report, print_explain, score_distribution
from app.scanner.scoring import score_signal
from app.scanner.signals import ScanEngine
from app.storage.models import CandidateDiagnostics, Metrics, ScanResult, TickerSnapshot


def minimal_diag(symbol="AAAUSDT", score=0, level="NO_SIGNAL"):
    ticker = TickerSnapshot(1, "bybit", symbol, 1, turnover_24h=None, volume_24h=None)
    metrics = Metrics("bybit", symbol, 1)
    return CandidateDiagnostics(
        timestamp_ms=1,
        exchange="bybit",
        symbol=symbol,
        ticker=ticker,
        metrics=metrics,
        score=score,
        level=level,
        signal_type="MOMENTUM_WATCH",
        state="NO_BREAKOUT",
        scores={},
        risk_penalty=0,
        reasons=[],
        warnings=[],
        filter_stage_passed="failed_score",
        rejection_reasons=["score below WATCH"],
    )


def test_diagnostic_output_does_not_crash_with_none_fields(capsys):
    diag = minimal_diag()
    result = ScanResult(
        symbols_total=1,
        tickers_total=1,
        symbols_scanned=1,
        enriched_count=1,
        symbols=[],
        tickers=[],
        enriched_tickers=[],
        diagnostics=[diag],
        signals=[],
        rejected={"AAAUSDT": ["score below WATCH"]},
        stage_counts={"enriched": 1},
    )
    print_diagnostic_report(result, top=30)
    captured = capsys.readouterr()
    assert "Diagnostic summary" in captured.out
    assert score_distribution([diag])["counts"]["NO_SIGNAL"] == 1


def test_explain_output_does_not_crash_with_none_fields(capsys):
    print_explain(minimal_diag())
    captured = capsys.readouterr()
    assert "Final level: NO_SIGNAL" in captured.out


def test_rejected_candidate_reasons_from_scan_engine():
    engine = ScanEngine(connector=None, config=DEFAULT_CONFIG)
    ticker = TickerSnapshot(1, "bybit", "AAAUSDT", 1, turnover_24h=100, spread_pct=0.5, turnover_rank_24h=999)
    metrics = Metrics("bybit", "AAAUSDT", 1)
    reasons, checks = engine._filter_reasons(ticker, metrics)
    assert "24h turnover below threshold" in reasons
    assert "spread too wide" in reasons
    assert "no activity/momentum trigger" in reasons
    assert not checks["liquidity"]


def test_score_component_explanations_present():
    ticker = TickerSnapshot(1, "bybit", "AAAUSDT", 1, turnover_24h=50_000_000, turnover_rank_24h=1, open_interest_value=10_000_000, funding_rate=0.0001)
    metrics = Metrics(
        "bybit",
        "AAAUSDT",
        1,
        price_change_5m=3,
        price_change_15m=5,
        price_change_1h=9,
        volume_spike_15m=3,
        turnover_spike_15m=3,
        oi_change_15m_pct=4,
        oi_change_1h_pct=9,
        funding_rate=0.0001,
        turnover_rank_24h=1,
    )
    total, level, _sig_type, scores, _risk, reasons, warnings, _grade, _label = score_signal(
        ticker,
        metrics,
        breakout=None,
        setup=None,
        chart_score_tuple=(0, [], [], 0),
        rsi_tuple=([], 0),
        config=DEFAULT_CONFIG,
    )
    assert total > 0
    assert level in {"WATCH", "HOT"}
    assert scores["activity"] > 0
    assert any("momentum" in reason for reason in reasons)
    assert warnings
