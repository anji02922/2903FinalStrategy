import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import run_backtest
from src.utils.helpers import load_config

c = load_config()
results = run_backtest(c)
if results:
    trades = results["closed_trades"]
    print(f"\n=== TRADE DETAILS ({len(trades)} trades) ===")
    for i, t in enumerate(trades[:10]):
        print(f"  #{i+1} {t['strategy']} {t['side']} entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} pnl=${t['net_pnl']:.2f} reason={t['exit_reason']}")
