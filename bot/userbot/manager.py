import asyncio
import hashlib
import html
import logging
import random
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

from ..config import config
from ..database.db import Database, Account
from ..utils.delivery_journal import DeliveryJournal
from ..utils.premium_emoji import pe


logger = logging.getLogger(__name__)

_DEVICE_POOL = [
    {"device_model": "Samsung Galaxy A54",   "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "Xiaomi Redmi Note 12", "system_version": "Android 12", "app_version": "10.13.2"},
    {"device_model": "Samsung Galaxy S23",   "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "OPPO A78",             "system_version": "Android 12", "app_version": "10.12.4"},
    {"device_model": "Xiaomi 13T",           "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "Samsung Galaxy A34",   "system_version": "Android 13", "app_version": "10.13.0"},
    {"device_model": "Samsung Galaxy S22",   "system_version": "Android 12", "app_version": "10.12.0"},
    {"device_model": "Xiaomi Redmi Note 11", "system_version": "Android 11", "app_version": "10.11.4"},
    {"device_model": "Xiaomi Poco X5",       "system_version": "Android 13", "app_version": "10.14.0"},
    {"device_model": "OPPO Reno8",           "system_version": "Android 13", "app_version": "10.13.5"},
    {"device_model": "Vivo V27",             "system_version": "Android 13", "app_version": "10.14.2"},
    {"device_model": "realme 10",            "system_version": "Android 12", "app_version": "10.12.6"},
    {"device_model": "OnePlus Nord 3",       "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "Huawei P50",           "system_version": "Android 12", "app_version": "10.12.4"},
    {"device_model": "iPhone 13",            "system_version": "iOS 17.5",   "app_version": "10.9.2"},
    {"device_model": "iPhone 12",            "system_version": "iOS 17.4",   "app_version": "10.8.1"},
    {"device_model": "iPhone 14",            "system_version": "iOS 17.5",   "app_version": "10.9.2"},
]

# Phone country calling code (longest prefix wins) -> (lang_code, system_lang_code).
# A userbot claiming Ukrainian locale on an Indian or Colombian number is a
# mismatch anti-fraud systems can flag; matching locale to the account's own
# number is a much more natural signal. Falls back to English.
_PHONE_LOCALE = {
    "7": ("ru", "ru"),          # Russia/Kazakhstan
    "375": ("ru", "ru"),        # Belarus
    "380": ("uk", "uk-UA"),     # Ukraine
    "1": ("en", "en-US"),       # US/Canada
    "44": ("en", "en-GB"),      # UK
    "91": ("en", "en-IN"),      # India
    "57": ("es", "es-CO"),      # Colombia
    "52": ("es", "es-MX"),      # Mexico
    "34": ("es", "es-ES"),      # Spain
    "55": ("pt", "pt-BR"),      # Brazil
    "351": ("pt", "pt-PT"),     # Portugal
    "62": ("id", "id-ID"),      # Indonesia
    "63": ("en", "en-PH"),      # Philippines
    "84": ("vi", "vi-VN"),      # Vietnam
    "95": ("en", "en-MM"),      # Myanmar
    "234": ("en", "en-NG"),     # Nigeria
    "998": ("uz", "uz-UZ"),     # Uzbekistan
    "996": ("ru", "ru-KG"),     # Kyrgyzstan
    "992": ("ru", "ru-TJ"),     # Tajikistan
}
_PHONE_LOCALE_PREFIXES = tuple(sorted(_PHONE_LOCALE, key=len, reverse=True))


def _locale_for_phone(phone: Optional[str]) -> tuple[str, str]:
    digits = (phone or "").lstrip("+").strip()
    for prefix in _PHONE_LOCALE_PREFIXES:
        if digits.startswith(prefix):
            return _PHONE_LOCALE[prefix]
    return ("en", "en-US")


def _device_for_account(account_id: int) -> dict:
    # A plain `account_id % len(pool)` makes every Nth account share an identical
    # device fingerprint in a trivially predictable pattern (accounts 1 and 6, 2
    # and 7, ...), which two concurrently-running accounts would then broadcast at
    # the same time. Hashing spreads the (still finite) pool assignment so it isn't
    # a simple arithmetic sequence, while staying stable across restarts.
    digest = hashlib.md5(str(account_id).encode()).digest()
    return _DEVICE_POOL[digest[0] % len(_DEVICE_POOL)]


def _parse_proxy(proxy_str: Optional[str]) -> Optional[tuple]:
    """Parse 'socks5://[user:pass@]host:port' into Telethon proxy tuple.

    Raises ValueError if a proxy was configured but couldn't be parsed — the
    caller must treat that as a hard failure rather than silently connecting
    without it, since a userbot account explicitly configured to use a proxy
    should never fall back to the server's real IP unnoticed.
    """
    if not proxy_str:
        return None
    import socks
    p = urlparse(proxy_str)
    host = p.hostname
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError("invalid proxy string (invalid port)") from None
    username = p.username or None
    password = p.password or None
    if not host or not port:
        # This error is logged by callers, so never include proxy_str: it may
        # contain SOCKS credentials.
        raise ValueError("invalid proxy string (missing host/port)")
    if username and password:
        return (socks.SOCKS5, host, port, True, username, password)
    return (socks.SOCKS5, host, port)

# Exceptions that mean the account is banned/deactivated/session killed
_BAN_ERRORS = (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)

_MONITOR_INTERVAL = 1800  # seconds between health checks (30 min)


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
        self._startup_task: Optional[asyncio.Task] = None
        self._sponsor_check_handler: Optional[Callable] = None
        self._client_locks: dict[int, asyncio.Lock] = {}
        self._proxy_failure_lock = asyncio.Lock()
        self._account_proxy_failure_callback: Optional[Callable] = None
        self._pending_proxy_problem_marks: set[int] = set()
        self._delivery_journal = DeliveryJournal(db.db_path)

    def set_message_handler(self, handler: Callable):
        self._message_handler = handler

    def set_group_reply_handler(self, handler: Callable):
        self._group_reply_handler = handler

    def set_bot_notify_callback(self, callback: Callable):
        self._bot_notify_callback = callback

    def set_account_proxy_failure_callback(self, callback: Callable):
        """callback(account_id) is awaited once (not on every retry) the
        first time an account's own proxy fails to connect."""
        self._account_proxy_failure_callback = callback

    def set_sponsor_check_handler(self, handler: Callable):
        self._sponsor_check_handler = handler

    async def start_client(self, account: Account) -> Optional[TelegramClient]:
        lock = self._client_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            return await self._start_client_unlocked(account)

    async def _start_client_unlocked(self, account: Account) -> Optional[TelegramClient]:
        if account.id in self._clients:
            client = self._clients[account.id]
            if client.is_connected():
                return client
            # Old client is disconnected — clean it up before creating a new one
            try:
                await client.disconnect()
            except Exception:
                pass
            del self._clients[account.id]

        try:
            proxy = _parse_proxy(account.proxy)
        except Exception as e:
            logger.error(f"Invalid proxy for account {account.phone}: {e}")
            if account.proxy_pool_id:
                await self._handle_pool_proxy_failure(account.proxy_pool_id, str(e))
            else:
                await self._notify_proxy_problem(account, str(e))
            return None

        client = None
        try:
            device = _device_for_account(account.id)
            lang_code, system_lang_code = _locale_for_phone(account.phone)
            client = TelegramClient(
                StringSession(account.session_string),
                account.api_id,
                account.api_hash,
                proxy=proxy,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code=lang_code,
                system_lang_code=system_lang_code,
                connection_retries=5,
                retry_delay=10,
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

            # The client is fully connected and authorized at this point —
            # a failure in either of these two bookkeeping writes must not
            # tear down an otherwise-working session (the except Exception
            # below pops it back out of self._clients and disconnects it,
            # which used to happen here for what's just a DB hiccup).
            try:
                await self.db.clear_proxy_problem_notified(account.id)

                fresh_session = client.session.save()
                if fresh_session != account.session_string:
                    await self.db.update_account_session(account.id, fresh_session)
            except Exception as e:
                logger.warning(f"Post-connect bookkeeping failed for account {account.id}, continuing anyway: {e}")

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
                    if self._sponsor_check_handler and fresh_account.auto_subscribe_sponsors and not event.is_private:
                        await self._sponsor_check_handler(event, fresh_account, me_id)

                except Exception as e:
                    logger.error(f"Error in message handler for account {account_id}: {e}", exc_info=True)

            logger.info(f"Started client for account {account.phone} (ID: {account.id})")
            return client

        except _BAN_ERRORS as e:
            # _start_client_unlocked() is called while the account lock is held.
            # Do not acquire the same lock again from the failure handler.
            await self._handle_account_problem(account.id, e, lock_held=True)
            self._clients.pop(account.id, None)
            self._me_ids.pop(account.id, None)
            try:
                if client is not None:
                    await client.disconnect()
                await asyncio.sleep(0)
            except Exception:
                pass
            return None
        except (OSError, asyncio.TimeoutError, ConnectionError) as e:
            logger.error(f"Proxy connection failed for {account.phone}: {e}")
            self._clients.pop(account.id, None)
            self._me_ids.pop(account.id, None)
            try:
                if client is not None:
                    await client.disconnect()
                await asyncio.sleep(0)
            except Exception:
                pass
            if account.proxy_pool_id:
                await self._handle_pool_proxy_failure(account.proxy_pool_id, str(e))
            elif account.proxy:
                await self._notify_proxy_problem(account, str(e))
            return None
        except Exception as e:
            logger.error(f"Error starting client for {account.phone}: {e}")
            self._clients.pop(account.id, None)
            self._me_ids.pop(account.id, None)
            try:
                if client is not None:
                    await client.disconnect()
                await asyncio.sleep(0)
            except Exception:
                pass
            return None

    async def _notify_proxy_problem(self, account: Account, error_text: str):
        # A dead own-proxy has no known end time (unlike a FloodWait), so
        # get_client() would otherwise keep retrying every cycle forever —
        # notify once, stop the mailing(s) using this account, and wait for
        # the owner to fix the proxy and restart manually instead of
        # spamming the same message on every retry. Persisted in the DB
        # (not just in-memory) — a bot restart, which happens on every
        # deploy, must not forget this account was already notified and
        # re-send the same alert again on the very next reconnect attempt.
        #
        # Everything in this function is wrapped — this runs from inside
        # _start_client_unlocked's own exception handlers, and a caller
        # (start_client, the monitor loop, a batch reconnect) shouldn't
        # have its own error handling short-circuited by a failure in what
        # is fundamentally just a best-effort notification side effect.
        try:
            journal_key = f"proxy-problem:{account.id}"
            if await self.db.is_proxy_problem_notified(account.id):
                await self._forget_delivery_journal(journal_key)
                return

            # The alert may have reached Telegram immediately before bot.db
            # became unavailable.  This independent journal survives a bot
            # restart, so repair the main marker without delivering twice.
            journal_contains = await self._delivery_journal_contains(journal_key)
            if journal_contains is None:
                # A corrupt/unreadable journal may contain the only proof of
                # a delivered alert.  Defer rather than risk a duplicate.
                return
            if journal_contains:
                if await self._mark_proxy_problem_notified_after_send(account.id):
                    await self._forget_delivery_journal(journal_key)
                return

            if account.id in self._pending_proxy_problem_marks:
                if await self._mark_proxy_problem_notified_after_send(account.id):
                    self._pending_proxy_problem_marks.discard(account.id)
                    await self._forget_delivery_journal(journal_key)
                return

            if self._account_proxy_failure_callback:
                try:
                    await self._account_proxy_failure_callback(account.id)
                except Exception as e:
                    logger.error(f"Account proxy failure callback failed for account {account.id}: {e}")

            if not self._bot_notify_callback:
                return
            user = await self.db.get_user_by_id(account.user_id)
            if not user:
                return
            parsed_proxy = urlparse(account.proxy) if account.proxy else None
            proxy_display = (
                f"socks5://{parsed_proxy.hostname}:{parsed_proxy.port}"
                if parsed_proxy and parsed_proxy.hostname and parsed_proxy.port
                else "настроенный SOCKS5-прокси"
            )
            await self._bot_notify_callback(
                user.telegram_id,
                pe(
                    f"⚠️ <b>Проблема с прокси!</b>\n\n"
                    f"📱 Аккаунт: <b>{html.escape(account.display_name)}</b>\n"
                    f"❌ Не удалось подключиться через прокси <code>{html.escape(proxy_display)}</code>.\n\n"
                    f"Рассылка через этот аккаунт остановлена.\n\n"
                    f"Чтобы возобновить работу:\n"
                    f"1. Зайдите в раздел «Аккаунты»\n"
                    f"2. Откройте аккаунт и установите рабочий прокси\n"
                    f"3. Запустите рассылку заново"
                ),
            )
            # Only mark as notified once the alert actually went out — a
            # failed send here (transient network blip) must not
            # permanently silence every future notification attempt for
            # this account, unlike the dedup check above which is meant to
            # stop *repeat* alerts once one has actually landed.
            journal_saved = await self._remember_delivery_journal(journal_key)
            if not await self._mark_proxy_problem_notified_after_send(account.id):
                self._pending_proxy_problem_marks.add(account.id)
                if not journal_saved:
                    logger.critical(
                        "Delivered proxy alert for account %s has no durable dedup marker; "
                        "a restart can duplicate it until storage is repaired",
                        account.id,
                    )
            else:
                await self._forget_delivery_journal(journal_key)
        except Exception as ne:
            logger.error(f"Failed to notify about proxy error for account {account.id}: {ne}")

    async def _delivery_journal_contains(self, key: str) -> bool | None:
        try:
            return await self._delivery_journal.contains(key)
        except Exception as exc:
            logger.error("Could not read notification delivery journal for %s; notification deferred: %s", key, exc)
            return None

    async def _remember_delivery_journal(self, key: str) -> bool:
        try:
            await self._delivery_journal.remember(key)
            return True
        except Exception as exc:
            # Continue to the main DB retries; this is an extra safety net,
            # not a reason to report a delivered notification as unsent.
            logger.error("Could not journal delivered notification %s: %s", key, exc)
            return False

    async def _forget_delivery_journal(self, key: str) -> None:
        try:
            await self._delivery_journal.forget(key)
        except Exception as exc:
            logger.warning("Could not clean notification delivery journal %s: %s", key, exc)

    async def _mark_proxy_problem_notified_after_send(self, account_id: int) -> bool:
        """Retry the durable dedup marker after an alert has actually been
        delivered; callers keep an in-memory pending marker if SQLite remains
        unavailable so retries never duplicate that delivered alert."""
        for attempt in range(3):
            try:
                await self.db.mark_proxy_problem_notified(account_id)
                return True
            except Exception as exc:
                if attempt == 2:
                    logger.error("Could not mark proxy alert sent for account %s: %s", account_id, exc)
                    return False
                await asyncio.sleep(1 + attempt)

    async def _handle_pool_proxy_failure(self, dead_pool_id: int, error_text: str):
        """A pool-assigned proxy died. Unlike a user's own proxy, the account
        owner must never learn their account rides on a shared pool proxy — so
        this notifies the admins instead, marks the proxy dead, reassigns
        EVERY account still on it (not just the one whose connection attempt
        happened to discover the failure — otherwise the rest sit stuck on
        the dead proxy until each independently hits the same error), and
        forces those accounts to reconnect right away instead of waiting for
        their own next natural reconnect attempt.

        Takes the pool proxy id directly (not an Account) so it can also be
        called during onboarding, before the new account even has a DB row —
        without this, a brand-new account that happened to get assigned a
        proxy that's dead but not yet detected as such would just time out
        during signup with no reassignment happening, and the very next
        signup attempt would likely land on that same still-"active" dead
        proxy again (nothing else would have marked it dead in the meantime).

        Serialized via _proxy_failure_lock: several accounts sharing the
        same dead proxy can all discover the failure at nearly the same
        time (e.g. a burst of connections at startup) and call this
        concurrently — without a lock, each would independently re-read the
        live pool-proxy load and could all pick the same "least loaded"
        replacement, pushing it over POOL_PROXY_ACCOUNT_CAP, and each would
        schedule its own reconnect batch, multiplying the intended
        concurrency-5 stagger. The lock makes only the first caller for a
        given dead proxy actually do the work; the rest see was_active=False
        and skip — that work is already in flight."""
        async with self._proxy_failure_lock:
            try:
                was_active = await self.db.deactivate_pool_proxy(dead_pool_id)
            except Exception as ne:
                logger.error(f"Failed to deactivate pool proxy {dead_pool_id}: {ne}")
                return

            # A previous attempt may already have marked the proxy inactive
            # and then failed while moving the accounts.  In that case do not
            # send another admin alert, but do retry the idempotent reassignment.
            if was_active and self._bot_notify_callback:
                for admin_id in config.ADMIN_IDS:
                    try:
                        await self._bot_notify_callback(
                            admin_id,
                            pe(
                                f"⚠️ <b>Прокси из пула отключён</b>\n\n"
                                f"Перестал работать и помечен неактивным. Затронутые аккаунты "
                                f"переподключаются на другой прокси из пула.\n\n"
                                f"{html.escape(error_text[:200])}"
                            ),
                        )
                    except Exception:
                        pass

            affected_ids: list[int] = []
            try:
                affected_ids = await self.db.reassign_all_accounts_off_dead_pool_proxy(dead_pool_id)
            except Exception as ne:
                logger.error(f"Failed to reassign accounts off dead pool proxy {dead_pool_id}: {ne}")

        if affected_ids:
            asyncio.create_task(self._reconnect_accounts_staggered(affected_ids))

    async def _reconnect_accounts_staggered(self, account_ids: list[int]):
        """Force accounts that were just reassigned off a dead pool proxy to
        pick up their new proxy immediately, instead of waiting for whatever
        naturally calls get_client() next (could be up to _MONITOR_INTERVAL
        away). Staggered/limited concurrency for the same reason
        start_all_clients() is — avoid a burst of simultaneous outbound
        proxy connections."""
        semaphore = asyncio.Semaphore(5)

        async def _reconnect_one(account_id: int):
            async with semaphore:
                await asyncio.sleep(random.uniform(0.3, 1.5))
                try:
                    await self.stop_client(account_id)
                    fresh = await self.db.get_account(account_id)
                    if fresh and fresh.is_active:
                        await self.start_client(fresh)
                except Exception as e:
                    logger.warning(f"Failed to reconnect account {account_id} onto new pool proxy: {e}")

        await asyncio.gather(*[_reconnect_one(aid) for aid in account_ids], return_exceptions=True)

    async def stop_client(self, account_id: int):
        lock = self._client_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            client = self._clients.pop(account_id, None)
            if client:
                await client.disconnect()
                self._me_ids.pop(account_id, None)
                logger.info(f"Stopped client for account {account_id}")

    async def logout_and_stop(self, account):
        async def _do():
            lock = self._client_locks.setdefault(account.id, asyncio.Lock())
            async with lock:
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
                    proxy = _parse_proxy(account.proxy)
                    client = TelegramClient(
                        StringSession(account.session_string), account.api_id, account.api_hash,
                        proxy=proxy,
                        connection_retries=3,
                        retry_delay=5,
                    )
                    try:
                        await client.connect()
                        await client.log_out()
                    except Exception as e:
                        logger.warning(f"Failed to log out account {account.id}: {e}")
                    finally:
                        # connect()/log_out() can be cancelled by the
                        # wait_for timeout below (CancelledError is a
                        # BaseException, not caught above) — disconnect
                        # here unconditionally so the socket isn't leaked.
                        try:
                            await client.disconnect()
                        except Exception:
                            pass

        try:
            # The account lock can be held for a long time by the daily
            # DeadAccountCleanupService sweep verifying this same account
            # against a slow/dead proxy (up to ~5 connection retries).
            # Without a bound here, a user deleting that exact account
            # would just hang — the DB-side deletion (delete_account) still
            # proceeds regardless of the outcome here, so it's safe to give
            # up on the Telegram-side logout rather than block on it.
            await asyncio.wait_for(_do(), timeout=20)
        except asyncio.TimeoutError:
            logger.warning(
                f"logout_and_stop: timed out waiting for account {account.id} "
                f"(likely busy with a background check) — proceeding without confirmed Telegram-side logout"
            )

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
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Account monitor started")

    async def _monitor_loop(self):
        while True:
            try:
                await asyncio.sleep(_MONITOR_INTERVAL)
                for account_id in list(self._clients.keys()):
                    # Hold the same per-account lock start_client()/logout_and_stop()
                    # use, so this loop never reconnects/touches a client that's
                    # concurrently being replaced or torn down elsewhere — without
                    # it, a race here could spin up a second live Telethon session
                    # for the same account (ban risk) or overwrite a fresher
                    # session_string with a stale one.
                    lock = self._client_locks.setdefault(account_id, asyncio.Lock())
                    async with lock:
                        client = self._clients.get(account_id)
                        if client is None:
                            continue  # removed/replaced while we waited for the lock
                        try:
                            if not client.is_connected():
                                await client.connect()
                            await client.get_me()
                            try:
                                fresh_session = client.session.save()
                                account = await self.db.get_account(account_id)
                                if account and fresh_session != account.session_string:
                                    await self.db.update_account_session(account_id, fresh_session)
                            except Exception:
                                pass
                        except _BAN_ERRORS as e:
                            await self._handle_account_problem(account_id, e, lock_held=True)
                        except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                            logger.warning(f"Monitor reconnect failed for account {account_id}: {e}")
                            account = await self.db.get_account(account_id)
                            if account:
                                if account.proxy_pool_id:
                                    await self._handle_pool_proxy_failure(account.proxy_pool_id, str(e))
                                elif account.proxy:
                                    await self._notify_proxy_problem(account, str(e))
                        except Exception as e:
                            logger.warning(f"Monitor check failed for account {account_id}: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Monitor loop iteration failed: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _handle_account_problem(self, account_id: int, error: Exception, *, lock_held: bool = False):
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

        # Remove from active clients under the same lock used by start_client.
        # start_client() already owns it when this method is called from its
        # exception path, so acquiring it again there would deadlock.
        lock = self._client_locks.setdefault(account_id, asyncio.Lock())
        if lock_held:
            old_client = self._clients.pop(account_id, None)
            self._me_ids.pop(account_id, None)
            if old_client:
                try:
                    await old_client.disconnect()
                except Exception:
                    logger.warning("Failed to disconnect broken client for account %s", account_id)
        else:
            async with lock:
                old_client = self._clients.pop(account_id, None)
                self._me_ids.pop(account_id, None)
                if old_client:
                    try:
                        await old_client.disconnect()
                    except Exception:
                        logger.warning("Failed to disconnect broken client for account %s", account_id)

        # Notify owner
        if self._bot_notify_callback:
            try:
                account = await self.db.get_account(account_id)
                if account:
                    user = await self.db.get_user_by_id(account.user_id)
                    if user:
                        await self._bot_notify_callback(
                            user.telegram_id,
                            pe(
                                f"⚠️ <b>Проблема с аккаунтом!</b>\n\n"
                                f"📱 Аккаунт: <b>{html.escape(account.display_name)}</b>\n"
                                f"❗️ Причина: {reason}\n\n"
                                f"Аккаунт отключён. Добавьте его заново в разделе «Аккаунты»."
                            ),
                        )
            except Exception as e:
                logger.error(f"Failed to notify about account {account_id} problem: {e}")

    async def start_all_clients(self, background: bool = False):
        accounts = await self.db.get_needed_accounts()
        if not accounts:
            logger.info("No accounts need connection at startup")
            return

        semaphore = asyncio.Semaphore(10)

        async def _start(account):
            if account.proxy:
                # Spread out proxied connections instead of firing them all
                # in the same instant — many simultaneous outbound
                # connections to non-standard SOCKS5 ports (vs. Telegram's
                # normal 443) is exactly the pattern datacenter/PaaS hosts
                # flag as abuse and respond to by blocking the container's
                # IP outright, which would break every account, proxied or
                # not, not just the ones actually misbehaving.
                await asyncio.sleep(random.uniform(0.3, 1.5))
            async with semaphore:
                return await self.start_client(account)

        async def _run_all():
            results = await asyncio.gather(*[_start(a) for a in accounts], return_exceptions=True)
            started = sum(1 for r in results if r is not None and not isinstance(r, Exception))
            logger.info(f"Startup complete: {started}/{len(accounts)} accounts active")

        if background:
            def _on_startup_done(t: asyncio.Task):
                if not t.cancelled():
                    exc = t.exception()
                    if exc:
                        logger.error(
                            f"Account startup task failed: {exc}",
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )

            self._startup_task = asyncio.create_task(_run_all(), name="startup_clients")
            self._startup_task.add_done_callback(_on_startup_done)
        else:
            await _run_all()

    async def stop_all_clients(self):
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()
            await asyncio.gather(self._startup_task, return_exceptions=True)
        self._startup_task = None
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

    async def verify_account_session(self, account: Account):
        """A session that gets kicked/revoked out-of-band (logged out from
        another device, or by Telegram itself) is normally only discovered
        the next time something naturally tries to use that account — a
        mailing send, an incoming message for the autoresponder, or
        _monitor_loop's health check (which only watches accounts already
        in self._clients). An idle account with no active mailing and no
        autoresponder traffic never hits any of those paths, so it can sit
        in the DB as is_active=1 indefinitely even though it's long dead.
        Callers should only invoke this for accounts NOT already tracked as
        connected (is_client_active() is False), to avoid a redundant
        second connection for accounts already being watched normally.

        Just reconnects via the normal path (_start_client_unlocked already
        handles stale-entry cleanup, proxy parsing, ban/proxy-failure
        routing, and not-authorized deactivation) instead of building a
        separate throwaway client — a second independent TelegramClient on
        the same session string is exactly the dual-live-session pattern
        this codebase has been hardened against elsewhere, and it left the
        connection alive afterward is actually preferable here: a verified
        idle account then stays connected and usable (e.g. for its
        autoresponder) instead of being immediately dropped again."""
        lock = self._client_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            if account.id in self._clients and self._clients[account.id].is_connected():
                return  # got connected through the normal path in the meantime
            await self._start_client_unlocked(account)
