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

    def _ts(self):
        return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # ---- Bot lifecycle ----

    def notify_startup(self, symbol: str, leverage: int, balance: float, demo: bool):
        mode = "🧪 DEMO" if demo else "🔥 LIVE"
        bar = "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩"
        text = (
            f"{bar}\n"
            f"⚡ <b>BOT STARTED</b> ⚡\n"
            f"{bar}\n"
            f"\n"
            f"📌  Mode       │  <b>{mode}</b>\n"
            f"💎  Symbol     │  <code>{symbol}</code>\n"
            f"🔧  Leverage   │  <b>{leverage}x</b>\n"
            f"💰  Balance    │  <code>${balance:,.2f}</code>\n"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_shutdown(self):
        bar = "🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥"
        text = (
            f"{bar}\n"
            f"🛑 <b>BOT STOPPED</b> 🛑\n"
            f"{bar}\n"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_error(self, error_msg: str):
        text = (
            f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n"
            f"⚠️ <b>ERROR DETECTED</b> ⚠️\n"
            f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n"
            f"\n"
            f"<code>{str(error_msg)[:400]}</code>\n"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_warning(self, title: str, detail: str, action: str = ""):
        """Urgent warning — bot handled it but user should check exchange."""
        action_line = f"\n🔧  <b>Action:</b> {action}\n" if action else ""
        text = (
            f"🟡🟡🟡🟡🟡🟡🟡🟡🟡🟡\n"
            f"⚠️ <b>{title}</b> ⚠️\n"
            f"🟡🟡🟡🟡🟡🟡🟡🟡🟡🟡\n"
            f"\n"
            f"<code>{str(detail)[:400]}</code>\n"
            f"{action_line}\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_position_risk(self, side: str, entry: float, current: float,
                             reason: str, detail: str):
        """CRITICAL — position may need manual closure."""
        direction = "⬆️ LONG" if side == "long" else "⬇️ SHORT"
        pnl_pct = ((current - entry) / entry * 100) if side == "long" else ((entry - current) / entry * 100)
        pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
        text = (
            f"🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴\n"
            f"🚨 <b>POSITION AT RISK</b> 🚨\n"
            f"🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴\n"
            f"\n"
            f"📌  Side       │  <b>{direction}</b>\n"
            f"🏷  Entry      │  <code>${entry:,.2f}</code>\n"
            f"💲  Current    │  <code>${current:,.2f}</code>\n"
            f"{pnl_icon}  PnL        │  <code>{pnl_pct:+.2f}%</code>\n"
            f"\n"
            f"{'─' * 28}\n"
            f"❗  <b>Reason:</b> {reason}\n"
            f"📝  <b>Detail:</b> {detail}\n"
            f"{'─' * 28}\n"
            f"\n"
            f"👉 <b>CHECK EXCHANGE IMMEDIATELY</b>\n"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_status(self, uptime_min: int, balance: float = None, in_position: bool = False):
        days = uptime_min // 1440
        hours = (uptime_min % 1440) // 60
        mins = uptime_min % 60
        uptime_str = ""
        if days > 0:
            uptime_str += f"{days}d "
        uptime_str += f"{hours}h {mins}m"
        pos_icon = "🟢 In Position" if in_position else "⏳ Idle"
        bal_line = f"💰  Balance    │  <code>${balance:,.2f}</code>\n" if balance else ""
        bar = "🟦🟦🟦🟦🟦🟦🟦🟦🟦🟦"
        text = (
            f"{bar}\n"
            f"📡 <b>STATUS CHECK</b> 📡\n"
            f"{bar}\n"
            f"\n"
            f"⏱  Uptime     │  <code>{uptime_str}</code>\n"
            f"📊  State      │  {pos_icon}\n"
            f"{bal_line}"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    # ---- Trade notifications ----

    def notify_entry(self, side: str, price: float, size: float, sl: float,
                     tp: float, strategy: str, leverage: int, balance: float):
        if side == "long":
            bar = "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩"
            direction = "⬆️ LONG"
            sl_dist = f"{(price - sl) / price * 100:.2f}%"
            tp_dist = f"{(tp - price) / price * 100:.2f}%"
        else:
            bar = "🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥"
            direction = "⬇️ SHORT"
            sl_dist = f"{(sl - price) / price * 100:.2f}%"
            tp_dist = f"{(price - tp) / price * 100:.2f}%"
        notional = size * price
        risk_dist = abs(price - sl)
        reward_dist = abs(tp - price)
        rr = reward_dist / risk_dist if risk_dist > 0 else 0
        text = (
            f"{bar}\n"
            f"📈 <b>TRADE OPENED — {direction}</b>\n"
            f"{bar}\n"
            f"\n"
            f"🎯  Strategy   │  <code>{strategy}</code>\n"
            f"🏷  Entry      │  <code>${price:,.2f}</code>\n"
            f"📦  Size       │  <code>{size:.3f}</code>  ×  {leverage}x\n"
            f"💵  Notional   │  <code>${notional:,.0f}</code>\n"
            f"\n"
            f"{'─' * 28}\n"
            f"🛑  Stop Loss  │  <code>${sl:,.2f}</code>  <i>({sl_dist})</i>\n"
            f"🎯  Take Prof  │  <code>${tp:,.2f}</code>  <i>({tp_dist})</i>\n"
            f"⚖️  R : R      │  <code>1 : {rr:.1f}</code>\n"
            f"{'─' * 28}\n"
            f"\n"
            f"💰  Balance    │  <code>${balance:,.2f}</code>\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)

    def notify_exit(self, side: str, entry_price: float, exit_price: float,
                    pnl_pct: float, net_pnl: float, reason: str,
                    size: float, duration_min: float, balance: float):
        direction = "LONG" if side == "long" else "SHORT"
        if net_pnl >= 0:
            bar = "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩"
            result = "WIN ✅"
            pnl_display = f"+${net_pnl:.2f}"
            pnl_pct_display = f"+{pnl_pct:.2f}%"
        else:
            bar = "🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥"
            result = "LOSS ❌"
            pnl_display = f"-${abs(net_pnl):.2f}"
            pnl_pct_display = f"{pnl_pct:.2f}%"
        if duration_min >= 60:
            dur_str = f"{int(duration_min // 60)}h {int(duration_min % 60)}m"
        else:
            dur_str = f"{int(duration_min)}m"
        text = (
            f"{bar}\n"
            f"📊 <b>TRADE CLOSED — {result}</b>\n"
            f"{bar}\n"
            f"\n"
            f"📌  Side       │  <b>{direction}</b>\n"
            f"🏷  Entry      │  <code>${entry_price:,.2f}</code>\n"
            f"🏷  Exit       │  <code>${exit_price:,.2f}</code>\n"
            f"📦  Size       │  <code>{size:.3f}</code>\n"
            f"\n"
            f"{'─' * 28}\n"
            f"💵  <b>PnL       │  {pnl_pct_display}  •  {pnl_display}</b>\n"
            f"{'─' * 28}\n"
            f"\n"
            f"📋  Reason     │  <code>{reason}</code>\n"
            f"⏱  Duration   │  <code>{dur_str}</code>\n"
            f"💰  Balance    │  <code>${balance:,.2f}</code>\n"
            f"\n"
            f"<i>🕐  {self._ts()}</i>"
        )
        return self._send(text)


