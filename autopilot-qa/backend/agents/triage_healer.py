"""Triage & Healer agent (spec §4.5, §9): heal benign locator changes with an explainable trace, report the rest."""
from pathlib import Path
from typing import Any

from agents.base import Agent, RunContext, Task
from db.models import BugReport, Execution, Script, SelectorMap, TestCase
from db.repository import get, insert
from db.selector_maps import by_version, derive, latest_active
from healing.engine import HealDecision, propose_heal
from healing.prompt import HealingFailure
from healing.triage import Triage, classify
from healing.verify import DomVerifier

HEALER_OPTIONS = {"temperature": 0.2, "num_ctx": 4096}


def _why_not_applied(decision: HealDecision) -> str:
    if decision.new_locator is None:
        return decision.verification or "no replacement proposed"
    if not decision.verified:
        return f"verification failed: {decision.verification}"
    return f"confidence {decision.confidence:.2f} below threshold {decision.threshold:.2f}"


class TriageHealer(Agent):
    name = "triage_healer"

    def __init__(self, verifier_factory=DomVerifier) -> None:
        self._verifier_factory = verifier_factory

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        execution = await get(ctx.db, Execution, task.payload["execution_id"])
        if execution is None:
            raise LookupError(f"execution {task.payload['execution_id']} not found")
        script = await get(ctx.db, Script, execution.script_id)
        test_case = await get(ctx.db, TestCase, script.test_case_id) if script else None
        failure = execution.failure or {"kind": "script", "error": "\n".join(execution.logs)}
        services = (await by_version(ctx.db, execution.selector_map_version)).map

        triage = classify(failure, services)
        await ctx.emit_thought(self.name, f"Triage: {triage.summary()}", task_id=task.task_id,
                               action={"triage": triage.as_dict()})
        if task.payload.get("after_heal"):
            return await self._roll_back(ctx, task, execution, test_case, triage)
        if not triage.healable:
            return await self._bug_report(ctx, task, execution, test_case, triage,
                                          f"Triage category {triage.category}: not attributable to a benign interface change.")

        service, element = triage.selector_key.split(".")
        latest = await latest_active(ctx.db)
        current = latest.map.get(service, {}).get("elements", {}).get(element, {}).get("selector")
        if current and current != triage.broken_selector:
            await ctx.emit_thought(self.name, f"Selector map v{latest.version} already maps {triage.selector_key} "
                                              f"to {current}; re-running with it.", task_id=task.task_id)
            return {"outcome": "healed", "selector_map_version": latest.version,
                    "decision": {"selector_key": triage.selector_key, "new_locator": current, "applied": True,
                                 "reused_version": latest.version}}

        dom_path = failure.get("dom_path")
        if not dom_path or not Path(dom_path).exists():
            return await self._bug_report(ctx, task, execution, test_case, triage,
                                          "No DOM snapshot was captured, so healing could not be attempted.")

        spec = services[service]["elements"][element]
        default_scope = services[service]["elements"][spec["scope"]]["selector"] if spec.get("scope") else None
        healing_failure = HealingFailure(page_url=failure.get("url") or "", broken_selector=triage.broken_selector,
                                         description=spec["description"],
                                         scope_selector=triage.scope_selector or default_scope,
                                         scope_text=triage.scope_text)

        async def stream_thought(text: str) -> None:
            await ctx.emit_thought(self.name, f"THOUGHT: {text}", task_id=task.task_id)

        async with self._verifier_factory() as verifier:
            decision = await propose_heal(ctx.llm, verifier, healing_failure, Path(dom_path).read_text(encoding="utf-8"),
                                          selector_key=triage.selector_key, model=ctx.settings.healer_model,
                                          threshold=ctx.settings.healing_confidence_threshold,
                                          options=HEALER_OPTIONS, on_thought=stream_thought,
                                          prompt_version=ctx.settings.healer_prompt_version)
        action = decision.as_action()

        if decision.applied:
            healed = derive(latest, service, element, decision.new_locator, run_id=ctx.run_id)
            await insert(ctx.db, healed)
            action["selector_map_version"] = healed.version
            await ctx.emit_thought(self.name, f"ACTION: applied {triage.selector_key} -> {decision.new_locator} "
                                              f"(confidence {decision.confidence:.2f} >= {decision.threshold:.2f}); "
                                              f"selector map v{healed.version}.", task_id=task.task_id, action=action)
            return {"outcome": "healed", "selector_map_version": healed.version, "decision": action}

        if decision.new_locator:
            # One reviewable proposal per (element, locator): several failing scripts must not create duplicates.
            existing = await ctx.db[SelectorMap.collection].find_one(
                {"status": "pending_review", f"map.{service}.elements.{element}.selector": decision.new_locator})
            if existing is None:
                proposal = derive(latest, service, element, decision.new_locator, status="pending_review", run_id=ctx.run_id)
                await insert(ctx.db, proposal)
                existing = {"version": proposal.version}
            action["proposal_version"] = existing["version"]
        reason = _why_not_applied(decision)
        await ctx.emit_thought(self.name, f"ACTION: not applied ({reason}).", task_id=task.task_id, action=action)
        if decision.new_locator and decision.verified:
            note = (f"Possible benign UI change requiring manual confirmation: proposed {decision.new_locator} "
                    f"for {triage.selector_key} ({reason}).")
        else:
            note = f"Healing was attempted but not applied: {reason}."
        return await self._bug_report(ctx, task, execution, test_case, triage, note) | {"decision": action}

    async def _roll_back(self, ctx: RunContext, task: Task, execution: Execution, test_case: TestCase | None,
                         triage: Triage) -> dict[str, Any]:
        """Healing is transactional: a heal whose re-run still fails is withdrawn so no later run uses it."""
        rolled = await ctx.db[SelectorMap.collection].find_one_and_update(
            {"version": execution.selector_map_version, "status": "active", "created_by": "healer"},
            {"$set": {"status": "rolled_back"}})
        note = "The healed locator did not fix the failure; manual investigation required."
        if rolled:
            note = f"Selector map v{rolled['version']} was rolled back: the healed locator did not fix the failure."
            await ctx.emit_thought(self.name, f"Rolled back selector map v{rolled['version']}: the re-run still failed.",
                                   task_id=task.task_id, action={"rolled_back_version": rolled["version"]})
        result = await self._bug_report(ctx, task, execution, test_case, triage, note)
        return result | {"rolled_back_version": rolled["version"] if rolled else None}

    async def _bug_report(self, ctx: RunContext, task: Task, execution: Execution, test_case: TestCase | None,
                          triage: Triage, note: str) -> dict[str, Any]:
        report = BugReport(run_id=ctx.run_id, execution_id=execution.id,
                           steps_to_reproduce=list(test_case.steps) if test_case else [],
                           expected=test_case.expected_result if test_case else "The test passes",
                           actual=triage.detail, root_cause=triage.root_cause, severity=triage.severity, notes=note)
        report_id = await insert(ctx.db, report)
        await ctx.emit_thought(self.name, f"Raised a {triage.severity}-severity {triage.root_cause} bug report. {note}",
                               task_id=task.task_id, action={"bug_report_id": report_id})
        return {"outcome": "bug_report", "bug_report_id": report_id, "root_cause": triage.root_cause,
                "severity": triage.severity}
