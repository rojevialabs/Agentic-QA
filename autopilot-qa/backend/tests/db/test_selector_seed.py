"""Guards the seed selector map against drift from the ShopLite v1 markup."""
import json
import re

from settings import BACKEND_DIR

SEED = BACKEND_DIR / "config" / "selector_map.v1.json"
PLATFORM = BACKEND_DIR.parents[1] / "target-platform"
SERVICES = {"auth", "catalog", "cart", "order", "payment"}


def platform_markup() -> str:
    files = [*PLATFORM.glob("*-service/views/*.html"), *PLATFORM.glob("*-service/server.js")]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def server_sources() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in PLATFORM.glob("*-service/server.js"))


def test_seed_covers_every_service():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    assert seed["version"] == 1
    assert set(seed["services"]) == SERVICES
    for name, svc in seed["services"].items():
        assert svc["base_url_env"] == f"{name.upper()}_SERVICE_URL"
        assert svc["elements"], name


def test_every_seed_selector_exists_in_the_v1_markup():
    markup, sources = platform_markup(), server_sources()
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    for service, svc in seed["services"].items():
        for key, element in svc["elements"].items():
            selector = element["selector"]
            assert element["description"], f"{service}.{key} needs a description"
            if selector.startswith("#"):
                # Either literal markup, or an id a service renders from a JS string literal (seeded-change switches).
                found = f'id="{selector[1:]}"' in markup or f"'{selector[1:]}'" in sources
                assert found, f"{service}.{key}: {selector} not in markup"
            elif selector.startswith("."):
                assert re.search(rf'class="{re.escape(selector[1:])}"', markup), f"{service}.{key}: {selector} not in markup"
            else:
                raise AssertionError(f"{service}.{key}: only #id and .class selectors are seeded, got {selector}")
            if "scope" in element:
                assert element["scope"] in svc["elements"], f"{service}.{key}: unknown scope {element['scope']}"
