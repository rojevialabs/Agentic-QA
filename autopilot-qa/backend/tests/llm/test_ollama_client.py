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
