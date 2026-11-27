# Phase 2 — MongoDB & Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Typed persistence for every AutoPilot-QA artifact (spec §7 + `selector_maps`), the `messages` capped collection (spec §5), backend settings, and an idempotent `init_db()`.

**Architecture:** `autopilot-qa/backend/` is a flat Python package run from its own directory (`uvicorn main:app`, `pytest`). `settings.py` (pydantic-settings) reads `.env`. `db/models.py` defines one Pydantic v2 model per collection on a small `Document` base that maps `_id` and converts ObjectId references. `db/mongo_client.py` owns the client, `init_db()` (collections, capped bus, indexes, selector-map seed), and `db/repository.py` has generic `insert/get/find`. Tests hit a real MongoDB, each in a throwaway database.

**Tech Stack:** Python 3.12, **PyMongo ≥4.13 `AsyncMongoClient`** (replaces Motor, which was deprecated in 2025 and reached end of support in May 2026), Pydantic v2, pydantic-settings, pytest + pytest-asyncio.

## Global Constraints

- Database name: `autopilot_qa` (tests use `autopilot_qa_test_<hex>` and drop it afterwards).
- `messages` is a capped collection (64 MiB). `init_db` refuses to continue if an uncapped `messages` already exists.
- Message schema exactly per spec §5: `message_id, run_id, from_agent, to_agent, task_id, type ∈ {request,result,error,thought}, payload, timestamp`.
- Agent names: `orchestrator, requirement_analyzer, test_generator, test_executor, triage_healer, dashboard`.
- `HEALING_CONFIDENCE_THRESHOLD` defaults to `0.80` and must be within [0, 1].
- ObjectId references (`requirement_id`, `test_case_id`, `script_id`, `execution_id`) are stored as ObjectId and exposed as `str` in Python.
- Datetimes are timezone-aware UTC, truncated to milliseconds (the BSON precision), so documents round-trip exactly.
- **Deviations from spec §7 (additive):** `run_id` on `scripts`, `executions` and `bug_reports` (needed for the `/runs/{run_id}/...` endpoints), `status` on `requirements` (run status for `GET /runs/{run_id}`), `path` on `scripts`, `healing` on `executions`, `notes` on `bug_reports`, and an 8th collection `selector_maps` (confirmed decision G8).
- Windows: use a venv at `autopilot-qa/backend/.venv`. Never use `--break-system-packages`.

## File Map

```
autopilot-qa/backend/
  requirements.txt
  pyproject.toml              pytest config only
  .env.example
  settings.py                 Settings + get_settings()
  config/selector_map.v1.json seed selector map (contract C4)
  db/__init__.py
  db/models.py                Document base + 8 models + Message
  db/mongo_client.py          create_client, get_db, init_db, seed_selector_map
  db/repository.py            insert, get, find
  scripts/__init__.py
  scripts/init_db.py          python -m scripts.init_db
  tests/conftest.py           `db` fixture (throwaway database)
  tests/test_settings.py
  tests/db/test_models.py
  tests/db/test_selector_seed.py
  tests/db/test_init_db.py
  tests/db/test_repository.py
```

---

### Task 1: Backend scaffold and settings

**Files:** Create `requirements.txt`, `pyproject.toml`, `.env.example`, `settings.py`, `tests/test_settings.py` (all under `autopilot-qa/backend/`). Remove `autopilot-qa/backend/.gitkeep`.

**Interfaces — Produces:** `Settings` (fields below), `get_settings() -> Settings` (cached), `Settings.service_urls() -> dict[str, str]` keyed `auth|catalog|cart|order|payment`, and `BACKEND_DIR: Path`.

- [ ] **Step 1: Write `requirements.txt`**

```
fastapi>=0.115
uvicorn[standard]>=0.32
pymongo>=4.13
pydantic>=2.9
pydantic-settings>=2.6
httpx>=0.27
pytest>=8.3
pytest-asyncio>=0.24
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

- [ ] **Step 3: Write `.env.example`**

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB=autopilot_qa
OLLAMA_URL=http://localhost:11434
LLM_BACKEND=ollama
ANALYZER_MODEL=llama3.1:8b
GENERATOR_MODEL=qwen2.5-coder:7b
HEALER_MODEL=llama3.1:8b
HEALING_CONFIDENCE_THRESHOLD=0.80
AUTH_SERVICE_URL=http://localhost:4001
CATALOG_SERVICE_URL=http://localhost:4002
CART_SERVICE_URL=http://localhost:4003
ORDER_SERVICE_URL=http://localhost:4004
PAYMENT_SERVICE_URL=http://localhost:4005
```

- [ ] **Step 4: Create the venv and install**

```powershell
cd C:\BITS\autopilot-qa-project\autopilot-qa\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

- [ ] **Step 5: Write the failing test `tests/test_settings.py`**

```python
import pytest
from pydantic import ValidationError

from settings import Settings


def test_defaults_match_spec(monkeypatch):
    monkeypatch.delenv("HEALING_CONFIDENCE_THRESHOLD", raising=False)
    s = Settings(_env_file=None)
    assert s.mongo_uri == "mongodb://localhost:27017"
    assert s.mongo_db == "autopilot_qa"
    assert s.ollama_url == "http://localhost:11434"
    assert s.healing_confidence_threshold == 0.80
    assert s.service_urls() == {
        "auth": "http://localhost:4001",
        "catalog": "http://localhost:4002",
        "cart": "http://localhost:4003",
        "order": "http://localhost:4004",
        "payment": "http://localhost:4005",
    }


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("HEALING_CONFIDENCE_THRESHOLD", "0.9")
    monkeypatch.setenv("CATALOG_SERVICE_URL", "http://localhost:9002")
    s = Settings(_env_file=None)
    assert s.healing_confidence_threshold == 0.9
    assert s.service_urls()["catalog"] == "http://localhost:9002"


def test_threshold_must_be_a_probability(monkeypatch):
    monkeypatch.setenv("HEALING_CONFIDENCE_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 6: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/test_settings.py -q`
Expected: `ModuleNotFoundError: No module named 'settings'`

- [ ] **Step 7: Write `settings.py`**

```python
"""Backend configuration, read from environment variables and backend/.env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "autopilot_qa"

    ollama_url: str = "http://localhost:11434"
    llm_backend: str = "ollama"
    analyzer_model: str = "llama3.1:8b"
    generator_model: str = "qwen2.5-coder:7b"
    healer_model: str = "llama3.1:8b"
    healing_confidence_threshold: float = Field(0.80, ge=0.0, le=1.0)

    auth_service_url: str = "http://localhost:4001"
    catalog_service_url: str = "http://localhost:4002"
    cart_service_url: str = "http://localhost:4003"
    order_service_url: str = "http://localhost:4004"
    payment_service_url: str = "http://localhost:4005"

    artifacts_dir: Path = BACKEND_DIR.parent / "artifacts"

    def service_urls(self) -> dict[str, str]:
        return {
            "auth": self.auth_service_url,
            "catalog": self.catalog_service_url,
            "cart": self.cart_service_url,
            "order": self.order_service_url,
            "payment": self.payment_service_url,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 8: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/test_settings.py -q`
Expected: `3 passed`

---

### Task 2: Pydantic models

**Files:** Create `db/__init__.py` (empty), `db/models.py`, `tests/db/test_models.py`.

**Interfaces — Produces:**
- `utcnow() -> datetime` (UTC, ms precision); `new_id() -> str` (uuid4).
- `Document(BaseModel)`: `collection: ClassVar[str]`, `object_id_fields: ClassVar[tuple[str, ...]]`, `id: str | None` (alias `_id`), `to_mongo() -> dict`, `from_mongo(doc) -> Self`.
- Models and their collections: `Requirement` (requirements), `TestCase` (test_cases), `Script` (scripts), `Execution` (executions), `BugReport` (bug_reports), `AgentTrace` (agent_traces), `Coverage` (coverage), `SelectorMap` (selector_maps); `DOCUMENT_MODELS` tuple.
- `Message(BaseModel)` with `collection = "messages"`.
- Literal aliases: `RunStatus, TestType, ExecStatus, RootCause, Severity, MessageType, AgentName`.

- [ ] **Step 1: Write the failing test `tests/db/test_models.py`**

```python
import uuid

import pytest
from bson import ObjectId
from pydantic import ValidationError

from db.models import BugReport, Message, Requirement, TestCase


def make_test_case(**overrides):
    fields = dict(run_id="run-1", requirement_id=str(ObjectId()), type="negative", title="expired card",
                  steps=["open payment page", "pay with 01/20"], expected_result="Payment declined: card expired")
    return TestCase(**(fields | overrides))


def test_message_defaults():
    m = Message(run_id="r", from_agent="orchestrator", to_agent="test_generator", task_id="t", type="request")
    uuid.UUID(m.message_id)
    assert m.payload == {}
    assert m.timestamp.tzinfo is not None
    assert m.timestamp.microsecond % 1000 == 0


@pytest.mark.parametrize("field,value", [("type", "shout"), ("from_agent", "intern")])
def test_message_rejects_values_outside_the_protocol(field, value):
    fields = dict(run_id="r", from_agent="orchestrator", to_agent="test_generator", task_id="t", type="thought")
    with pytest.raises(ValidationError):
        Message(**(fields | {field: value}))


def test_requirement_gets_a_run_id_and_pending_status():
    r = Requirement(text="User can log in")
    uuid.UUID(r.run_id)
    assert r.status == "pending"


def test_to_mongo_converts_refs_and_drops_empty_id():
    tc = make_test_case()
    doc = tc.to_mongo()
    assert "_id" not in doc
    assert doc["requirement_id"] == ObjectId(tc.requirement_id)
    assert doc["type"] == "negative"


def test_to_mongo_keeps_an_assigned_id_as_object_id():
    oid = ObjectId()
    assert make_test_case(id=str(oid)).to_mongo()["_id"] == oid


def test_from_mongo_exposes_object_ids_as_strings():
    oid, rid = ObjectId(), ObjectId()
    tc = TestCase.from_mongo(make_test_case().to_mongo() | {"_id": oid, "requirement_id": rid})
    assert tc.id == str(oid)
    assert tc.requirement_id == str(rid)


def test_test_case_type_is_constrained():
    with pytest.raises(ValidationError):
        make_test_case(type="smoke")


def test_bug_report_root_cause_is_constrained():
    with pytest.raises(ValidationError):
        BugReport(run_id="r", execution_id=str(ObjectId()), steps_to_reproduce=[], expected="a", actual="b",
                  root_cause="cosmic-rays", severity="low")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_models.py -q`
Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Write `db/models.py`**

```python
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
    status: Literal["active", "pending_review"] = "active"
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_models.py -q`
Expected: `9 passed`

---

### Task 3: Seed selector map (contract C4) with a drift guard

**Files:** Create `config/selector_map.v1.json`, `tests/db/test_selector_seed.py`.

**Interfaces — Produces:** `config/selector_map.v1.json` = `{"version": 1, "services": {service: {"base_url_env", "pages", "elements": {key: {"selector", "description", "scope"?}}}}}`. Logical keys are `<service>.<element>` (e.g. `catalog.addToCartButton`). A `scope` names another element in the same service.

- [ ] **Step 1: Write the failing test `tests/db/test_selector_seed.py`**

```python
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


def test_seed_covers_every_service():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    assert seed["version"] == 1
    assert set(seed["services"]) == SERVICES
    for name, svc in seed["services"].items():
        assert svc["base_url_env"] == f"{name.upper()}_SERVICE_URL"
        assert svc["elements"], name


def test_every_seed_selector_exists_in_the_v1_markup():
    markup = platform_markup()
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    for service, svc in seed["services"].items():
        for key, element in svc["elements"].items():
            selector = element["selector"]
            assert element["description"], f"{service}.{key} needs a description"
            if selector.startswith("#"):
                assert f'id="{selector[1:]}"' in markup, f"{service}.{key}: {selector} not in markup"
            elif selector.startswith("."):
                assert re.search(rf'class="{re.escape(selector[1:])}"', markup), f"{service}.{key}: {selector} not in markup"
            else:
                raise AssertionError(f"{service}.{key}: only #id and .class selectors are seeded, got {selector}")
            if "scope" in element:
                assert element["scope"] in svc["elements"], f"{service}.{key}: unknown scope {element['scope']}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_selector_seed.py -q`
Expected: FAIL with `FileNotFoundError` for `selector_map.v1.json`

- [ ] **Step 3: Write `config/selector_map.v1.json`**

```json
{
  "version": 1,
  "services": {
    "auth": {
      "base_url_env": "AUTH_SERVICE_URL",
      "pages": { "login": "/login.html", "register": "/register.html" },
      "elements": {
        "emailInput": { "selector": "#email", "description": "email text field on the login and register forms" },
        "passwordInput": { "selector": "#password", "description": "password field on the login and register forms" },
        "loginButton": { "selector": "#login-btn", "description": "button labelled 'Log in' that submits the login form" },
        "registerButton": { "selector": "#register-btn", "description": "button labelled 'Register' that submits the register form" },
        "message": { "selector": "#message", "description": "status text shown after submitting ('Logged in', 'Invalid email or password', 'Account created', ...)" }
      }
    },
    "catalog": {
      "base_url_env": "CATALOG_SERVICE_URL",
      "pages": { "catalog": "/catalog.html" },
      "elements": {
        "searchInput": { "selector": "#search-input", "description": "search text box above the product list" },
        "searchButton": { "selector": "#search-btn", "description": "button labelled 'Search' that submits the search" },
        "resultCount": { "selector": "#result-count", "description": "text '<n> products' giving the number of listed products" },
        "productCard": { "selector": ".product-card", "description": "list item for one product; filter it by product name" },
        "productName": { "selector": ".product-name", "scope": "productCard", "description": "product name heading inside a product card" },
        "productPrice": { "selector": ".product-price", "scope": "productCard", "description": "price text like '$59.99' inside a product card" },
        "productStock": { "selector": ".product-stock", "scope": "productCard", "description": "'In stock' or 'Out of stock' inside a product card" },
        "addToCartButton": { "selector": "#add-to-cart-btn", "scope": "productCard", "description": "button labelled 'Add to Cart' that adds this card's product to the cart; disabled when out of stock" },
        "cartMessage": { "selector": "#cart-message", "description": "status text after adding, e.g. 'Added Wireless Headphones to cart'" },
        "cartCount": { "selector": "#cart-count", "description": "number of items in the cart shown in the nav bar" },
        "noResults": { "selector": "#no-results", "description": "'No products found' shown when a search matches nothing" }
      }
    },
    "cart": {
      "base_url_env": "CART_SERVICE_URL",
      "pages": { "cart": "/cart.html" },
      "elements": {
        "cartItem": { "selector": ".cart-item", "description": "table row for one cart line; filter it by product name" },
        "itemName": { "selector": ".item-name", "scope": "cartItem", "description": "product name in a cart row" },
        "itemQty": { "selector": ".item-qty", "scope": "cartItem", "description": "quantity in a cart row" },
        "itemPrice": { "selector": ".item-price", "scope": "cartItem", "description": "line price like '$59.99' in a cart row" },
        "removeButton": { "selector": ".remove-btn", "scope": "cartItem", "description": "button labelled 'Remove' in a cart row" },
        "cartTotal": { "selector": "#cart-total", "description": "cart total like '$59.99'" },
        "emptyCart": { "selector": "#empty-cart", "description": "'Your cart is empty' message, visible only when the cart is empty" },
        "checkoutButton": { "selector": "#checkout-btn", "description": "button labelled 'Proceed to Checkout'; disabled when the cart is empty" }
      }
    },
    "order": {
      "base_url_env": "ORDER_SERVICE_URL",
      "pages": { "checkout": "/checkout.html" },
      "elements": {
        "shipName": { "selector": "#ship-name", "description": "shipping full name field" },
        "shipAddress": { "selector": "#ship-address", "description": "shipping address field" },
        "shipCity": { "selector": "#ship-city", "description": "shipping city field" },
        "shipZip": { "selector": "#ship-zip", "description": "shipping postcode field" },
        "placeOrderButton": { "selector": "#place-order-btn", "description": "button labelled 'Place Order'; on success navigates to the payment page" },
        "checkoutTotal": { "selector": "#checkout-total", "description": "order total like '$59.99'" },
        "checkoutMessage": { "selector": "#checkout-message", "description": "error text such as 'Your cart is empty' or 'Please fill in all shipping fields'" }
      }
    },
    "payment": {
      "base_url_env": "PAYMENT_SERVICE_URL",
      "pages": { "payment": "/payment.html" },
      "elements": {
        "orderId": { "selector": "#order-id", "description": "order id being paid, e.g. 'ord-1a2b3c4d'" },
        "amount": { "selector": "#amount", "description": "amount due like '$59.99'" },
        "cardNumber": { "selector": "#card-number", "description": "16-digit card number field" },
        "expiry": { "selector": "#expiry", "description": "card expiry field in MM/YY format" },
        "payButton": { "selector": "#pay-btn", "description": "button labelled 'Pay' that submits the payment" },
        "paymentStatus": { "selector": "#payment-status", "description": "'Payment approved' or 'Payment declined: <reason>'" }
      }
    }
  }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_selector_seed.py -q`
Expected: `2 passed`

---

### Task 4: Mongo client, `init_db`, repository, CLI

**Files:** Create `db/mongo_client.py`, `db/repository.py`, `scripts/__init__.py`, `scripts/init_db.py`, `tests/conftest.py`, `tests/db/test_init_db.py`, `tests/db/test_repository.py`.

**Interfaces:**
- Consumes: `Settings/get_settings/BACKEND_DIR` (Task 1), models (Task 2), seed JSON (Task 3).
- Produces: `MESSAGES_CAP_BYTES = 64 * 1024 * 1024`; `create_client(uri) -> AsyncMongoClient` (tz_aware); `get_db(settings=None) -> AsyncDatabase` (process-wide client); `async init_db(db) -> None`; `async seed_selector_map(db) -> None`; `async insert(db, doc) -> str` (also sets `doc.id`); `async get(db, cls, id) -> cls | None`; `async find(db, cls, filter=None, *, sort=None, limit=0) -> list[cls]` (string values for `cls.object_id_fields` in `filter` are converted to ObjectId).

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import uuid

import pytest

from db.mongo_client import create_client
from settings import get_settings


@pytest.fixture
async def db():
    """A fresh, empty database per test on the configured MongoDB; dropped afterwards."""
    uri = get_settings().mongo_uri
    client = create_client(uri)
    try:
        await client.admin.command("ping")
    except Exception as exc:  # fail loudly: these tests are meaningless without a real server
        await client.close()
        pytest.fail(f"MongoDB not reachable at {uri}: {exc}")
    name = f"autopilot_qa_test_{uuid.uuid4().hex[:8]}"
    yield client[name]
    await client.drop_database(name)
    await client.close()
```

- [ ] **Step 2: Write the failing test `tests/db/test_init_db.py`**

```python
import pytest
from pymongo.errors import DuplicateKeyError

from db.models import SelectorMap
from db.mongo_client import MESSAGES_CAP_BYTES, init_db
from db.repository import insert

ALL_COLLECTIONS = {"requirements", "test_cases", "scripts", "executions", "bug_reports",
                   "agent_traces", "coverage", "selector_maps", "messages"}


async def test_creates_every_collection(db):
    await init_db(db)
    assert ALL_COLLECTIONS <= set(await db.list_collection_names())


async def test_messages_is_capped(db):
    await init_db(db)
    options = await db["messages"].options()
    assert options["capped"] is True
    assert options["size"] == MESSAGES_CAP_BYTES


async def test_run_scoped_indexes(db):
    await init_db(db)
    traces = await db["agent_traces"].index_information()
    assert any(ix["key"] == [("run_id", 1), ("timestamp", 1)] for ix in traces.values())
    requirements = await db["requirements"].index_information()
    assert any(ix["key"] == [("run_id", 1)] and ix.get("unique") for ix in requirements.values())


async def test_seeds_selector_map_v1(db):
    await init_db(db)
    seeded = SelectorMap.from_mongo(await db["selector_maps"].find_one({"version": 1}))
    assert seeded.created_by == "seed"
    assert seeded.status == "active"
    assert seeded.map["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-cart-btn"


async def test_is_idempotent(db):
    await init_db(db)
    await init_db(db)
    assert await db["selector_maps"].count_documents({}) == 1


async def test_refuses_an_uncapped_messages_collection(db):
    await db.create_collection("messages")
    with pytest.raises(RuntimeError, match="not capped"):
        await init_db(db)


async def test_active_versions_are_unique_but_proposals_may_share_them(db):
    await init_db(db)
    await insert(db, SelectorMap(version=1, map={}, created_by="healer", status="pending_review"))
    with pytest.raises(DuplicateKeyError):
        await insert(db, SelectorMap(version=1, map={}, created_by="healer"))
```

- [ ] **Step 3: Write the failing test `tests/db/test_repository.py`**

```python
from bson import ObjectId

from db.models import Requirement, TestCase
from db.repository import find, get, insert


def case(run_id: str, requirement_id: str, title: str) -> TestCase:
    return TestCase(run_id=run_id, requirement_id=requirement_id, type="positive", title=title,
                    steps=["search headphones", "add to cart", "checkout", "pay"], expected_result="Payment approved")


async def test_insert_then_get_round_trips_exactly(db):
    requirement = Requirement(text="User can search for a product, add it to cart, and complete checkout.")
    requirement_id = await insert(db, requirement)
    tc = case(requirement.run_id, requirement_id, "happy path")

    tc_id = await insert(db, tc)

    assert tc.id == tc_id
    assert await get(db, TestCase, tc_id) == tc
    raw = await db["test_cases"].find_one({"_id": ObjectId(tc_id)})
    assert isinstance(raw["requirement_id"], ObjectId)


async def test_get_missing_returns_none(db):
    assert await get(db, TestCase, str(ObjectId())) is None


async def test_find_filters_by_run_and_by_string_object_id_ref(db):
    req_a, req_b = str(ObjectId()), str(ObjectId())
    for run_id, req, title in [("run-a", req_a, "one"), ("run-a", req_a, "two"), ("run-b", req_b, "three")]:
        await insert(db, case(run_id, req, title))

    assert [t.title for t in await find(db, TestCase, {"run_id": "run-a"}, sort=[("title", 1)])] == ["one", "two"]
    assert [t.title for t in await find(db, TestCase, {"requirement_id": req_b})] == ["three"]
    assert len(await find(db, TestCase, limit=2)) == 2
```

- [ ] **Step 4: Run to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_init_db.py tests/db/test_repository.py -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'db.mongo_client'`

- [ ] **Step 5: Write `db/mongo_client.py`**

```python
"""MongoDB connection and idempotent schema setup for the autopilot_qa database."""
import json

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from db.models import DOCUMENT_MODELS, Message, SelectorMap
from settings import BACKEND_DIR, Settings, get_settings

MESSAGES_CAP_BYTES = 64 * 1024 * 1024
SEED_SELECTOR_MAP = BACKEND_DIR / "config" / "selector_map.v1.json"

# collection -> [(keys, index options)]
INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    "requirements": [([("run_id", ASCENDING)], {"unique": True})],
    "test_cases": [([("run_id", ASCENDING)], {}), ([("requirement_id", ASCENDING)], {})],
    "scripts": [([("run_id", ASCENDING)], {}), ([("test_case_id", ASCENDING)], {})],
    "executions": [([("run_id", ASCENDING)], {}), ([("script_id", ASCENDING)], {})],
    "bug_reports": [([("run_id", ASCENDING)], {}), ([("execution_id", ASCENDING)], {})],
    "agent_traces": [([("run_id", ASCENDING), ("timestamp", ASCENDING)], {})],
    "coverage": [([("run_id", ASCENDING), ("requirement_id", ASCENDING)], {"unique": True})],
    # Only one *active* map per version; pending_review proposals may reuse a number.
    "selector_maps": [([("version", ASCENDING)], {"unique": True, "partialFilterExpression": {"status": "active"}})],
    "messages": [([("run_id", ASCENDING)], {})],
}

_client: AsyncMongoClient | None = None


def create_client(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)


def get_db(settings: Settings | None = None) -> AsyncDatabase:
    """The process-wide database handle (one client per process)."""
    global _client
    settings = settings or get_settings()
    if _client is None:
        _client = create_client(settings.mongo_uri)
    return _client[settings.mongo_db]


async def init_db(db: AsyncDatabase) -> None:
    """Create collections, the capped message bus, indexes and the v1 selector map. Safe to re-run."""
    existing = set(await db.list_collection_names())

    if Message.collection not in existing:
        await db.create_collection(Message.collection, capped=True, size=MESSAGES_CAP_BYTES)
    elif not (await db[Message.collection].options()).get("capped"):
        raise RuntimeError(f"'{Message.collection}' exists but is not capped; drop it and re-run init_db")

    for model in DOCUMENT_MODELS:
        if model.collection not in existing:
            await db.create_collection(model.collection)

    for name, specs in INDEXES.items():
        for keys, options in specs:
            await db[name].create_index(keys, **options)

    await seed_selector_map(db)


async def seed_selector_map(db: AsyncDatabase) -> None:
    if await db[SelectorMap.collection].find_one({"version": 1, "status": "active"}):
        return
    seed = json.loads(SEED_SELECTOR_MAP.read_text(encoding="utf-8"))
    doc = SelectorMap(version=seed["version"], map=seed["services"], created_by="seed")
    await db[SelectorMap.collection].insert_one(doc.to_mongo())
```

- [ ] **Step 6: Write `db/repository.py`**

```python
"""Generic persistence helpers for Document models."""
from typing import Any, TypeVar

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from db.models import Document

D = TypeVar("D", bound=Document)


async def insert(db: AsyncDatabase, doc: Document) -> str:
    result = await db[doc.collection].insert_one(doc.to_mongo())
    doc.id = str(result.inserted_id)
    return doc.id


async def get(db: AsyncDatabase, cls: type[D], id: str) -> D | None:
    raw = await db[cls.collection].find_one({"_id": ObjectId(id)})
    return cls.from_mongo(raw) if raw else None


async def find(
    db: AsyncDatabase,
    cls: type[D],
    filter: dict[str, Any] | None = None,
    *,
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
) -> list[D]:
    query = dict(filter or {})
    for name in cls.object_id_fields:
        if isinstance(query.get(name), str):
            query[name] = ObjectId(query[name])
    cursor = db[cls.collection].find(query, sort=sort, limit=limit)
    return [cls.from_mongo(raw) async for raw in cursor]
```

- [ ] **Step 7: Run to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/db -q`
Expected: `21 passed` (9 models + 2 seed + 7 init_db + 3 repository)

- [ ] **Step 8: Write `scripts/__init__.py` (empty) and `scripts/init_db.py`**

```python
"""Initialise the autopilot_qa database: python -m scripts.init_db"""
import asyncio

from db.mongo_client import get_db, init_db


async def main() -> None:
    db = get_db()
    await init_db(db)
    names = sorted(await db.list_collection_names())
    print(f"{db.name}: {', '.join(names)}")
    await db.client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `.\.venv\Scripts\python -m scripts.init_db` (twice)
Expected (both times): `autopilot_qa: agent_traces, bug_reports, coverage, executions, messages, requirements, scripts, selector_maps, test_cases`

- [ ] **Step 9: Full suite**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: `24 passed`

## Phase 2 Exit Checklist

- [ ] `pytest -q` green (24 tests) against the real MongoDB.
- [ ] `python -m scripts.init_db` run twice prints the same 9 collections, and `selector_maps` holds exactly one v1 document.
- [ ] Roadmap updated: PyMongo async instead of Motor; the additive schema fields are listed.
