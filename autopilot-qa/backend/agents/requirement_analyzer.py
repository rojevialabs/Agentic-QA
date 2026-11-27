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


def select_cases(drafts: list[TestCaseDraft], limit: int | None) -> list[TestCaseDraft]:
    """Keep at most `limit` cases, always keeping the first positive and the first negative; original order is kept."""
    if limit is None or len(drafts) <= limit:
        return drafts
    chosen: list[int] = []
    for wanted in ("positive", "negative"):
        index = next((i for i, d in enumerate(drafts) if d.type == wanted), None)
        if index is not None and len(chosen) < limit:
            chosen.append(index)
    chosen += [i for i in range(len(drafts)) if i not in chosen][: limit - len(chosen)]
    return [drafts[i] for i in sorted(chosen)]


def _limit_hint(max_cases: int) -> str:
    if max_cases == 2:
        return "\n\nWrite exactly 2 test cases: one positive and one negative, covering the main journey."
    return f"\n\nWrite exactly {max_cases} test cases, including at least one positive and one negative."


async def analyze(llm: LLMBackend, requirement: str, *, model: str | None = None, context: str | None = None,
                  max_cases: int | None = None) -> list[TestCaseDraft]:
    system = build_system_prompt(load_platform_context() if context is None else context)
    prompt = f"Requirement: {requirement.strip()}" + (_limit_hint(max_cases) if max_cases else "")
    raw = await llm.generate(prompt, system=system, model=model, format=output_schema(), options=OPTIONS)
    try:
        return select_cases(_parse(raw), max_cases)
    except ValueError as first:
        repair = (f"{prompt}\n\nYour previous answer was not valid:\n{first}\n\n"
                  f"Previous answer:\n{raw[:4000]}\n\nReturn the corrected JSON only.")
        raw = await llm.generate(repair, system=system, model=model, format=output_schema(), options=OPTIONS)
        try:
            return select_cases(_parse(raw), max_cases)
        except ValueError as second:
            raise AnalyzerError(f"invalid analyzer output after one repair: {second}") from second


class RequirementAnalyzer(Agent):
    name = "requirement_analyzer"

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        requirement_id, text = task.payload["requirement_id"], task.payload["text"]
        drafts = await analyze(ctx.llm, text, model=ctx.settings.analyzer_model, max_cases=task.payload.get("max_cases"))
        ids = [await insert(ctx.db, TestCase(run_id=ctx.run_id, requirement_id=requirement_id, **d.model_dump()))
               for d in drafts]
        counts = Counter(d.type for d in drafts)
        summary = ", ".join(f"{counts[t]} {t}" for t in ("positive", "negative", "boundary") if counts[t])
        await ctx.emit_thought(self.name, f"Derived {len(drafts)} test cases from the requirement: {summary}.",
                               task_id=task.task_id)
        return {"test_case_ids": ids, "counts": dict(counts)}
