const { test, expect } = require('@playwright/test');
const { URLS, resetAll } = require('./helpers');

// Proves the seeded v2 change is cosmetic: the renamed button still adds to cart.
test.skip(process.env.CATALOG_BUTTON_VARIANT !== 'v2', 'run with CATALOG_BUTTON_VARIANT=v2 against a v2 catalog');
test.beforeEach(async ({ request }) => resetAll(request));

test('v2 "Add to Bag" button still adds the product to the cart', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html?q=headphones`);
  const card = page.locator('.product-card', { hasText: 'Wireless Headphones' });
  await expect(card.locator('#add-to-cart-btn')).toHaveCount(0);
  await card.locator('#add-to-bag-btn').click();
  await expect(page.locator('#cart-message')).toHaveText('Added Wireless Headphones to cart');
});
