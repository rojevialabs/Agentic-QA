"""Live check of the local models: python -m scripts.ollama_smoke [model ...]

For each model: one non-streamed call, then one streamed THOUGHT/ACTION answer run through the parser.
Prints latency and throughput so model choice (roadmap Phase 3b) is based on data.
"""
import asyncio
import sys
import time

from llm.ollama_client import LLMError, OllamaBackend
from llm.trace_parser import ThoughtActionParser
from settings import get_settings

DEFAULT_MODELS = ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"]

HEALER_PROMPT = """A Playwright test failed: locator '#add-to-cart-btn' inside the product card for "Wireless Headphones" was not found.
Candidate elements now inside that card:
1. <h2 class="product-name">Wireless Headphones</h2>
2. <span class="product-price">$59.99</span>
3. <button id="add-to-bag-btn" data-product-id="p-100">Add to Bag</button>

Answer in exactly this format and nothing else:
THOUGHT: <1-2 sentences of reasoning>
ACTION: {"new_locator": "<css selector>", "confidence": <0.0-1.0>, "basis": "<short reason>"}"""


async def check(llm: OllamaBackend, model: str) -> None:
    print(f"\n=== {model} ===")
    started = time.perf_counter()
    reply = await llm.generate("Reply with exactly the word OK.", model=model, options={"temperature": 0})
    print(f"generate: {reply.strip()!r} in {time.perf_counter() - started:.1f}s (includes model load)")

    parser, chunks = ThoughtActionParser(), 0
    started = time.perf_counter()
    first_token_at = None
    async for chunk in llm.stream(HEALER_PROMPT, model=model, options={"temperature": 0}):
        first_token_at = first_token_at or time.perf_counter()
        chunks += 1
        for event in parser.feed(chunk):
            print(f"  {event}")
    for event in parser.finish():
        print(f"  {event}")
    total = time.perf_counter() - started
    ttft = (first_token_at or time.perf_counter()) - started
    print(f"stream: {chunks} chunks, first token {ttft:.1f}s, total {total:.1f}s, ~{chunks / max(total - ttft, 1e-6):.1f} chunks/s")


async def main(models: list[str]) -> int:
    async with OllamaBackend(get_settings().ollama_url, models[0]) as llm:
        for model in models:
            try:
                await check(llm, model)
            except LLMError as exc:
                print(f"FAILED {model}: {exc}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or DEFAULT_MODELS)))
