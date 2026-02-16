import asyncio
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """Collect media group messages into a single album list."""

    LATENCY = 0.5  # seconds to wait for all messages in a group

    def __init__(self):
        self.albums: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        album_id = event.media_group_id
        self.albums.setdefault(album_id, []).append(event)

        await asyncio.sleep(self.LATENCY)

        album = self.albums.pop(album_id, None)
        if not album:
            return  # Already handled by another message in the group

        data["album"] = album
        return await handler(event, data)
