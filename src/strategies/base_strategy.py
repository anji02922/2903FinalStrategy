from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        pass

    @abstractmethod
    def check_entry(self, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> dict | None:
        """Return {'side': 'long'|'short', 'sl_pct': float, 'tp_pct': float, ...} or None"""
        pass

    @abstractmethod
    def check_exit(self, position: dict, idx: int, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> dict | None:
        """Return {'reason': str, 'exit_price': float} or None"""
        pass
