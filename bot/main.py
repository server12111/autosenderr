import asyncio
import contextlib
import logging
import os
import sys

# Allow running as `python bot/main.py` directly
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "bot"

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import config
from .database.db import Database
from .handlers import setup_routers
from .middlewares.subscription import SubscriptionMiddleware
from .middlewares.album import AlbumMiddleware
from .userbot.manager import UserbotManager
from .services import CryptoBotService, TonPaymentService, PlategaService, AutoresponderService, MailingService, SubscriptionCheckerService


class ActivityMiddleware(BaseMiddleware):
    def __init__(self, db):
        self.db = db

    async def __call__(self, handler, event, data):
        from_user = getattr(event, "from_user", None)
        if from_user and not from_user.is_bot:
            try:
                await self.db.update_last_activity(from_user.id)
            except Exception:
                pass
        return await handler(event, data)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main():
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in .env file")
        sys.exit(1)

    if not config.CRYPTOBOT_TOKEN:
        logger.warning("CRYPTOBOT_TOKEN is not set — payment features will be unavailable")

    os.makedirs("data", exist_ok=True)
    os.makedirs("sessions", exist_ok=True)

    db = Database(config.DATABASE_PATH)
    await db.connect()
    logger.info("Database connected")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    cryptobot = CryptoBotService(config.CRYPTOBOT_TOKEN, testnet=config.CRYPTOBOT_TESTNET)

    ton_service = None
    if config.TON_WALLET_ADDRESS:
        ton_service = TonPaymentService(config.TON_WALLET_ADDRESS, config.TONCENTER_API_KEY)
        logger.info("TON payment service initialized")
    else:
        logger.info("TON_WALLET_ADDRESS not set, TON payments disabled")

    userbot_manager = UserbotManager(db, config.SESSIONS_PATH)

    autoresponder_service = AutoresponderService(db)

    mailing_service = MailingService(db, userbot_manager)

    subscription_checker = SubscriptionCheckerService(db, mailing_service)

    async def notify_user(user_id: int, text: str):
        try:
            await bot.send_message(user_id, text)
            logger.info(f"Successfully sent notification to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}", exc_info=True)

    userbot_manager.set_message_handler(autoresponder_service.handle_message)
    userbot_manager.set_group_reply_handler(autoresponder_service.handle_group_reply)
    userbot_manager.set_sponsor_check_handler(autoresponder_service.handle_sponsor_check)
    userbot_manager.set_bot_notify_callback(notify_user)

    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AlbumMiddleware())
    dp.message.middleware(ActivityMiddleware(db))
    dp.message.middleware(SubscriptionMiddleware(db))
    dp.callback_query.middleware(ActivityMiddleware(db))
    dp.callback_query.middleware(SubscriptionMiddleware(db))

    dp.include_router(setup_routers())

    platega_service = PlategaService(config.PLATEGA_MERCHANT_ID, config.PLATEGA_SECRET) if config.PLATEGA_MERCHANT_ID and config.PLATEGA_SECRET else None

    dp["db"] = db
    dp["cryptobot"] = cryptobot
    dp["ton_service"] = ton_service
    dp["platega_service"] = platega_service
    dp["userbot_manager"] = userbot_manager
    dp["mailing_service"] = mailing_service
    dp["autoresponder_service"] = autoresponder_service

    def _task_done_callback(task: asyncio.Task):
        if not task.cancelled():
            exc = task.exception()
            if exc:
                logger.critical(f"Background task '{task.get_name()}' died unexpectedly: {exc}", exc_info=exc)

    try:
        await userbot_manager.start_all_clients()
        logger.info("Userbot clients started")

        userbot_manager.start_monitor()
        if userbot_manager._monitor_task:
            userbot_manager._monitor_task.add_done_callback(_task_done_callback)
        logger.info("Account monitor started")

        await mailing_service.start()
        logger.info("Mailing service started")

        subscription_checker.start(bot)
        if subscription_checker._task:
            subscription_checker._task.add_done_callback(_task_done_callback)
        logger.info("Subscription checker started")

        logger.info("Starting bot polling...")
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.critical(f"Polling stopped unexpectedly: {e}", exc_info=True)
            raise
    finally:
        if subscription_checker._task and not subscription_checker._task.done():
            subscription_checker._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await subscription_checker._task
        if userbot_manager._monitor_task and not userbot_manager._monitor_task.done():
            userbot_manager._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await userbot_manager._monitor_task
        await mailing_service.stop()
        await userbot_manager.stop_all_clients()
        await db.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    import time
    attempt = 0
    while True:
        attempt += 1
        logger.info(f"=== Bot starting (attempt #{attempt}) ===")
        try:
            asyncio.run(main())
            logger.info("Bot exited cleanly — not restarting")
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.critical(f"Bot crashed: {e}", exc_info=True)
            logger.info("Restarting in 5 seconds...")
            time.sleep(5)
