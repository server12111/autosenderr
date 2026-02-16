import asyncio
import logging
from typing import Optional, Callable, Any

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.events import NewMessage

from ..database.db import Database, Account

logger = logging.getLogger(__name__)


class UserbotManager:
    def __init__(self, db: Database, sessions_path: str = "sessions"):
        self.db = db
        self.sessions_path = sessions_path
        self._clients: dict[int, TelegramClient] = {}
        self._message_handler: Optional[Callable] = None
        self._bot_notify_callback: Optional[Callable] = None

    def set_message_handler(self, handler: Callable):
        """Set handler for incoming messages (autoresponder)."""
        self._message_handler = handler

    def set_bot_notify_callback(self, callback: Callable):
        """Set callback to notify bot owner about new messages."""
        self._bot_notify_callback = callback

    async def start_client(self, account: Account) -> Optional[TelegramClient]:
        """Start a Telethon client for an account."""
        if account.id in self._clients:
            client = self._clients[account.id]
            if client.is_connected():
                return client

        try:
            client = TelegramClient(
                StringSession(account.session_string),
                account.api_id,
                account.api_hash,
            )

            await client.connect()

            if not await client.is_user_authorized():
                logger.warning(f"Account {account.phone} is not authorized")
                return None

            self._clients[account.id] = client

            account_id = account.id

            @client.on(NewMessage(incoming=True))
            async def handler(event):
                if self._message_handler:
                    try:
                        fresh_account = await self.db.get_account(account_id)
                        if fresh_account:
                            await self._message_handler(event, fresh_account, self._bot_notify_callback)
                        else:
                            logger.warning(f"Account {account_id} not found when handling message")
                    except Exception as e:
                        logger.error(f"Error in message handler for account {account_id}: {e}", exc_info=True)

            logger.info(f"Started client for account {account.phone} (ID: {account.id})")
            return client

        except Exception as e:
            logger.error(f"Error starting client for {account.phone}: {e}")
            return None

    async def stop_client(self, account_id: int):
        """Stop a Telethon client (disconnect without logging out to preserve session)."""
        if account_id in self._clients:
            client = self._clients[account_id]
            await client.disconnect()
            del self._clients[account_id]
            logger.info(f"Stopped client for account {account_id}")

    async def logout_and_stop(self, account):
        """Log out and stop a client, creating a temp client if needed."""
        if account.id in self._clients:
            client = self._clients[account.id]
            try:
                await client.log_out()
            except Exception:
                pass
            await client.disconnect()
            del self._clients[account.id]
            logger.info(f"Logged out and stopped client for account {account.id}")
        else:
            try:
                client = TelegramClient(
                    StringSession(account.session_string),
                    account.api_id,
                    account.api_hash,
                )
                await client.connect()
                await client.log_out()
                await client.disconnect()
                logger.info(f"Logged out temp client for account {account.id}")
            except Exception as e:
                logger.warning(f"Failed to log out account {account.id}: {e}")

    async def get_client(self, account_id: int) -> Optional[TelegramClient]:
        """Get an active client for an account."""
        if account_id in self._clients:
            client = self._clients[account_id]
            if client.is_connected():
                return client

        account = await self.db.get_account(account_id)
        if account and account.is_active:
            return await self.start_client(account)

        return None

    async def start_all_clients(self):
        """Start clients for all active accounts."""
        accounts = await self.db.get_all_active_accounts()
        for account in accounts:
            await self.start_client(account)

    async def stop_all_clients(self):
        """Stop all active clients."""
        for account_id in list(self._clients.keys()):
            await self.stop_client(account_id)

    async def create_new_session(
        self,
        phone: str,
        api_id: int,
        api_hash: str,
        code_callback: Callable[[], Any],
        password_callback: Optional[Callable[[], Any]] = None,
    ) -> Optional[str]:
        """Create a new session by logging in with phone code."""
        client = TelegramClient(StringSession(), api_id, api_hash)

        try:
            await client.connect()

            await client.sign_in(phone)

            code = await code_callback()
            try:
                await client.sign_in(phone, code)
            except Exception as e:
                if "Two-step" in str(e) or "password" in str(e).lower():
                    if password_callback:
                        password = await password_callback()
                        await client.sign_in(password=password)
                    else:
                        raise ValueError("2FA required but no password callback provided")
                else:
                    raise

            session_string = client.session.save()
            return session_string

        except Exception as e:
            logger.error(f"Error creating session for {phone}: {e}")
            raise
        finally:
            await client.disconnect()

    async def send_message(
        self, account_id: int, chat_identifier: str, text: str
    ) -> bool:
        """Send a message using an account."""
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
        """Check if client is active and connected."""
        return (
            account_id in self._clients
            and self._clients[account_id].is_connected()
        )
