'use strict';
const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../server');
const { createApp: createCatalog } = require('../../catalog-service/server');
const { startApp } = require('../../shared/test-utils');

let catalog;
let cart;
before(async () => {
  catalog = await startApp(createCatalog({ variant: 'v1' }));
  cart = await startApp(createApp({ catalogUrl: catalog.url }));
});
after(async () => { await cart.close(); await catalog.close(); });
beforeEach(() => fetch(`${cart.url}/api/__reset`, { method: 'POST' }));

const SID = { cookie: 'apqa_sid=shopper-1' };
const add = (body, headers = SID) =>
  fetch(`${cart.url}/api/cart/add`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
const getCart = async (headers = SID) => (await fetch(`${cart.url}/api/cart`, { headers })).json();

test('adding a product stores name/price from catalog and totals the cart', async () => {
  const res = await add({ productId: 'p-100', qty: 2 });
  assert.equal(res.status, 201);
  const body = await getCart();
  assert.equal(body.items.length, 1);
  assert.deepEqual({ ...body.items[0], itemId: 'x' }, { itemId: 'x', productId: 'p-100', name: 'Wireless Headphones', price: 59.99, qty: 2 });
  assert.equal(body.total, 119.98);
});

test('adding the same product again merges quantity', async () => {
  await add({ productId: 'p-102' });
  await add({ productId: 'p-102', qty: 3 });
  const { items } = await getCart();
  assert.equal(items.length, 1);
  assert.equal(items[0].qty, 4);
});

test('carts are isolated per session id', async () => {
  await add({ productId: 'p-100' });
  assert.deepEqual((await getCart({ cookie: 'apqa_sid=someone-else' })).items, []);
});

test('qty boundaries: 1 and 10 accepted, 0, 11 and non-integers rejected', async () => {
  assert.equal((await add({ productId: 'p-102', qty: 1 })).status, 201);
  assert.equal((await add({ productId: 'p-105', qty: 10 })).status, 201);
  for (const qty of [0, 11, 1.5, '2']) {
    const res = await add({ productId: 'p-103', qty });
    assert.equal(res.status, 400, `qty ${qty}`);
    assert.equal((await res.json()).error, 'invalid_qty');
  }
  const merged = await add({ productId: 'p-105', qty: 1 });
  assert.equal(merged.status, 400, 'merged qty above 10 is rejected');
});

test('out-of-stock product is rejected with 409', async () => {
  const res = await add({ productId: 'p-104' });
  assert.equal(res.status, 409);
  assert.deepEqual(await res.json(), { error: 'out_of_stock', available: 0 });
});

test('quantity above stock is rejected with 409', async () => {
  const res = await add({ productId: 'p-101', qty: 6 }); // stock 5
  assert.equal(res.status, 409);
});

test('unknown product is 404', async () => {
  assert.equal((await add({ productId: 'nope' })).status, 404);
});

test('catalog outage is 502', async () => {
  const orphan = await startApp(createApp({ catalogUrl: 'http://127.0.0.1:1' }));
  try {
    const res = await fetch(`${orphan.url}/api/cart/add`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...SID }, body: JSON.stringify({ productId: 'p-100' }),
    });
    assert.equal(res.status, 502);
  } finally {
    await orphan.close();
  }
});

test('remove one item and clear the cart', async () => {
  await add({ productId: 'p-100' });
  await add({ productId: 'p-102' });
  const { items } = await getCart();
  const del = await fetch(`${cart.url}/api/cart/${items[0].itemId}`, { method: 'DELETE', headers: SID });
  assert.equal((await del.json()).items.length, 1);
  assert.equal((await fetch(`${cart.url}/api/cart/nope`, { method: 'DELETE', headers: SID })).status, 404);
  await fetch(`${cart.url}/api/cart`, { method: 'DELETE', headers: SID });
  assert.deepEqual(await getCart(), { items: [], total: 0 });
});

test('cart page renders rows, total and enabled checkout', async () => {
  await add({ productId: 'p-100' });
  const html = await (await fetch(`${cart.url}/cart.html`, { headers: SID })).text();
  assert.equal((html.match(/class="cart-item"/g) || []).length, 1);
  assert.match(html, /id="cart-total">\$59\.99</);
  assert.match(html, /<button id="checkout-btn" >Proceed to Checkout<\/button>/);
  assert.match(html, /<p id="empty-cart" hidden>/);
});

test('empty cart page disables checkout', async () => {
  const html = await (await fetch(`${cart.url}/cart.html`, { headers: SID })).text();
  assert.match(html, /<button id="checkout-btn" disabled>/);
  assert.match(html, /<p id="empty-cart" >Your cart is empty<\/p>/);
});

test('seeded changes: doubled total, renamed or missing checkout button', async () => {
  const configure = (list) => fetch(`${cart.url}/api/__config`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seeded_changes: list }) });
  await add({ productId: 'p-100' });
  try {
    await configure(['cart.total_doubled']);
    assert.equal((await getCart()).total, 119.98);

    await configure(['cart.checkout_button_renamed']);
    let html = await (await fetch(`${cart.url}/cart.html`, { headers: SID })).text();
    assert.match(html, /<button id="proceed-btn" >Proceed to Checkout<\/button>/);
    assert.doesNotMatch(html, /id="checkout-btn"/);

    await configure(['cart.checkout_button_missing']);
    html = await (await fetch(`${cart.url}/cart.html`, { headers: SID })).text();
    assert.doesNotMatch(html, /Proceed to Checkout/);
  } finally {
    await configure([]);
  }
});
