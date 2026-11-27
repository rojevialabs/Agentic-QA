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

    from agents.triage_healer import TriageHealer

    return {"requirement_analyzer": RequirementAnalyzer(), "test_generator": TestGenerator(),
            "test_executor": TestExecutor(), "triage_healer": TriageHealer()}


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
                result = await agent.handle(Task(task.task_id, kind, payload, attempt), ctx)
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
                                            {"requirement_id": requirement_id, "text": requirement.text,
                                             "max_cases": requirement.max_cases})
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
        scripts = await find(self.db, Script, {"run_id": run_id}, sort=[("created_at", 1)])
        return await self.execute_scripts(requirement, [s.id for s in scripts])

    async def execute_scripts(self, requirement: Requirement, script_ids: list[str]) -> dict[str, Any]:
        """Execute (and triage/heal) the given scripts as `requirement`'s run; used by re-execution and evaluation."""
        ctx = self._ctx(requirement.run_id)
        await self._set_status(requirement.id, "running")
        escalations: list[dict[str, str]] = []
        try:
            version = (await latest_active(self.db)).version
            await self._execute_all(ctx, script_ids, escalations, selector_map_version=version)
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
                    await self._triage(ctx, script_id, executed["execution_id"])
            except TaskEscalated as exc:
                escalations.append(exc.as_dict())

    async def _triage(self, ctx: RunContext, script_id: str, execution_id: str) -> None:
        """Triage a failure; after a successful heal, re-run the script once on the new selector map (spec §9)."""
        triage = await self._call(ctx, "triage_healer", "triage", {"execution_id": execution_id, "script_id": script_id})
        if triage.get("outcome") != "healed":
            return
        rerun = await self._call(ctx, "test_executor", "execute", {
            "script_id": script_id, "selector_map_version": triage["selector_map_version"], "check_flaky": False,
            "healing": triage.get("decision", {}) | {"healed_from_execution_id": execution_id}})
        if rerun["status"] != "pass":
            await self._call(ctx, "triage_healer", "triage",
                             {"execution_id": rerun["execution_id"], "script_id": script_id, "after_heal": True})

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
