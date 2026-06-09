import os
import sys
import time
import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.helpers import load_config
from src.utils.logger import setup_logger
from src.utils.notifier import TelegramNotifier
from src.exchange.data_fetcher import DataFetcher
from src.exchange.binance_client import BinanceClient
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.backtesting.engine import BacktestEngine
from src.backtesting.report import BacktestReport
from src.strategies.mtf_momentum import MTFMomentumStrategy
from src.strategies.regime_filter import RegimeFilter
from src.risk.risk_manager import RiskManager
from src.database.trade_store import TradeStore


# ---------------------------------------------------------------------------
# Backtest mode (unchanged)
# ---------------------------------------------------------------------------

def run_backtest(config: dict):
    from loguru import logger as _log
    _log = setup_logger(
        level=config["logging"]["level"],
        log_file=config["logging"]["file"],
        console=config["logging"]["console"],
    )
    _log.info("=== STARTING BACKTEST ===")

    fetcher = DataFetcher(config)
    start = config["backtest"]["start_date"]
    end = config["backtest"]["end_date"]

    from datetime import datetime as _dt, timedelta as _td
    # Warmup: go back to the earlier of 5 days or the ISO-week Monday
    # so the backtest risk manager accumulates weekly PnL from same-week trades.
    start_dt = _dt.strptime(start, "%Y-%m-%d")
    iso = start_dt.isocalendar()
    week_monday = _dt.fromisocalendar(iso[0], iso[1], 1)
    warmup_start = min(start_dt - _td(days=5), week_monday).strftime("%Y-%m-%d")

    _log.info(f"Fetching 1m data from {warmup_start} to {end}")
    df_1m = fetcher.fetch_ohlcv("1m", warmup_start, end)
    if df_1m.empty:
        _log.error("No data fetched. Exiting.")
        return

    _log.info(f"Fetched {len(df_1m)} 1m candles")
    df_3m = fetcher.resample(df_1m, "3m")
    df_5m = fetcher.resample(df_1m, "5m")
    df_15m = fetcher.resample(df_1m, "15m")
    _log.info(f"Resampled: 3m={len(df_3m)}, 5m={len(df_5m)}, 15m={len(df_15m)}")

    engine = BacktestEngine(config)
    results = engine.run(df_1m, df_3m, df_5m, df_15m)

    report = BacktestReport(results, config)
    report.generate()
    _log.info("=== BACKTEST COMPLETE ===")
    return results


# ---------------------------------------------------------------------------
# Live / Paper trading mode
# ---------------------------------------------------------------------------

class LiveTrader:
    """Live execution loop that mirrors backtest engine logic exactly.

    Cycle (every 5 minutes on candle close):
      1. Fetch latest 5m and 15m candle data
      2. Calculate indicators (same as backtest)
      3. Check open position exits: breakeven, trailing, time, SL/TP (on-exchange)
      4. If no position + cooldown OK → check entry signal
      5. On signal → place limit entry, then SL + TP on exchange
      6. Risk manager gatekeeping (same limits as backtest)
    """

    def __init__(self, config: dict):
        self.config = config
        self.log = setup_logger(
            level=config["logging"]["level"],
            log_file=config["logging"]["file"],
            console=config["logging"]["console"],
        )

        # Exchange connection
        self.client = BinanceClient(config)
        self.order_mgr = OrderManager(self.client)
        be_threshold = config.get("mtf_momentum", {}).get("breakeven_threshold_pct", 0.3)
        self.tracker = PositionTracker(breakeven_threshold_pct=be_threshold)
        self.trade_store = TradeStore()

        # Telegram notifications
        self.notifier = TelegramNotifier(config)

        # Strategy components — same as backtest engine
        self.mtf_strategy = MTFMomentumStrategy(config)
        self.regime_filter = RegimeFilter(config)
        self.risk_manager = RiskManager(config)

        # Restore risk state from trade history so limits survive restarts
        past_trades = self.trade_store.get_all_trades()
        if past_trades:
            self.risk_manager.restore_from_trades(past_trades)

        # Override starting_capital with actual live balance on first startup
        try:
            live_balance = self.client.get_balance()
            if live_balance > 0:
                self.risk_manager.starting_capital = live_balance - sum(
                    t.get("net_pnl", 0) for t in past_trades
                ) if past_trades else live_balance
                self.risk_manager.current_capital = live_balance
        except Exception:
            pass  # Will be set on first candle cycle

        # Config values
        self.leverage = config["trading"]["leverage"]
        self.fee_maker = config["fees"]["maker"] / 100
        self.fee_taker = config["fees"]["taker"] / 100
        self.slippage = config["fees"]["slippage"] / 100
        self.cooldown_sec = 2700  # 45 minutes — matches backtest

        # Warmup: how many 5m candles we need for indicators
        # 200 5m = ~16h → EMA(50) fully converged; matches backtest which uses full history
        self.warmup_candles_5m = 200
        self.warmup_candles_15m = 100

        # Regime tracking
        self.last_regime_check = None
        self.last_regime_info = {"regime": "UNKNOWN", "override": None}

    # ---- Main loop ----

    def run(self):
        self.log.info("=" * 60)
        self.log.info("  LIVE TRADER STARTING")
        self.log.info(f"  Symbol: {self.client.symbol}")
        self.log.info(f"  Leverage: {self.leverage}x")
        self.log.info(f"  Demo mode: {self.client.is_demo}")
        self.log.info(f"  Telegram: {'ON' if self.notifier.enabled else 'OFF'}")
        self.log.info("=" * 60)

        # Initial setup
        self.client.set_margin_mode("cross")
        self.client.set_leverage()

        balance = self.client.get_balance()
        self.risk_manager.current_capital = balance
        self.log.info(f"Account balance: ${balance:.2f}")

        # Startup notification
        self.notifier.notify_startup(self.client.symbol, self.leverage, balance, self.client.is_demo)

        # Sync with any existing exchange position
        self._sync_exchange_position()

        # Main loop — poll every 3 seconds, act on 5m candle close
        last_5m_ts = None
        cycle_count = 0
        start_time = time.time()
        status_sent = 0
        while True:
            try:
                now = datetime.now(timezone.utc)
                cycle_count += 1

                # ---- Position monitoring (every cycle) ----
                if self.tracker.has_position:
                    self._monitor_position()

                # ---- Stale order cleanup (no position, check every ~30s) ----
                # Only cancel when no position — avoids killing active SL/TP.
                if not self.tracker.has_position and cycle_count % 10 == 0:
                    try:
                        stale = self.client.fetch_open_orders()
                        if stale:
                            self.log.warning(f"Found {len(stale)} stale orders with no position — cleaning up")
                            self.order_mgr._cancel_all_verified()
                    except Exception:
                        pass

                # ---- Signal check on 5m candle close ----
                # A 5m candle closes at :00, :05, :10, ... minutes
                current_5m = now.replace(second=0, microsecond=0)
                current_5m = current_5m.replace(minute=(current_5m.minute // 5) * 5)

                if last_5m_ts is None or current_5m > last_5m_ts:
                    # Wait 5s after candle close for exchange data to finalize.
                    # 3s was too short — Binance sometimes takes 4-5s to finalize
                    # the last candle's close/high/low, causing stale data reads.
                    if now.second < 5:
                        wait = 5 - now.second
                        self.log.debug(f"Waiting {wait}s for candle data to settle")
                        time.sleep(wait)

                    last_5m_ts = current_5m
                    self.log.info(f"--- 5m candle close: {current_5m.strftime('%H:%M')} UTC ---")
                    t0 = time.time()
                    self._on_candle_close(now)
                    elapsed = time.time() - t0
                    self.log.info(f"Candle processing took {elapsed:.1f}s")

                # Send status notification every 6 hours
                uptime_min = int((time.time() - start_time) / 60)
                hours_elapsed = uptime_min // 360  # every 6 hours
                if hours_elapsed > status_sent:
                    balance = self.client.get_balance()
                    self.notifier.notify_status(uptime_min, balance, self.tracker.has_position)
                    status_sent = hours_elapsed

                time.sleep(3)

            except KeyboardInterrupt:
                self.log.info("Shutting down gracefully...")
                self.notifier.notify_shutdown()
                break
            except Exception as e:
                self.log.error(f"Loop error: {e}")
                self.notifier.notify_error(f"Loop error: {e}")
                time.sleep(30)

    # ---- On each 5-min candle close ----

    def _on_candle_close(self, now: datetime):
        """Fetch data, compute indicators, check signals — mirrors backtest per-candle logic."""
        try:
            # Fetch enough 5m candles for indicator warmup
            df_5m = self._fetch_candles("5m", self.warmup_candles_5m)
            df_15m = self._fetch_candles("15m", self.warmup_candles_15m, drop_last=False)

            if df_5m is None or len(df_5m) < 30 or df_15m is None or len(df_15m) < 10:
                self.log.warning("Insufficient candle data, skipping cycle")
                return

            # Calculate indicators — SAME functions as backtest
            df_5m = self.mtf_strategy.calculate_indicators(df_5m)
            df_15m = self.mtf_strategy.calculate_higher_tf_indicators(df_15m)

            # Reset daily/hourly counters — same as backtest
            self.risk_manager.reset_day(now)
            self.risk_manager.reset_hour(now)
            self.risk_manager.reset_week(now)

            # Regime check every 15 minutes — same as backtest
            if self.last_regime_check is None or (now - self.last_regime_check).total_seconds() >= 900:
                if len(df_15m) >= 20 and len(df_5m) >= 60:
                    self.last_regime_info = self.regime_filter.calculate_indicators(df_15m, df_5m)
                    self.last_regime_check = now
                    self.log.info(f"Regime: {self.last_regime_info['regime']}")

            active_strat = self.regime_filter.get_active_strategy(self.last_regime_info, now)

            # ---- Strategy-specific exit check on 5m boundary (mirrors backtest) ----
            if self.tracker.has_position:
                pos = self.tracker.position
                if pos.get("strategy") == "mtf_momentum":
                    try:
                        ticker = self.client.fetch_ticker()
                        current_price = float(ticker["last"])
                        strat_exit = self.mtf_strategy.check_exit(
                            pos, len(df_5m) - 1, df_5m, df_15m
                        )
                        if strat_exit:
                            self._close_position_live(strat_exit["reason"], current_price)
                    except Exception as e:
                        self.log.debug(f"Strategy exit check: {e}")

            # ---- Entry check (only if no position) ----
            if not self.tracker.has_position and active_strat:
                self._check_entry(df_5m, df_15m, now)

        except Exception as e:
            self.log.error(f"Candle processing error: {e}")
            self.notifier.notify_error(f"Candle processing: {e}")

    # ---- Entry Logic (mirrors backtest exactly) ----

    def _check_entry(self, df_5m: pd.DataFrame, df_15m: pd.DataFrame, now: datetime):
        """Check for entry signal — same logic as backtest engine entry section."""
        # Cooldown check — 45 min between entries (same as backtest)
        if not self.tracker.cooldown_ok(self.cooldown_sec):
            return

        # Check signal on latest completed 5m candle
        idx = len(df_5m) - 1
        if idx < 2:
            return

        # Log indicator state for debugging
        row = df_5m.iloc[idx]
        prev = df_5m.iloc[idx - 1]
        ema_f = row.get("ema_fast")
        ema_s = row.get("ema_slow")
        rsi = row.get("rsi")
        cross_up = prev.get("ema_fast", 0) <= prev.get("ema_slow", 0) and ema_f is not None and ema_s is not None and ema_f > ema_s
        cross_dn = prev.get("ema_fast", 0) >= prev.get("ema_slow", 0) and ema_f is not None and ema_s is not None and ema_f < ema_s
        trend = self.mtf_strategy._get_trend_by_idx(df_15m, len(df_15m) - 1)
        self.log.info(
            f"5m candle: EMA_f={ema_f:.2f} EMA_s={ema_s:.2f} RSI={rsi:.1f} | "
            f"cross_up={cross_up} cross_dn={cross_dn} trend={trend}"
        )

        # Pass higher_tf_idx for O(1) lookup — matches backtest's _get_trend_by_idx
        higher_tf_idx = len(df_15m) - 1
        signal = self.mtf_strategy.check_entry(idx, df_5m, df_15m, higher_tf_idx=higher_tf_idx)
        if signal is None:
            return

        sl_pct = signal["sl_pct"]
        tp_pct = signal["tp_pct"]
        self.log.info(f"Signal: {signal['side']} | SL={sl_pct:.3f}% TP={tp_pct:.3f}%")

        # Risk check — same function as backtest
        balance = self.client.get_balance()
        self.risk_manager.current_capital = balance
        can_trade, reason = self.risk_manager.can_trade(balance, sl_pct, tp_pct, now)
        if not can_trade:
            self.log.info(f"Trade rejected: {reason}")
            return

        # Use the closed 5m candle's close as reference price for SL/TP calculation.
        # Backtest enters at next-candle open; live enters at market. Using the candle
        # close (not ticker) for SL/TP % offsets keeps the risk:reward geometry closer
        # to what the backtest computes, since backtest uses exec_price for SL/TP.
        candle_close_price = float(row["close"])

        # Position sizing — same as backtest
        size_value = self.risk_manager.calculate_position_size(balance, sl_pct)
        ticker = self.client.fetch_ticker()
        current_price = float(ticker["last"])
        size_contracts = (size_value * self.leverage) / current_price

        # Round to Binance precision (3 decimals for ETH)
        size_contracts = round(size_contracts, 3)
        if size_contracts <= 0:
            self.log.warning(f"Position size too small: {size_contracts}")
            return

        # Calculate SL/TP prices from candle close (not ticker) so % offsets match
        # backtest geometry. They get recalculated from fill price after execution.
        if signal["side"] == "long":
            sl_price = round(candle_close_price * (1 - sl_pct / 100), 2)
            tp_price = round(candle_close_price * (1 + tp_pct / 100), 2)
        else:
            sl_price = round(candle_close_price * (1 + sl_pct / 100), 2)
            tp_price = round(candle_close_price * (1 - tp_pct / 100), 2)

        # ---- Execute entry ----
        try:
            # Use market order for reliable fill (taker fee)
            # Note: backtest uses maker fee assumption, but market orders ensure fill
            entry_order = self.order_mgr.place_market_order(signal["side"], size_contracts)
            fill_price = float(entry_order.get("average", entry_order.get("price", current_price)))

            # Recalculate SL/TP based on actual fill price
            if signal["side"] == "long":
                sl_price = round(fill_price * (1 - sl_pct / 100), 2)
                tp_price = round(fill_price * (1 + tp_pct / 100), 2)
            else:
                sl_price = round(fill_price * (1 + sl_pct / 100), 2)
                tp_price = round(fill_price * (1 - tp_pct / 100), 2)

            # Place SL and TP on exchange
            sl_order, tp_order = self.order_mgr.place_sl_tp(
                signal["side"], size_contracts, sl_price, tp_price
            )

            sl_order_id = sl_order.get("id", "") if sl_order else ""
            tp_order_id = tp_order.get("id", "") if tp_order else ""

            # Track position — mirrors backtest pos dict
            self.tracker.open_position(
                side=signal["side"],
                strategy=signal.get("strategy", "mtf_momentum"),
                entry_price=fill_price,
                size_contracts=size_contracts,
                size_value=size_value,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                sl_price=sl_price,
                tp_price=tp_price,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                trailing_activation=signal.get("trailing_activation"),
                trailing_distance=signal.get("trailing_distance"),
                max_duration=signal.get("max_duration"),
            )
            self.risk_manager.open_positions = 1  # mirrors backtest

            self.log.info(
                f"ENTRY {signal['side'].upper()} @ {fill_price:.2f} | "
                f"size={size_contracts:.3f} | SL={sl_price:.2f} TP={tp_price:.2f}"
            )

            # Telegram notification — trade opened
            self.notifier.notify_entry(
                side=signal["side"],
                price=fill_price,
                size=size_contracts,
                sl=sl_price,
                tp=tp_price,
                strategy=signal.get("strategy", "mtf_momentum"),
                leverage=self.leverage,
                balance=balance,
            )

        except Exception as e:
            self.log.error(f"Entry execution failed: {e}")
            self.notifier.notify_error(f"Entry execution failed: {e}")
            # Cancel any partial orders
            try:
                self.order_mgr.cancel_all()
            except Exception:
                pass
            # Check if market order already filled (orphan position)
            try:
                exchange_pos = self.client.fetch_positions()
                if exchange_pos:
                    self.log.error(
                        "Entry partially succeeded — position exists on exchange. "
                        "Closing orphan position with market order."
                    )
                    p = exchange_pos[0]
                    orphan_side = "long" if float(p.get("contracts", 0)) > 0 else "short"
                    orphan_size = abs(float(p.get("contracts", 0)))
                    self.order_mgr.close_position_market(orphan_side, orphan_size)
                    self.notifier.notify_position_risk(
                        orphan_side, 0, 0,
                        "Orphan Position Detected & Closed",
                        f"Entry partially failed. Orphan {orphan_side} {orphan_size} contracts "
                        f"closed at market. Verify on exchange."
                    )
            except Exception as e2:
                self.log.error(f"Orphan position cleanup failed: {e2}")
                self.notifier.notify_position_risk(
                    "unknown", 0, 0,
                    "CRITICAL: Orphan Position May Exist",
                    f"Entry failed, orphan cleanup also failed: {e2}. "
                    f"CHECK EXCHANGE IMMEDIATELY and close manually if needed."
                )

    # ---- Position Monitoring (every 10s cycle) ----

    def _monitor_position(self):
        """Check breakeven, trailing stop, time exit — mirrors backtest _check_position_exit."""
        if not self.tracker.has_position:
            return

        try:
            ticker = self.client.fetch_ticker()
            current_price = float(ticker["last"])
            pos = self.tracker.position

            # 1. Check breakeven — move SL to entry price at +0.3%
            if self.tracker.check_breakeven(current_price):
                # Update SL on exchange to breakeven price (also re-places TP)
                new_sl, new_tp = self.order_mgr.update_stop_loss(
                    pos["sl_order_id"], pos["side"], pos["size_contracts"],
                    pos["entry_price"], tp_price=pos["tp_price"]
                )
                if new_sl is None:
                    # SL would immediately trigger — price crossed back past entry
                    self.log.warning("Breakeven SL rejected — closing at market")
                    self.notifier.notify_position_risk(
                        pos["side"], pos["entry_price"], current_price,
                        "Breakeven SL Rejected",
                        "Price crossed back below entry. SL would trigger immediately. "
                        "Closing position at market."
                    )
                    self._close_position_live("breakeven_sl_rejected", current_price)
                    return
                if new_sl:
                    self.tracker.position["sl_order_id"] = new_sl.get("id", "")
                    self.tracker.position["sl_price"] = pos["entry_price"]
                if new_tp:
                    self.tracker.position["tp_order_id"] = new_tp.get("id", "")
                self.tracker._save_state()
                self.log.info(f"SL moved to breakeven: {pos['entry_price']:.2f}")
                self.notifier.notify_warning(
                    "SL Moved to Breakeven",
                    f"{pos['side'].upper()} @ ${pos['entry_price']:,.2f} — "
                    f"SL now at entry. Current price: ${current_price:,.2f}",
                )

            # 2. Check trailing stop
            if self.tracker.check_trailing_stop(current_price):
                self._close_position_live("trailing_stop", current_price)
                return

            # 3. Check time-based exit (mirrors backtest mtf_strategy.check_exit)
            if self.tracker.check_time_exit():
                self._close_position_live("time_exit", current_price)
                return

            # 3b. Strategy-specific exits are checked in _on_candle_close (every 5m),
            # not here (every 3s). This matches backtest which checks on 5m boundaries
            # and avoids redundant API calls + indicator calculations every cycle.

            # 4. Check if exchange already closed position (SL/TP triggered)
            exchange_positions = self.client.fetch_positions()
            if not exchange_positions:
                # Position was closed by exchange (SL or TP hit)
                self.log.info("Position closed by exchange (SL/TP triggered)")
                # Cancel remaining SL or TP — verified retry for demo API
                self.order_mgr._cancel_all_verified()

                # Determine exit reason and use the actual SL/TP price (not ticker)
                sl_price = pos.get("sl_price", 0)
                tp_price = pos.get("tp_price", 0)
                if pos["side"] == "long":
                    if current_price <= sl_price * 1.005:
                        reason = "breakeven_stop" if pos.get("breakeven_activated") else "stop_loss"
                        exit_price = sl_price
                    else:
                        reason = "take_profit"
                        exit_price = tp_price
                else:
                    if current_price >= sl_price * 0.995:
                        reason = "breakeven_stop" if pos.get("breakeven_activated") else "stop_loss"
                        exit_price = sl_price
                    else:
                        reason = "take_profit"
                        exit_price = tp_price

                self.notifier.notify_warning(
                    f"Exchange Closed Position — {reason.upper()}",
                    f"{pos['side'].upper()} @ ${pos['entry_price']:,.2f} → "
                    f"${exit_price:,.2f} | Reason: {reason}",
                )

                trade = self.tracker.close_position(exit_price, reason)
                if trade:
                    self._record_trade(trade)

                # Post-close: verify no orphan orders remain
                try:
                    remaining = self.client.fetch_open_orders()
                    if remaining:
                        self.log.warning(
                            f"Post-exchange-close: {len(remaining)} orphan orders — cancelling"
                        )
                        self.order_mgr._cancel_all_verified()
                except Exception:
                    pass

        except Exception as e:
            self.log.error(f"Position monitor error: {e}")
            self.notifier.notify_error(f"Position monitor: {e}")

    def _close_position_live(self, reason: str, current_price: float):
        """Close position: cancel all orders, market close, record trade."""
        pos = self.tracker.position
        if not pos:
            return

        try:
            # Cancel all open SL/TP orders — verified retry for demo API
            self.order_mgr._cancel_all_verified()
            # Market close
            self.order_mgr.close_position_market(pos["side"], pos["size_contracts"])
        except Exception as e:
            self.log.error(f"Close execution error: {e}")
            self.notifier.notify_position_risk(
                pos["side"], pos["entry_price"], current_price,
                "Close Execution Failed",
                f"Failed to close {pos['side']} position: {e}. "
                f"Position may still be open on exchange!"
            )

        trade = self.tracker.close_position(current_price, reason)
        if trade:
            self._record_trade(trade)

        # Post-close safety: verify no orphan orders remain on exchange.
        # This catches cases where _cancel_all_verified partially failed
        # or algo orders were missed during the cancel cycle.
        try:
            remaining = self.client.fetch_open_orders()
            if remaining:
                self.log.warning(
                    f"Post-close: {len(remaining)} orphan orders found — cancelling"
                )
                self.order_mgr._cancel_all_verified()
        except Exception:
            pass

    def _record_trade(self, trade: dict):
        """Record to DB and risk manager — mirrors backtest record_trade call."""
        # Approximate net PnL (exchange fees already deducted from balance)
        pnl_pct = trade.get("pnl_pct", 0)
        notional = trade.get("size_value", 0) * self.leverage
        raw_pnl = pnl_pct / 100 * notional
        # Estimate fees: entry taker + exit taker (market orders)
        fees = notional * (self.fee_taker + self.slippage) * 2
        net_pnl = raw_pnl - fees

        # Risk manager — same as backtest engine's record_trade call
        self.risk_manager.record_trade(net_pnl, datetime.now(timezone.utc))
        self.risk_manager.open_positions = 0  # mirrors backtest

        # Persist to SQLite
        # Calculate duration for DB storage
        entry_ts = trade.get("entry_ts")
        duration_min = 0.0
        if entry_ts:
            try:
                if isinstance(entry_ts, str):
                    entry_dt = datetime.fromisoformat(entry_ts)
                else:
                    entry_dt = entry_ts
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                duration_min = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
            except Exception:
                pass

        self.trade_store.log_trade({
            "timestamp": trade.get("exit_ts", datetime.now(timezone.utc).isoformat()),
            "strategy": trade["strategy"],
            "side": trade["side"],
            "entry_price": trade["entry_price"],
            "exit_price": trade["exit_price"],
            "size": trade.get("size_value", 0),
            "pnl": raw_pnl,
            "fees": fees,
            "net_pnl": net_pnl,
            "duration_minutes": duration_min,
            "exit_reason": trade["exit_reason"],
            "regime": self.last_regime_info.get("regime", ""),
        })
        self.log.info(f"Trade recorded: net_pnl=${net_pnl:+.2f} | reason={trade['exit_reason']}")

        # Telegram notification — trade closed
        try:
            current_balance = self.client.get_balance()
        except Exception:
            current_balance = self.risk_manager.current_capital + net_pnl
        self.notifier.notify_exit(
            side=trade["side"],
            entry_price=trade["entry_price"],
            exit_price=trade["exit_price"],
            pnl_pct=pnl_pct,
            net_pnl=net_pnl,
            reason=trade["exit_reason"],
            size=trade.get("size_contracts", 0),
            duration_min=duration_min,
            balance=current_balance,
        )

    # ---- Data Fetching ----

    def _fetch_candles(self, timeframe: str, count: int, drop_last: bool = True) -> pd.DataFrame:
        """Fetch recent candles from exchange for live indicator calculation.
        
        Args:
            drop_last: If True, drops the last (incomplete) candle. Use True for
                       the entry timeframe (5m) so signals use closed candles.
                       Use False for higher timeframes (15m) so trend detection
                       uses current market data (matches backtest behaviour).
        """
        try:
            raw = self.client.fetch_ohlcv(timeframe, limit=count + (1 if drop_last else 0))
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            if drop_last and len(df) > 1:
                df = df.iloc[:-1].reset_index(drop=True)
            return df
        except Exception as e:
            self.log.error(f"Fetch {timeframe} candles error: {e}")
            return None

    # ---- Exchange State Sync ----

    def _sync_exchange_position(self):
        """On startup, check if there's an existing position on exchange."""
        try:
            positions = self.client.fetch_positions()
            if positions and not self.tracker.has_position:
                p = positions[0]
                side = "long" if float(p.get("contracts", 0)) > 0 else "short"
                contracts = abs(float(p.get('contracts', 0)))
                entry_price = float(p.get('entryPrice', 0))
                unrealized_pnl = float(p.get('unrealizedPnl', 0))
                self.log.warning(
                    f"Found orphan exchange position: {side} {contracts} contracts. "
                    f"Tracker has no record — manual intervention may be needed."
                )
                self.notifier.notify_position_risk(
                    side, entry_price, entry_price,
                    "Orphan Position on Startup",
                    f"{side.upper()} {contracts} contracts @ ${entry_price:,.2f} "
                    f"(uPnL: ${unrealized_pnl:+,.2f}). "
                    f"Bot has no record of this position. Close manually if unintended."
                )

            elif self.tracker.has_position and not positions:
                pos = self.tracker.position
                self.log.warning(
                    "Tracker has position but exchange does not — position was closed while offline."
                )
                self.notifier.notify_warning(
                    "Position Closed While Offline",
                    f"{pos['side'].upper()} @ ${pos['entry_price']:,.2f} was closed "
                    f"while bot was offline. Recording as offline_close.",
                )
                # Close tracker position
                ticker = self.client.fetch_ticker()
                trade = self.tracker.close_position(float(ticker["last"]), "offline_close")
                if trade:
                    self._record_trade(trade)
        except Exception as e:
            self.log.error(f"Position sync error: {e}")
            self.notifier.notify_error(f"Position sync on startup failed: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ETH/USDT Scalping Bot")
    parser.add_argument("--mode", default=os.getenv("BOT_MODE", "backtest"),
                        choices=["backtest", "paper", "live"])
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    config["trading"]["mode"] = args.mode

    if args.mode == "backtest":
        run_backtest(config)
    elif args.mode in ("paper", "live"):
        trader = LiveTrader(config)
        trader.run()


if __name__ == "__main__":
    main()
