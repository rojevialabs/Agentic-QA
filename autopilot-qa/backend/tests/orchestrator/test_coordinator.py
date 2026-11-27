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


def one_case():
    return FakeAgent("requirement_analyzer", {"test_case_ids": ["tc1"], "counts": {}})


def numbered_executor(statuses):
    status_iter = iter(statuses)
    agent = FakeAgent("test_executor")
    agent.result = lambda t: {"execution_id": f"e{len(agent.tasks)}", "status": next(status_iter), "failure": {}}
    return agent


async def test_a_heal_triggers_exactly_one_re_run_on_the_new_map(env):
    db, settings, requirement_id = env
    executor = numbered_executor(["fail", "pass"])
    healer = FakeAgent("triage_healer", {"outcome": "healed", "selector_map_version": 2, "decision": {"new_locator": "#b"}})
    registry = agents(requirement_analyzer=one_case(), test_executor=executor, triage_healer=healer)
    report = await coordinator(db, settings, registry).run(requirement_id)
    assert [t.payload for t in executor.tasks] == [
        {"script_id": "s-tc1"},
        {"script_id": "s-tc1", "selector_map_version": 2, "check_flaky": False,
         "healing": {"new_locator": "#b", "healed_from_execution_id": "e1"}}]
    assert len(healer.tasks) == 1 and report["status"] == "completed"


async def test_a_re_run_that_still_fails_is_triaged_after_heal(env):
    db, settings, requirement_id = env
    healer = FakeAgent("triage_healer", lambda t: {"outcome": "bug_report"} if t.payload.get("after_heal")
                       else {"outcome": "healed", "selector_map_version": 2, "decision": {}})
    registry = agents(requirement_analyzer=one_case(), test_executor=numbered_executor(["fail", "fail"]),
                      triage_healer=healer)
    await coordinator(db, settings, registry).run(requirement_id)
    assert [t.payload for t in healer.tasks] == [{"execution_id": "e1", "script_id": "s-tc1"},
                                                 {"execution_id": "e2", "script_id": "s-tc1", "after_heal": True}]


async def test_the_case_limit_reaches_the_analyzer(env):
    db, settings, _ = env
    requirement_id = await insert(db, Requirement(text="limited", run_id="run-2", max_cases=2))
    registry = agents()
    await coordinator(db, settings, registry).run(requirement_id)
    assert registry["requirement_analyzer"].tasks[0].payload["max_cases"] == 2


async def test_each_attempt_tells_the_agent_its_number(env):
    db, settings, requirement_id = env
    generator = FakeAgent("test_generator", {"script_id": "s"}, fail_times=2)
    await coordinator(db, settings, agents(test_generator=generator)).run(requirement_id)
    first_case = [t.attempt for t in generator.tasks[:3]]
    assert first_case == [1, 2, 3]
