import json
import os
from datetime import datetime, timezone
from loguru import logger


STATE_FILE = "data/live_state.json"


class PositionTracker:
    """Tracks open position state with JSON persistence for crash recovery.

    Mirrors backtest engine's position management exactly:
    - Breakeven stop at +0.3%
    - Trailing stop (activation + distance from highest PnL)
    - Time-based exit (max_duration)
    """

    def __init__(self):
        self.position = None  # single position (max_open_positions=1)
        self.last_entry_ts = None  # for cooldown tracking
        self._load_state()

    # ---- State Persistence ----

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                if state.get("position"):
                    self.position = state["position"]
                    # Convert stored ISO timestamps back
                    if self.position.get("entry_ts"):
                        self.position["entry_ts"] = datetime.fromisoformat(self.position["entry_ts"])
                    logger.info(f"Restored position: {self.position['side']} @ {self.position['entry_price']}")
                if state.get("last_entry_ts"):
                    self.last_entry_ts = datetime.fromisoformat(state["last_entry_ts"])
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def _save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        state = {
            "position": None,
            "last_entry_ts": self.last_entry_ts.isoformat() if self.last_entry_ts else None,
        }
        if self.position:
            pos_copy = dict(self.position)
            if isinstance(pos_copy.get("entry_ts"), datetime):
                pos_copy["entry_ts"] = pos_copy["entry_ts"].isoformat()
            state["position"] = pos_copy
        # Atomic write: write to temp file, then replace
        tmp_file = STATE_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_file, STATE_FILE)

    # ---- Position Lifecycle ----

    def open_position(self, side: str, strategy: str, entry_price: float, size_contracts: float,
                      size_value: float, sl_pct: float, tp_pct: float, sl_price: float, tp_price: float,
                      sl_order_id: str, tp_order_id: str,
                      trailing_activation: float = None, trailing_distance: float = None,
                      max_duration: int = None):
        now = datetime.now(timezone.utc)
        self.position = {
            "side": side,
            "strategy": strategy,
            "entry_price": entry_price,
            "entry_ts": now,
            "size_contracts": size_contracts,
            "size_value": size_value,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "trailing_activation": trailing_activation,
            "trailing_distance": trailing_distance,
            "max_duration": max_duration,
            "breakeven_activated": False,
            "highest_pnl_pct": 0.0,
        }
        self.last_entry_ts = now
        self._save_state()
        logger.info(f"Opened {side} {strategy} @ {entry_price:.2f} | SL={sl_price:.2f} TP={tp_price:.2f}")

    def close_position(self, exit_price: float, exit_reason: str):
        if not self.position:
            return None
        pos = self.position
        entry_price = pos["entry_price"]
        side = pos["side"]

        if side == "long":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        trade = {
            "strategy": pos["strategy"],
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_ts": pos["entry_ts"].isoformat() if isinstance(pos["entry_ts"], datetime) else pos["entry_ts"],
            "exit_ts": datetime.now(timezone.utc).isoformat(),
            "size_value": pos["size_value"],
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
        }

        logger.info(
            f"Closed {side} @ {exit_price:.2f} | reason={exit_reason} | pnl={pnl_pct:+.2f}%"
        )
        self.position = None
        self._save_state()
        return trade

    @property
    def has_position(self):
        return self.position is not None

    # ---- Exit Checks (mirrors backtest engine exactly) ----

    def check_breakeven(self, current_price: float) -> bool:
        """Check if we should move SL to breakeven (+0.3% threshold)."""
        if not self.position or self.position["breakeven_activated"]:
            return False
        pnl_pct = self._pnl_pct(current_price)
        if pnl_pct >= 0.3:
            self.position["breakeven_activated"] = True
            self.position["sl_price"] = self.position["entry_price"]
            self._save_state()
            logger.info(f"Breakeven activated @ {current_price:.2f} (pnl={pnl_pct:.2f}%)")
            return True
        return False

    def check_trailing_stop(self, current_price: float) -> bool:
        """Check if trailing stop should trigger. Returns True if should exit."""
        if not self.position or self.position.get("trailing_activation") is None:
            return False
        pnl_pct = self._pnl_pct(current_price)
        self.position["highest_pnl_pct"] = max(self.position["highest_pnl_pct"], pnl_pct)

        if self.position["highest_pnl_pct"] >= self.position["trailing_activation"]:
            drawback = self.position["highest_pnl_pct"] - pnl_pct
            if drawback >= self.position["trailing_distance"]:
                logger.info(
                    f"Trailing stop triggered: highest={self.position['highest_pnl_pct']:.2f}% "
                    f"current={pnl_pct:.2f}% drawback={drawback:.2f}%"
                )
                return True
        # Only save if highest_pnl_pct actually changed (avoid I/O every tick)
        if pnl_pct >= self.position["highest_pnl_pct"]:
            self._save_state()
        return False

    def check_time_exit(self) -> bool:
        """Check if max trade duration exceeded."""
        if not self.position or self.position.get("max_duration") is None:
            return False
        entry_ts = self.position["entry_ts"]
        if isinstance(entry_ts, str):
            entry_ts = datetime.fromisoformat(entry_ts)
        elapsed_min = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 60
        if elapsed_min >= self.position["max_duration"]:
            logger.info(f"Time exit triggered: {elapsed_min:.1f} min >= {self.position['max_duration']} min")
            return True
        return False

    def cooldown_ok(self, cooldown_seconds: int = 2700) -> bool:
        """Check 45-minute cooldown between entries (matching backtest)."""
        if self.last_entry_ts is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_entry_ts).total_seconds()
        return elapsed > cooldown_seconds

    def _pnl_pct(self, current_price: float) -> float:
        pos = self.position
        if pos["side"] == "long":
            return (current_price - pos["entry_price"]) / pos["entry_price"] * 100
        else:
            return (pos["entry_price"] - current_price) / pos["entry_price"] * 100
