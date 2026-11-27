import json

from agents.test_executor import TestExecutor
from agents.test_generator import EXAMPLE_PATH
from cli import dry_run
from db.models import Requirement
from db.mongo_client import init_db
from db.repository import find
from execution.playwright_runner import RunOutcome
from settings import Settings
from tests.fakes import FakeLLM

REQ = "User can remove an item from the cart."
CASES = json.dumps({"test_cases": [
    {"type": "positive", "title": "Removing the only item empties the cart", "steps": ["Open the catalog page"],
     "expected_result": "'Your cart is empty' is shown"},
    {"type": "negative", "title": "Checkout disabled for empty cart", "steps": ["Open the cart page"],
     "expected_result": "'Proceed to Checkout' is disabled"},
]})
SCRIPT = EXAMPLE_PATH.read_text(encoding="utf-8")


async def passing_runner(script_path, **kwargs):
    return RunOutcome("pass", 900)


async def no_reset(urls):
    return None


async def test_dry_run_chains_analyzer_generator_and_executor(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path / "gen", artifacts_dir=tmp_path / "art")
    lines = []
    report = await dry_run(REQ, db=db, llm=FakeLLM([CASES, SCRIPT, SCRIPT]), settings=settings,
                           executor=TestExecutor(runner=passing_runner, resetter=no_reset), out=lines.append)

    assert [r["status"] for r in report["rows"]] == ["pass", "pass"]
    assert all(r["script_path"].endswith(".spec.js") for r in report["rows"])
    [requirement] = await find(db, Requirement, {"run_id": report["run_id"]})
    assert requirement.status == "completed"
    assert any("2/2 passed" in line for line in lines)


async def test_dry_run_can_limit_the_cases(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path / "gen", artifacts_dir=tmp_path / "art")
    report = await dry_run(REQ, db=db, llm=FakeLLM([CASES, SCRIPT]), settings=settings,
                           executor=TestExecutor(runner=passing_runner, resetter=no_reset),
                           types=["negative"], out=lambda _: None)
    assert [r["type"] for r in report["rows"]] == ["negative"]


async def test_a_generation_failure_is_reported_not_raised(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path / "gen", artifacts_dir=tmp_path / "art")
    bad = "test('x', async () => {});"
    report = await dry_run(REQ, db=db, llm=FakeLLM([CASES, bad, bad]), settings=settings,
                           executor=TestExecutor(runner=passing_runner, resetter=no_reset), max_cases=1,
                           out=lambda _: None)
    [row] = report["rows"]
    assert row["status"] == "generation_failed" and "must start with" in row["detail"]
