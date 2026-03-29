"""Send sample entry + exit notifications to preview formatting."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.utils.helpers import load_config
from src.utils.notifier import TelegramNotifier

config = load_config()
n = TelegramNotifier(config)

# Sample LONG entry
ok1 = n.notify_entry(
    side="long", price=1985.50, size=57.234,
    sl=1970.12, tp=2032.80, strategy="mtf_momentum",
    leverage=12, balance=4775.03,
)
print("Entry:", "OK" if ok1 else "FAILED")

# Sample profit exit
ok2 = n.notify_exit(
    side="long", entry_price=1985.50, exit_price=2032.80,
    pnl_pct=2.38, net_pnl=142.50, reason="take_profit",
    size=57.234, duration_min=35, balance=4917.53,
)
print("Exit (profit):", "OK" if ok2 else "FAILED")

# Sample loss exit
ok3 = n.notify_exit(
    side="short", entry_price=2050.00, exit_price=2065.30,
    pnl_pct=-0.75, net_pnl=-45.20, reason="stop_loss",
    size=57.234, duration_min=12, balance=4872.33,
)
print("Exit (loss):", "OK" if ok3 else "FAILED")
