from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .config import load_config
from .futu_provider import FutuMinuteKlineProvider
from .history_scan import (
    build_sh_code_range,
    print_volume_spike_results,
    save_volume_spike_results_csv,
    scan_history_volume_spikes,
)
from .notify import ConsoleNotifier
from .realtime_cache import MinuteKlineCache, PeriodicKlineAnalyzer
from .rules import AlertEngine
from .storage import SQLiteMinuteBarStore
from .status import write_status
from .universe import build_candidate_codes
from .volume_alert import SQLiteVolumeAlertRunner
from .web_monitor import run_web_monitor


def main() -> None:
    parser = argparse.ArgumentParser(prog="cyb-monitor")
    parser.add_argument("--config", default="config.example.yaml", help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-codes", help="输出候选创业板股票代码")
    subparsers.add_parser("run", help="启动富途实时分钟K线监控")
    realtime_parser = subparsers.add_parser("run-realtime-cache", help="订阅分钟K线并每隔一段时间分析最新缓存")
    realtime_parser.add_argument("--interval-seconds", type=int, default=300, help="分析间隔，默认 300 秒")
    realtime_parser.add_argument("--cache-bars", type=int, default=5, help="每只股票缓存的分钟K线数量，默认 5")
    realtime_parser.add_argument("--max-subscribe", type=int, help="覆盖配置中的最大订阅股票数")
    realtime_parser.add_argument("--top-n", type=int, default=10, help="每次分析打印成交量最高的前 N 只，默认 10")
    realtime_parser.add_argument("--db", default="data/minute_bars.sqlite3", help="SQLite 数据库路径")
    realtime_parser.add_argument("--retention-days", type=int, default=92, help="分钟K线保留天数，默认 92 天")
    realtime_parser.add_argument("--status-dir", default="data/status", help="进程状态文件目录")
    alert_parser = subparsers.add_parser("run-volume-alerts", help="每隔一段时间从 SQLite 扫描最近5根K线并触发放量报警")
    alert_parser.add_argument("--db", default="data/minute_bars.sqlite3", help="SQLite 数据库路径")
    alert_parser.add_argument("--status-dir", default="data/status", help="进程状态文件目录")
    alert_parser.add_argument("--interval-seconds", type=int, default=300, help="扫描间隔，默认 300 秒")
    alert_parser.add_argument("--workers", type=int, default=20, help="并行线程数，默认 20")
    alert_parser.add_argument("--latest-bars", type=int, default=5, help="检查最新分钟K线数量，默认 5")
    alert_parser.add_argument("--baseline-days", type=int, default=20, help="历史基准交易日数量，默认 20")
    alert_parser.add_argument("--threshold", type=float, default=2.0, help="m/n 报警阈值，默认 2.0")
    web_parser = subparsers.add_parser("web-monitor", help="启动本地 Web 控制台监控订阅、存储和报警进程")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8088, help="监听端口，默认 8088")
    web_parser.add_argument("--db", default="data/minute_bars.sqlite3", help="SQLite 数据库路径")
    web_parser.add_argument("--status-dir", default="data/status", help="进程状态文件目录")
    scan_parser = subparsers.add_parser("scan-history-volume", help="验证历史1分钟成交量放大筛选逻辑")
    scan_parser.add_argument("--start", type=int, default=600001, help="起始股票代码，默认 600001")
    scan_parser.add_argument("--end", type=int, default=600999, help="结束股票代码，默认 600999")
    scan_parser.add_argument("--threshold", type=float, default=3.0, help="m/n 阈值，默认 3.0")
    scan_parser.add_argument("--trade-days", type=int, default=20, help="参与计算的最近交易日数量，默认 20")
    scan_parser.add_argument(
        "--date",
        help="目标交易日，格式 YYYY-MM-DD；不传则使用拉取数据中的最后一个交易日",
    )
    scan_parser.add_argument(
        "--lookback-calendar-days",
        type=int,
        default=80,
        help="向前拉取的自然日数量，默认 80",
    )
    scan_parser.add_argument("--page-size", type=int, default=1000, help="富途历史K线单页数量，默认 1000")
    scan_parser.add_argument("--max-pages", type=int, default=20, help="每只股票最多拉取页数，默认 20")
    scan_parser.add_argument(
        "--csv",
        default="volume_spikes.csv",
        help="CSV 保存路径，默认 volume_spikes.csv",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    codes = build_candidate_codes(config.universe, config.futu.market_prefix)

    if args.command == "list-codes":
        for code in codes:
            print(code)
        return

    if args.command == "scan-history-volume":
        from futu import OpenQuoteContext

        quote_ctx = OpenQuoteContext(host=config.futu.host, port=config.futu.port)
        try:
            results = scan_history_volume_spikes(
                quote_ctx,
                build_sh_code_range(args.start, args.end),
                autype_name=config.futu.autype,
                threshold=args.threshold,
                trade_days=args.trade_days,
                target_date=date.fromisoformat(args.date) if args.date else None,
                lookback_calendar_days=args.lookback_calendar_days,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
            print_volume_spike_results(results)
            csv_path = Path(args.csv)
            save_volume_spike_results_csv(results, csv_path)
            print(f"saved CSV: {csv_path}")
        finally:
            quote_ctx.close()
        return

    if args.command == "run-realtime-cache":
        if args.max_subscribe is not None:
            config.futu.max_subscribe = args.max_subscribe

        status_dir = Path(args.status_dir)
        cache = MinuteKlineCache(max_bars=args.cache_bars)
        store = SQLiteMinuteBarStore(
            Path(args.db),
            retention_days=args.retention_days,
            status_path=status_dir / "storage.json",
        )
        analyzer = PeriodicKlineAnalyzer(
            cache,
            interval_seconds=args.interval_seconds,
            min_bars=args.cache_bars,
            top_n=args.top_n,
            status_path=status_dir / "cache_analysis.json",
        )
        write_status(
            status_dir / "realtime.json",
            {
                "state": "starting",
                "max_subscribe": config.futu.max_subscribe,
                "kline_type": config.futu.kline_type,
                "db_path": args.db,
            },
        )
        store.start()
        analyzer.start()

        def on_bar(bar):
            cache.update(bar)
            store.enqueue(bar)

        provider = FutuMinuteKlineProvider(
            config.futu,
            config.rules,
            on_bar,
            on_baselines=None,
            on_status=lambda payload: write_status(
                status_dir / "realtime.json",
                {
                    "max_subscribe": config.futu.max_subscribe,
                    "kline_type": config.futu.kline_type,
                    "db_path": args.db,
                    **payload,
                },
            ),
        )
        try:
            provider.run(
                codes,
                prefixes=config.universe.prefixes,
                prefer_futu_universe=config.universe.source == "futu",
                selection=config.universe.selection,
                random_seed=config.universe.random_seed,
            )
        finally:
            write_status(status_dir / "realtime.json", {"state": "stopped"})
            analyzer.stop()
            store.stop()
        return

    if args.command == "run-volume-alerts":
        status_dir = Path(args.status_dir)
        runner = SQLiteVolumeAlertRunner(
            Path(args.db),
            interval_seconds=args.interval_seconds,
            workers=args.workers,
            latest_bars=args.latest_bars,
            baseline_days=args.baseline_days,
            threshold=args.threshold,
            status_path=status_dir / "alerts.json",
        )
        runner.run_forever()
        return

    if args.command == "web-monitor":
        run_web_monitor(args.host, args.port, Path(args.db), Path(args.status_dir))
        return

    notifier = ConsoleNotifier()
    engine = AlertEngine(config.rules)

    def on_bar(bar):
        for alert in engine.on_bar(bar):
            if config.notify.console:
                notifier.send(alert)

    provider = FutuMinuteKlineProvider(
        config.futu,
        config.rules,
        on_bar,
        on_baselines=engine.set_volume_baselines,
    )
    provider.run(
        codes,
        prefixes=config.universe.prefixes,
        prefer_futu_universe=config.universe.source == "futu",
        selection=config.universe.selection,
        random_seed=config.universe.random_seed,
    )


if __name__ == "__main__":
    main()
