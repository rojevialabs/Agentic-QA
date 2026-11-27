import pytest

from agents.platform_context import load_platform_context
from llm.schema import inline_refs


def test_context_holds_services_and_test_data_only():
    context = load_platform_context()
    assert context.startswith("## Services")
    assert "## Test data" in context and "## UI text" in context
    assert "Invalid email or password" in context and "Payment approved" in context
    for fact in ["Wireless Headphones", "Coffee Grinder", "demo@shop.test", "4111111111111111", "01/20"]:
        assert fact in context
    for noise in ["npm run start:all", "CATALOG_BUTTON_VARIANT", "## Page elements"]:
        assert noise not in context


def test_missing_section_is_an_error():
    with pytest.raises(ValueError, match="Nope"):
        load_platform_context(sections=("Services", "Nope"))


def test_inline_refs_resolves_definitions():
    schema = {"type": "object", "properties": {"x": {"$ref": "#/$defs/X", "description": "kept"}},
              "$defs": {"X": {"type": "array", "items": {"$ref": "#/$defs/Y"}}, "Y": {"type": "string"}}}
    assert inline_refs(schema) == {"type": "object", "properties": {
        "x": {"type": "array", "items": {"type": "string"}, "description": "kept"}}}
