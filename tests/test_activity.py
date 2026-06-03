from app.scanner.activity import activity_score, rank_tickers
from app.storage.models import TickerSnapshot


def test_turnover_rank_and_activity_score():
    tickers = [
        TickerSnapshot(1, "bybit", "AAAUSDT", 1, turnover_24h=10, volume_24h=1),
        TickerSnapshot(1, "bybit", "BBBUSDT", 1, turnover_24h=30, volume_24h=3),
        TickerSnapshot(1, "bybit", "CCCUSDT", 1, turnover_24h=20, volume_24h=2),
    ]
    ranked = rank_tickers(tickers)
    assert {t.symbol: t.turnover_rank_24h for t in ranked}["BBBUSDT"] == 1
    score, reasons = activity_score(1, 3.1, 2.1)
    assert score == 18
    assert any("top Bybit" in reason for reason in reasons)

