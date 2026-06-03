from app.scanner.rsi import calculate_rsi, rsi_warnings
from app.storage.models import Candle


def test_rsi_calculation_and_warning():
    candles = [
        Candle(i, "bybit", "AAAUSDT", "15", i, i + 1, i - 1, float(i), 1, 1)
        for i in range(1, 40)
    ]
    rsi = calculate_rsi(candles, 14)
    assert rsi == 100.0
    warnings, penalty = rsi_warnings(rsi, rsi, rsi, {"warning_15m": 80, "warning_1h": 80, "warning_4h": 80, "danger_15m": 85, "danger_1h": 85, "danger_4h": 80})
    assert len(warnings) == 3
    assert penalty == 18

