import time
import ccxt
from loguru import logger


class BinanceClient:
    def __init__(self, config: dict):
        exchange_cfg = config["exchange"]
        opts = {
            "enableRateLimit": True,
            "options": {"defaultType": "future", "fetchCurrencies": False},
        }
        if exchange_cfg.get("api_key"):
            opts["apiKey"] = exchange_cfg["api_key"]
            opts["secret"] = exchange_cfg["api_secret"]

        # Demo API uses demo-fapi.binance.com URLs (not testnet sandbox)
        self.is_demo = exchange_cfg.get("testnet", False)

        self.exchange = ccxt.binanceusdm(opts)

        if self.is_demo:
            # Copy all demo URLs into api URLs
            if "demo" in self.exchange.urls:
                for key, url in self.exchange.urls["demo"].items():
                    self.exchange.urls["api"][key] = url
            # Also override sapi endpoints (used by fetch_balance internally)
            self.exchange.urls["api"]["sapi"] = "https://demo-api.binance.com/sapi/v1"
            self.exchange.urls["api"]["sapiV2"] = "https://demo-api.binance.com/sapi/v2"
            self.exchange.urls["api"]["sapiV3"] = "https://demo-api.binance.com/sapi/v3"
            self.exchange.urls["api"]["sapiV4"] = "https://demo-api.binance.com/sapi/v4"
            logger.info("Using Binance Futures DEMO API (demo-fapi.binance.com)")

        self.symbol = exchange_cfg["symbol"]
        self.leverage = config["trading"]["leverage"]
        self.max_retries = 3

        # Separate mainnet client for OHLCV data so indicators match backtest
        # (demo API has slightly different prices due to separate order book)
        if self.is_demo:
            self.mainnet_exchange = ccxt.binanceusdm({
                "enableRateLimit": True,
                "options": {"defaultType": "future", "fetchCurrencies": False},
            })
            logger.info("Mainnet OHLCV client created for indicator data")
        else:
            self.mainnet_exchange = None

        logger.info(f"BinanceClient initialized for {self.symbol} | demo={self.is_demo}")

    def _retry(self, func, *args, **kwargs):
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                logger.warning(f"Attempt {attempt}/{self.max_retries} failed: {e}")
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error: {e}")
                raise

    def set_leverage(self):
        try:
            self._retry(self.exchange.set_leverage, self.leverage, self.symbol)
            logger.info(f"Leverage set to {self.leverage}x for {self.symbol}")
        except Exception as e:
            logger.warning(f"Could not set leverage: {e}")

    def set_margin_mode(self, mode="cross"):
        try:
            self._retry(self.exchange.set_margin_mode, mode, self.symbol)
            logger.info(f"Margin mode set to {mode} for {self.symbol}")
        except Exception as e:
            # Often fails if already set — not critical
            logger.debug(f"Margin mode note: {e}")

    def fetch_ohlcv(self, timeframe="1m", since=None, limit=1500):
        """Fetch OHLCV from mainnet (if demo) so indicators match backtest data."""
        ex = self.mainnet_exchange if self.mainnet_exchange else self.exchange
        return self._retry(ex.fetch_ohlcv, self.symbol, timeframe, since, limit)

    def fetch_ticker(self):
        return self._retry(self.exchange.fetch_ticker, self.symbol)

    def create_order(self, side, amount, order_type="market", price=None, params=None):
        if params is None:
            params = {}
        # Map 'long'/'short' to 'buy'/'sell' for ccxt
        side_map = {"long": "buy", "short": "sell", "buy": "buy", "sell": "sell"}
        ccxt_side = side_map.get(side.lower(), side.lower())
        return self._retry(
            self.exchange.create_order, self.symbol, order_type, ccxt_side, amount, price, params
        )

    def cancel_order(self, order_id):
        return self._retry(self.exchange.cancel_order, order_id, self.symbol)

    def cancel_all_orders(self):
        """Cancel ALL orders: regular + algo/conditional (STOP_MARKET, TP_MARKET)."""
        try:
            self._retry(self.exchange.cancel_all_orders, self.symbol)
        except Exception as e:
            logger.warning(f"Cancel regular orders: {e}")
        # Also cancel algo/conditional orders (SL / TP placed as algo)
        try:
            symbol_id = self.symbol.replace("/", "")
            self.exchange.fapiPrivateDeleteAlgoOpenOrders({"symbol": symbol_id})
        except Exception as e:
            logger.debug(f"Cancel algo orders: {e}")

    def fetch_open_orders(self):
        """Fetch both regular AND algo/conditional open orders."""
        regular = self._retry(self.exchange.fetch_open_orders, self.symbol)
        try:
            symbol_id = self.symbol.replace("/", "")
            resp = self.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": symbol_id})
            algo_list = resp if isinstance(resp, list) else resp.get("orders", [])
            for ao in algo_list:
                if ao.get("algoStatus") == "NEW":
                    regular.append({
                        "id": ao.get("algoId"),
                        "type": ao.get("orderType", "").lower(),
                        "side": ao.get("side", "").lower(),
                        "amount": float(ao.get("quantity", 0)),
                        "stopPrice": float(ao.get("triggerPrice", 0)),
                        "status": "open",
                        "info": ao,
                    })
        except Exception as e:
            logger.warning(f"Fetch algo orders: {e}")
        return regular

    def fetch_positions(self):
        positions = self._retry(self.exchange.fetch_positions, [self.symbol])
        # Return only positions with non-zero size
        return [p for p in positions if abs(float(p.get("contracts", 0))) > 0]

    def get_balance(self):
        balance = self._retry(self.exchange.fetch_balance)
        return float(balance.get("USDT", {}).get("total", 0))


