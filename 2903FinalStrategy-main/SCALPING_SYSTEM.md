# Prompt: Build an ETH/USDT Futures Scalping Bot with Backtesting

## Project Overview

Build a complete Python-based algorithmic scalping trading bot for **ETH/USDT perpetual futures on Binance** with **10-15x leverage**. The bot must implement two strategies, automatically switch between them based on market regime, include a full backtesting engine, and support live paper trading and real trading modes.

---

## Tech Stack

- **Language:** Python 3.11+
- **Exchange API:** `ccxt` (Binance Futures)
- **Indicators:** `pandas-ta` or `ta-lib`
- **Data:** `pandas`, `numpy`
- **Backtesting:** Custom engine (no external backtesting library — build from scratch for full control)
- **Logging:** `loguru` for structured logging
- **Config:** YAML config file for all tunable parameters
- **Notifications:** Telegram bot alerts (optional, via `python-telegram-bot`)
- **Storage:** SQLite for trade logs and performance tracking
- **Visualization:** `matplotlib` or `plotly` for backtest results

---

## Project Structure

```
eth_scalping_bot/
├── config/
│   └── config.yaml              # All configurable parameters
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point (backtest / paper / live modes)
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── binance_client.py    # Binance futures API wrapper via ccxt
│   │   └── data_fetcher.py      # Historical OHLCV data downloader
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base_strategy.py     # Abstract base class for strategies
│   │   ├── mtf_momentum.py      # Strategy #1: Multi-Timeframe Momentum Scalp
│   │   ├── bollinger_scalp.py   # Strategy #2: Bollinger Band Scalp
│   │   └── regime_filter.py     # Market regime detection + strategy switcher
│   ├── risk/
│   │   ├── __init__.py
│   │   └── risk_manager.py      # Position sizing, stop loss, daily limits
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── engine.py            # Backtesting engine
│   │   ├── data_loader.py       # Load and prepare historical data
│   │   └── report.py            # Performance metrics and visualization
│   ├── execution/
│   │   ├── __init__.py
│   │   └── order_manager.py     # Order placement, tracking, cancellation
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logger.py            # Logging setup
│   │   ├── telegram_alert.py    # Telegram notifications
│   │   └── helpers.py           # Common utility functions
│   └── database/
│       ├── __init__.py
│       └── trade_store.py       # SQLite trade log storage
├── data/
│   └── .gitkeep                 # Downloaded OHLCV data stored here
├── reports/
│   └── .gitkeep                 # Backtest reports saved here
├── tests/
│   ├── test_strategies.py
│   ├── test_risk_manager.py
│   └── test_backtest_engine.py
├── requirements.txt
└── README.md
```

---

## Strategy #1: Multi-Timeframe Momentum Scalp

### Logic

```
HIGHER TIMEFRAME (15m):
- Calculate 50 EMA on 15m candles
- Determine trend direction:
    - 50 EMA slope positive (current > 3 bars ago) = UPTREND
    - 50 EMA slope negative (current < 3 bars ago) = DOWNTREND
- Calculate ADX(14) on 15m:
    - ADX > 20 = trend confirmed
    - ADX <= 20 = no trend, skip this strategy

ENTRY TIMEFRAME (1m):
- Calculate 21 EMA on 1m candles
- Calculate RSI(14) on 1m candles

LONG ENTRY (all must be true):
    1. 15m trend = UPTREND (50 EMA slope positive)
    2. 15m ADX(14) > 20
    3. 1m price pulls back and touches or crosses below 21 EMA
    4. 1m price closes back above 21 EMA (bounce confirmation)
    5. 1m RSI(14) > 40 and < 70 (not oversold or overbought)
    6. Current candle volume > 1.2x average volume of last 20 candles

SHORT ENTRY (all must be true):
    1. 15m trend = DOWNTREND (50 EMA slope negative)
    2. 15m ADX(14) > 20
    3. 1m price rallies and touches or crosses above 21 EMA
    4. 1m price closes back below 21 EMA (rejection confirmation)
    5. 1m RSI(14) < 60 and > 30
    6. Current candle volume > 1.2x average volume of last 20 candles

EXIT:
    - Take Profit: 0.25% from entry (configurable)
    - Stop Loss: 0.15% from entry (configurable)
    - Trailing Stop: Activate after 0.15% profit, trail at 0.08%
    - Time-based exit: Close if trade open > 15 minutes with no TP/SL hit
    - Exit if 15m trend flips while in trade
```

---

## Strategy #2: Bollinger Band Scalp

### Logic

```
TIMEFRAME: 3m candles

INDICATORS:
- Bollinger Bands: SMA(20), 2 standard deviations
- BB Width: (Upper - Lower) / Middle
- RSI(14)
- Volume SMA(20)

RANGE DETECTION:
- BB Width < BB Width SMA(50) = market is in squeeze/range = ACTIVE
- BB Width > BB Width SMA(50) = market is expanding = INACTIVE (skip)

LONG ENTRY (all must be true):
    1. BB Width indicates range (squeeze active)
    2. Price touches or pierces lower Bollinger Band
    3. RSI(14) < 35 (approaching oversold)
    4. Candle shows rejection: close is above the lower band even if wick went below
    5. Volume on signal candle > 0.8x average (some participation, not dead)

SHORT ENTRY (all must be true):
    1. BB Width indicates range (squeeze active)
    2. Price touches or pierces upper Bollinger Band
    3. RSI(14) > 65 (approaching overbought)
    4. Candle shows rejection: close is below the upper band even if wick went above
    5. Volume on signal candle > 0.8x average

EXIT:
    - Take Profit #1: Middle band (SMA 20) — close 50% of position
    - Take Profit #2: Opposite band — close remaining 50%
    - Stop Loss: 0.2% beyond the band where you entered
    - If BB Width suddenly expands (breakout detected), close immediately
```

---

## Regime Filter (Strategy Switcher)

```
REGIME DETECTION runs every 15 minutes on 15m candles:

Inputs:
    - ADX(14) on 15m
    - ATR(14) on 5m (current vs 50-period average)

Logic:
    IF ADX(14) > 25:
        regime = "TRENDING"
        active_strategy = Multi-Timeframe Momentum Scalp (Strategy #1)

    ELIF ADX(14) < 20:
        regime = "RANGING"
        active_strategy = Bollinger Band Scalp (Strategy #2)

    ELIF 20 <= ADX(14) <= 25:
        regime = "TRANSITIONAL"
        active_strategy = None (reduce position size by 50% on either strategy)

    VOLATILITY CHECK (applied on top of regime):
    IF ATR(14) on 5m > 2.0x its 50-period SMA:
        override = "HIGH_VOLATILITY"
        action = pause trading for 30 minutes, then re-evaluate

    IF ATR(14) on 5m < 0.4x its 50-period SMA:
        override = "DEAD_MARKET"
        action = pause trading (no opportunity)

Log every regime change with timestamp.
```

---

## Risk Management Module

```yaml
# All values configurable in config.yaml

risk_per_trade: 0.75          # % of total capital risked per trade
max_position_size: 5          # % of capital per single position
leverage: 12                  # Default leverage (10-15 range)
max_open_positions: 2         # Maximum simultaneous positions
max_trades_per_hour: 8        # Prevent overtrading
max_trades_per_day: 30        # Hard daily limit
max_daily_loss: 3.0           # % — stop trading for the day if hit
max_daily_loss_streak: 4      # Consecutive losses — pause 1 hour
max_weekly_loss: 8.0          # % — stop trading for the week
min_risk_reward: 1.5          # Minimum R:R ratio to take trade

# Position sizing formula:
# position_size = (account_balance * risk_per_trade%) / (stop_loss_distance% * leverage)

# Fee accounting:
binance_maker_fee: 0.02       # % (with BNB discount)
binance_taker_fee: 0.05       # %
estimated_slippage: 0.01      # %
```

### Risk Manager Must:
1. Calculate position size dynamically based on account balance and stop distance
2. Reject trades that don't meet minimum R:R ratio
3. Track daily P&L and enforce daily/weekly loss limits
4. Track consecutive losses and pause after N losses in a row
5. Ensure total exposure never exceeds a configured maximum
6. Log every risk decision (trade taken, trade rejected with reason)
7. Account for fees + slippage in every profit calculation

---

## Backtesting Engine

### Data Requirements
- Download 6-12 months of **1-minute OHLCV candle data** for ETH/USDT from Binance via ccxt
- Also download 3m and 15m data (or resample from 1m data)
- Store locally as CSV or Parquet files in `data/` directory
- Data columns: `timestamp, open, high, low, close, volume`

### Engine Design

```
class BacktestEngine:

    Inputs:
        - Historical OHLCV data (multiple timeframes)
        - Strategy instance (Strategy #1 or #2 or combined with regime filter)
        - Risk manager instance
        - Initial capital (default: $1000)
        - Leverage (default: 12x)
        - Fee model (maker/taker fees + slippage)

    Process:
        1. Iterate through candles chronologically (1m resolution)
        2. At each candle:
            a. Update all indicators (use only past data — NO lookahead bias)
            b. Check regime filter → determine active strategy
            c. Check for exit signals on open positions
            d. Check for entry signals from active strategy
            e. Apply risk manager checks before opening new trades
            f. Record trade if signal passes all filters
        3. Track: entries, exits, P&L (gross and net of fees), drawdown

    CRITICAL RULES:
        - NO lookahead bias: indicators and signals use only data available at that candle
        - All prices execute at next candle's open (not current candle's close)
        - Fees and slippage applied to every entry and exit
        - Stop loss checked against candle high/low (not just close)
        - If candle high/low would hit BOTH SL and TP, assume SL hit first (conservative)
```

### Backtest Modes
1. **Single strategy backtest** — Run Strategy #1 or #2 alone
2. **Combined backtest** — Run both strategies with regime filter
3. **Walk-forward optimization** — Split data into rolling windows, optimize on train, validate on test
4. **Parameter sweep** — Grid search over key parameters (EMA lengths, RSI thresholds, TP/SL percentages)

### Performance Report

Generate and display the following after each backtest:

```
=== BACKTEST REPORT ===
Period: 2025-01-01 to 2025-12-31
Starting Capital: $1,000
Ending Capital: $X,XXX
Leverage Used: 12x

--- Returns ---
Total Return: XX.XX%
Annualized Return: XX.XX%
Monthly Returns: [table by month]
Best Month: XX.XX%
Worst Month: XX.XX%

--- Trades ---
Total Trades: XXX
Win Rate: XX.XX%
Average Win: $XX.XX (XX.XX%)
Average Loss: $XX.XX (XX.XX%)
Largest Win: $XX.XX
Largest Loss: $XX.XX
Average Trade Duration: XX minutes
Profit Factor: X.XX (gross profit / gross loss)
Expectancy: $X.XX per trade

--- Risk ---
Max Drawdown: XX.XX% (date range)
Max Consecutive Losses: X
Sharpe Ratio: X.XX
Sortino Ratio: X.XX
Calmar Ratio: X.XX

--- Strategy Breakdown ---
Strategy #1 (MTF Momentum): XX trades, XX% win rate, $XXX profit
Strategy #2 (Bollinger Scalp): XX trades, XX% win rate, $XXX profit
Regime: Trending XX% of time, Ranging XX% of time, Transitional XX%

--- Fee Impact ---
Gross Profit: $X,XXX
Total Fees Paid: $XXX
Total Slippage Cost: $XXX
Net Profit: $X,XXX

=== CHARTS ===
1. Equity curve (capital over time)
2. Drawdown chart
3. Monthly returns heatmap
4. Trade distribution (profit/loss histogram)
5. Entry/exit points plotted on price chart (sample period)
6. Strategy allocation over time (which strategy was active)
7. Win rate by hour of day
8. Win rate by day of week
```

---

## Config File (config.yaml)

```yaml
# Exchange
exchange:
  name: binance
  api_key: ""              # Leave empty for backtest mode
  api_secret: ""
  testnet: true            # Use testnet for paper trading
  symbol: "ETH/USDT"
  market_type: "future"

# Trading
trading:
  leverage: 12
  mode: "backtest"         # backtest | paper | live
  trading_hours:           # UTC hours when bot is active
    - start: 0
      end: 24              # 24/7 for crypto

# Strategy #1: Multi-Timeframe Momentum
mtf_momentum:
  enabled: true
  higher_tf: "15m"
  entry_tf: "1m"
  ema_higher: 50
  ema_entry: 21
  adx_period: 14
  adx_threshold: 20
  rsi_period: 14
  rsi_long_min: 40
  rsi_long_max: 70
  rsi_short_min: 30
  rsi_short_max: 60
  volume_multiplier: 1.2
  take_profit_pct: 0.25
  stop_loss_pct: 0.15
  trailing_stop_activation: 0.15
  trailing_stop_distance: 0.08
  max_trade_duration_minutes: 15

# Strategy #2: Bollinger Band Scalp
bollinger_scalp:
  enabled: true
  timeframe: "3m"
  bb_period: 20
  bb_std: 2.0
  bb_width_sma: 50
  rsi_period: 14
  rsi_oversold: 35
  rsi_overbought: 65
  volume_multiplier: 0.8
  tp1_pct: 50              # % of position closed at TP1 (middle band)
  tp2_target: "opposite_band"
  stop_loss_beyond_band_pct: 0.2

# Regime Filter
regime:
  check_interval_minutes: 15
  adx_trending_threshold: 25
  adx_ranging_threshold: 20
  atr_high_vol_multiplier: 2.0
  atr_dead_market_multiplier: 0.4
  high_vol_pause_minutes: 30

# Risk Management
risk:
  risk_per_trade_pct: 0.75
  max_position_size_pct: 5.0
  max_open_positions: 2
  max_trades_per_hour: 8
  max_trades_per_day: 30
  max_daily_loss_pct: 3.0
  max_consecutive_losses_pause: 4
  consecutive_loss_pause_minutes: 60
  max_weekly_loss_pct: 8.0
  min_risk_reward_ratio: 1.5

# Fees
fees:
  maker: 0.02
  taker: 0.05
  slippage: 0.01

# Backtesting
backtest:
  start_date: "2025-04-01"
  end_date: "2025-12-31"
  initial_capital: 1000
  data_directory: "data/"
  report_directory: "reports/"

# Notifications
telegram:
  enabled: false
  bot_token: ""
  chat_id: ""

# Logging
logging:
  level: "INFO"            # DEBUG for development
  file: "logs/bot.log"
  console: true
```

---

## Execution Flow

### Backtest Mode
```
1. python main.py --mode backtest
2. Load historical data from data/ (download if not present)
3. Resample 1m data to 3m and 15m timeframes
4. Initialize both strategies + regime filter + risk manager
5. Run backtesting engine
6. Generate performance report + charts
7. Save results to reports/
```

### Paper Trading Mode
```
1. python main.py --mode paper
2. Connect to Binance testnet via ccxt
3. Set leverage to configured value
4. Start main loop:
    a. Fetch latest candles (1m, 3m, 15m)
    b. Run regime filter
    c. Run active strategy
    d. If signal → simulate order (log but don't execute on real exchange)
    e. Track virtual P&L
    f. Send Telegram alert on each trade
    g. Sleep until next candle close
5. Log all trades to SQLite database
```

### Live Trading Mode
```
1. python main.py --mode live
2. Connect to Binance mainnet via ccxt
3. Verify API permissions (futures trading enabled)
4. Set leverage and margin mode (cross/isolated — use ISOLATED)
5. Same logic as paper trading but execute real orders
6. Use LIMIT orders for entries (reduce fees)
7. Use STOP_MARKET orders for stop losses (guaranteed execution)
8. Implement heartbeat check every 60 seconds
9. Graceful shutdown: close all positions on SIGINT/SIGTERM
```

---

## Important Implementation Notes

1. **No lookahead bias in backtesting** — This is the #1 most critical rule. Every indicator must be calculated using only data available at that point in time.

2. **Candle completion** — Only generate signals on closed/completed candles, never on live/forming candles.

3. **Execution at next candle open** — In backtesting, when a signal fires on candle N's close, execute at candle N+1's open price.

4. **Stop loss on high/low** — During backtesting, check if candle's high or low would trigger stop loss before checking take profit. If the candle's range hits both, assume stop loss hit first (worst case).

5. **Rate limiting** — Respect Binance API rate limits. Add delays between API calls. Cache data where possible.

6. **Error handling** — The bot must handle network errors, API timeouts, and exchange maintenance gracefully. Retry with exponential backoff. Never leave orphaned positions.

7. **Atomic operations** — When placing entry + stop loss, if the stop loss order fails, immediately close the position.

8. **Data validation** — Check for gaps in candle data. Skip periods with missing data rather than interpolating.

9. **Timezone** — All timestamps in UTC. Convert only for display.

10. **Leverage warning** — At 12x leverage, a 8.33% adverse move = liquidation. The stop losses (0.15-0.2%) provide significant buffer, but the risk manager must enforce position sizing strictly.

---

## Build Sequence

Build and test in this exact order:

1. **Data Fetcher** — Download and store historical 1m ETH/USDT data
2. **Indicator Calculations** — Implement all indicators and verify against TradingView
3. **Strategy #1 (MTF Momentum)** — Implement and unit test signal generation
4. **Strategy #2 (Bollinger Scalp)** — Implement and unit test signal generation
5. **Regime Filter** — Implement and test switching logic
6. **Risk Manager** — Implement all rules and test edge cases
7. **Backtesting Engine** — Build with anti-lookahead safeguards
8. **Performance Report** — Generate full report with charts
9. **Run Backtests** — Validate strategies on 6+ months of data
10. **Paper Trading Mode** — Connect to Binance testnet
11. **Live Trading Mode** — Connect to Binance mainnet with small capital
12. **Telegram Alerts** — Add notifications

After each module, write tests and validate correctness before moving to the next.
