import pandas as pd

df = pd.read_csv("reports/trades.csv")

print("=== EXIT REASON ANALYSIS ===")
for reason in df["exit_reason"].unique():
    sub = df[df["exit_reason"] == reason]
    w = sub[sub["net_pnl"] > 0]
    wr = len(w) / len(sub) * 100 if len(sub) > 0 else 0
    avg_pnl = sub["net_pnl"].mean()
    total = sub["net_pnl"].sum()
    avg_dur = sub["duration_minutes"].mean()
    print(f"  {reason:20s}: {len(sub):4d} trades | WR={wr:5.1f}% | avg=${avg_pnl:+8.2f} | total=${total:+12.2f} | dur={avg_dur:5.1f}m")

print("\n=== BREAKEVEN STOP DETAIL ===")
be = df[df["exit_reason"] == "breakeven_stop"]
print(f"  Count: {len(be)}, Total PnL: ${be['net_pnl'].sum():+.2f}, Avg fees: ${be['fees'].mean():.2f}")
print(f"  Avg duration: {be['duration_minutes'].mean():.1f} min")

print("\n=== STOP LOSS SIZE DISTRIBUTION ===")
sl = df[df["exit_reason"] == "stop_loss"]
for lo, hi in [(-500, -200), (-200, -100), (-100, -50), (-50, 0)]:
    cnt = len(sl[(sl["net_pnl"] >= lo) & (sl["net_pnl"] < hi)])
    print(f"  ${lo:>5} to ${hi:>4}: {cnt} trades")

print("\n=== TRAILING STOP WINS ===")
ts = df[df["exit_reason"] == "trailing_stop"]
print(f"  Total: {len(ts)}, Avg PnL: ${ts['net_pnl'].mean():+.2f}, Avg dur: {ts['duration_minutes'].mean():.1f}m")
tw = ts[ts["net_pnl"] > 0]
print(f"  Winners: {len(tw)}, Avg win: ${tw['net_pnl'].mean():+.2f}")

print("\n=== TIME EXIT ANALYSIS ===")
te = df[df["exit_reason"] == "time_exit"]
tw2 = te[te["net_pnl"] > 0]
tl2 = te[te["net_pnl"] <= 0]
print(f"  Total: {len(te)}, WR: {len(tw2)/len(te)*100:.1f}%")
print(f"  Avg winner: ${tw2['net_pnl'].mean():+.2f}" if len(tw2) else "  No winners")
print(f"  Avg loser: ${tl2['net_pnl'].mean():+.2f}" if len(tl2) else "  No losers")

print("\n=== CONSECUTIVE LOSS STREAKS ===")
streak = 0
max_streak = 0
for _, row in df.iterrows():
    if row["net_pnl"] <= 0:
        streak += 1
        max_streak = max(max_streak, streak)
    else:
        streak = 0
print(f"  Max consecutive losses: {max_streak}")

print("\n=== HOURLY PERFORMANCE ===")
df["hour"] = pd.to_datetime(df["entry_ts"]).dt.hour
hourly = df.groupby("hour").agg(trades=("net_pnl", "count"), pnl=("net_pnl", "sum"))
hourly["avg"] = hourly["pnl"] / hourly["trades"]
for h, row in hourly.iterrows():
    bar = "+" * int(max(0, row["avg"] / 2)) + "-" * int(max(0, -row["avg"] / 2))
    print(f"  {h:02d}:00  {row['trades']:3.0f} trades  ${row['pnl']:+8.0f}  avg=${row['avg']:+6.1f}  {bar}")
