"""Pydantic models for every MongoDB collection (spec §5, §7, plus selector_maps)."""
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, Literal, Self
from uuid import uuid4

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def utcnow() -> datetime:
    """UTC now truncated to milliseconds, the precision BSON stores, so documents round-trip exactly."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def new_id() -> str:
    return str(uuid4())


# ObjectIds are stored as ObjectId in Mongo and handled as str in Python.
ObjectIdStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, ObjectId) else v)]

RunStatus = Literal["pending", "running", "completed", "escalated", "failed"]
TestType = Literal["positive", "negative", "boundary"]
ExecStatus = Literal["pass", "fail", "flaky"]
RootCause = Literal["ui", "api", "data", "environment"]
Severity = Literal["low", "medium", "high"]
MessageType = Literal["request", "result", "error", "thought"]
AgentName = Literal["orchestrator", "requirement_analyzer", "test_generator", "test_executor", "triage_healer", "dashboard"]


class Document(BaseModel):
    """Base for persisted artifacts. `id` maps to Mongo `_id`; `object_id_fields` are stored as ObjectId."""

    model_config = ConfigDict(populate_by_name=True)

    collection: ClassVar[str]
    object_id_fields: ClassVar[tuple[str, ...]] = ()

    id: ObjectIdStr | None = Field(default=None, alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        doc = self.model_dump(by_alias=True)
        if doc["_id"] is None:
            del doc["_id"]
        else:
            doc["_id"] = ObjectId(doc["_id"])
        for name in self.object_id_fields:
            if doc.get(name) is not None:
                doc[name] = ObjectId(doc[name])
        return doc

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> Self:
        return cls.model_validate(doc)


class Requirement(Document):
    collection = "requirements"
    text: str
    run_id: str = Field(default_factory=new_id)
    status: RunStatus = "pending"
    error: str | None = None
    max_cases: int | None = Field(default=None, ge=2, le=6)  # quick-demo mode: analyse into at most this many cases
    submitted_at: datetime = Field(default_factory=utcnow)


class TestCase(Document):
    __test__ = False  # not a pytest test class
    collection = "test_cases"
    object_id_fields = ("requirement_id",)
    run_id: str
    requirement_id: ObjectIdStr
    type: TestType
    title: str
    steps: list[str]
    expected_result: str
    generated_at: datetime = Field(default_factory=utcnow)


class Script(Document):
    collection = "scripts"
    object_id_fields = ("test_case_id",)
    run_id: str
    test_case_id: ObjectIdStr
    language: Literal["playwright-js"] = "playwright-js"
    code: str
    path: str | None = None
    selector_map_version: int = 1
    created_at: datetime = Field(default_factory=utcnow)


class Execution(Document):
    collection = "executions"
    object_id_fields = ("script_id",)
    run_id: str
    script_id: ObjectIdStr
    status: ExecStatus
    duration_ms: int = 0
    screenshot_path: str | None = None
    logs: list[str] = Field(default_factory=list)
    healing: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None  # kind (script|environment), error, last_selector_key, dom_path, screenshot_path, url
    selector_map_version: int = 1
    attempts: int = 1
    executed_at: datetime = Field(default_factory=utcnow)


class BugReport(Document):
    collection = "bug_reports"
    object_id_fields = ("execution_id",)
    run_id: str
    execution_id: ObjectIdStr
    steps_to_reproduce: list[str]
    expected: str
    actual: str
    root_cause: RootCause
    severity: Severity
    notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentTrace(Document):
    collection = "agent_traces"
    run_id: str
    agent: AgentName
    task_id: str | None = None
    thought: str
    action: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class Coverage(Document):
    collection = "coverage"
    object_id_fields = ("requirement_id",)
    run_id: str
    requirement_id: ObjectIdStr
    has_test: bool
    last_status: ExecStatus | Literal["not_run"] = "not_run"


class SelectorMap(Document):
    collection = "selector_maps"
    version: int
    parent_version: int | None = None
    map: dict[str, Any]  # {service: {base_url_env, pages, elements: {key: {selector, scope?, description}}}}
    created_by: Literal["seed", "healer"]
    # active: in use; pending_review: below-threshold proposal; rolled_back: a heal whose re-run still failed;
    # archived: retired by the evaluation runner between trials.
    status: Literal["active", "pending_review", "rolled_back", "archived"] = "active"
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


DOCUMENT_MODELS: tuple[type[Document], ...] = (
    Requirement, TestCase, Script, Execution, BugReport, AgentTrace, Coverage, SelectorMap,
)


class Message(BaseModel):
    """One entry on the shared message bus (spec §5)."""

    collection: ClassVar[str] = "messages"
    message_id: str = Field(default_factory=new_id)
    run_id: str
    from_agent: AgentName
    to_agent: AgentName
    task_id: str
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)
