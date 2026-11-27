"""Live feed for the dashboard (spec §8 WS /ws/runs/{run_id}): replay a run's bus messages, then tail new ones."""
from typing import Any, Protocol

from orchestrator.message_bus import MessageBus


class JsonSocket(Protocol):
    async def accept(self) -> None: ...

    async def send_json(self, data: Any) -> None: ...


async def stream_run(socket: JsonSocket, bus: MessageBus, run_id: str) -> None:
    """Send every message of the run (history first) until the client disconnects."""
    await socket.accept()
    messages = bus.subscribe(run_id, replay=True)
    try:
        async for message in messages:
            try:
                await socket.send_json(message.model_dump(mode="json"))
            except Exception:  # the client went away (WebSocketDisconnect, closed transport, ...)
                return
    finally:
        await messages.aclose()
