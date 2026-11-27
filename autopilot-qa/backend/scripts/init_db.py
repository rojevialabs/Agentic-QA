"""Initialise the autopilot_qa database: python -m scripts.init_db"""
import asyncio

from db.mongo_client import get_db, init_db


async def main() -> None:
    db = get_db()
    await init_db(db)
    names = sorted(await db.list_collection_names())
    print(f"{db.name}: {', '.join(names)}")
    await db.client.close()


if __name__ == "__main__":
    asyncio.run(main())
