import json
import re

import pytest

from agents.base import RunContext, Task
from agents.platform_context import load_platform_context
from agents.requirement_analyzer import (AnalyzerError, RequirementAnalyzer, analyze, build_system_prompt,
                                         output_schema)
from db.models import AgentTrace, Requirement, TestCase
from db.repository import find, insert
from settings import Settings
from tests.fakes import FakeLLM

REQ = "User can search for a product, add it to cart, and complete checkout."


def case(type_, title, steps=("Open the catalog page", "Search for 'headphones'"), expected="'Payment approved' is shown"):
    return {"type": type_, "title": title, "steps": list(steps), "expected_result": expected}


VALID = json.dumps({"test_cases": [
    case("positive", "Buy Wireless Headphones with a valid card"),
    case("negative", "Payment with an expired card is declined", expected="'Payment declined: card expired' is shown"),
    case("boundary", "Card valid through the end of its expiry month"),
]})


async def test_analyze_returns_validated_drafts_with_constrained_output():
    llm = FakeLLM([VALID])
    drafts = await analyze(llm, REQ, model="llama3.1:8b")
    assert [d.type for d in drafts] == ["positive", "negative", "boundary"]
    [call] = llm.calls
    assert call["model"] == "llama3.1:8b"
    assert call["format"] == output_schema()
    assert REQ in call["prompt"]
    assert "Wireless Headphones" in call["system"] and "4111111111111111" in call["system"]
    assert call["options"]["temperature"] <= 0.3


async def test_invalid_json_gets_one_repair_attempt():
    llm = FakeLLM(["this is not json", VALID])
    assert len(await analyze(llm, REQ)) == 3
    repair = llm.calls[1]["prompt"]
    assert "was not valid" in repair and "this is not json" in repair


async def test_a_missing_negative_case_is_repaired():
    only_positive = json.dumps({"test_cases": [case("positive", "Happy path")]})
    llm = FakeLLM([only_positive, VALID])
    await analyze(llm, REQ)
    assert "missing negative" in llm.calls[1]["prompt"]


async def test_gives_up_after_one_repair():
    with pytest.raises(AnalyzerError, match="after one repair"):
        await analyze(FakeLLM(["{}", "{}"]), REQ)


async def test_titles_and_steps_are_trimmed_and_duplicates_dropped():
    messy = json.dumps({"test_cases": [
        case("positive", "  Happy path  ", steps=["  Open the catalog page ", " ", "Click 'Search'"]),
        case("positive", "happy PATH"),
        case("negative", "Expired card"),
    ]})
    drafts = await analyze(FakeLLM([messy]), REQ)
    assert [d.title for d in drafts] == ["Happy path", "Expired card"]
    assert drafts[0].steps == ["Open the catalog page", "Click 'Search'"]


def test_schema_is_self_contained_and_constrains_the_type():
    schema = output_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    item = schema["properties"]["test_cases"]["items"]
    assert item["properties"]["type"]["enum"] == ["positive", "negative", "boundary"]


def test_system_prompt_is_fully_rendered():
    prompt = build_system_prompt(load_platform_context())
    assert "Coffee Grinder" in prompt and "Your cart is empty" in prompt
    assert not re.search(r"\$[a-z_]", prompt)


async def test_agent_persists_test_cases_and_a_trace(db):
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM([VALID]), settings=Settings(_env_file=None))
    requirement_id = await insert(db, Requirement(text=REQ, run_id="run-1"))

    result = await RequirementAnalyzer().handle(Task("t1", "analyze", {"requirement_id": requirement_id, "text": REQ}), ctx)

    stored = await find(db, TestCase, {"run_id": "run-1"})
    assert result == {"test_case_ids": [t.id for t in stored], "counts": {"positive": 1, "negative": 1, "boundary": 1}}
    assert {t.requirement_id for t in stored} == {requirement_id}
    [trace] = await find(db, AgentTrace, {"run_id": "run-1"})
    assert trace.agent == "requirement_analyzer"
    assert trace.thought == "Derived 3 test cases from the requirement: 1 positive, 1 negative, 1 boundary."
    assert ctx.llm.calls[0]["model"] == "llama3.1:8b"


def draft(type_, title):
    from agents.requirement_analyzer import TestCaseDraft
    return TestCaseDraft(type=type_, title=title, steps=["Open the catalog page"], expected_result="something is shown")


def test_select_cases_keeps_a_positive_and_a_negative_in_original_order():
    from agents.requirement_analyzer import select_cases
    drafts = [draft("negative", "neg-1"), draft("boundary", "bnd-1"), draft("positive", "pos-1"), draft("negative", "neg-2")]
    assert [d.title for d in select_cases(drafts, 2)] == ["neg-1", "pos-1"]
    assert [d.title for d in select_cases(drafts, 3)] == ["neg-1", "bnd-1", "pos-1"]
    assert select_cases(drafts, None) == drafts
    assert select_cases(drafts, 4) == drafts


async def test_a_case_limit_is_requested_in_the_prompt_and_enforced():
    llm = FakeLLM([VALID])  # the fake model ignores the request and returns 3 cases
    drafts = await analyze(llm, REQ, max_cases=2)
    assert [d.type for d in drafts] == ["positive", "negative"]
    prompt = llm.calls[0]["prompt"]
    assert "Write exactly 2 test cases: one positive and one negative" in prompt and REQ in prompt


async def test_the_limit_is_applied_when_the_agent_runs(db):
    ctx = RunContext(run_id="run-q", db=db, llm=FakeLLM([VALID]), settings=Settings(_env_file=None))
    requirement_id = await insert(db, Requirement(text=REQ, run_id="run-q"))
    result = await RequirementAnalyzer().handle(
        Task("t1", "analyze", {"requirement_id": requirement_id, "text": REQ, "max_cases": 2}), ctx)
    assert result["counts"] == {"positive": 1, "negative": 1}
    assert len(await find(db, TestCase, {"run_id": "run-q"})) == 2
