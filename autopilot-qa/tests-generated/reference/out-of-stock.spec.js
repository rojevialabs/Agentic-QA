const { test, expect, sel, urls } = require('../support/fixtures');

test('Reference: an out-of-stock product cannot be added', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.searchInput')).fill('grinder');
  await page.locator(sel('catalog.searchButton')).click();
  const card = page.locator(sel('catalog.productCard'), { hasText: 'Coffee Grinder' });
  await expect(card.locator(sel('catalog.productStock'))).toHaveText('Out of stock');
  await expect(page.locator(sel('catalog.productCard'), { hasText: 'Coffee Grinder' }).locator(sel('catalog.addToCartButton'))).toBeDisabled();
});
