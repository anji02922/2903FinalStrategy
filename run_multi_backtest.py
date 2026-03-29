"""Run backtests across multiple periods to validate consistency."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import run_backtest
from src.utils.helpers import load_config

PERIODS = [
    # Previously tested periods — compare results
    ("2025-02-01", "2025-02-22"),  # Was +76.8%
    ("2024-01-05", "2024-02-02"),  # Was +46.6%
    ("2024-11-01", "2024-11-29"),  # Was +53.2%
    # New random periods
    ("2025-06-01", "2025-06-28"),  # Jun 2025
    ("2023-03-10", "2023-04-07"),  # Mar-Apr 2023
]

results_summary = []
for start, end in PERIODS:
    print(f"\n{'='*60}")
    print(f"  PERIOD: {start} to {end}")
    print(f"{'='*60}")
    config = load_config()
    config["backtest"]["start_date"] = start
    config["backtest"]["end_date"] = end
    config["logging"]["console"] = False  # quiet

    try:
        results = run_backtest(config)
        if results:
            trades = results["closed_trades"]
            init = results["initial_capital"]
            final = results["final_capital"]
            ret = (final - init) / init * 100
            wins = sum(1 for t in trades if t["net_pnl"] > 0)
            wr = wins / len(trades) * 100 if trades else 0
            results_summary.append((start, end, ret, len(trades), wr))
            print(f"  Return: {ret:+.1f}%  |  Trades: {len(trades)}  |  Win Rate: {wr:.1f}%")
        else:
            results_summary.append((start, end, None, 0, 0))
            print("  No results!")
    except Exception as e:
        results_summary.append((start, end, None, 0, 0))
        print(f"  ERROR: {e}")

print(f"\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
print(f"  {'Period':<28} {'Return':>8} {'Trades':>7} {'WinRate':>8}")
print(f"  {'-'*28} {'-'*8} {'-'*7} {'-'*8}")
for start, end, ret, trades, wr in results_summary:
    ret_str = f"{ret:+.1f}%" if ret is not None else "ERROR"
    print(f"  {start} to {end}  {ret_str:>8} {trades:>7} {wr:>7.1f}%")
all_profitable = all(r is not None and r > 0 for _, _, r, _, _ in results_summary)
print(f"\n  All profitable: {'YES' if all_profitable else 'NO'}")
