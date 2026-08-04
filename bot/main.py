import asyncio
import contextlib
import logging
import os
import sys
import time

# Allow running as `python bot/main.py` directly
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "bot"

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage

from .config import config
from .database.db import Database
from .handlers import setup_routers
from .middlewares.subscription import SubscriptionMiddleware
from .middlewares.album import AlbumMiddleware
from .userbot.manager import UserbotManager
from .services import CryptoBotService, TonPaymentService, PlategaService, AutoresponderService, MailingService, SubscriptionCheckerService, InactivityCleanupService
from .utils.time_utils import moscow_logging_converter


class ActivityMiddleware(BaseMiddleware):
    def __init__(self, db):
        self.db = db
        self._last_update: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        from_user = getattr(event, "from_user", None)
        if from_user and not from_user.is_bot:
            try:
                now = time.monotonic()
                if now - self._last_update.get(from_user.id, 0) >= 60:
                    await self.db.update_last_activity(from_user.id)
                    self._last_update[from_user.id] = now
                    if len(self._last_update) > 10000:
                        cutoff = now - 60
                        self._last_update = {
                            user_id: timestamp
                            for user_id, timestamp in self._last_update.items()
                            if timestamp >= cutoff
                        }
            except Exception:
                pass
        return await handler(event, data)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.Formatter.converter = staticmethod(moscow_logging_converter)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
_instance_lock_file = None


def acquire_instance_lock() -> bool:
    """Prevent two bot processes from handling the same updates."""
    global _instance_lock_file
    if _instance_lock_file is not None:
        return True

    lock_path = os.path.abspath(f"{config.DATABASE_PATH}.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_file = open(lock_path, "a+b")
    lock_file.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            if os.path.getsize(lock_path) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return False

    _instance_lock_file = lock_file
    return True


def release_instance_lock():
    global _instance_lock_file
    lock_file = _instance_lock_file
    if lock_file is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
        _instance_lock_file = None


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

    backfilled = await db.backfill_missing_proxies()
    if backfilled:
        logger.info(f"Backfilled pool/shared proxies onto {backfilled} existing account(s)")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    cryptobot = CryptoBotService(config.CRYPTOBOT_TOKEN, testnet=config.CRYPTOBOT_TESTNET)

    ton_service = None
    if config.TON_WALLET_ADDRESS:
        ton_service = TonPaymentService(config.TON_WALLET_ADDRESS, config.TONCENTER_API_KEY)
        logger.info("GRAM(TON) payment service initialized")
    else:
        logger.info("TON_WALLET_ADDRESS not set, GRAM(TON) payments disabled")

    userbot_manager = UserbotManager(db, config.SESSIONS_PATH)

    autoresponder_service = AutoresponderService(db)

    mailing_service = MailingService(db, userbot_manager)

    subscription_checker = SubscriptionCheckerService(db, mailing_service)

    inactivity_cleanup = InactivityCleanupService(db, userbot_manager)

    notify_locks: dict[int, asyncio.Lock] = {}

    async def notify_user(user_id: int, text: str):
        # Users with several accounts can have that many concurrent mailing
        # loops firing notifications (FloodWait, bans, proxy issues, ...) at
        # the same user chat_id around the same time. Telegram's per-chat
        # outgoing rate limit then rejects the extras with TelegramRetryAfter,
        # which callers swallow silently — serialize + retry so notifications
        # for users with more accounts don't get dropped more often.
        lock = notify_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            for attempt in range(3):
                try:
                    await bot.send_message(user_id, text)
                    logger.info(f"Successfully sent notification to user {user_id}")
                    return
                except TelegramRetryAfter as e:
                    if attempt == 2:
                        logger.error(f"Failed to notify user {user_id} after retries: {e}")
                        raise
                    await asyncio.sleep(e.retry_after + 0.5)
                except Exception as e:
                    logger.error(f"Failed to notify user {user_id}: {e}", exc_info=True)
                    raise

    userbot_manager.set_message_handler(autoresponder_service.handle_message)
    userbot_manager.set_group_reply_handler(autoresponder_service.handle_group_reply)
    userbot_manager.set_sponsor_check_handler(autoresponder_service.handle_sponsor_check)
    userbot_manager.set_bot_notify_callback(notify_user)

    dp = Dispatcher(storage=MemoryStorage())

    @dp.errors()
    async def _on_error(event):
        logger.error(f"Unhandled error while processing update: {event.exception}", exc_info=event.exception)
        callback_query = getattr(event.update, "callback_query", None)
        if callback_query:
            try:
                await callback_query.answer("⚠️ Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
            except Exception:
                pass
        return True

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
                logger.critical(
                    f"Background task '{task.get_name()}' died unexpectedly: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

    try:
        # Start account connections in background — bot responds immediately
        await userbot_manager.start_all_clients(background=True)
        logger.info("Userbot clients starting in background...")

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

        inactivity_cleanup.start()
        if inactivity_cleanup._task:
            inactivity_cleanup._task.add_done_callback(_task_done_callback)
        logger.info("Inactivity cleanup service started")

        logger.info("Starting bot polling...")
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.critical(f"Polling stopped unexpectedly: {e}", exc_info=True)
            raise
    finally:
        with contextlib.suppress(Exception):
            if subscription_checker._task and not subscription_checker._task.done():
                subscription_checker._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await subscription_checker._task
        with contextlib.suppress(Exception):
            if inactivity_cleanup._task and not inactivity_cleanup._task.done():
                inactivity_cleanup._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inactivity_cleanup._task
        with contextlib.suppress(Exception):
            if userbot_manager._monitor_task and not userbot_manager._monitor_task.done():
                userbot_manager._monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await userbot_manager._monitor_task
        with contextlib.suppress(Exception):
            await mailing_service.stop()
        with contextlib.suppress(Exception):
            await userbot_manager.stop_all_clients()
        with contextlib.suppress(Exception):
            await db.close()
        with contextlib.suppress(Exception):
            await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    if not acquire_instance_lock():
        logger.error("Another bot instance is already running")
        sys.exit(1)
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
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                consecutive_failures += 1
                logger.critical(f"Bot crashed: {e}", exc_info=True)
                backoff = min(5 * (2 ** (consecutive_failures - 1)), 300)
                logger.info(f"Restarting in {backoff}s (crash #{consecutive_failures})...")
                time.sleep(backoff)
    finally:
        release_instance_lock()
