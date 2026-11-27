const { test, expect, sel, urls } = require('../support/fixtures');

test('Removing the only item empties the cart', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.productCard'), { hasText: 'Yoga Mat' }).locator(sel('catalog.addToCartButton')).click();
  await expect(page.locator(sel('catalog.cartMessage'))).toHaveText('Added Yoga Mat to cart');
  await page.goto(urls.cart + '/cart.html');
  await page.locator(sel('cart.cartItem'), { hasText: 'Yoga Mat' }).locator(sel('cart.removeButton')).click();
  await expect(page.locator(sel('cart.emptyCart'))).toBeVisible();
  await expect(page.locator(sel('cart.cartTotal'))).toHaveText('$0.00');
  await expect(page.locator(sel('cart.checkoutButton'))).toBeDisabled();
});
