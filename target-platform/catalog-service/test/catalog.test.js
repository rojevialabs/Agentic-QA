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

test('seeded changes switch at runtime through /api/__config', async () => {
  await start({ variant: 'v1', changes: new Set() });
  const configure = (list) => fetch(`${svc.url}/api/__config`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seeded_changes: list }) });

  await configure(['catalog.add_button_renamed']);
  assert.equal(count(await page(), /<button id="add-to-bag-btn" data-product-id="p-\d+"( disabled)?>Add to Bag<\/button>/g), 6);

  await configure(['catalog.add_button_wishlist']);
  let html = await page();
  assert.equal(count(html, /<button class="wishlist-btn" data-product-id="p-\d+"( disabled)?>Add to Wishlist<\/button>/g), 6);
  assert.equal(count(html, /add-to-(cart|bag)-btn/g), 0);

  await configure(['catalog.add_button_compare']);
  assert.equal(count(await page(), /<button class="compare-btn" data-product-id="p-\d+"( disabled)?>Add to Compare<\/button>/g), 6);

  await configure(['catalog.out_of_stock_addable']);
  assert.match(await page(), /<button id="add-to-cart-btn" data-product-id="p-104">Add to Cart<\/button>/);

  await configure(['catalog.search_input_renamed']);
  html = await page();
  assert.match(html, /<input id="query" name="q" type="search" value="">/);
  assert.match(html, /<label for="query">/);
  assert.doesNotMatch(html, /id="search-input"/);

  await configure([]);
  assert.equal(count(await page(), /id="add-to-cart-btn"/g), 6);
  assert.deepEqual(await (await fetch(`${svc.url}/api/__config`)).json(), { service: 'catalog', seeded_changes: [] });
});

test('SEEDED_CHANGES env selects changes at startup', async () => {
  process.env.SEEDED_CHANGES = 'catalog.add_button_renamed';
  try {
    await start({ variant: 'v1' });
    assert.match(await page(), /id="add-to-bag-btn"/);
  } finally {
    delete process.env.SEEDED_CHANGES;
  }
});
