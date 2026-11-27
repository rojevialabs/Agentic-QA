"""AutoPilot-QA evaluation runner (roadmap Phase 10).

Run from the repository root with the backend virtualenv, ShopLite running (`npm run start:all` in target-platform)
and Ollama up:

    autopilot-qa\\backend\\.venv\\Scripts\\python evaluation\\run_experiment.py baseline
    autopilot-qa\\backend\\.venv\\Scripts\\python evaluation\\run_experiment.py trials [--changes ID ...] [--trials N]
    autopilot-qa\\backend\\.venv\\Scripts\\python evaluation\\run_experiment.py manual [--changes ID ...]
    autopilot-qa\\backend\\.venv\\Scripts\\python evaluation\\metrics\\compute_metrics.py

baseline-reference: registers Suite R, the hand-written reference scripts in autopilot-qa/tests-generated/reference
          (same format as generated scripts, logical selector keys). It isolates the healer from generator quality
          and exercises every seeded change.
baseline: for each registry requirement, a full run (analyze -> generate -> execute) on the clean platform. Scripts
          that pass there form the validated suite (a script that fails on a clean platform is a generation defect,
          not a change effect).
trials:   for each seeded change x N, reset healer-created selector maps, switch the change on through
          /api/__config, and execute a fresh copy of the validated suite (with triage and healing). Scripts are not
          regenerated, so every change faces the same tests; the healer is the variable under test.
manual:   the hand-written Playwright suite in evaluation/manual-baseline under each change (it never heals).

Everything is stored in the `autopilot_qa_eval` database (experiments collection) and evaluation/out/.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "autopilot-qa" / "backend"))

from db.models import Execution, Requirement, Script, TestCase  # noqa: E402
from db.mongo_client import create_client, init_db  # noqa: E402
from db.repository import find, get, insert  # noqa: E402
from execution.playwright_runner import ANSI, playwright_cli, reset_all_services  # noqa: E402
from llm.ollama_client import OllamaBackend  # noqa: E402
from orchestrator.coordinator import Coordinator, default_agents  # noqa: E402
from orchestrator.message_bus import MessageBus  # noqa: E402
from settings import get_settings  # noqa: E402

EVAL_DB = "autopilot_qa_eval"
OUT = ROOT / "evaluation" / "out"
REGISTRY = json.loads((ROOT / "evaluation" / "seeded-bugs" / "registry.json").read_text(encoding="utf-8"))
MANUAL_DIR = ROOT / "evaluation" / "manual-baseline"
REFERENCE_DIR = ROOT / "autopilot-qa" / "tests-generated" / "reference"


async def configure_platform(urls: dict[str, str], changes: list[str]) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        for service, url in urls.items():
            response = await client.post(f"{url}/api/__config", json={"seeded_changes": changes})
            response.raise_for_status()


async def reset_selector_maps(db) -> None:
    """Retire every live heal so each trial starts from the seed map (v1); rolled-back maps stay countable."""
    await db["selector_maps"].update_many({"created_by": "healer", "status": {"$in": ["active", "pending_review"]}},
                                          {"$set": {"status": "archived"}})


def coordinator(db, llm, settings) -> Coordinator:
    return Coordinator(db=db, llm=llm, settings=settings, bus=MessageBus(db), agents=default_agents())


async def latest_status(db, run_id: str) -> dict[str, str]:
    latest: dict[str, str] = {}
    for e in await find(db, Execution, {"run_id": run_id}, sort=[("executed_at", 1)]):
        latest[e.script_id] = e.status
    return latest


async def clean_passes(db, run_id: str) -> list[str]:
    """Scripts whose every execution passed with no healing: a heal on the clean platform can only have masked
    a defect in the script itself, so such scripts are not valid baseline tests."""
    by_script: dict[str, list[Execution]] = {}
    for e in await find(db, Execution, {"run_id": run_id}, sort=[("executed_at", 1)]):
        by_script.setdefault(e.script_id, []).append(e)
    return [sid for sid, runs in by_script.items() if all(e.status == "pass" and not e.healing for e in runs)]


async def revalidate(db) -> None:
    path = OUT / "baseline.json"
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    for run in runs:
        before = len(run["valid_script_ids"])
        run["valid_script_ids"] = await clean_passes(db, run["run_id"])
        print(f"{run['suite']:<9} {run['requirement'][:50]:<50} valid {before} -> {len(run['valid_script_ids'])}")
    path.write_text(json.dumps({"db": EVAL_DB, "runs": runs}, indent=2), encoding="utf-8")


async def baseline(db, llm, settings, requirements: list[str], append: bool = False) -> None:
    await reset_selector_maps(db)
    await configure_platform(settings.service_urls(), [])
    runs = []
    for text in requirements:
        requirement = Requirement(text=text)
        requirement_id = await insert(db, requirement)
        print(f"baseline: {text}", flush=True)
        report = await coordinator(db, llm, settings).run(requirement_id)
        statuses = await latest_status(db, requirement.run_id)
        valid = await clean_passes(db, requirement.run_id)
        runs.append({"suite": "generated", "requirement": text, "requirement_id": requirement_id, "run_id": requirement.run_id,
                     "status": report["status"], "scripts": len(statuses), "valid_script_ids": valid})
        print(f"  {report['status']}: {len(valid)}/{len(statuses)} scripts pass on the clean platform", flush=True)
    save_baseline(runs, append)


async def baseline_reference(db, llm, settings) -> None:
    """Register Suite R as its own baseline run and validate it on the clean platform."""
    await reset_selector_maps(db)
    await configure_platform(settings.service_urls(), [])
    requirement = Requirement(text="Reference suite (hand-written scripts with logical selector keys)")
    requirement.id = await insert(db, requirement)
    script_ids = []
    for spec in sorted(REFERENCE_DIR.glob("*.spec.js")):
        code = spec.read_text(encoding="utf-8")
        title = re.search(r"test\('([^']+)'", code).group(1)
        case_id = await insert(db, TestCase(run_id=requirement.run_id, requirement_id=requirement.id, type="positive",
                                            title=title, steps=[f"see {spec.name}"], expected_result="the script passes"))
        script_ids.append(await insert(db, Script(run_id=requirement.run_id, test_case_id=case_id, code=code, path=str(spec))))
    report = await coordinator(db, llm, settings).execute_scripts(requirement, script_ids)
    statuses = await latest_status(db, requirement.run_id)
    valid = await clean_passes(db, requirement.run_id)
    print(f"reference suite {report['status']}: {len(valid)}/{len(statuses)} scripts pass on the clean platform", flush=True)
    save_baseline([{"suite": "reference", "requirement": requirement.text, "requirement_id": requirement.id,
                    "run_id": requirement.run_id, "status": report["status"], "scripts": len(statuses),
                    "valid_script_ids": valid}], append=True)


def save_baseline(runs: list[dict], append: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "baseline.json"
    if append and path.exists():
        # Keep, per requirement, whichever baseline run produced the most validated scripts.
        merged = {r["requirement"]: r for r in json.loads(path.read_text(encoding="utf-8"))["runs"]}
        for run in runs:
            best = merged.get(run["requirement"])
            if best is None or len(run["valid_script_ids"]) > len(best["valid_script_ids"]):
                merged[run["requirement"]] = run
        runs = list(merged.values())
    path.write_text(json.dumps({"db": EVAL_DB, "runs": runs}, indent=2), encoding="utf-8")


async def trials(db, llm, settings, change_ids: list[str], count: int) -> None:
    runs = json.loads((OUT / "baseline.json").read_text(encoding="utf-8"))["runs"]
    changes = [c for c in REGISTRY["changes"] if not change_ids or c["id"] in change_ids]
    try:
        for change in changes:
            for trial in range(count):
                for base in runs:
                    if not base["valid_script_ids"]:
                        continue
                    await reset_selector_maps(db)
                    await configure_platform(settings.service_urls(), [change["id"]])
                    requirement = Requirement(text=base["requirement"])
                    requirement.id = await insert(db, requirement)
                    copies = []
                    for script_id in base["valid_script_ids"]:
                        original = await get(db, Script, script_id)
                        copies.append(await insert(db, Script(run_id=requirement.run_id, test_case_id=original.test_case_id,
                                                              code=original.code, path=original.path)))
                    started = datetime.now(timezone.utc)
                    report = await coordinator(db, llm, settings).execute_scripts(requirement, copies)
                    await db["experiments"].insert_one({
                        "suite": base.get("suite", "generated"),
                        "change_id": change["id"], "label": change["label"], "expected": change["expected"],
                        "trial": trial, "trial_run_id": requirement.run_id, "baseline_run_id": base["run_id"],
                        "requirement": base["requirement"], "status": report["status"], "counts": report["counts"],
                        "started_at": started, "finished_at": datetime.now(timezone.utc)})
                    statuses = sorted((await latest_status(db, requirement.run_id)).values())
                    print(f"{change['id']:<32} #{trial} {base['requirement'][:40]:<40} {report['status']:<10} "
                          f"final={statuses} bugs={report['counts']['bug_reports']}", flush=True)
    finally:
        await configure_platform(settings.service_urls(), [])


def _walk(suite: dict, out: dict[str, str]) -> None:
    for spec in suite.get("specs", []):
        results = spec["tests"][0]["results"] if spec.get("tests") else []
        out[spec["title"]] = results[-1]["status"] if results else "skipped"
    for child in suite.get("suites", []):
        _walk(child, out)


async def manual(settings, change_ids: list[str]) -> None:
    outcomes = {}
    targets = ["(none)"] + [c["id"] for c in REGISTRY["changes"] if not change_ids or c["id"] in change_ids]
    try:
        for change in targets:
            await configure_platform(settings.service_urls(), [] if change == "(none)" else [change])
            await reset_all_services(settings.service_urls())
            proc = subprocess.run(["node", str(playwright_cli(MANUAL_DIR)), "test", "--reporter=json"], cwd=MANUAL_DIR,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env={**os.environ, "FORCE_COLOR": "0"})
            report = json.loads(proc.stdout[proc.stdout.index("{"):]) if "{" in proc.stdout else {}
            tests: dict[str, str] = {}
            for suite in report.get("suites", []):
                _walk(suite, tests)
            outcomes[change] = tests
            failing = [t for t, s in tests.items() if s not in ("passed", "skipped")]
            print(f"{change:<32} {len(failing)} failing: {', '.join(failing) or '-'}", flush=True)
            if not tests:
                print(ANSI.sub("", proc.stderr)[:500])
    finally:
        await configure_platform(settings.service_urls(), [])
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "manual-baseline.json"
    merged = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}  # --changes updates only those
    path.write_text(json.dumps(merged | outcomes, indent=2), encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    base = commands.add_parser("baseline")
    base.add_argument("--requirement", action="append", help="default: the registry requirements")
    base.add_argument("--append", action="store_true", help="merge into baseline.json (keeps the better run per requirement)")
    commands.add_parser("baseline-reference")
    commands.add_parser("revalidate", help="re-apply the clean-pass rule to baseline.json")
    run = commands.add_parser("trials")
    run.add_argument("--changes", nargs="+", default=[])
    run.add_argument("--trials", type=int, default=5)
    man = commands.add_parser("manual")
    man.add_argument("--changes", nargs="+", default=[])
    opts = parser.parse_args()

    settings = get_settings()
    if opts.command == "manual":
        await manual(settings, opts.changes)
        return
    client = create_client(settings.mongo_uri)
    db = client[EVAL_DB]
    await init_db(db)
    try:
        async with OllamaBackend(settings.ollama_url, settings.analyzer_model) as llm:
            if opts.command == "revalidate":
                await revalidate(db)
            elif opts.command == "baseline-reference":
                await baseline_reference(db, llm, settings)
            elif opts.command == "baseline":
                await baseline(db, llm, settings, opts.requirement or REGISTRY["requirements"], append=opts.append)
            else:
                await trials(db, llm, settings, opts.changes, opts.trials)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
