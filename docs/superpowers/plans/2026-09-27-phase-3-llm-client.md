# Phase 3 — LLM Client & Reasoning-Trace Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement roadmap contracts C1 (`LLMBackend` + `OllamaBackend`, non-streaming and streaming) and C2 (`ThoughtActionParser`). Both are fully unit-tested without a model, and a live smoke script measures the three local models.

**Architecture:** `llm/ollama_client.py` wraps `POST {OLLAMA_URL}/api/generate` with `httpx.AsyncClient`. The HTTP transport can be injected, so tests use `httpx.MockTransport` with NDJSON bodies. Every failure (unreachable, model missing, timeout, error mid-stream) becomes one `LLMError` with an actionable message. `llm/trace_parser.py` is a pure, incremental state machine (PREAMBLE → THOUGHT → ACTION → DONE). It emits each THOUGHT line as soon as its newline arrives, and the ACTION as soon as its JSON object is balanced, so the Healer can stream thoughts to the dashboard live.

**Tech Stack:** Python 3.12, httpx ≥0.27, pytest-asyncio. Ollama 0.34 with `llama3.1:8b`, `qwen2.5-coder:7b`, `llama3.2:3b`.

## Global Constraints

- Ollama endpoint: `POST /api/generate`, body `{model, prompt, stream, system?, format?, options?}`. The non-stream reply is JSON `{response, done, ...}`. The stream is NDJSON with a `response` chunk per line and a final `done: true`.
- Healer output format: `THOUGHT: <1-2 sentences>` then `ACTION: {"new_locator": "...", "confidence": 0.0-1.0, "basis": "..."}`.
- Contract C1 signatures are fixed: `generate(prompt, *, system=None, model=None, format=None, options=None) -> str` and `stream(prompt, *, system=None, model=None, options=None) -> AsyncIterator[str]`.
- Contract C2 is fixed: `ThoughtActionParser.feed(chunk) -> list[Thought | Action | ParseError]` and `.finish() -> list[...]`.
- Unit tests never call a real model. Live checks live in `scripts/ollama_smoke.py` only.

## File Map

```
autopilot-qa/backend/
  llm/__init__.py
  llm/ollama_client.py     LLMError, LLMBackend, OllamaBackend, get_backend
  llm/trace_parser.py      Thought, Action, ParseError, ThoughtActionParser, parse
  scripts/ollama_smoke.py  live latency + format check per model
  tests/llm/test_ollama_client.py
  tests/llm/test_trace_parser.py
```

---

### Task 1: `ThoughtActionParser` (C2)

**Files:** Create `llm/__init__.py` (empty), `llm/trace_parser.py`, `tests/llm/test_trace_parser.py`.

**Interfaces — Produces:** frozen dataclasses `Thought(text: str)`, `Action(data: dict)`, `ParseError(raw: str, reason: str)`; `ThoughtActionParser` with `feed`, `finish`, and property `done: bool`; `parse(text) -> list[Event]` (feed + finish). `ParseError.reason` is one of `"missing ACTION"`, `"incomplete ACTION JSON"`, or starts with `"invalid ACTION JSON"`.

Parsing rules:
1. A line whose start matches `thought:` (any case, optionally wrapped in markdown `**`, `#`, `>`, `-`) opens THOUGHT state and emits its text.
2. In THOUGHT state, every further non-empty line is its own `Thought` (continuation).
3. Lines before any marker (preamble) are ignored.
4. ACTION opens on an uppercase `ACTION:` anywhere, or `action:` in any case at a line start. Lowercase "action:" mid-sentence is thought text.
5. After ACTION, the first balanced `{...}` (string- and escape-aware, so code fences and trailing prose are fine) is parsed as JSON. After that the parser is DONE and ignores further input.
6. `finish()` flushes a trailing partial line, then reports `missing ACTION` or `incomplete ACTION JSON` if applicable.

- [ ] **Step 1: Write the failing test `tests/llm/test_trace_parser.py`**

```python
from llm.trace_parser import Action, ParseError, Thought, ThoughtActionParser, parse

ACTION_JSON = '{"new_locator": "#add-to-bag-btn", "confidence": 0.91, "basis": "same data-product-id, text Add to Bag"}'
ACTION_DATA = {"new_locator": "#add-to-bag-btn", "confidence": 0.91, "basis": "same data-product-id, text Add to Bag"}
RESPONSE = f"THOUGHT: The add-to-cart id is gone but a button with the same data-product-id says Add to Bag.\nACTION: {ACTION_JSON}"


def feed_chunks(chunks):
    parser = ThoughtActionParser()
    events = []
    for chunk in chunks:
        events += parser.feed(chunk)
    return events + parser.finish()


def test_parses_a_complete_response():
    assert parse(RESPONSE) == [
        Thought("The add-to-cart id is gone but a button with the same data-product-id says Add to Bag."),
        Action(ACTION_DATA),
    ]


def test_character_by_character_stream_gives_the_same_events():
    assert feed_chunks(list(RESPONSE)) == parse(RESPONSE)


def test_thought_is_emitted_as_soon_as_its_newline_arrives():
    parser = ThoughtActionParser()
    assert parser.feed("THOUGHT: first idea") == []
    assert parser.feed("\nTHOUGHT: sec") == [Thought("first idea")]
    assert parser.feed("ond idea\n") == [Thought("second idea")]
    assert parser.feed("ACTION: {\"new_locator\": \"#x\",") == []
    assert parser.feed(" \"confidence\": 0.5, \"basis\": \"b\"}") == [Action({"new_locator": "#x", "confidence": 0.5, "basis": "b"})]
    assert parser.done


def test_markers_split_across_chunks():
    assert feed_chunks(["THOU", "GHT: x\nACT", "ION: {\"a\":", " 1}"]) == [Thought("x"), Action({"a": 1})]


def test_continuation_lines_and_blank_lines():
    text = "THOUGHT: line one\n\n  line two  \nACTION: {\"a\": 1}"
    assert parse(text) == [Thought("line one"), Thought("line two"), Action({"a": 1})]


def test_markdown_and_lowercase_markers():
    text = "**Thought:** the id changed\n**ACTION:** {\"a\": 2}"
    assert parse(text) == [Thought("the id changed"), Action({"a": 2})]
    assert parse("thought: t\naction: {\"a\": 3}") == [Thought("t"), Action({"a": 3})]


def test_json_in_a_code_fence_spanning_lines_with_trailing_prose():
    text = 'THOUGHT: t\nACTION:\n```json\n{\n  "new_locator": "#b",\n  "confidence": 0.8\n}\n```\nHope this helps!'
    assert parse(text) == [Thought("t"), Action({"new_locator": "#b", "confidence": 0.8})]


def test_braces_and_escaped_quotes_inside_json_strings():
    text = 'THOUGHT: t\nACTION: {"basis": "text has } and \\" quote {", "confidence": 0.7} trailing }'
    assert parse(text) == [Thought("t"), Action({"basis": 'text has } and " quote {', "confidence": 0.7})]


def test_action_on_the_same_line_as_the_thought():
    assert parse('THOUGHT: renamed button. ACTION: {"a": 1}') == [Thought("renamed button."), Action({"a": 1})]


def test_lowercase_action_word_inside_a_thought_is_not_a_marker():
    text = 'THOUGHT: the next action: click the bag button\nACTION: {"a": 1}'
    assert parse(text) == [Thought("the next action: click the bag button"), Action({"a": 1})]


def test_preamble_before_the_first_marker_is_ignored():
    assert parse('Sure! Here is my answer.\nTHOUGHT: t\nACTION: {"a": 1}') == [Thought("t"), Action({"a": 1})]


def test_missing_action_flushes_the_last_thought_and_reports():
    assert parse("THOUGHT: I am not sure") == [Thought("I am not sure"), ParseError("", "missing ACTION")]


def test_invalid_json_is_reported():
    [thought, error] = parse("THOUGHT: t\nACTION: {new_locator: '#x'}")
    assert thought == Thought("t")
    assert isinstance(error, ParseError)
    assert error.reason.startswith("invalid ACTION JSON")
    assert error.raw == "{new_locator: '#x'}"


def test_incomplete_json_at_finish_is_reported():
    assert parse('THOUGHT: t\nACTION: {"a": 1') == [Thought("t"), ParseError(' {"a": 1', "incomplete ACTION JSON")]


def test_input_after_the_action_is_ignored():
    parser = ThoughtActionParser()
    parser.feed('THOUGHT: t\nACTION: {"a": 1}')
    assert parser.feed("\nTHOUGHT: late") == []
    assert parser.finish() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/llm/test_trace_parser.py -q`
Expected: `ModuleNotFoundError: No module named 'llm'`

- [ ] **Step 3: Write `llm/trace_parser.py`**

```python
"""Incremental parser for the THOUGHT:/ACTION: reasoning-trace format (spec §9, roadmap contract C2)."""
import json
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


@dataclass(frozen=True)
class Thought:
    text: str


@dataclass(frozen=True)
class Action:
    data: dict[str, Any]


@dataclass(frozen=True)
class ParseError:
    raw: str
    reason: str


Event = Thought | Action | ParseError

_MD = r"[\s*_`#>\-]*"  # markdown decoration tolerated around markers
_THOUGHT_PREFIX = re.compile(rf"^{_MD}thought[*_`\s]*:[*_`\s]*", re.IGNORECASE)
_ACTION_ANYWHERE = re.compile(r"\bACTION[*_`\s]*:[*_`]*")  # uppercase marker, e.g. mid-line after a thought
_ACTION_LINE_START = re.compile(rf"^{_MD}action[*_`\s]*:[*_`]*", re.IGNORECASE | re.MULTILINE)


class _State(Enum):
    PREAMBLE = auto()
    THOUGHT = auto()
    ACTION = auto()
    DONE = auto()


def _balanced_object_end(text: str, start: int) -> int | None:
    """Index of the brace closing the object that opens at `start`, or None if it is not closed yet."""
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


class ThoughtActionParser:
    """Feed streamed text chunks; get Thought events per completed line and one Action (or ParseError)."""

    def __init__(self) -> None:
        self._state = _State.PREAMBLE
        self._line_buf = ""
        self._action_buf = ""

    @property
    def done(self) -> bool:
        return self._state is _State.DONE

    def feed(self, chunk: str) -> list[Event]:
        if self._state is _State.DONE:
            return []
        if self._state is _State.ACTION:
            self._action_buf += chunk
            return self._try_action()

        self._line_buf += chunk
        marker = self._find_action_marker(self._line_buf)
        if marker:
            events: list[Event] = []
            for line in self._line_buf[: marker.start()].splitlines():
                events += self._line(line)
            self._action_buf = self._line_buf[marker.end():]
            self._line_buf = ""
            self._state = _State.ACTION
            return events + self._try_action()

        *complete, self._line_buf = self._line_buf.split("\n")
        return [event for line in complete for event in self._line(line)]

    def finish(self) -> list[Event]:
        events: list[Event] = []
        if self._state in (_State.PREAMBLE, _State.THOUGHT):
            events += self._line(self._line_buf)
            events.append(ParseError("", "missing ACTION"))
        elif self._state is _State.ACTION:
            events.append(ParseError(self._action_buf, "incomplete ACTION JSON"))
        self._line_buf = self._action_buf = ""
        self._state = _State.DONE
        return events

    @staticmethod
    def _find_action_marker(text: str) -> re.Match[str] | None:
        matches = [m for m in (_ACTION_ANYWHERE.search(text), _ACTION_LINE_START.search(text)) if m]
        return min(matches, key=lambda m: m.start()) if matches else None

    def _line(self, line: str) -> list[Event]:
        text = line.strip()
        if not text:
            return []
        prefix = _THOUGHT_PREFIX.match(text)
        if prefix:
            self._state = _State.THOUGHT
            text = text[prefix.end():].strip()
            return [Thought(text)] if text else []
        return [Thought(text)] if self._state is _State.THOUGHT else []

    def _try_action(self) -> list[Event]:
        start = self._action_buf.find("{")
        if start == -1:
            return []
        end = _balanced_object_end(self._action_buf, start)
        if end is None:
            return []
        raw = self._action_buf[start : end + 1]
        self._state = _State.DONE
        try:
            return [Action(json.loads(raw))]
        except json.JSONDecodeError as exc:
            return [ParseError(raw, f"invalid ACTION JSON: {exc.msg}")]


def parse(text: str) -> list[Event]:
    """Parse a complete (non-streamed) response."""
    parser = ThoughtActionParser()
    return parser.feed(text) + parser.finish()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/llm/test_trace_parser.py -q`
Expected: `15 passed`

---

### Task 2: `OllamaBackend` (C1)

**Files:** Create `llm/ollama_client.py`, `tests/llm/test_ollama_client.py`.

**Interfaces:**
- Consumes: `Settings.ollama_url`, `Settings.llm_backend`, `Settings.analyzer_model` (Phase 2).
- Produces: `LLMError(RuntimeError)`; `LLMBackend` Protocol (C1); `OllamaBackend(base_url, default_model, *, timeout=300.0, transport=None)` with `generate`, `stream`, `aclose`, and async context manager support; `get_backend(settings=None) -> LLMBackend` (raises `ValueError` for an unknown `LLM_BACKEND`).

- [ ] **Step 1: Write the failing test `tests/llm/test_ollama_client.py`**

```python
import json

import httpx
import pytest

from llm.ollama_client import LLMError, OllamaBackend, get_backend
from settings import Settings


def ndjson(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def backend(handler) -> OllamaBackend:
    return OllamaBackend("http://ollama.test", "llama3.1:8b", transport=httpx.MockTransport(handler))


async def test_generate_sends_a_non_streaming_request_and_returns_the_text():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "llama3.1:8b", "response": "OK", "done": True})

    async with backend(handler) as llm:
        assert await llm.generate("Say OK", system="be terse", format={"type": "object"}, options={"temperature": 0}) == "OK"

    assert seen["url"] == "http://ollama.test/api/generate"
    assert seen["body"] == {"model": "llama3.1:8b", "prompt": "Say OK", "stream": False, "system": "be terse",
                            "format": {"type": "object"}, "options": {"temperature": 0}}


async def test_generate_omits_unset_fields_and_honours_the_model_override():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "hi", "done": True})

    async with backend(handler) as llm:
        await llm.generate("p", model="qwen2.5-coder:7b")
    assert bodies == [{"model": "qwen2.5-coder:7b", "prompt": "p", "stream": False}]


async def test_stream_yields_response_chunks_until_done():
    async def body():
        # NDJSON split mid-line across network reads, with a blank keep-alive line.
        yield b'{"response": "THOU", "done": false}\n{"respon'
        yield b'se": "GHT: x", "done": false}\n\n'
        yield b'{"response": "", "done": true, "eval_count": 3}\n'
        yield b'{"response": "ignored after done", "done": false}\n'

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=body())

    async with backend(handler) as llm:
        assert [c async for c in llm.stream("p")] == ["THOU", "GHT: x"]


async def test_error_chunk_mid_stream_raises():
    def handler(request):
        return httpx.Response(200, content=ndjson({"response": "a", "done": False}, {"error": "out of memory"}))

    async with backend(handler) as llm:
        with pytest.raises(LLMError, match="out of memory"):
            [c async for c in llm.stream("p")]


@pytest.mark.parametrize("streaming", [False, True])
async def test_missing_model_gives_a_pull_hint(streaming):
    def handler(request):
        return httpx.Response(404, json={"error": "model 'nope:1b' not found"})

    async with backend(handler) as llm:
        with pytest.raises(LLMError, match=r"nope:1b.*ollama pull nope:1b"):
            if streaming:
                [c async for c in llm.stream("p", model="nope:1b")]
            else:
                await llm.generate("p", model="nope:1b")


@pytest.mark.parametrize("streaming", [False, True])
async def test_unreachable_server_is_reported(streaming):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with backend(handler) as llm:
        with pytest.raises(LLMError, match="not reachable at http://ollama.test"):
            if streaming:
                [c async for c in llm.stream("p")]
            else:
                await llm.generate("p")


async def test_timeout_is_reported():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with backend(handler) as llm:
        with pytest.raises(LLMError, match="timed out"):
            await llm.generate("p")


async def test_get_backend_builds_ollama_from_settings():
    llm = get_backend(Settings(_env_file=None, ollama_url="http://ollama.test", analyzer_model="llama3.2:3b"))
    assert isinstance(llm, OllamaBackend)
    assert llm.default_model == "llama3.2:3b"
    assert llm.base_url == "http://ollama.test"
    await llm.aclose()


def test_get_backend_rejects_unknown_backends():
    with pytest.raises(ValueError, match="groq"):
        get_backend(Settings(_env_file=None, llm_backend="groq"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/llm/test_ollama_client.py -q`
Expected: `ModuleNotFoundError: No module named 'llm.ollama_client'`

- [ ] **Step 3: Write `llm/ollama_client.py`**

```python
"""LLM backend interface (roadmap contract C1) and its Ollama implementation."""
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx

from settings import Settings, get_settings


class LLMError(RuntimeError):
    """Any failure talking to the model backend, with an actionable message."""


class LLMBackend(Protocol):
    async def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                       format: dict | str | None = None, options: dict | None = None) -> str: ...

    def stream(self, prompt: str, *, system: str | None = None, model: str | None = None,
               options: dict | None = None) -> AsyncIterator[str]: ...


class OllamaBackend:
    """Talks to a local Ollama server via POST /api/generate."""

    def __init__(self, base_url: str, default_model: str, *, timeout: float = 300.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        # CPU-only inference can take minutes for a long answer, so only the connect step is short.
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0),
                                         transport=transport)

    async def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                       format: dict | str | None = None, options: dict | None = None) -> str:
        body = self._body(prompt, system=system, model=model, format=format, options=options, stream=False)
        async with self._request(body) as response:
            data = json.loads(await response.aread())
        if "error" in data:
            raise LLMError(f"Ollama error: {data['error']}")
        return data["response"]

    async def stream(self, prompt: str, *, system: str | None = None, model: str | None = None,
                     options: dict | None = None) -> AsyncIterator[str]:
        body = self._body(prompt, system=system, model=model, format=None, options=options, stream=True)
        async with self._request(body) as response:
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise LLMError(f"Ollama error mid-stream: {chunk['error']}")
                if chunk.get("response"):
                    yield chunk["response"]
                if chunk.get("done"):
                    return

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaBackend":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _body(self, prompt: str, *, system: str | None, model: str | None, format: dict | str | None,
              options: dict | None, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model or self.default_model, "prompt": prompt, "stream": stream}
        for key, value in (("system", system), ("format", format), ("options", options)):
            if value is not None:
                body[key] = value
        return body

    @asynccontextmanager
    async def _request(self, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        try:
            async with self._client.stream("POST", "/api/generate", json=body) as response:
                if response.status_code != 200:
                    raise LLMError(self._describe_failure(response.status_code, await response.aread(), body["model"]))
                yield response
        except httpx.ConnectError as exc:
            raise LLMError(f"Ollama not reachable at {self.base_url} ({exc}); is the Ollama app or `ollama serve` running?") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama request to {body['model']} timed out ({type(exc).__name__})") from exc

    @staticmethod
    def _describe_failure(status: int, raw: bytes, model: str) -> str:
        try:
            detail = json.loads(raw).get("error", raw.decode(errors="replace"))
        except ValueError:
            detail = raw.decode(errors="replace")
        if status == 404:
            return f"Ollama model {model} not found ({detail}); run `ollama pull {model}`"
        return f"Ollama returned HTTP {status}: {detail}"


def get_backend(settings: Settings | None = None) -> LLMBackend:
    settings = settings or get_settings()
    if settings.llm_backend == "ollama":
        return OllamaBackend(settings.ollama_url, settings.analyzer_model)
    raise ValueError(f"Unsupported LLM_BACKEND {settings.llm_backend!r}; supported: 'ollama'")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/llm -q`
Expected: `26 passed` (15 parser + 11 client)

---

### Task 3: Live smoke script

**Files:** Create `scripts/ollama_smoke.py`.

**Interfaces — Consumes:** `OllamaBackend`, `ThoughtActionParser`, `get_settings`.

- [ ] **Step 1: Write `scripts/ollama_smoke.py`**

```python
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
```


- [ ] **Step 2: Run it live**

Run: `.\.venv\Scripts\python -m scripts.ollama_smoke`
Expected: for each of the 3 models, a `generate: 'OK'` line, one or more `Thought(...)` lines, then `Action(data={'new_locator': '#add-to-bag-btn', ...})`, plus latency figures. Record the figures in the Phase 3 execution notes; they feed Phase 3b.

- [ ] **Step 3: Full suite**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: `50 passed` (24 from Phase 2 + 26)

## Phase 3 Exit Checklist

- [ ] `pytest -q` green (50 tests), with no network access needed for `tests/llm`.
- [ ] The smoke script streams a parsed THOUGHT/ACTION from every local model, and the latency figures are recorded below.

---

## Execution Notes (2026-09-27)

`pytest -q`: 50 passed. Code was extracted verbatim from this plan's code blocks. The only change: the unused `Thought/Action/ParseError` imports were removed from the smoke script.

Live smoke run (CPU only: Intel Iris Xe, 20 GB RAM, temperature 0, single healer-style prompt with 3 candidates):

| Model | generate (incl. load) | first token | total stream | ~chunks/s | Proposed locator | Confidence |
|---|---|---|---|---|---|---|
| llama3.2:3b | 6.9 s | 2.7 s | 11.5 s | 10.2 | `#add-to-bag-btn` ✔ | 0.8 |
| llama3.1:8b | 14.7 s | 6.0 s | 29.0 s | 4.1 | `#add-to-bag-btn` ✔ | 1.0 |
| qwen2.5-coder:7b | 23.5 s | 8.2 s | 31.0 s | 4.3 | `button[data-product-id='p-100']` ⚠ | 0.8 |

Findings for Phase 3b:
- All three followed the THOUGHT/ACTION format and parsed first time.
- llama3.1:8b reported confidence 1.0, an early sign that raw LLM confidence is overconfident (supports decision G10 and the calibration metric).
- qwen2.5-coder picked a selector that works but is **product-specific**. It would resolve for Wireless Headphones only, while the selector-map key `catalog.addToCartButton` applies to every card. The healer prompt must ask for a selector that is generic across repeated elements in scope, and verification should test it against more than one card.
- llama3.2:3b is about 2.5× faster than the 8B models and was correct here. It's a real candidate for the healer if the 20-trial spike confirms accuracy.
