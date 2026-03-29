import ccxt
from loguru import logger


class BinanceClient:
    def __init__(self, config: dict):
        exchange_cfg = config["exchange"]
        opts = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        if exchange_cfg.get("api_key"):
            opts["apiKey"] = exchange_cfg["api_key"]
            opts["secret"] = exchange_cfg["api_secret"]
        if exchange_cfg.get("testnet"):
            opts["sandbox"] = True

        self.exchange = ccxt.binanceusdm(opts)
        self.symbol = exchange_cfg["symbol"]
        self.leverage = config["trading"]["leverage"]
        logger.info(f"BinanceClient initialized for {self.symbol}")

    def set_leverage(self):
        try:
            self.exchange.set_leverage(self.leverage, self.symbol)
            logger.info(f"Leverage set to {self.leverage}x for {self.symbol}")
        except Exception as e:
            logger.warning(f"Could not set leverage: {e}")

    def fetch_ohlcv(self, timeframe="1m", since=None, limit=1500):
        return self.exchange.fetch_ohlcv(self.symbol, timeframe=timeframe, since=since, limit=limit)

    def fetch_ticker(self):
        return self.exchange.fetch_ticker(self.symbol)

    def create_order(self, side, amount, order_type="market", price=None, params=None):
        if params is None:
            params = {}
        return self.exchange.create_order(self.symbol, order_type, side, amount, price, params)

    def get_balance(self):
        balance = self.exchange.fetch_balance()
        return balance.get("USDT", {}).get("free", 0)
