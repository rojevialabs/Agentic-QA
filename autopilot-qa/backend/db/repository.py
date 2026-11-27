"""Generic persistence helpers for Document models."""
from typing import Any, TypeVar

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from db.models import Document

D = TypeVar("D", bound=Document)


async def insert(db: AsyncDatabase, doc: Document) -> str:
    result = await db[doc.collection].insert_one(doc.to_mongo())
    doc.id = str(result.inserted_id)
    return doc.id


async def get(db: AsyncDatabase, cls: type[D], id: str) -> D | None:
    raw = await db[cls.collection].find_one({"_id": ObjectId(id)})
    return cls.from_mongo(raw) if raw else None


async def find(
    db: AsyncDatabase,
    cls: type[D],
    filter: dict[str, Any] | None = None,
    *,
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
) -> list[D]:
    query = dict(filter or {})
    for name in cls.object_id_fields:
        if isinstance(query.get(name), str):
            query[name] = ObjectId(query[name])
    cursor = db[cls.collection].find(query, sort=sort, limit=limit)
    return [cls.from_mongo(raw) async for raw in cursor]
