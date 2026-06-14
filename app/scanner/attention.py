from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.storage.models import CandidateDiagnostics, TickerSnapshot
from app.utils.time import now_ms as current_now_ms


STATE_VERSION = 1


@dataclass(slots=True)
class AttentionSelection:
    selected: list[TickerSnapshot]
    next_state: dict[str, Any]
    stats: dict[str, Any]


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _perf_int(config: Mapping[str, Any], key: str, default: int) -> int:
    return max(0, _safe_int(config.get("performance", {}).get(key), default))


def _perf_float(config: Mapping[str, Any], key: str, default: float) -> float:
    return max(0.0, _safe_float(config.get("performance", {}).get(key), default))


def _symbol(ticker: TickerSnapshot) -> str:
    return ticker.symbol.upper()


def _state_symbols(state: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (state or {}).get("symbols", {})
    if not isinstance(raw, Mapping):
        return {}
    return {str(symbol).upper(): dict(value) for symbol, value in raw.items() if isinstance(value, Mapping)}


def _spread_penalty(ticker: TickerSnapshot) -> float:
    spread = _safe_float(ticker.spread_pct)
    if spread <= 0:
        return 0.0
    return min(35.0, spread * 35.0)


def market_activity_score(ticker: TickerSnapshot) -> float:
    rank = ticker.turnover_rank_24h or ticker.volume_rank_24h or 9999
    rank_score = max(0.0, 34.0 * (1.0 - min(rank, 250) / 250.0))

    move_score = min(
        38.0,
        abs(_safe_float(ticker.price_change_1m_pct)) * 24.0
        + abs(_safe_float(ticker.price_change_3m_pct)) * 12.0
        + abs(_safe_float(ticker.price_change_5m_pct)) * 7.0
        + abs(_safe_float(ticker.price_change_15m_pct)) * 3.0
        + abs(_safe_float(ticker.price_24h_pct)) / 12.0,
    )

    volume_delta = _safe_float(ticker.quote_volume_delta_5m)
    if volume_delta > 0:
        volume_score = min(16.0, math.log10(volume_delta + 1.0) * 2.2)
    else:
        turnover = _safe_float(ticker.turnover_24h)
        volume_score = min(9.0, max(0.0, math.log10(turnover / 10_000_000.0 + 1.0) * 3.0))

    trade_delta = _safe_float(ticker.trade_count_delta_5m)
    trade_score = min(8.0, math.log10(trade_delta + 1.0) * 2.2) if trade_delta > 0 else 0.0
    return max(0.0, rank_score + move_score + volume_score + trade_score - _spread_penalty(ticker))


def downside_activity_score(ticker: TickerSnapshot) -> float:
    downside = max(
        0.0,
        -_safe_float(ticker.price_change_1m_pct),
        -_safe_float(ticker.price_change_3m_pct) * 0.85,
        -_safe_float(ticker.price_change_5m_pct) * 0.65,
        -_safe_float(ticker.price_change_15m_pct) * 0.35,
    )
    if downside <= 0:
        return 0.0
    rank = ticker.turnover_rank_24h or ticker.volume_rank_24h or 9999
    rank_bonus = max(0.0, 14.0 * (1.0 - min(rank, 250) / 250.0))
    volume_bonus = min(12.0, math.log10(_safe_float(ticker.quote_volume_delta_5m) + 1.0) * 1.8)
    return max(0.0, downside * 32.0 + rank_bonus + volume_bonus - _spread_penalty(ticker))


def _plan_memory(recent_trade_plans: Sequence[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    memory: dict[str, dict[str, Any]] = {}
    for row in recent_trade_plans or []:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        score = _safe_float(row.get("score"), -1.0)
        created_at_ms = _safe_int(row.get("created_at_ms"))
        status = str(row.get("status") or "").lower()
        label = str(row.get("classifier_label") or "")
        item = memory.setdefault(
            symbol,
            {
                "best_score": -1.0,
                "best_score_ms": 0,
                "last_plan_ms": 0,
                "last_status": "",
                "last_label": "",
                "waiting": False,
                "waiting_score": -1.0,
                "waiting_ms": 0,
            },
        )
        if score > _safe_float(item.get("best_score"), -1.0):
            item["best_score"] = score
            item["best_score_ms"] = created_at_ms
        if created_at_ms >= _safe_int(item.get("last_plan_ms")):
            item["last_plan_ms"] = created_at_ms
            item["last_status"] = status
            item["last_label"] = label
        if status == "waiting_entry" and created_at_ms >= _safe_int(item.get("waiting_ms")):
            item["waiting"] = True
            item["waiting_score"] = score
            item["waiting_ms"] = created_at_ms
    return memory


def _cooldown_scope(config: Mapping[str, Any]) -> str:
    paper = config.get("paper", {}) if isinstance(config.get("paper", {}), Mapping) else {}
    scope = str(paper.get("cooldown_scope") or paper.get("trade_cooldown_scope") or "active_settings").lower()
    if scope in {"all", "all_history", "global"}:
        return "all_history"
    return "active_settings"


def _trade_in_cooldown_scope(row: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    if _cooldown_scope(config) == "all_history":
        return True
    runtime = config.get("runtime_settings", {}) if isinstance(config.get("runtime_settings", {}), Mapping) else {}
    current_hash = str(runtime.get("hash") or "")
    if not current_hash:
        return True
    return str(row.get("settings_hash") or "") == current_hash


def _loss_memory(recent_trades: Sequence[Mapping[str, Any]] | None, config: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    memory: dict[str, list[dict[str, Any]]] = {}
    for row in recent_trades or []:
        if not _trade_in_cooldown_scope(row, config):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        exit_time_ms = _safe_int(row.get("exit_time_ms"))
        net_pnl = _safe_float(row.get("net_pnl_usdt"))
        exit_reason = str(row.get("exit_reason") or "").upper()
        if exit_time_ms <= 0 or (net_pnl >= 0 and exit_reason != "STOP_LOSS"):
            continue
        memory.setdefault(symbol, []).append(
            {
                "exit_time_ms": exit_time_ms,
                "net_pnl_usdt": net_pnl,
                "exit_reason": exit_reason,
                "settings_hash": row.get("settings_hash"),
            }
        )
    return memory


def _loss_cooldown_active(symbol: str, losses: Mapping[str, list[dict[str, Any]]], config: Mapping[str, Any], now_ms: int) -> bool:
    rows = losses.get(symbol, [])
    if not rows:
        return False
    paper = config.get("paper", {}) if isinstance(config.get("paper", {}), Mapping) else {}
    repeat_loss_count = _safe_int(paper.get("repeat_loss_symbol_count"))
    repeat_loss_window_ms = _safe_int(paper.get("repeat_loss_window_minutes")) * 60_000
    repeat_loss_cooldown_ms = _safe_int(paper.get("repeat_loss_symbol_cooldown_minutes")) * 60_000
    if repeat_loss_count > 0 and repeat_loss_window_ms > 0 and repeat_loss_cooldown_ms > 0:
        clustered = [row for row in rows if now_ms - _safe_int(row.get("exit_time_ms")) <= repeat_loss_window_ms]
        if len(clustered) >= repeat_loss_count:
            latest_loss_ms = max(_safe_int(row.get("exit_time_ms")) for row in clustered)
            if now_ms - latest_loss_ms <= repeat_loss_cooldown_ms:
                return True

    stop_loss_cooldown_ms = _safe_int(paper.get("stop_loss_symbol_cooldown_minutes")) * 60_000
    if stop_loss_cooldown_ms > 0:
        return any(now_ms - _safe_int(row.get("exit_time_ms")) <= stop_loss_cooldown_ms for row in rows)
    return False


def _cooldown_minutes(score: float, config: Mapping[str, Any]) -> float:
    if score >= _perf_float(config, "attention_hot_score_threshold", 64.0):
        return _perf_float(config, "attention_hot_score_cooldown_minutes", 2.0)
    if score >= 50:
        return _perf_float(config, "attention_near_score_cooldown_minutes", 10.0)
    if score >= 30:
        return _perf_float(config, "attention_mid_score_cooldown_minutes", 25.0)
    return _perf_float(config, "attention_low_score_cooldown_minutes", 60.0)


def _is_due(
    symbol: str,
    state_symbols: Mapping[str, Mapping[str, Any]],
    plan_memory: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
    now_ms: int,
    *,
    current_hot_score: float = 0.0,
) -> bool:
    state = state_symbols.get(symbol, {})
    last_enriched_ms = _safe_int(state.get("last_enriched_ms"))
    if last_enriched_ms <= 0:
        return True
    forced_hot = current_hot_score >= _perf_float(config, "attention_force_hot_activity_score", 80.0)
    if forced_hot and now_ms - last_enriched_ms >= int(_perf_float(config, "attention_force_hot_cooldown_seconds", 45.0) * 1000):
        return True
    last_score = max(_safe_float(state.get("last_score"), -1.0), _safe_float(plan_memory.get(symbol, {}).get("best_score"), -1.0))
    return now_ms - last_enriched_ms >= int(_cooldown_minutes(last_score, config) * 60_000)


def _normalize_state(
    state: Mapping[str, Any] | None,
    *,
    now_ms: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    symbols = _state_symbols(state)
    ttl_ms = _perf_int(config, "attention_state_ttl_minutes", 360) * 60_000
    max_symbols = _perf_int(config, "attention_state_max_symbols", 600) or 600
    if ttl_ms > 0:
        symbols = {
            symbol: data
            for symbol, data in symbols.items()
            if now_ms
            - max(
                _safe_int(data.get("last_enriched_ms")),
                _safe_int(data.get("last_plan_ms")),
                _safe_int(data.get("last_seen_ms")),
            )
            <= ttl_ms
        }
    if len(symbols) > max_symbols:
        ranked = sorted(
            symbols.items(),
            key=lambda item: max(
                _safe_int(item[1].get("last_enriched_ms")),
                _safe_int(item[1].get("last_plan_ms")),
                _safe_int(item[1].get("last_seen_ms")),
            ),
            reverse=True,
        )
        symbols = dict(ranked[:max_symbols])
    normalized = {"version": STATE_VERSION, "updated_at_ms": now_ms, "symbols": symbols}
    if isinstance((state or {}).get("last_selection"), Mapping):
        normalized["last_selection"] = dict((state or {}).get("last_selection") or {})
    return normalized


def select_attention_candidates(
    active_watchlist: Sequence[TickerSnapshot],
    max_candidates: int,
    config: Mapping[str, Any],
    *,
    state: Mapping[str, Any] | None = None,
    recent_trade_plans: Sequence[Mapping[str, Any]] | None = None,
    recent_trades: Sequence[Mapping[str, Any]] | None = None,
    now_ms: int | None = None,
) -> AttentionSelection:
    now_ms = now_ms or current_now_ms()
    max_candidates = max(1, int(max_candidates))
    next_state = _normalize_state(state, now_ms=now_ms, config=config)
    state_symbols = next_state["symbols"]
    plans = _plan_memory(recent_trade_plans)

    for symbol, memory in plans.items():
        stored = state_symbols.setdefault(symbol, {})
        stored["last_plan_ms"] = max(_safe_int(stored.get("last_plan_ms")), _safe_int(memory.get("last_plan_ms")))
        stored["last_plan_status"] = memory.get("last_status") or stored.get("last_plan_status")
        stored["last_plan_label"] = memory.get("last_label") or stored.get("last_plan_label")
        stored["last_plan_score"] = max(_safe_float(stored.get("last_plan_score"), -1.0), _safe_float(memory.get("best_score"), -1.0))

    all_tickers_by_symbol = {_symbol(ticker): ticker for ticker in active_watchlist}
    losses = _loss_memory(recent_trades, config)
    loss_blocked_symbols = {
        symbol for symbol in all_tickers_by_symbol if _loss_cooldown_active(symbol, losses, config, now_ms)
    }
    ticker_by_symbol = {symbol: ticker for symbol, ticker in all_tickers_by_symbol.items() if symbol not in loss_blocked_symbols}
    activity = {symbol: market_activity_score(ticker) for symbol, ticker in ticker_by_symbol.items()}
    downside = {symbol: downside_activity_score(ticker) for symbol, ticker in ticker_by_symbol.items()}

    selected: list[TickerSnapshot] = []
    selected_symbols: set[str] = set()
    selected_reasons: dict[str, str] = {}

    def add_symbol(symbol: str, reason: str) -> bool:
        if len(selected) >= max_candidates or symbol in selected_symbols:
            return False
        ticker = ticker_by_symbol.get(symbol)
        if ticker is None:
            return False
        selected.append(ticker)
        selected_symbols.add(symbol)
        selected_reasons[symbol] = reason
        stored = state_symbols.setdefault(symbol, {})
        stored["last_enriched_ms"] = now_ms
        stored["last_selected_reason"] = reason
        stored["last_activity_score"] = round(activity.get(symbol, 0.0), 4)
        stored["last_downside_score"] = round(downside.get(symbol, 0.0), 4)
        return True

    def add_many(symbols: Sequence[str], reason: str, limit: int, *, due_only: bool = True) -> int:
        added = 0
        for symbol in symbols:
            if added >= limit or len(selected) >= max_candidates:
                break
            if symbol in selected_symbols:
                continue
            if due_only and not _is_due(symbol, state_symbols, plans, config, now_ms, current_hot_score=activity.get(symbol, 0.0)):
                continue
            if add_symbol(symbol, reason):
                added += 1
        return added

    waiting_lookback_ms = _perf_int(config, "attention_waiting_lookback_minutes", 90) * 60_000
    waiting_symbols = [
        symbol
        for symbol, memory in plans.items()
        if memory.get("waiting")
        and symbol in ticker_by_symbol
        and (waiting_lookback_ms <= 0 or now_ms - _safe_int(memory.get("waiting_ms")) <= waiting_lookback_ms)
    ]
    waiting_symbols.sort(key=lambda symbol: (_safe_float(plans[symbol].get("waiting_score")), _safe_int(plans[symbol].get("waiting_ms"))), reverse=True)

    hot_symbols = sorted(ticker_by_symbol, key=lambda symbol: activity.get(symbol, 0.0), reverse=True)

    recent_score_floor = _perf_float(config, "attention_recent_score_floor", 50.0)
    recent_lookback_ms = _perf_int(config, "attention_recent_plan_lookback_minutes", 120) * 60_000
    recent_symbols = [
        symbol
        for symbol, memory in plans.items()
        if symbol in ticker_by_symbol
        and _safe_float(memory.get("best_score"), -1.0) >= recent_score_floor
        and (recent_lookback_ms <= 0 or now_ms - _safe_int(memory.get("best_score_ms")) <= recent_lookback_ms)
    ]
    recent_symbols.sort(
        key=lambda symbol: (
            _safe_float(plans[symbol].get("best_score"), -1.0),
            activity.get(symbol, 0.0),
            _safe_int(plans[symbol].get("best_score_ms")),
        ),
        reverse=True,
    )

    reversal_floor = _perf_float(config, "attention_reversal_downside_score_floor", 18.0)
    reversal_symbols = [symbol for symbol in ticker_by_symbol if downside.get(symbol, 0.0) >= reversal_floor]
    reversal_symbols.sort(key=lambda symbol: downside.get(symbol, 0.0), reverse=True)

    rotation_symbols = sorted(
        ticker_by_symbol,
        key=lambda symbol: (
            _safe_int(state_symbols.get(symbol, {}).get("last_enriched_ms")),
            -activity.get(symbol, 0.0),
        ),
    )

    quotas = {
        "waiting": _perf_int(config, "attention_waiting_slots", 6),
        "hot": _perf_int(config, "attention_hot_slots", 8),
        "recent": _perf_int(config, "attention_recent_slots", 5),
        "reversal": _perf_int(config, "attention_reversal_slots", 3),
        "rotation": _perf_int(config, "attention_rotation_slots", 8),
    }

    counts: dict[str, int] = {}
    counts["waiting"] = add_many(waiting_symbols, "waiting", quotas["waiting"], due_only=False)
    counts["hot"] = add_many(hot_symbols, "hot", quotas["hot"], due_only=True)
    counts["recent"] = add_many(recent_symbols, "recent", quotas["recent"], due_only=True)
    counts["reversal"] = add_many(reversal_symbols, "reversal", quotas["reversal"], due_only=True)
    counts["rotation"] = add_many(rotation_symbols, "rotation", quotas["rotation"], due_only=False)

    if len(selected) < max_candidates:
        counts["fill_due"] = add_many(hot_symbols, "fill_due", max_candidates - len(selected), due_only=True)
    if len(selected) < max_candidates:
        counts["fill"] = add_many(hot_symbols, "fill", max_candidates - len(selected), due_only=False)

    next_state["last_selection"] = {
        "selected_at_ms": now_ms,
        "selected": [{"symbol": _symbol(ticker), "reason": selected_reasons.get(_symbol(ticker), "unknown")} for ticker in selected],
        "counts": counts,
    }
    return AttentionSelection(
        selected=selected,
        next_state=_normalize_state(next_state, now_ms=now_ms, config=config),
        stats={
            "enabled": True,
            "selected": len(selected),
            "bucket_counts": counts,
            "eligible_waiting": len(waiting_symbols),
            "eligible_recent": len(recent_symbols),
            "eligible_reversal": len(reversal_symbols),
            "loss_blocked": len(loss_blocked_symbols),
            "state_symbols": len(next_state["symbols"]),
        },
    )


def update_attention_state_from_diagnostics(
    state: Mapping[str, Any] | None,
    diagnostics: Sequence[CandidateDiagnostics],
    config: Mapping[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = now_ms or current_now_ms()
    next_state = _normalize_state(state, now_ms=now_ms, config=config)
    symbols = next_state["symbols"]
    for diagnostic in diagnostics:
        symbol = diagnostic.symbol.upper()
        stored = symbols.setdefault(symbol, {})
        stored["last_enriched_ms"] = now_ms
        stored["last_seen_ms"] = now_ms
        stored["last_score"] = float(diagnostic.score)
        stored["last_level"] = diagnostic.level
        stored["last_filter_stage"] = diagnostic.filter_stage_passed
        stored["last_review_label"] = diagnostic.review_label
        stored["last_signal_type"] = diagnostic.signal_type
    return _normalize_state(next_state, now_ms=now_ms, config=config)
