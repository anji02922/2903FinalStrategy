import time
import ccxt
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
        params = {"stopPrice": stop_price, "reduceOnly": True}
        order = self.client.create_order(close_side, amount, "STOP_MARKET", None, params)
        self._track(order)
        return order

    def place_take_profit(self, side: str, amount: float, tp_price: float):
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"TP {close_side.upper()} {amount:.4f} trigger={tp_price:.2f}")
        params = {"stopPrice": tp_price, "reduceOnly": True}
        order = self.client.create_order(close_side, amount, "TAKE_PROFIT_MARKET", None, params)
        self._track(order)
        return order

    def place_sl_tp(self, side: str, amount: float, sl_price: float, tp_price: float):
        """Place both SL and TP after entry — returns (sl_order, tp_order)."""
        sl_order = self.place_stop_loss(side, amount, sl_price)
        tp_order = self.place_take_profit(side, amount, tp_price)
        return sl_order, tp_order

    def update_stop_loss(self, old_sl_order_id: str, side: str, amount: float,
                         new_stop_price: float, tp_price: float = None):
        """Cancel ALL open orders, then re-place SL (and TP if provided).
        This avoids stale order ID issues and -4130 closePosition conflicts.
        Returns (new_sl, new_tp). If SL would immediately trigger, returns (None, None)
        — caller should close at market."""
        # Nuclear cancel — clear ALL orders reliably
        self._cancel_all_verified()
        # Place new SL
        try:
            new_sl = self.place_stop_loss(side, amount, new_stop_price)
        except ccxt.OrderImmediatelyFillable:
            logger.warning(f"SL @ {new_stop_price:.2f} would immediately trigger — price crossed back")
            return None, None
        # Re-place TP (since cancel_all removed it too)
        new_tp = None
        if tp_price is not None:
            new_tp = self.place_take_profit(side, amount, tp_price)
        return new_sl, new_tp

    def _cancel_all_verified(self, max_attempts: int = 3):
        """Cancel all open orders and verify they are actually gone."""
        for attempt in range(max_attempts):
            # Try bulk cancel
            self.cancel_all()
            time.sleep(0.5)
            # Also cancel individually (demo API sometimes ignores cancel_all)
            try:
                remaining = self.client.fetch_open_orders()
                if not remaining:
                    return  # All clear
                for order in remaining:
                    try:
                        self.client.cancel_order(order["id"])
                    except Exception:
                        pass
                time.sleep(0.5)
                # Final check
                remaining = self.client.fetch_open_orders()
                if not remaining:
                    return
            except Exception as e:
                logger.warning(f"Cancel verification attempt {attempt + 1}: {e}")
        logger.warning("Could not verify all orders canceled after max attempts")

    def cancel_order(self, order_id: str):
        logger.debug(f"Cancelling order {order_id}")
        result = self.client.cancel_order(order_id)
        self.open_orders.pop(order_id, None)
        return result

    def cancel_all(self):
        logger.info("Cancelling all open orders")
        result = self.client.cancel_all_orders()
        self.open_orders.clear()
        return result

    def close_position_market(self, side: str, amount: float):
        """Close position — market order opposite side with reduceOnly."""
        close_side = "sell" if side == "long" else "buy"
        logger.warning(f"MARKET CLOSE {close_side.upper()} {amount:.4f} (reduceOnly)")
        params = {"reduceOnly": True}
        return self.client.create_order(close_side, amount, "market", None, params)

    def _track(self, order):
        if order and "id" in order:
            self.open_orders[order["id"]] = order
