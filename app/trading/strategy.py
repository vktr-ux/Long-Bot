from __future__ import annotations

from dataclasses import asdict, dataclass

from app.scanner.breakout import atr
from app.scanner.metrics import closed_candles
from app.storage.models import CandidateDiagnostics, SymbolInfo
from app.trading.classifier import DirectionDecision
from app.trading.risk import RiskPlan, build_risk_plan
from app.utils.numbers import clamp

STRATEGY_VERSION = "paper_scalper_v6"


@dataclass(slots=True)
class TradePlan:
    exchange: str
    symbol: str
    direction: str
    classifier_label: str
    strategy_version: str
    score: int
    entry_price: float
    entry_grid: list[dict]
    initial_sl_price: float
    tp1_price: float
    be_plus_price: float
    be_plus_move_pct: float
    tp1_trigger_pct: float
    trailing_start_pct: float
    trailing_distance_pct: float
    risk: RiskPlan
    reasons: list[str]
    warnings: list[str]
    strategy_config_version: int | None = None
    settings_hash: str | None = None
    settings_json: dict | None = None
    status: str = "planned"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["risk"] = self.risk.to_dict()
        return data


def atr_pct(candles, price: float) -> float | None:
    if price <= 0:
        return None
    value = atr(candles)
    if value is None:
        return None
    return value / price * 100


def last_closed_breakout_price(diagnostic: CandidateDiagnostics, direction: str, buffer_pct: float = 0.0008) -> float:
    candles = closed_candles(diagnostic.candles.get("1", []))
    ticker = diagnostic.ticker
    if not candles:
        return ticker.ask_price if direction == "LONG" and ticker.ask_price else ticker.bid_price if direction == "SHORT" and ticker.bid_price else ticker.last_price
    last = candles[-1]
    if direction == "LONG":
        trigger = last.high * (1 + buffer_pct)
        return max(trigger, ticker.ask_price or ticker.last_price)
    trigger = last.low * (1 - buffer_pct)
    return min(trigger, ticker.bid_price or ticker.last_price)


def build_trade_plan(
    diagnostic: CandidateDiagnostics,
    decision: DirectionDecision,
    symbol_info: SymbolInfo | None,
    balance_usdt: float,
    config: dict,
) -> TradePlan | None:
    if decision.direction not in {"LONG", "SHORT"}:
        return None
    paper_cfg = config.get("paper", {})
    entry_cfg = config.get("entry", {})
    exit_cfg = config.get("exit", {})
    runtime_meta = config.get("runtime_settings", {})
    direction = decision.direction
    ticker = diagnostic.ticker
    entry_price = ticker.ask_price if direction == "LONG" and ticker.ask_price else ticker.bid_price if direction == "SHORT" and ticker.bid_price else ticker.last_price
    ladder_trigger = last_closed_breakout_price(diagnostic, direction)
    trigger_required = bool(entry_cfg.get("require_trigger_confirmation", True)) and entry_cfg.get("mode", "confirmation_ladder") == "confirmation_ladder"
    if trigger_required:
        price_for_risk = max(entry_price, ladder_trigger) if direction == "LONG" else min(entry_price, ladder_trigger)
    else:
        price_for_risk = entry_price if entry_price > 0 else ladder_trigger
    one_min_atr_pct = atr_pct(diagnostic.candles.get("1", []), price_for_risk)
    risk = build_risk_plan(
        balance_usdt=balance_usdt,
        entry_price=price_for_risk,
        score=diagnostic.score,
        spread_pct=ticker.spread_pct,
        atr_1m_pct=one_min_atr_pct,
        symbol_info=symbol_info,
        paper_cfg=paper_cfg,
    )
    reasons = list(decision.reasons)
    warnings = list(decision.warnings)
    if not risk.allowed:
        warnings.append(risk.reason or "risk plan rejected")
    notional = risk.notional_usdt
    leg_weights = list(entry_cfg.get("leg_weights", [0.70, 0.30]))
    max_legs = int(entry_cfg.get("max_legs", len(leg_weights)))
    if not entry_cfg.get("legs_enabled", True):
        leg_weights = [1.0]
    leg_weights = leg_weights[:max_legs]
    entry_grid = []
    for index, fraction in enumerate(leg_weights, start=1):
        entry_grid.append(
            {
                "leg": index,
                "fraction": fraction,
                "notional_usdt": round(notional * fraction, 8),
                "trigger_price": ladder_trigger,
                "condition": "confirmed continuation ladder; never average down" if index > 1 else "confirmed breakout or current price through ladder trigger",
            }
        )
    if direction == "LONG":
        initial_sl_price = price_for_risk * (1 - risk.initial_sl_pct / 100)
    else:
        initial_sl_price = price_for_risk * (1 + risk.initial_sl_pct / 100)
    roundtrip_cost_usdt = notional * (risk.cost_pct / 100)
    min_profit_usdt = float(exit_cfg.get("min_net_profit_after_breakeven_usdt", paper_cfg.get("min_net_profit_after_breakeven_usdt", 0.02)))
    be_plus_move_pct = ((roundtrip_cost_usdt + min_profit_usdt) / notional * 100) if notional else 999
    tp1_trigger_pct = float(
        clamp(
            max(float(exit_cfg.get("tp1_trigger_pct_min", 0.60)), be_plus_move_pct + 0.20),
            float(exit_cfg.get("tp1_trigger_pct_min", 0.60)),
            float(exit_cfg.get("tp1_trigger_pct_max", 1.20)),
        )
    )
    trailing_start_pct = max(float(exit_cfg.get("trailing_start_pct_min", 0.75)), 1.5 * risk.initial_sl_pct)
    trailing_distance_pct = float(
        clamp(
            max(
                float(exit_cfg.get("trailing_distance_pct_min", 0.35)),
                float(exit_cfg.get("trailing_spread_multiplier", 2.0)) * (ticker.spread_pct or 0),
                float(exit_cfg.get("trailing_atr_multiplier", 0.40)) * (one_min_atr_pct or 0),
            ),
            float(exit_cfg.get("trailing_distance_pct_min", 0.35)),
            float(exit_cfg.get("trailing_distance_pct_max", 0.85)),
        )
    )
    if direction == "LONG":
        be_plus_price = price_for_risk * (1 + be_plus_move_pct / 100)
        tp1_price = price_for_risk * (1 + tp1_trigger_pct / 100)
    else:
        be_plus_price = price_for_risk * (1 - be_plus_move_pct / 100)
        tp1_price = price_for_risk * (1 - tp1_trigger_pct / 100)
    return TradePlan(
        exchange=diagnostic.exchange,
        symbol=diagnostic.symbol,
        direction=direction,
        classifier_label=decision.label,
        strategy_version=STRATEGY_VERSION,
        score=max(diagnostic.score, decision.execution_score),
        entry_price=price_for_risk,
        entry_grid=entry_grid,
        initial_sl_price=initial_sl_price,
        tp1_price=tp1_price,
        be_plus_price=be_plus_price,
        be_plus_move_pct=be_plus_move_pct,
        tp1_trigger_pct=tp1_trigger_pct,
        trailing_start_pct=trailing_start_pct,
        trailing_distance_pct=trailing_distance_pct,
        risk=risk,
        reasons=reasons,
        warnings=warnings,
        strategy_config_version=runtime_meta.get("version"),
        settings_hash=runtime_meta.get("hash"),
        settings_json=runtime_meta.get("settings"),
        status="planned" if risk.allowed else "rejected",
    )
