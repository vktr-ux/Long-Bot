from app.scanner.outcomes import compute_outcome
from app.storage.models import Candle


def test_outcome_mfe_mae_and_threshold_ordering():
    candles = [
        Candle(1, "bybit", "AAAUSDT", "15", 100, 102, 99, 101, 1, 1),
        Candle(2, "bybit", "AAAUSDT", "15", 101, 106, 100, 105, 1, 1),
    ]
    result = compute_outcome(candles, 100, target=105, invalidation=97)
    assert result["mfe_pct"] == 6
    assert result["mae_pct"] == -1
    assert result["target_touched"]
    assert result["plus_3_before_minus_3"]

