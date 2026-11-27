# Phase 8 — Triage & Healer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explainable, threshold-gated self-healing (spec §4.5, §9). A failed execution is triaged deterministically. Locator failures go through a healing engine that streams THOUGHT lines live, verifies the proposal, and applies it only at confidence ≥ `HEALING_CONFIDENCE_THRESHOLD`; the script is then re-run once on a new selector-map version. Everything else, or a low-confidence proposal, becomes a structured bug report.

**Architecture:**
- `healing/triage.py`: classifies a failure from the real Playwright error text (captured in Phase 8 research), finds the broken selector, its scope and `hasText` filter, and maps the selector back to a selector-map key.
- `healing/engine.py`: `propose_heal()` runs candidate extraction (3b) → prompt v1 → stream + `ThoughtActionParser` → deterministic verification (not the failing locator; unique in scope; generic across sibling containers) → **one feedback round** if verification fails → threshold gate. The spike runner reuses it, so the evaluation measures the real code path.
- `agents/triage_healer.py`: triage → bug report, or engine → new `selector_maps` version (active) or a `pending_review` proposal plus a bug report. Every thought and the final decision go to `agent_traces` and the bus.
- Coordinator: `healed` → re-run the script once with the new version (`check_flaky: false`, `healing` attached). If that re-run fails too, a second triage with `after_heal: true` produces the bug report.
- Executor: defaults to the **latest active** selector map, so one heal benefits every later script in the run.

**Tech Stack:** Phase 3 parser, Phase 3b candidates/verifier, Phase 7 coordinator.

## Global Constraints

- Output format: `THOUGHT:` lines, then `ACTION: {"candidate": n|null, "new_locator": "...", "confidence": 0-1, "basis": "..."}`.
- Gate: applied only if verified **and** confidence ≥ threshold (default 0.80, env `HEALING_CONFIDENCE_THRESHOLD`). A below-threshold result is never auto-applied: it's stored as a `pending_review` proposal, with a bug report `root_cause: "ui"` whose note says "possible benign UI change requiring manual confirmation".
- Every healing attempt, applied or not, is logged to `agent_traces` with the decision (contract C7) as `action`.
- Triage mapping: `locator_missing` → healable, `ui`. `element_state` (resolved but not enabled/visible) → `ui`. `assertion` (value mismatch) → `data`. `navigation` (`toHaveURL`) → `api`. `environment` (connection refused / service down) → `environment`. `script_error` → `ui`.
- Severity: `environment` → high; order/payment pages or keys → high; everything else → medium.
- Healer model: `Settings.healer_model`. Options: temperature 0.2, `num_ctx` 4096. At most 2 LLM rounds per attempt.
- The prompt examples must not reuse the spike's decoy ("Add to Wishlist"), which stays held out for evaluation.

## File Map

```
autopilot-qa/backend/
  healing/triage.py                 Triage, classify, severity
  healing/verify.py                 + DomVerifier.scope_counts
  healing/prompt.py                 + versions, feedback
  healing/engine.py                 HealDecision, propose_heal
  prompts/healer_v1.md
  db/selector_maps.py               + derive
  agents/triage_healer.py           TriageHealer
  agents/test_executor.py           latest-active default, payload "healing"
  orchestrator/coordinator.py       heal -> re-run -> after_heal triage; registers TriageHealer
  scripts/healer_spike.py           + --mode engine / --prompt-version
  tests/healing/test_triage.py  tests/healing/test_engine.py  tests/agents/test_triage_healer.py
  (updated) tests/healing/test_prompt.py  tests/healing/test_verify.py  tests/agents/test_test_executor.py
            tests/orchestrator/test_coordinator.py  tests/db/test_selector_maps.py
```

The code for every file lives in the repository; each task below lists the tests that pin the behaviour and the commands to run. This deviates from the one-plan-holds-all-code rule deliberately, because by Phase 8 the plan would otherwise duplicate ~900 lines that are reviewed in the diff anyway.

---

### Task 1: Triage

**Interfaces — Produces:** `Triage(category, root_cause, healable, broken_selector, scope_selector, scope_text, selector_key, service, detail)` with `severity`, `summary()`, `as_dict()`; `classify(failure: dict, services: dict) -> Triage`.

- [ ] Tests (`tests/healing/test_triage.py`) use the real messages: click on a missing scoped locator; fill on a missing locator; click on a disabled element; `toHaveText`/`toBeVisible` with `element(s) not found`; text mismatch with `Received:`; `toHaveURL`; `net::ERR_CONNECTION_REFUSED`; `failure.kind == "environment"`; key lookup using the chain `.product-card` + `hasText` → `catalog.addToCartButton`; `last_selector_key` tie-break; unknown selector → `selector_key=None` (not healable); severity.
- [ ] Implement `healing/triage.py`; run `pytest tests/healing/test_triage.py -q`.

### Task 2: Verifier genericity and prompt v1

- [ ] `DomVerifier.scope_counts(html, locator, scope_selector) -> list[int]` (matches per scope container, in DOM order). Test: `#add-to-bag-btn` → six 1s on v2; `button[data-product-id='p-100']` → one 1 and five 0s.
- [ ] `prompts/healer_v1.md` and `render_healer_prompt(failure, extraction, *, version="v1", feedback=None)`, where `feedback=(locator, reason)` renders a "was rejected" paragraph. The v0 template keeps working (spike reproducibility). Tests: v1 includes candidate numbers, the "never propose it again" line, the `"candidate":` ACTION format, no unfilled placeholders, and the feedback paragraph only when given. Existing v0 assertions keep passing with `version="v0"`.

### Task 3: Healing engine

**Interfaces — Produces:** `HealDecision` (C7 fields + `candidate`, `rounds`, `thoughts`, `parse_errors`, `threshold`, `model`, `prompt_version`) with `as_action()`; `async propose_heal(llm, verifier, failure, html, *, selector_key, model, threshold, options=None, on_thought=None, max_rounds=2, prompt_version="v1") -> HealDecision`.

Verification rules: reject the failing locator itself. The target must be unique in scope. With a scope: no container may have more than 1 match, and when several containers exist, at least 2 must match (so a locator tied to one item fails).

- [ ] Tests (`tests/healing/test_engine.py`, FakeLLM streams + real Chromium on the fixtures): correct first answer → applied, thoughts forwarded to the sink; old locator → feedback → corrected → rounds 2; product-specific locator → "not generic" → null → not applied; null → not applied; verified but confidence 0.7 → not applied; unparseable twice → not applied with parse errors; confident decoy pick → verified and applied (this documents that verification alone cannot catch a wrong-purpose control; only the LLM can).
- [ ] Implement `healing/engine.py`.

### Task 4: Selector-map derivation, executor and coordinator changes

- [ ] `derive(parent, service, element, selector, *, created_by="healer", status="active", run_id=None, version=None) -> SelectorMap`. It deep-copies the parent, replaces one selector, records `healed_from`, and sets `parent_version`. Test: the parent is untouched and the child has v+1.
- [ ] Executor: `version = payload.selector_map_version or latest_active(db).version`; `payload.healing` is stored on the `Execution`. Test: with an active v2 and no payload version → v2; healing stored.
- [ ] Coordinator: on `{"outcome": "healed", "selector_map_version": v, "decision": d}` → `execute(script, selector_map_version=v, check_flaky=False, healing=d | {"healed_from_execution_id": …})`. If that is not a pass → `triage(execution, after_heal=True)`. `default_agents()` registers `TriageHealer`. Tests: healed → re-run payload; re-run fail → after_heal triage.

### Task 5: Triage & Healer agent

**Interfaces — Produces:** `TriageHealer(verifier_factory=DomVerifier)`. `handle` takes payload `{"execution_id", "script_id", "after_heal"?}` and returns `{"outcome": "healed", "selector_map_version", "decision"}` or `{"outcome": "bug_report", "bug_report_id", "root_cause", "severity", "decision"?}`.

- [ ] Tests (`tests/agents/test_triage_healer.py`, db + FakeLLM + real DOM fixture on disk): heal → v2 active with `#add-to-bag-btn` and `healed_from`, traces = triage line + THOUGHT lines + ACTION line with `action.applied`; low confidence → `pending_review` proposal + `ui` bug report with the manual-confirmation note; assertion mismatch → `data` bug report without an LLM call; environment → `environment`/high; `after_heal` → bug report; already healed in a newer map → reuse without an LLM call; missing DOM snapshot → bug report.
- [ ] Implement `agents/triage_healer.py`.

### Task 6: Engine spike (validation)

- [ ] `scripts/healer_spike.py --mode engine --prompt-version v1` runs `propose_heal` (the product code path, max 2 rounds) and judges with the same independent target/other-card checks as the v0 spike.
- [ ] Run 20 trials × {v2_renamed, decoy_wishlist} for llama3.1:8b and llama3.2:3b. Compare with the v0 single-shot run in `docs/spikes/healer-spike.md`.

### Task 7: Live end-to-end demo

- [ ] Platform on **v2**, API up, submit the reference requirement. Expected: executions `fail` → triage `locator_missing` → THOUGHT lines in `agent_traces` → selector map v2 `catalog.addToCartButton → #add-to-bag-btn` → re-run `pass` with `healing` set.
- [ ] Same with `HEALING_CONFIDENCE_THRESHOLD=0.99` → no heal and a `ui` bug report with the manual-confirmation note.

## Phase 8 Exit Checklist

- [ ] `pytest -q` green.
- [ ] Engine spike results recorded; the chosen model meets ≥ 18/20 on v2 and the decoy false-heal rate is reported.
- [ ] Live v2 demo heals; the threshold-0.99 demo produces a bug report instead.

---

## Execution Notes (2026-09-27)

**Live v2 demo (default threshold 0.80):** script fails on `#add-to-cart-btn` → triage `locator_missing at
catalog.addToCartButton` → THOUGHT streamed over the WebSocket → `ACTION: applied catalog.addToCartButton ->
#add-to-bag-btn (confidence 0.85 >= 0.80); selector map v2` → re-run on v2 **passes** with `healing` recorded. The next
script ran directly on v2 and passed (latest-active default). The whole heal took about 60 s on CPU.

**Live threshold-0.99 demo:** "ACTION: not applied (confidence 0.85 below threshold 0.99)", a `pending_review`
proposal, the active map stays v1, and a `ui` bug report reading "Possible benign UI change requiring manual
confirmation…".

Found live and fixed:
- **Triage rule order:** a real assertion message carries a call log with "waiting for locator … resolved to", which
  was classified `script_error`. Assertions are now checked first, with a regression test built from the real message.
- **Transactional healing** was added: a heal whose re-run fails is rolled back.
- `HEALER_PROMPT_VERSION` setting (v1 default, v2 selectable); spike results in `docs/spikes/healer-spike.md`.

Known follow-up: identical below-threshold proposals from several scripts are stored twice; they should be deduplicated.
