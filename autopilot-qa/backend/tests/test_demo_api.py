"""Demo controls: drive the whole demo from the dashboard (seeded changes, heal reset, per-run threshold)."""
import asyncio
import json

import httpx
import pytest

from agents.base import Agent
from db.models import Requirement, Script, SelectorMap
from db.mongo_client import init_db
from db.repository import insert
from db.selector_maps import latest_active
from main import create_app
from settings import Settings
from tests.fakes import FakeLLM


class FakeServices:
    """Stands in for the five ShopLite services' /api/__config."""

    def __init__(self):
        self.changes, self.posts = [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        service = {4001: "auth", 4002: "catalog", 4003: "cart", 4004: "order", 4005: "payment"}[request.url.port]
        if request.method == "POST":
            self.changes = json.loads(request.content)["seeded_changes"]
            self.posts.append(service)
        return httpx.Response(200, json={"service": service, "seeded_changes": self.changes})


class RecordingExecutor(Agent):
    name = "test_executor"

    def __init__(self):
        self.thresholds = []

    async def handle(self, task, ctx):
        self.thresholds.append(ctx.settings.healing_confidence_threshold)
        return {"execution_id": "e", "status": "pass", "failure": None}


@pytest.fixture
async def demo(db, tmp_path):
    await init_db(db)
    services = FakeServices()
    executor = RecordingExecutor()
    app = create_app(settings=Settings(_env_file=None, reports_dir=tmp_path / "reports", artifacts_dir=tmp_path / "art"),
                     db=db, llm=FakeLLM(), agents={"test_executor": executor},
                     service_transport=httpx.MockTransport(services.handler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield app, client, services, executor


async def test_state_lists_the_registry_changes_the_live_config_and_the_maps(demo):
    _, client, services, _ = demo
    services.changes = ["catalog.add_button_renamed"]
    state = (await client.get("/demo/state")).json()
    assert state["seeded_changes"] == ["catalog.add_button_renamed"]
    assert state["threshold"] == 0.8
    ids = {c["id"]: c["label"] for c in state["changes"]}
    assert ids["catalog.add_button_renamed"] == "benign_ui_change" and ids["cart.total_doubled"] == "real_defect"
    assert state["selector_maps"] == [{"version": 1, "status": "active", "created_by": "seed", "healed": []}]


async def test_switching_seeded_changes_reaches_every_service(demo):
    _, client, services, _ = demo
    response = await client.post("/demo/seeded-changes", json={"seeded_changes": ["catalog.add_button_wishlist"]})
    assert response.status_code == 200
    assert services.posts == ["auth", "catalog", "cart", "order", "payment"]
    assert services.changes == ["catalog.add_button_wishlist"]
    assert (await client.post("/demo/seeded-changes", json={"seeded_changes": ["nope"]})).status_code == 422


async def test_reset_healing_archives_healer_maps(demo):
    app, client, _, _ = demo
    await insert(app.state.db, SelectorMap(version=2, parent_version=1, map={"catalog": {"elements": {
        "addToCartButton": {"selector": "#add-to-bag-btn", "healed_from": "#add-to-cart-btn"}}}}, created_by="healer"))
    maps = (await client.get("/demo/state")).json()["selector_maps"]
    assert maps[-1]["healed"] == ["catalog.addToCartButton: #add-to-cart-btn -> #add-to-bag-btn"]
    assert (await client.post("/demo/reset-healing")).json() == {"archived": 1}
    assert (await latest_active(app.state.db)).version == 1


async def test_re_execution_can_use_a_one_off_threshold(demo):
    app, client, _, executor = demo
    requirement = Requirement(text="r", run_id="run-1")
    await insert(app.state.db, requirement)
    await insert(app.state.db, Script(run_id="run-1", test_case_id="0" * 24, code="x", path="x.spec.js"))
    assert (await client.post("/runs/run-1/execute", json={"threshold": 0.99})).status_code == 202
    await asyncio.wait_for(app.state.runs["run-1"], 10)
    assert executor.thresholds == [0.99]
    assert app.state.settings.healing_confidence_threshold == 0.8  # the global setting is untouched
    assert (await client.post("/runs/run-1/execute", json={"threshold": 1.5})).status_code == 422
