from __future__ import annotations

from dataclasses import asdict, dataclass

from app.storage.models import CandidateDiagnostics

TRADE_LABELS = {
    "LONG_CONTINUATION",
    "SHORT_INVERSE_LONG_SIGNAL",
    "SHORT_FAILED_BREAKOUT",
    "SHORT_BLOWOFF_REVERSAL",
    "NO_TRADE_LATE_CHASE",
    "NO_TRADE_BAD_LIQUIDITY",
    "NO_TRADE_CONFLICT",
}


@dataclass(slots=True)
class DirectionDecision:
    label: str
    direction: str
    reasons: list[str]
    warnings: list[str]
    execution_score: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _hot_rsi(diagnostic: CandidateDiagnostics) -> bool:
    metrics = diagnostic.metrics
    return any(value is not None and value >= 80 for value in (metrics.rsi_15m, metrics.rsi_1h, metrics.rsi_4h))


def _add_score(reasons: list[str], score: int, points: int, reason: str) -> int:
    if points > 0:
        reasons.append(f"+{points} {reason}")
    return score + points


def _between(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def _long_execution_score(diagnostic: CandidateDiagnostics, config: dict) -> tuple[int, list[str], list[str], dict[str, bool]]:
    metrics = diagnostic.metrics
    ticker = diagnostic.ticker
    breakout_state = diagnostic.breakout.state if diagnostic.breakout else "NO_BREAKOUT"
    setup = diagnostic.setup
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0

    price_1m = metrics.price_change_1m or 0
    price_5m = metrics.price_change_5m or 0
    price_15m = metrics.price_change_15m or 0
    price_1h = metrics.price_change_1h or 0
    price_4h = metrics.price_change_4h or 0
    volume_spike = max(metrics.volume_spike_15m or 0, metrics.turnover_spike_15m or 0)
    turnover_rank = metrics.turnover_rank_24h
    taker_ratio = metrics.taker_buy_sell_ratio
    oi_15m = metrics.oi_change_15m_pct
    funding = metrics.funding_rate
    min_price_15m = float(config.get("filters", {}).get("min_price_change_15m_pct_for_candidate", 0.8))
    min_volume_spike = float(config.get("filters", {}).get("min_volume_spike_for_candidate", 1.4))

    if price_5m >= 1.2:
        score = _add_score(reasons, score, 18, f"5m continuation {price_5m:+.2f}%")
    elif price_5m >= 0.5:
        score = _add_score(reasons, score, 12, f"5m continuation {price_5m:+.2f}%")
    elif price_5m >= 0.1 and price_15m >= 1.0:
        score = _add_score(reasons, score, 8, "shallow pullback inside positive 15m trend")

    if price_15m >= 2.5:
        score = _add_score(reasons, score, 18, f"15m impulse {price_15m:+.2f}%")
    elif price_15m >= min_price_15m:
        score = _add_score(reasons, score, 12, f"15m impulse {price_15m:+.2f}%")

    if price_1h >= 4.0:
        score = _add_score(reasons, score, 10, f"1h trend {price_1h:+.2f}%")
    elif price_1h >= float(config.get("filters", {}).get("min_price_change_1h_pct_for_candidate", 1.8)):
        score = _add_score(reasons, score, 6, f"1h trend {price_1h:+.2f}%")
    if price_4h >= 8.0:
        score = _add_score(reasons, score, 6, f"4h background {price_4h:+.2f}%")

    if volume_spike >= 2.4:
        score = _add_score(reasons, score, 18, f"volume/turnover spike x{volume_spike:.2f}")
    elif volume_spike >= min_volume_spike:
        score = _add_score(reasons, score, 12, f"volume/turnover spike x{volume_spike:.2f}")
    elif turnover_rank is not None and turnover_rank <= 10:
        score = _add_score(reasons, score, 8, f"top active turnover rank #{turnover_rank}")
    elif turnover_rank is not None and turnover_rank <= 25:
        score = _add_score(reasons, score, 5, f"active turnover rank #{turnover_rank}")

    if taker_ratio is None:
        score = _add_score(reasons, score, 4, "taker flow unavailable but not negative")
    elif taker_ratio >= 1.20:
        score = _add_score(reasons, score, 16, f"aggressive buy flow {taker_ratio:.2f}")
    elif taker_ratio >= 1.05:
        score = _add_score(reasons, score, 10, f"buy flow {taker_ratio:.2f}")

    if oi_15m is None:
        score = _add_score(reasons, score, 3, "OI unavailable but not blocking")
    elif oi_15m >= 3:
        score = _add_score(reasons, score, 12, f"OI expansion {oi_15m:+.2f}%")
    elif oi_15m >= 0:
        score = _add_score(reasons, score, 8, f"OI not falling {oi_15m:+.2f}%")

    if funding is None:
        score = _add_score(reasons, score, 3, "funding unavailable but not blocking")
    elif funding <= 0.0005:
        score = _add_score(reasons, score, 6, f"funding not overheated {funding * 100:.3f}%")

    if breakout_state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"}:
        score = _add_score(reasons, score, 12, f"chart state {breakout_state}")
    elif breakout_state in {"APPROACHING_RESISTANCE", "TESTING_RESISTANCE"}:
        score = _add_score(reasons, score, 4, f"chart state {breakout_state}")

    if setup and setup.estimated_rr is not None:
        if setup.estimated_rr >= 2.0:
            score = _add_score(reasons, score, 8, f"estimated R/R {setup.estimated_rr:.2f}")
        elif setup.estimated_rr >= 1.4:
            score = _add_score(reasons, score, 5, f"estimated R/R {setup.estimated_rr:.2f}")
    if setup and setup.chase_risk == "LOW":
        score = _add_score(reasons, score, 6, "low chase risk")
    elif setup and setup.chase_risk == "MEDIUM":
        score = _add_score(reasons, score, 2, "medium chase risk")

    if ticker.spread_pct is not None and ticker.spread_pct <= 0.08:
        score = _add_score(reasons, score, 4, f"tight spread {ticker.spread_pct:.3f}%")

    volume_confirmed = volume_spike >= min_volume_spike or (turnover_rank is not None and turnover_rank <= 25)
    breakout_confirmed = (
        breakout_state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"}
        and volume_confirmed
        and (taker_ratio is None or taker_ratio >= 1.05)
        and price_1m >= -0.25
        and price_5m >= -0.40
    )
    blockers = {
        "positive_momentum": price_5m >= 0.1 or price_15m >= min_price_15m or breakout_confirmed,
        "volume_confirmed": volume_confirmed,
        "buy_flow_ok": taker_ratio is None or taker_ratio >= 1.02,
        "oi_ok": (metrics.oi_change_15m_pct is None or metrics.oi_change_15m_pct > -2.5) and (metrics.oi_change_1h_pct is None or metrics.oi_change_1h_pct > -6),
        "funding_ok": funding is None or funding <= 0.001,
        "not_failed_breakout": breakout_state != "FAILED_BREAKOUT",
        "not_hard_chase": not (setup and setup.chase_risk == "HIGH"),
        "not_immediate_dump": not (price_1m <= -1.2 and price_5m <= -0.3),
    }

    if not blockers["buy_flow_ok"]:
        warnings.append("buy flow is not confirmed")
        score -= 18
    if not blockers["oi_ok"]:
        warnings.append("OI is falling too much for continuation")
        score -= 15
    if not blockers["funding_ok"]:
        warnings.append("funding is overheated for long continuation")
        score -= 12
    if not blockers["not_failed_breakout"]:
        warnings.append("failed breakout blocks long continuation")
        score -= 35
    if not blockers["not_hard_chase"]:
        warnings.append("HIGH chase risk blocks fresh long entry")
        score -= 25
    if not blockers["not_immediate_dump"]:
        warnings.append("immediate momentum flipped down")
        score -= 18
    if metrics.price_change_24h is not None:
        if metrics.price_change_24h > 120:
            warnings.append("24h move above +120%, continuation entry blocked")
            blockers["not_hard_chase"] = False
            score -= 35
        elif metrics.price_change_24h > 70:
            warnings.append("24h move above +70%, continuation score penalized")
            score -= 12
    if any(value is not None and value >= 92 for value in (metrics.rsi_15m, metrics.rsi_1h, metrics.rsi_4h)):
        warnings.append("RSI extreme, continuation score penalized")
        score -= 12

    score = max(0, min(100, int(score)))
    return score, reasons, warnings, blockers


def _short_execution_score(diagnostic: CandidateDiagnostics) -> tuple[int, list[str], list[str], dict[str, bool]]:
    metrics = diagnostic.metrics
    breakout_state = diagnostic.breakout.state if diagnostic.breakout else "NO_BREAKOUT"
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0
    price_1m = metrics.price_change_1m or 0
    price_5m = metrics.price_change_5m or 0
    taker_ratio = metrics.taker_buy_sell_ratio
    funding = metrics.funding_rate
    oi_15m = metrics.oi_change_15m_pct

    if breakout_state == "FAILED_BREAKOUT":
        score = _add_score(reasons, score, 30, "failed breakout")
    elif breakout_state == "OVEREXTENDED_AFTER_BREAKOUT":
        score = _add_score(reasons, score, 14, "overextended after breakout")
    if price_1m <= -0.3:
        score = _add_score(reasons, score, 12, f"1m momentum flipped {price_1m:+.2f}%")
    if price_5m <= -0.6:
        score = _add_score(reasons, score, 16, f"5m momentum flipped {price_5m:+.2f}%")
    if taker_ratio is not None and taker_ratio <= 0.88:
        score = _add_score(reasons, score, 16, f"sell flow {taker_ratio:.2f}")
    elif taker_ratio is not None and taker_ratio <= 0.95:
        score = _add_score(reasons, score, 10, f"sell flow {taker_ratio:.2f}")
    if oi_15m is not None and oi_15m <= -2:
        score = _add_score(reasons, score, 10, f"OI contraction {oi_15m:+.2f}%")
    if funding is not None and funding >= 0.0005:
        score = _add_score(reasons, score, 10, f"crowded long funding {funding * 100:.3f}%")
    if _hot_rsi(diagnostic):
        score = _add_score(reasons, score, 8, "hot RSI supports reversal watch")

    blockers = {
        "failed_or_blowoff": breakout_state in {"FAILED_BREAKOUT", "OVEREXTENDED_AFTER_BREAKOUT"} or _hot_rsi(diagnostic),
        "momentum_short": price_1m <= -0.3 and price_5m <= -0.2,
        "sell_pressure": (taker_ratio is not None and taker_ratio <= 0.95) or ((metrics.price_change_5m or 0) < 0 and (oi_15m or 0) < 0),
        "not_continuation": not ((metrics.price_change_5m or 0) > 0 and (metrics.price_change_15m or 0) > 0 and (metrics.volume_spike_15m or metrics.turnover_spike_15m or 0) >= 1.4),
    }
    if not blockers["not_continuation"]:
        warnings.append("strong long continuation context blocks short")
        score -= 35
    score = max(0, min(100, int(score)))
    return score, reasons, warnings, blockers


def classify_direction(diagnostic: CandidateDiagnostics, config: dict) -> DirectionDecision:
    metrics = diagnostic.metrics
    ticker = diagnostic.ticker
    breakout_state = diagnostic.breakout.state if diagnostic.breakout else "NO_BREAKOUT"
    reasons: list[str] = []
    warnings: list[str] = []
    strategy_cfg = config.get("strategy", {})
    entry_cfg = config.get("entry", {})
    direction_mode = strategy_cfg.get("direction_mode", "both")
    long_signal_execution = str(strategy_cfg.get("long_signal_execution", "normal")).lower()
    inverse_long_signal = long_signal_execution in {"inverse_short", "short_on_long_signal", "contrarian_short"}
    inverse_short_immediate_entry = bool(strategy_cfg.get("inverse_short_immediate_entry", False))
    inverse_short_relaxed_conditions = bool(strategy_cfg.get("inverse_short_relaxed_conditions", False))
    inverse_short_pullback_pct = float(entry_cfg.get("pullback_confirm_pct", 0.15))
    long_enabled = bool(strategy_cfg.get("long_enabled", True)) and direction_mode in {"both", "long_only", "auto"}
    short_enabled = bool(strategy_cfg.get("short_enabled", True)) and direction_mode in {"both", "short_only", "auto"}
    long_signal_allowed = long_enabled or (inverse_long_signal and short_enabled)
    long_min_score = int(strategy_cfg.get("long_min_score", 68))
    inverse_long_min_score = int(strategy_cfg.get("inverse_long_min_score", long_min_score))
    short_min_score = int(strategy_cfg.get("short_min_score", 88))
    long_high_conviction_score = int(strategy_cfg.get("long_high_conviction_score", 82))
    short_strict = bool(strategy_cfg.get("short_strict_mode", True))
    long_pullback_enabled = bool(strategy_cfg.get("long_pullback_entry_enabled", False))
    long_pullback_min_score = int(strategy_cfg.get("long_pullback_min_score", 74))
    long_pullback_min_pct = float(strategy_cfg.get("long_pullback_min_pct", 0.07))
    long_pullback_max_pct = float(strategy_cfg.get("long_pullback_max_pct", 0.60))
    short_breakdown_enabled = bool(strategy_cfg.get("short_breakdown_entry_enabled", False))
    short_breakdown_min_score = int(strategy_cfg.get("short_breakdown_min_score", 68))
    short_breakdown_min_1m_pct = float(strategy_cfg.get("short_breakdown_min_1m_pct", 0.18))
    short_breakdown_min_5m_pct = float(strategy_cfg.get("short_breakdown_min_5m_pct", 0.35))

    spread_limit = float(config.get("filters", {}).get("max_spread_pct", 0.20))
    absolute_spread_skip = float(config.get("filters", {}).get("max_spread_pct_absolute_skip", 0.35))
    if ticker.spread_pct is not None and ticker.spread_pct > absolute_spread_skip:
        return DirectionDecision("NO_TRADE_BAD_LIQUIDITY", "NO_TRADE", [], [f"spread {ticker.spread_pct:.3f}% exceeds absolute skip"])
    if ticker.spread_pct is not None and ticker.spread_pct > spread_limit:
        return DirectionDecision("NO_TRADE_BAD_LIQUIDITY", "NO_TRADE", [], [f"spread {ticker.spread_pct:.3f}% exceeds paper limit"])

    expected_notional = float(config.get("paper", {}).get("max_position_margin_usdt", 2.0)) * float(config.get("paper", {}).get("default_leverage", 5))
    if metrics.depth_usdt_20bps is not None and metrics.depth_usdt_20bps < expected_notional * 10:
        return DirectionDecision(
            "NO_TRADE_BAD_LIQUIDITY",
            "NO_TRADE",
            [],
            [f"20bps depth ${metrics.depth_usdt_20bps:.0f} below required buffer"],
        )

    late_chase = bool(strategy_cfg.get("avoid_late_chase", True)) and (
        (metrics.price_change_24h is not None and metrics.price_change_24h > 100)
        and not (breakout_state == "FRESH_BREAKOUT" and diagnostic.setup and diagnostic.setup.chase_risk != "HIGH")
    )
    if late_chase:
        return DirectionDecision("NO_TRADE_LATE_CHASE", "NO_TRADE", [], ["24h move already above +100%"])

    long_execution_score, long_reasons, long_warnings, long_blockers = _long_execution_score(diagnostic, config)
    high_conviction_long = (
        long_execution_score >= long_high_conviction_score
        and long_blockers["positive_momentum"]
        and long_blockers["buy_flow_ok"]
        and long_blockers["oi_ok"]
        and long_blockers["funding_ok"]
        and long_blockers["not_failed_breakout"]
        and long_blockers["not_hard_chase"]
        and long_blockers["not_immediate_dump"]
    )
    long_signal_conditions = [
        long_signal_allowed,
        long_execution_score >= long_min_score,
        long_blockers["positive_momentum"],
        long_blockers["volume_confirmed"] or high_conviction_long,
        long_blockers["buy_flow_ok"],
        long_blockers["oi_ok"],
        long_blockers["funding_ok"],
        long_blockers["not_failed_breakout"],
        long_blockers["not_hard_chase"],
        long_blockers["not_immediate_dump"],
    ]
    long_conditions = [long_enabled, *long_signal_conditions[1:]]
    strong_long_continuation = (
        long_blockers["positive_momentum"]
        and (long_blockers["volume_confirmed"] or high_conviction_long)
        and long_blockers["buy_flow_ok"]
        and long_blockers["not_failed_breakout"]
    )
    price_1m = metrics.price_change_1m or 0.0
    price_5m = metrics.price_change_5m or 0.0
    price_15m = metrics.price_change_15m or 0.0
    price_1h = metrics.price_change_1h or 0.0
    price_4h = metrics.price_change_4h or 0.0
    btc_15m = metrics.btc_change_15m
    btc_1h = metrics.btc_change_1h
    taker_ratio = metrics.taker_buy_sell_ratio
    oi_15m = metrics.oi_change_15m_pct
    funding = metrics.funding_rate

    long_pullback_quality = (
        long_blockers["volume_confirmed"]
        or breakout_state in {"FRESH_BREAKOUT", "CONFIRMED_BREAKOUT", "RETEST_HELD"}
        or price_5m >= 1.2
        or high_conviction_long
    )
    htf_long_ok = price_1h >= -0.6 and price_4h >= -2.5 and (btc_15m is None or btc_15m >= -0.45) and (btc_1h is None or btc_1h >= -0.90)
    pullback_depth = abs(price_1m) if price_1m < 0 else 0.0
    long_pullback_ok = (
        long_pullback_enabled
        and long_enabled
        and not inverse_long_signal
        and long_execution_score >= long_pullback_min_score
        and _between(pullback_depth, long_pullback_min_pct, long_pullback_max_pct)
        and price_5m >= 0.10
        and price_15m >= float(config.get("filters", {}).get("min_price_change_15m_pct_for_candidate", 0.8))
        and long_pullback_quality
        and htf_long_ok
        and long_blockers["buy_flow_ok"]
        and long_blockers["oi_ok"]
        and long_blockers["funding_ok"]
        and long_blockers["not_failed_breakout"]
        and long_blockers["not_hard_chase"]
        and long_blockers["not_immediate_dump"]
    )
    if long_pullback_ok:
        reasons.extend(
            [
                f"pullback LONG: execution_score {long_execution_score} >= {long_pullback_min_score}",
                f"controlled 1m pullback {price_1m:+.2f}% inside positive 5m/15m context",
                *long_reasons,
            ]
        )
        warnings.extend(long_warnings)
        warnings.append("paper experiment: cost-aware pullback LONG, not inverse short")
        return DirectionDecision("LONG_CONTINUATION", "LONG", reasons, warnings, execution_score=long_execution_score)

    inverse_short_pullback_confirmed = (metrics.price_change_1m or 0) <= -inverse_short_pullback_pct
    inverse_short_strict_ok = inverse_long_signal and short_enabled and all(long_signal_conditions) and long_execution_score >= inverse_long_min_score
    inverse_short_relaxed_ok = (
        inverse_long_signal
        and short_enabled
        and inverse_short_relaxed_conditions
        and long_signal_allowed
        and long_execution_score >= inverse_long_min_score
        and long_blockers["positive_momentum"]
        and long_blockers["not_failed_breakout"]
        and long_blockers["not_hard_chase"]
        and long_blockers["not_immediate_dump"]
    )
    if inverse_short_strict_ok or inverse_short_relaxed_ok:
        if not inverse_short_immediate_entry and not inverse_short_pullback_confirmed:
            warnings.append(
                f"inverse short waiting for 1m pullback >= {inverse_short_pullback_pct:.2f}% "
                f"(now {(metrics.price_change_1m or 0):+.2f}%)"
            )
            warnings.extend(long_warnings)
            return DirectionDecision("NO_TRADE_CONFLICT", "NO_TRADE", long_reasons[:6], warnings, execution_score=long_execution_score)
        reasons.extend(
            [
                f"inverse mode: LONG_CONTINUATION execution_score {long_execution_score} >= inverse_long_min_score {inverse_long_min_score}",
                *long_reasons,
            ]
        )
        warnings.extend(long_warnings)
        if inverse_short_relaxed_ok and not inverse_short_strict_ok:
            warnings.append("paper experiment: relaxed inverse-short conditions accepted after pullback")
        if inverse_short_immediate_entry:
            warnings.append("paper experiment: executing long-continuation signal as contrarian SHORT without pullback confirmation")
        else:
            reasons.append(f"1m pullback confirmation {(metrics.price_change_1m or 0):+.2f}% <= -{inverse_short_pullback_pct:.2f}%")
            warnings.append("paper experiment: executing long-continuation signal as contrarian SHORT after 1m pullback")
        return DirectionDecision("SHORT_INVERSE_LONG_SIGNAL", "SHORT", reasons, warnings, execution_score=long_execution_score)

    if not inverse_long_signal and not long_pullback_enabled and all(long_conditions):
        reasons.extend([f"execution_score {long_execution_score} >= long_min_score {long_min_score}", *long_reasons])
        warnings.extend(long_warnings)
        if high_conviction_long and not long_blockers["volume_confirmed"]:
            warnings.append(f"high-conviction continuation override: score {long_execution_score} >= {long_high_conviction_score}")
        return DirectionDecision("LONG_CONTINUATION", "LONG", reasons, warnings, execution_score=long_execution_score)

    if bool(strategy_cfg.get("avoid_shorting_strong_momentum", True)) and strong_long_continuation:
        warnings.append("strong LONG_CONTINUATION context; short blocked")
        warnings.extend(long_warnings)
        return DirectionDecision("NO_TRADE_CONFLICT", "NO_TRADE", reasons, warnings, execution_score=long_execution_score)

    short_execution_score, short_reasons, short_warnings, short_blockers = _short_execution_score(diagnostic)
    htf_short_ok = price_1h <= 1.2 and price_4h <= 6.0 and (btc_15m is None or btc_15m <= 0.60) and (btc_1h is None or btc_1h <= 1.20)
    short_breakdown_ok = (
        short_breakdown_enabled
        and short_enabled
        and short_execution_score >= short_breakdown_min_score
        and price_1m <= -short_breakdown_min_1m_pct
        and price_5m <= -short_breakdown_min_5m_pct
        and htf_short_ok
        and (
            breakout_state in {"FAILED_BREAKOUT", "OVEREXTENDED_AFTER_BREAKOUT"}
            or (taker_ratio is not None and taker_ratio <= 0.88)
            or (oi_15m is not None and oi_15m <= -2)
            or (funding is not None and funding >= 0.0005)
        )
        and short_blockers["sell_pressure"]
        and short_blockers["not_continuation"]
    )
    if short_breakdown_ok:
        reasons.extend(
            [
                f"breakdown SHORT: execution_score {short_execution_score} >= {short_breakdown_min_score}",
                f"1m/5m momentum down {price_1m:+.2f}%/{price_5m:+.2f}%",
                *short_reasons,
            ]
        )
        warnings.extend(short_warnings)
        warnings.append("paper experiment: cost-aware breakdown SHORT")
        return DirectionDecision("SHORT_BLOWOFF_REVERSAL", "SHORT", reasons, warnings, execution_score=short_execution_score)

    short_score_ok = diagnostic.score >= short_min_score and short_execution_score >= short_min_score
    short_conditions = [
        short_enabled,
        short_score_ok,
        short_blockers["failed_or_blowoff"],
        short_blockers["momentum_short"],
        short_blockers["sell_pressure"],
        short_blockers["not_continuation"],
    ]
    if short_strict and all(short_conditions):
        label = "SHORT_FAILED_BREAKOUT" if breakout_state == "FAILED_BREAKOUT" else "SHORT_BLOWOFF_REVERSAL"
        reasons.extend([f"diagnostic score {diagnostic.score} and execution_score {short_execution_score} >= short_min_score {short_min_score}", *short_reasons])
        warnings.extend(short_warnings)
        return DirectionDecision(label, "SHORT", reasons, warnings, execution_score=short_execution_score)
    if not short_strict and short_enabled and short_execution_score >= short_min_score and short_blockers["failed_or_blowoff"] and short_blockers["momentum_short"]:
        reasons.extend(["non-strict short reversal conditions met", *short_reasons])
        warnings.extend(short_warnings)
        return DirectionDecision("SHORT_BLOWOFF_REVERSAL", "SHORT", reasons, warnings, execution_score=short_execution_score)

    conflict_bits = [
        long_execution_score >= long_min_score and not long_blockers["positive_momentum"],
        long_blockers["positive_momentum"] and not long_blockers["volume_confirmed"],
        not long_blockers["buy_flow_ok"],
        not long_blockers["funding_ok"],
        not long_blockers["not_failed_breakout"],
    ]
    if any(conflict_bits):
        warnings.append(f"signals conflict, no forced trade; long_execution_score={long_execution_score}, short_execution_score={short_execution_score}")
    else:
        warnings.append(f"not enough independent confirmations; long_execution_score={long_execution_score}, short_execution_score={short_execution_score}")
    if long_execution_score >= short_execution_score:
        reasons.extend(long_reasons[:6])
    else:
        reasons.extend(short_reasons[:6])
    warnings.extend(long_warnings + short_warnings)
    return DirectionDecision("NO_TRADE_CONFLICT", "NO_TRADE", reasons, warnings, execution_score=max(long_execution_score, short_execution_score))
