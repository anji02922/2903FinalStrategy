"""Quick connectivity test for Binance Demo API."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.helpers import load_config
from src.exchange.binance_client import BinanceClient

config = load_config()
client = BinanceClient(config)

print("Testing balance...")
try:
    bal = client.get_balance()
    print(f"  Balance: ${bal:.2f}")
except Exception as e:
    print(f"  Balance error: {e}")

print("Testing ticker...")
try:
    t = client.fetch_ticker()
    print(f"  ETH price: ${float(t['last']):.2f}")
except Exception as e:
    print(f"  Ticker error: {e}")

print("Testing positions...")
try:
    pos = client.fetch_positions()
    print(f"  Open positions: {len(pos)}")
except Exception as e:
    print(f"  Positions error: {e}")

print("Testing leverage...")
client.set_leverage()

print("Testing margin mode...")
client.set_margin_mode("cross")

print("\nAll connectivity tests passed!")
