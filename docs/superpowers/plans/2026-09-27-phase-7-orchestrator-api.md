# Phase 7 — Orchestrator, Message Bus & REST API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A supervisor-worker pipeline (spec §4.1, §5) that runs Analyzer → Generator → Executor (→ Triage & Healer once Phase 8 registers it). Failed tasks are retried up to 2 times and then escalated. Every exchange is logged to the capped `messages` bus, and all of it is exposed through the REST API of spec §8.

**Architecture:**
- `orchestrator/message_bus.py` (contract C3): `publish` inserts into the capped collection. `history` returns a run's messages in insertion order. `subscribe` tails the collection with a tailable-await cursor, optionally replaying first; Phase 9's WebSocket uses it.
- `orchestrator/coordinator.py`: for every task it publishes `request` (orchestrator → agent), calls `agent.handle` in-process, then publishes `result` or `error` (agent → orchestrator). **The bus is the system of record; dispatch is in-process.** At the end it writes coverage, `reports/<run_id>.json`, and a final `result` message to `dashboard`.
- `main.py`: a `create_app()` factory (dependencies are injectable for tests) and a module-level `app` for `uvicorn main:app`. Runs execute as background asyncio tasks.

**Tech Stack:** FastAPI, PyMongo async (tailable cursors on a capped collection, which works on a standalone mongod), httpx `ASGITransport` for tests.

## Global Constraints

- Retries: `max_retries = 2`, so at most 3 attempts per task, each with its own `request` message carrying `attempt`. After the last error the task is escalated. Escalation of the analysis task ends the run. Escalation of a per-test-case task is recorded and the run continues.
- Run status: `running` → `completed` (no escalations) | `escalated` (≥1 escalated task) | `failed` (unexpected orchestrator exception, stored in `Requirement.error`).
- Triage runs only for executions with status `fail`, and only if a `triage_healer` agent is registered.
- Coverage (§7.7): one document per (run, requirement), upserted. `last_status` is taken over the **latest execution per script**: any fail → `fail`, else any flaky → `flaky`, else `pass`; `not_run` if none.
- REST per spec §8, plus `GET /runs/{run_id}/messages` (bus replay) and `GET /health`. `POST /requirements` → 202 `{run_id, requirement_id, status}`. `POST /runs/{run_id}/execute` → 202, or 409 while the run is still active.
- CORS allows the Vite dev server `http://localhost:5173`.
- New settings: `reports_dir` (default `autopilot-qa/reports`), `dashboard_origin`. New additive field `Requirement.error`.

## File Map

```
autopilot-qa/backend/
  settings.py                        + reports_dir, dashboard_origin
  db/models.py                       Requirement + error
  orchestrator/__init__.py
  orchestrator/message_bus.py        MessageBus
  orchestrator/coordinator.py        TaskEscalated, Coordinator, default_agents
  main.py                            create_app, app
  tests/orchestrator/test_message_bus.py
  tests/orchestrator/test_coordinator.py
  tests/test_api.py
autopilot-qa/docs/architecture.md
```

---

### Task 1: Message bus

**Interfaces — Produces:** `MessageBus(db)` with `async publish(msg)`, `async history(run_id) -> list[Message]`, and `subscribe(run_id, to_agent=None, *, replay=False, poll_interval=0.2) -> AsyncIterator[Message]`.

- [ ] **Step 1: Write the failing test `tests/orchestrator/test_message_bus.py`**

```python
import asyncio

import pytest

from db.models import Message
from db.mongo_client import init_db
from orchestrator.message_bus import MessageBus


def msg(run_id="run-1", to_agent="orchestrator", n=0, type_="result"):
    return Message(run_id=run_id, from_agent="test_generator", to_agent=to_agent, task_id=f"t{n}", type=type_,
                   payload={"n": n})


@pytest.fixture
async def bus(db):
    await init_db(db)
    return MessageBus(db)


async def next_message(stream, timeout=5):
    return await asyncio.wait_for(anext(stream), timeout)


async def test_history_returns_a_runs_messages_in_order(bus):
    for n in range(3):
        await bus.publish(msg(n=n))
    await bus.publish(msg(run_id="other", n=9))
    assert [m.payload["n"] for m in await bus.history("run-1")] == [0, 1, 2]


async def test_subscribe_with_replay_yields_the_past_then_the_live_messages(bus):
    await bus.publish(msg(n=0))
    stream = bus.subscribe("run-1", replay=True)
    try:
        assert (await next_message(stream)).payload == {"n": 0}
        await bus.publish(msg(run_id="other", n=5))
        await bus.publish(msg(n=1))
        assert (await next_message(stream)).payload == {"n": 1}
    finally:
        await stream.aclose()


async def test_subscribe_without_replay_only_sees_new_messages_for_the_agent(bus):
    await bus.publish(msg(n=0))
    stream = bus.subscribe("run-1", to_agent="dashboard")
    try:
        pending = asyncio.ensure_future(next_message(stream))
        await asyncio.sleep(0.3)
        await bus.publish(msg(n=1))                        # to orchestrator: filtered out
        await bus.publish(msg(n=2, to_agent="dashboard", type_="thought"))
        received = await pending
        assert (received.payload, received.type) == ({"n": 2}, "thought")
    finally:
        await stream.aclose()


async def test_subscribe_waits_on_an_empty_collection(bus):
    stream = bus.subscribe("run-1", replay=True)
    try:
        pending = asyncio.ensure_future(next_message(stream))
        await asyncio.sleep(0.5)
        await bus.publish(msg(n=7))
        assert (await pending).payload == {"n": 7}
    finally:
        await stream.aclose()
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFoundError: No module named 'orchestrator'`)

- [ ] **Step 3: Write `orchestrator/message_bus.py`**

```python
"""Shared message bus on the capped `messages` collection (spec §5, roadmap contract C3)."""
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pymongo import CursorType
from pymongo.asynchronous.database import AsyncDatabase

from db.models import Message


class MessageBus:
    def __init__(self, db: AsyncDatabase) -> None:
        self._messages = db[Message.collection]

    async def publish(self, msg: Message) -> None:
        await self._messages.insert_one(msg.model_dump())

    async def history(self, run_id: str) -> list[Message]:
        cursor = self._messages.find({"run_id": run_id}, sort=[("$natural", 1)])
        return [Message.model_validate(doc) async for doc in cursor]

    async def subscribe(self, run_id: str, to_agent: str | None = None, *, replay: bool = False,
                        poll_interval: float = 0.2) -> AsyncIterator[Message]:
        """Tail the bus for one run. With replay, first yield everything already published for it."""
        query: dict[str, Any] = {"run_id": run_id}
        if to_agent:
            query["to_agent"] = to_agent
        last_id = None
        if not replay:
            newest = await self._messages.find_one({}, sort=[("$natural", -1)])
            last_id = newest["_id"] if newest else None
        while True:
            resume = {**query, "_id": {"$gt": last_id}} if last_id is not None else query
            cursor = self._messages.find(resume, cursor_type=CursorType.TAILABLE_AWAIT)
            try:
                # A tailable cursor dies when its first batch is empty; recreate it after a short pause.
                while cursor.alive:
                    async for doc in cursor:
                        last_id = doc["_id"]
                        yield Message.model_validate(doc)
                    await asyncio.sleep(poll_interval)
            finally:
                await cursor.close()
            await asyncio.sleep(poll_interval)
```

- [ ] **Step 4: Run to verify it passes** — `4 passed`

---

### Task 2: Coordinator

**Interfaces — Produces:** `TaskEscalated(agent, kind, task_id, error)` with `.as_dict()`; `default_agents() -> dict[str, Agent]`; `Coordinator(*, db, llm, settings, bus, agents, max_retries=2)` with `async run(requirement_id) -> dict`, `async execute_existing(run_id) -> dict`, `async build_report(run_id) -> dict`. The report dict holds `run_id, status, requirement, test_cases, scripts, executions, bug_reports, coverage, escalations, counts` (documents in JSON mode).

- [ ] **Step 1: Add settings and the model field**

`settings.py` (below `tests_generated_dir`):

```python
    reports_dir: Path = BACKEND_DIR.parent / "reports"
    dashboard_origin: str = "http://localhost:5173"
```

`db/models.py`, in `Requirement` (after `status`):

```python
    error: str | None = None
```

- [ ] **Step 2: Write the failing test `tests/orchestrator/test_coordinator.py`**

```python
import json

import pytest

from agents.base import Agent
from db.models import Coverage, Requirement
from db.mongo_client import init_db
from db.repository import find, get, insert
from orchestrator.coordinator import Coordinator
from orchestrator.message_bus import MessageBus
from settings import Settings
from tests.fakes import FakeLLM


class FakeAgent(Agent):
    def __init__(self, name, result=None, fail_times=0):
        self.name, self.result, self.fail_times, self.tasks = name, result or {}, fail_times, []

    async def handle(self, task, ctx):
        self.tasks.append(task)
        if len(self.tasks) <= self.fail_times:
            raise RuntimeError(f"boom {len(self.tasks)}")
        return self.result(task) if callable(self.result) else self.result


def agents(**overrides):
    base = {
        "requirement_analyzer": FakeAgent("requirement_analyzer", {"test_case_ids": ["tc1", "tc2"], "counts": {}}),
        "test_generator": FakeAgent("test_generator", lambda t: {"script_id": "s-" + t.payload["test_case_id"]}),
        "test_executor": FakeAgent("test_executor", {"execution_id": "e1", "status": "pass", "failure": None}),
    }
    return base | overrides


@pytest.fixture
async def env(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, reports_dir=tmp_path / "reports")
    requirement_id = await insert(db, Requirement(text="User can check out", run_id="run-1"))
    return db, settings, requirement_id


def coordinator(db, settings, registry):
    return Coordinator(db=db, llm=FakeLLM(), settings=settings, bus=MessageBus(db), agents=registry)


async def test_happy_path_logs_every_exchange_and_writes_the_report(env):
    db, settings, requirement_id = env
    registry = agents()
    report = await coordinator(db, settings, registry).run(requirement_id)

    assert report["status"] == "completed" and report["escalations"] == []
    assert [t.payload for t in registry["test_executor"].tasks] == [{"script_id": "s-tc1"}, {"script_id": "s-tc2"}]
    history = await MessageBus(db).history("run-1")
    assert [(m.type, m.from_agent, m.to_agent) for m in history[:2]] == [
        ("request", "orchestrator", "requirement_analyzer"), ("result", "requirement_analyzer", "orchestrator")]
    assert history[-1].to_agent == "dashboard" and history[-1].payload["status"] == "completed"
    assert (await get(db, Requirement, requirement_id)).status == "completed"
    saved = json.loads((settings.reports_dir / "run-1.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed" and saved["requirement"]["text"] == "User can check out"
    [coverage] = await find(db, Coverage, {"run_id": "run-1"})
    assert (coverage.has_test, coverage.last_status) == (False, "not_run")


async def test_a_task_is_retried_twice_before_succeeding(env):
    db, settings, requirement_id = env
    registry = agents(test_generator=FakeAgent("test_generator", {"script_id": "s"}, fail_times=2))
    report = await coordinator(db, settings, registry).run(requirement_id)
    assert report["status"] == "completed"
    generator_messages = [m for m in await MessageBus(db).history("run-1")
                          if "test_generator" in (m.from_agent, m.to_agent)]
    assert [(m.type, m.payload.get("attempt")) for m in generator_messages[:6]] == [
        ("request", 1), ("error", 1), ("request", 2), ("error", 2), ("request", 3), ("result", 3)]
    assert generator_messages[1].payload["error"] == "RuntimeError: boom 1"


async def test_three_failures_escalate_and_the_run_continues(env):
    db, settings, requirement_id = env
    registry = agents(test_generator=FakeAgent("test_generator", {"script_id": "s"}, fail_times=3))
    report = await coordinator(db, settings, registry).run(requirement_id)
    assert report["status"] == "escalated"
    [escalation] = report["escalations"]
    assert (escalation["agent"], escalation["kind"], escalation["error"]) == ("test_generator", "generate", "RuntimeError: boom 3")
    assert len(registry["test_executor"].tasks) == 1  # tc2 still generated and executed
    assert (await get(db, Requirement, requirement_id)).status == "escalated"


async def test_an_escalated_analysis_ends_the_run(env):
    db, settings, requirement_id = env
    registry = agents(requirement_analyzer=FakeAgent("requirement_analyzer", fail_times=3))
    report = await coordinator(db, settings, registry).run(requirement_id)
    assert report["status"] == "escalated" and registry["test_generator"].tasks == []


async def test_triage_runs_only_for_failed_executions(env):
    db, settings, requirement_id = env
    statuses = iter(["fail", "pass"])
    healer = FakeAgent("triage_healer", {"outcome": "bug_report"})
    registry = agents(
        test_executor=FakeAgent("test_executor", lambda t: {"execution_id": "e-" + t.payload["script_id"],
                                                            "status": next(statuses), "failure": {}}),
        triage_healer=healer)
    await coordinator(db, settings, registry).run(requirement_id)
    assert [t.payload for t in healer.tasks] == [{"execution_id": "e-s-tc1", "script_id": "s-tc1"}]


async def test_an_unexpected_error_marks_the_run_failed(env):
    db, settings, requirement_id = env

    class Exploding(Coordinator):
        async def _execute_all(self, *args, **kwargs):
            raise KeyError("db went away")

    report = await Exploding(db=db, llm=FakeLLM(), settings=settings, bus=MessageBus(db), agents=agents()).run(requirement_id)
    stored = await get(db, Requirement, requirement_id)
    assert report["status"] == "failed" and stored.status == "failed" and "db went away" in stored.error
```

- [ ] **Step 3: Run to verify it fails** (`ModuleNotFoundError: No module named 'orchestrator.coordinator'`)

- [ ] **Step 4: Write `orchestrator/coordinator.py`**

```python
"""Orchestrator agent (spec §4.1): plans a run, delegates to the specialist agents over the message bus,
retries failed tasks up to twice, escalates, and merges everything into one run report."""
import json
from typing import Any

from bson import ObjectId

from agents.base import Agent, RunContext, Task
from db.models import BugReport, Coverage, Execution, Message, Requirement, Script, TestCase, new_id
from db.repository import find, get
from db.selector_maps import latest_active
from settings import Settings


class TaskEscalated(RuntimeError):
    def __init__(self, agent: str, kind: str, task_id: str, error: str) -> None:
        super().__init__(f"{agent} {kind} task {task_id} escalated after retries: {error}")
        self.agent, self.kind, self.task_id, self.error = agent, kind, task_id, error

    def as_dict(self) -> dict[str, str]:
        return {"agent": self.agent, "kind": self.kind, "task_id": self.task_id, "error": self.error}


def default_agents() -> dict[str, Agent]:
    from agents.requirement_analyzer import RequirementAnalyzer
    from agents.test_executor import TestExecutor
    from agents.test_generator import TestGenerator

    return {"requirement_analyzer": RequirementAnalyzer(), "test_generator": TestGenerator(),
            "test_executor": TestExecutor()}


def _dump(docs) -> list[dict[str, Any]]:
    return [d.model_dump(mode="json") for d in docs]


def _coverage_status(executions: list[Execution]) -> str:
    latest: dict[str, Execution] = {}
    for execution in sorted(executions, key=lambda e: e.executed_at):
        latest[execution.script_id] = execution
    statuses = {e.status for e in latest.values()}
    for status in ("fail", "flaky", "pass"):
        if status in statuses:
            return status
    return "not_run"


class Coordinator:
    def __init__(self, *, db, llm, settings: Settings, bus, agents: dict[str, Agent], max_retries: int = 2) -> None:
        self.db, self.llm, self.settings, self.bus = db, llm, settings, bus
        self.agents, self.max_retries = agents, max_retries

    def _ctx(self, run_id: str) -> RunContext:
        return RunContext(run_id=run_id, db=self.db, llm=self.llm, settings=self.settings, bus=self.bus)

    async def _publish(self, ctx: RunContext, *, from_agent: str, to_agent: str, task_id: str, type_: str,
                       payload: dict[str, Any]) -> None:
        await self.bus.publish(Message(run_id=ctx.run_id, from_agent=from_agent, to_agent=to_agent,
                                       task_id=task_id, type=type_, payload=payload))

    async def _call(self, ctx: RunContext, agent_name: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one task to an agent, retrying on error; raise TaskEscalated after the last attempt."""
        agent, task = self.agents[agent_name], Task(new_id(), kind, payload)
        error = ""
        for attempt in range(1, self.max_retries + 2):
            await self._publish(ctx, from_agent="orchestrator", to_agent=agent_name, task_id=task.task_id,
                                type_="request", payload={"kind": kind, "attempt": attempt, **payload})
            try:
                result = await agent.handle(task, ctx)
            except Exception as exc:  # any agent failure is retried, then escalated
                error = f"{type(exc).__name__}: {exc}"
                await self._publish(ctx, from_agent=agent_name, to_agent="orchestrator", task_id=task.task_id,
                                    type_="error", payload={"kind": kind, "attempt": attempt, "error": error})
                continue
            await self._publish(ctx, from_agent=agent_name, to_agent="orchestrator", task_id=task.task_id,
                                type_="result", payload={"kind": kind, "attempt": attempt, **result})
            return result
        raise TaskEscalated(agent_name, kind, task.task_id, error)

    async def _set_status(self, requirement_id: str, status: str, error: str | None = None) -> None:
        await self.db[Requirement.collection].update_one({"_id": ObjectId(requirement_id)},
                                                         {"$set": {"status": status, "error": error}})

    async def run(self, requirement_id: str) -> dict[str, Any]:
        requirement = await get(self.db, Requirement, requirement_id)
        if requirement is None:
            raise LookupError(f"requirement {requirement_id} not found")
        ctx = self._ctx(requirement.run_id)
        await self._set_status(requirement_id, "running")
        escalations: list[dict[str, str]] = []
        try:
            try:
                analysis = await self._call(ctx, "requirement_analyzer", "analyze",
                                            {"requirement_id": requirement_id, "text": requirement.text})
            except TaskEscalated as exc:
                escalations.append(exc.as_dict())
                return await self._finish(ctx, requirement, escalations)
            script_ids = []
            for test_case_id in analysis["test_case_ids"]:
                try:
                    generated = await self._call(ctx, "test_generator", "generate", {"test_case_id": test_case_id})
                    script_ids.append(generated["script_id"])
                except TaskEscalated as exc:
                    escalations.append(exc.as_dict())
            await self._execute_all(ctx, script_ids, escalations)
            return await self._finish(ctx, requirement, escalations)
        except Exception as exc:
            return await self._finish(ctx, requirement, escalations, error=f"{type(exc).__name__}: {exc}")

    async def execute_existing(self, run_id: str) -> dict[str, Any]:
        """Re-run a run's scripts against the latest active selector map (POST /runs/{run_id}/execute)."""
        [requirement] = await find(self.db, Requirement, {"run_id": run_id})
        ctx = self._ctx(run_id)
        await self._set_status(requirement.id, "running")
        escalations: list[dict[str, str]] = []
        try:
            scripts = await find(self.db, Script, {"run_id": run_id}, sort=[("created_at", 1)])
            version = (await latest_active(self.db)).version
            await self._execute_all(ctx, [s.id for s in scripts], escalations, selector_map_version=version)
            return await self._finish(ctx, requirement, escalations)
        except Exception as exc:
            return await self._finish(ctx, requirement, escalations, error=f"{type(exc).__name__}: {exc}")

    async def _execute_all(self, ctx: RunContext, script_ids: list[str], escalations: list[dict[str, str]],
                           selector_map_version: int | None = None) -> None:
        for script_id in script_ids:
            payload: dict[str, Any] = {"script_id": script_id}
            if selector_map_version is not None:
                payload["selector_map_version"] = selector_map_version
            try:
                executed = await self._call(ctx, "test_executor", "execute", payload)
                if executed["status"] == "fail" and "triage_healer" in self.agents:
                    await self._call(ctx, "triage_healer", "triage",
                                     {"execution_id": executed["execution_id"], "script_id": script_id})
            except TaskEscalated as exc:
                escalations.append(exc.as_dict())

    async def build_report(self, run_id: str) -> dict[str, Any]:
        requirements = await find(self.db, Requirement, {"run_id": run_id})
        test_cases = await find(self.db, TestCase, {"run_id": run_id}, sort=[("generated_at", 1)])
        scripts = await find(self.db, Script, {"run_id": run_id}, sort=[("created_at", 1)])
        executions = await find(self.db, Execution, {"run_id": run_id}, sort=[("executed_at", 1)])
        bug_reports = await find(self.db, BugReport, {"run_id": run_id}, sort=[("created_at", 1)])
        coverage = await find(self.db, Coverage, {"run_id": run_id})
        return {
            "run_id": run_id,
            "status": requirements[0].status if requirements else None,
            "requirement": requirements[0].model_dump(mode="json") if requirements else None,
            "test_cases": _dump(test_cases), "scripts": _dump(scripts), "executions": _dump(executions),
            "bug_reports": _dump(bug_reports), "coverage": _dump(coverage),
            "counts": {"test_cases": len(test_cases), "scripts": len(scripts), "executions": len(executions),
                       "bug_reports": len(bug_reports),
                       **{s: sum(e.status == s for e in executions) for s in ("pass", "fail", "flaky")}},
        }

    async def _finish(self, ctx: RunContext, requirement: Requirement, escalations: list[dict[str, str]],
                      error: str | None = None) -> dict[str, Any]:
        status = "failed" if error else ("escalated" if escalations else "completed")
        executions = await find(self.db, Execution, {"run_id": ctx.run_id})
        has_test = bool(await self.db[Script.collection].count_documents({"run_id": ctx.run_id}))
        await self.db[Coverage.collection].update_one(
            {"run_id": ctx.run_id, "requirement_id": ObjectId(requirement.id)},
            {"$set": {"has_test": has_test, "last_status": _coverage_status(executions)}}, upsert=True)
        await self._set_status(requirement.id, status, error)

        report = await self.build_report(ctx.run_id) | {"status": status, "escalations": escalations, "error": error}
        self.settings.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.reports_dir / f"{ctx.run_id}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await self._publish(ctx, from_agent="orchestrator", to_agent="dashboard", task_id="run", type_="result",
                            payload={"kind": "run", "status": status, "counts": report["counts"],
                                     "escalations": escalations, "error": error})
        return report
```

- [ ] **Step 5: Run to verify it passes** — `6 passed`

---

### Task 3: REST API

**Interfaces — Produces:** `create_app(*, settings=None, db=None, llm=None, agents=None) -> FastAPI` and module-level `app`. When `db` is given, dependencies are wired immediately (tests). Otherwise the lifespan connects to Mongo, runs `init_db`, and builds `get_backend()` and `default_agents()`. `app.state.runs: dict[run_id, asyncio.Task]`.

- [ ] **Step 1: Write the failing test `tests/test_api.py`**

```python
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
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFoundError: No module named 'main'`)

- [ ] **Step 3: Write `main.py`**

```python
"""AutoPilot-QA API (spec §8). Run with: uvicorn main:app --reload"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from db.models import BugReport, Coverage, Execution, Requirement, Script, TestCase
from db.mongo_client import get_db, init_db
from db.repository import find, insert
from llm.ollama_client import get_backend
from orchestrator.coordinator import Coordinator, default_agents
from orchestrator.message_bus import MessageBus
from settings import Settings, get_settings


class RequirementIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement text must not be blank")
        return value.strip()


def create_app(*, settings: Settings | None = None, db=None, llm=None, agents=None) -> FastAPI:
    settings = settings or get_settings()
    injected = db is not None

    def wire(app: FastAPI, db, llm, agents) -> None:
        app.state.db, app.state.llm = db, llm
        app.state.bus = MessageBus(db)
        app.state.coordinator = Coordinator(db=db, llm=llm, settings=settings, bus=app.state.bus, agents=agents)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not injected:
            database = get_db(settings)
            await init_db(database)
            wire(app, database, get_backend(settings), agents or default_agents())
        yield
        for task in app.state.runs.values():
            task.cancel()
        if not injected:
            await app.state.llm.aclose()
            await app.state.db.client.close()

    app = FastAPI(title="AutoPilot-QA", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.dashboard_origin], allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.runs = settings, {}
    if injected:
        wire(app, db, llm, agents or default_agents())

    def start(request: Request, run_id: str, coro) -> None:
        request.app.state.runs[run_id] = asyncio.create_task(coro)

    def active(request: Request, run_id: str) -> bool:
        task = request.app.state.runs.get(run_id)
        return task is not None and not task.done()

    async def requirement_for(request: Request, run_id: str) -> Requirement:
        found = await find(request.app.state.db, Requirement, {"run_id": run_id})
        if not found:
            raise HTTPException(404, f"run {run_id} not found")
        return found[0]

    async def listing(request: Request, run_id: str, model, sort: str) -> list[dict[str, Any]]:
        await requirement_for(request, run_id)
        docs = await find(request.app.state.db, model, {"run_id": run_id}, sort=[(sort, 1)])
        return [d.model_dump(mode="json") for d in docs]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/requirements", status_code=202)
    async def submit_requirement(body: RequirementIn, request: Request) -> dict[str, str]:
        requirement = Requirement(text=body.text)
        requirement_id = await insert(request.app.state.db, requirement)
        start(request, requirement.run_id, request.app.state.coordinator.run(requirement_id))
        return {"run_id": requirement.run_id, "requirement_id": requirement_id, "status": requirement.status}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> dict[str, Any]:
        await requirement_for(request, run_id)
        return await request.app.state.coordinator.build_report(run_id) | {"active": active(request, run_id)}

    @app.get("/runs/{run_id}/test-cases")
    async def get_test_cases(run_id: str, request: Request):
        return await listing(request, run_id, TestCase, "generated_at")

    @app.get("/runs/{run_id}/scripts")
    async def get_scripts(run_id: str, request: Request):
        return await listing(request, run_id, Script, "created_at")

    @app.get("/runs/{run_id}/results")
    async def get_results(run_id: str, request: Request):
        return await listing(request, run_id, Execution, "executed_at")

    @app.get("/runs/{run_id}/bug-reports")
    async def get_bug_reports(run_id: str, request: Request):
        return await listing(request, run_id, BugReport, "created_at")

    @app.get("/runs/{run_id}/coverage")
    async def get_coverage(run_id: str, request: Request):
        return await listing(request, run_id, Coverage, "run_id")

    @app.get("/runs/{run_id}/messages")
    async def get_messages(run_id: str, request: Request):
        await requirement_for(request, run_id)
        return [m.model_dump(mode="json") for m in await request.app.state.bus.history(run_id)]

    @app.post("/runs/{run_id}/execute", status_code=202)
    async def execute_run(run_id: str, request: Request) -> dict[str, str]:
        await requirement_for(request, run_id)
        if active(request, run_id):
            raise HTTPException(409, f"run {run_id} is still in progress")
        start(request, run_id, request.app.state.coordinator.execute_existing(run_id))
        return {"run_id": run_id, "status": "running"}

    return app


app = create_app()
```

- [ ] **Step 4: Run to verify it passes** — `pytest -q` all green.

---

### Task 4: Architecture document and live check

- [ ] **Step 1: Write `autopilot-qa/docs/architecture.md`** with the component overview, a Mermaid sequence diagram of one run (request/result/error/thought messages, retries, triage), the "bus is the system of record, dispatch is in-process" decision, the collections, and the API table.

- [ ] **Step 2: Live check** (platform on v1, Mongo up, after long LLM jobs)

```powershell
.\.venv\Scripts\python -m uvicorn main:app --port 8000
# in another shell
Invoke-RestMethod -Method Post http://localhost:8000/requirements -ContentType 'application/json' -Body '{"text":"User can search for a product, add it to cart, and complete checkout."}'
Invoke-RestMethod http://localhost:8000/runs/<run_id>
Invoke-RestMethod http://localhost:8000/runs/<run_id>/messages
```
Expected: the status goes from `running` to `completed` or `escalated`, and the messages reconstruct every step in order.

## Phase 7 Exit Checklist

- [ ] `pytest -q` green.
- [ ] Live: `POST /requirements` for the reference flow completes, and `/runs/{id}/messages` reconstructs the run in order.

---

## Execution Notes (2026-09-27)

All tests green (bus with tailable cursors on the real Mongo, coordinator with fake agents, API with real agents +
FakeLLM). Live run through the API (`POST /api/requirements` via the Vite proxy) for the reference requirement with
the catalog on v2 completed: 3 test cases, 3 scripts, 4 executions, and **34 bus messages** (10 request, 11 result,
13 thought) that reconstruct the run in order. `POST /runs/{id}/execute` re-ran the same scripts without regenerating.
Additions during execution: `Coordinator.execute_scripts()` (used by re-execution and the evaluation runner),
`/artifacts` static mount + `screenshot_url` on results, and the `/ws/runs/{run_id}` route (Phase 9).
