"""MongoDB connection and idempotent schema setup for the autopilot_qa database."""
import json

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from db.models import DOCUMENT_MODELS, Message, SelectorMap
from settings import BACKEND_DIR, Settings, get_settings

MESSAGES_CAP_BYTES = 64 * 1024 * 1024
SEED_SELECTOR_MAP = BACKEND_DIR / "config" / "selector_map.v1.json"

# collection -> [(keys, index options)]
INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    "requirements": [([("run_id", ASCENDING)], {"unique": True})],
    "test_cases": [([("run_id", ASCENDING)], {}), ([("requirement_id", ASCENDING)], {})],
    "scripts": [([("run_id", ASCENDING)], {}), ([("test_case_id", ASCENDING)], {})],
    "executions": [([("run_id", ASCENDING)], {}), ([("script_id", ASCENDING)], {})],
    "bug_reports": [([("run_id", ASCENDING)], {}), ([("execution_id", ASCENDING)], {})],
    "agent_traces": [([("run_id", ASCENDING), ("timestamp", ASCENDING)], {})],
    "coverage": [([("run_id", ASCENDING), ("requirement_id", ASCENDING)], {"unique": True})],
    # Only one *active* map per version; pending_review proposals may reuse a number.
    "selector_maps": [([("version", ASCENDING)], {"unique": True, "partialFilterExpression": {"status": "active"}})],
    "messages": [([("run_id", ASCENDING)], {})],
}

_client: AsyncMongoClient | None = None


def create_client(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)


def get_db(settings: Settings | None = None) -> AsyncDatabase:
    """The process-wide database handle (one client per process)."""
    global _client
    settings = settings or get_settings()
    if _client is None:
        _client = create_client(settings.mongo_uri)
    return _client[settings.mongo_db]


async def init_db(db: AsyncDatabase) -> None:
    """Create collections, the capped message bus, indexes and the v1 selector map. Safe to re-run."""
    existing = set(await db.list_collection_names())

    if Message.collection not in existing:
        await db.create_collection(Message.collection, capped=True, size=MESSAGES_CAP_BYTES)
    elif not (await db[Message.collection].options()).get("capped"):
        raise RuntimeError(f"'{Message.collection}' exists but is not capped; drop it and re-run init_db")

    for model in DOCUMENT_MODELS:
        if model.collection not in existing:
            await db.create_collection(model.collection)

    for name, specs in INDEXES.items():
        for keys, options in specs:
            await db[name].create_index(keys, **options)

    await seed_selector_map(db)


async def seed_selector_map(db: AsyncDatabase) -> None:
    if await db[SelectorMap.collection].find_one({"version": 1, "status": "active"}):
        return
    seed = json.loads(SEED_SELECTOR_MAP.read_text(encoding="utf-8"))
    doc = SelectorMap(version=seed["version"], map=seed["services"], created_by="seed")
    await db[SelectorMap.collection].insert_one(doc.to_mongo())
