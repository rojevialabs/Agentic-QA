"""AutoPilot-QA API (spec §8). Run with: uvicorn main:app --reload"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from demo import demo_router
from db.models import BugReport, Coverage, Execution, Requirement, Script, TestCase
from db.mongo_client import get_db, init_db
from db.repository import find, insert
from llm.ollama_client import get_backend
from orchestrator.coordinator import Coordinator, default_agents
from orchestrator.message_bus import MessageBus
from settings import Settings, get_settings
from websocket.live_feed import stream_run


class ExecuteIn(BaseModel):
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)  # one-off HEALING_CONFIDENCE_THRESHOLD


class RequirementIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    max_cases: int | None = Field(default=None, ge=2, le=6)  # quick demo: fewer cases = a faster run on a CPU

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement text must not be blank")
        return value.strip()


def create_app(*, settings: Settings | None = None, db=None, llm=None, agents=None, service_transport=None) -> FastAPI:
    settings = settings or get_settings()
    injected = db is not None

    def wire(app: FastAPI, db, llm, agents) -> None:
        app.state.db, app.state.llm = db, llm
        app.state.bus = MessageBus(db)
        app.state.coordinator = Coordinator(db=db, llm=llm, settings=settings, bus=app.state.bus, agents=agents)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not injected:
            database = get_db(settings)
            await init_db(database)
            wire(app, database, get_backend(settings), agents or default_agents())
        yield
        for task in app.state.runs.values():
            task.cancel()
        if not injected:
            await app.state.llm.aclose()
            await app.state.db.client.close()

    app = FastAPI(title="AutoPilot-QA", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.dashboard_origin], allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.runs, app.state.service_transport = settings, {}, service_transport
    app.include_router(demo_router(settings))
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=settings.artifacts_dir), name="artifacts")
    if injected:
        wire(app, db, llm, agents or default_agents())

    def start(request: Request, run_id: str, coro) -> None:
        request.app.state.runs[run_id] = asyncio.create_task(coro)

    def active(request: Request, run_id: str) -> bool:
        task = request.app.state.runs.get(run_id)
        return task is not None and not task.done()

    async def requirement_for(request: Request, run_id: str) -> Requirement:
        found = await find(request.app.state.db, Requirement, {"run_id": run_id})
        if not found:
            raise HTTPException(404, f"run {run_id} not found")
        return found[0]

    async def listing(request: Request, run_id: str, model, sort: str) -> list[dict[str, Any]]:
        await requirement_for(request, run_id)
        docs = await find(request.app.state.db, model, {"run_id": run_id}, sort=[(sort, 1)])
        return [d.model_dump(mode="json") for d in docs]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/requirements", status_code=202)
    async def submit_requirement(body: RequirementIn, request: Request) -> dict[str, str]:
        requirement = Requirement(text=body.text, max_cases=body.max_cases)
        requirement_id = await insert(request.app.state.db, requirement)
        start(request, requirement.run_id, request.app.state.coordinator.run(requirement_id))
        return {"run_id": requirement.run_id, "requirement_id": requirement_id, "status": requirement.status}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> dict[str, Any]:
        await requirement_for(request, run_id)
        return await request.app.state.coordinator.build_report(run_id) | {"active": active(request, run_id)}

    @app.get("/runs/{run_id}/test-cases")
    async def get_test_cases(run_id: str, request: Request):
        return await listing(request, run_id, TestCase, "generated_at")

    @app.get("/runs/{run_id}/scripts")
    async def get_scripts(run_id: str, request: Request):
        return await listing(request, run_id, Script, "created_at")

    def artifact_url(path: str | None) -> str | None:
        """Public URL of a file under the artifacts directory (served by the /artifacts mount)."""
        if not path:
            return None
        try:
            return "/artifacts/" + Path(path).resolve().relative_to(settings.artifacts_dir.resolve()).as_posix()
        except ValueError:
            return None

    @app.get("/runs/{run_id}/results")
    async def get_results(run_id: str, request: Request):
        results = await listing(request, run_id, Execution, "executed_at")
        return [r | {"screenshot_url": artifact_url(r["screenshot_path"])} for r in results]

    @app.get("/runs/{run_id}/bug-reports")
    async def get_bug_reports(run_id: str, request: Request):
        return await listing(request, run_id, BugReport, "created_at")

    @app.get("/runs/{run_id}/coverage")
    async def get_coverage(run_id: str, request: Request):
        return await listing(request, run_id, Coverage, "run_id")

    @app.get("/runs/{run_id}/messages")
    async def get_messages(run_id: str, request: Request):
        await requirement_for(request, run_id)
        return [m.model_dump(mode="json") for m in await request.app.state.bus.history(run_id)]

    @app.post("/runs/{run_id}/execute", status_code=202)
    async def execute_run(run_id: str, request: Request, body: ExecuteIn | None = None) -> dict[str, str]:
        await requirement_for(request, run_id)
        if active(request, run_id):
            raise HTTPException(409, f"run {run_id} is still in progress")
        coordinator = request.app.state.coordinator
        if body is not None and body.threshold is not None:
            # A one-off threshold for this re-execution only (demo: show a below-threshold proposal without a restart).
            state = request.app.state
            coordinator = Coordinator(db=state.db, llm=state.llm, bus=state.bus, agents=coordinator.agents,
                                      settings=settings.model_copy(update={"healing_confidence_threshold": body.threshold}))
        start(request, run_id, coordinator.execute_existing(run_id))
        return {"run_id": run_id, "status": "running"}

    @app.websocket("/ws/runs/{run_id}")
    async def live_feed(websocket: WebSocket, run_id: str) -> None:
        await stream_run(websocket, websocket.app.state.bus, run_id)

    return app


app = create_app()
