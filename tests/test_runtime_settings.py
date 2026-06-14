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


def test_runtime_settings_db_override_applies_to_config(tmp_path):
    config = load_config("config.paper.yaml")
    config["app"]["database_path"] = str(tmp_path / "paper.sqlite3")
    settings = build_runtime_settings_from_config(config)
    settings.strategy.short_min_score = 95
    settings.strategy.long_signal_execution = "inverse_short"
    settings.strategy.inverse_short_immediate_entry = False
    settings.risk.cooldown_scope = "all_history"
    effective = apply_runtime_settings_to_config(config, settings, version=7, settings_hash=runtime_settings_hash(settings))
    assert effective["strategy"]["short_min_score"] == 95
    assert effective["strategy"]["long_signal_execution"] == "inverse_short"
    assert effective["strategy"]["inverse_short_immediate_entry"] is False
    assert effective["runtime_settings"]["version"] == 7
    assert effective["paper"]["max_daily_trades"] == 0
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
