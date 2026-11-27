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
