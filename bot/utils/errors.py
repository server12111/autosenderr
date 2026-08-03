from telethon.errors import FloodWaitError

_FRIENDLY_MESSAGES = {
    "PasswordHashInvalidError": "❌ Неверный пароль. Попробуйте ещё раз.",
    "PhoneCodeInvalidError": "❌ Неверный код. Проверьте код и введите его ещё раз.",
    "PhoneCodeExpiredError": "❌ Код устарел. Начните добавление аккаунта заново, чтобы получить новый код.",
    "PhoneNumberBannedError": "❌ Этот номер заблокирован Telegram и не может быть использован.",
    "PhoneNumberInvalidError": "❌ Неверный номер телефона. Проверьте формат: +380991234567",
    "InviteHashExpiredError": "❌ Ссылка на папку устарела. Попробуйте получить новую ссылку.",
    "InviteHashInvalidError": "❌ Ссылка на папку недействительна. Проверьте ссылку и попробуйте снова.",
}


def friendly_error(e: Exception, default: str = "❌ Не удалось выполнить действие. Попробуйте ещё раз.") -> str:
    """Map a Telethon/Telegram exception to a short, jargon-free Russian message
    for end users. Falls back to `default` for anything not in the map — never
    leaks the raw exception text/class name to a regular user."""
    if isinstance(e, FloodWaitError):
        return f"⏳ Telegram временно ограничил действие. Подождите {e.seconds} сек. и попробуйте снова."
    return _FRIENDLY_MESSAGES.get(type(e).__name__, default)
