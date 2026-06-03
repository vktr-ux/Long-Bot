from copy import deepcopy

from app.config import DEFAULT_CONFIG
from app.scanner.replay import build_replay_step, run_replay_on_candles
from app.storage.models import Candle


MINUTE = 60_000


def c(ts, interval, close, volume=100, turnover=None, high=None, low=None, open_=None):
    return Candle(
        timestamp_ms=ts,
        exchange="bybit",
        symbol="MOCKUSDT",
        interval=interval,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        turnover=turnover if turnover is not None else volume * close,
        is_closed=True,
    )


def flat_candles():
    return {
        "1": [c(i * MINUTE, "1", 100) for i in range(260)],
        "5": [c(i * 5 * MINUTE, "5", 100) for i in range(80)],
        "15": [c(i * 15 * MINUTE, "15", 100) for i in range(120)],
        "60": [c(i * 60 * MINUTE, "60", 100) for i in range(80)],
        "240": [c(i * 240 * MINUTE, "240", 100) for i in range(80)],
    }


def breakout_candles(close=108, overheated=False):
    data = flat_candles()
    ts = 119 * 15 * MINUTE
    for i in range(245):
        volume = 10 if i < 230 else 80
        data["1"][i] = c(i * MINUTE, "1", 100 + max(0, i - 230) * 0.7, volume=volume)
    for i in range(70, 80):
        if overheated:
            value = 100 + (i - 70) * 3
        else:
            value = [96, 96, 97, 97, 98, 98, 99, 100, 100, close][i - 70]
        data["5"][i] = c(i * 5 * MINUTE, "5", value)
    for i in range(110, 120):
        if overheated:
            value = 98 + (i - 110) * 3
        else:
            value = [95, 95, 96, 96, 97, 97, 98, 99, 100, close][i - 110]
        data["15"][i] = c(i * 15 * MINUTE, "15", value)
    data["15"][-1] = c(ts, "15", close, volume=900, high=close + 1, low=102, open_=102)
    for i in range(70, 80):
        data["60"][i] = c(i * 60 * MINUTE, "60", 97 + (i - 70) * 1.5)
    four_hour = []
    for i in range(80):
        high = 100 if i in {20, 35, 50, 65} else 96
        close_value = 95
        volume = 100
        if i == 78:
            high = 106
            close_value = 104
            volume = 300
        if i == 79:
            high = close + 1
            close_value = close
            volume = 400
        four_hour.append(c((40 + i) * 15 * MINUTE, "240", close_value, volume=volume, high=high, low=92, open_=95))
    data["240"] = four_hour
    return data


def rising_oi():
    base = 100.0
    start = 115 * 15 * MINUTE - 12 * 5 * MINUTE
    return [(start + i * 5 * MINUTE, base + i * 2) for i in range(13)]


def funding():
    return [{"fundingRateTimestamp": str(110 * 15 * MINUTE), "fundingRate": "0.0001"}]


def test_replay_no_signal_in_flat_market():
    data = flat_candles()
    report = run_replay_on_candles("MOCKUSDT", "bybit", 100 * 15 * MINUTE, 119 * 15 * MINUTE, data, [], [], DEFAULT_CONFIG)
    assert report.first_signal is None
    assert report.breakout_phase == "no_signal"


def test_replay_watch_on_early_momentum():
    data = breakout_candles(close=101)
    step = build_replay_step("MOCKUSDT", "bybit", 119 * 15 * MINUTE, data, rising_oi(), funding(), (None, None, None), DEFAULT_CONFIG)
    assert step is not None
    assert step.level in {"WATCH", "HOT", "BREAKOUT_HOT"}
    assert step.score >= DEFAULT_CONFIG["scoring"]["levels"]["watch"]


def test_replay_breakout_hot_on_resistance_breakout_with_volume_and_oi():
    data = breakout_candles(close=108)
    config = deepcopy(DEFAULT_CONFIG)
    config["rsi"] = {**config["rsi"], "warning_15m": 101, "warning_1h": 101, "warning_4h": 101, "danger_15m": 101, "danger_1h": 101, "danger_4h": 101}
    config["setup_quality"] = {**config["setup_quality"], "target_atr_extension_mult": 5.0}
    step = build_replay_step("MOCKUSDT", "bybit", 119 * 15 * MINUTE, data, rising_oi(), funding(), (None, None, None), config)
    assert step is not None
    assert step.state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT"}
    assert step.level == "BREAKOUT_HOT"


def test_replay_warns_when_rsi_overheated():
    data = breakout_candles(close=140, overheated=True)
    step = build_replay_step("MOCKUSDT", "bybit", 119 * 15 * MINUTE, data, rising_oi(), funding(), (None, None, None), DEFAULT_CONFIG)
    assert step is not None
    assert any("RSI" in warning for warning in step.warnings)


def test_replay_missing_oi_funding_does_not_crash():
    data = breakout_candles(close=108)
    report = run_replay_on_candles("MOCKUSDT", "bybit", 119 * 15 * MINUTE, 119 * 15 * MINUTE, data, [], [], DEFAULT_CONFIG)
    assert report.steps
    assert report.missing_data_notes
    assert any("unavailable" in warning for warning in report.steps[0].warnings)
