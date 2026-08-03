import asyncio
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """Collect media group messages into a single album list."""

    LATENCY = 0.5  # seconds to wait for all messages in a group

    def __init__(self):
        # album_id -> {"messages": [...], "version": int}
        self.albums: dict[str, dict[str, Any]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        album_id = event.media_group_id
        entry = self.albums.setdefault(album_id, {"messages": [], "version": 0})
        entry["messages"].append(event)
        entry["version"] += 1
        my_version = entry["version"]

        # Re-arm the wait on every arrival instead of anchoring it to this
        # message's own arrival time — otherwise a late item in the group
        # (network jitter, slow media download) gets dispatched separately
        # as its own one-item "album" once the earlier messages' timers fire.
        await asyncio.sleep(self.LATENCY)

        if entry["version"] != my_version:
            return  # A newer message for this album arrived — it will dispatch instead

        self.albums.pop(album_id, None)
        data["album"] = entry["messages"]
        return await handler(event, data)
