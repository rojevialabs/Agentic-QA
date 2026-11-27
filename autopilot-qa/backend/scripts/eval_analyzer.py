"""Live check of the Requirement Analyzer: python -m scripts.eval_analyzer [--model M]

Runs the three evaluation requirements, prints the test cases for human review, and saves them to
evaluation/spikes/analyzer/<model>-<timestamp>.json.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

from agents.requirement_analyzer import AnalyzerError, analyze
from llm.ollama_client import OllamaBackend
from settings import BACKEND_DIR, get_settings

REQUIREMENTS = [
    "User can search for a product, add it to cart, and complete checkout.",
    "User can log in with valid credentials and sees an error with a wrong password.",
    "Payment is declined when the card is expired.",
]
OUT_DIR = BACKEND_DIR.parents[1] / "evaluation" / "spikes" / "analyzer"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=get_settings().analyzer_model)
    opts = parser.parse_args()

    report = {"model": opts.model, "runs": []}
    async with OllamaBackend(get_settings().ollama_url, opts.model) as llm:
        for requirement in REQUIREMENTS:
            started = time.perf_counter()
            try:
                drafts, error = await analyze(llm, requirement, model=opts.model), None
            except AnalyzerError as exc:
                drafts, error = [], str(exc)
            seconds = round(time.perf_counter() - started, 1)
            print(f"\n### {requirement}  ({seconds}s)")
            for d in drafts:
                print(f"- [{d.type}] {d.title}\n    steps: {' | '.join(d.steps)}\n    expect: {d.expected_result}")
            if error:
                print(f"  ERROR: {error}")
            report["runs"].append({"requirement": requirement, "seconds": seconds, "error": error,
                                   "test_cases": [d.model_dump() for d in drafts]})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"{opts.model.replace(':', '_')}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nsaved {path}")


if __name__ == "__main__":
    asyncio.run(main())
