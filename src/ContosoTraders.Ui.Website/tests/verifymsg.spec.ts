import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-001 / AC-002: Homepage renders with expected Contoso Traders content
test.describe('Homepage Content Verification', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');
  });

  test('homepage body should contain product category names', async ({ page }) => {
    // Verify Contoso Traders categories are present (at least 2 of 5)
    const bodyText = await page.locator('body').innerText();
    const categories = ['Laptops', 'Controllers', 'Desktops', 'Mobiles', 'Monitors'];
    const found = categories.filter(c => bodyText.includes(c));
    expect(found.length).toBeGreaterThanOrEqual(2);
  });

  test('homepage main content area should be visible', async ({ page }) => {
    // Root app container must render (React app mounted)
    const mainContent = page.locator('main, #root > div, [class*=App]').first();
    await expect(mainContent).toBeVisible({ timeout: 20000 });
  });

  test('homepage should display at least one product or promotion', async ({ page }) => {
    // Either product cards or a hero banner must be visible
    const content = page.locator(
      '[class*=ProductCard], [class*=MuiCard-root], [class*=hero], [class*=Hero], [class*=banner], img'
    ).first();
    await expect(content).toBeVisible({ timeout: 20000 });
  });
});
