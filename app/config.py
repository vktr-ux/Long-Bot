from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "exchange": "binance",
        "scan_interval_seconds": 60,
        "dry_run": False,
        "log_level": "INFO",
        "database_path": "data/scanner.sqlite3",
        "profile": "normal",
    },
    "exchanges": {
        "bybit": {
            "enabled": False,
            "base_url": "https://api.bybit.com",
            "category": "linear",
        },
        "binance": {"enabled": True, "base_url": "https://fapi.binance.com"},
    },
    "symbols": {
        "quote_asset": "USDT",
        "exclude_major_symbols": True,
        "major_symbols": ["BTCUSDT", "ETHUSDT"],
        "blacklist": [],
        "include_symbols": [],
    },
    "filters": {
        "min_turnover_24h_usd": 10_000_000,
        "min_quote_volume_24h_usd": 10_000_000,
        "real_money_min_quote_volume_24h_usd": 30_000_000,
        "min_volume_24h_usd": 5_000_000,
        "max_spread_pct": 0.20,
        "max_spread_pct_absolute_skip": 0.35,
        "min_5m_change_abs_pct": 0.8,
        "min_15m_volume_spike": 1.8,
        "min_price_change_15m_pct_for_candidate": 1.5,
        "min_price_change_1h_pct_for_candidate": 3.0,
        "min_volume_spike_for_candidate": 2.0,
        "top_activity_rank_candidate": 250,
    },
    "metrics": {
        "volume_spike_lookback_periods": 12,
        "candle_limit_1m": 300,
        "candle_limit_5m": 200,
        "candle_limit_15m": 160,
        "candle_limit_60m": 120,
        "candle_limit_240m": 120,
        "candle_limit_1d": 90,
    },
    "breakout": {
        "enabled": True,
        "timeframe": "240",
        "lookback_candles": 90,
        "swing_window": 2,
        "min_touches": 2,
        "zone_tolerance_pct": 0.015,
        "atr_tolerance_mult": 0.35,
        "breakout_buffer_pct": 0.006,
        "max_distance_above_zone_pct": 0.12,
        "approach_distance_pct": 0.02,
        "require_volume_confirmation": True,
    },
    "setup_quality": {
        "enabled": True,
        "min_room_to_target_pct": 5.0,
        "min_estimated_rr_for_hot": 1.5,
        "min_estimated_rr_for_breakout_hot": 1.8,
        "invalidation_atr_mult_breakout": 0.5,
        "invalidation_atr_mult_retest": 0.25,
        "target_atr_extension_mult": 2.0,
        "round_level_detection": True,
    },
    "breakout_upgrade": {
        "enabled": True,
        "max_score_gap_below_watch": 5,
        "min_volume_spike_15m": 1.8,
        "min_rr": 1.5,
        "max_chase_risk": "MEDIUM",
        "allow_hot_rsi_with_warning": True,
        "require_funding_not_overheated": True,
        "require_oi_not_strongly_negative": True,
    },
    "rsi": {
        "enabled": True,
        "period": 14,
        "warning_15m": 80,
        "warning_1h": 80,
        "warning_4h": 80,
        "danger_15m": 85,
        "danger_1h": 85,
        "danger_4h": 80,
    },
    "funding": {"good_max": 0.0003, "caution_min": 0.0005, "danger_min": 0.0010},
    "btc_filter": {"symbol": "BTCUSDT", "bad_15m_pct": -1.5, "bad_1h_pct": -3.0, "bad_4h_pct": -5.0},
    "scoring": {"levels": {"watch": 50, "hot": 65, "breakout_hot": 80, "very_hot": 90}},
    "cooldown": {
        "default_minutes": 30,
        "very_hot_minutes": 10,
        "score_increase_to_repeat": 10,
        "reset_after_minutes": 90,
        "allow_immediate_state_transition_alert": True,
    },
    "notifications": {
        "telegram_enabled": True,
        "min_level_to_send": "WATCH",
        "per_symbol_cooldown_minutes": {
            "WATCH": 90,
            "HOT": 45,
            "BREAKOUT_HOT": 30,
            "VERY_HOT": 15,
        },
        "repeat_rules": {
            "allow_if_level_upgraded": True,
            "allow_if_score_increased_by": 10,
            "allow_if_breakout_state_changed": True,
            "allow_if_price_moved_pct_since_last_alert": 5.0,
            "allow_if_new_target_or_invalidation": True,
        },
        "global_rate_limit": {
            "max_alerts_per_hour": 8,
            "max_alerts_per_10_minutes": 3,
        },
        "heartbeat": {"enabled": True, "interval_minutes": 360},
        "digest": {"enabled": False, "interval_minutes": 60},
    },
    "performance": {
        "max_enriched_candidates_per_cycle": 30,
        "max_concurrent_requests": 3,
        "binance_request_weight_limit_per_minute": 900,
        "binance_min_request_interval_seconds": 0.08,
        "symbol_universe_refresh_minutes": 20,
        "resistance_cache_ttl_minutes": 15,
        "orderbook_check_only_for_score_above": 55,
        "attention_scheduler_enabled": True,
        "attention_waiting_slots": 6,
        "attention_hot_slots": 8,
        "attention_recent_slots": 5,
        "attention_reversal_slots": 3,
        "attention_rotation_slots": 8,
        "attention_recent_score_floor": 50,
        "attention_recent_plan_lookback_minutes": 120,
        "attention_recent_plan_limit": 2000,
        "attention_waiting_lookback_minutes": 90,
        "attention_hot_score_threshold": 64,
        "attention_hot_score_cooldown_minutes": 2,
        "attention_near_score_cooldown_minutes": 10,
        "attention_mid_score_cooldown_minutes": 25,
        "attention_low_score_cooldown_minutes": 60,
        "attention_force_hot_activity_score": 80,
        "attention_force_hot_cooldown_seconds": 45,
        "attention_reversal_downside_score_floor": 18,
        "attention_state_ttl_minutes": 360,
        "attention_state_max_symbols": 600,
    },
    "market_data": {
        "use_websocket": True,
        "binance_market_stream_url": "wss://fstream.binance.com/market/stream?streams=!ticker@arr/!markPrice@arr",
        "binance_public_stream_url": "wss://fstream.binance.com/public/ws/!bookTicker",
        "websocket_reconnect_delay_seconds": 5,
        "websocket_fallback_to_rest_after_seconds": 45,
    },
    "storage": {
        "save_all_market_snapshots": True,
        "all_market_snapshot_interval_seconds": 60,
    },
    "paper": {
        "account_name": "main",
        "starting_balance_usdt": 20.0,
        "margin_mode": "isolated",
        "scan_interval_seconds": 15,
        "monitor_interval_seconds": 2,
        "max_open_positions": 5,
        "max_new_positions_per_cycle": 2,
        "max_position_margin_usdt": 2.0,
        "max_account_fraction_as_margin": 0.12,
        "max_leverage": 10,
        "default_leverage": 5,
        "maintenance_margin_rate": 0.01,
        "maintenance_amount_usdt": 0.0,
        "maintenance_margin_source": "assumed",
        "max_loss_per_trade_usdt": 0.20,
        "stop_loss_extra_buffer_pct": 0.50,
        "max_trades_per_hour": 0,
        "max_daily_loss_usdt": 1.00,
        "max_daily_trades": 0,
        "max_loss_streak": 10,
        "enforce_daily_loss_limit": False,
        "symbol_cooldown_minutes": 0,
        "direction_cooldown_minutes": 0,
        "stop_loss_symbol_cooldown_minutes": 90,
        "repeat_loss_symbol_cooldown_minutes": 240,
        "repeat_loss_symbol_count": 2,
        "repeat_loss_window_minutes": 360,
        "cooldown_scope": "active_settings",
        "fee_rate_taker": 0.0004,
        "fee_rate_maker": 0.0002,
        "entry_slippage_bps": 3,
        "exit_slippage_bps": 5,
        "min_net_profit_after_breakeven_usdt": 0.02,
        "preferred_net_profit_usdt": 0.05,
        "time_stop_seconds": 180,
        "max_hold_seconds": 600,
        "fallback_min_notional_usdt": 5.0,
        "fallback_step_size": 0.001,
        "persist_rejected_plans_per_cycle": 20,
    },
    "strategy": {
        "direction_mode": "both",
        "long_signal_execution": "normal",
        "long_enabled": True,
        "short_enabled": True,
        "long_min_score": 64,
        "inverse_long_min_score": 64,
        "short_min_score": 88,
        "short_strict_mode": True,
        "avoid_late_chase": True,
        "avoid_aggressive_buy_chase": False,
        "avoid_shorting_strong_momentum": True,
        "inverse_short_immediate_entry": False,
    },
    "entry": {
        "mode": "confirmation_ladder",
        "legs_enabled": True,
        "leg_weights": [0.70, 0.30],
        "max_legs": 2,
        "allow_average_down": False,
        "market_entry_allowed": True,
        "use_limit_ioc_for_paper_model": True,
        "require_trigger_confirmation": True,
        "trigger_tolerance_pct": 0.02,
        "max_entry_distance_above_trigger_pct": 0.45,
        "breakout_buffer_pct_min": 0.05,
        "breakout_buffer_pct_max": 0.12,
        "pullback_confirm_pct": 0.15,
        "chase_max_distance_pct": 0.60,
    },
    "exit": {
        "initial_sl_pct_min": 0.45,
        "initial_sl_pct_max": 1.10,
        "initial_sl_spread_multiplier": 1.5,
        "initial_sl_atr_multiplier": 0.35,
        "breakeven_plus_enabled": True,
        "breakeven_plus_trigger_extra_pct": 0.15,
        "min_net_profit_after_breakeven_usdt": 0.02,
        "preferred_net_profit_after_breakeven_usdt": 0.05,
        "tp1_enabled": True,
        "tp1_trigger_pct_min": 0.60,
        "tp1_trigger_pct_max": 1.20,
        "tp1_close_fraction": 0.50,
        "min_reward_risk_ratio": 0.0,
        "enforce_min_reward_risk_ratio": False,
        "trailing_enabled": True,
        "trailing_start_pct_min": 0.75,
        "trailing_distance_pct_min": 0.35,
        "trailing_distance_pct_max": 0.85,
        "trailing_spread_multiplier": 2.0,
        "trailing_atr_multiplier": 0.40,
        "time_stop_seconds": 180,
        "max_hold_seconds": 600,
    },
    "positions": {
        "allow_duplicate_symbol": False,
        "allow_opposite_positions_same_symbol": False,
        "max_open_positions": 5,
    },
    "web": {
        "dashboard_token_env": "DASHBOARD_TOKEN",
        "route_prefix": "/bot",
    },
    "telegram": {"enabled": True, "parse_mode": "HTML"},
    "profiles": {
        "conservative": {
            "filters": {
                "min_turnover_24h_usd": 20_000_000,
                "min_price_change_15m_pct_for_candidate": 2.5,
                "min_price_change_1h_pct_for_candidate": 5.0,
                "min_volume_spike_for_candidate": 2.5,
                "top_activity_rank_candidate": 20,
            },
            "scoring": {"levels": {"watch": 55, "hot": 70, "breakout_hot": 82, "very_hot": 92}},
        },
        "normal": {},
        "aggressive": {
            "filters": {
                "min_turnover_24h_usd": 5_000_000,
                "min_price_change_15m_pct_for_candidate": 0.8,
                "min_price_change_1h_pct_for_candidate": 1.8,
                "min_volume_spike_for_candidate": 1.4,
                "top_activity_rank_candidate": 250,
            },
            "scoring": {"levels": {"watch": 42, "hot": 60, "breakout_hot": 75, "very_hot": 88}},
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    load_dotenv()
    config = deepcopy(DEFAULT_CONFIG)
    config_path = Path(path)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        config = _deep_merge(config, loaded)
    profile = os.getenv("SCANNER_PROFILE") or config.get("app", {}).get("profile", "normal")
    config = apply_threshold_profile(config, profile)
    config["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "")
    token_env = config.get("web", {}).get("dashboard_token_env", "DASHBOARD_TOKEN")
    config.setdefault("web", {})["dashboard_token"] = os.getenv(token_env, "")
    return config


def apply_threshold_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profiles = config.get("profiles") or {}
    if profile not in profiles:
        raise ValueError(f"Unknown threshold profile: {profile}")
    merged = _deep_merge(config, profiles.get(profile) or {})
    merged.setdefault("app", {})["profile"] = profile
    return merged
