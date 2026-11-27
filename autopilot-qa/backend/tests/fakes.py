"""Test doubles shared across agent tests."""
from db.models import Message


class FakeLLM:
    """Scripted LLMBackend: returns queued replies and records every call."""

    def __init__(self, replies=(), stream_replies=()):
        self.replies = list(replies)
        self.stream_replies = [list(r) for r in stream_replies]
        self.calls: list[dict] = []

    async def generate(self, prompt, *, system=None, model=None, format=None, options=None):
        self.calls.append({"kind": "generate", "prompt": prompt, "system": system, "model": model,
                           "format": format, "options": options})
        if not self.replies:
            raise AssertionError("FakeLLM: unexpected generate() call")
        return self.replies.pop(0)

    async def stream(self, prompt, *, system=None, model=None, options=None):
        self.calls.append({"kind": "stream", "prompt": prompt, "system": system, "model": model, "options": options})
        if not self.stream_replies:
            raise AssertionError("FakeLLM: unexpected stream() call")
        for chunk in self.stream_replies.pop(0):
            yield chunk


class FakeBus:
    def __init__(self):
        self.messages: list[Message] = []

    async def publish(self, msg: Message) -> None:
        self.messages.append(msg)
