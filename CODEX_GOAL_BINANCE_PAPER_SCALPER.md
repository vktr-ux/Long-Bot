# GOAL FOR CODEX — Long-Bot → Binance Futures paper scalper + dashboard

Ты работаешь в существующем репозитории `Long-Bot`. Не переписывай проект с нуля. Максимально переиспользуй текущий Bybit scanner: scoring, breakout, metrics, RSI, state/cooldown, SQLite, Telegram, tests. Сейчас это public-data scanner/alert bot; нужно превратить его в Binance-first paper trading систему с точной статистикой и минималистичной веб-панелью.

## 0. Главный результат

Сделай за один проход максимально рабочий MVP:

1. Binance USD-M Futures public-data scanner вместо Bybit по умолчанию.
2. Paper trading engine со стартовым виртуальным балансом `20 USDT`.
3. Автоматический отбор LONG/SHORT возможностей, расчёт плеча, позиции, entry grid, stop loss, take profit, breakeven+, trailing stop, time stop.
4. Точная история сделок: entry/exit fills, комиссии, slippage, gross/net PnL, exit reason, MFE/MAE, длительность.
5. Веб-панель для мобильного и ПК: текущий бюджет от $20, открытые сделки, история, net PnL после комиссий, фильтры по датам/символам/направлению.
6. VPS-ready запуск через systemd, `.env`, `config.paper.yaml`, README с командами.

По умолчанию всё должно работать в `paper` режиме без реальных ордеров. Реальную торговлю не включать. Если добавляешь Binance signed client, он должен быть выключен по умолчанию и использоваться только для testnet/sandbox при явном `TRADING_MODE=binance_testnet` + ключах.

## 1. Что уже есть в архиве и что использовать

Текущий проект:

- Python 3.11+.
- `app/exchanges/bybit.py` — рабочий Bybit public connector.
- `app/exchanges/binance.py` — placeholder, его нужно реализовать.
- `app/scanner/*` — полезное ядро анализа: activity, metrics, derivatives, breakout, RSI, scoring, signals, state/cooldown.
- `app/storage/db.py` и `app/storage/models.py` — SQLite schema для symbols, market snapshots, signals, states, outcomes.
- `deploy/long-bot.service.example` — заготовка systemd.
- `tests/*` — хорошие тесты; старый `test_no_private_trading_code.py` устарел, потому что теперь нужен paper trading. Перепиши его в тест: live trading disabled by default, secrets never logged, paper mode does not call signed endpoints.

Сделай `ScanEngine` независимым от Bybit-типа: он должен принимать общий `ExchangeConnector`. Bybit можно оставить как fallback, но Binance должен стать default.

## 2. Binance connector

Реализуй `BinanceFuturesPublicConnector` в `app/exchanges/binance.py`.

Обязательные методы:

- `get_symbols()` через `GET /fapi/v1/exchangeInfo`.
  - Брать только USD-M perpetual: `quoteAsset == USDT`, `contractType == PERPETUAL`, `status == TRADING`.
  - Распарсить `PRICE_FILTER.tickSize`, `LOT_SIZE.stepSize/minQty/maxQty`, `MARKET_LOT_SIZE`, `MIN_NOTIONAL`/`NOTIONAL`, `pricePrecision`, `quantityPrecision`, `triggerProtect`.
- `get_tickers()` через комбинацию:
  - `GET /fapi/v1/ticker/24hr` без symbol для 24h price/volume/quoteVolume/trade count.
  - `GET /fapi/v1/ticker/bookTicker` без symbol для bid/ask/spread.
  - `GET /fapi/v1/premiumIndex` без symbol для mark price, funding, next funding.
- `get_klines(symbol, interval, limit)` через `GET /fapi/v1/klines`.
  - Binance возвращает oldest → newest, но всё равно явно сортируй ASC.
  - Используй close time для `is_closed`.
  - Маппинг интервалов: Bybit-style `1`, `5`, `15`, `60`, `240`, `D` → Binance `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.
- `get_open_interest_history(symbol, interval='5m', limit=50)` через `/futures/data/openInterestHist`.
- `get_taker_buy_sell_volume(symbol, period='5m', limit=30)` через `/futures/data/takerlongshortRatio`.
- `get_orderbook(symbol, limit=20)` через `/fapi/v1/depth` для depth/liquidity/slippage checks.
- retries, timeout, async semaphore, clear logging without secrets.

Добавь unit tests с fake responses для Binance:

- exchangeInfo filters parsed correctly;
- ticker + bookTicker + premiumIndex merge works;
- kline parsing and `is_closed` work;
- OI history ASC;
- spread calculation correct;
- symbol status uses `TRADING`, not Bybit `Trading`.

## 3. Scanner improvements for scalping

Сохрани текущий score/breakout logic, но добавь short-term activity layer.

Stage A every `10-15s`:

- all tickers, book tickers, premium index;
- store rolling snapshots;
- compute 1m/3m/5m/15m price delta from snapshots;
- compute quoteVolume delta / trade-count delta if possible;
- rank by quoteVolume, short-term delta, spread, trade count.

Stage B every `20-30s`, only top candidates:

- 1m/5m/15m/1h/4h klines;
- OI history;
- taker buy/sell ratio;
- depth limit 20;
- existing breakout/scoring.

Candidate filters for paper mode:

```yaml
filters:
  min_quote_volume_24h_usd: 10000000
  real_money_min_quote_volume_24h_usd: 30000000
  max_spread_pct: 0.20
  max_spread_pct_absolute_skip: 0.35
  min_5m_change_abs_pct: 0.8
  min_15m_volume_spike: 1.8
  top_activity_rank_candidate: 60
  exclude_major_symbols: true
```

Reject if:

- spread too wide;
- depth within 0.20% of mid < `position_notional * 10`;
- symbol filters make order impossible for $20 account;
- funding is extreme;
- score is weak and no short-term impulse.

## 4. Direction classifier

Добавь deterministic classifier, no LLM:

Labels:

- `LONG_CONTINUATION`
- `SHORT_FAILED_BREAKOUT`
- `SHORT_BLOWOFF_REVERSAL`
- `NO_TRADE_LATE_CHASE`
- `NO_TRADE_BAD_LIQUIDITY`
- `NO_TRADE_CONFLICT`

LONG only if most conditions are true:

- score >= 60 or level in `HOT/BREAKOUT_HOT/VERY_HOT`;
- 5m and/or 15m momentum positive;
- 15m volume/turnover spike >= 1.8;
- taker buy/sell ratio >= 1.05 if available;
- OI 15m not strongly negative, ideally positive;
- funding <= 0.0005;
- spread <= 0.20%;
- not `FAILED_BREAKOUT`;
- not 24h move already > +100% unless breakout is very fresh and chase risk is not HIGH.

SHORT only if:

- failed breakout / overextended after breakout / big upper wick / RSI hot;
- 1m-5m momentum has flipped down;
- taker sell pressure or OI falling with price;
- funding hot or long side crowded;
- no long-continuation condition remains.

Default to `NO_TRADE` when signals conflict. Low trade count is better than forced trades.

## 5. Strategy: entry grid, leverage, stop, TP, trailing

Use `confirmation_ladder`, not martingale. Never average down a losing trade.

### Position budget

Default config:

```yaml
paper:
  starting_balance_usdt: 20.0
  max_open_positions: 1
  max_position_margin_usdt: 2.0
  max_account_fraction_as_margin: 0.12
  max_leverage: 10
  default_leverage: 5
  max_loss_per_trade_usdt: 0.20
  max_daily_loss_usdt: 1.00
  max_daily_trades: 12
  max_loss_streak: 3
  symbol_cooldown_minutes: 20
  fee_rate_taker: 0.0004
  fee_rate_maker: 0.0002
  entry_slippage_bps: 3
  exit_slippage_bps: 5
  min_net_profit_after_breakeven_usdt: 0.02
  preferred_net_profit_usdt: 0.05
  time_stop_seconds: 180
  max_hold_seconds: 600
```

### Leverage selection

For paper without signed Binance access, use assumed leverage from config and mark source as `assumed`. For signed testnet/live later, query leverage brackets/symbol config and cap by exchange allowed leverage.

Leverage rule:

```text
if spread <= 0.08% and score >= 75 and initial_sl_pct <= 0.70%: allow up to 10x
elif spread <= 0.15% and score >= 65: allow up to 7x
else: allow up to 3-5x
final_leverage = min(config.max_leverage, exchange_allowed_or_assumed, risk_limited_leverage)
```

Risk sizing:

```text
cost_pct = entry_fee_pct + exit_fee_pct + entry_slippage_pct + exit_slippage_pct
max_notional_by_loss = max_loss_per_trade_usdt / (initial_sl_pct/100 + cost_pct)
desired_margin = min(max_position_margin_usdt, balance * max_account_fraction_as_margin)
notional = min(desired_margin * leverage, max_notional_by_loss)
notional = round down to Binance stepSize/minQty/minNotional
skip if notional < minNotional * 1.02
```

### Entry grid

LONG:

- leg 1: 70% notional on confirmed breakout: last closed 1m high + `0.05-0.12%` buffer, or market-paper fill if current price already breaks and spread/depth OK.
- leg 2: 30% only if trade is already favorable: either pullback holds VWAP/EMA9/previous breakout zone, or price makes continuation high. Do not place leg 2 below entry to average down.

SHORT mirrors LONG using last closed 1m low - buffer.

### Initial stop

```text
initial_sl_pct = clamp(max(0.45%, 1.5 * spread_pct, 0.35 * ATR_1m_pct), 0.45%, 1.10%)
```

Put hard stop immediately after fill in paper state. For future real/testnet orders use reduce-only/close-position protection and mark-price working type where possible.

### Breakeven+ stop

Do not move stop to profit until net math allows it.

```text
roundtrip_cost_usdt = entry_fee + estimated_exit_fee + slippage_buffer
min_profit_usdt = config.min_net_profit_after_breakeven_usdt  # default 0.02
be_plus_move_pct = (roundtrip_cost_usdt + min_profit_usdt) / notional * 100
```

LONG breakeven stop price:

```text
be_plus_price = entry_price * (1 + be_plus_move_pct / 100)
```

SHORT:

```text
be_plus_price = entry_price * (1 - be_plus_move_pct / 100)
```

Move SL to `be_plus_price` only after price has moved at least `be_plus_move_pct + 0.15%` in favor. Goal: even weak trades close with +$0.02-$0.05 net when the market actually gave enough movement, not by faking PnL.

### TP and trailing

- TP1 trigger: `max(0.45%, be_plus_move_pct + 0.20%)`; close 40-60%.
- After TP1: stop for rest = breakeven+.
- Trailing start: `max(0.75%, 1.5 * initial_sl_pct)`.
- Trailing distance: `clamp(max(0.35%, 2 * spread_pct, 0.40 * ATR_1m_pct), 0.35%, 0.85%)`.
- Exit by trailing stop, hard stop, TP1-only time stop, max hold, daily kill switch.

Exit reasons must be explicit:

`STOP_LOSS`, `BREAKEVEN_PLUS_STOP`, `TP1_PARTIAL`, `TRAILING_STOP`, `TIME_STOP`, `MAX_HOLD`, `DAILY_KILL_SWITCH`, `MANUAL_PAPER_CLOSE`, `SIGNAL_INVALIDATED`.

## 6. Paper execution engine

Add modules:

- `app/trading/classifier.py`
- `app/trading/risk.py`
- `app/trading/strategy.py`
- `app/trading/paper.py`
- `app/trading/position_manager.py`
- `app/trading/pnl.py`
- `app/trading/runner.py`

Paper engine must use real Binance public prices but simulated fills.

Fill model:

- Entry fill uses ask for long / bid for short plus configured slippage.
- Exit fill uses bid for long / ask for short minus/plus configured slippage.
- Fees applied on every fill notional.
- Net PnL = gross PnL - fees - slippage cost - funding cost.
- If simulating from candles and both stop and TP are touched in same candle, assume worst case: stop first.
- Open-position monitor must poll/stream price every 1-3s for accurate stop/trailing, independent of slower scanner interval.

Closed trade history must never use current mark price approximation. It must be computed from persisted fills.

## 7. SQLite schema

Extend schema idempotently. Keep old tables working.

Add:

- `paper_accounts(id, name, start_balance_usdt, cash_balance_usdt, equity_usdt, realized_pnl_usdt, total_fees_usdt, total_slippage_usdt, mode, created_at_ms, updated_at_ms)`
- `trade_plans(id, signal_id, exchange, symbol, direction, classifier_label, strategy_version, score, reasons_json, warnings_json, entry_grid_json, risk_json, status, created_at_ms)`
- `paper_orders(id, account_id, trade_plan_id, position_id, symbol, side, order_type, role, qty, price, trigger_price, status, created_at_ms, updated_at_ms)`
- `paper_fills(id, account_id, position_id, order_id, symbol, side, qty, price, notional_usdt, fee_usdt, slippage_usdt, liquidity_side, fill_source, filled_at_ms)`
- `paper_positions(id, account_id, trade_plan_id, symbol, direction, status, qty, entry_price, notional_usdt, margin_usdt, leverage, initial_sl_price, current_sl_price, tp1_price, trailing_active, trailing_distance_pct, high_watermark, low_watermark, unrealized_pnl_usdt, realized_pnl_usdt, fees_usdt, mfe_usdt, mae_usdt, opened_at_ms, closed_at_ms, exit_reason)`
- `paper_trades(id, account_id, position_id, symbol, direction, entry_time_ms, exit_time_ms, entry_price, exit_price, qty, notional_usdt, leverage, gross_pnl_usdt, fees_usdt, slippage_usdt, funding_usdt, net_pnl_usdt, roi_pct, mfe_usdt, mae_usdt, duration_seconds, exit_reason, strategy_version)`
- `equity_snapshots(id, account_id, timestamp_ms, cash_balance_usdt, equity_usdt, realized_pnl_usdt, unrealized_pnl_usdt, open_positions_count)`
- `bot_events(id, timestamp_ms, level, component, message, details_json)`

Add indexes for time, symbol, status.

## 8. Web dashboard

Use FastAPI + Jinja2/static vanilla JS/CSS, or another lightweight Python-first option. Avoid heavy frontend build unless already necessary.

Routes/API:

- `GET /` dashboard HTML.
- `GET /api/summary?from=&to=`
- `GET /api/open-positions`
- `GET /api/trades?from=&to=&symbol=&direction=&exit_reason=`
- `GET /api/equity?from=&to=`
- `GET /api/signals?from=&to=&symbol=&level=`
- `GET /api/settings`
- `GET /healthz`
- Optional paper-only: `POST /api/paper/close/{position_id}`.

Auth:

- Simple token/password from `DASHBOARD_TOKEN` env.
- Do not expose dashboard without auth on public VPS.
- Never show API secrets.

Minimalist UI:

Top cards:

- Balance: `current equity / 20 USDT`
- Net PnL after fees
- ROI %
- Today PnL
- Open positions
- Win rate
- Max drawdown
- Total fees

Tabs:

1. Overview: equity curve, daily PnL, win/loss, profit factor, drawdown.
2. Open: symbol, long/short, entry, mark, qty, notional, margin, leverage, current SL, TP1/trailing status, net unrealized PnL, MFE/MAE, age, score, reasons.
3. History: closed trades table with filters and CSV export.
4. Signals: scanner candidates, classifier label, reasons/warnings, rejected/no-trade reason.
5. Settings/Health: mode, exchange, scan interval, last update, service uptime, last errors.

Design:

- dark minimal;
- responsive cards for mobile;
- compact tables on desktop;
- green/red PnL but do not overuse colors;
- auto-refresh every 3-5s for open positions, 15-30s for history.

## 9. Commands and deployment

Add:

- `config.paper.yaml`
- update `.env.example` with `DASHBOARD_TOKEN`, `TRADING_MODE=paper`, optional Binance testnet keys placeholders.
- `python -m app.trading.runner --config config.paper.yaml`
- `python -m app.web.server --config config.paper.yaml --host 127.0.0.1 --port 8008`
- systemd files:
  - `deploy/long-bot-paper.service.example`
  - `deploy/long-bot-web.service.example`
- README section for VPS deployment.

If deploying on VPS:

- Work in `/opt/Long-Bot`.
- Create/update venv.
- Install requirements.
- Do not print `.env`.
- Start services only after tests pass.
- If Nginx exists, provide safe `/bot/` reverse proxy snippet; do not overwrite existing site config blindly.

## 10. Tests and acceptance

Add/adjust tests so `pytest -q` works without manual `PYTHONPATH` hacks. Add `pyproject.toml` or package setup if needed.

Required tests:

- Existing scanner tests still pass.
- Binance connector fake tests.
- Direction classifier tests.
- Risk sizing respects $20 balance, minNotional, stepSize, max loss.
- Fee/slippage PnL math exact for long and short.
- Breakeven+ stop only moves after net cost + min profit is available.
- Trailing stop state machine.
- Candle ambiguity assumes stop first.
- Closed trade history computed from fills.
- Dashboard API returns summary/open/history.
- Live/testnet signed client is never called in paper mode.

Acceptance commands:

```bash
pytest -q
python -m app.main --once --dry-run --config config.paper.yaml
python -m app.trading.runner --config config.paper.yaml --once
python -m app.web.server --config config.paper.yaml --host 127.0.0.1 --port 8008
```

Acceptance behavior:

- Binance scan produces candidates/signals or clean no-signal diagnostics.
- Paper account starts at exactly `20.00 USDT`.
- Paper position lifecycle can open, move SL to breakeven+, partially close TP1, close by trailing/time/stop.
- Dashboard shows current budget, open positions, closed trades and net PnL after commissions.
- No real Binance order is placed in default mode.

## 11. Weekly success tracking

Add dashboard/report metrics for the 7-day paper trial:

- net PnL after fees/slippage;
- equity vs starting 20 USDT;
- max drawdown;
- profit factor;
- win rate;
- average win/loss;
- stopout count;
- breakeven+ exits count;
- trailing exits count;
- PnL by symbol and direction;
- worst 10 trades with reasons.

Do not claim strategy is profitable. The goal is to collect honest data and make the paper system reliable enough for a week-long trial.
