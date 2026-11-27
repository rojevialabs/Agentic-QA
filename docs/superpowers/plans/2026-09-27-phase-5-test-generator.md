# Phase 5 — Test Generator Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a persisted test case into a statically validated Playwright script (contract C5) that reaches every element through a logical selector key. Healing can then later fix the selector map instead of the script (roadmap G7).

**Architecture:**
- `autopilot-qa/tests-generated/` is a small Playwright project. `support/fixtures.js` exports `test`, `expect`, `sel`, `urls`, and on failure writes `failure.json` + `dom.html` + `failure.png`. `examples/remove-only-item.spec.js` is a real, runnable script that doubles as the prompt's few-shot example.
- `db/selector_maps.py` loads the active selector map and writes it to a JSON file for a run.
- `execution/script_validator.py` enforces roadmap G14 (safety plus selector discipline) and runs `node --check`.
- `agents/test_generator.py` renders the prompt (rules, selector key catalogue, test data, example, test case), extracts the code, validates, makes one repair re-prompt, then writes `tests-generated/<run_id>/<test_case_id>.spec.js` and a `scripts` document.

**Tech Stack:** Python 3.12, Node 22, `@playwright/test` ^1.55, Phase 2–4 modules.

## Global Constraints

- Contract C5: `const { test, expect, sel, urls } = require('../support/fixtures');`, exactly one `test(...)`. Elements only via `page.locator(sel('<service>.<element>'), …)`, and scoped elements via `.locator(sel(...))` on their scope. Navigation only via `page.goto(urls.<service> + '<path>')`.
- `APQA_SELECTOR_MAP` points the fixture at a selector-map JSON file (the seed file by default). `APQA_ARTIFACTS_DIR` sets where failure artifacts go.
- Validator rejects: any `require` other than the fixtures, `import`, `process`, `globalThis`, `__dirname`/`__filename`, `eval`/`Function`, `fetch`/`XMLHttpRequest`/`WebSocket`, `page.evaluate*`, `page.$`/`$$`, `page.click/fill/type/check/press/hover(...)` with raw selectors, `getBy*`, `waitForTimeout`, raw `locator('...')` strings, unknown or non-literal selector keys, non-`urls.*` navigation, not exactly one `test(`, no `expect(`, and syntax errors.
- Model: `Settings.generator_model` (default `qwen2.5-coder:7b`). Temperature 0.2, `num_ctx` 8192. One repair attempt, then `GeneratorError`.
- New setting: `tests_generated_dir` (default `autopilot-qa/tests-generated`).

## File Map

```
autopilot-qa/tests-generated/
  package.json  playwright.config.js
  support/fixtures.js  support/fixtures.test.js
  examples/remove-only-item.spec.js
autopilot-qa/backend/
  settings.py                         + tests_generated_dir
  db/selector_maps.py                 latest_active, write_selector_map_file
  execution/__init__.py
  execution/script_validator.py       validate_script, ValidationResult
  agents/test_generator.py            extract_code, build_prompt, generate_script, GeneratorError, TestGenerator
  prompts/test_generator.md
  tests/db/test_selector_maps.py
  tests/execution/test_script_validator.py
  tests/agents/test_test_generator.py
.gitignore                            keep tests-generated/{package.json,playwright.config.js,package-lock.json,support,examples}
```

---

### Task 1: The generated-tests Playwright project

- [ ] **Step 1: Write `autopilot-qa/tests-generated/package.json`**

```json
{
  "name": "tests-generated",
  "private": true,
  "scripts": { "test": "playwright test", "test:support": "node --test support/fixtures.test.js" },
  "devDependencies": { "@playwright/test": "^1.55.0" }
}
```

- [ ] **Step 2: Write `autopilot-qa/tests-generated/playwright.config.js`**

```js
// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.js',
  workers: 1, // the target platform keeps shared in-memory state
  retries: 0, // flakiness is classified by the executor, not hidden by retries
  reporter: [['list']],
  use: { actionTimeout: 5000, navigationTimeout: 10000 },
  expect: { timeout: 5000 },
});
```

- [ ] **Step 3: Write `autopilot-qa/tests-generated/support/fixtures.js`**

```js
// Runtime for AutoPilot-QA generated scripts (roadmap contract C5).
const base = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const MAP_PATH = process.env.APQA_SELECTOR_MAP || path.join(__dirname, '..', '..', 'backend', 'config', 'selector_map.v1.json');
const selectorMap = JSON.parse(fs.readFileSync(MAP_PATH, 'utf8'));

const DEFAULT_PORTS = { auth: 4001, catalog: 4002, cart: 4003, order: 4004, payment: 4005 };
const urls = Object.fromEntries(
  Object.entries(selectorMap.services).map(([name, svc]) => [
    name,
    process.env[svc.base_url_env] || `http://localhost:${DEFAULT_PORTS[name]}`,
  ]),
);

let lastSelectorKey = null;

// Resolves a logical key such as 'catalog.addToCartButton' through the active selector map.
function sel(key) {
  const [service, element] = String(key).split('.');
  const entry = selectorMap.services[service] && selectorMap.services[service].elements[element];
  if (!entry) throw new Error(`Unknown selector key "${key}" (selector map v${selectorMap.version})`);
  lastSelectorKey = key;
  return entry.selector;
}

const stripAnsi = (s) => String(s).replace(/\u001b\[[0-9;]*m/g, '');

const test = base.test.extend({
  // On failure, capture what the Triage & Healer agent needs: error, DOM, screenshot, last selector key.
  apqaFailureCapture: [
    async ({ page }, use, testInfo) => {
      lastSelectorKey = null;
      await use();
      if (testInfo.status === testInfo.expectedStatus) return;
      const dir = process.env.APQA_ARTIFACTS_DIR || testInfo.outputDir;
      fs.mkdirSync(dir, { recursive: true });
      const domPath = path.join(dir, 'dom.html');
      const screenshotPath = path.join(dir, 'failure.png');
      const saved = { dom: false, screenshot: false };
      try { fs.writeFileSync(domPath, await page.content()); saved.dom = true; } catch { /* page may be closed */ }
      try { await page.screenshot({ path: screenshotPath, fullPage: true }); saved.screenshot = true; } catch { /* ditto */ }
      fs.writeFileSync(path.join(dir, 'failure.json'), JSON.stringify({
        error: testInfo.error ? stripAnsi(testInfo.error.message) : null,
        last_selector_key: lastSelectorKey,
        url: page.url(),
        dom_path: saved.dom ? domPath : null,
        screenshot_path: saved.screenshot ? screenshotPath : null,
        selector_map_version: selectorMap.version,
      }, null, 2));
    },
    { auto: true },
  ],
});

module.exports = { test, expect: base.expect, sel, urls, selectorMap };
```

- [ ] **Step 4: Write `autopilot-qa/tests-generated/support/fixtures.test.js`**

```js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const FIXTURES = path.join(__dirname, 'fixtures.js');
const run = (code, env = {}) =>
  execFileSync(process.execPath, ['-e', code], { env: { ...process.env, ...env }, encoding: 'utf8' }).trim();

test('sel resolves keys through the seed map and rejects unknown keys', () => {
  const { sel } = require('./fixtures');
  assert.equal(sel('catalog.addToCartButton'), '#add-to-cart-btn');
  assert.throws(() => sel('catalog.nope'), /Unknown selector key "catalog.nope" \(selector map v1\)/);
});

test('APQA_SELECTOR_MAP and service URL env vars are honoured', () => {
  const seed = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'config', 'selector_map.v1.json'), 'utf8'));
  seed.version = 2;
  seed.services.catalog.elements.addToCartButton.selector = '#add-to-bag-btn';
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'apqa-')), 'map.json');
  fs.writeFileSync(file, JSON.stringify(seed));
  const out = run(`const f=require(${JSON.stringify(FIXTURES)});console.log(f.sel('catalog.addToCartButton'), f.selectorMap.version, f.urls.catalog)`,
    { APQA_SELECTOR_MAP: file, CATALOG_SERVICE_URL: 'http://localhost:9002' });
  assert.equal(out, '#add-to-bag-btn 2 http://localhost:9002');
});
```

- [ ] **Step 5: Write `autopilot-qa/tests-generated/examples/remove-only-item.spec.js`** (the few-shot example; it must pass against the live platform)

```js
const { test, expect, sel, urls } = require('../support/fixtures');

test('Removing the only item empties the cart', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.productCard'), { hasText: 'Yoga Mat' }).locator(sel('catalog.addToCartButton')).click();
  await expect(page.locator(sel('catalog.cartMessage'))).toHaveText('Added Yoga Mat to cart');
  await page.goto(urls.cart + '/cart.html');
  await page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' }).locator(sel('cart.removeButton')).click();
  await expect(page.locator(sel('cart.emptyCart'))).toBeVisible();
  await expect(page.locator(sel('cart.cartTotal'))).toHaveText('$0.00');
  await expect(page.locator(sel('cart.checkoutButton'))).toBeDisabled();
});
```

- [ ] **Step 6: Update `.gitignore`** so the project files are tracked while generated runs stay ignored:

```gitignore
autopilot-qa/tests-generated/*
!autopilot-qa/tests-generated/.gitkeep
!autopilot-qa/tests-generated/package.json
!autopilot-qa/tests-generated/package-lock.json
!autopilot-qa/tests-generated/playwright.config.js
!autopilot-qa/tests-generated/support/
!autopilot-qa/tests-generated/examples/
```

- [ ] **Step 7: Install and run the support tests**

```powershell
cd autopilot-qa\tests-generated; npm install; npm run test:support
```
Expected: `# pass 2`.

- [ ] **Step 8: Run the example against the live platform** (with `target-platform` running `npm run start:all`, and after resetting all five services)

Run: `npx playwright test examples/`
Expected: `1 passed`.

---

### Task 2: Selector-map loading and the setting

**Files:** Modify `settings.py`; create `db/selector_maps.py`, `tests/db/test_selector_maps.py`.

**Interfaces — Produces:** `Settings.tests_generated_dir: Path`; `async latest_active(db) -> SelectorMap` (raises `LookupError` if none); `write_selector_map_file(selector_map, directory) -> Path` (writes `selector_map.v<N>.json` in the seed-file shape `{version, services}`).

- [ ] **Step 1: Add to `Settings` in `settings.py`** (below `artifacts_dir`)

```python
    tests_generated_dir: Path = BACKEND_DIR.parent / "tests-generated"
```

- [ ] **Step 2: Write the failing test `tests/db/test_selector_maps.py`**

```python
import json

import pytest

from db.models import SelectorMap
from db.mongo_client import init_db
from db.repository import insert
from db.selector_maps import latest_active, write_selector_map_file


async def test_latest_active_prefers_the_highest_active_version(db):
    await init_db(db)
    assert (await latest_active(db)).version == 1
    await insert(db, SelectorMap(version=3, map={"catalog": {}}, created_by="healer", status="pending_review"))
    await insert(db, SelectorMap(version=2, parent_version=1, map={"catalog": {}}, created_by="healer"))
    assert (await latest_active(db)).version == 2


async def test_latest_active_requires_a_map(db):
    with pytest.raises(LookupError, match="init_db"):
        await latest_active(db)


async def test_write_selector_map_file_uses_the_seed_shape(db, tmp_path):
    await init_db(db)
    path = write_selector_map_file(await latest_active(db), tmp_path)
    assert path.name == "selector_map.v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["services"]["catalog"]["elements"]["addToCartButton"]["selector"] == "#add-to-cart-btn"
```

- [ ] **Step 3: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_selector_maps.py -q`
Expected: `ModuleNotFoundError: No module named 'db.selector_maps'`

- [ ] **Step 4: Write `db/selector_maps.py`**

```python
"""Access to versioned selector maps (roadmap contract C4)."""
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/db/test_selector_maps.py tests/test_settings.py -q`
Expected: `6 passed`

---

### Task 3: Script validator

**Files:** Create `execution/__init__.py`, `execution/script_validator.py`, `tests/execution/test_script_validator.py`.

**Interfaces — Produces:** `ValidationResult(errors: list[str])` with `ok`; `validate_script(code, selector_map: dict, *, check_syntax=True) -> ValidationResult`. `selector_map` is the `services` dict.

- [ ] **Step 1: Write the failing test `tests/execution/test_script_validator.py`**

```python
import json

import pytest

from execution.script_validator import validate_script
from settings import BACKEND_DIR

SERVICES = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))["services"]
EXAMPLE = (BACKEND_DIR.parent / "tests-generated" / "examples" / "remove-only-item.spec.js").read_text(encoding="utf-8")
HEADER = "const { test, expect, sel, urls } = require('../support/fixtures');\n\n"


def script(body: str) -> str:
    return HEADER + "test('t', async ({ page }) => {\n" + body + "\n  await expect(page).toHaveURL(/x/);\n});\n"


def errors(code: str) -> list[str]:
    return validate_script(code, SERVICES).errors


def test_the_prompt_example_is_valid():
    assert validate_script(EXAMPLE, SERVICES).ok


def test_urls_inside_strings_are_not_comments():
    assert errors(script("  await expect(page).toHaveURL('http://localhost:4005/payment.html'); // real comment")) == []


def test_forbidden_words_inside_strings_are_fine():
    assert errors(script("  await expect(page.locator(sel('auth.message'))).toHaveText('process.env fetch( eval(');")) == []


@pytest.mark.parametrize("body, expected", [
    ("  const fs = require('fs');", "only require('../support/fixtures')"),
    ("  const x = process.env.SECRET;", "forbidden: process"),
    ("  await page.evaluate(() => 1);", "forbidden: page.evaluate"),
    ("  await page.waitForTimeout(500);", "forbidden: waitForTimeout"),
    ("  await fetch('http://evil');", "forbidden: fetch"),
    ("  await page.getByRole('button').click();", "forbidden: getBy"),
    ("  await page.click('#login-btn');", "forbidden: page.click"),
    ("  await page.locator('#login-btn').click();", "locator() must take sel("),
    ("  await page.locator(sel('catalog.nope')).click();", "unknown selector key 'catalog.nope'"),
    ("  const k = 'auth.loginButton';\n  await page.locator(sel(k)).click();", "sel() needs a string literal"),
    ("  await page.goto('http://localhost:4002/catalog.html');", "page.goto must start with urls.<service>"),
    ("  await page.goto(urls.shipping + '/x');", "unknown service 'shipping'"),
])
def test_violations_are_reported(body, expected):
    assert any(expected in e for e in errors(script(body))), errors(script(body))


def test_structure_rules():
    assert any("exactly one test(" in e for e in errors(HEADER + "test('a', async () => {});\ntest('b', async () => {});"))
    assert any("no expect(" in e for e in errors(HEADER + "test('a', async ({ page }) => { await page.goto(urls.auth + '/login.html'); });"))
    assert any("must start with" in e for e in errors("test('a', async () => { expect(1); });"))
    assert any("import" in e for e in errors("import { test } from '@playwright/test';\n" + script("")))


def test_syntax_errors_are_caught_by_node():
    assert any("syntax" in e for e in errors(script("  await page.goto(urls.auth + '/login.html'")))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/execution -q`
Expected: `ModuleNotFoundError: No module named 'execution'`

- [ ] **Step 3: Write `execution/script_validator.py`**

```python
"""Static checks for LLM-written Playwright scripts before they run on the host (roadmap G14, contract C5)."""
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HEADER = re.compile(r"^\s*const\s*\{[^}]*\}\s*=\s*require\(\s*'\.\./support/fixtures'\s*\)\s*;", re.MULTILINE)
_STRING = r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`"
# Strings and comments are scanned together so '//' inside a URL string is not taken for a comment.
_TOKENS = re.compile(rf"({_STRING})|//[^\n]*|/\*.*?\*/", re.DOTALL)

FORBIDDEN = {
    "process": r"\bprocess\b",
    "globalThis": r"\bglobalThis\b",
    "__dirname/__filename": r"\b__(dirname|filename)\b",
    "eval": r"\beval\s*\(",
    "Function constructor": r"\bFunction\s*\(",
    "dynamic import": r"\bimport\s*\(",
    "fetch": r"\bfetch\s*\(",
    "XMLHttpRequest": r"\bXMLHttpRequest\b",
    "WebSocket": r"\bWebSocket\b",
    "page.evaluate": r"\.evaluate(All|Handle)?\s*\(",
    "page.$": r"\.\$\$?\s*\(",
    "getBy": r"\.getBy[A-Z]\w*\s*\(",
    "waitForTimeout": r"\bwaitForTimeout\b",
    "page.click": r"\bpage\.(click|dblclick|fill|type|press|check|uncheck|hover|selectOption|tap|focus)\s*\(",
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _blank_strings(code: str) -> str:
    """Replace string contents with spaces so words inside test data never trigger identifier rules."""
    return re.sub(_STRING, lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], code)


def _strip_comments(code: str) -> str:
    return _TOKENS.sub(lambda m: m.group(1) or " ", code)


def _syntax_error(code: str) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "script.js"
        path.write_text(code, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return None
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    return next((line for line in lines if "Error" in line), lines[-1] if lines else "unknown error")


def validate_script(code: str, selector_map: dict, *, check_syntax: bool = True) -> ValidationResult:
    errors: list[str] = []
    no_comments = _strip_comments(code)
    bare = _blank_strings(no_comments)

    if not HEADER.match(no_comments):
        errors.append("script must start with: const { test, expect, sel, urls } = require('../support/fixtures');")
    if re.search(r"^\s*import\s", bare, re.MULTILINE):
        errors.append("ES module import is not allowed; use the fixtures require")
    requires = re.findall(r"\brequire\s*\(\s*([^)]*)\)", no_comments)
    if any(arg.strip() != "'../support/fixtures'" for arg in requires):
        errors.append("only require('../support/fixtures') is allowed")

    for name, pattern in FORBIDDEN.items():
        if re.search(pattern, bare):
            errors.append(f"forbidden: {name}")

    if len(re.findall(r"(?<![\w.])test\s*\(", bare)) != 1:
        errors.append("script must contain exactly one test(...)")
    if not re.search(r"\bexpect\s*\(", bare):
        errors.append("script has no expect(...) assertion")

    for arg in re.findall(r"\.locator\s*\(\s*([^,)]*)", no_comments):
        if not arg.strip().startswith("sel("):
            errors.append(f"locator() must take sel('<service>.<element>'), got {arg.strip()[:40]}")
    for arg in re.findall(r"\bsel\s*\(\s*([^)]*)\)", no_comments):
        literal = re.fullmatch(r"'([\w]+)\.([\w]+)'", arg.strip())
        if not literal:
            errors.append(f"sel() needs a string literal key like 'catalog.addToCartButton', got {arg.strip()[:40]}")
        elif literal.group(2) not in selector_map.get(literal.group(1), {}).get("elements", {}):
            errors.append(f"unknown selector key '{literal.group(1)}.{literal.group(2)}'")
    for arg in re.findall(r"\bpage\.goto\s*\(\s*([^)]*)\)", no_comments):
        service = re.match(r"urls\.(\w+)\b", arg.strip())
        if not service:
            errors.append(f"page.goto must start with urls.<service>, got {arg.strip()[:40]}")
        elif service.group(1) not in selector_map:
            errors.append(f"unknown service '{service.group(1)}' in page.goto")

    if check_syntax and not errors:
        problem = _syntax_error(code)
        if problem:
            errors.append(f"syntax error: {problem}")
    return ValidationResult(errors)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/execution -q`
Expected: `17 passed`

---

### Task 4: Test Generator agent

**Files:** Create `prompts/test_generator.md`, `agents/test_generator.py`, `tests/agents/test_test_generator.py`.

**Interfaces:**
- Consumes: `TestCase`, `Script`, `get`, `insert`, `latest_active`, `validate_script`, `load_platform_context`, `RunContext`/`Task`/`Agent`.
- Produces: `GeneratorError`; `OPTIONS`; `EXAMPLE_PATH`; `extract_code(reply) -> str`; `render_selector_catalogue(services) -> str`; `build_prompt(test_case, services) -> str`; `async generate_script(llm, test_case, services, *, model=None) -> str`; `TestGenerator(Agent)`. Its `handle` takes payload `{"test_case_id"}` and returns `{"script_id", "path", "selector_map_version"}`.

- [ ] **Step 1: Write the failing test `tests/agents/test_test_generator.py`**

```python
import json

import pytest

from agents.base import RunContext, Task
from agents.test_generator import (EXAMPLE_PATH, GeneratorError, TestGenerator, build_prompt, extract_code,
                                   generate_script)
from db.models import AgentTrace, Script, TestCase
from db.mongo_client import init_db
from db.repository import find, get, insert
from settings import BACKEND_DIR, Settings
from tests.fakes import FakeLLM

SERVICES = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))["services"]
GOOD = EXAMPLE_PATH.read_text(encoding="utf-8")
BAD = "const fs = require('fs');\ntest('x', async ({ page }) => { await page.click('#pay-btn'); });"
CASE = TestCase(run_id="run-1", requirement_id="0" * 24, type="negative", title="Expired card is declined",
                steps=["Open the payment page for an order", "Enter card 4111111111111111 and expiry 01/20", "Click 'Pay'"],
                expected_result="'Payment declined: card expired' is shown")


def test_extract_code_handles_fences_and_bare_replies():
    assert extract_code("Here you go:\n```javascript\nconst a = 1;\n```\nDone") == "const a = 1;\n"
    assert extract_code("```js\nx();\n```") == "x();\n"
    assert extract_code("  const b = 2;  ") == "const b = 2;\n"


def test_prompt_lists_keys_test_case_and_example():
    prompt = build_prompt(CASE, SERVICES)
    assert "catalog.addToCartButton (inside catalog.productCard)" in prompt
    assert "urls.payment + '/payment.html'" in prompt
    assert "Enter card 4111111111111111 and expiry 01/20" in prompt
    assert "Removing the only item empties the cart" in prompt  # the few-shot example
    assert "Title: Expired card is declined" in prompt


async def test_generate_script_returns_valid_code():
    llm = FakeLLM([f"```javascript\n{GOOD}```"])
    assert await generate_script(llm, CASE, SERVICES, model="qwen2.5-coder:7b") == GOOD
    assert llm.calls[0]["model"] == "qwen2.5-coder:7b"


async def test_invalid_code_gets_one_repair_with_the_errors():
    llm = FakeLLM([BAD, GOOD])
    assert await generate_script(llm, CASE, SERVICES) == GOOD
    repair = llm.calls[1]["prompt"]
    assert "only require('../support/fixtures') is allowed" in repair and "forbidden: page.click" in repair


async def test_gives_up_after_one_repair():
    with pytest.raises(GeneratorError, match="forbidden: page.click"):
        await generate_script(FakeLLM([BAD, BAD]), CASE, SERVICES)


async def test_agent_writes_the_script_and_persists_it(db, tmp_path):
    await init_db(db)
    settings = Settings(_env_file=None, tests_generated_dir=tmp_path)
    ctx = RunContext(run_id="run-1", db=db, llm=FakeLLM([GOOD]), settings=settings)
    case_id = await insert(db, CASE.model_copy())

    result = await TestGenerator().handle(Task("t2", "generate", {"test_case_id": case_id}), ctx)

    path = tmp_path / "run-1" / f"{case_id}.spec.js"
    assert result == {"script_id": result["script_id"], "path": str(path), "selector_map_version": 1}
    assert path.read_text(encoding="utf-8") == GOOD
    stored = await get(db, Script, result["script_id"])
    assert (stored.test_case_id, stored.code, stored.path, stored.selector_map_version) == (case_id, GOOD, str(path), 1)
    [trace] = await find(db, AgentTrace, {"run_id": "run-1"})
    assert trace.agent == "test_generator" and "Expired card is declined" in trace.thought
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/agents/test_test_generator.py -q`
Expected: `ModuleNotFoundError: No module named 'agents.test_generator'`

- [ ] **Step 3: Write `prompts/test_generator.md`** (a `string.Template`; literal dollar signs are `$$`)

```text
You write one Playwright test in JavaScript for ShopLite, a small e-commerce web app, from a manual test case.

Hard rules (a validator rejects the script otherwise):
1. The first line is exactly: const { test, expect, sel, urls } = require('../support/fixtures');
2. Exactly one test('<test case title>', async ({ page }) => { ... }); no other require, import or helper code.
3. Reach elements ONLY through selector keys: page.locator(sel('<service>.<element>')).
   An element marked "inside X" must be located inside X, filtered by visible text:
   page.locator(sel('catalog.productCard'), { hasText: 'Yoga Mat' }).locator(sel('catalog.addToCartButton'))
   Never write CSS selectors, getByRole/getByText, page.click('...') or page.fill('...') directly.
4. Navigate ONLY with page.goto(urls.<service> + '<page path>') using the page paths listed below.
5. Act with .click() / .fill('value') on locators. Assert with expect(locator).toHaveText / toContainText /
   toBeVisible / toBeHidden / toBeDisabled / toBeEnabled / toHaveCount, or expect(page).toHaveURL(/regex/).
   Never use waitForTimeout: expect() waits automatically.
6. After clicking an add-to-cart button, assert the cart message before navigating away (the add is asynchronous).
7. To pay, first create an order: add a product in the catalog, open the cart, click checkout, fill all shipping fields
   and click place order; the browser then lands on the payment page.
8. End with at least one expect() that checks the test case's expected result.
9. Reply with the JavaScript only, in one ```javascript code block.

Selector keys (service.element — purpose) and pages:
$selector_catalogue

Test data:
$test_data

Example of a correct script:
```javascript
$example```

Test case to automate:
Title: $title
Type: $type
Steps:
$steps
Expected result: $expected_result
```

- [ ] **Step 4: Write `agents/test_generator.py`**

```python
"""Test Generator agent: test case -> validated Playwright script (spec §4.3, roadmap contract C5)."""
import re
from string import Template
from typing import Any

from agents.base import Agent, RunContext, Task
from agents.platform_context import load_platform_context
from db.models import Script, TestCase
from db.repository import get, insert
from db.selector_maps import latest_active
from execution.script_validator import validate_script
from llm.ollama_client import LLMBackend
from settings import BACKEND_DIR

PROMPT_PATH = BACKEND_DIR / "prompts" / "test_generator.md"
EXAMPLE_PATH = BACKEND_DIR.parent / "tests-generated" / "examples" / "remove-only-item.spec.js"
OPTIONS = {"temperature": 0.2, "num_ctx": 8192}
_FENCE = re.compile(r"```(?:javascript|js)?[ \t]*\n(.*?)```", re.DOTALL)


class GeneratorError(RuntimeError):
    """The model did not produce a valid script, even after one repair attempt."""


def extract_code(reply: str) -> str:
    blocks = _FENCE.findall(reply)
    code = next((b for b in blocks if "test(" in b), blocks[0] if blocks else reply)
    return code.strip() + "\n"


def render_selector_catalogue(services: dict[str, Any]) -> str:
    lines = []
    for service, spec in services.items():
        pages = ", ".join(f"urls.{service} + '{path}'" for path in spec.get("pages", {}).values())
        lines.append(f"{service} — pages: {pages}")
        for key, element in spec["elements"].items():
            inside = f" (inside {service}.{element['scope']})" if element.get("scope") else ""
            lines.append(f"  {service}.{key}{inside} — {element['description']}")
    return "\n".join(lines)


def build_prompt(test_case: TestCase, services: dict[str, Any]) -> str:
    return Template(PROMPT_PATH.read_text(encoding="utf-8")).substitute(
        selector_catalogue=render_selector_catalogue(services),
        test_data=load_platform_context(("Test data",)),
        example=EXAMPLE_PATH.read_text(encoding="utf-8"),
        title=test_case.title,
        type=test_case.type,
        steps="\n".join(f"{i}. {step}" for i, step in enumerate(test_case.steps, start=1)),
        expected_result=test_case.expected_result,
    )


async def generate_script(llm: LLMBackend, test_case: TestCase, services: dict[str, Any], *,
                          model: str | None = None) -> str:
    prompt = build_prompt(test_case, services)
    code = extract_code(await llm.generate(prompt, model=model, options=OPTIONS))
    result = validate_script(code, services)
    if result.ok:
        return code
    repair = (f"{prompt}\n\nYour previous script was rejected by the validator:\n"
              + "\n".join(f"- {e}" for e in result.errors)
              + f"\n\nPrevious script:\n```javascript\n{code}```\n\nReturn the corrected script only.")
    code = extract_code(await llm.generate(repair, model=model, options=OPTIONS))
    result = validate_script(code, services)
    if not result.ok:
        raise GeneratorError("invalid script after one repair: " + "; ".join(result.errors))
    return code


class TestGenerator(Agent):
    __test__ = False  # not a pytest test class
    name = "test_generator"

    async def handle(self, task: Task, ctx: RunContext) -> dict[str, Any]:
        test_case = await get(ctx.db, TestCase, task.payload["test_case_id"])
        if test_case is None:
            raise LookupError(f"test case {task.payload['test_case_id']} not found")
        selector_map = await latest_active(ctx.db)
        code = await generate_script(ctx.llm, test_case, selector_map.map, model=ctx.settings.generator_model)

        path = ctx.settings.tests_generated_dir / ctx.run_id / f"{test_case.id}.spec.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        script_id = await insert(ctx.db, Script(run_id=ctx.run_id, test_case_id=test_case.id, code=code,
                                                path=str(path), selector_map_version=selector_map.version))
        await ctx.emit_thought(self.name, f"Generated a Playwright script for '{test_case.title}' "
                                          f"against selector map v{selector_map.version}.", task_id=task.task_id)
        return {"script_id": script_id, "path": str(path), "selector_map_version": selector_map.version}
```

- [ ] **Step 5: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/agents tests/execution tests/db -q`
Expected: all pass (the 6 generator tests included).

## Phase 5 Exit Checklist

- [ ] `pytest -q` green; `npm run test:support` green.
- [ ] `examples/remove-only-item.spec.js` passes against the live platform.
- [ ] Live: generate the reference-flow positive test case with `qwen2.5-coder:7b`. The script validates and passes against v1 with `npx playwright test <file>` (done during the Phase 6 dry run).

---

## Execution Notes (2026-09-27)

Tests: backend validator/generator green; `npm run test:support` 2/2 after fixing `node --test support/` →
`support/fixtures.test.js` (Node 22 doesn't accept a directory). The example spec passes live. Failure capture was
verified live with a broken map: failure.json, dom.html and failure.png, with `last_selector_key`.

Changes found in live generation (qwen2.5-coder:7b):
- **Invented scoping** (`cart.checkoutButton` looked up inside `cart.cartItem`). The validator now enforces the map's
  scopes both ways: scoped elements must be chained inside their scope, unscoped ones located on the page. This
  triggers the repair round.
- **Guessed URL** (`/\/payment\/\w+/`). The prompt now says to match the listed page paths (`/payment\.html/`).
- **Skipped a step** (the name field was never filled; the app correctly said "Please fill in all shipping fields").
  Rule 7 now names all four shipping fields. Such generation errors are why the evaluation only replays scripts that
  pass on the clean platform.
- The generator also receives the UI text section (exact messages to assert).

After these fixes, the live reference-flow script passes (see the Phase 6 notes).
