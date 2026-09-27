# AutoPilot-QA — Master Roadmap (Phase-wise Plan)

> **For agentic workers:** This is the *roadmap*. It fixes phase boundaries, cross-phase interface contracts and exit criteria. Each phase gets its own bite-sized plan file (`docs/superpowers/plans/YYYY-MM-DD-phase-N-<name>.md`) written at the start of that phase, using superpowers:writing-plans, and executed with superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Phase 1's detailed plan already exists: `2026-09-27-phase-1-target-platform.md`.

**Goal:** Build AutoPilot-QA, a local-LLM multi-agent QA framework that turns a plain-English requirement into executed Playwright tests against a 5-microservice e-commerce app, and **self-heals broken locators with a logged, explainable reasoning trace**.

**Architecture:** Two codebases in one repo. `target-platform/` is five Node/Express services with server-rendered HTML and in-memory stores. `autopilot-qa/` is a Python FastAPI backend: an async Orchestrator drives four specialist agents over a MongoDB capped-collection message bus, calls Ollama for inference, runs Playwright for execution, and streams THOUGHT lines to a React dashboard over WebSockets.

**Tech Stack:** Node 22 + Express 4 (target), Python 3.12 + FastAPI + Motor + Pydantic v2 + httpx (backend), Ollama (`llama3.1:8b` / `qwen2.5-coder:7b`), Playwright (JS test runner), MongoDB 7, React + Vite + Recharts (dashboard).

## Global Constraints

- Inference is local by default (Ollama at `OLLAMA_URL`, default `http://localhost:11434`). A cloud backend (Groq/Gemini) is allowed only behind the same `LLMBackend` interface.
- `HEALING_CONFIDENCE_THRESHOLD` defaults to `0.80`. Below it, a heal is **never** auto-applied.
- Healer output format: `THOUGHT: <1-2 sentences>` then `ACTION: {"new_locator": "...", "confidence": 0.0-1.0, "basis": "..."}`, streamed with `"stream": true`.
- Every bus message carries `message_id, run_id, from_agent, to_agent, task_id, type (request|result|error|thought), payload, timestamp`.
- Orchestrator retries a failed *task* at most 2 times, then escalates.
- Every healing attempt, successful or not, is written to `agent_traces`.
- Service ports: auth 4001, catalog 4002, cart 4003, order 4004, payment 4005. Every service exposes `POST /api/__reset`.
- `CATALOG_BUTTON_VARIANT=v1` → `<button id="add-to-cart-btn">Add to Cart</button>`; `v2` → `<button id="add-to-bag-btn">Add to Bag</button>`.
- Mongo database name: `autopilot_qa`.
- Out of scope: commercial packaging, SSO/RBAC, CI marketplace plugins, visual regression, API-contract healing, production-grade target platform.
- Platform: developer machine is Windows 11. Every command must work in PowerShell (or run in Docker). Use a Python venv, **not** `pip --break-system-packages` (that flag is Linux/PEP 668 specific).

---

## Part A — Spec Analysis

### A.1 What is solid in the spec

- The scope is clear: self-healing is the headline, and everything else exists to demonstrate and measure it.
- The build order (Section 13) is sound: each phase can be run and demoed before the next begins.
- The seeded UI change (`CATALOG_BUTTON_VARIANT`) is a clean, repeatable trigger for the core demo.
- MongoDB fits well: artifacts are variably shaped LLM JSON, and a capped collection plus a tailable cursor works as a real pub/sub on a **standalone** mongod (no replica set needed, unlike change streams).
- The THOUGHT/ACTION split makes explainability something you can test and parse, rather than just an aspiration.

### A.2 Gaps and ambiguities, with the decision taken

Each item below is a place where the spec is silent or contradictory. The roadmap adopts the **Decision** column. Items marked ⚠ change behaviour the spec states, so please confirm or override them before the phase that depends on them.

| # | Gap | Decision | Phase |
|---|-----|----------|-------|
| G1 | How do 5 independent services with separate stores know "whose cart" it is? | One shared cookie `apqa_sid`. Cookies are not port-isolated, so all `localhost:40xx` services see it. Cart is keyed by `apqa_sid`. Login attaches an email to the existing sid, so the cart survives login. **All public URLs must use `localhost`, never `127.0.0.1`**, or cookies split. | 1 |
| G2 | Pages on one port call APIs on another port. | Test-platform CORS middleware reflects `Origin` with credentials. Browser JS uses `fetch(..., {credentials:'include'})`. | 1 |
| G3 | ✅ *Confirmed 2026-09-27.* Spec says `POST /api/orders` "calls Payment Service", yet card details are entered on `payment.html` (payment-service). Both cannot be true as written. | Flow: checkout.html (shipping) → `POST /api/orders` creates order `pending_payment` and clears the cart → redirect to `payment.html?orderId&amount` → `POST /api/payments` → on approval, payment-service calls back `POST /api/orders/:id/paid`. The dependency direction is Payment→Order, not Order→Payment. | 1 |
| G4 | The Catalog "search" capability has no API. | `GET /api/products?q=` (case-insensitive, name+description) and `catalog.html?q=` (server-rendered GET form). | 1 |
| G5 | `id="add-to-cart-btn"` on every product card means duplicate DOM ids. | Kept verbatim (the demo depends on it). All locators are **scoped to a `.product-card`**. The healer's verification step (G10) checks uniqueness *within the scope*. | 1, 8 |
| G6 | The spec never says what the "selector map" (4.3) contains. | Versioned JSON of logical keys → selectors per service (contract C4). Generated scripts reference **logical keys**, not raw selectors (G7). | 5 |
| G7 | "Apply the new locator and retry the failed step once": a generated `.spec.js` run by the Playwright CLI cannot resume at a step. | Scripts call `sel('catalog.addToCartButton')` from a fixture that reads the selector map named by `APQA_SELECTOR_MAP`. Healing writes selector-map version N+1 and re-runs the **same unmodified script once**. `scripts.selector_map_version` records which map a run used. This also removes hallucinated selectors: an unknown key fails static validation before execution. | 5, 8 |
| G8 | ✅ *Confirmed 2026-09-27.* The spec lists 7 collections, and healed locators need a home. | Add an 8th collection, `selector_maps` (`{version, parent_version, map, created_by: "seed"\|"healer", run_id, created_at}`). Proposals below the threshold are stored with `status: "pending_review"` and are never used. | 2, 8 |
| G9 | Passing a full DOM to an 8B model exceeds its context and invites guessing. | Deterministic **candidate extraction** first: interactive elements (button/a/input/[role]), ranked by text/attribute/tag/position similarity to the broken element's last-known description. Only the top-k (≈8) go into the prompt. | 3b, 8 |
| G10 | LLM self-reported confidence is poorly calibrated. | Before the gate, the proposed locator must **resolve in the DOM snapshot** (≥1 match overall, exactly 1 within the script's scope, same tag/role as the original). If it fails, confidence is forced to 0 and the reason is logged. The raw LLM confidence is still logged for the calibration metric. | 8 |
| G11 | How is a test "flaky" (7.4)? | A failure that passes on an **unmodified** immediate re-run is `flaky`. A failure that passes only after a heal is `fail` + healed (recorded on the execution as `healing: {...}`). | 6, 8 |
| G12 | Distinguishing a benign UI change from a real defect. | Deterministic triage runs first. The error class (locator timeout vs assertion mismatch vs HTTP 4xx/5xx vs connection refused) maps to candidate root_cause (`ui`/`data`/`api`/`environment`). Only locator-not-found failures go to healing; the others go straight to a bug report. | 8 |
| G13 | LLMs will invent product names and test cards. | `target-platform/README.md` (Phase 1) doubles as the **platform context** doc (pages, seed products, test users, test cards). It is injected into the Analyzer and Generator prompts. | 1, 4, 5 |
| G14 | LLM-written JavaScript runs on the host. | A static validator before execution: only `require('../support/fixtures')` / `@playwright/test`; no `fs`, `child_process`, `process.env` writes, `eval`, or network calls other than `page`/`request`; only known selector keys. Scripts run in the `tests-generated/` sandbox dir. | 5 |
| G15 | ✅ *Confirmed 2026-09-27.* The spec allows "LangGraph or CrewAI", but it already specifies its own bus, retries and sequencing. | **A plain asyncio coordinator** (explicit state machine). LangGraph would duplicate the bus and state and hide the message trail that the explainability goal depends on. Recommendation: keep it framework-free and justify this in the dissertation. | 7 |
| G16 | The seeded-bug catalogue for evaluation is unspecified, with no ground truth. | `evaluation/seeded-bugs/registry.yaml`: each entry has an id, a service, a toggle (env flag), `label: benign_ui_change \| real_defect`, and the expected healer outcome. Target services read a `SEEDED_CHANGES` env var (comma list). Mechanism added in Phase 10. | 10 |
| G17 | Evaluation needs many restarts to toggle variants. | Optional test-only `POST /api/__config` on catalog (flip variant at runtime) for the evaluation runner. Restart via env remains the canonical demo path. | 10 |
| G18 | Two models are named. | Model is chosen **per agent** via env (`ANALYZER_MODEL`, `GENERATOR_MODEL`, `HEALER_MODEL`). Default: generator=`qwen2.5-coder:7b`, analyzer/healer=`llama3.1:8b`. Picked by the Phase 3b spike data. | 3, 3b |
| G19 | The spec has no JSON-robustness strategy. | The Analyzer uses Ollama `format` (JSON schema) + Pydantic validation + one repair re-prompt. The Healer can't use `format` (it needs a free-text THOUGHT), so the parser extracts the first balanced `{...}` after `ACTION:`, and on a parse failure the attempt is logged as `confidence: 0`. | 3, 4, 8 |
| G20 | Stock never decrements. | Intentional (YAGNI). Catalog stock is static seed data, and `__reset` restores it anyway. | 1 |

### A.3 Structural risk in the spec's build order

The headline feature (the Healer) is step 8 of 10. If the schedule slips, the thing the dissertation is judged on is the least mature. **Mitigation: insert Phase 3b, a healer feasibility spike**, straight after the Ollama client. Capture the v2 catalog DOM, run candidate extraction plus the THOUGHT/ACTION prompt 20×, and measure how often it proposes `#add-to-bag-btn` with confidence ≥0.80. This decides the model choice and the prompt shape early, and gives a fallback demo if later phases run late.

### A.4 Environment findings (2026-09-27)

- Node v22.21.0 ✔, Python 3.12.10 ✔, Docker 28.5.1 ✔.
- **Ollama is not on PATH.** Install it from ollama.com (Windows installer) before Phase 3, then `ollama pull llama3.1:8b` and `ollama pull qwen2.5-coder:7b`. 8B models need about 8 GB free RAM/VRAM. On CPU only, expect 5–20 s per healer call, so budget evaluation runtime for it.
- The repo is not a git repository yet (Phase 0).

---

## Part B — Cross-Phase Interface Contracts

These are fixed now so that a phase's implementer, seeing only their own plan, uses the same names as their neighbours. Change a contract only by editing this section first.

**C1 — LLM backend (`autopilot-qa/backend/llm/ollama_client.py`)**
```python
class LLMBackend(Protocol):
    async def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                       format: dict | str | None = None, options: dict | None = None) -> str: ...
    def stream(self, prompt: str, *, system: str | None = None, model: str | None = None,
               options: dict | None = None) -> AsyncIterator[str]: ...

class OllamaBackend(LLMBackend): ...          # httpx.AsyncClient, POST {OLLAMA_URL}/api/generate
def get_backend(name: str = os.getenv("LLM_BACKEND", "ollama")) -> LLMBackend: ...
```

**C2 — Reasoning-trace parser (`autopilot-qa/backend/llm/trace_parser.py`)**
```python
@dataclass
class Thought: text: str
@dataclass
class Action: data: dict            # parsed JSON after ACTION:
@dataclass
class ParseError: raw: str; reason: str

class ThoughtActionParser:
    def feed(self, chunk: str) -> list[Thought | Action | ParseError]: ...   # emits each completed THOUGHT line as soon as its newline arrives
    def finish(self) -> list[Thought | Action | ParseError]: ...
```

**C3 — Message bus (`autopilot-qa/backend/orchestrator/message_bus.py`)**
```python
class Message(BaseModel):   # exactly the Section 5 schema
    message_id: str; run_id: str; from_agent: str; to_agent: str; task_id: str
    type: Literal["request", "result", "error", "thought"]; payload: dict; timestamp: datetime

class MessageBus:
    async def publish(self, msg: Message) -> None: ...
    def subscribe(self, run_id: str, to_agent: str | None = None) -> AsyncIterator[Message]: ...  # tailable await cursor
```
Agent names (the `from_agent`/`to_agent` values): `orchestrator`, `requirement_analyzer`, `test_generator`, `test_executor`, `triage_healer`, `dashboard`.

**C4 — Selector map (`selector_maps` collection; seed file `autopilot-qa/backend/config/selector_map.v1.json`)**
```json
{ "version": 1,
  "services": {
    "catalog": { "base_url_env": "CATALOG_SERVICE_URL", "pages": { "catalog": "/catalog.html" },
      "elements": {
        "productCard":      { "selector": ".product-card", "description": "list item for one product" },
        "addToCartButton":  { "selector": "#add-to-cart-btn", "scope": "productCard", "description": "button that adds this product to the cart" }
      } } } }
```
Logical key syntax: `"<service>.<element>"`, e.g. `catalog.addToCartButton`.

**C5 — Generated-script contract (Phase 5 writes, Phase 6 runs, Phase 8 heals)**
```js
const { test, expect, sel, urls } = require('../support/fixtures');
test('<test_case title>', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.productCard'), { hasText: 'Wireless Headphones' })
            .locator(sel('catalog.addToCartButton')).click();
});
```
On failure, `support/fixtures.js` writes `artifacts/<run_id>/<script_id>/failure.json`: `{ error, last_selector_key, url, dom_path, screenshot_path }`.

**C6 — Agent interface (`autopilot-qa/backend/agents/base.py`)**
```python
class Agent(ABC):
    name: str
    async def handle(self, task: Task, ctx: RunContext) -> dict: ...   # returns the result payload; raising => bus "error" message
```
`RunContext` exposes `run_id`, `db`, `bus`, `llm`, `settings`, and `emit_thought(text)`, which does both the bus publish and the `agent_traces` insert.

**C7 — Healer decision (stored in `agent_traces.action` and on the execution)**
```json
{ "new_locator": "#add-to-bag-btn", "confidence": 0.91, "llm_confidence": 0.91, "basis": "...",
  "verified": true, "verification": "1 match in .product-card scope, tag=button",
  "applied": true, "selector_key": "catalog.addToCartButton", "selector_map_version": 2 }
```

---

## Part C — Phases

Effort sizes are rough estimates for one developer working with a coding agent: S ≈ 1–2 days, M ≈ 3–5 days, L ≈ 1–2 weeks.

### Phase 0 — Repository bootstrap (S)
**Goal:** a versioned, conventional repo skeleton.
- `git init`, `.gitignore` (node_modules, .venv, `__pycache__`, `.env`, `autopilot-qa/tests-generated/*`, `autopilot-qa/reports/*`, `artifacts/`, `test-results/`, `playwright-report/`), root `README.md` stub, `.editorconfig`.
- Directory skeleton from spec §11 (empty dirs keep a `.gitkeep`).
- Copy the spec into `docs/AutoPilot-QA-Technical-Specification.md`.

**Exit:** `git log` shows the initial commit, and the tree matches §11 at the top level.
*(Folded into Task 0 of the Phase 1 plan.)*

### Phase 1 — Target platform "ShopLite" (M) — **detailed plan written**
**Goal:** 5 independent services, the full manual flow working in a browser, the v1/v2 toggle, `__reset` everywhere.
- `target-platform/` npm workspace, `shared/kit.js` (cookie/CORS/render helpers), 5 services each with `node:test` tests, `scripts/start-all.js` (no Docker needed on Windows), `Dockerfile` + `docker-compose.yml` (5 services + Mongo).
- `evaluation/manual-baseline/` Playwright suite: the reference flow plus negatives (expired card, out-of-stock, empty cart). This is the manual-baseline comparison suite for Phase 10, started early.
- `target-platform/README.md`: selectors, seed data and test cards (the platform-context doc, G13).

**Exit / demo:** `npm test` green in `target-platform/`. The baseline suite passes against v1. Against v2, the reference flow **fails** waiting for `#add-to-cart-btn` while the v2 functional test passes, which proves the seed change breaks locators, not function.

### Phase 2 — MongoDB & data model (S)
**Goal:** typed persistence for every artifact, plus the bus collection.
- `autopilot-qa/backend/` Python package, venv, `requirements.txt` (fastapi, uvicorn, motor, pydantic, pydantic-settings, httpx, pytest, pytest-asyncio), `settings.py` (pydantic-settings, all spec §12 env vars plus the per-agent models and `APQA_ARTIFACTS_DIR`).
- `db/models.py`: Pydantic models for §7.1–7.7 + `selector_maps` (G8) + `Message` (C3).
- `db/mongo_client.py`: `get_db()`, `init_db()`. `init_db` is idempotent: it creates the `messages` capped collection (64 MB), creates indexes (`run_id` on every run-scoped collection, `(run_id, timestamp)` on `agent_traces`) and seeds `selector_maps` v1 from C4.
- Tests run against real Mongo in a throwaway database `autopilot_qa_test_<uuid>` (mongomock doesn't support tailable cursors).

**Exit:** `pytest backend/tests/db` green. `init_db()` run twice is a no-op the second time.

### Phase 3 — LLM client & trace parser (S)
**Goal:** C1 and C2, tested without a live model.
- `OllamaBackend.generate` (non-stream, `format` support) and `.stream` (NDJSON lines → `response` chunks), timeouts, and a clear error when Ollama is unreachable.
- `ThoughtActionParser` (C2): handles markers split across chunks, several THOUGHT lines, ACTION JSON spread over chunks, prose after the JSON, and a missing ACTION.
- Tests use `httpx.MockTransport` with recorded NDJSON fixtures. `scripts/ollama_smoke.py` does a live check (one generate, one stream).

**Exit:** unit tests green, and the live smoke script prints a streamed response from `llama3.1:8b`.

### Phase 3b — Healer feasibility spike (S) — *added, de-risks the headline*
**Goal:** evidence that a local 8B model can heal the v1→v2 change before building around it.
- Save the v2 `catalog.html` DOM. Prototype `healing/candidates.py` (extract and rank interactive elements, G9) and prompt v0 (THOUGHT/ACTION with few-shot).
- Run 20 trials per model (llama3.1:8b, qwen2.5-coder:7b). Record the correct-locator rate, the mean confidence, parse-failure rate and latency.
- Write up the findings in `docs/spikes/healer-spike.md` (a go/no-go on model, top-k and prompt shape).

**Exit:** ≥ 18/20 correct proposals with confidence ≥ 0.80 for the chosen model. Otherwise iterate on the prompt, or switch backend behind C1 before Phase 4.

### Phase 4 — Requirement Analyzer agent (M)
**Goal:** requirement text → validated `test_cases` (positive/negative/boundary).
- `agents/base.py` (C6), `agents/requirement_analyzer.py`, `prompts/requirement_analyzer.md` (few-shot, with the platform context from the Phase 1 README injected).
- Ollama `format` = the JSON schema of `list[TestCaseDraft]`, Pydantic validation, one repair re-prompt, then raise.
- Persist `requirements` + `test_cases`.
- Tests: a fake `LLMBackend` returns canned JSON (valid, invalid-then-repaired, invalid twice). `scripts/eval_analyzer.py` runs the live model on 3 requirements: the reference flow, "User can log in with valid credentials and sees an error with a wrong password", and "Payment is declined when the card is expired". A human reviews the output.

**Exit:** each of the 3 live requirements yields ≥1 positive and ≥1 negative case that mention only real products/cards.

### Phase 5 — Test Generator agent (M)
**Goal:** test case → a statically-valid Playwright script (C5).
- `autopilot-qa/tests-generated/support/fixtures.js` (`sel`, `urls`, failure capture of DOM + screenshot + `failure.json`, `actionTimeout` 5 s) and `playwright.config.js`.
- `agents/test_generator.py`, `prompts/test_generator.md` (template + selector-map keys + platform context), and `execution/script_validator.py` (G14 rules and unknown-key detection). On a validation failure it re-prompts once with the validator errors, then raises.
- Writes `tests-generated/<run_id>/<test_case_id>.spec.js` and persists `scripts` with `selector_map_version`.
- Tests: validator unit tests (good script, `require('fs')`, unknown key, raw selector string), generator with fake LLM.

**Exit:** for the reference-flow test case, the generated script passes validation and runs green against v1 when run by hand (`npx playwright test <file>`).

### Phase 6 — Test Executor agent + dry run (M)
**Goal:** run a script and produce an `executions` doc (§7.4), then a manual end-to-end dry run.
- `execution/playwright_runner.py`: `reset_all_services()` (POST `__reset` on all 5, fail fast with `root_cause: environment` if any is down), `run_script(path, selector_map_version) -> RunOutcome` (subprocess `npx playwright test --reporter=json`, env `APQA_SELECTOR_MAP`, `APQA_RUN_ID`, `APQA_SCRIPT_ID`), JSON-report parsing, `failure.json` pickup.
- `agents/test_executor.py`: runs the script; on failure re-runs it once unmodified to classify `flaky` (G11); persists `executions`.
- `cli.py dry-run "<requirement>"`: Analyzer → Generator → Executor in sequence, no Orchestrator.

**Exit:** `python -m cli dry-run "User can search for a product, add it to cart, and complete checkout."` prints test cases, script paths and pass/fail per script against v1.

### Phase 7 — Orchestrator, message bus & REST API (L)
**Goal:** supervisor-worker pipeline over the bus with retries, exposed through §8.
- `orchestrator/message_bus.py` (C3, tailable await cursor on `messages`).
- `orchestrator/coordinator.py`: task graph `analyze → generate(per test case) → execute(per script) → triage(per failure) → report`. Publishes `request`, awaits `result`/`error`, retries ≤2, then escalates (the run is marked `escalated` with the last error). Merges everything into a run report (`reports/<run_id>.json` and `runs` status).
- `main.py` FastAPI: every REST endpoint in §8. `POST /requirements` starts the run as a background task and returns `run_id` immediately. `POST /runs/{run_id}/execute` re-executes the existing scripts. The coverage doc (§7.7) is written at the end of the run.
- `docs/architecture.md` (sequence diagram of one run, the bus message trail).
- Tests: bus publish/subscribe ordering and filtering; coordinator with fake agents (happy path, a task that errors twice then succeeds, a task that errors 3× → escalation); API tests with `httpx.AsyncClient`.

**Exit:** `POST /requirements` for the reference flow completes, and every message for the `run_id` in `messages` reconstructs the run in order.

### Phase 8 — Triage & Healer agent (L) — **core feature**
**Goal:** explainable, threshold-gated self-healing, validated on the v1→v2 toggle.
- `healing/triage.py` (G12 deterministic classifier), `healing/candidates.py` (hardened from the spike), `healing/verify.py` (G10: loads the DOM snapshot in headless Chromium and counts matches in scope), `agents/triage_healer.py`, `prompts/healer.md`.
- The flow: classify → (locator failure) extract candidates → stream prompt → parser (C2). Each Thought → `ctx.emit_thought` (bus `thought` + `agent_traces`). Action → verify → gate on `HEALING_CONFIDENCE_THRESHOLD`.
  - ≥ threshold and verified: write `selector_maps` v N+1 (`created_by: healer`), re-run the script once with the new version, record `healing` on the execution. If the re-run still fails → bug report.
  - < threshold or unverified: `selector_maps` proposal `pending_review`, bug report `root_cause: "ui"`, note "possible benign UI change — manual confirmation required".
  - Non-locator failures: bug report with the triaged root_cause and severity rules (checkout/payment path = high).
- Every attempt is written to `agent_traces` (C7), including parse failures.
- Tests: triage table tests; candidate ranking on saved v1/v2 DOM fixtures; verify on fixtures (0 / 1 / many matches); healer with a fake streaming LLM (high confidence → applied, low confidence → bug report, garbage → confidence 0); threshold read from env.
- **Validation protocol:** 20 full runs with `CATALOG_BUTTON_VARIANT=v2` + 20 with v1 (control: the healer must not fire). Record the healing success rate and false-heal rate in `docs/spikes/healer-validation.md`.

**Exit / demo:** with v2 deployed, submitting the reference requirement ends in a healed pass. The THOUGHT lines are visible in `agent_traces`, and `selector_maps` v2 maps `catalog.addToCartButton → #add-to-bag-btn`. With the threshold set to 0.99, the same run produces a `ui` bug report and no heal.

### Phase 9 — WebSocket live feed & React dashboard (M)
**Goal:** watch a run live: pipeline status and the reasoning trace token-by-token.
- `websocket/live_feed.py`: `WS /ws/runs/{run_id}` replays past messages for the run and then tails the bus live.
- `frontend/` (Vite + React): `RequirementInput`, `PipelineView` (agent state per task from request/result/error), `ReasoningTrace` (thought stream, with a healer decision card showing confidence vs threshold), `TestCaseViewer`, `ExecutionResults` (screenshots, logs, healed badge), `BugReportPanel`, `CoverageDashboard` (Recharts: pass/fail/flaky/healed, healing confidence histogram).
- Tests: live_feed replay-then-tail test; component tests with Vitest + Testing Library for ReasoningTrace and PipelineView reducers.

**Exit / demo:** the full v2 demo run from the browser, with thoughts appearing live before the heal decision.

### Phase 10 — Evaluation harness (M)
**Goal:** reproducible numbers for the dissertation.
- `evaluation/seeded-bugs/registry.yaml` (G16), ≈10–15 entries across services, both labels:
  - benign: button id/text rename (the existing v2), label text change, element moved into a wrapper, class rename.
  - real defects: out-of-stock item addable, wrong cart total, expired card accepted, order total ≠ payment amount accepted, login accepts a wrong password.
  - The `SEEDED_CHANGES` env hook goes in the target services, plus the optional `__config` runtime toggle (G17).
- `evaluation/run_experiment.py`: for each registry entry × N trials, apply the toggle → reset → submit the requirement(s) → collect run_ids.
- `evaluation/metrics/compute_metrics.py` (reads Mongo):
  - requirement coverage % (requirements with ≥1 passing/executed test)
  - defect detection rate (real defects caught ÷ real defects seeded)
  - false bug-report rate (bug reports on benign changes that were healable)
  - **healing success rate** (benign changes healed to a pass ÷ benign changes)
  - **healing precision** (auto-applied heals that were correct ÷ auto-applied)
  - **confidence calibration**: Brier score, ECE and a reliability diagram (LLM-raw and post-verification)
  - time to first executable script vs the manual baseline, and mean LLM latency per agent.
- Baseline comparison: run `evaluation/manual-baseline` under each seeded change (it never heals; that is the control).

**Exit:** a single command reproduces the tables and charts in `evaluation/metrics/out/`.

---

## Part D — Testing & Workflow Conventions

- TDD per task (superpowers:test-driven-development). Commit after each green task.
- Node tests use `node:test` + global `fetch` (no extra deps). Python tests use pytest + pytest-asyncio. Browser E2E uses Playwright.
- LLM-dependent code is always unit-tested with a fake `LLMBackend`. Live-model checks are separate `scripts/eval_*.py` and are never part of `pytest`.
- Before claiming a phase done, run its exit command and paste the output (superpowers:verification-before-completion). Request a review at each phase boundary (superpowers:requesting-code-review).

## Part E — Risk Register (plan-level)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| 8B model can't heal reliably | Med | High | Phase 3b spike, candidate pre-ranking (G9), verification (G10), cloud backend behind C1 |
| Generator hallucinates selectors / invalid code | High | Med | Logical keys (G7), static validator + one repair prompt (G14) |
| CPU-only inference too slow for evaluation volume | Med | Med | Measure in 3b; reduce N; cache analyzer/generator outputs across trials (the healer is the variable under test) |
| Cross-service test-data drift | Med | Med | `__reset` on all services before every script (Phase 6) |
| Cookie split from `127.0.0.1` vs `localhost` | Med | Low | Only `localhost` in all URLs; documented in the target README |
| Capped `messages` evicts old runs | Low | Med | 64 MB cap; thoughts are also persisted to `agent_traces`; the report is exported per run |
| Headline feature late in schedule | Med | High | 3b spike gives an early fallback demo; Phase 8 is budgeted L |

## Part F — Spec Coverage Matrix

| Spec § | Covered in |
|--------|-----------|
| 1–3 summary/architecture | Parts A–C, Phase 7 `docs/architecture.md` |
| 4.1 Orchestrator | Phase 7 |
| 4.2 Requirement Analyzer | Phase 4 |
| 4.3 Test Generator | Phase 5 (+ C4, C5) |
| 4.4 Test Executor | Phase 6 |
| 4.5 Triage & Healer | Phase 3b, Phase 8 |
| 5 Message protocol | C3, Phase 7 |
| 6.1–6.5 Services | Phase 1 Tasks 2–6 |
| 6.6 Variant toggle | Phase 1 Task 3, Task 8 |
| 6.7 Reference flow | Phase 1 Task 8, Phases 6/8 exits |
| 6.8 `__reset` | Phase 1 (every service), Phase 6 runner |
| 7 Data model | Phase 2 (+ G8 `selector_maps`) |
| 8 API | Phase 7 (REST), Phase 9 (WS) |
| 9 Self-healing design | Phase 8 (+ C2, C7, G9–G12) |
| 10 Tech stack | Global header; G15 deviation on LangGraph/CrewAI |
| 11 Directory structure | Phase 0 + each phase's files |
| 12 Local setup / env | Phase 1 Task 7, Phase 2 settings |
| 13 Build order | Phases 1–10 (+3b inserted) |
| 14 Out of scope | Global Constraints |
