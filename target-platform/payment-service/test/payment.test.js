'use strict';
const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const { createApp, isExpired } = require('../server');
const { startApp } = require('../../shared/test-utils');

// A fake order-service lets us assert the callback payload directly.
const calls = [];
let fakeOrder, payment;
before(async () => {
  const app = express();
  app.use(express.json());
  app.post('/api/orders/:id/paid', (req, res) => {
    calls.push({ id: req.params.id, body: req.body });
    if (req.params.id === 'ord-missing') return res.status(404).json({ error: 'order_not_found' });
    if (req.params.id === 'ord-mismatch') return res.status(409).json({ error: 'amount_mismatch' });
    res.json({ orderId: req.params.id, status: 'paid' });
  });
  fakeOrder = await startApp(app);
  payment = await startApp(createApp({ orderUrl: fakeOrder.url, now: () => new Date('2026-09-15T12:00:00Z') }));
});
after(async () => { await payment.close(); await fakeOrder.close(); });
beforeEach(() => { calls.length = 0; });

const pay = (body) =>
  fetch(`${payment.url}/api/payments`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const CARD = { cardNumber: '4111 1111 1111 1111', expiry: '12/30', amount: 59.99 };

test('isExpired treats a card as valid through the end of its expiry month', () => {
  const now = new Date('2026-09-15T12:00:00Z');
  assert.equal(isExpired('09/26', now), false);
  assert.equal(isExpired('08/26', now), true);
  assert.equal(isExpired('12/26', now), false);
  assert.equal(isExpired('01/20', now), true);
});

test('valid card is approved and order-service is notified', async () => {
  const res = await pay({ ...CARD, orderId: 'ord-1' });
  assert.equal(res.status, 201);
  const body = await res.json();
  assert.equal(body.status, 'approved');
  assert.equal(body.last4, '1111');
  assert.deepEqual(calls, [{ id: 'ord-1', body: { paymentId: body.paymentId, amount: 59.99 } }]);
  const stored = await (await fetch(`${payment.url}/api/payments/${body.paymentId}`)).json();
  assert.equal(stored.orderId, 'ord-1');
});

test('expired card is declined with 402 and no callback', async () => {
  const res = await pay({ ...CARD, expiry: '08/26', orderId: 'ord-1' });
  assert.equal(res.status, 402);
  assert.deepEqual(await res.json(), { status: 'declined', error: 'card_expired' });
  assert.equal(calls.length, 0);
});

test('input validation', async () => {
  const cases = [
    [{ ...CARD, cardNumber: '4111' }, 'invalid_card_number'],
    [{ ...CARD, cardNumber: '4111-1111-1111-111a' }, 'invalid_card_number'],
    [{ ...CARD, expiry: '13/30' }, 'invalid_expiry'],
    [{ ...CARD, expiry: '2030-12' }, 'invalid_expiry'],
    [{ ...CARD, amount: 0 }, 'invalid_amount'],
    [{ ...CARD, amount: '59.99' }, 'invalid_amount'],
  ];
  for (const [body, error] of cases) {
    const res = await pay(body);
    assert.equal(res.status, 400, error);
    assert.deepEqual(await res.json(), { status: 'declined', error });
  }
});

test('order-service rejections pass through', async () => {
  assert.equal((await pay({ ...CARD, orderId: 'ord-missing' })).status, 404);
  const mismatch = await pay({ ...CARD, orderId: 'ord-mismatch' });
  assert.equal(mismatch.status, 409);
  assert.equal((await mismatch.json()).error, 'amount_mismatch');
});

test('order-service outage is 502', async () => {
  const orphan = await startApp(createApp({ orderUrl: 'http://127.0.0.1:1' }));
  try {
    const res = await fetch(`${orphan.url}/api/payments`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...CARD, orderId: 'ord-1' }),
    });
    assert.equal(res.status, 502);
  } finally {
    await orphan.close();
  }
});

test('payment page renders order id, amount and the form ids', async () => {
  const html = await (await fetch(`${payment.url}/payment.html?orderId=ord-42&amount=59.99`)).text();
  assert.match(html, /id="order-id">ord-42</);
  assert.match(html, /id="amount">\$59\.99</);
  for (const id of ['card-number', 'expiry', 'pay-btn', 'payment-status']) assert.match(html, new RegExp(`id="${id}"`));
});

test('__reset returns ok', async () => {
  const res = await fetch(`${payment.url}/api/__reset`, { method: 'POST' });
  assert.deepEqual(await res.json(), { ok: true, service: 'payment' });
});

test('seeded changes: renamed pay button and expired cards accepted', async () => {
  const configure = (list) => fetch(`${payment.url}/api/__config`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seeded_changes: list }) });
  try {
    await configure(['payment.pay_button_renamed', 'payment.expired_card_accepted']);
    const html = await (await fetch(`${payment.url}/payment.html?orderId=ord-1&amount=5`)).text();
    assert.match(html, /<button id="submit-payment-btn" type="submit">Pay now<\/button>/);
    assert.doesNotMatch(html, /id="pay-btn"/);
    assert.equal((await pay({ ...CARD, expiry: '01/20', orderId: 'ord-1' })).status, 201);
  } finally {
    await configure([]);
  }
  assert.equal((await pay({ ...CARD, expiry: '01/20' })).status, 402);
});
