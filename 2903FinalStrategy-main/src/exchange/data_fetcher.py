import os
import time
import urllib3
import pandas as pd
import ccxt
from loguru import logger
from datetime import datetime, timezone

# Suppress SSL warnings from corporate proxy interception
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DataFetcher:
    def __init__(self, config: dict):
        self.config = config
        self.data_dir = config["backtest"]["data_directory"]
        os.makedirs(self.data_dir, exist_ok=True)
        market_type = config["exchange"].get("market_type", "future")

        if market_type == "spot":
            self.exchange = ccxt.binance({"enableRateLimit": True})
        else:
            self.exchange = ccxt.binanceusdm({
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })

        # Bypass corporate proxy SSL interception (self-signed cert in chain).
        # Safe for backtest — only fetches public OHLCV candle data.
        self.exchange.session.verify = False
        self.exchange.verify = False

        # ccxt v4+ uses "ETH/USDT:USDT" for USDM futures perpetual contracts.
        raw_symbol = config["exchange"]["symbol"]
        if market_type == "future" and ":USDT" not in raw_symbol:
            self.symbol = raw_symbol + ":USDT"
        else:
            self.symbol = raw_symbol

        self._max_retries = 5

    def _ts(self, date_str: str) -> int:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def fetch_ohlcv(self, timeframe: str = "1m", start_date: str = None, end_date: str = None) -> pd.DataFrame:
        fname = f"{self.symbol.replace('/', '_').replace(':', '_')}_{timeframe}_{start_date}_{end_date}.csv"
        fpath = os.path.join(self.data_dir, fname)

        if os.path.exists(fpath):
            logger.info(f"Loading cached data from {fpath}")
            df = pd.read_csv(fpath, parse_dates=["timestamp"])
            return df

        logger.info(f"Downloading {self.symbol} {timeframe} data from {start_date} to {end_date}")
        since = self._ts(start_date) if start_date else None
        end_ts = self._ts(end_date) if end_date else None
        all_data = []
        limit = 1000
        consecutive_errors = 0

        while True:
            try:
                ohlcv = self.exchange.fetch_ohlcv(
                    self.symbol, timeframe=timeframe, since=since, limit=limit
                )
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error fetching data (attempt {consecutive_errors}): {e}")
                if consecutive_errors >= self._max_retries:
                    logger.error(f"Max retries ({self._max_retries}) exceeded. Returning partial data.")
                    break
                time.sleep(min(5 * consecutive_errors, 30))
                continue

            if not ohlcv:
                break

            all_data.extend(ohlcv)
            last_ts = ohlcv[-1][0]

            if len(all_data) % 50000 == 0:
                logger.info(f"  Downloaded {len(all_data)} candles so far...")

            if end_ts and last_ts >= end_ts:
                break
            if len(ohlcv) < limit:
                break

            since = last_ts + 1
            time.sleep(self.exchange.rateLimit / 1000)

        if not all_data:
            logger.warning("No data fetched!")
            return pd.DataFrame()

        df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        if end_ts:
            df = df[df["timestamp"] <= pd.Timestamp(end_date, tz="UTC")]

        df.to_csv(fpath, index=False)
        logger.info(f"Saved {len(df)} candles to {fpath}")
        return df

    def resample(self, df_1m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        mapping = {"3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
        rule = mapping.get(target_tf)
        if rule is None:
            raise ValueError(f"Unsupported timeframe: {target_tf}")

        df = df_1m.set_index("timestamp")
        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna().reset_index()
        return resampled
