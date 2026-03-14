import json
import secrets
import time
import aiosqlite
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from .models import SCHEMA

_SETTINGS_TTL = 60      # seconds — settings/prices
_CHANNELS_TTL = 300     # seconds — required channels list


@dataclass
class User:
    id: int
    telegram_id: int
    username: Optional[str]
    subscription_end: Optional[datetime]
    is_admin: bool
    ref_code: Optional[str]
    referred_by: Optional[int]
    ref_balance: float
    created_at: datetime

    @property
    def display_name(self) -> str:
        return f"@{self.username}" if self.username else str(self.telegram_id)


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
    group_autoresponder_enabled: bool
    group_autoresponder_text: Optional[str]
    autoresponder_photo: Optional[str]
    group_autoresponder_photo: Optional[str]
    is_active: bool
    created_at: datetime
    name: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.name if self.name else self.phone


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
    parse_mode: str = 'html'

    @property
    def photo_paths(self) -> list[str]:
        if not self.photo_path:
            return []
        try:
            paths = json.loads(self.photo_path)
            if isinstance(paths, list):
                return paths
        except (json.JSONDecodeError, TypeError):
            pass
        return [self.photo_path]


@dataclass
class MailingTarget:
    id: int
    mailing_id: int
    chat_identifier: str
    interval_seconds: Optional[int] = None
    last_sent_at: Optional[datetime] = None


@dataclass
class Payment:
    id: int
    user_id: int
    invoice_id: Optional[str]
    amount: float
    currency: str
    status: str
    plan_days: int
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


@dataclass
class WithdrawalRequest:
    id: int
    user_id: int
    amount: float
    wallet: Optional[str]
    status: str
    created_at: datetime


@dataclass
class RequiredChannel:
    id: int
    channel_id: int
    channel_username: Optional[str]
    channel_title: str
    added_at: datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._cache: dict = {}          # key -> value
        self._cache_ts: dict = {}       # key -> timestamp

    def _cache_get(self, key: str, ttl: float):
        if key in self._cache and (time.monotonic() - self._cache_ts.get(key, 0)) < ttl:
            return self._cache[key]
        return None

    def _cache_set(self, key: str, value):
        self._cache[key] = value
        self._cache_ts[key] = time.monotonic()

    def _cache_invalidate(self, *keys):
        for k in keys:
            self._cache.pop(k, None)
            self._cache_ts.pop(k, None)

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        await self._conn.execute("PRAGMA cache_size = -8000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._run_migrations()

    async def _run_migrations(self):
        """Run database migrations for new columns."""
        async def _add_col(table, col, definition):
            async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                cols = [r["name"] for r in await cur.fetchall()]
            if col not in cols:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                await self._conn.commit()

        # accounts
        await _add_col("accounts", "notify_messages",              "BOOLEAN DEFAULT FALSE")
        await _add_col("accounts", "name",                         "TEXT")
        await _add_col("accounts", "group_autoresponder_enabled",  "BOOLEAN DEFAULT FALSE")
        await _add_col("accounts", "group_autoresponder_text",     "TEXT")
        await _add_col("accounts", "autoresponder_photo",          "TEXT")
        await _add_col("accounts", "group_autoresponder_photo",    "TEXT")
        # mailing_messages
        await _add_col("mailing_messages", "photo_path",  "TEXT")
        await _add_col("mailing_messages", "parse_mode",  "TEXT DEFAULT 'html'")
        # mailing_targets
        await _add_col("mailing_targets", "interval_seconds", "INTEGER")
        await _add_col("mailing_targets", "last_sent_at",     "DATETIME")
        # payments
        await _add_col("payments", "payment_method", "TEXT DEFAULT 'cryptobot'")
        await _add_col("payments", "plan_days",       "INTEGER DEFAULT 30")
        # promocodes
        await _add_col("promocodes", "max_uses",   "INTEGER NOT NULL DEFAULT 1")
        await _add_col("promocodes", "uses_count", "INTEGER NOT NULL DEFAULT 0")
        # users
        await _add_col("users", "ref_code",     "TEXT")
        await _add_col("users", "referred_by",  "INTEGER")
        await _add_col("users", "ref_balance",  "REAL DEFAULT 0")

    async def close(self):
        if self._conn:
            await self._conn.close()

    def _parse_datetime(self, value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _row_to_user(self, row) -> "User":
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            username=row["username"],
            subscription_end=self._parse_datetime(row["subscription_end"]),
            is_admin=bool(row["is_admin"]),
            ref_code=row["ref_code"] if "ref_code" in row.keys() else None,
            referred_by=row["referred_by"] if "referred_by" in row.keys() else None,
            ref_balance=float(row["ref_balance"]) if "ref_balance" in row.keys() and row["ref_balance"] else 0.0,
            created_at=self._parse_datetime(row["created_at"]),
        )

    def _row_to_account(self, row) -> "Account":
        keys = row.keys()
        return Account(
            id=row["id"],
            user_id=row["user_id"],
            phone=row["phone"],
            session_string=row["session_string"],
            api_id=row["api_id"],
            api_hash=row["api_hash"],
            autoresponder_enabled=bool(row["autoresponder_enabled"]),
            autoresponder_text=row["autoresponder_text"],
            notify_messages=bool(row["notify_messages"]) if "notify_messages" in keys and row["notify_messages"] is not None else False,
            group_autoresponder_enabled=bool(row["group_autoresponder_enabled"]) if "group_autoresponder_enabled" in keys and row["group_autoresponder_enabled"] is not None else False,
            group_autoresponder_text=row["group_autoresponder_text"] if "group_autoresponder_text" in keys else None,
            autoresponder_photo=row["autoresponder_photo"] if "autoresponder_photo" in keys else None,
            group_autoresponder_photo=row["group_autoresponder_photo"] if "group_autoresponder_photo" in keys else None,
            is_active=bool(row["is_active"]),
            created_at=self._parse_datetime(row["created_at"]),
            name=row["name"] if "name" in keys else None,
        )

    # === Users ===
    async def get_user(self, telegram_id: int) -> Optional[User]:
        async with self._conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cur:
            row = await cur.fetchone()
            return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        async with self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return self._row_to_user(row) if row else None

    async def get_user_by_ref_code(self, ref_code: str) -> Optional[User]:
        async with self._conn.execute("SELECT * FROM users WHERE ref_code = ?", (ref_code,)) as cur:
            row = await cur.fetchone()
            return self._row_to_user(row) if row else None

    async def create_user(self, telegram_id: int, username: Optional[str] = None, is_admin: bool = False) -> User:
        ref_code = secrets.token_urlsafe(6)
        await self._conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, is_admin, ref_code) VALUES (?, ?, ?, ?)",
            (telegram_id, username, is_admin, ref_code),
        )
        await self._conn.commit()
        return await self.get_user(telegram_id)

    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> User:
        user = await self.get_user(telegram_id)
        if not user:
            from ..config import config
            is_admin = telegram_id in config.ADMIN_IDS
            user = await self.create_user(telegram_id, username, is_admin)
        elif not user.ref_code:
            ref_code = secrets.token_urlsafe(6)
            await self._conn.execute("UPDATE users SET ref_code=? WHERE id=?", (ref_code, user.id))
            await self._conn.commit()
            user = await self.get_user(telegram_id)
        return user

    async def set_referred_by(self, user_id: int, referrer_id: int):
        await self._conn.execute(
            "UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL",
            (referrer_id, user_id)
        )
        await self._conn.commit()

    async def add_ref_balance(self, user_id: int, amount: float):
        await self._conn.execute(
            "UPDATE users SET ref_balance = ref_balance + ? WHERE id = ?",
            (amount, user_id)
        )
        await self._conn.commit()

    async def deduct_ref_balance(self, user_id: int, amount: float):
        await self._conn.execute(
            "UPDATE users SET ref_balance = ref_balance - ? WHERE id = ?",
            (amount, user_id)
        )
        await self._conn.commit()

    async def update_subscription(self, user_id: int, subscription_end: datetime):
        await self._conn.execute(
            "UPDATE users SET subscription_end = ? WHERE id = ?",
            (subscription_end.isoformat(), user_id),
        )
        await self._conn.commit()

    async def get_all_users(self) -> list[User]:
        async with self._conn.execute("SELECT * FROM users") as cur:
            return [self._row_to_user(r) for r in await cur.fetchall()]

    async def get_referral_count(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    async def get_referral_buyers_count(self, user_id: int) -> int:
        """Count referrals who bought at least one subscription."""
        async with self._conn.execute(
            """SELECT COUNT(DISTINCT u.id) as cnt FROM users u
               JOIN payments p ON p.user_id = u.id
               WHERE u.referred_by = ? AND p.status = 'paid'""",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # === Accounts ===
    async def get_account(self, account_id: int) -> Optional[Account]:
        async with self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            return self._row_to_account(row) if row else None

    async def get_user_accounts(self, user_id: int) -> list[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cur:
            return [self._row_to_account(r) for r in await cur.fetchall()]

    async def count_user_accounts(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM accounts WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    async def create_account(self, user_id: int, phone: str, api_id: int, api_hash: str, session_string: str) -> int:
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

    async def update_account_name(self, account_id: int, name: str):
        await self._conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
        await self._conn.commit()

    async def update_autoresponder(self, account_id: int, enabled: bool, text: Optional[str] = None, photo: Optional[str] = None):
        if text is not None:
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ?, autoresponder_text = ?, autoresponder_photo = ? WHERE id = ?",
                (enabled, text, photo, account_id),
            )
        else:
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ? WHERE id = ?",
                (enabled, account_id),
            )
        await self._conn.commit()

    async def update_group_autoresponder(self, account_id: int, enabled: bool, text: Optional[str] = None, photo: Optional[str] = None):
        if text is not None:
            await self._conn.execute(
                "UPDATE accounts SET group_autoresponder_enabled = ?, group_autoresponder_text = ?, group_autoresponder_photo = ? WHERE id = ?",
                (enabled, text, photo, account_id),
            )
        else:
            await self._conn.execute(
                "UPDATE accounts SET group_autoresponder_enabled = ? WHERE id = ?",
                (enabled, account_id),
            )
        await self._conn.commit()

    async def update_notify_messages(self, account_id: int, enabled: bool):
        await self._conn.execute(
            "UPDATE accounts SET notify_messages = ? WHERE id = ?", (enabled, account_id)
        )
        await self._conn.commit()

    async def delete_account(self, account_id: int):
        await self._conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,))
        await self._conn.commit()

    async def deactivate_account(self, account_id: int):
        """Mark account as inactive (ban/session revoke)."""
        await self._conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,))
        await self._conn.commit()

    async def get_all_active_accounts(self) -> list[Account]:
        async with self._conn.execute("SELECT * FROM accounts WHERE is_active = 1") as cur:
            return [self._row_to_account(r) for r in await cur.fetchall()]

    # === Mailings ===
    async def get_mailing(self, mailing_id: int) -> Optional[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE id = ?", (mailing_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return Mailing(
                    id=row["id"], user_id=row["user_id"], account_id=row["account_id"],
                    name=row["name"], is_active=bool(row["is_active"]),
                    interval_seconds=row["interval_seconds"],
                    active_hours_json=row["active_hours_json"],
                    last_sent_at=self._parse_datetime(row["last_sent_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def get_user_mailings(self, user_id: int) -> list[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE user_id = ?", (user_id,)) as cur:
            rows = await cur.fetchall()
            return [Mailing(
                id=r["id"], user_id=r["user_id"], account_id=r["account_id"],
                name=r["name"], is_active=bool(r["is_active"]),
                interval_seconds=r["interval_seconds"], active_hours_json=r["active_hours_json"],
                last_sent_at=self._parse_datetime(r["last_sent_at"]),
                created_at=self._parse_datetime(r["created_at"]),
            ) for r in rows]

    async def get_active_mailings(self) -> list[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE is_active = 1") as cur:
            rows = await cur.fetchall()
            return [Mailing(
                id=r["id"], user_id=r["user_id"], account_id=r["account_id"],
                name=r["name"], is_active=bool(r["is_active"]),
                interval_seconds=r["interval_seconds"], active_hours_json=r["active_hours_json"],
                last_sent_at=self._parse_datetime(r["last_sent_at"]),
                created_at=self._parse_datetime(r["created_at"]),
            ) for r in rows]

    async def get_user_active_mailings(self, user_id: int) -> list[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [Mailing(
                id=r["id"], user_id=r["user_id"], account_id=r["account_id"],
                name=r["name"], is_active=bool(r["is_active"]),
                interval_seconds=r["interval_seconds"], active_hours_json=r["active_hours_json"],
                last_sent_at=self._parse_datetime(r["last_sent_at"]),
                created_at=self._parse_datetime(r["created_at"]),
            ) for r in rows]

    async def create_mailing(self, user_id: int, account_id: int, name: str,
                              interval_seconds: int, active_hours_json: Optional[str] = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO mailings (user_id, account_id, name, interval_seconds, active_hours_json) VALUES (?, ?, ?, ?, ?)",
            (user_id, account_id, name, interval_seconds, active_hours_json),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_mailing_status(self, mailing_id: int, is_active: bool):
        await self._conn.execute("UPDATE mailings SET is_active = ? WHERE id = ?", (is_active, mailing_id))
        await self._conn.commit()

    async def update_mailing_account(self, mailing_id: int, account_id: int):
        await self._conn.execute("UPDATE mailings SET account_id = ? WHERE id = ?", (account_id, mailing_id))
        await self._conn.commit()

    async def update_mailing_last_sent(self, mailing_id: int):
        await self._conn.execute(
            "UPDATE mailings SET last_sent_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), mailing_id),
        )
        await self._conn.commit()

    async def update_mailing_active_hours(self, mailing_id: int, active_hours_json: Optional[str]):
        await self._conn.execute(
            "UPDATE mailings SET active_hours_json = ? WHERE id = ?", (active_hours_json, mailing_id)
        )
        await self._conn.commit()

    async def delete_mailing(self, mailing_id: int):
        await self._conn.execute("DELETE FROM mailings WHERE id = ?", (mailing_id,))
        await self._conn.commit()

    async def count_all_mailings(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) as cnt FROM mailings") as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # === Mailing Messages ===
    async def get_mailing_messages(self, mailing_id: int) -> list[MailingMessage]:
        async with self._conn.execute(
            "SELECT * FROM mailing_messages WHERE mailing_id = ?", (mailing_id,)
        ) as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                keys = r.keys()
                result.append(MailingMessage(
                    id=r["id"], mailing_id=r["mailing_id"], text=r["text"],
                    photo_path=r["photo_path"] if "photo_path" in keys else None,
                    parse_mode=r["parse_mode"] if "parse_mode" in keys and r["parse_mode"] else 'html',
                ))
            return result

    async def add_mailing_message(self, mailing_id: int, text: str, photo_path: Optional[str] = None,
                                   photo_paths: Optional[list[str]] = None, parse_mode: str = 'html') -> int:
        stored = json.dumps(photo_paths) if photo_paths else photo_path
        cursor = await self._conn.execute(
            "INSERT INTO mailing_messages (mailing_id, text, photo_path, parse_mode) VALUES (?, ?, ?, ?)",
            (mailing_id, text, stored, parse_mode),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_message_parse_mode(self, message_id: int, parse_mode: str):
        await self._conn.execute(
            "UPDATE mailing_messages SET parse_mode = ? WHERE id = ?", (parse_mode, message_id)
        )
        await self._conn.commit()

    async def delete_mailing_message(self, message_id: int):
        import os
        async with self._conn.execute(
            "SELECT photo_path FROM mailing_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row["photo_path"]:
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
        await self._conn.execute("DELETE FROM mailing_messages WHERE id = ?", (message_id,))
        await self._conn.commit()

    # === Mailing Targets ===
    async def get_mailing_targets(self, mailing_id: int) -> list[MailingTarget]:
        async with self._conn.execute(
            "SELECT * FROM mailing_targets WHERE mailing_id = ?", (mailing_id,)
        ) as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                keys = r.keys()
                result.append(MailingTarget(
                    id=r["id"],
                    mailing_id=r["mailing_id"],
                    chat_identifier=r["chat_identifier"],
                    interval_seconds=r["interval_seconds"] if "interval_seconds" in keys else None,
                    last_sent_at=self._parse_datetime(r["last_sent_at"]) if "last_sent_at" in keys else None,
                ))
            return result

    async def add_mailing_target(self, mailing_id: int, chat_identifier: str) -> int:
        normalized = chat_identifier.strip()
        if not normalized.startswith('-') and not normalized.isdigit():
            if not normalized.startswith('@'):
                normalized = f"@{normalized}"
        cursor = await self._conn.execute(
            "INSERT INTO mailing_targets (mailing_id, chat_identifier) VALUES (?, ?)",
            (mailing_id, normalized),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_target_interval(self, target_id: int, interval_seconds: Optional[int]):
        await self._conn.execute(
            "UPDATE mailing_targets SET interval_seconds = ? WHERE id = ?",
            (interval_seconds, target_id),
        )
        await self._conn.commit()

    async def update_target_last_sent(self, target_id: int):
        await self._conn.execute(
            "UPDATE mailing_targets SET last_sent_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), target_id),
        )
        await self._conn.commit()

    async def delete_mailing_target(self, target_id: int):
        await self._conn.execute("DELETE FROM mailing_targets WHERE id = ?", (target_id,))
        await self._conn.commit()

    # === Autoresponder History ===
    async def autoresponder_history_exists(self, account_id: int, sender_telegram_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM autoresponder_history WHERE account_id = ? AND sender_telegram_id = ?",
            (account_id, sender_telegram_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def add_autoresponder_history(self, account_id: int, sender_telegram_id: int, message_text: Optional[str]):
        await self._conn.execute(
            "INSERT OR IGNORE INTO autoresponder_history (account_id, sender_telegram_id, message_text) VALUES (?, ?, ?)",
            (account_id, sender_telegram_id, message_text),
        )
        await self._conn.commit()

    async def clear_autoresponder_history(self, account_id: int):
        await self._conn.execute("DELETE FROM autoresponder_history WHERE account_id = ?", (account_id,))
        await self._conn.commit()

    # === Payments ===
    async def create_payment(self, user_id: int, invoice_id: str, amount: float,
                              currency: str, plan_days: int = 30) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO payments (user_id, invoice_id, amount, currency, plan_days) VALUES (?, ?, ?, ?, ?)",
            (user_id, invoice_id, amount, currency, plan_days),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_payment_by_invoice(self, invoice_id: str) -> Optional[Payment]:
        async with self._conn.execute("SELECT * FROM payments WHERE invoice_id = ?", (invoice_id,)) as cur:
            row = await cur.fetchone()
            if row:
                keys = row.keys()
                return Payment(
                    id=row["id"], user_id=row["user_id"], invoice_id=row["invoice_id"],
                    amount=row["amount"], currency=row["currency"], status=row["status"],
                    plan_days=row["plan_days"] if "plan_days" in keys and row["plan_days"] else 30,
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
        async with self._conn.execute("SELECT * FROM payments WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
            return [Payment(
                id=r["id"], user_id=r["user_id"], invoice_id=r["invoice_id"],
                amount=r["amount"], currency=r["currency"], status=r["status"],
                plan_days=r["plan_days"] if r["plan_days"] else 30,
                created_at=self._parse_datetime(r["created_at"]),
                paid_at=self._parse_datetime(r["paid_at"]),
            ) for r in rows]

    async def get_total_revenue(self) -> float:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status='paid' AND currency='USDT'"
        ) as cur:
            row = await cur.fetchone()
            return float(row["total"]) if row else 0.0

    async def count_paid_subscriptions(self) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM payments WHERE status='paid'"
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # === Settings ===
    async def get_setting(self, key: str) -> Optional[str]:
        cached = self._cache_get(f"setting:{key}", _SETTINGS_TTL)
        if cached is not None:
            return cached if cached != "__none__" else None
        async with self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            val = row["value"] if row else None
            self._cache_set(f"setting:{key}", val if val is not None else "__none__")
            return val

    async def set_setting(self, key: str, value: str):
        await self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await self._conn.commit()
        self._cache_invalidate(f"setting:{key}")

    async def get_price(self, plan_days: int = 30) -> float:
        from ..config import config
        key = f"price_{plan_days}d"
        val = await self.get_setting(key)
        if val:
            return float(val)
        return config.SUBSCRIPTION_PRICE

    async def set_price(self, plan_days: int, price: float):
        await self.set_setting(f"price_{plan_days}d", str(price))

    async def get_ref_percent(self) -> float:
        val = await self.get_setting("ref_percent")
        return float(val) if val else 10.0

    async def get_ref_min_withdraw(self) -> float:
        val = await self.get_setting("ref_min_withdraw")
        return float(val) if val else 5.0

    # === Promocodes ===
    async def create_promocode(self, code: str, duration_days: int = 30, max_uses: int = 1) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO promocodes (code, duration_days, max_uses) VALUES (?, ?, ?)",
            (code, duration_days, max_uses),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_promocode(self, code: str) -> Optional[Promocode]:
        async with self._conn.execute("SELECT * FROM promocodes WHERE code = ?", (code,)) as cur:
            row = await cur.fetchone()
            if row:
                return Promocode(
                    id=row["id"], code=row["code"], duration_days=row["duration_days"],
                    max_uses=row["max_uses"], uses_count=row["uses_count"],
                    is_used=bool(row["is_used"]), used_by=row["used_by"],
                    used_at=self._parse_datetime(row["used_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                )
        return None

    async def has_user_used_promocode(self, promocode_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM promocode_uses WHERE promocode_id = ? AND user_id = ?",
            (promocode_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

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
        async with self._conn.execute("SELECT * FROM promocodes ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [Promocode(
                id=r["id"], code=r["code"], duration_days=r["duration_days"],
                max_uses=r["max_uses"], uses_count=r["uses_count"],
                is_used=bool(r["is_used"]), used_by=r["used_by"],
                used_at=self._parse_datetime(r["used_at"]),
                created_at=self._parse_datetime(r["created_at"]),
            ) for r in rows]

    async def delete_promocode(self, promo_id: int):
        await self._conn.execute("DELETE FROM promocodes WHERE id = ?", (promo_id,))
        await self._conn.commit()

    # === Withdrawal Requests ===
    async def create_withdrawal_request(self, user_id: int, amount: float, wallet: Optional[str] = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO withdrawal_requests (user_id, amount, wallet) VALUES (?, ?, ?)",
            (user_id, amount, wallet),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_withdrawal_requests(self, status: Optional[str] = None) -> list[WithdrawalRequest]:
        if status:
            query = "SELECT * FROM withdrawal_requests WHERE status = ? ORDER BY created_at DESC"
            args = (status,)
        else:
            query = "SELECT * FROM withdrawal_requests ORDER BY created_at DESC"
            args = ()
        async with self._conn.execute(query, args) as cur:
            rows = await cur.fetchall()
            return [WithdrawalRequest(
                id=r["id"], user_id=r["user_id"], amount=r["amount"],
                wallet=r["wallet"], status=r["status"],
                created_at=self._parse_datetime(r["created_at"]),
            ) for r in rows]

    async def update_withdrawal_status(self, request_id: int, status: str):
        await self._conn.execute(
            "UPDATE withdrawal_requests SET status = ? WHERE id = ?", (status, request_id)
        )
        await self._conn.commit()

    # === Required Channels ===
    async def get_required_channels(self) -> list[RequiredChannel]:
        cached = self._cache_get("required_channels", _CHANNELS_TTL)
        if cached is not None:
            return cached
        async with self._conn.execute("SELECT * FROM required_channels ORDER BY added_at") as cur:
            rows = await cur.fetchall()
            result = [RequiredChannel(
                id=r["id"], channel_id=r["channel_id"], channel_username=r["channel_username"],
                channel_title=r["channel_title"], added_at=self._parse_datetime(r["added_at"]),
            ) for r in rows]
            self._cache_set("required_channels", result)
            return result

    async def add_required_channel(self, channel_id: int, channel_username: Optional[str], channel_title: str):
        await self._conn.execute(
            "INSERT OR REPLACE INTO required_channels (channel_id, channel_username, channel_title) VALUES (?, ?, ?)",
            (channel_id, channel_username, channel_title),
        )
        await self._conn.commit()
        self._cache_invalidate("required_channels")

    async def remove_required_channel(self, channel_id: int):
        await self._conn.execute("DELETE FROM required_channels WHERE channel_id = ?", (channel_id,))
        await self._conn.commit()
        self._cache_invalidate("required_channels")
