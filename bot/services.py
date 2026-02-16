import asyncio
import logging
import json
import os
import random
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

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
    """Service for CryptoBot payments."""

    def __init__(self, token: str, testnet: bool = False):
        self.token = token
        self.testnet = testnet
        if testnet:
            self.base_url = "https://testnet-pay.crypt.bot/api"
        else:
            self.base_url = "https://pay.crypt.bot/api"
        self.headers = {"Crypto-Pay-API-Token": token}
        self.last_error: Optional[CryptoBotError] = None

    async def create_invoice(
        self,
        amount: float,
        currency: str = "USDT",
        description: str = "",
        expires_in: int = 3600,
    ) -> Optional[Invoice]:
        """Create a payment invoice."""
        self.last_error = None
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                payload = {
                    "asset": currency,
                    "amount": str(amount),
                    "description": description,
                    "expires_in": expires_in,
                }
                async with session.post(
                    f"{self.base_url}/createInvoice",
                    headers=self.headers,
                    json=payload,
                ) as response:
                    data = await response.json()

                    if not data.get("ok"):
                        error = data.get("error", {})
                        error_code = str(error.get("code", "unknown"))
                        error_name = error.get("name", "unknown")
                        logger.error(f"CryptoBot error: code={error_code}, name={error_name}, full={data}")
                        self.last_error = CryptoBotError(
                            code=error_code,
                            name=error_name,
                            message=self._get_error_message(error_name),
                        )
                        return None

                    result = data["result"]
                    return Invoice(
                        invoice_id=str(result["invoice_id"]),
                        amount=float(result["amount"]),
                        currency=result["asset"],
                        pay_url=result["pay_url"],
                        status=result["status"],
                    )
        except Exception as e:
            logger.error(f"Error creating invoice: {e}")
            self.last_error = CryptoBotError(
                code="network_error",
                name="NetworkError",
                message=f"Помилка з'єднання: {str(e)}",
            )
            return None

    def _get_error_message(self, error_name: str) -> str:
        """Get user-friendly error message."""
        messages = {
            "UNAUTHORIZED": "Невірний API токен CryptoBot",
            "API_TOKEN_INVALID": "Невірний API токен CryptoBot",
            "INVALID_AMOUNT": "Невірна сума платежу",
            "INVALID_ASSET": "Невірна валюта",
        }
        return messages.get(error_name, f"Помилка CryptoBot: {error_name}")

    async def check_invoice_paid(self, invoice_id: str) -> bool:
        """Check if invoice is paid."""
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    f"{self.base_url}/getInvoices",
                    headers=self.headers,
                    json={"invoice_ids": invoice_id},
                ) as response:
                    data = await response.json()

                    if not data.get("ok"):
                        logger.error(f"CryptoBot error: {data}")
                        return False

                    items = data.get("result", {}).get("items", [])
                    if items:
                        return items[0].get("status") == "paid"
                    return False
        except Exception as e:
            logger.error(f"Error checking invoice: {e}")
            return False


class TonPaymentService:
    """Service for TON payments via Tonkeeper."""

    def __init__(self, wallet_address: str, api_key: str = ""):
        self.wallet_address = wallet_address
        self.api_key = api_key
        self.base_url = "https://toncenter.com/api/v2"
        self._cached_price: Optional[float] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 60  # cache price for 60 seconds

    async def get_ton_price_usdt(self) -> Optional[float]:
        """Get current TON price in USDT from CoinGecko."""
        import time as _time
        now = _time.time()
        if self._cached_price and (now - self._cache_time) < self._cache_ttl:
            return self._cached_price

        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "the-open-network", "vs_currencies": "usd"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    data = await response.json()
                    price = data.get("the-open-network", {}).get("usd")
                    if price and price > 0:
                        self._cached_price = float(price)
                        self._cache_time = now
                        logger.info(f"TON price fetched: {price} USDT")
                        return self._cached_price
        except Exception as e:
            logger.error(f"CoinGecko API error: {e}")

        # Fallback: try Binance
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": "TONUSDT"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    data = await response.json()
                    price = data.get("price")
                    if price:
                        self._cached_price = float(price)
                        self._cache_time = now
                        logger.info(f"TON price fetched (Binance): {price} USDT")
                        return self._cached_price
        except Exception as e:
            logger.error(f"Binance API error: {e}")

        return self._cached_price  # return stale cache if available

    async def calculate_ton_amount(self, usdt_amount: float) -> Optional[float]:
        """Convert USDT amount to TON using current exchange rate."""
        price = await self.get_ton_price_usdt()
        if not price:
            return None
        ton_amount = usdt_amount / price
        # Round up to 4 decimal places
        return round(ton_amount, 4)

    def generate_payment_link(self, amount_ton: float, comment: str) -> str:
        """Generate Tonkeeper deep link for payment."""
        from urllib.parse import quote
        nanotons = int(amount_ton * 1_000_000_000)
        return f"https://app.tonkeeper.com/transfer/{self.wallet_address}?amount={nanotons}&text={quote(comment)}"

    async def check_payment(self, amount_ton: float, comment: str) -> bool:
        """Check if payment with matching amount and comment was received."""
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            params = {
                "address": self.wallet_address,
                "limit": 30,
            }
            if self.api_key:
                params["api_key"] = self.api_key

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f"{self.base_url}/getTransactions",
                    params=params,
                ) as response:
                    data = await response.json()

                    if not data.get("ok"):
                        logger.error(f"TonCenter API error: {data}")
                        return False

                    expected_nanotons = int(amount_ton * 1_000_000_000)
                    tolerance = 0.05  # 5% tolerance for network fees

                    for tx in data.get("result", []):
                        in_msg = tx.get("in_msg", {})
                        tx_comment = in_msg.get("message", "")
                        tx_value = int(in_msg.get("value", "0"))

                        if tx_comment != comment:
                            continue

                        if tx_value >= int(expected_nanotons * (1 - tolerance)):
                            logger.info(f"TON payment found: comment={comment}, value={tx_value}")
                            return True

                    return False
        except Exception as e:
            logger.error(f"Error checking TON payment: {e}")
            return False


class AutoresponderService:
    """Service for handling autoresponder logic."""

    def __init__(self, db: Database):
        self.db = db

    async def handle_message(
        self,
        event,
        account,
        notify_callback: Optional[Callable] = None,
    ):
        """Handle incoming message for autoresponder."""
        if not event.is_private:
            return

        sender = await event.get_sender()
        if not sender:
            logger.warning(f"Could not get sender for message on account {account.phone}")
            return

        if sender.bot:
            logger.debug(f"Ignoring bot message from {sender.id} on account {account.phone}")
            return

        sender_id = sender.id

        # Send notification about incoming message if enabled
        if account.notify_messages and notify_callback:
            # Get sender information with proper None handling
            sender_first_name = getattr(sender, 'first_name', None) or ""
            sender_last_name = getattr(sender, 'last_name', None) or ""
            sender_name = f"{sender_first_name} {sender_last_name}".strip() or "Без имени"

            # Get username with proper handling
            sender_username_raw = getattr(sender, 'username', None)
            if sender_username_raw:
                sender_username = f"@{sender_username_raw}"
            else:
                sender_username = "не указан"

            message_text = event.text or "(медиа/стикер)"
            if len(message_text) > 200:
                message_text = message_text[:200] + "..."

            notification = (
                f"📥 Сообщение от:\n"
                f"👤 Имя: {sender_name}\n"
                f"🔗 Username: {sender_username}\n"
                f"🆔 ID: {sender_id}\n\n"
                f"💬 {message_text}\n\n"
                f"📱 Аккаунт: {account.phone}"
            )

            try:
                # Get the user's telegram_id from database
                user = await self.db.get_user_by_id(account.user_id)
                if user:
                    await notify_callback(user.telegram_id, notification)
                    logger.info(f"Notification sent to user {user.telegram_id} about message from {sender_id}")
                else:
                    logger.error(f"User {account.user_id} not found for account {account.id} ({account.phone})")
            except Exception as e:
                logger.error(f"Failed to send notification to user {account.user_id}: {e}", exc_info=True)

        # Check if autoresponder is enabled
        if not account.autoresponder_enabled:
            logger.debug(f"Autoresponder disabled for account {account.phone}")
            return

        if not account.autoresponder_text:
            logger.warning(f"Autoresponder enabled but no text set for account {account.phone}")
            return

        # Send autoresponder message
        try:
            await event.respond(account.autoresponder_text)

            logger.info(f"Autoresponder sent to {sender_id} (username: {getattr(sender, 'username', 'none')}) from account {account.phone}")
        except Exception as e:
            logger.error(f"Autoresponder error for account {account.phone} to sender {sender_id}: {e}", exc_info=True)


class MailingService:
    """Service for managing mailings."""

    def __init__(self, db: Database, userbot_manager):
        self.db = db
        self.userbot_manager = userbot_manager
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False

    async def start(self):
        """Start the mailing service and resume active mailings."""
        self._running = True
        mailings = await self.db.get_active_mailings()
        for mailing in mailings:
            await self._start_mailing_task(mailing.id)
        logger.info(f"Mailing service started with {len(mailings)} active mailings")

    async def stop(self):
        """Stop all mailing tasks."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        logger.info("Mailing service stopped")

    async def start_mailing(self, mailing_id: int) -> bool:
        """Start a specific mailing."""
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
        """Stop a specific mailing."""
        await self.db.update_mailing_status(mailing_id, False)
        if mailing_id in self._tasks:
            self._tasks[mailing_id].cancel()
            del self._tasks[mailing_id]

    async def delete_mailing(self, mailing_id: int):
        """Delete a mailing and stop its task."""
        await self.stop_mailing(mailing_id)
        await self.db.delete_mailing(mailing_id)

    async def _start_mailing_task(self, mailing_id: int):
        """Create and start a mailing task."""
        if mailing_id in self._tasks:
            self._tasks[mailing_id].cancel()

        task = asyncio.create_task(self._mailing_loop(mailing_id))
        self._tasks[mailing_id] = task

    async def _mailing_loop(self, mailing_id: int):
        """Main loop for a mailing."""
        logger.info(f"Starting mailing loop for mailing {mailing_id}")
        try:
            while self._running:
                try:
                    mailing = await self.db.get_mailing(mailing_id)
                    if not mailing or not mailing.is_active:
                        logger.info(f"Mailing {mailing_id} stopped: not active or not found")
                        break

                    if not self._is_active_hours(mailing.active_hours_json):
                        logger.debug(f"Mailing {mailing_id}: not in active hours, waiting...")
                        await asyncio.sleep(60)
                        continue

                    messages = await self.db.get_mailing_messages(mailing_id)
                    targets = await self.db.get_mailing_targets(mailing_id)

                    if not messages or not targets:
                        logger.warning(f"Mailing {mailing_id}: no messages or targets, waiting...")
                        await asyncio.sleep(60)
                        continue

                    client = await self.userbot_manager.get_client(mailing.account_id)
                    if not client:
                        logger.warning(f"No client for mailing {mailing_id}, waiting...")
                        await asyncio.sleep(60)
                        continue

                    if not client.is_connected():
                        logger.warning(f"Client for mailing {mailing_id} is disconnected, reconnecting...")
                        try:
                            await client.connect()
                        except Exception as conn_err:
                            logger.error(f"Failed to reconnect client for mailing {mailing_id}: {conn_err}")
                            await asyncio.sleep(60)
                            continue

                    msg = random.choice(messages)

                    for target_obj in targets:
                        target = target_obj.chat_identifier

                        # Normalize username format - ensure @ prefix for usernames
                        normalized_target = target
                        if not target.startswith('-') and not target.startswith('@') and not target.isdigit():
                            normalized_target = f"@{target}"

                        try:
                            photos = [p for p in msg.photo_paths if os.path.exists(p)]
                            if len(photos) > 1:
                                # Send as album
                                await client.send_file(
                                    normalized_target,
                                    photos,
                                    caption=msg.text or None,
                                )
                            elif len(photos) == 1:
                                await client.send_file(
                                    normalized_target,
                                    photos[0],
                                    caption=msg.text or None,
                                )
                            else:
                                await client.send_message(normalized_target, msg.text)
                            logger.info(f"Mailing {mailing_id} sent to {normalized_target}")
                        except Exception as e:
                            logger.error(f"Error sending mailing {mailing_id} to {normalized_target}: {e}")

                        # Small delay between targets to avoid flood
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

    def _is_active_hours(self, active_hours_json: Optional[str]) -> bool:
        """Check if current time is within active hours."""
        return is_within_active_hours(active_hours_json)
