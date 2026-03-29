import os
import sys
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.helpers import load_config
from src.utils.logger import setup_logger
from src.exchange.data_fetcher import DataFetcher
from src.backtesting.engine import BacktestEngine
from src.backtesting.report import BacktestReport


def run_backtest(config: dict):
    logger = setup_logger(
        level=config["logging"]["level"],
        log_file=config["logging"]["file"],
        console=config["logging"]["console"],
    )
    logger.info("=== STARTING BACKTEST ===")

    fetcher = DataFetcher(config)
    start = config["backtest"]["start_date"]
    end = config["backtest"]["end_date"]

    # Fetch 1m data (add extra days at start for indicator warmup)
    from datetime import datetime, timedelta
    warmup_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")

    logger.info(f"Fetching 1m data from {warmup_start} to {end}")
    df_1m = fetcher.fetch_ohlcv("1m", warmup_start, end)
    if df_1m.empty:
        logger.error("No data fetched. Exiting.")
        return

    logger.info(f"Fetched {len(df_1m)} 1m candles")

    # Resample to higher timeframes
    df_3m = fetcher.resample(df_1m, "3m")
    df_5m = fetcher.resample(df_1m, "5m")
    df_15m = fetcher.resample(df_1m, "15m")

    logger.info(f"Resampled: 3m={len(df_3m)}, 5m={len(df_5m)}, 15m={len(df_15m)}")

    # Run backtest
    engine = BacktestEngine(config)
    results = engine.run(df_1m, df_3m, df_5m, df_15m)

    # Generate report
    report = BacktestReport(results, config)
    report_text = report.generate()

    logger.info("=== BACKTEST COMPLETE ===")
    return results


def main():
    parser = argparse.ArgumentParser(description="ETH/USDT Scalping Bot")
    parser.add_argument("--mode", default="backtest", choices=["backtest", "paper", "live"])
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    config["trading"]["mode"] = args.mode

    if args.mode == "backtest":
        run_backtest(config)
    else:
        print(f"Mode '{args.mode}' not yet implemented. Use --mode backtest")


if __name__ == "__main__":
    main()
