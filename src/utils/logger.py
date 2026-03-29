import os
import sys
from loguru import logger


def setup_logger(level="INFO", log_file="logs/bot.log", console=True):
    logger.remove()
    fmt = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    if console:
        logger.add(sys.stderr, format=fmt, level=level, colorize=True)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        logger.add(log_file, format=fmt, level=level, rotation="10 MB", retention="7 days")
    return logger
