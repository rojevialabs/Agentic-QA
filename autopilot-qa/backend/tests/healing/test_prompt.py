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


def test_v0_prompt_carries_the_failure_context_and_numbered_candidates():
    prompt = render_healer_prompt(FAILURE, extraction(), version="v0")
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


def test_v1_prompt_asks_for_a_candidate_and_forbids_the_old_locator():
    prompt = render_healer_prompt(FAILURE, extraction())
    assert "Failed locator: #add-to-cart-btn (it no longer exists: never propose it again)" in prompt
    assert '1. <button id="add-to-bag-btn" data-product-id="p-100">Add to Bag</button>' in prompt
    assert 'ACTION: {"candidate": <candidate number or null>, "new_locator":' in prompt
    assert "Wishlist" not in prompt, "the evaluation decoy must stay out of the prompt"
    assert "was rejected" not in prompt
    assert not re.search(r"\$[a-z_]+", prompt)


def test_v1_feedback_paragraph():
    prompt = render_healer_prompt(FAILURE, extraction(), feedback=("#add-to-cart-btn", "it is the failing locator itself"))
    assert 'Your previous answer proposed "#add-to-cart-btn", which was rejected: it is the failing locator itself.' in prompt


def test_v2_prompt_requires_an_explicit_label_comparison():
    prompt = render_healer_prompt(FAILURE, extraction(), version="v2")
    assert 'THOUGHT: Same effect for the user? <yes or no>' in prompt
    assert '"candidate": null, "new_locator": null' in prompt
    assert "Add to Wishlist" not in prompt and "Add to Compare" not in prompt, "decoys stay out of the prompt"
    assert not re.search(r"\$[a-z_]+", prompt)
