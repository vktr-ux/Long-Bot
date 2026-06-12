from app.notifications.telegram import format_signal
from app.scanner.state import signal_notification_snapshot
from app.storage.models import BreakoutContext, Metrics, ResistanceZone, SetupPlan, SignalCandidate


def make_signal(score=62, price=105.0, state="CONFIRMED_BREAKOUT", turnover=12_500_000, volume_spike=3.4):
    ts = 1_000_000
    metrics = Metrics(
        exchange="bybit",
        symbol="AAAUSDT",
        timestamp_ms=ts,
        price_change_5m=1.2,
        price_change_15m=2.8,
        price_change_1h=6.4,
        price_change_4h=11.0,
        price_change_24h=18.2,
        volume_spike_15m=volume_spike,
        turnover_spike_15m=2.6,
        oi_change_15m_pct=1.7,
        oi_change_1h_pct=3.1,
        funding_rate=0.0001,
        turnover_24h=turnover,
        turnover_rank_24h=18,
        btc_change_15m=0.1,
        btc_change_1h=0.4,
        btc_change_4h=1.0,
        rsi_15m=71.2,
        rsi_1h=67.5,
        rsi_4h=64.0,
    )
    zone = ResistanceZone("240", 98.0, 100.0, 99.0, 3, 1, 2, 7.0)
    breakout = BreakoutContext(state, "240", zone, price, distance_above_zone_pct=5.0, volume_confirmed=True)
    setup = SetupPlan(
        "bybit",
        "AAAUSDT",
        "BREAKOUT_CONTINUATION",
        price,
        "already_above_breakout",
        invalidation_price=96.0,
        target_zone_low=120.0,
        target_zone_high=126.0,
        room_to_target_pct=14.2,
        estimated_rr=2.1,
        chase_risk="MEDIUM",
    )
    return SignalCandidate(
        timestamp_ms=ts,
        exchange="bybit",
        symbol="AAAUSDT",
        score=score,
        level="WATCH",
        signal_type="BREAKOUT_WATCH",
        state=state,
        metrics=metrics,
        breakout=breakout,
        setup=setup,
        scores={"activity": 10, "price_acceleration": 18, "volume_spike": 12, "breakout": 15},
        reasons=["fresh 4H breakout", "15m volume spike", "funding not overheated"],
        warnings=["RSI hot"],
        grade="B",
    )


def test_telegram_format_is_compact_russian_html():
    text = format_signal(make_signal())

    assert "<b>Long-Bot" in text
    assert "Открой график / проверь сетап" in text
    assert "📊 <b>Активность</b>" in text
    assert "✅ <b>Почему сигнал</b>" in text
    assert "свежий 4H пробой" in text
    assert "BUY signal" not in text


def test_telegram_format_includes_repeat_dynamics():
    previous = signal_notification_snapshot(
        make_signal(score=52, price=100.0, state="FRESH_BREAKOUT", turnover=10_000_000, volume_spike=2.0),
        sent_at_ms=900_000,
    )

    text = format_signal(make_signal(), previous)

    assert "🔁 <b>Динамика с прошлого сигнала</b>" in text
    assert "Score: +10 (52→62)" in text
    assert "Цена: +5.00%" in text
    assert "Оборот 24ч: +25.00%" in text
    assert "Объем 15м: x2.00→x3.40" in text


def test_telegram_format_handles_none_fields():
    signal = make_signal()
    signal.metrics.volume_spike_15m = None
    signal.metrics.turnover_spike_15m = None
    signal.metrics.rsi_15m = None
    signal.metrics.rsi_1h = None
    signal.metrics.rsi_4h = None
    signal.metrics.funding_rate = None

    text = format_signal(signal, {"score": 60})

    assert "n/a" in text
    assert "Long-Bot" in text
