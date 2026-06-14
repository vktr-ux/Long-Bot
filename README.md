# Long-Bot Binance Paper Scalper

Binance-first USD-M Futures public-data scanner with a paper-trading scalper, exact SQLite trade accounting, and a lightweight FastAPI dashboard. Default runtime is `paper`; it uses public market data and simulated fills only.

## What It Does

- Fetches Binance USD-M Futures public market data by default.
- Keeps Bybit public connector as a fallback/replay connector.
- Ranks symbols by 24h turnover/activity.
- Enriches only top candidates with candles and open-interest history.
- Calculates short-term momentum, volume/turnover spikes, OI/funding context, taker pressure, depth, BTC background, RSI warnings, 4H resistance/breakout state, setup quality, and score.
- Classifies deterministic LONG/SHORT/no-trade decisions.
- Simulates paper entries/exits, fees, slippage, breakeven+, trailing/time stops, MFE/MAE, and exact closed trade history from fills.
- Serves a token-protected dashboard with balance, open positions, history, signals, equity, settings-version impact, and runtime-editable trading rules.

## What It Does Not Do

- No real order placement.
- No exchange stop-loss or take-profit orders; stops/TP/trailing are simulated in paper state.
- No exchange private API keys required for default mode.
- No balance, position, or private stream access.
- No financial advice, profitability claim, or guaranteed trade outcome.

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
DASHBOARD_TOKEN=
TRADING_MODE=paper
```

Exchange API keys are not needed for paper mode because the scanner uses public market-data endpoints only.

## Run

Dry-run one scan with live Binance public data and no Telegram sends:

```bash
python -m app.main --once --dry-run --config config.paper.yaml
```

Run one paper scan/manage cycle:

```bash
python -m app.trading.runner --config config.paper.yaml --once
```

Run continuously until at least one closed paper trade is recorded:

```bash
python -m app.trading.runner --config config.paper.yaml --until-first-trade
```

Start the dashboard locally:

```bash
python -m app.web.server --config config.paper.yaml --host 127.0.0.1 --port 8008
```

## Runtime Settings

The dashboard includes a `Settings` tab for paper-mode tuning without redeploy. The active settings are stored in SQLite with a version and hash, and the runner hot-reloads the active version before each scan cycle.

Available API surfaces:

```text
GET  /api/settings/schema
GET  /api/settings/trading
POST /api/settings/validate
PUT  /api/settings/trading
POST /api/settings/apply
POST /api/settings/reset-defaults
GET  /api/settings/history
GET  /api/settings/export.yaml
POST /api/bot/pause
POST /api/bot/resume
GET  /api/bot/status
```

Paper exploration defaults:

```yaml
trading_mode: paper
risk_profile: exploration_paper
paper:
  max_open_positions: 5
  max_new_positions_per_cycle: 2
  max_position_margin_usdt: 3.0
  max_leverage: 12
  default_leverage: 8
  max_trades_per_hour: 0
  max_daily_trades: 0
  max_loss_streak: 10
  enforce_daily_loss_limit: false
strategy:
  long_min_score: 64
entry:
  require_trigger_confirmation: true
  max_entry_distance_above_trigger_pct: 0.45
```

`0` means unlimited for paper trade-count limits and disabled for `max_loss_streak`. Live/testnet settings cannot be made fully unlimited from the UI unless `ALLOW_UNSAFE_LIVE_SETTINGS=true` is set explicitly.

Each new trade plan, paper position, and closed paper trade stores `strategy_config_version` and `settings_hash`, so dashboard analytics can compare PnL by settings version. Open positions keep the settings snapshot saved at entry for exit management.

Current aggressive paper strategy code is `paper_scalper_v6`. It is trigger-gated: a valid LONG setup can be stored as `waiting_entry`, but the paper broker opens only after the calculated ladder trigger is reached. This avoids buying a signal before confirmation while still allowing active fresh-breakout scalping.

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

Send one Telegram connectivity test message:

```bash
python -m app.main --telegram-test
python -m app.main --telegram-test --config config.vps.yaml
```

Continuous scanner dry-run:

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

## VPS Deploy

Clone the repo and install dependencies:

```bash
git clone https://github.com/vktr-ux/Long-Bot.git /opt/Long-Bot
cd /opt/Long-Bot
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` locally with:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Run tests and paper checks:

```bash
pytest -q
python -m app.main --once --dry-run --config config.paper.yaml
python -m app.trading.runner --config config.paper.yaml --once
```

Install the paper runner and dashboard systemd services:

```bash
sudo cp deploy/long-bot-paper.service.example /etc/systemd/system/long-bot-paper.service
sudo cp deploy/long-bot-web.service.example /etc/systemd/system/long-bot-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now long-bot-paper long-bot-web
sudo systemctl status long-bot-paper
sudo systemctl status long-bot-web
```

Safe nginx location example for the shared `8443` site:

```nginx
location /bot/ {
    proxy_pass http://127.0.0.1:8008/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Operational commands:

```bash
cd /opt/Long-Bot
git pull
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
sudo systemctl restart long-bot-paper long-bot-web
sudo systemctl status long-bot-paper
sudo systemctl status long-bot-web
sudo journalctl -u long-bot-paper -f
sudo journalctl -u long-bot-web -f
```

Day-to-day strategy tuning should use the dashboard Settings tab: change values, click `Validate`, then `Apply`. A service restart is only needed for code/dependency changes or systemd/nginx changes.

## Outcome Tracking

The SQLite schema includes `signal_outcomes`, and `app.scanner.outcomes` can compute MFE/MAE and threshold hit ordering. Full scheduled outcome aggregation/reporting is a Phase 4.5 TODO.

## Paper Trading Roadmap

The next research direction is documented in [PAPER_TRADING_ROADMAP.md](PAPER_TRADING_ROADMAP.md).

Short version:

- First build a local paper-trading simulator that records what would have happened without exchange private API keys or real orders.
- Add a deterministic direction classifier for `LONG_CONTINUATION`, `SHORT_FAILED_BREAKOUT`, `LATE_CHASE`, and `NO_TRADE_VOLATILITY`.
- Track virtual balance, PnL, MFE/MAE, drawdown, and weekly Excel reports.
- Only after useful paper-trading results, consider a separate Bybit Demo Trading module, disabled by default and using demo-only keys from local `.env`.
