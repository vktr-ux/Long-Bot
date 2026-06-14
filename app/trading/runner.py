from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from app.config import load_config
from app.exchanges.factory import build_connector
from app.exchanges.binance import BinanceFuturesPublicConnector
from app.exchanges.binance_stream import BinanceWebSocketMarketCache
from app.runtime_settings import (
    RuntimeTradingSettings,
    apply_runtime_settings_to_config,
    build_runtime_settings_from_config,
    runtime_settings_hash,
)
from app.scanner.signals import ScanEngine
from app.storage.db import SQLiteStore
from app.storage.models import SymbolInfo, TickerSnapshot
from app.trading.classifier import classify_direction
from app.trading.paper import PaperBroker
from app.trading.position_manager import evaluate_position
from app.trading.strategy import STRATEGY_VERSION, TradePlan, build_trade_plan
from app.utils.logging import configure_logging
from app.utils.time import now_ms

LOGGER = logging.getLogger(__name__)
SCANNER_ATTENTION_STATE_KEY = "scanner_attention_state"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance-first paper scalper runner")
    parser.add_argument("--config", default="config.paper.yaml", help="Path to paper config")
    parser.add_argument("--once", action="store_true", help="Run one scan/manage cycle and exit")
    parser.add_argument("--until-first-trade", action="store_true", help="Run until at least one closed paper trade is recorded")
    parser.add_argument("--run-seconds", type=int, default=0, help="Optional maximum runtime for continuous mode")
    return parser.parse_args()


def symbol_map(symbols: list[SymbolInfo]) -> dict[str, SymbolInfo]:
    return {symbol.symbol.upper(): symbol for symbol in symbols}


def ticker_map(tickers: list[TickerSnapshot]) -> dict[str, TickerSnapshot]:
    return {ticker.symbol.upper(): ticker for ticker in tickers}


def open_position_symbol_set(positions: list[dict]) -> set[str]:
    return {str(position.get("symbol", "")).upper() for position in positions if position.get("symbol")}


def position_conflicts_with_open(existing_positions: list[dict], symbol: str, direction: str, config: dict) -> bool:
    positions_cfg = config.get("positions", {})
    allow_duplicate = bool(positions_cfg.get("allow_duplicate_symbol", False))
    allow_opposite = bool(positions_cfg.get("allow_opposite_positions_same_symbol", False))
    symbol = symbol.upper()
    direction = direction.upper()
    for position in existing_positions:
        if str(position.get("symbol", "")).upper() != symbol:
            continue
        open_direction = str(position.get("direction", "")).upper()
        if open_direction == direction and not allow_duplicate:
            return True
        if open_direction != direction and not allow_opposite:
            return True
    return False


def entry_trigger_status(plan: TradePlan, ticker: TickerSnapshot, config: dict) -> tuple[bool, str | None]:
    entry_cfg = config.get("entry", {})
    if entry_cfg.get("mode", "confirmation_ladder") != "confirmation_ladder":
        return True, None
    if not bool(entry_cfg.get("require_trigger_confirmation", True)):
        return True, None
    if not plan.entry_grid:
        return True, None
    trigger = float(plan.entry_grid[0].get("trigger_price") or 0)
    if trigger <= 0:
        return True, None
    if plan.direction.upper() == "LONG":
        current = float(ticker.ask_price or ticker.last_price or 0)
        tolerance = trigger * (1 - float(entry_cfg.get("trigger_tolerance_pct", 0.02)) / 100)
        if current < tolerance:
            return False, f"waiting entry trigger: ask {current:.8g} below {trigger:.8g}"
        distance_pct = (current - trigger) / trigger * 100
    else:
        current = float(ticker.bid_price or ticker.last_price or 0)
        tolerance = trigger * (1 + float(entry_cfg.get("trigger_tolerance_pct", 0.02)) / 100)
        if current > tolerance:
            return False, f"waiting entry trigger: bid {current:.8g} above {trigger:.8g}"
        distance_pct = (trigger - current) / trigger * 100
    max_overrun_pct = float(entry_cfg.get("max_entry_distance_above_trigger_pct", entry_cfg.get("chase_max_distance_pct", 0.60)))
    if distance_pct > max_overrun_pct:
        return False, f"entry trigger overrun: {distance_pct:.2f}% beyond trigger"
    return True, None


async def open_position_tickers(connector, positions: list[dict]) -> dict[str, TickerSnapshot]:
    get_cached_ticker = getattr(connector, "get_cached_ticker", None)
    get_orderbook = getattr(connector, "get_orderbook", None)
    if get_orderbook is None:
        return ticker_map(await connector.get_tickers())
    result: dict[str, TickerSnapshot] = {}
    timestamp = now_ms()
    for position in positions:
        symbol = position["symbol"].upper()
        if get_cached_ticker is not None:
            ticker = get_cached_ticker(symbol)
            if ticker is not None and ticker.bid_price is not None and ticker.ask_price is not None:
                result[symbol] = ticker
                continue
        orderbook = await get_orderbook(symbol, 20)
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        if not bids or not asks:
            continue
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100 if mid else None
        result[symbol] = TickerSnapshot(
            timestamp_ms=timestamp,
            exchange=connector.name,
            symbol=symbol,
            last_price=mid,
            bid_price=bid,
            ask_price=ask,
            spread_pct=spread,
        )
    return result


async def build_runner_connector(config: dict):
    connector = build_connector(config)
    market_data_cfg = config.get("market_data", {})
    use_websocket = bool(market_data_cfg.get("use_websocket", False))
    if use_websocket and isinstance(connector, BinanceFuturesPublicConnector):
        connector = BinanceWebSocketMarketCache(
            connector,
            market_stream_url=market_data_cfg.get("binance_market_stream_url") or "wss://fstream.binance.com/market/stream?streams=!ticker@arr/!markPrice@arr",
            public_stream_url=market_data_cfg.get("binance_public_stream_url") or "wss://fstream.binance.com/public/ws/!bookTicker",
            reconnect_delay_seconds=float(market_data_cfg.get("websocket_reconnect_delay_seconds", 5.0)),
            fallback_to_rest_after_seconds=float(market_data_cfg.get("websocket_fallback_to_rest_after_seconds", 45.0)),
        )
        await connector.start()
    return connector


def account_can_open(store: SQLiteStore, account_id: int, config: dict) -> tuple[bool, str | None]:
    paper_cfg = config.get("paper", {})
    open_positions = store.get_open_positions(account_id)
    if len(open_positions) >= int(paper_cfg.get("max_open_positions", 1)):
        return False, "max open positions reached"
    now = now_ms()
    day_start = now - 24 * 60 * 60 * 1000
    trades = store.list_trades(from_ms=day_start, limit=1000)
    opened_today = sum(1 for position in open_positions if int(position.get("opened_at_ms") or 0) >= day_start)
    max_daily_trades = int(paper_cfg.get("max_daily_trades", 0))
    if max_daily_trades > 0 and len(trades) + opened_today >= max_daily_trades:
        return False, "max daily trades reached"
    hour_start = now - 60 * 60 * 1000
    hourly_trades = store.list_trades(from_ms=hour_start, limit=1000)
    opened_this_hour = sum(1 for position in open_positions if int(position.get("opened_at_ms") or 0) >= hour_start)
    max_trades_per_hour = int(paper_cfg.get("max_trades_per_hour", 0))
    if max_trades_per_hour > 0 and len(hourly_trades) + opened_this_hour >= max_trades_per_hour:
        return False, "max trades per hour reached"
    today_pnl = sum(float(trade["net_pnl_usdt"]) for trade in trades)
    if bool(paper_cfg.get("enforce_daily_loss_limit", False)) and today_pnl <= -float(paper_cfg.get("max_daily_loss_usdt", 1.0)):
        return False, "daily loss switch active"
    streak = 0
    for trade in trades:
        if float(trade["net_pnl_usdt"]) < 0:
            streak += 1
        else:
            break
    max_loss_streak = int(paper_cfg.get("max_loss_streak", 10))
    if max_loss_streak > 0 and streak >= max_loss_streak:
        return False, "loss streak switch active"
    return True, None


def cooldown_scope(config: dict) -> str:
    paper_cfg = config.get("paper", {})
    scope = str(paper_cfg.get("cooldown_scope") or paper_cfg.get("trade_cooldown_scope") or "active_settings").lower()
    if scope in {"all", "all_history", "global"}:
        return "all_history"
    return "active_settings"


def trade_in_cooldown_scope(trade: dict, config: dict) -> bool:
    if cooldown_scope(config) == "all_history":
        return True
    current_hash = str(config.get("runtime_settings", {}).get("hash") or "")
    if not current_hash:
        return True
    return str(trade.get("settings_hash") or "") == current_hash


def scoped_cooldown_trades(trades: list[dict], config: dict) -> list[dict]:
    return [trade for trade in trades if trade_in_cooldown_scope(trade, config)]


def cooldown_reason(store: SQLiteStore, symbol: str, direction: str, config: dict) -> str | None:
    paper_cfg = config.get("paper", {})
    now = now_ms()
    repeat_loss_count = int(paper_cfg.get("repeat_loss_symbol_count", 0))
    repeat_loss_window_ms = int(paper_cfg.get("repeat_loss_window_minutes", 0)) * 60 * 1000
    repeat_loss_cooldown_ms = int(paper_cfg.get("repeat_loss_symbol_cooldown_minutes", 0)) * 60 * 1000
    if repeat_loss_count > 0 and repeat_loss_window_ms > 0 and repeat_loss_cooldown_ms > 0:
        trades = scoped_cooldown_trades(store.list_trades(from_ms=now - repeat_loss_window_ms, symbol=symbol, limit=200), config)
        losses = [trade for trade in trades if float(trade.get("net_pnl_usdt") or 0) < 0]
        if len(losses) >= repeat_loss_count:
            latest_loss_ms = max(int(trade.get("exit_time_ms") or 0) for trade in losses)
            if now - latest_loss_ms <= repeat_loss_cooldown_ms:
                return f"symbol repeat-loss cooldown active after {len(losses)} losses ({cooldown_scope(config)})"

    stop_loss_cooldown_ms = int(paper_cfg.get("stop_loss_symbol_cooldown_minutes", 0)) * 60 * 1000
    if stop_loss_cooldown_ms > 0:
        trades = scoped_cooldown_trades(store.list_trades(from_ms=now - stop_loss_cooldown_ms, symbol=symbol, limit=100), config)
        for trade in trades:
            exit_reason = str(trade.get("exit_reason") or "").upper()
            if exit_reason == "STOP_LOSS" or float(trade.get("net_pnl_usdt") or 0) < 0:
                return f"symbol loss cooldown active after {exit_reason or 'negative trade'} ({cooldown_scope(config)})"

    symbol_cooldown_ms = int(paper_cfg.get("symbol_cooldown_minutes", 0)) * 60 * 1000
    if symbol_cooldown_ms > 0:
        trades = scoped_cooldown_trades(store.list_trades(from_ms=now - symbol_cooldown_ms, symbol=symbol, limit=100), config)
        if trades:
            return f"symbol cooldown active after {trades[0].get('exit_reason') or 'recent trade'} ({cooldown_scope(config)})"
    direction_cooldown_ms = int(paper_cfg.get("direction_cooldown_minutes", 0)) * 60 * 1000
    if direction_cooldown_ms > 0:
        trades = scoped_cooldown_trades(store.list_trades(from_ms=now - direction_cooldown_ms, direction=direction, limit=200), config)
        if trades:
            return f"{direction.upper()} cooldown active after recent trade ({cooldown_scope(config)})"
    return None


def persist_plan(store: SQLiteStore, plan: TradePlan, status: str, signal_id: int | None = None) -> int:
    risk = plan.risk.to_dict()
    risk.update(
        {
            "initial_sl_price": plan.initial_sl_price,
            "tp1_price": plan.tp1_price,
            "be_plus_price": plan.be_plus_price,
            "be_plus_move_pct": plan.be_plus_move_pct,
            "trailing_start_pct": plan.trailing_start_pct,
            "trailing_distance_pct": plan.trailing_distance_pct,
        }
    )
    return store.insert_trade_plan(
        signal_id=signal_id,
        exchange=plan.exchange,
        symbol=plan.symbol,
        direction=plan.direction,
        classifier_label=plan.classifier_label,
        strategy_version=plan.strategy_version,
        score=plan.score,
        reasons=plan.reasons,
        warnings=plan.warnings,
        entry_grid=plan.entry_grid,
        risk=risk,
        status=status,
        strategy_config_version=plan.strategy_config_version,
        settings_hash=plan.settings_hash,
        settings_json=plan.settings_json,
    )


def persist_no_trade(store: SQLiteStore, diagnostic, decision, config: dict) -> None:
    runtime_meta = config.get("runtime_settings", {})
    store.insert_trade_plan(
        exchange=diagnostic.exchange,
        symbol=diagnostic.symbol,
        direction=decision.direction,
        classifier_label=decision.label,
        strategy_version=STRATEGY_VERSION,
        score=max(diagnostic.score, decision.execution_score),
        reasons=decision.reasons or diagnostic.reasons,
        warnings=decision.warnings or diagnostic.warnings,
        entry_grid=[],
        risk={},
        status="rejected",
        strategy_config_version=runtime_meta.get("version"),
        settings_hash=runtime_meta.get("hash"),
        settings_json=runtime_meta.get("settings"),
    )


async def manage_open_positions(connector, store: SQLiteStore, broker: PaperBroker, account_id: int, config: dict) -> int:
    open_positions = store.get_open_positions(account_id)
    if not open_positions:
        return await process_paper_commands(connector, store, broker, account_id)
    tickers = await open_position_tickers(connector, open_positions)
    closed_count = 0
    for position in open_positions:
        ticker = tickers.get(position["symbol"].upper())
        if ticker is None:
            continue
        marked = broker.mark_position(position, ticker)
        direction = marked["direction"].upper()
        price = ticker.bid_price if direction == "LONG" and ticker.bid_price else ticker.ask_price if direction == "SHORT" and ticker.ask_price else ticker.last_price
        action = evaluate_position(marked, price, now_ms(), config)
        if action.updates:
            store.update_paper_position(int(position["id"]), action.updates)
            marked = store.get_position(int(position["id"])) or marked
        if action.action == "PARTIAL_CLOSE":
            broker.close_position(int(position["id"]), ticker, action.reason, action.close_fraction)
        elif action.action == "CLOSE":
            trade_id = broker.close_position(int(position["id"]), ticker, action.reason, 1.0)
            if trade_id is not None:
                closed_count += 1
    closed_count += await process_paper_commands(connector, store, broker, account_id)
    snapshot_equity(store, account_id)
    return closed_count


async def process_paper_commands(connector, store: SQLiteStore, broker: PaperBroker, account_id: int) -> int:
    commands = store.list_pending_paper_commands("MANUAL_CLOSE", limit=20)
    if not commands:
        return 0
    positions_by_id = {int(position["id"]): position for position in store.get_open_positions(account_id)}
    closed_count = 0
    for command in commands:
        command_id = int(command["id"])
        position_id = int(command.get("position_id") or 0)
        position = positions_by_id.get(position_id)
        if not position:
            store.complete_paper_command(command_id, "SKIPPED", {"reason": "position not open", "position_id": position_id})
            continue
        tickers = await open_position_tickers(connector, [position])
        ticker = tickers.get(position["symbol"].upper())
        if ticker is None:
            store.complete_paper_command(command_id, "ERROR", {"reason": "ticker unavailable", "position_id": position_id})
            continue
        trade_id = broker.close_position(position_id, ticker, "MANUAL_PAPER_CLOSE", 1.0)
        if trade_id is None:
            store.complete_paper_command(command_id, "SKIPPED", {"reason": "broker did not close", "position_id": position_id})
            continue
        closed_count += 1
        store.complete_paper_command(command_id, "DONE", {"position_id": position_id, "trade_id": trade_id})
    return closed_count


def snapshot_equity(store: SQLiteStore, account_id: int) -> None:
    account = store.get_paper_account(account_id)
    if not account:
        return
    open_positions = store.get_open_positions(account_id)
    unrealized = sum(float(position.get("unrealized_pnl_usdt") or 0) for position in open_positions)
    equity = float(account["cash_balance_usdt"]) + unrealized
    store.update_paper_account_totals(account_id, unrealized_pnl=unrealized)
    fresh = store.get_paper_account(account_id) or account
    store.insert_equity_snapshot(
        account_id=account_id,
        cash_balance_usdt=float(fresh["cash_balance_usdt"]),
        equity_usdt=equity,
        realized_pnl_usdt=float(fresh["realized_pnl_usdt"]),
        unrealized_pnl_usdt=unrealized,
        open_positions_count=len(open_positions),
    )


async def run_cycle(connector, config: dict, store: SQLiteStore, account_id: int) -> tuple[int, int]:
    engine = ScanEngine(connector, config)
    attention_state = store.get_bot_state(SCANNER_ATTENTION_STATE_KEY, {})
    cycle_now_ms = now_ms()
    attention_lookback_minutes = int(config.get("performance", {}).get("attention_recent_plan_lookback_minutes", 120))
    recent_trade_plans = store.list_recent_trade_plans(
        from_ms=cycle_now_ms - max(1, attention_lookback_minutes) * 60 * 1000,
        limit=int(config.get("performance", {}).get("attention_recent_plan_limit", 2000)),
    )
    paper_cfg = config.get("paper", {})
    trade_lookback_minutes = max(
        int(paper_cfg.get("stop_loss_symbol_cooldown_minutes", 0)),
        int(paper_cfg.get("repeat_loss_window_minutes", 0)),
        int(paper_cfg.get("symbol_cooldown_minutes", 0)),
        60,
    )
    recent_trades = store.list_trades(from_ms=cycle_now_ms - trade_lookback_minutes * 60 * 1000, limit=2000)
    result = await engine.scan_once(
        snapshot_enricher=store.enrich_snapshot_deltas,
        attention_state=attention_state,
        recent_trade_plans=recent_trade_plans,
        recent_trades=recent_trades,
    )
    if result.attention_state:
        store.set_bot_state(SCANNER_ATTENTION_STATE_KEY, result.attention_state)
    store.upsert_symbols(result.symbols)
    store.enrich_snapshot_deltas(result.tickers)
    store.insert_snapshots(result.tickers)
    for signal in result.signals:
        store.insert_signal(signal, sent_to_telegram=False)

    symbols_by_name = symbol_map(result.symbols)
    broker = PaperBroker(store, config, account_id)
    closed_count = await manage_open_positions(connector, store, broker, account_id, config)
    if store.is_bot_paused():
        store.record_bot_event("INFO", "runner", "paper open skipped", {"reason": "bot paused"})
        snapshot_equity(store, account_id)
        return 0, closed_count
    can_open, block_reason = account_can_open(store, account_id, config)
    opened_count = 0
    if not can_open:
        store.record_bot_event("INFO", "runner", "paper open skipped", {"reason": block_reason})
        return opened_count, closed_count

    seen_rejections = 0
    open_positions = store.get_open_positions(account_id)
    max_open_positions = int(config.get("paper", {}).get("max_open_positions", 1))
    max_new_positions_per_cycle = int(config.get("paper", {}).get("max_new_positions_per_cycle", 1))
    for diagnostic in result.diagnostics:
        if len(open_positions) >= max_open_positions or opened_count >= max_new_positions_per_cycle:
            break
        decision = classify_direction(diagnostic, config)
        if decision.direction == "NO_TRADE":
            if seen_rejections < int(config.get("paper", {}).get("persist_rejected_plans_per_cycle", 20)):
                persist_no_trade(store, diagnostic, decision, config)
                seen_rejections += 1
            continue
        if position_conflicts_with_open(open_positions, diagnostic.symbol, decision.direction, config):
            continue
        cooldown = cooldown_reason(store, diagnostic.symbol, decision.direction, config)
        if cooldown:
            store.record_bot_event("INFO", "runner", "paper open skipped", {"symbol": diagnostic.symbol, "direction": decision.direction, "reason": cooldown})
            continue
        plan = build_trade_plan(
            diagnostic,
            decision,
            symbols_by_name.get(diagnostic.symbol.upper()),
            float((store.get_paper_account(account_id) or {}).get("cash_balance_usdt") or 20.0),
            config,
        )
        if plan is None:
            continue
        if not plan.risk.allowed:
            persist_plan(store, plan, "rejected")
            continue
        trigger_ready, trigger_reason = entry_trigger_status(plan, diagnostic.ticker, config)
        if not trigger_ready:
            if trigger_reason:
                plan.warnings.append(trigger_reason)
            persist_plan(store, plan, "waiting_entry")
            continue
        trade_plan_id = persist_plan(store, plan, "planned")
        broker.open_position(plan, diagnostic.ticker, trade_plan_id=trade_plan_id)
        open_positions.append({"symbol": diagnostic.symbol, "direction": decision.direction})
        opened_count += 1
    snapshot_equity(store, account_id)
    return opened_count, closed_count


async def monitor_loop(connector, config: dict, store: SQLiteStore, account_id: int, stop_event: asyncio.Event) -> None:
    broker = PaperBroker(store, config, account_id)
    interval = float(config.get("paper", {}).get("monitor_interval_seconds", 2.0))
    while not stop_event.is_set():
        try:
            closed = await manage_open_positions(connector, store, broker, account_id, config)
            if closed:
                LOGGER.info("paper monitor closed=%s", closed)
        except Exception:  # noqa: BLE001 - monitor should keep retrying public-price checks.
            LOGGER.exception("paper monitor cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def settings_from_row(row: dict[str, Any]) -> RuntimeTradingSettings:
    return RuntimeTradingSettings.model_validate(row["settings"])


def ensure_runtime_config(store: SQLiteStore, config: dict) -> dict:
    defaults = build_runtime_settings_from_config(config)
    settings_row = store.ensure_runtime_settings(defaults.model_dump(mode="json"), runtime_settings_hash(defaults))
    settings = settings_from_row(settings_row)
    return apply_runtime_settings_to_config(
        config,
        settings,
        version=int(settings_row["version"]),
        settings_hash=str(settings_row["settings_hash"]),
    )


def reload_runtime_config_if_needed(store: SQLiteStore, config: dict, current_version: int | None) -> int | None:
    row = store.get_active_runtime_settings()
    if not row:
        return current_version
    version = int(row["version"])
    if version == current_version:
        return current_version
    settings = settings_from_row(row)
    next_config = apply_runtime_settings_to_config(config, settings, version=version, settings_hash=str(row["settings_hash"]))
    config.clear()
    config.update(next_config)
    store.record_bot_event("INFO", "runner", "SETTINGS_RELOADED", {"version": version, "hash": row["settings_hash"]})
    return version


async def async_main() -> int:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config["app"]["log_level"])
    store = SQLiteStore(config["app"]["database_path"])
    config = ensure_runtime_config(store, config)
    account_id = store.ensure_paper_account(
        name=config.get("paper", {}).get("account_name", "main"),
        starting_balance_usdt=float(config.get("paper", {}).get("starting_balance_usdt", 20.0)),
        mode="paper",
    )
    connector = await build_runner_connector(config)
    current_settings_version = config.get("runtime_settings", {}).get("version")
    start_ms = now_ms()
    initial_trades = len(store.list_trades(limit=10_000))
    stop_event = asyncio.Event()
    monitor_task = None if args.once else asyncio.create_task(monitor_loop(connector, config, store, account_id, stop_event))
    try:
        while True:
            current_settings_version = reload_runtime_config_if_needed(store, config, current_settings_version)
            opened, closed = await run_cycle(connector, config, store, account_id)
            store.set_bot_state("last_scanner_tick_ms", now_ms())
            LOGGER.info("paper cycle done opened=%s closed=%s", opened, closed)
            if args.once:
                return 0
            if args.until_first_trade and len(store.list_trades(limit=10_000)) > initial_trades:
                return 0
            if args.run_seconds and now_ms() - start_ms >= args.run_seconds * 1000:
                return 0
            await asyncio.sleep(float(config.get("paper", {}).get("scan_interval_seconds", config["app"].get("scan_interval_seconds", 15))))
    finally:
        stop_event.set()
        if monitor_task is not None:
            await monitor_task
        await connector.close()
        store.close()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
