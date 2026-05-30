import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-001: Homepage loads with correct page title
test.describe('Page Title Validation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');
  });

  test('should have title "Contoso Traders"', async ({ page }) => {
    await expect(page).toHaveTitle('Contoso Traders');
  });

  test('should show main navigation on homepage', async ({ page }) => {
    const nav = page.locator('nav, header, [class*=Header], [class*=Navbar]').first();
    await expect(nav).toBeVisible({ timeout: 15000 });
  });
});
