You write one Playwright test in JavaScript for ShopLite, a small e-commerce web app, from a manual test case.

Hard rules (a validator rejects the script otherwise):
1. The first line is exactly: const { test, expect, sel, urls } = require('../support/fixtures');
2. Exactly one test('<test case title>', async ({ page }) => { ... }); no other require, import or helper code.
3. Reach elements ONLY through selector keys: page.locator(sel('<service>.<element>')).
   An element marked "inside X" must be located inside X, filtered by visible text:
   page.locator(sel('catalog.productCard'), { hasText: 'Yoga Mat' }).locator(sel('catalog.addToCartButton'))
   Never write CSS selectors, getByRole/getByText, page.click('...') or page.fill('...') directly.
4. Navigate ONLY with page.goto(urls.<service> + '<page path>') using the page paths listed below.
5. Act with .click() / .fill('value') on locators. Assert with expect(locator).toHaveText / toContainText /
   toBeVisible / toBeHidden / toBeDisabled / toBeEnabled / toHaveCount, or expect(page).toHaveURL(/regex/).
   Never use waitForTimeout: expect() waits automatically.
6. After clicking an add-to-cart button, assert the cart message before navigating away (the add is asynchronous).
7. To pay, first create an order: add a product in the catalog, open the cart, click checkout, fill ALL FOUR shipping
   fields (order.shipName, order.shipAddress, order.shipCity, order.shipZip) and click order.placeOrderButton;
   the browser then lands on the payment page. Never open the checkout or payment page with page.goto: they only
   work when reached through this flow.
8. End with at least one expect() that checks the test case's expected result.
   To check which page the browser is on, match the page path listed below, e.g. expect(page).toHaveURL(/payment\.html/).
   Elements without "(inside ...)" are located directly on the page: page.locator(sel('cart.checkoutButton')).
9. To check that rows or cards were added or removed, count them: expect(page.locator(sel('cart.cartItem'))).toHaveCount(1),
   or for one row: expect(page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' })).toHaveCount(0).
   Never use .not.toHaveText or .not.toContainText.
10. Removing a cart row reloads the page: right after clicking a 'Remove' button, wait for the result with a toHaveCount
    assertion before doing anything else.
11. Assert ONLY values the test case or the UI text below states. Never invent numbers, counts or messages. A product price
    is shown as '$$' plus two decimals (for example '$$24.00'); the cart total is the sum of the rows. The search result line
    reads '<n> products' where n is how many products match the search words (searching 'headphones' shows '1 products').
12. 'Add to Cart' buttons exist only on the catalog page. Before EVERY add, navigate there with
    page.goto(urls.catalog + '/catalog.html') (then search for the product again if needed), even if the test case's steps
    seem to add it from the cart page. Never look for an add button on the cart page, and never use raw page.click.
13. Never click an element that the test case expects to be disabled (an empty cart's 'Proceed to Checkout', an out-of-stock
    product's 'Add to Cart'): assert expect(locator).toBeDisabled() instead.
14. Reply with the JavaScript only, in one ```javascript code block.

Selector keys (service.element — purpose) and pages:
$selector_catalogue

UI text and test data (assert these exact messages):
$test_data

Example of a correct script:
```javascript
$example```

Test case to automate:
Title: $title
Type: $type
Steps:
$steps
Expected result: $expected_result
