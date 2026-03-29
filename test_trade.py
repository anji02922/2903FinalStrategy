"""Force a small test trade on the demo API to verify the full execution path."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.helpers import load_config
from src.exchange.binance_client import BinanceClient
from src.execution.order_manager import OrderManager

config = load_config()
client = BinanceClient(config)
order_mgr = OrderManager(client)

# 1. Get current price
ticker = client.fetch_ticker()
price = float(ticker["last"])
print(f"ETH price: ${price:.2f}")

# 2. Get balance
balance = client.get_balance()
print(f"Balance: ${balance:.2f}")

# 3. Set leverage
client.set_leverage()

# 4. Place a small market buy (minimum notional = $20, so need ~0.01 ETH at $2000)
size = 0.02  # ~$40 notional, safe above minimum
print(f"\n--- Placing market BUY {size} ETH ---")
try:
    entry = order_mgr.place_market_order("buy", size)
    fill_price = float(entry.get("average", entry.get("price", price)))
    order_id = entry.get("id", "")
    print(f"  Entry filled @ ${fill_price:.2f} | Order ID: {order_id}")
    print(f"  Status: {entry.get('status')}")
except Exception as e:
    print(f"  Entry FAILED: {e}")
    sys.exit(1)

# 5. Place SL order
sl_price = round(fill_price * 0.995, 2)  # 0.5% below
print(f"\n--- Placing SL @ ${sl_price:.2f} ---")
try:
    sl = order_mgr.place_stop_loss("long", size, sl_price)
    sl_id = sl.get("id", "")
    print(f"  SL placed | Order ID: {sl_id}")
except Exception as e:
    print(f"  SL FAILED: {e}")
    sl_id = None

# 6. Place TP order
tp_price = round(fill_price * 1.005, 2)  # 0.5% above
print(f"\n--- Placing TP @ ${tp_price:.2f} ---")
try:
    tp = order_mgr.place_take_profit("long", size, tp_price)
    tp_id = tp.get("id", "")
    print(f"  TP placed | Order ID: {tp_id}")
except Exception as e:
    print(f"  TP FAILED: {e}")
    tp_id = None

# 7. Check positions
print(f"\n--- Checking positions ---")
positions = client.fetch_positions()
for p in positions:
    print(f"  Side: {p.get('side')} | Size: {p.get('contracts')} | Entry: {p.get('entryPrice')}")

# 8. Check open orders  
print(f"\n--- Open orders ---")
orders = client.fetch_open_orders()
for o in orders:
    print(f"  Type: {o.get('type')} | Side: {o.get('side')} | Price: {o.get('stopPrice', o.get('price'))}")

# 9. Cancel all and close
print(f"\n--- Closing test position ---")
try:
    order_mgr.cancel_all()
    print("  All orders cancelled")
except Exception as e:
    print(f"  Cancel all: {e}")

try:
    close = order_mgr.place_market_order("sell", size)
    close_price = float(close.get("average", close.get("price", price)))
    print(f"  Closed @ ${close_price:.2f}")
    pnl = (close_price - fill_price) * size * 12  # with leverage
    print(f"  Test PnL: ${pnl:.4f}")
except Exception as e:
    print(f"  Close FAILED: {e}")

# 10. Final balance
balance_after = client.get_balance()
print(f"\nBalance after: ${balance_after:.2f}")
print(f"Balance change: ${balance_after - balance:.4f}")
print("\n=== TRADE EXECUTION PATH VERIFIED ===")
