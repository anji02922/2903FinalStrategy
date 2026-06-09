from datetime import datetime, timedelta, timezone
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
        iso = dt.isocalendar() if hasattr(dt, "isocalendar") else None
        if iso:
            yw = (iso[0], iso[1])  # (year, week) — avoids cross-year collision
            if self.current_week != yw:
                self.current_week = yw
                self.weekly_pnl = 0.0

    def calculate_position_size(self, capital: float, sl_pct: float) -> float:
        """Return margin value. Callers multiply by leverage for notional."""
        if sl_pct <= 0:
            return 0
        risk_amount = capital * self.risk_per_trade
        position_margin = risk_amount / (sl_pct / 100 * self.leverage)
        max_margin = capital * self.max_position_size
        return min(position_margin, max_margin)

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

        # Min R:R ratio — both entry and exit use taker (market orders) in live
        if sl_pct > 0 and tp_pct > 0:
            total_fee_pct = (self.fees["taker"] + self.fees["slippage"]) + (self.fees["taker"] + self.fees["slippage"])
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

    def restore_from_trades(self, trades: list):
        """Reconstruct weekly/daily PnL from trade history (SQLite rows).

        Called on live bot startup so risk limits survive restarts.
        Each trade dict must have 'timestamp' (ISO str) and 'net_pnl' (float).
        """
        now = datetime.now(timezone.utc)
        today = now.date()
        current_hour = (today, now.hour)
        iso_now = now.isocalendar()
        current_yw = (iso_now[0], iso_now[1])

        weekly_pnl = 0.0
        daily_pnl = 0.0
        trades_today = 0
        trades_this_hour = 0
        consecutive_losses = 0

        sorted_trades = sorted(trades, key=lambda t: t["timestamp"])

        for t in sorted_trades:
            ts_str = t.get("timestamp", "")
            net_pnl = t.get("net_pnl", 0.0)
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            iso_t = ts.isocalendar()
            t_yw = (iso_t[0], iso_t[1])

            if t_yw == current_yw:
                weekly_pnl += net_pnl
            if ts.date() == today:
                daily_pnl += net_pnl
                trades_today += 1
                if (ts.date(), ts.hour) == current_hour:
                    trades_this_hour += 1

        # Consecutive losses: walk from newest trade backwards
        for t in reversed(sorted_trades):
            if t.get("net_pnl", 0) < 0:
                consecutive_losses += 1
            else:
                break

        self.weekly_pnl = weekly_pnl
        self.daily_pnl = daily_pnl
        self.trades_today = trades_today
        self.trades_this_hour = trades_this_hour
        self.consecutive_losses = consecutive_losses
        self.current_week = current_yw
        self.current_day = today
        self.current_hour = current_hour

        # Adjust capital for all-time PnL
        total_pnl = sum(t.get("net_pnl", 0) for t in trades)
        self.current_capital = self.starting_capital + total_pnl

        logger.info(
            f"Risk state restored: weekly_pnl=${weekly_pnl:+.2f}, daily_pnl=${daily_pnl:+.2f}, "
            f"trades_today={trades_today}, consecutive_losses={consecutive_losses}, "
            f"capital=${self.current_capital:.2f}"
        )

    def get_fee_cost(self, position_value: float, is_maker: bool = False) -> float:
        fee_rate = self.fees["maker"] if is_maker else self.fees["taker"]
        slippage = self.fees["slippage"]
        return position_value * (fee_rate + slippage) / 100
