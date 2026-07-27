import asyncio
import logging
import time
import os
from logging.handlers import RotatingFileHandler

from bot.main import main, acquire_instance_lock, release_instance_lock
from bot.utils.time_utils import moscow_logging_converter

os.makedirs("data", exist_ok=True)

_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=_fmt)
logging.Formatter.converter = staticmethod(moscow_logging_converter)

file_handler = RotatingFileHandler(
    "data/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(_fmt))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    if not acquire_instance_lock():
        logger.error("Another bot instance is already running")
        raise SystemExit(1)
    attempt = 0
    consecutive_failures = 0
    try:
        while True:
            attempt += 1
            logger.info(f"=== Bot starting (attempt #{attempt}) ===")
            try:
                asyncio.run(main())
                logger.info("Bot exited cleanly — not restarting")
                break
            except (KeyboardInterrupt, SystemExit):
                logger.info("Bot stopped by user or config error — not restarting")
                break
            except Exception as e:
                consecutive_failures += 1
                logger.critical(f"Bot crashed: {e}", exc_info=True)
                backoff = min(5 * (2 ** (consecutive_failures - 1)), 300)
                logger.info(f"Restarting in {backoff}s (crash #{consecutive_failures})...")
                time.sleep(backoff)
    finally:
        release_instance_lock()
