'use strict';
const path = require('node:path');
const express = require('express');
const { cors, ensureSid, render, publicUrls, seededChanges, configRoutes } = require('../shared/kit');

const SEED_USERS = [{ email: 'demo@shop.test', password: 'demo1234' }];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;
const VIEWS = path.join(__dirname, 'views');

function createApp({ urls = publicUrls(), changes = seededChanges() } = {}) {
  let users; // email -> { email, password }
  let sessions; // sid -> email
  const reset = () => {
    users = new Map(SEED_USERS.map((u) => [u.email, { ...u }]));
    sessions = new Map();
  };
  reset();

  const app = express();
  app.use(cors, express.json());
  configRoutes(app, 'auth', changes);

  app.post('/api/register', (req, res) => {
    const { email, password } = req.body || {};
    if (typeof email !== 'string' || !EMAIL_RE.test(email)) return res.status(400).json({ error: 'invalid_email' });
    if (typeof password !== 'string' || password.length < MIN_PASSWORD) {
      return res.status(400).json({ error: 'password_too_short', min: MIN_PASSWORD });
    }
    if (users.has(email)) return res.status(409).json({ error: 'email_taken' });
    users.set(email, { email, password });
    res.status(201).json({ email });
  });

  // Binds the email to the caller's existing sid so a cart built before login survives it.
  app.post('/api/login', ensureSid, (req, res) => {
    const { email, password } = req.body || {};
    const user = typeof email === 'string' ? users.get(email) : undefined;
    if (!user || user.password !== password) return res.status(401).json({ error: 'invalid_credentials' });
    sessions.set(req.sid, email);
    res.json({ token: req.sid, email });
  });

  app.get('/api/me', ensureSid, (req, res) => {
    const email = sessions.get(req.sid);
    if (!email) return res.status(401).json({ error: 'not_logged_in' });
    res.json({ email });
  });

  app.post('/api/__reset', (req, res) => {
    reset();
    res.json({ ok: true, service: 'auth' });
  });

  for (const page of ['login', 'register']) {
    app.get(`/${page}.html`, ensureSid, (req, res) => res.type('html').send(render(path.join(VIEWS, `${page}.html`), urls)));
  }
  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4001;
  createApp().listen(port, () => console.log(`auth-service listening on http://localhost:${port}`));
}

module.exports = { createApp };
