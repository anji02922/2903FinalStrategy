"""Quick test: send a Telegram message to verify bot token + chat ID."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.helpers import load_config
from src.utils.notifier import TelegramNotifier

config = load_config()
notifier = TelegramNotifier(config)

print(f"Enabled: {notifier.enabled}")
print(f"Chat ID: {notifier.chat_id}")
print(f"Token: {notifier.bot_token[:10]}...")

ok = notifier.send_message("🤖 <b>Scalping Bot Test</b>\n\nTelegram notifications are working!")
print(f"Send result: {'SUCCESS' if ok else 'FAILED'}")
