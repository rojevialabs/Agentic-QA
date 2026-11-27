import json

import pytest

from agents.base import RunContext, Task
from agents.test_generator import (EXAMPLE_PATH, GeneratorError, TestGenerator, build_prompt, extract_code,
                                   generate_script)
from db.models import AgentTrace, Script, TestCase
from db.mongo_client import init_db
from db.repository import find, get, insert
from settings import BACKEND_DIR, Settings
from tests.fakes import FakeLLM

SERVICES = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))["services"]
GOOD = EXAMPLE_PATH.read_text(encoding="utf-8")
BAD = "const fs = require('fs');\ntest('x', async ({ page }) => { await page.click('#pay-btn'); });"
CASE = TestCase(run_id="run-1", requirement_id="0" * 24, type="negative", title="Expired card is declined",
                steps=["Open the payment page for an order", "Enter card 4111111111111111 and expiry 01/20", "Click 'Pay'"],
                expected_result="'Payment declined: card expired' is shown")


def test_extract_code_handles_fences_and_bare_replies():
    assert extract_code("Here you go:\n```javascript\nconst a = 1;\n```\nDone") == "const a = 1;\n"
    assert extract_code("```js\nx();\n```") == "x();\n"
    assert extract_code("  const b = 2;  ") == "const b = 2;\n"


def test_prompt_lists_keys_test_case_and_example():
    prompt = build_prompt(CASE, SERVICES)
    assert "catalog.addToCartButton (inside catalog.productCard)" in prompt
    assert "urls.payment + '/payment.html'" in prompt
    assert "Enter card 4111111111111111 and expiry 01/20" in prompt
    assert "Removing the only item empties the cart" in prompt  # the few-shot example
    assert "Title: Expired card is declined" in prompt


async def test_generate_script_returns_valid_code():
    llm = FakeLLM([f"```javascript\n{GOOD}```"])
    assert await generate_script(llm, CASE, SERVICES, model="qwen2.5-coder:7b") == GOOD
    assert llm.calls[0]["model"] == "qwen2.5-coder:7b"


async def test_invalid_code_gets_one_repair_with_the_errors():
    llm = FakeLLM([BAD, GOOD])
    assert await generate_script(llm, CASE, SERVICES) == GOOD
    repair = llm.calls[1]["prompt"]
    assert "only require('../support/fixtures') is allowed" in repair and "forbidden: page.click" in repair


async def test_gives_up_after_one_repair():
    with pytest.raises(GeneratorError, match="forbidden: page.click"):
        await generate_script(FakeLLM([BAD, BAD]), CASE, SERVICES)


async def test_agent_writes_the_script_and_persists_it(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path)
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM([GOOD]), settings=settings)
    case_id = await insert(db, CASE.model_copy())

    result = await TestGenerator().handle(Task("t2", "generate", {"test_case_id": case_id}), ctx)

    path = tmp_path / "run-1" / f"{case_id}.spec.js"
    assert result == {"script_id": result["script_id"], "path": str(path), "selector_map_version": 1}
    assert path.read_text(encoding="utf-8") == GOOD
    stored = await get(db, Script, result["script_id"])
    assert (stored.test_case_id, stored.code, stored.path, stored.selector_map_version) == (case_id, GOOD, str(path), 1)
    [trace] = await find(db, AgentTrace, {"run_id": "run-1"})
    assert trace.agent == "test_generator" and "Expired card is declined" in trace.thought


def test_retries_sample_more_widely_than_the_first_attempt():
    from agents.test_generator import temperature_for
    assert [temperature_for(n) for n in (1, 2, 3)] == pytest.approx([0.2, 0.45, 0.7])
    assert temperature_for(9) == pytest.approx(0.9)


async def test_the_attempt_number_sets_the_generation_temperature(db, tmp_path):
    await init_db(db)
    ctx = RunContext(run_id="run-t", db=db, llm=FakeLLM([GOOD]), settings=Settings(_env_file=None, tests_generated_dir=tmp_path))
    case_id = await insert(db, CASE.model_copy())
    await TestGenerator().handle(Task("t9", "generate", {"test_case_id": case_id}, attempt=3), ctx)
    assert ctx.llm.calls[0]["options"]["temperature"] == pytest.approx(0.7)


async def test_a_keyed_page_fill_is_accepted_without_a_repair_round():
    natural = GOOD.replace("await page.locator(sel('cart.cartTotal'))", "await page.locator(sel('cart.cartTotal'))")
    natural = natural.rstrip().rsplit("});", 1)[0] + "  await page.fill(sel('auth.emailInput'), 'demo@shop.test');\n});\n"
    llm = FakeLLM([f"```javascript\n{natural}```"])
    code = await generate_script(llm, CASE, SERVICES)
    assert "page.locator(sel('auth.emailInput')).fill('demo@shop.test')" in code
    assert len(llm.calls) == 1, "no repair round should have been needed"
