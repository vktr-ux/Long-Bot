# Paper Trading Roadmap

This project is still a scanner/alert bot first. Do not add real exchange trading,
live private API keys, balance access, position access, or real order placement as
part of this roadmap.

## Current Idea

The scanner appears to detect high-energy market imbalance better than it detects
direction. Some signals behave like long continuation setups, while others behave
like failed-breakout or panic-continuation short setups.

Before any demo exchange integration, test this locally with a paper-trading
engine that only records what would have happened.

## Phase 1: Local Paper Trader

Build a local paper-trading module with no private exchange API access.

Inputs:
- Existing scanner signals from SQLite.
- Future live scanner signals.
- Public Bybit candle data for outcome tracking.
- A deterministic direction classifier.

Virtual account:
- Example starting balance: 1000 USDT.
- Configurable risk per trade, default 0.5-1.0%.
- Configurable max open positions, default 2-3.
- Configurable max trades per hour/day.
- One symbol cooldown to avoid repeated late entries.

Execution model:
- Enter on the next fully closed 1m candle after a signal.
- Apply configurable fee and slippage assumptions.
- Do not place real orders.
- Record simulated entries/exits in SQLite.

Exit model candidates:
- Fixed time exits: 3m, 5m, 15m, 30m, 60m.
- Optional trailing/time hybrid after enough data.
- No real stop-loss or take-profit orders in this phase; only simulated exit rules.

Reports:
- Per-trade PnL.
- Per-strategy PnL.
- Win rate.
- Average win/loss.
- MFE/MAE.
- Max drawdown.
- Equity curve.
- Weekly Excel report.

## Direction Classifier

Start rule-based, not LLM-based. It must be deterministic and easy to backtest.

Suggested labels:
- `LONG_CONTINUATION`
- `SHORT_FAILED_BREAKOUT`
- `LATE_CHASE`
- `NO_TRADE_VOLATILITY`

Long continuation candidate:
- Fresh or confirmed breakout.
- 15m momentum positive.
- 1h momentum positive.
- 4h momentum not hostile.
- Volume spike present.
- OI rising or not strongly falling.
- Funding not overheated.
- Not a late repeated signal after a large move.

Short failed-breakout candidate:
- High score or strong activity/volume/OI.
- 15m momentum strongly negative or 1h momentum strongly negative.
- Breakout state or volume spike exists, but price is failing.
- RSI can be low; low RSI alone should not block panic-continuation shorts.
- Mark as short-watch only in paper trading until enough statistics exist.

Late chase / no trade:
- Repeated signals on the same symbol after a large MFE.
- Strong move already happened before the current signal.
- Conflicting momentum across timeframes.
- Wide spread, hostile liquidity, or unclear breakout state.

## Strategies To Test Separately

1. `LONG_ONLY`
   - Trades only `LONG_CONTINUATION`.

2. `SHORT_ONLY`
   - Trades only `SHORT_FAILED_BREAKOUT`.

3. `LONG_AND_SHORT`
   - Trades both, with strict position and cooldown limits.

Each strategy should have its own paper equity curve and performance summary.

## Phase 2: Bybit Demo Trading

Only consider Bybit Demo Trading after the local paper trader shows useful
results over a meaningful sample.

Requirements before demo integration:
- At least 200-500 classified paper signals, or one full week of stable live
  paper tracking.
- Positive expectancy after fees/slippage.
- Acceptable max drawdown.
- Clear difference between long, short, and no-trade labels.
- Manual review of worst losses.

Bybit demo guardrails:
- Use Bybit Demo Trading only, never live trading.
- Demo API keys must be created by the user in Bybit Demo Trading.
- Store demo keys only in a local `.env`, never in git.
- Demo module must be disabled by default.
- The production scanner remains alert-only unless the user explicitly starts a
  demo/paper command.

## Important Guardrails

- Do not add live Bybit/Binance private keys.
- Do not add live order placement.
- Do not add live balance or position access.
- Do not turn Telegram alerts into buy/sell instructions.
- Keep Telegram phrasing as analysis/review language.
- Prefer paper-trading evidence before any exchange demo execution.
