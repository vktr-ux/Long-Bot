# GOAL.md — Crypto Long Scanner v2.2

Версия документа: `v2.2`  
Дата: `2026-06-04`  
Язык проекта: `Python 3.11+`  
Режим проекта: `local-first`, без автоторговли

---

## 0. Зачем переписали goal

Первая версия goal описывала простой momentum scanner: цена растет, объем растет, OI растет, funding нормальный — отправить alert.

После разбора кейса `OPNUSDT` стало понятно, что этого мало.

В кейсе OPN важны были не только цена и объем. Там сработала связка:

```text
1. Монета попала в топ активности на Bybit.
2. Это был не рандомный низколиквидный тикер, а свежий Launchpool/listing asset с деривативами на крупных биржах.
3. Был свежий narrative trigger вокруг prediction markets.
4. Цена пробивала многонедельный 4H resistance.
5. Пробой шел импульсной свечой с резким ростом объема.
6. Вход был не "на дне", а после подтверждения силы.
7. Публичный пост в большой канал мог добавить follow-flow, но это не должно быть первичным источником сигнала.
```

Поэтому новый бот должен быть не просто `price pump scanner`, а **многослойный радар аномальной активности + breakout context scanner**.


---

## 0.1. Sufficiency audit v2.2 — достаточно ли этого для нормального long scanner

Ответ: **да, этого достаточно для разработки сильного MVP long-scanner**, который будет находить OPN-style market anomalies и отправлять их человеку на ручной отсмотр. Но этого **недостаточно**, чтобы честно называть систему "автоматически находит пиздатые сделки" без проверки на истории и без живой калибровки.

Правильная формулировка продукта:

```text
бот детектит потенциально сильные long setups
→ человек вручную проверяет график/уровень/стоп/ретест/контекст
→ человек принимает решение
```

Неправильная формулировка:

```text
бот гарантированно находит сделки с высоким winrate
```

### 0.1.1. Данных достаточно для детекта OPN-like setups, если есть минимум 4 слоя

Сигнал уровня `BREAKOUT_HOT` или `VERY_HOT` не должен появляться по одному фактору. Нужна связка:

```text
1. Activity anomaly:
   symbol резко поднялся по turnover/volume rank или вошел в top activity.

2. Momentum + volume:
   цена ускорилась вверх, а объем/turnover вырос относительно собственной базы.

3. Derivatives confirmation:
   OI растет, funding не перегрет, bid/ask spread нормальный.

4. Chart context:
   цена тестирует или пробивает 1H/4H resistance zone.
```

Если есть только `price up`, это не сделка, а шум.
Если есть `price up + volume`, это `WATCH/HOT`.
Если есть `price up + volume + OI + 4H breakout`, это уже `BREAKOUT_HOT`.
Если сверху есть narrative/event boost и нет жестких risk warnings, это может стать `VERY_HOT`.

### 0.1.2. Минимальные acceptance критерии именно для "потенциально пиздатых сделок"

`BREAKOUT_HOT` должен требовать хотя бы 3 из 4:

```text
- activity_score >= 12/20
- momentum_score >= 12/20
- derivatives_score >= 10/20
- chart_score >= 12/25
```

`VERY_HOT` должен требовать:

```text
- total_score >= 90
- chart_state in [FRESH_BREAKOUT, CONFIRMED_BREAKOUT, RETEST_HELD]
- activity_score >= 12
- momentum_score >= 12
- no fatal risk warnings
```

Fatal risk warnings for VERY_HOT:

```text
- spread_pct > max_spread_pct
- funding_rate > danger_min
- FAILED_BREAKOUT state
- price already overextended too far above breakout zone
- BTC dumping hard by config threshold
```

Если fatal risk есть, максимум `HOT/WATCH`, но не `VERY_HOT`.

### 0.1.3. Двухступенчатый scan обязателен

Нельзя каждую минуту грузить глубокие candles/orderbook/OI-history для всех 500+ symbols. Это убьет rate limits и будет медленно.

Делать так:

```text
Stage A — broad scan, every 30–60 seconds:
- get all tickers
- rank symbols by turnover/volume/24h change
- quick candidate filters
- pick top 30–80 candidates

Stage B — deep scan, only for candidates:
- fetch 1m/5m/15m/1h/4h candles
- fetch OI history
- optionally fetch long/short ratio
- optionally fetch orderbook
- run breakout engine
- score and alert
```

### 0.1.4. Обязательные технические детали, без которых бот будет ловить мусор

Добавить в реализацию:

```text
- Bybit instruments-info pagination via cursor, because linear symbols can be >500.
- global async rate limiter per exchange.
- per-endpoint retry/backoff.
- request timeout.
- local cache TTL for symbols and slow data.
- stable candle sorting ASC, because exchanges may return reverse order.
- closed-candle vs live-candle flag.
- prevent duplicate alerts via state machine + cooldown.
- save all emitted signals for later analysis.
```

### 0.1.5. Обязательная проверка качества сигналов после MVP

После первых живых алертов нужно не спорить "бот умный или нет", а измерять.

Для каждого сигнала сохранять future outcome labels:

```text
mfe_15m, mfe_1h, mfe_4h, mfe_24h      # maximum favorable excursion
mae_15m, mae_1h, mae_4h, mae_24h      # maximum adverse excursion
time_to_mfe
hit_plus_3_pct_before_minus_2_pct
hit_plus_5_pct_before_minus_3_pct
retest_happened
breakout_failed
```

Это нужно для калибровки threshold'ов. Без этого score будет красивой игрушкой, а не рабочим радаром.

### 0.1.6. Что считать успехом первой версии

MVP считается успешным, если он:

```text
- стабильно находит top activity coins;
- заранее или одновременно с публичными каналами подсвечивает сильные импульсы;
- отличает обычный pump от 4H breakout context;
- не спамит мусором;
- дает понятные reasons/warnings;
- сохраняет сигналы для разбора;
- не требует биржевых API keys;
- не пытается торговать сам.
```

---

## 1. Главная цель проекта

Нужно сделать локально запускаемый Telegram-сканер криптовалютных фьючерсных пар, который ищет ранние или средне-ранние long continuation setups.

Бот **НЕ торгует сам**.  
Бот **НЕ открывает сделки**.  
Бот **НЕ дает команду BUY/SELL**.  
Бот **НЕ использует биржевые private API keys**.

Он только сканирует рынок и отправляет Telegram-алерт:

```text
В этой монете появилась аномальная активность.
Есть momentum + объем + OI/funding context + возможно пробой 4H уровня.
Открой график и проверь сетап руками.
```

Правильная схема:

```text
scanner found anomaly
→ Telegram alert
→ human opens chart
→ human checks level / retest / stop / invalidation
→ human decides manually
```

Неправильная схема:

```text
bot says BUY
→ user blindly enters
```


---

---

## 2. Core philosophy

Мы не пытаемся угадать каждый памп заранее.

Мы пытаемся быстрее толпы увидеть, что:

```text
в монете уже началась аномальная активность,
но движение еще может иметь continuation,
потому что оно подтверждено объемом, деривативами и графическим контекстом.
```

Бот должен находить не "самые дешевые монеты", а **монеты, где активность резко изменилась**.

Ключевая мысль из кейса OPN:

```text
Не нужно заранее вручную мониторить топ-500/1000 монет.
Нужно сканировать всю биржевую вселенную и ловить момент,
когда один тикер резко выходит в топ активности и одновременно ломает важный уровень.
```

---

## 3. What the bot is

Бот — это **scanner / radar / alert engine**.

Он должен отвечать на вопросы:

```text
1. Какие пары на Bybit Futures сейчас стали необычно активными?
2. Где цена ускоряется вверх?
3. Где объем сильно выше обычного?
4. Где OI подтверждает приток деривативного интереса?
5. Где funding еще не слишком перегрет?
6. Где есть пробой 1H/4H resistance?
7. Где есть свежий narrative/news catalyst?
8. Где риск уже слишком позднего входа?
```

---

## 4. What the bot is NOT

Бот не должен:

```text
- торговать;
- ставить ордера;
- читать баланс;
- управлять позициями;
- использовать Binance/Bybit API keys;
- давать торговые команды;
- писать BUY NOW / LONG NOW;
- обещать прибыль;
- считать, что score = гарантия успеха.
```

Все сообщения должны звучать как:

```text
Открыть график. Проверить уровень, стоп и сценарий руками.
```

---

## 5. API access

### 5.1. Биржевые ключи не нужны

На первом этапе не нужны:

```text
BINANCE_API_KEY
BINANCE_SECRET
BYBIT_API_KEY
BYBIT_SECRET
```

Нужны только публичные market data endpoints.

### 5.2. Что реально нужно

```text
1. Интернет с локального компьютера или VPS.
2. Доступ к Bybit public market-data API.
3. Доступ к Binance public market-data API, когда включим Binance source.
4. Telegram Bot Token.
5. Telegram Chat ID.
```

### 5.3. Что дает наличие аккаунтов Bybit/Binance

Наличие аккаунтов полезно для ручной проверки в интерфейсе бирж:

```text
- быстро открыть пару из Telegram alert;
- посмотреть стакан, ленту, funding, OI, топ активности;
- вручную принять решение о входе;
- вручную выставить стоп/тейк, если человек решил торговать.
```

Но для scanner MVP аккаунты **не нужны технически**.

Не добавлять в MVP:

```text
BYBIT_API_KEY
BYBIT_API_SECRET
BINANCE_API_KEY
BINANCE_API_SECRET
```

Если когда-нибудь понадобится account-aware mode, например "покажи мои позиции / funding cost / liquidation risk", это должен быть отдельный read-only модуль. Он не должен смешиваться с signal scanner.

### 5.4. Если локально API блокируется

Проект должен без изменений запускаться на VPS.

---

## 6. Data sources

## 6.0. Exchange resource matrix

Цель — использовать не “всё подряд”, а все ресурсы, которые реально помогают поймать OPN-style setup раньше толпы.

### 6.0.1. Bybit resources

| Resource | Priority | Public/private | Purpose |
|---|---:|---|---|
| REST instruments-info | P0 | public | список активных USDT linear perpetuals, launchTime, contract status, tick/qty filters |
| REST tickers | P0 | public | lastPrice, 24h change, prevPrice1h, OI, turnover, volume, funding, bid/ask |
| REST kline | P0 | public | 1m/5m/15m/1h/4h candles, volume spike, resistance breakout |
| REST open-interest | P0 | public | OI history 5m/15m/1h, подтверждение притока плеча |
| REST funding/history | P1 | public | funding trend, перегрев лонгов |
| REST account-ratio | P1 | public market endpoint | long/short crowd ratio, дополнительный контекст |
| REST orderbook | P1 | public | spread, depth, liquidity quality, slippage risk |
| REST recent public trades | P2 | public | aggressive trade flow, optional |
| Public WebSocket tickers | P1 | public | быстрее ловить изменение цены/OI/turnover/funding |
| Public WebSocket kline | P1 | public | быстрее ловить candle impulse и breakout |
| Public WebSocket orderbook/book ticker | P2 | public | spread/depth changes, liquidity holes |
| Public WebSocket trades | P2 | public | short-term tape/flow confirmation |
| Public WebSocket liquidations | P2 | public | optional: liquidation impulse / squeeze context |
| Private account/order/position APIs | banned in MVP | private | не использовать |

### 6.0.2. Binance resources

| Resource | Priority | Public/private | Purpose |
|---|---:|---|---|
| REST exchangeInfo | P0 Phase 3 | public | список USDⓈ-M perpetual symbols, status, filters |
| REST ticker/24hr | P0 Phase 3 | public | 24h price/volume/quoteVolume, activity rank |
| REST klines | P0 Phase 3 | public | candles, volume spike, breakout engine |
| REST openInterest | P0 Phase 3 | public | current OI |
| REST openInterestHist | P1 Phase 3 | public | historical OI trend |
| REST fundingRate / fundingInfo | P1 Phase 3 | public | funding history/current funding constraints |
| REST topLongShortAccountRatio | P2 Phase 3 | public | top trader account ratio, crowd context |
| REST globalLongShortAccountRatio | P2 Phase 3 | public | broad crowd ratio |
| REST taker buy/sell volume | P2 Phase 3 | public | aggressive buyer/seller pressure |
| REST order book / book ticker | P2 Phase 3 | public | spread/depth/liquidity |
| WebSocket market streams | P2 Phase 3.5 | public | all-market tickers, kline, mark price, book ticker, liquidations |
| User Data Streams / Account / Trade APIs | banned in MVP | private | не использовать |

### 6.0.3. Priority meaning

```text
P0 = must-have для MVP или указанной фазы
P1 = strong upgrade, сильно улучшает качество сигналов
P2 = optional, добавлять после стабильной первой версии
banned = не использовать в этом проекте
```

Вывод: для Bybit MVP используем P0, затем быстро добавляем P1. Для Binance не блокируем MVP, но проектная архитектура должна быть готова к Phase 3.

## 6.0. Exchange resource completeness policy

У сканера есть три уровня использования ресурсов бирж.

### Level A — обязательный MVP

Это минимум, без которого бот не увидит OPN-подобный разгон:

```text
- список активных USDT perpetual contracts;
- tickers: last price, 24h change, 24h volume/turnover, bid/ask;
- candles/kline: 1m/5m/15m/1h/4h;
- current open interest;
- funding rate;
- BTCUSDT background candles.
```

### Level B — обязательно для "почти идеальной" версии

Это надо добавить после живого MVP, потому что именно это отличает простой pump scanner от нормального activity/breakout radar:

```text
- open interest history 5m/15m/1h;
- long/short account ratio;
- top trader long/short ratio там, где биржа дает такие данные;
- taker buy/sell volume or recent public trades;
- orderbook depth near current price;
- mark price / premium / basis context;
- cross-exchange confirmation: Bybit + Binance одновременно.
```

### Level C — later / optional

```text
- WebSocket streams для меньшей задержки;
- official announcements watcher;
- token unlocks;
- CoinMarketCap/CoinGecko market cap;
- social/narrative mindshare;
- account-aware read-only module для своих открытых позиций.
```

В MVP делаем Level A на Bybit.
В Phase 2 доводим до Level B и подключаем Binance.


## 6.0.4. Official API verification notes / implementation quirks

Codex must implement these exact details. These are not optional.

### Bybit quirks

```text
1. /v5/market/instruments-info must support cursor pagination.
   There are more than 500 linear symbols, so one request may not return all contracts.

2. /v5/market/tickers with category=linear can be used as the cheap all-market scan.
   It contains lastPrice, prevPrice1h, price24hPcnt, openInterest, openInterestValue,
   turnover24h, volume24h, fundingRate, nextFundingTime, bid1/ask1.

3. /v5/market/kline returns candles in reverse order by startTime.
   Always sort candles ASC before computing price change, RSI, ATR, swing highs and volume spike.

4. /v5/market/kline limit max is 1000.
   Use enough candles for 1m volume spike and 4h breakout, but do not refetch everything every cycle.

5. /v5/market/open-interest supports intervalTime values like 5min, 15min, 30min, 1h, 4h, 1d.
   Use it for OI history. Stored snapshots are fallback, not primary advanced OI source.

6. /v5/market/account-ratio is the Bybit long/short ratio endpoint.
   It is optional for MVP, useful for Level 2/3.

7. Respect Bybit IP rate limits and implement request throttling.
   Do not run at the edge of limits.
```

### Binance quirks

```text
1. USDⓈ-M futures exchange info is /fapi/v1/exchangeInfo.
2. USDⓈ-M all-symbol 24h ticker is /fapi/v1/ticker/24hr without symbol, but it has higher request weight.
3. USDⓈ-M klines are /fapi/v1/klines and request weight depends on LIMIT.
4. USDⓈ-M current OI is /fapi/v1/openInterest.
5. USDⓈ-M historical OI is /futures/data/openInterestHist with symbol + period.
   Do not use COIN-M pair/contractType parameters for USDⓈ-M implementation.
6. USDⓈ-M taker buy/sell volume is /futures/data/takerBuySellVol with symbol + period.
7. Binance futures-data endpoints usually expose only recent history windows.
   Do not assume unlimited historical data from these endpoints.
8. Binance public WebSocket routes are split into public/market/private paths.
   Do not use private/user streams in this project.
```

### Two-stage scanning is mandatory

Do not fetch expensive endpoints for all symbols on every 60-second cycle.

Correct architecture:

```text
Stage A — cheap all-market scan every cycle:
- Bybit tickers for category=linear
- ranks, 24h turnover, price24h, prevPrice1h, funding/current OI, spread

Stage B — enrich only candidates:
- klines 1m/5m/15m/1h/4h
- open-interest history
- orderbook
- long/short ratio
- recent trades / taker flow

Stage C — cache slow data:
- 4H candles/resistance zones cached and refreshed less frequently
- symbol universe refreshed every 10–30 minutes
- narratives/events loaded from local YAML
```

Default candidate enrichment cap:

```yaml
performance:
  max_enriched_candidates_per_cycle: 40
  max_concurrent_requests: 10
  symbol_universe_refresh_minutes: 20
  resistance_cache_ttl_minutes: 15
  orderbook_check_only_for_score_above: 55
```

## 6.1. Primary source: Bybit Futures

MVP делаем через Bybit V5 public API.

### 6.1.0. Bybit endpoint matrix

Level A — MVP endpoints:

```text
GET /v5/market/instruments-info        active USDT linear perpetual universe
GET /v5/market/tickers                 price, prevPrice1h, 24h stats, OI, funding, bid/ask
GET /v5/market/kline                   1m/5m/15m/1h/4h candles
```

Level B — advanced endpoints:

```text
GET /v5/market/open-interest           OI history by 5min/15min/1h/4h/1d
GET /v5/market/account-ratio           long/short account ratio
GET /v5/market/funding/history         funding trend, not only current funding
GET /v5/market/orderbook               depth/liquidity near breakout level
GET /v5/market/recent-trade            tape / aggressive flow proxy
GET /v5/market/mark-price-kline        mark-price confirmation, optional
GET /v5/market/premium-index-price-kline premium context, optional
```

Level C — later:

```text
WebSocket tickers / kline / orderbook / publicTrade streams
```

Используем:

```text
category=linear
quoteCoin=USDT
status=Trading
```

### 6.1.1. Why Bybit first

Bybit удобен для MVP, потому что `tickers` для linear contracts сразу дает много полей:

```text
lastPrice
prevPrice24h
price24hPcnt
prevPrice1h
openInterest
openInterestValue
turnover24h
volume24h
fundingRate
nextFundingTime
bid1Price
ask1Price
```

Это позволяет быстро собрать первую версию без private API.

### 6.1.2. Bybit public WebSocket, Phase 1.8

После REST MVP добавить public WebSocket layer.

Нужные streams/topics:

```text
tickers.{symbol}       # price/OI/turnover/funding/bid/ask updates
kline.1.{symbol}       # 1m candle impulse
kline.5.{symbol}       # 5m candle impulse
kline.15.{symbol}      # signal timing
kline.240.{symbol}     # breakout context, optional via WS or REST
orderbook.{depth}.{symbol} or book ticker equivalent, optional
publicTrade.{symbol}, optional
liquidation/all liquidation stream, optional if stable
```

WebSocket не заменяет REST полностью. REST нужен для bootstrap, historical candles, OI/funding history and database consistency. WebSocket нужен для скорости.

Architecture requirement:

```text
REST bootstrap → local state/cache → WebSocket updates → periodic score recalculation → Telegram alert
```

Если WebSocket падает:

```text
1. Log warning.
2. Reconnect with backoff.
3. Continue REST polling fallback.
```

---

## 6.2. Secondary source: Binance Futures, Phase 2

Binance добавить после стабильного Bybit MVP.

### 6.2.0. Binance endpoint matrix

Level A — Binance MVP source:

```text
GET /fapi/v1/exchangeInfo              active USDⓈ-M perpetual universe
GET /fapi/v1/ticker/24hr               24h price/volume/turnover stats
GET /fapi/v1/klines                    candles; includes taker-buy fields inside kline response
GET /fapi/v1/openInterest              current OI
GET /fapi/v1/fundingRate               funding history/current recent funding
GET /fapi/v1/premiumIndex              mark price + lastFundingRate, if needed
```

Level B — Binance advanced derivatives data:

```text
GET /futures/data/openInterestHist             OI history
GET /futures/data/globalLongShortAccountRatio  all traders long/short account ratio
GET /futures/data/topLongShortAccountRatio     top traders account ratio
GET /futures/data/topLongShortPositionRatio    top traders position ratio
GET /futures/data/takerlongshortRatio          taker buy/sell volume ratio
GET /fapi/v1/depth                             orderbook depth
GET /fapi/v1/aggTrades                         aggressive trade/tape proxy
GET /fapi/v1/markPriceKlines                   mark price candles, optional
GET /fapi/v1/premiumIndexKlines                premium index candles, optional
```

Level C — later:

```text
Binance WebSocket miniTicker/ticker/kline/depth/aggTrade streams
Binance announcements/listings/Launchpool watcher
```

Binance сложнее, потому что часть данных надо дергать отдельными запросами. Зато Binance дает более богатый public derivatives dataset: OI history, global long/short, top trader ratios and taker buy/sell volume.

---

## 6.3. Narrative / news source, Phase 1.5 or 2

Нужен отдельный слой `narrative trigger`.

В MVP можно сделать **manual-first**:

```text
config/narratives.yaml
config/symbol_tags.yaml
config/events.yaml
```

Автоматическое чтение новостей можно добавить потом.

Примеры narrative triggers:

```text
Binance Wallet Prediction Markets
Predict.fun campaign
Launchpool
Launchpad
Alpha
new listing
futures listing
sector upgrade
points campaign
airdrop campaign
```

### 6.3.1. Why narrative matters

OPN был связан с prediction markets. Если рядом выходит новость от Binance про Prediction Markets / Predict.fun, а OPN — маленький и ликвидный токен этого сектора, то его надо подсвечивать сильнее.

---

## 6.4. Unlock / event source, Phase 1.5 or 2

В MVP можно сделать manual `events.yaml`.

Пример:

```yaml
events:
  OPNUSDT:
    - type: unlock
      date_utc: "2026-06-05T00:00:00Z"
      amount_tokens: 32090000
      note: "Large unlock / airdrop allocation"
```

Логика:

```text
unlock рядом не всегда отменяет long,
но обязательно добавляет warning и risk penalty.
```

Важно: памп перед unlock может быть как ловушкой, так и частью спекулятивного сценария. Бот не должен автоматически short/long по unlock. Он должен предупреждать.

---

## 6.5. Social / influencer source, not MVP

Публичные посты больших каналов могут создавать follow-flow.

Но в MVP не нужно автоматизировать Telegram/X monitoring.

В будущем можно добавить:

```text
- manual social trigger input;
- Telegram public channel watcher;
- X keyword watcher;
- Kaito/mindshare API, if available;
- simple RSS/news watcher.
```

Но базовый edge должен быть в market data, а не в том, что кто-то уже написал в канал.

---

## 7. Lessons from OPN case study

### 7.1. What happened structurally

OPN was not a random unknown coin. It had:

```text
- major exchange futures presence;
- Bybit perpetual contract;
- Binance Launchpool/listing background;
- prediction markets narrative;
- fresh Binance Wallet / Predict.fun announcements;
- relatively small market cap compared with possible derivatives turnover;
- strong breakout on 4H chart;
- public influencer call after entry, which could add social follow-flow.
```

### 7.2. Important insight

The entry was not from the bottom.

It was closer to:

```text
price already pumped
→ old 4H resistance broke
→ trader entered around breakout continuation
→ leverage turned a ~9% price move into ~80% ROI
```

So our scanner should not obsess over catching the exact bottom. It should catch:

```text
fresh activity + confirmed breakout + continuation potential
```

### 7.3. What the public post means

If a trader enters first and posts the setup in a public channel after entry, several things may be true:

```text
1. He already had a position before the crowd.
2. The public channel may add buying pressure if the audience reacts.
3. The screenshot itself can become a social catalyst.
4. But we cannot rely on that as a primary signal.
5. Our scanner should ideally alert before or around the same moment: top activity + breakout.
```

Do not encode assumptions like "influencer knows insider info" into the bot. Encode observable market behavior.

---

## 8. Scanner layers

The bot has multiple layers.

```text
Layer 1: Universe
Layer 2: Activity rank
Layer 3: Momentum
Layer 4: Volume / turnover acceleration
Layer 5: Derivatives confirmation
Layer 6: Chart breakout context
Layer 7: Narrative / catalyst boost
Layer 8: Risk penalties
Layer 9: Signal state machine
Layer 10: Telegram alert
```

---

## 9. Layer 1 — Universe

Use all active Bybit USDT linear perpetuals.

Filtering:

```text
category = linear
quote_asset = USDT
status = Trading
```

Config:

```yaml
symbols:
  quote_asset: "USDT"
  exclude_major_symbols: true
  major_symbols:
    - BTCUSDT
    - ETHUSDT
  blacklist: []
  include_symbols: []
```

BTCUSDT and ETHUSDT should still be fetched as market background symbols, even if excluded from signal generation.

---

## 10. Layer 2 — Activity rank

This layer tries to approximate phrases like:

```text
"one of the most active coins on Bybit today"
"entered TOP-3 active coins"
```

### 10.1. Activity metrics

For each symbol calculate:

```text
turnover_24h
volume_24h
turnover_rank_24h
volume_rank_24h
price_change_24h_rank
turnover_rank_change_since_previous_scan
volume_spike_15m
volume_spike_1h
turnover_spike_15m
turnover_spike_1h
```

### 10.2. Activity score

Activity score range: `0–20`.

Suggested scoring:

```text
+10 if turnover_rank_24h <= 5
+8  if turnover_rank_24h <= 10
+5  if turnover_rank_24h <= 25

+5  if volume_spike_15m >= 3.0
+3  if volume_spike_15m >= 2.0

+5  if turnover_spike_15m >= 3.0
+3  if turnover_spike_15m >= 2.0

+3  if rank improved by 50+ places since last stored rank
```

Cap activity score at 20.

### 10.3. Why rank matters

A small-cap coin with 24h turnover near large caps is a special situation. It means attention and leverage are concentrated there.

This is exactly the type of signal the bot must surface.

---

## 11. Layer 3 — Momentum

### 11.1. Price changes

Calculate:

```text
price_change_1m
price_change_5m
price_change_15m
price_change_1h
price_change_4h
price_change_24h
```

Formula:

```text
price_change_pct = (current_price - past_price) / past_price * 100
```

### 11.2. Momentum score

Momentum score range: `0–20`.

Suggested scoring:

```text
+4 if price_change_5m  >= +2%
+7 if price_change_15m >= +4%
+7 if price_change_1h  >= +8%
+4 if price_change_4h  >= +12%
```

Cap at 20.

### 11.3. Candle quality

Calculate for latest 15m and 1h candle:

```text
body_pct = abs(close - open) / (high - low)
close_position = (close - low) / (high - low)
upper_wick_pct = (high - max(open, close)) / (high - low)
lower_wick_pct = (min(open, close) - low) / (high - low)
```

Good impulse candle:

```text
body_pct >= 0.55
close_position >= 0.70
upper_wick_pct <= 0.30
```

Add reasons:

```text
"15m impulse candle closed near high"
"1h impulse candle body strong"
```

Warnings:

```text
"large upper wick — possible rejection"
```

---

## 12. Layer 4 — Volume / turnover acceleration

### 12.1. Volume spike

Use 1m candles.

```text
volume_spike_15m = last_15m_volume / average_previous_15m_volume
turnover_spike_15m = last_15m_turnover / average_previous_15m_turnover
```

Lookback:

```yaml
metrics:
  volume_spike_lookback_periods: 12
```

This compares current 15 minutes to roughly previous 3 hours.

### 12.2. Volume score

Volume can be part of activity score or separate.

In v2 use it inside activity score and reasons/warnings.

Good:

```text
volume_spike_15m >= 2.5
turnover_spike_15m >= 2.5
```

Very good:

```text
volume_spike_15m >= 5.0
turnover_spike_15m >= 5.0
```

Bad:

```text
price up but volume_spike_15m < 1.5
```

Warning:

```text
"price moved without strong volume confirmation"
```

---

## 13. Layer 5 — Derivatives confirmation

Derivatives layer is critical.

We need to distinguish:

```text
price up + OI up      → fresh leverage entering, continuation possible
price up + OI flat    → maybe spot-driven, still possible
price up + OI down    → short covering / squeeze, can continue but less stable
price up + funding hot → late, crowded, risk of pullback
```

### 13.1. OI metrics

Calculate:

```text
oi_current
oi_change_5m_pct
oi_change_15m_pct
oi_change_1h_pct
oi_value_current
open_interest_value_24h_rank
```

Use:

```text
1. Ticker openInterest/openInterestValue
2. /v5/market/open-interest for history when possible
3. Stored snapshots as fallback
```

### 13.2. OI score

Derivative score range: `0–20`.

Suggested scoring:

```text
+6 if oi_change_15m_pct >= +3%
+8 if oi_change_1h_pct  >= +8%
+4 if open_interest_value is high enough vs liquidity threshold
+4 if price up and OI up together
```

Cap at 20.

### 13.3. Funding metrics

Calculate:

```text
funding_rate_current
funding_rate_abs
funding_trend_last_3_events optional
next_funding_time
```

Default thresholds:

```yaml
funding:
  good_max: 0.0003       # 0.03%
  caution_min: 0.0005    # 0.05%
  danger_min: 0.0010     # 0.10%
```

Scoring:

```text
+4 if 0 <= funding_rate <= 0.0003
+2 if funding_rate < 0 and everything else is bullish, because shorts may still be paying
-8 if funding_rate > 0.0005
-15 if funding_rate > 0.0010
```

### 13.4. Long/short ratio optional

If endpoint works reliably, fetch:

```text
/v5/market/account-ratio
```

Metrics:

```text
long_ratio
short_ratio
long_short_ratio
long_ratio_change
```

Warnings:

```text
"long account ratio very crowded"
"long/short ratio rising too fast"
```

Do not block MVP on this.

---

## 14. Layer 6 — Chart breakout context

This is the biggest upgrade from v1.

We need to detect cases like OPN:

```text
price has been below a 4H resistance zone for weeks
→ current impulse breaks above it
→ activity and derivatives confirm
```

### 14.1. Timeframes

Fetch candles:

```text
1m  — for short-term movement and volume spike
5m  — for short-term trend
15m — for signal timing
1h  — for intraday structure
4h  — for major resistance/breakout context
1d  — optional for higher timeframe context
```

Bybit kline interval values:

```text
1
5
15
60
240
D
```

### 14.2. Resistance detection algorithm

Use 4H candles.

Parameters:

```yaml
breakout:
  enabled: true
  timeframe: "240"
  lookback_candles: 90
  swing_window: 2
  min_touches: 2
  zone_tolerance_pct: 0.015
  atr_tolerance_mult: 0.35
  breakout_buffer_pct: 0.006
  max_distance_above_zone_pct: 0.12
  require_volume_confirmation: true
```

Algorithm:

```text
1. Load last N 4H candles, sorted ASC.
2. Identify swing highs:
   high[i] > highs of swing_window candles left and right.
3. Cluster swing highs into resistance zones.
4. Zone tolerance = max(price * zone_tolerance_pct, ATR14 * atr_tolerance_mult).
5. Keep zones with min_touches >= 2.
6. Pick nearest valid resistance zone below/around current price.
7. Breakout if:
   current_price > zone_high * (1 + breakout_buffer_pct)
   AND previous 4H close was <= zone_high OR breakout happened within recent 1–3 candles.
8. Avoid chasing if:
   current_price > zone_high * (1 + max_distance_above_zone_pct)
```

### 14.3. Breakout states

```text
NO_BREAKOUT
APPROACHING_RESISTANCE
TESTING_RESISTANCE
FRESH_BREAKOUT
CONFIRMED_BREAKOUT
RETESTING_BROKEN_RESISTANCE
RETEST_HELD
FAILED_BREAKOUT
OVEREXTENDED_AFTER_BREAKOUT
```

Definitions:

```text
APPROACHING_RESISTANCE:
  current price within 2% below zone_high

TESTING_RESISTANCE:
  current price inside resistance zone

FRESH_BREAKOUT:
  current price above zone_high + buffer, but 4H candle not closed yet

CONFIRMED_BREAKOUT:
  latest closed 4H candle closed above zone_high + buffer

RETESTING_BROKEN_RESISTANCE:
  price returned to zone after breakout

RETEST_HELD:
  price touched zone and bounced back above it

FAILED_BREAKOUT:
  price broke above zone but returned below zone_mid / zone_low

OVEREXTENDED_AFTER_BREAKOUT:
  price is too far above zone without retest
```

### 14.4. Chart score

Chart score range: `0–25`.

Suggested scoring:

```text
+4  if price is approaching a valid 4H resistance
+6  if price is testing a valid 4H resistance
+12 if fresh breakout above valid 4H resistance
+15 if confirmed 4H breakout
+18 if retest of broken resistance held
+5  if resistance zone has 3+ touches
+4  if breakout candle has volume confirmation
-8  if failed breakout
-8  if large upper wick after breakout
-10 if overextended > max_distance_above_zone_pct
```

Cap at 25.

### 14.5. RSI warning

Calculate RSI 14 on:

```text
15m
1h
4h
```

Do not block a signal just because RSI is high. Momentum breakouts often have high RSI.

But add warnings:

```text
RSI 15m > 80 → warning
RSI 1h > 80  → warning
RSI 4h > 80  → stronger warning
```

Risk penalty:

```text
-4 if RSI 15m > 85
-6 if RSI 1h > 85
-8 if RSI 4h > 80
```

---

## 15. Layer 7 — Narrative / catalyst boost

### 15.1. Manual narrative config

Create:

```text
config/symbol_tags.yaml
config/narratives.yaml
config/events.yaml
```

Example `symbol_tags.yaml`:

```yaml
OPNUSDT:
  sectors:
    - prediction_markets
    - launchpool
  tags:
    - binance_launchpool
    - bybit_perp
    - small_cap
```

Example `narratives.yaml`:

```yaml
active_narratives:
  - id: prediction_markets
    active_until_utc: "2026-06-07T00:00:00Z"
    weight: 8
    keywords:
      - prediction markets
      - Predict.fun
      - Predict Points
      - Binance Wallet Prediction
```

### 15.2. Narrative score

Narrative score range: `0–15`.

Suggested scoring:

```text
+8 if symbol sector matches an active narrative
+4 if symbol has major_exchange_listing / launchpool / alpha tag
+3 if there was a relevant event in last 72h
```

Cap at 15.

### 15.3. Future auto news watcher

Not MVP, but design should allow:

```text
- Binance announcements watcher
- Bybit announcements watcher
- OKX announcements watcher
- RSS parser
- keyword classifier
```

---

## 16. Layer 8 — Risk penalties

Risk penalties are separate from positive score.

### 16.1. Overextension

```text
-8  if price_change_24h > +60%
-15 if price_change_24h > +100%
-25 if price_change_24h > +150%
```

This should not always block a signal. It should make alert more cautious.

### 16.2. Funding risk

```text
-8  if funding_rate > 0.0005
-15 if funding_rate > 0.0010
-25 if funding_rate > 0.0020
```

### 16.3. BTC background

Fetch BTCUSDT and calculate:

```text
btc_change_15m
btc_change_1h
btc_change_4h
```

Penalties:

```text
-8  if btc_change_15m < -1.5%
-15 if btc_change_1h < -3.0%
-20 if btc_change_4h < -5.0%
```

### 16.4. Unlock/event risk

From `events.yaml`:

```text
-5  if unlock within 7 days
-10 if unlock within 3 days
-15 if unlock within 24 hours
```

Warnings:

```text
"large unlock within 24h"
"event risk: token unlock soon"
```

### 16.5. Liquidity/spread risk

Calculate spread:

```text
spread_pct = (ask1Price - bid1Price) / mid_price * 100
```

Penalty:

```text
-10 if spread_pct > 0.30%
-20 if spread_pct > 0.75%
```

### 16.6. Fakeout risk

Warnings/penalties:

```text
-8 if latest candle has upper_wick_pct > 0.45
-10 if price returned below breakout zone
-8 if volume collapsed after breakout
```

---

## 17. Combined score system

Total score:

```text
total_score =
  activity_score      # 0–20
+ momentum_score      # 0–20
+ derivatives_score   # 0–20
+ chart_score         # 0–25
+ narrative_score     # 0–15
- risk_penalty        # 0–60+
```

Clamp:

```text
0 <= total_score <= 100
```

### 17.1. Signal levels

```text
0–49    NO_SIGNAL
50–64   WATCH
65–79   HOT
80–89   BREAKOUT_HOT
90–100  VERY_HOT
```

### 17.2. Signal types

```text
MOMENTUM_WATCH
ACTIVITY_SPIKE
BREAKOUT_WATCH
BREAKOUT_HOT
BREAKOUT_RETEST
NARRATIVE_BREAKOUT
OVEREXTENDED_WARNING
FAILED_BREAKOUT_WARNING
```

### 17.3. Important naming rule

Never call it `BUY`.

Use:

```text
open chart
check setup
manual review
watch for retest
breakout context
risk warning
```

---

## 18. Candidate filters

Before full scoring, filter obvious noise.

Candidate passes if:

```text
turnover_24h >= min_turnover_24h_usd
AND spread_pct <= max_spread_pct
AND symbol not in blacklist
AND at least one of:
    price_change_15m >= min_price_change_15m_pct_for_candidate
    price_change_1h >= min_price_change_1h_pct_for_candidate
    turnover_rank_24h <= top_activity_rank_candidate
    volume_spike_15m >= min_volume_spike_for_candidate
    chart_state in [TESTING_RESISTANCE, FRESH_BREAKOUT, CONFIRMED_BREAKOUT]
```

Config:

```yaml
filters:
  min_turnover_24h_usd: 10000000
  min_volume_24h_usd: 5000000
  max_spread_pct: 0.30
  min_price_change_15m_pct_for_candidate: 1.5
  min_price_change_1h_pct_for_candidate: 3.0
  min_volume_spike_for_candidate: 2.0
  top_activity_rank_candidate: 30
```

---

## 19. State machine

Each symbol should have a state.

```text
IDLE
DISCOVERED_ACTIVITY
APPROACHING_LEVEL
TESTING_LEVEL
FRESH_BREAKOUT
CONFIRMED_BREAKOUT
RETESTING
RETEST_HELD
CONTINUATION
OVEREXTENDED
FAILED
COOLDOWN
```

### 19.1. Why state matters

Without state, the bot will spam the same signal repeatedly.

With state, it can say:

```text
First alert: OPNUSDT entered top activity + approaching 4H resistance.
Second alert: OPNUSDT broke 4H resistance.
Third alert: OPNUSDT retested and held broken resistance.
Warning: OPNUSDT overextended after breakout.
```

### 19.2. State transition examples

```text
IDLE → DISCOVERED_ACTIVITY
  if activity_score >= threshold

DISCOVERED_ACTIVITY → APPROACHING_LEVEL
  if price within 2% below resistance

APPROACHING_LEVEL → TESTING_LEVEL
  if price enters resistance zone

TESTING_LEVEL → FRESH_BREAKOUT
  if current price breaks zone_high + buffer

FRESH_BREAKOUT → CONFIRMED_BREAKOUT
  if 4H candle closes above zone_high + buffer

FRESH_BREAKOUT → FAILED
  if price returns below zone_mid/zone_low

CONFIRMED_BREAKOUT → RETESTING
  if price returns to broken zone

RETESTING → RETEST_HELD
  if price bounces from zone

CONFIRMED_BREAKOUT → OVEREXTENDED
  if price runs too far above zone without retest
```

---

## 20. Telegram alert format v2

Alert must be compact but information dense.

Example:

```text
🚨 LONG SCANNER / BREAKOUT_HOT

Symbol: OPNUSDT
Exchange: Bybit Futures
Score: 84/100
State: FRESH_BREAKOUT

Activity:
Bybit turnover rank: #3
24h turnover: $75.4M
15m volume spike: x5.8

Price:
5m: +3.2%
15m: +8.7%
1h: +22.4%
4h: +48.1%
24h: +104.0%

Derivatives:
OI 15m: +7.5%
OI 1h: +18.3%
Funding: 0.018%

Chart:
4H resistance: 0.215–0.225
Current price: 0.2403
Breakout distance: +6.8% above zone
RSI 4H: 84

Narrative:
prediction_markets active
major exchange / Launchpool tag

Reasons:
• top Bybit activity
• price + volume impulse
• OI confirms leverage inflow
• 4H resistance breakout
• sector narrative active

Warnings:
• 24h move already > +100%
• RSI 4H > 80
• do not chase without level / retest / stop

Action:
Open chart. Do not enter without level, invalidation and stop.
```

### 20.1. If no breakout

```text
🚨 LONG SCANNER / WATCH

Symbol: XYZUSDT
State: DISCOVERED_ACTIVITY

Reason:
Activity spike detected, but no 4H breakout yet.

Action:
Open chart. Watch resistance/retest, do not chase.
```

---

## 21. Anti-spam / cooldown v2

Rules:

```text
1. Do not send same symbol + same state more than once per 30 minutes.
2. Allow earlier repeat if score increased by 10+.
3. Allow state transition alert immediately.
4. VERY_HOT can repeat every 10 minutes max.
5. Warnings can repeat only if risk state changed.
6. Reset cooldown after 90 minutes of no alerts.
```

Config:

```yaml
cooldown:
  default_minutes: 30
  very_hot_minutes: 10
  score_increase_to_repeat: 10
  reset_after_minutes: 90
  allow_immediate_state_transition_alert: true
```

---

## 22. Dry-run and local workflow

Commands:

```bash
python -m app.main --once --dry-run
python -m app.main --dry-run
python -m app.main --once
python -m app.main --config config.yaml
```

Dry-run behavior:

```text
1. Real public API calls.
2. No Telegram sends.
3. Print top candidates and signals to console.
4. Save data to SQLite unless config disables it.
```

Console output should include:

```text
Top activity rank
Top momentum candidates
Breakout candidates
Rejected candidates with reasons
Generated signals
```

---

## 23. Project structure

```text
crypto-long-scanner/
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── scheduler.py
│   │
│   ├── exchanges/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── bybit.py
│   │   └── binance.py
│   │
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── activity.py
│   │   ├── metrics.py
│   │   ├── derivatives.py
│   │   ├── breakout.py
│   │   ├── rsi.py
│   │   ├── scoring.py
│   │   ├── filters.py
│   │   ├── state.py
│   │   └── signals.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py
│   │   └── models.py
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── telegram.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       ├── numbers.py
│       └── time.py
│
├── config/
│   ├── narratives.yaml
│   ├── symbol_tags.yaml
│   └── events.yaml
│
├── tests/
│   ├── test_activity.py
│   ├── test_metrics.py
│   ├── test_derivatives.py
│   ├── test_breakout.py
│   ├── test_rsi.py
│   ├── test_scoring.py
│   ├── test_state.py
│   ├── test_filters.py
│   └── test_bybit_connector.py
│
├── data/
│   └── .gitkeep
│
├── config.yaml
├── .env.example
├── requirements.txt
├── README.md
├── AGENTS.md
└── GOAL.md
```

---

## 24. Suggested dependencies

`requirements.txt`:

```text
httpx
python-dotenv
PyYAML
pydantic
pytest
pytest-asyncio
```

Optional:

```text
rich
```

Use standard `sqlite3`. No heavy ORM unless absolutely necessary.

---

## 25. Config.yaml v2

```yaml
app:
  scan_interval_seconds: 60
  dry_run: false
  log_level: INFO
  database_path: data/scanner.sqlite3

exchanges:
  bybit:
    enabled: true
    base_url: "https://api.bybit.com"
    category: "linear"
  binance:
    enabled: false
    base_url: "https://fapi.binance.com"

symbols:
  quote_asset: "USDT"
  exclude_major_symbols: true
  major_symbols:
    - BTCUSDT
    - ETHUSDT
  blacklist: []
  include_symbols: []

filters:
  min_turnover_24h_usd: 10000000
  min_volume_24h_usd: 5000000
  max_spread_pct: 0.30
  min_price_change_15m_pct_for_candidate: 1.5
  min_price_change_1h_pct_for_candidate: 3.0
  min_volume_spike_for_candidate: 2.0
  top_activity_rank_candidate: 30

metrics:
  volume_spike_lookback_periods: 12
  candle_limit_1m: 300
  candle_limit_5m: 200
  candle_limit_15m: 160
  candle_limit_60m: 120
  candle_limit_240m: 120
  candle_limit_1d: 90

breakout:
  enabled: true
  timeframe: "240"
  lookback_candles: 90
  swing_window: 2
  min_touches: 2
  zone_tolerance_pct: 0.015
  atr_tolerance_mult: 0.35
  breakout_buffer_pct: 0.006
  max_distance_above_zone_pct: 0.12
  approach_distance_pct: 0.02
  require_volume_confirmation: true

rsi:
  enabled: true
  period: 14
  warning_15m: 80
  warning_1h: 80
  warning_4h: 80
  danger_15m: 85
  danger_1h: 85
  danger_4h: 80

funding:
  good_max: 0.0003
  caution_min: 0.0005
  danger_min: 0.0010

btc_filter:
  symbol: BTCUSDT
  bad_15m_pct: -1.5
  bad_1h_pct: -3.0
  bad_4h_pct: -5.0

scoring:
  levels:
    watch: 50
    hot: 65
    breakout_hot: 80
    very_hot: 90

cooldown:
  default_minutes: 30
  very_hot_minutes: 10
  score_increase_to_repeat: 10
  reset_after_minutes: 90
  allow_immediate_state_transition_alert: true

performance:
  max_enriched_candidates_per_cycle: 40
  max_concurrent_requests: 10
  symbol_universe_refresh_minutes: 20
  resistance_cache_ttl_minutes: 15
  orderbook_check_only_for_score_above: 55

telegram:
  enabled: true
  parse_mode: HTML
```

---

## 26. Environment variables

`.env.example`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Do not add exchange keys.

---

## 27. Data models

Use pydantic or dataclasses.

### 27.1. SymbolInfo

```python
class SymbolInfo:
    exchange: str
    symbol: str
    base_asset: str | None
    quote_asset: str
    status: str
    contract_type: str | None
```

### 27.2. TickerSnapshot

```python
class TickerSnapshot:
    timestamp_ms: int
    exchange: str
    symbol: str
    last_price: float
    price_24h_pct: float | None
    turnover_24h: float | None
    volume_24h: float | None
    turnover_rank_24h: int | None
    volume_rank_24h: int | None
    open_interest: float | None
    open_interest_value: float | None
    funding_rate: float | None
    next_funding_time_ms: int | None
    bid_price: float | None
    ask_price: float | None
    spread_pct: float | None
```

### 27.3. Candle

```python
class Candle:
    timestamp_ms: int
    exchange: str
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float | None
```

### 27.4. Metrics

```python
class Metrics:
    exchange: str
    symbol: str
    timestamp_ms: int

    price_change_1m: float | None
    price_change_5m: float | None
    price_change_15m: float | None
    price_change_1h: float | None
    price_change_4h: float | None
    price_change_24h: float | None

    volume_spike_15m: float | None
    turnover_spike_15m: float | None
    volume_spike_1h: float | None
    turnover_spike_1h: float | None

    oi_change_5m_pct: float | None
    oi_change_15m_pct: float | None
    oi_change_1h_pct: float | None
    funding_rate: float | None

    turnover_24h: float | None
    turnover_rank_24h: int | None
    volume_rank_24h: int | None
    spread_pct: float | None

    btc_change_15m: float | None
    btc_change_1h: float | None
    btc_change_4h: float | None

    rsi_15m: float | None
    rsi_1h: float | None
    rsi_4h: float | None
```

### 27.5. ResistanceZone

```python
class ResistanceZone:
    timeframe: str
    zone_low: float
    zone_high: float
    zone_mid: float
    touches: int
    first_touch_ts_ms: int
    last_touch_ts_ms: int
    strength_score: float
```

### 27.6. BreakoutContext

```python
class BreakoutContext:
    state: str
    timeframe: str
    resistance_zone: ResistanceZone | None
    current_price: float
    distance_to_zone_pct: float | None
    distance_above_zone_pct: float | None
    breakout_buffer_pct: float
    latest_candle_body_pct: float | None
    latest_candle_close_position: float | None
    latest_candle_upper_wick_pct: float | None
    volume_confirmed: bool
```

### 27.7. SignalCandidate

```python
class SignalCandidate:
    timestamp_ms: int
    exchange: str
    symbol: str
    score: int
    level: str
    signal_type: str
    state: str
    metrics: Metrics
    breakout: BreakoutContext | None
    scores: dict[str, int]
    risk_penalty: int
    reasons: list[str]
    warnings: list[str]
```

---

## 28. SQLite schema

### 28.1. symbols

```sql
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_asset TEXT,
    quote_asset TEXT,
    status TEXT,
    contract_type TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE(exchange, symbol)
);
```

### 28.2. market_snapshots

```sql
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_price REAL NOT NULL,
    price_24h_pct REAL,
    turnover_24h REAL,
    volume_24h REAL,
    turnover_rank_24h INTEGER,
    volume_rank_24h INTEGER,
    open_interest REAL,
    open_interest_value REAL,
    funding_rate REAL,
    next_funding_time_ms INTEGER,
    bid_price REAL,
    ask_price REAL,
    spread_pct REAL
);
```

Index:

```sql
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
ON market_snapshots(exchange, symbol, timestamp_ms);
```

### 28.3. candles

```sql
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    turnover REAL,
    UNIQUE(exchange, symbol, interval, timestamp_ms)
);
```

Index:

```sql
CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval_time
ON candles(exchange, symbol, interval, timestamp_ms);
```

### 28.4. symbol_states

```sql
CREATE TABLE IF NOT EXISTS symbol_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    score INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    UNIQUE(exchange, symbol)
);
```

### 28.5. signals

```sql
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    score INTEGER NOT NULL,
    level TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    state TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    breakout_json TEXT,
    sent_to_telegram INTEGER NOT NULL DEFAULT 0
);
```

Index:

```sql
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
ON signals(exchange, symbol, timestamp_ms);
```

---

## 29. Exchange connector interface

```python
class ExchangeConnector:
    name: str

    async def get_symbols(self) -> list[SymbolInfo]:
        ...

    async def get_tickers(self) -> list[TickerSnapshot]:
        ...

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        ...

    async def get_open_interest_history(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[tuple[int, float]]:
        ...

    async def get_long_short_ratio(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> list[dict]:
        ...

    async def get_orderbook(
        self,
        symbol: str,
        limit: int,
    ) -> dict | None:
        ...

    async def get_recent_trades(
        self,
        symbol: str,
        limit: int,
    ) -> list[dict]:
        ...

    async def get_taker_flow(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> list[dict]:
        ...
```

`get_long_short_ratio`, `get_orderbook`, `get_recent_trades`, and `get_taker_flow` can be optional / return empty data if not implemented in the current phase.

---

## 30. HTTP requirements

Use:

```text
httpx.AsyncClient
timeout 10–15 sec
retry 3 attempts
backoff 0.5 → 1 → 2 sec
```

Do not fail the whole scan because of one bad symbol.

Log errors clearly.

---

## 31. Implementation order

### Phase 1 — Bybit REST MVP with activity + momentum

```text
1. Project skeleton
2. Config + env loading
3. SQLite init
4. Bybit connector: instruments-info, tickers, klines
5. Bybit open-interest history
6. Activity rank from tickers
7. Basic metrics: price changes, volume spike, OI change, funding, spread
8. Basic scoring
9. Telegram alert
10. Dry-run and --once
```

### Phase 1.2 — Bybit derivatives quality upgrade

```text
1. Funding history trend
2. Long/short account ratio where available
3. Orderbook spread/depth check
4. Better OI 5m/15m/1h slope
5. Add warnings for poor liquidity / wide spread
```

### Phase 1.5 — Breakout engine

```text
1. Fetch 4H candles
2. Detect swing highs
3. Cluster resistance zones
4. Detect breakout state
5. Add chart score
6. Add RSI warning
7. Update Telegram alert with chart context
```

### Phase 1.8 — Bybit public WebSocket speed layer

```text
1. REST bootstrap all symbols and recent candles
2. Subscribe to ticker streams for candidate universe
3. Subscribe to kline streams for active candidates
4. Maintain in-memory latest state/cache
5. Recalculate score faster when activity spikes
6. Reconnect with backoff
7. Keep REST fallback active
```

### Phase 2 — Narrative/event configs

```text
1. symbol_tags.yaml
2. narratives.yaml
3. events.yaml
4. Narrative score
5. Unlock risk warnings
```

### Phase 3 — Binance REST source

```text
1. Binance connector: exchangeInfo, ticker/24hr, klines, openInterest, fundingRate
2. Optional: openInterestHist, long/short ratios, taker buy/sell volume
3. Normalized data model
4. Cross-exchange confirmation
5. Cross-exchange volume / OI comparison
```

### Phase 3.5 — Binance public WebSocket source

```text
1. All-market ticker/mini-ticker streams
2. Kline streams
3. Mark price streams
4. Book ticker/depth streams
5. Optional liquidation streams
6. Normalize into same internal state as Bybit
```

### Phase 4 — Backtest/replay

```text
1. Save all snapshots
2. Replay a specific date/time window
3. Evaluate what alerts would have fired
4. Tune thresholds
```


### Phase 4.5 — Outcome tracking / calibration loop

This is required before trusting alerts with real money.

For every emitted signal, record future price behavior:

```text
return_after_5m
return_after_15m
return_after_1h
return_after_4h
return_after_24h
max_favorable_excursion_1h
max_adverse_excursion_1h
max_favorable_excursion_4h
max_adverse_excursion_4h
whether_price_retested_breakout_zone
whether_retest_held
whether_signal_was_overextended
```

Add table `signal_outcomes`:

```sql
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_timestamp_ms INTEGER NOT NULL,
    entry_reference_price REAL NOT NULL,
    return_5m REAL,
    return_15m REAL,
    return_1h REAL,
    return_4h REAL,
    return_24h REAL,
    mfe_1h REAL,
    mae_1h REAL,
    mfe_4h REAL,
    mae_4h REAL,
    retested_zone INTEGER,
    retest_held INTEGER,
    computed_at_ms INTEGER NOT NULL
);
```

Add command:

```bash
python -m app.main --report-outcomes
```

Report must show:

```text
signals by level
average return after alert
median return after alert
MFE/MAE distribution
false positive examples
best signal examples
threshold suggestions
```

### Phase 4.6 — Minimum alert quality gates

A HOT/BREAKOUT_HOT/VERY_HOT signal must not come from one single factor.

Require at least 3 independent confirmations:

```text
1. Activity/momentum confirmation
2. Volume/turnover confirmation
3. Derivatives confirmation OR chart breakout confirmation
4. Optional narrative confirmation
```

Examples:

```text
Bad HOT:
price +8% but no volume, no OI, no breakout.

Good HOT:
price +8%, volume x4, OI +10%, testing 4H resistance.

Good BREAKOUT_HOT:
top activity rank, volume x5, OI +15%, fresh 4H breakout, funding not crazy.
```

If only one layer is strong, send WATCH at most.

### Phase 4.7 — Invalidation / risk context in alert

The bot still must not tell the user to buy, but every alert should show where the idea becomes invalid.

Examples:

```text
Possible invalidation:
- below broken 4H zone_low
- below latest 15m impulse candle low
- if price returns below breakout zone and volume collapses
```

This is not an order instruction. It is context for manual review.

---

## 32. Tests

Need tests:

```text
test_activity.py
- turnover rank
- rank changes
- volume spike

test_metrics.py
- price change
- volume spike
- candle sorting ASC
- spread calculation

test_derivatives.py
- OI change
- funding thresholds
- price/OI regime classification

test_breakout.py
- swing high detection
- resistance clustering
- fresh breakout
- confirmed breakout
- retest held
- failed breakout
- overextended after breakout

test_rsi.py
- RSI calculation
- RSI warnings

test_scoring.py
- WATCH/HOT/BREAKOUT_HOT/VERY_HOT
- risk penalties
- OPN-like scenario should produce BREAKOUT_HOT or VERY_HOT with warnings

test_state.py
- state transitions
- cooldown by same state
- immediate alert on state transition

test_bybit_connector.py
- mocked instruments-info pagination
- mocked tickers
- mocked kline reverse order sorting
- mocked open-interest

test_outcomes.py
- signal outcome calculations
- MFE/MAE calculations
- report grouping by signal level
```

---

## 33. README requirements

README must explain:

```text
1. What the project does
2. What it does NOT do
3. Why exchange API keys are not needed
4. How to install dependencies
5. How to create Telegram bot token
6. How to get Telegram chat id
7. How to fill .env
8. How to configure config.yaml
9. How to run dry-run
10. How to run one scan
11. How to run continuous mode
12. What WATCH/HOT/BREAKOUT_HOT/VERY_HOT mean
13. How score works
14. How breakout detection works
15. How cooldown works
16. What to do if Bybit API is blocked locally
17. How to move to VPS
18. Why alerts are not financial advice
```

---

## 34. AGENTS.md requirements

```markdown
# AGENTS.md

## Project goal

Build and maintain a local-first crypto long momentum + breakout scanner.

## Non-negotiable rules

- Do not add exchange trading API keys.
- Do not implement order placement.
- Do not implement auto-trading.
- Use public market data only.
- All alerts must say "open chart / check setup", never "buy now".
- Prefer simple, readable Python.
- Keep Bybit MVP working before adding Binance.
- Update GOAL.md when product logic changes.

## Strategy logic

The scanner should combine:

- activity rank
- price momentum
- volume/turnover spike
- open interest / funding context
- 4H resistance breakout context
- narrative/event boosts
- risk warnings

## Important

The scanner is a radar, not a trading system.
```

---

## 35. Acceptance criteria v2

Project is accepted if:

```text
1. python -m app.main --once --dry-run runs locally.
2. It fetches live Bybit public data.
3. It builds the USDT linear perpetual universe.
4. It ranks symbols by activity.
5. It calculates price/volume/OI/funding metrics.
6. It detects 4H resistance zones and breakout states.
7. It calculates RSI warnings.
8. It calculates total score with layer breakdown.
9. It emits WATCH/HOT/BREAKOUT_HOT/VERY_HOT signals.
10. It logs rejected candidates with reasons.
11. It formats Telegram alerts with reasons and warnings.
12. It has cooldown and state transitions.
13. It creates SQLite DB automatically.
14. It requires no exchange API keys.
15. It contains no order placement code.
16. Tests run through pytest.
17. README explains setup and usage.
18. Bybit instruments-info pagination is implemented.
19. Bybit kline reverse order is handled.
20. Expensive endpoints are called only for enriched candidates, not every symbol.
21. Outcome tracking/reporting exists or is explicitly marked as Phase 4.5 TODO.
```

---

## 36. One-prompt instruction for Codex

Use this prompt with the file:

```text
Read GOAL.md carefully and implement the project according to it.

Build a local-first Python crypto long momentum + breakout scanner.

Use public market data only. Do not add exchange API keys. Do not implement order placement or auto-trading.

Start with Bybit Futures MVP. Implement two-stage scanning: cheap all-market ticker scan first, then enrich only top candidates. Implement activity rank, momentum metrics, volume spike, OI/funding context, 4H resistance breakout detection, RSI warnings, score system, state machine, SQLite, dry-run and Telegram alerts. Handle Bybit instruments pagination and reverse-ordered klines correctly.

Binance is optional Phase 2 and should not block MVP.

After implementation:
1. update README.md with setup/run instructions
2. create .env.example
3. create config.yaml and config/*.yaml examples
4. add tests
5. run pytest if possible
6. run python -m app.main --once --dry-run if possible
7. summarize what was implemented and what remains
```

---

## 37. Final product principle

The bot should help recreate part of the OPN-style edge:

```text
not insider knowledge,
not magic predictions,
not manual watchlist of 1000 coins,

but:
market-wide scan
→ unusual activity
→ derivative confirmation
→ chart breakout
→ narrative boost
→ risk warnings
→ fast human review
```

That is the edge we are trying to automate.


---

## 38. Recheck addendum v2.2 — is this enough to detect high-quality long setups?

After reviewing the design again, the information is enough to build a strong **long setup detector**, not a guaranteed trading system.

The project has enough market inputs to detect OPN-style situations:

```text
activity rank
+ price acceleration
+ volume/turnover spike
+ OI/funding confirmation
+ BTC background
+ 4H resistance breakout
+ RSI/overextension warnings
+ narrative/event boost
+ state machine
+ Telegram alert for manual review
```

But one extra layer is mandatory if we want to improve from “interesting alerts” to “potentially high-quality setups”: **signal outcome tracking and calibration**.

Without outcome tracking the bot can be implemented, but we will not know whether the alerts are actually good.

---

## 39. Staged scanning policy — avoid slow/noisy API usage

Do not fetch heavy data for every symbol on every scan.

Use a staged pipeline.

### Stage 1 — full universe, cheap scan

Every scan cycle:

```text
1. Fetch all Bybit linear USDT tickers.
2. Rank all symbols by turnover24h, volume24h, price_change_1h, price_change_24h.
3. Calculate cheap metrics available from tickers:
   - last price
   - 1h change from prevPrice1h
   - 24h change
   - turnover/volume rank
   - current OI
   - current funding
   - spread from bid/ask
4. Select preliminary candidates only.
```

Preliminary candidate condition:

```text
symbol passes liquidity/spread filters
AND at least one of:
- top 30 by turnover rank
- 15m/1h momentum detected from stored snapshots
- 24h price change rank in top 30
- OI/turnover changed sharply since previous scan
```

### Stage 2 — candidate deep scan

Only for preliminary candidates:

```text
1. Fetch 1m/5m/15m/1h candles.
2. Calculate volume spike and short-term price acceleration.
3. Fetch OI history if needed.
4. Recalculate score.
```

### Stage 3 — breakout scan

Only for candidates with sufficient score or activity:

```text
1. Fetch 4H candles.
2. Detect resistance zones.
3. Detect breakout/retest/fakeout states.
4. Calculate chart score.
```

### Stage 4 — high-conviction confirmation

Only for HOT/BREAKOUT candidates:

```text
1. Fetch orderbook depth/spread if enabled.
2. Fetch funding history if enabled.
3. Fetch long/short ratio if endpoint works reliably.
4. Add final warnings and reasons.
```

This matters because polling klines/OI/orderbooks for every symbol every minute is slow, noisy and unnecessary.

---

## 40. Signal outcome tracking — required for real quality

Every sent alert must be tracked after the fact.

Add table `signal_outcomes`.

### 40.1. Why this matters

The first version may generate many “cool-looking” alerts.

We need to answer:

```text
Which alerts actually moved after the signal?
Which alerts immediately dumped?
Which layers mattered most?
Was BREAKOUT_HOT better than ACTIVITY_SPIKE?
Were high RSI warnings actually dangerous or acceptable?
Was funding > 0.05% too late?
```

### 40.2. Outcome windows

For every signal, check future market behavior after:

```text
5m
15m
30m
1h
2h
4h
8h
24h
```

### 40.3. Outcome metrics

For each signal save:

```text
entry_reference_price       # price at alert time
future_return_5m_pct
future_return_15m_pct
future_return_30m_pct
future_return_1h_pct
future_return_2h_pct
future_return_4h_pct
future_return_8h_pct
future_return_24h_pct

mfe_1h_pct                  # maximum favorable excursion after signal
mae_1h_pct                  # maximum adverse excursion after signal
mfe_4h_pct
mae_4h_pct
mfe_24h_pct
mae_24h_pct

time_to_mfe_4h_minutes
time_to_mae_4h_minutes

hit_plus_3_before_minus_2   # did +3% happen before -2%?
hit_plus_5_before_minus_3
hit_plus_10_before_minus_5

fakeout_flag                # breakout failed after alert
retest_held_flag            # broken resistance retest held
continued_without_retest    # momentum continued without clean retest
```

### 40.4. SQLite schema

```sql
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_timestamp_ms INTEGER NOT NULL,
    evaluated_at_ms INTEGER NOT NULL,
    entry_reference_price REAL NOT NULL,

    future_return_5m_pct REAL,
    future_return_15m_pct REAL,
    future_return_30m_pct REAL,
    future_return_1h_pct REAL,
    future_return_2h_pct REAL,
    future_return_4h_pct REAL,
    future_return_8h_pct REAL,
    future_return_24h_pct REAL,

    mfe_1h_pct REAL,
    mae_1h_pct REAL,
    mfe_4h_pct REAL,
    mae_4h_pct REAL,
    mfe_24h_pct REAL,
    mae_24h_pct REAL,

    time_to_mfe_4h_minutes REAL,
    time_to_mae_4h_minutes REAL,

    hit_plus_3_before_minus_2 INTEGER,
    hit_plus_5_before_minus_3 INTEGER,
    hit_plus_10_before_minus_5 INTEGER,

    fakeout_flag INTEGER,
    retest_held_flag INTEGER,
    continued_without_retest INTEGER,

    notes TEXT,
    UNIQUE(signal_id)
);
```

### 40.5. Outcome evaluation job

Add a scheduled job:

```text
Every 5 minutes:
1. Find signals without final 24h outcome.
2. Fetch needed candles from SQLite or exchange API.
3. Update partial outcomes.
4. Finalize outcomes after 24h.
```

---

## 41. Daily calibration report

Add a command:

```bash
python -m app.main --report today
python -m app.main --report 7d
```

Report should print:

```text
Total signals
Signals by type
Average MFE/MAE
Median MFE/MAE
% hit +3 before -2
% hit +5 before -3
% fakeouts
Best signal types
Worst signal types
Most useful score layers
Symbols with repeated false positives
Threshold suggestions
```

This report is what turns the scanner from a toy into a tuning machine.

---

## 42. Alert quality grading

Each alert should include a grade separate from score.

```text
Grade A — activity + OI + volume + 4H breakout + BTC ok + risk acceptable
Grade B — strong activity/momentum, but chart context incomplete
Grade C — interesting activity, but high risk / overextended / no level
```

Rules:

```text
Grade A requires:
- score >= 80
- chart_state in FRESH_BREAKOUT / CONFIRMED_BREAKOUT / RETEST_HELD
- activity_score >= 12
- derivatives_score >= 8
- no danger funding
- no major spread warning

Grade B requires:
- score >= 65
- at least activity + momentum confirmation

Grade C:
- WATCH-level signal or too many warnings
```

Telegram alert should show:

```text
Grade: A/B/C
This is not a BUY signal. Open chart and validate manually.
```

---

## 43. Entry reference, invalidation reference, and chase warning

The bot should not tell the user where to buy.

But for manual review it can show objective reference levels:

```text
breakout_zone: 0.215–0.225
current_price: 0.2403
distance_above_zone: +6.8%
possible_invalidation_reference: below zone_mid / zone_low
chase_warning: current price is far above breakout zone
```

Never write:

```text
Entry: buy here
Stop: put stop here
Take profit: sell here
```

Use:

```text
Reference zone
Invalidation reference
Manual review required
```

---

## 44. Final answer on sufficiency

The GOAL is sufficient to implement a serious detector if Codex follows it.

Minimum useful version:

```text
Bybit REST
+ staged scanning
+ activity rank
+ momentum/volume/OI/funding
+ 4H breakout engine
+ state machine
+ Telegram alerts
+ SQLite storage
```

Serious version:

```text
Minimum version
+ outcome tracking
+ calibration report
+ orderbook/funding history/long-short ratio
+ Binance cross-exchange confirmation
+ WebSocket speed layer
```

Do not expect the first thresholds to be perfect. The first edge is speed and structure. The real edge improves after outcome tracking and threshold calibration.

---

## 38. Final audit patch v2.2 — достаточно ли информации для “пиздатых long setups”

### 38.1. Итог аудита

Информации в этом файле достаточно, чтобы Codex реализовал **рабочий long scanner / alert bot**, который находит OPN-style ситуации:

```text
activity spike
+ price momentum
+ volume / turnover acceleration
+ OI / funding context
+ 4H breakout / retest context
+ narrative / event boost
+ risk warnings
```

Но для формулировки “потенциально пиздатая сделка” одного score мало. Нужен еще слой **setup quality**:

```text
где не поздно;
где есть понятная зона входа;
где понятно, где сетап сломан;
где есть room-to-run до следующей зоны;
где примерный risk/reward не мусорный;
где после сигналов можно измерять фактический outcome.
```

Поэтому v2.2 добавляет обязательный слой:

```text
Target / invalidation / room-to-run / post-alert outcome tracking.
```

Бот всё еще не должен говорить BUY. Но он должен подсвечивать, насколько сетап **технически пригоден для ручного отсмотра**.

---

## 39. Setup quality layer — entry zone, invalidation, target, room-to-run

### 39.1. Зачем это нужно

Сигнал “монета летит и пробила уровень” может быть уже поздним. Чтобы отсечь плохие догонялки, бот должен оценить:

```text
1. где была пробитая зона;
2. насколько далеко цена уже ушла от зоны;
3. где логичная invalidation;
4. где ближайшая зона сопротивления / target zone;
5. хватает ли room-to-run;
6. есть ли минимальный estimated R/R.
```

Это не торговая рекомендация. Это аналитика для ручного решения.

### 39.2. New model: SetupPlan

```python
class SetupPlan:
    exchange: str
    symbol: str
    setup_type: str  # BREAKOUT_CONTINUATION, BREAKOUT_RETEST, MOMENTUM_WATCH
    current_price: float

    entry_context: str  # already_above_breakout, retest_zone, approaching_level
    breakout_zone_low: float | None
    breakout_zone_high: float | None

    suggested_watch_zone_low: float | None
    suggested_watch_zone_high: float | None

    invalidation_price: float | None
    invalidation_reason: str | None
    distance_to_invalidation_pct: float | None

    target_zone_low: float | None
    target_zone_high: float | None
    target_reason: str | None
    room_to_target_pct: float | None

    estimated_rr: float | None
    chase_risk: str  # LOW, MEDIUM, HIGH
```

### 39.3. Invalidation logic

Для breakout continuation:

```text
invalidation_price = min(
  breakout_zone_low,
  breakout_zone_high - ATR14_4H * 0.5
)
```

Для retest setup:

```text
invalidation_price = breakout_zone_low - ATR14_4H * 0.25
```

Для pure momentum без уровня:

```text
invalidation_price = None
warning: "no clean invalidation level — weaker setup"
```

### 39.4. Target / next resistance detection

После пробоя текущей resistance zone нужно искать следующую зону сверху.

Алгоритм:

```text
1. Use 4H candles and optional 1D candles.
2. Detect swing highs above current price.
3. Cluster them into resistance zones.
4. Pick the nearest valid resistance zone above current price.
5. If no historical zone exists, use fallback targets:
   - round number levels;
   - ATR extension;
   - measured move from range height.
```

Target priority:

```text
1. nearest 4H/1D resistance above current price;
2. psychological round levels;
3. measured move = breakout_zone_high + prior_range_height;
4. ATR extension = current_price + ATR14_4H * 1.5–2.5.
```

### 39.5. Room-to-run

```text
room_to_target_pct = (target_zone_low - current_price) / current_price * 100
```

Warnings:

```text
room_to_target_pct < 4%  → "little room to next resistance"
room_to_target_pct < distance_to_invalidation_pct → "bad asymmetry"
```

### 39.6. Estimated R/R

```text
risk_pct = abs(current_price - invalidation_price) / current_price * 100
reward_pct = abs(target_zone_low - current_price) / current_price * 100
estimated_rr = reward_pct / risk_pct
```

Scoring:

```text
+8 if estimated_rr >= 2.0
+5 if estimated_rr >= 1.5
0  if estimated_rr unknown
-8 if estimated_rr < 1.2
-12 if room_to_target_pct < 4%
```

Config:

```yaml
setup_quality:
  enabled: true
  min_room_to_target_pct: 5.0
  min_estimated_rr_for_hot: 1.5
  min_estimated_rr_for_breakout_hot: 1.8
  invalidation_atr_mult_breakout: 0.5
  invalidation_atr_mult_retest: 0.25
  target_atr_extension_mult: 2.0
  round_level_detection: true
```

### 39.7. Chase risk

```text
LOW:
  price is within 0–3% above breakout zone OR retest is happening

MEDIUM:
  price is 3–8% above breakout zone

HIGH:
  price is >8–12% above breakout zone or RSI/funding/24h move is hot
```

If chase risk is HIGH, alert can still be sent, but level should be capped unless everything else is extremely strong.

---

## 40. Post-alert outcome tracking

### 40.1. Why

Без outcome tracking бот быстро превратится в красивую игрушку. Нужно измерять, какие сигналы реально дают continuation.

После каждого alert сохранять future outcome:

```text
15m, 1h, 4h, 12h, 24h windows
max favorable excursion (MFE)
max adverse excursion (MAE)
close-to-close return
whether target was touched
whether invalidation was touched
whether +3% happened before -3%
whether +5% happened before -5%
```

### 40.2. signal_outcomes table

```sql
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    evaluated_at_ms INTEGER NOT NULL,
    window TEXT NOT NULL,
    entry_reference_price REAL NOT NULL,
    max_price REAL,
    min_price REAL,
    close_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    close_return_pct REAL,
    target_touched INTEGER,
    invalidation_touched INTEGER,
    plus_3_before_minus_3 INTEGER,
    plus_5_before_minus_5 INTEGER,
    details_json TEXT,
    UNIQUE(signal_id, window)
);
```

### 40.3. Outcome evaluator

Add a scheduled evaluator:

```text
Every scan cycle:
1. find signals without completed outcome windows;
2. fetch required candles;
3. compute outcome metrics;
4. update signal_outcomes;
5. print daily summary in logs.
```

### 40.4. Success metrics

README should include how to analyze:

```text
- average MFE by signal_type;
- average MAE by signal_type;
- % signals with +5% before -3%;
- % BREAKOUT_RETEST better than FRESH_BREAKOUT;
- best thresholds for volume/OI/funding/chase risk.
```

This is critical for tuning the scanner.

---

## 41. “Potentially good trade” rubric

A signal is not automatically a good trade. For a signal to be classified as a high-quality manual-review setup, require:

```text
1. Score >= HOT threshold.
2. Activity score confirms unusual attention.
3. Momentum score confirms price expansion.
4. Volume/turnover confirms the move.
5. OI is rising OR the system explicitly labels it as short squeeze / covering.
6. Funding is not in danger zone.
7. Chart state is FRESH_BREAKOUT, CONFIRMED_BREAKOUT, RETESTING or RETEST_HELD.
8. Chase risk is not HIGH, or alert must explicitly say “do not chase”.
9. Room-to-target is acceptable.
10. Estimated R/R is at least 1.5 when target/invalidation are available.
11. BTC background is not actively hostile.
12. No critical event risk without warning.
```

Signal labels:

```text
HIGH_QUALITY_REVIEW
AGGRESSIVE_MOMENTUM_REVIEW
WAIT_FOR_RETEST
TOO_LATE_CHASE_WARNING
NO_CLEAN_SETUP
```

Telegram must use these labels instead of BUY/SELL.

---

## 42. Rate limits, batching and request strategy

### 42.1. Why this matters

If the bot fetches deep candles, OI history and orderbook for every symbol every minute, it will be slow and may hit rate limits.

Use staged scanning.

### 42.2. Stage 1 — cheap global scan

Every scan interval:

```text
1. Fetch all instruments only from cache; refresh every 30–60 min.
2. Fetch all tickers in one call where supported.
3. Rank symbols by turnover/volume/change.
4. Select candidates using cheap ticker fields.
```

Bybit instruments-info must handle pagination because linear symbols can exceed one default page.

### 42.3. Stage 2 — candidate enrichment

Only for top candidates:

```text
- fetch 1m/5m/15m/1h/4h candles;
- fetch OI history;
- fetch funding history if needed;
- fetch orderbook if candidate score is already promising;
- fetch long/short ratio only if enabled.
```

Suggested limits:

```yaml
scanner:
  max_enriched_candidates_per_cycle: 40
  max_breakout_candidates_per_cycle: 30
  instruments_refresh_minutes: 60
  ticker_refresh_seconds: 60
  expensive_data_cache_seconds: 180
```

### 42.4. Concurrency limits

```yaml
http:
  timeout_seconds: 12
  max_retries: 3
  backoff_seconds: [0.5, 1.0, 2.0]
  max_concurrent_requests: 8
  max_concurrent_requests_per_exchange: 5
```

Implementation:

```text
Use asyncio.Semaphore for concurrent HTTP calls.
Do not let one exchange or one symbol block the whole cycle.
```

---

## 43. Data correctness rules

### 43.1. Closed candle vs live candle

For breakout detection, distinguish:

```text
live_current_candle
latest_closed_candle
```

`FRESH_BREAKOUT` can use live price/current candle.
`CONFIRMED_BREAKOUT` must use latest closed 4H candle.

### 43.2. Candle ordering

Always sort candles ASC by timestamp. Bybit returns kline list in reverse order.

### 43.3. Units

Normalize:

```text
USDT turnover = quote currency turnover;
base volume = coin amount;
openInterest = contract/base amount depending on exchange;
openInterestValue = USD/USDT value when available;
```

For cross-symbol ranking, prefer:

```text
turnover_24h
open_interest_value
quote_volume
```

not raw base volume.

### 43.4. Missing data

If a metric is missing:

```text
- do not crash;
- do not award positive points for it;
- add warning if it is important;
- store null in DB.
```

---

## 44. Market cap / FDV / listing-age context

### 44.1. Why

The OPN case was powerful partly because derivatives turnover was huge relative to market cap / attention baseline.

Exchange APIs usually do not provide reliable circulating market cap and FDV. Therefore this is optional but valuable.

### 44.2. MVP approach

Manual config first:

```yaml
symbol_tags:
  OPNUSDT:
    market_cap_bucket: small
    listing_age_bucket: fresh
    sectors: [prediction_markets]
    tags: [binance_launchpool, bybit_perp]
```

### 44.3. Later external provider

Optional providers:

```text
CoinGecko
CoinMarketCap
CryptoRank
Tokenomist / unlock source
```

Any external provider must be cached aggressively and should not block scanner if unavailable.

### 44.4. Listing age

Use exchange instrument `launchTime` when available.

Scoring idea:

```text
+3 if listing age is 1–120 days and liquidity is healthy
+2 if symbol has Launchpool/Alpha/major listing tag
warning if pre-market / abnormal contract status
```

---

## 45. Backtest / replay minimum acceptance

Before trusting live alerts, implement at least a simple replay mode:

```bash
python -m app.main replay --exchange bybit --symbol OPNUSDT --start 2026-06-01 --end 2026-06-05
```

Replay should answer:

```text
1. Did the scanner detect activity before or near the public call?
2. Did it identify the 4H resistance zone?
3. Did it label the move as FRESH_BREAKOUT / BREAKOUT_HOT?
4. Did it warn about RSI / 24h extension?
5. What was MFE/MAE after alert?
```

If historical API depth is insufficient, use saved live snapshots going forward. For older examples, manual candle CSV import is acceptable.

Acceptance condition for OPN-like scenarios:

```text
The scanner should fire at least WATCH before breakout and BREAKOUT_HOT near the breakout, not only after the move is completely overextended.
```

---

## 46. Updated Codex instruction after audit

Use this prompt with the file:

```text
Read GOAL.md fully. Implement the Bybit MVP first, but include the v2.2 audit additions that make this a useful long setup scanner rather than just a pump alert bot.

Public market data only. No exchange API keys. No order placement. No auto-trading.

The MVP must include:
- Bybit instruments/tickers/kline/open-interest public data;
- paginated instruments-info;
- staged scan pipeline: cheap global scan → candidate enrichment;
- activity rank;
- price/volume/turnover momentum;
- OI/funding context;
- 4H resistance breakout detection;
- RSI warnings;
- setup quality layer: entry context, invalidation, next target, room-to-run, estimated R/R, chase risk;
- state machine and cooldown;
- Telegram alerts that say “open chart / check setup”, never “buy now”;
- SQLite storage;
- signal_outcomes table and basic post-alert outcome tracking;
- --once --dry-run mode;
- tests for scoring, breakout, setup quality and outcome tracking.

Binance and WebSocket are Phase 2/3 and should not block the Bybit REST MVP.
```

