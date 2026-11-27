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
