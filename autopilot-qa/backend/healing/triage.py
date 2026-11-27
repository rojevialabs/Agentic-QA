"""Deterministic failure triage (roadmap G12): only a genuinely missing locator is sent to the healer.

The rules follow the error text Playwright 1.5x actually produces (captured with a probe spec during Phase 8):
- action on a missing element:   "waiting for locator(...)" and no "locator resolved to"
- assertion on a missing element: "Error: element(s) not found"
- element present but unusable:   "locator resolved to" + "element is not enabled|visible|stable|editable"
- wrong value:                    an expect() failure with "Received"
- wrong page:                     expect(page).toHaveURL
- service down:                   net::ERR_CONNECTION_REFUSED (or an environment failure from the executor)
"""
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse
from typing import Any, Literal

from db.models import RootCause, Severity

Category = Literal["locator_missing", "wrong_page", "element_state", "assertion", "navigation", "environment", "script_error"]

_CHAIN_TOKEN = re.compile(r"locator\('((?:\\.|[^'\\])*)'\)|filter\(\{\s*hasText:\s*'((?:\\.|[^'\\])*)'\s*\}\)")
_ENVIRONMENT = re.compile(r"net::ERR_|ECONNREFUSED|service not available", re.IGNORECASE)
_STATE = re.compile(r"element is not (?:enabled|visible|stable|editable)", re.IGNORECASE)
_HIGH_RISK = re.compile(r"checkout|payment|order|:4004|:4005", re.IGNORECASE)
_DETAIL_LINE = re.compile(r"^(Locator:|Expected|Received|Error: element|- waiting for locator|- locator resolved|- element is not)")

ROOT_CAUSE: dict[str, RootCause] = {"locator_missing": "ui", "wrong_page": "ui", "element_state": "ui", "assertion": "data",
                                    "navigation": "api", "environment": "environment", "script_error": "ui"}


@dataclass(frozen=True)
class Triage:
    category: Category
    root_cause: RootCause
    healable: bool
    detail: str
    broken_selector: str | None = None
    scope_selector: str | None = None
    scope_text: str | None = None
    selector_key: str | None = None
    service: str | None = None
    high_risk: bool = False

    @property
    def severity(self) -> Severity:
        return "high" if self.category == "environment" or self.high_risk else "medium"

    def summary(self) -> str:
        where = f" at {self.selector_key} ({self.broken_selector})" if self.selector_key else (
            f" at {self.broken_selector}" if self.broken_selector else "")
        verdict = "healable locator failure" if self.healable else f"{self.root_cause} defect, not healable"
        return f"{self.category}{where}: {verdict}."

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"severity": self.severity}


def _chain(error: str) -> list[tuple[str, str | None]]:
    """[(selector, hasText filter applied to it)] for the first locator chain mentioned in the error."""
    line = next((l for l in error.splitlines() if "waiting for locator(" in l or l.strip().startswith("Locator:")), "")
    chain: list[tuple[str, str | None]] = []
    for selector, has_text in _CHAIN_TOKEN.findall(line):
        if selector:
            chain.append((selector.replace("\\'", "'"), None))
        elif chain:
            chain[-1] = (chain[-1][0], has_text.replace("\\'", "'"))
    return chain


def _find_key(services: dict[str, Any], selector: str, scope_selector: str | None,
              preferred: str | None) -> tuple[str | None, str | None]:
    matches = [(svc, key) for svc, spec in services.items() for key, element in spec.get("elements", {}).items()
               if element.get("selector") == selector]
    if not matches:
        return None, None
    if preferred and preferred.count(".") == 1 and tuple(preferred.split(".")) in matches:
        service, key = preferred.split(".")
        return f"{service}.{key}", service

    def scope_of(svc: str, key: str) -> str | None:
        scope_key = services[svc]["elements"][key].get("scope")
        return services[svc]["elements"][scope_key]["selector"] if scope_key else None

    service, key = next(((s, k) for s, k in matches if scope_of(s, k) == scope_selector), matches[0])
    return f"{service}.{key}", service


def _category(failure: dict[str, Any], error: str) -> Category:
    if failure.get("kind") == "environment" or _ENVIRONMENT.search(error):
        return "environment"
    if "toHaveURL" in error:
        return "navigation"
    if "element(s) not found" in error:
        return "locator_missing"
    # Checked before the call-log rules: an assertion's call log also says "waiting for locator" / "resolved to".
    if "expect(" in error and "Received" in error:
        return "assertion"
    if "waiting for locator(" in error:
        if "locator resolved to" in error:
            return "element_state" if _STATE.search(error) else "script_error"
        return "locator_missing"
    return "script_error"


def classify(failure: dict[str, Any], services: dict[str, Any]) -> Triage:
    error = (failure.get("error") or "").strip()
    category = _category(failure, error)
    lines = [l.strip() for l in error.splitlines() if l.strip()]
    detail = "\n".join(lines[:1] + [l for l in lines[1:] if _DETAIL_LINE.match(l)]) or "no error message captured"

    chain = _chain(error)
    broken = chain[-1][0] if chain else None
    scope_selector, scope_text = (chain[-2][0], chain[-2][1]) if len(chain) > 1 else (None, None)
    selector_key, service = _find_key(services, broken, scope_selector, failure.get("last_selector_key")) if broken else (None, None)

    pages = list(services.get(service, {}).get("pages", {}).values()) if service else []
    current_path = urlparse(failure.get("url") or "").path
    if category == "locator_missing" and pages and current_path and current_path not in pages:
        # The element belongs to another page: the test is on the wrong page, so nothing here can replace it.
        category = "wrong_page"
        detail = (f"{selector_key} only exists on {' / '.join(pages)}, but the test looked for it on {current_path}.\n"
                  + detail)

    high_risk = service in ("order", "payment") or bool(_HIGH_RISK.search(error)) or (
        category != "locator_missing" and bool(_HIGH_RISK.search(failure.get("url") or "")))
    return Triage(category=category, root_cause=ROOT_CAUSE[category],
                  healable=category == "locator_missing" and selector_key is not None, detail=detail,
                  broken_selector=broken, scope_selector=scope_selector, scope_text=scope_text,
                  selector_key=selector_key, service=service, high_risk=high_risk)
