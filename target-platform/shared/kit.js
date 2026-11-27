'use strict';
// Shared helpers for the ShopLite target platform. Test-platform code: permissive by design.
const fs = require('node:fs');
const crypto = require('node:crypto');

const SID_COOKIE = 'apqa_sid';

function parseCookies(header = '') {
  const out = {};
  for (const part of String(header).split(';')) {
    const idx = part.indexOf('=');
    if (idx === -1) continue;
    const key = part.slice(0, idx).trim();
    const raw = part.slice(idx + 1).trim();
    if (!key) continue;
    try {
      out[key] = decodeURIComponent(raw);
    } catch {
      out[key] = raw;
    }
  }
  return out;
}

// Cookies are not port-scoped, so one sid is shared by every localhost:40xx service.
function ensureSid(req, res, next) {
  let sid = parseCookies(req.headers.cookie)[SID_COOKIE];
  if (!sid) {
    sid = crypto.randomUUID();
    res.append('Set-Cookie', `${SID_COOKIE}=${sid}; Path=/; SameSite=Lax`);
  }
  req.sid = sid;
  next();
}

const sidCookieHeader = (sid) => `${SID_COOKIE}=${encodeURIComponent(sid)}`;

// Reflects any Origin so pages served on one port can call APIs on another with cookies.
function cors(req, res, next) {
  const origin = req.headers.origin;
  if (origin) {
    res.set('Access-Control-Allow-Origin', origin);
    res.set('Access-Control-Allow-Credentials', 'true');
    res.set('Access-Control-Allow-Headers', 'Content-Type');
    res.set('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
    res.set('Vary', 'Origin');
  }
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
}

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

// {{{KEY}}} inserts raw HTML; {{KEY}} inserts an escaped value. Missing keys render empty.
function render(templatePath, vars) {
  return fs
    .readFileSync(templatePath, 'utf8')
    .replace(/\{\{\{(\w+)\}\}\}/g, (_, k) => String(vars[k] ?? ''))
    .replace(/\{\{(\w+)\}\}/g, (_, k) => escapeHtml(vars[k] ?? ''));
}

function publicUrls(env = process.env) {
  return {
    AUTH_URL: env.PUBLIC_AUTH_URL || 'http://localhost:4001',
    CATALOG_URL: env.PUBLIC_CATALOG_URL || 'http://localhost:4002',
    CART_URL: env.PUBLIC_CART_URL || 'http://localhost:4003',
    ORDER_URL: env.PUBLIC_ORDER_URL || 'http://localhost:4004',
    PAYMENT_URL: env.PUBLIC_PAYMENT_URL || 'http://localhost:4005',
  };
}

// Evaluation hooks (test-only): seeded UI changes and defects, see evaluation/seeded-bugs/registry.json.
function seededChanges(env = process.env) {
  return new Set(String(env.SEEDED_CHANGES || '').split(',').map((s) => s.trim()).filter(Boolean));
}

// GET/POST /api/__config lets the experiment runner switch seeded changes without restarting the service.
function configRoutes(app, service, changes) {
  const describe = () => ({ service, seeded_changes: [...changes] });
  app.get('/api/__config', (req, res) => res.json(describe()));
  app.post('/api/__config', (req, res) => {
    const list = req.body && req.body.seeded_changes;
    if (!Array.isArray(list)) return res.status(400).json({ error: 'seeded_changes must be an array' });
    changes.clear();
    for (const change of list) changes.add(String(change));
    res.json(describe());
  });
}

module.exports = {
  SID_COOKIE, parseCookies, ensureSid, sidCookieHeader, cors, escapeHtml, render, publicUrls, seededChanges, configRoutes,
};
