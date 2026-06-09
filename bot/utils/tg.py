from aiogram.exceptions import TelegramBadRequest


async def safe_edit(message, text: str, **kwargs):
    """Edit message text, silently ignoring 'message not found/not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        pass
