from pathlib import Path


def test_no_private_or_trading_api_code_exists():
    forbidden = [
        "api_key",
        "api_secret",
        "bybit_api",
        "binance_api",
        "place_order",
        "create_order",
        "/v5/order",
        "get_balance",
        "get_position",
        "private_stream",
        "stop_loss",
        "take_profit",
    ]
    app_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in Path("app").rglob("*.py"))
    for token in forbidden:
        assert token not in app_text
