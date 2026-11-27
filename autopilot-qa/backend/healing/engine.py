"""Healing engine (spec §9): candidates -> streamed THOUGHT/ACTION -> deterministic verification -> threshold gate.

Shared by the Triage & Healer agent and the evaluation spike, so the numbers measure the real code path.
"""
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from healing.candidates import extract_candidates
from healing.prompt import PROMPT_VERSION, HealingFailure, render_healer_prompt
from healing.verify import DomVerifier
from llm.ollama_client import LLMBackend
from llm.trace_parser import Action, ParseError, Thought, ThoughtActionParser

ThoughtSink = Callable[[str], Awaitable[None]]


@dataclass
class HealDecision:
    """One healing attempt (roadmap contract C7). `confidence` is the gated value: 0 unless verified."""

    selector_key: str | None
    broken_selector: str
    new_locator: str | None = None
    candidate: int | None = None
    confidence: float = 0.0
    llm_confidence: float | None = None
    basis: str = ""
    verified: bool = False
    verification: str = ""
    applied: bool = False
    threshold: float = 0.8
    rounds: int = 0
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    thoughts: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    def as_action(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "thoughts"}


def _probability(value: Any) -> float | None:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def verify_proposal(verifier: DomVerifier, html: str, failure: HealingFailure, locator: str) -> tuple[bool, str]:
    """Deterministic checks a proposal must pass before its confidence counts (roadmap G10)."""
    if locator.strip() == failure.broken_selector.strip():
        return False, "it is the failing locator itself"
    target = await verifier.check(html, locator, scope_selector=failure.scope_selector, scope_text=failure.scope_text)
    if target.error:
        return False, f"it cannot be used here ({target.error})"
    if not target.unique:
        return False, f"it matches {target.count} elements in the scope, and exactly 1 is required"
    tag = target.matches[0]["tag"]
    if not failure.scope_selector:
        return True, f"1 match on the page; tag={tag}"
    counts = await verifier.scope_counts(html, locator, failure.scope_selector)
    if max(counts) > 1 or (len(counts) > 1 and sum(counts) < 2):
        return False, (f"it is not generic: it matches {counts} elements across the {len(counts)} "
                       f"'{failure.scope_selector}' containers, but must work in each of them")
    return True, f"1 match in scope; matches in {sum(counts)} of {len(counts)} '{failure.scope_selector}' containers; tag={tag}"


async def propose_heal(llm: LLMBackend, verifier: DomVerifier, failure: HealingFailure, html: str, *,
                       selector_key: str | None, model: str | None, threshold: float,
                       options: dict[str, Any] | None = None, on_thought: ThoughtSink | None = None,
                       max_rounds: int = 2, prompt_version: str = PROMPT_VERSION) -> HealDecision:
    extraction = extract_candidates(html, broken_selector=failure.broken_selector, description=failure.description,
                                    scope_selector=failure.scope_selector, scope_text=failure.scope_text)
    decision = HealDecision(selector_key=selector_key, broken_selector=failure.broken_selector, threshold=threshold,
                            model=model, prompt_version=prompt_version)
    feedback: tuple[str, str] | None = None

    for round_ in range(1, max_rounds + 1):
        decision.rounds = round_
        prompt = render_healer_prompt(failure, extraction, version=prompt_version, feedback=feedback)
        parser, events = ThoughtActionParser(), []

        async def take(new_events: list) -> None:
            for event in new_events:
                events.append(event)
                if isinstance(event, Thought):  # forwarded as soon as the line is complete
                    decision.thoughts.append(event.text)
                    if on_thought is not None:
                        await on_thought(event.text)

        async for chunk in llm.stream(prompt, model=model, options=options):
            await take(parser.feed(chunk))
        await take(parser.finish())

        decision.parse_errors += [e.reason for e in events if isinstance(e, ParseError)]
        action = next((e.data for e in events if isinstance(e, Action)), None)
        if action is None:
            decision.verification = "the answer did not follow the THOUGHT/ACTION format"
            feedback = ("(no ACTION)", "the answer did not follow the THOUGHT/ACTION format")
            continue

        locator = action.get("new_locator")
        decision.new_locator = locator.strip() if isinstance(locator, str) and locator.strip() else None
        decision.llm_confidence = _probability(action.get("confidence"))
        decision.candidate = _integer(action.get("candidate"))
        decision.basis = str(action.get("basis") or "")
        decision.confidence, decision.verified, decision.applied = 0.0, False, False
        if decision.new_locator is None:
            decision.verification = "the model found no candidate that does the same job"
            break

        decision.verified, decision.verification = await verify_proposal(verifier, html, failure, decision.new_locator)
        if decision.verified:
            decision.confidence = decision.llm_confidence or 0.0
            decision.applied = decision.confidence >= threshold
            break
        feedback = (decision.new_locator, decision.verification)
    return decision
