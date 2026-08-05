import asyncio
import logging
import html
import json
import os
import random
import re
import ssl
import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Callable

from telethon.errors import (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
    FloodWaitError,
    UserNotParticipantError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    UserAlreadyParticipantError,
    InviteRequestSentError,
    ChatSendMediaForbiddenError,
    ChatGuestSendForbiddenError,
    SlowModeWaitError,
    RightForbiddenError,
    ChatAdminRequiredError,
    PeerFloodError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import KeyboardButtonUrl

_BAN_ERRORS = (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)

_CHAT_BAN_ERRORS = (
    UserBannedInChannelError,
)

# Errors that may mean "not a member" — try to join first
_NOT_MEMBER_ERRORS = (
    UserNotParticipantError,
    ChatWriteForbiddenError,
)

import aiohttp
import certifi

from .config import config
from .database.db import Database, is_safe_media_path
from .keyboards.inline import subscription_expired_keyboard
from .utils.time_utils import is_within_active_hours, now_moscow
from .utils.premium_emoji import pe

logger = logging.getLogger(__name__)


@dataclass
class Invoice:
    invoice_id: str
    amount: float
    currency: str
    pay_url: str
    status: str


_uah_rate_cache: Optional[float] = None
_uah_rate_cache_at: float = 0
_UAH_CACHE_TTL: float = 3600


async def get_usd_uah_rate() -> float:
    """Fetch USD/UAH rate from NBU API. Cached 1h. Fallback: 41.0"""
    import time as _time
    global _uah_rate_cache, _uah_rate_cache_at
    now = _time.time()
    if _uah_rate_cache and (now - _uah_rate_cache_at) < _UAH_CACHE_TTL:
        return _uah_rate_cache
    try:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=USD&json",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json(content_type=None)
                rate = float(data[0]["rate"])
                _uah_rate_cache = rate
                _uah_rate_cache_at = now
                return rate
    except Exception as e:
        logging.warning(f"NBU UAH rate fetch failed: {e}")
        return _uah_rate_cache or 41.0


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
                                        headers=self.headers, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
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
                                        json={"invoice_ids": invoice_id},
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
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
            # 30 was too easy to grief: sending 30+ small "dust" transfers to the
            # (public) wallet address pushes a legitimate pending payment out of
            # the checked window, so a real payer's "check payment" would report
            # "not paid" even though the money arrived.
            params = {"address": self.wallet_address, "limit": 100}
            if self.api_key:
                params["api_key"] = self.api_key
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f"{self.base_url}/getTransactions", params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        return False
                    expected = int(amount_ton * 1_000_000_000)
                    for tx in data.get("result", []):
                        in_msg = tx.get("in_msg", {})
                        if in_msg.get("message") != comment:
                            continue
                        if int(in_msg.get("value", "0")) >= int(expected * 0.99):
                            return True
                    return False
        except Exception as e:
            logger.error(f"Error checking GRAM(TON) payment: {e}")
            return False


class PlategaService:
    """Platega SBP payment service (rubles via СБП). API: https://docs.platega.io/"""

    _BASE_URL = "https://app.platega.io"

    _usd_rub_cache: Optional[float] = None
    _usd_rub_cache_at: float = 0
    _CACHE_TTL: float = 3600  # 1 hour

    def __init__(self, merchant_id: str, secret: str):
        self.merchant_id = merchant_id
        self.secret = secret

    def _headers(self) -> dict:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json",
        }

    async def get_usd_rub_rate(self) -> float:
        import time as _time
        now = _time.time()
        if PlategaService._usd_rub_cache and (now - PlategaService._usd_rub_cache_at) < self._CACHE_TTL:
            return PlategaService._usd_rub_cache
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://www.cbr-xml-daily.ru/daily_json.js",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json(content_type=None)
                    rate = float(data["Valute"]["USD"]["Value"])
                    PlategaService._usd_rub_cache = rate
                    PlategaService._usd_rub_cache_at = now
                    return rate
        except Exception as e:
            logger.warning(f"Platega: failed to get USD/RUB rate: {e}")
            return PlategaService._usd_rub_cache or 90.0

    async def calculate_rub_price(self, usdt_amount: float) -> float:
        """Convert USDT amount to RUB with the 5% commission."""
        rate = await self.get_usd_rub_rate()
        return round(usdt_amount * 1.05 * rate)

    async def create_invoice(self, amount_rub: float, order_id: str, description: str) -> Optional[dict]:
        """Create Platega payment. Returns {'payment_id': ..., 'payment_url': ...} or None."""
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            payload = {
                "paymentDetails": {
                    "amount": amount_rub,
                    "currency": "RUB",
                },
                "description": description,
                "payload": order_id,  # store order_id in payload for reference
            }
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    f"{self._BASE_URL}/v2/transaction/process",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    raw = await resp.text()
                    logger.info(f"Platega create_invoice HTTP {resp.status}: {raw[:500]}")
                    try:
                        data = json.loads(raw)
                    except Exception:
                        logger.error(f"Platega: non-JSON response: {raw[:200]}")
                        return None
                    transaction_id = data.get("transactionId")
                    pay_url = data.get("url")
                    if transaction_id and pay_url:
                        return {"payment_id": transaction_id, "payment_url": pay_url}
                    logger.error(f"Platega create_invoice bad response: {data}")
                    return None
        except Exception as e:
            logger.error(f"Platega create_invoice exception: {type(e).__name__}: {e}", exc_info=True)
            return None

    async def check_payment(self, transaction_id: str) -> bool:
        """Check if Platega transaction is confirmed (paid)."""
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f"{self._BASE_URL}/transaction/{transaction_id}",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    raw = await resp.text()
                    logger.info(f"Platega check_payment HTTP {resp.status}: {raw[:300]}")
                    data = json.loads(raw)
                    status = str(data.get("status") or "").upper()
                    return status == "CONFIRMED"
        except Exception as e:
            logger.warning(f"Platega check_payment exception: {e}")
            return False


def _extract_button_urls(message) -> list:
    """Extract all URLs from inline keyboard buttons."""
    urls = []
    try:
        if message.buttons:
            for row in message.buttons:
                for btn in row:
                    url = getattr(btn, 'url', None)
                    if url:
                        urls.append(url)
            return urls
    except Exception:
        pass
    # Fallback: read from raw reply_markup
    try:
        from telethon.tl.types import ReplyInlineMarkup
        rm = getattr(message, 'reply_markup', None)
        if isinstance(rm, ReplyInlineMarkup):
            for row in rm.rows:
                for btn in row.buttons:
                    url = getattr(btn, 'url', None)
                    if url:
                        urls.append(url)
    except Exception:
        pass
    return urls


def _parse_telegram_links(urls: list) -> tuple:
    """Parse t.me URLs into (public_usernames, invite_hashes)."""
    channel_usernames = []
    invite_hashes = []
    for url in urls:
        # Private invite: t.me/+HASH or t.me/joinchat/HASH
        m = re.search(r't\.me/(?:joinchat/|\+)([A-Za-z0-9_\-]+)', url)
        if m:
            h = m.group(1)
            if h not in invite_hashes:
                invite_hashes.append(h)
            continue
        # Public channel: t.me/username
        m = re.search(r't\.me/([A-Za-z0-9_]+)', url)
        if m:
            username = m.group(1)
            if username not in channel_usernames:
                channel_usernames.append(username)
            continue
        # Deep link: tg://resolve?domain=username
        m = re.search(r'tg://resolve\?domain=([A-Za-z0-9_]+)', url)
        if m:
            username = m.group(1)
            if username not in channel_usernames:
                channel_usernames.append(username)
    return channel_usernames, invite_hashes


async def _resolve_redirect_urls(urls: list) -> list:
    """Follow redirects and scrape HTML/JS for t.me links in non-t.me URLs."""
    from urllib.parse import unquote
    result = list(urls)
    non_tme = [u for u in urls if 't.me' not in u and 'tg://' not in u and u.startswith('http')]
    if not non_tme:
        return result

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    def _extract_tme(text: str) -> list:
        found = []
        # normal https://t.me/... and escaped https:\/\/t.me\/...
        text_unesc = text.replace('\/', '/').replace('\\/', '/')
        for pattern in [
            r'https?://t\.me/[A-Za-z0-9_+/\-]+',
            r'tg://[^\s"\'<>\\\]]+',
        ]:
            for m in re.finditer(pattern, text_unesc):
                link = m.group(0).rstrip('.,);"\' ')
                # URL-decode if needed
                link = unquote(link)
                if link not in found:
                    found.append(link)
        return found

    def _safe_url(url: str) -> bool:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            host = parsed.hostname.lower()
            if host in {"localhost", "localhost.localdomain"}:
                return False
            for addr in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(addr[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False
            return True
        except (ValueError, OSError):
            return False

    try:
        connector = aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
        async with aiohttp.ClientSession(connector=connector) as session:
            for url in non_tme:
                if not _safe_url(url):
                    logger.warning("Skipped unsafe sponsor URL: %s", url)
                    continue
                try:
                    async with session.get(
                        url, allow_redirects=False,
                        timeout=aiohttp.ClientTimeout(total=10),
                        headers=headers,
                    ) as resp:
                        final = str(resp.url)
                        location = resp.headers.get("Location")
                        if location:
                            from urllib.parse import urljoin
                            redirected = urljoin(str(resp.url), location)
                            if _safe_url(redirected):
                                final = redirected
                        if final not in result:
                            result.append(final)
                        body_bytes = await resp.content.read(1024 * 1024 + 1)
                        if len(body_bytes) > 1024 * 1024:
                            logger.warning("Sponsor URL response is too large: %s", url)
                            continue
                        body = body_bytes.decode(resp.charset or "utf-8", errors="replace")
                        for link in _extract_tme(body):
                            if link not in result:
                                result.append(link)
                                logger.info(f"Sponsor track: found t.me link in page: {link}")
                except Exception as e:
                    logger.debug(f"Sponsor track: failed to resolve {url}: {e}")
    except Exception:
        pass
    return result


class AutoresponderService:
    def __init__(self, db: Database):
        self.db = db
        self._notification_events: dict[tuple[int, int, int], float] = {}

    async def handle_message(self, event, account, notify_callback: Optional[Callable] = None):
        """Handle incoming private message for autoresponder."""
        if not event.is_private:
            return

        sender = await event.get_sender()
        if not sender or getattr(sender, 'bot', False):
            return

        sender_id = sender.id

        if account.notify_messages and notify_callback:
            event_id = getattr(event, "id", None)
            event_chat_id = getattr(event, "chat_id", None)
            event_key = (account.id, event_chat_id, event_id)
            now = time.monotonic()
            duplicate = event_id is not None and (
                now - self._notification_events.get(event_key, 0) < 3600
            )
            if not duplicate:
                if event_id is not None:
                    self._notification_events[event_key] = now
                    if len(self._notification_events) > 10000:
                        cutoff = now - 3600
                        self._notification_events = {
                            key: timestamp
                            for key, timestamp in self._notification_events.items()
                            if timestamp >= cutoff
                        }
                notification_sent = False
                sender_name = (f"{getattr(sender,'first_name','') or ''} {getattr(sender,'last_name','') or ''}").strip() or "Без имени"
                sender_username = f"@{sender.username}" if getattr(sender, 'username', None) else "не указан"
                msg_text = event.text or "(медиа/стикер)"
                if len(msg_text) > 200:
                    msg_text = msg_text[:200] + "..."
                notification = pe(
                    f"📥 Сообщение от:\n"
                    f"👤 {html.escape(sender_name)}\n"
                    f"🔗 {html.escape(sender_username)}\n"
                    f"🆔 {sender_id}\n\n"
                    f"💬 {html.escape(msg_text)}\n\n"
                    f"📱 Аккаунт: {html.escape(account.display_name)}"
                )
                try:
                    user = await self.db.get_user_by_id(account.user_id)
                    if user:
                        await notify_callback(user.telegram_id, notification)
                        notification_sent = True
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Notification error: {e}")
                finally:
                    if (
                        event_id is not None
                        and not notification_sent
                        and self._notification_events.get(event_key) == now
                    ):
                        self._notification_events.pop(event_key, None)

        if not account.autoresponder_enabled or not account.autoresponder_text:
            return

        claimed = await self.db.claim_autoresponder_history(account.id, sender_id, event.text)
        if not claimed:
            return

        try:
            ar_user = await self.db.get_user_by_id(account.user_id)
            sig = _FREE_TIER_SIGNATURE if (ar_user and Database.is_free_ad_active(ar_user)) else ""
            ar_text = ((account.autoresponder_text or "") + sig) or None
            if (
                account.autoresponder_photo
                and is_safe_media_path(account.autoresponder_photo)
                and os.path.exists(account.autoresponder_photo)
            ):
                await event.client.send_file(
                    sender_id, account.autoresponder_photo,
                    caption=ar_text
                )
            else:
                await event.respond(ar_text or "")
            logger.info(f"Autoresponder sent to {sender_id} from {account.phone}")
        except Exception as e:
            # Allow a retry if sending failed after the atomic claim.
            await self.db.clear_autoresponder_history_for_sender(account.id, sender_id)
            logger.error(f"Autoresponder error: {e}")

    async def handle_group_reply(self, event, account, me_id: int, notify_callback: Optional[Callable] = None):
        """Handle group message that is a reply to this account's message."""
        if event.is_private:
            return

        if not event.reply_to:
            return

        if not account.group_autoresponder_enabled or not account.group_autoresponder_text:
            return

        sender_id = None
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
            claimed = await self.db.claim_autoresponder_history(account.id, sender_id, event.text)
            if not claimed:
                return
            gr_user = await self.db.get_user_by_id(account.user_id)
            sig = _FREE_TIER_SIGNATURE if (gr_user and Database.is_free_ad_active(gr_user)) else ""
            gr_text = ((account.group_autoresponder_text or "") + sig) or None
            if (
                account.group_autoresponder_photo
                and is_safe_media_path(account.group_autoresponder_photo)
                and os.path.exists(account.group_autoresponder_photo)
            ):
                await event.client.send_file(
                    event.chat_id, account.group_autoresponder_photo,
                    caption=gr_text,
                    reply_to=event.id
                )
            else:
                await event.reply(gr_text or "")
            logger.info(f"Group autoresponder replied to {sender_id} from {account.phone}")
        except Exception as e:
            if sender_id is not None:
                await self.db.clear_autoresponder_history_for_sender(account.id, sender_id)
            logger.error(f"Group autoresponder error: {e}")

    async def handle_sponsor_check(self, event, account, me_id: int):
        """
        Auto-join sponsor channels when a bot/service tags this account in a group chat
        (either as a reply to our message, or as a direct @mention) and the message
        contains buttons or text with t.me/ channel links.
        """
        if event.is_private:
            return

        original = None  # наше оригінальне повідомлення (якщо це reply)

        # Сценарій 1: повідомлення є reply на наше повідомлення
        if event.reply_to:
            try:
                orig = await event.get_reply_message()
                if orig:
                    orig_sender = await orig.get_sender()
                    if orig_sender and orig_sender.id == me_id:
                        original = orig
            except Exception:
                pass

        # Сценарій 2: повідомлення тегає наш акаунт через @mention entity
        is_mention = False
        if original is None:
            try:
                for ent in (getattr(event.message, 'entities', None) or []):
                    if getattr(ent, 'user_id', None) == me_id:
                        is_mention = True
                        break
            except Exception:
                pass

        # Якщо жоден сценарій не спрацював — повідомлення не стосується нашого акаунту
        if original is None and not is_mention:
            return

        # Фільтр: пропускаємо якщо відправник — звичайний юзер (не бот, не анонімний адмін)
        try:
            sender = await event.get_sender()
            from telethon.tl.types import User as _TLUser
            if sender and isinstance(sender, _TLUser) and not sender.bot:
                return
        except Exception:
            pass  # не вдалось визначити — продовжуємо

        # Витягуємо посилання з кнопок
        urls = _extract_button_urls(event.message)

        # Витягуємо t.me/ і tg:// посилання з тексту
        msg_text = getattr(event.message, 'text', None) or getattr(event.message, 'message', None) or ""
        if msg_text:
            urls += re.findall(r'https?://t\.me/\S+|tg://[^\s]+', msg_text)

        urls = await _resolve_redirect_urls(urls)
        channel_usernames, invite_hashes = _parse_telegram_links(urls)

        # Також витягуємо @username згадки з тексту (деякі боти пишуть @channel без t.me/ лінку)
        if msg_text:
            for mention in re.findall(r'@([A-Za-z0-9_]{5,32})', msg_text):
                if mention not in channel_usernames:
                    channel_usernames.append(mention)

        if not channel_usernames and not invite_hashes:
            return

        logger.info(f"Account {account.phone}: sponsor gate detected (reply={original is not None}, mention={is_mention}), public={channel_usernames} invites={invite_hashes}")

        joined_any = True

        for username in channel_usernames:
            try:
                await event.client(JoinChannelRequest(f"@{username}"))
                logger.info(f"Account {account.phone} auto-joined sponsor @{username}")
                await asyncio.sleep(1)
            except UserAlreadyParticipantError:
                logger.info(f"Account {account.phone} is already subscribed to sponsor @{username}")
            except InviteRequestSentError:
                logger.info(f"Account {account.phone} sent join request for @{username} — pending approval")
                joined_any = False
            except Exception as e:
                joined_any = False
                logger.warning(f"Account {account.phone} failed to join @{username}: {e}")

        for h in invite_hashes:
            try:
                await event.client(ImportChatInviteRequest(h))
                logger.info(f"Account {account.phone} auto-joined sponsor via invite hash {h}")
                await asyncio.sleep(1)
            except UserAlreadyParticipantError:
                logger.info(f"Account {account.phone} is already subscribed via invite {h}")
            except InviteRequestSentError:
                logger.info(f"Account {account.phone} sent join request for invite {h} — pending approval")
                joined_any = False
            except Exception as e:
                joined_any = False
                logger.warning(f"Account {account.phone} failed to join invite {h}: {e}")

        # Після підписки — повторно надсилаємо наше оригінальне повідомлення
        # (тільки якщо це був reply — ми знаємо яке повідомлення відправити)
        if joined_any and original is not None:
            try:
                await asyncio.sleep(2)
                text = getattr(original, 'text', None) or getattr(original, 'message', None)
                entities = getattr(original, 'entities', None)
                media = getattr(original, 'media', None)
                if media:
                    await event.client.send_file(
                        event.chat_id,
                        media,
                        caption=text or None,
                        formatting_entities=entities if entities else None,
                    )
                    logger.info(f"Account {account.phone}: re-sent mailing media after sponsor join in {event.chat_id}")
                elif text:
                    await event.client.send_message(
                        event.chat_id,
                        text,
                        formatting_entities=entities if entities else None,
                    )
                    logger.info(f"Account {account.phone}: re-sent mailing message after sponsor join in {event.chat_id}")
            except Exception as e:
                logger.warning(f"Account {account.phone}: failed to re-send after sponsor join: {e}")


def _build_telethon_entities(entities_json: str) -> list:
    """Convert serialized aiogram entities JSON to Telethon MessageEntity objects."""
    from telethon.tl.types import (
        MessageEntityBold, MessageEntityItalic, MessageEntityCode,
        MessageEntityUnderline, MessageEntityStrike, MessageEntitySpoiler,
        MessageEntityPre, MessageEntityTextUrl, MessageEntityCustomEmoji,
        MessageEntityBlockquote,
    )
    TYPE_MAP = {
        "bold":          MessageEntityBold,
        "italic":        MessageEntityItalic,
        "code":          MessageEntityCode,
        "underline":     MessageEntityUnderline,
        "strikethrough": MessageEntityStrike,
        "spoiler":       MessageEntitySpoiler,
    }
    result = []
    try:
        items = json.loads(entities_json)
    except Exception:
        return result
    for e in items:
        t = e.get("type", "")
        o = e.get("offset", 0)
        l = e.get("length", 0)
        if t in TYPE_MAP:
            result.append(TYPE_MAP[t](offset=o, length=l))
        elif t == "pre":
            result.append(MessageEntityPre(offset=o, length=l,
                                           language=e.get("language", "") or ""))
        elif t == "text_link":
            result.append(MessageEntityTextUrl(offset=o, length=l, url=e.get("url", "")))
        elif t == "custom_emoji":
            try:
                result.append(MessageEntityCustomEmoji(
                    offset=o, length=l,
                    document_id=int(e["custom_emoji_id"])
                ))
            except (KeyError, ValueError):
                pass
        elif t == "blockquote":
            try:
                result.append(MessageEntityBlockquote(offset=o, length=l))
            except TypeError:
                pass
        elif t == "expandable_blockquote":
            try:
                result.append(MessageEntityBlockquote(offset=o, length=l, collapsed=True))
            except TypeError:
                pass
    return result


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _truncate_utf16(text: str, max_units: int) -> str:
    if _utf16_length(text) <= max_units:
        return text
    return text.encode("utf-16-le")[:max_units * 2].decode("utf-16-le", errors="ignore")


_FREE_TIER_SIGNATURE = "\n━━━━━━━━━━\n🤖 Отправлено через @feAutoSenderBot"


class MailingService:
    def __init__(self, db: Database, userbot_manager):
        self.db = db
        self.userbot_manager = userbot_manager
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False
        self._stagger_until: dict[int, dict[int, datetime]] = {}  # mailing_id -> {target_id -> first_send_allowed_at}
        self._me_cache: dict[int, object] = {}  # account_id -> me object, avoids get_me() every loop
        self._mailing_locks: dict[int, asyncio.Lock] = {}
        self._working_targets: dict[int, set[int]] = {}

    async def start(self):
        self._running = True
        mailings = await self.db.get_active_mailings()
        for m in mailings:
            await self._start_mailing_task(m.id)
        logger.info(f"Mailing service started with {len(mailings)} active mailings")

    async def stop(self):
        self._running = False
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._working_targets.clear()

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
        task = self._tasks.pop(mailing_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._stagger_until.pop(mailing_id, None)
        self._working_targets.pop(mailing_id, None)

    async def stop_user_mailings(self, user_id: int):
        """Stop all active mailings for a user (called when subscription expires)."""
        mailings = await self.db.get_user_active_mailings(user_id)
        for m in mailings:
            await self.stop_mailing(m.id)
        return len(mailings)

    def _mark_target_working(self, mailing_id: int, target_id: int):
        self._working_targets.setdefault(mailing_id, set()).add(target_id)

    def _mark_target_not_working(self, mailing_id: int, target_id: int):
        targets = self._working_targets.get(mailing_id)
        if targets is not None:
            targets.discard(target_id)

    def _clear_working_targets(self, mailing_id: int):
        self._working_targets.pop(mailing_id, None)

    def forget_target(self, mailing_id: int, target_id: int):
        """Evict a deleted target from the in-memory working set immediately,
        instead of waiting for the next full-clear event to notice it's gone."""
        self._mark_target_not_working(mailing_id, target_id)

    async def get_free_tier_runtime_stats(self) -> dict:
        working_target_ids = {
            target_id
            for mailing_id, target_ids in self._working_targets.items()
            if (
                self._running
                and (task := self._tasks.get(mailing_id)) is not None
                and not task.done()
            )
            for target_id in target_ids
        }

        total_users = await self.db.count_free_tier_users()
        rows = await self.db.get_free_tier_target_rows()
        active_rows = [row for row in rows if row["target_id"] in working_target_ids]

        active_user_ids = {row["user_id"] for row in active_rows}
        active_mailing_ids = {row["mailing_id"] for row in active_rows}
        chat_users: dict[str, dict] = {}

        for row in active_rows:
            chat = row["chat_identifier"]
            chat_data = chat_users.setdefault(
                chat.casefold(),
                {"chat_identifier": chat, "users": {}},
            )
            user_delays = chat_data["users"]
            user_delays.setdefault(row["user_id"], []).append(int(row["delay_seconds"]))

        chats = []
        for chat_data in chat_users.values():
            user_delays = chat_data["users"]
            average_by_user = [
                sum(delays) / len(delays)
                for delays in user_delays.values()
            ]
            chats.append({
                "chat_identifier": chat_data["chat_identifier"],
                "user_count": len(user_delays),
                "average_delay": round(sum(average_by_user) / len(average_by_user)),
            })
        chats.sort(key=lambda row: (-row["user_count"], row["chat_identifier"].lower()))

        return {
            "total_users": total_users,
            "active_users": len(active_user_ids),
            "active_mailings": len(active_mailing_ids),
            "unique_chats": len(chats),
            "chats": chats,
        }

    async def send_hidden_tag(self, mailing_id: int) -> int:
        mailing = await self.db.get_mailing(mailing_id)
        if not mailing:
            return 0
        client = await self.userbot_manager.get_client(mailing.account_id)
        if not client:
            return 0

        from telethon.tl.types import MessageEntityMentionName

        sent = 0
        for target_obj in await self.db.get_mailing_targets(mailing_id):
            target = target_obj.chat_identifier
            try:
                participants = await client.get_participants(target)
                participants = [
                    user for user in participants
                    if getattr(user, "id", None) and not getattr(user, "bot", False)
                    and not getattr(user, "deleted", False)
                ]
                if not participants:
                    continue
                for start in range(0, len(participants), 4096):
                    batch = participants[start:start + 4096]
                    text = "\u2063" * len(batch)
                    entities = [
                        MessageEntityMentionName(offset=index, length=1, user_id=user.id)
                        for index, user in enumerate(batch)
                    ]
                    await client.send_message(
                        target, text, formatting_entities=entities,
                        reply_to=target_obj.thread_id,
                    )
                    sent += 1
            except Exception as error:
                logger.warning("Hidden tag failed for %s: %s", target, error)
        return sent

    async def _notify_flood_wait(self, user, account_id: int, seconds: int):
        notify = getattr(self.userbot_manager, '_bot_notify_callback', None)
        if not notify or not user:
            return
        try:
            account_obj = await self.db.get_account(account_id)
            finish_at = now_moscow() + timedelta(seconds=seconds)
            await notify(
                user.telegram_id,
                pe(
                    "⚠️ <b>FloodWait</b>\n\n"
                    f"Аккаунт: <b>{html.escape(str(account_obj.display_name if account_obj else account_id))}</b>\n"
                    f"Длительность: <b>{seconds} сек.</b>\n"
                    f"Окончание (МСК): <b>{finish_at.strftime('%d.%m.%Y %H:%M:%S')}</b>\n\n"
                    "Рекомендуется сменить аккаунт."
                ),
            )
        except Exception:
            pass

    async def delete_mailing(self, mailing_id: int):
        await self.stop_mailing(mailing_id)
        await self.db.delete_mailing(mailing_id)

    async def _has_chat_activity(self, client, target: str, since: datetime, my_id: int) -> bool:
        """Повертає True якщо хтось (крім бота) писав у чат після since."""
        try:
            msgs = await client.get_messages(target, limit=10)
            for m in msgs:
                msg_time = m.date.replace(tzinfo=None) if m.date.tzinfo else m.date
                if msg_time > since and m.sender_id != my_id:
                    return True
            return False
        except Exception:
            return True  # якщо не можемо перевірити — дозволяємо відправку

    async def _has_new_activity_after_account_message(self, client, target: str, my_id: int) -> bool:
        """Check whether another participant sent the latest message."""
        try:
            messages = await client.get_messages(target, limit=1)
            for message in messages:
                return not bool(
                    getattr(message, "out", getattr(message, "sender_id", None) == my_id)
                )
            return True
        except Exception:
            return True

    async def _get_recent_user_ids(self, client, target: str, my_id: int) -> list:
        try:
            from telethon.tl.types import User

            messages = await client.get_messages(target, limit=100)
            result = []
            seen = set()
            for message in messages:
                sender_id = getattr(message, "sender_id", None)
                if not sender_id or sender_id == my_id or sender_id in seen:
                    continue
                sender = getattr(message, "sender", None)
                if sender is None:
                    try:
                        sender = await message.get_sender()
                    except Exception:
                        continue
                if not isinstance(sender, User) or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
                    continue
                seen.add(sender_id)
                result.append(sender)
                if len(result) == 15:
                    break
            return result
        except Exception:
            return []

    async def _send_msg(
        self,
        client,
        target: str,
        msg,
        pm: Optional[str],
        reply_to=None,
        add_signature: bool = False,
        hidden_tag_user_ids: Optional[list[int]] = None,
    ) -> None:
        """Send one mailing message to target (forward / text / photo)."""
        if msg.is_forward:
            peer = int(msg.forward_peer) if msg.forward_peer.lstrip('-').isdigit() else msg.forward_peer
            entity = await client.get_entity(peer)
            await client.forward_messages(
                target, [msg.forward_msg_id], from_peer=entity
            )
            return

        dice_emoji = msg.text if msg.is_dice else None
        sig = _FREE_TIER_SIGNATURE if add_signature else ""
        entities = _build_telethon_entities(msg.entities_json) if msg.entities_json else None
        photos = [
            p for p in msg.photo_paths
            if is_safe_media_path(p) and os.path.exists(p)
        ]
        video = (
            msg.video_path
            if msg.video_path
            and is_safe_media_path(msg.video_path)
            and os.path.exists(msg.video_path)
            else None
        )

        raw_text = "" if dice_emoji else (msg.text or "")
        max_len = 1024 if (photos or video) else 4096
        if raw_text or sig:
            if sig and _utf16_length(raw_text) + _utf16_length(sig) > max_len:
                raw_text = _truncate_utf16(
                    raw_text,
                    max_len - _utf16_length(sig),
                )
                if entities:
                    raw_length = _utf16_length(raw_text)
                    entities = [
                        entity
                        for entity in entities
                        if entity.offset + entity.length <= raw_length
                    ]
            text = raw_text + sig
        else:
            text = None
        if hidden_tag_user_ids and (text is not None or photos or video or dice_emoji):
            from telethon.tl.types import InputMessageEntityMentionName

            base_text = text or ""
            input_users = []
            for user in hidden_tag_user_ids:
                try:
                    input_users.append(await client.get_input_entity(user))
                except Exception:
                    continue

            if input_users and not entities and base_text and pm and not dice_emoji:
                base_text, entities = await client._parse_message_text(base_text, pm)

            available = max_len - _utf16_length(base_text)
            count = max(0, min(len(input_users), available))
            if count:
                offset = _utf16_length(base_text)
                hidden_entities = [
                    InputMessageEntityMentionName(
                        offset=offset + index,
                        length=1,
                        user_id=input_user,
                    )
                    for index, input_user in enumerate(input_users[:count])
                ]
                text = base_text + ("\u2063" * len(hidden_entities))
                entities = (entities or []) + hidden_entities
        eff_pm = None if entities else ("html" if dice_emoji else pm)
        if dice_emoji:
            from telethon.tl.types import InputMediaDice

            await client.send_file(
                target,
                InputMediaDice(dice_emoji),
                caption=text,
                parse_mode=eff_pm,
                formatting_entities=entities,
                reply_to=reply_to,
            )
        elif video:
            await client.send_file(target, video, caption=text, parse_mode=eff_pm,
                                   formatting_entities=entities, reply_to=reply_to)
        elif len(photos) > 1:
            await client.send_file(target, photos, caption=text, parse_mode=eff_pm,
                                   formatting_entities=entities, reply_to=reply_to)
        elif len(photos) == 1:
            await client.send_file(target, photos[0], caption=text, parse_mode=eff_pm,
                                   formatting_entities=entities, reply_to=reply_to)
        else:
            if not text and not entities:
                raise ValueError(f"Mailing message has no text and no photos")
            await client.send_message(target, text or "", parse_mode=eff_pm,
                                      formatting_entities=entities, reply_to=reply_to)

    async def _start_mailing_task(self, mailing_id: int):
        lock = self._mailing_locks.setdefault(mailing_id, asyncio.Lock())
        async with lock:
            if mailing_id in self._tasks and not self._tasks[mailing_id].done():
                return
            self._clear_working_targets(mailing_id)
            mailing = await self.db.get_mailing(mailing_id)
            if mailing:
                targets = await self.db.get_mailing_targets(mailing_id)
                virgin = [t for t in targets if t.last_sent_at is None]
                if virgin:
                    stagger_delay = min(30, mailing.interval_seconds // max(len(targets), 1))
                    now = now_moscow()
                    self._stagger_until[mailing_id] = {
                        t.id: now + timedelta(seconds=i * stagger_delay)
                        for i, t in enumerate(virgin)
                    }
            task = asyncio.create_task(self._mailing_loop(mailing_id))
            self._tasks[mailing_id] = task

    async def _mailing_loop(self, mailing_id: int):
        logger.info(f"Mailing {mailing_id}: loop started")
        try:
            while self._running:
                try:
                    mailing = await self.db.get_mailing(mailing_id)
                    if not mailing or not mailing.is_active:
                        self._clear_working_targets(mailing_id)
                        logger.info(
                            f"Mailing {mailing_id}: loop exiting — "
                            f"{'not found in DB' if not mailing else 'is_active set to False'}"
                        )
                        break

                    if not self._is_active_hours(mailing.active_hours_json):
                        self._clear_working_targets(mailing_id)
                        await asyncio.sleep(60)
                        continue

                    messages = await self.db.get_mailing_messages(mailing_id)
                    targets = await self.db.get_mailing_targets(mailing_id)

                    if not messages or not targets:
                        self._clear_working_targets(mailing_id)
                        logger.debug(
                            f"Mailing {mailing_id}: no messages ({len(messages)}) or "
                            f"targets ({len(targets)}), waiting 60s"
                        )
                        await asyncio.sleep(60)
                        continue

                    logger.info(
                        f"Mailing {mailing_id} [{mailing.name}]: cycle — "
                        f"{len(messages)} message(s), {len(targets)} target(s), "
                        f"account_id={mailing.account_id}"
                    )

                    # Resolve main client
                    client = await self.userbot_manager.get_client(mailing.account_id)
                    if not client:
                        self._clear_working_targets(mailing_id)
                        await asyncio.sleep(60)
                        continue

                    if not client.is_connected():
                        try:
                            await client.connect()
                        except _BAN_ERRORS as e:
                            self._clear_working_targets(mailing_id)
                            await self._handle_mailing_ban(mailing_id, mailing.account_id, e)
                            return
                        except Exception as e:
                            self._clear_working_targets(mailing_id)
                            logger.error(f"Reconnect failed for mailing {mailing_id}: {e}")
                            await asyncio.sleep(60)
                            continue

                    now = now_moscow()
                    sent_any = False
                    cycle_sent = 0
                    cycle_errors = 0
                    if mailing.account_id not in self._me_cache:
                        self._me_cache[mailing.account_id] = await client.get_me()
                    me = self._me_cache[mailing.account_id]

                    # Free tier: check once per cycle
                    mailing_user = await self.db.get_user_by_id(mailing.user_id)
                    add_sig = Database.is_free_ad_active(mailing_user) if mailing_user else False
                    if add_sig and not any(not message.is_forward for message in messages):
                        self._clear_working_targets(mailing_id)
                        logger.debug(
                            f"Mailing {mailing_id}: all messages are forwards, "
                            "waiting for a sendable free-tier message"
                        )
                        await asyncio.sleep(60)
                        continue

                    for target_idx, target_obj in enumerate(targets):
                        # Interval check per target — jitter ±3s around the configured
                        # value so sends don't land on a perfectly regular cadence
                        # (a dead-even interval is itself a spam-pattern signal).
                        base_interval = target_obj.interval_seconds or mailing.interval_seconds
                        target_interval = max(1, base_interval + random.uniform(-3, 3))
                        if target_obj.last_sent_at is not None:
                            elapsed = (now - target_obj.last_sent_at).total_seconds()
                            if elapsed < target_interval:
                                if target_obj.last_account_id in (None, mailing.account_id):
                                    self._mark_target_working(mailing_id, target_obj.id)
                                logger.debug(f"Mailing {mailing_id}: {target_obj.chat_identifier} — interval not elapsed ({elapsed:.0f}/{target_interval}s), skip")
                                continue

                        # Stagger: new targets wait their offset before first send
                        if target_obj.last_sent_at is None:
                            stagger_ready = self._stagger_until.get(mailing_id, {}).get(target_obj.id, datetime.min)
                            if now < stagger_ready:
                                continue
                        current_account_id = mailing.account_id
                        target_client = client
                        target_me = me
                        target = target_obj.chat_identifier
                        if target.lstrip('-').isdigit():
                            target = int(target)
                        elif "t.me/+" in target:
                            # Keep invite links intact so _try_join_and_send()
                            # can use ImportChatInviteRequest.
                            pass
                        elif not target.startswith('@'):
                            target = f"@{target}"

                        if target_obj.last_sent_at is not None and not await self._has_new_activity_after_account_message(
                            target_client, target, target_me.id
                        ):
                            self._mark_target_not_working(mailing_id, target_obj.id)
                            continue

                        hidden_tag_user_ids = []
                        if mailing.hidden_tag_enabled and not add_sig:
                            hidden_tag_user_ids = await self._get_recent_user_ids(
                                target_client, target, target_me.id
                            )

                        sendable = [m for m in messages if not m.is_forward] if add_sig else messages
                        if not sendable:
                            logger.debug(f"Mailing {mailing_id}: all messages are forwards, skipping target for free-tier user")
                            continue
                        msg = random.choice(sendable)
                        pm = msg.parse_mode or 'html'
                        if pm == 'plain':
                            pm = None

                        reply_to_id = target_obj.thread_id  # use topic thread if set
                        if mailing.reply_mode:
                            try:
                                if mailing.reply_mode == 'last':
                                    offset = 0
                                elif mailing.reply_mode == 'fixed':
                                    offset = mailing.reply_offset - 1
                                else:  # random
                                    offset = random.randint(
                                        mailing.reply_random_min - 1,
                                        mailing.reply_random_max - 1
                                    )
                                msgs_list = await target_client.get_messages(target, limit=offset + 1)
                                if msgs_list and len(msgs_list) > offset:
                                    reply_to_id = msgs_list[offset].id
                            except Exception as e:
                                logger.warning(f"Mailing {mailing_id}: failed to get reply target: {e}")

                        if config.MAILING_DEBUG:
                            logger.info(
                                f"Mailing {mailing_id}: [{target_idx + 1}/{len(targets)}] "
                                f"→ {target} (account {current_account_id})"
                            )
                        else:
                            logger.debug(
                                f"Mailing {mailing_id}: [{target_idx + 1}/{len(targets)}] "
                                f"→ {target} (account {current_account_id})"
                            )
                        try:
                            await self._send_msg(
                                target_client, target, msg, pm,
                                reply_to=reply_to_id,
                                add_signature=add_sig,
                                hidden_tag_user_ids=hidden_tag_user_ids,
                            )
                            if config.MAILING_DEBUG:
                                logger.info(f"Mailing {mailing_id}: ✓ sent to {target} via account {current_account_id}")
                            else:
                                logger.debug(f"Mailing {mailing_id}: ✓ sent to {target} via account {current_account_id}")
                            await self.db.update_target_last_sent(target_obj.id, current_account_id)
                            self._mark_target_working(mailing_id, target_obj.id)
                            sent_any = True
                            cycle_sent += 1
                        except _BAN_ERRORS as e:
                            try:
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type=type(e).__name__,
                                    error_text=str(e)[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                )
                            except Exception:
                                pass
                            await self._handle_mailing_ban(mailing_id, current_account_id, e)
                            return
                        except _CHAT_BAN_ERRORS as e:
                            self._mark_target_not_working(mailing_id, target_obj.id)
                            cycle_errors += 1
                            try:
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type=type(e).__name__,
                                    error_text=str(e)[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                            except Exception:
                                pass
                            await self._handle_chat_ban(mailing_id, current_account_id, target_obj, e)
                        except _NOT_MEMBER_ERRORS as e:
                            try:
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type=type(e).__name__,
                                    error_text=str(e)[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                            except Exception:
                                pass
                            logger.info(f"Mailing {mailing_id}: not participant/forbidden in '{target}', attempting auto-join")
                            joined = await self._try_join_and_send(
                                target_client, target, target_obj, msg, pm,
                                mailing_id, current_account_id,
                                reply_to=reply_to_id,
                                add_signature=add_sig,
                                hidden_tag_user_ids=hidden_tag_user_ids,
                            )
                            if joined:
                                self._mark_target_working(mailing_id, target_obj.id)
                                sent_any = True
                            else:
                                self._mark_target_not_working(mailing_id, target_obj.id)
                        except SlowModeWaitError as e:
                            self._mark_target_not_working(mailing_id, target_obj.id)
                            try:
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type=type(e).__name__,
                                    error_text=f"{e.seconds}s",
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                            except Exception:
                                pass
                            logger.warning(f"Mailing {mailing_id}: slow mode in '{target}', wait {e.seconds}s")
                            await asyncio.sleep(min(e.seconds, 60))
                        except FloodWaitError as e:
                            self._clear_working_targets(mailing_id)
                            await self._notify_flood_wait(mailing_user, current_account_id, e.seconds)
                            cycle_errors += 1
                            wait = min(e.seconds, 3600)
                            logger.warning(
                                f"Mailing {mailing_id}: FloodWait {e.seconds}s on account "
                                f"{current_account_id} for {target} — sleeping {wait}s "
                                f"and deferring remaining targets to the next cycle"
                            )
                            try:
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type="FloodWait",
                                    error_text=f"{e.seconds}s",
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                            except Exception:
                                pass
                            await asyncio.sleep(wait)
                            # FloodWait is account-wide, not per-target — every
                            # remaining target in this cycle would immediately
                            # hit the same wait again, stalling for
                            # up_to_3600s and spamming duplicate flood alerts.
                            # Defer them to the next cycle instead.
                            break
                        except (ChatSendMediaForbiddenError, ChatGuestSendForbiddenError, RightForbiddenError, ChatAdminRequiredError) as e:
                            self._mark_target_not_working(mailing_id, target_obj.id)
                            await self.db.add_error_log(
                                user_id=mailing.user_id,
                                error_type=type(e).__name__,
                                error_text=str(e)[:300],
                                account_id=current_account_id,
                                mailing_id=mailing_id,
                                chat_identifier=target,
                            )
                            logger.warning(f"Mailing {mailing_id}: send forbidden in '{target}' ({type(e).__name__}) — chat restricts this message type, skipping")
                        except Exception as e:
                            err_str = str(e)
                            if isinstance(e, ValueError) and isinstance(target, str) and "t.me/+" in target and "not part of" in err_str:
                                # Telethon raises this locally (no RPC round-trip) when the
                                # invite link hasn't been joined yet — _NOT_MEMBER_ERRORS
                                # never fires here since it's not a Telegram RPCError.
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type="InviteNotJoined",
                                    error_text=err_str[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                                logger.info(f"Mailing {mailing_id}: invite link '{target}' not joined yet, attempting auto-join")
                                joined = await self._try_join_and_send(
                                    target_client, target, target_obj, msg, pm,
                                    mailing_id, current_account_id,
                                    reply_to=reply_to_id,
                                    add_signature=add_sig,
                                    hidden_tag_user_ids=hidden_tag_user_ids,
                                )
                                if joined:
                                    self._mark_target_working(mailing_id, target_obj.id)
                                    sent_any = True
                                else:
                                    self._mark_target_not_working(mailing_id, target_obj.id)
                            elif isinstance(e, ValueError) and "Could not find the input entity" in err_str:
                                self._mark_target_not_working(mailing_id, target_obj.id)
                                cycle_errors += 1
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type="EntityUnresolvable",
                                    error_text=err_str[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                                logger.warning(
                                    f"Mailing {mailing_id}: account never saw chat '{target}' — "
                                    f"can't resolve chat_id without access_hash, account must join/see it first"
                                )
                            elif "PLAIN_FORBIDDEN" in err_str or "SEND_PLAIN" in err_str:
                                self._mark_target_not_working(mailing_id, target_obj.id)
                                cycle_errors += 1
                                await self.db.add_error_log(
                                    user_id=mailing.user_id,
                                    error_type="PlainForbidden",
                                    error_text=err_str[:300],
                                    account_id=current_account_id,
                                    mailing_id=mailing_id,
                                    chat_identifier=target,
                                )
                                logger.warning(f"Mailing {mailing_id}: '{target}' — чат разрешает только медиа (PLAIN_FORBIDDEN), skipping")
                            elif "PEER_FLOOD" in err_str or isinstance(e, PeerFloodError):
                                self._clear_working_targets(mailing_id)
                                cycle_errors += 1
                                try:
                                    await self.db.add_error_log(
                                        user_id=mailing.user_id,
                                        error_type="PeerFlood",
                                        error_text="PEER_FLOOD",
                                        account_id=current_account_id,
                                        mailing_id=mailing_id,
                                        chat_identifier=target,
                                    )
                                except Exception:
                                    pass
                                logger.warning(f"Mailing {mailing_id}: PEER_FLOOD на аккаунте, пауза 60с")
                                await asyncio.sleep(60)
                                # Same reasoning as FloodWaitError above — this
                                # is account-wide, so stop hammering the rest
                                # of this cycle's targets and retry next cycle.
                                break
                            else:
                                self._mark_target_not_working(mailing_id, target_obj.id)
                                cycle_errors += 1
                                try:
                                    await self.db.add_error_log(
                                        user_id=mailing.user_id,
                                        error_type=type(e).__name__,
                                        error_text=str(e)[:300],
                                        account_id=current_account_id,
                                        mailing_id=mailing_id,
                                        chat_identifier=target,
                                    )
                                except Exception:
                                    pass
                                logger.error(f"Error sending mailing {mailing_id} to {target}: {e}")

                        await asyncio.sleep(3)

                    logger.info(
                        f"Mailing {mailing_id}: cycle done — sent {cycle_sent}, errors {cycle_errors}"
                    )

                    if sent_any:
                        await self.db.update_mailing_last_sent(mailing_id)

                    # Sleep until next possible send (min 30s, max 60s)
                    min_interval = min(
                        (t.interval_seconds or mailing.interval_seconds for t in targets),
                        default=mailing.interval_seconds,
                    )
                    sleep_time = max(30, min(60, min_interval // 10))
                    await asyncio.sleep(sleep_time)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error in mailing {mailing_id} iteration: {e}")
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info(f"Mailing {mailing_id} task cancelled")
        except Exception as e:
            self._clear_working_targets(mailing_id)
            logger.error(f"Mailing {mailing_id} loop fatal error: {e}", exc_info=True)
            self._tasks.pop(mailing_id, None)
            if self._running:
                await asyncio.sleep(30)
                try:
                    mailing = await self.db.get_mailing(mailing_id)
                    if mailing and mailing.is_active:
                        logger.info(f"Mailing {mailing_id} auto-restarting after fatal error")
                        # The current task was removed from _tasks above, so the
                        # normal serialized start path is safe here.
                        await self._start_mailing_task(mailing_id)
                except Exception as restart_err:
                    logger.error(f"Mailing {mailing_id} auto-restart failed: {restart_err}")
        finally:
            current_task = self._tasks.get(mailing_id)
            if current_task is asyncio.current_task():
                self._clear_working_targets(mailing_id)

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
                            pe(
                                f"⚠️ <b>Проблема с аккаунтом!</b>\n\n"
                                f"📱 Аккаунт: <b>{html.escape(account.display_name)}</b>\n"
                                f"❗️ Причина: {reason}\n\n"
                                f"Рассылка остановлена. Аккаунт отключён.\n"
                                f"Добавьте аккаунт заново в разделе «Аккаунты»."
                            ),
                        )
            except Exception as e:
                logger.error(f"Failed to notify about ban for account {account_id}: {e}")

    def _is_active_hours(self, active_hours_json: Optional[str]) -> bool:
        return is_within_active_hours(active_hours_json)

    async def _handle_chat_ban(self, mailing_id: int, account_id: int, target_obj, error: Exception):
        """Log chat-specific ban/restriction — target is kept, just skip this cycle."""
        chat = target_obj.chat_identifier
        error_name = type(error).__name__
        logger.warning(f"Mailing {mailing_id}: restricted in '{chat}' ({error_name}) — skipping this cycle, target kept")

    async def _try_join_and_send(
        self,
        client,
        target: str,
        target_obj,
        msg,
        pm,
        mailing_id: int,
        account_id: Optional[int] = None,
        reply_to=None,
        add_signature: bool = False,
        hidden_tag_user_ids: Optional[list[int]] = None,
    ) -> bool:
        """Try to join target channel/group, then retry sending. Returns True if message was sent."""
        mailing = await self.db.get_mailing(mailing_id)
        try:
            if isinstance(target, str) and "t.me/+" in target:
                joined = await client(ImportChatInviteRequest(target.rsplit("+", 1)[1]))
                if getattr(joined, "chats", None):
                    target = joined.chats[0]
            else:
                await client(JoinChannelRequest(target))
            logger.info(f"Mailing {mailing_id}: auto-joined '{target}'")
            await asyncio.sleep(2)
        except UserAlreadyParticipantError:
            logger.info(f"Mailing {mailing_id}: already participant in '{target}' — trying to send")
        except InviteRequestSentError as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            logger.info(f"Mailing {mailing_id}: join request sent/pending for '{target}'")
            return False
        except _BAN_ERRORS as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            await self._handle_mailing_ban(mailing_id, account_id, e)
            return False
        except FloodWaitError as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type="FloodWait",
                    error_text=f"{e.seconds}s",
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
                user = await self.db.get_user_by_id(mailing.user_id)
                await self._notify_flood_wait(user, account_id, e.seconds)
            await asyncio.sleep(min(e.seconds, 3600))
            return False
        except Exception as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            logger.warning(f"Mailing {mailing_id}: failed to join '{target}': {e}")
            return False

        try:
            await self._send_msg(
                client, target, msg, pm,
                reply_to=reply_to,
                add_signature=add_signature,
                hidden_tag_user_ids=hidden_tag_user_ids,
            )
            await self.db.update_target_last_sent(target_obj.id, account_id)
            logger.info(f"Mailing {mailing_id}: sent to '{target}' after auto-join")
            return True
        except _BAN_ERRORS as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            await self._handle_mailing_ban(mailing_id, account_id, e)
            return False
        except FloodWaitError as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type="FloodWait",
                    error_text=f"{e.seconds}s",
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
                user = await self.db.get_user_by_id(mailing.user_id)
                await self._notify_flood_wait(user, account_id, e.seconds)
            await asyncio.sleep(min(e.seconds, 3600))
            return False
        except ChatWriteForbiddenError as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            logger.warning(f"Mailing {mailing_id}: can't write to '{target}' after join — slow mode or temporary restriction, skipping")
            return False
        except Exception as e:
            if mailing:
                await self.db.add_error_log(
                    user_id=mailing.user_id,
                    error_type=type(e).__name__,
                    error_text=str(e)[:300],
                    account_id=account_id,
                    mailing_id=mailing_id,
                    chat_identifier=str(target),
                )
            logger.error(f"Mailing {mailing_id}: retry send to '{target}' failed after join: {e}")
            return False


class SubscriptionCheckerService:
    """Background service that stops mailings when subscription expires and notifies users."""

    def __init__(self, db: Database, mailing_service: MailingService):
        self.db = db
        self.mailing_service = mailing_service
        self._task: Optional[asyncio.Task] = None

    def start(self, bot):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(bot))
        logger.info("Subscription checker started")

    async def _loop(self, bot):
        while True:
            try:
                # Check first, then sleep — checking after the sleep left subscriptions that
                # expired shortly before a restart unenforced for up to an extra hour.
                await self._check(bot)
                await asyncio.sleep(3600)  # check every hour
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Subscription checker error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _send_reminder(self, bot, user, days: int):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💳 Продлить подписку", callback_data="subscription")
        ]])
        if days == 3:
            text = pe("⏰ <b>Подписка заканчивается через 3 дня!</b>\n\nПродлите заранее, чтобы не потерять доступ к функциям.")
        else:
            text = pe("❗️ <b>Подписка заканчивается завтра!</b>\n\nПродлите сейчас, чтобы не потерять доступ к функциям.")
        try:
            await bot.send_message(user.telegram_id, text, parse_mode="HTML", reply_markup=keyboard)
            await self.db.set_user_reminder_sent(user.id, days)
        except Exception as e:
            logger.warning(f"Failed to send {days}d reminder to {user.telegram_id}: {e}")

    async def _check(self, bot):
        users = await self.db.get_users_needing_subscription_check()
        now = now_moscow()
        await self.db.purge_inactive_accounts()
        for user in users:
            if not user.subscription_end:
                continue
            if Database.is_free_ad_active(user):
                continue
            delta = (user.subscription_end - now).total_seconds()

            # Subscription expired
            if delta < 0:
                stopped = await self.mailing_service.stop_user_mailings(user.id)
                await self.db.disable_user_autoresponders(user.id)
                if not user.subscription_expired_notified_at:
                    try:
                        if stopped > 0:
                            msg = pe(f"⚠️ <b>Ваша подписка истекла.</b>\n\n"
                                     f"⛔️ Остановлено рассылок: <b>{stopped}</b>\n"
                                     "Доступ к авторассылкам и автоответчику ограничен.\n\n"
                                     "🔄 Продлите подписку или включите бесплатный тариф\n"
                                     "для продолжения работы.")
                        else:
                            msg = pe("⚠️ <b>Ваша подписка истекла.</b>\n\n"
                                     "Доступ к авторассылкам и автоответчику ограничен.\n\n"
                                     "🔄 Продлите подписку или включите бесплатный тариф\n"
                                     "для продолжения работы.")
                        await bot.send_message(user.telegram_id, msg, parse_mode="HTML",
                                               reply_markup=subscription_expired_keyboard())
                        await self.db.set_subscription_expired_notified(user.id)
                    except Exception:
                        pass
                continue

            # Reminder 3 days before
            if 71 * 3600 < delta < 73 * 3600 and not user.reminder_3d_sent_at:
                await self._send_reminder(bot, user, 3)

            # Reminder 1 day before
            elif 23 * 3600 < delta < 25 * 3600 and not user.reminder_1d_sent_at:
                await self._send_reminder(bot, user, 1)


class InactivityCleanupService:
    """Background service that logs out and permanently deletes userbot
    accounts whose own mailing(s) have been stopped (and had no activity)
    for 15+ days. Scoped per-account, not per-user — a user's other,
    unrelated accounts (e.g. a brand-new one with no mailing yet) are never
    touched just because a different account of theirs went stale. No
    warning is sent — the account is just gone, same as if the user had
    deleted it themselves via the bot."""

    def __init__(self, db: Database, userbot_manager, days: int = 15):
        self.db = db
        self.userbot_manager = userbot_manager
        self.days = days
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Inactivity cleanup service started")

    async def _loop(self):
        while True:
            try:
                await self._check()
                await asyncio.sleep(3600)  # check every hour
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Inactivity cleanup error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _check(self):
        accounts = await self.db.get_stale_accounts(days=self.days)
        for account in accounts:
            try:
                await self.userbot_manager.logout_and_stop(account)
            except Exception as e:
                logger.warning(f"Inactivity cleanup: failed to log out account {account.id}: {e}")
            try:
                await self.db.delete_account_permanently(account.id)
            except Exception as e:
                logger.error(f"Inactivity cleanup: failed to delete account {account.id}: {e}")
                continue
            logger.info(
                f"Inactivity cleanup: removed account {account.id} (user_id={account.user_id}) "
                f"— no mailing activity in {self.days}+ days"
            )
