import asyncio
import json

import httpx
import pytest

from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_executor import TestExecutor
from agents.test_generator import EXAMPLE_PATH, TestGenerator
from db.mongo_client import init_db
from execution.playwright_runner import RunOutcome
from main import create_app
from settings import Settings
from tests.fakes import FakeLLM

REQ = "User can remove an item from the cart."
CASES = json.dumps({"test_cases": [
    {"type": "positive", "title": "Removing the only item empties the cart", "steps": ["Open the catalog page"],
     "expected_result": "'Your cart is empty' is shown"},
    {"type": "negative", "title": "Checkout disabled for an empty cart", "steps": ["Open the cart page"],
     "expected_result": "'Proceed to Checkout' is disabled"},
]})
SCRIPT = EXAMPLE_PATH.read_text(encoding="utf-8")


async def runner(script_path, **kwargs):
    return RunOutcome("pass", 800)


async def no_reset(urls):
    return None


@pytest.fixture
async def api(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path / "gen", artifacts_dir=tmp_path / "art",
                        reports_dir=tmp_path / "reports")
    agents = {"requirement_analyzer": RequirementAnalyzer(), "test_generator": TestGenerator(),
              "test_executor": TestExecutor(runner=runner, resetter=no_reset)}
    app = create_app(settings=settings, db=db, llm=FakeLLM([CASES, SCRIPT, SCRIPT]), agents=agents)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield app, client


async def submit(app, client):
    response = await client.post("/requirements", json={"text": REQ})
    assert response.status_code == 202
    body = response.json()
    await asyncio.wait_for(app.state.runs[body["run_id"]], 30)
    return body


async def test_submitting_a_requirement_runs_the_pipeline(api):
    app, client = api
    body = await submit(app, client)
    assert body["status"] == "pending" and len(body["requirement_id"]) == 24

    run = (await client.get(f"/runs/{body['run_id']}")).json()
    assert run["status"] == "completed"
    assert run["counts"] | {} == {"test_cases": 2, "scripts": 2, "executions": 2, "bug_reports": 0,
                                  "pass": 2, "fail": 0, "flaky": 0}


async def test_artifact_endpoints(api):
    app, client = api
    run_id = (await submit(app, client))["run_id"]
    cases = (await client.get(f"/runs/{run_id}/test-cases")).json()
    assert [c["type"] for c in cases] == ["positive", "negative"] and "id" in cases[0]
    assert len((await client.get(f"/runs/{run_id}/scripts")).json()) == 2
    assert {r["status"] for r in (await client.get(f"/runs/{run_id}/results")).json()} == {"pass"}
    assert (await client.get(f"/runs/{run_id}/bug-reports")).json() == []
    [coverage] = (await client.get(f"/runs/{run_id}/coverage")).json()
    assert (coverage["has_test"], coverage["last_status"]) == (True, "pass")
    messages = (await client.get(f"/runs/{run_id}/messages")).json()
    assert messages[0]["type"] == "request" and messages[-1]["to_agent"] == "dashboard"


async def test_re_execution(api):
    app, client = api
    run_id = (await submit(app, client))["run_id"]
    response = await client.post(f"/runs/{run_id}/execute")
    assert response.status_code == 202
    await asyncio.wait_for(app.state.runs[run_id], 30)
    assert len((await client.get(f"/runs/{run_id}/results")).json()) == 4


async def test_unknown_runs_and_bad_input(api):
    _, client = api
    assert (await client.get("/runs/nope")).status_code == 404
    assert (await client.get("/runs/nope/test-cases")).status_code == 404
    assert (await client.post("/runs/nope/execute")).status_code == 404
    assert (await client.post("/requirements", json={"text": "  "})).status_code == 422
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_results_carry_a_screenshot_url_under_the_artifacts_mount(api):
    app, client = api
    run_id = (await submit(app, client))["run_id"]
    settings = app.state.settings
    shot = settings.artifacts_dir / run_id / "s" / "failure.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fake")
    await app.state.db["executions"].update_many({"run_id": run_id}, {"$set": {"screenshot_path": str(shot)}})
    [first, *_] = (await client.get(f"/runs/{run_id}/results")).json()
    assert first["screenshot_url"] == f"/artifacts/{run_id}/s/failure.png"
    assert (await client.get(first["screenshot_url"])).content == b"\x89PNG fake"


async def test_max_cases_is_stored_validated_and_limits_the_analysis(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path / "gen", artifacts_dir=tmp_path / "art",
                        reports_dir=tmp_path / "reports")
    agents = {"requirement_analyzer": RequirementAnalyzer(), "test_generator": TestGenerator(),
              "test_executor": TestExecutor(runner=runner, resetter=no_reset)}
    app = create_app(settings=settings, db=db, llm=FakeLLM([CASES, SCRIPT]), agents=agents)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for bad in (1, 7):
            assert (await client.post("/requirements", json={"text": REQ, "max_cases": bad})).status_code == 422
        response = await client.post("/requirements", json={"text": REQ, "max_cases": 2})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await asyncio.wait_for(app.state.runs[run_id], 30)
        assert len((await client.get(f"/runs/{run_id}/test-cases")).json()) == 2
