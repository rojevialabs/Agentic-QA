'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const express = require('express');
const kit = require('./kit');
const { startApp } = require('./test-utils');

test('parseCookies reads multiple cookies and skips malformed parts', () => {
  assert.deepEqual(kit.parseCookies('a=1; apqa_sid=abc%20d; junk; b = 2'), { a: '1', apqa_sid: 'abc d', b: '2' });
  assert.deepEqual(kit.parseCookies(undefined), {});
});

test('render escapes {{X}} and leaves {{{X}}} raw', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'kit-')), 't.html');
  fs.writeFileSync(file, '<p>{{NAME}}</p>{{{HTML}}}{{MISSING}}');
  assert.equal(kit.render(file, { NAME: '<b>&', HTML: '<i>x</i>' }), '<p>&lt;b&gt;&amp;</p><i>x</i>');
});

test('ensureSid issues a cookie when absent and reuses an existing one', async () => {
  const app = express();
  app.get('/sid', kit.ensureSid, (req, res) => res.json({ sid: req.sid }));
  const svc = await startApp(app);
  try {
    const fresh = await fetch(`${svc.url}/sid`);
    const { sid } = await fresh.json();
    assert.match(fresh.headers.get('set-cookie'), new RegExp(`^apqa_sid=${sid}; Path=/; SameSite=Lax`));

    const reused = await fetch(`${svc.url}/sid`, { headers: { cookie: 'apqa_sid=existing' } });
    assert.deepEqual(await reused.json(), { sid: 'existing' });
    assert.equal(reused.headers.get('set-cookie'), null);
  } finally {
    await svc.close();
  }
});

test('cors reflects the origin with credentials and answers preflight', async () => {
  const app = express();
  app.use(kit.cors);
  app.post('/x', (req, res) => res.json({ ok: true }));
  const svc = await startApp(app);
  try {
    const res = await fetch(`${svc.url}/x`, { method: 'OPTIONS', headers: { origin: 'http://localhost:4002' } });
    assert.equal(res.status, 204);
    assert.equal(res.headers.get('access-control-allow-origin'), 'http://localhost:4002');
    assert.equal(res.headers.get('access-control-allow-credentials'), 'true');
  } finally {
    await svc.close();
  }
});

test('publicUrls defaults to localhost ports and honours overrides', () => {
  assert.deepEqual(kit.publicUrls({}), {
    AUTH_URL: 'http://localhost:4001',
    CATALOG_URL: 'http://localhost:4002',
    CART_URL: 'http://localhost:4003',
    ORDER_URL: 'http://localhost:4004',
    PAYMENT_URL: 'http://localhost:4005',
  });
  assert.equal(kit.publicUrls({ PUBLIC_CART_URL: 'http://localhost:9003' }).CART_URL, 'http://localhost:9003');
});
