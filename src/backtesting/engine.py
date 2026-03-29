import pandas as pd
import numpy as np
from loguru import logger
from ..strategies.mtf_momentum import MTFMomentumStrategy
from ..strategies.bollinger_scalp import BollingerScalpStrategy
from ..strategies.regime_filter import RegimeFilter
from ..risk.risk_manager import RiskManager


class BacktestEngine:
    def __init__(self, config: dict):
        self.config = config
        self.leverage = config["trading"]["leverage"]
        self.initial_capital = config["backtest"]["initial_capital"]
        self.fee_taker = config["fees"]["taker"] / 100
        self.fee_maker = config["fees"]["maker"] / 100
        self.slippage = config["fees"]["slippage"] / 100

        self.mtf_enabled = config.get("mtf_momentum", {}).get("enabled", True)
        self.bb_enabled = config.get("bollinger_scalp", {}).get("enabled", True)
        self.mtf_strategy = MTFMomentumStrategy(config) if self.mtf_enabled else None
        self.bb_strategy = BollingerScalpStrategy(config) if self.bb_enabled else None
        self.regime_filter = RegimeFilter(config)
        self.risk_manager = RiskManager(config)

    def run(self, df_1m: pd.DataFrame, df_3m: pd.DataFrame, df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> dict:
        logger.info(f"Starting backtest: {len(df_1m)} 1m candles, capital=${self.initial_capital}")

        # Determine actual backtest start date (skip warmup)
        bt_start = pd.Timestamp(self.config["backtest"]["start_date"], tz="UTC")

        # Calculate indicators on each timeframe
        if self.mtf_enabled:
            df_5m = self.mtf_strategy.calculate_indicators(df_5m)  # MTF uses 5m
            df_15m = self.mtf_strategy.calculate_higher_tf_indicators(df_15m)
        else:
            import ta as ta_lib
            df_15m = df_15m.copy()
            df_15m["ema_higher"] = ta_lib.trend.ema_indicator(df_15m["close"], window=50)
        if self.bb_enabled:
            df_3m = self.bb_strategy.calculate_indicators(df_3m)

        capital = float(self.initial_capital)
        positions = []  # open positions
        closed_trades = []
        equity_curve = []
        regime_log = []
        last_regime_check = None
        in_backtest_period = False
        last_bb_entry_idx = -10  # Track last BB entry 3m candle to avoid duplicates
        last_entry_ts = None  # Cooldown tracking

        for i in range(1, len(df_1m)):
            row = df_1m.iloc[i]
            ts = row["timestamp"]

            # Only trade during actual backtest period
            if ts >= bt_start:
                in_backtest_period = True

            # Reset daily counters
            self.risk_manager.reset_day(ts)
            self.risk_manager.reset_hour(ts)
            self.risk_manager.reset_week(ts)

            # Regime check every 15 minutes
            do_regime = False
            if last_regime_check is None:
                do_regime = True
            elif (ts - last_regime_check).total_seconds() >= 900:
                do_regime = True

            regime_info = {"regime": self.regime_filter.current_regime, "override": None}
            if do_regime:
                avail_15m = df_15m[df_15m["timestamp"] <= ts]
                avail_5m = df_5m[df_5m["timestamp"] <= ts]
                if len(avail_15m) >= 20 and len(avail_5m) >= 60:
                    regime_info = self.regime_filter.calculate_indicators(avail_15m, avail_5m)
                    last_regime_check = ts
                    if in_backtest_period:
                        regime_log.append({"timestamp": ts, **regime_info})

            # Skip actual trading during warmup period
            if not in_backtest_period:
                continue

            active_strat = self.regime_filter.get_active_strategy(regime_info, ts)

            # --- Check exits on open positions ---
            positions_to_close = []
            for pos_idx, pos in enumerate(positions):
                exit_info = self._check_position_exit(pos, row, df_1m, df_3m, df_5m, df_15m, i)
                if exit_info:
                    positions_to_close.append((pos_idx, exit_info))

            # Close positions (reverse order to not mess up indices)
            for pos_idx, exit_info in sorted(positions_to_close, reverse=True):
                pos = positions.pop(pos_idx)
                trade = self._close_position(pos, exit_info, capital)
                capital += trade["net_pnl"]
                self.risk_manager.record_trade(trade["net_pnl"], ts)
                self.risk_manager.open_positions = len(positions)
                closed_trades.append(trade)

            # --- Check entries (both strategies, BB gated by regime) ---
            can_enter = len(positions) < self.risk_manager.max_open_positions
            if can_enter and active_strat:
                signal = None

                # MTF momentum on 5m candles (trend-following)
                if self.mtf_enabled:
                    mtf_idx = self._find_tf_idx(df_5m, ts)
                    if mtf_idx is not None and mtf_idx >= 2:
                        # MTF cooldown: at least 45 min between entries
                        if last_entry_ts is None or (ts - last_entry_ts).total_seconds() > 2700:
                            signal = self.mtf_strategy.check_entry(mtf_idx, df_5m, df_15m)

                # BB scalp on 3m candles — ONLY in ranging/transitional regimes
                if signal is None and self.bb_enabled and active_strat in ("bollinger_scalp", "transitional"):
                    bb_idx = self._find_tf_idx(df_3m, ts)
                    if bb_idx is not None and bb_idx > last_bb_entry_idx + 3:
                        # Cooldown: skip if we entered within the last 10 minutes
                        if last_entry_ts is None or (ts - last_entry_ts).total_seconds() > 600:
                            signal = self.bb_strategy.check_entry(bb_idx, df_3m)

                if signal is None and i % 2000 == 0:
                    logger.info(f"No signal at candle {i}/{len(df_1m)}, active_strat={active_strat}, positions={len(positions)}")

                if signal:
                    sl_pct = signal["sl_pct"]
                    tp_pct = signal["tp_pct"]
                    logger.debug(f"Signal: {signal['side']} {signal.get('strategy')} sl={sl_pct:.3f}% tp={tp_pct:.3f}%")

                    can_trade, reason = self.risk_manager.can_trade(capital, sl_pct, tp_pct, ts)
                    if not can_trade:
                        logger.debug(f"Trade rejected: {reason}")
                    if can_trade:
                        # Execute at next candle's open
                        if i + 1 < len(df_1m):
                            exec_price = df_1m.iloc[i + 1]["open"]
                            size_value = self.risk_manager.calculate_position_size(capital, sl_pct)

                            size_contracts = (size_value * self.leverage) / exec_price
                            entry_fee = self._calc_entry_fee(size_value * self.leverage)

                            pos = {
                                "side": signal["side"],
                                "strategy": signal.get("strategy", active_strat),
                                "entry_price": exec_price,
                                "entry_ts": df_1m.iloc[i + 1]["timestamp"],
                                "size_value": size_value,
                                "size_contracts": size_contracts,
                                "sl_pct": sl_pct,
                                "tp_pct": tp_pct,
                                "trailing_activation": signal.get("trailing_activation"),
                                "trailing_distance": signal.get("trailing_distance"),
                                "max_duration": signal.get("max_duration"),
                                "tp2_target": signal.get("tp2_target"),
                                "tp1_close_pct": signal.get("tp1_close_pct"),
                                "bb_middle": signal.get("bb_middle"),
                                "bb_upper": signal.get("bb_upper"),
                                "bb_lower": signal.get("bb_lower"),
                                "entry_fee": entry_fee,
                                "regime": regime_info["regime"],
                                "highest_pnl_pct": 0.0,
                                "tp1_hit": False,
                            }
                            positions.append(pos)
                            self.risk_manager.open_positions = len(positions)
                            self.risk_manager.trades_today += 1
                            self.risk_manager.trades_this_hour += 1
                            last_entry_ts = ts
                            # Track BB entry index to avoid duplicates
                            if signal.get("strategy") == "bollinger_scalp":
                                bb_idx = self._find_tf_idx(df_3m, ts)
                                if bb_idx is not None:
                                    last_bb_entry_idx = bb_idx

            # Track equity
            unrealized = sum(self._unrealized_pnl(p, row) for p in positions)
            equity_curve.append({"timestamp": ts, "equity": capital + unrealized})

        # Force close remaining positions at last candle
        last_row = df_1m.iloc[-1]
        for pos in positions:
            exit_info = {"reason": "backtest_end", "exit_price": last_row["close"]}
            trade = self._close_position(pos, exit_info, capital)
            capital += trade["net_pnl"]
            closed_trades.append(trade)

        return {
            "closed_trades": closed_trades,
            "equity_curve": pd.DataFrame(equity_curve),
            "regime_log": regime_log,
            "final_capital": capital,
            "initial_capital": self.initial_capital,
        }

    def _check_position_exit(self, pos: dict, row, df_1m, df_3m, df_5m, df_15m, idx) -> dict | None:
        entry_price = pos["entry_price"]
        side = pos["side"]
        candle_high = row["high"]
        candle_low = row["low"]

        # Breakeven stop: once price moved 0.3% in favor, move SL to entry
        current_pnl_pct = self._pnl_pct(pos, row["close"])
        if not pos.get("breakeven_activated") and current_pnl_pct >= 0.3:
            pos["breakeven_activated"] = True
            pos["original_sl_pct"] = pos["sl_pct"]

        # Calculate SL/TP prices (use breakeven if activated)
        if pos.get("breakeven_activated"):
            sl_price = entry_price  # breakeven
        elif side == "long":
            sl_price = entry_price * (1 - pos["sl_pct"] / 100)
        else:
            sl_price = entry_price * (1 + pos["sl_pct"] / 100)

        if side == "long":
            tp_price = entry_price * (1 + pos["tp_pct"] / 100)
        else:
            tp_price = entry_price * (1 - pos["tp_pct"] / 100)

        # Check SL hit (check SL before TP — conservative)
        sl_hit = False
        if side == "long" and candle_low <= sl_price:
            sl_hit = True
        elif side == "short" and candle_high >= sl_price:
            sl_hit = True

        if sl_hit:
            reason = "breakeven_stop" if pos.get("breakeven_activated") else "stop_loss"
            return {"reason": reason, "exit_price": sl_price}

        # Check TP hit
        tp_hit = False
        if side == "long" and candle_high >= tp_price:
            tp_hit = True
        elif side == "short" and candle_low <= tp_price:
            tp_hit = True

        if tp_hit:
            return {"reason": "take_profit", "exit_price": tp_price}

        # Trailing stop logic for MTF Momentum
        if pos.get("trailing_activation") is not None:
            current_pnl_pct = self._pnl_pct(pos, row["close"])
            pos["highest_pnl_pct"] = max(pos["highest_pnl_pct"], current_pnl_pct)

            if pos["highest_pnl_pct"] >= pos["trailing_activation"]:
                drawback = pos["highest_pnl_pct"] - current_pnl_pct
                if drawback >= pos["trailing_distance"]:
                    trail_price = row["close"]
                    return {"reason": "trailing_stop", "exit_price": trail_price}

        # Strategy-specific exits
        if pos["strategy"] == "mtf_momentum":
            mtf_idx = self._find_tf_idx(df_5m, row["timestamp"])
            if mtf_idx is not None:
                exit_info = self.mtf_strategy.check_exit(pos, mtf_idx, df_5m, df_15m)
                if exit_info:
                    return exit_info
        elif pos["strategy"] == "bollinger_scalp":
            bb_idx = self._find_tf_idx(df_3m, row["timestamp"])
            if bb_idx is not None:
                exit_info = self.bb_strategy.check_exit(pos, bb_idx, df_3m)
                if exit_info:
                    return exit_info

        return None

    def _close_position(self, pos: dict, exit_info: dict, capital: float) -> dict:
        exit_price = exit_info["exit_price"]
        entry_price = pos["entry_price"]
        side = pos["side"]
        notional = pos["size_value"] * self.leverage

        if side == "long":
            raw_pnl = (exit_price - entry_price) / entry_price * notional
        else:
            raw_pnl = (entry_price - exit_price) / entry_price * notional

        entry_fee = pos["entry_fee"]
        exit_fee = self._calc_exit_fee(notional)
        total_fees = entry_fee + exit_fee
        net_pnl = raw_pnl - total_fees

        duration = 0
        if "entry_ts" in pos:
            duration = (exit_info.get("exit_ts", pos["entry_ts"]) - pos["entry_ts"]).total_seconds() / 60 if isinstance(exit_info.get("exit_ts"), pd.Timestamp) else 0

        return {
            "strategy": pos["strategy"],
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_ts": pos["entry_ts"],
            "size_value": pos["size_value"],
            "notional": notional,
            "raw_pnl": raw_pnl,
            "fees": total_fees,
            "net_pnl": net_pnl,
            "exit_reason": exit_info["reason"],
            "regime": pos.get("regime", ""),
            "duration_minutes": duration,
        }

    def _calc_entry_fee(self, notional: float) -> float:
        """Entry uses maker order (limit)."""
        return notional * (self.fee_maker + self.slippage)

    def _calc_exit_fee(self, notional: float) -> float:
        """Exit uses taker order (market/trigger)."""
        return notional * (self.fee_taker + self.slippage)

    def _pnl_pct(self, pos: dict, current_price: float) -> float:
        if pos["side"] == "long":
            return (current_price - pos["entry_price"]) / pos["entry_price"] * 100
        else:
            return (pos["entry_price"] - current_price) / pos["entry_price"] * 100

    def _unrealized_pnl(self, pos: dict, row) -> float:
        pnl_pct = self._pnl_pct(pos, row["close"])
        notional = pos["size_value"] * self.leverage
        return pnl_pct / 100 * notional

    def _find_tf_idx(self, df_tf: pd.DataFrame, ts) -> int | None:
        mask = df_tf["timestamp"] <= ts
        if mask.any():
            return mask.values.nonzero()[0][-1]
        return None
