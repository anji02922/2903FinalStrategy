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
    warmup_start = (_dt.strptime(start, "%Y-%m-%d") - _td(days=5)).strftime("%Y-%m-%d")

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
        self.tracker = PositionTracker()
        self.trade_store = TradeStore()

        # Telegram notifications
        self.notifier = TelegramNotifier(config)

        # Strategy components — same as backtest engine
        self.mtf_strategy = MTFMomentumStrategy(config)
        self.regime_filter = RegimeFilter(config)
        self.risk_manager = RiskManager(config)

        # Config values
        self.leverage = config["trading"]["leverage"]
        self.fee_maker = config["fees"]["maker"] / 100
        self.fee_taker = config["fees"]["taker"] / 100
        self.slippage = config["fees"]["slippage"] / 100
        self.cooldown_sec = 2700  # 45 minutes — matches backtest

        # Warmup: how many 5m candles we need for indicators
        self.warmup_candles_5m = 60
        self.warmup_candles_15m = 30

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

        # Startup log only (no Telegram spam for non-trade events)

        # Sync with any existing exchange position
        self._sync_exchange_position()

        # Main loop — poll every 3 seconds, act on 5m candle close
        last_5m_ts = None
        cycle_count = 0
        while True:
            try:
                now = datetime.now(timezone.utc)
                cycle_count += 1

                # ---- Position monitoring (every cycle) ----
                if self.tracker.has_position:
                    self._monitor_position()

                # ---- Signal check on 5m candle close ----
                # A 5m candle closes at :00, :05, :10, ... minutes
                current_5m = now.replace(second=0, microsecond=0)
                current_5m = current_5m.replace(minute=(current_5m.minute // 5) * 5)

                if last_5m_ts is None or current_5m > last_5m_ts:
                    # Wait 3s after candle close for exchange data to finalize
                    if now.second < 3:
                        wait = 3 - now.second
                        self.log.debug(f"Waiting {wait}s for candle data to settle")
                        time.sleep(wait)

                    last_5m_ts = current_5m
                    self.log.info(f"--- 5m candle close: {current_5m.strftime('%H:%M')} UTC ---")
                    t0 = time.time()
                    self._on_candle_close(now)
                    elapsed = time.time() - t0
                    self.log.info(f"Candle processing took {elapsed:.1f}s")

                # Log heartbeat every ~60 cycles (~3 min)
                if cycle_count % 60 == 0:
                    pos_status = "IN POSITION" if self.tracker.has_position else "NO POSITION"
                    self.log.debug(f"Heartbeat: cycle={cycle_count} | {pos_status}")

                time.sleep(3)

            except KeyboardInterrupt:
                self.log.info("Shutting down gracefully...")
                break
            except Exception as e:
                self.log.error(f"Loop error: {e}")
                time.sleep(30)

    # ---- On each 5-min candle close ----

    def _on_candle_close(self, now: datetime):
        """Fetch data, compute indicators, check signals — mirrors backtest per-candle logic."""
        try:
            # Fetch enough 5m candles for indicator warmup
            df_5m = self._fetch_candles("5m", self.warmup_candles_5m)
            df_15m = self._fetch_candles("15m", self.warmup_candles_15m)

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

            # ---- Entry check (only if no position) ----
            if not self.tracker.has_position and active_strat:
                self._check_entry(df_5m, df_15m, now)

        except Exception as e:
            self.log.error(f"Candle processing error: {e}")

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
        trend = self.mtf_strategy._get_trend(df_15m, row["timestamp"])
        self.log.info(
            f"5m candle: EMA_f={ema_f:.2f} EMA_s={ema_s:.2f} RSI={rsi:.1f} | "
            f"cross_up={cross_up} cross_dn={cross_dn} trend={trend}"
        )

        signal = self.mtf_strategy.check_entry(idx, df_5m, df_15m)
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

        # Calculate SL/TP prices — same formula as backtest
        if signal["side"] == "long":
            sl_price = round(current_price * (1 - sl_pct / 100), 2)
            tp_price = round(current_price * (1 + tp_pct / 100), 2)
        else:
            sl_price = round(current_price * (1 + sl_pct / 100), 2)
            tp_price = round(current_price * (1 - tp_pct / 100), 2)

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
            # Cancel any partial orders
            try:
                self.order_mgr.cancel_all()
            except Exception:
                pass

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
                # Update SL on exchange to breakeven price
                new_sl = self.order_mgr.update_stop_loss(
                    pos["sl_order_id"], pos["side"], pos["size_contracts"], pos["entry_price"]
                )
                self.tracker.position["sl_order_id"] = new_sl.get("id", "") if new_sl else ""
                self.tracker.position["sl_price"] = pos["entry_price"]
                self.tracker._save_state()
                self.log.info(f"SL moved to breakeven: {pos['entry_price']:.2f}")

            # 2. Check trailing stop
            if self.tracker.check_trailing_stop(current_price):
                self._close_position_live("trailing_stop", current_price)
                return

            # 3. Check time-based exit (mirrors backtest mtf_strategy.check_exit)
            if self.tracker.check_time_exit():
                self._close_position_live("time_exit", current_price)
                return

            # 3b. Strategy-specific exits (mirrors backtest engine calling strategy.check_exit)
            if pos.get("strategy") == "mtf_momentum":
                try:
                    df_5m = self._fetch_candles("5m", 10)
                    df_15m = self._fetch_candles("15m", 5)
                    if df_5m is not None and len(df_5m) > 2:
                        strat_exit = self.mtf_strategy.check_exit(pos, len(df_5m) - 1, df_5m, df_15m)
                        if strat_exit:
                            self._close_position_live(strat_exit["reason"], current_price)
                            return
                except Exception as e:
                    self.log.debug(f"Strategy exit check: {e}")

            # 4. Check if exchange already closed position (SL/TP triggered)
            exchange_positions = self.client.fetch_positions()
            if not exchange_positions:
                # Position was closed by exchange (SL or TP hit)
                self.log.info("Position closed by exchange (SL/TP triggered)")
                open_orders = self.client.fetch_open_orders()
                # Cancel remaining SL or TP order
                for order in open_orders:
                    try:
                        self.order_mgr.cancel_order(order["id"])
                    except Exception:
                        pass

                # Determine exit reason based on price vs SL/TP
                if pos["side"] == "long":
                    if current_price <= pos.get("sl_price", 0) * 1.001:
                        reason = "breakeven_stop" if pos.get("breakeven_activated") else "stop_loss"
                    else:
                        reason = "take_profit"
                else:
                    if current_price >= pos.get("sl_price", float("inf")) * 0.999:
                        reason = "breakeven_stop" if pos.get("breakeven_activated") else "stop_loss"
                    else:
                        reason = "take_profit"

                trade = self.tracker.close_position(current_price, reason)
                if trade:
                    self._record_trade(trade)

        except Exception as e:
            self.log.error(f"Position monitor error: {e}")

    def _close_position_live(self, reason: str, current_price: float):
        """Close position: cancel all orders, market close, record trade."""
        pos = self.tracker.position
        if not pos:
            return

        try:
            # Cancel all open SL/TP orders
            self.order_mgr.cancel_all()
            # Market close
            self.order_mgr.close_position_market(pos["side"], pos["size_contracts"])
        except Exception as e:
            self.log.error(f"Close execution error: {e}")

        trade = self.tracker.close_position(current_price, reason)
        if trade:
            self._record_trade(trade)

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

        # Persist to SQLite
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
            "duration_minutes": 0,
            "exit_reason": trade["exit_reason"],
            "regime": self.last_regime_info.get("regime", ""),
        })
        self.log.info(f"Trade recorded: net_pnl=${net_pnl:+.2f} | reason={trade['exit_reason']}")

        # Telegram notification — trade closed
        try:
            current_balance = self.client.get_balance()
        except Exception:
            current_balance = self.risk_manager.current_capital + net_pnl
        # Calculate duration
        entry_ts = trade.get("entry_ts")
        duration_min = 0.0
        if entry_ts:
            try:
                from dateutil.parser import parse as dt_parse
                entry_dt = dt_parse(entry_ts) if isinstance(entry_ts, str) else entry_ts
                duration_min = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
            except Exception:
                pass
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

    def _fetch_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        """Fetch recent candles from exchange for live indicator calculation.
        Drops the last (incomplete/current) candle to match backtest behavior."""
        try:
            raw = self.client.fetch_ohlcv(timeframe, limit=count + 1)  # +1 because we drop last
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            # Drop the last candle — it's the current incomplete candle
            # Backtest only ever uses completed candles
            if len(df) > 1:
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
                self.log.warning(
                    f"Found orphan exchange position: {side} {abs(float(p.get('contracts', 0)))} contracts. "
                    f"Tracker has no record — manual intervention may be needed."
                )

            elif self.tracker.has_position and not positions:
                self.log.warning(
                    "Tracker has position but exchange does not — position was closed while offline."
                )
                # Close tracker position
                ticker = self.client.fetch_ticker()
                trade = self.tracker.close_position(float(ticker["last"]), "offline_close")
                if trade:
                    self._record_trade(trade)
        except Exception as e:
            self.log.error(f"Position sync error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ETH/USDT Scalping Bot")
    parser.add_argument("--mode", default="backtest", choices=["backtest", "paper", "live"])
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
