from agents.base import RunContext
from db.models import AgentTrace
from db.repository import find
from settings import Settings
from tests.fakes import FakeBus, FakeLLM


async def test_emit_thought_persists_a_trace_without_a_bus(db):
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(), settings=Settings(_env_file=None))
    await ctx.emit_thought("requirement_analyzer", "Derived 3 test cases", task_id="t1")
    [trace] = await find(db, AgentTrace, {"run_id": "run-1"})
    assert (trace.agent, trace.thought, trace.task_id, trace.action) == ("requirement_analyzer", "Derived 3 test cases", "t1", {})


async def test_emit_thought_also_publishes_to_the_dashboard(db):
    bus = FakeBus()
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM(), settings=Settings(_env_file=None), bus=bus)
    await ctx.emit_thought("triage_healer", "The id changed", task_id="t9", action={"confidence": 0.9})
    [msg] = bus.messages
    assert (msg.type, msg.from_agent, msg.to_agent, msg.task_id) == ("thought", "triage_healer", "dashboard", "t9")
    assert msg.payload == {"text": "The id changed", "action": {"confidence": 0.9}}
    assert len(await find(db, AgentTrace, {"run_id": "run-1"})) == 1
