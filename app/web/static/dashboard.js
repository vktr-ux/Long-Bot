const tokenFromUrl = new URLSearchParams(window.location.search).get("token");
if (tokenFromUrl) localStorage.setItem("dashboardToken", tokenFromUrl);
const token = localStorage.getItem("dashboardToken") || "";

let latestTrades = [];
let latestSettings = null;
let latestTradingSettings = null;
let settingsEditorDirty = false;

function money(value, digits = 4) {
  const num = Number(value || 0);
  return `${num >= 0 ? "" : "-"}$${Math.abs(num).toFixed(digits)}`;
}

function pct(value) {
  const num = Number(value || 0);
  return `${num.toFixed(2)}%`;
}

function cls(value) {
  return Number(value || 0) >= 0 ? "pos" : "neg";
}

function marginUsdt(row) {
  const stored = Number(row.margin_usdt || 0);
  if (Number.isFinite(stored) && stored > 0) return stored;
  const notional = Number(row.notional_usdt || 0);
  const leverage = Number(row.leverage || 0);
  return leverage > 0 ? notional / leverage : 0;
}

function riskDetails(row) {
  return row?.details?.risk || {};
}

function plannedLossUsdt(row) {
  const risk = riskDetails(row);
  const lossPct = Number(risk.loss_sizing_pct || 0);
  const notional = Number(row.notional_usdt || 0);
  return lossPct > 0 ? notional * lossPct / 100 : 0;
}

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

async function api(path, options = {}) {
  const relativePath = path.replace(/^\/+/, "");
  const headers = { "X-Dashboard-Token": token, ...(options.headers || {}) };
  const response = await fetch(relativePath, { ...options, headers });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = payload.detail ? ` ${payload.detail}` : "";
    } catch (_error) {
      detail = "";
    }
    throw new Error(`${response.status} ${path}${detail}`);
  }
  return response.json();
}

async function apiText(path) {
  const relativePath = path.replace(/^\/+/, "");
  const response = await fetch(relativePath, { headers: { "X-Dashboard-Token": token } });
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.text();
}

function writeApi(path, body = {}) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function metric(label, value, klass = "") {
  return `<div class="metric"><span>${label}</span><strong class="${klass}">${value}</strong></div>`;
}

function renderMetrics(summary) {
  document.getElementById("metrics").innerHTML = [
    metric("Equity / Start", `${money(summary.current_equity_usdt)} / ${money(summary.starting_balance_usdt, 2)}`, cls(summary.current_equity_usdt - summary.starting_balance_usdt)),
    metric("Net PnL", money(summary.net_pnl_usdt), cls(summary.net_pnl_usdt)),
    metric("ROI", pct(summary.roi_pct), cls(summary.roi_pct)),
    metric("Today PnL", money(summary.today_pnl_usdt), cls(summary.today_pnl_usdt)),
    metric("Open Positions", `${summary.open_positions ?? 0} / ${summary.max_open_positions ?? 0}`),
    metric("Trades Today", summary.trades_today ?? 0),
    metric("Win Rate", pct(summary.win_rate_pct)),
    metric("Max Drawdown", pct(summary.max_drawdown_pct), "neg"),
    metric("Total Fees", money(summary.total_fees_usdt)),
    metric("Settings", `v${summary.active_settings_version ?? "?"} ${summary.active_settings_hash ?? ""}`),
  ].join("");
  document.getElementById("tracking").innerHTML = `
    <p>Trades: <strong>${summary.trades}</strong></p>
    <p>Profit factor: <strong>${Number(summary.profit_factor || 0).toFixed(2)}</strong></p>
    <p>Avg win/loss: <strong>${money(summary.avg_win_usdt)} / ${money(summary.avg_loss_usdt)}</strong></p>
    <p>Stop / BE+ / trailing: <strong>${summary.stopout_count} / ${summary.breakeven_plus_count} / ${summary.trailing_count}</strong></p>
  `;
}

function table(target, headers, rows, emptyText = "No rows") {
  const head = `<thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>`;
  const body = `<tbody>${rows.join("") || `<tr><td colspan="${headers.length}" class="muted">${escapeHtml(emptyText)}</td></tr>`}</tbody>`;
  document.getElementById(target).innerHTML = head + body;
}

function renderOpen(rows) {
  table("open-table", ["Time", "Symbol", "Side", "Entry", "Qty", "Notional", "Margin", "Lev", "Mode", "Liq", "SL", "SL%", "TP1", "TP%", "BE+", "Risk", "Cost", "UPnL", "MFE", "MAE", "Age", "Settings", "Close"], rows.map((row) => {
    const age = Math.max(0, (Date.now() - row.opened_at_ms) / 1000).toFixed(0);
    const details = row.details || {};
    const risk = riskDetails(row);
    const liq = Number(details.liquidation_price || 0);
    return `<tr>
      <td>${new Date(row.opened_at_ms).toLocaleTimeString()}</td><td>${row.symbol}</td><td>${row.direction}</td><td>${Number(row.entry_price).toFixed(6)}</td>
      <td>${Number(row.qty).toFixed(6)}</td><td>${money(row.notional_usdt, 2)}</td><td>${money(marginUsdt(row), 2)}</td><td>${Number(row.leverage || 0).toFixed(0)}x</td><td>${escapeHtml(details.margin_mode || "isolated")}</td><td title="${escapeHtml(details.liquidation_source || "")}">${liq ? liq.toFixed(6) : "-"}</td>
      <td>${Number(row.current_sl_price).toFixed(6)}</td><td>${pct(risk.initial_sl_pct)}</td><td>${Number(row.tp1_price).toFixed(6)}</td><td>${pct(details.tp1_trigger_pct)}</td>
      <td>${Number(details.be_plus_price || 0).toFixed(6)}</td><td title="SL + roundtrip fee/slippage + stop buffer">${money(plannedLossUsdt(row))} / ${pct(risk.loss_sizing_pct)}</td><td>${pct(risk.cost_pct)}</td>
      <td class="${cls(row.unrealized_pnl_usdt)}">${money(row.unrealized_pnl_usdt)}</td>
      <td>${money(row.mfe_usdt)}</td><td>${money(row.mae_usdt)}</td><td>${age}s</td><td>v${row.strategy_config_version || details.strategy_config_version || "?"}</td>
      <td><button class="manual-close" data-position-id="${row.id}">Close</button></td>
    </tr>`;
  }));
}

function renderTrades(rows, settingsMeta = latestTradingSettings) {
  latestTrades = rows;
  const scope = document.getElementById("history-scope");
  if (scope) {
    const version = settingsMeta?.version ? `v${settingsMeta.version}` : "active version";
    const hash = settingsMeta?.settings_hash_short ? ` / ${settingsMeta.settings_hash_short}` : "";
    scope.textContent = `Showing closed trades for ${version}${hash}. Older versions are archived in Impact.`;
  }
  table("history-table", ["Time", "Symbol", "Side", "Entry", "Exit", "Qty", "Notional", "Margin", "Lev", "Mode", "Liq", "Gross", "Entry Fee", "Exit Fee", "Total Fees", "Slippage", "Funding", "Net", "ROI", "Reason", "Duration", "MFE", "MAE", "Settings"], rows.map((row) => `
    <tr>
      <td>${new Date(row.exit_time_ms).toLocaleString()}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(row.direction)}</td>
      <td>${Number(row.entry_price).toFixed(6)}</td><td>${Number(row.exit_price).toFixed(6)}</td>
      <td>${Number(row.qty).toFixed(6)}</td><td>${money(row.notional_usdt, 2)}</td><td>${money(marginUsdt(row), 2)}</td><td>${Number(row.leverage || 0).toFixed(0)}x</td><td>${escapeHtml(row.margin_mode || "isolated")}</td><td>${Number(row.liquidation_price || 0) ? Number(row.liquidation_price).toFixed(6) : "-"}</td><td class="${cls(row.gross_pnl_usdt)}">${money(row.gross_pnl_usdt)}</td>
      <td>${money(row.entry_fee_usdt)}</td><td>${money(row.exit_fee_usdt)}</td><td>${money(row.total_fees_usdt || row.fees_usdt)}</td>
      <td>${money(row.slippage_usdt)}</td><td>${money(row.funding_usdt)}</td><td class="${cls(row.net_pnl_usdt)}">${money(row.net_pnl_usdt)}</td><td>${pct(row.roi_pct)}</td>
      <td>${escapeHtml(row.exit_reason)}</td><td>${Number(row.duration_seconds).toFixed(0)}s</td><td>${money(row.mfe_usdt)}</td><td>${money(row.mae_usdt)}</td><td>v${row.strategy_config_version || "legacy"}</td>
    </tr>`), "No closed trades in the active version yet. Check Impact for older versions.");
}

function renderSignals(rows) {
  table("signals-table", ["Time", "Symbol", "Direction", "Label", "Score", "Status", "Settings", "Reasons", "Warnings"], rows.map((row) => `
    <tr>
      <td>${new Date(row.created_at_ms).toLocaleString()}</td><td>${row.symbol}</td><td>${row.direction}</td>
      <td>${row.classifier_label}</td><td>${row.score}</td><td>${row.status}</td>
      <td>v${row.strategy_config_version || "legacy"}</td>
      <td>${(row.reasons || []).slice(0, 3).join("; ")}</td><td>${(row.warnings || []).slice(0, 3).join("; ")}</td>
    </tr>`));
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "-";
}

function renderImpactReasonRows(rows = []) {
  if (!rows.length) return `<p class="muted">No exit breakdown yet</p>`;
  return `
    <table class="mini-table">
      <thead><tr><th>Reason</th><th>Trades</th><th>Win</th><th>Net</th><th>Gross</th><th>Fees</th><th>Slip</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.exit_reason)}</td><td>${row.trades}</td><td>${pct(row.win_rate_pct)}</td>
          <td class="${cls(row.net_pnl_usdt)}">${money(row.net_pnl_usdt)}</td>
          <td class="${cls(row.gross_pnl_usdt)}">${money(row.gross_pnl_usdt)}</td>
          <td>${money(row.fees_usdt)}</td><td>${money(row.slippage_usdt)}</td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
}

function renderImpactTradeRows(rows = []) {
  if (!rows.length) return `<p class="muted">No trades in this version</p>`;
  return `
    <table class="mini-table">
      <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Net</th><th>ROI</th><th>Notional</th><th>Margin</th><th>Lev</th><th>Liq</th><th>MFE</th><th>MAE</th><th>Reason</th><th>Duration</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr>
          <td>${formatTime(row.exit_time_ms)}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(row.direction)}</td>
          <td class="${cls(row.net_pnl_usdt)}">${money(row.net_pnl_usdt)}</td><td>${pct(row.roi_pct)}</td>
          <td>${money(row.notional_usdt, 2)}</td><td>${money(marginUsdt(row), 2)}</td><td>${Number(row.leverage || 0).toFixed(0)}x</td><td>${Number(row.liquidation_price || 0) ? Number(row.liquidation_price).toFixed(6) : "-"}</td>
          <td>${money(row.mfe_usdt)}</td><td>${money(row.mae_usdt)}</td><td>${escapeHtml(row.exit_reason)}</td>
          <td>${Number(row.duration_seconds || 0).toFixed(0)}s</td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
}

function impactStat(label, value, klass = "") {
  return `<div class="impact-stat"><span>${label}</span><strong class="${klass}">${value}</strong></div>`;
}

function renderImpact(payload) {
  const target = document.getElementById("impact-list");
  if (!target) return;
  const versions = Array.isArray(payload?.versions) ? payload.versions : [];
  if (!versions.length) {
    target.innerHTML = `<p class="muted">No settings versions yet</p>`;
    return;
  }
  target.innerHTML = versions.map((row) => {
    const stats = row.stats || {};
    const title = row.version === "legacy" ? "legacy" : `v${row.version}`;
    const active = row.is_active ? `<span class="pill">active</span>` : `<span class="pill muted-pill">archived</span>`;
    const period = row.first_exit_time_ms
      ? `${formatTime(row.first_exit_time_ms)} - ${formatTime(row.last_exit_time_ms)}`
      : "No closed trades";
    const comment = row.comment ? escapeHtml(row.comment) : "No comment";
    const openAttr = row.is_active ? " open" : "";
    return `
      <details class="impact-version${row.is_active ? " active" : ""}"${openAttr}>
        <summary class="impact-summary">
          <div class="impact-summary-main">
            <div>
              <div class="impact-version-title">${title} ${active}</div>
              <div class="impact-comment">${comment}</div>
            </div>
            <div class="impact-meta">
              <span>${escapeHtml(row.settings_hash_short || "no hash")}</span>
              <span>Created ${formatTime(row.created_at_ms)}</span>
              <span>${period}</span>
            </div>
          </div>
          <div class="impact-stats">
            ${impactStat("Trades", stats.trades || 0)}
            ${impactStat("Net", money(stats.net_pnl_usdt), cls(stats.net_pnl_usdt))}
            ${impactStat("ROI", pct(stats.roi_pct), cls(stats.roi_pct))}
            ${impactStat("Win", pct(stats.win_rate_pct))}
            ${impactStat("PF", Number(stats.profit_factor || 0).toFixed(2))}
            ${impactStat("Fees", money(stats.total_fees_usdt))}
          </div>
        </summary>
        <div class="impact-body">
          <section class="impact-section">
            <h3>Exit Breakdown</h3>
            ${renderImpactReasonRows(row.by_exit_reason)}
          </section>
          <section class="impact-section">
            <h3>Trades</h3>
            <div class="impact-trades-scroll">${renderImpactTradeRows(row.trades)}</div>
          </section>
        </div>
      </details>`;
  }).join("");
}

const settingsHelp = [
  { group: "Общее", path: "trading_mode", about: "Режим исполнения. Сейчас должен оставаться paper, чтобы бот не отправлял реальные ордера.", values: "paper - только симуляция; testnet/live зарезервированы и требуют защитных лимитов." },
  { group: "Общее", path: "risk_profile", about: "Профиль риска, который описывает назначение текущего набора настроек.", values: "exploration_paper - исследование на paper; live_safety - более строгий профиль для будущего live/testnet." },

  { group: "Сканер", path: "scanner.scan_interval_seconds", about: "Пауза между полными циклами поиска новых входов.", values: "1-3600 секунд." },
  { group: "Сканер", path: "scanner.monitor_interval_seconds", about: "Пауза между проверками уже открытых paper-позиций.", values: "0.5-60 секунд." },
  { group: "Сканер", path: "scanner.max_enriched_candidates_per_cycle", about: "Сколько лучших кандидатов за цикл можно дополнительно обогащать REST-данными.", values: "1-250. Чем выше, тем больше API-нагрузка." },
  { group: "Сканер", path: "scanner.max_concurrent_requests", about: "Сколько REST-запросов бот может выполнять параллельно при обогащении кандидатов.", values: "1-20. На VPS лучше держать умеренно." },
  { group: "Сканер", path: "scanner.top_activity_rank_candidate", about: "Размер активного watchlist из самых активных Binance USD-M символов.", values: "1-500. Сейчас целевой режим - до 250 символов." },
  { group: "Сканер", path: "scanner.attention_scheduler_enabled", about: "Включает распределение 30 deep-check слотов между несколькими корзинами, а не только верхушкой рейтинга.", values: "true/false." },
  { group: "Сканер", path: "scanner.attention_waiting_slots", about: "Сколько слотов держать за кандидатами, которые уже дали план и ждут подтверждения входа.", values: "0-250. Практический режим: 4-8." },
  { group: "Сканер", path: "scanner.attention_hot_slots", about: "Сколько слотов отдавать самым активным символам прямо сейчас по WebSocket/snapshot движению.", values: "0-250. Практический режим: 6-10." },
  { group: "Сканер", path: "scanner.attention_recent_slots", about: "Сколько слотов отдавать свежим сильным кандидатам из последних trade plans.", values: "0-250. Практический режим: 4-8." },
  { group: "Сканер", path: "scanner.attention_reversal_slots", about: "Сколько слотов отдавать резким падениям, чтобы не упускать short/reversal сценарии.", values: "0-250. Практический режим: 2-5." },
  { group: "Сканер", path: "scanner.attention_rotation_slots", about: "Сколько слотов отдавать ротации старых непроверенных символов из watchlist.", values: "0-250. Практический режим: 6-12." },
  { group: "Сканер", path: "scanner.attention_recent_score_floor", about: "Минимальный прошлый score, чтобы символ попал в корзину recent.", values: "0-100. Ниже - шире выборка, выше - строже." },
  { group: "Сканер", path: "scanner.attention_recent_plan_lookback_minutes", about: "Сколько минут истории trade plans учитывать для recent-корзины.", values: "1-1440 минут." },
  { group: "Сканер", path: "scanner.attention_waiting_lookback_minutes", about: "Сколько минут держать waiting_entry кандидата в отдельной корзине.", values: "1-1440 минут." },
  { group: "Сканер", path: "scanner.attention_hot_score_cooldown_minutes", about: "Минимальная пауза повторной deep-check проверки для сильных прошлых score.", values: "0-1440 минут." },
  { group: "Сканер", path: "scanner.attention_near_score_cooldown_minutes", about: "Пауза повторной проверки для кандидатов около проходного score.", values: "0-1440 минут." },
  { group: "Сканер", path: "scanner.attention_mid_score_cooldown_minutes", about: "Пауза повторной проверки для средних кандидатов.", values: "0-1440 минут." },
  { group: "Сканер", path: "scanner.attention_low_score_cooldown_minutes", about: "Пауза повторной проверки для слабых кандидатов, чтобы не забивать слоты мусорными повторами.", values: "0-1440 минут." },
  { group: "Сканер", path: "scanner.save_all_market_snapshots", about: "Сохранять ли рыночные snapshot-данные по watchlist для дальнейшей аналитики.", values: "true/false." },
  { group: "Сканер", path: "scanner.all_market_snapshot_interval_seconds", about: "Как часто сохранять общий market snapshot по watchlist.", values: "1-3600 секунд." },

  { group: "Фильтры рынка", path: "filters.min_quote_volume_24h_usd", about: "Минимальный 24h оборот в USDT для допуска символа в обычный paper-анализ.", values: "0 и выше." },
  { group: "Фильтры рынка", path: "filters.real_money_min_quote_volume_24h_usd", about: "Минимальный 24h оборот для режимов, где потенциально важна реальная ликвидность.", values: "0 и выше." },
  { group: "Фильтры рынка", path: "filters.min_volume_24h_usd", about: "Дополнительный нижний порог 24h объема.", values: "0 и выше." },
  { group: "Фильтры рынка", path: "filters.max_spread_pct", about: "Максимальный нормальный спред bid/ask, при котором символ еще можно рассматривать.", values: "0-5%." },
  { group: "Фильтры рынка", path: "filters.max_spread_pct_absolute_skip", about: "Жесткий предел спреда: выше него символ пропускается независимо от остальных сигналов.", values: "0-10%." },
  { group: "Фильтры рынка", path: "filters.min_5m_change_abs_pct", about: "Минимальное абсолютное движение цены за 5 минут для интереса к символу.", values: "0-50%." },
  { group: "Фильтры рынка", path: "filters.min_15m_volume_spike", about: "Минимальный всплеск объема за 15 минут относительно базового уровня.", values: "0 и выше. 1.8 значит примерно 180% от базы." },
  { group: "Фильтры рынка", path: "filters.min_price_change_15m_pct_for_candidate", about: "Минимальное изменение цены за 15 минут для попадания в кандидаты.", values: "0 и выше, в процентах." },
  { group: "Фильтры рынка", path: "filters.min_price_change_1h_pct_for_candidate", about: "Минимальное изменение цены за 1 час для попадания в кандидаты.", values: "0 и выше, в процентах." },
  { group: "Фильтры рынка", path: "filters.min_volume_spike_for_candidate", about: "Минимальный volume spike для первичного отбора кандидата.", values: "0 и выше. 1.4 значит примерно 140% от базы." },
  { group: "Фильтры рынка", path: "filters.exclude_major_symbols", about: "Исключать ли крупные мажоры вроде BTC/ETH из paper-скальпера.", values: "true - исключать; false - разрешить." },

  { group: "Стратегия", path: "strategy.direction_mode", about: "Какие направления сделок разрешены классификатору.", values: "both, long_only, short_only, auto." },
  { group: "Стратегия", path: "strategy.long_signal_execution", about: "Как исполнять найденный LONG_CONTINUATION сигнал.", values: "normal - обычный LONG; inverse_short - открыть SHORT от long-сигнала для проверки гипотезы локального отката." },
  { group: "Стратегия", path: "strategy.long_enabled", about: "Глобально разрешает LONG-входы.", values: "true/false." },
  { group: "Стратегия", path: "strategy.short_enabled", about: "Глобально разрешает SHORT-входы.", values: "true/false." },
  { group: "Стратегия", path: "strategy.long_min_score", about: "Минимальный score классификатора для LONG-входа.", values: "0-100. Выше - меньше сделок, строже отбор." },
  { group: "Стратегия", path: "strategy.inverse_long_min_score", about: "Минимальный score long-сигнала, который можно исполнять как inverse SHORT.", values: "0-100. Для эксперимента обычно равен long_min_score." },
  { group: "Стратегия", path: "strategy.short_min_score", about: "Минимальный score классификатора для SHORT-входа.", values: "0-100. Для шортов сейчас намеренно строже." },
  { group: "Стратегия", path: "strategy.long_high_conviction_score", about: "Порог очень сильного LONG setup, который может пройти без части вторичных подтверждений вроде отдельного volume spike.", values: "0-100. Ниже - больше агрессивных входов." },
  { group: "Стратегия", path: "strategy.short_strict_mode", about: "Включает дополнительные строгие проверки перед SHORT-входом.", values: "true/false." },
  { group: "Стратегия", path: "strategy.avoid_late_chase", about: "Не входить, если движение уже слишком далеко ушло и вход похож на позднюю погоню.", values: "true/false." },
  { group: "Стратегия", path: "strategy.avoid_aggressive_buy_chase", about: "Блокировать LONG, когда агрессивный buy-flow приходит уже после импульса и похож на поздний вход.", values: "true/false. Для свежего low-risk breakout блок не применяется." },
  { group: "Стратегия", path: "strategy.avoid_shorting_strong_momentum", about: "Блокировать шорты против сильного восходящего импульса.", values: "true/false." },
  { group: "Стратегия", path: "strategy.inverse_short_immediate_entry", about: "Для inverse_short входить сразу от LONG-сигнала без подтверждения 1m отката.", values: "true - агрессивно сразу; false - сначала ждать 1m откат минимум на entry.pullback_confirm_pct, затем входить по текущему bid." },
  { group: "Стратегия", path: "strategy.inverse_short_relaxed_conditions", about: "Для inverse_short разрешать SHORT после 1m отката, даже если не все LONG-фильтры идеальны.", values: "true - ловить сдувающийся long-сетап; false - инвертировать только полный LONG_CONTINUATION." },
  { group: "Стратегия", path: "strategy.long_pullback_entry_enabled", about: "Включить cost-aware LONG после контролируемого 1m отката в сильном 5m/15m/HTF контексте.", values: "true - торговать buy-the-dip; false - обычная long-логика." },
  { group: "Стратегия", path: "strategy.long_pullback_min_score", about: "Минимальный execution score для pullback LONG.", values: "0-100. Ниже - больше входов, выше - строже." },
  { group: "Стратегия", path: "strategy.long_pullback_min_pct", about: "Минимальная глубина 1m отката для pullback LONG.", values: "Проценты. 0.07 значит откат хотя бы -0.07%." },
  { group: "Стратегия", path: "strategy.long_pullback_max_pct", about: "Максимальная глубина 1m отката для pullback LONG.", values: "Проценты. Слишком глубокий откат считается falling knife." },
  { group: "Стратегия", path: "strategy.short_breakdown_entry_enabled", about: "Включить cost-aware SHORT только по реальному breakdown/failed-breakout с sell-flow.", values: "true - разрешить такие шорты; false - не использовать relaxed breakdown вход." },
  { group: "Стратегия", path: "strategy.short_breakdown_min_score", about: "Минимальный short execution score для breakdown SHORT.", values: "0-100. Ниже - больше шортов, выше - строже." },
  { group: "Стратегия", path: "strategy.short_breakdown_min_1m_pct", about: "Минимальное падение за 1m для breakdown SHORT.", values: "Проценты. 0.18 значит 1m <= -0.18%." },
  { group: "Стратегия", path: "strategy.short_breakdown_min_5m_pct", about: "Минимальное падение за 5m для breakdown SHORT.", values: "Проценты. 0.35 значит 5m <= -0.35%." },

  { group: "Риск", path: "risk.starting_balance_usdt", about: "Стартовый paper-баланс для расчета PnL/ROI.", values: "Больше 0 USDT." },
  { group: "Риск", path: "risk.margin_mode", about: "Режим маржи для paper-модели и будущего live/testnet исполнения.", values: "isolated или cross. Для подготовки к бою используем isolated." },
  { group: "Риск", path: "risk.max_open_positions", about: "Максимум одновременно открытых paper-позиций.", values: "1-50. Сейчас целевой лимит - 5." },
  { group: "Риск", path: "risk.max_new_positions_per_cycle", about: "Сколько новых позиций бот может открыть за один полный цикл сканирования.", values: "1-50. Для агрессивного paper-режима сейчас 2." },
  { group: "Риск", path: "risk.max_position_margin_usdt", about: "Максимальная маржа на одну позицию.", values: "Больше 0 USDT." },
  { group: "Риск", path: "risk.max_account_fraction_as_margin", about: "Максимальная доля баланса, которую можно использовать как маржу на одну позицию.", values: "0-1. Например 0.12 = 12%." },
  { group: "Риск", path: "risk.max_leverage", about: "Верхний предел плеча.", values: "1-125." },
  { group: "Риск", path: "risk.default_leverage", about: "Плечо по умолчанию для новых планов.", values: "1-125 и не выше max_leverage." },
  { group: "Риск", path: "risk.maintenance_margin_rate", about: "Maintenance margin rate для расчетной цены ликвидации isolated-позиции.", values: "0-0.5. Fallback до загрузки точного Binance leverage bracket." },
  { group: "Риск", path: "risk.maintenance_amount_usdt", about: "Maintenance amount/cum для расчетной цены ликвидации isolated-позиции.", values: "0 и выше USDT. Для маленьких позиций обычно 0." },
  { group: "Риск", path: "risk.maintenance_margin_source", about: "Источник maintenance-параметров для расчета ликвидации.", values: "assumed, binance_leverage_bracket или binance_position_risk." },
  { group: "Риск", path: "risk.max_loss_per_trade_usdt", about: "Максимально допустимый расчетный убыток на одну сделку.", values: "Больше 0 USDT." },
  { group: "Риск", path: "risk.stop_loss_extra_buffer_pct", about: "Дополнительный запас движения цены для расчета размера позиции на случай проскальзывания и пробоя стопа.", values: "0-10%. Больше = меньше размер позиции и ближе фактический убыток к max_loss_per_trade_usdt." },
  { group: "Риск", path: "risk.max_trades_per_hour", about: "Лимит новых сделок в час.", values: "0 = без лимита; иначе целое число." },
  { group: "Риск", path: "risk.max_daily_trades", about: "Лимит новых сделок за день.", values: "0 = без лимита; иначе целое число." },
  { group: "Риск", path: "risk.max_loss_streak", about: "Сколько убыточных закрытых сделок подряд допускается до остановки новых входов.", values: "0 = выключено; иначе целое число." },
  { group: "Риск", path: "risk.enforce_daily_loss_limit", about: "Включать ли дневной стоп по убытку.", values: "true/false." },
  { group: "Риск", path: "risk.max_daily_loss_usdt", about: "Размер дневного убытка, после которого новые входы блокируются, если включен enforce_daily_loss_limit.", values: "0 и выше USDT." },
  { group: "Риск", path: "risk.symbol_cooldown_minutes", about: "Пауза после сделки по тому же символу перед новым входом.", values: "0 = без паузы; иначе минуты." },
  { group: "Риск", path: "risk.direction_cooldown_minutes", about: "Пауза после сделки в том же направлении перед новым входом.", values: "0 = без паузы; иначе минуты." },
  { group: "Риск", path: "risk.stop_loss_symbol_cooldown_minutes", about: "Пауза по символу после STOP_LOSS или отрицательной сделки.", values: "0 = выключено; практический режим 60-120 минут." },
  { group: "Риск", path: "risk.repeat_loss_symbol_cooldown_minutes", about: "Усиленная пауза по символу после нескольких недавних убытков.", values: "0 = выключено; практический режим 180-360 минут." },
  { group: "Риск", path: "risk.repeat_loss_symbol_count", about: "Сколько убытков по символу считать повторной проблемой.", values: "0 = выключено; практический режим 2-3." },
  { group: "Риск", path: "risk.repeat_loss_window_minutes", about: "Окно истории для подсчета повторных убытков по символу.", values: "0 = выключено; практический режим 240-720 минут." },
  { group: "Риск", path: "risk.cooldown_scope", about: "Какие прошлые сделки учитываются для symbol/direction/loss cooldown.", values: "active_settings - только текущий settings hash; all_history - вся история независимо от версии." },

  { group: "Вход", path: "entry.mode", about: "Схема входа в позицию.", values: "confirmation_ladder, single_market, pullback_limit." },
  { group: "Вход", path: "entry.legs_enabled", about: "Разрешить деление входа на несколько частей.", values: "true/false." },
  { group: "Вход", path: "entry.leg_weights", about: "Доли частей входа. Сумма должна быть 1.0.", values: "Список чисел, например [0.7, 0.3]." },
  { group: "Вход", path: "entry.max_legs", about: "Максимальное число частей входа.", values: "1-5; leg_weights не должен быть длиннее max_legs." },
  { group: "Вход", path: "entry.allow_average_down", about: "Разрешить усреднение против позиции.", values: "true/false. Для текущего paper-профиля лучше false." },
  { group: "Вход", path: "entry.market_entry_allowed", about: "Разрешить рыночный вход в paper-модели.", values: "true/false." },
  { group: "Вход", path: "entry.use_limit_ioc_for_paper_model", about: "Моделировать вход как limit IOC там, где это применимо.", values: "true/false." },
  { group: "Вход", path: "entry.require_trigger_confirmation", about: "Не открывать позицию сразу после сигнала, пока цена не пройдет расчетный trigger из entry_grid.", values: "true/false. Для V5 включено." },
  { group: "Вход", path: "entry.pullback_long_market_entry", about: "Для pullback LONG использовать текущий ask как trigger, не отключая trigger-confirmation для SHORT и других сетапов.", values: "true - buy-the-dip входит по откату; false - ждать общий trigger из entry_grid." },
  { group: "Вход", path: "entry.trigger_tolerance_pct", about: "Допустимая погрешность около trigger, чтобы не терять вход из-за микроскопической разницы bid/ask.", values: "0-5%." },
  { group: "Вход", path: "entry.max_entry_distance_above_trigger_pct", about: "Максимальная дистанция, на которую цена может убежать дальше trigger; выше этого вход считается поздней погоней.", values: "0-50%." },
  { group: "Вход", path: "entry.breakout_buffer_pct_min", about: "Минимальный буфер над/под уровнем пробоя для входной сетки.", values: "0-5% и не выше breakout_buffer_pct_max." },
  { group: "Вход", path: "entry.breakout_buffer_pct_max", about: "Максимальный буфер над/под уровнем пробоя для входной сетки.", values: "0-5% и не ниже breakout_buffer_pct_min." },
  { group: "Вход", path: "entry.pullback_confirm_pct", about: "Размер отката/подтверждения для pullback-логики.", values: "0-10%." },
  { group: "Вход", path: "entry.chase_max_distance_pct", about: "Максимальная дистанция от расчетной зоны входа, после которой вход считается погоней.", values: "0-50%." },

  { group: "Выход", path: "exit.initial_sl_pct_min", about: "Минимальная дистанция начального стопа.", values: "Больше 0, до 20%, не выше initial_sl_pct_max." },
  { group: "Выход", path: "exit.initial_sl_pct_max", about: "Максимальная дистанция начального стопа.", values: "Больше 0, до 20%, не ниже initial_sl_pct_min." },
  { group: "Выход", path: "exit.initial_sl_spread_multiplier", about: "Насколько учитывать спред при расчете начального стопа.", values: "0 и выше." },
  { group: "Выход", path: "exit.initial_sl_atr_multiplier", about: "Насколько учитывать ATR/волатильность при расчете начального стопа.", values: "0 и выше." },
  { group: "Выход", path: "exit.breakeven_plus_enabled", about: "Разрешить перенос стопа в безубыток плюс небольшой net profit.", values: "true/false." },
  { group: "Выход", path: "exit.breakeven_plus_trigger_extra_pct", about: "Дополнительное движение цены, после которого можно включать breakeven-plus.", values: "0-20%." },
  { group: "Выход", path: "exit.min_net_profit_after_breakeven_usdt", about: "Минимальная чистая прибыль, которую breakeven-plus старается защитить после комиссий.", values: "0 и выше USDT." },
  { group: "Выход", path: "exit.preferred_net_profit_after_breakeven_usdt", about: "Желаемая чистая прибыль для более комфортного breakeven-plus уровня.", values: "0 и выше USDT." },
  { group: "Выход", path: "exit.tp1_enabled", about: "Разрешить частичную фиксацию первой цели.", values: "true/false." },
  { group: "Выход", path: "exit.tp1_trigger_pct_min", about: "Минимальное движение цены для TP1.", values: "0-100% и не выше tp1_trigger_pct_max." },
  { group: "Выход", path: "exit.tp1_trigger_pct_max", about: "Максимальное движение цены для TP1.", values: "0-100% и не ниже tp1_trigger_pct_min." },
  { group: "Выход", path: "exit.tp1_extra_after_cost_pct", about: "Минимальный запас движения сверх расчетной стоимости входа/выхода для TP1.", values: "0-100%. Ниже = ближе take profit." },
  { group: "Выход", path: "exit.tp1_close_fraction", about: "Какая доля позиции закрывается на TP1.", values: "Больше 0 и до 1.0. Например 0.5 = половина." },
  { group: "Выход", path: "exit.profit_guard_enabled", about: "Включает защиту сделки, которая уже дала небольшой плюс, но начинает отдавать движение.", values: "true/false." },
  { group: "Выход", path: "exit.profit_guard_trigger_pct", about: "С какого максимального движения в плюс считать, что сделку уже надо защищать.", values: "0-100% движения цены." },
  { group: "Выход", path: "exit.profit_guard_floor_pct", about: "До какого текущего плюса можно откатиться после срабатывания защиты, прежде чем бот закроет позицию.", values: "0-100% движения цены." },
  { group: "Выход", path: "exit.profit_guard_min_age_seconds", about: "Минимальный возраст позиции перед закрытием по отдаче прибыли.", values: "0 и выше секунд." },
  { group: "Выход", path: "exit.small_profit_time_exit_enabled", about: "Разрешает закрывать всю позицию, если она достаточно долго держит небольшой плюс, но не дошла до TP.", values: "true/false." },
  { group: "Выход", path: "exit.small_profit_time_exit_seconds", about: "Сколько секунд позиция должна быть в плюсе перед small-profit выходом.", values: "0 и выше секунд." },
  { group: "Выход", path: "exit.small_profit_time_exit_min_pct", about: "Минимальный текущий плюс для small-profit выхода.", values: "0-100% движения цены." },
  { group: "Выход", path: "exit.trailing_enabled", about: "Разрешить trailing stop после движения в плюс.", values: "true/false." },
  { group: "Выход", path: "exit.trailing_start_pct_min", about: "Минимальное движение цены в плюс, после которого можно включать trailing.", values: "0-100%." },
  { group: "Выход", path: "exit.trailing_distance_pct_min", about: "Минимальная дистанция trailing stop.", values: "0-100% и не выше trailing_distance_pct_max." },
  { group: "Выход", path: "exit.trailing_distance_pct_max", about: "Максимальная дистанция trailing stop.", values: "0-100% и не ниже trailing_distance_pct_min." },
  { group: "Выход", path: "exit.trailing_spread_multiplier", about: "Насколько учитывать спред в trailing stop.", values: "0 и выше." },
  { group: "Выход", path: "exit.trailing_atr_multiplier", about: "Насколько учитывать ATR/волатильность в trailing stop.", values: "0 и выше." },
  { group: "Выход", path: "exit.time_stop_seconds", about: "Через сколько секунд можно закрыть слабую позицию по time stop.", values: "0 = выключено; иначе секунд, не больше max_hold_seconds." },
  { group: "Выход", path: "exit.max_hold_seconds", about: "Максимальное время удержания позиции.", values: "0 = выключено; иначе секунд." },

  { group: "Комиссии", path: "fees.fee_rate_taker", about: "Комиссия taker, учитываемая в paper PnL.", values: "0-0.1. Например 0.0004 = 0.04%." },
  { group: "Комиссии", path: "fees.fee_rate_maker", about: "Комиссия maker, учитываемая в paper PnL.", values: "0-0.1. Например 0.0002 = 0.02%." },

  { group: "Проскальзывание", path: "slippage.entry_slippage_bps", about: "Модель проскальзывания на входе.", values: "0-500 bps. 1 bps = 0.01%." },
  { group: "Проскальзывание", path: "slippage.exit_slippage_bps", about: "Модель проскальзывания на выходе.", values: "0-500 bps. 1 bps = 0.01%." },

  { group: "Позиции", path: "positions.allow_duplicate_symbol", about: "Разрешить несколько одновременных позиций по одному символу.", values: "true/false. По умолчанию false." },
  { group: "Позиции", path: "positions.allow_opposite_positions_same_symbol", about: "Разрешить одновременно LONG и SHORT по одному символу.", values: "true/false. По умолчанию false." },
  { group: "Позиции", path: "positions.max_open_positions", about: "Дублирующий лимит открытых позиций для подсистемы позиций; синхронизируется с risk.max_open_positions.", values: "1-50." },

  { group: "Дашборд", path: "dashboard.route_prefix", about: "Префикс HTTP-маршрута, под которым опубликован dashboard.", values: "Строка, сейчас /bot." },
  { group: "Дашборд", path: "dashboard.refresh_fast_seconds", about: "Частота быстрого обновления метрик, графика и открытых позиций.", values: "1-120 секунд." },
  { group: "Дашборд", path: "dashboard.refresh_slow_seconds", about: "Частота медленного обновления истории, сигналов и статуса settings.", values: "5-300 секунд." },
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function getSettingsPath(obj, path) {
  return path.split(".").reduce((acc, key) => {
    if (acc === null || acc === undefined) return undefined;
    return Object.prototype.hasOwnProperty.call(acc, key) ? acc[key] : undefined;
  }, obj);
}

function renderSettingValue(value) {
  if (value === undefined) return '<span class="muted">нет в JSON</span>';
  if (Array.isArray(value) || (value && typeof value === "object")) return escapeHtml(JSON.stringify(value));
  return escapeHtml(value);
}

function renderSettingsHelp(settings = latestSettings) {
  const target = document.getElementById("settings-help-table");
  if (!target || !settings) return;
  let currentGroup = "";
  const rows = [];
  settingsHelp.forEach((item) => {
    if (item.group !== currentGroup) {
      currentGroup = item.group;
      rows.push(`<tr class="settings-help-group"><td colspan="4">${escapeHtml(currentGroup)}</td></tr>`);
    }
    rows.push(`
      <tr>
        <td><code>${escapeHtml(item.path)}</code></td>
        <td class="settings-help-value">${renderSettingValue(getSettingsPath(settings, item.path))}</td>
        <td>${escapeHtml(item.about)}</td>
        <td>${escapeHtml(item.values)}</td>
      </tr>
    `);
  });
  target.innerHTML = `
    <thead><tr><th>Строка</th><th>Текущее значение</th><th>Что значит</th><th>Допустимые значения</th></tr></thead>
    <tbody>${rows.join("")}</tbody>
  `;
}

function renderEquity(rows) {
  const canvas = document.getElementById("equity-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width = canvas.clientWidth * devicePixelRatio;
  const height = canvas.height = canvas.clientHeight * devicePixelRatio;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#4ea1ff";
  ctx.lineWidth = 2 * devicePixelRatio;
  if (rows.length < 2) return;
  const values = rows.map((r) => Number(r.equity_usdt));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(0.0001, max - min);
  ctx.beginPath();
  rows.forEach((row, index) => {
    const x = (index / (rows.length - 1)) * width;
    const y = height - ((Number(row.equity_usdt) - min) / span) * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function prettySettings(settings) {
  return JSON.stringify(settings, null, 2);
}

function setSettingsEditorStatus(message, klass = "") {
  const target = document.getElementById("settings-editor-status");
  target.textContent = message;
  target.className = `inline-status ${klass}`.trim();
}

function parseSettingsEditor() {
  const editor = document.getElementById("settings-editor");
  try {
    const parsed = JSON.parse(editor.value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("settings JSON must be an object");
    }
    latestSettings = parsed;
    renderSettingsHelp(parsed);
    setSettingsEditorStatus("JSON parsed", "pos");
    return parsed;
  } catch (error) {
    setSettingsEditorStatus(`JSON error: ${error.message}`, "neg");
    throw error;
  }
}

function writeSettingsEditor(settings, { dirty = false } = {}) {
  latestSettings = JSON.parse(JSON.stringify(settings));
  document.getElementById("settings-editor").value = prettySettings(latestSettings);
  settingsEditorDirty = dirty;
  renderSettingsHelp(latestSettings);
}

function renderSettings(payload, botStatus) {
  document.getElementById("settings-meta").innerHTML = [
    `<span class="pill">v${payload.version}</span>`,
    `<span class="pill">${payload.settings_hash_short}</span>`,
    `<span class="pill">${botStatus.paused ? "paused" : "running"}</span>`,
    `<span class="pill">open ${botStatus.open_positions}</span>`,
    botStatus.pending_account_reset ? `<span class="pill warning">account reset pending</span>` : "",
    settingsEditorDirty ? `<span class="pill warning">unsaved editor changes</span>` : "",
  ].join("");
  if (!settingsEditorDirty) {
    writeSettingsEditor(payload.settings);
    setSettingsEditorStatus("Loaded active runtime settings");
  }
}

async function refreshFast() {
  try {
    const [summary, open, equity] = await Promise.all([api("api/summary"), api("api/open-positions"), api("api/equity")]);
    renderMetrics(summary);
    renderOpen(open);
    renderEquity(equity);
    document.getElementById("status").textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  }
}

async function refreshSlow() {
  const symbol = document.getElementById("filter-symbol").value.trim();
  const direction = document.getElementById("filter-direction").value;
  const exitReason = document.getElementById("filter-exit").value.trim();
  const qs = new URLSearchParams();
  qs.set("scope", "active");
  if (symbol) qs.set("symbol", symbol.toUpperCase());
  if (direction) qs.set("direction", direction);
  if (exitReason) qs.set("exit_reason", exitReason.toUpperCase());
  const [trades, signals, tradingSettings, botStatus, impact] = await Promise.all([
    api(`api/trades?${qs}`),
    api("api/signals"),
    api("api/settings/trading"),
    api("api/bot/status"),
    api("api/impact"),
  ]);
  latestTradingSettings = tradingSettings;
  renderTrades(trades, tradingSettings);
  renderSignals(signals);
  renderSettings(tradingSettings, botStatus);
  renderImpact(impact);
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab, .panel").forEach((el) => el.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
  });
});

["filter-symbol", "filter-direction", "filter-exit"].forEach((id) => {
  document.getElementById(id).addEventListener("input", refreshSlow);
});

document.getElementById("csv-export").addEventListener("click", () => {
  const headers = ["exit_time_ms", "symbol", "direction", "entry_price", "exit_price", "qty", "notional_usdt", "margin_usdt", "leverage", "margin_mode", "liquidation_price", "gross_pnl_usdt", "entry_fee_usdt", "exit_fee_usdt", "fees_usdt", "slippage_usdt", "funding_usdt", "net_pnl_usdt", "roi_pct", "exit_reason", "strategy_config_version"];
  const lines = [headers.join(",")].concat(latestTrades.map((row) => headers.map((key) => csvCell(key === "margin_usdt" ? marginUsdt(row) : row[key])).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `paper-trades-${latestTradingSettings?.version ? `v${latestTradingSettings.version}` : "active"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

async function settingsAction(path, body = {}) {
  const comment = document.getElementById("settings-comment").value.trim();
  const settings = parseSettingsEditor();
  const result = await writeApi(path, { settings, comment, ...body });
  writeSettingsEditor(result.settings, { dirty: path.includes("validate") });
  setSettingsEditorStatus(path.includes("validate") ? `Valid settings. Hash ${result.settings_hash?.slice(0, 12) || ""}` : `Applied settings v${result.version}`);
  document.getElementById("status").textContent = `settings ${result.version ? `v${result.version}` : "ok"}`;
  await refreshSlow();
}

document.getElementById("settings-validate").addEventListener("click", () => settingsAction("api/settings/validate").catch((error) => {
  document.getElementById("status").textContent = error.message;
}));

document.getElementById("settings-apply").addEventListener("click", () => settingsAction("api/settings/apply").catch((error) => {
  document.getElementById("status").textContent = error.message;
}));

document.getElementById("settings-format").addEventListener("click", () => {
  const parsed = parseSettingsEditor();
  writeSettingsEditor(parsed, { dirty: true });
  setSettingsEditorStatus("Formatted JSON", "pos");
});

document.getElementById("settings-editor").addEventListener("input", () => {
  settingsEditorDirty = true;
  try {
    const parsed = JSON.parse(document.getElementById("settings-editor").value);
    if (parsed && !Array.isArray(parsed) && typeof parsed === "object") {
      latestSettings = parsed;
      renderSettingsHelp(parsed);
      setSettingsEditorStatus("Unsaved valid JSON", "pos");
    } else {
      throw new Error("settings JSON must be an object");
    }
  } catch (error) {
    setSettingsEditorStatus(`JSON error: ${error.message}`, "neg");
  }
});

document.getElementById("settings-reset").addEventListener("click", () => writeApi("api/settings/reset-defaults", { comment: "reset from dashboard" }).then((result) => {
  writeSettingsEditor(result.settings);
  setSettingsEditorStatus(`Reset settings v${result.version}`, "pos");
  return refreshSlow();
}).catch((error) => {
  document.getElementById("status").textContent = error.message;
}));

document.getElementById("settings-export").addEventListener("click", async () => {
  const text = await apiText("api/settings/export.yaml");
  const blob = new Blob([text], { type: "application/x-yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "long-bot-runtime-settings.yaml";
  a.click();
  URL.revokeObjectURL(url);
});

document.getElementById("bot-pause").addEventListener("click", () => writeApi("api/bot/pause").then(refreshSlow));
document.getElementById("bot-resume").addEventListener("click", () => writeApi("api/bot/resume").then(refreshSlow));

document.getElementById("open-table").addEventListener("click", async (event) => {
  const button = event.target.closest(".manual-close");
  if (!button) return;
  await writeApi(`api/paper/close/${button.dataset.positionId}`);
  document.getElementById("status").textContent = "manual close queued";
  await refreshFast();
});

refreshFast();
refreshSlow();
setInterval(refreshFast, 5000);
setInterval(refreshSlow, 20000);
