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


async def test_scope_counts_reveal_whether_a_locator_is_generic(verifier):
    assert await verifier.scope_counts(V2, "#add-to-bag-btn", CARD) == [1, 1, 1, 1, 1, 1]
    assert await verifier.scope_counts(V2, "button[data-product-id='p-100']", CARD) == [1, 0, 0, 0, 0, 0]
    assert await verifier.scope_counts(V2, "button[[", CARD) == [0, 0, 0, 0, 0, 0]
