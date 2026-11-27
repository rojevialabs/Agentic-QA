# Phase 4 — Requirement Analyzer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a plain-English requirement into validated, persisted `test_cases` (positive / negative / boundary), grounded in the real ShopLite platform. Also lay down the agent base (contract C6) that every later agent uses.

**Architecture:** `agents/base.py` defines `Task`, `RunContext` (db, llm, settings, optional bus, `emit_thought`) and the `Agent` ABC. `agents/platform_context.py` pulls the `Services` and `Test data` sections out of `target-platform/README.md` (roadmap G13), so the prompt only knows real products, users and cards. `agents/requirement_analyzer.py` builds a few-shot system prompt, calls `LLMBackend.generate` with Ollama structured output (`format` = inlined JSON schema), validates with Pydantic (≥1 positive and ≥1 negative case), makes **one** repair re-prompt on failure (roadmap G19), then persists and writes an agent trace.

**Tech Stack:** Python 3.12, Pydantic v2, `string.Template`, Phase 2 repository/models, Phase 3 `LLMBackend`.

## Global Constraints

- Test case fields per spec §7.2: `type ∈ {positive, negative, boundary}`, `title`, `steps: list[str]`, `expected_result`.
- Every analysis must include at least one positive and one negative case. At most 8 cases are kept, with duplicate titles removed.
- Exactly one repair attempt, then raise `AnalyzerError`.
- The model comes from `Settings.analyzer_model` (default `llama3.1:8b`). Temperature 0.2, `num_ctx` 8192.
- The few-shot example must not be one of the evaluation requirements (it uses cart removal).
- Unit tests use a fake LLM. Live evaluation is `scripts/eval_analyzer.py`, run by hand.
- **Contract C6 refinement:** `RunContext.emit_thought(agent, text, *, task_id=None, action=None)` takes the agent name explicitly, because one context is shared by all agents in a run.

## File Map

```
autopilot-qa/backend/
  agents/__init__.py
  agents/base.py                    Task, Publisher, RunContext, Agent
  agents/platform_context.py        load_platform_context
  agents/requirement_analyzer.py    TestCaseDraft, AnalyzerOutput, AnalyzerError, output_schema, build_system_prompt, analyze, RequirementAnalyzer
  llm/schema.py                     inline_refs
  prompts/requirement_analyzer.md
  scripts/eval_analyzer.py
  tests/fakes.py                    FakeLLM, FakeBus
  tests/agents/test_base.py
  tests/agents/test_platform_context.py
  tests/agents/test_requirement_analyzer.py
```

---

### Task 1: Agent base, test fakes, platform context, schema helper

**Files:** Create `agents/__init__.py`, `agents/base.py`, `agents/platform_context.py`, `llm/schema.py`, `tests/fakes.py`, `tests/agents/test_base.py`, `tests/agents/test_platform_context.py`.

**Interfaces — Produces:**
- `Task(task_id: str, kind: str, payload: dict)` (frozen).
- `Publisher` Protocol: `async publish(msg: Message) -> None`.
- `RunContext(run_id, db, llm, settings, bus=None)` with `async emit_thought(agent, text, *, task_id=None, action=None) -> None`. It always inserts an `AgentTrace`. If a bus is set, it also publishes a `thought` Message to `dashboard` with payload `{"text", "action"}`.
- `Agent` ABC: `name: ClassVar[AgentName]`, `async handle(task, ctx) -> dict`.
- `load_platform_context(sections=("Services", "Test data"), path=PLATFORM_README) -> str` (raises `ValueError` when a section is missing).
- `inline_refs(schema) -> dict` (resolves local `$ref`s, drops `$defs`).
- `tests/fakes.py`: `FakeLLM(replies=(), stream_replies=())` recording `.calls`; `FakeBus` recording `.messages`.

- [ ] **Step 1: Write `tests/fakes.py`**

```python
"""Test doubles shared across agent tests."""
from db.models import Message


class FakeLLM:
    """Scripted LLMBackend: returns queued replies and records every call."""

    def __init__(self, replies=(), stream_replies=()):
        self.replies = list(replies)
        self.stream_replies = [list(r) for r in stream_replies]
        self.calls: list[dict] = []

    async def generate(self, prompt, *, system=None, model=None, format=None, options=None):
        self.calls.append({"kind": "generate", "prompt": prompt, "system": system, "model": model,
                           "format": format, "options": options})
        if not self.replies:
            raise AssertionError("FakeLLM: unexpected generate() call")
        return self.replies.pop(0)

    async def stream(self, prompt, *, system=None, model=None, options=None):
        self.calls.append({"kind": "stream", "prompt": prompt, "system": system, "model": model, "options": options})
        if not self.stream_replies:
            raise AssertionError("FakeLLM: unexpected stream() call")
        for chunk in self.stream_replies.pop(0):
            yield chunk


class FakeBus:
    def __init__(self):
        self.messages: list[Message] = []

    async def publish(self, msg: Message) -> None:
        self.messages.append(msg)
```

- [ ] **Step 2: Write the failing tests**

`tests/agents/test_base.py`:

```python
from agents.base import RunContext
from db.models import AgentTrace
from db.repository import find
from settings import Settings
from tests.fakes import FakeBus, FakeLLM


async def test_emit_thought_persists_a_trace_without_a_bus(db):
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(), settings=Settings(_env_file=None))
    await ctx.emit_thought("requirement_analyzer", "Derived 3 test cases", task_id="t1")
    [trace] = await find(db, AgentTrace, {"run_id": "run-1"})
    assert (trace.agent, trace.thought, trace.task_id, trace.action) == ("requirement_analyzer", "Derived 3 test cases", "t1", {})


async def test_emit_thought_also_publishes_to_the_dashboard(db):
    bus = FakeBus()
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(), settings=Settings(_env_file=None), bus=bus)
    await ctx.emit_thought("triage_healer", "The id changed", task_id="t9", action={"confidence": 0.9})
    [msg] = bus.messages
    assert (msg.type, msg.from_agent, msg.to_agent, msg.task_id) == ("thought", "triage_healer", "dashboard", "t9")
    assert msg.payload == {"text": "The id changed", "action": {"confidence": 0.9}}
    assert len(await find(db, AgentTrace, {"run_id": "run-1"})) == 1
```

`tests/agents/test_platform_context.py`:

```python
import pytest

from agents.platform_context import load_platform_context
from llm.schema import inline_refs


def test_context_holds_services_and_test_data_only():
    context = load_platform_context()
    assert context.startswith("## Services")
    assert "## Test data" in context
    for fact in ["Wireless Headphones", "Coffee Grinder", "demo@shop.test", "4111111111111111", "01/20"]:
        assert fact in context
    for noise in ["npm run start:all", "CATALOG_BUTTON_VARIANT", "## Page elements"]:
        assert noise not in context


def test_missing_section_is_an_error():
    with pytest.raises(ValueError, match="Nope"):
        load_platform_context(sections=("Services", "Nope"))


def test_inline_refs_resolves_definitions():
    schema = {"type": "object", "properties": {"x": {"$ref": "#/$defs/X", "description": "kept"}},
              "$defs": {"X": {"type": "array", "items": {"$ref": "#/$defs/Y"}}, "Y": {"type": "string"}}}
    assert inline_refs(schema) == {"type": "object", "properties": {
        "x": {"type": "array", "items": {"type": "string"}, "description": "kept"}}}
```

- [ ] **Step 3: Run to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/agents -q`
Expected: `ModuleNotFoundError: No module named 'agents'`

- [ ] **Step 4: Write `agents/base.py`**

```python
"""Shared agent interface (roadmap contract C6)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from pymongo.asynchronous.database import AsyncDatabase

from db.models import AgentName, AgentTrace, Message
from db.repository import insert
from llm.ollama_client import LLMBackend
from settings import Settings


@dataclass(frozen=True)
class Task:
    task_id: str
    kind: str
    payload: dict[str, Any]


class Publisher(Protocol):
    async def publish(self, msg: Message) -> None: ...


@dataclass
class RunContext:
    """Everything an agent may touch during one run."""

    run_id: str
    db: AsyncDatabase
    llm: LLMBackend
    settings: Settings
    bus: Publisher | None = None

    async def emit_thought(self, agent: AgentName, text: str, *, task_id: str | None = None,
                           action: dict[str, Any] | None = None) -> None:
        """Record a reasoning-trace line in agent_traces and, when a bus is attached, stream it to the dashboard."""
        action = action or {}
        await insert(self.db, AgentTrace(run_id=self.run_id, agent=agent, task_id=task_id, thought=text, action=action))
        if self.bus is not None:
            await self.bus.publish(Message(run_id=self.run_id, from_agent=agent, to_agent="dashboard",
                                           task_id=task_id or "-", type="thought",
                                           payload={"text": text, "action": action}))


class Agent(ABC):
    name: ClassVar[AgentName]

    @abstractmethod
    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        """Do the task and return the result payload. Raising means an `error` message and a retry."""
```

- [ ] **Step 5: Write `agents/platform_context.py`**

```python
"""Grounding facts about ShopLite for LLM prompts (roadmap G13), taken from the target platform README."""
import re

from settings import BACKEND_DIR

PLATFORM_README = BACKEND_DIR.parents[1] / "target-platform" / "README.md"


def load_platform_context(sections: tuple[str, ...] = ("Services", "Test data"), path=PLATFORM_README) -> str:
    """Return the requested level-2 sections of the README, in the order given."""
    found: dict[str, str] = {}
    for chunk in re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]:
        title = chunk.splitlines()[0].strip()
        found[title] = "## " + chunk.strip()
    missing = [s for s in sections if s not in found]
    if missing:
        raise ValueError(f"platform README has no section(s): {', '.join(missing)}")
    return "\n\n".join(found[s] for s in sections)
```

- [ ] **Step 6: Write `llm/schema.py`**

```python
"""JSON-schema helpers for structured LLM output."""
from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local $refs into a self-contained schema, for grammar-constrained decoding (Ollama `format`)."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].rsplit("/", 1)[-1]]
                return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
```

- [ ] **Step 7: Run to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/agents -q`
Expected: `5 passed`

---

### Task 2: Requirement Analyzer

**Files:** Create `prompts/requirement_analyzer.md`, `agents/requirement_analyzer.py`, `tests/agents/test_requirement_analyzer.py`.

**Interfaces:**
- Consumes: Task 1, `TestCase`/`TestType` models, `insert`.
- Produces: `TestCaseDraft(type, title, steps, expected_result)`; `AnalyzerOutput(test_cases)`; `AnalyzerError`; `MAX_CASES = 8`; `OPTIONS`; `output_schema() -> dict`; `build_system_prompt(context) -> str`; `async analyze(llm, requirement, *, model=None, context=None) -> list[TestCaseDraft]`; `RequirementAnalyzer(Agent)`. Its `handle` takes a task payload `{"requirement_id": str, "text": str}` and returns `{"test_case_ids": [str], "counts": {type: n}}`.

- [ ] **Step 1: Write the failing test `tests/agents/test_requirement_analyzer.py`**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/agents/test_requirement_analyzer.py -q`
Expected: `ModuleNotFoundError: No module named 'agents.requirement_analyzer'`

- [ ] **Step 3: Write `prompts/requirement_analyzer.md`** (a `string.Template`; literal dollar signs are written `$$`)

```text
You are a senior QA engineer. You turn one product requirement into manual-style UI test cases for ShopLite,
a small e-commerce web app. Use ONLY the pages, products, users, cards and messages described below;
never invent products, accounts, cards, pages or features.

$platform_context

Rules:
- Write 3 to 6 test cases. Include at least one "positive" and at least one "negative" case, plus "boundary"
  cases wherever the requirement touches a limit (cart quantity 1-10, stock levels, card expiry month,
  password length, empty cart).
- Steps are concrete actions a user performs in the browser, in order, naming the page, the product and the
  exact values to type. Refer to buttons and links by their visible label, in single quotes.
- expected_result is one observable outcome: the exact message shown, the page reached, or the state of a
  button (enabled/disabled).
- Output JSON only, matching the schema: {"test_cases": [{"type", "title", "steps", "expected_result"}]}.

Example. Requirement: "User can remove an item from the cart."
{"test_cases": [
  {"type": "positive", "title": "Removing the only item empties the cart",
   "steps": ["Open the catalog page", "Click 'Add to Cart' on Yoga Mat", "Open the cart page", "Click 'Remove' on Yoga Mat"],
   "expected_result": "'Your cart is empty' is shown and the cart total is $$0.00"},
  {"type": "negative", "title": "Checkout is unavailable after removing the last item",
   "steps": ["Open the catalog page", "Click 'Add to Cart' on USB-C Charger", "Open the cart page", "Click 'Remove' on USB-C Charger"],
   "expected_result": "'Proceed to Checkout' is disabled"},
  {"type": "boundary", "title": "An 11th unit of the same product is rejected",
   "steps": ["Open the catalog page", "Click 'Add to Cart' on Yoga Mat 10 times", "Click 'Add to Cart' on Yoga Mat once more"],
   "expected_result": "'Could not add Yoga Mat to cart' is shown"}
]}
```

- [ ] **Step 4: Write `agents/requirement_analyzer.py`**

```python
"""Requirement Analyzer agent: plain-English requirement -> validated test cases (spec §4.2)."""
from collections import Counter
from string import Template
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base import Agent, RunContext, Task
from agents.platform_context import load_platform_context
from db.models import TestCase, TestType
from db.repository import insert
from llm.ollama_client import LLMBackend
from llm.schema import inline_refs
from settings import BACKEND_DIR

PROMPT_PATH = BACKEND_DIR / "prompts" / "requirement_analyzer.md"
MAX_CASES = 8
OPTIONS = {"temperature": 0.2, "num_ctx": 8192}


class TestCaseDraft(BaseModel):
    __test__ = False  # not a pytest test class
    type: TestType
    title: str = Field(min_length=3)
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=3)

    @field_validator("title", "expected_result", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("steps", mode="before")
    @classmethod
    def _clean_steps(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [s.strip() for s in value if isinstance(s, str) and s.strip()]
        return value


class AnalyzerOutput(BaseModel):
    test_cases: list[TestCaseDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def _covers_positive_and_negative(self) -> "AnalyzerOutput":
        missing = {"positive", "negative"} - {c.type for c in self.test_cases}
        if missing:
            raise ValueError(f"missing {' and '.join(sorted(missing))} test case")
        return self


class AnalyzerError(RuntimeError):
    """The model did not produce valid test cases, even after one repair attempt."""


def output_schema() -> dict[str, Any]:
    return inline_refs(AnalyzerOutput.model_json_schema())


def build_system_prompt(context: str) -> str:
    return Template(PROMPT_PATH.read_text(encoding="utf-8")).substitute(platform_context=context)


def _parse(raw: str) -> list[TestCaseDraft]:
    """Validate a raw model reply. Raises ValueError (pydantic.ValidationError) when invalid."""
    seen: set[str] = set()
    cases: list[TestCaseDraft] = []
    for draft in AnalyzerOutput.model_validate_json(raw).test_cases:
        if draft.title.lower() not in seen:
            seen.add(draft.title.lower())
            cases.append(draft)
    return cases[:MAX_CASES]


async def analyze(llm: LLMBackend, requirement: str, *, model: str | None = None,
                  context: str | None = None) -> list[TestCaseDraft]:
    system = build_system_prompt(load_platform_context() if context is None else context)
    prompt = f"Requirement: {requirement.strip()}"
    raw = await llm.generate(prompt, system=system, model=model, format=output_schema(), options=OPTIONS)
    try:
        return _parse(raw)
    except ValueError as first:
        repair = (f"{prompt}\n\nYour previous answer was not valid:\n{first}\n\n"
                  f"Previous answer:\n{raw[:4000]}\n\nReturn the corrected JSON only.")
        raw = await llm.generate(repair, system=system, model=model, format=output_schema(), options=OPTIONS)
        try:
            return _parse(raw)
        except ValueError as second:
            raise AnalyzerError(f"invalid analyzer output after one repair: {second}") from second


class RequirementAnalyzer(Agent):
    name = "requirement_analyzer"

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        requirement_id, text = task.payload["requirement_id"], task.payload["text"]
        drafts = await analyze(ctx.llm, text, model=ctx.settings.analyzer_model)
        ids = [await insert(ctx.db, TestCase(run_id=ctx.run_id, requirement_id=requirement_id, **d.model_dump()))
               for d in drafts]
        counts = Counter(d.type for d in drafts)
        summary = ", ".join(f"{counts[t]} {t}" for t in ("positive", "negative", "boundary") if counts[t])
        await ctx.emit_thought(self.name, f"Derived {len(drafts)} test cases from the requirement: {summary}.",
                               task_id=task.task_id)
        return {"test_case_ids": ids, "counts": dict(counts)}
```

- [ ] **Step 5: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/agents -q`
Expected: `13 passed` (5 + 8)

---

### Task 3: Live evaluation script

**Files:** Create `scripts/eval_analyzer.py`.

- [ ] **Step 1: Write `scripts/eval_analyzer.py`**

```python
"""Live check of the Requirement Analyzer: python -m scripts.eval_analyzer [--model M]

Runs the three evaluation requirements, prints the test cases for human review, and saves them to
evaluation/spikes/analyzer/<model>-<timestamp>.json.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

from agents.requirement_analyzer import AnalyzerError, analyze
from llm.ollama_client import OllamaBackend
from settings import BACKEND_DIR, get_settings

REQUIREMENTS = [
    "User can search for a product, add it to cart, and complete checkout.",
    "User can log in with valid credentials and sees an error with a wrong password.",
    "Payment is declined when the card is expired.",
]
OUT_DIR = BACKEND_DIR.parents[1] / "evaluation" / "spikes" / "analyzer"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=get_settings().analyzer_model)
    opts = parser.parse_args()

    report = {"model": opts.model, "runs": []}
    async with OllamaBackend(get_settings().ollama_url, opts.model) as llm:
        for requirement in REQUIREMENTS:
            started = time.perf_counter()
            try:
                drafts, error = await analyze(llm, requirement, model=opts.model), None
            except AnalyzerError as exc:
                drafts, error = [], str(exc)
            seconds = round(time.perf_counter() - started, 1)
            print(f"\n### {requirement}  ({seconds}s)")
            for d in drafts:
                print(f"- [{d.type}] {d.title}\n    steps: {' | '.join(d.steps)}\n    expect: {d.expected_result}")
            if error:
                print(f"  ERROR: {error}")
            report["runs"].append({"requirement": requirement, "seconds": seconds, "error": error,
                                   "test_cases": [d.model_dump() for d in drafts]})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"{opts.model.replace(':', '_')}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nsaved {path}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run live** (after any other long LLM job has finished): `.\.venv\Scripts\python -m scripts.eval_analyzer`
Expected: each requirement yields ≥1 positive and ≥1 negative case, and steps mention only real products, users and cards. Record the review in the execution notes.

## Phase 4 Exit Checklist

- [ ] `pytest -q` green.
- [ ] Live evaluation reviewed: all 3 requirements produce grounded cases (no invented products or cards). Findings recorded below.

---

## Execution Notes (2026-09-27)

Unit tests: 13 passed (fake LLM). Code was extracted verbatim from this plan.

**Live check 1 failed grounding.** Products and cards were real, but labels and messages were invented ("Continue", "CVV",
"Pay Now", "Login", "Welcome…", "Invalid password", "Payment successful"), and search used a product id. Cause:
the platform context held Services and Test data but never the UI text.

**Fix:** a `## UI text` section in `target-platform/README.md` (exact labels and messages, search semantics, the only
path to checkout) is now part of the default context (`Services`, `UI text`, `Test data`). The prompt also requires
quoting messages exactly and searching by product words.

**Live check 2 passed** (llama3.1:8b; 128 s / 39 s / 92 s): every label, message, product and card matches ShopLite.
Remaining quality issues: one "boundary" case isn't a real boundary, and the positive checkout case stops at the
payment page instead of paying. Outputs: `evaluation/spikes/analyzer/`.
