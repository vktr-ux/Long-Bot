from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ScannerSettings(BaseModel):
    scan_interval_seconds: float = Field(default=15, ge=1, le=3600)
    monitor_interval_seconds: float = Field(default=2, ge=0.5, le=60)
    max_enriched_candidates_per_cycle: int = Field(default=30, ge=1, le=250)
    max_concurrent_requests: int = Field(default=3, ge=1, le=20)
    top_activity_rank_candidate: int = Field(default=250, ge=1, le=500)
    save_all_market_snapshots: bool = True
    all_market_snapshot_interval_seconds: int = Field(default=15, ge=1, le=3600)
    attention_scheduler_enabled: bool = True
    attention_waiting_slots: int = Field(default=6, ge=0, le=250)
    attention_hot_slots: int = Field(default=8, ge=0, le=250)
    attention_recent_slots: int = Field(default=5, ge=0, le=250)
    attention_reversal_slots: int = Field(default=3, ge=0, le=250)
    attention_rotation_slots: int = Field(default=8, ge=0, le=250)
    attention_recent_score_floor: float = Field(default=50, ge=0, le=100)
    attention_recent_plan_lookback_minutes: int = Field(default=120, ge=1, le=1440)
    attention_waiting_lookback_minutes: int = Field(default=90, ge=1, le=1440)
    attention_hot_score_cooldown_minutes: float = Field(default=2, ge=0, le=1440)
    attention_near_score_cooldown_minutes: float = Field(default=10, ge=0, le=1440)
    attention_mid_score_cooldown_minutes: float = Field(default=25, ge=0, le=1440)
    attention_low_score_cooldown_minutes: float = Field(default=60, ge=0, le=1440)


class FilterSettings(BaseModel):
    min_quote_volume_24h_usd: float = Field(default=10_000_000, ge=0)
    real_money_min_quote_volume_24h_usd: float = Field(default=30_000_000, ge=0)
    min_volume_24h_usd: float = Field(default=5_000_000, ge=0)
    max_spread_pct: float = Field(default=0.20, ge=0, le=5)
    max_spread_pct_absolute_skip: float = Field(default=0.35, ge=0, le=10)
    min_5m_change_abs_pct: float = Field(default=0.8, ge=0, le=50)
    min_15m_volume_spike: float = Field(default=1.8, ge=0)
    min_price_change_15m_pct_for_candidate: float = Field(default=0.8, ge=0)
    min_price_change_1h_pct_for_candidate: float = Field(default=1.8, ge=0)
    min_volume_spike_for_candidate: float = Field(default=1.4, ge=0)
    exclude_major_symbols: bool = True


class StrategySettings(BaseModel):
    direction_mode: Literal["both", "long_only", "short_only", "auto"] = "both"
    long_signal_execution: Literal["normal", "inverse_short"] = "normal"
    long_enabled: bool = True
    short_enabled: bool = True
    long_min_score: int = Field(default=64, ge=0, le=100)
    inverse_long_min_score: int = Field(default=64, ge=0, le=100)
    short_min_score: int = Field(default=88, ge=0, le=100)
    long_high_conviction_score: int = Field(default=82, ge=0, le=100)
    short_strict_mode: bool = True
    avoid_late_chase: bool = True
    avoid_aggressive_buy_chase: bool = False
    avoid_shorting_strong_momentum: bool = True
    inverse_short_immediate_entry: bool = False
    inverse_short_relaxed_conditions: bool = False
    long_continuation_quality_gate: bool = False
    long_continuation_min_5m_pct: float = Field(default=0.50, ge=0, le=50)
    long_continuation_strong_15m_pct: float = Field(default=2.50, ge=0, le=100)
    long_continuation_top_rank: int = Field(default=10, ge=1, le=500)
    long_pullback_entry_enabled: bool = False
    long_pullback_min_score: int = Field(default=74, ge=0, le=100)
    long_pullback_min_pct: float = Field(default=0.07, ge=0, le=10)
    long_pullback_max_pct: float = Field(default=0.60, ge=0, le=10)
    short_breakdown_entry_enabled: bool = False
    short_breakdown_min_score: int = Field(default=68, ge=0, le=100)
    short_breakdown_min_1m_pct: float = Field(default=0.18, ge=0, le=10)
    short_breakdown_min_5m_pct: float = Field(default=0.35, ge=0, le=20)

    @model_validator(mode="after")
    def ordered_strategy_ranges(self) -> "StrategySettings":
        if self.long_pullback_min_pct > self.long_pullback_max_pct:
            raise ValueError("long_pullback_min_pct must be <= long_pullback_max_pct")
        return self


class RiskSettings(BaseModel):
    starting_balance_usdt: float = Field(default=20.0, gt=0)
    margin_mode: Literal["isolated", "cross"] = "isolated"
    max_open_positions: int = Field(default=5, ge=1, le=50)
    max_new_positions_per_cycle: int = Field(default=2, ge=1, le=50)
    max_position_margin_usdt: float = Field(default=3.0, gt=0)
    max_account_fraction_as_margin: float = Field(default=0.18, gt=0, le=1)
    max_leverage: float = Field(default=12, ge=1, le=125)
    default_leverage: float = Field(default=8, ge=1, le=125)
    maintenance_margin_rate: float = Field(default=0.01, ge=0, le=0.5)
    maintenance_amount_usdt: float = Field(default=0.0, ge=0)
    maintenance_margin_source: Literal["assumed", "binance_leverage_bracket", "binance_position_risk"] = "assumed"
    max_loss_per_trade_usdt: float = Field(default=0.35, gt=0)
    stop_loss_extra_buffer_pct: float = Field(default=0.50, ge=0, le=10)
    max_trades_per_hour: int = Field(default=0, ge=0)
    max_daily_trades: int = Field(default=0, ge=0)
    max_loss_streak: int = Field(default=10, ge=0)
    enforce_daily_loss_limit: bool = False
    max_daily_loss_usdt: float = Field(default=1.0, ge=0)
    symbol_cooldown_minutes: int = Field(default=20, ge=0)
    direction_cooldown_minutes: int = Field(default=5, ge=0)
    stop_loss_symbol_cooldown_minutes: int = Field(default=90, ge=0)
    repeat_loss_symbol_cooldown_minutes: int = Field(default=240, ge=0)
    repeat_loss_symbol_count: int = Field(default=2, ge=0)
    repeat_loss_window_minutes: int = Field(default=360, ge=0)
    cooldown_scope: Literal["active_settings", "all_history"] = "active_settings"

    @model_validator(mode="after")
    def leverage_order(self) -> "RiskSettings":
        if self.default_leverage > self.max_leverage:
            raise ValueError("default_leverage must be <= max_leverage")
        return self


class EntrySettings(BaseModel):
    mode: Literal["confirmation_ladder", "single_market", "pullback_limit"] = "confirmation_ladder"
    legs_enabled: bool = True
    leg_weights: list[float] = Field(default_factory=lambda: [0.70, 0.30])
    max_legs: int = Field(default=2, ge=1, le=5)
    allow_average_down: bool = False
    market_entry_allowed: bool = True
    use_limit_ioc_for_paper_model: bool = True
    require_trigger_confirmation: bool = True
    pullback_long_market_entry: bool = False
    scale_in_enabled: bool = False
    scale_in_step_pct: float = Field(default=0.30, ge=0, le=10)
    scale_in_max_leg_overrun_pct: float = Field(default=0.35, ge=0, le=10)
    trigger_tolerance_pct: float = Field(default=0.02, ge=0, le=5)
    max_entry_distance_above_trigger_pct: float = Field(default=0.45, ge=0, le=50)
    breakout_buffer_pct_min: float = Field(default=0.05, ge=0, le=5)
    breakout_buffer_pct_max: float = Field(default=0.12, ge=0, le=5)
    pullback_confirm_pct: float = Field(default=0.15, ge=0, le=10)
    chase_max_distance_pct: float = Field(default=0.60, ge=0, le=50)

    @field_validator("leg_weights")
    @classmethod
    def leg_weights_sum_to_one(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("leg_weights must not be empty")
        if any(item < 0 for item in value):
            raise ValueError("leg_weights must be non-negative")
        if abs(sum(value) - 1.0) > 0.001:
            raise ValueError("leg_weights must sum to 1.0")
        return value

    @model_validator(mode="after")
    def buffer_order(self) -> "EntrySettings":
        if self.breakout_buffer_pct_min > self.breakout_buffer_pct_max:
            raise ValueError("breakout_buffer_pct_min must be <= breakout_buffer_pct_max")
        if len(self.leg_weights) > self.max_legs:
            raise ValueError("leg_weights length must be <= max_legs")
        return self


class ExitSettings(BaseModel):
    initial_sl_pct_min: float = Field(default=0.45, gt=0, le=20)
    initial_sl_pct_max: float = Field(default=0.95, gt=0, le=20)
    initial_sl_spread_multiplier: float = Field(default=1.5, ge=0)
    initial_sl_atr_multiplier: float = Field(default=0.35, ge=0)
    breakeven_plus_enabled: bool = True
    breakeven_plus_trigger_extra_pct: float = Field(default=0.25, ge=0, le=20)
    min_net_profit_after_breakeven_usdt: float = Field(default=0.02, ge=0)
    preferred_net_profit_after_breakeven_usdt: float = Field(default=0.05, ge=0)
    tp1_enabled: bool = True
    tp1_trigger_pct_min: float = Field(default=0.80, ge=0, le=100)
    tp1_trigger_pct_max: float = Field(default=1.80, ge=0, le=100)
    tp1_extra_after_cost_pct: float = Field(default=0.20, ge=0, le=100)
    tp1_close_fraction: float = Field(default=0.50, gt=0, le=1)
    min_reward_risk_ratio: float = Field(default=0.0, ge=0, le=100)
    enforce_min_reward_risk_ratio: bool = False
    profit_guard_enabled: bool = True
    profit_guard_trigger_pct: float = Field(default=0.30, ge=0, le=100)
    profit_guard_floor_pct: float = Field(default=0.08, ge=0, le=100)
    profit_guard_min_age_seconds: int = Field(default=20, ge=0)
    small_profit_time_exit_enabled: bool = True
    small_profit_time_exit_seconds: int = Field(default=30, ge=0)
    small_profit_time_exit_min_pct: float = Field(default=0.25, ge=0, le=100)
    trailing_enabled: bool = True
    trailing_start_pct_min: float = Field(default=1.20, ge=0, le=100)
    trailing_distance_pct_min: float = Field(default=0.45, ge=0, le=100)
    trailing_distance_pct_max: float = Field(default=0.95, ge=0, le=100)
    trailing_spread_multiplier: float = Field(default=2.0, ge=0)
    trailing_atr_multiplier: float = Field(default=0.40, ge=0)
    time_stop_seconds: int = Field(default=240, ge=0)
    max_hold_seconds: int = Field(default=900, ge=0)

    @model_validator(mode="after")
    def ordered_ranges(self) -> "ExitSettings":
        if self.initial_sl_pct_min > self.initial_sl_pct_max:
            raise ValueError("initial_sl_pct_min must be <= initial_sl_pct_max")
        if self.tp1_trigger_pct_min > self.tp1_trigger_pct_max:
            raise ValueError("tp1_trigger_pct_min must be <= tp1_trigger_pct_max")
        if self.trailing_distance_pct_min > self.trailing_distance_pct_max:
            raise ValueError("trailing_distance_pct_min must be <= trailing_distance_pct_max")
        if self.time_stop_seconds and self.max_hold_seconds and self.time_stop_seconds > self.max_hold_seconds:
            raise ValueError("time_stop_seconds must be <= max_hold_seconds")
        return self


class FeesSettings(BaseModel):
    fee_rate_taker: float = Field(default=0.0004, ge=0, le=0.1)
    fee_rate_maker: float = Field(default=0.0002, ge=0, le=0.1)


class SlippageSettings(BaseModel):
    entry_slippage_bps: float = Field(default=3, ge=0, le=500)
    exit_slippage_bps: float = Field(default=5, ge=0, le=500)


class PositionsSettings(BaseModel):
    allow_duplicate_symbol: bool = False
    allow_opposite_positions_same_symbol: bool = False
    max_open_positions: int = Field(default=5, ge=1, le=50)


class DashboardSettings(BaseModel):
    route_prefix: str = "/bot"
    refresh_fast_seconds: int = Field(default=5, ge=1, le=120)
    refresh_slow_seconds: int = Field(default=20, ge=5, le=300)


class RuntimeTradingSettings(BaseModel):
    trading_mode: Literal["paper", "testnet", "live"] = "paper"
    risk_profile: Literal["exploration_paper", "live_safety"] = "exploration_paper"
    scanner: ScannerSettings = Field(default_factory=ScannerSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    entry: EntrySettings = Field(default_factory=EntrySettings)
    exit: ExitSettings = Field(default_factory=ExitSettings)
    fees: FeesSettings = Field(default_factory=FeesSettings)
    slippage: SlippageSettings = Field(default_factory=SlippageSettings)
    positions: PositionsSettings = Field(default_factory=PositionsSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)

    @model_validator(mode="after")
    def profile_consistency(self) -> "RuntimeTradingSettings":
        if self.positions.max_open_positions != self.risk.max_open_positions:
            self.positions.max_open_positions = self.risk.max_open_positions
        return self


def build_runtime_settings_from_config(config: dict[str, Any]) -> RuntimeTradingSettings:
    paper = config.get("paper", {})
    filters = config.get("filters", {})
    performance = config.get("performance", {})
    storage = config.get("storage", {})
    limits = config.get("limits", {})
    strategy = config.get("strategy", {})
    entry = config.get("entry", {})
    exit_cfg = config.get("exit", {})
    positions = config.get("positions", {})
    dashboard = config.get("dashboard", {})
    symbols = config.get("symbols", {})
    web = config.get("web", {})

    settings = RuntimeTradingSettings(
        trading_mode=config.get("trading_mode") or config.get("app", {}).get("trading_mode") or "paper",
        risk_profile=config.get("risk_profile") or "exploration_paper",
        scanner=ScannerSettings(
            scan_interval_seconds=paper.get("scan_interval_seconds", config.get("app", {}).get("scan_interval_seconds", 15)),
            monitor_interval_seconds=paper.get("monitor_interval_seconds", 2),
            max_enriched_candidates_per_cycle=performance.get("max_enriched_candidates_per_cycle", 30),
            max_concurrent_requests=performance.get("max_concurrent_requests", 3),
            top_activity_rank_candidate=filters.get("top_activity_rank_candidate", 250),
            save_all_market_snapshots=storage.get("save_all_market_snapshots", True),
            all_market_snapshot_interval_seconds=storage.get("all_market_snapshot_interval_seconds", 15),
            attention_scheduler_enabled=performance.get("attention_scheduler_enabled", True),
            attention_waiting_slots=performance.get("attention_waiting_slots", 6),
            attention_hot_slots=performance.get("attention_hot_slots", 8),
            attention_recent_slots=performance.get("attention_recent_slots", 5),
            attention_reversal_slots=performance.get("attention_reversal_slots", 3),
            attention_rotation_slots=performance.get("attention_rotation_slots", 8),
            attention_recent_score_floor=performance.get("attention_recent_score_floor", 50),
            attention_recent_plan_lookback_minutes=performance.get("attention_recent_plan_lookback_minutes", 120),
            attention_waiting_lookback_minutes=performance.get("attention_waiting_lookback_minutes", 90),
            attention_hot_score_cooldown_minutes=performance.get("attention_hot_score_cooldown_minutes", 2),
            attention_near_score_cooldown_minutes=performance.get("attention_near_score_cooldown_minutes", 10),
            attention_mid_score_cooldown_minutes=performance.get("attention_mid_score_cooldown_minutes", 25),
            attention_low_score_cooldown_minutes=performance.get("attention_low_score_cooldown_minutes", 60),
        ),
        filters=FilterSettings(
            min_quote_volume_24h_usd=filters.get("min_quote_volume_24h_usd", filters.get("min_turnover_24h_usd", 10_000_000)),
            real_money_min_quote_volume_24h_usd=filters.get("real_money_min_quote_volume_24h_usd", 30_000_000),
            min_volume_24h_usd=filters.get("min_volume_24h_usd", 5_000_000),
            max_spread_pct=filters.get("max_spread_pct", 0.20),
            max_spread_pct_absolute_skip=filters.get("max_spread_pct_absolute_skip", 0.35),
            min_5m_change_abs_pct=filters.get("min_5m_change_abs_pct", 0.8),
            min_15m_volume_spike=filters.get("min_15m_volume_spike", filters.get("min_volume_spike_for_candidate", 1.8)),
            min_price_change_15m_pct_for_candidate=filters.get("min_price_change_15m_pct_for_candidate", 0.8),
            min_price_change_1h_pct_for_candidate=filters.get("min_price_change_1h_pct_for_candidate", 1.8),
            min_volume_spike_for_candidate=filters.get("min_volume_spike_for_candidate", 1.4),
            exclude_major_symbols=filters.get("exclude_major_symbols", symbols.get("exclude_major_symbols", True)),
        ),
        strategy=StrategySettings(**strategy),
        risk=RiskSettings(
            starting_balance_usdt=paper.get("starting_balance_usdt", 20.0),
            margin_mode=paper.get("margin_mode", "isolated"),
            max_open_positions=positions.get("max_open_positions", paper.get("max_open_positions", 5)),
            max_new_positions_per_cycle=paper.get("max_new_positions_per_cycle", 2),
            max_position_margin_usdt=paper.get("max_position_margin_usdt", 2.0),
            max_account_fraction_as_margin=paper.get("max_account_fraction_as_margin", 0.12),
            max_leverage=paper.get("max_leverage", 10),
            default_leverage=paper.get("default_leverage", 5),
            maintenance_margin_rate=paper.get("maintenance_margin_rate", 0.01),
            maintenance_amount_usdt=paper.get("maintenance_amount_usdt", 0.0),
            maintenance_margin_source=paper.get("maintenance_margin_source", "assumed"),
            max_loss_per_trade_usdt=paper.get("max_loss_per_trade_usdt", 0.20),
            stop_loss_extra_buffer_pct=paper.get("stop_loss_extra_buffer_pct", 0.50),
            max_trades_per_hour=limits.get("max_trades_per_hour", paper.get("max_trades_per_hour", 0)),
            max_daily_trades=limits.get("max_daily_trades", paper.get("max_daily_trades", 0)),
            max_loss_streak=limits.get("max_loss_streak", paper.get("max_loss_streak", 10)),
            enforce_daily_loss_limit=limits.get("enforce_daily_loss_limit", paper.get("enforce_daily_loss_limit", False)),
            max_daily_loss_usdt=limits.get("max_daily_loss_usdt", paper.get("max_daily_loss_usdt", 1.0)),
            symbol_cooldown_minutes=limits.get("symbol_cooldown_minutes", paper.get("symbol_cooldown_minutes", 0)),
            direction_cooldown_minutes=limits.get("direction_cooldown_minutes", paper.get("direction_cooldown_minutes", 0)),
            stop_loss_symbol_cooldown_minutes=limits.get(
                "stop_loss_symbol_cooldown_minutes",
                paper.get("stop_loss_symbol_cooldown_minutes", 90),
            ),
            repeat_loss_symbol_cooldown_minutes=limits.get(
                "repeat_loss_symbol_cooldown_minutes",
                paper.get("repeat_loss_symbol_cooldown_minutes", 240),
            ),
            repeat_loss_symbol_count=limits.get("repeat_loss_symbol_count", paper.get("repeat_loss_symbol_count", 2)),
            repeat_loss_window_minutes=limits.get("repeat_loss_window_minutes", paper.get("repeat_loss_window_minutes", 360)),
            cooldown_scope=limits.get("cooldown_scope", paper.get("cooldown_scope", "active_settings")),
        ),
        entry=EntrySettings(**entry),
        exit=ExitSettings(
            initial_sl_pct_min=exit_cfg.get("initial_sl_pct_min", paper.get("initial_sl_pct_min", 0.45)),
            initial_sl_pct_max=exit_cfg.get("initial_sl_pct_max", paper.get("initial_sl_pct_max", 1.10)),
            initial_sl_spread_multiplier=exit_cfg.get("initial_sl_spread_multiplier", paper.get("initial_sl_spread_multiplier", 1.5)),
            initial_sl_atr_multiplier=exit_cfg.get("initial_sl_atr_multiplier", paper.get("initial_sl_atr_multiplier", 0.35)),
            breakeven_plus_enabled=exit_cfg.get("breakeven_plus_enabled", paper.get("breakeven_plus_enabled", True)),
            breakeven_plus_trigger_extra_pct=exit_cfg.get("breakeven_plus_trigger_extra_pct", paper.get("breakeven_plus_trigger_extra_pct", 0.15)),
            min_net_profit_after_breakeven_usdt=exit_cfg.get("min_net_profit_after_breakeven_usdt", paper.get("min_net_profit_after_breakeven_usdt", 0.02)),
            preferred_net_profit_after_breakeven_usdt=exit_cfg.get("preferred_net_profit_after_breakeven_usdt", paper.get("preferred_net_profit_usdt", 0.05)),
            tp1_enabled=exit_cfg.get("tp1_enabled", paper.get("tp1_enabled", True)),
            tp1_trigger_pct_min=exit_cfg.get("tp1_trigger_pct_min", paper.get("tp1_trigger_pct_min", 0.60)),
            tp1_trigger_pct_max=exit_cfg.get("tp1_trigger_pct_max", paper.get("tp1_trigger_pct_max", 1.20)),
            tp1_extra_after_cost_pct=exit_cfg.get("tp1_extra_after_cost_pct", paper.get("tp1_extra_after_cost_pct", 0.20)),
            tp1_close_fraction=exit_cfg.get("tp1_close_fraction", paper.get("tp1_close_fraction", 0.50)),
            min_reward_risk_ratio=exit_cfg.get("min_reward_risk_ratio", paper.get("min_reward_risk_ratio", 0.0)),
            enforce_min_reward_risk_ratio=exit_cfg.get("enforce_min_reward_risk_ratio", paper.get("enforce_min_reward_risk_ratio", False)),
            profit_guard_enabled=exit_cfg.get("profit_guard_enabled", paper.get("profit_guard_enabled", True)),
            profit_guard_trigger_pct=exit_cfg.get("profit_guard_trigger_pct", paper.get("profit_guard_trigger_pct", 0.30)),
            profit_guard_floor_pct=exit_cfg.get("profit_guard_floor_pct", paper.get("profit_guard_floor_pct", 0.08)),
            profit_guard_min_age_seconds=exit_cfg.get("profit_guard_min_age_seconds", paper.get("profit_guard_min_age_seconds", 20)),
            small_profit_time_exit_enabled=exit_cfg.get("small_profit_time_exit_enabled", paper.get("small_profit_time_exit_enabled", True)),
            small_profit_time_exit_seconds=exit_cfg.get("small_profit_time_exit_seconds", paper.get("small_profit_time_exit_seconds", 30)),
            small_profit_time_exit_min_pct=exit_cfg.get("small_profit_time_exit_min_pct", paper.get("small_profit_time_exit_min_pct", 0.25)),
            trailing_enabled=exit_cfg.get("trailing_enabled", paper.get("trailing_enabled", True)),
            trailing_start_pct_min=exit_cfg.get("trailing_start_pct_min", paper.get("trailing_start_pct_min", 0.75)),
            trailing_distance_pct_min=exit_cfg.get("trailing_distance_pct_min", paper.get("trailing_distance_pct_min", 0.35)),
            trailing_distance_pct_max=exit_cfg.get("trailing_distance_pct_max", paper.get("trailing_distance_pct_max", 0.85)),
            trailing_spread_multiplier=exit_cfg.get("trailing_spread_multiplier", paper.get("trailing_spread_multiplier", 2.0)),
            trailing_atr_multiplier=exit_cfg.get("trailing_atr_multiplier", paper.get("trailing_atr_multiplier", 0.40)),
            time_stop_seconds=exit_cfg.get("time_stop_seconds", paper.get("time_stop_seconds", 180)),
            max_hold_seconds=exit_cfg.get("max_hold_seconds", paper.get("max_hold_seconds", 600)),
        ),
        fees=FeesSettings(
            fee_rate_taker=paper.get("fee_rate_taker", config.get("fees", {}).get("fee_rate_taker", 0.0004)),
            fee_rate_maker=paper.get("fee_rate_maker", config.get("fees", {}).get("fee_rate_maker", 0.0002)),
        ),
        slippage=SlippageSettings(
            entry_slippage_bps=paper.get("entry_slippage_bps", config.get("slippage", {}).get("entry_slippage_bps", 3)),
            exit_slippage_bps=paper.get("exit_slippage_bps", config.get("slippage", {}).get("exit_slippage_bps", 5)),
        ),
        positions=PositionsSettings(
            allow_duplicate_symbol=positions.get("allow_duplicate_symbol", False),
            allow_opposite_positions_same_symbol=positions.get("allow_opposite_positions_same_symbol", False),
            max_open_positions=positions.get("max_open_positions", paper.get("max_open_positions", 5)),
        ),
        dashboard=DashboardSettings(
            route_prefix=web.get("route_prefix", dashboard.get("route_prefix", "/bot")),
            refresh_fast_seconds=dashboard.get("refresh_fast_seconds", 5),
            refresh_slow_seconds=dashboard.get("refresh_slow_seconds", 20),
        ),
    )
    return validate_runtime_settings(settings)


def validate_runtime_settings(settings: RuntimeTradingSettings) -> RuntimeTradingSettings:
    if settings.trading_mode in {"live", "testnet"}:
        unsafe = (
            settings.risk.max_trades_per_hour == 0
            or settings.risk.max_daily_trades == 0
            or settings.risk.max_loss_streak == 0
            or not settings.risk.enforce_daily_loss_limit
        )
        if unsafe and os.getenv("ALLOW_UNSAFE_LIVE_SETTINGS", "").lower() not in {"1", "true", "yes"}:
            raise ValueError("unsafe live/testnet limits require ALLOW_UNSAFE_LIVE_SETTINGS=true")
    return settings


def normalize_settings_payload(payload: dict[str, Any], base_config: dict[str, Any]) -> RuntimeTradingSettings:
    raw = payload.get("settings", payload)
    base = build_runtime_settings_from_config(base_config).model_dump()
    if isinstance(raw, RuntimeTradingSettings):
        return validate_runtime_settings(raw)
    if not isinstance(raw, dict):
        raise ValueError("settings payload must be an object")
    merged = _deep_merge(base, raw)
    try:
        return validate_runtime_settings(RuntimeTradingSettings.model_validate(merged))
    except ValidationError as exc:
        raise ValueError("; ".join(error["msg"] for error in exc.errors())) from exc


def runtime_settings_hash(settings: RuntimeTradingSettings | dict[str, Any]) -> str:
    data = settings.model_dump(mode="json") if isinstance(settings, RuntimeTradingSettings) else settings
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def apply_runtime_settings_to_config(
    config: dict[str, Any],
    settings: RuntimeTradingSettings,
    *,
    version: int | None = None,
    settings_hash: str | None = None,
) -> dict[str, Any]:
    merged = deepcopy(config)
    settings_hash = settings_hash or runtime_settings_hash(settings)
    merged["trading_mode"] = settings.trading_mode
    merged["risk_profile"] = settings.risk_profile
    merged.setdefault("app", {})["trading_mode"] = settings.trading_mode
    merged.setdefault("runtime_settings", {}).update(
        {"version": version, "hash": settings_hash, "settings": settings.model_dump(mode="json")}
    )

    merged.setdefault("filters", {}).update(settings.filters.model_dump())
    merged["filters"]["top_activity_rank_candidate"] = settings.scanner.top_activity_rank_candidate
    merged.setdefault("symbols", {})["exclude_major_symbols"] = settings.filters.exclude_major_symbols
    merged.setdefault("performance", {}).update(
        {
            "max_enriched_candidates_per_cycle": settings.scanner.max_enriched_candidates_per_cycle,
            "max_concurrent_requests": settings.scanner.max_concurrent_requests,
            "attention_scheduler_enabled": settings.scanner.attention_scheduler_enabled,
            "attention_waiting_slots": settings.scanner.attention_waiting_slots,
            "attention_hot_slots": settings.scanner.attention_hot_slots,
            "attention_recent_slots": settings.scanner.attention_recent_slots,
            "attention_reversal_slots": settings.scanner.attention_reversal_slots,
            "attention_rotation_slots": settings.scanner.attention_rotation_slots,
            "attention_recent_score_floor": settings.scanner.attention_recent_score_floor,
            "attention_recent_plan_lookback_minutes": settings.scanner.attention_recent_plan_lookback_minutes,
            "attention_waiting_lookback_minutes": settings.scanner.attention_waiting_lookback_minutes,
            "attention_hot_score_cooldown_minutes": settings.scanner.attention_hot_score_cooldown_minutes,
            "attention_near_score_cooldown_minutes": settings.scanner.attention_near_score_cooldown_minutes,
            "attention_mid_score_cooldown_minutes": settings.scanner.attention_mid_score_cooldown_minutes,
            "attention_low_score_cooldown_minutes": settings.scanner.attention_low_score_cooldown_minutes,
        }
    )
    merged.setdefault("storage", {}).update(
        {
            "save_all_market_snapshots": settings.scanner.save_all_market_snapshots,
            "all_market_snapshot_interval_seconds": settings.scanner.all_market_snapshot_interval_seconds,
        }
    )
    merged["strategy"] = settings.strategy.model_dump()
    merged["entry"] = settings.entry.model_dump()
    merged["exit"] = settings.exit.model_dump()
    merged["fees"] = settings.fees.model_dump()
    merged["slippage"] = settings.slippage.model_dump()
    merged["positions"] = settings.positions.model_dump()
    merged.setdefault("dashboard", {}).update(settings.dashboard.model_dump())
    merged.setdefault("web", {})["route_prefix"] = settings.dashboard.route_prefix

    paper_update = {
        "starting_balance_usdt": settings.risk.starting_balance_usdt,
        "margin_mode": settings.risk.margin_mode,
        "scan_interval_seconds": settings.scanner.scan_interval_seconds,
        "monitor_interval_seconds": settings.scanner.monitor_interval_seconds,
        "max_open_positions": settings.risk.max_open_positions,
        "max_new_positions_per_cycle": settings.risk.max_new_positions_per_cycle,
        "max_position_margin_usdt": settings.risk.max_position_margin_usdt,
        "max_account_fraction_as_margin": settings.risk.max_account_fraction_as_margin,
        "max_leverage": settings.risk.max_leverage,
        "default_leverage": settings.risk.default_leverage,
        "maintenance_margin_rate": settings.risk.maintenance_margin_rate,
        "maintenance_amount_usdt": settings.risk.maintenance_amount_usdt,
        "maintenance_margin_source": settings.risk.maintenance_margin_source,
        "max_loss_per_trade_usdt": settings.risk.max_loss_per_trade_usdt,
        "stop_loss_extra_buffer_pct": settings.risk.stop_loss_extra_buffer_pct,
        "max_trades_per_hour": settings.risk.max_trades_per_hour,
        "max_daily_trades": settings.risk.max_daily_trades,
        "max_loss_streak": settings.risk.max_loss_streak,
        "enforce_daily_loss_limit": settings.risk.enforce_daily_loss_limit,
        "max_daily_loss_usdt": settings.risk.max_daily_loss_usdt,
        "symbol_cooldown_minutes": settings.risk.symbol_cooldown_minutes,
        "direction_cooldown_minutes": settings.risk.direction_cooldown_minutes,
        "stop_loss_symbol_cooldown_minutes": settings.risk.stop_loss_symbol_cooldown_minutes,
        "repeat_loss_symbol_cooldown_minutes": settings.risk.repeat_loss_symbol_cooldown_minutes,
        "repeat_loss_symbol_count": settings.risk.repeat_loss_symbol_count,
        "repeat_loss_window_minutes": settings.risk.repeat_loss_window_minutes,
        "cooldown_scope": settings.risk.cooldown_scope,
        "fee_rate_taker": settings.fees.fee_rate_taker,
        "fee_rate_maker": settings.fees.fee_rate_maker,
        "entry_slippage_bps": settings.slippage.entry_slippage_bps,
        "exit_slippage_bps": settings.slippage.exit_slippage_bps,
        "min_net_profit_after_breakeven_usdt": settings.exit.min_net_profit_after_breakeven_usdt,
        "preferred_net_profit_usdt": settings.exit.preferred_net_profit_after_breakeven_usdt,
        "time_stop_seconds": settings.exit.time_stop_seconds,
        "max_hold_seconds": settings.exit.max_hold_seconds,
        "initial_sl_pct_min": settings.exit.initial_sl_pct_min,
        "initial_sl_pct_max": settings.exit.initial_sl_pct_max,
        "initial_sl_spread_multiplier": settings.exit.initial_sl_spread_multiplier,
        "initial_sl_atr_multiplier": settings.exit.initial_sl_atr_multiplier,
        "breakeven_plus_enabled": settings.exit.breakeven_plus_enabled,
        "breakeven_plus_trigger_extra_pct": settings.exit.breakeven_plus_trigger_extra_pct,
        "tp1_enabled": settings.exit.tp1_enabled,
        "tp1_trigger_pct_min": settings.exit.tp1_trigger_pct_min,
        "tp1_trigger_pct_max": settings.exit.tp1_trigger_pct_max,
        "tp1_extra_after_cost_pct": settings.exit.tp1_extra_after_cost_pct,
        "tp1_close_fraction": settings.exit.tp1_close_fraction,
        "min_reward_risk_ratio": settings.exit.min_reward_risk_ratio,
        "enforce_min_reward_risk_ratio": settings.exit.enforce_min_reward_risk_ratio,
        "profit_guard_enabled": settings.exit.profit_guard_enabled,
        "profit_guard_trigger_pct": settings.exit.profit_guard_trigger_pct,
        "profit_guard_floor_pct": settings.exit.profit_guard_floor_pct,
        "profit_guard_min_age_seconds": settings.exit.profit_guard_min_age_seconds,
        "small_profit_time_exit_enabled": settings.exit.small_profit_time_exit_enabled,
        "small_profit_time_exit_seconds": settings.exit.small_profit_time_exit_seconds,
        "small_profit_time_exit_min_pct": settings.exit.small_profit_time_exit_min_pct,
        "trailing_enabled": settings.exit.trailing_enabled,
        "trailing_start_pct_min": settings.exit.trailing_start_pct_min,
        "trailing_distance_pct_min": settings.exit.trailing_distance_pct_min,
        "trailing_distance_pct_max": settings.exit.trailing_distance_pct_max,
        "trailing_spread_multiplier": settings.exit.trailing_spread_multiplier,
        "trailing_atr_multiplier": settings.exit.trailing_atr_multiplier,
    }
    merged.setdefault("paper", {}).update(paper_update)
    merged["limits"] = {
        "max_trades_per_hour": settings.risk.max_trades_per_hour,
        "max_daily_trades": settings.risk.max_daily_trades,
        "max_loss_streak": settings.risk.max_loss_streak,
        "enforce_daily_loss_limit": settings.risk.enforce_daily_loss_limit,
        "max_daily_loss_usdt": settings.risk.max_daily_loss_usdt,
        "symbol_cooldown_minutes": settings.risk.symbol_cooldown_minutes,
        "direction_cooldown_minutes": settings.risk.direction_cooldown_minutes,
        "stop_loss_symbol_cooldown_minutes": settings.risk.stop_loss_symbol_cooldown_minutes,
        "repeat_loss_symbol_cooldown_minutes": settings.risk.repeat_loss_symbol_cooldown_minutes,
        "repeat_loss_symbol_count": settings.risk.repeat_loss_symbol_count,
        "repeat_loss_window_minutes": settings.risk.repeat_loss_window_minutes,
        "cooldown_scope": settings.risk.cooldown_scope,
    }
    return merged


def settings_to_yaml(settings: RuntimeTradingSettings) -> str:
    return yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, allow_unicode=False)
