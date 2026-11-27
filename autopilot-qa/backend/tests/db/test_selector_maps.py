import json

import pytest

from db.models import SelectorMap
from db.mongo_client import init_db
from db.repository import insert
from db.selector_maps import latest_active, write_selector_map_file


async def test_latest_active_prefers_the_highest_active_version(db):
    await init_db(db)
    assert (await latest_active(db)).version == 1
    await insert(db, SelectorMap(version=3, map={"catalog": {}}, created_by="healer", status="pending_review"))
    await insert(db, SelectorMap(version=2, parent_version=1, map={"catalog": {}}, created_by="healer"))
    assert (await latest_active(db)).version == 2


async def test_latest_active_requires_a_map(db):
    with pytest.raises(LookupError, match="init_db"):
        await latest_active(db)


async def test_write_selector_map_file_uses_the_seed_shape(db, tmp_path):
    await init_db(db)
    path = write_selector_map_file(await latest_active(db), tmp_path)
    assert path.name == "selector_map.v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["services"]["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-cart-btn"


async def test_by_version_finds_only_active_maps(db):
    from db.selector_maps import by_version
    await init_db(db)
    assert (await by_version(db, 1)).created_by == "seed"
    await insert(db, SelectorMap(version=2, map={}, created_by="healer", status="pending_review"))
    with pytest.raises(LookupError, match="v2"):
        await by_version(db, 2)


def test_derive_replaces_one_selector_and_leaves_the_parent_untouched():
    from db.selector_maps import derive
    parent = SelectorMap(version=1, created_by="seed", map={
        "catalog": {"elements": {"addToCartButton": {"selector": "#add-to-cart-btn", "description": "d"}}}})
    child = derive(parent, "catalog", "addToCartButton", "#add-to-bag-btn", run_id="run-1")
    assert (child.version, child.parent_version, child.created_by, child.status, child.run_id) == (2, 1, "healer", "active", "run-1")
    assert child.map["catalog"]["elements"]["addToCartButton"] == {
        "selector": "#add-to-bag-btn", "description": "d", "healed_from": "#add-to-cart-btn"}
    assert parent.map["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-cart-btn"
