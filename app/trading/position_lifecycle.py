from __future__ import annotations

import json
from typing import Any

from app.trading.position_manager import favorable_move_pct

POSITION_LIFECYCLE_STATE_KEY = "position_lifecycle_state"
PROFIT_HOLD_BUCKETS_SECONDS = (30, 60, 120, 240, 420, 900, 1800)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _position_details(position: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(position.get("details_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _event(
    position: dict[str, Any],
    *,
    timestamp_ms: int,
    price: float,
    event_type: str,
    move_pct: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = _as_float(position.get("entry_price"))
    qty = _as_float(position.get("qty"))
    notional = abs(entry * qty)
    leverage = _as_float(position.get("leverage"), 1.0) or 1.0
    margin = notional / leverage if leverage else notional
    unrealized = _as_float(position.get("unrealized_pnl_usdt"))
    roi_pct = unrealized / margin * 100 if margin > 0 else move_pct * leverage
    age_seconds = max(0.0, (timestamp_ms - _as_int(position.get("opened_at_ms"))) / 1000)
    details = extra or {}
    return {
        "timestamp_ms": timestamp_ms,
        "position_id": _as_int(position.get("id")),
        "account_id": _as_int(position.get("account_id")),
        "symbol": str(position.get("symbol") or "").upper(),
        "direction": str(position.get("direction") or "").upper(),
        "event_type": event_type.upper(),
        "age_seconds": age_seconds,
        "price": price,
        "entry_price": entry,
        "move_pct": move_pct,
        "roi_pct": roi_pct,
        "unrealized_pnl_usdt": unrealized,
        "realized_pnl_usdt": _as_float(position.get("realized_pnl_usdt")),
        "qty": qty,
        "notional_usdt": notional,
        "current_sl_price": _as_float(position.get("current_sl_price")),
        "tp1_price": _as_float(position.get("tp1_price")),
        "mfe_usdt": _as_float(position.get("mfe_usdt")),
        "mae_usdt": _as_float(position.get("mae_usdt")),
        "strategy_config_version": position.get("strategy_config_version"),
        "settings_hash": position.get("settings_hash"),
        "details_json": json.dumps(details, sort_keys=True),
    }


def prune_position_lifecycle_state(state: dict[str, Any], open_position_ids: set[int]) -> dict[str, Any]:
    positions = state.get("positions", {})
    if not isinstance(positions, dict):
        positions = {}
    open_keys = {str(position_id) for position_id in open_position_ids}
    return {"positions": {key: value for key, value in positions.items() if key in open_keys}}


def update_position_lifecycle(
    state: dict[str, Any] | None,
    position: dict[str, Any],
    *,
    price: float,
    timestamp_ms: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    next_state = dict(state or {})
    positions = dict(next_state.get("positions") or {})
    position_id = str(_as_int(position.get("id")))
    stored = dict(positions.get(position_id) or {})
    events: list[dict[str, Any]] = []

    details = _position_details(position)
    move_pct = favorable_move_pct(position, price)
    is_positive = move_pct > 0
    was_positive = bool(stored.get("positive"))
    entry_ms = _as_int(position.get("opened_at_ms"))
    age_seconds = max(0.0, (timestamp_ms - entry_ms) / 1000)
    tp1_trigger_pct = _as_float(details.get("tp1_trigger_pct"), _as_float(details.get("be_plus_move_pct"), 0.0))
    tp1_done = bool(details.get("tp1_done"))
    be_plus_armed = bool(details.get("be_plus_armed"))

    if not stored:
        events.append(
            _event(
                position,
                timestamp_ms=timestamp_ms,
                price=price,
                event_type="POSITION_TRACKED",
                move_pct=move_pct,
                extra={"age_seconds": age_seconds, "tp1_trigger_pct": tp1_trigger_pct},
            )
        )
        stored = {
            "positive": False,
            "profit_hold_buckets": [],
            "small_profit_hold_buckets": [],
            "max_move_pct": move_pct,
            "min_move_pct": move_pct,
        }

    stored["max_move_pct"] = max(_as_float(stored.get("max_move_pct"), move_pct), move_pct)
    stored["min_move_pct"] = min(_as_float(stored.get("min_move_pct"), move_pct), move_pct)

    if is_positive:
        if not was_positive:
            stored["positive_started_ms"] = timestamp_ms
            stored["profit_hold_buckets"] = []
            events.append(
                _event(
                    position,
                    timestamp_ms=timestamp_ms,
                    price=price,
                    event_type="PROFIT_STARTED",
                    move_pct=move_pct,
                    extra={"age_seconds": age_seconds},
                )
            )
        positive_started_ms = _as_int(stored.get("positive_started_ms"), timestamp_ms)
        positive_duration = max(0.0, (timestamp_ms - positive_started_ms) / 1000)
        logged = {int(item) for item in stored.get("profit_hold_buckets") or []}
        for bucket in PROFIT_HOLD_BUCKETS_SECONDS:
            if positive_duration >= bucket and bucket not in logged:
                events.append(
                    _event(
                        position,
                        timestamp_ms=timestamp_ms,
                        price=price,
                        event_type=f"PROFIT_HELD_{bucket}S",
                        move_pct=move_pct,
                        extra={
                            "positive_duration_seconds": positive_duration,
                            "bucket_seconds": bucket,
                            "tp1_done": tp1_done,
                            "be_plus_armed": be_plus_armed,
                        },
                    )
                )
                logged.add(bucket)
        stored["profit_hold_buckets"] = sorted(logged)

        is_small_profit = not tp1_done and tp1_trigger_pct > 0 and move_pct < tp1_trigger_pct
        if is_small_profit:
            if not stored.get("small_profit_started_ms"):
                stored["small_profit_started_ms"] = timestamp_ms
                stored["small_profit_hold_buckets"] = []
            small_started_ms = _as_int(stored.get("small_profit_started_ms"), timestamp_ms)
            small_duration = max(0.0, (timestamp_ms - small_started_ms) / 1000)
            small_logged = {int(item) for item in stored.get("small_profit_hold_buckets") or []}
            for bucket in PROFIT_HOLD_BUCKETS_SECONDS:
                if small_duration >= bucket and bucket not in small_logged:
                    events.append(
                        _event(
                            position,
                            timestamp_ms=timestamp_ms,
                            price=price,
                            event_type=f"SMALL_PROFIT_HELD_{bucket}S",
                            move_pct=move_pct,
                            extra={
                                "small_profit_duration_seconds": small_duration,
                                "bucket_seconds": bucket,
                                "tp1_trigger_pct": tp1_trigger_pct,
                                "tp1_done": tp1_done,
                            },
                        )
                    )
                    small_logged.add(bucket)
            stored["small_profit_hold_buckets"] = sorted(small_logged)
        else:
            stored.pop("small_profit_started_ms", None)
            stored["small_profit_hold_buckets"] = []
    elif was_positive:
        positive_started_ms = _as_int(stored.get("positive_started_ms"), timestamp_ms)
        events.append(
            _event(
                position,
                timestamp_ms=timestamp_ms,
                price=price,
                event_type="PROFIT_GAVE_BACK",
                move_pct=move_pct,
                extra={
                    "positive_duration_seconds": max(0.0, (timestamp_ms - positive_started_ms) / 1000),
                    "max_move_pct": _as_float(stored.get("max_move_pct")),
                    "min_move_pct": _as_float(stored.get("min_move_pct")),
                    "tp1_done": tp1_done,
                    "be_plus_armed": be_plus_armed,
                },
            )
        )
        stored.pop("positive_started_ms", None)
        stored.pop("small_profit_started_ms", None)
        stored["profit_hold_buckets"] = []
        stored["small_profit_hold_buckets"] = []

    if tp1_done and not stored.get("tp1_done_logged"):
        events.append(
            _event(
                position,
                timestamp_ms=timestamp_ms,
                price=price,
                event_type="TP1_DONE",
                move_pct=move_pct,
                extra={"age_seconds": age_seconds},
            )
        )
        stored["tp1_done_logged"] = True
    if be_plus_armed and not stored.get("be_plus_armed_logged"):
        events.append(
            _event(
                position,
                timestamp_ms=timestamp_ms,
                price=price,
                event_type="BE_PLUS_ARMED",
                move_pct=move_pct,
                extra={"age_seconds": age_seconds},
            )
        )
        stored["be_plus_armed_logged"] = True

    stored["positive"] = is_positive
    stored["last_seen_ms"] = timestamp_ms
    stored["last_move_pct"] = move_pct
    positions[position_id] = stored
    next_state["positions"] = positions
    return next_state, events
