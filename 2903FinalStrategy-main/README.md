# ETH/USDT Multi-Timeframe Momentum Scalping Bot

A fully automated cryptocurrency trading bot for **ETH/USDT perpetual futures** on Binance. The bot implements a **Multi-Timeframe (MTF) EMA Crossover Momentum Strategy** with an optional **Bollinger Band Mean-Reversion Scalp Strategy**, governed by a **Market Regime Filter** and comprehensive **multi-layer risk management**.

---

## Table of Contents

- [Strategy Overview](#strategy-overview)
- [Strategy #1 — Multi-Timeframe Momentum (Active)](#strategy-1--multi-timeframe-momentum-active)
  - [Indicators Used](#indicators-used)
  - [Price Data Used](#price-data-used)
  - [Higher Timeframe Trend Detection](#higher-timeframe-trend-detection)
  - [Entry Conditions](#entry-conditions)
  - [Exit Conditions](#exit-conditions)
- [Strategy #2 — Bollinger Band Scalp (Disabled)](#strategy-2--bollinger-band-scalp-disabled)
- [Market Regime Filter](#market-regime-filter)
- [Risk Management](#risk-management)
  - [Position Sizing](#position-sizing)
  - [Stop Loss Logic](#stop-loss-logic)
  - [Take Profit Logic](#take-profit-logic)
  - [Trailing Stop (Adaptive)](#trailing-stop-adaptive)
  - [Breakeven Stop](#breakeven-stop)
  - [Momentum Decay Exit](#momentum-decay-exit)
  - [Time-Based Exit](#time-based-exit)
  - [Trade Frequency Limits](#trade-frequency-limits)
  - [Loss Limits & Circuit Breakers](#loss-limits--circuit-breakers)
  - [Fee & Slippage Accounting](#fee--slippage-accounting)
  - [Minimum Risk-Reward Ratio](#minimum-risk-reward-ratio)
  - [Cooldown Between Trades](#cooldown-between-trades)
- [Execution Flow](#execution-flow)
  - [Backtest Mode](#backtest-mode)
  - [Live/Paper Mode](#livepaper-mode)
- [Project Structure](#project-structure)
- [Configuration Reference](#configuration-reference)
- [Installation & Usage](#installation--usage)

---

## Strategy Overview

| Property            | Value                                          |
|---------------------|------------------------------------------------|
| **Market**          | ETH/USDT Perpetual Futures (Binance USDM)     |
| **Strategy Type**   | Trend-Following Momentum (with regime filter)  |
| **Entry Timeframe** | 5-minute candles                               |
| **Trend Timeframe** | 15-minute candles                              |
| **Execution**       | 1-minute candles (backtest) / 5-minute (live)  |
| **Leverage**        | 12x (cross margin)                             |
| **Direction**       | Both Long and Short                            |
| **Max Positions**   | 1 at a time                                    |

The core philosophy is: **trade in the direction of the higher-timeframe trend, enter on lower-timeframe momentum crossovers, and manage risk aggressively with ATR-based dynamic stops, trailing stops, breakeven protection, and multiple circuit breakers.**

---

## Strategy #1 — Multi-Timeframe Momentum (Active)

> **Status: ENABLED** (`mtf_momentum.enabled: true`)

This is the primary and currently active strategy. It is a **5-minute EMA crossover trend-following strategy** that only takes trades aligned with the **15-minute trend direction**.

### Indicators Used

All indicators are calculated using the **`ta`** (Technical Analysis) library on **close prices** unless stated otherwise.

| Indicator                        | Price Used          | Timeframe | Parameters                  |
|----------------------------------|---------------------|-----------|-----------------------------|
| **EMA Fast (12)**                | `close`             | 5m        | `window=12`                 |
| **EMA Slow (26)**                | `close`             | 5m        | `window=26`                 |
| **EMA Higher (20)**              | `close`             | 15m       | `window=20`                 |
| **RSI (14)**                     | `close`             | 5m        | `window=14`                 |
| **ATR (14)**                     | `high`, `low`, `close` | 5m     | `window=14`                 |
| **MACD** *(optional, disabled)*  | `close`             | 5m        | `fast=12, slow=26, sign=9`  |

### Price Data Used

| Purpose                      | Price Column(s)          | Timeframe |
|------------------------------|--------------------------|-----------|
| EMA calculation              | `close`                  | 5m        |
| RSI calculation              | `close`                  | 5m        |
| ATR calculation              | `high`, `low`, `close`   | 5m        |
| Higher TF trend detection    | `close` vs EMA(20)       | 15m       |
| Entry signal (crossover)     | `ema_fast` vs `ema_slow` | 5m        |
| Trade execution (backtest)   | Next candle's `open`     | 1m        |
| Trade execution (live)       | Market price (taker)     | Real-time |
| Stop loss hit detection      | `low` (long), `high` (short) | 1m    |
| Take profit hit detection    | `high` (long), `low` (short) | 1m    |
| Trailing stop evaluation     | `close`                  | 1m        |
| Momentum decay exit          | `close` (RSI of 5m)     | 5m        |

### Higher Timeframe Trend Detection

The 15-minute EMA(20) determines the trend direction. A **threshold buffer of 0.3%** prevents whipsaw signals near the EMA.

```
UPTREND:    15m close > EMA(20) × (1 + 0.003)
DOWNTREND:  15m close < EMA(20) × (1 - 0.003)
NEUTRAL:    Neither condition met → NO TRADES
```

- Trend is evaluated using the **latest completed 15m candle** (no look-ahead bias).
- If trend is `None` (neutral/insufficient data), **no entry signal is generated**.

### Entry Conditions

Entries are checked **only on 5-minute candle boundaries** (when a 5m candle closes).

#### Long Entry (all must be true)

1. **15m Trend = UP** — Close is above EMA(20) by at least 0.3%
2. **5m EMA Bullish Crossover** — `EMA(12)` crosses above `EMA(26)` on the current candle:
   - Previous candle: `ema_fast <= ema_slow`
   - Current candle: `ema_fast > ema_slow`
3. **RSI Filter** — RSI(14) is between **40 and 70** (not oversold, not overbought)
4. **MACD Confirmation** *(disabled)* — MACD histogram > 0 (when `macd_confirmation: true`)
5. **Cooldown** — At least 45 minutes since last entry
6. **Hour Filter** — Current UTC hour is not in `skip_hours_utc` list (currently empty)
7. **Risk Manager Approval** — All risk limits pass (see Risk Management section)

#### Short Entry (all must be true)

1. **15m Trend = DOWN** — Close is below EMA(20) by at least 0.3%
2. **5m EMA Bearish Crossover** — `EMA(12)` crosses below `EMA(26)` on the current candle:
   - Previous candle: `ema_fast >= ema_slow`
   - Current candle: `ema_fast < ema_slow`
3. **RSI Filter** — RSI(14) is between **30 and 60** (not overbought, not oversold)
4. **MACD Confirmation** *(disabled)* — MACD histogram < 0 (when `macd_confirmation: true`)
5. **Cooldown** — At least 45 minutes since last entry
6. **Hour Filter** — Current UTC hour is not in `skip_hours_utc` list (currently empty)
7. **Risk Manager Approval** — All risk limits pass

#### Execution Price

- **Backtest**: Entry is executed at the **next 1-minute candle's `open`** price after the signal (no look-ahead bias).
- **Live**: Entry is executed via a **market order** (taker fee) at current market price.

### Exit Conditions

Exits are checked in the following priority order on **every 1-minute candle** (backtest) or **every 3 seconds** (live):

| Priority | Exit Type             | Condition                                                                                   | Exit Price                        |
|----------|-----------------------|---------------------------------------------------------------------------------------------|-----------------------------------|
| 1        | **Stop Loss**         | 1m `low` ≤ SL price (long) or 1m `high` ≥ SL price (short)                                | SL price (fixed)                  |
| 2        | **Take Profit**       | 1m `high` ≥ TP price (long) or 1m `low` ≤ TP price (short)                                | TP price (fixed)                  |
| 3        | **Trailing Stop**     | Drawback from highest PnL exceeds adaptive distance (see below)                             | Current `close`                   |
| 4        | **Momentum Decay**    | After 25 min: RSI < 45 for longs, RSI > 55 for shorts                                      | Current `close`                   |
| 5        | **Time Exit**         | Trade duration ≥ 90 minutes                                                                | Current `close`                   |
| 6        | **Backtest End**      | Force-close all open positions at end of backtest                                           | Last candle `close`               |

SL is checked **before** TP on each candle (conservative assumption — if both could trigger on the same candle, SL wins).

---

## Strategy #2 — Bollinger Band Scalp (Disabled)

> **Status: DISABLED** (`bollinger_scalp.enabled: false`)

A mean-reversion scalping strategy on 3-minute candles, designed for ranging/sideways markets. It is only activated when the Regime Filter detects `RANGING` or `TRANSITIONAL` market conditions.

| Property         | Value                      |
|------------------|----------------------------|
| Timeframe        | 3-minute candles           |
| BB Period        | 20                         |
| BB Std Dev       | 2.0                        |
| RSI Oversold     | < 42 (long entry)          |
| RSI Overbought   | > 58 (short entry)         |
| Volume Filter    | Volume > 0.3× SMA(20)     |
| Stop Loss        | 0.3% beyond the touched band |
| Take Profit      | Middle Bollinger Band      |

**Long**: Price touches lower band + closes above it + RSI < 42 + volume OK.
**Short**: Price touches upper band + closes below it + RSI > 58 + volume OK.
**BB Breakout Exit**: If BB width exceeds 2.5× its SMA(50), exit at close (volatility expansion).

---

## Market Regime Filter

The regime filter classifies the market every **15 minutes** and determines which strategy is allowed to trade.

### Regime Detection

| Metric           | Source                                  | Calculation                                   |
|------------------|-----------------------------------------|-----------------------------------------------|
| **ADX (14)**     | 15m `high`, `low`, `close`              | ADX indicator, window=14                      |
| **ATR Ratio**    | 5m `high`, `low`, `close`               | Current ATR(14) / SMA(50) of ATR(14)          |

### Regime Classification

| Regime           | Condition        | Allowed Strategy     |
|------------------|------------------|----------------------|
| **TRENDING**     | ADX > 25         | `mtf_momentum`       |
| **RANGING**      | ADX < 20         | `bollinger_scalp`    |
| **TRANSITIONAL** | 20 ≤ ADX ≤ 25   | Either (reduced)     |

### Volatility Overrides (Trading Paused)

| Override           | Condition          | Action                                      |
|--------------------|--------------------|---------------------------------------------|
| **HIGH_VOLATILITY**| ATR Ratio > 3.0    | Pause all trading for 15 minutes            |
| **DEAD_MARKET**    | ATR Ratio < 0.3    | No trading (insufficient movement)          |

---

## Risk Management

The bot implements **8 layers of risk management** that work together to protect capital.

### Position Sizing

Position size is calculated **per-trade** based on risk amount and stop loss distance:

```
risk_amount    = capital × 3.0%                     (risk per trade)
position_margin = risk_amount / (sl_pct / 100 × leverage)
max_margin     = capital × 36.0%                    (maximum position size)
final_margin   = min(position_margin, max_margin)
notional_value = final_margin × leverage (12x)
size_contracts = notional_value / entry_price
```

| Parameter              | Value  | Description                             |
|------------------------|--------|-----------------------------------------|
| `risk_per_trade_pct`   | 3.0%   | Max capital risked per trade            |
| `max_position_size_pct`| 36.0%  | Max margin allocation per trade         |
| `leverage`             | 12x    | Cross margin leverage                   |

### Stop Loss Logic

The stop loss is **ATR-adaptive** — it uses the larger of the fixed percentage or the ATR-derived distance:

```
ATR-based SL = (ATR × 1.5) / close × 100
Fixed SL     = 0.4%
Final SL     = max(ATR-based SL, Fixed SL)
```

| Parameter          | Value | Description                                       |
|--------------------|-------|---------------------------------------------------|
| `stop_loss_pct`    | 0.4%  | Minimum stop loss distance                        |
| `atr_sl_multiplier`| 1.5   | ATR multiplier for dynamic SL                     |

**SL Price Calculation:**
- Long: `entry_price × (1 - sl_pct / 100)`
- Short: `entry_price × (1 + sl_pct / 100)`

SL is placed on-exchange as a `STOP_MARKET` order with `reduceOnly: true`.

### Take Profit Logic

The take profit is also **ATR-adaptive**:

```
ATR-based TP = (ATR × 5.0) / close × 100
Fixed TP     = 1.0%
Final TP     = max(ATR-based TP, Fixed TP)
```

| Parameter          | Value | Description                                       |
|--------------------|-------|---------------------------------------------------|
| `take_profit_pct`  | 1.0%  | Minimum take profit distance                      |
| `atr_tp_multiplier`| 5.0   | ATR multiplier for dynamic TP                     |

**TP Price Calculation:**
- Long: `entry_price × (1 + tp_pct / 100)`
- Short: `entry_price × (1 - tp_pct / 100)`

TP is placed on-exchange as a `TAKE_PROFIT_MARKET` order with `reduceOnly: true`.

### Trailing Stop (Adaptive)

The trailing stop **tightens as profit grows**, locking in more profit on extended moves while giving early winners room to run.

| Parameter                  | Value | Description                                     |
|----------------------------|-------|-------------------------------------------------|
| `trailing_stop_activation` | 0.8%  | PnL % threshold to activate trailing stop       |
| `trailing_stop_distance`   | 0.35% | Initial drawback tolerance from peak PnL        |

**Adaptive Tightening Formula:**

```python
profit_ratio     = highest_pnl_pct / trailing_activation    # e.g. 1.6% / 0.8% = 2.0
tightening       = min(0.4, (profit_ratio - 1) × 0.2)      # e.g. min(0.4, 0.2) = 0.2
adaptive_distance = trailing_distance × (1 - tightening)    # e.g. 0.35% × 0.8 = 0.28%
```

**Trigger**: If `(highest_pnl_pct - current_pnl_pct) >= adaptive_distance`, the trailing stop fires and exits at current close.

At **activation (0.8% PnL)**, the full distance (0.35%) is used. At **2× activation (1.6%)**, distance shrinks to **80%** (0.28%). At **3× activation (2.4%)**, distance shrinks to **60%** (0.21%). Maximum tightening is capped at 40%.

### Breakeven Stop

When unrealized PnL reaches **+0.3%**, the stop loss is moved to the **entry price** (breakeven).

| Parameter                  | Value | Description                                     |
|----------------------------|-------|-------------------------------------------------|
| `breakeven_threshold_pct`  | 0.3%  | PnL threshold to move SL to entry price         |

- In backtest: breakeven activates **at end of candle** and takes effect on the **next candle** (no look-ahead bias).
- In live: the exchange SL order is cancelled and re-placed at the entry price.

### Momentum Decay Exit

A custom exit that catches trades where momentum has reversed but price hasn't hit the stop loss yet. This reduces average stop loss size.

| Condition                    | Trigger                                  |
|------------------------------|------------------------------------------|
| Trade has been open ≥ 25 min | AND                                      |
| **Long**: RSI(14) < 45      | Exit at current close                    |
| **Short**: RSI(14) > 55     | Exit at current close                    |

### Time-Based Exit

Any trade open for more than **90 minutes** is force-closed at the current close price.

| Parameter                    | Value    | Description                          |
|------------------------------|----------|--------------------------------------|
| `max_trade_duration_minutes` | 90 min   | Maximum time a trade can remain open |

### Trade Frequency Limits

| Parameter              | Value  | Description                                       |
|------------------------|--------|---------------------------------------------------|
| `max_open_positions`   | 1      | Only one position at a time                       |
| `max_trades_per_hour`  | 10     | Maximum entries per clock hour                    |
| `max_trades_per_day`   | 40     | Maximum entries per calendar day                  |
| **Cooldown**           | 45 min | Minimum time between consecutive entries          |

### Loss Limits & Circuit Breakers

| Parameter                        | Value  | Description                                      |
|----------------------------------|--------|--------------------------------------------------|
| `max_daily_loss_pct`             | 4.0%   | Max daily drawdown before trading stops           |
| `max_weekly_loss_pct`            | 10.0%  | Max weekly drawdown before trading stops          |
| `max_consecutive_losses_pause`   | 8      | Pause trading after 8 consecutive losses          |
| `consecutive_loss_pause_minutes` | 15 min | Duration of pause after consecutive losses        |

- **Daily loss limit**: If `daily_pnl / capital < -4.0%`, no new trades are opened for the rest of the day.
- **Weekly loss limit**: If `weekly_pnl / capital < -10.0%`, no new trades are opened for the rest of the week.
- **Consecutive loss pause**: After 8 consecutive losing trades, the bot pauses for 15 minutes before allowing new entries.

### Fee & Slippage Accounting

Fees are deducted on **both entry and exit** in backtest and live to ensure realistic PnL.

| Parameter   | Value  | Description                                |
|-------------|--------|--------------------------------------------|
| `maker`     | 0.02%  | Maker fee rate                             |
| `taker`     | 0.05%  | Taker fee rate (used for market orders)    |
| `slippage`  | 0.005% | Estimated slippage per execution           |

**Entry fee** = notional × (taker + slippage) = notional × 0.055%
**Exit fee** = notional × (taker + slippage) = notional × 0.055%
**Total round-trip fee** = notional × 0.11%

Both backtest and live use **taker fees** (market orders) for consistency.

### Minimum Risk-Reward Ratio

Before opening any trade, the bot checks the **fee-adjusted risk:reward ratio**:

```
total_fee_pct  = (taker + slippage) × 2    = 0.11%
effective_tp   = tp_pct - total_fee_pct
effective_sl   = sl_pct + total_fee_pct
R:R            = effective_tp / effective_sl
```

| Parameter             | Value | Description                                    |
|-----------------------|-------|------------------------------------------------|
| `min_risk_reward_ratio`| 0.8  | Minimum fee-adjusted R:R to accept a trade     |

If the R:R is below 0.8 after fees, the trade is rejected.

### Cooldown Between Trades

| Mode     | Cooldown  | Description                                                  |
|----------|-----------|--------------------------------------------------------------|
| Backtest | 45 min    | Minimum 2,700 seconds between entries                        |
| Live     | 45 min    | Same 2,700 second cooldown, tracked in `PositionTracker`     |

---

## Execution Flow

### Backtest Mode

```
1. Load config → Fetch 1m OHLCV data from Binance (cached to CSV)
2. Resample 1m → 3m, 5m, 15m candles
3. Pre-compute indicators on all timeframes
4. Pre-compute regime indicators (ADX on 15m, ATR ratio on 5m)
5. Build O(1) index maps: 1m → 3m/5m/15m using searchsorted
6. Iterate every 1m candle:
   a. Reset daily/hourly/weekly counters
   b. Check regime every 15 min (using completed candles only)
   c. Check exits on open positions (SL → TP → trailing → strategy → breakeven)
   d. On 5m boundary: check MTF entry signal if regime allows
   e. On 3m boundary: check BB entry signal if regime allows (disabled)
   f. Execute entry at NEXT 1m candle's open price
   g. Record equity curve
7. Force-close remaining positions at backtest end
8. Generate report (text + CSV + equity chart PNG)
```

### Live/Paper Mode

```
1. Load config → Connect to Binance Futures API (demo or live)
2. Set leverage (12x) and margin mode (cross)
3. Sync with any existing exchange position
4. Restore risk state from SQLite trade history
5. Main loop (every 3 seconds):
   a. If position open → monitor: breakeven, trailing stop, time exit, exchange SL/TP fill
   b. Every 30s with no position → clean stale orders
   c. On 5m candle close (wait 5s for data to settle):
      i.   Fetch 200× 5m candles and 100× 15m candles
      ii.  Calculate indicators (same functions as backtest)
      iii. Check regime (every 15 min)
      iv.  Check strategy-specific exits on 5m boundary
      v.   Check entry signal (same logic as backtest)
      vi.  Execute via market order → place SL + TP on exchange
   d. Every 6 hours → send Telegram status notification
6. On shutdown → send Telegram notification
```

**Live-Backtest Parity**: The live trader uses the **exact same** strategy, indicator, and risk management classes as the backtest engine, ensuring consistent behavior.

---

## Project Structure

```
2903FinalStrategy-main/
├── config/
│   └── config.yaml              # All strategy, risk, and exchange configuration
├── src/
│   ├── main.py                  # Entry point: backtest runner + LiveTrader class
│   ├── strategies/
│   │   ├── base_strategy.py     # Abstract base class for strategies
│   │   ├── mtf_momentum.py      # Multi-Timeframe Momentum strategy (ACTIVE)
│   │   ├── bollinger_scalp.py   # Bollinger Band Scalp strategy (disabled)
│   │   └── regime_filter.py     # Market regime detection (ADX + ATR)
│   ├── risk/
│   │   └── risk_manager.py      # Position sizing, loss limits, trade gating
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_manager.py     # Exchange order placement (market, SL, TP)
│   │   └── position_tracker.py  # Live position state + JSON persistence
│   ├── exchange/
│   │   ├── binance_client.py    # Binance USDM Futures API wrapper (ccxt)
│   │   └── data_fetcher.py      # OHLCV data download + CSV caching + resampling
│   ├── backtesting/
│   │   ├── engine.py            # Backtest engine (1m candle iteration)
│   │   └── report.py            # Performance report + equity chart generation
│   ├── database/
│   │   └── trade_store.py       # SQLite trade persistence for live mode
│   └── utils/
│       ├── helpers.py           # Config loading utilities
│       ├── logger.py            # Loguru logger setup
│       └── notifier.py          # Telegram Bot API notifications
├── run_backtest.py              # Quick-start backtest script
├── backtest_monthly.py          # Month-by-month backtest runner
├── analyze_trades.py            # Trade analysis utility
├── test_live_orders.py          # Live order placement test
├── test_apr4_backtest.py        # Specific date range backtest test
├── test_coins.py                # Multi-coin testing
├── requirements.txt             # Python dependencies
├── data/                        # Cached OHLCV CSVs + live state JSON
├── reports/                     # Backtest reports + equity charts
└── logs/                        # Log files
```

---

## Configuration Reference

All configuration is in `config/config.yaml`. Below is every parameter with its current active value:

### Exchange

| Key           | Value        | Description                              |
|---------------|--------------|------------------------------------------|
| `name`        | `binance`    | Exchange name                            |
| `testnet`     | `true`       | Use demo API (demo-fapi.binance.com)     |
| `symbol`      | `ETH/USDT`   | Trading pair                             |
| `market_type` | `future`     | Perpetual futures                        |

### Trading

| Key        | Value      | Description                              |
|------------|------------|------------------------------------------|
| `leverage` | `12`       | Cross margin leverage                    |
| `mode`     | `backtest` | Current mode (backtest/paper/live)       |

### MTF Momentum Strategy

| Key                          | Value   | Description                                   |
|------------------------------|---------|-----------------------------------------------|
| `enabled`                    | `true`  | Strategy is active                            |
| `higher_tf`                  | `15m`   | Higher timeframe for trend                    |
| `entry_tf`                   | `5m`    | Entry signal timeframe                        |
| `ema_higher`                 | `20`    | EMA period on 15m for trend                   |
| `ema_fast`                   | `12`    | Fast EMA period on 5m                         |
| `ema_slow`                   | `26`    | Slow EMA period on 5m                         |
| `adx_period`                 | `14`    | ADX lookback period                           |
| `adx_threshold`              | `20`    | ADX threshold for trending                    |
| `rsi_period`                 | `14`    | RSI lookback period                           |
| `rsi_long_min`               | `40`    | Min RSI for long entry                        |
| `rsi_long_max`               | `70`    | Max RSI for long entry                        |
| `rsi_short_min`              | `30`    | Min RSI for short entry                       |
| `rsi_short_max`              | `60`    | Max RSI for short entry                       |
| `volume_multiplier`          | `0.5`   | Volume filter multiplier                      |
| `trend_threshold_pct`        | `0.3`   | % buffer for trend detection                  |
| `take_profit_pct`            | `1.0`   | Minimum TP distance (%)                       |
| `stop_loss_pct`              | `0.4`   | Minimum SL distance (%)                       |
| `atr_sl_multiplier`          | `1.5`   | ATR × multiplier for dynamic SL               |
| `atr_tp_multiplier`          | `5.0`   | ATR × multiplier for dynamic TP               |
| `trailing_stop_activation`   | `0.8`   | PnL % to activate trailing stop               |
| `trailing_stop_distance`     | `0.35`  | Initial trailing drawback tolerance (%)       |
| `max_trade_duration_minutes` | `90`    | Force-close after this duration               |
| `breakeven_threshold_pct`    | `0.3`   | Move SL to entry at this PnL %                |
| `macd_confirmation`          | `false` | Require MACD histogram confirmation           |
| `skip_hours_utc`             | `[]`    | Hours to skip (empty = trade all hours)       |

### Risk Management

| Key                              | Value  | Description                              |
|----------------------------------|--------|------------------------------------------|
| `risk_per_trade_pct`             | `3.0`  | % of capital risked per trade            |
| `max_position_size_pct`          | `36.0` | Max margin as % of capital               |
| `max_open_positions`             | `1`    | Maximum concurrent positions             |
| `max_trades_per_hour`            | `10`   | Hourly trade cap                         |
| `max_trades_per_day`             | `40`   | Daily trade cap                          |
| `max_daily_loss_pct`             | `4.0`  | Daily loss circuit breaker               |
| `max_consecutive_losses_pause`   | `8`    | Pause after N consecutive losses         |
| `consecutive_loss_pause_minutes` | `15`   | Pause duration (minutes)                 |
| `max_weekly_loss_pct`            | `10.0` | Weekly loss circuit breaker              |
| `min_risk_reward_ratio`          | `0.8`  | Minimum fee-adjusted R:R                 |

### Fees

| Key        | Value   | Description                              |
|------------|---------|------------------------------------------|
| `maker`    | `0.02`  | Maker fee (%)                            |
| `taker`    | `0.05`  | Taker fee (%)                            |
| `slippage` | `0.005` | Estimated slippage (%)                   |

### Backtest

| Key               | Value        | Description                          |
|-------------------|--------------|--------------------------------------|
| `start_date`      | `2025-01-01` | Backtest start date                  |
| `end_date`        | `2025-12-31` | Backtest end date                    |
| `initial_capital`  | `1000`       | Starting capital (USDT)              |
| `data_directory`  | `data/`      | Where OHLCV CSVs are cached         |
| `report_directory`| `reports/`   | Where reports are saved              |

---

## Installation & Usage

### Prerequisites

- Python 3.11+
- Binance Futures account (demo or live)

### Install

```bash
pip install -r requirements.txt
```

### Run Backtest

```bash
python run_backtest.py
```

Or with explicit mode:

```bash
python -m src.main --mode backtest --config config/config.yaml
```

### Run Live/Paper Trading

1. Add your Binance API credentials to `config/config.yaml`
2. Set `testnet: true` for demo or `testnet: false` for live
3. Run:

```bash
python -m src.main --mode live --config config/config.yaml
```

### Backtest Report Output

Reports are saved to `reports/` and include:
- `backtest_report.txt` — Full performance summary (returns, win rate, Sharpe, Sortino, drawdown, strategy breakdown, exit reasons, fees)
- `trades.csv` — Every trade with entry/exit prices, PnL, fees, duration, exit reason
- `equity_curve.png` — Equity curve + drawdown chart

---

## Dependencies

| Package      | Version  | Purpose                                |
|--------------|----------|----------------------------------------|
| `ccxt`       | ≥ 4.0.0  | Binance Futures API connectivity       |
| `pandas`     | ≥ 2.0.0  | Data manipulation and resampling       |
| `ta`         | ≥ 0.11.0 | Technical indicators (EMA, RSI, ATR, BB, MACD, ADX) |
| `numpy`      | ≥ 1.24.0 | Numerical operations                   |
| `pyyaml`     | ≥ 6.0    | YAML config file parsing               |
| `loguru`     | ≥ 0.7.0  | Structured logging                     |
| `matplotlib` | ≥ 3.7.0  | Equity curve chart generation          |
| `tabulate`   | ≥ 0.9.0  | Report table formatting                |

---

## Risk Disclaimer

This software is provided for educational and research purposes only. Cryptocurrency trading involves substantial risk of loss. Past backtest performance does not guarantee future results. Use at your own risk.
