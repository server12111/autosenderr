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
    # ``TONCENTRE_API_KEY`` was the name used by the hosting panel and older
    # deployments.  Keep accepting it so a normal server migration does not
    # silently disable the TonCenter key; the correctly spelled name wins when
    # both are present.
    TONCENTER_API_KEY: str = os.getenv("TONCENTER_API_KEY") or os.getenv("TONCENTRE_API_KEY", "")
    TON_SUBSCRIPTION_PRICE: float = _safe_float("TON_SUBSCRIPTION_PRICE", 0.5)
    TON_EXTRA_ACCOUNT_PRICE: float = _safe_float("TON_EXTRA_ACCOUNT_PRICE", 0.05)

    # Telegram Stars (XTR) payments — fixed prices in stars, not USD-derived,
    # set directly by the bot owner rather than computed from SUBSCRIPTION_PRICE.
    STARS_PRICE_1D: int = _safe_int("STARS_PRICE_1D", 19)
    STARS_PRICE_7D: int = _safe_int("STARS_PRICE_7D", 99)
    STARS_PRICE_30D: int = _safe_int("STARS_PRICE_30D", 299)
    STARS_EXTRA_ACCOUNT_PRICE: int = _safe_int("STARS_EXTRA_ACCOUNT_PRICE", 19)

    PRIVACY_URL: str = os.getenv("PRIVACY_URL", "https://telegra.ph/Politika-konfidencialnosti-05-31-36")
    TERMS_URL: str = os.getenv("TERMS_URL", "https://telegra.ph/Polzovatelskoe-soglashenie-05-31-24")

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bot.db")
    SESSIONS_PATH: str = os.getenv("SESSIONS_PATH", "sessions")

    MAILING_DEBUG: bool = os.getenv("MAILING_DEBUG", "false").lower() == "true"

    # Below this many seconds-per-target, a mailing needs more messages/sec
    # from one account than is safe — Telegram's FloodWait/frozen-account
    # anti-spam kicks in well before this. Used to warn when interval is too
    # short for the number of targets (see callback_create_targets_done).
    MIN_SAFE_SECONDS_PER_TARGET: float = _safe_float("MIN_SAFE_SECONDS_PER_TARGET", 3.0)

    # New accounts are more likely to get FloodWait/frozen if they mail at
    # full speed immediately — accounts younger than this many days get their
    # send interval multiplied (see MailingService._mailing_loop).
    WARMUP_ACCOUNT_DAYS: int = _safe_int("WARMUP_ACCOUNT_DAYS", 5)
    WARMUP_MULTIPLIER: float = _safe_float("WARMUP_MULTIPLIER", 2.5)

    # Default Telegram API credentials (from official apps)
    DEFAULT_API_ID: int = _safe_int("DEFAULT_API_ID", _safe_int("API_ID", 2040))
    DEFAULT_API_HASH: str = os.getenv("DEFAULT_API_HASH") or os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")


def get_stars_price(plan_days: int) -> int:
    """Fixed Stars price per plan — the 7/30-day USD prices don't convert to
    a single consistent stars-per-dollar rate (they were set independently),
    so this is a direct plan_days -> stars lookup, not a computed conversion.
    Falls back to a per-day rate derived from the 30-day price for any
    plan_days not explicitly priced (there are currently only three)."""
    fixed = {1: config.STARS_PRICE_1D, 7: config.STARS_PRICE_7D, 30: config.STARS_PRICE_30D}
    if plan_days in fixed:
        return fixed[plan_days]
    return max(1, round(config.STARS_PRICE_30D / 30 * plan_days))


config = Config()

