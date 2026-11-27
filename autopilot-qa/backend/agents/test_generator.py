"""Test Generator agent: test case -> validated Playwright script (spec §4.3, roadmap contract C5)."""
import re
from string import Template
from typing import Any

from agents.base import Agent, RunContext, Task
from agents.platform_context import load_platform_context
from db.models import Script, TestCase
from db.repository import get, insert
from db.selector_maps import latest_active
from execution.script_validator import normalize_script, validate_script
from llm.ollama_client import LLMBackend
from settings import BACKEND_DIR

PROMPT_PATH = BACKEND_DIR / "prompts" / "test_generator.md"
EXAMPLE_PATH = BACKEND_DIR.parent / "tests-generated" / "examples" / "remove-only-item.spec.js"
OPTIONS = {"temperature": 0.2, "num_ctx": 8192}
_FENCE = re.compile(r"```(?:javascript|js)?[ \t]*\n(.*?)```", re.DOTALL)


class GeneratorError(RuntimeError):
    """The model did not produce a valid script, even after one repair attempt."""


def extract_code(reply: str) -> str:
    blocks = _FENCE.findall(reply)
    code = next((b for b in blocks if "test(" in b), blocks[0] if blocks else reply)
    return code.strip() + "\n"


def render_selector_catalogue(services: dict[str, Any]) -> str:
    lines = []
    for service, spec in services.items():
        pages = ", ".join(f"urls.{service} + '{path}'" for path in spec.get("pages", {}).values())
        lines.append(f"{service} — pages: {pages}")
        for key, element in spec["elements"].items():
            inside = f" (inside {service}.{element['scope']})" if element.get("scope") else ""
            lines.append(f"  {service}.{key}{inside} — {element['description']}")
    return "\n".join(lines)


def build_prompt(test_case: TestCase, services: dict[str, Any]) -> str:
    return Template(PROMPT_PATH.read_text(encoding="utf-8")).substitute(
        selector_catalogue=render_selector_catalogue(services),
        test_data=load_platform_context(("UI text", "Test data")),
        example=EXAMPLE_PATH.read_text(encoding="utf-8"),
        title=test_case.title,
        type=test_case.type,
        steps="\n".join(f"{i}. {step}" for i, step in enumerate(test_case.steps, start=1)),
        expected_result=test_case.expected_result,
    )


def temperature_for(attempt: int) -> float:
    """A retry at the same low temperature just repeats the failure, so each attempt samples more widely."""
    return min(OPTIONS["temperature"] + 0.25 * (attempt - 1), 0.9)


async def generate_script(llm: LLMBackend, test_case: TestCase, services: dict[str, Any], *,
                          model: str | None = None, attempt: int = 1) -> str:
    options = OPTIONS | {"temperature": temperature_for(attempt)}
    prompt = build_prompt(test_case, services)
    code = normalize_script(extract_code(await llm.generate(prompt, model=model, options=options)))
    result = validate_script(code, services)
    if result.ok:
        return code
    repair = (f"{prompt}\n\nYour previous script was rejected by the validator:\n"
              + "\n".join(f"- {e}" for e in result.errors)
              + f"\n\nPrevious script:\n```javascript\n{code}```\n\nReturn the corrected script only.")
    code = normalize_script(extract_code(await llm.generate(repair, model=model, options=options)))
    result = validate_script(code, services)
    if not result.ok:
        raise GeneratorError("invalid script after one repair: " + "; ".join(result.errors))
    return code


class TestGenerator(Agent):
    __test__ = False  # not a pytest test class
    name = "test_generator"

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        test_case = await get(ctx.db, TestCase, task.payload["test_case_id"])
        if test_case is None:
            raise LookupError(f"test case {task.payload['test_case_id']} not found")
        selector_map = await latest_active(ctx.db)
        code = await generate_script(ctx.llm, test_case, selector_map.map, model=ctx.settings.generator_model,
                                     attempt=task.attempt)

        path = ctx.settings.tests_generated_dir / ctx.run_id / f"{test_case.id}.spec.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        script_id = await insert(ctx.db, Script(run_id=ctx.run_id, test_case_id=test_case.id, code=code,
                                                path=str(path), selector_map_version=selector_map.version))
        await ctx.emit_thought(self.name, f"Generated a Playwright script for '{test_case.title}' "
                                          f"against selector map v{selector_map.version}.", task_id=task.task_id)
        return {"script_id": script_id, "path": str(path), "selector_map_version": selector_map.version}
