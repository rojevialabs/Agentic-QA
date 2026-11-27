'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, render, publicUrls, seededChanges, configRoutes } = require('../shared/kit');

const CARD_RE = /^\d{16}$/;
const EXPIRY_RE = /^(0[1-9]|1[0-2])\/(\d{2})$/;
const VIEW = path.join(__dirname, 'views', 'payment.html');

// A card is valid through the last day of its expiry month (MM/YY, 20YY).
function isExpired(expiry, now) {
  const [, mm, yy] = EXPIRY_RE.exec(expiry);
  const firstOfNextMonth = new Date(Date.UTC(2000 + Number(yy), Number(mm), 1)); // Date.UTC months are 0-based
  return now >= firstOfNextMonth;
}

const decline = (res, status, error) => res.status(status).json({ status: 'declined', error });

function createApp({
  urls = publicUrls(),
  orderUrl = process.env.ORDER_SERVICE_URL || 'http://localhost:4004',
  now = () => new Date(),
  changes = seededChanges(),
} = {}) {
  let payments; // paymentId -> payment
  const reset = () => { payments = new Map(); };
  reset();

  const app = express();
  app.use(cors, express.json());
  configRoutes(app, 'payment', changes);

  app.post('/api/payments', async (req, res) => {
    const { cardNumber, expiry, amount, orderId } = req.body || {};
    const digits = typeof cardNumber === 'string' ? cardNumber.replace(/\s+/g, '') : '';
    if (!CARD_RE.test(digits)) return decline(res, 400, 'invalid_card_number');
    if (typeof expiry !== 'string' || !EXPIRY_RE.test(expiry)) return decline(res, 400, 'invalid_expiry');
    if (typeof amount !== 'number' || !(amount > 0)) return decline(res, 400, 'invalid_amount');
    // Seeded real defect payment.expired_card_accepted skips the expiry check.
    if (!changes.has('payment.expired_card_accepted') && isExpired(expiry, now())) return decline(res, 402, 'card_expired');

    const payment = {
      paymentId: `pay-${crypto.randomUUID().slice(0, 8)}`,
      orderId: orderId ?? null,
      amount,
      last4: digits.slice(-4),
      status: 'approved',
      createdAt: now().toISOString(),
    };

    if (orderId) {
      let orderRes;
      try {
        orderRes = await fetch(`${orderUrl}/api/orders/${encodeURIComponent(orderId)}/paid`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paymentId: payment.paymentId, amount }),
        });
      } catch {
        return decline(res, 502, 'order_unavailable');
      }
      if (!orderRes.ok) {
        const { error } = await orderRes.json().catch(() => ({ error: 'order_rejected' }));
        return decline(res, orderRes.status, error);
      }
    }

    payments.set(payment.paymentId, payment);
    res.status(201).json(payment);
  });

  app.get('/api/payments/:id', (req, res) => {
    const payment = payments.get(req.params.id);
    if (!payment) return res.status(404).json({ error: 'payment_not_found' });
    res.json(payment);
  });

  app.post('/api/__reset', (req, res) => {
    reset();
    res.json({ ok: true, service: 'payment' });
  });

  app.get('/payment.html', (req, res) => {
    const orderId = typeof req.query.orderId === 'string' ? req.query.orderId : '';
    const amount = Number(req.query.amount);
    res.type('html').send(
      render(VIEW, {
        ...urls, ORDER_ID: orderId, AMOUNT: Number.isFinite(amount) ? amount.toFixed(2) : '0.00',
        PAY_BUTTON: changes.has('payment.pay_button_renamed') // seeded benign change
          ? '<button id="submit-payment-btn" type="submit">Pay now</button>'
          : '<button id="pay-btn" type="submit">Pay</button>',
      }),
    );
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4005;
  createApp().listen(port, () => console.log(`payment-service listening on http://localhost:${port}`));
}

module.exports = { createApp, isExpired };
