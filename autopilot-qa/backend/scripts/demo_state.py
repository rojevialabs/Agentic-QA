"""Show or reset the healing state of the demo database.

    python -m scripts.demo_state            list selector-map versions (what the healer has written)
    python -m scripts.demo_state --reset    archive every healer-created map, so the next run starts from v1 again
"""
import argparse
import asyncio

from db.mongo_client import get_db, init_db


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true")
    opts = parser.parse_args()
    db = get_db()
    await init_db(db)
    maps = db["selector_maps"]
    if opts.reset:
        result = await maps.update_many({"created_by": "healer", "status": {"$in": ["active", "pending_review"]}},
                                        {"$set": {"status": "archived"}})
        print(f"archived {result.modified_count} healer map(s); the active map is the seed again")
    cursor = maps.find({"status": {"$ne": "archived"}}, sort=[("version", 1), ("created_at", 1)])
    async for m in cursor:
        changed = [f"{svc}.{key}: {el.get('healed_from')} -> {el['selector']}"
                   for svc, spec in m["map"].items() for key, el in spec.get("elements", {}).items() if el.get("healed_from")]
        print(f"v{m['version']:<3} {m['status']:<15} {m['created_by']:<7} {'; '.join(changed) or '(seed selectors)'}")
    await db.client.close()


if __name__ == "__main__":
    asyncio.run(main())
