# Phase 6 — Test Executor Agent + Dry Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a generated script against the live platform and record an `executions` document (spec §7.4) with pass / fail / flaky and failure artifacts. Then wire Analyzer → Generator → Executor into a manual dry run (`python -m cli dry-run "<requirement>"`), with no Orchestrator yet.

**Architecture:** `execution/playwright_runner.py` resets all five services (spec §6.8), launches `node <tests-generated>/node_modules/@playwright/test/cli.js test <file> --reporter=json` in a worker thread, parses the JSON report, and picks up `failure.json` written by the fixtures. Running the command in a worker thread avoids Windows `npx.cmd` and event-loop subprocess limits under uvicorn. `agents/test_executor.py` resolves the selector map version, writes the map file, runs up to two attempts (flaky classification, roadmap G11) and persists the result. `cli.py` chains the three agents.

**Tech Stack:** Python 3.12, httpx, subprocess, Node Playwright ^1.55, Phase 2–5 modules.

## Global Constraints

- Before every attempt, `POST /api/__reset` goes to all five services. If any is down, the execution fails with `failure.kind = "environment"` and the script isn't started.
- Status rules: first attempt passes → `pass`. First fails and an unmodified re-run passes → `flaky`. Both fail → `fail`. The re-run can be disabled with task payload `check_flaky: false`.
- Artifacts: `<artifacts_dir>/<run_id>/<script_id>/v<map version>-attempt<N>/` (failure.json, dom.html, failure.png, stdout.json, stderr.txt, playwright output).
- The runner passes `APQA_SELECTOR_MAP`, `APQA_ARTIFACTS_DIR`, the five `*_SERVICE_URL`s and `FORCE_COLOR=0` to Playwright.
- **Additive fields on `Execution`:** `failure: dict | None`, `selector_map_version: int`, `attempts: int`.
- The executor task payload is `{"script_id", "selector_map_version"?, "check_flaky"?}`. The result is `{"execution_id", "status", "failure"}`.

## File Map

```
autopilot-qa/backend/
  db/models.py                         Execution + failure, selector_map_version, attempts
  db/selector_maps.py                  + by_version
  execution/playwright_runner.py       ServiceUnavailableError, reset_all_services, RunOutcome, parse_report, run_script
  agents/test_executor.py              TestExecutor
  cli.py                               dry_run(), main()
  tests/execution/test_playwright_runner.py
  tests/agents/test_test_executor.py
  tests/test_cli.py
```

---

### Task 1: Model and selector-map additions

- [ ] **Step 1: Add fields to `Execution` in `db/models.py`** (after `healing`)

```python
    failure: dict[str, Any] | None = None  # kind (script|environment), error, last_selector_key, dom_path, screenshot_path, url
    selector_map_version: int = 1
    attempts: int = 1
```

- [ ] **Step 2: Add `by_version` to `db/selector_maps.py`**

```python
async def by_version(db: AsyncDatabase, version: int) -> SelectorMap:
    raw = await db[SelectorMap.collection].find_one({"version": version, "status": "active"})
    if raw is None:
        raise LookupError(f"no active selector map v{version}")
    return SelectorMap.from_mongo(raw)
```

and extend `tests/db/test_selector_maps.py`:

```python
async def test_by_version_finds_only_active_maps(db):
    from db.selector_maps import by_version
    await init_db(db)
    assert (await by_version(db, 1)).created_by == "seed"
    await insert(db, SelectorMap(version=2, map={}, created_by="healer", status="pending_review"))
    with pytest.raises(LookupError, match="v2"):
        await by_version(db, 2)
```

Run: `.\.venv\Scripts\python -m pytest tests/db -q` → all pass.

---

### Task 2: Playwright runner

**Files:** Create `execution/playwright_runner.py`, `tests/execution/test_playwright_runner.py`.

**Interfaces — Produces:** `ServiceUnavailableError(service, url, detail)`; `async reset_all_services(urls, *, transport=None)`; `RunOutcome(status, duration_ms, error=None, logs=[], failure=None, artifacts_dir=None)`; `parse_report(report) -> (passed, duration_ms, first_error, logs)`; `async run_script(script_path, *, tests_dir, selector_map_file, artifacts_dir, service_urls, timeout_s=300) -> RunOutcome`.

- [ ] **Step 1: Write the failing test `tests/execution/test_playwright_runner.py`**

```python
import json
import shutil
import uuid

import httpx
import pytest

from execution.playwright_runner import ServiceUnavailableError, parse_report, reset_all_services, run_script
from settings import BACKEND_DIR

TESTS_DIR = BACKEND_DIR.parent / "tests-generated"
SEED_MAP = BACKEND_DIR / "config" / "selector_map.v1.json"
HEADER = "const { test, expect } = require('../support/fixtures');\n"
PASSING = HEADER + "test('ok', async ({ page }) => {\n  await page.setContent('<p id=\"x\">hi</p>');\n  await expect(page.locator('#x')).toHaveText('hi');\n});\n"
FAILING = PASSING.replace("toHaveText('hi')", "toHaveText('bye', { timeout: 500 })")
URLS = {"auth": "http://a.test", "catalog": "http://c.test"}


@pytest.fixture
def scratch():
    directory = TESTS_DIR / f"_pytest_{uuid.uuid4().hex[:8]}"
    directory.mkdir()
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


async def run(scratch, tmp_path, code):
    script = scratch / "case.spec.js"
    script.write_text(code, encoding="utf-8")
    return await run_script(script, tests_dir=TESTS_DIR, selector_map_file=SEED_MAP,
                            artifacts_dir=tmp_path / "attempt1", service_urls=URLS)


async def test_passing_script(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, PASSING)
    assert (outcome.status, outcome.error, outcome.failure) == ("pass", None, None)
    assert outcome.duration_ms > 0


async def test_failing_script_returns_the_error_and_captured_artifacts(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, FAILING)
    assert outcome.status == "fail"
    assert "bye" in outcome.error and "\x1b" not in outcome.error
    assert outcome.failure["dom_path"].endswith("dom.html")
    assert (tmp_path / "attempt1" / "dom.html").exists()
    assert (tmp_path / "attempt1" / "stdout.json").exists()


async def test_a_script_that_does_not_load_fails_cleanly(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, HEADER + "test('broken', async () => {\n")
    assert outcome.status == "fail" and outcome.error


def test_parse_report_walks_nested_suites():
    report = {"stats": {"expected": 0, "unexpected": 1}, "errors": [], "suites": [{"specs": [], "suites": [{"specs": [
        {"tests": [{"results": [{"duration": 42, "error": {"message": "\x1b[31mboom\x1b[39m"},
                                 "stdout": [{"text": "log line\n"}], "stderr": []}]}]}]}]}]}
    assert parse_report(report) == (False, 42, "boom", ["log line"])


async def test_reset_calls_every_service():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    await reset_all_services(URLS, transport=httpx.MockTransport(handler))
    assert seen == ["http://a.test/api/__reset", "http://c.test/api/__reset"]


@pytest.mark.parametrize("respond", ["refuse", "500"])
async def test_reset_names_the_unavailable_service(respond):
    def handler(request):
        if request.url.host == "c.test":
            if respond == "refuse":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ServiceUnavailableError, match="catalog service not available at http://c.test") as info:
        await reset_all_services(URLS, transport=httpx.MockTransport(handler))
    assert info.value.service == "catalog"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/execution/test_playwright_runner.py -q`
Expected: `ModuleNotFoundError: No module named 'execution.playwright_runner'`

- [ ] **Step 3: Write `execution/playwright_runner.py`**

```python
"""Runs generated Playwright scripts against the target platform and parses the results (spec §4.4, §6.8)."""
import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

ANSI = re.compile(r"\x1b\[[0-9;]*m")
URL_ENV = {"auth": "AUTH_SERVICE_URL", "catalog": "CATALOG_SERVICE_URL", "cart": "CART_SERVICE_URL",
           "order": "ORDER_SERVICE_URL", "payment": "PAYMENT_SERVICE_URL"}


class ServiceUnavailableError(RuntimeError):
    def __init__(self, service: str, url: str, detail: str) -> None:
        super().__init__(f"{service} service not available at {url}: {detail}")
        self.service, self.url = service, url


async def reset_all_services(urls: dict[str, str], *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Restore every service's seed data so runs never drift (spec §6.8)."""
    async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
        for service, url in urls.items():
            try:
                response = await client.post(f"{url}/api/__reset")
            except httpx.HTTPError as exc:
                raise ServiceUnavailableError(service, url, str(exc) or type(exc).__name__) from exc
            if response.status_code != 200:
                raise ServiceUnavailableError(service, url, f"HTTP {response.status_code}")


@dataclass
class RunOutcome:
    status: Literal["pass", "fail"]
    duration_ms: int
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    artifacts_dir: Path | None = None


def parse_report(report: dict[str, Any]) -> tuple[bool, int, str | None, list[str]]:
    """Reduce a Playwright JSON report to (passed, duration_ms, first error, log lines)."""
    results: list[dict[str, Any]] = []

    def walk(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results.extend(test.get("results", []))
        for child in suite.get("suites", []):
            walk(child)

    for suite in report.get("suites", []):
        walk(suite)
    errors = [ANSI.sub("", e.get("message", "")) for e in report.get("errors", [])]
    errors += [ANSI.sub("", r["error"].get("message", "")) for r in results if r.get("error")]
    logs = [entry.get("text", "").rstrip() for r in results for stream in ("stdout", "stderr") for entry in r.get(stream, [])]
    stats = report.get("stats", {})
    passed = not errors and stats.get("unexpected", 0) == 0 and stats.get("expected", 0) >= 1
    return passed, int(sum(r.get("duration", 0) for r in results)), (errors[0] if errors else None), [l for l in logs if l]


def playwright_cli(tests_dir: Path) -> Path:
    cli = tests_dir / "node_modules" / "@playwright" / "test" / "cli.js"
    if not cli.exists():
        raise FileNotFoundError(f"{cli} is missing; run `npm install` in {tests_dir}")
    return cli


async def run_script(script_path: Path, *, tests_dir: Path, selector_map_file: Path, artifacts_dir: Path,
                     service_urls: dict[str, str], timeout_s: int = 300) -> RunOutcome:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "APQA_SELECTOR_MAP": str(selector_map_file), "APQA_ARTIFACTS_DIR": str(artifacts_dir),
           "FORCE_COLOR": "0", **{URL_ENV[s]: url for s, url in service_urls.items() if s in URL_ENV}}
    relative = Path(script_path).resolve().relative_to(tests_dir.resolve()).as_posix()
    command = ["node", str(playwright_cli(tests_dir)), "test", relative, "--reporter=json",
               f"--output={(artifacts_dir / 'playwright').as_posix()}"]

    started = time.perf_counter()
    try:
        proc = await asyncio.to_thread(subprocess.run, command, cwd=tests_dir, env=env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return RunOutcome("fail", timeout_s * 1000, error=f"script timed out after {timeout_s}s", artifacts_dir=artifacts_dir)
    wall_ms = int((time.perf_counter() - started) * 1000)
    (artifacts_dir / "stdout.json").write_text(proc.stdout, encoding="utf-8")
    (artifacts_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")

    try:
        report = json.loads(proc.stdout[proc.stdout.index("{"):])
    except ValueError:
        detail = ANSI.sub("", proc.stderr.strip())[:500]
        return RunOutcome("fail", wall_ms, error=f"no JSON report (exit {proc.returncode}): {detail}", artifacts_dir=artifacts_dir)
    passed, duration_ms, error, logs = parse_report(report)
    failure_file = artifacts_dir / "failure.json"
    failure = json.loads(failure_file.read_text(encoding="utf-8")) if (not passed and failure_file.exists()) else None
    return RunOutcome("pass" if passed else "fail", duration_ms or wall_ms, error, logs, failure, artifacts_dir)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/execution/test_playwright_runner.py -q`
Expected: `7 passed`

---

### Task 3: Test Executor agent

**Files:** Create `agents/test_executor.py`, `tests/agents/test_test_executor.py`.

**Interfaces — Produces:** `TestExecutor(runner=run_script, resetter=reset_all_services)` with `handle(task, ctx) -> {"execution_id", "status", "failure"}`. `failure` is `None` on pass; otherwise `{"kind": "script" | "environment", "error", ...failure.json fields}` from the **first** attempt.

- [ ] **Step 1: Write the failing test `tests/agents/test_test_executor.py`**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/agents/test_test_executor.py -q`
Expected: `ModuleNotFoundError: No module named 'agents.test_executor'`

- [ ] **Step 3: Write `agents/test_executor.py`**

```python
"""Test Executor agent: run a script, classify pass/fail/flaky, persist the execution (spec §4.4)."""
from pathlib import Path
from typing import Any

from agents.base import Agent, RunContext, Task
from db.models import Execution, Script
from db.repository import get, insert
from db.selector_maps import by_version, write_selector_map_file
from execution.playwright_runner import RunOutcome, ServiceUnavailableError, reset_all_services, run_script


def _failure(outcome: RunOutcome) -> dict[str, Any] | None:
    if outcome.status == "pass":
        return None
    return {"kind": "script", "error": outcome.error, **(outcome.failure or {})}


def _first_line(text: str | None) -> str:
    return text.strip().splitlines()[0] if text and text.strip() else ""


class TestExecutor(Agent):
    __test__ = False  # not a pytest test class
    name = "test_executor"

    def __init__(self, runner=run_script, resetter=reset_all_services) -> None:
        self._run = runner
        self._reset = resetter

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        script = await get(ctx.db, Script, task.payload["script_id"])
        if script is None:
            raise LookupError(f"script {task.payload['script_id']} not found")
        version = task.payload.get("selector_map_version", script.selector_map_version)
        base_dir = ctx.settings.artifacts_dir / ctx.run_id / script.id
        map_file = write_selector_map_file(await by_version(ctx.db, version), base_dir)
        urls = ctx.settings.service_urls()

        attempts: list[RunOutcome] = []
        for attempt in (1, 2):
            attempt_dir = base_dir / f"v{version}-attempt{attempt}"
            try:
                await self._reset(urls)
            except ServiceUnavailableError as exc:
                attempts.append(RunOutcome("fail", 0, error=str(exc), artifacts_dir=attempt_dir,
                                           failure={"kind": "environment", "service": exc.service, "error": str(exc)}))
                break
            outcome = await self._run(Path(script.path), tests_dir=ctx.settings.tests_generated_dir,
                                      selector_map_file=map_file, artifacts_dir=attempt_dir, service_urls=urls)
            attempts.append(outcome)
            if outcome.status == "pass" or not task.payload.get("check_flaky", True):
                break

        first, last = attempts[0], attempts[-1]
        status = "pass" if first.status == "pass" else ("flaky" if last.status == "pass" else "fail")
        failure = _failure(first)
        logs = [line for a in attempts for line in a.logs]
        logs += [f"attempt {i}: {a.status}" + (f" - {_first_line(a.error)}" if a.error else "") for i, a in enumerate(attempts, 1)]
        execution_id = await insert(ctx.db, Execution(
            run_id=ctx.run_id, script_id=script.id, status=status, duration_ms=first.duration_ms,
            screenshot_path=(failure or {}).get("screenshot_path"), logs=logs, failure=failure,
            selector_map_version=version, attempts=len(attempts)))

        detail = f" ({_first_line(first.error)})" if status != "pass" and first.error else ""
        await ctx.emit_thought(self.name, f"Executed script {script.id} with selector map v{version}: {status}{detail}.",
                               task_id=task.task_id)
        return {"execution_id": execution_id, "status": status, "failure": failure}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/agents/test_test_executor.py -q`
Expected: `6 passed`

---

### Task 4: Dry-run CLI

**Files:** Create `cli.py`, `tests/test_cli.py`.

**Interfaces — Produces:** `async dry_run(requirement, *, db, llm, settings, executor=None, max_cases=None, types=None, out=print) -> dict` returning `{"run_id", "requirement_id", "rows": [{"test_case_id", "type", "title", "script_path", "status", "detail"}]}`; `main(argv=None)`.

- [ ] **Step 1: Write the failing test `tests/test_cli.py`**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/test_cli.py -q`
Expected: `ModuleNotFoundError: No module named 'cli'`

- [ ] **Step 3: Write `cli.py`**

```python
"""AutoPilot-QA command line.

    python -m cli dry-run "<requirement>" [--max-cases N] [--types positive negative boundary]

Runs Requirement Analyzer -> Test Generator -> Test Executor in sequence (no Orchestrator) against the live platform.
"""
import argparse
import asyncio
import sys
import time
from typing import Any

from bson import ObjectId

from agents.base import RunContext, Task
from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_executor import TestExecutor
from agents.test_generator import GeneratorError, TestGenerator
from db.models import Requirement, TestCase, new_id
from db.mongo_client import get_db, init_db
from db.repository import get, insert
from llm.ollama_client import OllamaBackend
from settings import Settings, get_settings


def _first_line(text: str | None) -> str:
    return text.strip().splitlines()[0] if text and text.strip() else ""


async def dry_run(requirement: str, *, db, llm, settings: Settings, executor: TestExecutor | None = None,
                  max_cases: int | None = None, types: list[str] | None = None, out=print) -> dict[str, Any]:
    executor = executor or TestExecutor()
    doc = Requirement(text=requirement, status="running")
    requirement_id = await insert(db, doc)
    ctx = RunContext(run_id=doc.run_id, db=db, llm=llm, settings=settings)
    out(f"run {doc.run_id}")

    started = time.perf_counter()
    analysis = await RequirementAnalyzer().handle(
        Task(new_id(), "analyze", {"requirement_id": requirement_id, "text": requirement}), ctx)
    cases = [await get(db, TestCase, i) for i in analysis["test_case_ids"]]
    cases = [c for c in cases if not types or c.type in types][: max_cases or None]
    out(f"analyzer: {len(analysis['test_case_ids'])} test cases in {time.perf_counter() - started:.0f}s; running {len(cases)}")

    rows = []
    for case in cases:
        row = {"test_case_id": case.id, "type": case.type, "title": case.title, "script_path": None, "detail": ""}
        started = time.perf_counter()
        try:
            generated = await TestGenerator().handle(Task(new_id(), "generate", {"test_case_id": case.id}), ctx)
        except GeneratorError as exc:
            row.update(status="generation_failed", detail=str(exc))
        else:
            row["script_path"] = generated["path"]
            executed = await executor.handle(Task(new_id(), "execute", {"script_id": generated["script_id"]}), ctx)
            row.update(status=executed["status"], detail=_first_line((executed["failure"] or {}).get("error")))
        rows.append(row)
        out(f"  [{case.type}] {case.title}: {row['status']} ({time.perf_counter() - started:.0f}s) {row['detail']}")

    await db[Requirement.collection].update_one({"_id": ObjectId(requirement_id)}, {"$set": {"status": "completed"}})
    passed = sum(r["status"] == "pass" for r in rows)
    out(f"summary: {passed}/{len(rows)} passed")
    return {"run_id": doc.run_id, "requirement_id": requirement_id, "rows": rows}


async def _main(opts: argparse.Namespace) -> int:
    settings = get_settings()
    db = get_db(settings)
    await init_db(db)
    try:
        async with OllamaBackend(settings.ollama_url, settings.analyzer_model) as llm:
            report = await dry_run(opts.requirement, db=db, llm=llm, settings=settings,
                                   max_cases=opts.max_cases, types=opts.types)
    finally:
        await db.client.close()
    return 0 if all(r["status"] == "pass" for r in report["rows"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    dry = commands.add_parser("dry-run", help="analyze -> generate -> execute one requirement")
    dry.add_argument("requirement")
    dry.add_argument("--max-cases", type=int)
    dry.add_argument("--types", nargs="+", choices=["positive", "negative", "boundary"])
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Live dry run** (platform running on v1, after any other long LLM job)

Run: `.\.venv\Scripts\python -m cli dry-run "User can search for a product, add it to cart, and complete checkout." --types positive --max-cases 1`
Expected: `summary: 1/1 passed`. Then run it again without `--max-cases`, and record per-case results in the execution notes.

## Phase 6 Exit Checklist

- [ ] `pytest -q` green.
- [ ] The live dry run prints test cases → script paths → pass/fail for the reference requirement against v1, and the reference positive case passes.

---

## Execution Notes (2026-09-27)

Unit and integration tests green (the runner tests use real Playwright). Live dry run, reference requirement,
positive case, platform v1:
- Attempt 1: fail (generator scoped the checkout button inside a cart row; see the Phase 5 notes).
- Attempt 2: fail (generator skipped the name field; the app was right).
- Attempt 3: **`summary: 1/1 passed`**. Analyzer 89 s, then generate + execute 237 s (CPU only).

Both earlier failures were classified correctly by the executor as `fail` with a full `failure.json`. Neither was
flaky, and each got an unmodified re-run.
