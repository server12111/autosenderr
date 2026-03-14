SCHEMA = """
-- Пользователи бота
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    subscription_end DATETIME,
    is_admin BOOLEAN DEFAULT FALSE,
    ref_code TEXT UNIQUE,
    referred_by INTEGER,
    ref_balance REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (referred_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Telegram аккаунты (userbot)
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT,
    phone TEXT NOT NULL,
    session_string TEXT,
    api_id INTEGER NOT NULL,
    api_hash TEXT NOT NULL,
    autoresponder_enabled BOOLEAN DEFAULT FALSE,
    autoresponder_text TEXT,
    notify_messages BOOLEAN DEFAULT FALSE,
    group_autoresponder_enabled BOOLEAN DEFAULT FALSE,
    group_autoresponder_text TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Рассылки
CREATE TABLE IF NOT EXISTS mailings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    interval_seconds INTEGER NOT NULL DEFAULT 300,
    active_hours_json TEXT,
    last_sent_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- Тексты для рандомизации
CREATE TABLE IF NOT EXISTS mailing_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailing_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    photo_path TEXT,
    parse_mode TEXT DEFAULT 'html',
    FOREIGN KEY (mailing_id) REFERENCES mailings(id) ON DELETE CASCADE
);

-- Целевые группы/чаты
CREATE TABLE IF NOT EXISTS mailing_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailing_id INTEGER NOT NULL,
    chat_identifier TEXT NOT NULL,
    interval_seconds INTEGER,
    last_sent_at DATETIME,
    FOREIGN KEY (mailing_id) REFERENCES mailings(id) ON DELETE CASCADE
);

-- История автоответов
CREATE TABLE IF NOT EXISTS autoresponder_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    sender_telegram_id INTEGER NOT NULL,
    message_text TEXT,
    responded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
    UNIQUE(account_id, sender_telegram_id)
);

-- История платежей
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    invoice_id TEXT UNIQUE,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDT',
    payment_method TEXT DEFAULT 'cryptobot',
    plan_days INTEGER DEFAULT 30,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Настройки бота
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Промокоды
CREATE TABLE IF NOT EXISTS promocodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    duration_days INTEGER NOT NULL DEFAULT 30,
    max_uses INTEGER NOT NULL DEFAULT 1,
    uses_count INTEGER NOT NULL DEFAULT 0,
    is_used BOOLEAN DEFAULT FALSE,
    used_by INTEGER,
    used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (used_by) REFERENCES users(id) ON DELETE SET NULL
);

-- История использования промокодов
CREATE TABLE IF NOT EXISTS promocode_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promocode_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (promocode_id) REFERENCES promocodes(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(promocode_id, user_id)
);

-- Запросы на вывод реферального баланса
CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    wallet TEXT,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Обязательные каналы для подписки
CREATE TABLE IF NOT EXISTS required_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL UNIQUE,
    channel_username TEXT,
    channel_title TEXT NOT NULL,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
