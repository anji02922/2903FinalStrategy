"""Test connectivity with sapi override for demo."""
import ccxt, os
from dotenv import load_dotenv
load_dotenv()

ex = ccxt.binanceusdm({
    "apiKey": os.getenv("BINANCE_API_KEY"),
    "secret": os.getenv("BINANCE_API_SECRET"),
    "enableRateLimit": True,
    "options": {"defaultType": "future", "fetchCurrencies": False},
})

# Override ALL URLs to demo
for key, url in ex.urls["demo"].items():
    ex.urls["api"][key] = url

# Also override sapi endpoints
ex.urls["api"]["sapi"] = "https://demo-api.binance.com/sapi/v1"
ex.urls["api"]["sapiV2"] = "https://demo-api.binance.com/sapi/v2"
ex.urls["api"]["sapiV3"] = "https://demo-api.binance.com/sapi/v3"
ex.urls["api"]["sapiV4"] = "https://demo-api.binance.com/sapi/v4"

print("Testing balance...")
try:
    bal = ex.fetch_balance()
    usdt = bal.get("USDT", {})
    print(f"  Balance: free={usdt.get('free')}, total={usdt.get('total')}")
except Exception as e:
    print(f"  Balance error: {e}")

print("Testing ticker...")
try:
    t = ex.fetch_ticker("ETH/USDT")
    print(f"  ETH price: {t['last']}")
except Exception as e:
    print(f"  Ticker error: {e}")

print("Testing positions...")
try:
    pos = ex.fetch_positions(["ETH/USDT"])
    active = [p for p in pos if abs(float(p.get("contracts", 0))) > 0]
    print(f"  Positions: {len(active)} active")
except Exception as e:
    print(f"  Positions error: {e}")

print("Testing set leverage...")
try:
    ex.set_leverage(12, "ETH/USDT")
    print("  Leverage: OK")
except Exception as e:
    print(f"  Leverage: {e}")

print("Testing open orders...")
try:
    orders = ex.fetch_open_orders("ETH/USDT")
    print(f"  Open orders: {len(orders)}")
except Exception as e:
    print(f"  Orders: {e}")

print("\nDone!")
