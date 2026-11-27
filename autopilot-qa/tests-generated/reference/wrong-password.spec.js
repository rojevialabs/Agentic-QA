const { test, expect, sel, urls } = require('../support/fixtures');

test('Reference: login rejects a wrong password', async ({ page }) => {
  await page.goto(urls.auth + '/login.html');
  await page.locator(sel('auth.emailInput')).fill('demo@shop.test');
  await page.locator(sel('auth.passwordInput')).fill('wrong-password');
  await page.locator(sel('auth.loginButton')).click();
  await expect(page.locator(sel('auth.message'))).toHaveText('Invalid email or password');
});
