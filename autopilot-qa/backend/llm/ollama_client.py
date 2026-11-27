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
