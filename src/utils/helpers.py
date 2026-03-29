import yaml
import os
from dotenv import load_dotenv

load_dotenv()


def load_config(path="config/config.yaml"):
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    # Override with environment variables if set
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if api_key and api_key != "your_api_key_here":
        config["exchange"]["api_key"] = api_key
        config["exchange"]["api_secret"] = api_secret
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token:
        config["telegram"]["bot_token"] = tg_token
        config["telegram"]["chat_id"] = tg_chat
    return config


def timeframe_to_minutes(tf: str) -> int:
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    return mapping.get(tf, 1)


def pct_change(entry_price: float, exit_price: float, side: str) -> float:
    if side == "long":
        return ((exit_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - exit_price) / entry_price) * 100
