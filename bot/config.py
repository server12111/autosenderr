import os
from dotenv import load_dotenv

load_dotenv()


def _safe_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        print(f"[config] WARNING: invalid value for {key}, using default {default}")
        return default


def _safe_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        print(f"[config] WARNING: invalid value for {key}, using default {default}")
        return default


def _safe_admin_ids() -> list:
    raw = os.getenv("ADMIN_IDS", "") or os.getenv("ADMIN_ID", "")
    result = []
    for x in raw.split(","):
        x = x.strip()
        if x:
            try:
                result.append(int(x))
            except ValueError:
                print(f"[config] WARNING: invalid admin id '{x}', skipping")
    return result


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    CRYPTOBOT_TESTNET: bool = os.getenv("CRYPTOBOT_TESTNET", "false").lower() == "true"

    ADMIN_IDS: list = _safe_admin_ids()

    SUBSCRIPTION_PRICE: float = _safe_float("SUBSCRIPTION_PRICE", 3.0)

    # Account limits
    FREE_ACCOUNTS_LIMIT: int = 10
    EXTRA_ACCOUNT_PRICE: float = 0.2
    SUBSCRIPTION_CURRENCY: str = os.getenv("SUBSCRIPTION_CURRENCY", "USDT")

    # Platega SBP payments (rubles)
    PLATEGA_MERCHANT_ID: str = os.getenv("PLATEGA_MERCHANT_ID", "")
    PLATEGA_SECRET: str = os.getenv("PLATEGA_SECRET", "")

    # TON payments
    TON_WALLET_ADDRESS: str = os.getenv("TON_WALLET_ADDRESS", "")
    TONCENTER_API_KEY: str = os.getenv("TONCENTER_API_KEY", "")
    TON_SUBSCRIPTION_PRICE: float = _safe_float("TON_SUBSCRIPTION_PRICE", 0.5)
    TON_EXTRA_ACCOUNT_PRICE: float = _safe_float("TON_EXTRA_ACCOUNT_PRICE", 0.05)

    PRIVACY_URL: str = os.getenv("PRIVACY_URL", "https://telegra.ph/Politika-konfidencialnosti-05-31-36")
    TERMS_URL: str = os.getenv("TERMS_URL", "https://telegra.ph/Polzovatelskoe-soglashenie-05-31-24")

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bot.db")
    SESSIONS_PATH: str = os.getenv("SESSIONS_PATH", "sessions")

    MAILING_DEBUG: bool = os.getenv("MAILING_DEBUG", "false").lower() == "true"

    # Default Telegram API credentials (from official apps)
    DEFAULT_API_ID: int = _safe_int("DEFAULT_API_ID", _safe_int("API_ID", 2040))
    DEFAULT_API_HASH: str = os.getenv("DEFAULT_API_HASH") or os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")


config = Config()

