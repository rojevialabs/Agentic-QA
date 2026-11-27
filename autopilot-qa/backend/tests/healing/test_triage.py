"""Triage rules, driven by real Playwright 1.5x error messages (captured with a probe spec, ANSI removed)."""
import json

import pytest

from healing.triage import classify
from settings import BACKEND_DIR

SERVICES = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))["services"]

CLICK_MISSING = """TimeoutError: locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('.product-card').filter({ hasText: 'Wireless Headphones' }).locator('#add-to-cart-btn')
"""
FILL_MISSING = """TimeoutError: locator.fill: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('#card-number')
"""
CLICK_DISABLED = """TimeoutError: locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('.product-card').filter({ hasText: 'Coffee Grinder' }).locator('#add-to-cart-btn')
    - locator resolved to <button disabled id="add-to-cart-btn" data-product-id="p-104">Add to Cart</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
"""
TEXT_MISSING = """Error: expect(locator).toHaveText(expected) failed

Locator: locator('#cart-message')
Expected: "Added Wireless Headphones to cart"
Timeout: 5000ms
Error: element(s) not found
"""
VISIBLE_MISSING = """Error: expect(locator).toBeVisible() failed

Locator: locator('#empty-cart')
Expected: visible
Timeout: 5000ms
Error: element(s) not found
"""
TEXT_MISMATCH = """Error: expect(locator).toHaveText(expected) failed

Locator:  locator('#cart-total')
Expected: "$59.99"
Received: "$119.98"
Timeout:  5000ms
"""
URL_MISMATCH = """Error: expect(page).toHaveURL(expected) failed

Expected pattern: /localhost:4005\\/payment\\.html/
Received string:  "http://localhost:4004/checkout.html"
Timeout: 5000ms
"""
REFUSED = """Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:4002/catalog.html
Call log:
  - navigating to "http://localhost:4002/catalog.html", waiting until "load"
"""


def triage(error, **failure):
    return classify({"kind": "script", "error": error, **failure}, SERVICES)


def test_a_missing_scoped_locator_is_healable_and_mapped_to_its_key():
    t = triage(CLICK_MISSING)
    assert (t.category, t.root_cause, t.healable) == ("locator_missing", "ui", True)
    assert (t.broken_selector, t.scope_selector, t.scope_text) == ("#add-to-cart-btn", ".product-card", "Wireless Headphones")
    assert (t.selector_key, t.service) == ("catalog.addToCartButton", "catalog")
    assert t.severity == "medium"


def test_a_missing_unscoped_locator_on_the_payment_page_is_high_severity():
    t = triage(FILL_MISSING)
    assert (t.category, t.selector_key, t.scope_selector) == ("locator_missing", "payment.cardNumber", None)
    assert t.severity == "high"


@pytest.mark.parametrize("error, key", [(TEXT_MISSING, "catalog.cartMessage"), (VISIBLE_MISSING, "cart.emptyCart")])
def test_assertions_on_missing_elements_are_healable(error, key):
    t = triage(error)
    assert (t.category, t.healable, t.selector_key) == ("locator_missing", True, key)


def test_an_element_that_exists_but_is_disabled_is_not_a_locator_problem():
    t = triage(CLICK_DISABLED)
    assert (t.category, t.root_cause, t.healable) == ("element_state", "ui", False)
    assert "element is not enabled" in t.detail


def test_a_wrong_value_is_a_data_defect():
    t = triage(TEXT_MISMATCH)
    assert (t.category, t.root_cause, t.healable, t.selector_key) == ("assertion", "data", False, "cart.cartTotal")
    assert 'Received: "$119.98"' in t.detail


def test_a_wrong_page_is_an_api_defect_on_the_checkout_path():
    t = triage(URL_MISMATCH)
    assert (t.category, t.root_cause, t.healable, t.severity) == ("navigation", "api", False, "high")


@pytest.mark.parametrize("failure", [{"kind": "script", "error": REFUSED},
                                     {"kind": "environment", "service": "cart", "error": "cart service not available"}])
def test_environment_failures(failure):
    t = classify(failure, SERVICES)
    assert (t.category, t.root_cause, t.healable, t.severity) == ("environment", "environment", False, "high")


def test_last_selector_key_breaks_ties_between_identical_selectors():
    services = json.loads(json.dumps(SERVICES))
    services["auth"]["elements"]["otherEmail"] = {"selector": "#email", "description": "d"}
    error = FILL_MISSING.replace("#card-number", "#email")
    preferred = classify({"kind": "script", "error": error, "last_selector_key": "auth.otherEmail"}, services)
    assert preferred.selector_key == "auth.otherEmail"
    assert classify({"kind": "script", "error": error}, services).selector_key == "auth.emailInput"


def test_an_unknown_selector_cannot_be_healed():
    t = triage(FILL_MISSING.replace("#card-number", "#never-seen"))
    assert (t.category, t.selector_key, t.healable) == ("locator_missing", None, False)


def test_anything_else_is_a_script_error():
    t = triage("ReferenceError: foo is not defined")
    assert (t.category, t.root_cause, t.healable) == ("script_error", "ui", False)


REAL_TEXT_MISMATCH_WITH_CALL_LOG = """Error: expect(locator).toHaveText(expected) failed

Locator:  locator('#result-count')
Expected: "3 products"
Received: "1 products"
Timeout:  5000ms

Call log:
  - Expect "toHaveText" locator('#result-count') with timeout 5000ms
  - waiting for locator('#result-count')
    13 × locator resolved to <p id="result-count">1 products</p>
       - unexpected value "1 products"
"""


def test_a_real_assertion_message_with_its_call_log_is_still_an_assertion():
    # Regression from the live v2 demo: the call log's "locator resolved to" lines must not hide the assertion.
    t = triage(REAL_TEXT_MISMATCH_WITH_CALL_LOG)
    assert (t.category, t.root_cause, t.selector_key) == ("assertion", "data", "catalog.resultCount")
    assert 'Received: "1 products"' in t.detail


CART_TOTAL_ON_CATALOG = """Error: expect(locator).toHaveText(expected) failed

Locator: locator('#cart-total')
Expected: "$59.99"
Timeout: 5000ms
Error: element(s) not found
"""


def test_an_element_of_another_page_is_a_script_error_not_a_ui_change():
    # Regression from the evaluation baseline: a script asserted the cart total while still on the catalog page,
    # and the healer "healed" it onto the product price. Such an element cannot be on this page at all.
    t = triage(CART_TOTAL_ON_CATALOG, url="http://localhost:4002/catalog.html")
    assert (t.category, t.healable, t.selector_key) == ("wrong_page", False, "cart.cartTotal")
    assert "only exists on /cart.html" in t.detail


def test_the_right_page_with_a_query_string_is_still_healable():
    t = triage(FILL_MISSING, url="http://localhost:4005/payment.html?orderId=ord-1&amount=5")
    assert (t.category, t.healable) == ("locator_missing", True)
