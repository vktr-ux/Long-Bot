from __future__ import annotations

import json

from app.storage.db import SQLiteStore
from app.storage.models import TickerSnapshot
from app.trading.pnl import closed_trade_from_fills, funding_cost_usdt, funding_event_count, gross_pnl, simulate_fill
from app.trading.strategy import TradePlan
from app.utils.time import now_ms


def _entry_side(direction: str) -> str:
    return "BUY" if direction.upper() == "LONG" else "SELL"


def _exit_side(direction: str) -> str:
    return "SELL" if direction.upper() == "LONG" else "BUY"


def _entry_reference_price(direction: str, ticker: TickerSnapshot, fallback: float) -> float:
    if direction.upper() == "LONG":
        return ticker.ask_price or ticker.last_price or fallback
    return ticker.bid_price or ticker.last_price or fallback


def _exit_reference_price(direction: str, ticker: TickerSnapshot, fallback: float) -> float:
    if direction.upper() == "LONG":
        return ticker.bid_price or ticker.last_price or fallback
    return ticker.ask_price or ticker.last_price or fallback


def _to_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def estimate_position_funding(position: dict, ticker: TickerSnapshot, closed_at_ms: int, details: dict) -> tuple[float, dict]:
    funding_details = dict(details.get("funding") or {})
    entry_next_funding_ms = _to_int(funding_details.get("entry_next_funding_time_ms"))
    event_count = funding_event_count(
        int(position.get("opened_at_ms") or 0),
        closed_at_ms,
        entry_next_funding_ms,
    )
    rate = _to_float(ticker.funding_rate)
    if rate is None:
        rate = _to_float(funding_details.get("entry_rate"))
    notional = _to_float(position.get("notional_usdt")) or abs(float(position.get("entry_price") or 0) * float(position.get("qty") or 0))
    funding_usdt = funding_cost_usdt(str(position.get("direction") or ""), notional, rate, event_count)
    return funding_usdt, {
        **funding_details,
        "entry_rate": funding_details.get("entry_rate"),
        "entry_next_funding_time_ms": entry_next_funding_ms,
        "exit_rate": rate,
        "funding_events": event_count,
        "funding_usdt": funding_usdt,
    }


class PaperBroker:
    def __init__(self, store: SQLiteStore, config: dict, account_id: int):
        self.store = store
        self.config = config
        self.account_id = account_id

    @property
    def paper_cfg(self) -> dict:
        return self.config.get("paper", {})

    def open_position(self, plan: TradePlan, ticker: TickerSnapshot, trade_plan_id: int | None = None) -> int:
        ts = now_ms()
        side = _entry_side(plan.direction)
        reference_price = _entry_reference_price(plan.direction, ticker, plan.entry_price)
        fill = simulate_fill(
            side=side,
            qty=plan.risk.qty,
            reference_price=reference_price,
            role="ENTRY",
            fee_rate=float(self.paper_cfg.get("fee_rate_taker", 0.0004)),
            slippage_bps=float(self.paper_cfg.get("entry_slippage_bps", 3)),
            fill_source="binance_public_book_paper",
        )
        details = plan.to_dict()
        details["tp1_done"] = False
        details["be_plus_armed"] = False
        details["strategy_config_version"] = plan.strategy_config_version
        details["settings_hash"] = plan.settings_hash
        details["funding"] = {
            "entry_rate": ticker.funding_rate,
            "entry_next_funding_time_ms": ticker.next_funding_time_ms,
        }
        position_id = self.store.insert_paper_position(
            {
                "account_id": self.account_id,
                "trade_plan_id": trade_plan_id,
                "symbol": plan.symbol,
                "direction": plan.direction,
                "status": "OPEN",
                "qty": fill.qty,
                "entry_price": fill.price,
                "notional_usdt": fill.notional_usdt,
                "margin_usdt": fill.notional_usdt / plan.risk.leverage,
                "leverage": plan.risk.leverage,
                "initial_sl_price": plan.initial_sl_price,
                "current_sl_price": plan.initial_sl_price,
                "tp1_price": plan.tp1_price,
                "trailing_active": 0,
                "trailing_distance_pct": plan.trailing_distance_pct,
                "high_watermark": fill.price,
                "low_watermark": fill.price,
                "unrealized_pnl_usdt": 0.0,
                "realized_pnl_usdt": -(fill.fee_usdt + fill.slippage_usdt),
                "fees_usdt": fill.fee_usdt,
                "mfe_usdt": 0.0,
                "mae_usdt": 0.0,
                "opened_at_ms": ts,
                "closed_at_ms": None,
                "exit_reason": None,
                "details_json": json.dumps(details),
                "strategy_config_version": plan.strategy_config_version,
                "settings_hash": plan.settings_hash,
                "settings_json": json.dumps(plan.settings_json) if plan.settings_json else None,
            }
        )
        order_id = self.store.insert_paper_order(
            account_id=self.account_id,
            trade_plan_id=trade_plan_id,
            position_id=position_id,
            symbol=plan.symbol,
            side=side,
            order_type="MARKET",
            role="ENTRY",
            qty=fill.qty,
            price=fill.price,
            trigger_price=None,
            status="FILLED",
            timestamp_ms=ts,
        )
        self.store.insert_paper_fill(
            account_id=self.account_id,
            position_id=position_id,
            order_id=order_id,
            symbol=plan.symbol,
            side=side,
            qty=fill.qty,
            price=fill.price,
            notional_usdt=fill.notional_usdt,
            fee_usdt=fill.fee_usdt,
            slippage_usdt=fill.slippage_usdt,
            liquidity_side=fill.liquidity_side,
            fill_source=fill.fill_source,
            filled_at_ms=ts,
        )
        self.store.update_paper_account_totals(
            self.account_id,
            realized_delta=-(fill.fee_usdt + fill.slippage_usdt),
            fee_delta=fill.fee_usdt,
            slippage_delta=fill.slippage_usdt,
        )
        if trade_plan_id is not None:
            self.store.update_trade_plan_status(trade_plan_id, "opened")
        self.store.record_bot_event("INFO", "paper", f"opened paper {plan.direction} {plan.symbol}", {"position_id": position_id})
        return position_id

    def mark_position(self, position: dict, ticker: TickerSnapshot) -> dict:
        direction = position["direction"].upper()
        mark = ticker.bid_price if direction == "LONG" and ticker.bid_price else ticker.ask_price if direction == "SHORT" and ticker.ask_price else ticker.last_price
        qty = float(position["qty"])
        entry = float(position["entry_price"])
        unrealized = gross_pnl(direction, entry, mark, qty)
        high = max(float(position.get("high_watermark") or entry), mark)
        low = min(float(position.get("low_watermark") or entry), mark)
        if direction == "LONG":
            mfe = max(float(position.get("mfe_usdt") or 0), (high - entry) * qty)
            mae = min(float(position.get("mae_usdt") or 0), (low - entry) * qty)
        else:
            mfe = max(float(position.get("mfe_usdt") or 0), (entry - low) * qty)
            mae = min(float(position.get("mae_usdt") or 0), (entry - high) * qty)
        updates = {
            "unrealized_pnl_usdt": unrealized,
            "high_watermark": high,
            "low_watermark": low,
            "mfe_usdt": mfe,
            "mae_usdt": mae,
        }
        self.store.update_paper_position(int(position["id"]), updates)
        return {**position, **updates}

    def close_position(self, position_id: int, ticker: TickerSnapshot, reason: str, fraction: float = 1.0) -> int | None:
        position = self.store.get_position(position_id)
        if not position or position["status"] != "OPEN":
            return None
        fraction = max(0.0, min(1.0, fraction))
        if fraction <= 0:
            return None
        direction = position["direction"].upper()
        qty_to_close = float(position["qty"]) * fraction
        side = _exit_side(direction)
        reference_price = _exit_reference_price(direction, ticker, float(position["entry_price"]))
        fill = simulate_fill(
            side=side,
            qty=qty_to_close,
            reference_price=reference_price,
            role="EXIT",
            fee_rate=float(self.paper_cfg.get("fee_rate_taker", 0.0004)),
            slippage_bps=float(self.paper_cfg.get("exit_slippage_bps", 5)),
            fill_source="binance_public_book_paper",
        )
        ts = now_ms()
        order_id = self.store.insert_paper_order(
            account_id=self.account_id,
            trade_plan_id=position.get("trade_plan_id"),
            position_id=position_id,
            symbol=position["symbol"],
            side=side,
            order_type="MARKET",
            role=reason,
            qty=fill.qty,
            price=fill.price,
            trigger_price=None,
            status="FILLED",
            timestamp_ms=ts,
        )
        self.store.insert_paper_fill(
            account_id=self.account_id,
            position_id=position_id,
            order_id=order_id,
            symbol=position["symbol"],
            side=side,
            qty=fill.qty,
            price=fill.price,
            notional_usdt=fill.notional_usdt,
            fee_usdt=fill.fee_usdt,
            slippage_usdt=fill.slippage_usdt,
            liquidity_side=fill.liquidity_side,
            fill_source=fill.fill_source,
            filled_at_ms=ts,
        )
        close_gross = gross_pnl(direction, float(position["entry_price"]), fill.price, fill.qty)
        remaining_qty = max(0.0, float(position["qty"]) - fill.qty)
        details = json.loads(position.get("details_json") or "{}")
        if reason == "TP1_PARTIAL":
            details["tp1_done"] = True
        funding_usdt = 0.0
        if remaining_qty <= 0:
            funding_usdt, funding_details = estimate_position_funding(position, ticker, ts, details)
            details["funding"] = funding_details
        close_delta = close_gross - fill.fee_usdt - fill.slippage_usdt - funding_usdt
        updates = {
            "qty": remaining_qty,
            "realized_pnl_usdt": float(position.get("realized_pnl_usdt") or 0) + close_delta,
            "fees_usdt": float(position.get("fees_usdt") or 0) + fill.fee_usdt,
            "details_json": json.dumps(details),
        }
        trade_id: int | None = None
        if remaining_qty <= 0:
            updates.update({"status": "CLOSED", "closed_at_ms": ts, "exit_reason": reason, "unrealized_pnl_usdt": 0.0})
        self.store.update_paper_position(position_id, updates)
        self.store.update_paper_account_totals(
            self.account_id,
            realized_delta=close_delta,
            fee_delta=fill.fee_usdt,
            slippage_delta=fill.slippage_usdt,
        )
        if remaining_qty <= 0:
            final_position = self.store.get_position(position_id) or {**position, **updates}
            fills = self.store.get_position_fills(position_id)
            trade = closed_trade_from_fills(final_position, fills, funding_usdt=funding_usdt)
            trade_id = self.store.insert_paper_trade(trade)
            if final_position.get("trade_plan_id"):
                self.store.update_trade_plan_status(int(final_position["trade_plan_id"]), "closed")
            self.store.record_bot_event("INFO", "paper", f"closed paper {direction} {position['symbol']} by {reason}", {"position_id": position_id, "trade_id": trade_id})
        return trade_id
