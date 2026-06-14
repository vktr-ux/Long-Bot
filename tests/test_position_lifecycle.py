import json

from app.storage.db import SQLiteStore
from app.trading.position_lifecycle import prune_position_lifecycle_state, update_position_lifecycle


def position(**overrides):
    data = {
        "id": 1,
        "account_id": 1,
        "symbol": "AAAUSDT",
        "direction": "SHORT",
        "qty": 1.0,
        "entry_price": 100.0,
        "notional_usdt": 100.0,
        "margin_usdt": 12.5,
        "leverage": 8.0,
        "current_sl_price": 102.0,
        "tp1_price": 99.3,
        "unrealized_pnl_usdt": 0.0,
        "realized_pnl_usdt": 0.0,
        "mfe_usdt": 0.0,
        "mae_usdt": 0.0,
        "opened_at_ms": 1_000,
        "strategy_config_version": 14,
        "settings_hash": "abc",
        "details_json": json.dumps({"tp1_trigger_pct": 0.7, "tp1_done": False, "be_plus_armed": False}),
    }
    data.update(overrides)
    return data


def test_position_lifecycle_logs_small_profit_hold_and_giveback():
    state = {}

    state, events = update_position_lifecycle(state, position(), price=100.2, timestamp_ms=1_000)
    assert [event["event_type"] for event in events] == ["POSITION_TRACKED"]

    state, events = update_position_lifecycle(
        state,
        position(unrealized_pnl_usdt=0.4, mfe_usdt=0.4),
        price=99.6,
        timestamp_ms=2_000,
    )
    assert [event["event_type"] for event in events] == ["PROFIT_STARTED"]

    state, events = update_position_lifecycle(
        state,
        position(unrealized_pnl_usdt=0.5, mfe_usdt=0.5),
        price=99.5,
        timestamp_ms=33_000,
    )
    event_types = {event["event_type"] for event in events}
    assert "PROFIT_HELD_30S" in event_types
    assert "SMALL_PROFIT_HELD_30S" in event_types

    state, events = update_position_lifecycle(
        state,
        position(unrealized_pnl_usdt=-0.1, mfe_usdt=0.5, mae_usdt=-0.1),
        price=100.1,
        timestamp_ms=50_000,
    )
    assert [event["event_type"] for event in events] == ["PROFIT_GAVE_BACK"]
    details = json.loads(events[0]["details_json"])
    assert details["positive_duration_seconds"] == 48
    assert details["max_move_pct"] == 0.5


def test_position_lifecycle_state_prunes_closed_positions_and_store_persists_events(tmp_path):
    state = {"positions": {"1": {"positive": True}, "2": {"positive": True}}}
    assert prune_position_lifecycle_state(state, {2}) == {"positions": {"2": {"positive": True}}}

    store = SQLiteStore(tmp_path / "paper.sqlite3")
    _, events = update_position_lifecycle({}, position(), price=100.2, timestamp_ms=1_000)
    store.insert_position_lifecycle_events(events)
    rows = store.list_position_lifecycle_events(symbol="AAAUSDT")
    store.close()

    assert len(rows) == 1
    assert rows[0]["event_type"] == "POSITION_TRACKED"
    assert rows[0]["settings_hash"] == "abc"
