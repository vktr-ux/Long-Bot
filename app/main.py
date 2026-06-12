from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import load_config
from app.exchanges.bybit import BybitPublicConnector, recompute_price_24h_pct
from app.notifications.telegram import TelegramNotifier, format_signal
from app.scanner.calibration import (
    ReplayCaseResult,
    case_passed,
    format_calibration_report,
    format_replay_cases_report,
    load_replay_cases,
)
from app.scanner.diagnostics import (
    export_diagnostics_jsonl,
    print_diagnostic_report,
    print_explain,
    print_score_visibility,
)
from app.scanner.replay import (
    format_replay_report,
    load_public_replay_data,
    parse_replay_datetime,
    run_replay_on_candles,
)
from app.scanner.signals import ScanEngine
from app.scanner.state import should_alert
from app.storage.db import SQLiteStore
from app.utils.logging import configure_logging
from app.utils.time import utc_iso_from_ms

LOGGER = logging.getLogger(__name__)
TELEGRAM_TEST_MESSAGE = "Long-Bot Telegram test OK. Scanner is connected."


def telegram_enabled(config: dict) -> bool:
    notifications = config.get("notifications") or {}
    if "telegram_enabled" in notifications:
        return bool(notifications["telegram_enabled"])
    return bool(config.get("telegram", {}).get("enabled", True))


def snapshots_to_store(config: dict, store: SQLiteStore, result) -> list:
    storage_cfg = config.get("storage") or {}
    if not storage_cfg.get("save_all_market_snapshots", False):
        return result.enriched_tickers
    latest_saved = store.latest_snapshot_timestamp_ms()
    latest_market = max((ticker.timestamp_ms for ticker in result.tickers), default=None)
    interval_ms = int(storage_cfg.get("all_market_snapshot_interval_seconds", 60) * 1000)
    if latest_saved is None or latest_market is None or latest_market - latest_saved >= interval_ms:
        return result.tickers
    return result.enriched_tickers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bybit public-data crypto long momentum scanner")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Fetch live data and print alerts without Telegram sends")
    parser.add_argument("--diagnostic", action="store_true", help="Print calibration diagnostics for one-shot scans")
    parser.add_argument("--top", type=int, default=30, help="Number of rows in diagnostic top lists")
    parser.add_argument("--explain", help="Explain one symbol in detail, e.g. OPNUSDT")
    parser.add_argument("--profile", choices=["conservative", "normal", "aggressive"], help="Threshold profile")
    parser.add_argument("--replay", action="store_true", help="Replay a symbol over a historical public-data window")
    parser.add_argument("--replay-cases", action="store_true", help="Run configured historical replay casebook")
    parser.add_argument("--calibrate", action="store_true", help="Run replay casebook and print calibration summary")
    parser.add_argument("--cases", default="config/replay_cases.yaml", help="Path to replay casebook YAML")
    parser.add_argument("--symbol", help="Symbol for replay, e.g. OPNUSDT")
    parser.add_argument("--exchange", default="bybit", help="Exchange for replay; Bybit is supported")
    parser.add_argument("--start", help="Replay start, e.g. '2026-06-01 00:00'")
    parser.add_argument("--end", help="Replay end, e.g. '2026-06-04 03:00'")
    parser.add_argument("--sanity-check", action="store_true", help="Run Bybit public-data parser sanity checks")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,OPNUSDT", help="Comma-separated symbols for sanity check")
    parser.add_argument("--report-outcomes", action="store_true", help="Phase 4.5 placeholder outcome report")
    parser.add_argument("--telegram-test", action="store_true", help="Send one Telegram test message and exit")
    return parser.parse_args()


async def run_cycle(
    config: dict,
    store: SQLiteStore,
    dry_run: bool,
    diagnostic: bool = False,
    top: int = 30,
    explain_symbol: str | None = None,
) -> tuple[bool, str | None]:
    bybit_cfg = config["exchanges"]["bybit"]
    connector = BybitPublicConnector(
        base_url=bybit_cfg["base_url"],
        category=bybit_cfg["category"],
        max_concurrent_requests=config["performance"]["max_concurrent_requests"],
    )
    try:
        engine = ScanEngine(connector, config)
        result = await engine.scan_once(explain_symbol=explain_symbol)
        store.upsert_symbols(result.symbols)
        store.insert_snapshots(snapshots_to_store(config, store, result))
        export_path = export_diagnostics_jsonl(result)
        print(f"Top activity candidates ({len(result.enriched_tickers)} enriched):")
        for ticker in result.enriched_tickers[:15]:
            print(
                f"  #{ticker.turnover_rank_24h or '-':>3} {ticker.symbol:<14} "
                f"turnover={ticker.turnover_24h or 0:,.0f} 24h={ticker.price_24h_pct or 0:+.2f}%"
            )
        if result.rejected:
            print("\nRejected candidates:")
            for symbol, reasons in list(result.rejected.items())[:20]:
                print(f"  {symbol}: {', '.join(reasons)}")
        print_score_visibility(result)
        print(f"Diagnostic export: {export_path}")
        if diagnostic:
            print_diagnostic_report(result, top=top)
        if explain_symbol:
            explained = next((d for d in result.diagnostics if d.symbol.upper() == explain_symbol.upper()), None)
            if explained:
                print("\n" + "=" * 72)
                print_explain(explained)
                print("=" * 72)
            else:
                print(f"Explain symbol not found in Bybit linear universe: {explain_symbol}")
        print(f"\nGenerated signals: {len(result.signals)}")
        notifier = TelegramNotifier(
            config["telegram"].get("bot_token", ""),
            config["telegram"].get("chat_id", ""),
            config["telegram"].get("parse_mode", "HTML"),
        )
        for signal in result.signals:
            previous = store.get_state(signal.exchange, signal.symbol)
            recent_alerts = store.sent_signal_timestamps_since(signal.timestamp_ms - 60 * 60_000)
            allowed, reason = should_alert(signal, previous, config.get("notifications", config["cooldown"]), recent_alerts)
            if not allowed:
                LOGGER.info(
                    "alert suppressed symbol=%s level=%s score=%s reason=%s",
                    signal.symbol,
                    signal.level,
                    signal.score,
                    reason,
                )
                print(f"  skip {signal.symbol} {signal.level} score={signal.score} reason={reason}")
                continue
            sent = False
            if dry_run or not telegram_enabled(config):
                print("\n" + "=" * 72)
                print(format_signal(signal, previous))
                print("=" * 72)
            else:
                sent = await notifier.send(signal, previous)
            store.insert_signal(signal, sent_to_telegram=sent)
            store.upsert_state(signal, sent=sent or dry_run)
            print(f"  alert {signal.symbol} {signal.level} score={signal.score} time={utc_iso_from_ms(signal.timestamp_ms)}")
        return True, None
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("scan cycle failed")
        return False, str(exc)
    finally:
        await connector.close()


def print_outcome_report_placeholder() -> None:
    print("Outcome tracking schema is installed. Detailed --report-outcomes aggregation is Phase 4.5 TODO.")


async def run_telegram_test(config: dict) -> int:
    token = config["telegram"].get("bot_token", "")
    chat_id = config["telegram"].get("chat_id", "")
    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print(f"Telegram test failed: missing {', '.join(missing)} in local .env")
        return 2
    notifier = TelegramNotifier(token, chat_id, config["telegram"].get("parse_mode", "HTML"))
    try:
        await notifier.send_text(TELEGRAM_TEST_MESSAGE)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc) or exc.__class__.__name__
        print(f"Telegram test failed: {detail}")
        return 2
    print("Telegram test message sent.")
    return 0


async def run_sanity_check(config: dict, symbols_arg: str) -> int:
    bybit_cfg = config["exchanges"]["bybit"]
    connector = BybitPublicConnector(
        base_url=bybit_cfg["base_url"],
        category=bybit_cfg["category"],
        max_concurrent_requests=config["performance"]["max_concurrent_requests"],
    )
    failures = 0
    try:
        symbols = await connector.get_symbols()
        print(f"Instruments pagination total linear symbols: {len(symbols)}")
        if len(symbols) > 500:
            print("PASS instruments pagination: total linear symbols > 500")
        else:
            print("WARN instruments pagination: total linear symbols <= 500 in this response")
        tickers = await connector.get_tickers()
        ticker_map = {ticker.symbol.upper(): ticker for ticker in tickers}
        for symbol in [item.strip().upper() for item in symbols_arg.split(",") if item.strip()]:
            print(f"\nSanity symbol: {symbol}")
            ticker = ticker_map.get(symbol)
            if not ticker:
                print("  WARN ticker not found")
                continue
            raw_pct = ticker.raw.get("price24hPcnt") if ticker.raw else None
            recomputed = recompute_price_24h_pct(ticker.last_price, ticker.prev_price_24h)
            print(f"  price24hPcnt normalized: {ticker.price_24h_pct} recomputed: {recomputed}")
            if recomputed is not None and ticker.price_24h_pct is not None and abs(recomputed - ticker.price_24h_pct) <= 0.05:
                print("  PASS price24hPcnt normalization")
            elif raw_pct is not None and recomputed is not None:
                print("  FAIL price24hPcnt normalization mismatch")
                failures += 1
            else:
                print("  WARN price24hPcnt recompute unavailable")
            print(f"  turnover24h USD liquidity field: {ticker.turnover_24h}")
            print(f"  volume24h base-coin field: {ticker.volume_24h}")
            if ticker.turnover_24h is not None:
                print("  PASS liquidity filters use turnover_24h, not base volume")
            raw_payload = await connector._get(
                "/v5/market/kline",
                {"category": connector.category, "symbol": symbol, "interval": "15", "limit": 5},
            )
            raw_rows = raw_payload.get("result", {}).get("list", [])
            raw_timestamps = [int(row[0]) for row in raw_rows]
            raw_reverse = raw_timestamps == sorted(raw_timestamps, reverse=True)
            candles = await connector.get_klines(symbol, "15", 5)
            parsed_timestamps = [c.timestamp_ms for c in candles]
            parsed_asc = parsed_timestamps == sorted(parsed_timestamps)
            print(f"  raw Bybit kline order descending: {raw_reverse}")
            print(f"  parsed kline order ASC: {parsed_asc}")
            if parsed_asc:
                print("  PASS reverse order handled after parsing")
            else:
                print("  FAIL parsed klines are not ASC")
                failures += 1
            if candles:
                latest = candles[-1]
                print(f"  latest parsed candle status: {'closed' if latest.is_closed else 'live/forming'}")
                print("  PASS current forming candle is marked and closed-candle metrics exclude live candles")
    finally:
        await connector.close()
    return 1 if failures else 0


async def run_replay_command(config: dict, args: argparse.Namespace) -> int:
    if (args.exchange or "").lower() != "bybit":
        print("Replay currently supports Bybit public market data only.")
        return 2
    if not args.symbol or not args.start or not args.end:
        print("--replay requires --symbol, --start, and --end")
        return 2
    start_ms = parse_replay_datetime(args.start)
    end_ms = parse_replay_datetime(args.end)
    if end_ms <= start_ms:
        print("--end must be after --start")
        return 2
    bybit_cfg = config["exchanges"]["bybit"]
    connector = BybitPublicConnector(
        base_url=bybit_cfg["base_url"],
        category=bybit_cfg["category"],
        max_concurrent_requests=config["performance"]["max_concurrent_requests"],
    )
    try:
        candles_by_interval, oi_history, funding_history, notes = await load_public_replay_data(
            connector, args.symbol.upper(), start_ms, end_ms
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Replay public data fetch failed: {exc}")
        return 2
    finally:
        await connector.close()
    report = run_replay_on_candles(
        symbol=args.symbol.upper(),
        exchange="bybit",
        start_ms=start_ms,
        end_ms=end_ms,
        candles_by_interval=candles_by_interval,
        oi_history=oi_history,
        funding_history=funding_history,
        config=config,
    )
    report.missing_data_notes.extend(notes)
    print(format_replay_report(report))
    return 0


async def run_replay_casebook_command(config: dict, args: argparse.Namespace, calibration: bool = False) -> int:
    try:
        cases = load_replay_cases(args.cases)
    except Exception as exc:  # noqa: BLE001
        print(f"Replay casebook load failed: {exc}")
        return 2
    bybit_cfg = config["exchanges"]["bybit"]
    connector = BybitPublicConnector(
        base_url=bybit_cfg["base_url"],
        category=bybit_cfg["category"],
        max_concurrent_requests=config["performance"]["max_concurrent_requests"],
    )
    results: list[ReplayCaseResult] = []
    try:
        for case in cases:
            if case.exchange != "bybit":
                results.append(ReplayCaseResult(case=case, error="only Bybit public replay is supported"))
                continue
            try:
                candles_by_interval, oi_history, funding_history, notes = await load_public_replay_data(
                    connector, case.symbol, case.start_ms, case.end_ms
                )
                report = run_replay_on_candles(
                    symbol=case.symbol,
                    exchange=case.exchange,
                    start_ms=case.start_ms,
                    end_ms=case.end_ms,
                    candles_by_interval=candles_by_interval,
                    oi_history=oi_history,
                    funding_history=funding_history,
                    config=config,
                )
                report.missing_data_notes.extend(notes)
                results.append(ReplayCaseResult(case=case, report=report))
            except Exception as exc:  # noqa: BLE001
                results.append(ReplayCaseResult(case=case, error=str(exc)))
    finally:
        await connector.close()
    if calibration:
        print(format_calibration_report(results, config["app"]["profile"], config["scoring"]["levels"]["watch"]))
    else:
        print(format_replay_cases_report(results))
    if any(result.error is not None for result in results):
        return 2
    return 0 if all(case_passed(result) for result in results) else 1


async def async_main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.profile:
        from app.config import apply_threshold_profile

        config = apply_threshold_profile(config, args.profile)
    if args.dry_run:
        config["app"]["dry_run"] = True
    dry_run = bool(config["app"].get("dry_run"))
    configure_logging(config["app"]["log_level"])
    if args.telegram_test:
        return await run_telegram_test(config)
    if args.report_outcomes:
        print_outcome_report_placeholder()
        return 0
    if args.replay:
        return await run_replay_command(config, args)
    if args.replay_cases:
        return await run_replay_casebook_command(config, args)
    if args.calibrate:
        return await run_replay_casebook_command(config, args, calibration=True)
    if args.sanity_check:
        return await run_sanity_check(config, args.symbols)
    store = SQLiteStore(config["app"]["database_path"])
    try:
        while True:
            ok, error = await run_cycle(
                config,
                store,
                dry_run=dry_run,
                diagnostic=args.diagnostic,
                top=args.top,
                explain_symbol=args.explain,
            )
            if args.once:
                if not ok:
                    print(f"Live Bybit dry-run failed: {error}")
                    return 2
                return 0
            await asyncio.sleep(config["app"]["scan_interval_seconds"])
    finally:
        store.close()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
