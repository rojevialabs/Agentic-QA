"""Healer evaluation spike: python -m scripts.healer_spike [--mode single|engine] [--models M ...] [--trials N]

--mode single (Phase 3b): one streamed answer to the v0 prompt, no feedback round.
--mode engine (Phase 8):  the product code path, healing.engine.propose_heal (prompt v1, verification, one feedback round).

Every proposed locator is judged independently in headless Chromium (in the target card and in a second card, to
check it is generic). Writes per-trial JSONL plus a markdown summary to evaluation/spikes/healer-3b/.
"""
import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone

from healing.candidates import extract_candidates
from healing.engine import propose_heal
from healing.prompt import HealingFailure, render_healer_prompt
from healing.verify import DomVerifier, LocatorCheck
from llm.ollama_client import LLMError, OllamaBackend
from llm.trace_parser import Action, ParseError, Thought, ThoughtActionParser
from settings import BACKEND_DIR, get_settings

FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "dom"
OUT_DIR = BACKEND_DIR.parents[1] / "evaluation" / "spikes" / "healer-3b"
SEED = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))
ELEMENT = SEED["services"]["catalog"]["elements"]["addToCartButton"]
SCOPE = SEED["services"]["catalog"]["elements"][ELEMENT["scope"]]["selector"]
TARGET_CARD, TARGET_ID = "Wireless Headphones", "p-100"
OTHER_CARD, OTHER_ID = "Yoga Mat", "p-105"
EXPECTED_LABEL = "Add to Bag"
FAILURE = HealingFailure(page_url="http://localhost:4002/catalog.html", broken_selector=ELEMENT["selector"],
                         description=ELEMENT["description"], scope_selector=SCOPE, scope_text=TARGET_CARD)
SCENARIOS = {"v2_renamed": ("catalog_v2.html", "heal"), "decoy_wishlist": ("catalog_decoy.html", "no_heal")}
DEFAULT_MODELS = ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"]


def _is_add_button(match: dict, product_id: str) -> bool:
    return (match["tag"] == "button" and match["attrs"].get("data-product-id") == product_id
            and match["text"] == EXPECTED_LABEL)


def judge(expect: str, confidence: float | None, target: LocatorCheck, other: LocatorCheck, threshold: float,
          would_apply: bool | None = None) -> dict:
    """`would_apply` defaults to the single-shot gate; engine mode passes the engine's own decision."""
    if would_apply is None:
        would_apply = confidence is not None and confidence >= threshold and target.unique
    target_ok = target.unique and _is_add_button(target.matches[0], TARGET_ID)
    generic_ok = other.unique and _is_add_button(other.matches[0], OTHER_ID)
    correct = (would_apply and target_ok and generic_ok) if expect == "heal" else not would_apply
    llm_refused = confidence is None or confidence < threshold
    return {"would_apply": would_apply, "target_ok": target_ok, "generic_ok": generic_ok,
            "llm_locator_ok": target_ok and generic_ok, "llm_refused": llm_refused,
            "rescued_by_verify": not llm_refused and not would_apply, "correct": correct}


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def run_trial(llm: OllamaBackend, verifier: DomVerifier, *, model: str, scenario: str, trial: int,
                    temperature: float, threshold: float, prompt_version: str = "v0") -> dict:
    dom_file, expect = SCENARIOS[scenario]
    html = (FIXTURES / dom_file).read_text(encoding="utf-8")
    prompt = render_healer_prompt(FAILURE, extract_candidates(
        html, broken_selector=FAILURE.broken_selector, description=FAILURE.description,
        scope_selector=FAILURE.scope_selector, scope_text=FAILURE.scope_text), version=prompt_version)

    parser, raw, events = ThoughtActionParser(), [], []
    started = time.perf_counter()
    try:
        async for chunk in llm.stream(prompt, model=model, options={"temperature": temperature, "seed": trial}):
            raw.append(chunk)
            events += parser.feed(chunk)
        events += parser.finish()
        llm_error = None
    except LLMError as exc:
        llm_error = str(exc)
    latency = time.perf_counter() - started

    action = next((e.data for e in events if isinstance(e, Action)), None)
    locator = action.get("new_locator") if action else None
    locator = locator.strip() if isinstance(locator, str) and locator.strip() else None
    confidence = _as_float(action.get("confidence")) if action else None
    target = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=TARGET_CARD)
    other = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=OTHER_CARD)
    return {
        "model": model, "scenario": scenario, "expect": expect, "trial": trial, "temperature": temperature,
        "mode": "single", "prompt_version": prompt_version, "rounds": 1, "latency_s": round(latency, 2),
        "llm_error": llm_error,
        "thoughts": [e.text for e in events if isinstance(e, Thought)],
        "parse_errors": [e.reason for e in events if isinstance(e, ParseError)],
        "action": action, "locator": locator, "confidence": confidence,
        "target": asdict(target), "other": asdict(other), "raw": "".join(raw),
        **judge(expect, confidence, target, other, threshold),
    }


async def run_engine_trial(llm: OllamaBackend, verifier: DomVerifier, *, model: str, scenario: str, trial: int,
                           temperature: float, threshold: float, prompt_version: str = "v1") -> dict:
    dom_file, expect = SCENARIOS[scenario]
    html = (FIXTURES / dom_file).read_text(encoding="utf-8")
    started = time.perf_counter()
    try:
        decision = await propose_heal(llm, verifier, FAILURE, html, selector_key="catalog.addToCartButton", model=model,
                                      threshold=threshold, options={"temperature": temperature, "seed": trial},
                                      prompt_version=prompt_version)
        llm_error = None
    except LLMError as exc:
        decision, llm_error = None, str(exc)
    latency = time.perf_counter() - started
    locator = decision.new_locator if decision else None
    target = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=TARGET_CARD)
    other = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=OTHER_CARD)
    confidence = decision.llm_confidence if decision else None
    return {
        "model": model, "scenario": scenario, "expect": expect, "trial": trial, "temperature": temperature,
        "mode": "engine", "prompt_version": prompt_version, "rounds": decision.rounds if decision else 0,
        "latency_s": round(latency, 2), "llm_error": llm_error,
        "thoughts": decision.thoughts if decision else [], "parse_errors": decision.parse_errors if decision else [],
        "action": decision.as_action() if decision else None, "locator": locator, "confidence": confidence,
        "target": asdict(target), "other": asdict(other), "raw": "",
        **judge(expect, confidence, target, other, threshold, would_apply=bool(decision and decision.applied)),
    }


def summarise(rows: list[dict], threshold: float) -> str:
    lines = [
        f"Threshold {threshold}. `correct` = heal auto-applies with a verified generic locator (v2) / "
        "no auto-apply (decoy). `locator ok` ignores confidence.",
        "",
        "| model | scenario | n | parsed | correct | locator ok | would apply | LLM refused | rescued by verify "
        "| mean rounds | mean conf | median s | p90 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in dict.fromkeys(r["model"] for r in rows):
        for scenario in dict.fromkeys(r["scenario"] for r in rows):
            group = [r for r in rows if r["model"] == model and r["scenario"] == scenario]
            if not group:
                continue
            n = len(group)
            parsed = sum(1 for r in group if r["action"] is not None and not r["parse_errors"])
            confs = [r["confidence"] for r in group if r["confidence"] is not None]
            lat = sorted(r["latency_s"] for r in group)
            p90 = statistics.quantiles(lat, n=10)[-1] if n > 1 else lat[0]
            lines.append(
                f"| {model} | {scenario} | {n} | {parsed}/{n} | **{sum(r['correct'] for r in group)}/{n}** | "
                f"{sum(r['llm_locator_ok'] for r in group)}/{n} | {sum(r['would_apply'] for r in group)}/{n} | "
                f"{sum(r['llm_refused'] for r in group)}/{n} | {sum(r['rescued_by_verify'] for r in group)}/{n} | "
                f"{statistics.mean(r.get('rounds', 1) for r in group):.2f} | "
                f"{statistics.mean(confs) if confs else float('nan'):.2f} | {statistics.median(lat):.1f} | {p90:.1f} |"
            )
    return "\n".join(lines)


async def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    args.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    args.add_argument("--trials", type=int, default=20)
    args.add_argument("--temperature", type=float, default=0.7)
    args.add_argument("--mode", choices=["single", "engine"], default="single")
    args.add_argument("--prompt-version", help="default: v0 for single, v1 for engine")
    args.add_argument("--summarise", metavar="JSONL", help="print the summary of an existing results file and exit")
    args.add_argument("--print-prompt", action="store_true", help="print the v2 prompt and exit")
    opts = args.parse_args()

    settings = get_settings()
    if opts.summarise:
        with open(opts.summarise, encoding="utf-8") as results:
            print(summarise([json.loads(line) for line in results], settings.healing_confidence_threshold))
        return
    prompt_version = opts.prompt_version or ("v1" if opts.mode == "engine" else "v0")
    trial_fn = run_engine_trial if opts.mode == "engine" else run_trial
    if opts.print_prompt:
        html = (FIXTURES / SCENARIOS["v2_renamed"][0]).read_text(encoding="utf-8")
        print(render_healer_prompt(FAILURE, extract_candidates(
            html, broken_selector=FAILURE.broken_selector, description=FAILURE.description,
            scope_selector=FAILURE.scope_selector, scope_text=FAILURE.scope_text), version=prompt_version))
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = OUT_DIR / f"results-{opts.mode}-{prompt_version}-{stamp}.jsonl"
    rows: list[dict] = []
    async with OllamaBackend(settings.ollama_url, opts.models[0]) as llm, DomVerifier() as verifier:
        with results_path.open("w", encoding="utf-8") as out:
            for model in opts.models:
                for scenario in opts.scenarios:
                    for trial in range(opts.trials):
                        row = await trial_fn(llm, verifier, model=model, scenario=scenario, trial=trial,
                                             temperature=opts.temperature, prompt_version=prompt_version,
                                             threshold=settings.healing_confidence_threshold)
                        rows.append(row)
                        out.write(json.dumps(row) + "\n")
                        out.flush()
                        print(f"{model:<18} {scenario:<15} #{trial:<2} {'OK ' if row['correct'] else 'BAD'} "
                              f"conf={row['confidence']} locator={row['locator']!r} {row['latency_s']}s", flush=True)

    summary = summarise(rows, settings.healing_confidence_threshold)
    (OUT_DIR / f"summary-{opts.mode}-{prompt_version}-{stamp}.md").write_text(
        f"# Healer spike {stamp}\n\nMode {opts.mode}, prompt {prompt_version}, temperature {opts.temperature}, "
        f"{opts.trials} trials, seeds 0..{opts.trials - 1}.\n\n{summary}\n", encoding="utf-8")
    print("\n" + summary + f"\n\nresults: {results_path}")


if __name__ == "__main__":
    asyncio.run(main())
