# Phase 1 — Target Platform "ShopLite" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 5-microservice e-commerce app that AutoPilot-QA tests. It needs a working search → cart → checkout → pay flow, a `CATALOG_BUTTON_VARIANT` v1/v2 toggle, `POST /api/__reset` on every service, and a hand-written Playwright baseline suite.

**Architecture:** An npm workspace in `target-platform/`, with one Express 4 app per service. Each `server.js` exports `createApp(config)` so tests can inject URLs, the variant and a clock, and it only listens when run directly. Services share nothing at runtime except the `apqa_sid` cookie, which is visible to every `localhost` port. A dependency-free `shared/kit.js` holds the cookie, CORS and template helpers. HTML is server-rendered from `views/*.html` with `{{X}}` (escaped) and `{{{X}}}` (raw) placeholders.

**Tech Stack:** Node 22, Express ^4.21.2, `node:test` + global `fetch`, `@playwright/test` ^1.55 (baseline suite), Docker Compose.

## Global Constraints

- Ports: auth 4001, catalog 4002, cart 4003, order 4004, payment 4005.
- Every service exposes `POST /api/__reset` → `200 {"ok": true, "service": "<name>"}` and restores its seed state.
- `CATALOG_BUTTON_VARIANT=v1` (default) renders `<button id="add-to-cart-btn">Add to Cart</button>`; `v2` renders `<button id="add-to-bag-btn">Add to Bag</button>`. Function is identical.
- At least one out-of-stock product (Coffee Grinder, `p-104`).
- `POST /api/payments` declines with 402 `card_expired` when the expiry month is in the past.
- Checkout flow (confirmed decision G3): `POST /api/orders` → `pending_payment` → `payment.html` → `POST /api/payments` → payment-service calls `POST /api/orders/:id/paid`.
- Public (browser) URLs always use `localhost`, never `127.0.0.1`, or the shared cookie splits.
- This is test-platform code: no auth hardening, permissive CORS, in-memory stores. Do not make it production-grade.
- All commands must work in Windows PowerShell.

## File Map

```
.gitignore
README.md
docs/AutoPilot-QA-Technical-Specification.md      (copy of the spec)
target-platform/
  package.json                 workspace root, test + start:all scripts
  README.md                    selectors, seed data, test cards (LLM platform context)
  Dockerfile                   one image per service via ARG SERVICE
  .dockerignore
  docker-compose.yml           5 services + mongo:7
  scripts/start-all.js         spawn all 5 locally (no Docker needed)
  shared/kit.js                cookies, CORS, escapeHtml, render, publicUrls
  shared/kit.test.js
  shared/test-utils.js         startApp(app) -> {url, close}
  auth-service/    package.json server.js views/login.html views/register.html test/auth.test.js
  catalog-service/ package.json server.js seed-data.json views/catalog.html test/catalog.test.js
  cart-service/    package.json server.js views/cart.html test/cart.test.js
  order-service/   package.json server.js views/checkout.html test/order.test.js
  payment-service/ package.json server.js views/payment.html test/payment.test.js
evaluation/manual-baseline/
  package.json playwright.config.js tests/helpers.js tests/checkout-flow.spec.js tests/negative.spec.js tests/variant-v2.spec.js
```

---

### Task 0: Repository bootstrap

**Files:**
- Create: `.gitignore`, `README.md`, `docs/AutoPilot-QA-Technical-Specification.md`, `.gitkeep` files in the empty skeleton dirs

**Interfaces:** Produces the top-level layout from spec §11.

- [ ] **Step 1: Initialise git and the skeleton**

```powershell
cd C:\BITS\autopilot-qa-project
git init
New-Item -ItemType Directory -Force autopilot-qa\backend, autopilot-qa\frontend, autopilot-qa\tests-generated, autopilot-qa\reports, autopilot-qa\docs, target-platform, evaluation\seeded-bugs, evaluation\manual-baseline, evaluation\metrics | Out-Null
foreach ($d in 'autopilot-qa\backend','autopilot-qa\frontend','autopilot-qa\tests-generated','autopilot-qa\reports','autopilot-qa\docs','evaluation\seeded-bugs','evaluation\metrics') { New-Item -ItemType File -Force "$d\.gitkeep" | Out-Null }
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
node_modules/
.venv/
__pycache__/
*.pyc
.env
test-results/
playwright-report/
artifacts/
autopilot-qa/tests-generated/*
!autopilot-qa/tests-generated/.gitkeep
!autopilot-qa/tests-generated/support/
autopilot-qa/reports/*
!autopilot-qa/reports/.gitkeep
```

- [ ] **Step 3: Write root `README.md`**

```markdown
# AutoPilot-QA

Multi-agent QA framework (local LLM via Ollama) that tests a 5-microservice e-commerce app and self-heals broken UI locators with a logged reasoning trace.

- `autopilot-qa/` — the framework (FastAPI backend, React dashboard)
- `target-platform/` — ShopLite, the sample app under test (see its README)
- `evaluation/` — seeded bugs, manual baseline suite, metrics
- `docs/` — specification and implementation plans (`docs/superpowers/plans/`)
```

- [ ] **Step 4: Copy the spec** from wherever the user keeps `AutoPilot-QA-End-to-End-Spec.md` to `docs/AutoPilot-QA-Technical-Specification.md`. If the file isn't on disk, ask the user for its path. Do not retype it.

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "chore: bootstrap repository skeleton and planning docs"
```

---

### Task 1: Workspace root and shared kit

**Files:**
- Create: `target-platform/package.json`, `target-platform/shared/kit.js`, `target-platform/shared/test-utils.js`, `target-platform/shared/kit.test.js`
- Create (placeholders so the workspace installs): `target-platform/<each service>/package.json`

**Interfaces:**
- Produces (`shared/kit.js`): `SID_COOKIE = 'apqa_sid'`, `parseCookies(header) -> object`, `ensureSid(req,res,next)` (sets `req.sid`, issues a cookie if it's missing), `sidCookieHeader(sid) -> 'apqa_sid=<sid>'`, `cors(req,res,next)`, `escapeHtml(s) -> string`, `render(templatePath, vars) -> string`, `publicUrls(env?) -> {AUTH_URL, CATALOG_URL, CART_URL, ORDER_URL, PAYMENT_URL}`.
- Produces (`shared/test-utils.js`): `startApp(app) -> Promise<{url, close}>`.

- [ ] **Step 1: Write `target-platform/package.json`**

```json
{
  "name": "target-platform",
  "private": true,
  "workspaces": ["auth-service", "catalog-service", "cart-service", "order-service", "payment-service"],
  "scripts": {
    "start:all": "node scripts/start-all.js",
    "test": "node --test shared/kit.test.js && npm test --workspaces"
  },
  "devDependencies": {
    "express": "^4.21.2"
  }
}
```

- [ ] **Step 2: Write each service `package.json`.** Five files; replace `<name>` with `auth-service`, `catalog-service`, `cart-service`, `order-service`, `payment-service`.

```json
{
  "name": "<name>",
  "version": "0.1.0",
  "private": true,
  "main": "server.js",
  "scripts": {
    "start": "node server.js",
    "test": "node --test"
  },
  "dependencies": {
    "express": "^4.21.2"
  }
}
```

Run: `cd target-platform; npm install`
Expected: `added N packages`, with `node_modules/express` at the workspace root.

- [ ] **Step 3: Write the failing kit test** `target-platform/shared/kit.test.js`

```js
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
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd target-platform; node --test shared/kit.test.js`
Expected: FAIL with `Cannot find module './kit'`

- [ ] **Step 5: Write `target-platform/shared/kit.js`**

```js
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

module.exports = { SID_COOKIE, parseCookies, ensureSid, sidCookieHeader, cors, escapeHtml, render, publicUrls };
```

- [ ] **Step 6: Write `target-platform/shared/test-utils.js`**

```js
'use strict';

// Starts an Express app on an ephemeral port. close() also drops keep-alive sockets held by fetch.
function startApp(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}`,
        close: () =>
          new Promise((done) => {
            server.closeAllConnections();
            server.close(done);
          }),
      });
    });
  });
}

module.exports = { startApp };
```

- [ ] **Step 7: Run it to verify it passes**

Run: `node --test shared/kit.test.js`
Expected: `# pass 5`, `# fail 0`

- [ ] **Step 8: Commit**

```powershell
git add target-platform/package.json target-platform/package-lock.json target-platform/shared target-platform/*/package.json
git commit -m "feat(target): npm workspace and shared cookie/cors/render kit"
```

---

### Task 2: auth-service (port 4001)

**Files:**
- Create: `target-platform/auth-service/server.js`, `views/login.html`, `views/register.html`, `test/auth.test.js`

**Interfaces:**
- Consumes: `cors, ensureSid, render, publicUrls` from `../shared/kit`, and `startApp` from `../../shared/test-utils`.
- Produces: `createApp({ urls? }) -> express.App`. API: `POST /api/register {email,password}` → 201 `{email}` | 400 `invalid_email` / `password_too_short` | 409 `email_taken`. `POST /api/login` → 200 `{token, email}` (token = sid) | 401 `invalid_credentials`. `GET /api/me` → 200 `{email}` | 401. `POST /api/__reset`. Seed user `demo@shop.test` / `demo1234`. Page ids: `#email #password #login-btn #register-btn #message` (`data-state` = `success|error`).

- [ ] **Step 1: Write the failing test** `target-platform/auth-service/test/auth.test.js`

```js
'use strict';
const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../server');
const { startApp } = require('../../shared/test-utils');

let svc;
beforeEach(async () => { svc = await startApp(createApp()); });
afterEach(() => svc.close());

const post = (path, body, headers = {}) =>
  fetch(svc.url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });

test('seeded demo user logs in and the token is the session id', async () => {
  const res = await post('/api/login', { email: 'demo@shop.test', password: 'demo1234' }, { cookie: 'apqa_sid=sid-1' });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { token: 'sid-1', email: 'demo@shop.test' });

  const me = await fetch(`${svc.url}/api/me`, { headers: { cookie: 'apqa_sid=sid-1' } });
  assert.deepEqual(await me.json(), { email: 'demo@shop.test' });
});

test('login without a cookie issues one', async () => {
  const res = await post('/api/login', { email: 'demo@shop.test', password: 'demo1234' });
  assert.equal(res.status, 200);
  assert.match(res.headers.get('set-cookie'), /^apqa_sid=/);
});

test('wrong password is rejected with 401', async () => {
  const res = await post('/api/login', { email: 'demo@shop.test', password: 'nope' });
  assert.equal(res.status, 401);
  assert.deepEqual(await res.json(), { error: 'invalid_credentials' });
});

test('/api/me is 401 for an anonymous session', async () => {
  const res = await fetch(`${svc.url}/api/me`, { headers: { cookie: 'apqa_sid=anon' } });
  assert.equal(res.status, 401);
});

test('register creates an account that can log in', async () => {
  assert.equal((await post('/api/register', { email: 'new@shop.test', password: 'longenough' })).status, 201);
  assert.equal((await post('/api/login', { email: 'new@shop.test', password: 'longenough' })).status, 200);
});

test('register validates email, password length and duplicates', async () => {
  assert.deepEqual(await (await post('/api/register', { email: 'bad', password: 'longenough' })).json(), { error: 'invalid_email' });
  const short = await post('/api/register', { email: 'x@shop.test', password: 'short' });
  assert.equal(short.status, 400);
  assert.equal((await short.json()).error, 'password_too_short');
  const dup = await post('/api/register', { email: 'demo@shop.test', password: 'longenough' });
  assert.equal(dup.status, 409);
});

test('__reset forgets registered users', async () => {
  await post('/api/register', { email: 'tmp@shop.test', password: 'longenough' });
  const reset = await post('/api/__reset', {});
  assert.deepEqual(await reset.json(), { ok: true, service: 'auth' });
  assert.equal((await post('/api/login', { email: 'tmp@shop.test', password: 'longenough' })).status, 401);
});

test('login and register pages expose the form ids', async () => {
  const login = await (await fetch(`${svc.url}/login.html`)).text();
  for (const id of ['email', 'password', 'login-btn', 'message']) assert.match(login, new RegExp(`id="${id}"`));
  assert.match(login, /href="http:\/\/localhost:4002\/catalog.html"/);
  const register = await (await fetch(`${svc.url}/register.html`)).text();
  assert.match(register, /id="register-btn"/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w auth-service`
Expected: FAIL with `Cannot find module '../server'`

- [ ] **Step 3: Write `target-platform/auth-service/server.js`**

```js
'use strict';
const path = require('node:path');
const express = require('express');
const { cors, ensureSid, render, publicUrls } = require('../shared/kit');

const SEED_USERS = [{ email: 'demo@shop.test', password: 'demo1234' }];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;
const VIEWS = path.join(__dirname, 'views');

function createApp({ urls = publicUrls() } = {}) {
  let users; // email -> { email, password }
  let sessions; // sid -> email
  const reset = () => {
    users = new Map(SEED_USERS.map((u) => [u.email, { ...u }]));
    sessions = new Map();
  };
  reset();

  const app = express();
  app.use(cors, express.json());

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
```

- [ ] **Step 4: Write `target-platform/auth-service/views/login.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Log in · ShopLite</title></head>
<body>
  <nav>
    <a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a> |
    <a id="nav-cart" href="{{CART_URL}}/cart.html">Cart</a> |
    <a id="nav-register" href="{{AUTH_URL}}/register.html">Register</a>
  </nav>
  <h1>Log in</h1>
  <form id="login-form" novalidate>
    <label for="email">Email</label>
    <input id="email" name="email" type="email">
    <label for="password">Password</label>
    <input id="password" name="password" type="password">
    <button id="login-btn" type="submit">Log in</button>
  </form>
  <p id="message" role="status"></p>
  <script>
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: e.target.email.value, password: e.target.password.value }),
      });
      const msg = document.getElementById('message');
      msg.textContent = res.ok ? 'Logged in' : 'Invalid email or password';
      msg.dataset.state = res.ok ? 'success' : 'error';
    });
  </script>
</body>
</html>
```

- [ ] **Step 5: Write `target-platform/auth-service/views/register.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Register · ShopLite</title></head>
<body>
  <nav>
    <a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a> |
    <a id="nav-login" href="{{AUTH_URL}}/login.html">Log in</a>
  </nav>
  <h1>Create an account</h1>
  <form id="register-form" novalidate>
    <label for="email">Email</label>
    <input id="email" name="email" type="email">
    <label for="password">Password</label>
    <input id="password" name="password" type="password">
    <button id="register-btn" type="submit">Register</button>
  </form>
  <p id="message" role="status"></p>
  <script>
    const ERRORS = {
      invalid_email: 'Please enter a valid email',
      password_too_short: 'Password must be at least 8 characters',
      email_taken: 'An account with this email already exists',
    };
    document.getElementById('register-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const res = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: e.target.email.value, password: e.target.password.value }),
      });
      const msg = document.getElementById('message');
      if (res.ok) {
        msg.textContent = 'Account created';
        msg.dataset.state = 'success';
      } else {
        const { error } = await res.json();
        msg.textContent = ERRORS[error] || 'Registration failed';
        msg.dataset.state = 'error';
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test -w auth-service`
Expected: `# pass 8`, `# fail 0`

- [ ] **Step 7: Commit**

```powershell
git add target-platform/auth-service
git commit -m "feat(target): auth-service with register/login pages and reset"
```

---

### Task 3: catalog-service (port 4002) with the button-variant toggle

**Files:**
- Create: `target-platform/catalog-service/seed-data.json`, `server.js`, `views/catalog.html`, `test/catalog.test.js`

**Interfaces:**
- Consumes: `cors, ensureSid, render, publicUrls, escapeHtml` from kit.
- Produces: `createApp({ urls?, variant? })` (variant defaults to `process.env.CATALOG_BUTTON_VARIANT || 'v1'`; an unknown value throws). API: `GET /api/products?q=` → array; `GET /api/products/:id` → product | 404 `not_found`; `POST /api/__reset`. Product shape: `{id, name, description, price, stock}`. Page: `GET /catalog.html?q=` with `#search-input #search-btn #result-count #product-list .product-card[data-product-id] .product-name .product-price .product-stock #cart-message #cart-count`, and the variant button carrying `data-product-id`.

- [ ] **Step 1: Write `target-platform/catalog-service/seed-data.json`**

```json
[
  { "id": "p-100", "name": "Wireless Headphones", "description": "Over-ear bluetooth headphones with noise cancelling", "price": 59.99, "stock": 12 },
  { "id": "p-101", "name": "Mechanical Keyboard", "description": "Tenkeyless keyboard with brown switches", "price": 89.5, "stock": 5 },
  { "id": "p-102", "name": "USB-C Charger", "description": "65W fast charger", "price": 24.0, "stock": 30 },
  { "id": "p-103", "name": "Running Shoes", "description": "Lightweight road running shoes", "price": 74.95, "stock": 8 },
  { "id": "p-104", "name": "Coffee Grinder", "description": "Burr grinder with 18 settings", "price": 45.0, "stock": 0 },
  { "id": "p-105", "name": "Yoga Mat", "description": "6mm non-slip exercise mat", "price": 19.99, "stock": 20 }
]
```

- [ ] **Step 2: Write the failing test** `target-platform/catalog-service/test/catalog.test.js`

```js
'use strict';
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../server');
const { startApp } = require('../../shared/test-utils');

let svc;
const start = async (opts) => { svc = await startApp(createApp(opts)); };
afterEach(() => svc && svc.close());

const getJson = async (path) => (await fetch(svc.url + path)).json();
const page = async (path = '/catalog.html') => (await fetch(svc.url + path)).text();
const count = (html, re) => (html.match(re) || []).length;

test('lists all six seeded products', async () => {
  await start({ variant: 'v1' });
  const products = await getJson('/api/products');
  assert.equal(products.length, 6);
  assert.ok(products.some((p) => p.stock === 0), 'at least one product must be out of stock');
});

test('search matches name or description case-insensitively', async () => {
  await start({ variant: 'v1' });
  assert.deepEqual((await getJson('/api/products?q=HEADPHONES')).map((p) => p.id), ['p-100']);
  assert.deepEqual((await getJson('/api/products?q=burr')).map((p) => p.id), ['p-104']);
  assert.deepEqual(await getJson('/api/products?q=zzz'), []);
});

test('get product by id, 404 for unknown', async () => {
  await start({ variant: 'v1' });
  assert.equal((await getJson('/api/products/p-102')).name, 'USB-C Charger');
  const res = await fetch(`${svc.url}/api/products/nope`);
  assert.equal(res.status, 404);
});

test('v1 renders the spec Add to Cart button on every card', async () => {
  await start({ variant: 'v1' });
  const html = await page();
  assert.equal(count(html, /class="product-card"/g), 6);
  assert.equal(count(html, /<button id="add-to-cart-btn" data-product-id="p-\d+"( disabled)?>Add to Cart<\/button>/g), 6);
  assert.equal(count(html, /add-to-bag-btn/g), 0);
});

test('v2 renders Add to Bag with a new id and no v1 button', async () => {
  await start({ variant: 'v2' });
  const html = await page();
  assert.equal(count(html, /<button id="add-to-bag-btn" data-product-id="p-\d+"( disabled)?>Add to Bag<\/button>/g), 6);
  assert.equal(count(html, /add-to-cart-btn/g), 0);
});

test('out-of-stock product button is disabled', async () => {
  await start({ variant: 'v1' });
  const html = await page();
  assert.match(html, /<button id="add-to-cart-btn" data-product-id="p-104" disabled>/);
  assert.match(html, /<button id="add-to-cart-btn" data-product-id="p-100">/);
});

test('catalog page search renders only matching cards and echoes the query', async () => {
  await start({ variant: 'v1' });
  const html = await page('/catalog.html?q=headphones');
  assert.equal(count(html, /class="product-card"/g), 1);
  assert.match(html, /id="search-input" name="q" type="search" value="headphones"/);
  assert.match(html, /id="result-count">1 products/);
  assert.match(await page('/catalog.html?q=zzz'), /id="no-results"/);
});

test('unknown variant fails fast', () => {
  assert.throws(() => createApp({ variant: 'v9' }), /CATALOG_BUTTON_VARIANT/);
});

test('__reset returns ok', async () => {
  await start({ variant: 'v1' });
  const res = await fetch(`${svc.url}/api/__reset`, { method: 'POST' });
  assert.deepEqual(await res.json(), { ok: true, service: 'catalog' });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -w catalog-service`
Expected: FAIL with `Cannot find module '../server'`

- [ ] **Step 4: Write `target-platform/catalog-service/server.js`**

```js
'use strict';
const path = require('node:path');
const express = require('express');
const { cors, ensureSid, render, publicUrls, escapeHtml } = require('../shared/kit');
const SEED = require('./seed-data.json');

// Seeded UI change for the self-healing demo (spec §6.6): same function, different id and label.
const VARIANTS = {
  v1: { id: 'add-to-cart-btn', label: 'Add to Cart' },
  v2: { id: 'add-to-bag-btn', label: 'Add to Bag' },
};
const VIEW = path.join(__dirname, 'views', 'catalog.html');

function productCard(p, button) {
  const inStock = p.stock > 0;
  return `    <li class="product-card" data-product-id="${escapeHtml(p.id)}">
      <h2 class="product-name">${escapeHtml(p.name)}</h2>
      <p class="product-description">${escapeHtml(p.description)}</p>
      <span class="product-price">$${p.price.toFixed(2)}</span>
      <span class="product-stock">${inStock ? 'In stock' : 'Out of stock'}</span>
      <button id="${button.id}" data-product-id="${escapeHtml(p.id)}"${inStock ? '' : ' disabled'}>${button.label}</button>
    </li>`;
}

function createApp({ urls = publicUrls(), variant = process.env.CATALOG_BUTTON_VARIANT || 'v1' } = {}) {
  const button = VARIANTS[variant];
  if (!button) throw new Error(`Unknown CATALOG_BUTTON_VARIANT "${variant}" (expected v1 or v2)`);

  let products;
  const reset = () => { products = structuredClone(SEED); };
  reset();

  const search = (q) => {
    const needle = (typeof q === 'string' ? q : '').trim().toLowerCase();
    if (!needle) return products;
    return products.filter((p) => `${p.name} ${p.description}`.toLowerCase().includes(needle));
  };

  const app = express();
  app.use(cors, express.json());

  app.get('/api/products', (req, res) => res.json(search(req.query.q)));

  app.get('/api/products/:id', (req, res) => {
    const product = products.find((p) => p.id === req.params.id);
    if (!product) return res.status(404).json({ error: 'not_found' });
    res.json(product);
  });

  app.post('/api/__reset', (req, res) => {
    reset();
    res.json({ ok: true, service: 'catalog' });
  });

  app.get('/catalog.html', ensureSid, (req, res) => {
    const query = typeof req.query.q === 'string' ? req.query.q : '';
    const list = search(query);
    res.type('html').send(
      render(VIEW, {
        ...urls,
        QUERY: query,
        RESULT_COUNT: list.length,
        PRODUCT_CARDS: list.length ? list.map((p) => productCard(p, button)).join('\n') : '    <li id="no-results">No products found</li>',
      }),
    );
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4002;
  const variant = process.env.CATALOG_BUTTON_VARIANT || 'v1';
  createApp().listen(port, () => console.log(`catalog-service listening on http://localhost:${port} (button ${variant})`));
}

module.exports = { createApp };
```

- [ ] **Step 5: Write `target-platform/catalog-service/views/catalog.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Catalog · ShopLite</title></head>
<body>
  <nav>
    <a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a> |
    <a id="nav-cart" href="{{CART_URL}}/cart.html">Cart (<span id="cart-count">0</span>)</a> |
    <a id="nav-login" href="{{AUTH_URL}}/login.html">Log in</a>
  </nav>
  <h1>Catalog</h1>
  <form id="search-form" method="get" action="/catalog.html">
    <label for="search-input">Search</label>
    <input id="search-input" name="q" type="search" value="{{QUERY}}">
    <button id="search-btn" type="submit">Search</button>
  </form>
  <p id="result-count">{{RESULT_COUNT}} products</p>
  <ul id="product-list">
{{{PRODUCT_CARDS}}}
  </ul>
  <p id="cart-message" role="status"></p>
  <script>
    const CART_URL = '{{CART_URL}}';
    async function refreshCount() {
      const res = await fetch(CART_URL + '/api/cart', { credentials: 'include' });
      if (!res.ok) return;
      const { items } = await res.json();
      document.getElementById('cart-count').textContent = items.reduce((n, i) => n + i.qty, 0);
    }
    // Delegated on data-product-id, so the handler is identical for the v1 and v2 button.
    document.getElementById('product-list').addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-product-id]');
      if (!btn) return;
      const name = btn.closest('.product-card').querySelector('.product-name').textContent;
      const res = await fetch(CART_URL + '/api/cart/add', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ productId: btn.dataset.productId, qty: 1 }),
      });
      const msg = document.getElementById('cart-message');
      msg.textContent = res.ok ? `Added ${name} to cart` : `Could not add ${name} to cart`;
      msg.dataset.state = res.ok ? 'success' : 'error';
      refreshCount();
    });
    refreshCount();
  </script>
</body>
</html>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test -w catalog-service`
Expected: `# pass 9`, `# fail 0`

- [ ] **Step 7: Commit**

```powershell
git add target-platform/catalog-service
git commit -m "feat(target): catalog-service with search and CATALOG_BUTTON_VARIANT toggle"
```

---

### Task 4: cart-service (port 4003)

**Files:**
- Create: `target-platform/cart-service/server.js`, `views/cart.html`, `test/cart.test.js`

**Interfaces:**
- Consumes: the catalog `GET /api/products/:id` (Task 3); kit `cors, ensureSid, render, publicUrls, escapeHtml`.
- Produces: `createApp({ urls?, catalogUrl? })` (`catalogUrl` defaults to `CATALOG_SERVICE_URL || http://localhost:4002`). All routes are keyed by the `apqa_sid` cookie. `GET /api/cart` → `{items:[{itemId, productId, name, price, qty}], total}`. `POST /api/cart/add {productId, qty=1}` → 201 cart | 400 `invalid_qty` (integer 1..10, including the merged qty) | 404 `product_not_found` | 409 `out_of_stock` | 502 `catalog_unavailable`. `DELETE /api/cart/:itemId` → cart | 404 `item_not_found`. `DELETE /api/cart` → empty cart. `POST /api/__reset`. Page ids: `.cart-item[data-item-id] .item-name .item-qty .item-price .remove-btn #cart-total #empty-cart #checkout-btn`.

- [ ] **Step 1: Write the failing test** `target-platform/cart-service/test/cart.test.js`

```js
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w cart-service`
Expected: FAIL with `Cannot find module '../server'`

- [ ] **Step 3: Write `target-platform/cart-service/server.js`**

```js
'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, ensureSid, render, publicUrls, escapeHtml } = require('../shared/kit');

const MAX_QTY = 10;
const VIEW = path.join(__dirname, 'views', 'cart.html');
const round2 = (n) => Math.round(n * 100) / 100;

async function fetchProduct(catalogUrl, productId) {
  if (typeof productId !== 'string' || !productId) return null;
  const res = await fetch(`${catalogUrl}/api/products/${encodeURIComponent(productId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`catalog responded ${res.status}`);
  return res.json();
}

function createApp({ urls = publicUrls(), catalogUrl = process.env.CATALOG_SERVICE_URL || 'http://localhost:4002' } = {}) {
  let carts; // sid -> [{ itemId, productId, name, price, qty }]
  const reset = () => { carts = new Map(); };
  reset();

  const cartOf = (sid) => {
    if (!carts.has(sid)) carts.set(sid, []);
    return carts.get(sid);
  };
  const summary = (items) => ({ items, total: round2(items.reduce((sum, i) => sum + i.price * i.qty, 0)) });
  const validQty = (q) => Number.isInteger(q) && q >= 1 && q <= MAX_QTY;

  const app = express();
  app.use(cors, express.json());

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
        CHECKOUT_DISABLED: items.length ? '' : 'disabled',
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
```

- [ ] **Step 4: Write `target-platform/cart-service/views/cart.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Cart · ShopLite</title></head>
<body>
  <nav>
    <a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a> |
    <a id="nav-cart" href="{{CART_URL}}/cart.html">Cart</a>
  </nav>
  <h1>Your cart</h1>
  <p id="empty-cart" {{EMPTY_HIDDEN}}>Your cart is empty</p>
  <table id="cart-table">
    <thead><tr><th>Product</th><th>Qty</th><th>Price</th><th></th></tr></thead>
    <tbody>
{{{CART_ROWS}}}
    </tbody>
  </table>
  <p>Total: <span id="cart-total">${{TOTAL}}</span></p>
  <button id="checkout-btn" {{CHECKOUT_DISABLED}}>Proceed to Checkout</button>
  <script>
    document.getElementById('cart-table').addEventListener('click', async (e) => {
      const btn = e.target.closest('.remove-btn');
      if (!btn) return;
      await fetch('/api/cart/' + encodeURIComponent(btn.dataset.itemId), { method: 'DELETE' });
      location.reload();
    });
    document.getElementById('checkout-btn').addEventListener('click', () => {
      location.href = '{{ORDER_URL}}/checkout.html';
    });
  </script>
</body>
</html>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w cart-service`
Expected: `# pass 11`, `# fail 0`

- [ ] **Step 6: Commit**

```powershell
git add target-platform/cart-service
git commit -m "feat(target): cart-service with qty boundaries and stock checks"
```

---

### Task 5: order-service (port 4004)

**Files:**
- Create: `target-platform/order-service/server.js`, `views/checkout.html`, `test/order.test.js`

**Interfaces:**
- Consumes: the cart `GET /api/cart` and `DELETE /api/cart` with a forwarded `cookie: apqa_sid=<sid>` (Task 4); kit `cors, ensureSid, render, publicUrls, sidCookieHeader`.
- Produces: `createApp({ urls?, cartUrl? })` (`cartUrl` defaults to `CART_SERVICE_URL || http://localhost:4003`). `POST /api/orders {shipping:{name,address,city,zip}}` → 201 `{orderId, items, total, shipping, status:'pending_payment', createdAt}` | 400 `invalid_shipping` (with `missing: [...]`) | 400 `cart_empty` | 502 `cart_unavailable`. `GET /api/orders` → this sid's orders. `GET /api/orders/:id` → order | 404. `POST /api/orders/:id/paid {paymentId, amount}` → 200 order with `status:'paid'` | 404 | 409 `already_paid` | 409 `amount_mismatch`. `POST /api/__reset`. Page ids: `#ship-name #ship-address #ship-city #ship-zip #place-order-btn #checkout-total #checkout-message`; on success it redirects to `{{PAYMENT_URL}}/payment.html?orderId=<id>&amount=<total>`.

- [ ] **Step 1: Write the failing test** `target-platform/order-service/test/order.test.js`

```js
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w order-service`
Expected: FAIL with `Cannot find module '../server'`

- [ ] **Step 3: Write `target-platform/order-service/server.js`**

```js
'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, ensureSid, render, publicUrls, sidCookieHeader } = require('../shared/kit');

const SHIPPING_FIELDS = ['name', 'address', 'city', 'zip'];
const VIEW = path.join(__dirname, 'views', 'checkout.html');

function createApp({ urls = publicUrls(), cartUrl = process.env.CART_SERVICE_URL || 'http://localhost:4003' } = {}) {
  let orders; // orderId -> order (including the owning sid)
  const reset = () => { orders = new Map(); };
  reset();

  const publicOrder = ({ sid, ...rest }) => rest;
  const cartRequest = (sid, method = 'GET') => fetch(`${cartUrl}/api/cart`, { method, headers: { cookie: sidCookieHeader(sid) } });

  const app = express();
  app.use(cors, express.json());

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
    res.type('html').send(render(VIEW, { ...urls, TOTAL: total }));
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4004;
  createApp().listen(port, () => console.log(`order-service listening on http://localhost:${port}`));
}

module.exports = { createApp };
```

- [ ] **Step 4: Write `target-platform/order-service/views/checkout.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Checkout · ShopLite</title></head>
<body>
  <nav>
    <a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a> |
    <a id="nav-cart" href="{{CART_URL}}/cart.html">Cart</a>
  </nav>
  <h1>Checkout</h1>
  <p>Order total: <span id="checkout-total">{{TOTAL}}</span></p>
  <form id="checkout-form" novalidate>
    <label for="ship-name">Full name</label> <input id="ship-name" name="name">
    <label for="ship-address">Address</label> <input id="ship-address" name="address">
    <label for="ship-city">City</label> <input id="ship-city" name="city">
    <label for="ship-zip">Postcode</label> <input id="ship-zip" name="zip">
    <button id="place-order-btn" type="submit">Place Order</button>
  </form>
  <p id="checkout-message" role="status"></p>
  <script>
    const ERRORS = { cart_empty: 'Your cart is empty', invalid_shipping: 'Please fill in all shipping fields' };
    document.getElementById('checkout-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.target;
      const shipping = { name: f.name.value, address: f.address.value, city: f.city.value, zip: f.zip.value };
      const res = await fetch('/api/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shipping }),
      });
      const body = await res.json();
      if (res.ok) {
        location.href = '{{PAYMENT_URL}}/payment.html?orderId=' + encodeURIComponent(body.orderId) + '&amount=' + body.total;
        return;
      }
      const msg = document.getElementById('checkout-message');
      msg.textContent = ERRORS[body.error] || 'Could not place order';
      msg.dataset.state = 'error';
    });
  </script>
</body>
</html>
```

Note: `f.name` on a `<form>` element resolves to the input named `name` (form named-control access), because the input with `name="name"` shadows the form's own `name` property.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w order-service`
Expected: `# pass 7`, `# fail 0`

- [ ] **Step 6: Commit**

```powershell
git add target-platform/order-service
git commit -m "feat(target): order-service with checkout page and payment callback"
```

---

### Task 6: payment-service (port 4005)

**Files:**
- Create: `target-platform/payment-service/server.js`, `views/payment.html`, `test/payment.test.js`

**Interfaces:**
- Consumes: the order `POST /api/orders/:id/paid {paymentId, amount}` (Task 5); kit `cors, render, publicUrls`.
- Produces: `createApp({ urls?, orderUrl?, now? })` (`orderUrl` defaults to `ORDER_SERVICE_URL || http://localhost:4004`; `now` defaults to `() => new Date()`). Also exports `isExpired(expiry, now)`. `POST /api/payments {cardNumber, expiry, amount, orderId?}` → 201 `{paymentId, orderId, amount, last4, status:'approved', createdAt}` | 400 `{status:'declined', error: 'invalid_card_number'|'invalid_expiry'|'invalid_amount'}` | 402 `{status:'declined', error:'card_expired'}` | 404 `order_not_found` | 409 (passes through the order's `amount_mismatch`/`already_paid`) | 502 `order_unavailable`. `GET /api/payments/:id`. `POST /api/__reset`. Page ids: `#order-id #amount #card-number #expiry #pay-btn #payment-status` (text `Payment approved` / `Payment declined: <reason>`, `data-state`).

- [ ] **Step 1: Write the failing test** `target-platform/payment-service/test/payment.test.js`

```js
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w payment-service`
Expected: FAIL with `Cannot find module '../server'`

- [ ] **Step 3: Write `target-platform/payment-service/server.js`**

```js
'use strict';
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { cors, render, publicUrls } = require('../shared/kit');

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
} = {}) {
  let payments; // paymentId -> payment
  const reset = () => { payments = new Map(); };
  reset();

  const app = express();
  app.use(cors, express.json());

  app.post('/api/payments', async (req, res) => {
    const { cardNumber, expiry, amount, orderId } = req.body || {};
    const digits = typeof cardNumber === 'string' ? cardNumber.replace(/\s+/g, '') : '';
    if (!CARD_RE.test(digits)) return decline(res, 400, 'invalid_card_number');
    if (typeof expiry !== 'string' || !EXPIRY_RE.test(expiry)) return decline(res, 400, 'invalid_expiry');
    if (typeof amount !== 'number' || !(amount > 0)) return decline(res, 400, 'invalid_amount');
    if (isExpired(expiry, now())) return decline(res, 402, 'card_expired');

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
      render(VIEW, { ...urls, ORDER_ID: orderId, AMOUNT: Number.isFinite(amount) ? amount.toFixed(2) : '0.00' }),
    );
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 4005;
  createApp().listen(port, () => console.log(`payment-service listening on http://localhost:${port}`));
}

module.exports = { createApp, isExpired };
```

- [ ] **Step 4: Write `target-platform/payment-service/views/payment.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Payment · ShopLite</title></head>
<body>
  <nav><a id="nav-catalog" href="{{CATALOG_URL}}/catalog.html">Catalog</a></nav>
  <h1>Payment</h1>
  <p>Order <span id="order-id">{{ORDER_ID}}</span> — amount due <span id="amount">${{AMOUNT}}</span></p>
  <form id="payment-form" novalidate>
    <label for="card-number">Card number</label> <input id="card-number" name="cardNumber" inputmode="numeric">
    <label for="expiry">Expiry (MM/YY)</label> <input id="expiry" name="expiry" placeholder="MM/YY">
    <button id="pay-btn" type="submit">Pay</button>
  </form>
  <p id="payment-status" role="status"></p>
  <script>
    const REASONS = {
      card_expired: 'card expired',
      invalid_card_number: 'invalid card number',
      invalid_expiry: 'invalid expiry date',
      invalid_amount: 'invalid amount',
      order_not_found: 'order not found',
      already_paid: 'order already paid',
    };
    document.getElementById('payment-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const res = await fetch('/api/payments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cardNumber: e.target.cardNumber.value,
          expiry: e.target.expiry.value,
          amount: Number('{{AMOUNT}}'),
          orderId: '{{ORDER_ID}}' || undefined,
        }),
      });
      const body = await res.json();
      const status = document.getElementById('payment-status');
      status.textContent = res.ok ? 'Payment approved' : 'Payment declined: ' + (REASONS[body.error] || 'payment failed');
      status.dataset.state = res.ok ? 'success' : 'error';
    });
  </script>
</body>
</html>
```

Note: `{{ORDER_ID}}` inside a JS string is HTML-escaped by `render`, which also neutralises quotes. That's enough for a test platform.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w payment-service`
Expected: `# pass 8`, `# fail 0`

- [ ] **Step 6: Run the full workspace suite**

Run: `cd target-platform; npm test`
Expected: kit 5 + auth 8 + catalog 9 + cart 11 + order 7 + payment 8, all passing, exit code 0.

- [ ] **Step 7: Commit**

```powershell
git add target-platform/payment-service
git commit -m "feat(target): payment-service with expiry validation and order callback"
```

---

### Task 7: Local runner, Docker Compose, platform README

**Files:**
- Create: `target-platform/scripts/start-all.js`, `target-platform/Dockerfile`, `target-platform/.dockerignore`, `target-platform/docker-compose.yml`, `target-platform/README.md`

**Interfaces:**
- Consumes: every `server.js` (it honours `PORT`, `*_SERVICE_URL`, `CATALOG_BUTTON_VARIANT`).
- Produces: `npm run start:all` (all 5 on 4001–4005) and `docker compose up --build` (the same plus mongo on 27017). The README is the platform context the Phase 4/5 agents load.

- [ ] **Step 1: Write `target-platform/scripts/start-all.js`**

```js
'use strict';
// Starts all five services in one terminal (no Docker needed). Ctrl+C stops them all.
const { spawn } = require('node:child_process');
const path = require('node:path');

const SERVICES = [
  ['auth-service', 4001],
  ['catalog-service', 4002],
  ['cart-service', 4003],
  ['order-service', 4004],
  ['payment-service', 4005],
];

let stopping = false;
const children = SERVICES.map(([name, port]) => {
  const child = spawn(process.execPath, ['server.js'], {
    cwd: path.join(__dirname, '..', name),
    env: { ...process.env, PORT: String(port) },
    stdio: 'inherit',
  });
  child.on('exit', (code) => {
    if (stopping) return;
    console.error(`${name} exited with code ${code}; stopping the rest`);
    stop(1);
  });
  return child;
});

function stop(code) {
  stopping = true;
  for (const child of children) child.kill();
  process.exit(code);
}

process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));
```

- [ ] **Step 2: Smoke-test the runner**

Run (terminal 1): `cd target-platform; npm run start:all`
Expected: five `... listening on http://localhost:400x` lines, catalog showing `(button v1)`.

Run (terminal 2):
```powershell
foreach ($p in 4001..4005) { (Invoke-RestMethod -Method Post "http://localhost:$p/api/__reset").service }
```
Expected: `auth`, `catalog`, `cart`, `order`, `payment`.

Then open `http://localhost:4002/catalog.html` and click through the whole flow by hand: search "headphones" → Add to Cart → Cart → Proceed to Checkout → fill shipping → Place Order → card `4111111111111111`, expiry `12/30` → Pay → "Payment approved". Stop with Ctrl+C.

- [ ] **Step 3: Write `target-platform/Dockerfile`**

```dockerfile
FROM node:22-alpine
ARG SERVICE
WORKDIR /app
COPY shared ./shared
COPY ${SERVICE}/package.json ./${SERVICE}/
RUN cd ${SERVICE} && npm install --omit=dev --no-audit --no-fund
COPY ${SERVICE} ./${SERVICE}
WORKDIR /app/${SERVICE}
ENV NODE_ENV=production
CMD ["node", "server.js"]
```

- [ ] **Step 4: Write `target-platform/.dockerignore`**

```
**/node_modules
**/test
scripts
```

- [ ] **Step 5: Write `target-platform/docker-compose.yml`**

```yaml
# ShopLite target platform + MongoDB for AutoPilot-QA.
# Browser-facing URLs stay on localhost (see PUBLIC_*_URL defaults); service-to-service calls use compose DNS.
services:
  mongo:
    image: mongo:7
    ports: ["27017:27017"]
    volumes: [mongo-data:/data/db]

  auth-service:
    build: { context: ., args: { SERVICE: auth-service } }
    environment: { PORT: "4001" }
    ports: ["4001:4001"]

  catalog-service:
    build: { context: ., args: { SERVICE: catalog-service } }
    environment:
      PORT: "4002"
      CATALOG_BUTTON_VARIANT: ${CATALOG_BUTTON_VARIANT:-v1}
    ports: ["4002:4002"]

  cart-service:
    build: { context: ., args: { SERVICE: cart-service } }
    environment:
      PORT: "4003"
      CATALOG_SERVICE_URL: http://catalog-service:4002
    ports: ["4003:4003"]
    depends_on: [catalog-service]

  order-service:
    build: { context: ., args: { SERVICE: order-service } }
    environment:
      PORT: "4004"
      CART_SERVICE_URL: http://cart-service:4003
    ports: ["4004:4004"]
    depends_on: [cart-service]

  payment-service:
    build: { context: ., args: { SERVICE: payment-service } }
    environment:
      PORT: "4005"
      ORDER_SERVICE_URL: http://order-service:4004
    ports: ["4005:4005"]
    depends_on: [order-service]

volumes:
  mongo-data: {}
```

- [ ] **Step 6: Verify compose**

Run: `cd target-platform; docker compose up --build -d; docker compose ps`
Expected: 6 containers `running`. Re-run the `__reset` loop from Step 2 and get the same five names back. Then `docker compose down`.

- [ ] **Step 7: Write `target-platform/README.md`**

````markdown
# ShopLite — AutoPilot-QA target platform

A deliberately minimal e-commerce app made of 5 independent services (separate processes, separate in-memory stores). It exists only to be tested by AutoPilot-QA. **Not production code.**

## Run

```powershell
npm install
npm run start:all                                           # all 5 services, no Docker
$env:CATALOG_BUTTON_VARIANT='v2'; npm run start:all         # seeded UI change
docker compose up --build                                   # 5 services + MongoDB
$env:CATALOG_BUTTON_VARIANT='v2'; docker compose up -d catalog-service   # redeploy catalog only
npm test
```

Always browse via `http://localhost:...` (not `127.0.0.1`). The shared session cookie `apqa_sid` is per-host.

## Services

| Service | Port | Pages | API |
|---|---|---|---|
| auth | 4001 | `/login.html`, `/register.html` | `POST /api/register`, `POST /api/login`, `GET /api/me` |
| catalog | 4002 | `/catalog.html?q=` | `GET /api/products?q=`, `GET /api/products/:id` |
| cart | 4003 | `/cart.html` | `GET /api/cart`, `POST /api/cart/add`, `DELETE /api/cart/:itemId`, `DELETE /api/cart` |
| order | 4004 | `/checkout.html` | `POST /api/orders`, `GET /api/orders`, `GET /api/orders/:id`, `POST /api/orders/:id/paid` |
| payment | 4005 | `/payment.html?orderId=&amount=` | `POST /api/payments`, `GET /api/payments/:id` |

Every service: `POST /api/__reset` restores its seed state (test-only).

Checkout flow: catalog → cart → checkout (`POST /api/orders`, status `pending_payment`, cart cleared) → payment page → `POST /api/payments` → payment-service calls `POST /api/orders/:id/paid`.

## Page elements (stable ids)

| Page | Elements |
|---|---|
| login / register | `#email`, `#password`, `#login-btn` / `#register-btn`, `#message` (`data-state=success\|error`) |
| catalog | `#search-input`, `#search-btn`, `#result-count`, `#product-list`, `.product-card[data-product-id]`, `.product-name`, `.product-price`, `.product-stock`, add button (below), `#cart-message`, `#cart-count`, `#no-results` |
| cart | `.cart-item[data-item-id]`, `.item-name`, `.item-qty`, `.item-price`, `.remove-btn`, `#cart-total`, `#empty-cart`, `#checkout-btn` |
| checkout | `#ship-name`, `#ship-address`, `#ship-city`, `#ship-zip`, `#place-order-btn`, `#checkout-total`, `#checkout-message` |
| payment | `#order-id`, `#amount`, `#card-number`, `#expiry`, `#pay-btn`, `#payment-status` (`Payment approved` / `Payment declined: <reason>`) |

### Seeded UI change — `CATALOG_BUTTON_VARIANT`

| Variant | Button (one per `.product-card`, always with `data-product-id`) |
|---|---|
| `v1` (default) | `<button id="add-to-cart-btn">Add to Cart</button>` |
| `v2` | `<button id="add-to-bag-btn">Add to Bag</button>` |

The id repeats on every card, so always scope the locator to a `.product-card`. Out-of-stock products render the button `disabled`.

## Test data

| Kind | Values |
|---|---|
| User | `demo@shop.test` / `demo1234` |
| Products | p-100 Wireless Headphones $59.99 (12) · p-101 Mechanical Keyboard $89.50 (5) · p-102 USB-C Charger $24.00 (30) · p-103 Running Shoes $74.95 (8) · **p-104 Coffee Grinder $45.00 (0, out of stock)** · p-105 Yoga Mat $19.99 (20) |
| Cart qty | integer 1–10 per line (boundary), and ≤ stock |
| Valid card | `4111111111111111`, expiry `12/30` |
| Expired card | `4111111111111111`, expiry `01/20` → `Payment declined: card expired` |
| Card rules | 16 digits (spaces allowed), expiry `MM/YY`, valid through the end of that month |
| Shipping | name, address, city, zip: all required |
````

- [ ] **Step 8: Commit**

```powershell
git add target-platform/scripts target-platform/Dockerfile target-platform/.dockerignore target-platform/docker-compose.yml target-platform/README.md
git commit -m "feat(target): local runner, docker compose and platform README"
```

---

### Task 8: Manual baseline Playwright suite (and the v2 break demonstration)

**Files:**
- Create: `evaluation/manual-baseline/package.json`, `playwright.config.js`, `tests/helpers.js`, `tests/checkout-flow.spec.js`, `tests/negative.spec.js`, `tests/variant-v2.spec.js`

**Interfaces:**
- Consumes: the running platform (Task 7) and the element ids from the platform README.
- Produces: the hand-written baseline used as the Phase 10 comparison, plus `URLS` / `resetAll(request)` / `goToPaymentWith(page, productName)` helpers.

- [ ] **Step 1: Write `evaluation/manual-baseline/package.json`**

```json
{
  "name": "manual-baseline",
  "private": true,
  "scripts": { "test": "playwright test" },
  "devDependencies": { "@playwright/test": "^1.55.0" }
}
```

Run: `cd evaluation/manual-baseline; npm install; npx playwright install chromium`

- [ ] **Step 2: Write `evaluation/manual-baseline/playwright.config.js`**

```js
// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  workers: 1, // services hold shared in-memory state; tests reset it in beforeEach
  retries: 0,
  reporter: [['list']],
  use: { actionTimeout: 5000, navigationTimeout: 10000, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  expect: { timeout: 5000 },
});
```

- [ ] **Step 3: Write `evaluation/manual-baseline/tests/helpers.js`**

```js
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
```

- [ ] **Step 4: Write `evaluation/manual-baseline/tests/checkout-flow.spec.js`** (the spec §6.7 reference flow)

```js
const { test, expect } = require('@playwright/test');
const { URLS, resetAll } = require('./helpers');

test.beforeEach(async ({ request }) => resetAll(request));

test('user can search for a product, add it to cart, and complete checkout', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html`);
  await page.fill('#search-input', 'headphones');
  await page.click('#search-btn');
  const card = page.locator('.product-card', { hasText: 'Wireless Headphones' });
  await expect(card).toHaveCount(1);
  await card.locator('#add-to-cart-btn').click();
  await expect(page.locator('#cart-message')).toHaveText('Added Wireless Headphones to cart');
  await expect(page.locator('#cart-count')).toHaveText('1');

  await page.goto(`${URLS.cart}/cart.html`);
  await expect(page.locator('.cart-item')).toHaveCount(1);
  await expect(page.locator('#cart-total')).toHaveText('$59.99');
  await page.click('#checkout-btn');

  await expect(page).toHaveURL(`${URLS.order}/checkout.html`);
  await expect(page.locator('#checkout-total')).toHaveText('$59.99');
  await page.fill('#ship-name', 'Ada Lovelace');
  await page.fill('#ship-address', '1 Analytical St');
  await page.fill('#ship-city', 'London');
  await page.fill('#ship-zip', 'N1 9GU');
  await page.click('#place-order-btn');

  await expect(page).toHaveURL(/localhost:4005\/payment\.html\?orderId=ord-.+&amount=59\.99/);
  await page.fill('#card-number', '4111111111111111');
  await page.fill('#expiry', '12/30');
  await page.click('#pay-btn');
  await expect(page.locator('#payment-status')).toHaveText('Payment approved');

  const orderId = await page.locator('#order-id').textContent();
  const order = await (await page.request.get(`${URLS.order}/api/orders/${orderId}`)).json();
  expect(order.status).toBe('paid');
});
```

- [ ] **Step 5: Write `evaluation/manual-baseline/tests/negative.spec.js`**

```js
const { test, expect } = require('@playwright/test');
const { URLS, resetAll, goToPaymentWith } = require('./helpers');

test.beforeEach(async ({ request }) => resetAll(request));

test('expired payment card is declined', async ({ page }) => {
  await goToPaymentWith(page, 'p-102');
  await page.fill('#card-number', '4111111111111111');
  await page.fill('#expiry', '01/20');
  await page.click('#pay-btn');
  await expect(page.locator('#payment-status')).toHaveText('Payment declined: card expired');
  const orderId = await page.locator('#order-id').textContent();
  const order = await (await page.request.get(`${URLS.order}/api/orders/${orderId}`)).json();
  expect(order.status).toBe('pending_payment');
});

test('out-of-stock product cannot be added to cart', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html?q=grinder`);
  const card = page.locator('.product-card', { hasText: 'Coffee Grinder' });
  await expect(card.locator('.product-stock')).toHaveText('Out of stock');
  await expect(card.locator('button[data-product-id]')).toBeDisabled();
});

test('checkout is disabled for an empty cart', async ({ page }) => {
  await page.goto(`${URLS.cart}/cart.html`);
  await expect(page.locator('#empty-cart')).toBeVisible();
  await expect(page.locator('#checkout-btn')).toBeDisabled();
});

test('login rejects a wrong password', async ({ page }) => {
  await page.goto(`${URLS.auth}/login.html`);
  await page.fill('#email', 'demo@shop.test');
  await page.fill('#password', 'wrong-password');
  await page.click('#login-btn');
  await expect(page.locator('#message')).toHaveText('Invalid email or password');
});
```

- [ ] **Step 6: Write `evaluation/manual-baseline/tests/variant-v2.spec.js`**

```js
const { test, expect } = require('@playwright/test');
const { URLS, resetAll } = require('./helpers');

// Proves the seeded v2 change is cosmetic: the renamed button still adds to cart.
test.skip(process.env.CATALOG_BUTTON_VARIANT !== 'v2', 'run with CATALOG_BUTTON_VARIANT=v2 against a v2 catalog');
test.beforeEach(async ({ request }) => resetAll(request));

test('v2 "Add to Bag" button still adds the product to the cart', async ({ page }) => {
  await page.goto(`${URLS.catalog}/catalog.html?q=headphones`);
  const card = page.locator('.product-card', { hasText: 'Wireless Headphones' });
  await expect(card.locator('#add-to-cart-btn')).toHaveCount(0);
  await card.locator('#add-to-bag-btn').click();
  await expect(page.locator('#cart-message')).toHaveText('Added Wireless Headphones to cart');
});
```

- [ ] **Step 7: Run against v1**

Run (terminal 1): `cd target-platform; npm run start:all`
Run (terminal 2): `cd evaluation/manual-baseline; npx playwright test`
Expected: `5 passed, 1 skipped` (the v2 test is skipped).

- [ ] **Step 8: Run against v2 (the self-healing demo precondition)**

Stop terminal 1, then run: `$env:CATALOG_BUTTON_VARIANT='v2'; npm run start:all`
Run (terminal 2): `$env:CATALOG_BUTTON_VARIANT='v2'; npx playwright test`
Expected: `1 failed, 5 passed`. The failure is `checkout-flow.spec.js` with `waiting for locator('.product-card').filter({ hasText: 'Wireless Headphones' }).locator('#add-to-cart-btn')` and a 5000 ms timeout. `variant-v2.spec.js` passes. This is exactly the breakage the Healer must repair in Phase 8.

Clean up: `Remove-Item Env:CATALOG_BUTTON_VARIANT` in both terminals and restart the platform on v1.

- [ ] **Step 9: Commit**

```powershell
git add evaluation/manual-baseline/package.json evaluation/manual-baseline/package-lock.json evaluation/manual-baseline/playwright.config.js evaluation/manual-baseline/tests
git commit -m "test(eval): manual Playwright baseline incl. reference flow and v2 break"
```

---

## Phase 1 Exit Checklist

- [ ] `cd target-platform; npm test`: every suite green (48 tests).
- [ ] The manual click-through works in a browser (Task 7 Step 2).
- [ ] Baseline: v1 → 5 passed / 1 skipped; v2 → only the reference flow fails, on `#add-to-cart-btn`.
- [ ] `docker compose up --build` starts 6 containers, and all five `__reset` calls answer.
- [ ] Request a code review (superpowers:requesting-code-review) before starting the Phase 2 plan.
