"""Dissertation metrics from the evaluation database.

    <backend venv python> evaluation/metrics/compute_metrics.py [--db autopilot_qa_eval]

Reads the `experiments` collection written by evaluation/run_experiment.py (one document per trial run), plus
executions, bug reports, agent traces and bus messages, and writes evaluation/metrics/out/{metrics.json,report.md}.

Definitions (per trial = one seeded change applied while the validated baseline suite runs):
- healing success rate:  benign trials with >=1 change-induced failure that ended all-pass via >=1 applied heal
- false bug-report rate: benign trials that produced >=1 bug report
- defect detection rate: defect trials that produced >=1 bug report
- false heal rate:       defect trials in which >=1 heal was auto-applied
- false committed heal:  defect trials in which an auto-applied heal survived its re-run (was not rolled back)
- heal precision:        applied heals whose locator is in the change's acceptable set (any heal on a defect is wrong)
- calibration:           raw LLM confidence of every proposed locator vs ground-truth correctness (Brier, ECE)
"""
import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"
REGISTRY_PATH = ROOT / "evaluation" / "seeded-bugs" / "registry.json"


def normalise_locator(locator: str | None) -> str | None:
    return None if locator is None else locator.replace("'", '"').replace(" ", "")


def _changes(registry: dict) -> dict[str, dict]:
    return {c["id"]: c for c in registry["changes"]}


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _decision_correct(change: dict, locator: str | None) -> int:
    if change["label"] != "benign_ui_change" or locator is None:
        return 0
    return int(normalise_locator(locator) in {normalise_locator(a) for a in change.get("acceptable_locators", [])})


def trial_correct(change: dict, t: dict) -> bool:
    if change["label"] == "benign_ui_change":
        return t["final_all_pass"] and t["bug_reports"] == 0
    # Healing is transactional: a false heal that was rolled back after its re-run failed leaves no lasting damage.
    return t["bug_reports"] > 0 and t["heals_applied"] - t.get("heals_rolled_back", 0) == 0


def summarise(trials: list[dict], registry: dict) -> dict[str, Any]:
    changes = _changes(registry)
    rows = []
    for change_id, change in changes.items():
        group = [t for t in trials if t["change_id"] == change_id]
        if not group:
            continue
        rows.append({
            "change_id": change_id, "label": change["label"], "expected": change["expected"], "trials": len(group),
            "correct": sum(trial_correct(change, t) for t in group),
            "with_failures": sum(t["initial_failures"] > 0 for t in group),
            "healed": sum(t["heals_applied"] > 0 for t in group),
            "rolled_back": sum(t.get("heals_rolled_back", 0) > 0 for t in group),
            "reported": sum(t["bug_reports"] > 0 for t in group),
            "final_all_pass": sum(t["final_all_pass"] for t in group),
        })

    benign = [t for t in trials if changes[t["change_id"]]["label"] == "benign_ui_change"]
    defects = [t for t in trials if changes[t["change_id"]]["label"] == "real_defect"]
    opportunities = [t for t in benign if t["initial_failures"] > 0]
    applied = [(changes[t["change_id"]], d) for t in trials for d in t["decisions"] if d.get("applied")]
    overall = {
        "trials": len(trials), "benign_trials": len(benign), "defect_trials": len(defects),
        "healing_success_rate": _rate(sum(t["heals_applied"] > 0 and t["final_all_pass"] for t in opportunities), len(opportunities)),
        "false_bug_report_rate": _rate(sum(t["bug_reports"] > 0 for t in benign), len(benign)),
        "defect_detection_rate": _rate(sum(t["bug_reports"] > 0 for t in defects), len(defects)),
        "false_heal_rate": _rate(sum(t["heals_applied"] > 0 for t in defects), len(defects)),
        "false_committed_heal_rate": _rate(
            sum(t["heals_applied"] - t.get("heals_rolled_back", 0) > 0 for t in defects), len(defects)),
        "heal_precision": _rate(sum(_decision_correct(c, d.get("new_locator")) for c, d in applied), len(applied)),
        "heals_applied": len(applied),
    }
    return {"overall": overall, "changes": rows}


def decision_points(trials: list[dict], registry: dict) -> list[dict]:
    """Every proposed locator with its raw LLM confidence and whether it was actually right."""
    changes = _changes(registry)
    return [{"change_id": t["change_id"], "confidence": d["llm_confidence"],
             "correct": _decision_correct(changes[t["change_id"]], d["new_locator"]), "applied": bool(d.get("applied"))}
            for t in trials for d in t["decisions"]
            if d.get("new_locator") is not None and d.get("llm_confidence") is not None]


def calibration(points: list[dict], bins: int = 5) -> dict[str, Any]:
    if not points:
        return {"n": 0, "brier": None, "ece": None, "bins": []}
    table = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        members = [p for p in points if lo <= p["confidence"] < hi or (i == bins - 1 and p["confidence"] == 1.0)]
        table.append({"lo": lo, "hi": hi, "n": len(members),
                      "mean_confidence": statistics.mean(p["confidence"] for p in members) if members else None,
                      "accuracy": statistics.mean(p["correct"] for p in members) if members else None})
    n = len(points)
    return {
        "n": n,
        "brier": statistics.mean((p["confidence"] - p["correct"]) ** 2 for p in points),
        "ece": sum(b["n"] / n * abs(b["accuracy"] - b["mean_confidence"]) for b in table if b["n"]),
        "bins": table,
    }


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def agent_latency(messages: list[dict]) -> dict[str, dict]:
    """Seconds from each task's last request to its result, per agent (includes model load time)."""
    requests: dict[str, dict] = {}
    durations: dict[str, list[float]] = {}
    for m in messages:
        if m["type"] == "request":
            requests[m["task_id"]] = m
        elif m["type"] == "result" and m["task_id"] in requests and m["from_agent"] != "orchestrator":
            seconds = (_time(m["timestamp"]) - _time(requests[m["task_id"]]["timestamp"])).total_seconds()
            durations.setdefault(m["from_agent"], []).append(seconds)
    return {agent: {"n": len(v), "median_s": round(statistics.median(v), 2), "max_s": round(max(v), 2)}
            for agent, v in durations.items()}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render_markdown(summary: dict, cal: dict, latency: dict, manual: dict, title: str = "AutoPilot-QA evaluation") -> str:
    o = summary["overall"]
    lines = [
        f"# {title}", "",
        f"{o['trials']} trials ({o['benign_trials']} benign UI changes, {o['defect_trials']} real defects).", "",
        "| Metric | Value |", "|---|---|",
        f"| Healing success rate | {_pct(o['healing_success_rate'])} |",
        f"| False bug-report rate (benign) | {_pct(o['false_bug_report_rate'])} |",
        f"| Defect detection rate | {_pct(o['defect_detection_rate'])} |",
        f"| False heal rate (defects, applied) | {_pct(o['false_heal_rate'])} |",
        f"| False committed heal rate (survived the re-run) | {_pct(o['false_committed_heal_rate'])} |",
        f"| Heal precision ({o['heals_applied']} applied) | {_pct(o['heal_precision'])} |",
        f"| Confidence calibration: Brier / ECE (n={cal['n']}) | "
        f"{'n/a' if cal['brier'] is None else f'{cal['brier']:.3f} / {cal['ece']:.3f}'} |",
        "", "## Per change", "",
        "| change | label | trials | correct | with failures | healed | rolled back | reported | all pass |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['change_id']} | {r['label']} | {r['trials']} | {r['correct']} | {r['with_failures']} | "
              f"{r['healed']} | {r['rolled_back']} | {r['reported']} | {r['final_all_pass']} |" for r in summary["changes"]]
    lines += ["", "## Reliability (raw LLM confidence)", "", "| confidence bin | n | mean confidence | accuracy |", "|---|---|---|---|"]
    lines += [f"| {b['lo']:.1f}–{b['hi']:.1f} | {b['n']} | "
              f"{'—' if b['mean_confidence'] is None else f'{b['mean_confidence']:.2f}'} | "
              f"{'—' if b['accuracy'] is None else f'{b['accuracy']:.2f}'} |" for b in cal["bins"]]
    if latency:
        lines += ["", "## Agent latency", "", "| agent | tasks | median s | max s |", "|---|---|---|---|"]
        lines += [f"| {a} | {v['n']} | {v['median_s']} | {v['max_s']} |" for a, v in sorted(latency.items())]
    if manual:
        lines += ["", "## Manual baseline suite (no healing)", "", "| change | failing tests |", "|---|---|"]
        for change, tests in manual.items():
            ran = {t: s for t, s in tests.items() if s != "skipped"}  # the v2-only test is skipped on purpose
            lines.append(f"| {change} | {sum(s != 'passed' for s in ran.values())}/{len(ran)} |")
    return "\n".join(lines) + "\n"


async def load_trials(db) -> tuple[list[dict], list[dict]]:
    from db.models import BugReport, Execution
    from db.repository import find

    trials, messages = [], []
    async for experiment in db["experiments"].find({}, sort=[("started_at", 1)]):
        run_id = experiment["trial_run_id"]
        executions = await find(db, Execution, {"run_id": run_id}, sort=[("executed_at", 1)])
        latest: dict[str, Execution] = {}
        first: dict[str, Execution] = {}
        for e in executions:
            first.setdefault(e.script_id, e)
            latest[e.script_id] = e
        decisions = [t["action"] async for t in db["agent_traces"].find(
            {"run_id": run_id, "agent": "triage_healer", "action.selector_key": {"$exists": True},
             "action.applied": {"$exists": True}, "action.llm_confidence": {"$exists": True}})]
        trials.append({
            "suite": experiment.get("suite", "generated"),
            "change_id": experiment["change_id"], "trial": experiment["trial"], "run_id": run_id,
            "initial_failures": sum(e.status == "fail" and (e.failure or {}).get("kind") != "environment" for e in first.values()),
            "heals_applied": sum(bool(d.get("applied")) for d in decisions),
            "heals_rolled_back": await db["selector_maps"].count_documents({"run_id": run_id, "status": "rolled_back"}),
            "final_all_pass": bool(latest) and all(e.status == "pass" for e in latest.values()),
            "bug_reports": len(await find(db, BugReport, {"run_id": run_id})),
            "decisions": decisions,
        })
        messages += [m async for m in db["messages"].find({"run_id": run_id}, {"_id": 0})]
    for m in messages:
        m["timestamp"] = m["timestamp"].isoformat()
    return trials, messages


async def main() -> None:
    sys.path.insert(0, str(ROOT / "autopilot-qa" / "backend"))
    from db.mongo_client import create_client
    from settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="autopilot_qa_eval")
    opts = parser.parse_args()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    client = create_client(get_settings().mongo_uri)
    try:
        trials, messages = await load_trials(client[opts.db])
    finally:
        await client.close()
    manual_path = ROOT / "evaluation" / "out" / "manual-baseline.json"
    manual = json.loads(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else {}

    # Suite R (reference scripts) isolates the healer; Suite G (generated) measures the whole pipeline.
    suites = {"all": trials} | {s: [t for t in trials if t["suite"] == s] for s in ("reference", "generated")}
    results, sections = {}, []
    for name, group in suites.items():
        if not group:
            continue
        summary = summarise(group, registry)
        cal = calibration(decision_points(group, registry))
        results[name] = {"summary": summary, "calibration": cal}
        sections.append(render_markdown(summary, cal, agent_latency(messages) if name == "all" else {},
                                        manual if name == "all" else {}, title=f"AutoPilot-QA evaluation — {name} suite"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "metrics.json").write_text(json.dumps(
        {"suites": results, "latency": agent_latency(messages), "manual_baseline": manual, "trials": trials},
        indent=2, default=str), encoding="utf-8")
    report = "\n".join(sections)
    (OUT_DIR / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
