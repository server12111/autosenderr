import asyncio
import json
import logging
import os
import secrets
import time
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

from .models import SCHEMA
from ..utils.delivery_journal import DeliveryJournal
from ..utils.time_utils import now_moscow

logger = logging.getLogger(__name__)

_SETTINGS_TTL = 60      # seconds — settings/prices
_CHANNELS_TTL = 300     # seconds — required channels list
POOL_PROXY_ACCOUNT_CAP = 50   # max active accounts per pool proxy

# SQLite cannot bind table/column identifiers.  Keep the only dynamic DDL in
# migrations behind this exact allowlist so no caller can turn _add_col() into
# an SQL-injection primitive.
_ALLOWED_MIGRATION_COLUMNS = frozenset({
    ('accounts', 'notify_messages', 'BOOLEAN DEFAULT FALSE'), ('accounts', 'name', 'TEXT'),
    ('accounts', 'group_autoresponder_enabled', 'BOOLEAN DEFAULT FALSE'), ('accounts', 'group_autoresponder_text', 'TEXT'),
    ('accounts', 'autoresponder_photo', 'TEXT'), ('accounts', 'group_autoresponder_photo', 'TEXT'),
    ('accounts', 'proxy', 'TEXT'), ('accounts', 'auto_subscribe_sponsors', 'BOOLEAN DEFAULT FALSE'),
    ('accounts', 'proxy_pool_id', 'INTEGER DEFAULT NULL'), ('accounts', 'proxy_problem_notified', 'INTEGER DEFAULT 0'),
    ('accounts', 'flood_wait_until', 'TEXT DEFAULT NULL'), ('mailing_messages', 'photo_path', 'TEXT'),
    ('mailing_messages', 'video_path', 'TEXT'), ('mailing_messages', 'parse_mode', "TEXT DEFAULT 'html'"),
    ('mailing_messages', 'entities_json', 'TEXT'), ('mailing_messages', 'forward_peer', 'TEXT'),
    ('mailing_messages', 'forward_msg_id', 'INTEGER'), ('mailing_targets', 'interval_seconds', 'INTEGER'),
    ('mailing_targets', 'last_sent_at', 'DATETIME'), ('mailings', 'reply_mode', 'TEXT DEFAULT NULL'),
    ('mailings', 'reply_offset', 'INTEGER DEFAULT 1'), ('mailings', 'reply_random_min', 'INTEGER DEFAULT 1'),
    ('mailings', 'reply_random_max', 'INTEGER DEFAULT 5'), ('payments', 'payment_method', "TEXT DEFAULT 'cryptobot'"),
    ('payments', 'plan_days', 'INTEGER DEFAULT 30'), ('payments', 'price_usdt', 'REAL DEFAULT NULL'),
    ('promocodes', 'max_uses', 'INTEGER NOT NULL DEFAULT 1'), ('promocodes', 'uses_count', 'INTEGER NOT NULL DEFAULT 0'),
    ('promocodes', 'is_subscription', 'INTEGER DEFAULT 0'), ('mailing_targets', 'thread_id', 'INTEGER DEFAULT NULL'),
    ('mailing_targets', 'is_forum', 'INTEGER DEFAULT 0'), ('mailing_targets', 'last_account_id', 'INTEGER DEFAULT NULL'),
    ('mailings', 'keep_targets_on_ban', 'INTEGER DEFAULT 0'), ('mailings', 'account_rotation_mode', "TEXT DEFAULT 'per_target'"),
    ('mailings', 'batch_size', 'INTEGER DEFAULT NULL'), ('mailings', 'batch_pause', 'INTEGER DEFAULT 10'),
    ('mailings', 'hidden_tag_enabled', 'INTEGER DEFAULT 0'), ('users', 'ref_code', 'TEXT'),
    ('users', 'referred_by', 'INTEGER'), ('users', 'ref_balance', 'REAL DEFAULT 0'), ('users', 'last_activity', 'DATETIME'),
    ('users', 'subscription_expired_notified_at', 'DATETIME DEFAULT NULL'), ('users', 'reminder_3d_sent_at', 'DATETIME DEFAULT NULL'),
    ('users', 'reminder_1d_sent_at', 'DATETIME DEFAULT NULL'), ('users', 'welcome_pin_msg_id', 'INTEGER DEFAULT NULL'),
    ('users', 'subscription_type', 'TEXT DEFAULT NULL'),
})


def is_safe_media_path(path: str) -> bool:
    try:
        media_root = os.path.realpath("data")
        resolved = os.path.realpath(path)
        return resolved != media_root and os.path.commonpath((media_root, resolved)) == media_root
    except (OSError, TypeError, ValueError):
        return False


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
    last_activity: Optional[datetime] = None
    subscription_expired_notified_at: Optional[datetime] = None
    reminder_3d_sent_at: Optional[datetime] = None
    reminder_1d_sent_at: Optional[datetime] = None
    welcome_pin_msg_id: Optional[int] = None
    subscription_type: Optional[str] = None

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
    proxy: Optional[str] = None
    auto_subscribe_sponsors: bool = False
    proxy_pool_id: Optional[int] = None

    @property
    def display_name(self) -> str:
        return self.name if self.name else self.phone


@dataclass
class PoolProxy:
    id: int
    proxy: str
    is_active: bool
    added_by: Optional[int]
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
    reply_mode: Optional[str] = None
    reply_offset: int = 1
    reply_random_min: int = 1
    reply_random_max: int = 5
    keep_targets_on_ban: bool = False
    account_rotation_mode: str = "per_target"
    batch_size: Optional[int] = None
    batch_pause: int = 10
    hidden_tag_enabled: bool = False


@dataclass
class MailingMessage:
    id: int
    mailing_id: int
    text: str
    photo_path: Optional[str] = None
    video_path: Optional[str] = None
    parse_mode: str = 'html'
    entities_json: Optional[str] = None
    forward_peer: Optional[str] = None
    forward_msg_id: Optional[int] = None

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

    @property
    def is_forward(self) -> bool:
        return bool(self.forward_peer and self.forward_msg_id)

    @property
    def is_dice(self) -> bool:
        return self.parse_mode == "dice"


@dataclass
class MailingTarget:
    id: int
    mailing_id: int
    chat_identifier: str
    interval_seconds: Optional[int] = None
    last_sent_at: Optional[datetime] = None
    thread_id: Optional[int] = None
    is_forum: bool = False
    last_account_id: Optional[int] = None


@dataclass
class Payment:
    id: int
    user_id: int
    invoice_id: Optional[str]
    amount: float
    currency: str
    payment_method: str
    status: str
    plan_days: int
    created_at: datetime
    paid_at: Optional[datetime]
    price_usdt: Optional[float] = None


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
    is_subscription: bool = False


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


@dataclass
class ErrorLog:
    id: int
    user_id: int
    account_id: Optional[int]
    mailing_id: Optional[int]
    error_type: str
    error_text: Optional[str]
    chat_identifier: Optional[str]
    created_at: datetime
    account_display: Optional[str] = None
    mailing_name: Optional[str] = None


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._cache: dict = {}          # key -> value
        self._cache_ts: dict = {}       # key -> timestamp
        self._transaction_lock = asyncio.Lock()

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
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        await self._conn.execute("PRAGMA cache_size = -8000")
        # Without this, a write on this connection that lands at the same
        # moment as a BEGIN IMMEDIATE transaction on one of the separate
        # short-lived connections below (create_account, payment/promo/
        # withdrawal handling) raises "database is locked" immediately
        # instead of waiting for the lock to clear — with 260+ accounts
        # constantly writing (session updates, target last_sent, error
        # logs), that collision is common enough to surface as random,
        # unexplained "Произошла ошибка" on unrelated actions (creating a
        # mailing, admin cleanup, admin promo code create/delete, etc.).
        # 5s wasn't always enough under real write load — reports of the
        # same generic error recurring specifically on admin promo actions
        # pointed at this still being too short, not fully fixed. Raised to
        # 15s: still bounded (can't hang forever), just more patient before
        # giving up.
        await self._conn.execute("PRAGMA busy_timeout = 15000")
        await self._conn.executescript(SCHEMA)
        await self._run_migrations()
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status_paid_at ON payments (status, paid_at)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mailings_user_active ON mailings (user_id, is_active)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_targets_mailing_last_sent ON mailing_targets (mailing_id, last_sent_at)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user_active ON accounts (user_id, is_active)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mailing_messages_mailing ON mailing_messages (mailing_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_targets_mailing ON mailing_targets (mailing_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments (user_id, status)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_users_subscription_end ON users (subscription_end)")
        await self._conn.commit()

    async def _run_migrations(self):
        """Run database migrations for new columns."""
        async def _add_col(table, col, definition):
            if (table, col, definition) not in _ALLOWED_MIGRATION_COLUMNS:
                raise ValueError("Attempted to run an unapproved schema migration")
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
        await _add_col("accounts", "proxy",                        "TEXT")
        await _add_col("accounts", "auto_subscribe_sponsors",      "BOOLEAN DEFAULT FALSE")
        await _add_col("accounts", "proxy_pool_id",                "INTEGER DEFAULT NULL")
        await _add_col("accounts", "proxy_problem_notified",       "INTEGER DEFAULT 0")
        await _add_col("accounts", "flood_wait_until",             "TEXT DEFAULT NULL")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS proxy_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                added_by INTEGER,
                created_at DATETIME DEFAULT (datetime('now', '+3 hours'))
            )
        """)
        await self._conn.commit()
        # mailing_messages
        await _add_col("mailing_messages", "photo_path",    "TEXT")
        await _add_col("mailing_messages", "video_path",    "TEXT")
        await _add_col("mailing_messages", "parse_mode",    "TEXT DEFAULT 'html'")
        await _add_col("mailing_messages", "entities_json", "TEXT")
        await _add_col("mailing_messages", "forward_peer",  "TEXT")
        await _add_col("mailing_messages", "forward_msg_id","INTEGER")
        # mailing_targets
        await _add_col("mailing_targets", "interval_seconds", "INTEGER")
        await _add_col("mailing_targets", "last_sent_at",     "DATETIME")
        # mailings — reply mode
        await _add_col("mailings", "reply_mode",       "TEXT DEFAULT NULL")
        await _add_col("mailings", "reply_offset",     "INTEGER DEFAULT 1")
        await _add_col("mailings", "reply_random_min", "INTEGER DEFAULT 1")
        await _add_col("mailings", "reply_random_max", "INTEGER DEFAULT 5")
        # payments
        await _add_col("payments", "payment_method", "TEXT DEFAULT 'cryptobot'")
        await _add_col("payments", "plan_days",       "INTEGER DEFAULT 30")
        # USDT price quoted at invoice creation — TON/Platega `amount` is in a different
        # currency (TON/RUB), so referral commission must be computed from this, not a
        # live re-fetch of the current price at confirmation time.
        await _add_col("payments", "price_usdt",      "REAL DEFAULT NULL")
        # promocodes
        await _add_col("promocodes", "max_uses",        "INTEGER NOT NULL DEFAULT 1")
        await _add_col("promocodes", "uses_count",      "INTEGER NOT NULL DEFAULT 0")
        await _add_col("promocodes", "is_subscription", "INTEGER DEFAULT 0")
        # mailing_targets — topics support
        await _add_col("mailing_targets", "thread_id",       "INTEGER DEFAULT NULL")
        await _add_col("mailing_targets", "is_forum",        "INTEGER DEFAULT 0")
        await _add_col("mailing_targets", "last_account_id", "INTEGER DEFAULT NULL")
        await self._conn.execute(
            """UPDATE mailing_targets
               SET interval_seconds = (
                   SELECT interval_seconds
                   FROM mailings
                   WHERE mailings.id = mailing_targets.mailing_id
               )
               WHERE interval_seconds IS NULL"""
        )
        await self._conn.commit()
        # mailings — keep targets on ban
        await _add_col("mailings", "keep_targets_on_ban", "INTEGER DEFAULT 0")
        # mailings — account rotation mode for multi-account mailings
        await _add_col("mailings", "account_rotation_mode", "TEXT DEFAULT 'per_target'")
        # mailings — batch sending mode
        await _add_col("mailings", "batch_size",  "INTEGER DEFAULT NULL")
        await _add_col("mailings", "batch_pause", "INTEGER DEFAULT 10")
        await _add_col("mailings", "hidden_tag_enabled", "INTEGER DEFAULT 0")
        # users
        await _add_col("users", "ref_code",       "TEXT")
        await _add_col("users", "referred_by",    "INTEGER")
        await _add_col("users", "ref_balance",    "REAL DEFAULT 0")
        await _add_col("users", "last_activity",  "DATETIME")
        await _add_col("users", "subscription_expired_notified_at", "DATETIME DEFAULT NULL")
        await _add_col("users", "reminder_3d_sent_at", "DATETIME DEFAULT NULL")
        await _add_col("users", "reminder_1d_sent_at", "DATETIME DEFAULT NULL")
        await _add_col("users", "welcome_pin_msg_id", "INTEGER DEFAULT NULL")
        await _add_col("users", "subscription_type", "TEXT DEFAULT NULL")
        # mailing_accounts — multiple accounts per mailing (round-robin)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS mailing_accounts (
                mailing_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                PRIMARY KEY (mailing_id, account_id),
                FOREIGN KEY (mailing_id) REFERENCES mailings(id) ON DELETE CASCADE,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        await self._conn.commit()

        # Error log table for mailing/account errors
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS mailing_error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                account_id INTEGER,
                mailing_id INTEGER,
                error_type TEXT NOT NULL,
                error_text TEXT,
                chat_identifier TEXT,
                created_at DATETIME DEFAULT (datetime('now', '+3 hours'))
            )
        """)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_error_log_user ON mailing_error_log (user_id, created_at DESC)"
        )
        await self._conn.commit()

        # Seed default subscription prices if not set
        for plan_days, default_price in [(7, 1.0), (30, 3.0)]:
            key = f"price_{plan_days}d"
            async with self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self._conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(default_price)),
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
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError) as e:
            # A single malformed timestamp in the DB (legacy data from an
            # older code path, manual edit, etc.) must not crash every
            # handler that happens to read the row it's in — treat it as
            # unset instead of raising, but log it since silently losing a
            # subscription_end/etc. is itself worth noticing and fixing.
            logger.warning(f"Failed to parse datetime value {value!r}: {e}")
            return None

    def _row_to_user(self, row) -> "User":
        keys = row.keys()
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            username=row["username"],
            subscription_end=self._parse_datetime(row["subscription_end"]),
            is_admin=bool(row["is_admin"]),
            ref_code=row["ref_code"] if "ref_code" in keys else None,
            referred_by=row["referred_by"] if "referred_by" in keys else None,
            ref_balance=float(row["ref_balance"]) if "ref_balance" in keys and row["ref_balance"] else 0.0,
            created_at=self._parse_datetime(row["created_at"]),
            last_activity=self._parse_datetime(row["last_activity"]) if "last_activity" in keys else None,
            subscription_expired_notified_at=self._parse_datetime(row["subscription_expired_notified_at"]) if "subscription_expired_notified_at" in keys else None,
            reminder_3d_sent_at=self._parse_datetime(row["reminder_3d_sent_at"]) if "reminder_3d_sent_at" in keys else None,
            reminder_1d_sent_at=self._parse_datetime(row["reminder_1d_sent_at"]) if "reminder_1d_sent_at" in keys else None,
            welcome_pin_msg_id=row["welcome_pin_msg_id"] if "welcome_pin_msg_id" in keys and row["welcome_pin_msg_id"] is not None else None,
            subscription_type=row["subscription_type"] if "subscription_type" in keys else None,
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
            proxy=row["proxy"] if "proxy" in keys else None,
            auto_subscribe_sponsors=bool(row["auto_subscribe_sponsors"]) if "auto_subscribe_sponsors" in keys and row["auto_subscribe_sponsors"] is not None else False,
            proxy_pool_id=row["proxy_pool_id"] if "proxy_pool_id" in keys else None,
        )

    # === Users ===
    async def update_user_username(self, telegram_id: int, username: Optional[str]):
        await self._conn.execute("UPDATE users SET username = ? WHERE telegram_id = ?", (username, telegram_id))
        await self._conn.commit()

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
        created_at = now_moscow().isoformat()
        await self._conn.execute(
            "INSERT OR IGNORE INTO users "
            "(telegram_id, username, is_admin, ref_code, created_at) VALUES (?, ?, ?, ?, ?)",
            (telegram_id, username, is_admin, ref_code, created_at),
        )
        await self._conn.commit()
        return await self.get_user(telegram_id)

    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> tuple:
        user = await self.get_user(telegram_id)
        if not user:
            from ..config import config
            is_admin = telegram_id in config.ADMIN_IDS
            user = await self.create_user(telegram_id, username, is_admin)
            return user, True
        if not user.ref_code:
            ref_code = secrets.token_urlsafe(6)
            await self._conn.execute("UPDATE users SET ref_code=? WHERE id=?", (ref_code, user.id))
            await self._conn.commit()
            user = await self.get_user(telegram_id)
        return user, False

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

    async def deduct_ref_balance(self, user_id: int, amount: float) -> bool:
        async with self._conn.execute(
            "UPDATE users SET ref_balance = ref_balance - ? "
            "WHERE id = ? AND ref_balance >= ?",
            (amount, user_id, amount),
        ) as cur:
            updated = cur.rowcount > 0
        await self._conn.commit()
        return updated

    async def update_user_pin_msg_id(self, user_id: int, msg_id: Optional[int]):
        await self._conn.execute(
            "UPDATE users SET welcome_pin_msg_id = ? WHERE id = ?", (msg_id, user_id)
        )
        await self._conn.commit()

    async def disable_user_autoresponders(self, user_id: int):
        """Disable all autoresponders for all accounts of a user."""
        await self._conn.execute(
            "UPDATE accounts SET autoresponder_enabled = 0, group_autoresponder_enabled = 0 WHERE user_id = ?",
            (user_id,)
        )
        await self._conn.commit()

    async def update_subscription(self, user_id: int, subscription_end: datetime):
        await self._conn.execute(
            "UPDATE users SET subscription_end = ?, subscription_expired_notified_at = NULL, "
            "reminder_3d_sent_at = NULL, reminder_1d_sent_at = NULL WHERE id = ?",
            (subscription_end.isoformat(), user_id),
        )
        await self._conn.commit()

    async def activate_free_tier(self, user_id: int):
        await self._conn.execute(
            "UPDATE users SET subscription_type = 'free_ad', subscription_end = NULL,"
            " subscription_expired_notified_at = NULL WHERE id = ?",
            (user_id,)
        )
        await self._conn.commit()

    async def deactivate_free_tier(self, user_id: int):
        await self._conn.execute(
            "UPDATE users SET subscription_type = NULL WHERE id = ?", (user_id,)
        )
        await self._conn.commit()

    @staticmethod
    def is_free_ad_active(user: "User") -> bool:
        """True if user is on free_ad tier and has no active paid subscription."""
        if user.subscription_type != "free_ad":
            return False
        if user.subscription_end and user.subscription_end > now_moscow():
            return False
        return True

    async def get_all_users(self) -> list[User]:
        async with self._conn.execute("SELECT * FROM users") as cur:
            return [self._row_to_user(r) for r in await cur.fetchall()]

    async def get_users_needing_subscription_check(self) -> list[User]:
        """Return only users with active or recently expired subscriptions — avoids full table scan."""
        async with self._conn.execute(
            """SELECT * FROM users
               WHERE subscription_end IS NOT NULL
                 """
        ) as cur:
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

    async def get_account_for_user(self, account_id: int, telegram_id: int) -> Optional[Account]:
        async with self._conn.execute(
            "SELECT a.* FROM accounts a JOIN users u ON u.id = a.user_id "
            "WHERE a.id = ? AND u.telegram_id = ?",
            (account_id, telegram_id),
        ) as cur:
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

    async def create_account(
        self,
        user_id: int,
        phone: str,
        api_id: int,
        api_hash: str,
        session_string: str,
        account_payment_invoice: Optional[str] = None,
    ) -> int:
        async with self._transaction_lock:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 15000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    async with conn.execute(
                        "SELECT id FROM accounts WHERE user_id = ? AND phone = ? "
                        "ORDER BY is_active ASC, id ASC LIMIT 1",
                        (user_id, phone),
                    ) as cur:
                        existing = await cur.fetchone()
                    if existing:
                        await conn.execute(
                            "UPDATE accounts SET api_id=?, api_hash=?, session_string=?, is_active=1 WHERE id=?",
                            (api_id, api_hash, session_string, existing["id"]),
                        )
                        # Re-authorizing an already known phone does not add
                        # an account.  Keep a fresh paid entitlement intact;
                        # an already consumed invoice is simply an idempotent
                        # retry of the successful original save.
                        await conn.commit()
                        return existing["id"]
                    cursor = await conn.execute(
                        "INSERT INTO accounts "
                        "(user_id, phone, api_id, api_hash, session_string, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, phone, api_id, api_hash, session_string, now_moscow().isoformat()),
                    )
                    if account_payment_invoice:
                        await self._consume_extra_account_payment(
                            conn, account_payment_invoice, user_id, existing_account=False,
                        )
                    await conn.commit()
                    return cursor.lastrowid
                except BaseException:
                    await conn.rollback()
                    raise

    @staticmethod
    async def _consume_extra_account_payment(conn, invoice_id: str, user_id: int, *, existing_account: bool):
        """Consume one durable extra-account entitlement in the same transaction
        that creates the account.  ``account_setup`` may safely be resumed after
        an FSM/UI failure; ``account_used`` is accepted only for an idempotent
        retry that re-saves the same already-existing phone."""
        async with conn.execute(
            "UPDATE payments SET status = 'account_used' "
            "WHERE invoice_id = ? AND user_id = ? AND status = 'account_setup'",
            (invoice_id, user_id),
        ) as cur:
            consumed = cur.rowcount > 0
        if consumed:
            return
        if existing_account:
            async with conn.execute(
                "SELECT status FROM payments WHERE invoice_id = ? AND user_id = ?",
                (invoice_id, user_id),
            ) as cur:
                payment = await cur.fetchone()
            if payment and payment["status"] == "account_used":
                return
        raise ValueError("Оплата дополнительного аккаунта уже использована или не подтверждена")

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
            async with self._conn.execute(
                "SELECT autoresponder_photo FROM accounts WHERE id = ?", (account_id,)
            ) as cur:
                row = await cur.fetchone()
            old_photo = row["autoresponder_photo"] if row else None
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ?, autoresponder_text = ?, autoresponder_photo = ? WHERE id = ?",
                (enabled, text, photo, account_id),
            )
        else:
            old_photo = None
            await self._conn.execute(
                "UPDATE accounts SET autoresponder_enabled = ? WHERE id = ?",
                (enabled, account_id),
            )
        await self._conn.commit()
        if old_photo and old_photo != photo and is_safe_media_path(old_photo):
            try:
                os.remove(old_photo)
            except OSError:
                pass

    async def update_group_autoresponder(self, account_id: int, enabled: bool, text: Optional[str] = None, photo: Optional[str] = None):
        if text is not None:
            async with self._conn.execute(
                "SELECT group_autoresponder_photo FROM accounts WHERE id = ?", (account_id,)
            ) as cur:
                row = await cur.fetchone()
            old_photo = row["group_autoresponder_photo"] if row else None
            await self._conn.execute(
                "UPDATE accounts SET group_autoresponder_enabled = ?, group_autoresponder_text = ?, group_autoresponder_photo = ? WHERE id = ?",
                (enabled, text, photo, account_id),
            )
        else:
            old_photo = None
            await self._conn.execute(
                "UPDATE accounts SET group_autoresponder_enabled = ? WHERE id = ?",
                (enabled, account_id),
            )
        await self._conn.commit()
        if old_photo and old_photo != photo and is_safe_media_path(old_photo):
            try:
                os.remove(old_photo)
            except OSError:
                pass

    async def update_notify_messages(self, account_id: int, enabled: bool):
        await self._conn.execute(
            "UPDATE accounts SET notify_messages = ? WHERE id = ?", (enabled, account_id)
        )
        await self._conn.commit()

    async def update_account_proxy(self, account_id: int, proxy: Optional[str], proxy_pool_id: Optional[int] = None):
        await self._conn.execute(
            "UPDATE accounts SET proxy = ?, proxy_pool_id = ? WHERE id = ?", (proxy, proxy_pool_id, account_id)
        )
        await self._conn.commit()

    async def is_proxy_problem_notified(self, account_id: int) -> bool:
        async with self._conn.execute(
            "SELECT proxy_problem_notified FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row["proxy_problem_notified"]) if row else False

    async def mark_proxy_problem_notified(self, account_id: int):
        await self._conn.execute(
            "UPDATE accounts SET proxy_problem_notified = 1 WHERE id = ?", (account_id,)
        )
        await self._conn.commit()

    async def clear_proxy_problem_notified(self, account_id: int):
        await self._conn.execute(
            "UPDATE accounts SET proxy_problem_notified = 0 WHERE id = ? AND proxy_problem_notified = 1", (account_id,)
        )
        await self._conn.commit()
        # A successful reconnect or proxy replacement starts a new proxy
        # lifecycle.  A delivery-retry marker from the old broken proxy must
        # not suppress the next genuinely new failure notification.
        try:
            await DeliveryJournal(self.db_path).forget(f"proxy-problem:{account_id}")
        except Exception as exc:
            logger.warning("Could not clear proxy delivery journal for account %s: %s", account_id, exc)

    async def get_flood_wait_until(self, account_id: int) -> Optional[datetime]:
        async with self._conn.execute(
            "SELECT flood_wait_until FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
            return self._parse_datetime(row["flood_wait_until"]) if row else None

    async def set_flood_wait_until(self, account_id: int, finish_at: Optional[datetime]):
        await self._conn.execute(
            "UPDATE accounts SET flood_wait_until = ? WHERE id = ?",
            (finish_at.isoformat() if finish_at else None, account_id),
        )
        await self._conn.commit()

    # === Proxy pool ===

    async def add_pool_proxy(self, proxy: str, added_by: Optional[int]) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO proxy_pool (proxy, added_by) VALUES (?, ?)", (proxy, added_by)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_pool_proxies(self) -> list[tuple["PoolProxy", int]]:
        """All pool proxies with the number of active accounts currently on each."""
        async with self._conn.execute(
            """SELECT p.id, p.proxy, p.is_active, p.added_by, p.created_at,
                      (SELECT COUNT(*) FROM accounts a WHERE a.proxy_pool_id = p.id AND a.is_active = 1) as cnt
               FROM proxy_pool p ORDER BY p.created_at DESC"""
        ) as cur:
            rows = await cur.fetchall()
            return [
                (
                    PoolProxy(
                        id=r["id"], proxy=r["proxy"], is_active=bool(r["is_active"]),
                        added_by=r["added_by"], created_at=self._parse_datetime(r["created_at"]),
                    ),
                    r["cnt"],
                )
                for r in rows
            ]

    async def delete_pool_proxy(self, pool_id: int, cap: int = POOL_PROXY_ACCOUNT_CAP) -> list[int]:
        """Reassign any accounts still on this proxy to another pool proxy
        before deleting the row — otherwise they'd silently keep using the
        now-deleted pool_id forever (no FK enforces this), invisible to
        get_pool_proxies(), and a later real failure of that same physical
        proxy would never notify admins again (deactivate_pool_proxy would
        match 0 rows for a pool_id that no longer exists). Returns the ids
        of accounts that got reassigned, so the caller can reconnect them."""
        affected = await self.reassign_all_accounts_off_dead_pool_proxy(pool_id, cap=cap)
        await self._conn.execute("DELETE FROM proxy_pool WHERE id = ?", (pool_id,))
        await self._conn.commit()
        return affected

    async def deactivate_pool_proxy(self, pool_id: int) -> bool:
        """Mark a pool proxy dead. Returns True only if it was still active
        (so callers don't re-notify for a proxy that's already been reported)."""
        cur = await self._conn.execute(
            "UPDATE proxy_pool SET is_active = 0 WHERE id = ? AND is_active = 1", (pool_id,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_least_loaded_pool_proxy(
        self, exclude_id: Optional[int] = None, cap: Optional[int] = None
    ) -> Optional["PoolProxy"]:
        inner = (
            "SELECT p.id, p.proxy, p.is_active, p.added_by, p.created_at, "
            "(SELECT COUNT(*) FROM accounts a WHERE a.proxy_pool_id = p.id AND a.is_active = 1) as cnt "
            "FROM proxy_pool p WHERE p.is_active = 1"
        )
        params: list = []
        if exclude_id is not None:
            inner += " AND p.id != ?"
            params.append(exclude_id)
        query = f"SELECT * FROM ({inner}) WHERE 1=1"
        if cap is not None:
            query += " AND cnt < ?"
            params.append(cap)
        query += " ORDER BY cnt ASC, id ASC LIMIT 1"
        async with self._conn.execute(query, params) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return PoolProxy(
                id=row["id"], proxy=row["proxy"], is_active=bool(row["is_active"]),
                added_by=row["added_by"], created_at=self._parse_datetime(row["created_at"]),
            )

    async def find_own_proxy_with_room(self, user_id: int, cap: int = 3) -> Optional[str]:
        """A proxy the user already typed in themselves (not pool-assigned) that
        has fewer than `cap` accounts on it — reused automatically for their next
        account instead of asking again."""
        async with self._conn.execute(
            """SELECT proxy, COUNT(*) as cnt FROM accounts
               WHERE user_id = ? AND is_active = 1 AND proxy_pool_id IS NULL
                     AND proxy IS NOT NULL AND proxy != ''
               GROUP BY proxy HAVING cnt < ?
               ORDER BY cnt DESC LIMIT 1""",
            (user_id, cap),
        ) as cur:
            row = await cur.fetchone()
            return row["proxy"] if row else None

    async def pick_proxy_for_new_account(
        self, user_id: int, cap: int = POOL_PROXY_ACCOUNT_CAP
    ) -> tuple[Optional[str], Optional[int], bool]:
        """Decide which proxy a not-yet-created account should connect
        through, WITHOUT writing anything — the account row doesn't exist
        until after Telegram login succeeds, so the caller uses this to pick
        the proxy the Telethon client itself connects with (otherwise the
        very first login happens unproxied and only gets a proxy assigned
        afterward, for future connections only).
        Returns (proxy, proxy_pool_id, pool_exhausted) — pool_exhausted is
        True only when pool proxies exist but every one is already at the
        per-proxy cap, so the caller can alert admins."""
        own_proxy = await self.find_own_proxy_with_room(user_id)
        if own_proxy:
            return own_proxy, None, False
        pool = await self.get_least_loaded_pool_proxy(cap=cap)
        if pool:
            return pool.proxy, pool.id, False
        any_pool = await self.get_least_loaded_pool_proxy()
        return None, None, any_pool is not None

    async def auto_assign_proxy(
        self, user_id: int, account_id: int, cap: int = POOL_PROXY_ACCOUNT_CAP
    ) -> Optional[str]:
        """Called when a new account was created without an explicit proxy: reuse
        the user's own under-capacity proxy if they have one, else hand out the
        least-loaded pool proxy, else leave the account without a proxy."""
        own_proxy = await self.find_own_proxy_with_room(user_id)
        if own_proxy:
            await self.update_account_proxy(account_id, own_proxy, proxy_pool_id=None)
            return own_proxy
        pool = await self.get_least_loaded_pool_proxy(cap=cap)
        if pool:
            await self.update_account_proxy(account_id, pool.proxy, proxy_pool_id=pool.id)
            return pool.proxy
        return None

    async def reassign_all_accounts_off_dead_pool_proxy(
        self, dead_pool_id: int, cap: int = POOL_PROXY_ACCOUNT_CAP
    ) -> list[int]:
        """Reassigns every account still pointing at a pool proxy that just
        died — not only the one whose connection attempt happened to be the
        one that discovered it. Without this, every other account sharing
        the dead proxy stays stuck on it until its own next connection
        attempt fails too, one at a time. Returns the ids of accounts that
        got reassigned, so the caller can reconnect them right away instead
        of waiting."""
        async with self._conn.execute(
            "SELECT id FROM accounts WHERE proxy_pool_id = ? AND is_active = 1",
            (dead_pool_id,),
        ) as cur:
            rows = await cur.fetchall()
        reassigned = []
        for row in rows:
            pool = await self.get_least_loaded_pool_proxy(exclude_id=dead_pool_id, cap=cap)
            if not pool:
                break  # no room anywhere else — leave the rest on the dead proxy
            await self.update_account_proxy(row["id"], pool.proxy, proxy_pool_id=pool.id)
            reassigned.append(row["id"])
        return reassigned

    async def backfill_missing_proxies(self, cap: int = POOL_PROXY_ACCOUNT_CAP) -> list[int]:
        """Backfill: assign a proxy (an under-capacity proxy the user
        already owns, else a pool slot) to every active account that
        doesn't have one yet. Idempotent — only touches rows with proxy IS
        NULL/'' — so it's safe to call both on every startup and whenever
        an admin adds a new pool proxy. Returns the ids of accounts that
        got assigned, so the caller can force already-connected clients to
        reconnect and actually pick up the new proxy (a DB-only update
        doesn't affect a client that's already running unproxied)."""
        async with self._conn.execute(
            "SELECT id, user_id FROM accounts WHERE is_active = 1 AND (proxy IS NULL OR proxy = '')"
        ) as cur:
            rows = await cur.fetchall()
        assigned = []
        for row in rows:
            proxy = await self.auto_assign_proxy(row["user_id"], row["id"], cap=cap)
            if proxy:
                assigned.append(row["id"])
        return assigned

    async def update_auto_subscribe_sponsors(self, account_id: int, enabled: bool):
        await self._conn.execute(
            "UPDATE accounts SET auto_subscribe_sponsors = ? WHERE id = ?", (enabled, account_id)
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

    async def get_needed_accounts(self) -> list[Account]:
        """Accounts that need an active Telethon connection: autoresponder on OR active mailing.
        Accounts of users with active subscription come first; expired-subscription users last."""
        async with self._conn.execute("""
            SELECT DISTINCT a.*,
                CASE
                    WHEN u.is_admin = 1 THEN 0
                    WHEN u.subscription_end IS NOT NULL
                         AND replace(u.subscription_end, 'T', ' ') > datetime('now', '+3 hours') THEN 1
                    WHEN u.subscription_type = 'free_ad' THEN 2
                    ELSE 3
                END AS _priority
            FROM accounts a
            JOIN users u ON a.user_id = u.id
            WHERE a.is_active = 1 AND (
                a.autoresponder_enabled = 1 OR
                a.group_autoresponder_enabled = 1 OR
                a.notify_messages = 1 OR
                EXISTS (
                    SELECT 1 FROM mailings m
                    WHERE m.account_id = a.id AND m.is_active = 1
                )
            )
            ORDER BY _priority ASC
        """) as cur:
            return [self._row_to_account(r) for r in await cur.fetchall()]

    async def get_registrations_by_period(self, period: str) -> list:
        # created_at is stored as isoformat() with a 'T' separator, while datetime('now', ...)
        # returns a space-separated string — raw text comparison would put every 'T...' row
        # ahead of the threshold regardless of actual time, so normalize before comparing.
        if period == "day":
            sql = ("SELECT strftime('%H:00', created_at) as label, COUNT(*) as cnt "
                   "FROM users WHERE replace(created_at, 'T', ' ') >= datetime('now', '+3 hours', '-1 day') "
                   "GROUP BY label ORDER BY label")
        elif period == "week":
            sql = ("SELECT strftime('%d.%m', created_at) as label, COUNT(*) as cnt "
                   "FROM users WHERE replace(created_at, 'T', ' ') >= datetime('now', '+3 hours', '-7 days') "
                   "GROUP BY label ORDER BY label")
        elif period == "month":
            sql = ("SELECT strftime('%d.%m', created_at) as label, COUNT(*) as cnt "
                   "FROM users WHERE replace(created_at, 'T', ' ') >= datetime('now', '+3 hours', '-30 days') "
                   "GROUP BY label ORDER BY label")
        else:  # year
            sql = ("SELECT strftime('%m.%Y', created_at) as label, COUNT(*) as cnt "
                   "FROM users WHERE replace(created_at, 'T', ' ') >= datetime('now', '+3 hours', '-1 year') "
                   "GROUP BY label ORDER BY label")
        async with self._conn.execute(sql) as cur:
            return [(r["label"], r["cnt"]) for r in await cur.fetchall()]

    async def count_inactive_accounts(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 0") as cur:
            return (await cur.fetchone())[0]

    async def purge_inactive_accounts(self) -> int:
        """Delete inactive accounts that are not referenced by any mailing."""
        async with self._conn.execute(
            "SELECT id, autoresponder_photo, group_autoresponder_photo FROM accounts a WHERE a.is_active = 0 "
            "AND NOT EXISTS (SELECT 1 FROM mailings m WHERE m.account_id = a.id) "
            "AND NOT EXISTS (SELECT 1 FROM mailing_accounts ma WHERE ma.account_id = a.id)"
        ) as cur:
            rows = await cur.fetchall()
        count = len(rows)
        await self._conn.execute(
            "DELETE FROM accounts WHERE is_active = 0 "
            "AND NOT EXISTS (SELECT 1 FROM mailings m WHERE m.account_id = accounts.id) "
            "AND NOT EXISTS (SELECT 1 FROM mailing_accounts ma WHERE ma.account_id = accounts.id)"
        )
        await self._conn.commit()
        for row in rows:
            async with self._conn.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (row["id"],)
            ) as cur:
                if await cur.fetchone():
                    continue
            for path in (row["autoresponder_photo"], row["group_autoresponder_photo"]):
                if path and is_safe_media_path(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        return count

    async def delete_account_permanently(self, account_id: int) -> None:
        """Hard-delete an account row — cascades to its mailings/targets/
        messages/autoresponder-history rows via ON DELETE CASCADE — and clean
        up its local photo files, including photo/video files attached to
        any of its mailings' messages (the CASCADE only removes DB rows, it
        never runs this application-level file cleanup, so without doing it
        here those files would leak on disk forever). Unlike
        deactivate_account/delete_account (which just flip is_active=0),
        this removes the row immediately."""
        async with self._conn.execute(
            "SELECT autoresponder_photo, group_autoresponder_photo FROM accounts WHERE id = ?",
            (account_id,),
        ) as cur:
            row = await cur.fetchone()
        async with self._conn.execute(
            """SELECT photo_path, video_path FROM mailing_messages
               WHERE mailing_id IN (SELECT id FROM mailings WHERE account_id = ?)""",
            (account_id,),
        ) as cur:
            message_rows = await cur.fetchall()
        await self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        await self._conn.commit()
        if row:
            for path in (row["autoresponder_photo"], row["group_autoresponder_photo"]):
                if path and is_safe_media_path(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        for msg_row in message_rows:
            paths = []
            if msg_row["photo_path"]:
                try:
                    parsed = json.loads(msg_row["photo_path"])
                    paths = parsed if isinstance(parsed, list) else [msg_row["photo_path"]]
                except (json.JSONDecodeError, TypeError):
                    paths = [msg_row["photo_path"]]
            if msg_row["video_path"]:
                paths.append(msg_row["video_path"])
            for path in paths:
                if not is_safe_media_path(path):
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def get_stale_accounts(self, days: int = 15) -> list[Account]:
        """Accounts that have at least one mailing of their own, where NONE
        of that account's mailings is currently active and NONE has had
        activity (a send, or — if it never sent — its creation) within the
        last `days` days. Scoped per-account (not per-user) on purpose: a
        user's brand-new account with no mailing yet must never be swept
        just because a DIFFERENT, older account of theirs went stale."""
        cutoff = (now_moscow() - timedelta(days=days)).isoformat()
        async with self._conn.execute(
            """SELECT a.* FROM accounts a
               WHERE a.is_active = 1
                 AND EXISTS (SELECT 1 FROM mailings m WHERE m.account_id = a.id)
                 AND NOT EXISTS (
                     SELECT 1 FROM mailings m2
                     WHERE m2.account_id = a.id
                       AND (m2.is_active = 1 OR COALESCE(m2.last_sent_at, m2.created_at) >= ?)
                 )""",
            (cutoff,),
        ) as cur:
            return [self._row_to_account(r) for r in await cur.fetchall()]

    # === Mailings ===
    def _row_to_mailing(self, r) -> Mailing:
        keys = r.keys() if hasattr(r, 'keys') else []
        return Mailing(
            id=r["id"], user_id=r["user_id"], account_id=r["account_id"],
            name=r["name"], is_active=bool(r["is_active"]),
            interval_seconds=r["interval_seconds"],
            active_hours_json=r["active_hours_json"],
            last_sent_at=self._parse_datetime(r["last_sent_at"]),
            created_at=self._parse_datetime(r["created_at"]),
            reply_mode=r["reply_mode"] if "reply_mode" in keys else None,
            reply_offset=r["reply_offset"] if "reply_offset" in keys else 1,
            reply_random_min=r["reply_random_min"] if "reply_random_min" in keys else 1,
            reply_random_max=r["reply_random_max"] if "reply_random_max" in keys else 5,
            keep_targets_on_ban=bool(r["keep_targets_on_ban"]) if "keep_targets_on_ban" in keys else False,
            account_rotation_mode=r["account_rotation_mode"] if "account_rotation_mode" in keys else "per_target",
            batch_size=r["batch_size"] if "batch_size" in keys and r["batch_size"] is not None else None,
            batch_pause=r["batch_pause"] if "batch_pause" in keys and r["batch_pause"] is not None else 10,
            hidden_tag_enabled=bool(r["hidden_tag_enabled"]) if "hidden_tag_enabled" in keys else False,
        )

    async def get_mailing(self, mailing_id: int) -> Optional[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE id = ?", (mailing_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return self._row_to_mailing(row)
        return None

    async def get_mailing_for_user(self, mailing_id: int, telegram_id: int) -> Optional[Mailing]:
        async with self._conn.execute(
            "SELECT m.* FROM mailings m JOIN users u ON u.id = m.user_id "
            "WHERE m.id = ? AND u.telegram_id = ?",
            (mailing_id, telegram_id),
        ) as cur:
            row = await cur.fetchone()
            return self._row_to_mailing(row) if row else None

    async def get_user_mailings(self, user_id: int) -> list[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE user_id = ?", (user_id,)) as cur:
            rows = await cur.fetchall()
            return [self._row_to_mailing(r) for r in rows]

    async def get_active_mailings(self) -> list[Mailing]:
        async with self._conn.execute("SELECT * FROM mailings WHERE is_active = 1") as cur:
            rows = await cur.fetchall()
            return [self._row_to_mailing(r) for r in rows]

    async def get_active_mailings_by_account(self, account_id: int) -> list[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE account_id = ? AND is_active = 1", (account_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_mailing(r) for r in rows]

    async def get_user_active_mailings(self, user_id: int) -> list[Mailing]:
        async with self._conn.execute(
            "SELECT * FROM mailings WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_mailing(r) for r in rows]

    async def create_mailing(self, user_id: int, account_id: int, name: str,
                              interval_seconds: int, active_hours_json: Optional[str] = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO mailings "
            "(user_id, account_id, name, interval_seconds, active_hours_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, account_id, name, interval_seconds, active_hours_json, now_moscow().isoformat()),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_mailing_status(self, mailing_id: int, is_active: bool):
        await self._conn.execute("UPDATE mailings SET is_active = ? WHERE id = ?", (is_active, mailing_id))
        await self._conn.commit()

    async def update_mailing_hidden_tag(self, mailing_id: int, enabled: bool):
        await self._conn.execute(
            "UPDATE mailings SET hidden_tag_enabled = ? WHERE id = ?",
            (1 if enabled else 0, mailing_id),
        )
        await self._conn.commit()

    async def update_mailing_batch(self, mailing_id: int, batch_size: Optional[int], batch_pause: int):
        await self._conn.execute(
            "UPDATE mailings SET batch_size = ?, batch_pause = ? WHERE id = ?",
            (batch_size, batch_pause, mailing_id)
        )
        await self._conn.commit()

    async def update_mailing_account(self, mailing_id: int, account_id: int):
        await self._conn.execute("UPDATE mailings SET account_id = ? WHERE id = ?", (account_id, mailing_id))
        await self._conn.commit()

    async def update_mailing_last_sent(self, mailing_id: int):
        await self._conn.execute(
            "UPDATE mailings SET last_sent_at = ? WHERE id = ?",
            (now_moscow().isoformat(), mailing_id),
        )
        await self._conn.commit()

    async def update_mailing_active_hours(self, mailing_id: int, active_hours_json: Optional[str]):
        await self._conn.execute(
            "UPDATE mailings SET active_hours_json = ? WHERE id = ?", (active_hours_json, mailing_id)
        )
        await self._conn.commit()

    async def update_mailing_reply_mode(self, mailing_id: int, mode: Optional[str],
                                         offset: int, rmin: int, rmax: int):
        await self._conn.execute(
            "UPDATE mailings SET reply_mode=?, reply_offset=?, reply_random_min=?, reply_random_max=? WHERE id=?",
            (mode, offset, rmin, rmax, mailing_id)
        )
        await self._conn.commit()

    async def delete_mailing(self, mailing_id: int):
        async with self._conn.execute(
            "SELECT photo_path, video_path FROM mailing_messages WHERE mailing_id = ?",
            (mailing_id,),
        ) as cur:
            rows = await cur.fetchall()
        await self._conn.execute("DELETE FROM mailings WHERE id = ?", (mailing_id,))
        await self._conn.commit()
        for row in rows:
            paths = []
            if row["photo_path"]:
                try:
                    parsed = json.loads(row["photo_path"])
                    paths = parsed if isinstance(parsed, list) else [row["photo_path"]]
                except (json.JSONDecodeError, TypeError):
                    paths = [row["photo_path"]]
            if row["video_path"]:
                paths.append(row["video_path"])
            for path in paths:
                if not is_safe_media_path(path):
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass

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
                    video_path=r["video_path"] if "video_path" in keys else None,
                    parse_mode=r["parse_mode"] if "parse_mode" in keys and r["parse_mode"] else 'html',
                    entities_json=r["entities_json"] if "entities_json" in keys else None,
                    forward_peer=r["forward_peer"] if "forward_peer" in keys else None,
                    forward_msg_id=r["forward_msg_id"] if "forward_msg_id" in keys else None,
                ))
            return result

    async def add_mailing_message(self, mailing_id: int, text: str, photo_path: Optional[str] = None,
                                   photo_paths: Optional[list[str]] = None, video_path: Optional[str] = None,
                                   parse_mode: str = 'html', entities_json: Optional[str] = None) -> int:
        stored = json.dumps(photo_paths) if photo_paths else photo_path
        cursor = await self._conn.execute(
            "INSERT INTO mailing_messages (mailing_id, text, photo_path, video_path, parse_mode, entities_json) VALUES (?, ?, ?, ?, ?, ?)",
            (mailing_id, text, stored, video_path, parse_mode, entities_json),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def add_mailing_forward(self, mailing_id: int, forward_peer: str, forward_msg_id: int) -> int:
        """Save a forward-type mailing message (no text/photo, just source ref)."""
        cursor = await self._conn.execute(
            "INSERT INTO mailing_messages (mailing_id, text, parse_mode, forward_peer, forward_msg_id) VALUES (?, ?, ?, ?, ?)",
            (mailing_id, "", 'html', forward_peer, forward_msg_id),
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
            "SELECT photo_path, video_path FROM mailing_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                if row["photo_path"]:
                    paths = []
                    try:
                        parsed = json.loads(row["photo_path"])
                        if isinstance(parsed, list):
                            paths = parsed
                    except (json.JSONDecodeError, TypeError):
                        paths = [row["photo_path"]]
                    for path in paths:
                        if not is_safe_media_path(path):
                            continue
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                if row["video_path"] and is_safe_media_path(row["video_path"]):
                    try:
                        os.remove(row["video_path"])
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
                    thread_id=r["thread_id"] if "thread_id" in keys else None,
                    is_forum=bool(r["is_forum"]) if "is_forum" in keys else False,
                    last_account_id=r["last_account_id"] if "last_account_id" in keys else None,
                ))
            return result

    async def add_mailing_target(self, mailing_id: int, chat_identifier: str, is_forum: bool = False) -> int:
        normalized = chat_identifier.strip()
        if not normalized.startswith('-') and not normalized.isdigit() and not normalized.startswith("https://t.me/+"):
            if not normalized.startswith('@'):
                normalized = f"@{normalized}"
        # De-dup: without this, re-importing the same list/folder (or adding
        # "@chan" after "chan" already normalized to the same value) creates
        # a second row for the same chat, and the mailing loop then sends to
        # it twice per cycle.
        async with self._conn.execute(
            "SELECT id FROM mailing_targets WHERE mailing_id = ? AND chat_identifier = ?",
            (mailing_id, normalized),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return existing["id"]
        cursor = await self._conn.execute(
            """INSERT INTO mailing_targets
               (mailing_id, chat_identifier, is_forum, interval_seconds)
               SELECT ?, ?, ?, interval_seconds
               FROM mailings
               WHERE id = ?""",
            (mailing_id, normalized, 1 if is_forum else 0, mailing_id),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_target_is_forum(self, target_id: int, value: bool):
        await self._conn.execute(
            "UPDATE mailing_targets SET is_forum=? WHERE id=?", (1 if value else 0, target_id)
        )
        await self._conn.commit()

    async def update_target_interval(self, target_id: int, interval_seconds: Optional[int]):
        await self._conn.execute(
            "UPDATE mailing_targets SET interval_seconds = ? WHERE id = ?",
            (interval_seconds, target_id),
        )
        await self._conn.commit()

    async def update_all_target_intervals(self, mailing_id: int, interval_seconds: int):
        # Also updates the mailing's own base interval_seconds — without
        # this, "apply to all" only touched per-target overrides while the
        # mailing's own interval (shown on its main info screen, and used
        # as the fallback for any target added later with no override of
        # its own) stayed frozen at whatever was set at creation. Users
        # doing "change the interval for the whole mailing" via this
        # button would see the change take effect on actual sends but the
        # overview screen's displayed interval never move, looking exactly
        # like the change didn't apply.
        await self._conn.execute(
            "UPDATE mailing_targets SET interval_seconds = ? WHERE mailing_id = ?",
            (interval_seconds, mailing_id),
        )
        await self._conn.execute(
            "UPDATE mailings SET interval_seconds = ? WHERE id = ?",
            (interval_seconds, mailing_id),
        )
        await self._conn.commit()

    async def get_dashboard_stats(self) -> dict:
        async with self._conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM users) AS total_users,
                (SELECT COUNT(*) FROM users WHERE subscription_end IS NOT NULL
                    AND replace(subscription_end, 'T', ' ') > datetime('now', '+3 hours')) AS active_subs,
                (SELECT COUNT(*) FROM accounts WHERE is_active = 1) AS accounts,
                (SELECT COUNT(*) FROM mailings WHERE is_active = 1) AS active_mailings,
                (SELECT COUNT(*) FROM mailings) AS total_mailings,
                (SELECT COUNT(*) FROM payments WHERE status = 'paid') AS paid_subs
        """) as cur:
            row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in (
            "total_users", "active_subs", "accounts", "active_mailings",
            "total_mailings", "paid_subs"
        )}

    async def count_free_tier_users(self) -> int:
        now = now_moscow().isoformat()
        async with self._conn.execute(
            """SELECT COUNT(*) AS cnt
               FROM users
               WHERE subscription_type = 'free_ad'
                 AND (subscription_end IS NULL
                      OR replace(subscription_end, 'T', ' ') <= replace(?, 'T', ' '))""",
            (now,),
        ) as cur:
            row = await cur.fetchone()
            return int(row["cnt"] or 0)

    async def get_free_tier_target_rows(self) -> list[dict]:
        now = now_moscow().isoformat()
        async with self._conn.execute(
            """SELECT
                   mt.id AS target_id,
                   mt.chat_identifier,
                   COALESCE(mt.interval_seconds, m.interval_seconds) AS delay_seconds,
                   m.id AS mailing_id,
                   m.user_id
               FROM mailing_targets mt
               JOIN mailings m ON m.id = mt.mailing_id
               JOIN users u ON u.id = m.user_id
               JOIN accounts a ON a.id = m.account_id
               WHERE m.is_active = 1
                 AND a.is_active = 1
                 AND u.subscription_type = 'free_ad'
                 AND (u.subscription_end IS NULL
                      OR replace(u.subscription_end, 'T', ' ') <= replace(?, 'T', ' '))""",
            (now,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def update_target_last_sent(self, target_id: int, account_id: Optional[int] = None):
        await self._conn.execute(
            "UPDATE mailing_targets SET last_sent_at = ?, last_account_id = ? WHERE id = ?",
            (now_moscow().isoformat(), account_id, target_id),
        )
        await self._conn.commit()

    async def delete_mailing_target(self, target_id: int):
        await self._conn.execute("DELETE FROM mailing_targets WHERE id = ?", (target_id,))
        await self._conn.commit()

    async def delete_sent_targets(self, mailing_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM mailing_targets WHERE mailing_id = ? AND last_sent_at IS NOT NULL",
            (mailing_id,)
        ) as cur:
            row = await cur.fetchone()
            count = row["cnt"] if row else 0
        await self._conn.execute(
            "DELETE FROM mailing_targets WHERE mailing_id = ? AND last_sent_at IS NOT NULL",
            (mailing_id,)
        )
        await self._conn.commit()
        return count

    # === Autoresponder History ===
    async def autoresponder_history_exists(self, account_id: int, sender_telegram_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM autoresponder_history WHERE account_id = ? AND sender_telegram_id = ?",
            (account_id, sender_telegram_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def claim_autoresponder_history(self, account_id: int, sender_telegram_id: int,
                                          message_text: Optional[str]) -> bool:
        async with self._conn.execute(
            "INSERT OR IGNORE INTO autoresponder_history "
            "(account_id, sender_telegram_id, message_text, responded_at) VALUES (?, ?, ?, ?)",
            (account_id, sender_telegram_id, message_text, now_moscow().isoformat()),
        ) as cur:
            claimed = cur.rowcount > 0
        await self._conn.commit()
        return claimed

    async def add_autoresponder_history(self, account_id: int, sender_telegram_id: int, message_text: Optional[str]):
        await self._conn.execute(
            "INSERT OR IGNORE INTO autoresponder_history "
            "(account_id, sender_telegram_id, message_text, responded_at) VALUES (?, ?, ?, ?)",
            (account_id, sender_telegram_id, message_text, now_moscow().isoformat()),
        )
        await self._conn.commit()

    async def clear_autoresponder_history(self, account_id: int):
        await self._conn.execute("DELETE FROM autoresponder_history WHERE account_id = ?", (account_id,))
        await self._conn.commit()

    async def clear_autoresponder_history_for_sender(self, account_id: int, sender_telegram_id: int):
        await self._conn.execute(
            "DELETE FROM autoresponder_history WHERE account_id = ? AND sender_telegram_id = ?",
            (account_id, sender_telegram_id),
        )
        await self._conn.commit()

    # === Payments ===
    async def create_payment(self, user_id: int, invoice_id: str, amount: float,
                              currency: str, plan_days: int = 30,
                              payment_method: str = "cryptobot",
                              price_usdt: Optional[float] = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO payments "
            "(user_id, invoice_id, amount, currency, plan_days, payment_method, created_at, price_usdt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, invoice_id, amount, currency, plan_days, payment_method, now_moscow().isoformat(),
             price_usdt if price_usdt is not None else amount),
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
                    amount=row["amount"], currency=row["currency"],
                    payment_method=row["payment_method"] if "payment_method" in keys else "cryptobot",
                    status=row["status"],
                    plan_days=row["plan_days"] if "plan_days" in keys and row["plan_days"] else 30,
                    created_at=self._parse_datetime(row["created_at"]),
                    paid_at=self._parse_datetime(row["paid_at"]),
                    price_usdt=row["price_usdt"] if "price_usdt" in keys and row["price_usdt"] is not None else row["amount"],
                )
        return None

    async def update_payment_status(self, invoice_id: str, status: str) -> bool:
        """Returns True if row was actually updated (not already in that status)."""
        paid_at = now_moscow().isoformat() if status == "paid" else None
        async with self._conn.execute(
            "UPDATE payments SET status = ?, paid_at = ? WHERE invoice_id = ? AND status != ?",
            (status, paid_at, invoice_id, status),
        ) as cur:
            updated = cur.rowcount > 0
        await self._conn.commit()
        return updated

    async def complete_subscription_payment(
        self,
        invoice_id: str,
        referral_amount: float,
    ) -> Optional[datetime]:
        """Atomically mark a payment paid and extend the related subscription."""
        async with self._transaction_lock:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 15000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    async with conn.execute(
                        """SELECT p.user_id, p.plan_days, p.status,
                                  u.subscription_end, u.referred_by
                           FROM payments p
                           JOIN users u ON u.id = p.user_id
                           WHERE p.invoice_id = ?
                             AND p.payment_method IN ('cryptobot', 'ton', 'platega')""",
                        (invoice_id,),
                    ) as cur:
                        row = await cur.fetchone()
                    if not row or row["status"] == "paid":
                        await conn.rollback()
                        return None

                    current_end = self._parse_datetime(row["subscription_end"])
                    now = now_moscow()
                    plan_days = int(row["plan_days"] or 30)
                    new_end = (
                        current_end + timedelta(days=plan_days)
                        if current_end and current_end > now
                        else now + timedelta(days=plan_days)
                    )

                    await conn.execute(
                        """UPDATE payments
                           SET status = 'paid', paid_at = ?
                           WHERE invoice_id = ?
                             AND payment_method IN ('cryptobot', 'ton', 'platega')
                             AND status != 'paid'""",
                        (now.isoformat(), invoice_id),
                    )
                    await conn.execute(
                        """UPDATE users
                           SET subscription_end = ?,
                               subscription_type = CASE
                                   WHEN subscription_type = 'free_ad' THEN NULL
                                   ELSE subscription_type
                               END,
                               subscription_expired_notified_at = NULL,
                               reminder_3d_sent_at = NULL,
                               reminder_1d_sent_at = NULL
                           WHERE id = ?""",
                        (new_end.isoformat(), row["user_id"]),
                    )

                    if row["referred_by"] and referral_amount > 0:
                        async with conn.execute(
                            "SELECT value FROM settings WHERE key = 'ref_percent'"
                        ) as cur:
                            setting = await cur.fetchone()
                        ref_percent = float(setting["value"]) if setting else 10.0
                        reward = round(referral_amount * ref_percent / 100, 4)
                        if reward > 0:
                            await conn.execute(
                                "UPDATE users SET ref_balance = ref_balance + ? WHERE id = ?",
                                (reward, row["referred_by"]),
                            )

                    await conn.commit()
                    # These DB flags are deliberately reset for the renewed
                    # subscription.  The sidecar is only a handoff journal,
                    # never a cross-subscription source of truth.
                    journal = DeliveryJournal(self.db_path)
                    for key in (
                        f"subscription-expired:{row['user_id']}",
                        f"subscription-reminder:{row['user_id']}:3",
                        f"subscription-reminder:{row['user_id']}:1",
                    ):
                        try:
                            await journal.forget(key)
                        except Exception as exc:
                            logger.warning("Could not clear subscription delivery journal %s: %s", key, exc)
                    return new_end
                except BaseException:
                    await conn.rollback()
                    raise

    async def get_pending_payments(self) -> list[Payment]:
        async with self._conn.execute("SELECT * FROM payments WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                keys = r.keys()
                result.append(Payment(
                    id=r["id"], user_id=r["user_id"], invoice_id=r["invoice_id"],
                    amount=r["amount"], currency=r["currency"], status=r["status"],
                    plan_days=r["plan_days"] if r["plan_days"] else 30,
                    created_at=self._parse_datetime(r["created_at"]),
                    paid_at=self._parse_datetime(r["paid_at"]),
                    price_usdt=r["price_usdt"] if "price_usdt" in keys and r["price_usdt"] is not None else r["amount"],
                ))
            return result

    async def get_platega_stats(self) -> dict:
        """Revenue stats for Platega payments."""
        async with self._conn.execute("""
            SELECT
                COALESCE(SUM(p.amount), 0) as total_rub,
                COALESCE(SUM(CASE WHEN date(p.paid_at) = date('now', '+3 hours', '-1 day') THEN p.amount ELSE 0 END), 0) as yesterday_rub,
                COALESCE(SUM(CASE WHEN date(p.paid_at) = date('now', '+3 hours') THEN p.amount ELSE 0 END), 0) as today_rub,
                COUNT(*) as total_count
            FROM payments p
            WHERE p.payment_method = 'platega' AND p.status = 'paid'
        """) as cur:
            row = await cur.fetchone()
        totals = {
            "total_rub": float(row["total_rub"] or 0),
            "yesterday_rub": float(row["yesterday_rub"] or 0),
            "today_rub": float(row["today_rub"] or 0),
            "total_count": int(row["total_count"] or 0),
        }
        async with self._conn.execute("""
            SELECT u.telegram_id, u.username, p.amount, p.paid_at, p.plan_days
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.payment_method = 'platega' AND p.status = 'paid'
            ORDER BY p.paid_at DESC
            LIMIT 20
        """) as cur:
            rows = await cur.fetchall()
        totals["recent"] = [dict(r) for r in rows]
        return totals

    async def get_payment_method_stats(self, method: str) -> dict:
        """Stats for a specific payment method: today/yesterday/week/month/total."""
        async with self._conn.execute("""
            SELECT
                COUNT(CASE WHEN date(paid_at) = date('now', '+3 hours') THEN 1 END)                          AS today_count,
                COALESCE(SUM(CASE WHEN date(paid_at) = date('now', '+3 hours') THEN amount END), 0)           AS today_amount,
                COUNT(CASE WHEN date(paid_at) = date('now', '+3 hours', '-1 day') THEN 1 END)                AS yesterday_count,
                COALESCE(SUM(CASE WHEN date(paid_at) = date('now', '+3 hours', '-1 day') THEN amount END), 0) AS yesterday_amount,
                COUNT(CASE WHEN paid_at >= date('now', '+3 hours', '-7 days') THEN 1 END)                    AS week_count,
                COALESCE(SUM(CASE WHEN paid_at >= date('now', '+3 hours', '-7 days') THEN amount END), 0)     AS week_amount,
                COUNT(CASE WHEN paid_at >= date('now', '+3 hours', 'start of month') THEN 1 END)             AS month_count,
                COALESCE(SUM(CASE WHEN paid_at >= date('now', '+3 hours', 'start of month') THEN amount END), 0) AS month_amount,
                COUNT(*)                                                                           AS total_count,
                COALESCE(SUM(amount), 0)                                                           AS total_amount
            FROM payments
            WHERE payment_method = ? AND status = 'paid'
        """, (method,)) as cur:
            row = await cur.fetchone()
        return {
            "today_count":     int(row["today_count"] or 0),
            "today_amount":    float(row["today_amount"] or 0),
            "yesterday_count": int(row["yesterday_count"] or 0),
            "yesterday_amount":float(row["yesterday_amount"] or 0),
            "week_count":      int(row["week_count"] or 0),
            "week_amount":     float(row["week_amount"] or 0),
            "month_count":     int(row["month_count"] or 0),
            "month_amount":    float(row["month_amount"] or 0),
            "total_count":     int(row["total_count"] or 0),
            "total_amount":    float(row["total_amount"] or 0),
        }

    async def get_revenue_by_currency(self) -> dict:
        async with self._conn.execute(
            "SELECT currency, COALESCE(SUM(amount), 0) as total FROM payments WHERE status='paid' GROUP BY currency"
        ) as cur:
            rows = await cur.fetchall()
        return {r["currency"]: float(r["total"]) for r in rows}

    async def update_last_activity(self, telegram_id: int) -> None:
        await self._conn.execute(
            "UPDATE users SET last_activity=? WHERE telegram_id=?",
            (now_moscow().isoformat(), telegram_id),
        )
        await self._conn.commit()

    async def get_hourly_activity(self, hours: int = 24) -> list:
        """Returns list of (hour 0-23, count) for the last N hours."""
        async with self._conn.execute(
            """
            SELECT CAST(strftime('%H', last_activity) AS INTEGER) as h, COUNT(*) as cnt
            FROM users
            WHERE replace(last_activity, 'T', ' ') >= datetime('now', '+3 hours', ?)
            GROUP BY h
            """,
            (f"-{hours} hours",),
        ) as cur:
            rows = await cur.fetchall()
        by_hour = {r["h"]: r["cnt"] for r in rows}
        return [(h, by_hour.get(h, 0)) for h in range(24)]

    async def has_paid_subscription(self, user_id: int) -> bool:
        """True if user has an active subscription (paid or promo)."""
        async with self._conn.execute(
            "SELECT subscription_end FROM users WHERE id = ? LIMIT 1", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                end = self._parse_datetime(row[0])
                return end is not None and end > now_moscow()
        return False

    async def set_subscription_expired_notified(self, user_id: int):
        await self._conn.execute(
            "UPDATE users SET subscription_expired_notified_at = ? WHERE id = ?",
            (now_moscow().isoformat(), user_id)
        )
        await self._conn.commit()

    async def set_user_reminder_sent(self, user_id: int, days: int):
        # Two literal queries instead of splicing a column name into SQL
        # text — avoids that pattern turning into an injection hole if a
        # future caller ever passes `days` through from something less
        # tightly controlled than this hardcoded 3-vs-1 ternary.
        if days == 3:
            query = "UPDATE users SET reminder_3d_sent_at = ? WHERE id = ?"
        else:
            query = "UPDATE users SET reminder_1d_sent_at = ? WHERE id = ?"
        await self._conn.execute(query, (now_moscow().isoformat(), user_id))
        await self._conn.commit()

    async def update_mailing_keep_targets(self, mailing_id: int, value: bool):
        await self._conn.execute(
            "UPDATE mailings SET keep_targets_on_ban = ? WHERE id = ?",
            (1 if value else 0, mailing_id)
        )
        await self._conn.commit()

    async def update_target_thread(self, target_id: int, thread_id: Optional[int]):
        await self._conn.execute(
            "UPDATE mailing_targets SET thread_id = ? WHERE id = ?",
            (thread_id, target_id)
        )
        await self._conn.commit()

    async def get_subscription_stats(self) -> list[dict]:
        """Return subscription info for users who have at least one paid payment."""
        async with self._conn.execute("""
            WITH ranked_payments AS (
                SELECT p.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY p.user_id
                           ORDER BY COALESCE(p.paid_at, p.created_at) DESC, p.id DESC
                       ) AS payment_rank
                FROM payments p
                WHERE p.status = 'paid'
            ),
            payment_counts AS (
                SELECT user_id, COUNT(*) AS purchase_count
                FROM ranked_payments
                GROUP BY user_id
            )
            SELECT u.id, u.telegram_id, u.username, u.subscription_end,
                   pc.purchase_count,
                   p.paid_at AS last_paid_at,
                   p.plan_days AS last_plan_days,
                   p.amount AS last_amount,
                   p.payment_method AS last_method
            FROM users u
            INNER JOIN payment_counts pc ON pc.user_id = u.id
            INNER JOIN ranked_payments p
                    ON p.user_id = u.id AND p.payment_rank = 1
            ORDER BY u.subscription_end IS NULL, u.subscription_end DESC
        """) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

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
    async def create_promocode(self, code: str, duration_days: int = 30, max_uses: int = 1, is_subscription: bool = False) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO promocodes "
            "(code, duration_days, max_uses, is_subscription, created_at) VALUES (?, ?, ?, ?, ?)",
            (code, duration_days, max_uses, 1 if is_subscription else 0, now_moscow().isoformat()),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def create_paid_promo_payment(self, user_id: int, invoice_id: str, plan_days: int):
        await self._conn.execute(
            "INSERT OR IGNORE INTO payments "
            "(user_id, invoice_id, amount, currency, payment_method, plan_days, status, "
            "created_at, paid_at) "
            "VALUES (?, ?, 0, 'USDT', 'promocode', ?, 'paid', ?, ?)",
            (
                user_id,
                invoice_id,
                plan_days,
                now_moscow().isoformat(),
                now_moscow().isoformat(),
            )
        )
        await self._conn.commit()

    async def get_promocode(self, code: str) -> Optional[Promocode]:
        async with self._conn.execute("SELECT * FROM promocodes WHERE code = ?", (code,)) as cur:
            row = await cur.fetchone()
            if row:
                keys = row.keys()
                return Promocode(
                    id=row["id"], code=row["code"], duration_days=row["duration_days"],
                    max_uses=row["max_uses"], uses_count=row["uses_count"],
                    is_used=bool(row["is_used"]), used_by=row["used_by"],
                    used_at=self._parse_datetime(row["used_at"]),
                    created_at=self._parse_datetime(row["created_at"]),
                    is_subscription=bool(row["is_subscription"]) if "is_subscription" in keys else False,
                )
        return None

    async def has_user_used_promocode(self, promocode_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM promocode_uses WHERE promocode_id = ? AND user_id = ?",
            (promocode_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def use_promocode(self, code: str, user_id: int, promocode_id: int) -> bool:
        async with self._transaction_lock:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 15000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    async with conn.execute(
                        "INSERT OR IGNORE INTO promocode_uses "
                        "(promocode_id, user_id, used_at) VALUES (?, ?, ?)",
                        (promocode_id, user_id, now_moscow().isoformat()),
                    ) as cur:
                        inserted = cur.rowcount > 0
                    if not inserted:
                        await conn.rollback()
                        return False
                    used_at = now_moscow()
                    async with conn.execute(
                        "UPDATE promocodes SET uses_count = uses_count + 1, "
                        "is_used = CASE WHEN uses_count + 1 >= max_uses THEN 1 ELSE 0 END, "
                        "used_by = ?, used_at = ? WHERE code = ? AND uses_count < max_uses",
                        (user_id, used_at.isoformat(), code),
                    ) as cur:
                        updated = cur.rowcount > 0
                    if not updated:
                        await conn.rollback()
                        return False

                    async with conn.execute(
                        "SELECT duration_days, is_subscription FROM promocodes WHERE id = ?",
                        (promocode_id,),
                    ) as cur:
                        promo = await cur.fetchone()
                    async with conn.execute(
                        "SELECT subscription_end FROM users WHERE id = ?",
                        (user_id,),
                    ) as cur:
                        user = await cur.fetchone()
                    if not promo or not user:
                        await conn.rollback()
                        return False

                    current_end = self._parse_datetime(user["subscription_end"])
                    new_end = (
                        current_end + timedelta(days=promo["duration_days"])
                        if current_end and current_end > used_at
                        else used_at + timedelta(days=promo["duration_days"])
                    )
                    await conn.execute(
                        """UPDATE users
                           SET subscription_end = ?,
                               subscription_type = CASE
                                   WHEN subscription_type = 'free_ad' THEN NULL
                                   ELSE subscription_type
                               END,
                               subscription_expired_notified_at = NULL,
                               reminder_3d_sent_at = NULL,
                               reminder_1d_sent_at = NULL
                           WHERE id = ?""",
                        (new_end.isoformat(), user_id),
                    )
                    if promo["is_subscription"]:
                        await conn.execute(
                            "INSERT OR IGNORE INTO payments "
                            "(user_id, invoice_id, amount, currency, payment_method, plan_days, "
                            "status, created_at, paid_at) "
                            "VALUES (?, ?, 0, 'USDT', 'promocode', ?, 'paid', ?, ?)",
                            (
                                user_id,
                                f"promo_{code}_{user_id}",
                                promo["duration_days"],
                                used_at.isoformat(),
                                used_at.isoformat(),
                            ),
                        )
                    await conn.commit()
                    return True
                except BaseException:
                    await conn.rollback()
                    raise

    async def get_all_promocodes(self) -> list[Promocode]:
        async with self._conn.execute("SELECT * FROM promocodes ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                keys = r.keys()
                result.append(Promocode(
                    id=r["id"], code=r["code"], duration_days=r["duration_days"],
                    max_uses=r["max_uses"], uses_count=r["uses_count"],
                    is_used=bool(r["is_used"]), used_by=r["used_by"],
                    used_at=self._parse_datetime(r["used_at"]),
                    created_at=self._parse_datetime(r["created_at"]),
                    is_subscription=bool(r["is_subscription"]) if "is_subscription" in keys else False,
                ))
            return result

    async def delete_promocode(self, promo_id: int):
        await self._conn.execute("DELETE FROM promocodes WHERE id = ?", (promo_id,))
        await self._conn.commit()

    async def update_promocode_max_uses(self, promo_id: int, new_max_uses: int):
        await self._conn.execute(
            "UPDATE promocodes SET max_uses = ?, "
            "is_used = CASE WHEN uses_count >= ? THEN 1 ELSE 0 END WHERE id = ?",
            (new_max_uses, new_max_uses, promo_id),
        )
        await self._conn.commit()

    # === Withdrawal Requests ===
    async def create_withdrawal_from_balance(
        self,
        user_id: int,
        amount: float,
        wallet: Optional[str] = None,
    ) -> bool:
        async with self._transaction_lock:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 15000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    async with conn.execute(
                        "UPDATE users SET ref_balance = ref_balance - ? "
                        "WHERE id = ? AND ref_balance >= ?",
                        (amount, user_id, amount),
                    ) as cur:
                        if cur.rowcount <= 0:
                            await conn.rollback()
                            return False
                    await conn.execute(
                        "INSERT INTO withdrawal_requests "
                        "(user_id, amount, wallet, created_at) VALUES (?, ?, ?, ?)",
                        (user_id, amount, wallet, now_moscow().isoformat()),
                    )
                    await conn.commit()
                    return True
                except BaseException:
                    await conn.rollback()
                    raise

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

    async def update_withdrawal_status(self, request_id: int, status: str) -> bool:
        async with self._conn.execute(
            "UPDATE withdrawal_requests SET status = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, request_id),
        ) as cur:
            updated = cur.rowcount > 0
        await self._conn.commit()
        return updated

    async def decline_withdrawal_request(self, request_id: int) -> bool:
        async with self._transaction_lock:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 15000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    async with conn.execute(
                        "SELECT user_id, amount FROM withdrawal_requests "
                        "WHERE id = ? AND status = 'pending'",
                        (request_id,),
                    ) as cur:
                        row = await cur.fetchone()
                    if not row:
                        await conn.rollback()
                        return False
                    await conn.execute(
                        "UPDATE withdrawal_requests SET status = 'declined' "
                        "WHERE id = ? AND status = 'pending'",
                        (request_id,),
                    )
                    await conn.execute(
                        "UPDATE users SET ref_balance = ref_balance + ? WHERE id = ?",
                        (row["amount"], row["user_id"]),
                    )
                    await conn.commit()
                    return True
                except BaseException:
                    await conn.rollback()
                    raise

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
            "INSERT OR REPLACE INTO required_channels "
            "(channel_id, channel_username, channel_title, added_at) VALUES (?, ?, ?, ?)",
            (channel_id, channel_username, channel_title, now_moscow().isoformat()),
        )
        await self._conn.commit()
        self._cache_invalidate("required_channels")

    async def remove_required_channel(self, channel_id: int):
        await self._conn.execute("DELETE FROM required_channels WHERE channel_id = ?", (channel_id,))
        await self._conn.commit()
        self._cache_invalidate("required_channels")

    # === Error Log ===
    async def add_error_log(
        self,
        user_id: int,
        error_type: str,
        error_text: Optional[str] = None,
        account_id: Optional[int] = None,
        mailing_id: Optional[int] = None,
        chat_identifier: Optional[str] = None,
    ):
        await self._conn.execute(
            "INSERT INTO mailing_error_log "
            "(user_id, account_id, mailing_id, error_type, error_text, chat_identifier, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, account_id, mailing_id, error_type,
             error_text[:500] if error_text else None, chat_identifier,
             now_moscow().isoformat()),
        )
        await self._conn.execute(
            """DELETE FROM mailing_error_log WHERE user_id = ? AND id NOT IN (
                   SELECT id FROM mailing_error_log WHERE user_id = ? ORDER BY created_at DESC LIMIT 100
               )""",
            (user_id, user_id),
        )
        await self._conn.commit()

    async def get_user_error_logs(self, user_id: int, limit: int = 20) -> list:
        async with self._conn.execute(
            """SELECT el.*,
                   COALESCE(a.name, a.phone) as account_display,
                   m.name as mailing_name
               FROM mailing_error_log el
               LEFT JOIN accounts a ON a.id = el.account_id
               LEFT JOIN mailings m ON m.id = el.mailing_id
               WHERE el.user_id = ?
               ORDER BY el.created_at DESC
               LIMIT ?""",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [ErrorLog(
                id=r["id"],
                user_id=r["user_id"],
                account_id=r["account_id"],
                mailing_id=r["mailing_id"],
                error_type=r["error_type"],
                error_text=r["error_text"],
                chat_identifier=r["chat_identifier"],
                created_at=self._parse_datetime(r["created_at"]),
                account_display=r["account_display"],
                mailing_name=r["mailing_name"],
            ) for r in rows]

    async def get_user_diagnostics(self, telegram_id: int) -> Optional[dict]:
        user = await self.get_user(telegram_id)
        if not user:
            return None

        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM accounts WHERE user_id = ? AND is_active = 1", (user.id,)
        ) as cur:
            row = await cur.fetchone()
            account_count = int(row["cnt"] or 0)

        async with self._conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) as active_cnt "
            "FROM mailings WHERE user_id = ?",
            (user.id,),
        ) as cur:
            row = await cur.fetchone()
            total_mailings = int(row["total"] or 0)
            active_mailings = int(row["active_cnt"] or 0)

        async with self._conn.execute(
            """SELECT COUNT(*) as cnt FROM mailing_targets mt
               JOIN mailings m ON m.id = mt.mailing_id
               WHERE m.user_id = ?""",
            (user.id,),
        ) as cur:
            row = await cur.fetchone()
            total_chats = int(row["cnt"] or 0)

        return {
            "user": user,
            "account_count": account_count,
            "total_mailings": total_mailings,
            "active_mailings": active_mailings,
            "total_chats": total_chats,
        }
