'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, ensureSid, render, publicUrls, escapeHtml, seededChanges, configRoutes } = require('../shared/kit');

const MAX_QTY = 10;
const VIEW = path.join(__dirname, 'views', 'cart.html');
const round2 = (n) => Math.round(n * 100) / 100;

function checkoutButton(changes, disabled) {
  if (changes.has('cart.checkout_button_missing')) return ''; // seeded real defect
  const id = changes.has('cart.checkout_button_renamed') ? 'proceed-btn' : 'checkout-btn'; // seeded benign change
  return `<button id="${id}" ${disabled}>Proceed to Checkout</button>`;
}

async function fetchProduct(catalogUrl, productId) {
  if (typeof productId !== 'string' || !productId) return null;
  const res = await fetch(`${catalogUrl}/api/products/${encodeURIComponent(productId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`catalog responded ${res.status}`);
  return res.json();
}

function createApp({
  urls = publicUrls(),
  catalogUrl = process.env.CATALOG_SERVICE_URL || 'http://localhost:4002',
  changes = seededChanges(),
} = {}) {
  let carts; // sid -> [{ itemId, productId, name, price, qty }]
  const reset = () => { carts = new Map(); };
  reset();

  const cartOf = (sid) => {
    if (!carts.has(sid)) carts.set(sid, []);
    return carts.get(sid);
  };
  // Seeded real defect cart.total_doubled: a pricing bug that doubles every total.
  const factor = () => (changes.has('cart.total_doubled') ? 2 : 1);
  const summary = (items) => ({ items, total: round2(factor() * items.reduce((sum, i) => sum + i.price * i.qty, 0)) });
  const validQty = (q) => Number.isInteger(q) && q >= 1 && q <= MAX_QTY;

  const app = express();
  app.use(cors, express.json());
  configRoutes(app, 'cart', changes);

  app.get('/api/cart', ensureSid, (req, res) => res.json(summary(cartOf(req.sid))));

  app.post('/api/cart/add', ensureSid, async (req, res) => {
    const { productId, qty = 1 } = req.body || {};
    if (!validQty(qty)) return res.status(400).json({ error: 'invalid_qty', max: MAX_QTY });

    let product;
    try {
      product = await fetchProduct(catalogUrl, productId);
    } catch {
      return res.status(502).json({ error: 'catalog_unavailable' });
    }
    if (!product) return res.status(404).json({ error: 'product_not_found' });

    const items = cartOf(req.sid);
    const existing = items.find((i) => i.productId === productId);
    const newQty = (existing ? existing.qty : 0) + qty;
    if (!validQty(newQty)) return res.status(400).json({ error: 'invalid_qty', max: MAX_QTY });
    if (newQty > product.stock) return res.status(409).json({ error: 'out_of_stock', available: product.stock });

    if (existing) existing.qty = newQty;
    else items.push({ itemId: crypto.randomUUID(), productId, name: product.name, price: product.price, qty });
    res.status(201).json(summary(items));
  });

  app.delete('/api/cart/:itemId', ensureSid, (req, res) => {
    const items = cartOf(req.sid);
    const idx = items.findIndex((i) => i.itemId === req.params.itemId);
    if (idx === -1) return res.status(404).json({ error: 'item_not_found' });
    items.splice(idx, 1);
    res.json(summary(items));
  });

  // Used by order-service once an order has been created from the cart.
  app.delete('/api/cart', ensureSid, (req, res) => {
    carts.set(req.sid, []);
    res.json(summary([]));
  });

  app.post('/api/__reset', (req, res) => {
    reset();
    res.json({ ok: true, service: 'cart' });
  });

  app.get('/cart.html', ensureSid, (req, res) => {
    const { items, total } = summary(cartOf(req.sid));
    const rows = items
      .map(
        (i) => `      <tr class="cart-item" data-item-id="${escapeHtml(i.itemId)}">
        <td class="item-name">${escapeHtml(i.name)}</td>
        <td class="item-qty">${i.qty}</td>
        <td class="item-price">$${(i.price * i.qty).toFixed(2)}</td>
        <td><button class="remove-btn" data-item-id="${escapeHtml(i.itemId)}">Remove</button></td>
      </tr>`,
      )
      .join('\n');
    res.type('html').send(
      render(VIEW, {
        ...urls,
        CART_ROWS: rows,
        TOTAL: total.toFixed(2),
        EMPTY_HIDDEN: items.length ? 'hidden' : '',
        CHECKOUT_BUTTON: checkoutButton(changes, items.length ? '' : 'disabled'),
      }),
    );
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4003;
  createApp().listen(port, () => console.log(`cart-service listening on http://localhost:${port}`));
}

module.exports = { createApp };
