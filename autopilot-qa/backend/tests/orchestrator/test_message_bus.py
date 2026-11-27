import asyncio

import pytest

from db.models import Message
from db.mongo_client import init_db
from orchestrator.message_bus import MessageBus


def msg(run_id="run-1", to_agent="orchestrator", n=0, type_="result"):
    return Message(run_id=run_id, from_agent="test_generator", to_agent=to_agent, task_id=f"t{n}", type=type_,
                   payload={"n": n})


@pytest.fixture
async def bus(db):
    await init_db(db)
    return MessageBus(db)


async def next_message(stream, timeout=5):
    return await asyncio.wait_for(anext(stream), timeout)


async def test_history_returns_a_runs_messages_in_order(bus):
    for n in range(3):
        await bus.publish(msg(n=n))
    await bus.publish(msg(run_id="other", n=9))
    assert [m.payload["n"] for m in await bus.history("run-1")] == [0, 1, 2]


async def test_subscribe_with_replay_yields_the_past_then_the_live_messages(bus):
    await bus.publish(msg(n=0))
    stream = bus.subscribe("run-1", replay=True)
    try:
        assert (await next_message(stream)).payload == {"n": 0}
        await bus.publish(msg(run_id="other", n=5))
        await bus.publish(msg(n=1))
        assert (await next_message(stream)).payload == {"n": 1}
    finally:
        await stream.aclose()


async def test_subscribe_without_replay_only_sees_new_messages_for_the_agent(bus):
    await bus.publish(msg(n=0))
    stream = bus.subscribe("run-1", to_agent="dashboard")
    try:
        pending = asyncio.ensure_future(next_message(stream))
        await asyncio.sleep(0.3)
        await bus.publish(msg(n=1))                        # to orchestrator: filtered out
        await bus.publish(msg(n=2, to_agent="dashboard", type_="thought"))
        received = await pending
        assert (received.payload, received.type) == ({"n": 2}, "thought")
    finally:
        await stream.aclose()


async def test_subscribe_waits_on_an_empty_collection(bus):
    stream = bus.subscribe("run-1", replay=True)
    try:
        pending = asyncio.ensure_future(next_message(stream))
        await asyncio.sleep(0.5)
        await bus.publish(msg(n=7))
        assert (await pending).payload == {"n": 7}
    finally:
        await stream.aclose()
