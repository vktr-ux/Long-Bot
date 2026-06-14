from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.runtime_settings import (
    RuntimeTradingSettings,
    apply_runtime_settings_to_config,
    build_runtime_settings_from_config,
    normalize_settings_payload,
    runtime_settings_hash,
    settings_to_yaml,
)
from app.storage.db import SQLiteStore
from app.utils.time import now_ms


def parse_ms(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def safe_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def max_drawdown(equity: list[dict[str, Any]]) -> float:
    peak: float | None = None
    worst = 0.0
    for row in equity:
        value = float(row["equity_usdt"])
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak * 100)
    return worst


def summarize_trades(trades: list[dict[str, Any]], start_balance: float) -> dict[str, Any]:
    net = sum(float(trade["net_pnl_usdt"]) for trade in trades)
    gross_profit = sum(float(trade["net_pnl_usdt"]) for trade in trades if float(trade["net_pnl_usdt"]) > 0)
    gross_loss = abs(sum(float(trade["net_pnl_usdt"]) for trade in trades if float(trade["net_pnl_usdt"]) < 0))
    wins = [trade for trade in trades if float(trade["net_pnl_usdt"]) > 0]
    losses = [trade for trade in trades if float(trade["net_pnl_usdt"]) < 0]
    win_loss_ratio = (len(wins) / len(losses)) if losses else (None if wins else 0.0)
    loss_win_ratio = (len(losses) / len(wins)) if wins else (float(len(losses)) if losses else 0.0)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl_usdt": net,
        "roi_pct": (net / start_balance * 100) if start_balance else 0,
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0,
        "win_loss_ratio": win_loss_ratio,
        "loss_win_ratio": loss_win_ratio,
        "win_loss_target_4x": len(wins) >= 4 * len(losses) if losses else bool(wins),
        "win_loss_target_10x": len(wins) >= 10 * len(losses) if losses else bool(wins),
        "profit_factor": (gross_profit / gross_loss) if gross_loss else (gross_profit if gross_profit else 0),
        "avg_win_usdt": (sum(float(t["net_pnl_usdt"]) for t in wins) / len(wins)) if wins else 0,
        "avg_loss_usdt": (sum(float(t["net_pnl_usdt"]) for t in losses) / len(losses)) if losses else 0,
        "stopout_count": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        "breakeven_plus_count": sum(1 for t in trades if t["exit_reason"] == "BREAKEVEN_PLUS_STOP"),
        "trailing_count": sum(1 for t in trades if t["exit_reason"] == "TRAILING_STOP"),
        "total_fees_usdt": sum(float(t["fees_usdt"]) for t in trades),
    }


def group_trades_by_exit_reason(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for trade in trades:
        key = str(trade.get("exit_reason") or "UNKNOWN")
        bucket = grouped.setdefault(
            key,
            {
                "exit_reason": key,
                "trades": 0,
                "wins": 0,
                "net_pnl_usdt": 0.0,
                "gross_pnl_usdt": 0.0,
                "fees_usdt": 0.0,
                "slippage_usdt": 0.0,
            },
        )
        bucket["trades"] += 1
        bucket["wins"] += 1 if float(trade["net_pnl_usdt"]) > 0 else 0
        bucket["net_pnl_usdt"] += float(trade["net_pnl_usdt"])
        bucket["gross_pnl_usdt"] += float(trade["gross_pnl_usdt"])
        bucket["fees_usdt"] += float(trade["fees_usdt"])
        bucket["slippage_usdt"] += float(trade["slippage_usdt"])
    for bucket in grouped.values():
        bucket["win_rate_pct"] = (bucket["wins"] / bucket["trades"] * 100) if bucket["trades"] else 0
    return sorted(grouped.values(), key=lambda row: float(row["net_pnl_usdt"]))


def matches_active_settings(trade: dict[str, Any], active: dict[str, Any] | None) -> bool:
    if not active:
        return True
    active_version = str(active.get("version") or "")
    trade_version = str(trade.get("strategy_config_version") or "")
    if active_version and trade_version:
        return trade_version == active_version
    active_hash = str(active.get("settings_hash") or "")
    return not active_hash or str(trade.get("settings_hash") or "") == active_hash


def compact_trade_row(trade: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "position_id",
        "symbol",
        "direction",
        "entry_time_ms",
        "exit_time_ms",
        "duration_seconds",
        "entry_price",
        "exit_price",
        "qty",
        "notional_usdt",
        "margin_usdt",
        "leverage",
        "gross_pnl_usdt",
        "funding_usdt",
        "fees_usdt",
        "slippage_usdt",
        "net_pnl_usdt",
        "roi_pct",
        "margin_mode",
        "liquidation_price",
        "liquidation_source",
        "planned_loss_usdt",
        "planned_target_net_profit_usdt",
        "planned_reward_risk_ratio",
        "required_reward_risk_ratio",
        "mfe_usdt",
        "mae_usdt",
        "exit_reason",
        "strategy_config_version",
        "settings_hash",
    ]
    return {key: trade.get(key) for key in keys}


def build_settings_impact(store: SQLiteStore, start_balance: float) -> list[dict[str, Any]]:
    settings_rows = store.list_runtime_settings(limit=200)
    settings_by_version = {str(row["version"]): row for row in settings_rows}
    trades = enrich_trade_rows(store, store.list_trades(limit=50_000))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        key = str(trade.get("strategy_config_version") or "legacy")
        grouped.setdefault(key, []).append(trade)

    for version in settings_by_version:
        grouped.setdefault(version, [])

    impact: list[dict[str, Any]] = []
    for version, version_trades in grouped.items():
        settings_row = settings_by_version.get(version)
        version_start_balance = start_balance
        if settings_row:
            version_start_balance = float(settings_row.get("settings", {}).get("risk", {}).get("starting_balance_usdt") or start_balance)
        stats = summarize_trades(version_trades, version_start_balance)
        first_exit = min((int(trade["exit_time_ms"]) for trade in version_trades), default=None)
        last_exit = max((int(trade["exit_time_ms"]) for trade in version_trades), default=None)
        impact.append(
            {
                "version": version,
                "is_active": bool(settings_row and settings_row.get("is_active")),
                "settings_hash": settings_row.get("settings_hash") if settings_row else None,
                "settings_hash_short": str(settings_row.get("settings_hash") or "")[:12] if settings_row else None,
                "created_at_ms": settings_row.get("created_at_ms") if settings_row else None,
                "created_by": settings_row.get("created_by") if settings_row else None,
                "comment": settings_row.get("comment") if settings_row else None,
                "first_exit_time_ms": first_exit,
                "last_exit_time_ms": last_exit,
                "stats": stats,
                "by_exit_reason": group_trades_by_exit_reason(version_trades),
                "trades": [compact_trade_row(trade) for trade in sorted(version_trades, key=lambda row: int(row["exit_time_ms"]), reverse=True)],
            }
        )
    return sorted(impact, key=lambda row: -1 if row["version"] == "legacy" else -int(row["version"]))


def enrich_trade_rows(store: SQLiteStore, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        position = store.get_position(int(row["position_id"])) if row.get("position_id") else None
        position_details = safe_json(position.get("details_json") if position else None, {})
        if position:
            row["margin_usdt"] = float(position.get("margin_usdt") or 0)
        elif row.get("notional_usdt") is not None and row.get("leverage"):
            row["margin_usdt"] = float(row.get("notional_usdt") or 0) / float(row.get("leverage") or 1)
        else:
            row["margin_usdt"] = 0.0
        row["margin_mode"] = position_details.get("margin_mode") or "isolated"
        row["liquidation_price"] = position_details.get("liquidation_price")
        row["liquidation_source"] = position_details.get("liquidation_source")
        row["planned_loss_usdt"] = position_details.get("planned_loss_usdt")
        row["planned_target_net_profit_usdt"] = position_details.get("planned_target_net_profit_usdt")
        row["planned_reward_risk_ratio"] = position_details.get("planned_reward_risk_ratio")
        row["required_reward_risk_ratio"] = position_details.get("required_reward_risk_ratio")
        if row.get("entry_fee_usdt") is None or row.get("exit_fee_usdt") is None:
            fills = store.get_position_fills(int(row["position_id"]))
            direction = row["direction"].upper()
            entry_side = "BUY" if direction == "LONG" else "SELL"
            exit_side = "SELL" if direction == "LONG" else "BUY"
            row["entry_fee_usdt"] = sum(float(fill["fee_usdt"]) for fill in fills if fill["side"].upper() == entry_side)
            row["exit_fee_usdt"] = sum(float(fill["fee_usdt"]) for fill in fills if fill["side"].upper() == exit_side)
        row["total_fees_usdt"] = float(row.get("fees_usdt") or 0)
        enriched.append(row)
    return enriched


def dict_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    keys = sorted(set(before) | set(after))
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            nested = dict_diff(old, new)
            if nested:
                diff[key] = nested
        elif old != new:
            diff[key] = {"from": old, "to": new}
    return diff


def active_settings_for_app(store: SQLiteStore, config: dict[str, Any]) -> tuple[RuntimeTradingSettings, dict[str, Any]]:
    defaults = build_runtime_settings_from_config(config)
    row = store.ensure_runtime_settings(defaults.model_dump(mode="json"), runtime_settings_hash(defaults))
    settings = RuntimeTradingSettings.model_validate(row["settings"])
    effective_config = apply_runtime_settings_to_config(
        config,
        settings,
        version=int(row["version"]),
        settings_hash=str(row["settings_hash"]),
    )
    return settings, effective_config


def create_app(config_path: str = "config.paper.yaml") -> FastAPI:
    config = load_config(config_path)
    store = SQLiteStore(config["app"]["database_path"])
    _settings, config = active_settings_for_app(store, config)
    app = FastAPI(title="Long-Bot Paper Dashboard")
    app.state.config = config
    app.state.store = store
    app.state.started_at_ms = now_ms()
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def auth(request: Request, token: str | None = Query(default=None)) -> None:
        expected = app.state.config.get("web", {}).get("dashboard_token") or os.getenv("DASHBOARD_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN is required")
        header = request.headers.get("authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.lower().startswith("bearer ") else ""
        provided = token or request.headers.get("x-dashboard-token") or bearer or request.cookies.get("dashboard_token")
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid dashboard token")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        account = app.state.store.get_paper_account()
        active = app.state.store.get_active_runtime_settings()
        return {
            "ok": True,
            "mode": config.get("paper", {}).get("account_name", "main"),
            "account_ready": bool(account),
            "settings_version": active.get("version") if active else None,
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, response: Response, token: str | None = Query(default=None)) -> str:
        expected = app.state.config.get("web", {}).get("dashboard_token") or os.getenv("DASHBOARD_TOKEN", "")
        provided = token or request.cookies.get("dashboard_token")
        if expected and provided == expected:
            response.set_cookie("dashboard_token", expected, httponly=False, samesite="lax")
            return (Path(__file__).resolve().parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
        return (Path(__file__).resolve().parent / "templates" / "login.html").read_text(encoding="utf-8")

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def api_summary(from_: str | None = Query(default=None, alias="from"), to: str | None = None) -> dict[str, Any]:
        from_ms = parse_ms(from_)
        to_ms = parse_ms(to)
        account = app.state.store.get_paper_account() or {}
        start_balance = float(account.get("start_balance_usdt") or config.get("paper", {}).get("starting_balance_usdt", 20.0))
        active = app.state.store.get_active_runtime_settings() or {}
        all_trades = enrich_trade_rows(app.state.store, app.state.store.list_trades(from_ms=from_ms, to_ms=to_ms, limit=10_000))
        trades = [trade for trade in all_trades if matches_active_settings(trade, active)]
        equity_from_ms = from_ms
        if equity_from_ms is None and active.get("created_at_ms"):
            equity_from_ms = int(active["created_at_ms"])
        equity = app.state.store.list_equity_snapshots(from_ms=equity_from_ms, to_ms=to_ms, limit=10_000)
        stats = summarize_trades(trades, start_balance)
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        day_start = now - 24 * 60 * 60 * 1000
        all_today_trades = enrich_trade_rows(app.state.store, app.state.store.list_trades(from_ms=day_start, limit=10_000))
        today_trades = [trade for trade in all_today_trades if matches_active_settings(trade, active)]
        by_symbol: dict[str, float] = {}
        by_direction: dict[str, float] = {}
        by_settings: dict[str, dict[str, Any]] = {}
        for trade in trades:
            by_symbol[trade["symbol"]] = by_symbol.get(trade["symbol"], 0.0) + float(trade["net_pnl_usdt"])
            by_direction[trade["direction"]] = by_direction.get(trade["direction"], 0.0) + float(trade["net_pnl_usdt"])
        for trade in all_trades:
            version_key = str(trade.get("strategy_config_version") or "legacy")
            bucket = by_settings.setdefault(version_key, {"trades": 0, "net_pnl_usdt": 0.0, "wins": 0, "losses": 0, "stop_loss_count": 0, "breakeven_plus_count": 0, "trailing_count": 0})
            bucket["trades"] += 1
            bucket["net_pnl_usdt"] += float(trade["net_pnl_usdt"])
            bucket["wins"] += 1 if float(trade["net_pnl_usdt"]) > 0 else 0
            bucket["losses"] += 1 if float(trade["net_pnl_usdt"]) < 0 else 0
            bucket["stop_loss_count"] += 1 if trade["exit_reason"] == "STOP_LOSS" else 0
            bucket["breakeven_plus_count"] += 1 if trade["exit_reason"] == "BREAKEVEN_PLUS_STOP" else 0
            bucket["trailing_count"] += 1 if trade["exit_reason"] == "TRAILING_STOP" else 0
        for bucket in by_settings.values():
            bucket["win_rate_pct"] = (bucket["wins"] / bucket["trades"] * 100) if bucket["trades"] else 0
            bucket["win_loss_ratio"] = (bucket["wins"] / bucket["losses"]) if bucket["losses"] else (None if bucket["wins"] else 0.0)
            bucket["loss_win_ratio"] = (bucket["losses"] / bucket["wins"]) if bucket["wins"] else (float(bucket["losses"]) if bucket["losses"] else 0.0)
            bucket["win_loss_target_4x"] = bucket["wins"] >= 4 * bucket["losses"] if bucket["losses"] else bool(bucket["wins"])
            bucket["win_loss_target_10x"] = bucket["wins"] >= 10 * bucket["losses"] if bucket["losses"] else bool(bucket["wins"])
        return {
            "account": account,
            "current_equity_usdt": float(account.get("equity_usdt") or start_balance),
            "starting_balance_usdt": start_balance,
            "open_positions": len(app.state.store.get_open_positions()),
            "max_open_positions": int(config.get("paper", {}).get("max_open_positions", 1)),
            "trades_today": len(today_trades),
            "today_pnl_usdt": sum(float(trade["net_pnl_usdt"]) for trade in today_trades),
            "max_drawdown_pct": max_drawdown(equity),
            "pnl_by_symbol": by_symbol,
            "pnl_by_direction": by_direction,
            "pnl_by_settings_version": by_settings,
            "active_settings_version": active.get("version"),
            "active_settings_hash": (active.get("settings_hash") or "")[:12],
            "worst_10_trades": sorted(trades, key=lambda trade: float(trade["net_pnl_usdt"]))[:10],
            **stats,
        }

    @app.get("/api/impact", dependencies=[Depends(auth)])
    def api_impact() -> dict[str, Any]:
        account = app.state.store.get_paper_account() or {}
        start_balance = float(account.get("start_balance_usdt") or config.get("paper", {}).get("starting_balance_usdt", 20.0))
        return {"versions": build_settings_impact(app.state.store, start_balance)}

    @app.get("/api/open-positions", dependencies=[Depends(auth)])
    def api_open_positions() -> list[dict[str, Any]]:
        rows = app.state.store.get_open_positions()
        for row in rows:
            row["details"] = safe_json(row.pop("details_json", None), {})
        return rows

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def api_trades(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
        symbol: str | None = None,
        direction: str | None = None,
        exit_reason: str | None = None,
        scope: str = Query(default="active", pattern="^(active|all)$"),
    ) -> list[dict[str, Any]]:
        rows = enrich_trade_rows(
            app.state.store,
            app.state.store.list_trades(
                from_ms=parse_ms(from_),
                to_ms=parse_ms(to),
                symbol=symbol,
                direction=direction,
                exit_reason=exit_reason,
                limit=1000,
            ),
        )
        if scope == "all":
            return rows
        active = app.state.store.get_active_runtime_settings()
        return [row for row in rows if matches_active_settings(row, active)]

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def api_equity(from_: str | None = Query(default=None, alias="from"), to: str | None = None) -> list[dict[str, Any]]:
        return app.state.store.list_equity_snapshots(from_ms=parse_ms(from_), to_ms=parse_ms(to), limit=2000)

    @app.get("/api/signals", dependencies=[Depends(auth)])
    def api_signals(from_: str | None = Query(default=None, alias="from"), to: str | None = None, symbol: str | None = None, level: str | None = None) -> list[dict[str, Any]]:
        plans = app.state.store.list_recent_trade_plans(from_ms=parse_ms(from_), to_ms=parse_ms(to), limit=500)
        out = []
        for plan in plans:
            if symbol and plan["symbol"].upper() != symbol.upper():
                continue
            if level and plan["classifier_label"] != level:
                continue
            plan["reasons"] = safe_json(plan.pop("reasons_json", None), [])
            plan["warnings"] = safe_json(plan.pop("warnings_json", None), [])
            plan["entry_grid"] = safe_json(plan.pop("entry_grid_json", None), [])
            plan["risk"] = safe_json(plan.pop("risk_json", None), {})
            out.append(plan)
        return out

    @app.get("/api/settings", dependencies=[Depends(auth)])
    def api_settings() -> dict[str, Any]:
        active = app.state.store.get_active_runtime_settings()
        runtime_settings = RuntimeTradingSettings.model_validate(active["settings"]).model_dump(mode="json") if active else None
        return {
            "mode": "paper",
            "exchange": config.get("app", {}).get("exchange"),
            "database_path": config.get("app", {}).get("database_path"),
            "scan_interval_seconds": config.get("paper", {}).get("scan_interval_seconds"),
            "active_settings_version": active.get("version") if active else None,
            "active_settings_hash": (active.get("settings_hash") or "")[:12] if active else None,
            "paper": {k: v for k, v in config.get("paper", {}).items() if "secret" not in k.lower() and "token" not in k.lower()},
            "runtime_settings": runtime_settings,
            "web": {"auth": "token", "token_configured": bool(config.get("web", {}).get("dashboard_token"))},
        }

    @app.get("/api/settings/schema", dependencies=[Depends(auth)])
    def api_settings_schema() -> dict[str, Any]:
        return RuntimeTradingSettings.model_json_schema()

    @app.get("/api/settings/trading", dependencies=[Depends(auth)])
    def api_settings_trading() -> dict[str, Any]:
        active = app.state.store.get_active_runtime_settings()
        if not active:
            settings, _effective = active_settings_for_app(app.state.store, app.state.config)
            active = app.state.store.get_active_runtime_settings() or {"version": 1, "settings_hash": runtime_settings_hash(settings), "settings": settings.model_dump(mode="json")}
        settings = RuntimeTradingSettings.model_validate(active["settings"])
        return {
            "version": active["version"],
            "settings_hash": active["settings_hash"],
            "settings_hash_short": str(active["settings_hash"])[:12],
            "created_at_ms": active.get("created_at_ms"),
            "created_by": active.get("created_by"),
            "comment": active.get("comment"),
            "settings": settings.model_dump(mode="json"),
        }

    @app.post("/api/settings/validate", dependencies=[Depends(auth)])
    async def api_settings_validate(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            settings = normalize_settings_payload(payload, app.state.config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "settings_hash": runtime_settings_hash(settings), "settings": settings.model_dump(mode="json")}

    def apply_settings(settings: RuntimeTradingSettings, *, changed_by: str, comment: str | None) -> dict[str, Any]:
        active_before = app.state.store.get_active_runtime_settings()
        before_settings = active_before.get("settings") if active_before else {}
        settings_dict = settings.model_dump(mode="json")
        settings_hash = runtime_settings_hash(settings)
        row = app.state.store.apply_runtime_settings(
            settings_dict,
            settings_hash,
            changed_by=changed_by,
            comment=comment,
            diff=dict_diff(before_settings, settings_dict),
        )
        app.state.config.clear()
        app.state.config.update(
            apply_runtime_settings_to_config(
                load_config(config_path),
                settings,
                version=int(row["version"]),
                settings_hash=str(row["settings_hash"]),
            )
        )
        app.state.store.record_bot_event("INFO", "web", "SETTINGS_APPLIED", {"version": row["version"], "hash": row["settings_hash"]})
        app.state.store.set_bot_state(
            "pending_account_reset",
            {
                "settings_version": row["version"],
                "settings_hash": row["settings_hash"],
                "starting_balance_usdt": settings.risk.starting_balance_usdt,
                "requested_at_ms": now_ms(),
                "changed_by": changed_by,
                "comment": comment,
            },
        )
        app.state.store.record_bot_event(
            "INFO",
            "web",
            "PAPER_ACCOUNT_RESET_QUEUED",
            {"version": row["version"], "starting_balance_usdt": settings.risk.starting_balance_usdt},
        )
        return {
            "ok": True,
            "version": row["version"],
            "settings_hash": row["settings_hash"],
            "settings_hash_short": str(row["settings_hash"])[:12],
            "settings": row["settings"],
            "account_reset_queued": True,
        }

    @app.put("/api/settings/trading", dependencies=[Depends(auth)])
    async def api_settings_put(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            settings = normalize_settings_payload(payload, app.state.config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return apply_settings(settings, changed_by=str(payload.get("changed_by") or "dashboard"), comment=payload.get("comment"))

    @app.post("/api/settings/apply", dependencies=[Depends(auth)])
    async def api_settings_apply(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            settings = normalize_settings_payload(payload, app.state.config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return apply_settings(settings, changed_by=str(payload.get("changed_by") or "dashboard"), comment=payload.get("comment"))

    @app.post("/api/settings/reset-defaults", dependencies=[Depends(auth)])
    async def api_settings_reset_defaults(request: Request) -> dict[str, Any]:
        payload = await request.json() if request.headers.get("content-length") not in {None, "0"} else {}
        settings = build_runtime_settings_from_config(load_config(config_path))
        return apply_settings(settings, changed_by=str(payload.get("changed_by") or "dashboard"), comment=payload.get("comment") or "reset to YAML defaults")

    @app.get("/api/settings/history", dependencies=[Depends(auth)])
    def api_settings_history() -> dict[str, Any]:
        return {
            "versions": app.state.store.list_runtime_settings(limit=50),
            "audit": app.state.store.list_settings_audit_log(limit=100),
        }

    @app.get("/api/settings/export.yaml", dependencies=[Depends(auth)])
    def api_settings_export_yaml() -> Response:
        active = app.state.store.get_active_runtime_settings()
        if not active:
            raise HTTPException(status_code=404, detail="runtime settings not initialized")
        settings = RuntimeTradingSettings.model_validate(active["settings"])
        return Response(settings_to_yaml(settings), media_type="application/x-yaml")

    @app.post("/api/bot/pause", dependencies=[Depends(auth)])
    def api_bot_pause() -> dict[str, Any]:
        app.state.store.set_bot_state("paused", True)
        app.state.store.record_bot_event("INFO", "web", "BOT_PAUSED", {})
        return {"ok": True, "paused": True}

    @app.post("/api/bot/resume", dependencies=[Depends(auth)])
    def api_bot_resume() -> dict[str, Any]:
        app.state.store.set_bot_state("paused", False)
        app.state.store.record_bot_event("INFO", "web", "BOT_RESUMED", {})
        return {"ok": True, "paused": False}

    @app.get("/api/bot/status", dependencies=[Depends(auth)])
    def api_bot_status() -> dict[str, Any]:
        active = app.state.store.get_active_runtime_settings()
        latest_snapshot = app.state.store.latest_snapshot_timestamp_ms()
        last_tick = app.state.store.get_bot_state("last_scanner_tick_ms")
        pending_reset = app.state.store.get_bot_state("pending_account_reset")
        return {
            "paused": app.state.store.is_bot_paused(),
            "runner_online": bool(last_tick and int(datetime.now(tz=timezone.utc).timestamp() * 1000) - int(last_tick) < 120_000),
            "last_scanner_tick_ms": last_tick,
            "last_price_update_ms": latest_snapshot,
            "active_settings_version": active.get("version") if active else None,
            "active_settings_hash": active.get("settings_hash") if active else None,
            "open_positions": len(app.state.store.get_open_positions()),
            "database_path": app.state.config.get("app", {}).get("database_path"),
            "pending_account_reset": pending_reset if pending_reset else None,
            "uptime_seconds": max(0, int((int(datetime.now(tz=timezone.utc).timestamp() * 1000) - app.state.started_at_ms) / 1000)),
            "recent_errors": [
                dict(row)
                for row in app.state.store.conn.execute(
                    "SELECT * FROM bot_events WHERE level IN ('ERROR', 'WARNING') ORDER BY id DESC LIMIT 10"
                ).fetchall()
            ],
        }

    @app.post("/api/paper/close/{position_id}", dependencies=[Depends(auth)])
    def api_close_position(position_id: int) -> dict[str, Any]:
        position = app.state.store.get_position(position_id)
        if not position or position.get("status") != "OPEN":
            raise HTTPException(status_code=404, detail="open paper position not found")
        command_id = app.state.store.enqueue_paper_command("MANUAL_CLOSE", position_id, {"source": "dashboard"})
        app.state.store.record_bot_event("INFO", "web", "MANUAL_CLOSE_REQUESTED", {"position_id": position_id, "command_id": command_id})
        return {"ok": True, "command_id": command_id, "position_id": position_id, "status": "PENDING"}

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-Bot paper dashboard")
    parser.add_argument("--config", default="config.paper.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
