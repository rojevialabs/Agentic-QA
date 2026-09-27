# AutoPilot-QA — End-to-End Engineering Specification

**Purpose of this document**: this is a complete, self-contained build spec for AutoPilot-QA, intended to be handed to an AI coding agent (Claude Code) to scaffold and implement the project end to end. It covers the multi-agent framework itself, the sample e-commerce target platform it tests, the data model, the communication protocol, and a suggested build order.

---

## 1. Project Summary

AutoPilot-QA is a multi-agent framework that automates the software QA lifecycle for a small e-commerce platform built as independent microservices. Given a plain-English requirement, it autonomously generates test cases, writes automation scripts, executes them across service boundaries, and **self-heals** when the UI underneath it changes — all coordinated by an orchestrator agent, running entirely on a free local LLM (Ollama), with no per-token inference cost and no data leaving the local environment.

**The headline capability, and the primary focus of the whole project, is explainable self-healing.** When a locator breaks (e.g. a button renamed during an independent microservice deploy), the healing agent does not fail silently or guess — it reasons about the current page, proposes a replacement element, scores its own confidence, and logs the full reasoning trace for audit. Every other component exists to make this capability demonstrable and measurable.

**One-line pitch**: *AutoPilot-QA tests a microservices e-commerce platform end-to-end, and heals itself — transparently, with logged reasoning — when any one service changes underneath it.*

---

## 2. Two Things Being Built

This spec covers **two separate codebases** that live in one repository:

1. **`autopilot-qa/`** — the actual multi-agent QA framework (the dissertation project).
2. **`target-platform/`** — a small, deliberately simple e-commerce app built as 5 independent microservices, which exists *only* to be tested by AutoPilot-QA. It is not meant to be production-quality; it exists to make self-healing and cross-service test flows demonstrable.

Do not over-build the target platform. It should be just complex enough to support the flows described in Section 6.

---

## 3. High-Level Architecture

```
Web dashboard (React)  <-->  Orchestrator agent
Orchestrator --> Requirement Analyzer  --> Local LLM (Ollama)
Orchestrator --> Test Generator        --> Local LLM (Ollama)
Orchestrator --> Test Executor         --> Local LLM (Ollama)
Orchestrator --> Triage & Healer        --> Local LLM (Ollama)
Test Executor --> Target Platform (5 independent microservices)
All agents    --> MongoDB (message bus + artifact storage)
```

Layers:
- **Presentation** — React dashboard, WebSocket live feed of agent status and reasoning trace
- **Orchestration** — Coordinator agent, task queue, shared message bus
- **Agent layer** — 4 specialist agents, each a thin wrapper around a role-specific prompt + tool access
- **Inference** — Ollama (local), pluggable to a cloud LLM (Groq/Gemini) via a backend interface if local quality proves insufficient
- **Execution** — Playwright driving the target platform's microservices
- **Persistence** — MongoDB (documents, not rows — every core artifact is a variably-shaped, LLM-generated JSON object)

---

## 4. Agent Specifications

### 4.1 Orchestrator Agent
- **Role**: receives a requirement, builds a task plan, delegates to specialist agents in sequence, retries a failed task up to 2 times before escalating, merges all outputs into one run report.
- **Pattern**: supervisor-worker over the shared message bus (Section 5).
- **Input**: requirement text, microservices config (service URLs/ports)
- **Output**: task graph, aggregated run report

### 4.2 Requirement Analyzer Agent
- **Role**: parses a plain-English requirement into structured test scenarios (positive, negative, boundary).
- **Prompt strategy**: few-shot, JSON-schema-constrained output (see 6.4 for example requirements to test against).
- **Input**: requirement text
- **Output**: array of test case objects (schema in 7.2)

### 4.3 Test Generator Agent
- **Role**: converts each test case into an executable Playwright script.
- **Prompt strategy**: template-guided code generation, given the target service's known selector map as context to reduce hallucinated selectors.
- **Input**: test case objects, per-service selector map
- **Output**: runnable Playwright script (`.spec.js`) per test case

### 4.4 Test Executor Agent
- **Role**: runs the generated script against the live target platform — potentially driving a flow across multiple microservices in one script — and captures pass/fail, logs, and screenshots.
- **Input**: script path
- **Output**: execution result object (schema in 7.4)

### 4.5 Triage & Healer Agent
- **Role**: on a failure, first attempts self-healing (find the nearest matching element by text/attributes/position, with a confidence score). Only raises a structured bug report when the failure cannot be attributed to a benign interface change, or when confidence is below the threshold.
- **Confidence threshold**: 0.80 (configurable via env var `HEALING_CONFIDENCE_THRESHOLD`). Below threshold → flag for manual review, do not auto-apply.
- **Input**: failure logs, screenshot, DOM snapshot
- **Output**: `{ healed_locator, confidence, basis }` OR a bug report object (schema in 7.5)
- **Reasoning trace**: every healer decision must be prompted to emit `THOUGHT:` (plain reasoning) before `ACTION:` (JSON decision), streamed token-by-token from Ollama and parsed server-side into `type: "thought"` messages (see Section 5).

---

## 5. Inter-Agent Communication Protocol

All agents communicate through a shared message bus, implemented as a **MongoDB capped collection** (`messages`), coordinated by the Orchestrator (supervisor-worker pattern). This makes every exchange logged and queryable rather than fired-and-forgotten — essential for the explainability goal and for reconstructing a full run during evaluation.

**Message schema:**
```json
{
  "message_id": "uuid",
  "run_id": "uuid",
  "from_agent": "test_generator",
  "to_agent": "orchestrator",
  "task_id": "uuid",
  "type": "request | result | error | thought",
  "payload": { "...": "..." },
  "timestamp": "iso8601"
}
```

- `type: "thought"` — streamed reasoning-trace lines (used heavily by the Healer)
- `type: "result"` — final structured output of an agent's task
- `type: "error"` — triggers the Orchestrator's retry logic (max 2 retries per task, then escalate)

Every message carries `run_id` and `task_id` so a complete run can be reconstructed end-to-end.

---

## 6. Target Platform: Sample E-Commerce Microservices App

A deliberately minimal e-commerce app, split into 5 independently deployable services. Each service is a separate process with its own port, its own in-memory data store (no shared DB — this is what makes them "independent"), and a plain server-rendered HTML page for Playwright to drive (no SPA framework needed here — keep it simple and fast to scaffold).

| Service | Port | Purpose |
|---|---|---|
| `auth-service` | 4001 | Register / login |
| `catalog-service` | 4002 | Browse & search products |
| `cart-service` | 4003 | Add/remove items from cart |
| `order-service` | 4004 | Checkout, order history |
| `payment-service` | 4005 | Mock payment processing |

### 6.1 Auth Service
- `POST /api/register` — `{ email, password }`
- `POST /api/login` — `{ email, password }` → returns a session token (simple in-memory session map is fine)
- `GET /login.html` — email + password fields, "Log in" button (`id="login-btn"`)
- `GET /register.html` — same pattern

### 6.2 Catalog Service
- `GET /api/products` — returns seeded product list (include at least one **out-of-stock** product for the negative test case)
- `GET /api/products/:id`
- `GET /catalog.html` — lists products, each with an **"Add to Cart" button**. This button is the deliberate seed point for the self-healing demo (see 6.5).

### 6.3 Cart Service
- `POST /api/cart/add` — `{ productId, qty }`
- `GET /api/cart`
- `DELETE /api/cart/:itemId`
- `GET /cart.html` — cart contents + "Proceed to Checkout" button

### 6.4 Order Service
- `POST /api/orders` — creates an order from the current cart, calls Payment Service
- `GET /api/orders/:id`
- `GET /checkout.html` — shipping details form + "Place Order" button

### 6.5 Payment Service
- `POST /api/payments` — `{ cardNumber, expiry, amount }`. Simulate failure if `expiry` is in the past (this is the **expired payment card** negative test case).
- `GET /payment.html` — card number, expiry, "Pay" button

### 6.6 Seeded UI-Change Mechanism (for the self-healing demo)

The Catalog service's "Add to Cart" button must support a **feature-flag toggle** that simulates an independent deploy changing the button, without changing its underlying function:

```
Env var: CATALOG_BUTTON_VARIANT=v1 | v2
v1 (default): <button id="add-to-cart-btn">Add to Cart</button>
v2 (seeded change): <button id="add-to-bag-btn">Add to Bag</button>
```

Toggling this env var and restarting the Catalog service is how the self-healing demo is triggered: a script written against `v1` will fail against `v2` until the Healer agent adapts.

### 6.7 Reference End-to-End Flow

The canonical example flow used throughout the dissertation (slides, reports, and the evaluation suite) is:

> Requirement: *"User can search for a product, add it to cart, and complete checkout."*

This flow crosses **Catalog → Cart → Order → Payment** in one script, which is exactly the kind of cross-service flow that makes self-healing meaningful (a change in one service must not silently break the whole flow).

### 6.8 Seed Data Reset

Each service must expose a `POST /api/__reset` (test-only, never exposed in a "production" mental model) that resets its in-memory store to a known seed state. The Executor calls this before every run to avoid cross-run test-data drift (see Risk: "Cross-service test data & environment drift" in the dissertation's risk register).

---

## 7. Data Model (MongoDB)

Database: `autopilot_qa`. All collections other than `messages` (Section 5) are standard collections.

### 7.1 `requirements`
```json
{ "_id": "ObjectId", "text": "...", "submitted_at": "iso8601", "run_id": "uuid" }
```

### 7.2 `test_cases`
```json
{
  "_id": "ObjectId", "run_id": "uuid", "requirement_id": "ObjectId",
  "type": "positive | negative | boundary",
  "title": "...", "steps": ["..."], "expected_result": "...",
  "generated_at": "iso8601"
}
```

### 7.3 `scripts`
```json
{ "_id": "ObjectId", "test_case_id": "ObjectId", "language": "playwright-js", "code": "...", "selector_map_version": 1 }
```

### 7.4 `executions`
```json
{
  "_id": "ObjectId", "script_id": "ObjectId",
  "status": "pass | fail | flaky",
  "duration_ms": 0, "screenshot_path": "...", "logs": ["..."],
  "executed_at": "iso8601"
}
```

### 7.5 `bug_reports`
```json
{
  "_id": "ObjectId", "execution_id": "ObjectId",
  "steps_to_reproduce": ["..."], "expected": "...", "actual": "...",
  "root_cause": "ui | api | data | environment",
  "severity": "low | medium | high", "created_at": "iso8601"
}
```

### 7.6 `agent_traces`
```json
{ "_id": "ObjectId", "run_id": "uuid", "agent": "healer", "thought": "...", "action": {}, "timestamp": "iso8601" }
```

### 7.7 `coverage`
```json
{ "_id": "ObjectId", "run_id": "uuid", "requirement_id": "ObjectId", "has_test": true, "last_status": "pass" }
```

---

## 8. API Specification (FastAPI)

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/requirements` | submit a new requirement, triggers the Orchestrator |
| GET | `/runs/{run_id}` | full run status + all artifacts |
| GET | `/runs/{run_id}/test-cases` | generated test cases |
| GET | `/runs/{run_id}/scripts` | generated scripts |
| POST | `/runs/{run_id}/execute` | trigger execution |
| GET | `/runs/{run_id}/results` | execution results |
| GET | `/runs/{run_id}/bug-reports` | triage output |
| GET | `/runs/{run_id}/coverage` | coverage matrix |
| WS | `/ws/runs/{run_id}` | live agent status + reasoning-trace stream |

---

## 9. Self-Healing Design (the core feature — build this carefully)

Every agent prompt that can produce a healing decision must separate reasoning from action:

```
THOUGHT: <1-2 sentences, plain reasoning>
ACTION: {"new_locator": "...", "confidence": 0.0-1.0, "basis": "..."}
```

Implementation notes:
- Call Ollama with `"stream": true`.
- Parse the stream server-side, splitting on the `THOUGHT:` / `ACTION:` markers.
- Push each completed `THOUGHT` line to the frontend immediately via WebSocket (`type: "thought"` message) **and** persist it to `agent_traces`.
- If `confidence >= HEALING_CONFIDENCE_THRESHOLD` (default 0.80): apply the new locator and retry the failed step once.
- If `confidence < threshold`: do **not** auto-apply. Instead raise a bug report with `root_cause: "ui"` and a note that it may be a benign UI change requiring manual confirmation.
- Log every healing attempt (successful or not) to `agent_traces`, since this data feeds the dissertation's evaluation metrics (healing success rate, healing confidence accuracy).

---

## 10. Tech Stack

| Layer | Choice |
|---|---|
| LLM | Ollama (local) — `llama3.1:8b` or `qwen2.5-coder:7b` |
| Agent orchestration | LangGraph or CrewAI (Python) |
| Backend API | FastAPI |
| Database | MongoDB (Motor async driver) |
| Frontend (AutoPilot-QA dashboard) | React (Vite) |
| Real-time | WebSockets |
| Test execution | Playwright |
| Charts | Recharts |
| Target platform services | Node.js + Express, server-rendered HTML (no frontend framework needed) |

---

## 11. Directory Structure

```
autopilot-qa-project/
├── autopilot-qa/                       # the dissertation framework itself
│   ├── backend/
│   │   ├── main.py                     # FastAPI entrypoint
│   │   ├── orchestrator/
│   │   │   ├── coordinator.py          # task delegation, sequencing, retries
│   │   │   └── message_bus.py          # MongoDB capped-collection pub/sub
│   │   ├── agents/
│   │   │   ├── requirement_analyzer.py
│   │   │   ├── test_generator.py
│   │   │   ├── test_executor.py
│   │   │   └── triage_healer.py
│   │   ├── llm/
│   │   │   └── ollama_client.py        # streaming client, pluggable backend interface
│   │   ├── db/
│   │   │   └── mongo_client.py         # Motor connection + collection helpers
│   │   ├── execution/
│   │   │   └── playwright_runner.py
│   │   ├── websocket/
│   │   │   └── live_feed.py            # streams agent status + thoughts to frontend
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   ├── RequirementInput.jsx
│   │   │   │   ├── PipelineView.jsx
│   │   │   │   ├── ReasoningTrace.jsx
│   │   │   │   ├── TestCaseViewer.jsx
│   │   │   │   ├── ExecutionResults.jsx
│   │   │   │   ├── BugReportPanel.jsx
│   │   │   │   └── CoverageDashboard.jsx
│   │   │   └── App.jsx
│   │   ├── package.json
│   │   └── vite.config.js
│   │
│   ├── tests-generated/                # output: agent-written scripts land here
│   ├── reports/                        # output: bug reports, coverage exports
│   └── docs/
│       └── architecture.md
│
├── target-platform/                    # the sample app under test (NOT production code)
│   ├── auth-service/
│   │   ├── server.js
│   │   ├── views/login.html
│   │   ├── views/register.html
│   │   └── package.json
│   ├── catalog-service/
│   │   ├── server.js
│   │   ├── views/catalog.html
│   │   ├── seed-data.json
│   │   └── package.json
│   ├── cart-service/
│   │   ├── server.js
│   │   ├── views/cart.html
│   │   └── package.json
│   ├── order-service/
│   │   ├── server.js
│   │   ├── views/checkout.html
│   │   └── package.json
│   ├── payment-service/
│   │   ├── server.js
│   │   ├── views/payment.html
│   │   └── package.json
│   └── docker-compose.yml              # spins up all 5 services + MongoDB together
│
├── evaluation/
│   ├── seeded-bugs/                    # config for deliberately seeded defects
│   ├── manual-baseline/                # hand-written Selenium/Playwright suite for comparison
│   └── metrics/
│       └── compute_metrics.py          # coverage %, defect detection, healing success rate, etc.
│
├── docs/
│   └── AutoPilot-QA-Technical-Specification.md
│
└── README.md
```

---

## 12. Local Setup

1. `ollama pull llama3.1:8b` (or `qwen2.5-coder:7b`)
2. `docker run -d -p 27017:27017 mongo` (or use `docker-compose.yml` in `target-platform/` which should also start Mongo)
3. Target platform: `cd target-platform && docker-compose up` (all 5 services + Mongo)
4. AutoPilot-QA backend: `cd autopilot-qa/backend && pip install -r requirements.txt --break-system-packages && uvicorn main:app --reload`
5. AutoPilot-QA frontend: `cd autopilot-qa/frontend && npm install && npm run dev`
6. Confirm Ollama is reachable at `localhost:11434` before submitting a requirement

**Environment variables** (`.env` for the backend):
```
MONGO_URI=mongodb://localhost:27017
OLLAMA_URL=http://localhost:11434
HEALING_CONFIDENCE_THRESHOLD=0.80
AUTH_SERVICE_URL=http://localhost:4001
CATALOG_SERVICE_URL=http://localhost:4002
CART_SERVICE_URL=http://localhost:4003
ORDER_SERVICE_URL=http://localhost:4004
PAYMENT_SERVICE_URL=http://localhost:4005
```

---

## 13. Suggested Build Order

Build in this order — each phase should be independently runnable/demoable before moving to the next:

1. **Target platform first.** Scaffold all 5 microservices with the endpoints and HTML pages in Section 6. Get the full manual flow (search → cart → checkout → pay) working by clicking through it in a browser, including the `CATALOG_BUTTON_VARIANT` toggle and the `/api/__reset` endpoints. Nothing in AutoPilot-QA can be tested without this working first.
2. **MongoDB + data model.** Stand up the 7 collections from Section 7, plus the `messages` capped collection from Section 5.
3. **Ollama integration.** A minimal `ollama_client.py` that can do a non-streaming call and a streaming call, tested standalone before wiring into any agent.
4. **Requirement Analyzer agent**, in isolation: given a requirement string, produce structured test cases (Section 7.2 schema). Verify manually against 2-3 example requirements before continuing.
5. **Test Generator agent**: turn a test case into a Playwright script targeting the target platform.
6. **Test Executor agent**: run that script, capture pass/fail. At this point, do a full manual dry run: requirement in → script out → executed → result, without the Orchestrator yet.
7. **Orchestrator + message bus**: wire the above three agents together with retries and the message schema from Section 5.
8. **Triage & Healer agent**, including the `THOUGHT:`/`ACTION:` prompt pattern and confidence-threshold gating. Test it specifically against the `CATALOG_BUTTON_VARIANT` toggle — this is the core self-healing demo and should be validated thoroughly before moving on.
9. **WebSocket live feed + React dashboard.** Build the pipeline view and reasoning-trace panel last, once the backend pipeline actually produces the events they're meant to display.
10. **Evaluation harness**: seeded-bugs config, manual baseline suite, and `compute_metrics.py`.

---

## 14. Explicitly Out of Scope

Do not build these — they are future work, not part of this dissertation:
- Commercial packaging, licensing, or a pricing model
- Enterprise SSO / RBAC / audit logging beyond the agent-trace logging already specified
- CI/CD marketplace plugin packaging
- Visual regression (screenshot diffing) testing
- Self-healing for API-level contract drift (only UI-locator healing is in scope for this dissertation)
- Making `target-platform/` production-grade in any way — it exists solely to be tested

---

## 15. Key Terms Glossary

- **Run** — one end-to-end execution of the pipeline for a single requirement, identified by `run_id`.
- **Healing** — the Triage & Healer agent's act of finding and applying a replacement locator when the original one is not found.
- **Confidence** — a 0.0–1.0 score the Healer assigns to a proposed replacement locator; below `HEALING_CONFIDENCE_THRESHOLD` it is not auto-applied.
- **Reasoning trace** — the sequence of `THOUGHT:` lines an agent emits before its `ACTION:`, logged to `agent_traces` and streamed live to the dashboard.
- **Target platform** — the 5-microservice sample e-commerce app that AutoPilot-QA tests; not part of the framework itself.
