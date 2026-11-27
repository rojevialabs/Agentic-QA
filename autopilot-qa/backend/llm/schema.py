"""JSON-schema helpers for structured LLM output."""
from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local $refs into a self-contained schema, for grammar-constrained decoding (Ollama `format`)."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].rsplit("/", 1)[-1]]
                return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
