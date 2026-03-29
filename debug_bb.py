import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.helpers import load_config
from src.exchange.data_fetcher import DataFetcher
from src.strategies.bollinger_scalp import BollingerScalpStrategy

config = load_config()
fetcher = DataFetcher(config)
df_1m = fetcher.fetch_ohlcv("1m", "2026-03-22", "2026-03-29")
df_3m = fetcher.resample(df_1m, "3m")
bb = BollingerScalpStrategy(config)
df_3m = bb.calculate_indicators(df_3m)

total_candles = len(df_3m.dropna())
print(f"Total 3m candles with indicators: {total_candles}")

# Lower band touches
lb = df_3m[(df_3m["low"] <= df_3m["bb_lower"]) & (df_3m["close"] > df_3m["bb_lower"])]
print(f"Lower band rejection candles: {len(lb)}")
lb_rsi = lb[lb["rsi"] < 45]
print(f"  ... with RSI < 45: {len(lb_rsi)}")
lb_rsi_vol = lb_rsi[lb_rsi["volume"] > 0.3 * lb_rsi["vol_sma"]]
print(f"  ... with volume filter: {len(lb_rsi_vol)}")

# Upper band touches
ub = df_3m[(df_3m["high"] >= df_3m["bb_upper"]) & (df_3m["close"] < df_3m["bb_upper"])]
print(f"Upper band rejection candles: {len(ub)}")
ub_rsi = ub[ub["rsi"] > 55]
print(f"  ... with RSI > 55: {len(ub_rsi)}")
ub_rsi_vol = ub_rsi[ub_rsi["volume"] > 0.3 * ub_rsi["vol_sma"]]
print(f"  ... with volume filter: {len(ub_rsi_vol)}")

print(f"\nRSI range: {df_3m['rsi'].min():.1f} - {df_3m['rsi'].max():.1f}, mean: {df_3m['rsi'].mean():.1f}")
print(f"BB width range: {df_3m['bb_width'].min():.4f} - {df_3m['bb_width'].max():.4f}")
