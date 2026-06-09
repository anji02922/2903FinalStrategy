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
    # Allow env override for testnet mode (TESTNET=false for production)
    testnet_env = os.getenv("TESTNET")
    if testnet_env is not None:
        config["exchange"]["testnet"] = testnet_env.lower() in ("true", "1", "yes")
    return config
