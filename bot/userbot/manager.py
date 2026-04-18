import asyncio
import logging
from typing import Optional, Callable, Any
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.events import NewMessage
from telethon.errors import (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)

from ..database.db import Database, Account


_DEVICE_POOL = [
    {"device_model": "Samsung Galaxy A54",   "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "Xiaomi Redmi Note 12", "system_version": "Android 12", "app_version": "10.13.2"},
    {"device_model": "Samsung Galaxy S23",   "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "OPPO A78",             "system_version": "Android 12", "app_version": "10.12.4"},
    {"device_model": "Xiaomi 13T",           "system_version": "Android 13", "app_version": "10.14.5"},
]


def _parse_proxy(proxy_str: Optional[str]) -> Optional[tuple]:
    """Parse 'socks5://[user:pass@]host:port' into Telethon proxy tuple."""
    if not proxy_str:
        return None
    try:
        import socks
        p = urlparse(proxy_str)
        host = p.hostname
        port = p.port
        username = p.username or None
        password = p.password or None
        if not host or not port:
            return None
        if username and password:
            return (socks.SOCKS5, host, port, True, username, password)
        return (socks.SOCKS5, host, port)
    except Exception as e:
        logger.warning(f"Failed to parse proxy '{proxy_str}': {e}")
        return None

logger = logging.getLogger(__name__)

# Exceptions that mean the account is banned/deactivated/session killed
_BAN_ERRORS = (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)

_MONITOR_INTERVAL = 600  # seconds between health checks


class UserbotManager:
    def __init__(self, db: Database, sessions_path: str = "sessions"):
        self.db = db
        self.sessions_path = sessions_path
        self._clients: dict[int, TelegramClient] = {}
        self._me_ids: dict[int, int] = {}  # account_id -> telegram user id
        self._message_handler: Optional[Callable] = None
        self._group_reply_handler: Optional[Callable] = None
        self._bot_notify_callback: Optional[Callable] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._sponsor_check_handler: Optional[Callable] = None

    def set_message_handler(self, handler: Callable):
        self._message_handler = handler

    def set_group_reply_handler(self, handler: Callable):
        self._group_reply_handler = handler

    def set_bot_notify_callback(self, callback: Callable):
        self._bot_notify_callback = callback

    def set_sponsor_check_handler(self, handler: Callable):
        self._sponsor_check_handler = handler

    async def start_client(self, account: Account) -> Optional[TelegramClient]:
        if account.id in self._clients:
            client = self._clients[account.id]
            if client.is_connected():
                return client

        try:
            proxy = _parse_proxy(account.proxy)
            device = _DEVICE_POOL[account.id % len(_DEVICE_POOL)]
            client = TelegramClient(
                StringSession(account.session_string),
                account.api_id,
                account.api_hash,
                proxy=proxy,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code="uk",
                system_lang_code="uk-UA",
            )
            await client.connect()

            if not await client.is_user_authorized():
                logger.warning(f"Account {account.phone} is not authorized — deactivating")
                await client.disconnect()
                await asyncio.sleep(0)
                await self.db.deactivate_account(account.id)
                return None

            me = await client.get_me()
            self._me_ids[account.id] = me.id
            self._clients[account.id] = client

            fresh_session = client.session.save()
            if fresh_session != account.session_string:
                await self.db.update_account_session(account.id, fresh_session)

            account_id = account.id
            me_id = me.id

            @client.on(NewMessage(incoming=True))
            async def handler(event):
                try:
                    fresh_account = await self.db.get_account(account_id)
                    if not fresh_account:
                        return

                    # Handle private message autoresponder
                    if self._message_handler and event.is_private:
                        await self._message_handler(event, fresh_account, self._bot_notify_callback)

                    # Handle group reply autoresponder
                    if self._group_reply_handler and not event.is_private and event.reply_to:
                        await self._group_reply_handler(event, fresh_account, me_id, self._bot_notify_callback)

                    # Auto-subscribe to sponsor channels
                    if self._sponsor_check_handler and not event.is_private:
                        await self._sponsor_check_handler(event, fresh_account)

                except Exception as e:
                    logger.error(f"Error in message handler for account {account_id}: {e}", exc_info=True)

            logger.info(f"Started client for account {account.phone} (ID: {account.id})")
            return client

        except _BAN_ERRORS as e:
            await self._handle_account_problem(account.id, e)
            try:
                await client.disconnect()
                await asyncio.sleep(0)
            except Exception:
                pass
            return None
        except Exception as e:
            logger.error(f"Error starting client for {account.phone}: {e}")
            try:
                await client.disconnect()
                await asyncio.sleep(0)
            except Exception:
                pass
            return None

    async def stop_client(self, account_id: int):
        if account_id in self._clients:
            client = self._clients[account_id]
            await client.disconnect()
            del self._clients[account_id]
            self._me_ids.pop(account_id, None)
            logger.info(f"Stopped client for account {account_id}")

    async def logout_and_stop(self, account):
        if account.id in self._clients:
            client = self._clients[account.id]
            try:
                await client.log_out()
            except Exception:
                pass
            await client.disconnect()
            del self._clients[account.id]
            self._me_ids.pop(account.id, None)
        else:
            try:
                proxy = _parse_proxy(account.proxy)
                client = TelegramClient(
                    StringSession(account.session_string), account.api_id, account.api_hash,
                    proxy=proxy,
                )
                await client.connect()
                await client.log_out()
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Failed to log out account {account.id}: {e}")

    async def get_client(self, account_id: int) -> Optional[TelegramClient]:
        if account_id in self._clients:
            client = self._clients[account_id]
            if client.is_connected():
                return client
        account = await self.db.get_account(account_id)
        if account and account.is_active:
            return await self.start_client(account)
        return None

    def start_monitor(self):
        """Start background health-check loop for all connected accounts."""
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Account monitor started")

    async def _monitor_loop(self):
        while True:
            await asyncio.sleep(_MONITOR_INTERVAL)
            for account_id in list(self._clients.keys()):
                try:
                    client = self._clients[account_id]
                    if not client.is_connected():
                        try:
                            await client.connect()
                        except _BAN_ERRORS as e:
                            await self._handle_account_problem(account_id, e)
                            continue
                    await client.get_me()
                    try:
                        fresh_session = client.session.save()
                        account = await self.db.get_account(account_id)
                        if account and fresh_session != account.session_string:
                            await self.db.update_account_session(account_id, fresh_session)
                    except Exception:
                        pass
                except _BAN_ERRORS as e:
                    await self._handle_account_problem(account_id, e)
                except Exception as e:
                    logger.warning(f"Monitor check failed for account {account_id}: {e}")

    async def _handle_account_problem(self, account_id: int, error: Exception):
        """Mark account inactive and notify owner."""
        error_name = type(error).__name__

        if isinstance(error, (UserDeactivatedBanError,)):
            reason = "⛔️ аккаунт заблокирован Telegram"
        elif isinstance(error, UserDeactivatedError):
            reason = "❌ аккаунт деактивирован"
        elif isinstance(error, (AuthKeyUnregisteredError, SessionRevokedError, AuthKeyDuplicatedError)):
            reason = "🔑 сессия сброшена (аккаунт вышел или заморожен)"
        else:
            reason = f"неизвестная ошибка: {error_name}"

        logger.warning(f"Account {account_id} problem detected: {reason}")

        # Mark inactive in DB
        try:
            await self.db.deactivate_account(account_id)
        except Exception as e:
            logger.error(f"Failed to deactivate account {account_id}: {e}")

        # Remove from active clients
        self._clients.pop(account_id, None)
        self._me_ids.pop(account_id, None)

        # Notify owner
        if self._bot_notify_callback:
            try:
                account = await self.db.get_account(account_id)
                if account:
                    user = await self.db.get_user_by_id(account.user_id)
                    if user:
                        await self._bot_notify_callback(
                            user.telegram_id,
                            f"⚠️ <b>Проблема с аккаунтом!</b>\n\n"
                            f"📱 Аккаунт: <b>{account.display_name}</b>\n"
                            f"❗️ Причина: {reason}\n\n"
                            f"Аккаунт отключён. Добавьте его заново в разделе «Аккаунты».",
                        )
            except Exception as e:
                logger.error(f"Failed to notify about account {account_id} problem: {e}")

    async def start_all_clients(self):
        accounts = await self.db.get_all_active_accounts()
        if not accounts:
            logger.info("No active accounts to start")
            return

        semaphore = asyncio.Semaphore(5)

        async def _start(account):
            async with semaphore:
                return await self.start_client(account)

        results = await asyncio.gather(*[_start(a) for a in accounts], return_exceptions=True)
        started = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        logger.info(f"Startup complete: {started}/{len(accounts)} accounts active")

    async def stop_all_clients(self):
        for account_id in list(self._clients.keys()):
            await self.stop_client(account_id)

    async def send_message(self, account_id: int, chat_identifier: str, text: str) -> bool:
        client = await self.get_client(account_id)
        if not client:
            return False
        try:
            await client.send_message(chat_identifier, text)
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def is_client_active(self, account_id: int) -> bool:
        return account_id in self._clients and self._clients[account_id].is_connected()
