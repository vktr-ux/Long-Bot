from __future__ import annotations

import json

from app.storage.db import SQLiteStore
from app.storage.models import TickerSnapshot
from app.trading.pnl import closed_trade_from_fills, funding_cost_usdt, funding_event_count, gross_pnl, simulate_fill
from app.trading.risk import (
    estimate_isolated_liquidation_price,
    floor_to_step,
    maintenance_amount_usdt,
    maintenance_margin_rate,
)
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


def _runtime_entry_settings(details: dict, fallback_config: dict) -> dict:
    settings = details.get("settings_json") or details.get("runtime_settings") or {}
    if isinstance(settings, dict) and isinstance(settings.get("entry"), dict):
        return {**fallback_config.get("entry", {}), **settings["entry"]}
    return fallback_config.get("entry", {})


def _leg_qty(total_qty: float, fraction: float, step_size: float, remaining_qty: float, *, last_leg: bool) -> float:
    if last_leg:
        return floor_to_step(remaining_qty, step_size)
    return floor_to_step(min(total_qty * fraction, remaining_qty), step_size)


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
        entry_settings = plan.settings_json.get("entry", {}) if isinstance(plan.settings_json, dict) else self.config.get("entry", {})
        entry_grid = [dict(leg) for leg in plan.entry_grid]
        scale_in_enabled = (
            bool(entry_settings.get("scale_in_enabled", False))
            and bool(entry_settings.get("legs_enabled", True))
            and bool(entry_settings.get("allow_average_down", False))
            and len(entry_grid) > 1
        )
        entry_qty = plan.risk.qty
        if scale_in_enabled:
            first_fraction = float(entry_grid[0].get("fraction") or 1.0)
            entry_qty = _leg_qty(plan.risk.qty, first_fraction, plan.risk.step_size, plan.risk.qty, last_leg=len(entry_grid) == 1)
            first_notional = entry_qty * reference_price
            if entry_qty <= 0 or first_notional < plan.risk.min_notional * 1.02:
                scale_in_enabled = False
                entry_qty = plan.risk.qty
        fill = simulate_fill(
            side=side,
            qty=entry_qty,
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
        if scale_in_enabled:
            entry_grid[0].update(
                {
                    "status": "filled",
                    "filled_at_ms": ts,
                    "fill_qty": fill.qty,
                    "fill_price": fill.price,
                    "fill_notional_usdt": fill.notional_usdt,
                }
            )
        details["entry_grid"] = entry_grid
        details["scale_in"] = {
            "enabled": scale_in_enabled,
            "planned_total_qty": plan.risk.qty,
            "planned_total_notional_usdt": plan.risk.notional_usdt,
            "filled_legs": [1] if scale_in_enabled else [leg.get("leg") for leg in entry_grid],
            "max_legs": len(entry_grid),
        }
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

    def add_scale_in_leg(self, position: dict, ticker: TickerSnapshot) -> bool:
        details = json.loads(position.get("details_json") or "{}")
        scale_in = details.get("scale_in") or {}
        if not scale_in.get("enabled"):
            return False
        if details.get("tp1_done") or details.get("be_plus_armed") or details.get("profit_guard_armed") or int(position.get("trailing_active") or 0):
            return False
        entry_grid = [dict(leg) for leg in details.get("entry_grid") or []]
        if len(entry_grid) <= 1:
            return False
        filled_legs = {int(leg) for leg in scale_in.get("filled_legs") or []}
        next_leg = None
        for leg in sorted(entry_grid, key=lambda item: int(item.get("leg") or 0)):
            leg_number = int(leg.get("leg") or 0)
            status = str(leg.get("status") or "").lower()
            if leg_number > 1 and leg_number not in filled_legs and status not in {"filled", "skipped"}:
                next_leg = leg
                break
        if not next_leg:
            return False

        direction = str(position["direction"]).upper()
        reference_price = _entry_reference_price(direction, ticker, float(position["entry_price"]))
        trigger = float(next_leg.get("trigger_price") or 0)
        if reference_price <= 0 or trigger <= 0:
            return False
        entry_settings = _runtime_entry_settings(details, self.config)
        max_overrun_pct = float(entry_settings.get("scale_in_max_leg_overrun_pct", 0.35))
        reclaim_pct = float(entry_settings.get("scale_in_reclaim_pct", 0.0))
        leg_number = int(next_leg.get("leg") or 0)
        ts = now_ms()

        def persist_leg_state() -> None:
            details["scale_in"] = scale_in
            details["entry_grid"] = [
                next_leg if int(leg.get("leg") or 0) == leg_number else leg
                for leg in entry_grid
            ]
            self.store.update_paper_position(int(position["id"]), {"details_json": json.dumps(details)})

        def disable_scale_in(reason: str) -> None:
            next_leg.update({"status": "skipped", "skipped_at_ms": ts, "skip_reason": reason})
            scale_in["enabled"] = False
            scale_in["disabled_at_ms"] = ts
            scale_in["disabled_reason"] = reason
            persist_leg_state()
            self.store.record_bot_event(
                "INFO",
                "paper",
                f"scale-in disabled {position['direction']} {position['symbol']}",
                {"position_id": int(position["id"]), "leg": leg_number, "reason": reason},
            )

        stop = float(position["current_sl_price"])
        old_entry = float(position["entry_price"])
        if direction == "LONG":
            if reference_price <= stop:
                return False
            trigger_touched = reference_price <= trigger
            overrun_pct = (trigger - reference_price) / trigger * 100 if trigger_touched else 0.0
            reclaim_ready = reference_price >= trigger * (1 + reclaim_pct / 100)
            improves_entry = reference_price < old_entry
        else:
            if reference_price >= stop:
                return False
            trigger_touched = reference_price >= trigger
            overrun_pct = (reference_price - trigger) / trigger * 100 if trigger_touched else 0.0
            reclaim_ready = reference_price <= trigger * (1 - reclaim_pct / 100)
            improves_entry = reference_price > old_entry

        if reclaim_pct > 0:
            status = str(next_leg.get("status") or "").lower()
            armed = bool(next_leg.get("armed_at_ms")) or status == "armed"
            if not armed:
                if not trigger_touched:
                    return False
                if overrun_pct > max_overrun_pct:
                    disable_scale_in(f"scale-in touch overrun {overrun_pct:.3f}% > {max_overrun_pct:.3f}%")
                    return False
                next_leg.update(
                    {
                        "status": "armed",
                        "armed_at_ms": ts,
                        "armed_price": reference_price,
                        "armed_overrun_pct": overrun_pct,
                        "reclaim_pct": reclaim_pct,
                    }
                )
                persist_leg_state()
                self.store.record_bot_event(
                    "INFO",
                    "paper",
                    f"armed scale-in leg {leg_number} {position['direction']} {position['symbol']}",
                    {"position_id": int(position["id"]), "leg": leg_number, "trigger": trigger, "price": reference_price},
                )
                return False

            armed_overrun = float(next_leg.get("armed_overrun_pct") or 0)
            dirty = False
            if trigger_touched:
                if overrun_pct > armed_overrun:
                    next_leg["armed_overrun_pct"] = overrun_pct
                    dirty = True
                if direction == "LONG":
                    armed_worst = min(float(next_leg.get("armed_worst_price") or reference_price), reference_price)
                else:
                    armed_worst = max(float(next_leg.get("armed_worst_price") or reference_price), reference_price)
                if next_leg.get("armed_worst_price") != armed_worst:
                    next_leg["armed_worst_price"] = armed_worst
                    dirty = True
                armed_overrun = max(armed_overrun, overrun_pct)
            if armed_overrun > max_overrun_pct:
                disable_scale_in(f"scale-in armed overrun {armed_overrun:.3f}% > {max_overrun_pct:.3f}%")
                return False
            if not reclaim_ready or not improves_entry:
                if dirty:
                    persist_leg_state()
                return False
            overrun_pct = armed_overrun
        else:
            if not trigger_touched:
                return False

        if overrun_pct > max_overrun_pct:
            return False

        planned_total_qty = float(scale_in.get("planned_total_qty") or (details.get("risk") or {}).get("qty") or position.get("qty") or 0)
        current_qty = float(position.get("qty") or 0)
        remaining_qty = max(0.0, planned_total_qty - current_qty)
        if remaining_qty <= 0:
            return False
        remaining_leg_numbers = [
            int(leg.get("leg") or 0)
            for leg in entry_grid
            if int(leg.get("leg") or 0) not in filled_legs and leg.get("status") != "filled"
        ]
        is_last_leg = len(remaining_leg_numbers) <= 1
        step_size = float((details.get("risk") or {}).get("step_size") or 0)
        fraction = float(next_leg.get("fraction") or 0)
        qty = _leg_qty(planned_total_qty, fraction, step_size, remaining_qty, last_leg=is_last_leg)
        if qty <= 0:
            return False
        min_notional = float((details.get("risk") or {}).get("min_notional") or self.paper_cfg.get("fallback_min_notional_usdt", 5.0))
        if qty * reference_price < min_notional * 1.02:
            return False

        side = _entry_side(direction)
        fill = simulate_fill(
            side=side,
            qty=qty,
            reference_price=reference_price,
            role="ENTRY",
            fee_rate=float(self.paper_cfg.get("fee_rate_taker", 0.0004)),
            slippage_bps=float(self.paper_cfg.get("entry_slippage_bps", 3)),
            fill_source="binance_public_book_paper_scale_in",
        )
        old_qty = current_qty
        new_qty = old_qty + fill.qty
        new_entry = ((old_entry * old_qty) + (fill.price * fill.qty)) / new_qty
        notional = abs(new_entry * new_qty)
        leverage = float(position.get("leverage") or 1)
        margin = notional / leverage if leverage else notional
        high = max(float(position.get("high_watermark") or old_entry), reference_price, fill.price)
        low = min(float(position.get("low_watermark") or old_entry), reference_price, fill.price)
        if direction == "LONG":
            mfe = max(0.0, (high - new_entry) * new_qty)
            mae = min(0.0, (low - new_entry) * new_qty)
            tp1_price = new_entry * (1 + float(details.get("tp1_trigger_pct", 0)) / 100)
            be_plus_price = new_entry * (1 + float(details.get("be_plus_move_pct", 0)) / 100)
        else:
            mfe = max(0.0, (new_entry - low) * new_qty)
            mae = min(0.0, (new_entry - high) * new_qty)
            tp1_price = new_entry * (1 - float(details.get("tp1_trigger_pct", 0)) / 100)
            be_plus_price = new_entry * (1 - float(details.get("be_plus_move_pct", 0)) / 100)

        leg_number = int(next_leg.get("leg") or 0)
        next_leg.update(
            {
                "status": "filled",
                "filled_at_ms": ts,
                "fill_qty": fill.qty,
                "fill_price": fill.price,
                "fill_notional_usdt": fill.notional_usdt,
                "overrun_pct": overrun_pct,
            }
        )
        entry_grid = [next_leg if int(leg.get("leg") or 0) == leg_number else leg for leg in entry_grid]
        filled_legs.add(leg_number)
        scale_in["filled_legs"] = sorted(filled_legs)
        scale_in["filled_qty"] = new_qty
        scale_in["filled_fraction"] = (new_qty / planned_total_qty) if planned_total_qty else 1.0
        details["scale_in"] = scale_in
        details["entry_grid"] = entry_grid
        details["be_plus_price"] = be_plus_price
        details["average_entry_price"] = new_entry
        if details.get("margin_mode") == "isolated":
            details["liquidation_price"] = estimate_isolated_liquidation_price(
                direction=direction,
                entry_price=new_entry,
                qty=new_qty,
                isolated_margin_usdt=margin,
                maintenance_margin_rate_value=maintenance_margin_rate(self.paper_cfg),
                maintenance_amount_usdt_value=maintenance_amount_usdt(self.paper_cfg),
            )

        order_id = self.store.insert_paper_order(
            account_id=self.account_id,
            trade_plan_id=position.get("trade_plan_id"),
            position_id=int(position["id"]),
            symbol=position["symbol"],
            side=side,
            order_type="MARKET",
            role=f"SCALE_IN_{leg_number}",
            qty=fill.qty,
            price=fill.price,
            trigger_price=trigger,
            status="FILLED",
            timestamp_ms=ts,
        )
        self.store.insert_paper_fill(
            account_id=self.account_id,
            position_id=int(position["id"]),
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
        self.store.update_paper_position(
            int(position["id"]),
            {
                "qty": new_qty,
                "entry_price": new_entry,
                "notional_usdt": notional,
                "margin_usdt": margin,
                "tp1_price": tp1_price,
                "high_watermark": high,
                "low_watermark": low,
                "mfe_usdt": mfe,
                "mae_usdt": mae,
                "realized_pnl_usdt": float(position.get("realized_pnl_usdt") or 0) - (fill.fee_usdt + fill.slippage_usdt),
                "fees_usdt": float(position.get("fees_usdt") or 0) + fill.fee_usdt,
                "details_json": json.dumps(details),
            },
        )
        self.store.update_paper_account_totals(
            self.account_id,
            realized_delta=-(fill.fee_usdt + fill.slippage_usdt),
            fee_delta=fill.fee_usdt,
            slippage_delta=fill.slippage_usdt,
        )
        self.store.record_bot_event(
            "INFO",
            "paper",
            f"scale-in leg {leg_number} {position['direction']} {position['symbol']}",
            {"position_id": int(position["id"]), "leg": leg_number, "qty": fill.qty, "price": fill.price},
        )
        return True

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
