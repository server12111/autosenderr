import asyncio
import logging
import json
import os
import random
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

from telethon.errors import (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
    FloodWaitError,
)

_BAN_ERRORS = (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)

import aiohttp
import certifi

from .database.db import Database
from .utils.time_utils import is_within_active_hours

logger = logging.getLogger(__name__)


@dataclass
class Invoice:
    invoice_id: str
    amount: float
    currency: str
    pay_url: str
    status: str


@dataclass
class CryptoBotError:
    code: str
    name: str
    message: str


class CryptoBotService:
    def __init__(self, token: str, testnet: bool = False):
        self.token = token
        self.testnet = testnet
        self.base_url = "https://testnet-pay.crypt.bot/api" if testnet else "https://pay.crypt.bot/api"
        self.headers = {"Crypto-Pay-API-Token": token}
        self.last_error: Optional[CryptoBotError] = None

    async def create_invoice(self, amount: float, currency: str = "USDT",
                              description: str = "", expires_in: int = 3600) -> Optional[Invoice]:
        self.last_error = None
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                payload = {"asset": currency, "amount": str(amount),
                           "description": description, "expires_in": expires_in}
                async with session.post(f"{self.base_url}/createInvoice",
                                        headers=self.headers, json=payload) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        error = data.get("error", {})
                        self.last_error = CryptoBotError(
                            code=str(error.get("code", "unknown")),
                            name=error.get("name", "unknown"),
                            message=self._get_error_message(error.get("name", "")),
                        )
                        return None
                    r = data["result"]
                    return Invoice(invoice_id=str(r["invoice_id"]), amount=float(r["amount"]),
                                   currency=r["asset"], pay_url=r["pay_url"], status=r["status"])
        except Exception as e:
            logger.error(f"Error creating invoice: {e}")
            self.last_error = CryptoBotError("network_error", "NetworkError", f"Ошибка соединения: {e}")
            return None

    def _get_error_message(self, error_name: str) -> str:
        messages = {
            "UNAUTHORIZED": "Неверный API токен CryptoBot",
            "API_TOKEN_INVALID": "Неверный API токен CryptoBot",
            "INVALID_AMOUNT": "Неверная сумма платежа",
            "INVALID_ASSET": "Неверная валюта",
        }
        return messages.get(error_name, f"Ошибка CryptoBot: {error_name}")

    async def check_invoice_paid(self, invoice_id: str) -> bool:
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(f"{self.base_url}/getInvoices",
                                        headers=self.headers,
                                        json={"invoice_ids": invoice_id}) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        return False
                    items = data.get("result", {}).get("items", [])
                    return items[0].get("status") == "paid" if items else False
        except Exception as e:
            logger.error(f"Error checking invoice: {e}")
            return False


class TonPaymentService:
    def __init__(self, wallet_address: str, api_key: str = ""):
        self.wallet_address = wallet_address
        self.api_key = api_key
        self.base_url = "https://toncenter.com/api/v2"
        self._cached_price: Optional[float] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 60

    async def get_ton_price_usdt(self) -> Optional[float]:
        import time as _time
        now = _time.time()
        if self._cached_price and (now - self._cache_time) < self._cache_ttl:
            return self._cached_price
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "the-open-network", "vs_currencies": "usd"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    price = data.get("the-open-network", {}).get("usd")
                    if price and price > 0:
                        self._cached_price = float(price)
                        self._cache_time = now
                        return self._cached_price
        except Exception as e:
            logger.error(f"CoinGecko error: {e}")
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": "TONUSDT"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    price = data.get("price")
                    if price:
                        self._cached_price = float(price)
                        self._cache_time = now
                        return self._cached_price
        except Exception as e:
            logger.error(f"Binance error: {e}")
        return self._cached_price

    async def calculate_ton_amount(self, usdt_amount: float) -> Optional[float]:
        price = await self.get_ton_price_usdt()
        if not price:
            return None
        return round(usdt_amount / price, 4)

    def generate_payment_link(self, amount_ton: float, comment: str) -> str:
        from urllib.parse import quote
        nanotons = int(amount_ton * 1_000_000_000)
        return f"https://app.tonkeeper.com/transfer/{self.wallet_address}?amount={nanotons}&text={quote(comment)}"

    async def check_payment(self, amount_ton: float, comment: str) -> bool:
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            params = {"address": self.wallet_address, "limit": 30}
            if self.api_key:
                params["api_key"] = self.api_key
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{self.base_url}/getTransactions", params=params) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        return False
                    expected = int(amount_ton * 1_000_000_000)
                    for tx in data.get("result", []):
                        in_msg = tx.get("in_msg", {})
                        if in_msg.get("message") != comment:
                            continue
                        if int(in_msg.get("value", "0")) >= int(expected * 0.95):
                            return True
                    return False
        except Exception as e:
            logger.error(f"Error checking TON payment: {e}")
            return False


class AutoresponderService:
    def __init__(self, db: Database):
        self.db = db

    async def handle_message(self, event, account, notify_callback: Optional[Callable] = None):
        """Handle incoming private message for autoresponder."""
        if not event.is_private:
            return

        sender = await event.get_sender()
        if not sender or getattr(sender, 'bot', False):
            return

        sender_id = sender.id

        if account.notify_messages and notify_callback:
            sender_name = (f"{getattr(sender,'first_name','') or ''} {getattr(sender,'last_name','') or ''}").strip() or "Без имени"
            sender_username = f"@{sender.username}" if getattr(sender, 'username', None) else "не указан"
            msg_text = event.text or "(медиа/стикер)"
            if len(msg_text) > 200:
                msg_text = msg_text[:200] + "..."
            notification = (
                f"📥 Сообщение от:\n"
                f"👤 {sender_name}\n"
                f"🔗 {sender_username}\n"
                f"🆔 {sender_id}\n\n"
                f"💬 {msg_text}\n\n"
                f"📱 Аккаунт: {account.display_name}"
            )
            try:
                user = await self.db.get_user_by_id(account.user_id)
                if user:
                    await notify_callback(user.telegram_id, notification)
            except Exception as e:
                logger.error(f"Notification error: {e}")

        if not account.autoresponder_enabled or not account.autoresponder_text:
            return

        already = await self.db.autoresponder_history_exists(account.id, sender_id)
        if already:
            return

        try:
            if account.autoresponder_photo and os.path.exists(account.autoresponder_photo):
                await event.client.send_file(
                    sender_id, account.autoresponder_photo,
                    caption=account.autoresponder_text or None
                )
            else:
                await event.respond(account.autoresponder_text)
            await self.db.add_autoresponder_history(account.id, sender_id, event.text)
            logger.info(f"Autoresponder sent to {sender_id} from {account.phone}")
        except Exception as e:
            logger.error(f"Autoresponder error: {e}")

    async def handle_group_reply(self, event, account, me_id: int, notify_callback: Optional[Callable] = None):
        """Handle group message that is a reply to this account's message."""
        if event.is_private:
            return

        if not event.reply_to:
            return

        if not account.group_autoresponder_enabled or not account.group_autoresponder_text:
            return

        try:
            original = await event.get_reply_message()
            if not original:
                return
            orig_sender = await original.get_sender()
            if not orig_sender or orig_sender.id != me_id:
                return

            sender = await event.get_sender()
            if not sender or getattr(sender, 'bot', False):
                return

            sender_id = sender.id
            if account.group_autoresponder_photo and os.path.exists(account.group_autoresponder_photo):
                await event.client.send_file(
                    event.chat_id, account.group_autoresponder_photo,
                    caption=account.group_autoresponder_text or None,
                    reply_to=event.id
                )
            else:
                await event.reply(account.group_autoresponder_text)
            logger.info(f"Group autoresponder replied to {sender_id} from {account.phone}")
        except Exception as e:
            logger.error(f"Group autoresponder error: {e}")


class MailingService:
    def __init__(self, db: Database, userbot_manager):
        self.db = db
        self.userbot_manager = userbot_manager
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False

    async def start(self):
        self._running = True
        mailings = await self.db.get_active_mailings()
        for m in mailings:
            await self._start_mailing_task(m.id)
        logger.info(f"Mailing service started with {len(mailings)} active mailings")

    async def stop(self):
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    async def start_mailing(self, mailing_id: int) -> bool:
        mailing = await self.db.get_mailing(mailing_id)
        if not mailing:
            return False
        account = await self.db.get_account(mailing.account_id)
        if not account or not account.is_active:
            return False
        client = await self.userbot_manager.get_client(mailing.account_id)
        if not client:
            return False
        await self.db.update_mailing_status(mailing_id, True)
        await self._start_mailing_task(mailing_id)
        return True

    async def stop_mailing(self, mailing_id: int):
        await self.db.update_mailing_status(mailing_id, False)
        if mailing_id in self._tasks:
            self._tasks[mailing_id].cancel()
            del self._tasks[mailing_id]

    async def stop_user_mailings(self, user_id: int):
        """Stop all active mailings for a user (called when subscription expires)."""
        mailings = await self.db.get_user_active_mailings(user_id)
        for m in mailings:
            await self.stop_mailing(m.id)
        return len(mailings)

    async def delete_mailing(self, mailing_id: int):
        await self.stop_mailing(mailing_id)
        await self.db.delete_mailing(mailing_id)

    async def _start_mailing_task(self, mailing_id: int):
        if mailing_id in self._tasks:
            self._tasks[mailing_id].cancel()
        task = asyncio.create_task(self._mailing_loop(mailing_id))
        self._tasks[mailing_id] = task

    async def _mailing_loop(self, mailing_id: int):
        logger.info(f"Starting mailing loop for mailing {mailing_id}")
        try:
            while self._running:
                try:
                    mailing = await self.db.get_mailing(mailing_id)
                    if not mailing or not mailing.is_active:
                        break

                    if not self._is_active_hours(mailing.active_hours_json):
                        await asyncio.sleep(60)
                        continue

                    messages = await self.db.get_mailing_messages(mailing_id)
                    targets = await self.db.get_mailing_targets(mailing_id)

                    if not messages or not targets:
                        await asyncio.sleep(60)
                        continue

                    client = await self.userbot_manager.get_client(mailing.account_id)
                    if not client:
                        await asyncio.sleep(60)
                        continue

                    if not client.is_connected():
                        try:
                            await client.connect()
                        except _BAN_ERRORS as e:
                            await self._handle_mailing_ban(mailing_id, mailing.account_id, e)
                            return
                        except Exception as e:
                            logger.error(f"Reconnect failed for mailing {mailing_id}: {e}")
                            await asyncio.sleep(60)
                            continue

                    msg = random.choice(messages)

                    # Determine parse_mode for Telethon
                    pm = msg.parse_mode or 'html'
                    if pm == 'plain':
                        pm = None

                    for target_obj in targets:
                        target = target_obj.chat_identifier
                        if not target.startswith('-') and not target.startswith('@') and not target.isdigit():
                            target = f"@{target}"

                        try:
                            photos = [p for p in msg.photo_paths if os.path.exists(p)]
                            if len(photos) > 1:
                                await client.send_file(target, photos, caption=msg.text or None)
                            elif len(photos) == 1:
                                await client.send_file(target, photos[0], caption=msg.text or None)
                            else:
                                await client.send_message(target, msg.text, parse_mode=pm)
                            logger.info(f"Mailing {mailing_id} sent to {target}")
                        except _BAN_ERRORS as e:
                            await self._handle_mailing_ban(mailing_id, mailing.account_id, e)
                            return
                        except FloodWaitError as e:
                            logger.warning(f"FloodWait {e.seconds}s for mailing {mailing_id}")
                            await asyncio.sleep(e.seconds)
                        except Exception as e:
                            logger.error(f"Error sending mailing {mailing_id} to {target}: {e}")

                        await asyncio.sleep(3)

                    await self.db.update_mailing_last_sent(mailing_id)
                    await asyncio.sleep(mailing.interval_seconds)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error in mailing {mailing_id} iteration: {e}")
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info(f"Mailing {mailing_id} task cancelled")
        except Exception as e:
            logger.error(f"Mailing {mailing_id} loop fatal error: {e}")

    async def _handle_mailing_ban(self, mailing_id: int, account_id: int, error: Exception):
        """Stop mailing, deactivate account, notify owner."""
        error_name = type(error).__name__
        if isinstance(error, (UserDeactivatedBanError,)):
            reason = "⛔️ аккаунт заблокирован Telegram"
        elif isinstance(error, UserDeactivatedError):
            reason = "❌ аккаунт деактивирован"
        else:
            reason = "🔑 сессия сброшена (аккаунт заморожен или вышел)"

        logger.warning(f"Mailing {mailing_id}: ban detected on account {account_id} — {error_name}")

        await self.stop_mailing(mailing_id)
        await self.db.deactivate_account(account_id)

        notify = getattr(self.userbot_manager, '_bot_notify_callback', None)
        if notify:
            try:
                account = await self.db.get_account(account_id)
                if account:
                    user = await self.db.get_user_by_id(account.user_id)
                    if user:
                        await notify(
                            user.telegram_id,
                            f"⚠️ <b>Проблема с аккаунтом!</b>\n\n"
                            f"📱 Аккаунт: <b>{account.display_name}</b>\n"
                            f"❗️ Причина: {reason}\n\n"
                            f"Рассылка остановлена. Аккаунт отключён.\n"
                            f"Добавьте аккаунт заново в разделе «Аккаунты».",
                        )
            except Exception as e:
                logger.error(f"Failed to notify about ban for account {account_id}: {e}")

    def _is_active_hours(self, active_hours_json: Optional[str]) -> bool:
        return is_within_active_hours(active_hours_json)


class SubscriptionCheckerService:
    """Background service that stops mailings when subscription expires and notifies users."""

    def __init__(self, db: Database, mailing_service: MailingService):
        self.db = db
        self.mailing_service = mailing_service
        self._task: Optional[asyncio.Task] = None

    def start(self, bot):
        self._task = asyncio.create_task(self._loop(bot))
        logger.info("Subscription checker started")

    async def _loop(self, bot):
        while True:
            await asyncio.sleep(3600)  # check every hour
            try:
                await self._check(bot)
            except Exception as e:
                logger.error(f"Subscription checker error: {e}")

    async def _check(self, bot):
        users = await self.db.get_all_users()
        now = datetime.now()
        for user in users:
            if user.subscription_end and user.subscription_end < now:
                stopped = await self.mailing_service.stop_user_mailings(user.id)
                if stopped > 0:
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            "⚠️ <b>Ваша подписка истекла.</b>\n\n"
                            f"Остановлено {stopped} рассылок.\n"
                            "Продлите подписку в разделе «Подписка», чтобы возобновить работу.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
