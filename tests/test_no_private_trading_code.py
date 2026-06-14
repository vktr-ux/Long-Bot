from pathlib import Path


def test_no_live_signed_trading_endpoints_exist_in_app_code():
    forbidden = [
        "/fapi/v1/order",
        "/fapi/v2/account",
        "/fapi/v2/positionrisk",
        "x-mbx-apikey",
        "signature=",
        "hmac.new",
        "/v5/order",
        "/v5/position",
        "/v5/account",
        "private_stream",
    ]
    app_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in Path("app").rglob("*.py"))
    for token in forbidden:
        assert token not in app_text


def test_paper_mode_and_env_example_do_not_ship_real_secrets():
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "TRADING_MODE=paper" in env_example
    assert "BINANCE_TESTNET_API_KEY=" in env_example
    assert "BINANCE_TESTNET_API_SECRET=" in env_example
    assert "change_me" in env_example
