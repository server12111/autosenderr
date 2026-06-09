import asyncio
import logging
import time
import os
from logging.handlers import RotatingFileHandler

from bot.main import main

os.makedirs("data", exist_ok=True)

_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=_fmt)

file_handler = RotatingFileHandler(
    "data/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(_fmt))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    attempt = 0
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
            logger.critical(f"Bot crashed: {e}", exc_info=True)
            logger.info("Restarting in 5 seconds...")
            time.sleep(5)
