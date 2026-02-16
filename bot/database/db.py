import json
import aiosqlite
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from .models import SCHEMA


@dataclass
class User:
    id: int
    telegram_id: int
    username: Optional[str]
    subscription_end: Optional[datetime]
    is_admin: bool
    created_at: datetime


@dataclass
class Account:
    id: int
    user_id: int
    phone: str
    session_string: Optional[str]
    api_id: int
    api_hash: str
    autoresponder_enabled: bool
    autoresponder_text: Optional[str]
    notify_messages: bool
    is_active: bool
    created_at: datetime


@dataclass
class Mailing:
    id: int
    user_id: int
    account_id: int
    name: str
    is_active: bool
    interval_seconds: int
    active_hours_json: Optional[str]
    last_sent_at: Optional[datetime]
    created_at: datetime


@dataclass
class MailingMessage:
    id: int
    mailing_id: int
    text: str
    photo_path: Optional[str] = None

    @property
    def photo_paths(self) -> list[str]:
        """Parse photo_path as JSON array, with fallback for legacy single-path strings."""
        if not self.photo_path:
            return []
        try:
            paths = json.loads(self.photo_path)
            if isinstance(paths, list):
                return paths
        except (json.JSONDecodeError, TypeError):
            pass
        # Legacy single-path string
        return [self.photo_path]


@dataclass
class MailingTarget:
    id: int
    mailing_id: int
    chat_identifier: str


@dataclass
class Payment:
    id: int
    user_id: int
    invoice_id: Optional[str]
    amount: float
    currency: str
    status: str
    created_at: datetime
    paid_at: Optional[datetime]


@dataclass
class Promocode:
    id: int
    code: str
    duration_days: int
    max_uses: int
    uses_count: int
    is_used: bool
    used_by: Optional[int]
    used_at: Optional[datetime]
    created_at: datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        # Run migrations
        await self._run_migrations()

    async def _run_migrations(self):
        """Run database migrations for new columns."""
        # Check if notify_messages column exists
        async with self._conn.execute("PRAGMA table_info(accounts)") as cursor:
            columns = await cursor.fetchall()
            column_names = [col["name"] for col in columns]

        if "notify_messages" not in column_names:
            await self._conn.execute(
                "ALTER TABLE accounts ADD COLUMN notify_messages BOOLEAN DEFAULT FALSE"
            )
            await self._conn.commit()

        # Check if payment_method column exists in payments
        async with self._conn.execute("PRAGMA table_info(payments)") as cursor:
            columns = await cursor.fetchall()
            payment_column_names = [col["name"] for col in columns]

        # Check if photo_path column exists in mailing_messages
        async with self._conn.execute("PRAGMA table_info(mailing_messages)") as cursor:
            columns = await cursor.fetchall()
            mm_column_names = [col["name"] for col in columns]

        if "photo_path" not in mm_column_names:
            await self._conn.execute(
                "ALTER TABLE mailing_messages ADD COLUMN photo_path TEXT"
            )
            await self._conn.commit()

        if "payment_method" not in payment_column_names:
            await self._conn.execute(
                "ALTER TABLE payments ADD COLUMN payment_method TEXT DEFAULT 'cryptobot'"
            )
            await self._conn.commit()

        # Check if max_uses / uses_count columns exist in promocodes
        async with self._conn.execute("PRAGMA table_info(promocodes)") as cursor:
            columns = await cursor.fetchall()
            promo_column_names = [col["name"] for col in columns]

        if "max_uses" not in promo_column_names:
            await self._conn.execute(
                "ALTER TABLE promocodes ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1"
            )
            await self._conn.commit()

        if "uses_count" not in promo_column_names:
            await self._conn.execute(
                "ALTER TABLE promocodes ADD COLUMN uses_count INTEGER NOT NULL DEFAULT 0"
            )
            # Migrate: existing used promos get uses_count = 1
            await self._conn.execute(
                "UPDATE promocodes SET uses_count = 1 WHERE is_used = 1"
            )
            await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    def _parse_datetime(self, value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    # === Users ===
    async def get_user(self, telegram_id: int) -> Optional[User]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return User(
                    id=row["id"],
                    telegram_id=row["telegram_id"],
                    username=row["username"],
                    subscription_end=self._parse_datetime(row["subscription_end"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return User(
                    id=row["id"],
                    telegram_id=row["telegram_id"],
                    username=row["username"],
                    subscription_end=self._parse_datetime(row["subscription_end"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def create_user(self, telegram_id: int, username: Optional[str] = None, is_admin: bool = False) -> User:
        await self._conn.execute(
            "INSERT INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)",
            (telegram_id, username, is_admin),
        )
        await self._conn.commit()
        return await self.get_user(telegram_id)

    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> User:
        user = await self.get_user(telegram_id)
        if not user:
            from ..config import config
            is_admin = telegram_id in config.ADMIN_IDS
            user = await self.create_user(telegram_id, username, is_admin)
        return user

    async def update_subscription(self, user_id: int, subscription_end: datetime):
        await self._conn.execute(
            "UPDATE users SET subscription_end = ? WHERE id = ?",
            (subscription_end.isoformat(), user_id),
        )
        await self._conn.commit()

    async def get_all_users(self) -> list[User]:
        async with self._conn.execute("SELECT * FROM users") as cursor:
            rows = await cursor.fetchall()
            return [
                User(
                    id=row["id"],
                    telegram_id=row["telegram_id"],
                    username=row["username"],
                    subscription_end=self._parse_datetime(row["subscription_end"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    # === Accounts ===
    async def get_account(self, account_id: int) -> Optional[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Account(
                    id=row["id"],
                    user_id=row["user_id"],
                    phone=row["phone"],
                    session_string=row["session_string"],
                    api_id=row["api_id"],
                    api_hash=row["api_hash"],
                    autoresponder_enabled=bool(row["autoresponder_enabled"]),
                    autoresponder_text=row["autoresponder_text"],
                    notify_messages=bool(row["notify_messages"]) if row["notify_messages"] is not None else False,
                    is_active=bool(row["is_active"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def get_user_accounts(self, user_id: int) -> list[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Account(
                    id=row["id"],
                    user_id=row["user_id"],
                    phone=row["phone"],
                    session_string=row["session_string"],
                    api_id=row["api_id"],
                    api_hash=row["api_hash"],
                    autoresponder_enabled=bool(row["autoresponder_enabled"]),
                    autoresponder_text=row["autoresponder_text"],
                    notify_messages=bool(row["notify_messages"]) if row["notify_messages"] is not None else False,
                    is_active=bool(row["is_active"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    async def count_user_accounts(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM accounts WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    async def create_account(
        self, user_id: int, phone: str, api_id: int, api_hash: str, session_string: str
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO accounts (user_id, phone, api_id, api_hash, session_string) VALUES (?, ?, ?, ?, ?)",
            (user_id, phone, api_id, api_hash, session_string),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_account_session(self, account_id: int, session_string: str):
        await self._conn.execute(
            "UPDATE accounts SET session_string = ? WHERE id = ?",
            (session_string, account_id),
        )
        await self._conn.commit()

    async def update_autoresponder(
        self, account_id: int, enabled: bool, text: Optional[str] = None
    ):
        if text is not None:
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ?, autoresponder_text = ? WHERE id = ?",
                (enabled, text, account_id),
            )
        else:
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ? WHERE id = ?",
                (enabled, account_id),
            )
        await self._conn.commit()

    async def delete_account(self, account_id: int):
        await self._conn.execute(
            "UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,)
        )
        await self._conn.commit()

    async def get_all_active_accounts(self) -> list[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Account(
                    id=row["id"],
                    user_id=row["user_id"],
                    phone=row["phone"],
                    session_string=row["session_string"],
                    api_id=row["api_id"],
                    api_hash=row["api_hash"],
                    autoresponder_enabled=bool(row["autoresponder_enabled"]),
                    autoresponder_text=row["autoresponder_text"],
                    notify_messages=bool(row["notify_messages"]) if row["notify_messages"] is not None else False,
                    is_active=bool(row["is_active"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    async def update_notify_messages(self, account_id: int, enabled: bool):
        await self._conn.execute(
            "UPDATE accounts SET notify_messages = ? WHERE id = ?",
            (enabled, account_id),
        )
        await self._conn.commit()

    # === Mailings ===
    async def get_mailing(self, mailing_id: int) -> Optional[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE id = ?", (mailing_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Mailing(
                    id=row["id"],
                    user_id=row["user_id"],
                    account_id=row["account_id"],
                    name=row["name"],
                    is_active=bool(row["is_active"]),
                    interval_seconds=row["interval_seconds"],
                    active_hours_json=row["active_hours_json"],
                    last_sent_at=self._parse_datetime(row["last_sent_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def get_user_mailings(self, user_id: int) -> list[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Mailing(
                    id=row["id"],
                    user_id=row["user_id"],
                    account_id=row["account_id"],
                    name=row["name"],
                    is_active=bool(row["is_active"]),
                    interval_seconds=row["interval_seconds"],
                    active_hours_json=row["active_hours_json"],
                    last_sent_at=self._parse_datetime(row["last_sent_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    async def get_active_mailings(self) -> list[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Mailing(
                    id=row["id"],
                    user_id=row["user_id"],
                    account_id=row["account_id"],
                    name=row["name"],
                    is_active=bool(row["is_active"]),
                    interval_seconds=row["interval_seconds"],
                    active_hours_json=row["active_hours_json"],
                    last_sent_at=self._parse_datetime(row["last_sent_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    async def create_mailing(
        self,
        user_id: int,
        account_id: int,
        name: str,
        interval_seconds: int,
        active_hours_json: Optional[str] = None,
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO mailings (user_id, account_id, name, interval_seconds, active_hours_json) VALUES (?, ?, ?, ?, ?)",
            (user_id, account_id, name, interval_seconds, active_hours_json),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_mailing_status(self, mailing_id: int, is_active: bool):
        await self._conn.execute(
            "UPDATE mailings SET is_active = ? WHERE id = ?", (is_active, mailing_id)
        )
        await self._conn.commit()

    async def update_mailing_last_sent(self, mailing_id: int):
        await self._conn.execute(
            "UPDATE mailings SET last_sent_at = ? WHERE id = ?",
            (datetime.now().isoformat(), mailing_id),
        )
        await self._conn.commit()

    async def update_mailing_active_hours(self, mailing_id: int, active_hours_json: Optional[str]):
        await self._conn.execute(
            "UPDATE mailings SET active_hours_json = ? WHERE id = ?",
            (active_hours_json, mailing_id),
        )
        await self._conn.commit()

    async def delete_mailing(self, mailing_id: int):
        await self._conn.execute("DELETE FROM mailings WHERE id = ?", (mailing_id,))
        await self._conn.commit()

    # === Mailing Messages ===
    async def get_mailing_messages(self, mailing_id: int) -> list[MailingMessage]:
        async with self._conn.execute(
            "SELECT * FROM mailing_messages WHERE mailing_id = ?", (mailing_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                MailingMessage(id=row["id"], mailing_id=row["mailing_id"], text=row["text"], photo_path=row["photo_path"] if "photo_path" in row.keys() else None)
                for row in rows
            ]

    async def add_mailing_message(self, mailing_id: int, text: str, photo_path: Optional[str] = None, photo_paths: Optional[list[str]] = None) -> int:
        if photo_paths:
            stored = json.dumps(photo_paths)
        else:
            stored = photo_path
        cursor = await self._conn.execute(
            "INSERT INTO mailing_messages (mailing_id, text, photo_path) VALUES (?, ?, ?)",
            (mailing_id, text, stored),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def delete_mailing_message(self, message_id: int):
        # Get photo_path before deleting to clean up files
        import os
        async with self._conn.execute(
            "SELECT photo_path FROM mailing_messages WHERE id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row["photo_path"]:
                # Parse as JSON array or legacy single path
                paths = []
                try:
                    parsed = json.loads(row["photo_path"])
                    if isinstance(parsed, list):
                        paths = parsed
                except (json.JSONDecodeError, TypeError):
                    paths = [row["photo_path"]]
                for path in paths:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        await self._conn.execute(
            "DELETE FROM mailing_messages WHERE id = ?", (message_id,)
        )
        await self._conn.commit()

    # === Mailing Targets ===
    async def get_mailing_targets(self, mailing_id: int) -> list[MailingTarget]:
        async with self._conn.execute(
            "SELECT * FROM mailing_targets WHERE mailing_id = ?", (mailing_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                MailingTarget(
                    id=row["id"],
                    mailing_id=row["mailing_id"],
                    chat_identifier=row["chat_identifier"],
                )
                for row in rows
            ]

    async def add_mailing_target(self, mailing_id: int, chat_identifier: str) -> int:
        # Normalize chat identifier
        normalized = chat_identifier.strip()
        # If it's a username (not a numeric ID or group ID starting with -), ensure it has @
        if not normalized.startswith('-') and not normalized.isdigit():
            if not normalized.startswith('@'):
                normalized = f"@{normalized}"

        cursor = await self._conn.execute(
            "INSERT INTO mailing_targets (mailing_id, chat_identifier) VALUES (?, ?)",
            (mailing_id, normalized),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def delete_mailing_target(self, target_id: int):
        await self._conn.execute(
            "DELETE FROM mailing_targets WHERE id = ?", (target_id,)
        )
        await self._conn.commit()

    # === Autoresponder History ===
    async def autoresponder_history_exists(
        self, account_id: int, sender_telegram_id: int
    ) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM autoresponder_history WHERE account_id = ? AND sender_telegram_id = ?",
            (account_id, sender_telegram_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def add_autoresponder_history(
        self, account_id: int, sender_telegram_id: int, message_text: Optional[str]
    ):
        await self._conn.execute(
            "INSERT OR IGNORE INTO autoresponder_history (account_id, sender_telegram_id, message_text) VALUES (?, ?, ?)",
            (account_id, sender_telegram_id, message_text),
        )
        await self._conn.commit()

    async def clear_autoresponder_history(self, account_id: int):
        await self._conn.execute(
            "DELETE FROM autoresponder_history WHERE account_id = ?", (account_id,)
        )
        await self._conn.commit()

    # === Payments ===
    async def create_payment(
        self, user_id: int, invoice_id: str, amount: float, currency: str
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO payments (user_id, invoice_id, amount, currency) VALUES (?, ?, ?, ?)",
            (user_id, invoice_id, amount, currency),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_payment_by_invoice(self, invoice_id: str) -> Optional[Payment]:
        async with self._conn.execute(
            "SELECT * FROM payments WHERE invoice_id = ?", (invoice_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Payment(
                    id=row["id"],
                    user_id=row["user_id"],
                    invoice_id=row["invoice_id"],
                    amount=row["amount"],
                    currency=row["currency"],
                    status=row["status"],
                    created_at=self._parse_datetime(row["created_at"]),
                    paid_at=self._parse_datetime(row["paid_at"]),
                )
        return None

    async def update_payment_status(self, invoice_id: str, status: str):
        paid_at = datetime.now().isoformat() if status == "paid" else None
        await self._conn.execute(
            "UPDATE payments SET status = ?, paid_at = ? WHERE invoice_id = ?",
            (status, paid_at, invoice_id),
        )
        await self._conn.commit()

    async def get_pending_payments(self) -> list[Payment]:
        async with self._conn.execute(
            "SELECT * FROM payments WHERE status = 'pending'"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Payment(
                    id=row["id"],
                    user_id=row["user_id"],
                    invoice_id=row["invoice_id"],
                    amount=row["amount"],
                    currency=row["currency"],
                    status=row["status"],
                    created_at=self._parse_datetime(row["created_at"]),
                    paid_at=self._parse_datetime(row["paid_at"]),
                )
                for row in rows
            ]

    # === Settings ===
    async def get_setting(self, key: str) -> Optional[str]:
        async with self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def set_setting(self, key: str, value: str):
        await self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._conn.commit()

    # === Promocodes ===
    async def create_promocode(self, code: str, duration_days: int = 30, max_uses: int = 1) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO promocodes (code, duration_days, max_uses) VALUES (?, ?, ?)",
            (code, duration_days, max_uses),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_promocode(self, code: str) -> Optional[Promocode]:
        async with self._conn.execute(
            "SELECT * FROM promocodes WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Promocode(
                    id=row["id"],
                    code=row["code"],
                    duration_days=row["duration_days"],
                    max_uses=row["max_uses"],
                    uses_count=row["uses_count"],
                    is_used=bool(row["is_used"]),
                    used_by=row["used_by"],
                    used_at=self._parse_datetime(row["used_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def has_user_used_promocode(self, promocode_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM promocode_uses WHERE promocode_id = ? AND user_id = ?",
            (promocode_id, user_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def use_promocode(self, code: str, user_id: int, promocode_id: int):
        await self._conn.execute(
            "INSERT OR IGNORE INTO promocode_uses (promocode_id, user_id) VALUES (?, ?)",
            (promocode_id, user_id),
        )
        await self._conn.execute(
            "UPDATE promocodes SET uses_count = uses_count + 1, "
            "is_used = CASE WHEN uses_count + 1 >= max_uses THEN 1 ELSE 0 END, "
            "used_by = ?, used_at = ? WHERE code = ?",
            (user_id, datetime.now().isoformat(), code),
        )
        await self._conn.commit()

    async def get_all_promocodes(self) -> list[Promocode]:
        async with self._conn.execute(
            "SELECT * FROM promocodes ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Promocode(
                    id=row["id"],
                    code=row["code"],
                    duration_days=row["duration_days"],
                    max_uses=row["max_uses"],
                    uses_count=row["uses_count"],
                    is_used=bool(row["is_used"]),
                    used_by=row["used_by"],
                    used_at=self._parse_datetime(row["used_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
                for row in rows
            ]

    async def delete_promocode(self, promo_id: int):
        await self._conn.execute("DELETE FROM promocodes WHERE id = ?", (promo_id,))
        await self._conn.commit()
