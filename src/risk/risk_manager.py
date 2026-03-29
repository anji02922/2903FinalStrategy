from datetime import datetime, timedelta
from loguru import logger


class RiskManager:
    def __init__(self, config: dict):
        c = config["risk"]
        self.risk_per_trade = c["risk_per_trade_pct"] / 100
        self.max_position_size = c["max_position_size_pct"] / 100
        self.max_open_positions = c["max_open_positions"]
        self.max_trades_per_hour = c["max_trades_per_hour"]
        self.max_trades_per_day = c["max_trades_per_day"]
        self.max_daily_loss = c["max_daily_loss_pct"] / 100
        self.max_consecutive_losses = c["max_consecutive_losses_pause"]
        self.consecutive_pause_minutes = c["consecutive_loss_pause_minutes"]
        self.max_weekly_loss = c["max_weekly_loss_pct"] / 100
        self.min_rr = c["min_risk_reward_ratio"]

        self.fees = config["fees"]
        self.leverage = config["trading"]["leverage"]

        # State tracking
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.consecutive_losses = 0
        self.trades_today = 0
        self.trades_this_hour = 0
        self.open_positions = 0
        self.current_day = None
        self.current_hour = None
        self.current_week = None
        self.pause_until = None
        self.starting_capital = config["backtest"]["initial_capital"]
        self.current_capital = self.starting_capital

    def reset_day(self, dt):
        day = dt.date() if hasattr(dt, "date") else dt
        if self.current_day != day:
            self.current_day = day
            self.daily_pnl = 0.0
            self.trades_today = 0
            logger.debug(f"Daily counters reset for {day}")

    def reset_hour(self, dt):
        hour = (dt.date(), dt.hour) if hasattr(dt, "hour") else None
        if hour and self.current_hour != hour:
            self.current_hour = hour
            self.trades_this_hour = 0

    def reset_week(self, dt):
        week = dt.isocalendar()[1] if hasattr(dt, "isocalendar") else None
        if week and self.current_week != week:
            self.current_week = week
            self.weekly_pnl = 0.0

    def calculate_position_size(self, capital: float, sl_pct: float) -> float:
        if sl_pct <= 0:
            return 0
        risk_amount = capital * self.risk_per_trade
        position_value = risk_amount / (sl_pct / 100)
        max_allowed = capital * self.max_position_size * self.leverage
        return min(position_value, max_allowed)

    def can_trade(self, capital: float, sl_pct: float, tp_pct: float, current_ts=None) -> tuple[bool, str]:
        if current_ts:
            self.reset_day(current_ts)
            self.reset_hour(current_ts)
            self.reset_week(current_ts)

        # Check pause
        if self.pause_until and current_ts and current_ts < self.pause_until:
            return False, f"Paused until {self.pause_until}"

        # Daily loss limit
        if capital > 0 and (self.daily_pnl / capital) < -self.max_daily_loss:
            return False, f"Daily loss limit hit: {self.daily_pnl:.2f}"

        # Weekly loss limit
        if capital > 0 and (self.weekly_pnl / capital) < -self.max_weekly_loss:
            return False, f"Weekly loss limit hit: {self.weekly_pnl:.2f}"

        # Max positions
        if self.open_positions >= self.max_open_positions:
            return False, f"Max open positions reached: {self.open_positions}"

        # Trades per day
        if self.trades_today >= self.max_trades_per_day:
            return False, f"Max daily trades reached: {self.trades_today}"

        # Trades per hour
        if self.trades_this_hour >= self.max_trades_per_hour:
            return False, f"Max hourly trades reached: {self.trades_this_hour}"

        # Min R:R ratio
        if sl_pct > 0 and tp_pct > 0:
            total_fee_pct = (self.fees["maker"] + self.fees["slippage"]) + (self.fees["taker"] + self.fees["slippage"])
            effective_tp = tp_pct - total_fee_pct
            effective_sl = sl_pct + total_fee_pct
            if effective_sl > 0:
                rr = effective_tp / effective_sl
                if rr < self.min_rr:
                    return False, f"R:R too low: {rr:.2f} < {self.min_rr}"

        return True, "OK"

    def record_trade(self, pnl: float, current_ts=None):
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.trades_today += 1
        self.trades_this_hour += 1
        self.current_capital += pnl

        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                if current_ts:
                    self.pause_until = current_ts + timedelta(minutes=self.consecutive_pause_minutes)
                    logger.warning(f"{self.consecutive_losses} consecutive losses — pausing until {self.pause_until}")
        else:
            self.consecutive_losses = 0

    def get_fee_cost(self, position_value: float, is_maker: bool = False) -> float:
        fee_rate = self.fees["maker"] if is_maker else self.fees["taker"]
        slippage = self.fees["slippage"]
        return position_value * (fee_rate + slippage) / 100
