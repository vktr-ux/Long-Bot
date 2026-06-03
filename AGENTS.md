# AGENTS.md

## Project rules

- Main specification: ./crypto_long_scanner_GOAL.md
- This project is a crypto long momentum scanner, not an auto-trading bot.
- Use public market data only.
- Do not implement order placement.
- Do not add Binance/Bybit private API keys.
- Do not access balances, positions, or private user streams.
- Do not read or use ../SECRETS_PRIVATE.md.
- Do not hardcode secrets.
- Alerts must say “open chart / check setup”, never “buy now”.
- Start with Bybit REST MVP.
- Binance is optional Phase 2 and must not block MVP.