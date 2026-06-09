"""Quick verification: Apr 4-5 backtest to confirm revert."""
import yaml
from src.backtesting.engine import BacktestEngine
from src.exchange.data_fetcher import DataFetcher
from datetime import datetime, timedelta

with open("config/config.yaml") as f:
    config = yaml.safe_load(f)

config["backtest"]["start_date"] = "2026-04-04"
config["backtest"]["end_date"] = "2026-04-05"
config["backtest"]["initial_capital"] = 1000

fetcher = DataFetcher(config)
start_dt = datetime.strptime("2026-04-04", "%Y-%m-%d")
iso = start_dt.isocalendar()
week_monday = datetime.fromisocalendar(iso[0], iso[1], 1)
warmup_start = min(start_dt - timedelta(days=5), week_monday).strftime("%Y-%m-%d")

print(f"Fetching 1m data from {warmup_start} to 2026-04-05...")
df_1m = fetcher.fetch_ohlcv("1m", warmup_start, "2026-04-05")
print(f"Fetched {len(df_1m)} 1m candles")

df_3m = fetcher.resample(df_1m, "3m")
df_5m = fetcher.resample(df_1m, "5m")
df_15m = fetcher.resample(df_1m, "15m")

engine = BacktestEngine(config)
results = engine.run(df_1m, df_3m, df_5m, df_15m)

trades = results["closed_trades"]
final = results["final_capital"]
ret = (final / 1000 - 1) * 100
print(f"\nTotal trades: {len(trades)}")
print(f"Final capital: ${final:.2f}")
print(f"Return: {ret:.2f}%")
if trades:
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    losses = len(trades) - wins
    print(f"Wins: {wins}, Losses: {losses}")
    print(f"Win rate: {wins/len(trades)*100:.1f}%")
    for i, t in enumerate(trades):
        side = t["side"]
        strat = t.get("strategy", "?")
        entry = t["entry_price"]
        exit_p = t["exit_price"]
        pnl = t["net_pnl"]
        reason = t["exit_reason"]
        print(f"  Trade {i+1}: {side} {strat} | entry={entry:.2f} exit={exit_p:.2f} | PnL=${pnl:.2f} ({reason})")
