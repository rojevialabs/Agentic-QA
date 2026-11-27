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
    # Negated text matches on a locator that can resolve to several rows (or to a page mid-reload) fail unpredictably.
    "not.toHaveText/not.toContainText (check rows with toHaveCount)": r"\.not\.(?:toHaveText|toContainText)\s*\(",
    "page.click/page.fill/... with a raw selector (write page.locator(sel('<service>.<element>')).fill(...) or .click())":
        r"\bpage\.(click|dblclick|fill|type|press|check|uncheck|hover|selectOption|tap|focus)\s*\(",
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


_CHAINED = re.compile(r"sel\(\s*'(\w+)\.(\w+)'\s*\)(?:\s*,\s*\{[^}]*\})?\s*\)\s*\.locator\(\s*sel\(\s*'(\w+)\.(\w+)'\s*\)")
_ON_PAGE = re.compile(r"\bpage\.locator\(\s*sel\(\s*'(\w+)\.(\w+)'\s*\)")


def _scope_errors(code: str, selector_map: dict) -> list[str]:
    """Scoped elements (e.g. the add button of one product card) must be chained inside their declared scope,
    and unscoped ones located on the page, exactly as the selector map declares."""
    def scope_of(service: str, element: str) -> str | None:
        return selector_map.get(service, {}).get("elements", {}).get(element, {}).get("scope")

    errors = []
    for outer_svc, outer, inner_svc, inner in _CHAINED.findall(code):
        if inner_svc != outer_svc or scope_of(inner_svc, inner) != outer:
            errors.append(f"{inner_svc}.{inner} is not inside {outer_svc}.{outer}; locate it as the selector map declares")
    for service, element in _ON_PAGE.findall(code):
        scope = scope_of(service, element)
        if scope:
            errors.append(f"{service}.{element} must be located inside {service}.{scope}: "
                          f"page.locator(sel('{service}.{scope}'), {{ hasText: '...' }}).locator(sel('{service}.{element}'))")
    return errors


_KEY = r"sel\(\s*'[\w.]+'\s*\)"
_LITERAL = r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*\""""
_FILL = re.compile(rf"\bpage\.fill\(\s*({_KEY})\s*,\s*({_LITERAL})\s*\)")
_PLAIN = re.compile(rf"\bpage\.(click|dblclick|check|uncheck|hover|focus|tap)\(\s*({_KEY})\s*\)")


def normalize_script(code: str) -> str:
    """Rewrite the natural `page.fill(sel('key'), 'v')` / `page.click(sel('key'))` into the accepted
    `page.locator(sel('key')).fill('v')` form. Only calls whose selector is already a selector-map key are touched,
    so raw CSS selectors are still rejected by the validator."""
    code = _FILL.sub(lambda m: f"page.locator({m.group(1)}).fill({m.group(2)})", code)
    return _PLAIN.sub(lambda m: f"page.locator({m.group(2)}).{m.group(1)}()", code)


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
    errors += _scope_errors(no_comments, selector_map)
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
