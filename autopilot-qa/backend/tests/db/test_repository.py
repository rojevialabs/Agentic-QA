from bson import ObjectId

from db.models import Requirement, TestCase
from db.repository import find, get, insert


def case(run_id: str, requirement_id: str, title: str) -> TestCase:
    return TestCase(run_id=run_id, requirement_id=requirement_id, type="positive", title=title,
                    steps=["search headphones", "add to cart", "checkout", "pay"], expected_result="Payment approved")


async def test_insert_then_get_round_trips_exactly(db):
    requirement = Requirement(text="User can search for a product, add it to cart, and complete checkout.")
    requirement_id = await insert(db, requirement)
    tc = case(requirement.run_id, requirement_id, "happy path")

    tc_id = await insert(db, tc)

    assert tc.id == tc_id
    assert await get(db, TestCase, tc_id) == tc
    raw = await db["test_cases"].find_one({"_id": ObjectId(tc_id)})
    assert isinstance(raw["requirement_id"], ObjectId)


async def test_get_missing_returns_none(db):
    assert await get(db, TestCase, str(ObjectId())) is None


async def test_find_filters_by_run_and_by_string_object_id_ref(db):
    req_a, req_b = str(ObjectId()), str(ObjectId())
    for run_id, req, title in [("run-a", req_a, "one"), ("run-a", req_a, "two"), ("run-b", req_b, "three")]:
        await insert(db, case(run_id, req, title))

    assert [t.title for t in await find(db, TestCase, {"run_id": "run-a"}, sort=[("title", 1)])] == ["one", "two"]
    assert [t.title for t in await find(db, TestCase, {"requirement_id": req_b})] == ["three"]
    assert len(await find(db, TestCase, limit=2)) == 2
