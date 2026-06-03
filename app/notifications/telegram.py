from __future__ import annotations

import html
import logging

import httpx

from app.storage.models import SignalCandidate
from app.utils.numbers import fmt_money, fmt_pct

LOGGER = logging.getLogger(__name__)


def _line(name: str, value: object) -> str:
    return f"{name}: {value}"


def format_signal(signal: SignalCandidate) -> str:
    metrics = signal.metrics
    breakout = signal.breakout
    setup = signal.setup
    lines: list[str] = [
        f"LONG SCANNER / {html.escape(signal.level)}",
        "",
        _line("Symbol", html.escape(signal.symbol)),
        _line("Exchange", "Bybit Futures"),
        _line("Score", f"{signal.score}/100"),
        _line("Grade", signal.grade),
        _line("Review label", signal.review_label),
        _line("State", html.escape(signal.state)),
        "",
        "Activity:",
        _line("Bybit turnover rank", f"#{metrics.turnover_rank_24h}" if metrics.turnover_rank_24h else "n/a"),
        _line("24h turnover", fmt_money(metrics.turnover_24h)),
        _line("15m volume spike", f"x{metrics.volume_spike_15m:.2f}" if metrics.volume_spike_15m else "n/a"),
        "",
        "Price:",
        _line("5m", fmt_pct(metrics.price_change_5m)),
        _line("15m", fmt_pct(metrics.price_change_15m)),
        _line("1h", fmt_pct(metrics.price_change_1h)),
        _line("4h", fmt_pct(metrics.price_change_4h)),
        _line("24h", fmt_pct(metrics.price_change_24h)),
        "",
        "Derivatives:",
        _line("OI 15m", fmt_pct(metrics.oi_change_15m_pct)),
        _line("OI 1h", fmt_pct(metrics.oi_change_1h_pct)),
        _line("Funding", f"{metrics.funding_rate * 100:.3f}%" if metrics.funding_rate is not None else "n/a"),
    ]
    if breakout:
        lines.extend(["", "Chart:"])
        if breakout.resistance_zone:
            zone = breakout.resistance_zone
            lines.append(_line("4H resistance", f"{zone.zone_low:.8g}-{zone.zone_high:.8g}"))
        lines.extend(
            [
                _line("Current price", f"{breakout.current_price:.8g}"),
                _line("Breakout distance", fmt_pct(breakout.distance_above_zone_pct)),
                _line("RSI 4H", f"{metrics.rsi_4h:.1f}" if metrics.rsi_4h is not None else "n/a"),
            ]
        )
    if setup:
        lines.extend(
            [
                "",
                "Setup quality:",
                _line("Entry context", setup.entry_context),
                _line("Invalidation reference", f"{setup.invalidation_price:.8g}" if setup.invalidation_price else "n/a"),
                _line("Target reference zone", f"{setup.target_zone_low:.8g}-{setup.target_zone_high:.8g}" if setup.target_zone_low and setup.target_zone_high else "n/a"),
                _line("Room to target", fmt_pct(setup.room_to_target_pct)),
                _line("Estimated R/R", f"{setup.estimated_rr:.2f}" if setup.estimated_rr else "n/a"),
                _line("Chase risk", setup.chase_risk),
            ]
        )
    lines.extend(["", "Scores:"])
    lines.extend([f"{name}: {value}" for name, value in signal.scores.items()])
    if signal.reasons:
        lines.extend(["", "Reasons:"])
        lines.extend(f"- {html.escape(reason)}" for reason in signal.reasons[:10])
    if signal.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {html.escape(warning)}" for warning in signal.warnings[:10])
    lines.extend(["", "Action:", "This is not a BUY signal. Open chart / check setup manually."])
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, parse_mode: str = "HTML"):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.parse_mode = parse_mode

    async def send(self, signal: SignalCandidate) -> bool:
        if not self.bot_token or not self.chat_id:
            LOGGER.warning("Telegram token/chat id missing; alert not sent")
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": format_signal(signal),
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True

