from app.config import DEFAULT_CONFIG
from app.scanner.breakout import build_setup_plan, cluster_resistance_zones, detect_breakout
from app.storage.models import Candle


def make_breakout_candles():
    candles = []
    price = 90.0
    for i in range(80):
        high = 100.0 if i in {10, 25, 45, 65} else price + 2
        low = price - 2
        close = min(high - 1, price + 0.5)
        candles.append(Candle(i, "bybit", "AAAUSDT", "240", price, high, low, close, 100, 1000))
        price += 0.03
    candles.append(Candle(81, "bybit", "AAAUSDT", "240", 99, 104, 98, 103, 300, 3000))
    return candles


def test_resistance_clustering_and_fresh_breakout_setup():
    cfg = DEFAULT_CONFIG["breakout"]
    candles = make_breakout_candles()
    zones = cluster_resistance_zones(candles[:-1], cfg)
    assert zones
    context = detect_breakout(candles, 103.0, cfg)
    assert context.state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT"}
    setup = build_setup_plan("bybit", "AAAUSDT", context, candles, DEFAULT_CONFIG["setup_quality"])
    assert setup.invalidation_price is not None
    assert setup.room_to_target_pct is not None

