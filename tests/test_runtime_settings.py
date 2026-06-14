import pytest

from app.config import load_config
from app.runtime_settings import (
    RuntimeTradingSettings,
    apply_runtime_settings_to_config,
    build_runtime_settings_from_config,
    normalize_settings_payload,
    runtime_settings_hash,
)
from app.storage.db import SQLiteStore


def test_runtime_settings_defaults_match_paper_exploration_profile(tmp_path):
    config = load_config("config.paper.yaml")
    config["app"]["database_path"] = str(tmp_path / "paper.sqlite3")
    settings = build_runtime_settings_from_config(config)
    assert settings.trading_mode == "paper"
    assert settings.risk.max_open_positions == 5
    assert settings.risk.max_daily_trades == 0
    assert settings.risk.max_trades_per_hour == 0
    assert settings.risk.max_loss_streak == 10
    assert not settings.risk.enforce_daily_loss_limit
    assert settings.scanner.top_activity_rank_candidate == 250
    assert settings.scanner.max_enriched_candidates_per_cycle == 30
    assert settings.scanner.attention_scheduler_enabled
    assert settings.scanner.attention_rotation_slots == 8
    assert settings.strategy.long_signal_execution == "normal"
    assert settings.strategy.inverse_long_min_score == 64
    assert settings.strategy.inverse_short_immediate_entry is False
    assert settings.strategy.inverse_short_relaxed_conditions is False
    assert settings.strategy.long_continuation_quality_gate is False
    assert settings.strategy.long_continuation_min_5m_pct == 0.5
    assert settings.strategy.long_continuation_strong_15m_pct == 2.5
    assert settings.strategy.long_continuation_top_rank == 10
    assert settings.strategy.long_pullback_entry_enabled is False
    assert settings.strategy.short_breakdown_entry_enabled is False
    assert settings.entry.pullback_long_market_entry is False
    assert settings.entry.scale_in_enabled is False
    assert settings.entry.scale_in_step_pct == 0.3
    assert settings.entry.scale_in_max_leg_overrun_pct == 0.35
    assert settings.entry.scale_in_reclaim_pct == 0.04
    assert settings.risk.stop_loss_extra_buffer_pct == 0.5
    assert settings.risk.stop_loss_symbol_cooldown_minutes == 90
    assert settings.risk.repeat_loss_symbol_count == 2
    assert settings.risk.cooldown_scope == "active_settings"


def test_runtime_settings_apply_increments_version_and_audit(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    settings = RuntimeTradingSettings()
    initial = store.ensure_runtime_settings(settings.model_dump(mode="json"), runtime_settings_hash(settings))
    assert initial["version"] == 1

    updated = settings.model_copy(deep=True)
    updated.risk.max_open_positions = 4
    row = store.apply_runtime_settings(
        updated.model_dump(mode="json"),
        runtime_settings_hash(updated),
        changed_by="test",
        comment="lower position cap",
        diff={"risk": {"max_open_positions": {"from": 5, "to": 4}}},
    )
    assert row["version"] == 2
    assert row["settings"]["risk"]["max_open_positions"] == 4
    assert store.list_settings_audit_log()[0]["new_version"] == 2
    store.close()


def test_reset_paper_account_keeps_history_but_resets_budget(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    account_id = store.ensure_paper_account(starting_balance_usdt=20)
    store.update_paper_account_totals(account_id, realized_delta=-3, fee_delta=0.5, slippage_delta=0.4)

    store.reset_paper_account(account_id, 20)

    account = store.get_paper_account(account_id)
    assert account["start_balance_usdt"] == 20
    assert account["cash_balance_usdt"] == 20
    assert account["equity_usdt"] == 20
    assert account["realized_pnl_usdt"] == 0
    assert account["total_fees_usdt"] == 0
    assert account["total_slippage_usdt"] == 0
    store.close()


def test_runtime_settings_db_override_applies_to_config(tmp_path):
    config = load_config("config.paper.yaml")
    config["app"]["database_path"] = str(tmp_path / "paper.sqlite3")
    settings = build_runtime_settings_from_config(config)
    settings.strategy.short_min_score = 95
    settings.strategy.long_signal_execution = "inverse_short"
    settings.strategy.inverse_short_immediate_entry = False
    settings.strategy.inverse_short_relaxed_conditions = True
    settings.strategy.long_continuation_quality_gate = True
    settings.strategy.long_continuation_min_5m_pct = 0.7
    settings.strategy.long_continuation_strong_15m_pct = 3.0
    settings.strategy.long_continuation_top_rank = 8
    settings.strategy.long_pullback_entry_enabled = True
    settings.strategy.short_breakdown_entry_enabled = True
    settings.entry.pullback_long_market_entry = True
    settings.entry.scale_in_enabled = True
    settings.entry.scale_in_step_pct = 0.25
    settings.entry.scale_in_max_leg_overrun_pct = 0.4
    settings.entry.scale_in_reclaim_pct = 0.05
    settings.risk.cooldown_scope = "all_history"
    effective = apply_runtime_settings_to_config(config, settings, version=7, settings_hash=runtime_settings_hash(settings))
    assert effective["strategy"]["short_min_score"] == 95
    assert effective["strategy"]["long_signal_execution"] == "inverse_short"
    assert effective["strategy"]["inverse_short_immediate_entry"] is False
    assert effective["strategy"]["inverse_short_relaxed_conditions"] is True
    assert effective["strategy"]["long_continuation_quality_gate"] is True
    assert effective["strategy"]["long_continuation_min_5m_pct"] == 0.7
    assert effective["strategy"]["long_continuation_strong_15m_pct"] == 3.0
    assert effective["strategy"]["long_continuation_top_rank"] == 8
    assert effective["strategy"]["long_pullback_entry_enabled"] is True
    assert effective["strategy"]["short_breakdown_entry_enabled"] is True
    assert effective["entry"]["pullback_long_market_entry"] is True
    assert effective["entry"]["scale_in_enabled"] is True
    assert effective["entry"]["scale_in_step_pct"] == 0.25
    assert effective["entry"]["scale_in_max_leg_overrun_pct"] == 0.4
    assert effective["entry"]["scale_in_reclaim_pct"] == 0.05
    assert effective["runtime_settings"]["version"] == 7
    assert effective["paper"]["max_daily_trades"] == 0
    assert effective["paper"]["stop_loss_extra_buffer_pct"] == 0.5
    assert effective["performance"]["attention_scheduler_enabled"]
    assert effective["paper"]["stop_loss_symbol_cooldown_minutes"] == 90
    assert effective["paper"]["cooldown_scope"] == "all_history"
    assert effective["limits"]["cooldown_scope"] == "all_history"


def test_runtime_settings_validation_rejects_invalid_ranges():
    with pytest.raises(ValueError):
        normalize_settings_payload({"entry": {"leg_weights": [0.9, 0.9]}}, load_config("config.paper.yaml"))
    with pytest.raises(ValueError):
        normalize_settings_payload({"exit": {"trailing_distance_pct_min": 2, "trailing_distance_pct_max": 1}}, load_config("config.paper.yaml"))
    with pytest.raises(ValueError):
        normalize_settings_payload({"fees": {"fee_rate_taker": -0.1}}, load_config("config.paper.yaml"))
