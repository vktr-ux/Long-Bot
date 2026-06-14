from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.storage.models import SymbolInfo
from app.utils.numbers import clamp


@dataclass(slots=True)
class RiskPlan:
    allowed: bool
    reason: str | None
    balance_usdt: float
    entry_price: float
    qty: float
    notional_usdt: float
    margin_usdt: float
    leverage: float
    leverage_source: str
    initial_sl_pct: float
    cost_pct: float
    max_notional_by_loss: float
    min_notional: float
    step_size: float

    def to_dict(self) -> dict:
        return asdict(self)


def floor_to_step(value: float, step: float | None) -> float:
    if value <= 0:
        return 0.0
    if not step or step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(math.floor(value / step) * step, precision + 2)


def initial_stop_pct(spread_pct: float | None, atr_1m_pct: float | None, paper_cfg: dict | None = None) -> float:
    cfg = paper_cfg or {}
    min_pct = float(cfg.get("initial_sl_pct_min", 0.45))
    max_pct = float(cfg.get("initial_sl_pct_max", 1.10))
    spread_mult = float(cfg.get("initial_sl_spread_multiplier", 1.5))
    atr_mult = float(cfg.get("initial_sl_atr_multiplier", 0.35))
    spread_component = spread_mult * (spread_pct or 0)
    atr_component = atr_mult * (atr_1m_pct or 0)
    return float(clamp(max(min_pct, spread_component, atr_component), min_pct, max_pct))


def roundtrip_cost_fraction(paper_cfg: dict) -> float:
    fee_rate = float(paper_cfg.get("fee_rate_taker", 0.0004))
    entry_slippage = float(paper_cfg.get("entry_slippage_bps", 3)) / 10_000
    exit_slippage = float(paper_cfg.get("exit_slippage_bps", 5)) / 10_000
    return fee_rate * 2 + entry_slippage + exit_slippage


def choose_leverage(spread_pct: float | None, score: int, initial_sl_pct_value: float, paper_cfg: dict) -> float:
    max_leverage = float(paper_cfg.get("max_leverage", 10))
    default_leverage = float(paper_cfg.get("default_leverage", 5))
    spread = spread_pct if spread_pct is not None else 999
    if spread <= 0.08 and score >= 82 and initial_sl_pct_value <= 0.80:
        chosen = min(max_leverage, 12)
    elif spread <= 0.10 and score >= 75:
        chosen = min(max_leverage, 10)
    elif spread <= 0.15 and score >= 65:
        chosen = min(max_leverage, 8)
    else:
        chosen = min(default_leverage, 8)
    return min(max_leverage, chosen)


def build_risk_plan(
    *,
    balance_usdt: float,
    entry_price: float,
    score: int,
    spread_pct: float | None,
    atr_1m_pct: float | None,
    symbol_info: SymbolInfo | None,
    paper_cfg: dict,
) -> RiskPlan:
    sl_pct = initial_stop_pct(spread_pct, atr_1m_pct, paper_cfg)
    cost_fraction = roundtrip_cost_fraction(paper_cfg)
    max_loss = float(paper_cfg.get("max_loss_per_trade_usdt", 0.20))
    max_notional_by_loss = max_loss / (sl_pct / 100 + cost_fraction)
    leverage = choose_leverage(spread_pct, score, sl_pct, paper_cfg)
    desired_margin = min(
        float(paper_cfg.get("max_position_margin_usdt", 2.0)),
        balance_usdt * float(paper_cfg.get("max_account_fraction_as_margin", 0.12)),
    )
    raw_notional = min(desired_margin * leverage, max_notional_by_loss)
    min_notional = float((symbol_info.min_notional if symbol_info and symbol_info.min_notional else None) or paper_cfg.get("fallback_min_notional_usdt", 5.0))
    step_size = float(
        (symbol_info.market_step_size if symbol_info and symbol_info.market_step_size else None)
        or (symbol_info.step_size if symbol_info and symbol_info.step_size else None)
        or paper_cfg.get("fallback_step_size", 0.001)
    )
    if entry_price <= 0:
        return RiskPlan(False, "entry price unavailable", balance_usdt, entry_price, 0, 0, 0, leverage, "assumed", sl_pct, cost_fraction * 100, max_notional_by_loss, min_notional, step_size)
    qty = floor_to_step(raw_notional / entry_price, step_size)
    notional = qty * entry_price
    if notional < min_notional * 1.02:
        return RiskPlan(False, "notional below exchange minNotional buffer", balance_usdt, entry_price, qty, notional, 0, leverage, "assumed", sl_pct, cost_fraction * 100, max_notional_by_loss, min_notional, step_size)
    margin = notional / leverage if leverage else 0
    return RiskPlan(True, None, balance_usdt, entry_price, qty, notional, margin, leverage, "assumed", sl_pct, cost_fraction * 100, max_notional_by_loss, min_notional, step_size)
