import pytest
from pymongo.errors import DuplicateKeyError

from db.models import SelectorMap
from db.mongo_client import MESSAGES_CAP_BYTES, init_db
from db.repository import insert

ALL_COLLECTIONS = {"requirements", "test_cases", "scripts", "executions", "bug_reports",
                   "agent_traces", "coverage", "selector_maps", "messages"}


async def test_creates_every_collection(db):
    await init_db(db)
    assert ALL_COLLECTIONS <= set(await db.list_collection_names())


async def test_messages_is_capped(db):
    await init_db(db)
    options = await db["messages"].options()
    assert options["capped"] is True
    assert options["size"] == MESSAGES_CAP_BYTES


async def test_run_scoped_indexes(db):
    await init_db(db)
    traces = await db["agent_traces"].index_information()
    assert any(ix["key"] == [("run_id", 1), ("timestamp", 1)] for ix in traces.values())
    requirements = await db["requirements"].index_information()
    assert any(ix["key"] == [("run_id", 1)] and ix.get("unique") for ix in requirements.values())


async def test_seeds_selector_map_v1(db):
    await init_db(db)
    seeded = SelectorMap.from_mongo(await db["selector_maps"].find_one({"version": 1}))
    assert seeded.created_by == "seed"
    assert seeded.status == "active"
    assert seeded.map["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-cart-btn"


async def test_is_idempotent(db):
    await init_db(db)
    await init_db(db)
    assert await db["selector_maps"].count_documents({}) == 1


async def test_refuses_an_uncapped_messages_collection(db):
    await db.create_collection("messages")
    with pytest.raises(RuntimeError, match="not capped"):
        await init_db(db)


async def test_active_versions_are_unique_but_proposals_may_share_them(db):
    await init_db(db)
    await insert(db, SelectorMap(version=1, map={}, created_by="healer", status="pending_review"))
    with pytest.raises(DuplicateKeyError):
        await insert(db, SelectorMap(version=1, map={}, created_by="healer"))
