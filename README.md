# AutoPilot-QA

Multi-agent QA framework (local LLM via Ollama) that tests a 5-microservice e-commerce app and self-heals broken UI locators with a logged reasoning trace.

- `autopilot-qa/` — the framework (FastAPI backend, React dashboard)
- `target-platform/` — ShopLite, the sample app under test (see its README)
- `evaluation/` — seeded bugs, manual baseline suite, metrics
- `docs/` — specification and implementation plans (`docs/superpowers/plans/`)
