'use strict';
const path = require('node:path');
const express = require('express');
const { cors, ensureSid, render, publicUrls, escapeHtml, seededChanges, configRoutes } = require('../shared/kit');
const SEED = require('./seed-data.json');

// Seeded UI change for the self-healing demo (spec §6.6): same function, different id and label.
const VARIANTS = {
  v1: { attr: 'id="add-to-cart-btn"', label: 'Add to Cart' },
  v2: { attr: 'id="add-to-bag-btn"', label: 'Add to Bag' },
};
// Seeded real defect (evaluation): a control with a different purpose replaces the add button.
const WISHLIST = { attr: 'class="wishlist-btn"', label: 'Add to Wishlist' };
// Held-out decoy for the final evaluation: never used while developing prompts.
const COMPARE = { attr: 'class="compare-btn"', label: 'Add to Compare' };
const VIEW = path.join(__dirname, 'views', 'catalog.html');

function productCard(p, button, changes) {
  const inStock = p.stock > 0 || changes.has('catalog.out_of_stock_addable');
  return `    <li class="product-card" data-product-id="${escapeHtml(p.id)}">
      <h2 class="product-name">${escapeHtml(p.name)}</h2>
      <p class="product-description">${escapeHtml(p.description)}</p>
      <span class="product-price">$${p.price.toFixed(2)}</span>
      <span class="product-stock">${p.stock > 0 ? 'In stock' : 'Out of stock'}</span>
      <button ${button.attr} data-product-id="${escapeHtml(p.id)}"${inStock ? '' : ' disabled'}>${button.label}</button>
    </li>`;
}

function createApp({
  urls = publicUrls(),
  variant = process.env.CATALOG_BUTTON_VARIANT || 'v1',
  changes = seededChanges(),
} = {}) {
  if (!VARIANTS[variant]) throw new Error(`Unknown CATALOG_BUTTON_VARIANT "${variant}" (expected v1 or v2)`);
  const addButton = () => {
    if (changes.has('catalog.add_button_wishlist')) return WISHLIST;
    if (changes.has('catalog.add_button_compare')) return COMPARE;
    return VARIANTS[changes.has('catalog.add_button_renamed') ? 'v2' : variant];
  };

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
  configRoutes(app, 'catalog', changes);

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
    const button = addButton();
    res.type('html').send(
      render(VIEW, {
        ...urls,
        QUERY: query,
        SEARCH_ID: changes.has('catalog.search_input_renamed') ? 'query' : 'search-input',
        RESULT_COUNT: list.length,
        PRODUCT_CARDS: list.length ? list.map((p) => productCard(p, button, changes)).join('\n') : '    <li id="no-results">No products found</li>',
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
