from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.scanner.replay import ReplayReport, parse_replay_datetime
from app.utils.time import utc_iso_from_ms

LEVEL_RANK = {"NO_SIGNAL": 0, "WATCH": 1, "HOT": 2, "BREAKOUT_HOT": 3, "VERY_HOT": 4}


@dataclass(slots=True)
class ReplayCase:
    case_id: str
    exchange: str
    symbol: str
    start: str
    end: str
    expected: dict[str, Any] = field(default_factory=dict)

    @property
    def start_ms(self) -> int:
        return parse_replay_datetime(self.start)

    @property
    def end_ms(self) -> int:
        return parse_replay_datetime(self.end)


@dataclass(slots=True)
class ReplayCaseResult:
    case: ReplayCase
    report: ReplayReport | None = None
    error: str | None = None


def load_replay_cases(path: str | Path) -> list[ReplayCase]:
    case_path = Path(path)
    with case_path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    raw_cases = loaded.get("cases", loaded) if isinstance(loaded, dict) else loaded
    if isinstance(raw_cases, dict):
        raw_cases = [{"id": case_id, **case_data} for case_id, case_data in raw_cases.items()]
    if not isinstance(raw_cases, list):
        raise ValueError("Replay casebook must contain a list under 'cases'")
    cases: list[ReplayCase] = []
    for index, raw in enumerate(raw_cases, start=1):
        case_id = str(raw.get("id") or raw.get("case_id") or f"CASE_{index}")
        cases.append(
            ReplayCase(
                case_id=case_id,
                exchange=str(raw.get("exchange", "bybit")).lower(),
                symbol=str(raw["symbol"]).upper(),
                start=str(raw["start"]),
                end=str(raw["end"]),
                expected=dict(raw.get("expected") or {}),
            )
        )
    return cases


def case_passed(result: ReplayCaseResult) -> bool:
    if result.error or not result.report:
        return False
    expected = result.case.expected
    min_level = str(expected.get("min_level", "WATCH"))
    actual_level = result.report.first_signal.level if result.report.first_signal else "NO_SIGNAL"
    if LEVEL_RANK.get(actual_level, 0) < LEVEL_RANK.get(min_level, 1):
        return False
    expected_phase = expected.get("phase")
    if expected_phase and result.report.breakout_phase != expected_phase:
        return False
    return True


def _first_signal_summary(report: ReplayReport | None) -> str:
    if not report or not report.first_signal:
        if report and report.steps:
            best = max(report.steps, key=lambda step: step.score)
            return f"NO_SIGNAL best={best.symbol} {best.score}/{best.level} state={best.state}"
        return "NO_SIGNAL"
    signal = report.first_signal
    return (
        f"{signal.level} score={signal.score} "
        f"time={utc_iso_from_ms(signal.timestamp_ms)} price={signal.ticker.last_price:.8g} "
        f"phase={report.breakout_phase}"
    )


def format_replay_cases_report(results: list[ReplayCaseResult]) -> str:
    lines = ["Replay casebook report", f"Cases: {len(results)}"]
    for result in results:
        status = "PASS" if case_passed(result) else "FAIL"
        if result.error:
            lines.append(f"- {result.case.case_id}: {status} error={result.error}")
            continue
        lines.append(f"- {result.case.case_id}: {status} {_first_signal_summary(result.report)}")
    return "\n".join(lines)


def format_calibration_report(results: list[ReplayCaseResult], profile: str, watch_threshold: int | None = None) -> str:
    passed = sum(1 for result in results if case_passed(result))
    lines = [
        "Calibration report",
        f"Profile: {profile}",
        f"Cases passed: {passed}/{len(results)}",
    ]
    for result in results:
        status = "PASS" if case_passed(result) else "FAIL"
        lines.append(f"- {result.case.case_id}: {status} {_first_signal_summary(result.report)}")
        if result.report and not result.report.first_signal and result.report.steps:
            best = max(result.report.steps, key=lambda step: step.score)
            if watch_threshold is not None:
                lines.append(f"  near miss gap to WATCH: {max(watch_threshold - best.score, 0)}")
            lines.append(f"  best reasons: {best.reasons}")
            lines.append(f"  best warnings: {best.warnings}")
    return "\n".join(lines)
