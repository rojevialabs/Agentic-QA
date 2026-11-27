# Phase 3b — Healer Feasibility Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get evidence, before any agent is built around it, that a local model can heal the v1→v2 catalog change. It must propose a *generic* locator with confidence ≥ 0.80, and must refuse a real defect (a decoy button with a different function). The evidence decides the healer model and prompt shape.

**Architecture:** Three reusable building blocks that Phase 8 will harden, plus one throwaway runner:
- `healing/candidates.py`: stdlib-HTML-parser DOM tree plus deterministic ranking of elements inside the broken locator's scope (roadmap G9).
- `healing/prompt.py` + `prompts/healer_v0.md`: renders the THOUGHT/ACTION prompt from the failure context and the top-k candidates.
- `healing/verify.py`: headless Chromium (Playwright for Python, JavaScript disabled) that counts what a proposed locator matches inside a scope (roadmap G10).
- `scripts/healer_spike.py`: streams each trial through `ThoughtActionParser`, verifies the locator in the target card **and** in a second card (the genericity check), judges it, and writes JSONL plus a markdown summary.

**Tech Stack:** Python 3.12 stdlib `html.parser`, `string.Template`, Playwright for Python ≥1.55 (Chromium), the Phase 3 `OllamaBackend` and `ThoughtActionParser`.

## Global Constraints

- Healer output format: `THOUGHT: …` then `ACTION: {"new_locator": "...", "confidence": 0.0-1.0, "basis": "..."}`, with `"new_locator": null` allowed when no candidate fits.
- Threshold: `HEALING_CONFIDENCE_THRESHOLD`, default 0.80. A heal *would auto-apply* only if confidence ≥ threshold **and** the locator resolves to exactly one element in the scope.
- Exit criterion (roadmap Phase 3b): ≥ 18/20 correct heals on `v2_renamed` for the chosen model. Also report the decoy false-heal rate.
- Trials use temperature 0.7 with `seed = trial index`: varied but reproducible.
- The element description comes from the seed selector map (`config/selector_map.v1.json`), not a hand-written string.
- Unit tests never call a model. Chromium tests are local and fast.

## File Map

```
autopilot-qa/backend/
  requirements.txt                          + playwright>=1.55
  healing/__init__.py
  healing/candidates.py                     Node, parse_dom, matches, find_scope, Candidate, Extraction, extract_candidates
  healing/prompt.py                         HealingFailure, render_candidates, render_healer_prompt, PROMPT_VERSION
  healing/verify.py                         LocatorCheck, DomVerifier
  prompts/healer_v0.md
  scripts/healer_spike.py                   judge(), run_trial(), main()
  tests/fixtures/dom/catalog_v1.html        rendered by catalog-service (v1)
  tests/fixtures/dom/catalog_v2.html        rendered by catalog-service (v2)
  tests/fixtures/dom/catalog_decoy.html     v2 with every add button replaced by an "Add to Wishlist" button
  tests/healing/test_candidates.py
  tests/healing/test_prompt.py
  tests/healing/test_verify.py
  tests/healing/test_spike_judge.py
evaluation/spikes/healer-3b/                 results-<ts>.jsonl, summary-<ts>.md (generated)
docs/spikes/healer-spike.md                  findings and go/no-go
```

---

### Task 1: DOM fixtures and Playwright for Python

- [ ] **Step 1: Render the fixtures from the real catalog service** (run in `target-platform/`)

```powershell
node -e "const fs=require('fs');const {createApp}=require('./catalog-service/server');const {startApp}=require('./shared/test-utils');const out='../autopilot-qa/backend/tests/fixtures/dom/';fs.mkdirSync(out,{recursive:true});(async()=>{for(const v of ['v1','v2']){const s=await startApp(createApp({variant:v}));fs.writeFileSync(out+'catalog_'+v+'.html',await (await fetch(s.url+'/catalog.html')).text());await s.close();}const v2=fs.readFileSync(out+'catalog_v2.html','utf8');fs.writeFileSync(out+'catalog_decoy.html',v2.replace(/<button id=\"add-to-bag-btn\" data-product-id=\"([^\"]+)\"( disabled)?>Add to Bag<\/button>/g,'<button class=\"wishlist-btn\" data-product-id=\"$1\">Add to Wishlist</button>'));})()"
```

Expected: three files. `catalog_decoy.html` contains 6 `wishlist-btn` and 0 `add-to-bag-btn`.

- [ ] **Step 2: Add `playwright>=1.55` to `requirements.txt`, install it, and install Chromium**

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
```

---

### Task 2: Candidate extraction

**Files:** Create `healing/__init__.py` (empty), `healing/candidates.py`, `tests/healing/test_candidates.py`.

**Interfaces — Produces:**
- `parse_dom(html) -> Node`; `Node.tag/attrs/children/order`, `Node.id`, `Node.classes`, `Node.text`, `Node.elements()`.
- `matches(node, selector) -> bool` for single compound selectors (`tag`, `#id`, `.class`, `[attr]`, `[attr='v']`, and combinations). It raises `ValueError` for combinators.
- `find_scope(root, selector, has_text) -> Node | None` (case-insensitive substring, like Playwright `hasText`).
- `Candidate(rank, score, tag, attrs, text, snippet, suggested: tuple[str, ...])`; `Extraction(scope_found: bool, candidates: list[Candidate])`.
- `extract_candidates(html, *, broken_selector, description, scope_selector=None, scope_text=None, k=8) -> Extraction`.

Scoring (0–1): +0.35 interactive; +0.20 tag equals the expected tag (from the broken selector's leading tag or a tag word in the description); +0.25 × Jaccard(tokens of id+classes, tokens of the broken selector's names); +0.20 × Jaccard(tokens of text, tokens of the first quoted label in the description, else the whole description). Tokens are lower-case alphanumeric words, split on camelCase and punctuation, with stopwords (`btn, button, the, a, an, to, of, this, that, and, for, in, on, it`) and 1-character words dropped. Ties go to document order.

- [ ] **Step 1: Write the failing test `tests/healing/test_candidates.py`**

```python
import json

import pytest

from healing.candidates import extract_candidates, matches, parse_dom
from settings import BACKEND_DIR

FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "dom"
V2 = (FIXTURES / "catalog_v2.html").read_text(encoding="utf-8")
DECOY = (FIXTURES / "catalog_decoy.html").read_text(encoding="utf-8")
SEED = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))
DESC = SEED["services"]["catalog"]["elements"]["addToCartButton"]["description"]


def extract(html, **overrides):
    kwargs = dict(broken_selector="#add-to-cart-btn", description=DESC, scope_selector=".product-card",
                  scope_text="Wireless Headphones")
    return extract_candidates(html, **(kwargs | overrides))


def test_parse_dom_ignores_script_text_and_keeps_order():
    root = parse_dom("<div id='a'>hi <b>there</b><script>var x = 'button';</script></div>")
    div = next(n for n in root.elements() if n.id == "a")
    assert div.text == "hi there"


def test_matches_compound_selectors():
    node = next(n for n in parse_dom("<button id='b' class='x y' data-product-id='p-1' disabled>Go</button>").elements()
                if n.tag == "button")
    for selector in ["button", "#b", ".y", "button.x#b", "[data-product-id]", "[data-product-id='p-1']", '[disabled]']:
        assert matches(node, selector), selector
    for selector in ["a", "#c", ".z", "[data-product-id='p-2']", "[title]"]:
        assert not matches(node, selector), selector


def test_combinators_are_rejected():
    node = parse_dom("<p>x</p>")
    with pytest.raises(ValueError):
        matches(node, ".card button")


def test_v2_ranks_the_renamed_button_first_and_stays_in_scope():
    ex = extract(V2)
    assert ex.scope_found
    top = ex.candidates[0]
    assert (top.tag, top.attrs["id"], top.text) == ("button", "add-to-bag-btn", "Add to Bag")
    assert top.suggested[:2] == ("#add-to-bag-btn", "button[data-product-id]")
    assert top.snippet == '<button id="add-to-bag-btn" data-product-id="p-100">Add to Bag</button>'
    assert [c.rank for c in ex.candidates] == list(range(1, len(ex.candidates) + 1))
    assert all("Yoga" not in c.text and "Search" not in c.text for c in ex.candidates)


def test_decoy_page_offers_the_wishlist_button():
    ex = extract(DECOY)
    assert any(c.attrs.get("class") == "wishlist-btn" for c in ex.candidates)
    assert all(c.attrs.get("id") != "add-to-bag-btn" for c in ex.candidates)


def test_top_k_limits_the_list():
    assert len(extract(V2, scope_selector=None, scope_text=None, k=3).candidates) == 3


def test_missing_scope_falls_back_to_the_whole_page():
    ex = extract(V2, scope_text="No Such Product")
    assert not ex.scope_found
    assert any(c.attrs.get("id") == "search-btn" for c in ex.candidates)


def test_boolean_attributes_render_without_a_value():
    ex = extract(V2, scope_text="Coffee Grinder")
    assert ex.candidates[0].snippet == '<button id="add-to-bag-btn" data-product-id="p-104" disabled>Add to Bag</button>'
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_candidates.py -q`
Expected: `ModuleNotFoundError: No module named 'healing'`

- [ ] **Step 3: Write `healing/candidates.py`**

```python
"""Deterministic candidate extraction for self-healing (roadmap G9).

Given a DOM snapshot and a locator that no longer matches, list the elements inside the locator's scope
that most resemble the missing one, so the LLM chooses among a few real elements instead of reading a page.
"""
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_TEXT_TAGS = {"script", "style", "template"}
INTERACTIVE_TAGS = {"button", "a", "input", "select", "textarea"}
TAG_WORDS = {"button": "button", "link": "a", "field": "input", "input": "input", "box": "input", "dropdown": "select"}
STOPWORDS = {"btn", "button", "the", "a", "an", "to", "of", "this", "that", "and", "for", "in", "on", "it"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node | str"] = field(default_factory=list)
    order: int = 0

    @property
    def id(self) -> str | None:
        return self.attrs.get("id")

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def elements(self) -> Iterator["Node"]:
        """This node and all descendant elements, in document order."""
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.elements()

    @property
    def text(self) -> str:
        parts: list[str] = []

        def walk(node: Node) -> None:
            if node.tag in SKIP_TEXT_TAGS:
                return
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)

        walk(self)
        return " ".join("".join(parts).split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self._stack = [self.root]
        self._count = 0

    def _add(self, tag: str, attrs: list[tuple[str, str | None]]) -> Node:
        self._count += 1
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=self._stack[-1], order=self._count)
        self._stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def parse_dom(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


_COMPOUND = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<parts>(?:[#.][\w-]+|\[[\w-]+(?:=(?:\"[^\"]*\"|'[^']*'|[\w-]+))?\])*)$")
_PART = re.compile(r"#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:\"([^\"]*)\"|'([^']*)'|([\w-]+)))?\]")


def matches(node: Node, selector: str) -> bool:
    """Match a single compound CSS selector (no combinators). Used for scopes from the selector map."""
    selector = selector.strip()
    compound = _COMPOUND.match(selector)
    if not selector or not compound:
        raise ValueError(f"unsupported scope selector {selector!r}: use one compound selector like .card or button#id")
    if compound["tag"] and node.tag != compound["tag"].lower():
        return False
    for id_, cls, attr, dq, sq, bare in _PART.findall(compound["parts"]):
        if id_ and node.id != id_:
            return False
        if cls and cls not in node.classes:
            return False
        if attr:
            if attr not in node.attrs:
                return False
            expected = dq or sq or bare
            if expected and node.attrs[attr] != expected:
                return False
    return True


def find_scope(root: Node, selector: str, has_text: str | None = None) -> Node | None:
    needle = (has_text or "").lower()
    for node in root.elements():
        if node is not root and matches(node, selector) and needle in node.text.lower():
            return node
    return None


def tokens(text: str) -> frozenset[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return frozenset(w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 1 and w not in STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def is_interactive(node: Node) -> bool:
    return node.tag in INTERACTIVE_TAGS or any(a in node.attrs for a in ("role", "onclick", "tabindex"))


def _is_candidate(node: Node) -> bool:
    if node.tag.startswith("#") or node.tag in SKIP_TEXT_TAGS:
        return False
    labelled = bool(node.id or node.classes or any(a.startswith("data-") for a in node.attrs))
    return is_interactive(node) or (labelled and bool(node.text))


@dataclass(frozen=True)
class _Expectation:
    tag: str | None
    name_tokens: frozenset[str]
    label_tokens: frozenset[str]

    @classmethod
    def from_failure(cls, broken_selector: str, description: str) -> "_Expectation":
        leading_tag = re.match(r"^([a-zA-Z][\w-]*)", broken_selector)
        described_tag = next((TAG_WORDS[w] for w in re.findall(r"[a-z]+", description.lower()) if w in TAG_WORDS), None)
        quoted = re.search(r"'([^']+)'|\"([^\"]+)\"", description)
        label = (quoted.group(1) or quoted.group(2)) if quoted else description
        names = " ".join(re.findall(r"[#.]([\w-]+)", broken_selector))
        return cls(leading_tag.group(1).lower() if leading_tag else described_tag, tokens(names), tokens(label))

    def score(self, node: Node) -> float:
        value = 0.35 if is_interactive(node) else 0.0
        if self.tag and node.tag == self.tag:
            value += 0.20
        value += 0.25 * _jaccard(tokens(" ".join([node.id or "", *node.classes])), self.name_tokens)
        value += 0.20 * _jaccard(tokens(node.text), self.label_tokens)
        return round(value, 3)


@dataclass(frozen=True)
class Candidate:
    rank: int
    score: float
    tag: str
    attrs: dict[str, str]
    text: str
    snippet: str
    suggested: tuple[str, ...]


@dataclass(frozen=True)
class Extraction:
    scope_found: bool
    candidates: list[Candidate]


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def snippet(node: Node, max_text: int = 60) -> str:
    attrs = "".join(f' {k}="{_short(v, 40)}"' if v else f" {k}" for k, v in node.attrs.items())
    if node.tag in VOID_TAGS:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{_short(node.text, max_text)}</{node.tag}>"


def suggested_selectors(node: Node) -> tuple[str, ...]:
    """Generic ways to address this element, most stable first. No attribute *values* besides the id."""
    out = [f"#{node.id}"] if node.id else []
    out += [f"{node.tag}[{a}]" for a in node.attrs if a.startswith("data-")]
    out += [f"{node.tag}.{c}" for c in node.classes]
    if node.text and is_interactive(node):
        out.append(f'{node.tag}:has-text("{node.text.replace(chr(34), chr(92) + chr(34))}")')
    return tuple(out)


def extract_candidates(html: str, *, broken_selector: str, description: str, scope_selector: str | None = None,
                       scope_text: str | None = None, k: int = 8) -> Extraction:
    root = parse_dom(html)
    scope = find_scope(root, scope_selector, scope_text) if scope_selector else None
    container = scope or root
    expectation = _Expectation.from_failure(broken_selector, description)
    nodes = [n for n in container.elements() if n is not container and _is_candidate(n)]
    ranked = sorted(nodes, key=lambda n: (-expectation.score(n), n.order))[:k]
    candidates = [
        Candidate(rank=i, score=expectation.score(n), tag=n.tag, attrs=dict(n.attrs), text=n.text,
                  snippet=snippet(n), suggested=suggested_selectors(n))
        for i, n in enumerate(ranked, start=1)
    ]
    return Extraction(scope_found=scope is not None or not scope_selector, candidates=candidates)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_candidates.py -q`
Expected: `8 passed`

---

### Task 3: Healer prompt v0

**Files:** Create `prompts/healer_v0.md`, `healing/prompt.py`, `tests/healing/test_prompt.py`.

**Interfaces — Produces:** `PROMPT_VERSION = "v0"`; `HealingFailure(page_url, broken_selector, description, scope_selector=None, scope_text=None)` with `scope_phrase() -> str`; `render_candidates(candidates) -> str`; `render_healer_prompt(failure, extraction) -> str`.

- [ ] **Step 1: Write the failing test `tests/healing/test_prompt.py`**

```python
import re

from healing.candidates import extract_candidates
from healing.prompt import HealingFailure, render_candidates, render_healer_prompt
from settings import BACKEND_DIR

V2 = (BACKEND_DIR / "tests" / "fixtures" / "dom" / "catalog_v2.html").read_text(encoding="utf-8")
FAILURE = HealingFailure(page_url="http://localhost:4002/catalog.html", broken_selector="#add-to-cart-btn",
                         description="button labelled 'Add to Cart' that adds this card's product to the cart",
                         scope_selector=".product-card", scope_text="Wireless Headphones")


def extraction(scope_text="Wireless Headphones"):
    return extract_candidates(V2, broken_selector=FAILURE.broken_selector, description=FAILURE.description,
                              scope_selector=FAILURE.scope_selector, scope_text=scope_text)


def test_prompt_carries_the_failure_context_and_numbered_candidates():
    prompt = render_healer_prompt(FAILURE, extraction())
    assert "Failed locator: #add-to-cart-btn" in prompt
    assert FAILURE.description in prompt
    assert 'the element matching ".product-card" that contains the text "Wireless Headphones"' in prompt
    assert '1. <button id="add-to-bag-btn" data-product-id="p-100">Add to Bag</button>' in prompt
    assert "suggested selectors: #add-to-bag-btn | button[data-product-id]" in prompt
    assert 'ACTION: {"new_locator":' in prompt
    assert not re.search(r"\$[a-z_]+", prompt), "unfilled template placeholder"


def test_prompt_flags_a_missing_scope():
    prompt = render_healer_prompt(FAILURE, extraction(scope_text="No Such Product"))
    assert "(scope not found; candidates are from the whole page)" in prompt


def test_no_candidates_renders_a_placeholder():
    assert render_candidates([]) == "(no elements found in scope)"


def test_scope_phrase_without_scope():
    assert HealingFailure("u", "#x", "d").scope_phrase() == "the whole page"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_prompt.py -q`
Expected: `ModuleNotFoundError: No module named 'healing.prompt'`

- [ ] **Step 3: Write `prompts/healer_v0.md`**

```text
You are the self-healing component of an automated UI test suite.

A test step failed because its locator no longer matches any element.
- Page: $page_url
- Failed locator: $broken_selector
- The element's purpose: $description
- The locator is evaluated inside: $scope

Candidate elements currently inside that scope, most similar first:
$candidates

Decide which candidate, if any, performs the SAME function as the missing element.
Rules:
1. The locator is reused for every repeated container (for example every product card), so it must be generic:
   never include values that identify one particular item, such as a product id or product name.
2. Prefer, in order: a stable id selector, an attribute that every such element shares (e.g. button[data-product-id]),
   then visible text.
3. A renamed label with the same meaning (e.g. "Add to Cart" -> "Add to Basket") is a benign change: heal it.
   A control with a different purpose is NOT a replacement: if the original control is gone, do not pick another one.
4. Confidence: 0.9-1.0 only when purpose, element type and attributes clearly match apart from naming;
   0.5-0.8 when plausible but uncertain; below 0.5 when no candidate performs the same function.
   When no candidate matches, answer with "new_locator": null and confidence below 0.3.

Answer in exactly this format and nothing else:
THOUGHT: <1-2 sentences of reasoning>
ACTION: {"new_locator": "<css selector or null>", "confidence": <0.0-1.0>, "basis": "<short reason>"}
```

- [ ] **Step 4: Write `healing/prompt.py`**

```python
"""Renders the healer's THOUGHT/ACTION prompt from a failure and its candidate elements."""
from dataclasses import dataclass
from string import Template

from healing.candidates import Candidate, Extraction
from settings import BACKEND_DIR

PROMPT_VERSION = "v0"
TEMPLATE_PATH = BACKEND_DIR / "prompts" / f"healer_{PROMPT_VERSION}.md"


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


def render_healer_prompt(failure: HealingFailure, extraction: Extraction) -> str:
    scope = failure.scope_phrase()
    if not extraction.scope_found:
        scope += " (scope not found; candidates are from the whole page)"
    return Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(
        page_url=failure.page_url,
        broken_selector=failure.broken_selector,
        description=failure.description,
        scope=scope,
        candidates=render_candidates(extraction.candidates),
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_prompt.py -q`
Expected: `4 passed`

---

### Task 4: Locator verification in headless Chromium

**Files:** Create `healing/verify.py`, `tests/healing/test_verify.py`.

**Interfaces — Produces:** `LocatorCheck(locator, scope_matches, matches: tuple[dict, ...] = (), error=None)` with `count` and `unique` properties. Each match is `{"tag", "attrs", "text"}`. `DomVerifier` is an async context manager with `check(html, locator, *, scope_selector=None, scope_text=None) -> LocatorCheck`.

- [ ] **Step 1: Write the failing test `tests/healing/test_verify.py`**

```python
import pytest

from healing.verify import DomVerifier
from settings import BACKEND_DIR

V2 = (BACKEND_DIR / "tests" / "fixtures" / "dom" / "catalog_v2.html").read_text(encoding="utf-8")
CARD = ".product-card"


@pytest.fixture
async def verifier():
    async with DomVerifier() as v:
        yield v


async def check(verifier, locator, card="Wireless Headphones"):
    return await verifier.check(V2, locator, scope_selector=CARD, scope_text=card)


async def test_renamed_button_resolves_uniquely_in_its_card(verifier):
    result = await check(verifier, "#add-to-bag-btn")
    assert result.unique
    assert result.scope_matches == 1
    assert result.matches[0] == {"tag": "button", "attrs": {"id": "add-to-bag-btn", "data-product-id": "p-100"},
                                 "text": "Add to Bag"}


async def test_old_locator_matches_nothing(verifier):
    result = await check(verifier, "#add-to-cart-btn")
    assert (result.count, result.error, result.unique) == (0, None, False)


async def test_product_specific_locator_is_not_generic(verifier):
    assert (await check(verifier, "button[data-product-id='p-100']")).unique
    assert (await check(verifier, "button[data-product-id='p-100']", card="Yoga Mat")).count == 0


async def test_playwright_text_selectors_work(verifier):
    assert (await check(verifier, 'button:has-text("Add to Bag")', card="Yoga Mat")).unique


async def test_invalid_selector_reports_an_error(verifier):
    result = await check(verifier, "button[[")
    assert result.error and not result.unique


async def test_missing_locator_and_missing_scope(verifier):
    assert (await check(verifier, None)).error == "no locator proposed"
    assert (await check(verifier, "#add-to-bag-btn", card="No Such Product")).error == "scope not found"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_verify.py -q`
Expected: `ModuleNotFoundError: No module named 'healing.verify'`

- [ ] **Step 3: Write `healing/verify.py`**

```python
"""Checks a proposed locator against a DOM snapshot in headless Chromium (roadmap G10)."""
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

_MATCH_INFO = """els => els.map(e => ({
  tag: e.tagName.toLowerCase(),
  attrs: Object.fromEntries([...e.attributes].map(a => [a.name, a.value])),
  text: e.textContent.trim().replace(/\\s+/g, ' '),
}))"""


@dataclass(frozen=True)
class LocatorCheck:
    locator: str | None
    scope_matches: int
    matches: tuple[dict[str, Any], ...] = ()
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def unique(self) -> bool:
        return self.error is None and self.count == 1


class DomVerifier:
    """One headless Chromium for many checks. JavaScript is disabled: we inspect markup, not behaviour."""

    async def __aenter__(self) -> "DomVerifier":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch()
        context = await self._browser.new_context(java_script_enabled=False)
        self._page = await context.new_page()
        self._loaded: str | None = None
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._browser.close()
        await self._playwright.stop()

    async def check(self, html: str, locator: str | None, *, scope_selector: str | None = None,
                    scope_text: str | None = None) -> LocatorCheck:
        if not locator:
            return LocatorCheck(locator, 0, error="no locator proposed")
        if html != self._loaded:
            await self._page.set_content(html)
            self._loaded = html
        scope = self._page.locator(scope_selector, has_text=scope_text) if scope_selector else self._page.locator("html")
        scope_matches = await scope.count()
        if not scope_matches:
            return LocatorCheck(locator, 0, error="scope not found")
        try:
            found = await scope.first.locator(locator).evaluate_all(_MATCH_INFO)
        except PlaywrightError as exc:
            return LocatorCheck(locator, scope_matches, error=str(exc).splitlines()[0])
        return LocatorCheck(locator, scope_matches, tuple(found))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_verify.py -q`
Expected: `6 passed`

---

### Task 5: Spike runner

**Files:** Create `scripts/healer_spike.py`, `tests/healing/test_spike_judge.py`.

**Interfaces — Consumes:** everything above plus `OllamaBackend`, `ThoughtActionParser`. **Produces:** `judge(expect, confidence, target, other, threshold) -> dict` with keys `would_apply, target_ok, generic_ok, llm_locator_ok, correct`.

Judging rules:
- `would_apply` = confidence ≥ threshold **and** the locator is unique in the target card.
- `target_ok` / `generic_ok` = unique in the Wireless Headphones / Yoga Mat card **and** the match is that card's add button (`button`, matching `data-product-id`, text "Add to Bag").
- `heal` scenario: `correct` = `would_apply and target_ok and generic_ok`.
- `no_heal` scenario: `correct` = `not would_apply`.
- `llm_locator_ok` = `target_ok and generic_ok`, regardless of confidence.
- `llm_refused` = confidence missing or below threshold; `rescued_by_verify` = the LLM was confident but verification blocked the heal (e.g. a hallucinated locator).

- [ ] **Step 1: Write the failing test `tests/healing/test_spike_judge.py`**

```python
from healing.verify import LocatorCheck
from scripts.healer_spike import judge

ADD_P100 = {"tag": "button", "attrs": {"id": "add-to-bag-btn", "data-product-id": "p-100"}, "text": "Add to Bag"}
ADD_P105 = {"tag": "button", "attrs": {"id": "add-to-bag-btn", "data-product-id": "p-105"}, "text": "Add to Bag"}
WISH_P100 = {"tag": "button", "attrs": {"class": "wishlist-btn", "data-product-id": "p-100"}, "text": "Add to Wishlist"}
NONE = LocatorCheck(None, 0, error="no locator proposed")


def found(*matches):
    return LocatorCheck("x", 1, tuple(matches))


def test_heal_needs_confidence_the_right_target_and_a_generic_locator():
    assert judge("heal", 0.9, found(ADD_P100), found(ADD_P105), 0.8)["correct"]
    low = judge("heal", 0.7, found(ADD_P100), found(ADD_P105), 0.8)
    assert low["llm_locator_ok"] and not low["correct"]
    specific = judge("heal", 0.9, found(ADD_P100), found(), 0.8)
    assert specific["target_ok"] and not specific["generic_ok"] and not specific["correct"]


def test_a_confident_wrong_pick_is_never_correct():
    assert not judge("heal", 0.95, found(WISH_P100), found(), 0.8)["correct"]


def test_no_heal_is_correct_unless_it_would_auto_apply():
    assert not judge("no_heal", 0.9, found(WISH_P100), found(), 0.8)["correct"]
    assert judge("no_heal", 0.4, found(WISH_P100), found(), 0.8)["correct"]
    assert judge("no_heal", None, NONE, NONE, 0.8)["correct"]


def test_a_confident_hallucinated_locator_is_rescued_by_verification():
    result = judge("no_heal", 0.9, found(), found(), 0.8)
    assert result["correct"] and result["rescued_by_verify"] and not result["llm_refused"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/healing/test_spike_judge.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.healer_spike'`

- [ ] **Step 3: Write `scripts/healer_spike.py`**

```python
"""Phase 3b healer feasibility spike: python -m scripts.healer_spike [--models M ...] [--trials N]

Streams the v0 healer prompt for saved DOM snapshots, verifies every proposed locator in headless Chromium
(in the target card and in a second card, to check it is generic), and writes per-trial JSONL plus a
markdown summary to evaluation/spikes/healer-3b/.
"""
import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone

from healing.candidates import extract_candidates
from healing.prompt import PROMPT_VERSION, HealingFailure, render_healer_prompt
from healing.verify import DomVerifier, LocatorCheck
from llm.ollama_client import LLMError, OllamaBackend
from llm.trace_parser import Action, ParseError, Thought, ThoughtActionParser
from settings import BACKEND_DIR, get_settings

FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "dom"
OUT_DIR = BACKEND_DIR.parents[1] / "evaluation" / "spikes" / "healer-3b"
SEED = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))
ELEMENT = SEED["services"]["catalog"]["elements"]["addToCartButton"]
SCOPE = SEED["services"]["catalog"]["elements"][ELEMENT["scope"]]["selector"]
TARGET_CARD, TARGET_ID = "Wireless Headphones", "p-100"
OTHER_CARD, OTHER_ID = "Yoga Mat", "p-105"
EXPECTED_LABEL = "Add to Bag"
FAILURE = HealingFailure(page_url="http://localhost:4002/catalog.html", broken_selector=ELEMENT["selector"],
                         description=ELEMENT["description"], scope_selector=SCOPE, scope_text=TARGET_CARD)
SCENARIOS = {"v2_renamed": ("catalog_v2.html", "heal"), "decoy_wishlist": ("catalog_decoy.html", "no_heal")}
DEFAULT_MODELS = ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"]


def _is_add_button(match: dict, product_id: str) -> bool:
    return (match["tag"] == "button" and match["attrs"].get("data-product-id") == product_id
            and match["text"] == EXPECTED_LABEL)


def judge(expect: str, confidence: float | None, target: LocatorCheck, other: LocatorCheck, threshold: float) -> dict:
    would_apply = confidence is not None and confidence >= threshold and target.unique
    target_ok = target.unique and _is_add_button(target.matches[0], TARGET_ID)
    generic_ok = other.unique and _is_add_button(other.matches[0], OTHER_ID)
    correct = (would_apply and target_ok and generic_ok) if expect == "heal" else not would_apply
    llm_refused = confidence is None or confidence < threshold
    return {"would_apply": would_apply, "target_ok": target_ok, "generic_ok": generic_ok,
            "llm_locator_ok": target_ok and generic_ok, "llm_refused": llm_refused,
            "rescued_by_verify": not llm_refused and not would_apply, "correct": correct}


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def run_trial(llm: OllamaBackend, verifier: DomVerifier, *, model: str, scenario: str, trial: int,
                    temperature: float, threshold: float) -> dict:
    dom_file, expect = SCENARIOS[scenario]
    html = (FIXTURES / dom_file).read_text(encoding="utf-8")
    prompt = render_healer_prompt(FAILURE, extract_candidates(
        html, broken_selector=FAILURE.broken_selector, description=FAILURE.description,
        scope_selector=FAILURE.scope_selector, scope_text=FAILURE.scope_text))

    parser, raw, events = ThoughtActionParser(), [], []
    started = time.perf_counter()
    try:
        async for chunk in llm.stream(prompt, model=model, options={"temperature": temperature, "seed": trial}):
            raw.append(chunk)
            events += parser.feed(chunk)
        events += parser.finish()
        llm_error = None
    except LLMError as exc:
        llm_error = str(exc)
    latency = time.perf_counter() - started

    action = next((e.data for e in events if isinstance(e, Action)), None)
    locator = action.get("new_locator") if action else None
    locator = locator.strip() if isinstance(locator, str) and locator.strip() else None
    confidence = _as_float(action.get("confidence")) if action else None
    target = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=TARGET_CARD)
    other = await verifier.check(html, locator, scope_selector=SCOPE, scope_text=OTHER_CARD)
    return {
        "model": model, "scenario": scenario, "expect": expect, "trial": trial, "temperature": temperature,
        "prompt_version": PROMPT_VERSION, "latency_s": round(latency, 2), "llm_error": llm_error,
        "thoughts": [e.text for e in events if isinstance(e, Thought)],
        "parse_errors": [e.reason for e in events if isinstance(e, ParseError)],
        "action": action, "locator": locator, "confidence": confidence,
        "target": asdict(target), "other": asdict(other), "raw": "".join(raw),
        **judge(expect, confidence, target, other, threshold),
    }


def summarise(rows: list[dict], threshold: float) -> str:
    lines = [
        f"Threshold {threshold}. `correct` = heal auto-applies with a verified generic locator (v2) / "
        "no auto-apply (decoy). `locator ok` ignores confidence.",
        "",
        "| model | scenario | n | parsed | correct | locator ok | would apply | LLM refused | rescued by verify "
        "| mean conf | median s | p90 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in dict.fromkeys(r["model"] for r in rows):
        for scenario in dict.fromkeys(r["scenario"] for r in rows):
            group = [r for r in rows if r["model"] == model and r["scenario"] == scenario]
            if not group:
                continue
            n = len(group)
            parsed = sum(1 for r in group if r["action"] is not None and not r["parse_errors"])
            confs = [r["confidence"] for r in group if r["confidence"] is not None]
            lat = sorted(r["latency_s"] for r in group)
            p90 = statistics.quantiles(lat, n=10)[-1] if n > 1 else lat[0]
            lines.append(
                f"| {model} | {scenario} | {n} | {parsed}/{n} | **{sum(r['correct'] for r in group)}/{n}** | "
                f"{sum(r['llm_locator_ok'] for r in group)}/{n} | {sum(r['would_apply'] for r in group)}/{n} | "
                f"{sum(r['llm_refused'] for r in group)}/{n} | {sum(r['rescued_by_verify'] for r in group)}/{n} | "
                f"{statistics.mean(confs) if confs else float('nan'):.2f} | {statistics.median(lat):.1f} | {p90:.1f} |"
            )
    return "\n".join(lines)


async def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    args.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    args.add_argument("--trials", type=int, default=20)
    args.add_argument("--temperature", type=float, default=0.7)
    args.add_argument("--print-prompt", action="store_true", help="print the v2 prompt and exit")
    opts = args.parse_args()

    settings = get_settings()
    if opts.print_prompt:
        html = (FIXTURES / SCENARIOS["v2_renamed"][0]).read_text(encoding="utf-8")
        print(render_healer_prompt(FAILURE, extract_candidates(
            html, broken_selector=FAILURE.broken_selector, description=FAILURE.description,
            scope_selector=FAILURE.scope_selector, scope_text=FAILURE.scope_text)))
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = OUT_DIR / f"results-{stamp}.jsonl"
    rows: list[dict] = []
    async with OllamaBackend(settings.ollama_url, opts.models[0]) as llm, DomVerifier() as verifier:
        with results_path.open("w", encoding="utf-8") as out:
            for model in opts.models:
                for scenario in opts.scenarios:
                    for trial in range(opts.trials):
                        row = await run_trial(llm, verifier, model=model, scenario=scenario, trial=trial,
                                              temperature=opts.temperature,
                                              threshold=settings.healing_confidence_threshold)
                        rows.append(row)
                        out.write(json.dumps(row) + "\n")
                        out.flush()
                        print(f"{model:<18} {scenario:<15} #{trial:<2} {'OK ' if row['correct'] else 'BAD'} "
                              f"conf={row['confidence']} locator={row['locator']!r} {row['latency_s']}s", flush=True)

    summary = summarise(rows, settings.healing_confidence_threshold)
    (OUT_DIR / f"summary-{stamp}.md").write_text(
        f"# Healer spike {stamp}\n\nPrompt {PROMPT_VERSION}, temperature {opts.temperature}, "
        f"{opts.trials} trials, seeds 0..{opts.trials - 1}.\n\n{summary}\n", encoding="utf-8")
    print("\n" + summary + f"\n\nresults: {results_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python -m pytest tests/healing -q`
Expected: `22 passed` (8 + 4 + 6 + 4)

- [ ] **Step 5: Inspect the prompt, smoke one trial, then launch the full run**

```powershell
.\.venv\Scripts\python -m scripts.healer_spike --print-prompt
.\.venv\Scripts\python -m scripts.healer_spike --models llama3.2:3b --trials 1
.\.venv\Scripts\python -m scripts.healer_spike            # 3 models x 2 scenarios x 20 trials, roughly 50 minutes on CPU
```

- [ ] **Step 6: Write `docs/spikes/healer-spike.md`** with the summary table, failure analysis (read the `thoughts` and `raw` of every incorrect trial), the model decision, and the prompt changes carried into Phase 8.

## Phase 3b Exit Checklist

- [ ] `pytest -q` green (50 + 22 = 72).
- [ ] The full spike run is complete and the summary is committed under `evaluation/spikes/healer-3b/`.
- [ ] Chosen model reaches ≥ 18/20 `correct` on `v2_renamed`; decoy false-heal rate reported. If no model qualifies: iterate on the prompt (a v1 prompt file, rerun), or evaluate the cloud backend behind C1.
- [ ] `docs/spikes/healer-spike.md` states the decision; the roadmap progress log is updated.
