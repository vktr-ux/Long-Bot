from types import SimpleNamespace

from app.storage.models import TickerSnapshot
from app.trading.runner import account_can_open, cooldown_reason, entry_trigger_status, new_entries_block_reason
from app.utils.time import now_ms


class FakeStore:
    def __init__(self, open_positions, trades, *, pending_reset=None, active_settings=None):
        self._open_positions = open_positions
        self._trades = trades
        self._pending_reset = pending_reset
        self._active_settings = active_settings

    def get_open_positions(self, account_id):
        return self._open_positions

    def get_bot_state(self, key, default=None):
        if key == "pending_account_reset":
            return self._pending_reset
        return default

    def get_active_runtime_settings(self):
        return self._active_settings

    def list_trades(self, from_ms=None, to_ms=None, symbol=None, direction=None, exit_reason=None, limit=1000):
        rows = self._trades
        if from_ms is not None:
            rows = [row for row in rows if int(row.get("exit_time_ms", now_ms())) >= from_ms]
        if symbol:
            rows = [row for row in rows if row.get("symbol") == symbol.upper()]
        if direction:
            rows = [row for row in rows if row.get("direction") == direction.upper()]
        return rows[:limit]


def _trade(net=0.01, symbol="AAAUSDT", direction="LONG", age_ms=0, settings_hash=None):
    return {
        "net_pnl_usdt": net,
        "symbol": symbol,
        "direction": direction,
        "exit_time_ms": now_ms() - age_ms,
        "exit_reason": "STOP_LOSS" if net < 0 else "BREAKEVEN_PLUS_STOP",
        "settings_hash": settings_hash,
    }


def test_daily_trade_limit_counts_open_positions_opened_today():
    store = FakeStore(
        open_positions=[{"symbol": "AAAUSDT", "opened_at_ms": now_ms()}],
        trades=[_trade() for _ in range(14)],
    )
    can_open, reason = account_can_open(
        store,
        account_id=1,
        config={"paper": {"max_open_positions": 5, "max_daily_trades": 15, "max_daily_loss_usdt": 100, "max_loss_streak": 15}},
    )
    assert not can_open
    assert reason == "max daily trades reached"


def test_zero_trade_limits_and_zero_loss_streak_are_unlimited():
    store = FakeStore(
        open_positions=[],
        trades=[_trade(-0.01) for _ in range(25)],
    )
    can_open, reason = account_can_open(
        store,
        account_id=1,
        config={"paper": {"max_open_positions": 5, "max_daily_trades": 0, "max_trades_per_hour": 0, "max_daily_loss_usdt": 100, "max_loss_streak": 0}},
    )
    assert can_open
    assert reason is None


def test_symbol_and_direction_cooldowns_block_reentry():
    store = FakeStore(open_positions=[], trades=[_trade(-0.05, symbol="AAAUSDT", direction="LONG", age_ms=5 * 60 * 1000)])
    assert cooldown_reason(store, "AAAUSDT", "LONG", {"paper": {"symbol_cooldown_minutes": 20, "direction_cooldown_minutes": 0}}).startswith("symbol cooldown")
    assert cooldown_reason(store, "BBBUSDT", "LONG", {"paper": {"symbol_cooldown_minutes": 0, "direction_cooldown_minutes": 10}}).startswith("LONG cooldown")
    assert cooldown_reason(store, "AAAUSDT", "LONG", {"paper": {"symbol_cooldown_minutes": 1, "direction_cooldown_minutes": 1}}) is None


def test_stop_loss_symbol_cooldown_blocks_losing_symbol_longer_than_generic_pause():
    store = FakeStore(open_positions=[], trades=[_trade(-0.05, symbol="AAAUSDT", direction="LONG", age_ms=45 * 60 * 1000)])

    reason = cooldown_reason(
        store,
        "AAAUSDT",
        "LONG",
        {"paper": {"symbol_cooldown_minutes": 20, "direction_cooldown_minutes": 0, "stop_loss_symbol_cooldown_minutes": 90}},
    )

    assert reason.startswith("symbol loss cooldown")


def test_symbol_loss_cooldown_can_be_scoped_to_active_settings_hash():
    store = FakeStore(open_positions=[], trades=[_trade(-0.05, symbol="AAAUSDT", direction="LONG", age_ms=5 * 60 * 1000, settings_hash="old")])

    scoped = cooldown_reason(
        store,
        "AAAUSDT",
        "LONG",
        {
            "runtime_settings": {"hash": "new"},
            "paper": {
                "cooldown_scope": "active_settings",
                "symbol_cooldown_minutes": 0,
                "direction_cooldown_minutes": 0,
                "stop_loss_symbol_cooldown_minutes": 90,
            },
        },
    )
    assert scoped is None

    global_scope = cooldown_reason(
        store,
        "AAAUSDT",
        "LONG",
        {
            "runtime_settings": {"hash": "new"},
            "paper": {
                "cooldown_scope": "all_history",
                "symbol_cooldown_minutes": 0,
                "direction_cooldown_minutes": 0,
                "stop_loss_symbol_cooldown_minutes": 90,
            },
        },
    )
    assert global_scope.startswith("symbol loss cooldown")

    store = FakeStore(open_positions=[], trades=[_trade(-0.05, symbol="AAAUSDT", direction="LONG", age_ms=5 * 60 * 1000, settings_hash="new")])
    active_scope = cooldown_reason(
        store,
        "AAAUSDT",
        "LONG",
        {
            "runtime_settings": {"hash": "new"},
            "paper": {
                "cooldown_scope": "active_settings",
                "symbol_cooldown_minutes": 0,
                "direction_cooldown_minutes": 0,
                "stop_loss_symbol_cooldown_minutes": 90,
            },
        },
    )
    assert active_scope.startswith("symbol loss cooldown")


def test_repeat_loss_symbol_cooldown_blocks_clustered_symbol_losses():
    store = FakeStore(
        open_positions=[],
        trades=[
            _trade(-0.05, symbol="AAAUSDT", direction="LONG", age_ms=45 * 60 * 1000),
            _trade(-0.03, symbol="AAAUSDT", direction="LONG", age_ms=180 * 60 * 1000),
        ],
    )

    reason = cooldown_reason(
        store,
        "AAAUSDT",
        "LONG",
        {
            "paper": {
                "symbol_cooldown_minutes": 20,
                "direction_cooldown_minutes": 0,
                "stop_loss_symbol_cooldown_minutes": 0,
                "repeat_loss_symbol_count": 2,
                "repeat_loss_window_minutes": 360,
                "repeat_loss_symbol_cooldown_minutes": 240,
            }
        },
    )

    assert reason.startswith("symbol repeat-loss cooldown")


def test_entry_trigger_confirmation_blocks_early_and_late_chase_entries():
    cfg = {"entry": {"mode": "confirmation_ladder", "require_trigger_confirmation": True, "trigger_tolerance_pct": 0.02, "max_entry_distance_above_trigger_pct": 0.45}}
    plan = SimpleNamespace(direction="LONG", entry_grid=[{"trigger_price": 101.0}])

    ready, reason = entry_trigger_status(plan, TickerSnapshot(now_ms(), "binance", "AAAUSDT", 100.5, ask_price=100.5), cfg)
    assert not ready
    assert "waiting entry trigger" in reason

    ready, reason = entry_trigger_status(plan, TickerSnapshot(now_ms(), "binance", "AAAUSDT", 101.1, ask_price=101.1), cfg)
    assert ready
    assert reason is None

    ready, reason = entry_trigger_status(plan, TickerSnapshot(now_ms(), "binance", "AAAUSDT", 101.7, ask_price=101.7), cfg)
    assert not ready
    assert "overrun" in reason


def test_new_entries_are_blocked_during_pending_reset_or_stale_runtime_config():
    config = {"runtime_settings": {"version": 24, "hash": "runner-hash"}}

    pending_store = FakeStore(open_positions=[], trades=[], pending_reset={"settings_version": 25})
    assert new_entries_block_reason(pending_store, config) == "account reset pending"

    stale_version_store = FakeStore(open_positions=[], trades=[], active_settings={"version": 25, "settings_hash": "runner-hash"})
    assert new_entries_block_reason(stale_version_store, config).startswith("settings changed mid-cycle")

    stale_hash_store = FakeStore(open_positions=[], trades=[], active_settings={"version": 24, "settings_hash": "active-hash"})
    assert new_entries_block_reason(stale_hash_store, config).startswith("settings changed mid-cycle")

    current_store = FakeStore(open_positions=[], trades=[], active_settings={"version": 24, "settings_hash": "runner-hash"})
    assert new_entries_block_reason(current_store, config) is None
