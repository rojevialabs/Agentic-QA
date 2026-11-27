import pytest
from bson import ObjectId

from agents.base import RunContext, Task
from agents.test_executor import TestExecutor
from db.models import AgentTrace, Execution, Script, SelectorMap
from db.mongo_client import init_db
from db.repository import find, get, insert
from execution.playwright_runner import RunOutcome, ServiceUnavailableError
from settings import Settings
from tests.fakes import FakeLLM

ERROR = "TimeoutError: locator.click: Timeout 5000ms exceeded.\nwaiting for locator('#add-to-cart-btn')"


def outcome(status):
    failure = {"error": ERROR, "last_selector_key": "catalog.addToCartButton", "dom_path": "dom.html",
               "screenshot_path": "failure.png"} if status == "fail" else None
    return RunOutcome(status, 1200, error=ERROR if status == "fail" else None, logs=[f"{status} log"], failure=failure)


class ScriptedRunner:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def __call__(self, script_path, **kwargs):
        self.calls.append(kwargs)
        return self.outcomes.pop(0)


async def no_reset(urls):
    return None


@pytest.fixture
async def setup(db, tmp_path):
    await init_db(db)
    script_id = await insert(db, Script(run_id="run-1", test_case_id=str(ObjectId()), code="x",
                                        path=str(tmp_path / "case.spec.js")))
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(), settings=Settings(_env_file=None, artifacts_dir=tmp_path / "art"))
    return ctx, script_id


async def execute(ctx, script_id, runner, resetter=no_reset, **payload):
    agent = TestExecutor(runner=runner, resetter=resetter)
    result = await agent.handle(Task("t3", "execute", {"script_id": script_id, **payload}), ctx)
    return result, await get(ctx.db, Execution, result["execution_id"])


async def test_a_pass_needs_one_attempt(setup):
    ctx, script_id = setup
    runner = ScriptedRunner(outcome("pass"))
    result, stored = await execute(ctx, script_id, runner)
    assert (result["status"], result["failure"], stored.attempts, stored.selector_map_version) == ("pass", None, 1, 1)
    assert runner.calls[0]["selector_map_file"].name == "selector_map.v1.json"
    assert runner.calls[0]["artifacts_dir"].name == "v1-attempt1"


async def test_fail_then_pass_is_flaky(setup):
    ctx, script_id = setup
    result, stored = await execute(ctx, script_id, ScriptedRunner(outcome("fail"), outcome("pass")))
    assert (result["status"], stored.attempts) == ("flaky", 2)
    assert stored.failure["last_selector_key"] == "catalog.addToCartButton"


async def test_two_failures_are_a_fail_with_artifacts_and_a_trace(setup):
    ctx, script_id = setup
    result, stored = await execute(ctx, script_id, ScriptedRunner(outcome("fail"), outcome("fail")))
    assert result["status"] == "fail"
    assert result["failure"]["kind"] == "script"
    assert stored.screenshot_path == "failure.png"
    assert stored.logs[-2:] == ["attempt 1: fail - TimeoutError: locator.click: Timeout 5000ms exceeded.",
                                "attempt 2: fail - TimeoutError: locator.click: Timeout 5000ms exceeded."]
    [trace] = await find(ctx.db, AgentTrace, {"run_id": "run-1"})
    assert trace.thought.startswith(f"Executed script {script_id} with selector map v1: fail")


async def test_flaky_check_can_be_disabled(setup):
    ctx, script_id = setup
    runner = ScriptedRunner(outcome("fail"))
    result, _ = await execute(ctx, script_id, runner, check_flaky=False)
    assert result["status"] == "fail" and len(runner.calls) == 1


async def test_a_selector_map_version_can_be_requested(setup):
    ctx, script_id = setup
    await insert(ctx.db, SelectorMap(version=2, parent_version=1, map={"catalog": {}}, created_by="healer"))
    runner = ScriptedRunner(outcome("pass"))
    _, stored = await execute(ctx, script_id, runner, selector_map_version=2)
    assert stored.selector_map_version == 2
    assert runner.calls[0]["selector_map_file"].name == "selector_map.v2.json"


async def test_an_unavailable_service_is_an_environment_failure(setup):
    ctx, script_id = setup

    async def down(urls):
        raise ServiceUnavailableError("cart", "http://localhost:4003", "connection refused")

    runner = ScriptedRunner()
    result, stored = await execute(ctx, script_id, runner, resetter=down)
    assert result["status"] == "fail" and runner.calls == []
    assert result["failure"] == {"kind": "environment", "service": "cart",
                                 "error": "cart service not available at http://localhost:4003: connection refused"}


async def test_the_latest_active_map_is_the_default_and_healing_is_recorded(setup):
    ctx, script_id = setup
    await insert(ctx.db, SelectorMap(version=2, parent_version=1, map={"catalog": {}}, created_by="healer"))
    _, stored = await execute(ctx, script_id, ScriptedRunner(outcome("pass")), healing={"new_locator": "#add-to-bag-btn"})
    assert stored.selector_map_version == 2
    assert stored.healing == {"new_locator": "#add-to-bag-btn"}
