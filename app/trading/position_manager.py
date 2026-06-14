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


def mfe_move_pct(position: dict) -> float:
    notional = float(position.get("notional_usdt") or 0)
    if notional <= 0:
        entry = float(position.get("entry_price") or 0)
        qty = float(position.get("qty") or 0)
        notional = abs(entry * qty)
    if notional <= 0:
        return 0.0
    return float(position.get("mfe_usdt") or 0) / notional * 100


def price_at_favorable_move(position: dict, move_pct: float) -> float:
    entry = float(position["entry_price"])
    if position["direction"].upper() == "LONG":
        return entry * (1 + move_pct / 100)
    return entry * (1 - move_pct / 100)


def evaluate_position(position: dict, price: float, timestamp_ms: int, config: dict) -> PositionAction:
    details = json.loads(position.get("details_json") or "{}")
    paper_cfg = config.get("paper", {})
    runtime_settings = details.get("settings_json") or details.get("runtime_settings") or {}
    config_exit_settings = config.get("exit", {})
    runtime_exit_settings = runtime_settings.get("exit", {}) if isinstance(runtime_settings, dict) else {}
    exit_settings = {**config_exit_settings, **runtime_exit_settings}
    direction = position["direction"].upper()
    opened_at = int(position["opened_at_ms"])
    age_seconds = max(0, (timestamp_ms - opened_at) / 1000)
    move_pct = favorable_move_pct(position, price)
    profit_guard_enabled = bool(exit_settings.get("profit_guard_enabled", paper_cfg.get("profit_guard_enabled", True)))
    max_move_pct = max(float(details.get("max_favorable_move_pct", move_pct)), move_pct, mfe_move_pct(position))
    details_dirty = False
    if profit_guard_enabled and details.get("max_favorable_move_pct") != max_move_pct:
        details["max_favorable_move_pct"] = max_move_pct
        details_dirty = True
    if profit_guard_enabled and move_pct > 0 and not details.get("profit_started_ms"):
        details["profit_started_ms"] = timestamp_ms
        details_dirty = True

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
        close_fraction = float(exit_settings.get("tp1_close_fraction", paper_cfg.get("tp1_close_fraction", 0.5)))
        if close_fraction >= 0.999:
            return PositionAction("CLOSE", "SCALP_TAKE_PROFIT", 1.0, updates)
        return PositionAction("PARTIAL_CLOSE", "TP1_PARTIAL", close_fraction, updates)

    if profit_guard_enabled and not details.get("tp1_done"):
        guard_trigger_pct = float(exit_settings.get("profit_guard_trigger_pct", paper_cfg.get("profit_guard_trigger_pct", 0.30)))
        guard_floor_pct = float(exit_settings.get("profit_guard_floor_pct", paper_cfg.get("profit_guard_floor_pct", 0.08)))
        guard_min_age = float(exit_settings.get("profit_guard_min_age_seconds", paper_cfg.get("profit_guard_min_age_seconds", 20)))
        net_profit_floor_pct = float(details.get("be_plus_move_pct", 0))
        guarded_stop_floor_pct = max(guard_floor_pct, net_profit_floor_pct)
        effective_guard_trigger_pct = max(guard_trigger_pct, guarded_stop_floor_pct)
        if max_move_pct >= effective_guard_trigger_pct:
            if not details.get("profit_guard_armed"):
                details["profit_guard_armed"] = True
                details_dirty = True
            if move_pct >= guarded_stop_floor_pct:
                guarded_stop = price_at_favorable_move(position, guarded_stop_floor_pct)
                current_stop = float(position["current_sl_price"])
                if direction == "LONG":
                    updates["current_sl_price"] = max(current_stop, guarded_stop)
                else:
                    updates["current_sl_price"] = min(current_stop, guarded_stop)
            pct_tolerance = 0.000001
            if (
                age_seconds >= guard_min_age
                and move_pct <= guarded_stop_floor_pct + pct_tolerance
            ):
                updates["details_json"] = json.dumps(details)
                return PositionAction("CLOSE", "PROFIT_GIVEBACK_EXIT", 1.0, updates)

        small_time_enabled = bool(exit_settings.get("small_profit_time_exit_enabled", paper_cfg.get("small_profit_time_exit_enabled", True)))
        profit_started_ms = int(details.get("profit_started_ms") or 0)
        positive_age_seconds = max(0, (timestamp_ms - profit_started_ms) / 1000) if profit_started_ms else 0
        small_exit_seconds = float(exit_settings.get("small_profit_time_exit_seconds", paper_cfg.get("small_profit_time_exit_seconds", 30)))
        small_exit_min_pct = float(exit_settings.get("small_profit_time_exit_min_pct", paper_cfg.get("small_profit_time_exit_min_pct", 0.25)))
        effective_small_exit_min_pct = max(small_exit_min_pct, float(details.get("be_plus_move_pct", 0)))
        if (
            small_time_enabled
            and small_exit_seconds > 0
            and max_move_pct >= effective_guard_trigger_pct
            and positive_age_seconds >= small_exit_seconds
            and move_pct >= effective_small_exit_min_pct
        ):
            updates["details_json"] = json.dumps(details)
            return PositionAction("CLOSE", "SMALL_PROFIT_TIME_EXIT", 1.0, updates)

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
    if details_dirty:
        return PositionAction("UPDATE", "MANAGE", 0.0, {"details_json": json.dumps(details)})
    return PositionAction("HOLD", "NO_ACTION", 0.0)
