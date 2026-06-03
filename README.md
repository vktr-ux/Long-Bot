# Crypto Long Momentum Scanner

Local-first Bybit public-data scanner for unusual crypto futures activity and 4H breakout context. It is a radar for manual review, not an auto-trading bot.

## What It Does

- Fetches Bybit USDT linear perpetual public market data.
- Ranks symbols by 24h turnover/activity.
- Enriches only top candidates with candles and open-interest history.
- Calculates price momentum, volume/turnover spikes, OI/funding context, BTC background, RSI warnings, 4H resistance/breakout state, setup quality, and score.
- Sends or prints alerts that say `open chart / check setup`.
- Saves symbols, market snapshots, states, signals, and outcome-tracking schema in SQLite.

## What It Does Not Do

- No order placement.
- No auto-buy, auto-sell, stop-loss, or take-profit orders.
- No exchange private API keys.
- No balance, position, or private stream access.
- No financial advice or guaranteed trade claims.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with your Telegram bot token and chat id when you want live Telegram sends:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Exchange API keys are not needed because the scanner uses public market-data endpoints only.

## Run

Dry-run one scan with live Bybit data and no Telegram sends:

```bash
python -m app.main --once --dry-run
```

Diagnostic one-shot scan:

```bash
python -m app.main --once --dry-run --diagnostic --top 30
```

Explain one symbol:

```bash
python -m app.main --once --dry-run --explain OPNUSDT
python -m app.main --once --dry-run --explain ENAUSDT
```

Run Bybit parser/data sanity checks:

```bash
python -m app.main --sanity-check --symbols BTCUSDT,ETHUSDT,OPNUSDT
```

Continuous dry-run:

```bash
python -m app.main --dry-run
```

Use a custom config:

```bash
python -m app.main --config config.yaml
```

Use a threshold profile:

```bash
python -m app.main --once --dry-run --diagnostic --profile aggressive
```

`normal` is the default. `aggressive` is intended for diagnostics and calibration visibility, not production Telegram alerts.

Run the historical replay casebook:

```bash
python -m app.main --replay-cases --profile normal
```

Run calibration against a casebook:

```bash
python -m app.main --calibrate --cases config/replay_cases.yaml
```

Compare profiles:

```bash
python -m app.main --once --dry-run --diagnostic --top 50 --profile normal
python -m app.main --once --dry-run --diagnostic --top 50 --profile aggressive
```

Live Telegram mode:

```bash
python -m app.main
```

## Signal Levels

- `WATCH`: activity or momentum worth opening a chart.
- `HOT`: multiple layers confirm activity/momentum.
- `BREAKOUT_HOT`: activity, momentum, derivatives/chart confirmation with 4H breakout context.
- `VERY_HOT`: high score, breakout state, no fatal risk warning.

Alerts include a separate grade:

- `A`: strong activity + derivatives + breakout + acceptable risk.
- `B`: strong momentum/activity but incomplete context.
- `C`: interesting but weaker or riskier review setup.

## Scoring

The score combines:

- Activity: turnover rank, volume spike, turnover spike.
- Momentum: 5m, 15m, 1h, and 4h price changes.
- Derivatives: OI change and funding.
- Chart: 4H resistance, breakout state, touches, volume confirmation.
- Setup quality: invalidation reference, target reference, room-to-run, approximate R/R, chase risk.
- Risk penalties: hot funding, hostile BTC background, wide spread, overextension, RSI danger, failed breakout.

Higher levels require multiple independent confirmations. A single price pump should not become `HOT`.

The normal profile also has a configurable breakout upgrade rule. It can upgrade a strong fresh or confirmed 4H breakout from near-WATCH to `WATCH` when volume, momentum, funding, OI, room-to-run, R/R, and chase-risk checks are acceptable. The numeric score is preserved, and the alert/replay output adds explicit reasons and warnings for the upgrade.

## Breakout Detection

The scanner loads sorted 4H candles, detects swing highs, clusters them into resistance zones, and labels states such as `APPROACHING_RESISTANCE`, `TESTING_RESISTANCE`, `FRESH_BREAKOUT`, `CONFIRMED_BREAKOUT`, `FAILED_BREAKOUT`, and `OVEREXTENDED_AFTER_BREAKOUT`.

Setup quality is analytical context only. It shows reference zones and invalidation references for manual chart review; it never tells the user where to buy.

## Cooldown

The state machine suppresses duplicate alerts for the same symbol/state. It allows immediate alerts on state transitions and earlier repeats when score increases materially.

## Diagnostics

Every scan prints score visibility:

- highest score candidate
- top 10 candidate scores
- level counts
- strongest rejected candidates and reasons

Diagnostic mode also prints:

- top symbols by activity rank
- top symbols by 15m and 1h price change
- top symbols by 15m volume spike
- top symbols by OI change
- filter-stage counts
- score distribution

Each scan exports candidate diagnostics to:

```text
data/diagnostics/latest_candidates.jsonl
```

Each JSONL row includes timestamp, exchange, symbol, score, level, metrics, reasons, warnings, filter stage, and rejection reason.

## Replay

Replay simulates scanner decisions candle-by-candle using public Bybit historical market data:

```bash
python -m app.main --replay --symbol OPNUSDT --exchange bybit --start "2026-06-01 00:00" --end "2026-06-04 03:00" --profile normal
```

Replay casebooks are YAML files under `config/`, starting with:

```bash
python -m app.main --replay-cases --profile normal
python -m app.main --calibrate --cases config/replay_cases.yaml --profile normal
```

Replay output includes first signal time/level/price, breakout zone, score, reasons, warnings, max favorable price after signal, max drawdown, target reference hit, and invalidation reference hit.

Historical OI/funding are used only when public Bybit endpoints return them for the replay window. If they are unavailable, replay falls back to price, volume, and breakout layers, marks OI/funding missing, and does not fake derivatives confirmation.

## If Bybit Is Blocked

If `python -m app.main --once --dry-run` fails due to network or Bybit access, tests still validate the parser and scanner logic with mocked responses. Use a VPS or network where `https://api.bybit.com` is reachable.

## Outcome Tracking

The SQLite schema includes `signal_outcomes`, and `app.scanner.outcomes` can compute MFE/MAE and threshold hit ordering. Full scheduled outcome aggregation/reporting is a Phase 4.5 TODO.
