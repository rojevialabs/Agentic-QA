const { test, expect } = require('@playwright/test');
const { URLS, resetAll } = require('./helpers');

test.beforeEach(async ({ request }) => resetAll(request));

test('user can search for a product, add it to cart, and complete checkout', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html`);
  await page.fill('#search-input', 'headphones');
  await page.click('#search-btn');
  const card = page.locator('.product-card', { hasText: 'Wireless Headphones' });
  await expect(card).toHaveCount(1);
  await card.locator('#add-to-cart-btn').click();
  await expect(page.locator('#cart-message')).toHaveText('Added Wireless Headphones to cart');
  await expect(page.locator('#cart-count')).toHaveText('1');

  await page.goto(`${URLS.cart}/cart.html`);
  await expect(page.locator('.cart-item')).toHaveCount(1);
  await expect(page.locator('#cart-total')).toHaveText('$59.99');
  await page.click('#checkout-btn');

  await expect(page).toHaveURL(`${URLS.order}/checkout.html`);
  await expect(page.locator('#checkout-total')).toHaveText('$59.99');
  await page.fill('#ship-name', 'Ada Lovelace');
  await page.fill('#ship-address', '1 Analytical St');
  await page.fill('#ship-city', 'London');
  await page.fill('#ship-zip', 'N1 9GU');
  await page.click('#place-order-btn');

  await expect(page).toHaveURL(/localhost:4005\/payment\.html\?orderId=ord-.+&amount=59\.99/);
  await page.fill('#card-number', '4111111111111111');
  await page.fill('#expiry', '12/30');
  await page.click('#pay-btn');
  await expect(page.locator('#payment-status')).toHaveText('Payment approved');

  const orderId = await page.locator('#order-id').textContent();
  const order = await (await page.request.get(`${URLS.order}/api/orders/${orderId}`)).json();
  expect(order.status).toBe('paid');
});
