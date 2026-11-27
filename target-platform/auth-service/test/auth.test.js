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

test('auth exposes the evaluation config endpoint too', async () => {
  assert.deepEqual(await (await fetch(`${svc.url}/api/__config`)).json(), { service: 'auth', seeded_changes: [] });
});
