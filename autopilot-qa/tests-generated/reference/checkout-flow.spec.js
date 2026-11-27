const { test, expect, sel, urls } = require('../support/fixtures');

test('Reference: search, add to cart, check out and pay', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.searchInput')).fill('headphones');
  await page.locator(sel('catalog.searchButton')).click();
  await page.locator(sel('catalog.productCard'), { hasText: 'Wireless Headphones' }).locator(sel('catalog.addToCartButton')).click();
  await expect(page.locator(sel('catalog.cartMessage'))).toHaveText('Added Wireless Headphones to cart');
  await page.goto(urls.cart + '/cart.html');
  await expect(page.locator(sel('cart.cartTotal'))).toHaveText('$59.99');
  await page.locator(sel('cart.checkoutButton')).click();
  await page.locator(sel('order.shipName')).fill('Ada Lovelace');
  await page.locator(sel('order.shipAddress')).fill('1 Analytical St');
  await page.locator(sel('order.shipCity')).fill('London');
  await page.locator(sel('order.shipZip')).fill('N1 9GU');
  await page.locator(sel('order.placeOrderButton')).click();
  await expect(page).toHaveURL(/payment\.html/);
  await page.locator(sel('payment.cardNumber')).fill('4111111111111111');
  await page.locator(sel('payment.expiry')).fill('12/30');
  await page.locator(sel('payment.payButton')).click();
  await expect(page.locator(sel('payment.paymentStatus'))).toHaveText('Payment approved');
});
