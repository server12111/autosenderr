from aiogram.exceptions import TelegramBadRequest


async def safe_edit(message, text: str, **kwargs):
    """Edit message text, silently ignoring 'message not found/not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "not found" in msg or "not modified" in msg or "message to edit not found" in msg:
            return
        raise
