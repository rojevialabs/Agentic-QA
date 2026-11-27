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
