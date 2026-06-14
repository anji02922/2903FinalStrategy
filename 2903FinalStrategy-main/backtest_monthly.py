"""
Yearly backtest runner with constant capital per trade.

Runs backtests year-by-year (2020–2026) with constant position sizing.
Produces a year-wise statistics table and a net PnL vs time curve.

Usage:
    python backtest_monthly.py 2020 2026
"""

import sys
import os
import copy
import warnings
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.exchange.data_fetcher import DataFetcher
from src.backtesting.engine import BacktestEngine
from src.utils.logger import setup_logger


def load_config():
    with open("config/config.yaml", "r") as f:
        return yaml.safe_load(f)


def compute_year_stats(trades, equity_curve, initial_capital, year):
    """Compute statistics for a single year matching the table format."""
    stats = {}
    if not trades:
        return {k: 0.0 for k in [
            "thPnl", "netPnl", "tv", "mtmtv(bps)", "mdd", "annaul_netPnl/mdd",
            "annualized_sharpe(20dayslookback)", "capital", "annualized_return",
            "RiOC(mdd/cap)%", "Positive_Negative_Days_Ratio",
            "Long_Short_Pnl_Ratio", "Trade_perport_perday",
            "Avg_Trade_Minutes", "No_Of_Days",
            "worst_day_by_22days_pnl", "worst_22day_by_22days_pnl",
            "worst_22day_by_pnl", "netPnl(long)", "netPnl(short)",
            "tv(long)", "tv(short)",
        ]}

    df = pd.DataFrame(trades)
    capital = initial_capital

    # Total notional traded (trade value)
    tv_total = df["notional"].sum()
    tv_long = df[df["side"] == "long"]["notional"].sum()
    tv_short = df[df["side"] == "short"]["notional"].sum()

    # Net PnL
    net_pnl = df["net_pnl"].sum()
    raw_pnl = df["raw_pnl"].sum()  # theoretical PnL (before fees)

    # Net PnL by side
    net_pnl_long = df[df["side"] == "long"]["net_pnl"].sum()
    net_pnl_short = df[df["side"] == "short"]["net_pnl"].sum()

    # MTM TV (bps) = net_pnl / tv * 10000
    mtmtv_bps = (net_pnl / tv_total * 10000) if tv_total > 0 else 0

    # Build daily PnL series
    df["date"] = pd.to_datetime(df["exit_ts"]).dt.date
    daily_pnl = df.groupby("date")["net_pnl"].sum()

    # Number of trading days
    n_days = len(daily_pnl)

    # Max drawdown from equity curve
    mdd = 0.0
    if not equity_curve.empty:
        eq = equity_curve["equity"].values
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        mdd = dd.min()

    # Annualized return
    annualized_return = (net_pnl / capital) * 100 * (365 / max(n_days, 1))

    # Annual netPnl/mdd
    ann_pnl_mdd = (annualized_return / abs(mdd)) if mdd != 0 else 0

    # Sharpe (20-day lookback, annualized)
    if len(daily_pnl) >= 20:
        rolling_mean = daily_pnl.rolling(20).mean()
        rolling_std = daily_pnl.rolling(20).std()
        sharpe_series = (rolling_mean / rolling_std) * np.sqrt(252)
        ann_sharpe_20d = sharpe_series.dropna().mean() if not sharpe_series.dropna().empty else 0
    elif len(daily_pnl) > 1:
        ann_sharpe_20d = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252) if daily_pnl.std() > 0 else 0
    else:
        ann_sharpe_20d = 0

    # RiOC (mdd/cap) %
    rioc = (abs(mdd) / 100 * capital / capital * 100) if capital > 0 else 0
    rioc = abs(mdd)  # mdd is already in % of equity

    # Positive/Negative days ratio
    pos_days = (daily_pnl > 0).sum()
    neg_days = (daily_pnl <= 0).sum()
    pos_neg_ratio = (pos_days / neg_days) if neg_days > 0 else float("inf")

    # Long/Short PnL ratio
    long_short_ratio = (net_pnl_long / abs(net_pnl_short)) if net_pnl_short != 0 else float("inf")

    # Trades per day
    trades_per_day = len(df) / max(n_days, 1)

    # Average trade duration
    avg_duration = df["duration_minutes"].mean() if "duration_minutes" in df.columns else 0

    # Worst day by 22-day PnL
    if len(daily_pnl) >= 22:
        rolling_22d_pnl = daily_pnl.rolling(22).sum()
        worst_day_by_22d = daily_pnl.min()
        worst_22d = rolling_22d_pnl.dropna().min()
        worst_22d_by_pnl = worst_22d / net_pnl if net_pnl != 0 else 0
    else:
        worst_day_by_22d = daily_pnl.min() if len(daily_pnl) > 0 else 0
        worst_22d = daily_pnl.sum() if len(daily_pnl) > 0 else 0
        worst_22d_by_pnl = 0

    return {
        "thPnl": raw_pnl,
        "netPnl": net_pnl,
        "tv": tv_total,
        "mtmtv(bps)": mtmtv_bps,
        "mdd": mdd,
        "annaul_netPnl/mdd": ann_pnl_mdd,
        "annualized_sharpe(20dayslookback)": ann_sharpe_20d,
        "capital": capital,
        "annualized_return": annualized_return,
        "RiOC(mdd/cap)%": rioc,
        "Positive_Negative_Days_Ratio": pos_neg_ratio,
        "Long_Short_Pnl_Ratio": long_short_ratio,
        "Trade_perport_perday": trades_per_day,
        "Avg_Trade_Minutes": avg_duration,
        "No_Of_Days": n_days,
        "worst_day_by_22days_pnl": worst_day_by_22d,
        "worst_22day_by_22days_pnl": worst_22d,
        "worst_22day_by_pnl": worst_22d_by_pnl,
        "netPnl(long)": net_pnl_long,
        "netPnl(short)": net_pnl_short,
        "tv(long)": tv_long,
        "tv(short)": tv_short,
    }


def run_yearly_backtest(start_year: int, end_year: int, initial_capital: float = 1000.0):
    base_config = load_config()
    setup_logger(level="ERROR", log_file="logs/monthly_backtest.log", console=True)

    base_config["exchange"]["market_type"] = "future"

    all_trades = []
    all_equity_curves = []
    yearly_stats = {}

    for year in range(start_year, end_year + 1):
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31"
        warmup_start = (datetime(year, 1, 1) - timedelta(days=10)).strftime("%Y-%m-%d")

        print(f"\n{'='*72}")
        print(f"  DOWNLOADING {year} DATA  (cached after first run)")
        print(f"{'='*72}")

        cfg = copy.deepcopy(base_config)
        cfg["backtest"]["start_date"] = year_start
        cfg["backtest"]["end_date"] = year_end
        cfg["backtest"]["initial_capital"] = initial_capital

        fetcher = DataFetcher(cfg)
        df_1m = fetcher.fetch_ohlcv("1m", warmup_start, year_end)

        if df_1m.empty:
            print(f"  No data for {year}. Skipping.")
            continue

        print(f"  Loaded {len(df_1m):,} 1m candles")
        df_3m = fetcher.resample(df_1m, "3m")
        df_5m = fetcher.resample(df_1m, "5m")
        df_15m = fetcher.resample(df_1m, "15m")

        print(f"\n  Running {year} backtest (constant capital=${initial_capital:,.2f})...")
        engine = BacktestEngine(cfg)

        def _progress(current, total):
            pct = current / total * 100
            print(f"\r  Progress: {current:,}/{total:,} candles ({pct:.0f}%)", end="", flush=True)

        results = engine.run(df_1m, df_3m, df_5m, df_15m, progress_cb=_progress)
        print()

        trades = results["closed_trades"]
        equity_curve = results["equity_curve"]

        # Compute year stats
        stats = compute_year_stats(trades, equity_curve, initial_capital, year)
        yearly_stats[year] = stats

        # Accumulate for combined stats
        all_trades.extend(trades)
        if not equity_curve.empty:
            all_equity_curves.append(equity_curve)

        print(f"  {year}: {len(trades)} trades, netPnl=${stats['netPnl']:+,.2f}, "
              f"mdd={stats['mdd']:.2f}%, sharpe={stats['annualized_sharpe(20dayslookback)']:.3f}")

    # Build combined equity curve with constant capital (cumulative net PnL)
    if all_equity_curves:
        combined_equity = pd.concat(all_equity_curves, ignore_index=True)
        combined_equity = combined_equity.sort_values("timestamp").reset_index(drop=True)
    else:
        combined_equity = pd.DataFrame()

    # Combined stats across all years
    combined_stats = compute_year_stats(all_trades, combined_equity, initial_capital, "combined")
    yearly_stats["combined"] = combined_stats

    # Print year-wise stats table
    print_stats_table(yearly_stats, start_year, end_year)

    # Save stats to CSV
    os.makedirs("reports", exist_ok=True)
    stats_df = pd.DataFrame(yearly_stats).T
    stats_df.index.name = "year"
    stats_df.to_csv("reports/yearly_stats.csv")

    # Save trades CSV
    if all_trades:
        pd.DataFrame(all_trades).to_csv("reports/all_trades.csv", index=False)

    # Plot net PnL vs time curve
    plot_pnl_curve(all_trades, initial_capital)

    return yearly_stats


def print_stats_table(yearly_stats, start_year, end_year):
    """Print year-wise statistics in a table format."""
    years = [y for y in range(start_year, end_year + 1) if y in yearly_stats]
    if "combined" in yearly_stats:
        years.append("combined")

    metrics = [
        ("thPnl", "thPnl", ".2f"),
        ("netPnl", "netPnl", ".2f"),
        ("tv", "tv", ".2f"),
        ("mtmtv(bps)", "mtmtv(bps)", ".4f"),
        ("mdd", "mdd", ".4f"),
        ("annaul_netPnl/mdd", "annaul_netPnl/mdd", ".4f"),
        ("annualized_sharpe(20dayslookback)", "ann_sharpe(20d)", ".4f"),
        ("capital", "capital", ".2f"),
        ("annualized_return", "annualized_return", ".4f"),
        ("RiOC(mdd/cap)%", "RiOC(mdd/cap)%", ".4f"),
        ("Positive_Negative_Days_Ratio", "Pos/Neg Days Ratio", ".4f"),
        ("Long_Short_Pnl_Ratio", "Long/Short PnL Ratio", ".4f"),
        ("Trade_perport_perday", "Trades/day", ".4f"),
        ("Avg_Trade_Minutes", "Avg Trade Minutes", ".2f"),
        ("No_Of_Days", "No_Of_Days", ".0f"),
        ("worst_day_by_22days_pnl", "worst_day_by_22d_pnl", ".4f"),
        ("worst_22day_by_22days_pnl", "worst_22d_by_22d_pnl", ".4f"),
        ("worst_22day_by_pnl", "worst_22d_by_pnl", ".4f"),
        ("netPnl(long)", "netPnl(long)", ".2f"),
        ("netPnl(short)", "netPnl(short)", ".2f"),
        ("tv(long)", "tv(long)", ".2f"),
        ("tv(short)", "tv(short)", ".2f"),
    ]

    print(f"\n{'='*120}")
    print(f"  YEAR-WISE STATISTICS  ({start_year} — {end_year})")
    print(f"{'='*120}")

    # Header
    header = f"  {'Metric':<30}"
    for y in years:
        header += f" {str(y):>18}"
    print(header)
    print(f"  {'-'*30}" + f" {'-'*18}" * len(years))

    # Rows
    for key, label, fmt in metrics:
        row = f"  {label:<30}"
        for y in years:
            val = yearly_stats[y].get(key, 0)
            if isinstance(val, (int, float)):
                row += f" {val:>18{fmt}}"
            else:
                row += f" {str(val):>18}"
        print(row)

    print(f"{'='*120}")


def plot_pnl_curve(all_trades, initial_capital):
    """Plot cumulative net PnL vs time and save to reports/."""
    if not all_trades:
        print("  No trades to plot.")
        return

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        df = pd.DataFrame(all_trades)
        df["exit_ts"] = pd.to_datetime(df["exit_ts"])
        df = df.sort_values("exit_ts").reset_index(drop=True)
        df["cumulative_pnl"] = df["net_pnl"].cumsum()

        fig, axes = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={"height_ratios": [3, 1]})

        # Cumulative Net PnL curve
        ax1 = axes[0]
        ax1.plot(df["exit_ts"], df["cumulative_pnl"], linewidth=1.2, color="blue", label="Cumulative Net PnL")
        ax1.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax1.set_title(f"Cumulative Net PnL vs Time (Constant Capital = ${initial_capital:,.0f})", fontsize=14)
        ax1.set_ylabel("Cumulative Net PnL ($)", fontsize=12)
        ax1.legend(fontsize=11)
        ax1.grid(True, alpha=0.3)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")

        # Drawdown from cumulative PnL
        cum_pnl = df["cumulative_pnl"].values
        equity = initial_capital + cum_pnl
        peak = np.maximum.accumulate(equity)
        dd_pct = (equity - peak) / peak * 100

        ax2 = axes[1]
        ax2.fill_between(df["exit_ts"], dd_pct, 0, alpha=0.4, color="red")
        ax2.set_title("Drawdown (%)", fontsize=12)
        ax2.set_ylabel("DD %", fontsize=11)
        ax2.grid(True, alpha=0.3)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

        plt.tight_layout()
        os.makedirs("reports", exist_ok=True)
        plt.savefig("reports/pnl_curve.png", dpi=150)
        plt.close()
        print(f"\n  PnL curve saved to reports/pnl_curve.png")

        # Also save a per-year overlay chart
        fig2, ax3 = plt.subplots(figsize=(16, 8))
        df["year"] = df["exit_ts"].dt.year
        colors = plt.cm.tab10(np.linspace(0, 1, df["year"].nunique()))
        for idx, (yr, grp) in enumerate(df.groupby("year")):
            grp = grp.copy()
            grp["year_cum_pnl"] = grp["net_pnl"].cumsum()
            ax3.plot(grp["exit_ts"], grp["year_cum_pnl"], linewidth=1.2,
                     color=colors[idx], label=f"{yr}")

        ax3.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax3.set_title(f"Per-Year Cumulative Net PnL (Constant Capital = ${initial_capital:,.0f})", fontsize=14)
        ax3.set_ylabel("Cumulative Net PnL ($)", fontsize=12)
        ax3.set_xlabel("Date", fontsize=12)
        ax3.legend(fontsize=11)
        ax3.grid(True, alpha=0.3)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha="right")
        plt.tight_layout()
        plt.savefig("reports/pnl_curve_yearly.png", dpi=150)
        plt.close()
        print(f"  Per-year PnL curve saved to reports/pnl_curve_yearly.png")

    except Exception as e:
        print(f"  Could not generate PnL chart: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python backtest_monthly.py <start_year> <end_year>")
        print("Example: python backtest_monthly.py 2020 2026")
        sys.exit(1)

    start_y = int(sys.argv[1])
    end_y = int(sys.argv[2])
    run_yearly_backtest(start_y, end_y)
