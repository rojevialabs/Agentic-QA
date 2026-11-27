"""Test Executor agent: run a script, classify pass/fail/flaky, persist the execution (spec §4.4)."""
from pathlib import Path
from typing import Any

from agents.base import Agent, RunContext, Task
from db.models import Execution, Script
from db.repository import get, insert
from db.selector_maps import by_version, latest_active, write_selector_map_file
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
        # Default to the newest active map, so one heal benefits every later script in the run.
        version = task.payload.get("selector_map_version") or (await latest_active(ctx.db)).version
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
            selector_map_version=version, attempts=len(attempts), healing=task.payload.get("healing")))

        detail = f" ({_first_line(first.error)})" if status != "pass" and first.error else ""
        await ctx.emit_thought(self.name, f"Executed script {script.id} with selector map v{version}: {status}{detail}.",
                               task_id=task.task_id)
        return {"execution_id": execution_id, "status": status, "failure": failure}
