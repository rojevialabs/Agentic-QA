"""Demo controls for the dashboard (test/demo only): seeded ShopLite changes and heal resets, driven from the UI."""
import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db.models import SelectorMap
from settings import BACKEND_DIR, Settings

REGISTRY_PATH = BACKEND_DIR.parents[1] / "evaluation" / "seeded-bugs" / "registry.json"


class SeededChangesIn(BaseModel):
    seeded_changes: list[str]


def _healed(selector_map: dict[str, Any]) -> list[str]:
    return [f"{service}.{key}: {element['healed_from']} -> {element['selector']}"
            for service, spec in selector_map.get("map", {}).items()
            for key, element in spec.get("elements", {}).items() if element.get("healed_from")]


def demo_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/demo", tags=["demo"])
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8")) if REGISTRY_PATH.exists() else {"changes": []}
    changes = [{k: c.get(k) for k in ("id", "label", "description")} for c in registry["changes"]]
    known = {c["id"] for c in changes}

    def client(request: Request) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=request.app.state.service_transport, timeout=5.0)

    @router.get("/state")
    async def state(request: Request) -> dict[str, Any]:
        async with client(request) as http:
            try:
                live = (await http.get(f"{settings.catalog_service_url}/api/__config")).json()["seeded_changes"]
            except httpx.HTTPError:
                live = None  # ShopLite not running
        maps = request.app.state.db[SelectorMap.collection].find(
            {"status": {"$ne": "archived"}}, sort=[("version", 1), ("created_at", 1)])
        return {
            "seeded_changes": live,
            "threshold": request.app.state.settings.healing_confidence_threshold,
            "changes": changes,
            "selector_maps": [{"version": m["version"], "status": m["status"], "created_by": m["created_by"],
                               "healed": _healed(m)} async for m in maps],
        }

    @router.post("/seeded-changes")
    async def set_seeded_changes(body: SeededChangesIn, request: Request) -> dict[str, Any]:
        unknown = [c for c in body.seeded_changes if c not in known]
        if unknown:
            raise HTTPException(422, f"unknown seeded change(s): {', '.join(unknown)}")
        async with client(request) as http:
            for service, url in settings.service_urls().items():
                try:
                    response = await http.post(f"{url}/api/__config", json={"seeded_changes": body.seeded_changes})
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(502, f"{service} service not reachable at {url}: {exc}") from exc
        return {"seeded_changes": body.seeded_changes}

    @router.post("/reset-healing")
    async def reset_healing(request: Request) -> dict[str, int]:
        result = await request.app.state.db[SelectorMap.collection].update_many(
            {"created_by": "healer", "status": {"$in": ["active", "pending_review"]}}, {"$set": {"status": "archived"}})
        return {"archived": result.modified_count}

    return router
