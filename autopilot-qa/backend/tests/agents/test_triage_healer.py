import json

import pytest
from bson import ObjectId

from agents.base import RunContext, Task
from agents.triage_healer import TriageHealer
from db.models import AgentTrace, BugReport, Execution, Script, SelectorMap, TestCase
from db.mongo_client import init_db
from db.repository import find, get, insert
from db.selector_maps import derive, latest_active
from settings import BACKEND_DIR, Settings
from tests.fakes import FakeLLM

V2 = (BACKEND_DIR / "tests" / "fixtures" / "dom" / "catalog_v2.html").read_text(encoding="utf-8")
CLICK_MISSING = ("TimeoutError: locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for locator('.product-card')"
                 ".filter({ hasText: 'Wireless Headphones' }).locator('#add-to-cart-btn')\n")
TEXT_MISMATCH = ("Error: expect(locator).toHaveText(expected) failed\n\nLocator:  locator('#cart-total')\n"
                 'Expected: "$59.99"\nReceived: "$119.98"\nTimeout:  5000ms\n')


def answer(locator, confidence):
    action = {"candidate": 1, "new_locator": locator, "confidence": confidence, "basis": "renamed label"}
    return ["THOUGHT: The Add to Cart button is now labelled Add to Bag.\n", "ACTION: " + json.dumps(action)]


@pytest.fixture
async def world(db, tmp_path):
    await init_db(db)
    dom = tmp_path / "dom.html"
    dom.write_text(V2, encoding="utf-8")
    case_id = await insert(db, TestCase(run_id="run-1", requirement_id="0" * 24, type="positive", title="Buy headphones",
                                        steps=["Search for 'headphones'", "Click 'Add to Cart'"],
                                        expected_result="'Payment approved' is shown"))
    script_id = await insert(db, Script(run_id="run-1", test_case_id=case_id, code="x", path="x.spec.js"))

    async def failed_execution(error=CLICK_MISSING, **failure):
        return await insert(db, Execution(run_id="run-1", script_id=script_id, status="fail", failure={
            "kind": "script", "error": error, "dom_path": str(dom), "url": "http://localhost:4002/catalog.html",
            "last_selector_key": "catalog.addToCartButton", **failure}))

    return db, script_id, failed_execution


async def triage(db, script_id, execution_id, llm=None, **payload):
    ctx = RunContext(run_id="run-1", db=db, llm=llm or FakeLLM(), settings=Settings(_env_file=None))
    result = await TriageHealer().handle(Task("t4", "triage", {"execution_id": execution_id, "script_id": script_id,
                                                               **payload}), ctx)
    return result, ctx


async def test_a_confident_verified_heal_creates_a_new_active_map(world):
    db, script_id, failed = world
    result, ctx = await triage(db, script_id, await failed(), FakeLLM(stream_replies=[answer("#add-to-bag-btn", 0.9)]))

    assert (result["outcome"], result["selector_map_version"]) == ("healed", 2)
    healed = await latest_active(db)
    element = healed.map["catalog"]["elements"]["addToCartButton"]
    assert (healed.version, healed.parent_version, element["selector"], element["healed_from"]) == (
        2, 1, "#add-to-bag-btn", "#add-to-cart-btn")
    traces = await find(db, AgentTrace, {"run_id": "run-1"}, sort=[("timestamp", 1)])
    assert [t.thought for t in traces] == [
        "Triage: locator_missing at catalog.addToCartButton (#add-to-cart-btn): healable locator failure.",
        "THOUGHT: The Add to Cart button is now labelled Add to Bag.",
        "ACTION: applied catalog.addToCartButton -> #add-to-bag-btn (confidence 0.90 >= 0.80); selector map v2.",
    ]
    assert traces[-1].action["applied"] is True and traces[-1].action["verified"] is True
    assert ctx.llm.calls[0]["model"] == "llama3.1:8b"


async def test_low_confidence_is_a_proposal_and_a_ui_bug_report(world):
    db, script_id, failed = world
    result, _ = await triage(db, script_id, await failed(), FakeLLM(stream_replies=[answer("#add-to-bag-btn", 0.6)]))

    assert (result["outcome"], result["root_cause"], result["severity"]) == ("bug_report", "ui", "medium")
    assert (await latest_active(db)).version == 1
    [proposal] = await find(db, SelectorMap, {"status": "pending_review"})
    assert proposal.map["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-bag-btn"
    report = await get(db, BugReport, result["bug_report_id"])
    assert report.notes.startswith("Possible benign UI change requiring manual confirmation")
    assert "confidence 0.60 below threshold 0.80" in report.notes
    assert report.steps_to_reproduce == ["Search for 'headphones'", "Click 'Add to Cart'"]
    assert report.expected == "'Payment approved' is shown"


async def test_a_wrong_value_is_reported_without_asking_the_llm(world):
    db, script_id, failed = world
    result, _ = await triage(db, script_id, await failed(error=TEXT_MISMATCH))
    assert (result["outcome"], result["root_cause"]) == ("bug_report", "data")
    report = await get(db, BugReport, result["bug_report_id"])
    assert 'Received: "$119.98"' in report.actual


async def test_an_environment_failure_is_high_severity(world):
    db, script_id, failed = world
    result, _ = await triage(db, script_id, await failed(kind="environment", error="cart service not available"))
    assert (result["root_cause"], result["severity"]) == ("environment", "high")


async def test_a_failure_after_a_heal_is_reported(world):
    db, script_id, failed = world
    result, _ = await triage(db, script_id, await failed(), after_heal=True)
    report = await get(db, BugReport, result["bug_report_id"])
    assert result["outcome"] == "bug_report" and "healed locator did not fix" in report.notes


async def test_an_earlier_heal_in_the_run_is_reused_without_the_llm(world):
    db, script_id, failed = world
    await insert(db, derive(await latest_active(db), "catalog", "addToCartButton", "#add-to-bag-btn", run_id="run-1"))
    result, _ = await triage(db, script_id, await failed())
    assert (result["outcome"], result["selector_map_version"]) == ("healed", 2)
    assert result["decision"]["new_locator"] == "#add-to-bag-btn"


async def test_without_a_dom_snapshot_healing_is_not_attempted(world):
    db, script_id, failed = world
    result, _ = await triage(db, script_id, await failed(dom_path=None))
    report = await get(db, BugReport, result["bug_report_id"])
    assert result["outcome"] == "bug_report" and "No DOM snapshot" in report.notes


async def test_a_heal_that_does_not_fix_the_test_is_rolled_back(world):
    db, script_id, failed = world
    healed = derive(await latest_active(db), "catalog", "addToCartButton", "button.wishlist-btn", run_id="run-1")
    await insert(db, healed)
    execution_id = await failed()
    await db["executions"].update_one({"_id": ObjectId(execution_id)},
                                      {"$set": {"selector_map_version": 2}})

    result, _ = await triage(db, script_id, execution_id, after_heal=True)

    assert result["outcome"] == "bug_report" and result["rolled_back_version"] == 2
    assert (await latest_active(db)).version == 1
    [rolled] = await find(db, SelectorMap, {"status": "rolled_back"})
    assert rolled.version == 2
    report = await get(db, BugReport, result["bug_report_id"])
    assert "rolled back" in report.notes


async def test_the_healer_prompt_version_comes_from_settings(world):
    db, script_id, failed = world
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(stream_replies=[answer("#add-to-bag-btn", 0.9)]),
                     settings=Settings(_env_file=None, healer_prompt_version="v2"))
    await TriageHealer().handle(Task("t", "triage", {"execution_id": await failed(), "script_id": script_id}), ctx)
    assert "Same effect for the user?" in ctx.llm.calls[0]["prompt"]


async def test_the_same_below_threshold_proposal_is_stored_once(world):
    db, script_id, failed = world
    for _ in range(2):
        await triage(db, script_id, await failed(), FakeLLM(stream_replies=[answer("#add-to-bag-btn", 0.6)]))
    assert len(await find(db, SelectorMap, {"status": "pending_review"})) == 1
    assert len(await find(db, BugReport, {"run_id": "run-1"})) == 2  # each failure still gets its bug report
