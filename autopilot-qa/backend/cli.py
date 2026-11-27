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
