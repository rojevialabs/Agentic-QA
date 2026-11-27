'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, ensureSid, render, publicUrls, sidCookieHeader, seededChanges, configRoutes } = require('../shared/kit');

const SHIPPING_FIELDS = ['name', 'address', 'city', 'zip'];
const VIEW = path.join(__dirname, 'views', 'checkout.html');

function createApp({
  urls = publicUrls(),
  cartUrl = process.env.CART_SERVICE_URL || 'http://localhost:4003',
  changes = seededChanges(),
} = {}) {
  let orders; // orderId -> order (including the owning sid)
  const reset = () => { orders = new Map(); };
  reset();

  const publicOrder = ({ sid, ...rest }) => rest;
  const cartRequest = (sid, method = 'GET') => fetch(`${cartUrl}/api/cart`, { method, headers: { cookie: sidCookieHeader(sid) } });

  const app = express();
  app.use(cors, express.json());
  configRoutes(app, 'order', changes);

  app.post('/api/orders', ensureSid, async (req, res) => {
    const shipping = (req.body && req.body.shipping) || {};
    const missing = SHIPPING_FIELDS.filter((f) => typeof shipping[f] !== 'string' || !shipping[f].trim());
    if (missing.length) return res.status(400).json({ error: 'invalid_shipping', missing });

    let cart;
    try {
      const cartRes = await cartRequest(req.sid);
      if (!cartRes.ok) throw new Error(`cart responded ${cartRes.status}`);
      cart = await cartRes.json();
    } catch {
      return res.status(502).json({ error: 'cart_unavailable' });
    }
    if (!cart.items.length) return res.status(400).json({ error: 'cart_empty' });

    const order = {
      orderId: `ord-${crypto.randomUUID().slice(0, 8)}`,
      sid: req.sid,
      items: cart.items,
      total: cart.total,
      shipping: Object.fromEntries(SHIPPING_FIELDS.map((f) => [f, shipping[f].trim()])),
      status: 'pending_payment',
      createdAt: new Date().toISOString(),
    };
    orders.set(order.orderId, order);
    await cartRequest(req.sid, 'DELETE').catch(() => {}); // a stale cart is harmless; the order is already recorded
    res.status(201).json(publicOrder(order));
  });

  app.get('/api/orders', ensureSid, (req, res) => {
    res.json([...orders.values()].filter((o) => o.sid === req.sid).map(publicOrder));
  });

  app.get('/api/orders/:id', (req, res) => {
    const order = orders.get(req.params.id);
    if (!order) return res.status(404).json({ error: 'order_not_found' });
    res.json(publicOrder(order));
  });

  // Called by payment-service after an approved payment (confirmed flow G3).
  app.post('/api/orders/:id/paid', (req, res) => {
    const order = orders.get(req.params.id);
    if (!order) return res.status(404).json({ error: 'order_not_found' });
    if (order.status === 'paid') return res.status(409).json({ error: 'already_paid' });
    const { paymentId, amount } = req.body || {};
    if (amount !== order.total) return res.status(409).json({ error: 'amount_mismatch', expected: order.total });
    Object.assign(order, { status: 'paid', paymentId, paidAt: new Date().toISOString() });
    res.json(publicOrder(order));
  });

  app.post('/api/__reset', (req, res) => {
    reset();
    res.json({ ok: true, service: 'order' });
  });

  app.get('/checkout.html', ensureSid, async (req, res) => {
    let total = 'unavailable';
    try {
      const cart = await (await cartRequest(req.sid)).json();
      total = `$${cart.total.toFixed(2)}`;
    } catch {
      // Render the form anyway; placing the order will surface the cart error.
    }
    const renamed = changes.has('order.place_order_renamed'); // seeded benign change
    const button = renamed ? '<button id="confirm-order-btn" type="submit">Confirm Order</button>'
      : '<button id="place-order-btn" type="submit">Place Order</button>';
    res.type('html').send(render(VIEW, { ...urls, TOTAL: total, PLACE_ORDER_BUTTON: button }));
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4004;
  createApp().listen(port, () => console.log(`order-service listening on http://localhost:${port}`));
}

module.exports = { createApp };
