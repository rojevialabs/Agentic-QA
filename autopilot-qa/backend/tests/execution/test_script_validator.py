import json

import pytest

from execution.script_validator import validate_script
from settings import BACKEND_DIR

SERVICES = json.loads((BACKEND_DIR / "config" / "selector_map.v1.json").read_text(encoding="utf-8"))["services"]
EXAMPLE = (BACKEND_DIR.parent / "tests-generated" / "examples" / "remove-only-item.spec.js").read_text(encoding="utf-8")
HEADER = "const { test, expect, sel, urls } = require('../support/fixtures');\n\n"


def script(body: str) -> str:
    return HEADER + "test('t', async ({ page }) => {\n" + body + "\n  await expect(page).toHaveURL(/x/);\n});\n"


def errors(code: str) -> list[str]:
    return validate_script(code, SERVICES).errors


def test_the_prompt_example_is_valid():
    assert validate_script(EXAMPLE, SERVICES).ok


def test_urls_inside_strings_are_not_comments():
    assert errors(script("  await expect(page).toHaveURL('http://localhost:4005/payment.html'); // real comment")) == []


def test_the_recommended_way_to_check_a_row_is_gone_is_valid():
    body = ("  await page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' }).locator(sel('cart.removeButton')).click();\n"
            "  await expect(page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' })).toHaveCount(0);")
    assert errors(script(body)) == []


def test_forbidden_words_inside_strings_are_fine():
    assert errors(script("  await expect(page.locator(sel('auth.message'))).toHaveText('process.env fetch( eval(');")) == []


@pytest.mark.parametrize("body, expected", [
    ("  const fs = require('fs');", "only require('../support/fixtures')"),
    ("  const x = process.env.SECRET;", "forbidden: process"),
    ("  await page.evaluate(() => 1);", "forbidden: page.evaluate"),
    ("  await page.waitForTimeout(500);", "forbidden: waitForTimeout"),
    ("  await fetch('http://evil');", "forbidden: fetch"),
    ("  await page.getByRole('button').click();", "forbidden: getBy"),
    ("  await page.click('#login-btn');", "forbidden: page.click"),
    ("  await page.locator('#login-btn').click();", "locator() must take sel("),
    ("  await page.locator(sel('catalog.nope')).click();", "unknown selector key 'catalog.nope'"),
    ("  const k = 'auth.loginButton';\n  await page.locator(sel(k)).click();", "sel() needs a string literal"),
    ("  await page.goto('http://localhost:4002/catalog.html');", "page.goto must start with urls.<service>"),
    ("  await page.goto(urls.shipping + '/x');", "unknown service 'shipping'"),
    ("  await expect(page.locator(sel('cart.cartItem'))).not.toHaveText('Yoga Mat');", "forbidden: not.toHaveText"),
    ("  await expect(page.locator(sel('cart.cartItem'))).not.toContainText('Yoga Mat');", "forbidden: not.toHaveText"),
    ("  await page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' }).locator(sel('cart.checkoutButton')).click();",
     "cart.checkoutButton is not inside cart.cartItem"),
    ("  await page.locator(sel('catalog.addToCartButton')).click();", "catalog.addToCartButton must be located inside catalog.productCard"),
])
def test_violations_are_reported(body, expected):
    assert any(expected in e for e in errors(script(body))), errors(script(body))


def test_structure_rules():
    assert any("exactly one test(" in e for e in errors(HEADER + "test('a', async () => {});\ntest('b', async () => {});"))
    assert any("no expect(" in e for e in errors(HEADER + "test('a', async ({ page }) => { await page.goto(urls.auth + '/login.html'); });"))
    assert any("must start with" in e for e in errors("test('a', async () => { expect(1); });"))
    assert any("import" in e for e in errors("import { test } from '@playwright/test';\n" + script("")))


def test_syntax_errors_are_caught_by_node():
    assert any("syntax" in e for e in errors(script("  await page.goto(urls.auth + '/login.html'")))


def test_keyed_page_actions_are_rewritten_to_the_locator_form():
    from execution.script_validator import normalize_script
    code = script("  await page.fill(sel('order.shipName'), 'John Doe');\n"
                  "  await page.click(sel('cart.checkoutButton'));\n"
                  "  await page.fill(sel('payment.cardNumber'), \"4111, 1111\");")
    fixed = normalize_script(code)
    assert "await page.locator(sel('order.shipName')).fill('John Doe');" in fixed
    assert "await page.locator(sel('cart.checkoutButton')).click();" in fixed
    assert 'await page.locator(sel(\'payment.cardNumber\')).fill("4111, 1111");' in fixed
    assert validate_script(fixed, SERVICES).ok


def test_raw_selectors_are_not_rewritten_and_the_message_names_the_real_problem():
    from execution.script_validator import normalize_script
    code = script("  await page.fill('#ship-name', 'John Doe');")
    assert normalize_script(code) == code
    [message] = [e for e in errors(code) if e.startswith("forbidden")]
    assert "page.fill" in message and "page.locator(sel(" in message
