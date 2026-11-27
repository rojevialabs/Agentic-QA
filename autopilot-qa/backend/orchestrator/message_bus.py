"""Shared message bus on the capped `messages` collection (spec §5, roadmap contract C3)."""
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pymongo import CursorType
from pymongo.asynchronous.database import AsyncDatabase

from db.models import Message


class MessageBus:
    def __init__(self, db: AsyncDatabase) -> None:
        self._messages = db[Message.collection]

    async def publish(self, msg: Message) -> None:
        await self._messages.insert_one(msg.model_dump())

    async def history(self, run_id: str) -> list[Message]:
        cursor = self._messages.find({"run_id": run_id}, sort=[("$natural", 1)])
        return [Message.model_validate(doc) async for doc in cursor]

    async def subscribe(self, run_id: str, to_agent: str | None = None, *, replay: bool = False,
                        poll_interval: float = 0.2) -> AsyncIterator[Message]:
        """Tail the bus for one run. With replay, first yield everything already published for it."""
        query: dict[str, Any] = {"run_id": run_id}
        if to_agent:
            query["to_agent"] = to_agent
        last_id = None
        if not replay:
            newest = await self._messages.find_one({}, sort=[("$natural", -1)])
            last_id = newest["_id"] if newest else None
        while True:
            resume = {**query, "_id": {"$gt": last_id}} if last_id is not None else query
            cursor = self._messages.find(resume, cursor_type=CursorType.TAILABLE_AWAIT)
            try:
                # A tailable cursor dies when its first batch is empty; recreate it after a short pause.
                while cursor.alive:
                    async for doc in cursor:
                        last_id = doc["_id"]
                        yield Message.model_validate(doc)
                    await asyncio.sleep(poll_interval)
            finally:
                await cursor.close()
            await asyncio.sleep(poll_interval)
