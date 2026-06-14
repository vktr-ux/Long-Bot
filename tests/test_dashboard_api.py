import copy
from pathlib import Path

from fastapi.testclient import TestClient

from app.runtime_settings import runtime_settings_hash
from app.web.server import create_app


def test_dashboard_api_summary_open_history_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-token")
    db_path = tmp_path / "paper.sqlite3"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  exchange: binance
  database_path: "{db_path.as_posix()}"
  log_level: INFO
  profile: normal
paper:
  starting_balance_usdt: 20
web:
  dashboard_token_env: DASHBOARD_TOKEN
""",
        encoding="utf-8",
    )
    app = create_app(str(config_path))
    store = app.state.store
    account_id = store.ensure_paper_account(starting_balance_usdt=20)
    active = store.get_active_runtime_settings()
    store.insert_paper_position(
        {
            "account_id": account_id,
            "trade_plan_id": None,
            "symbol": "AAAUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "qty": 1,
            "entry_price": 100,
            "notional_usdt": 100,
            "margin_usdt": 20,
            "leverage": 5,
            "initial_sl_price": 99,
            "current_sl_price": 100.1,
            "tp1_price": 101,
            "trailing_active": 0,
            "trailing_distance_pct": 0.4,
            "high_watermark": 100,
            "low_watermark": 100,
            "unrealized_pnl_usdt": 0.1,
            "realized_pnl_usdt": 0,
            "fees_usdt": 0,
            "mfe_usdt": 0.1,
            "mae_usdt": 0,
            "opened_at_ms": 1,
            "closed_at_ms": None,
            "exit_reason": None,
            "details_json": "{}",
        }
    )
    store.insert_paper_trade(
        {
            "account_id": account_id,
            "position_id": 1,
            "symbol": "AAAUSDT",
            "direction": "LONG",
            "entry_time_ms": 1,
            "exit_time_ms": 2,
            "entry_price": 100,
            "exit_price": 101,
            "qty": 1,
            "notional_usdt": 100,
            "leverage": 5,
            "gross_pnl_usdt": 1,
            "fees_usdt": 0.08,
            "slippage_usdt": 0.02,
            "funding_usdt": 0,
            "net_pnl_usdt": 0.9,
            "roi_pct": 4.5,
            "mfe_usdt": 1,
            "mae_usdt": -0.1,
            "duration_seconds": 1,
            "exit_reason": "TRAILING_STOP",
            "strategy_version": "paper_scalper_v1",
            "strategy_config_version": active["version"],
            "settings_hash": active["settings_hash"],
        }
    )
    client = TestClient(app)
    assert client.get("/api/summary").status_code == 401
    headers = {"X-Dashboard-Token": "test-token"}
    assert client.get("/healthz").json()["ok"] is True
    summary = client.get("/api/summary", headers=headers).json()
    assert summary["starting_balance_usdt"] == 20
    assert summary["net_pnl_usdt"] == 0.9
    assert summary["open_positions"] == 1
    assert client.get("/api/open-positions", headers=headers).json()[0]["symbol"] == "AAAUSDT"
    manual_close = client.post("/api/paper/close/1", headers=headers).json()
    assert manual_close["status"] == "PENDING"
    assert store.list_pending_paper_commands("MANUAL_CLOSE")[0]["position_id"] == 1
    assert client.get("/api/trades", headers=headers).json()[0]["exit_reason"] == "TRAILING_STOP"
    settings = client.get("/api/settings", headers=headers).json()
    assert settings["mode"] == "paper"
    assert settings["active_settings_version"] == 1
    summary = client.get("/api/summary", headers=headers).json()
    assert summary["active_settings_version"] == 1
    assert summary["pnl_by_settings_version"]["1"]["trades"] == 1
    impact = client.get("/api/impact", headers=headers).json()
    assert impact["versions"][0]["version"] == "1"
    assert impact["versions"][0]["stats"]["trades"] == 1
    assert impact["versions"][0]["trades"][0]["symbol"] == "AAAUSDT"
    store.close()


def test_dashboard_trade_history_defaults_to_active_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-token")
    db_path = tmp_path / "paper.sqlite3"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  exchange: binance
  database_path: "{db_path.as_posix()}"
  log_level: INFO
  profile: normal
paper:
  starting_balance_usdt: 20
web:
  dashboard_token_env: DASHBOARD_TOKEN
""",
        encoding="utf-8",
    )
    app = create_app(str(config_path))
    store = app.state.store
    account_id = store.ensure_paper_account(starting_balance_usdt=20)
    version_1 = store.get_active_runtime_settings()
    settings_v2 = copy.deepcopy(version_1["settings"])
    settings_v2["risk"]["max_open_positions"] = 2
    version_2 = store.apply_runtime_settings(settings_v2, runtime_settings_hash(settings_v2), comment="v2")

    def insert_trade(symbol: str, net: float, version: dict, exit_time_ms: int) -> None:
        store.insert_paper_trade(
            {
                "account_id": account_id,
                "position_id": exit_time_ms,
                "symbol": symbol,
                "direction": "LONG",
                "entry_time_ms": exit_time_ms - 1,
                "exit_time_ms": exit_time_ms,
                "entry_price": 100,
                "exit_price": 101,
                "qty": 1,
                "notional_usdt": 100,
                "leverage": 5,
                "gross_pnl_usdt": net,
                "fees_usdt": 0,
                "slippage_usdt": 0,
                "funding_usdt": 0,
                "net_pnl_usdt": net,
                "roi_pct": net,
                "mfe_usdt": net,
                "mae_usdt": 0,
                "duration_seconds": 1,
                "exit_reason": "TAKE_PROFIT",
                "strategy_version": "paper_scalper_v1",
                "strategy_config_version": version["version"],
                "settings_hash": version["settings_hash"],
            }
        )

    insert_trade("OLDUSDT", -0.1, version_1, 10)
    insert_trade("NEWUSDT", 0.2, version_2, 20)

    client = TestClient(app)
    headers = {"X-Dashboard-Token": "test-token"}
    active_rows = client.get("/api/trades", headers=headers).json()
    assert [row["symbol"] for row in active_rows] == ["NEWUSDT"]
    all_rows = client.get("/api/trades?scope=all", headers=headers).json()
    assert {row["symbol"] for row in all_rows} == {"OLDUSDT", "NEWUSDT"}
    summary = client.get("/api/summary", headers=headers).json()
    assert summary["trades"] == 1
    assert summary["net_pnl_usdt"] == 0.2
    impact = client.get("/api/impact", headers=headers).json()
    versions = {row["version"]: row for row in impact["versions"]}
    assert versions["1"]["stats"]["trades"] == 1
    assert versions["2"]["stats"]["trades"] == 1
    store.close()


def test_dashboard_runtime_settings_write_endpoints_require_auth_and_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-token")
    db_path = tmp_path / "paper.sqlite3"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  exchange: binance
  database_path: "{db_path.as_posix()}"
  log_level: INFO
  profile: normal
paper:
  starting_balance_usdt: 20
web:
  dashboard_token_env: DASHBOARD_TOKEN
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(str(config_path)))
    assert client.post("/api/settings/apply", json={}).status_code == 401
    headers = {"X-Dashboard-Token": "test-token"}
    current = client.get("/api/settings/trading", headers=headers).json()
    settings = current["settings"]
    settings["risk"]["max_open_positions"] = 4
    assert client.post("/api/settings/validate", headers=headers, json={"settings": settings}).json()["ok"] is True
    applied = client.post("/api/settings/apply", headers=headers, json={"settings": settings, "comment": "test"}).json()
    assert applied["version"] == 2
    assert applied["settings"]["risk"]["max_open_positions"] == 4
    assert applied["account_reset_queued"] is True
    assert client.get("/api/settings/history", headers=headers).json()["versions"][0]["version"] == 2
    assert client.app.state.store.get_bot_state("pending_account_reset")["settings_version"] == 2
