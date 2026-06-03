from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "scan_interval_seconds": 60,
        "dry_run": False,
        "log_level": "INFO",
        "database_path": "data/scanner.sqlite3",
        "profile": "normal",
    },
    "exchanges": {
        "bybit": {
            "enabled": True,
            "base_url": "https://api.bybit.com",
            "category": "linear",
        },
        "binance": {"enabled": False, "base_url": "https://fapi.binance.com"},
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
        "min_volume_24h_usd": 5_000_000,
        "max_spread_pct": 0.30,
        "min_price_change_15m_pct_for_candidate": 1.5,
        "min_price_change_1h_pct_for_candidate": 3.0,
        "min_volume_spike_for_candidate": 2.0,
        "top_activity_rank_candidate": 30,
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
    "performance": {
        "max_enriched_candidates_per_cycle": 40,
        "max_concurrent_requests": 10,
        "symbol_universe_refresh_minutes": 20,
        "resistance_cache_ttl_minutes": 15,
        "orderbook_check_only_for_score_above": 55,
    },
    "storage": {
        "save_all_market_snapshots": True,
        "all_market_snapshot_interval_seconds": 60,
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
                "top_activity_rank_candidate": 60,
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
    return config


def apply_threshold_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profiles = config.get("profiles") or {}
    if profile not in profiles:
        raise ValueError(f"Unknown threshold profile: {profile}")
    merged = _deep_merge(config, profiles.get(profile) or {})
    merged.setdefault("app", {})["profile"] = profile
    return merged
