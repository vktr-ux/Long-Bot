import asyncio

from app.main import run_telegram_test


def test_telegram_test_without_env_vars_fails_clearly(capsys):
    config = {"telegram": {"bot_token": "", "chat_id": "", "parse_mode": "HTML"}}
    exit_code = asyncio.run(run_telegram_test(config))
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "Telegram test failed" in output
    assert "TELEGRAM_BOT_TOKEN" in output
    assert "TELEGRAM_CHAT_ID" in output
