from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(slots=True)
class PositionAction:
    action: str
    reason: str
    close_fraction: float = 0.0
    updates: dict | None = None


def favorable_move_pct(position: dict, price: float) -> float:
    entry = float(position["entry_price"])
    if entry <= 0:
        return 0.0
    if position["direction"].upper() == "LONG":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def stop_touched(position: dict, price: float) -> bool:
    stop = float(position["current_sl_price"])
    if position["direction"].upper() == "LONG":
        return price <= stop
    return price >= stop


def tp1_touched(position: dict, price: float) -> bool:
    tp1 = float(position["tp1_price"])
    if position["direction"].upper() == "LONG":
        return price >= tp1
    return price <= tp1


def evaluate_position(position: dict, price: float, timestamp_ms: int, config: dict) -> PositionAction:
    details = json.loads(position.get("details_json") or "{}")
    paper_cfg = config.get("paper", {})
    runtime_settings = details.get("settings_json") or details.get("runtime_settings") or {}
    exit_settings = runtime_settings.get("exit", {}) if isinstance(runtime_settings, dict) else {}
    direction = position["direction"].upper()
    opened_at = int(position["opened_at_ms"])
    age_seconds = max(0, (timestamp_ms - opened_at) / 1000)
    move_pct = favorable_move_pct(position, price)

    if stop_touched(position, price):
        initial_stop = float(position["initial_sl_price"])
        current_stop = float(position["current_sl_price"])
        if abs(current_stop - initial_stop) <= initial_stop * 0.000001:
            reason = "STOP_LOSS"
        else:
            stop_profit_pct = favorable_move_pct(position, current_stop)
            be_move = float(details.get("be_plus_move_pct", 0))
            reason = "PROFIT_LOCK_STOP" if stop_profit_pct > be_move + 0.20 else "BREAKEVEN_PLUS_STOP"
        return PositionAction("CLOSE", reason, 1.0)

    updates: dict = {}
    tp1_enabled = bool(exit_settings.get("tp1_enabled", paper_cfg.get("tp1_enabled", True)))
    if tp1_enabled and not details.get("tp1_done") and tp1_touched(position, price):
        details["tp1_done"] = True
        details["be_plus_armed"] = True
        updates["current_sl_price"] = float(details.get("be_plus_price", position["current_sl_price"]))
        updates["details_json"] = json.dumps(details)
        return PositionAction("PARTIAL_CLOSE", "TP1_PARTIAL", float(exit_settings.get("tp1_close_fraction", paper_cfg.get("tp1_close_fraction", 0.5))), updates)

    be_enabled = bool(exit_settings.get("breakeven_plus_enabled", paper_cfg.get("breakeven_plus_enabled", True)))
    be_extra = float(exit_settings.get("breakeven_plus_trigger_extra_pct", paper_cfg.get("breakeven_plus_trigger_extra_pct", 0.15)))
    be_trigger = float(details.get("be_plus_move_pct", 999)) + be_extra
    if tp1_enabled and not details.get("tp1_done"):
        be_trigger = max(be_trigger, float(details.get("tp1_trigger_pct", 999)))
    if be_enabled and not details.get("be_plus_armed") and move_pct >= be_trigger:
        updates["current_sl_price"] = float(details["be_plus_price"])
        details["be_plus_armed"] = True

    trailing_start_pct = float(details.get("trailing_start_pct", 999))
    trailing_distance_pct = float(position.get("trailing_distance_pct") or details.get("trailing_distance_pct") or 0.5)
    trailing_enabled = bool(exit_settings.get("trailing_enabled", paper_cfg.get("trailing_enabled", True)))
    if trailing_enabled and move_pct >= trailing_start_pct:
        updates["trailing_active"] = 1
        if direction == "LONG":
            high = max(float(position.get("high_watermark") or price), price)
            updates["current_sl_price"] = max(float(position["current_sl_price"]), high * (1 - trailing_distance_pct / 100))
        else:
            low = min(float(position.get("low_watermark") or price), price)
            updates["current_sl_price"] = min(float(position["current_sl_price"]), low * (1 + trailing_distance_pct / 100))

    max_hold = float(exit_settings.get("max_hold_seconds", paper_cfg.get("max_hold_seconds", 600)))
    time_stop = float(exit_settings.get("time_stop_seconds", paper_cfg.get("time_stop_seconds", 180)))
    if max_hold > 0 and age_seconds >= max_hold:
        return PositionAction("CLOSE", "MAX_HOLD", 1.0, updates or None)
    if time_stop > 0 and age_seconds >= time_stop and not details.get("tp1_done"):
        mfe_usdt = float(position.get("mfe_usdt") or 0)
        notional = float(position.get("notional_usdt") or 0)
        mfe_pct = (mfe_usdt / notional * 100) if notional > 0 else 0.0
        be_move_pct = float(details.get("be_plus_move_pct", 0))
        minimum_progress_pct = max(0.12, be_move_pct * 0.6)
        if move_pct <= 0 or mfe_pct < minimum_progress_pct:
            return PositionAction("CLOSE", "TIME_STOP", 1.0, updates or None)

    if updates:
        details["be_plus_armed"] = details.get("be_plus_armed", False)
        updates["details_json"] = json.dumps(details)
        return PositionAction("UPDATE", "MANAGE", 0.0, updates)
    return PositionAction("HOLD", "NO_ACTION", 0.0)
