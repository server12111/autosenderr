import asyncio
import logging
import json
import os
import random
import re
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
    UserNotParticipantError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    UserAlreadyParticipantError,
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

from .database.db import Database
from .utils.time_utils import is_within_active_hours
from .utils.premium_emoji import pe

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
            notification = pe(
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

    async def handle_sponsor_check(self, event, account):
        """
        Detect messages in groups that mention this account and contain
        URL buttons with t.me/ links (sponsor subscription gates). Auto-join those channels.
        """
        if not account.auto_subscribe_sponsors:
            return

        try:
            # 1. Check buttons FIRST — fast exit if none
            if not event.message.buttons:
                return

            # 2. Collect channel links from buttons (public @username and private invite hashes)
            channel_usernames = []   # @username → JoinChannelRequest
            invite_hashes = []       # hash → ImportChatInviteRequest

            for row in event.message.buttons:
                for button in row:
                    url = getattr(button, 'url', None) or ""
                    if not url:
                        continue

                    # Format 1: t.me/+HASH or t.me/joinchat/HASH (private invite)
                    m = re.search(r't\.me/(?:joinchat/|\+)([A-Za-z0-9_\-]+)', url)
                    if m:
                        h = m.group(1)
                        if h not in invite_hashes:
                            invite_hashes.append(h)
                        continue

                    # Format 2: t.me/username (public channel)
                    m = re.search(r't\.me/([A-Za-z0-9_]+)', url)
                    if m:
                        username = m.group(1)
                        if username not in channel_usernames:
                            channel_usernames.append(username)
                        continue

                    # Format 3: tg://resolve?domain=username
                    m = re.search(r'tg://resolve\?domain=([A-Za-z0-9_]+)', url)
                    if m:
                        username = m.group(1)
                        if username not in channel_usernames:
                            channel_usernames.append(username)

            if not channel_usernames and not invite_hashes:
                return

            # 3. Check if message is relevant to our account
            me = await event.client.get_me()
            msg_text = (event.message.text or event.message.message or "").lower()
            me_username = (me.username or "").lower()
            is_relevant = False

            # 3a. @username present in text
            if me_username and f"@{me_username}" in msg_text:
                is_relevant = True

            # 3b. Entity mentions: MessageEntityMentionName (by user_id) or MessageEntityMention (@username in text)
            if not is_relevant and event.message.entities:
                from telethon.tl.types import MessageEntityMentionName, MessageEntityMention
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityMentionName) and entity.user_id == me.id:
                        is_relevant = True
                        break
                    if isinstance(entity, MessageEntityMention):
                        raw = (event.message.text or "")[entity.offset:entity.offset + entity.length]
                        if me_username and raw.lstrip('@').lower() == me_username:
                            is_relevant = True
                            break

            # 3c. Fallback: if sender is a bot, consider relevant anyway
            if not is_relevant:
                try:
                    sender = await event.get_sender()
                    if sender and getattr(sender, 'bot', False):
                        is_relevant = True
                except Exception:
                    pass

            if not is_relevant:
                return

            # 4. Subscribe to detected channels
            logger.info(f"Account {account.phone}: sponsor gate detected, public={channel_usernames} invites={invite_hashes}")
            for username in channel_usernames:
                try:
                    await event.client(JoinChannelRequest(f"@{username}"))
                    logger.info(f"Account {account.phone} auto-joined sponsor @{username}")
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Account {account.phone} failed to join @{username}: {e}")

            for h in invite_hashes:
                try:
                    await event.client(ImportChatInviteRequest(h))
                    logger.info(f"Account {account.phone} auto-joined sponsor via invite hash {h}")
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Account {account.phone} failed to join invite {h}: {e}")

        except Exception as e:
            logger.error(f"Sponsor check error for account {account.phone}: {e}")


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
    return result


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

    async def _send_msg(self, client, target: str, msg, pm: Optional[str]) -> None:
        """Send one mailing message to target (forward / text / photo)."""
        if msg.is_forward:
            peer = int(msg.forward_peer) if msg.forward_peer.lstrip('-').isdigit() else msg.forward_peer
            entity = await client.get_entity(peer)
            await client.forward_messages(
                target, [msg.forward_msg_id], from_peer=entity
            )
            return

        entities = _build_telethon_entities(msg.entities_json) if msg.entities_json else None
        text = msg.text or None
        eff_pm = None if entities else pm  # use entities directly — skip parse_mode

        photos = [p for p in msg.photo_paths if os.path.exists(p)]
        if len(photos) > 1:
            await client.send_file(target, photos, caption=text, parse_mode=eff_pm,
                                   formatting_entities=entities)
        elif len(photos) == 1:
            await client.send_file(target, photos[0], caption=text, parse_mode=eff_pm,
                                   formatting_entities=entities)
        else:
            await client.send_message(target, text, parse_mode=eff_pm,
                                      formatting_entities=entities)

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

                    now = datetime.utcnow()
                    sent_any = False
                    me = await client.get_me()

                    for target_obj in targets:
                        # Each target uses its own interval or falls back to mailing default
                        target_interval = target_obj.interval_seconds or mailing.interval_seconds
                        if target_obj.last_sent_at is not None:
                            elapsed = (now - target_obj.last_sent_at).total_seconds()
                            if elapsed < target_interval:
                                continue  # Not due yet for this target

                        target = target_obj.chat_identifier
                        if not target.startswith('-') and not target.startswith('@') and not target.isdigit():
                            target = f"@{target}"

                        # Перевіряємо активність у чаті після останньої відправки
                        if target_obj.last_sent_at is not None:
                            has_activity = await self._has_chat_activity(client, target, target_obj.last_sent_at, me.id)
                            if not has_activity:
                                logger.info(f"Mailing {mailing_id}: no activity in {target} since last send, skipping")
                                continue

                        msg = random.choice(messages)
                        pm = msg.parse_mode or 'html'
                        if pm == 'plain':
                            pm = None

                        try:
                            await self._send_msg(client, target, msg, pm)
                            logger.info(f"Mailing {mailing_id} sent to {target}")
                            await self.db.update_target_last_sent(target_obj.id)
                            sent_any = True
                        except _BAN_ERRORS as e:
                            await self._handle_mailing_ban(mailing_id, mailing.account_id, e)
                            return
                        except _CHAT_BAN_ERRORS as e:
                            await self._handle_chat_ban(mailing_id, mailing.account_id, target_obj, e)
                        except _NOT_MEMBER_ERRORS:
                            logger.info(f"Mailing {mailing_id}: not participant/forbidden in '{target}', attempting auto-join")
                            joined = await self._try_join_and_send(client, target, target_obj, msg, pm, mailing_id, mailing.account_id)
                            if joined:
                                sent_any = True
                        except FloodWaitError as e:
                            logger.warning(f"FloodWait {e.seconds}s for mailing {mailing_id}")
                            await asyncio.sleep(e.seconds)
                        except Exception as e:
                            logger.error(f"Error sending mailing {mailing_id} to {target}: {e}")

                        await asyncio.sleep(3)

                    if sent_any:
                        await self.db.update_mailing_last_sent(mailing_id)

                    # Sleep until next possible send (min 30s, max 60s)
                    min_interval = min(
                        t.interval_seconds or mailing.interval_seconds for t in targets
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
                            pe(
                                f"⚠️ <b>Проблема с аккаунтом!</b>\n\n"
                                f"📱 Аккаунт: <b>{account.display_name}</b>\n"
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
        """Notify owner about chat-specific ban/mute, remove target from mailing."""
        from .database.db import MailingTarget
        chat = target_obj.chat_identifier
        error_name = type(error).__name__

        if isinstance(error, UserBannedInChannelError):
            reason = "🚫 аккаунт забанен в этом чате"
        else:
            reason = "🔇 аккаунт замьючен или нет прав писать"

        logger.warning(f"Mailing {mailing_id}: chat ban on '{chat}': {error_name}")

        try:
            await self.db.delete_mailing_target(target_obj.id)
        except Exception as e:
            logger.error(f"Failed to delete banned target {target_obj.id}: {e}")

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
                                f"⚠️ <b>Проблема с рассылкой!</b>\n\n"
                                f"📱 Аккаунт: <b>{account.display_name}</b>\n"
                                f"💬 Чат: <code>{chat}</code>\n"
                                f"❗️ {reason}\n\n"
                                f"Цель автоматически удалена из рассылки."
                            ),
                        )
            except Exception as e:
                logger.error(f"Failed to notify about chat ban for account {account_id}: {e}")

    async def _try_join_and_send(self, client, target: str, target_obj, msg, pm, mailing_id: int, account_id: Optional[int] = None) -> bool:
        """Try to join target channel/group, then retry sending. Returns True if message was sent."""
        try:
            await client(JoinChannelRequest(target))
            logger.info(f"Mailing {mailing_id}: auto-joined '{target}'")
            await asyncio.sleep(2)
        except UserAlreadyParticipantError as e:
            # Account IS already a member but can't write → muted/restricted
            logger.warning(f"Mailing {mailing_id}: already participant in '{target}' but write forbidden — muted/restricted")
            if account_id is not None:
                await self._handle_chat_ban(mailing_id, account_id, target_obj, e)
            return False
        except Exception as e:
            logger.warning(f"Mailing {mailing_id}: failed to join '{target}': {e}")
            return False

        try:
            await self._send_msg(client, target, msg, pm)
            await self.db.update_target_last_sent(target_obj.id)
            logger.info(f"Mailing {mailing_id}: sent to '{target}' after auto-join")
            return True
        except ChatWriteForbiddenError as e:
            # Joined successfully but still can't write → muted/restricted
            logger.warning(f"Mailing {mailing_id}: still can't write to '{target}' after join — muted/restricted")
            if account_id is not None:
                await self._handle_chat_ban(mailing_id, account_id, target_obj, e)
            return False
        except Exception as e:
            logger.error(f"Mailing {mailing_id}: retry send to '{target}' failed after join: {e}")
            return False


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
                            pe("⚠️ <b>Ваша подписка истекла.</b>\n\n"
                            f"Остановлено {stopped} рассылок.\n"
                            "Продлите подписку в разделе «Подписка», чтобы возобновить работу."),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
