import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    CRYPTOBOT_TESTNET: bool = os.getenv("CRYPTOBOT_TESTNET", "false").lower() == "true"

    _admin_ids_str = os.getenv("ADMIN_IDS", "") or os.getenv("ADMIN_ID", "")
    ADMIN_IDS: list[int] = [int(x.strip()) for x in _admin_ids_str.split(",") if x.strip()]

    SUBSCRIPTION_PRICE: float = float(os.getenv("SUBSCRIPTION_PRICE", "3"))

    # Account limits
    FREE_ACCOUNTS_LIMIT: int = 10
    EXTRA_ACCOUNT_PRICE: float = 0.2
    SUBSCRIPTION_CURRENCY: str = os.getenv("SUBSCRIPTION_CURRENCY", "USDT")

    # TON payments
    TON_WALLET_ADDRESS: str = os.getenv("TON_WALLET_ADDRESS", "")
    TONCENTER_API_KEY: str = os.getenv("TONCENTER_API_KEY", "")
    TON_SUBSCRIPTION_PRICE: float = float(os.getenv("TON_SUBSCRIPTION_PRICE", "0.5"))
    TON_EXTRA_ACCOUNT_PRICE: float = float(os.getenv("TON_EXTRA_ACCOUNT_PRICE", "0.05"))

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bot.db")
    SESSIONS_PATH: str = os.getenv("SESSIONS_PATH", "sessions")

    # Default Telegram API credentials (from official apps)
    DEFAULT_API_ID: int = int(os.getenv("DEFAULT_API_ID", "2040"))
    DEFAULT_API_HASH: str = os.getenv("DEFAULT_API_HASH", "b18441a1ff607e10a989891a5462e627")


config = Config()

