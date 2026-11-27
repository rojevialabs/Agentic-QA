import asyncio

import pytest

from db.models import Message
from db.mongo_client import init_db
from orchestrator.message_bus import MessageBus
from websocket.live_feed import stream_run


class FakeSocket:
    def __init__(self, fail_after=None):
        self.accepted, self.sent, self.fail_after = False, [], fail_after

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("client went away")
        self.sent.append(data)


def thought(n):
    return Message(run_id="run-1", from_agent="triage_healer", to_agent="dashboard", task_id="t", type="thought",
                   payload={"text": f"line {n}", "action": {}})


async def wait_for(predicate, timeout=5):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(poll(), timeout)


async def test_replays_history_then_streams_live_messages_as_json(db):
    await init_db(db)
    bus = MessageBus(db)
    await bus.publish(thought(0))
    socket = FakeSocket()
    task = asyncio.create_task(stream_run(socket, bus, "run-1"))
    try:
        await wait_for(lambda: len(socket.sent) == 1)
        await bus.publish(thought(1))
        await wait_for(lambda: len(socket.sent) == 2)
    finally:
        task.cancel()
    assert socket.accepted
    assert [m["payload"]["text"] for m in socket.sent] == ["line 0", "line 1"]
    assert isinstance(socket.sent[0]["timestamp"], str)


async def test_a_disconnect_ends_the_stream_quietly(db):
    await init_db(db)
    bus = MessageBus(db)
    await bus.publish(thought(0))
    await bus.publish(thought(1))
    await asyncio.wait_for(stream_run(FakeSocket(fail_after=1), bus, "run-1"), 5)
