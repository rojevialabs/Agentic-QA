const { expect } = require('@playwright/test');

const URLS = {
  auth: 'http://localhost:4001',
  catalog: 'http://localhost:4002',
  cart: 'http://localhost:4003',
  order: 'http://localhost:4004',
  payment: 'http://localhost:4005',
};

async function resetAll(request) {
  for (const base of Object.values(URLS)) {
    const res = await request.post(`${base}/api/__reset`);
    expect(res.ok(), `${base} reset`).toBeTruthy();
  }
}

// Seeds the cart through the API (not the catalog UI) so payment tests don't depend on the add button variant.
async function goToPaymentWith(page, productId) {
  await page.goto(`${URLS.catalog}/catalog.html`); // issues the apqa_sid cookie
  const add = await page.request.post(`${URLS.cart}/api/cart/add`, { data: { productId, qty: 1 } });
  expect(add.status()).toBe(201);
  await page.goto(`${URLS.order}/checkout.html`);
  await page.fill('#ship-name', 'Ada Lovelace');
  await page.fill('#ship-address', '1 Analytical St');
  await page.fill('#ship-city', 'London');
  await page.fill('#ship-zip', 'N1 9GU');
  await page.click('#place-order-btn');
  await expect(page).toHaveURL(/localhost:4005\/payment\.html\?orderId=ord-/);
}

module.exports = { URLS, resetAll, goToPaymentWith };
