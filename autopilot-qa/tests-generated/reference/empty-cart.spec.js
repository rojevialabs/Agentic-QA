const { test, expect, sel, urls } = require('../support/fixtures');

test('Reference: checkout is disabled for an empty cart', async ({ page }) => {
  await page.goto(urls.cart + '/cart.html');
  await expect(page.locator(sel('cart.emptyCart'))).toBeVisible();
  await expect(page.locator(sel('cart.checkoutButton'))).toBeDisabled();
});
