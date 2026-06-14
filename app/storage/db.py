from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.scanner.state import signal_notification_snapshot
from app.storage.models import Candle, SignalCandidate, SymbolInfo, TickerSnapshot
from app.utils.time import now_ms


SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_asset TEXT,
    quote_asset TEXT,
    status TEXT,
    contract_type TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    tick_size REAL,
    step_size REAL,
    min_qty REAL,
    max_qty REAL,
    market_step_size REAL,
    market_min_qty REAL,
    market_max_qty REAL,
    min_notional REAL,
    price_precision INTEGER,
    quantity_precision INTEGER,
    trigger_protect REAL,
    UNIQUE(exchange, symbol)
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_price REAL NOT NULL,
    price_24h_pct REAL,
    turnover_24h REAL,
    volume_24h REAL,
    turnover_rank_24h INTEGER,
    volume_rank_24h INTEGER,
    open_interest REAL,
    open_interest_value REAL,
    funding_rate REAL,
    next_funding_time_ms INTEGER,
    bid_price REAL,
    ask_price REAL,
    spread_pct REAL,
    mark_price REAL,
    index_price REAL,
    trade_count_24h INTEGER,
    price_change_1m_pct REAL,
    price_change_3m_pct REAL,
    price_change_5m_pct REAL,
    price_change_15m_pct REAL,
    quote_volume_delta_5m REAL,
    trade_count_delta_5m INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
ON market_snapshots(exchange, symbol, timestamp_ms);
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    turnover REAL,
    UNIQUE(exchange, symbol, interval, timestamp_ms)
);
CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval_time
ON candles(exchange, symbol, interval, timestamp_ms);
CREATE TABLE IF NOT EXISTS symbol_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    score INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    UNIQUE(exchange, symbol)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    score INTEGER NOT NULL,
    level TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    state TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    breakout_json TEXT,
    setup_json TEXT,
    grade TEXT,
    review_label TEXT,
    sent_to_telegram INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
ON signals(exchange, symbol, timestamp_ms);
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    evaluated_at_ms INTEGER NOT NULL,
    window TEXT NOT NULL,
    entry_reference_price REAL NOT NULL,
    max_price REAL,
    min_price REAL,
    close_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    close_return_pct REAL,
    target_touched INTEGER,
    invalidation_touched INTEGER,
    plus_3_before_minus_3 INTEGER,
    plus_5_before_minus_5 INTEGER,
    details_json TEXT,
    UNIQUE(signal_id, window)
);
CREATE TABLE IF NOT EXISTS paper_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    start_balance_usdt REAL NOT NULL,
    cash_balance_usdt REAL NOT NULL,
    equity_usdt REAL NOT NULL,
    realized_pnl_usdt REAL NOT NULL DEFAULT 0,
    total_fees_usdt REAL NOT NULL DEFAULT 0,
    total_slippage_usdt REAL NOT NULL DEFAULT 0,
    mode TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    classifier_label TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    score INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    entry_grid_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol_time
ON trade_plans(exchange, symbol, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_trade_plans_status
ON trade_plans(status);
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    trade_plan_id INTEGER,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    role TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL,
    trigger_price REAL,
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_position
ON paper_orders(position_id);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    position_id INTEGER,
    order_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    fee_usdt REAL NOT NULL,
    slippage_usdt REAL NOT NULL,
    liquidity_side TEXT NOT NULL,
    fill_source TEXT NOT NULL,
    filled_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_fills_position
ON paper_fills(position_id, filled_at_ms);
CREATE INDEX IF NOT EXISTS idx_paper_fills_symbol_time
ON paper_fills(symbol, filled_at_ms);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    trade_plan_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    margin_usdt REAL NOT NULL,
    leverage REAL NOT NULL,
    initial_sl_price REAL NOT NULL,
    current_sl_price REAL NOT NULL,
    tp1_price REAL NOT NULL,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_distance_pct REAL NOT NULL,
    high_watermark REAL,
    low_watermark REAL,
    unrealized_pnl_usdt REAL NOT NULL DEFAULT 0,
    realized_pnl_usdt REAL NOT NULL DEFAULT 0,
    fees_usdt REAL NOT NULL DEFAULT 0,
    mfe_usdt REAL NOT NULL DEFAULT 0,
    mae_usdt REAL NOT NULL DEFAULT 0,
    opened_at_ms INTEGER NOT NULL,
    closed_at_ms INTEGER,
    exit_reason TEXT,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status
ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol_time
ON paper_positions(symbol, opened_at_ms);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time_ms INTEGER NOT NULL,
    exit_time_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    qty REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    leverage REAL NOT NULL,
    gross_pnl_usdt REAL NOT NULL,
    fees_usdt REAL NOT NULL,
    slippage_usdt REAL NOT NULL,
    funding_usdt REAL NOT NULL,
    net_pnl_usdt REAL NOT NULL,
    roi_pct REAL NOT NULL,
    mfe_usdt REAL NOT NULL,
    mae_usdt REAL NOT NULL,
    duration_seconds REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    strategy_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_time
ON paper_trades(exit_time_ms);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol
ON paper_trades(symbol);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    cash_balance_usdt REAL NOT NULL,
    equity_usdt REAL NOT NULL,
    realized_pnl_usdt REAL NOT NULL,
    unrealized_pnl_usdt REAL NOT NULL,
    open_positions_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_snapshots_account_time
ON equity_snapshots(account_id, timestamp_ms);
CREATE TABLE IF NOT EXISTS position_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    event_type TEXT NOT NULL,
    age_seconds REAL NOT NULL,
    price REAL NOT NULL,
    entry_price REAL NOT NULL,
    move_pct REAL NOT NULL,
    roi_pct REAL NOT NULL,
    unrealized_pnl_usdt REAL NOT NULL,
    realized_pnl_usdt REAL NOT NULL,
    qty REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    current_sl_price REAL,
    tp1_price REAL,
    mfe_usdt REAL,
    mae_usdt REAL,
    strategy_config_version INTEGER,
    settings_hash TEXT,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_position_lifecycle_position_time
ON position_lifecycle_events(position_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_position_lifecycle_symbol_time
ON position_lifecycle_events(symbol, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_position_lifecycle_event_type
ON position_lifecycle_events(event_type, timestamp_ms);
CREATE TABLE IF NOT EXISTS bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_bot_events_time
ON bot_events(timestamp_ms);
CREATE TABLE IF NOT EXISTS runtime_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    settings_json TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    created_by TEXT,
    comment TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_settings_version
ON runtime_settings(version);
CREATE INDEX IF NOT EXISTS idx_runtime_settings_active
ON runtime_settings(is_active, version);
CREATE TABLE IF NOT EXISTS settings_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    previous_version INTEGER,
    new_version INTEGER NOT NULL,
    changed_by TEXT,
    comment TEXT,
    diff_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_type TEXT NOT NULL,
    position_id INTEGER,
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    executed_at_ms INTEGER,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_commands_status
ON paper_commands(status, created_at_ms);
"""


TABLE_COLUMNS: dict[str, dict[str, str]] = {
    "symbols": {
        "tick_size": "REAL",
        "step_size": "REAL",
        "min_qty": "REAL",
        "max_qty": "REAL",
        "market_step_size": "REAL",
        "market_min_qty": "REAL",
        "market_max_qty": "REAL",
        "min_notional": "REAL",
        "price_precision": "INTEGER",
        "quantity_precision": "INTEGER",
        "trigger_protect": "REAL",
    },
    "market_snapshots": {
        "mark_price": "REAL",
        "index_price": "REAL",
        "trade_count_24h": "INTEGER",
        "price_change_1m_pct": "REAL",
        "price_change_3m_pct": "REAL",
        "price_change_5m_pct": "REAL",
        "price_change_15m_pct": "REAL",
        "quote_volume_delta_5m": "REAL",
        "trade_count_delta_5m": "INTEGER",
    },
    "trade_plans": {
        "strategy_config_version": "INTEGER",
        "settings_hash": "TEXT",
        "settings_json": "TEXT",
    },
    "paper_positions": {
        "strategy_config_version": "INTEGER",
        "settings_hash": "TEXT",
        "settings_json": "TEXT",
    },
    "paper_trades": {
        "entry_fee_usdt": "REAL",
        "exit_fee_usdt": "REAL",
        "strategy_config_version": "INTEGER",
        "settings_hash": "TEXT",
        "settings_json": "TEXT",
    },
}


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        for table, columns in TABLE_COLUMNS.items():
            existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, definition in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self.conn.close()

    def upsert_symbols(self, symbols: list[SymbolInfo]) -> None:
        ts = now_ms()
        self.conn.executemany(
            """
            INSERT INTO symbols(
                exchange, symbol, base_asset, quote_asset, status, contract_type, created_at_ms, updated_at_ms,
                tick_size, step_size, min_qty, max_qty, market_step_size, market_min_qty, market_max_qty,
                min_notional, price_precision, quantity_precision, trigger_protect
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol) DO UPDATE SET
                base_asset=excluded.base_asset,
                quote_asset=excluded.quote_asset,
                status=excluded.status,
                contract_type=excluded.contract_type,
                updated_at_ms=excluded.updated_at_ms,
                tick_size=excluded.tick_size,
                step_size=excluded.step_size,
                min_qty=excluded.min_qty,
                max_qty=excluded.max_qty,
                market_step_size=excluded.market_step_size,
                market_min_qty=excluded.market_min_qty,
                market_max_qty=excluded.market_max_qty,
                min_notional=excluded.min_notional,
                price_precision=excluded.price_precision,
                quantity_precision=excluded.quantity_precision,
                trigger_protect=excluded.trigger_protect
            """,
            [
                (
                    s.exchange,
                    s.symbol,
                    s.base_asset,
                    s.quote_asset,
                    s.status,
                    s.contract_type,
                    ts,
                    ts,
                    s.tick_size,
                    s.step_size,
                    s.min_qty,
                    s.max_qty,
                    s.market_step_size,
                    s.market_min_qty,
                    s.market_max_qty,
                    s.min_notional,
                    s.price_precision,
                    s.quantity_precision,
                    s.trigger_protect,
                )
                for s in symbols
            ],
        )
        self.conn.commit()

    def insert_snapshots(self, snapshots: list[TickerSnapshot]) -> None:
        self.conn.executemany(
            """
            INSERT INTO market_snapshots(
                timestamp_ms, exchange, symbol, last_price, price_24h_pct, turnover_24h, volume_24h,
                turnover_rank_24h, volume_rank_24h, open_interest, open_interest_value, funding_rate,
                next_funding_time_ms, bid_price, ask_price, spread_pct, mark_price, index_price,
                trade_count_24h, price_change_1m_pct, price_change_3m_pct, price_change_5m_pct,
                price_change_15m_pct, quote_volume_delta_5m, trade_count_delta_5m
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.timestamp_ms,
                    s.exchange,
                    s.symbol,
                    s.last_price,
                    s.price_24h_pct,
                    s.turnover_24h,
                    s.volume_24h,
                    s.turnover_rank_24h,
                    s.volume_rank_24h,
                    s.open_interest,
                    s.open_interest_value,
                    s.funding_rate,
                    s.next_funding_time_ms,
                    s.bid_price,
                    s.ask_price,
                    s.spread_pct,
                    s.mark_price,
                    s.index_price,
                    s.trade_count_24h,
                    s.price_change_1m_pct,
                    s.price_change_3m_pct,
                    s.price_change_5m_pct,
                    s.price_change_15m_pct,
                    s.quote_volume_delta_5m,
                    s.trade_count_delta_5m,
                )
                for s in snapshots
            ],
        )
        self.conn.commit()

    def enrich_snapshot_deltas(self, snapshots: list[TickerSnapshot], windows_seconds: tuple[int, ...] = (60, 180, 300, 900)) -> None:
        if not snapshots:
            return
        for snapshot in snapshots:
            for seconds in windows_seconds:
                cutoff = snapshot.timestamp_ms - seconds * 1000
                row = self.conn.execute(
                    """
                    SELECT last_price, turnover_24h, trade_count_24h FROM market_snapshots
                    WHERE exchange=? AND symbol=? AND timestamp_ms <= ?
                    ORDER BY timestamp_ms DESC LIMIT 1
                    """,
                    (snapshot.exchange, snapshot.symbol, cutoff),
                ).fetchone()
                if not row:
                    continue
                old_price = row["last_price"]
                if old_price:
                    change = (snapshot.last_price - float(old_price)) / float(old_price) * 100
                    if seconds == 60:
                        snapshot.price_change_1m_pct = change
                    elif seconds == 180:
                        snapshot.price_change_3m_pct = change
                    elif seconds == 300:
                        snapshot.price_change_5m_pct = change
                    elif seconds == 900:
                        snapshot.price_change_15m_pct = change
                if seconds == 300:
                    old_turnover = row["turnover_24h"]
                    old_count = row["trade_count_24h"]
                    if old_turnover is not None and snapshot.turnover_24h is not None:
                        snapshot.quote_volume_delta_5m = max(0.0, snapshot.turnover_24h - float(old_turnover))
                    if old_count is not None and snapshot.trade_count_24h is not None:
                        snapshot.trade_count_delta_5m = max(0, snapshot.trade_count_24h - int(old_count))

    def latest_snapshot_timestamp_ms(self) -> int | None:
        row = self.conn.execute("SELECT MAX(timestamp_ms) AS latest_ms FROM market_snapshots").fetchone()
        if not row or row["latest_ms"] is None:
            return None
        return int(row["latest_ms"])

    def upsert_candles(self, candles: list[Candle]) -> None:
        self.conn.executemany(
            """
            INSERT INTO candles(timestamp_ms, exchange, symbol, interval, open, high, low, close, volume, turnover)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol, interval, timestamp_ms) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                volume=excluded.volume, turnover=excluded.turnover
            """,
            [
                (c.timestamp_ms, c.exchange, c.symbol, c.interval, c.open, c.high, c.low, c.close, c.volume, c.turnover)
                for c in candles
            ],
        )
        self.conn.commit()

    def get_state(self, exchange: str, symbol: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM symbol_states WHERE exchange=? AND symbol=?", (exchange, symbol)
        ).fetchone()
        if not row:
            return None
        details = json.loads(row["details_json"])
        details.update({"state": row["state"], "score": row["score"], "timestamp_ms": row["timestamp_ms"]})
        return details

    def upsert_state(self, signal: SignalCandidate, sent: bool) -> None:
        snapshot = signal_notification_snapshot(signal, sent_at_ms=signal.timestamp_ms if sent else 0)
        details = {
            **snapshot,
            "last_score": signal.score,
            "last_level": signal.level,
            "last_sent_ms": signal.timestamp_ms if sent else None,
            "warnings": signal.warnings,
        }
        self.conn.execute(
            """
            INSERT INTO symbol_states(timestamp_ms, exchange, symbol, state, score, details_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol) DO UPDATE SET
                timestamp_ms=excluded.timestamp_ms,
                state=excluded.state,
                score=excluded.score,
                details_json=excluded.details_json
            """,
            (signal.timestamp_ms, signal.exchange, signal.symbol, signal.state, signal.score, json.dumps(details)),
        )
        self.conn.commit()

    def sent_signal_timestamps_since(self, cutoff_ms: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT timestamp_ms FROM signals
            WHERE sent_to_telegram=1 AND timestamp_ms >= ?
            ORDER BY timestamp_ms ASC
            """,
            (cutoff_ms,),
        ).fetchall()
        return [int(row["timestamp_ms"]) for row in rows]

    def insert_signal(self, signal: SignalCandidate, sent_to_telegram: bool) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO signals(
                timestamp_ms, exchange, symbol, score, level, signal_type, state,
                scores_json, reasons_json, warnings_json, metrics_json, breakout_json, setup_json,
                grade, review_label, sent_to_telegram
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.timestamp_ms,
                signal.exchange,
                signal.symbol,
                signal.score,
                signal.level,
                signal.signal_type,
                signal.state,
                json.dumps(signal.scores),
                json.dumps(signal.reasons),
                json.dumps(signal.warnings),
                json.dumps(asdict(signal.metrics)),
                json.dumps(asdict(signal.breakout)) if signal.breakout else None,
                json.dumps(asdict(signal.setup)) if signal.setup else None,
                signal.grade,
                signal.review_label,
                1 if sent_to_telegram else 0,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def ensure_paper_account(self, name: str = "main", starting_balance_usdt: float = 20.0, mode: str = "paper") -> int:
        row = self.conn.execute("SELECT id FROM paper_accounts WHERE name=?", (name,)).fetchone()
        if row:
            return int(row["id"])
        ts = now_ms()
        cur = self.conn.execute(
            """
            INSERT INTO paper_accounts(
                name, start_balance_usdt, cash_balance_usdt, equity_usdt, realized_pnl_usdt,
                total_fees_usdt, total_slippage_usdt, mode, created_at_ms, updated_at_ms
            ) VALUES(?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (name, starting_balance_usdt, starting_balance_usdt, starting_balance_usdt, mode, ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_paper_account(self, account_id: int | None = None, name: str = "main") -> dict[str, Any] | None:
        if account_id is not None:
            row = self.conn.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM paper_accounts WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def update_paper_account_totals(
        self,
        account_id: int,
        realized_delta: float = 0.0,
        fee_delta: float = 0.0,
        slippage_delta: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> None:
        ts = now_ms()
        self.conn.execute(
            """
            UPDATE paper_accounts
            SET realized_pnl_usdt=realized_pnl_usdt + ?,
                total_fees_usdt=total_fees_usdt + ?,
                total_slippage_usdt=total_slippage_usdt + ?,
                cash_balance_usdt=cash_balance_usdt + ?,
                equity_usdt=cash_balance_usdt + ? + ?,
                updated_at_ms=?
            WHERE id=?
            """,
            (realized_delta, fee_delta, slippage_delta, realized_delta, realized_delta, unrealized_pnl, ts, account_id),
        )
        self.conn.commit()

    def reset_paper_account(self, account_id: int, starting_balance_usdt: float, unrealized_pnl: float = 0.0) -> None:
        ts = now_ms()
        equity = starting_balance_usdt + unrealized_pnl
        self.conn.execute(
            """
            UPDATE paper_accounts
            SET start_balance_usdt=?,
                cash_balance_usdt=?,
                equity_usdt=?,
                realized_pnl_usdt=0,
                total_fees_usdt=0,
                total_slippage_usdt=0,
                updated_at_ms=?
            WHERE id=?
            """,
            (starting_balance_usdt, starting_balance_usdt, equity, ts, account_id),
        )
        self.conn.commit()

    def insert_trade_plan(
        self,
        *,
        exchange: str,
        symbol: str,
        direction: str,
        classifier_label: str,
        strategy_version: str,
        score: int,
        reasons: list[str],
        warnings: list[str],
        entry_grid: list[dict[str, Any]],
        risk: dict[str, Any],
        status: str,
        signal_id: int | None = None,
        created_at_ms: int | None = None,
        strategy_config_version: int | None = None,
        settings_hash: str | None = None,
        settings_json: dict[str, Any] | str | None = None,
    ) -> int:
        ts = created_at_ms or now_ms()
        settings_json_value = json.dumps(settings_json) if isinstance(settings_json, dict) else settings_json
        cur = self.conn.execute(
            """
            INSERT INTO trade_plans(
                signal_id, exchange, symbol, direction, classifier_label, strategy_version, score,
                reasons_json, warnings_json, entry_grid_json, risk_json, status, created_at_ms,
                strategy_config_version, settings_hash, settings_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                exchange,
                symbol,
                direction,
                classifier_label,
                strategy_version,
                score,
                json.dumps(reasons),
                json.dumps(warnings),
                json.dumps(entry_grid),
                json.dumps(risk),
                status,
                ts,
                strategy_config_version,
                settings_hash,
                settings_json_value,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_trade_plan_status(self, trade_plan_id: int, status: str) -> None:
        self.conn.execute("UPDATE trade_plans SET status=? WHERE id=?", (status, trade_plan_id))
        self.conn.commit()

    def insert_paper_order(
        self,
        *,
        account_id: int,
        trade_plan_id: int | None,
        position_id: int | None,
        symbol: str,
        side: str,
        order_type: str,
        role: str,
        qty: float,
        price: float | None,
        trigger_price: float | None,
        status: str,
        timestamp_ms: int | None = None,
    ) -> int:
        ts = timestamp_ms or now_ms()
        cur = self.conn.execute(
            """
            INSERT INTO paper_orders(
                account_id, trade_plan_id, position_id, symbol, side, order_type, role,
                qty, price, trigger_price, status, created_at_ms, updated_at_ms
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, trade_plan_id, position_id, symbol, side, order_type, role, qty, price, trigger_price, status, ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_paper_fill(
        self,
        *,
        account_id: int,
        position_id: int | None,
        order_id: int | None,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        notional_usdt: float,
        fee_usdt: float,
        slippage_usdt: float,
        liquidity_side: str,
        fill_source: str,
        filled_at_ms: int | None = None,
    ) -> int:
        ts = filled_at_ms or now_ms()
        cur = self.conn.execute(
            """
            INSERT INTO paper_fills(
                account_id, position_id, order_id, symbol, side, qty, price, notional_usdt,
                fee_usdt, slippage_usdt, liquidity_side, fill_source, filled_at_ms
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                position_id,
                order_id,
                symbol,
                side,
                qty,
                price,
                notional_usdt,
                fee_usdt,
                slippage_usdt,
                liquidity_side,
                fill_source,
                ts,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_paper_position(self, position: dict[str, Any]) -> int:
        fields = [
            "account_id",
            "trade_plan_id",
            "symbol",
            "direction",
            "status",
            "qty",
            "entry_price",
            "notional_usdt",
            "margin_usdt",
            "leverage",
            "initial_sl_price",
            "current_sl_price",
            "tp1_price",
            "trailing_active",
            "trailing_distance_pct",
            "high_watermark",
            "low_watermark",
            "unrealized_pnl_usdt",
            "realized_pnl_usdt",
            "fees_usdt",
            "mfe_usdt",
            "mae_usdt",
            "opened_at_ms",
            "closed_at_ms",
            "exit_reason",
            "details_json",
            "strategy_config_version",
            "settings_hash",
            "settings_json",
        ]
        values = [position.get(field) for field in fields]
        placeholders = ", ".join("?" for _ in fields)
        cur = self.conn.execute(
            f"INSERT INTO paper_positions({', '.join(fields)}) VALUES({placeholders})",
            values,
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_paper_position(self, position_id: int, updates: dict[str, Any]) -> None:
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.conn.execute(
            f"UPDATE paper_positions SET {assignments} WHERE id=?",
            [*updates.values(), position_id],
        )
        self.conn.commit()

    def get_open_positions(self, account_id: int | None = None) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        if account_id is None:
            query = "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at_ms DESC"
            params = ()
        else:
            query = "SELECT * FROM paper_positions WHERE status='OPEN' AND account_id=? ORDER BY opened_at_ms DESC"
            params = (account_id,)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def get_position(self, position_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM paper_positions WHERE id=?", (position_id,)).fetchone()
        return dict(row) if row else None

    def get_position_fills(self, position_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM paper_fills WHERE position_id=? ORDER BY filled_at_ms ASC, id ASC",
                (position_id,),
            ).fetchall()
        ]

    def insert_paper_trade(self, trade: dict[str, Any]) -> int:
        fields = [
            "account_id",
            "position_id",
            "symbol",
            "direction",
            "entry_time_ms",
            "exit_time_ms",
            "entry_price",
            "exit_price",
            "qty",
            "notional_usdt",
            "leverage",
            "gross_pnl_usdt",
            "fees_usdt",
            "slippage_usdt",
            "funding_usdt",
            "net_pnl_usdt",
            "roi_pct",
            "mfe_usdt",
            "mae_usdt",
            "duration_seconds",
            "exit_reason",
            "strategy_version",
            "entry_fee_usdt",
            "exit_fee_usdt",
            "strategy_config_version",
            "settings_hash",
            "settings_json",
        ]
        values = [trade.get(field) for field in fields]
        placeholders = ", ".join("?" for _ in fields)
        cur = self.conn.execute(
            f"INSERT INTO paper_trades({', '.join(fields)}) VALUES({placeholders})",
            values,
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_equity_snapshot(
        self,
        account_id: int,
        cash_balance_usdt: float,
        equity_usdt: float,
        realized_pnl_usdt: float,
        unrealized_pnl_usdt: float,
        open_positions_count: int,
        timestamp_ms: int | None = None,
    ) -> int:
        ts = timestamp_ms or now_ms()
        cur = self.conn.execute(
            """
            INSERT INTO equity_snapshots(
                account_id, timestamp_ms, cash_balance_usdt, equity_usdt,
                realized_pnl_usdt, unrealized_pnl_usdt, open_positions_count
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, ts, cash_balance_usdt, equity_usdt, realized_pnl_usdt, unrealized_pnl_usdt, open_positions_count),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_position_lifecycle_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        fields = [
            "timestamp_ms",
            "position_id",
            "account_id",
            "symbol",
            "direction",
            "event_type",
            "age_seconds",
            "price",
            "entry_price",
            "move_pct",
            "roi_pct",
            "unrealized_pnl_usdt",
            "realized_pnl_usdt",
            "qty",
            "notional_usdt",
            "current_sl_price",
            "tp1_price",
            "mfe_usdt",
            "mae_usdt",
            "strategy_config_version",
            "settings_hash",
            "details_json",
        ]
        placeholders = ", ".join("?" for _ in fields)
        self.conn.executemany(
            f"INSERT INTO position_lifecycle_events({', '.join(fields)}) VALUES({placeholders})",
            [[event.get(field) for field in fields] for event in events],
        )
        self.conn.commit()

    def list_position_lifecycle_events(
        self,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        symbol: str | None = None,
        position_id: int | None = None,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM position_lifecycle_events WHERE 1=1"
        params: list[Any] = []
        if from_ms is not None:
            query += " AND timestamp_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            query += " AND timestamp_ms <= ?"
            params.append(to_ms)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if position_id is not None:
            query += " AND position_id = ?"
            params.append(position_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.upper())
        query += " ORDER BY timestamp_ms DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def list_trades(
        self,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        symbol: str | None = None,
        direction: str | None = None,
        exit_reason: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM paper_trades WHERE 1=1"
        params: list[Any] = []
        if from_ms is not None:
            query += " AND exit_time_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            query += " AND exit_time_ms <= ?"
            params.append(to_ms)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if direction:
            query += " AND direction = ?"
            params.append(direction.upper())
        if exit_reason:
            query += " AND exit_reason = ?"
            params.append(exit_reason.upper())
        query += " ORDER BY exit_time_ms DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def list_equity_snapshots(self, from_ms: int | None = None, to_ms: int | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        query = "SELECT * FROM equity_snapshots WHERE 1=1"
        params: list[Any] = []
        if from_ms is not None:
            query += " AND timestamp_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            query += " AND timestamp_ms <= ?"
            params.append(to_ms)
        query += " ORDER BY timestamp_ms ASC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def list_recent_trade_plans(self, from_ms: int | None = None, to_ms: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM trade_plans WHERE 1=1"
        params: list[Any] = []
        if from_ms is not None:
            query += " AND created_at_ms >= ?"
            params.append(from_ms)
        if to_ms is not None:
            query += " AND created_at_ms <= ?"
            params.append(to_ms)
        query += " ORDER BY created_at_ms DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def record_bot_event(self, level: str, component: str, message: str, details: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO bot_events(timestamp_ms, level, component, message, details_json)
            VALUES(?, ?, ?, ?, ?)
            """,
            (now_ms(), level, component, message, json.dumps(details or {})),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_active_runtime_settings(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM runtime_settings
            WHERE is_active=1
            ORDER BY version DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["settings"] = json.loads(data["settings_json"])
        return data

    def ensure_runtime_settings(
        self,
        settings: dict[str, Any],
        settings_hash: str,
        *,
        created_by: str = "system",
        comment: str = "initial settings from YAML",
    ) -> dict[str, Any]:
        active = self.get_active_runtime_settings()
        if active:
            return active
        ts = now_ms()
        self.conn.execute(
            """
            INSERT INTO runtime_settings(version, settings_json, settings_hash, is_active, created_at_ms, created_by, comment)
            VALUES(1, ?, ?, 1, ?, ?, ?)
            """,
            (json.dumps(settings, sort_keys=True), settings_hash, ts, created_by, comment),
        )
        self.conn.execute(
            """
            INSERT INTO settings_audit_log(timestamp_ms, previous_version, new_version, changed_by, comment, diff_json)
            VALUES(?, NULL, 1, ?, ?, ?)
            """,
            (ts, created_by, comment, json.dumps({"initial": True})),
        )
        self.conn.commit()
        return self.get_active_runtime_settings() or {}

    def apply_runtime_settings(
        self,
        settings: dict[str, Any],
        settings_hash: str,
        *,
        changed_by: str = "dashboard",
        comment: str | None = None,
        diff: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = self.get_active_runtime_settings()
        previous_version = int(active["version"]) if active else None
        new_version = (previous_version or 0) + 1
        ts = now_ms()
        with self.conn:
            self.conn.execute("UPDATE runtime_settings SET is_active=0 WHERE is_active=1")
            self.conn.execute(
                """
                INSERT INTO runtime_settings(version, settings_json, settings_hash, is_active, created_at_ms, created_by, comment)
                VALUES(?, ?, ?, 1, ?, ?, ?)
                """,
                (new_version, json.dumps(settings, sort_keys=True), settings_hash, ts, changed_by, comment),
            )
            self.conn.execute(
                """
                INSERT INTO settings_audit_log(timestamp_ms, previous_version, new_version, changed_by, comment, diff_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (ts, previous_version, new_version, changed_by, comment, json.dumps(diff or {})),
            )
        return self.get_active_runtime_settings() or {}

    def list_runtime_settings(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runtime_settings ORDER BY version DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["settings"] = json.loads(data["settings_json"])
            out.append(data)
        return out

    def list_settings_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM settings_audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["diff"] = json.loads(data.get("diff_json") or "{}")
            out.append(data)
        return out

    def set_bot_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO bot_runtime_state(key, value, updated_at_ms)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_ms=excluded.updated_at_ms
            """,
            (key, json.dumps(value), now_ms()),
        )
        self.conn.commit()

    def get_bot_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM bot_runtime_state WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def is_bot_paused(self) -> bool:
        return bool(self.get_bot_state("paused", False))

    def enqueue_paper_command(self, command_type: str, position_id: int | None = None, details: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO paper_commands(command_type, position_id, status, created_at_ms, executed_at_ms, details_json)
            VALUES(?, ?, 'PENDING', ?, NULL, ?)
            """,
            (command_type, position_id, now_ms(), json.dumps(details or {})),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_pending_paper_commands(self, command_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM paper_commands WHERE status='PENDING'"
        params: list[Any] = []
        if command_type:
            query += " AND command_type=?"
            params.append(command_type)
        query += " ORDER BY created_at_ms ASC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def complete_paper_command(self, command_id: int, status: str, details: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            UPDATE paper_commands
            SET status=?, executed_at_ms=?, details_json=?
            WHERE id=?
            """,
            (status, now_ms(), json.dumps(details or {}), command_id),
        )
        self.conn.commit()
