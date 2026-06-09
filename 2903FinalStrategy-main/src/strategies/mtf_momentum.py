import pandas as pd
import ta as ta_lib
from .base_strategy import BaseStrategy


class MTFMomentumStrategy(BaseStrategy):
    """5-minute EMA crossover trend-following strategy with ATR-based stops."""

    def __init__(self, config: dict):
        super().__init__(config)
        c = config["mtf_momentum"]
        self.ema_fast_period = c.get("ema_fast", 8)
        self.ema_slow_period = c.get("ema_slow", 21)
        self.ema_higher = c["ema_higher"]
        self.rsi_period = c["rsi_period"]
        self.tp_pct = c["take_profit_pct"]
        self.sl_pct = c["stop_loss_pct"]
        self.trailing_activation = c["trailing_stop_activation"]
        self.trailing_distance = c["trailing_stop_distance"]
        self.max_duration = c["max_trade_duration_minutes"]
        self.atr_sl_mult = c.get("atr_sl_multiplier", 1.5)
        self.atr_tp_mult = c.get("atr_tp_multiplier", 3.0)
        self.trend_threshold = c.get("trend_threshold_pct", 0.03) / 100
        self.rsi_long_min = c.get("rsi_long_min", 40)
        self.rsi_long_max = c.get("rsi_long_max", 70)
        self.rsi_short_min = c.get("rsi_short_min", 30)
        self.rsi_short_max = c.get("rsi_short_max", 60)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators on 5m dataframe."""
        df = df.copy()
        df["ema_fast"] = ta_lib.trend.ema_indicator(df["close"], window=self.ema_fast_period)
        df["ema_slow"] = ta_lib.trend.ema_indicator(df["close"], window=self.ema_slow_period)
        df["rsi"] = ta_lib.momentum.rsi(df["close"], window=self.rsi_period)
        df["atr"] = ta_lib.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
        return df

    def calculate_higher_tf_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate 15m indicators for trend direction."""
        df = df.copy()
        df["ema_higher"] = ta_lib.trend.ema_indicator(df["close"], window=self.ema_higher)
        return df

    def _get_trend(self, higher_tf_df: pd.DataFrame, current_ts: pd.Timestamp) -> str | None:
        available = higher_tf_df[higher_tf_df["timestamp"] <= current_ts]
        if len(available) < 4:
            return None
        latest = available.iloc[-1]
        ema_val = latest.get("ema_higher")
        if pd.isna(ema_val):
            return None
        price = latest["close"]
        t = self.trend_threshold
        if price > ema_val * (1 + t):
            return "up"
        elif price < ema_val * (1 - t):
            return "down"
        return None

    def _get_trend_by_idx(self, higher_tf_df: pd.DataFrame, idx: int) -> str | None:
        """O(1) trend lookup using pre-computed index."""
        if idx < 3:
            return None
        latest = higher_tf_df.iloc[idx]
        ema_val = latest.get("ema_higher")
        if pd.isna(ema_val):
            return None
        price = latest["close"]
        t = self.trend_threshold
        if price > ema_val * (1 + t):
            return "up"
        elif price < ema_val * (1 - t):
            return "down"
        return None

    def check_entry(self, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None, higher_tf_idx: int = None) -> dict | None:
        """Check for entry on 5m candles using EMA crossover."""
        if idx < 2 or higher_tf_df is None:
            return None
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        for col in ["ema_fast", "ema_slow", "rsi", "atr"]:
            if pd.isna(row.get(col, float("nan"))) or pd.isna(prev.get(col, float("nan"))):
                return None

        if higher_tf_idx is not None:
            trend = self._get_trend_by_idx(higher_tf_df, higher_tf_idx)
        else:
            trend = self._get_trend(higher_tf_df, row["timestamp"])
        if trend is None:
            return None

        # EMA crossover detection
        cross_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
        cross_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]

        # ATR-based dynamic stops
        atr_val = row["atr"]
        if atr_val > 0:
            sl_pct = max(self.sl_pct, (atr_val * self.atr_sl_mult) / row["close"] * 100)
            tp_pct = max(self.tp_pct, (atr_val * self.atr_tp_mult) / row["close"] * 100)
        else:
            sl_pct = self.sl_pct
            tp_pct = self.tp_pct

        # LONG: 15m trend up + EMA cross up with RSI confirmation
        if trend == "up" and cross_up:
            rsi_ok = self.rsi_long_min < row["rsi"] < self.rsi_long_max
            if rsi_ok:
                return {
                    "side": "long",
                    "strategy": "mtf_momentum",
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "trailing_activation": self.trailing_activation,
                    "trailing_distance": self.trailing_distance,
                    "max_duration": self.max_duration,
                }

        # SHORT: 15m trend down + EMA cross down with RSI confirmation
        if trend == "down" and cross_down:
            rsi_ok = self.rsi_short_min < row["rsi"] < self.rsi_short_max
            if rsi_ok:
                return {
                    "side": "short",
                    "strategy": "mtf_momentum",
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "trailing_activation": self.trailing_activation,
                    "trailing_distance": self.trailing_distance,
                    "max_duration": self.max_duration,
                }

        return None

    def check_exit(self, position: dict, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> dict | None:
        """Check exits on 5m candles."""
        row = df.iloc[idx]
        entry_ts = position["entry_ts"]

        # Normalise entry_ts to pd.Timestamp for consistent subtraction
        if isinstance(entry_ts, str):
            entry_ts = pd.Timestamp(entry_ts)
        elif not isinstance(entry_ts, pd.Timestamp):
            entry_ts = pd.Timestamp(entry_ts)
        if entry_ts.tzinfo is None:
            entry_ts = entry_ts.tz_localize("UTC")

        # Time-based exit
        if (row["timestamp"] - entry_ts).total_seconds() / 60 >= self.max_duration:
            return {"reason": "time_exit", "exit_price": row["close"]}

        return None  # SL/TP/trailing handled by engine
