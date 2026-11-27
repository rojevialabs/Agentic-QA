// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.js',
  workers: 1, // the target platform keeps shared in-memory state
  retries: 0, // flakiness is classified by the executor, not hidden by retries
  reporter: [['list']],
  use: { actionTimeout: 5000, navigationTimeout: 10000 },
  expect: { timeout: 5000 },
});
