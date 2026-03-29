from loguru import logger


class OrderManager:
    def __init__(self, client):
        self.client = client
        self.open_orders = {}  # order_id -> order info

    def place_market_order(self, side: str, amount: float):
        logger.info(f"MARKET {side.upper()} {amount:.4f}")
        order = self.client.create_order(side, amount, "market")
        self._track(order)
        return order

    def place_limit_order(self, side: str, amount: float, price: float):
        logger.info(f"LIMIT {side.upper()} {amount:.4f} @ {price:.2f}")
        order = self.client.create_order(side, amount, "limit", price, {"timeInForce": "GTC"})
        self._track(order)
        return order

    def place_stop_loss(self, side: str, amount: float, stop_price: float):
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"SL {close_side.upper()} {amount:.4f} trigger={stop_price:.2f}")
        params = {"stopPrice": stop_price, "closePosition": True}
        order = self.client.create_order(close_side, amount, "STOP_MARKET", None, params)
        self._track(order)
        return order

    def place_take_profit(self, side: str, amount: float, tp_price: float):
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"TP {close_side.upper()} {amount:.4f} trigger={tp_price:.2f}")
        params = {"stopPrice": tp_price, "closePosition": True}
        order = self.client.create_order(close_side, amount, "TAKE_PROFIT_MARKET", None, params)
        self._track(order)
        return order

    def place_sl_tp(self, side: str, amount: float, sl_price: float, tp_price: float):
        """Place both SL and TP after entry — returns (sl_order, tp_order)."""
        sl_order = self.place_stop_loss(side, amount, sl_price)
        tp_order = self.place_take_profit(side, amount, tp_price)
        return sl_order, tp_order

    def update_stop_loss(self, old_sl_order_id: str, side: str, amount: float, new_stop_price: float):
        """Cancel existing SL and place new one (for breakeven/trailing)."""
        try:
            self.cancel_order(old_sl_order_id)
        except Exception as e:
            logger.warning(f"Failed to cancel old SL {old_sl_order_id}: {e}")
        return self.place_stop_loss(side, amount, new_stop_price)

    def cancel_order(self, order_id: str):
        logger.info(f"Cancelling order {order_id}")
        result = self.client.cancel_order(order_id)
        self.open_orders.pop(order_id, None)
        return result

    def cancel_all(self):
        logger.info("Cancelling all open orders")
        result = self.client.cancel_all_orders()
        self.open_orders.clear()
        return result

    def close_position_market(self, side: str, amount: float):
        """Emergency close — market order opposite side."""
        close_side = "sell" if side == "long" else "buy"
        logger.warning(f"MARKET CLOSE {close_side.upper()} {amount:.4f}")
        return self.place_market_order(close_side, amount)

    def _track(self, order):
        if order and "id" in order:
            self.open_orders[order["id"]] = order
