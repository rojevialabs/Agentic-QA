'use strict';
const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../server');
const { createApp: createCatalog } = require('../../catalog-service/server');
const { createApp: createCart } = require('../../cart-service/server');
const { startApp } = require('../../shared/test-utils');

let catalog, cart, order;
before(async () => {
  catalog = await startApp(createCatalog({ variant: 'v1' }));
  cart = await startApp(createCart({ catalogUrl: catalog.url }));
  order = await startApp(createApp({ cartUrl: cart.url }));
});
after(async () => { await order.close(); await cart.close(); await catalog.close(); });
beforeEach(async () => {
  await fetch(`${cart.url}/api/__reset`, { method: 'POST' });
  await fetch(`${order.url}/api/__reset`, { method: 'POST' });
});

const SID = { cookie: 'apqa_sid=buyer-1' };
const SHIPPING = { name: 'Ada Lovelace', address: '1 Analytical St', city: 'London', zip: 'N1 9GU' };
const json = { 'Content-Type': 'application/json' };
const addToCart = (productId, qty = 1) =>
  fetch(`${cart.url}/api/cart/add`, { method: 'POST', headers: { ...json, ...SID }, body: JSON.stringify({ productId, qty }) });
const placeOrder = (shipping = SHIPPING, headers = SID) =>
  fetch(`${order.url}/api/orders`, { method: 'POST', headers: { ...json, ...headers }, body: JSON.stringify({ shipping }) });
const markPaid = (id, body) =>
  fetch(`${order.url}/api/orders/${id}/paid`, { method: 'POST', headers: json, body: JSON.stringify(body) });

test('placing an order snapshots the cart, sets pending_payment and clears the cart', async () => {
  await addToCart('p-100', 2);
  const res = await placeOrder();
  assert.equal(res.status, 201);
  const created = await res.json();
  assert.match(created.orderId, /^ord-[0-9a-f]{8}$/);
  assert.equal(created.status, 'pending_payment');
  assert.equal(created.total, 119.98);
  assert.equal(created.items[0].name, 'Wireless Headphones');
  assert.equal(created.sid, undefined, 'sid must not leak');

  const cartNow = await (await fetch(`${cart.url}/api/cart`, { headers: SID })).json();
  assert.deepEqual(cartNow.items, []);
});

test('empty cart cannot be ordered', async () => {
  const res = await placeOrder();
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, 'cart_empty');
});

test('missing shipping fields are listed', async () => {
  await addToCart('p-100');
  const res = await placeOrder({ name: 'Ada', address: ' ', city: 'London' });
  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'invalid_shipping', missing: ['address', 'zip'] });
});

test('get order by id and list orders for the session', async () => {
  await addToCart('p-102');
  const { orderId } = await (await placeOrder()).json();
  assert.equal((await (await fetch(`${order.url}/api/orders/${orderId}`)).json()).orderId, orderId);
  assert.equal((await fetch(`${order.url}/api/orders/ord-missing`)).status, 404);
  const mine = await (await fetch(`${order.url}/api/orders`, { headers: SID })).json();
  assert.deepEqual(mine.map((o) => o.orderId), [orderId]);
  const theirs = await (await fetch(`${order.url}/api/orders`, { headers: { cookie: 'apqa_sid=other' } })).json();
  assert.deepEqual(theirs, []);
});

test('payment callback marks paid once and checks the amount', async () => {
  await addToCart('p-102'); // 24.00
  const { orderId } = await (await placeOrder()).json();
  const wrong = await markPaid(orderId, { paymentId: 'pay-1', amount: 1 });
  assert.equal(wrong.status, 409);
  assert.equal((await wrong.json()).error, 'amount_mismatch');

  const ok = await markPaid(orderId, { paymentId: 'pay-1', amount: 24 });
  assert.equal(ok.status, 200);
  const paid = await ok.json();
  assert.equal(paid.status, 'paid');
  assert.equal(paid.paymentId, 'pay-1');

  assert.equal((await markPaid(orderId, { paymentId: 'pay-2', amount: 24 })).status, 409);
  assert.equal((await markPaid('ord-missing', { paymentId: 'x', amount: 1 })).status, 404);
});

test('cart outage is 502', async () => {
  const orphan = await startApp(createApp({ cartUrl: 'http://127.0.0.1:1' }));
  try {
    const res = await fetch(`${orphan.url}/api/orders`, { method: 'POST', headers: { ...json, ...SID }, body: JSON.stringify({ shipping: SHIPPING }) });
    assert.equal(res.status, 502);
  } finally {
    await orphan.close();
  }
});

test('checkout page shows the cart total and the form ids', async () => {
  await addToCart('p-100');
  const html = await (await fetch(`${order.url}/checkout.html`, { headers: SID })).text();
  for (const id of ['ship-name', 'ship-address', 'ship-city', 'ship-zip', 'place-order-btn']) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /id="checkout-total">\$59\.99</);
  assert.match(html, /http:\/\/localhost:4005\/payment\.html/);
});

test('seeded change: renamed place-order button', async () => {
  const configure = (list) => fetch(`${order.url}/api/__config`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seeded_changes: list }) });
  try {
    await configure(['order.place_order_renamed']);
    const html = await (await fetch(`${order.url}/checkout.html`, { headers: SID })).text();
    assert.match(html, /<button id="confirm-order-btn" type="submit">Confirm Order<\/button>/);
    assert.doesNotMatch(html, /id="place-order-btn"/);
  } finally {
    await configure([]);
  }
});
