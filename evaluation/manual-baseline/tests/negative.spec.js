const { test, expect } = require('@playwright/test');
const { URLS, resetAll, goToPaymentWith } = require('./helpers');

test.beforeEach(async ({ request }) => resetAll(request));

test('expired payment card is declined', async ({ page }) => {
  await goToPaymentWith(page, 'p-102');
  await page.fill('#card-number', '4111111111111111');
  await page.fill('#expiry', '01/20');
  await page.click('#pay-btn');
  await expect(page.locator('#payment-status')).toHaveText('Payment declined: card expired');
  const orderId = await page.locator('#order-id').textContent();
  const order = await (await page.request.get(`${URLS.order}/api/orders/${orderId}`)).json();
  expect(order.status).toBe('pending_payment');
});

test('out-of-stock product cannot be added to cart', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html?q=grinder`);
  const card = page.locator('.product-card', { hasText: 'Coffee Grinder' });
  await expect(card.locator('.product-stock')).toHaveText('Out of stock');
  await expect(card.locator('button[data-product-id]')).toBeDisabled();
});

test('checkout is disabled for an empty cart', async ({ page }) => {
  await page.goto(`${URLS.cart}/cart.html`);
  await expect(page.locator('#empty-cart')).toBeVisible();
  await expect(page.locator('#checkout-btn')).toBeDisabled();
});

test('login rejects a wrong password', async ({ page }) => {
  await page.goto(`${URLS.auth}/login.html`);
  await page.fill('#email', 'demo@shop.test');
  await page.fill('#password', 'wrong-password');
  await page.click('#login-btn');
  await expect(page.locator('#message')).toHaveText('Invalid email or password');
});
