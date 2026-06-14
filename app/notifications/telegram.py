from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from app.storage.models import SignalCandidate
from app.utils.numbers import fmt_money, fmt_pct

LOGGER = logging.getLogger(__name__)


LEVEL_LABELS = {
    "WATCH": "наблюдение",
    "HOT": "горячий",
    "BREAKOUT_HOT": "пробой горячий",
    "VERY_HOT": "очень горячий",
    "NO_SIGNAL": "нет сигнала",
}

LEVEL_EMOJIS = {
    "WATCH": "👀",
    "HOT": "🔥",
    "BREAKOUT_HOT": "🚀",
    "VERY_HOT": "🚨",
}

STATE_LABELS = {
    "APPROACHING_RESISTANCE": "подход к сопротивлению",
    "TESTING_RESISTANCE": "тест сопротивления",
    "FRESH_BREAKOUT": "свежий пробой",
    "CONFIRMED_BREAKOUT": "подтвержденный пробой",
    "RETEST_HELD": "ретест удержан",
    "FAILED_BREAKOUT": "ложный пробой",
    "OVEREXTENDED_AFTER_BREAKOUT": "поздно после пробоя",
    "NO_BREAKOUT": "без пробоя",
    "UNKNOWN": "неизвестно",
}

CHASE_LABELS = {
    "LOW": "низкий",
    "MEDIUM": "средний",
    "HIGH": "высокий",
    "UNKNOWN": "неизвестно",
}

TEXT_TRANSLATIONS = {
    "fresh 4H breakout": "свежий 4H пробой",
    "confirmed 4H breakout": "подтвержденный 4H пробой",
    "volume confirmed breakout": "пробой подтвержден объемом",
    "breakout candle volume confirmed": "объем свечи пробоя подтвержден",
    "room-to-run exists": "есть запас хода до цели",
    "R/R acceptable": "R/R приемлемый",
    "funding not overheated": "funding не перегрет",
    "upgraded to WATCH by breakout rule": "повышен до WATCH правилом пробоя",
    "active Bybit turnover rank": "активный оборот Bybit",
    "top Bybit turnover rank": "топ оборота Bybit",
    "15m volume spike": "всплеск объема 15м",
    "15m turnover spike": "всплеск оборота 15м",
    "5m momentum": "импульс 5м",
    "15m momentum": "импульс 15м",
    "1h momentum": "импульс 1ч",
    "4h momentum": "импульс 4ч",
    "OI 15m rising": "OI 15м растет",
    "OI 1h rising": "OI 1ч растет",
    "negative funding": "отрицательный funding",
    "score below regular WATCH threshold, upgraded by breakout rule": "score ниже обычного WATCH, повышен правилом пробоя",
    "historical activity rank unavailable in replay": "исторический activity rank недоступен в replay",
    "HIGH chase risk - open chart / check setup, do not chase": "высокий chase-risk: не гнаться, открыть график / проверить сетап",
    "medium chase risk - wait for clean level/retest if needed": "средний chase-risk: лучше ждать чистый уровень/ретест",
    "price overextended above breakout zone": "цена далеко над зоной пробоя",
    "weak estimated R/R": "слабый R/R",
    "RSI hot": "RSI горячий",
    "already_above_breakout": "цена уже выше зоны пробоя",
    "breakout_continuation": "продолжение после пробоя",
    "clean_retest": "чистый ретест",
}

SCORE_LABELS = {
    "activity": "активность",
    "price_acceleration": "импульс",
    "volume_spike": "объем",
    "open_interest": "OI",
    "funding": "funding",
    "btc_background": "BTC фон",
    "breakout": "пробой",
    "setup_quality": "сетап",
}


def _translate(value: str | None) -> str:
    if not value:
        return "n/a"
    exact = TEXT_TRANSLATIONS.get(value)
    if exact:
        return exact
    lowered = value.lower()
    for source, target in TEXT_TRANSLATIONS.items():
        if source.lower() in lowered:
            return target
    return value


def _label(mapping: dict[str, str], value: str | None) -> str:
    if not value:
        return "n/a"
    return mapping.get(str(value).upper(), str(value))


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(float(value))
    if abs_value >= 100:
        return f"{value:.2f}"
    if abs_value >= 1:
        return f"{value:.4f}"
    if abs_value >= 0.01:
        return f"{value:.5f}"
    return f"{value:.8g}"


def _fmt_x(value: float | None) -> str:
    return f"x{value:.2f}" if value is not None else "n/a"


def _fmt_funding(value: float | None) -> str:
    return f"{value * 100:+.3f}%" if value is not None else "n/a"


def _fmt_rr(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _previous_value(previous_state: dict[str, Any] | None, *names: str) -> Any:
    if not previous_state:
        return None
    for name in names:
        value = previous_state.get(name)
        if value is not None:
            return value
    return None


def _pct_delta(current: float | None, previous: Any) -> str | None:
    previous_value = _to_float(previous)
    if current is None or previous_value is None or previous_value == 0:
        return None
    return fmt_pct((float(current) - previous_value) / abs(previous_value) * 100)


def _point_delta(current: float | None, previous: Any, suffix: str = " п.п.") -> str | None:
    previous_value = _to_float(previous)
    if current is None or previous_value is None:
        return None
    delta = current - previous_value
    if abs(delta) < 0.005:
        return None
    return f"{delta:+.2f}{suffix}"


def _state_label(value: str | None) -> str:
    return _label(STATE_LABELS, value)


def _signal_price(signal: SignalCandidate) -> float | None:
    if signal.breakout:
        return signal.breakout.current_price
    if signal.setup:
        return signal.setup.current_price
    return None


def _score_components(signal: SignalCandidate) -> str | None:
    if not signal.scores:
        return None
    parts = []
    for name, value in signal.scores.items():
        label = SCORE_LABELS.get(name, name.replace("_", " "))
        parts.append(f"{html.escape(label)} {value:+d}")
    return " · ".join(parts[:8])


def _repeat_dynamics(signal: SignalCandidate, previous_state: dict[str, Any] | None) -> list[str]:
    if not previous_state:
        return []
    metrics = signal.metrics
    current_price = _signal_price(signal)
    lines: list[str] = []

    previous_score = _previous_value(previous_state, "score", "last_score")
    if previous_score is not None:
        score_delta = signal.score - int(previous_score)
        lines.append(f"Score: {score_delta:+d} ({int(previous_score)}→{signal.score})")

    price_delta = _pct_delta(current_price, _previous_value(previous_state, "price", "last_price"))
    if price_delta:
        lines.append(f"Цена: {price_delta}")

    turnover_delta = _pct_delta(metrics.turnover_24h, _previous_value(previous_state, "turnover_24h"))
    if turnover_delta:
        lines.append(f"Оборот 24ч: {turnover_delta}")

    previous_spike = _to_float(_previous_value(previous_state, "volume_spike_15m"))
    if metrics.volume_spike_15m is not None and previous_spike is not None:
        lines.append(f"Объем 15м: x{previous_spike:.2f}→x{metrics.volume_spike_15m:.2f}")

    oi_delta = _point_delta(metrics.oi_change_15m_pct, _previous_value(previous_state, "oi_change_15m_pct"))
    if oi_delta:
        lines.append(f"OI 15м: {oi_delta}")

    previous_state_name = _previous_value(previous_state, "breakout_state", "state")
    if previous_state_name and str(previous_state_name) != signal.state:
        lines.append(f"4H: {_state_label(str(previous_state_name))} → {_state_label(signal.state)}")

    previous_target = _to_float(_previous_value(previous_state, "target_reference"))
    current_target = signal.setup.target_zone_low if signal.setup else None
    if current_target is not None and previous_target is not None and abs(current_target - previous_target) > 1e-12:
        lines.append(f"Цель: {_fmt_price(previous_target)}→{_fmt_price(current_target)}")

    previous_invalidation = _to_float(_previous_value(previous_state, "invalidation_reference"))
    current_invalidation = signal.setup.invalidation_price if signal.setup else None
    if current_invalidation is not None and previous_invalidation is not None and abs(current_invalidation - previous_invalidation) > 1e-12:
        lines.append(f"Инвалидация: {_fmt_price(previous_invalidation)}→{_fmt_price(current_invalidation)}")

    return lines[:6]


def format_signal(signal: SignalCandidate, previous_state: dict[str, Any] | None = None) -> str:
    metrics = signal.metrics
    breakout = signal.breakout
    setup = signal.setup
    level_label = _label(LEVEL_LABELS, signal.level)
    level_emoji = LEVEL_EMOJIS.get(signal.level, "🔔")
    current_price = _signal_price(signal)
    lines: list[str] = [
        f"{level_emoji} <b>Long-Bot · {html.escape(signal.level)} ({html.escape(level_label)})</b>",
        f"<code>{html.escape(signal.symbol)}</code> · {html.escape(signal.exchange.title())} Futures · <b>{signal.score}/100</b> · грейд {html.escape(signal.grade)}",
        "",
        f"💵 <b>Цена</b>: <code>{_fmt_price(current_price)}</code>",
        f"5м: {fmt_pct(metrics.price_change_5m)}",
        f"15м: {fmt_pct(metrics.price_change_15m)}",
        f"1ч: {fmt_pct(metrics.price_change_1h)}",
        f"4ч: {fmt_pct(metrics.price_change_4h)}",
        f"24ч: {fmt_pct(metrics.price_change_24h)}",
        "",
        "📊 <b>Активность</b>",
        (
            f"Оборот 24ч: {fmt_money(metrics.turnover_24h)} · "
            f"место #{metrics.turnover_rank_24h}" if metrics.turnover_rank_24h else f"Оборот 24ч: {fmt_money(metrics.turnover_24h)} · место n/a"
        ),
        f"Объем 15м: {_fmt_x(metrics.volume_spike_15m)} · всплеск оборота: {_fmt_x(metrics.turnover_spike_15m)}",
        "",
        "🧲 <b>Деривативы</b>",
        f"OI 15м: {fmt_pct(metrics.oi_change_15m_pct)}",
        f"OI 1ч: {fmt_pct(metrics.oi_change_1h_pct)}",
        f"Фандинг: {_fmt_funding(metrics.funding_rate)}",
        f"BTC 15м: {fmt_pct(metrics.btc_change_15m)}",
        f"BTC 1ч: {fmt_pct(metrics.btc_change_1h)}",
        f"BTC 4ч: {fmt_pct(metrics.btc_change_4h)}",
    ]
    if breakout:
        lines.extend(["", "📈 <b>График 4H</b>"])
        if breakout.resistance_zone:
            zone = breakout.resistance_zone
            lines.append(f"Сопротивление: <code>{_fmt_price(zone.zone_low)}-{_fmt_price(zone.zone_high)}</code>")
        lines.extend(
            [
                f"Статус: <b>{html.escape(_state_label(breakout.state))}</b>",
                f"Дистанция над зоной: {fmt_pct(breakout.distance_above_zone_pct)}",
                (
                    "RSI: "
                    f"15м {metrics.rsi_15m:.1f}" if metrics.rsi_15m is not None else "RSI: 15м n/a"
                )
                + (f" · 1ч {metrics.rsi_1h:.1f}" if metrics.rsi_1h is not None else " · 1ч n/a")
                + (f" · 4ч {metrics.rsi_4h:.1f}" if metrics.rsi_4h is not None else " · 4ч n/a"),
            ]
        )
    if setup:
        lines.extend(
            [
                "",
                "🎯 <b>План проверки</b>",
                f"Контекст: {html.escape(_translate(setup.entry_context))}",
                f"Инвалидация: <code>{_fmt_price(setup.invalidation_price)}</code>",
                (
                    f"Цель: <code>{_fmt_price(setup.target_zone_low)}-{_fmt_price(setup.target_zone_high)}</code>"
                    if setup.target_zone_low and setup.target_zone_high
                    else "Цель: n/a"
                ),
                (
                    f"Запас до цели {fmt_pct(setup.room_to_target_pct)} · "
                    f"R/R {_fmt_rr(setup.estimated_rr)} · "
                    f"риск догонять {_label(CHASE_LABELS, setup.chase_risk)}"
                ),
            ]
        )

    dynamics = _repeat_dynamics(signal, previous_state)
    if dynamics:
        lines.extend(["", "🔁 <b>Динамика с прошлого сигнала</b>"])
        lines.extend(html.escape(line) for line in dynamics)

    components = _score_components(signal)
    if components:
        lines.extend(["", f"🧮 <b>Компоненты score</b>: {components}"])

    if signal.reasons:
        lines.extend(["", "✅ <b>Почему сигнал</b>"])
        lines.extend(f"• {html.escape(_translate(reason))}" for reason in signal.reasons[:5])
    if signal.warnings:
        lines.extend(["", "⚠️ <b>Риски</b>"])
        lines.extend(f"• {html.escape(_translate(warning))}" for warning in signal.warnings[:5])
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, parse_mode: str = "HTML"):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.parse_mode = parse_mode

    async def send(self, signal: SignalCandidate, previous_state: dict[str, Any] | None = None) -> bool:
        return await self.send_text(format_signal(signal, previous_state))

    async def send_text(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            LOGGER.warning("Telegram token/chat id missing; alert not sent")
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True
