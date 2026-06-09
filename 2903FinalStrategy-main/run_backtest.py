import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import run_backtest
from src.utils.helpers import load_config

c = load_config()
results = run_backtest(c)
if results:
    trades = results["closed_trades"]
    print(f"\n=== TRADE DETAILS ({len(trades)} trades) ===")
    for i, t in enumerate(trades):
        ts = t.get('entry_ts', '')
        if hasattr(ts, 'strftime'):
            ts = ts.strftime('%Y-%m-%d %H:%M')
        print(f"  #{i+1:>2} {ts} | {t['side']:>5} | entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} | pnl=${t['net_pnl']:>+8.2f} | {t['exit_reason']}")
