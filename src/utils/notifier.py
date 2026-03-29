"""Telegram notification module for the scalping bot."""

import json
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone
from loguru import logger


class TelegramNotifier:
    """Send formatted notifications via Telegram Bot API."""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds

    def __init__(self, config: dict):
        tg = config.get("telegram", {})
        self.enabled = bool(tg.get("bot_token")) and bool(tg.get("chat_id"))
        self.bot_token = tg.get("bot_token", "")
        self.chat_id = tg.get("chat_id", "")
        self.url = self.BASE_URL.format(token=self.bot_token)

        if not self.enabled:
            logger.warning("Telegram notifications disabled — missing bot_token or chat_id")

    # ---- Core send with retries ----

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message via Telegram Bot API with retry logic."""
        if not self.enabled:
            return False

        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }).encode("utf-8")

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    self.url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        body = resp.read().decode()
                        logger.warning(f"Telegram API returned {resp.status}: {body}")
            except urllib.error.HTTPError as e:
                body = e.read().decode() if e.fp else ""
                logger.warning(f"Telegram send attempt {attempt}/{self.MAX_RETRIES} failed: HTTP {e.code} — {body}")
            except Exception as e:
                logger.warning(f"Telegram send attempt {attempt}/{self.MAX_RETRIES} failed: {e}")

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY * attempt)

        logger.error("Telegram notification failed after all retries")
        return False

    # ---- Bot lifecycle and trade notifications ----

    def notify_startup(self, symbol: str, leverage: int, balance: float, demo: bool):
        mode = "DEMO" if demo else "LIVE"
        text = (
            f"🟢 <b>Bot Started ({mode})</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Leverage: {leverage}x\n"
            f"Balance: <code>${balance:.2f}</code>\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self._send(text)

    def notify_shutdown(self):
        text = f"🔴 <b>Bot Stopped</b>\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return self._send(text)

    def notify_error(self, error_msg: str):
        text = (
            f"⚠️ <b>Bot Error</b>\n"
            f"<code>{str(error_msg)[:500]}</code>\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self._send(text)

    def notify_status(self, uptime_min: int):
        hours = uptime_min // 60
        mins = uptime_min % 60
        text = (
            f"🟢 <b>Bot Running</b>\n"
            f"Uptime: <code>{hours}h {mins}m</code>\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self._send(text)

    def notify_entry(self, side: str, price: float, size: float, sl: float,
                     tp: float, strategy: str, leverage: int, balance: float):
        arrow = "🟢" if side == "long" else "🔴"
        direction = "LONG" if side == "long" else "SHORT"
        notional = size * price
        risk_dist = abs(price - sl)
        reward_dist = abs(tp - price)
        rr = reward_dist / risk_dist if risk_dist > 0 else 0
        text = (
            f"{arrow} <b>Trade Opened — {direction}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Strategy: <code>{strategy}</code>\n"
            f"Entry Price: <code>${price:.2f}</code>\n"
            f"Size: <code>{size:.3f}</code> contracts\n"
            f"Notional: <code>${notional:,.0f}</code> ({leverage}x)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Stop Loss: <code>${sl:.2f}</code>\n"
            f"Take Profit: <code>${tp:.2f}</code>\n"
            f"Risk:Reward: <code>1:{rr:.1f}</code>\n"
            f"Balance: <code>${balance:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self._send(text)

    def notify_exit(self, side: str, entry_price: float, exit_price: float,
                    pnl_pct: float, net_pnl: float, reason: str,
                    size: float, duration_min: float, balance: float):
        direction = "LONG" if side == "long" else "SHORT"
        if net_pnl >= 0:
            emoji = "✅"
            result = "PROFIT"
        else:
            emoji = "❌"
            result = "LOSS"
        text = (
            f"{emoji} <b>Trade Closed — {result}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Side: {direction}\n"
            f"Entry: <code>${entry_price:.2f}</code>\n"
            f"Exit: <code>${exit_price:.2f}</code>\n"
            f"Size: <code>{size:.3f}</code> contracts\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"PnL: <code>{pnl_pct:+.2f}%</code>  |  <code>${net_pnl:+.2f}</code>\n"
            f"Exit Reason: <code>{reason}</code>\n"
            f"Duration: <code>{duration_min:.0f} min</code>\n"
            f"Balance: <code>${balance:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self._send(text)
        return self._send(text)

    def send_message(self, text: str) -> bool:
        """Send a raw text message (for testing/ad-hoc)."""
        return self._send(text)
