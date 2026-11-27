"""Renders the healer's THOUGHT/ACTION prompt from a failure and its candidate elements."""
from dataclasses import dataclass
from string import Template

from healing.candidates import Candidate, Extraction
from settings import BACKEND_DIR

PROMPT_VERSION = "v1"  # v0 is kept for reproducing the Phase 3b spike


@dataclass(frozen=True)
class HealingFailure:
    page_url: str
    broken_selector: str
    description: str
    scope_selector: str | None = None
    scope_text: str | None = None

    def scope_phrase(self) -> str:
        if not self.scope_selector:
            return "the whole page"
        phrase = f'the element matching "{self.scope_selector}"'
        return phrase + (f' that contains the text "{self.scope_text}"' if self.scope_text else "")


def render_candidates(candidates: list[Candidate]) -> str:
    if not candidates:
        return "(no elements found in scope)"
    return "\n".join(
        f"{c.rank}. {c.snippet}\n   suggested selectors: {' | '.join(c.suggested) or '(none)'}" for c in candidates
    )


def render_feedback(feedback: tuple[str, str] | None) -> str:
    if feedback is None:
        return ""
    locator, reason = feedback
    return (f'\nYour previous answer proposed "{locator}", which was rejected: {reason}.\n'
            "Choose again from the candidates above, or answer null if none of them does the same job.\n")


def render_healer_prompt(failure: HealingFailure, extraction: Extraction, *, version: str = PROMPT_VERSION,
                         feedback: tuple[str, str] | None = None) -> str:
    scope = failure.scope_phrase()
    if not extraction.scope_found:
        scope += " (scope not found; candidates are from the whole page)"
    template = Template((BACKEND_DIR / "prompts" / f"healer_{version}.md").read_text(encoding="utf-8"))
    return template.substitute(
        page_url=failure.page_url,
        broken_selector=failure.broken_selector,
        description=failure.description,
        scope=scope,
        candidates=render_candidates(extraction.candidates),
        feedback=render_feedback(feedback),
    )
