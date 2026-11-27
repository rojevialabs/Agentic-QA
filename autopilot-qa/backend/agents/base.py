"""Shared agent interface (roadmap contract C6)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from pymongo.asynchronous.database import AsyncDatabase

from db.models import AgentName, AgentTrace, Message
from db.repository import insert
from llm.ollama_client import LLMBackend
from settings import Settings


@dataclass(frozen=True)
class Task:
    task_id: str
    kind: str
    payload: dict[str, Any]
    attempt: int = 1  # 1 = first try; the orchestrator retries a failed task up to twice


class Publisher(Protocol):
    async def publish(self, msg: Message) -> None: ...


@dataclass
class RunContext:
    """Everything an agent may touch during one run."""

    run_id: str
    db: AsyncDatabase
    llm: LLMBackend
    settings: Settings
    bus: Publisher | None = None

    async def emit_thought(self, agent: AgentName, text: str, *, task_id: str | None = None,
                           action: dict[str, Any] | None = None) -> None:
        """Record a reasoning-trace line in agent_traces and, when a bus is attached, stream it to the dashboard."""
        action = action or {}
        await insert(self.db, AgentTrace(run_id=self.run_id, agent=agent, task_id=task_id, thought=text, action=action))
        if self.bus is not None:
            await self.bus.publish(Message(run_id=self.run_id, from_agent=agent, to_agent="dashboard",
                                           task_id=task_id or "-", type="thought",
                                           payload={"text": text, "action": action}))


class Agent(ABC):
    name: ClassVar[AgentName]

    @abstractmethod
    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        """Do the task and return the result payload. Raising means an `error` message and a retry."""
