const { test, expect, sel, urls } = require('../support/fixtures');

test('Reference: an expired card is declined', async ({ page }) => {
  await page.goto(urls.catalog + '/catalog.html');
  await page.locator(sel('catalog.searchInput')).fill('charger');
  await page.locator(sel('catalog.searchButton')).click();
  await page.locator(sel('catalog.productCard'), { hasText: 'USB-C Charger' }).locator(sel('catalog.addToCartButton')).click();
  await expect(page.locator(sel('catalog.cartMessage'))).toHaveText('Added USB-C Charger to cart');
  await page.goto(urls.cart + '/cart.html');
  await page.locator(sel('cart.checkoutButton')).click();
  await page.locator(sel('order.shipName')).fill('Ada Lovelace');
  await page.locator(sel('order.shipAddress')).fill('1 Analytical St');
  await page.locator(sel('order.shipCity')).fill('London');
  await page.locator(sel('order.shipZip')).fill('N1 9GU');
  await page.locator(sel('order.placeOrderButton')).click();
  await expect(page).toHaveURL(/payment\.html/);
  await page.locator(sel('payment.cardNumber')).fill('4111111111111111');
  await page.locator(sel('payment.expiry')).fill('01/20');
  await page.locator(sel('payment.payButton')).click();
  await expect(page.locator(sel('payment.paymentStatus'))).toHaveText('Payment declined: card expired');
});
