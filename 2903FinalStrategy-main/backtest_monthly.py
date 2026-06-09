"""
Monthly backtest runner — runs backtests per year with monthly return breakdown.

Capital carries forward from one year to the next (daily compounding within each run).
Uses Binance spot data (futures data only exists from Nov 2019).

Usage:
    python backtest_monthly.py 2018 2019
    python backtest_monthly.py 2018 2018
"""

import sys
import os
import copy
import warnings
import yaml
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.exchange.data_fetcher import DataFetcher
from src.backtesting.engine import BacktestEngine
from src.utils.logger import setup_logger


def load_config():
    with open("config/config.yaml", "r") as f:
        return yaml.safe_load(f)


def run_monthly_backtest(start_year: int, end_year: int, initial_capital: float = 1000.0):
    base_config = load_config()
    setup_logger(level="ERROR", log_file="logs/monthly_backtest.log", console=True)

    # Use futures data for 2020+ (futures launched Nov 2019), spot for earlier
    if start_year >= 2020:
        base_config["exchange"]["market_type"] = "future"
    else:
        base_config["exchange"]["market_type"] = "spot"

    capital = initial_capital
    all_monthly = []
    yearly_summaries = {}

    for year in range(start_year, end_year + 1):
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31"
        # Extra days for indicator + ISO week warmup
        warmup_start = (datetime(year, 1, 1) - timedelta(days=10)).strftime("%Y-%m-%d")

        print(f"\n{'='*72}")
        print(f"  DOWNLOADING {year} DATA  (cached after first run)")
        print(f"{'='*72}")

        cfg = copy.deepcopy(base_config)
        cfg["backtest"]["start_date"] = year_start
        cfg["backtest"]["end_date"] = year_end
        cfg["backtest"]["initial_capital"] = capital

        fetcher = DataFetcher(cfg)
        df_1m = fetcher.fetch_ohlcv("1m", warmup_start, year_end)

        if df_1m.empty:
            print(f"  No data for {year}. Skipping.")
            continue

        print(f"  Loaded {len(df_1m):,} 1m candles")
        df_3m = fetcher.resample(df_1m, "3m")
        df_5m = fetcher.resample(df_1m, "5m")
        df_15m = fetcher.resample(df_1m, "15m")

        print(f"\n  Running {year} backtest (capital=${capital:,.2f})...")
        engine = BacktestEngine(cfg)

        def _progress(current, total):
            pct = current / total * 100
            print(f"\r  Progress: {current:,}/{total:,} candles ({pct:.0f}%)", end="", flush=True)

        results = engine.run(df_1m, df_3m, df_5m, df_15m, progress_cb=_progress)
        print()  # newline after progress

        ending_capital = results["final_capital"]
        trades = results["closed_trades"]
        equity_curve = results["equity_curve"]

        # ── Extract monthly returns from equity curve ──
        if not equity_curve.empty:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                equity_curve["month"] = equity_curve["timestamp"].dt.to_period("M")
            monthly_equity = equity_curve.groupby("month").agg(
                first_eq=("equity", "first"),
                last_eq=("equity", "last"),
                min_eq=("equity", "min"),
            )
        else:
            monthly_equity = pd.DataFrame()

        # Group trades by month
        trades_by_month = defaultdict(list)
        for t in trades:
            ts = t.get("entry_ts")
            if hasattr(ts, "month"):
                # Attribute warmup trades (entry before year start) to January
                if ts.year < year:
                    key = f"{year}-01"
                else:
                    key = f"{ts.year}-{ts.month:02d}"
            else:
                key = "unknown"
            trades_by_month[key].append(t)

        # Print monthly table
        print(f"\n{'='*72}")
        print(f"  {year} MONTHLY RETURNS  |  Starting Capital: ${capital:,.2f}")
        print(f"{'='*72}")
        print(f"  {'Month':<10} {'Start':>11} {'End':>11} {'PnL':>10} {'Return':>8} {'Trades':>7} {'WR':>6} {'MaxDD':>7}")
        print(f"  {'-'*10} {'-'*11} {'-'*11} {'-'*10} {'-'*8} {'-'*7} {'-'*6} {'-'*7}")

        month_start_cap = capital
        for month_num in range(1, 13):
            month_key = f"{year}-{month_num:02d}"
            period_key = pd.Period(month_key, freq="M")
            month_trades = trades_by_month.get(month_key, [])
            n_trades = len(month_trades)
            wins = sum(1 for t in month_trades if t["net_pnl"] > 0)
            wr = (wins / n_trades * 100) if n_trades else 0
            month_pnl = sum(t["net_pnl"] for t in month_trades)
            month_end_cap = month_start_cap + month_pnl
            pct = (month_pnl / month_start_cap * 100) if month_start_cap > 0 else 0

            # Max drawdown from equity curve for this month
            max_dd = 0.0
            if not equity_curve.empty and period_key in monthly_equity.index:
                month_eq = equity_curve[equity_curve["month"] == period_key]["equity"]
                if len(month_eq) > 0:
                    running_max = month_eq.cummax()
                    dd = (month_eq - running_max) / running_max * 100
                    max_dd = dd.min()

            all_monthly.append({
                "month": month_key, "starting": month_start_cap, "ending": month_end_cap,
                "pnl": month_pnl, "pct": pct, "trades": n_trades, "wins": wins,
                "wr": wr, "max_dd": max_dd,
            })

            print(f"  {month_key:<10} ${month_start_cap:>10,.2f} ${month_end_cap:>10,.2f} "
                  f"${month_pnl:>+9,.2f} {pct:>+7.1f}% {n_trades:>6} {wr:>5.1f}% {max_dd:>6.1f}%")

            month_start_cap = month_end_cap

        # Year summary
        year_pnl = ending_capital - capital
        year_pct = (year_pnl / capital) * 100 if capital > 0 else 0
        year_trades = len(trades)
        year_wins = sum(1 for t in trades if t["net_pnl"] > 0)
        year_wr = (year_wins / year_trades * 100) if year_trades else 0

        # Yearly max drawdown
        year_max_dd = 0.0
        if not equity_curve.empty:
            eq = equity_curve["equity"]
            running_max = eq.cummax()
            dd = (eq - running_max) / running_max * 100
            year_max_dd = dd.min()

        yearly_summaries[year] = {
            "start": capital, "end": ending_capital,
            "pnl": year_pnl, "pct": year_pct,
            "trades": year_trades, "wr": year_wr, "max_dd": year_max_dd,
        }

        print(f"  {'-'*10} {'-'*11} {'-'*11} {'-'*10} {'-'*8} {'-'*7} {'-'*6} {'-'*7}")
        print(f"  {year:<10} ${capital:>10,.2f} ${ending_capital:>10,.2f} "
              f"${year_pnl:>+9,.2f} {year_pct:>+7.1f}% {year_trades:>6} {year_wr:>5.1f}% {year_max_dd:>6.1f}%")

        capital = ending_capital

    # Final summary
    total_pnl = capital - initial_capital
    total_pct = (total_pnl / initial_capital) * 100

    print(f"\n{'='*72}")
    print(f"  OVERALL SUMMARY  ({start_year} — {end_year})")
    print(f"{'='*72}")
    print(f"  Starting Capital:  ${initial_capital:>12,.2f}")
    print(f"  Ending Capital:    ${capital:>12,.2f}")
    print(f"  Total Net Profit:  ${total_pnl:>+12,.2f}")
    print(f"  Total Return:      {total_pct:>+11.1f}%")
    print(f"  Total Trades:      {sum(r['trades'] for r in all_monthly):>12}")
    print()
    for year, ys in yearly_summaries.items():
        print(f"  {year}:  ${ys['start']:>10,.2f} -> ${ys['end']:>10,.2f}  "
              f"({ys['pct']:>+7.1f}%)  {ys['trades']} trades  WR={ys['wr']:.1f}%  MaxDD={ys['max_dd']:.1f}%")
    print(f"{'='*72}")

    return all_monthly


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python backtest_monthly.py <start_year> <end_year>")
        print("Example: python backtest_monthly.py 2018 2019")
        sys.exit(1)

    start_y = int(sys.argv[1])
    end_y = int(sys.argv[2])
    run_monthly_backtest(start_y, end_y)
