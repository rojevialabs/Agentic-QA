// Runtime for AutoPilot-QA generated scripts (roadmap contract C5).
const base = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const MAP_PATH = process.env.APQA_SELECTOR_MAP || path.join(__dirname, '..', '..', 'backend', 'config', 'selector_map.v1.json');
const selectorMap = JSON.parse(fs.readFileSync(MAP_PATH, 'utf8'));

const DEFAULT_PORTS = { auth: 4001, catalog: 4002, cart: 4003, order: 4004, payment: 4005 };
const urls = Object.fromEntries(
  Object.entries(selectorMap.services).map(([name, svc]) => [
    name,
    process.env[svc.base_url_env] || `http://localhost:${DEFAULT_PORTS[name]}`,
  ]),
);

let lastSelectorKey = null;

// Resolves a logical key such as 'catalog.addToCartButton' through the active selector map.
function sel(key) {
  const [service, element] = String(key).split('.');
  const entry = selectorMap.services[service] && selectorMap.services[service].elements[element];
  if (!entry) throw new Error(`Unknown selector key "${key}" (selector map v${selectorMap.version})`);
  lastSelectorKey = key;
  return entry.selector;
}

const stripAnsi = (s) => String(s).replace(/\u001b\[[0-9;]*m/g, '');

const test = base.test.extend({
  // On failure, capture what the Triage & Healer agent needs: error, DOM, screenshot, last selector key.
  apqaFailureCapture: [
    async ({ page }, use, testInfo) => {
      lastSelectorKey = null;
      await use();
      if (testInfo.status === testInfo.expectedStatus) return;
      const dir = process.env.APQA_ARTIFACTS_DIR || testInfo.outputDir;
      fs.mkdirSync(dir, { recursive: true });
      const domPath = path.join(dir, 'dom.html');
      const screenshotPath = path.join(dir, 'failure.png');
      const saved = { dom: false, screenshot: false };
      try { fs.writeFileSync(domPath, await page.content()); saved.dom = true; } catch { /* page may be closed */ }
      try { await page.screenshot({ path: screenshotPath, fullPage: true }); saved.screenshot = true; } catch { /* ditto */ }
      fs.writeFileSync(path.join(dir, 'failure.json'), JSON.stringify({
        error: testInfo.error ? stripAnsi(testInfo.error.message) : null,
        last_selector_key: lastSelectorKey,
        url: page.url(),
        dom_path: saved.dom ? domPath : null,
        screenshot_path: saved.screenshot ? screenshotPath : null,
        selector_map_version: selectorMap.version,
      }, null, 2));
    },
    { auto: true },
  ],
});

module.exports = { test, expect: base.expect, sel, urls, selectorMap };
