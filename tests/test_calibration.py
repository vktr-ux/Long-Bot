from app.scanner.calibration import (
    ReplayCaseResult,
    case_passed,
    format_calibration_report,
    load_replay_cases,
)
from app.scanner.replay import ReplayReport
from app.storage.models import CandidateDiagnostics, Metrics, TickerSnapshot


def test_replay_casebook_parser_works(tmp_path):
    casebook = tmp_path / "cases.yaml"
    casebook.write_text(
        """
cases:
  - id: OPN_JUNE_2026_BREAKOUT
    exchange: bybit
    symbol: OPNUSDT
    start: "2026-06-01 00:00"
    end: "2026-06-04 03:00"
    expected:
      min_level: WATCH
      phase: during_breakout
""".strip(),
        encoding="utf-8",
    )
    cases = load_replay_cases(casebook)
    assert len(cases) == 1
    assert cases[0].case_id == "OPN_JUNE_2026_BREAKOUT"
    assert cases[0].symbol == "OPNUSDT"
    assert cases[0].expected["min_level"] == "WATCH"


def test_calibration_report_works_with_mocked_replay_cases(tmp_path):
    casebook = tmp_path / "cases.yaml"
    casebook.write_text(
        """
cases:
  - id: MOCK_BREAKOUT
    exchange: bybit
    symbol: MOCKUSDT
    start: "2026-06-01 00:00"
    end: "2026-06-04 03:00"
    expected:
      min_level: WATCH
      phase: during_breakout
""".strip(),
        encoding="utf-8",
    )
    case = load_replay_cases(casebook)[0]
    ticker = TickerSnapshot(1, "bybit", "MOCKUSDT", 1.23)
    metrics = Metrics("bybit", "MOCKUSDT", 1)
    signal = CandidateDiagnostics(
        timestamp_ms=1,
        exchange="bybit",
        symbol="MOCKUSDT",
        ticker=ticker,
        metrics=metrics,
        score=47,
        level="WATCH",
        signal_type="BREAKOUT_WATCH",
        state="FRESH_BREAKOUT",
        scores={},
        risk_penalty=0,
        reasons=["upgraded to WATCH by breakout rule"],
        warnings=["historical activity rank unavailable in replay"],
        filter_stage_passed="replay_scored",
    )
    report = ReplayReport(
        symbol="MOCKUSDT",
        exchange="bybit",
        start_ms=1,
        end_ms=2,
        profile="normal",
        first_signal=signal,
        first_breakout_time_ms=1,
        breakout_phase="during_breakout",
        steps=[signal],
    )
    result = ReplayCaseResult(case=case, report=report)
    assert case_passed(result)
    text = format_calibration_report([result], "normal", watch_threshold=50)
    assert "Calibration report" in text
    assert "Cases passed: 1/1" in text
    assert "MOCK_BREAKOUT: PASS" in text
