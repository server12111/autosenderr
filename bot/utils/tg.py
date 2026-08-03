import asyncio

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError


async def safe_edit(message, text: str, **kwargs):
    """Edit message text, silently ignoring 'message not found/not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "not found" in msg or "not modified" in msg or "message to edit not found" in msg:
            return
        raise


async def _retry_network(coro_fn, retries: int):
    """Run coro_fn() up to `retries` extra times on TelegramNetworkError (transient
    connectivity blips), with a short backoff. Raises the last error if still failing."""
    for attempt in range(retries + 1):
        try:
            return await coro_fn()
        except TelegramNetworkError:
            if attempt == retries:
                raise
            await asyncio.sleep(1 + attempt)


async def edit_or_answer(callback, text: str, retries: int = 2, delete_on_fallback: bool = False, **kwargs):
    """Edit the callback's message, falling back to sending a fresh message (like
    the admin panel's old 'try: edit_text() except Exception: answer()' pattern —
    edit can fail for many reasons: not modified, original deleted, media/text
    type mismatch, etc). The difference from the old pattern: each step now
    retries transient TelegramNetworkError a couple of times with backoff before
    giving up, so a brief connectivity drop self-heals instead of immediately
    falling through to the fallback and then failing that too — which previously
    surfaced as an unhandled exception (a generic "error, try again" alert)."""
    try:
        await _retry_network(lambda: callback.message.edit_text(text, **kwargs), retries)
        return
    except Exception:
        pass
    if delete_on_fallback:
        try:
            await _retry_network(lambda: callback.message.delete(), retries)
        except Exception:
            pass
    await _retry_network(lambda: callback.message.answer(text, **kwargs), retries)
