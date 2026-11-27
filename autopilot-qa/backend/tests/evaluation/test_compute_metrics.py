"""Metric definitions for the dissertation, checked on hand-computed synthetic trials."""
import importlib.util

import pytest

from settings import BACKEND_DIR

_SPEC = importlib.util.spec_from_file_location(
    "compute_metrics", BACKEND_DIR.parents[1] / "evaluation" / "metrics" / "compute_metrics.py")
metrics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(metrics)

REGISTRY = {"changes": [
    {"id": "catalog.add_button_renamed", "label": "benign_ui_change", "expected": "heal",
     "acceptable_locators": ["#add-to-bag-btn", "button:has-text(\"Add to Bag\")"]},
    {"id": "cart.total_doubled", "label": "real_defect", "expected": "report"},
    {"id": "catalog.add_button_wishlist", "label": "real_defect", "expected": "report"},
]}


def trial(change, *, failed, healed, final_pass, reports, decisions=(), rolled_back=0):
    return {"change_id": change, "initial_failures": failed, "heals_applied": healed, "heals_rolled_back": rolled_back,
            "final_all_pass": final_pass, "bug_reports": reports, "decisions": list(decisions)}


def decision(locator, confidence, applied):
    return {"new_locator": locator, "llm_confidence": confidence, "confidence": confidence if applied else 0.0,
            "applied": applied}


TRIALS = [
    # benign: healed cleanly, healed with a wrong-but-passing locator, and one not healed
    trial("catalog.add_button_renamed", failed=2, healed=1, final_pass=True, reports=0,
          decisions=[decision("#add-to-bag-btn", 0.9, True)]),
    trial("catalog.add_button_renamed", failed=2, healed=1, final_pass=True, reports=0,
          decisions=[decision("button:has-text('Add to Bag')", 0.85, True)]),
    trial("catalog.add_button_renamed", failed=2, healed=0, final_pass=False, reports=2,
          decisions=[decision("#add-to-bag-btn", 0.6, False)]),
    # defects: detected; the decoy once falsely healed
    trial("cart.total_doubled", failed=1, healed=0, final_pass=False, reports=1),
    trial("catalog.add_button_wishlist", failed=2, healed=1, final_pass=True, reports=0,
          decisions=[decision("button.wishlist-btn", 0.9, True)]),
    trial("catalog.add_button_wishlist", failed=2, healed=1, rolled_back=1, final_pass=False, reports=2,
          decisions=[decision("button.wishlist-btn", 0.85, True)]),
    trial("catalog.add_button_wishlist", failed=2, healed=0, final_pass=False, reports=2,
          decisions=[decision(None, 0.1, False)]),
]


def test_locators_are_compared_modulo_quotes_and_spaces():
    assert metrics.normalise_locator("button:has-text('Add to Bag')") == metrics.normalise_locator('button:has-text("Add to Bag")')


def test_headline_rates():
    summary = metrics.summarise(TRIALS, REGISTRY)
    overall = summary["overall"]
    assert overall["healing_success_rate"] == pytest.approx(2 / 3)
    assert overall["false_bug_report_rate"] == pytest.approx(1 / 3)
    assert overall["defect_detection_rate"] == pytest.approx(3 / 4)
    assert overall["false_heal_rate"] == pytest.approx(2 / 4)            # applied, even if later rolled back
    assert overall["false_committed_heal_rate"] == pytest.approx(1 / 4)  # survived the re-run
    assert overall["heal_precision"] == pytest.approx(2 / 4)  # 4 applied heals, the two decoy ones are wrong


def test_per_change_rows():
    rows = {r["change_id"]: r for r in metrics.summarise(TRIALS, REGISTRY)["changes"]}
    assert rows["catalog.add_button_renamed"] | {} == rows["catalog.add_button_renamed"]
    assert (rows["catalog.add_button_renamed"]["trials"], rows["catalog.add_button_renamed"]["correct"]) == (3, 2)
    assert (rows["catalog.add_button_wishlist"]["trials"], rows["catalog.add_button_wishlist"]["correct"]) == (3, 2)
    assert rows["catalog.add_button_wishlist"]["rolled_back"] == 1
    assert rows["cart.total_doubled"]["correct"] == 1


def test_a_benign_trial_where_nothing_failed_is_not_counted_as_a_heal_opportunity():
    quiet = [trial("catalog.add_button_renamed", failed=0, healed=0, final_pass=True, reports=0)]
    assert metrics.summarise(quiet, REGISTRY)["overall"]["healing_success_rate"] is None


def test_calibration_uses_raw_llm_confidence_against_ground_truth():
    points = metrics.decision_points(TRIALS, REGISTRY)
    assert [(p["confidence"], p["correct"]) for p in points] == [(0.9, 1), (0.85, 1), (0.6, 1), (0.9, 0), (0.85, 0)]
    cal = metrics.calibration(points, bins=5)
    assert cal["n"] == 5
    assert cal["brier"] == pytest.approx((0.01 + 0.0225 + 0.16 + 0.81 + 0.7225) / 5)
    top = next(b for b in cal["bins"] if b["lo"] == pytest.approx(0.8))
    assert (top["n"], top["accuracy"]) == (4, pytest.approx(2 / 4))


def test_latency_per_agent_from_bus_messages():
    msgs = [
        {"type": "request", "to_agent": "test_generator", "from_agent": "orchestrator", "task_id": "a", "timestamp": "2026-09-27T10:00:00Z"},
        {"type": "result", "to_agent": "orchestrator", "from_agent": "test_generator", "task_id": "a", "timestamp": "2026-09-27T10:00:30Z"},
        {"type": "request", "to_agent": "test_generator", "from_agent": "orchestrator", "task_id": "b", "timestamp": "2026-09-27T10:01:00Z"},
        {"type": "result", "to_agent": "orchestrator", "from_agent": "test_generator", "task_id": "b", "timestamp": "2026-09-27T10:01:10Z"},
    ]
    assert metrics.agent_latency(msgs)["test_generator"] == {"n": 2, "median_s": 20.0, "max_s": 30.0}


def test_markdown_report_renders():
    text = metrics.render_markdown(metrics.summarise(TRIALS, REGISTRY), metrics.calibration(metrics.decision_points(TRIALS, REGISTRY)), {}, {})
    assert "| catalog.add_button_renamed | benign_ui_change | 3 |" in text
    assert "Healing success rate" in text
