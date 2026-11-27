const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const FIXTURES = path.join(__dirname, 'fixtures.js');
const run = (code, env = {}) =>
  execFileSync(process.execPath, ['-e', code], { env: { ...process.env, ...env }, encoding: 'utf8' }).trim();

test('sel resolves keys through the seed map and rejects unknown keys', () => {
  const { sel } = require('./fixtures');
  assert.equal(sel('catalog.addToCartButton'), '#add-to-cart-btn');
  assert.throws(() => sel('catalog.nope'), /Unknown selector key "catalog.nope" \(selector map v1\)/);
});

test('APQA_SELECTOR_MAP and service URL env vars are honoured', () => {
  const seed = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'config', 'selector_map.v1.json'), 'utf8'));
  seed.version = 2;
  seed.services.catalog.elements.addToCartButton.selector = '#add-to-bag-btn';
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'apqa-')), 'map.json');
  fs.writeFileSync(file, JSON.stringify(seed));
  const out = run(`const f=require(${JSON.stringify(FIXTURES)});console.log(f.sel('catalog.addToCartButton'), f.selectorMap.version, f.urls.catalog)`,
    { APQA_SELECTOR_MAP: file, CATALOG_SERVICE_URL: 'http://localhost:9002' });
  assert.equal(out, '#add-to-bag-btn 2 http://localhost:9002');
});
