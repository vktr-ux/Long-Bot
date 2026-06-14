# MASTER GOAL FOR CODEX — Long-Bot V2: Binance paper scalper + editable trading rules UI

Ты работаешь в существующем репозитории `Long-Bot`. Не переписывай проект с нуля. Сначала изучи текущий код, текущую структуру и уже сделанные изменения. Максимально переиспользуй существующие модули scanner/scoring/storage/dashboard/trading, но доведи систему до рабочего paper-trading MVP под Binance USD-M Futures.

## 0. Контекст и проблема текущего результата

Уже есть первый paper-прогон. Пример истории:

- 12 сделок, все `SHORT`.
- 3 прибыльные, 9 убыточных.
- Примерный результат: около `-$0.50` от стартовых `20 USDT`.
- Много быстрых стопов за `2-9s`.
- `BREAKEVEN_PLUS_STOP` работает, но бот слишком часто шортит памп и переходит в повторные входы.
- Колонка `Fees` в UI, похоже, показывает не total roundtrip fees, а одну сторону комиссии, хотя net PnL считает две стороны. Исправить отображение.

Главный вывод: paper engine уже полезен, но нужно улучшить стратегию, добавить точную аналитику и дать возможность менять торговые правила прямо из веб-интерфейса без ручного редактирования YAML на сервере.

## 1. Главный результат за один проход

Сделай V2 системы:

1. Binance USD-M Futures paper scalper на реальных public market data.
2. Paper account со стартовым балансом `20 USDT`.
3. Одновременно до `5` открытых paper-позиций на время тестов.
4. На время тестов не ограничивать количество сделок в день/час по умолчанию.
5. Лимит consecutive losses сделать настраиваемым; default для paper: `10`, но UI должен позволять отключить лимит значением `0`.
6. Все risk/trade/scanner/entry/exit/SL/TP/trailing правила должны редактироваться из веб-панели.
7. Настройки должны сохраняться, валидироваться, версионироваться и применяться без полного redeploy, через hot reload worker-а.
8. Каждая сделка должна хранить `strategy_config_version` / `settings_hash`, чтобы потом понимать, при каких правилах она была открыта.
9. Dashboard должен показывать текущий бюджет, открытые сделки, историю, PnL после комиссий, MFE/MAE, причину входа/выхода, active settings и результаты по версиям стратегии.
10. Live trading не включать по умолчанию. Все изменения по снятию лимитов относятся только к `TRADING_MODE=paper`.

Важно: цель не обещать прибыль, а получить честную, детальную, быстро настраиваемую paper-систему для недельного теста.

## 2. Режимы риска: paper exploration vs live safety

Добавь явное разделение режимов:

```yaml
trading_mode: paper
risk_profile: exploration_paper
```

Default для `exploration_paper`:

```yaml
paper:
  starting_balance_usdt: 20.0
  max_open_positions: 5
  max_position_margin_usdt: 2.0
  max_account_fraction_as_margin: 0.12
  max_leverage: 10
  default_leverage: 5

limits:
  max_trades_per_hour: 0          # 0 = unlimited in paper
  max_daily_trades: 0             # 0 = unlimited in paper
  max_loss_streak: 10             # editable; 0 = disabled
  enforce_daily_loss_limit: false # in paper default false, only warning
  max_daily_loss_usdt: 1.00       # warning threshold if enforce=false
  symbol_cooldown_minutes: 0      # editable; default no cooldown for data collection
  direction_cooldown_minutes: 0   # editable

fees:
  fee_rate_taker: 0.0004
  fee_rate_maker: 0.0002

slippage:
  entry_slippage_bps: 3
  exit_slippage_bps: 5
```

Но добавь отдельный `live_safety` profile, даже если live пока не используется:

```yaml
live_safety:
  max_open_positions: 1
  max_trades_per_hour: 6
  max_daily_trades: 12
  max_loss_streak: 3
  enforce_daily_loss_limit: true
  max_daily_loss_usdt: 0.40
  symbol_cooldown_minutes: 20
```

В `paper` режиме разреши отключать ограничения, чтобы собрать статистику. В `live/testnet` режиме не позволяй через UI случайно поставить совсем безлимитные значения без явного `ALLOW_UNSAFE_LIVE_SETTINGS=true`.

## 3. Strategy V2: исправить перекос в SHORT

Сейчас бот слишком часто шортит активные пампы. Нужно сделать направление более строгим.

Добавь настройки:

```yaml
strategy:
  direction_mode: both              # both | long_only | short_only | auto
  long_enabled: true
  short_enabled: true
  long_min_score: 68
  short_min_score: 88
  short_strict_mode: true
  avoid_late_chase: true
  avoid_shorting_strong_momentum: true
```

### LONG_CONTINUATION

LONG должен быть основной momentum-стратегией на щитках.

Разрешать LONG, если большинство условий true:

- short-term momentum positive на 1m/3m/5m;
- 5m или 15m volume spike выше порога;
- spread нормальный;
- depth достаточный;
- taker buy pressure >= threshold, если доступен;
- OI не противоречит движению;
- цена не в конце гигантской свечи без отката;
- signal score >= `long_min_score`.

### SHORT_FAILED_BREAKOUT / SHORT_BLOWOFF_REVERSAL

SHORT разрешать только при сильном подтверждении, если `short_strict_mode=true`:

- failed breakout / lower high / rejection wick;
- 1m-5m momentum уже развернулся вниз;
- price потеряла micro-support;
- taker sell pressure появился или buy pressure исчез;
- OI/funding показывают перегрев или разгрузку;
- нет активного LONG_CONTINUATION setup;
- score >= `short_min_score`.

Если признаки конфликтуют — возвращай `NO_TRADE_CONFLICT`, а не forced SHORT.

Добавь в dashboard/Signals явную причину:

- почему вошёл;
- почему пропустил;
- почему выбран LONG/SHORT;
- какие условия true/false.

## 4. Entry grid: не мартингейл, а confirmation ladder

Не делать сетку “усреднения против себя”. Делать micro-grid/ladder по подтверждению.

Настройки должны редактироваться из UI:

```yaml
entry:
  mode: confirmation_ladder       # confirmation_ladder | single_market | pullback_limit
  legs_enabled: true
  leg_weights: [0.70, 0.30]
  max_legs: 2
  allow_average_down: false
  market_entry_allowed: true
  use_limit_ioc_for_paper_model: true
  breakout_buffer_pct_min: 0.05
  breakout_buffer_pct_max: 0.12
  pullback_confirm_pct: 0.15
  chase_max_distance_pct: 0.60
```

LONG:

- leg 1: вход на подтверждённом breakout / marketable limit paper fill;
- leg 2: только если сделка уже подтверждается: continuation high или pullback удержал зону;
- не добавлять leg 2 просто потому что цена пошла против entry.

SHORT зеркально, но только после confirmed failed breakout.

## 5. Stop loss, breakeven+, TP1, trailing

Все параметры редактируемые через UI.

```yaml
exit:
  initial_sl_pct_min: 0.45
  initial_sl_pct_max: 1.10
  initial_sl_spread_multiplier: 1.5
  initial_sl_atr_multiplier: 0.35

  breakeven_plus_enabled: true
  breakeven_plus_trigger_extra_pct: 0.15
  min_net_profit_after_breakeven_usdt: 0.02
  preferred_net_profit_after_breakeven_usdt: 0.05

  tp1_enabled: true
  tp1_trigger_pct_min: 0.60
  tp1_trigger_pct_max: 1.20
  tp1_close_fraction: 0.50

  trailing_enabled: true
  trailing_start_pct_min: 0.75
  trailing_distance_pct_min: 0.35
  trailing_distance_pct_max: 0.85
  trailing_spread_multiplier: 2.0
  trailing_atr_multiplier: 0.40

  time_stop_seconds: 180
  max_hold_seconds: 600
```

Формула breakeven+ должна быть честной по net PnL.

LONG:

```text
roundtrip_cost_usdt = entry_fee + estimated_exit_fee + slippage_buffer
min_profit_usdt = settings.exit.min_net_profit_after_breakeven_usdt
be_plus_move_pct = (roundtrip_cost_usdt + min_profit_usdt) / notional_usdt * 100
be_plus_price = entry_price * (1 + be_plus_move_pct / 100)
```

SHORT:

```text
be_plus_price = entry_price * (1 - be_plus_move_pct / 100)
```

Двигать SL в breakeven+ только если цена реально дала движение, достаточное для roundtrip cost + desired net profit. Не рисовать прибыль, если рынок её не дал.

Exit reasons разделить точнее:

- `STOP_LOSS`
- `BREAKEVEN_PLUS_STOP`
- `PROFIT_LOCK_STOP`
- `TP1_PARTIAL`
- `TRAILING_STOP`
- `TIME_STOP`
- `MAX_HOLD`
- `SIGNAL_INVALIDATED`
- `MANUAL_PAPER_CLOSE`
- `RISK_LIMIT_STOP`

Если сделка закрылась в заметный плюс по подтянутому стопу, это `PROFIT_LOCK_STOP`, а не always `BREAKEVEN_PLUS_STOP`.

## 6. Fees/PnL: исправить отображение и расчёты

PnL должен считаться только из persisted fills, не из приблизительной текущей цены.

В UI History добавить колонки:

- `Gross PnL`
- `Entry Fee`
- `Exit Fee`
- `Total Fees`
- `Slippage`
- `Net PnL`
- `ROI %`
- `MFE`
- `MAE`
- `Exit Reason`
- `Strategy Version`

Если сейчас `Fees` показывает только одну сторону комиссии, исправить:

- либо показывать `Total Fees`,
- либо явно показывать `Entry Fee` и `Exit Fee`.

Формулы:

LONG:

```text
gross = (exit_price - avg_entry_price) * qty
```

SHORT:

```text
gross = (avg_entry_price - exit_price) * qty
```

```text
net = gross - entry_fee - exit_fee - slippage_usdt - funding_usdt
```

Для открытых позиций показывать `estimated_unrealized_net_pnl`, но для закрытых — только итог по fills.

## 7. Runtime editable settings in web dashboard

Это ключевая задача V2.

Сделай возможность менять торговые правила из веб-интерфейса.

### 7.1 Backend models

Добавь Pydantic-модели или dataclasses с валидацией:

- `ScannerSettings`
- `FilterSettings`
- `StrategySettings`
- `RiskSettings`
- `EntrySettings`
- `ExitSettings`
- `FeesSettings`
- `SlippageSettings`
- `DashboardSettings`
- общий `RuntimeTradingSettings`

Добавь table:

```sql
runtime_settings(
  id INTEGER PRIMARY KEY,
  version INTEGER NOT NULL,
  settings_json TEXT NOT NULL,
  settings_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  created_by TEXT,
  comment TEXT
)
```

Добавь audit table:

```sql
settings_audit_log(
  id INTEGER PRIMARY KEY,
  timestamp_ms INTEGER NOT NULL,
  previous_version INTEGER,
  new_version INTEGER NOT NULL,
  changed_by TEXT,
  comment TEXT,
  diff_json TEXT NOT NULL
)
```

При старте:

1. загрузить YAML defaults;
2. если в DB есть active runtime settings — они override YAML;
3. если нет — создать initial version `1` из YAML.

### 7.2 API endpoints

Добавь:

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

Требования:

- Все write endpoints только под dashboard auth token.
- Не принимать/не показывать API keys и secrets.
- На `PUT /api/settings/trading` валидировать диапазоны.
- На `apply` увеличивать version, сохранять settings_hash, писать audit log.
- Возвращать понятные ошибки валидации.

### 7.3 Hot reload

Worker/trading runner должен каждые `2-5s` проверять active settings version.

Если version изменилась:

- загрузить новую конфигурацию;
- использовать её для новых signals/trade plans;
- не ломать уже открытые позиции;
- открытые позиции должны управляться по правилам, которые были сохранены в их `trade_plan` при входе;
- опционально можно разрешить `apply_to_open_positions`, но default `false`.

Добавь bot event:

```text
SETTINGS_RELOADED version=X hash=Y
```

### 7.4 UI settings page

Добавь вкладку `Settings` с минималистичным редактором.

Разделы:

1. **Mode**
   - trading_mode read-only/current;
   - risk_profile;
   - pause/resume bot.

2. **Scanner filters**
   - min quote volume 24h;
   - max spread %;
   - min 5m change %;
   - min volume spike;
   - top activity rank;
   - exclude majors.

3. **Direction**
   - direction_mode: both/long_only/short_only/auto;
   - long enabled;
   - short enabled;
   - long_min_score;
   - short_min_score;
   - short_strict_mode;
   - avoid_shorting_strong_momentum.

4. **Risk**
   - starting balance read-only once account created;
   - max_open_positions, default `5`;
   - max position margin;
   - max leverage;
   - default leverage;
   - max loss per trade;
   - max trades per hour, `0 = unlimited`;
   - max daily trades, `0 = unlimited`;
   - max loss streak, `0 = disabled`, default `10`;
   - daily loss enforcement toggle.

5. **Entry**
   - entry mode;
   - market entry allowed;
   - max legs;
   - leg weights;
   - breakout buffer;
   - pullback confirm;
   - chase max distance;
   - allow average down must default false and show warning if enabled.

6. **Exit / SL / TP / Trailing**
   - initial SL min/max;
   - breakeven+ enabled;
   - minimum net profit after breakeven;
   - TP1 trigger and close fraction;
   - trailing enabled;
   - trailing start;
   - trailing distance min/max;
   - time stop;
   - max hold.

7. **Fees/slippage**
   - maker/taker fees;
   - entry/exit slippage bps.

UI behavior:

- `Validate` button: checks settings without applying.
- `Apply` button: creates new version and hot reloads.
- `Reset to defaults` button.
- `Export YAML` button.
- Show current active version/hash and last applied timestamp.
- Show validation errors inline.
- Add a comment field: “why changed”.
- Display warning: “These settings affect new paper trades only by default.”

Do not implement arbitrary shell command execution from the web UI. If service restart is needed, document systemd commands in README. Optional service-control endpoint only if disabled by default with `ALLOW_WEB_SERVICE_CONTROL=false` and only supports allowlisted actions.

## 8. Dashboard analytics additions

Update dashboard to support real strategy tuning.

### Overview cards

- Start balance: `20.00 USDT`
- Current equity
- Net PnL after fees
- ROI %
- Today PnL
- Open positions count / max open positions
- Trades today
- Win rate
- Profit factor
- Max drawdown
- Total fees
- Current settings version

### Open positions table

Columns:

- Time opened
- Symbol
- Side
- Entry
- Mark/last price
- Qty
- Notional
- Margin
- Leverage
- Unrealized gross PnL
- Estimated fees if closed now
- Estimated net PnL
- Initial SL
- Current SL
- BE+ price
- TP1
- Trailing active
- MFE
- MAE
- Age
- Strategy version
- Entry reasons
- Manual close button, paper only

### History table

Columns:

- Time
- Symbol
- Side
- Entry
- Exit
- Qty
- Gross PnL
- Entry Fee
- Exit Fee
- Total Fees
- Slippage
- Net PnL
- ROI %
- Reason
- Duration
- MFE
- MAE
- Strategy version

Filters:

- Today
- 7D
- Custom date range
- Symbol
- Side
- Exit reason
- Strategy version

Add CSV export.

### Signals tab

Show last candidates, including skipped ones:

- symbol;
- score;
- classifier label;
- proposed side;
- spread;
- volume spike;
- OI change;
- taker ratio;
- decision: trade / skip;
- reasons true/false;
- active settings version.

### Settings impact tab or section

Aggregate PnL by settings version:

- trades;
- net PnL;
- win rate;
- average win/loss;
- profit factor;
- stop loss count;
- breakeven+ count;
- trailing count.

This is required so we can tune settings from the UI and compare versions honestly.

## 9. Paper execution and multi-position handling

Because `max_open_positions=5`, fix concurrency carefully.

Requirements:

- One account, multiple positions.
- Do not open duplicate same symbol+direction unless setting allows it. Default: no duplicate same symbol+direction.
- Allow different symbols simultaneously up to 5.
- Each position has isolated paper margin.
- Margin reservation must reduce available cash/equity appropriately.
- Position updates must be independent.
- Position manager loop should update all open positions every `1-3s`.
- Each position stores its own entry settings, exit settings, and thresholds from the active version at open time.
- Manual close in UI should close only that paper position and persist fills.

Add settings:

```yaml
positions:
  allow_duplicate_symbol: false
  allow_opposite_positions_same_symbol: false
  max_open_positions: 5
```

## 10. Safer deployment / redeploy flow

Update deployment docs.

Expected VPS flow:

```bash
cd /opt/Long-Bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
sudo systemctl restart long-bot-paper
sudo systemctl restart long-bot-web
sudo systemctl status long-bot-paper --no-pager
sudo systemctl status long-bot-web --no-pager
```

But day-to-day strategy tuning should not require redeploy:

- change settings in UI;
- click Validate;
- click Apply;
- runner hot-reloads new settings;
- new trades use new version.

Add `GET /healthz` and `GET /api/bot/status` showing:

- runner online/offline;
- last scanner tick;
- last price update;
- active settings version;
- open positions;
- DB path;
- uptime;
- recent errors.

## 11. Tests to add/update

Make `pytest -q` work without manual `PYTHONPATH`.

Add tests:

1. Runtime settings load from YAML then DB override.
2. Settings validation rejects invalid ranges:
   - negative leverage;
   - max_open_positions < 1;
   - leg weights not summing to 1;
   - TP close fraction > 1;
   - trailing min > max;
   - fee rates < 0.
3. Settings apply increments version and writes audit log.
4. Worker hot reload picks up new settings version.
5. New trade stores `strategy_config_version` and settings hash.
6. Existing open position keeps original exit rules after settings change.
7. UI/API settings endpoints require auth for write endpoints.
8. Paper mode default max_open_positions is 5.
9. Paper mode allows max_trades_per_hour=0 and max_daily_trades=0 as unlimited.
10. max_loss_streak=10 default, and 0 disables it in paper.
11. Direction classifier does not short strong LONG_CONTINUATION momentum when `avoid_shorting_strong_momentum=true`.
12. SHORT requires stricter score than LONG by default.
13. Fees display total roundtrip fee correctly.
14. Closed PnL uses fills only.
15. Manual paper close persists exit fill and correct net PnL.
16. Multi-position manager updates up to 5 positions independently.
17. Dashboard summary includes active settings version.
18. PnL by settings version aggregation works.

Acceptance commands:

```bash
pytest -q
python -m app.trading.runner --config config.paper.yaml --once
python -m app.web.server --config config.paper.yaml --host 127.0.0.1 --port 8008
```

## 12. Acceptance behavior

After implementation:

1. Open dashboard.
2. It shows paper balance starting from `20 USDT`.
3. Settings page shows active strategy/risk/entry/exit rules.
4. User can change `max_open_positions` to `5`, `max_loss_streak` to `10`, disable max trades, edit long/short scores, SL/TP/trailing settings.
5. Clicking Validate shows either success or clear validation errors.
6. Clicking Apply creates new settings version and worker reloads it without restart.
7. New trade plans and trades store that settings version.
8. Open positions table updates every few seconds.
9. Closed history shows gross PnL, total fees, net PnL, MFE/MAE, reason, duration.
10. Signals tab shows why bot entered or skipped.
11. Settings version performance can be compared.
12. Default mode does not place any real Binance orders.

## 13. Implementation priority

Do in this order:

1. Fix Fees/PnL display and persisted fee fields.
2. Add runtime settings models + DB tables + validation.
3. Add settings API endpoints.
4. Add Settings UI page.
5. Add hot reload in runner.
6. Add strategy_config_version/settings_hash to trade plans/positions/trades.
7. Change paper defaults: max open positions 5, max trades unlimited, loss streak 10/disableable.
8. Improve direction classifier to avoid over-shorting strong momentum.
9. Add multi-position position manager robustness.
10. Add dashboard analytics by settings version.
11. Add/adjust tests and README deployment instructions.

## 14. Non-negotiables

- Do not expose secrets in UI or logs.
- Do not execute arbitrary shell commands from web UI.
- Do not place live orders in default paper mode.
- Do not average down losing trades by default.
- Do not fake closed PnL from current mark price; use fills.
- Do not break existing tests without replacing them with better equivalents.
- Make errors visible in dashboard and logs.
- Keep UI minimal, mobile-friendly, and fast.
