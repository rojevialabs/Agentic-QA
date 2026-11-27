"""Access to versioned selector maps (roadmap contract C4)."""
import copy
import json
from pathlib import Path

from pymongo.asynchronous.database import AsyncDatabase

from db.models import SelectorMap


async def latest_active(db: AsyncDatabase) -> SelectorMap:
    raw = await db[SelectorMap.collection].find_one({"status": "active"}, sort=[("version", -1)])
    if raw is None:
        raise LookupError("no active selector map; run init_db first")
    return SelectorMap.from_mongo(raw)


def write_selector_map_file(selector_map: SelectorMap, directory: Path) -> Path:
    """Write the map in the seed-file shape read by tests-generated/support/fixtures.js."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"selector_map.v{selector_map.version}.json"
    path.write_text(json.dumps({"version": selector_map.version, "services": selector_map.map}, indent=2),
                    encoding="utf-8")
    return path


async def by_version(db: AsyncDatabase, version: int) -> SelectorMap:
    raw = await db[SelectorMap.collection].find_one({"version": version, "status": "active"})
    if raw is None:
        raise LookupError(f"no active selector map v{version}")
    return SelectorMap.from_mongo(raw)


def derive(parent: SelectorMap, service: str, element: str, selector: str, *, created_by: str = "healer",
           status: str = "active", run_id: str | None = None, version: int | None = None) -> SelectorMap:
    """A new map version identical to `parent` except for one element's selector (recorded in `healed_from`)."""
    services = copy.deepcopy(parent.map)
    entry = services[service]["elements"][element]
    entry["healed_from"], entry["selector"] = entry["selector"], selector
    return SelectorMap(version=version or parent.version + 1, parent_version=parent.version, map=services,
                       created_by=created_by, status=status, run_id=run_id)
