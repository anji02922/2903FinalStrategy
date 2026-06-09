from datetime import timedelta

import pandas as pd
import ta as ta_lib
from loguru import logger


class RegimeFilter:
    def __init__(self, config: dict):
        c = config["regime"]
        self.adx_trending = c["adx_trending_threshold"]
        self.adx_ranging = c["adx_ranging_threshold"]
        self.atr_high_vol = c["atr_high_vol_multiplier"]
        self.atr_dead = c["atr_dead_market_multiplier"]
        self.pause_minutes = c["high_vol_pause_minutes"]
        self.current_regime = "UNKNOWN"
        self.volatility_override = None
        self.pause_until = None

    def calculate_indicators(self, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
        result = {"regime": "UNKNOWN", "override": None, "adx": 0, "atr_ratio": 0}

        if len(df_15m) < 30:
            return result

        try:
            adx_indicator = ta_lib.trend.ADXIndicator(df_15m["high"], df_15m["low"], df_15m["close"], window=14)
            adx_series = adx_indicator.adx()
            adx_val = adx_series.iloc[-1]
            if not pd.isna(adx_val):
                result["adx"] = adx_val
        except Exception:
            pass

        if len(df_5m) >= 60:
            atr = ta_lib.volatility.average_true_range(df_5m["high"], df_5m["low"], df_5m["close"], window=14)
            if atr is not None:
                atr_sma = atr.rolling(50).mean()
                curr_atr = atr.iloc[-1]
                avg_atr = atr_sma.iloc[-1]
                if not pd.isna(curr_atr) and not pd.isna(avg_atr) and avg_atr > 0:
                    ratio = curr_atr / avg_atr
                    result["atr_ratio"] = ratio
                    if ratio > self.atr_high_vol:
                        result["override"] = "HIGH_VOLATILITY"
                    elif ratio < self.atr_dead:
                        result["override"] = "DEAD_MARKET"

        adx = result["adx"]
        if adx > self.adx_trending:
            result["regime"] = "TRENDING"
        elif adx < self.adx_ranging:
            result["regime"] = "RANGING"
        else:
            result["regime"] = "TRANSITIONAL"

        if result["regime"] != self.current_regime:
            logger.info(f"Regime changed: {self.current_regime} -> {result['regime']} (ADX={adx:.1f})")
            self.current_regime = result["regime"]

        if result["override"]:
            self.volatility_override = result["override"]
            logger.info(f"Volatility override: {result['override']} (ATR ratio={result['atr_ratio']:.2f})")

        return result

    def check_regime_fast(self, adx_val: float, atr_ratio: float, current_ts=None) -> dict:
        """Use pre-computed ADX and ATR ratio for fast regime detection (backtest only)."""
        result = {"regime": "UNKNOWN", "override": None, "adx": 0, "atr_ratio": 0}

        if not pd.isna(adx_val):
            result["adx"] = adx_val
        if not pd.isna(atr_ratio):
            result["atr_ratio"] = atr_ratio
            if atr_ratio > self.atr_high_vol:
                result["override"] = "HIGH_VOLATILITY"
            elif atr_ratio < self.atr_dead:
                result["override"] = "DEAD_MARKET"

        adx = result["adx"]
        if adx > self.adx_trending:
            result["regime"] = "TRENDING"
        elif adx < self.adx_ranging:
            result["regime"] = "RANGING"
        else:
            result["regime"] = "TRANSITIONAL"

        if result["regime"] != self.current_regime:
            self.current_regime = result["regime"]

        if result["override"]:
            self.volatility_override = result["override"]

        return result

    def get_active_strategy(self, regime_info: dict, current_ts=None) -> str | None:
        if self.pause_until and current_ts and current_ts < self.pause_until:
            return None

        if regime_info.get("override") == "HIGH_VOLATILITY":
            if current_ts:
                self.pause_until = current_ts + timedelta(minutes=self.pause_minutes)
            return None

        if regime_info.get("override") == "DEAD_MARKET":
            return None

        regime = regime_info["regime"]
        if regime == "TRENDING":
            return "mtf_momentum"
        elif regime == "RANGING":
            return "bollinger_scalp"
        elif regime == "TRANSITIONAL":
            return "transitional"  # either strategy but reduced size
        return None
