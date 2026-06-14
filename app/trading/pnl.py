from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class SimulatedFill:
    side: str
    qty: float
    reference_price: float
    price: float
    notional_usdt: float
    fee_usdt: float
    slippage_usdt: float
    liquidity_side: str
    fill_source: str

    def to_dict(self) -> dict:
        return asdict(self)


def apply_slippage(reference_price: float, bps: float, *, side: str, role: str) -> float:
    slippage_fraction = bps / 10_000
    side = side.upper()
    role = role.upper()
    if role == "ENTRY":
        if side == "BUY":
            return reference_price * (1 + slippage_fraction)
        return reference_price * (1 - slippage_fraction)
    if side == "SELL":
        return reference_price * (1 - slippage_fraction)
    return reference_price * (1 + slippage_fraction)


def simulate_fill(
    *,
    side: str,
    qty: float,
    reference_price: float,
    role: str,
    fee_rate: float,
    slippage_bps: float,
    fill_source: str = "paper",
) -> SimulatedFill:
    fill_price = apply_slippage(reference_price, slippage_bps, side=side, role=role)
    notional = abs(qty * fill_price)
    fee = notional * fee_rate
    slippage = abs(fill_price - reference_price) * abs(qty)
    return SimulatedFill(
        side=side.upper(),
        qty=qty,
        reference_price=reference_price,
        price=fill_price,
        notional_usdt=notional,
        fee_usdt=fee,
        slippage_usdt=slippage,
        liquidity_side="taker",
        fill_source=fill_source,
    )


def gross_pnl(direction: str, entry_price: float, exit_price: float, qty: float) -> float:
    if direction.upper() == "LONG":
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty


def net_pnl(direction: str, entry_price: float, exit_price: float, qty: float, fees: float, slippage: float, funding: float = 0.0) -> float:
    return gross_pnl(direction, entry_price, exit_price, qty) - fees - slippage - funding


def weighted_average_price(fills: list[dict]) -> float:
    qty = sum(abs(float(fill["qty"])) for fill in fills)
    if qty <= 0:
        return 0.0
    return sum(abs(float(fill["qty"])) * float(fill["price"]) for fill in fills) / qty


def closed_trade_from_fills(position: dict, fills: list[dict], funding_usdt: float = 0.0) -> dict:
    direction = position["direction"].upper()
    entry_side = "BUY" if direction == "LONG" else "SELL"
    exit_side = "SELL" if direction == "LONG" else "BUY"
    entry_fills = [fill for fill in fills if fill["side"].upper() == entry_side]
    exit_fills = [fill for fill in fills if fill["side"].upper() == exit_side]
    if not entry_fills or not exit_fills:
        raise ValueError("closed trade requires both entry and exit fills")
    qty = min(sum(abs(float(fill["qty"])) for fill in entry_fills), sum(abs(float(fill["qty"])) for fill in exit_fills))
    entry_price = weighted_average_price(entry_fills)
    exit_price = weighted_average_price(exit_fills)
    entry_fee = sum(float(fill["fee_usdt"]) for fill in entry_fills)
    exit_fee = sum(float(fill["fee_usdt"]) for fill in exit_fills)
    fees = entry_fee + exit_fee
    slippage = sum(float(fill["slippage_usdt"]) for fill in entry_fills + exit_fills)
    gross = gross_pnl(direction, entry_price, exit_price, qty)
    net = gross - fees - slippage - funding_usdt
    notional = entry_price * qty
    leverage = float(position.get("leverage") or 1)
    margin = notional / leverage if leverage else notional
    entry_time = min(int(fill["filled_at_ms"]) for fill in entry_fills)
    exit_time = max(int(fill["filled_at_ms"]) for fill in exit_fills)
    duration = max(0.0, (exit_time - entry_time) / 1000)
    roi = (net / margin * 100) if margin else 0.0
    details = {}
    if position.get("details_json"):
        try:
            details = json.loads(position.get("details_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
    return {
        "account_id": position["account_id"],
        "position_id": position["id"],
        "symbol": position["symbol"],
        "direction": direction,
        "entry_time_ms": entry_time,
        "exit_time_ms": exit_time,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "notional_usdt": notional,
        "leverage": leverage,
        "gross_pnl_usdt": gross,
        "fees_usdt": fees,
        "slippage_usdt": slippage,
        "funding_usdt": funding_usdt,
        "net_pnl_usdt": net,
        "roi_pct": roi,
        "mfe_usdt": float(position.get("mfe_usdt") or 0),
        "mae_usdt": float(position.get("mae_usdt") or 0),
        "duration_seconds": duration,
        "exit_reason": position.get("exit_reason") or "UNKNOWN",
        "strategy_version": position.get("strategy_version") or details.get("strategy_version") or "paper_scalper_v1",
        "entry_fee_usdt": entry_fee,
        "exit_fee_usdt": exit_fee,
        "strategy_config_version": position.get("strategy_config_version"),
        "settings_hash": position.get("settings_hash"),
        "settings_json": position.get("settings_json"),
    }


def candle_exit_reason(direction: str, high: float, low: float, stop_price: float, tp_price: float) -> str | None:
    direction = direction.upper()
    if direction == "LONG":
        stop_touched = low <= stop_price
        tp_touched = high >= tp_price
    else:
        stop_touched = high >= stop_price
        tp_touched = low <= tp_price
    if stop_touched:
        return "STOP_LOSS"
    if tp_touched:
        return "TP1_PARTIAL"
    return None
