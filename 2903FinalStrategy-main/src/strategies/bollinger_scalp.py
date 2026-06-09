import pandas as pd
import ta as ta_lib
from .base_strategy import BaseStrategy


class BollingerScalpStrategy(BaseStrategy):
    def __init__(self, config: dict):
        super().__init__(config)
        c = config["bollinger_scalp"]
        self.bb_period = c["bb_period"]
        self.bb_std = c["bb_std"]
        self.bb_width_sma = c["bb_width_sma"]
        self.rsi_period = c["rsi_period"]
        self.rsi_oversold = c["rsi_oversold"]
        self.rsi_overbought = c["rsi_overbought"]
        self.volume_multiplier = c["volume_multiplier"]
        self.tp1_pct = c["tp1_pct"] / 100.0
        self.sl_beyond_band_pct = c["stop_loss_beyond_band_pct"]

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        bb_indicator = ta_lib.volatility.BollingerBands(df["close"], window=self.bb_period, window_dev=self.bb_std)
        df["bb_upper"] = bb_indicator.bollinger_hband()
        df["bb_middle"] = bb_indicator.bollinger_mavg()
        df["bb_lower"] = bb_indicator.bollinger_lband()
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]
        df["bb_width_sma"] = df["bb_width"].rolling(window=self.bb_width_sma).mean()

        df["rsi"] = ta_lib.momentum.rsi(df["close"], window=self.rsi_period)
        df["vol_sma"] = df["volume"].rolling(window=20).mean()
        return df

    def check_entry(self, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> dict | None:
        if idx < 1:
            return None
        row = df.iloc[idx]

        for col in ["bb_upper", "bb_lower", "bb_middle", "rsi", "vol_sma"]:
            if pd.isna(row.get(col, float("nan"))):
                return None

        vol_ok = row["volume"] > self.volume_multiplier * row["vol_sma"] if row["vol_sma"] > 0 else True

        # LONG: price touches lower band, RSI oversold, rejection candle
        if row["low"] <= row["bb_lower"] and row["close"] > row["bb_lower"]:
            if row["rsi"] < self.rsi_oversold and vol_ok:
                sl_price = row["bb_lower"] * (1 - self.sl_beyond_band_pct / 100)
                sl_pct = abs(row["close"] - sl_price) / row["close"] * 100
                tp_pct = abs(row["bb_middle"] - row["close"]) / row["close"] * 100
                if tp_pct < 0.05:
                    return None
                return {
                    "side": "long",
                    "strategy": "bollinger_scalp",
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "tp2_target": "opposite_band",
                    "tp1_close_pct": self.tp1_pct,
                    "bb_middle": row["bb_middle"],
                    "bb_upper": row["bb_upper"],
                    "bb_lower": row["bb_lower"],
                }

        # SHORT: price touches upper band, RSI overbought, rejection candle
        if row["high"] >= row["bb_upper"] and row["close"] < row["bb_upper"]:
            if row["rsi"] > self.rsi_overbought and vol_ok:
                sl_price = row["bb_upper"] * (1 + self.sl_beyond_band_pct / 100)
                sl_pct = abs(sl_price - row["close"]) / row["close"] * 100
                tp_pct = abs(row["close"] - row["bb_middle"]) / row["close"] * 100
                if tp_pct < 0.05:
                    return None
                return {
                    "side": "short",
                    "strategy": "bollinger_scalp",
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "tp2_target": "opposite_band",
                    "tp1_close_pct": self.tp1_pct,
                    "bb_middle": row["bb_middle"],
                    "bb_upper": row["bb_upper"],
                    "bb_lower": row["bb_lower"],
                }

        return None

    def check_exit(self, position: dict, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> dict | None:
        row = df.iloc[idx]

        if pd.isna(row.get("bb_width", float("nan"))) or pd.isna(row.get("bb_width_sma", float("nan"))):
            return None

        # BB breakout exit: width suddenly expands dramatically
        if row["bb_width"] > 2.5 * row["bb_width_sma"]:
            return {"reason": "bb_breakout", "exit_price": row["close"]}

        return None  # SL/TP handled by engine
