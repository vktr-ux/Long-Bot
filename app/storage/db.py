from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    spread_pct REAL
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
"""


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_symbols(self, symbols: list[SymbolInfo]) -> None:
        ts = now_ms()
        self.conn.executemany(
            """
            INSERT INTO symbols(exchange, symbol, base_asset, quote_asset, status, contract_type, created_at_ms, updated_at_ms)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol) DO UPDATE SET
                base_asset=excluded.base_asset,
                quote_asset=excluded.quote_asset,
                status=excluded.status,
                contract_type=excluded.contract_type,
                updated_at_ms=excluded.updated_at_ms
            """,
            [
                (s.exchange, s.symbol, s.base_asset, s.quote_asset, s.status, s.contract_type, ts, ts)
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
                next_funding_time_ms, bid_price, ask_price, spread_pct
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                )
                for s in snapshots
            ],
        )
        self.conn.commit()

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
        details = {
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

