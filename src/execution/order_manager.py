from loguru import logger


class OrderManager:
    def __init__(self, client):
        self.client = client
        self.open_orders = {}

    def place_market_order(self, side: str, amount: float):
        logger.info(f"Placing MARKET {side} order: {amount}")
        return self.client.create_order(side, amount, "market")

    def place_limit_order(self, side: str, amount: float, price: float):
        logger.info(f"Placing LIMIT {side} order: {amount} @ {price}")
        return self.client.create_order(side, amount, "limit", price)

    def place_stop_loss(self, side: str, amount: float, stop_price: float):
        params = {"stopPrice": stop_price, "type": "STOP_MARKET"}
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"Placing STOP_MARKET {close_side}: {amount} @ {stop_price}")
        return self.client.create_order(close_side, amount, "stop_market", None, params)
