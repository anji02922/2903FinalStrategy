import numpy as np
import pandas as pd
import ta as ta_lib
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

    def run(self, df_1m: pd.DataFrame, df_3m: pd.DataFrame, df_5m: pd.DataFrame, df_15m: pd.DataFrame, progress_cb=None) -> dict:
        logger.info(f"Starting backtest: {len(df_1m)} 1m candles, capital=${self.initial_capital}")

        # Determine actual backtest start date (skip warmup)
        bt_start = pd.Timestamp(self.config["backtest"]["start_date"], tz="UTC")

        # Risk-warmup: start trading from the Monday of bt_start's ISO week
        # so weekly_pnl accumulates correctly (matches live behaviour).
        iso = bt_start.isocalendar()
        week_monday = pd.Timestamp.fromisocalendar(iso[0], iso[1], 1).tz_localize("UTC")
        risk_warmup_start = min(week_monday, bt_start)

        # Calculate indicators on each timeframe
        if self.mtf_enabled:
            df_5m = self.mtf_strategy.calculate_indicators(df_5m)  # MTF uses 5m
            df_15m = self.mtf_strategy.calculate_higher_tf_indicators(df_15m)
        if self.bb_enabled:
            df_3m = self.bb_strategy.calculate_indicators(df_3m)

        # ── Pre-compute O(1) index mappings (searchsorted) ──────────────
        #
        # "side='left' - 1" maps each 1m candle to the last COMPLETED
        # higher-TF candle (the one whose close is already finalised).
        # The old "side='right' - 1" returned the CURRENT (still-open)
        # candle, which is look-ahead bias — its close/high/low aren't
        # known yet in real-time.
        ts_1m_int = df_1m["timestamp"].values.astype("int64")
        ts_3m_int = df_3m["timestamp"].values.astype("int64")
        ts_5m_int = df_5m["timestamp"].values.astype("int64")
        ts_15m_int = df_15m["timestamp"].values.astype("int64")

        idx_map_3m = np.searchsorted(ts_3m_int, ts_1m_int, side="left") - 1
        idx_map_5m = np.searchsorted(ts_5m_int, ts_1m_int, side="left") - 1
        idx_map_15m = np.searchsorted(ts_15m_int, ts_1m_int, side="left") - 1

        # Store for use in _check_position_exit
        self._idx_map_3m = idx_map_3m
        self._idx_map_5m = idx_map_5m

        # ── Pre-compute regime indicators on full dataframes ────────────
        adx_ind = ta_lib.trend.ADXIndicator(df_15m["high"], df_15m["low"], df_15m["close"], window=14)
        pre_adx = adx_ind.adx().values
        atr_raw = ta_lib.volatility.average_true_range(df_5m["high"], df_5m["low"], df_5m["close"], window=14)
        atr_sma = atr_raw.rolling(50).mean()
        pre_atr_ratio = (atr_raw / atr_sma).values

        capital = float(self.initial_capital)
        capital_at_bt_start = None  # Track capital when real period starts
        positions = []  # open positions
        closed_trades = []
        equity_curve = []
        regime_log = []
        last_regime_check = None
        in_backtest_period = False
        in_risk_warmup = False  # True during ISO-week warmup before bt_start
        last_bb_entry_idx = -10  # Track last BB entry 3m candle to avoid duplicates
        last_entry_ts = None  # Cooldown tracking
        n_candles = len(df_1m)
        prev_5m_idx = idx_map_5m[0] if n_candles > 0 else -1  # boundary detection
        prev_3m_idx = idx_map_3m[0] if n_candles > 0 else -1

        for i in range(1, n_candles):
            row = df_1m.iloc[i]
            ts = row["timestamp"]

            # Progress callback
            if progress_cb and i % 50000 == 0:
                progress_cb(i, n_candles)

            # Risk warmup: trade from the Monday of bt_start's ISO week
            if ts >= risk_warmup_start and not in_risk_warmup and not in_backtest_period:
                in_risk_warmup = True

            # Only trade during actual backtest period
            if ts >= bt_start:
                if not in_backtest_period:
                    capital_at_bt_start = capital
                in_backtest_period = True
                in_risk_warmup = False

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
                # Use completed candles: idx_map gives the candle whose
                # open_time <= ts, which may still be open. Subtract 1 to
                # guarantee we only read finalised data.
                i15 = max(idx_map_15m[i] - 1, 0)
                i5 = max(idx_map_5m[i] - 1, 0)
                if i15 >= 19 and i5 >= 59:
                    adx_val = float(pre_adx[i15])
                    atr_ratio_val = float(pre_atr_ratio[i5])
                    regime_info = self.regime_filter.check_regime_fast(adx_val, atr_ratio_val, ts)
                last_regime_check = ts
                if in_backtest_period:
                    regime_log.append({"timestamp": ts, **regime_info})

            # Skip actual trading during indicator warmup period
            if not in_backtest_period and not in_risk_warmup:
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
                if in_backtest_period:
                    closed_trades.append(trade)

            # --- Detect higher-TF candle boundaries ---
            # side="left" - 1 maps to the candle whose open_time <= ts.
            # When idx increments, a NEW candle just opened, meaning the
            # PREVIOUS candle (curr_idx - 1) just completed.  We read
            # indicators from that completed candle — no look-ahead.
            curr_5m_idx = idx_map_5m[i]
            curr_3m_idx = idx_map_3m[i]
            is_5m_boundary = curr_5m_idx > prev_5m_idx
            is_3m_boundary = curr_3m_idx > prev_3m_idx
            prev_5m_idx = curr_5m_idx
            prev_3m_idx = curr_3m_idx

            # --- Check entries (both strategies, BB gated by regime) ---
            # Only check on timeframe boundaries — matches live which checks
            # once per 5m/3m candle close.
            can_enter = len(positions) < self.risk_manager.max_open_positions
            if can_enter and active_strat:
                signal = None

                # MTF momentum on 5m candles (trend-following)
                if self.mtf_enabled and is_5m_boundary:
                    # curr_5m_idx points to the candle that just OPENED;
                    # the one that just CLOSED is curr_5m_idx - 1.
                    mtf_idx = curr_5m_idx - 1
                    if mtf_idx >= 2:
                        # MTF cooldown: at least 45 min between entries
                        if last_entry_ts is None or (ts - last_entry_ts).total_seconds() > 2700:
                            # 15m: use current candle (matches live which uses
                            # drop_last=False for 15m trend detection)
                            htf_idx = idx_map_15m[i]
                            signal = self.mtf_strategy.check_entry(mtf_idx, df_5m, df_15m, higher_tf_idx=htf_idx)

                # BB scalp on 3m candles — ONLY in ranging/transitional regimes
                if signal is None and self.bb_enabled and is_3m_boundary and active_strat in ("bollinger_scalp", "transitional"):
                    bb_idx = curr_3m_idx - 1
                    if bb_idx >= 0 and bb_idx > last_bb_entry_idx + 3:
                        # Cooldown: skip if we entered within the last 10 minutes
                        if last_entry_ts is None or (ts - last_entry_ts).total_seconds() > 600:
                            signal = self.bb_strategy.check_entry(bb_idx, df_3m)

                if signal is None and i % 2000 == 0:
                    logger.info(f"No signal at candle {i}/{n_candles}, active_strat={active_strat}, positions={len(positions)}")

                if signal:
                    sl_pct = signal["sl_pct"]
                    tp_pct = signal["tp_pct"]
                    logger.debug(f"Signal: {signal['side']} {signal.get('strategy')} sl={sl_pct:.3f}% tp={tp_pct:.3f}%")

                    can_trade, reason = self.risk_manager.can_trade(capital, sl_pct, tp_pct, ts)
                    if not can_trade:
                        logger.debug(f"Trade rejected: {reason}")
                    if can_trade:
                        # Execute at next candle's open
                        if i + 1 < n_candles:
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
                            last_entry_ts = ts
                            # Track BB entry index to avoid duplicates
                            if signal.get("strategy") == "bollinger_scalp":
                                last_bb_entry_idx = idx_map_3m[i]

            # Track equity (only during actual backtest period)
            if in_backtest_period:
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
            "bt_start_capital": capital_at_bt_start if capital_at_bt_start is not None else float(self.initial_capital),
        }

    def _check_position_exit(self, pos: dict, row, df_1m, df_3m, df_5m, df_15m, idx) -> dict | None:
        entry_price = pos["entry_price"]
        side = pos["side"]
        candle_high = row["high"]
        candle_low = row["low"]

        # Breakeven: use PRIOR state for this candle's SL check, then update
        # flag at the end. Activating and applying in the same candle is
        # look-ahead bias — the close isn't known when the low/high occurs.
        was_breakeven = pos.get("breakeven_activated", False)

        # Calculate SL/TP prices (use breakeven only if active BEFORE this candle)
        if was_breakeven:
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
            reason = "breakeven_stop" if was_breakeven else "stop_loss"
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
            mtf_idx = self._idx_map_5m[idx]
            if mtf_idx >= 0:
                exit_info = self.mtf_strategy.check_exit(pos, mtf_idx, df_5m, df_15m)
                if exit_info:
                    # Use 1m close as exit price (not 5m close which may be
                    # from an open candle). Matches live which uses ticker price.
                    exit_info["exit_price"] = row["close"]
                    return exit_info
        elif pos["strategy"] == "bollinger_scalp":
            bb_idx = self._idx_map_3m[idx]
            if bb_idx >= 0:
                exit_info = self.bb_strategy.check_exit(pos, bb_idx, df_3m)
                if exit_info:
                    exit_info["exit_price"] = row["close"]
                    return exit_info

        # Activate breakeven AFTER all exit checks so it takes effect next candle
        if not was_breakeven:
            current_pnl_pct = self._pnl_pct(pos, row["close"])
            if current_pnl_pct >= 0.3:
                pos["breakeven_activated"] = True
                pos["original_sl_pct"] = pos["sl_pct"]

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
        """Entry uses taker order (market) to match live execution."""
        return notional * (self.fee_taker + self.slippage)

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
