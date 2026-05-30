import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-007: Search by keyword returns relevant products
// AC-008: Search by category name returns results
test.describe('Search Box', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');
  });

  test('search box should be present and visible', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search by product name or search by image');
    await expect(searchInput).toBeVisible({ timeout: 15000 });
  });

  test('searching for "laptop" should return product results', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search by product name or search by image');
    await expect(searchInput).toBeVisible({ timeout: 15000 });

    await searchInput.fill('laptop');
    await searchInput.press('Enter');
    await page.waitForLoadState('domcontentloaded');

    // Results: product cards must appear
    const productCards = page.locator(
      '[class*=ProductCard], [class*=MuiCard-root], [class*=product], article'
    );
    await expect(productCards.first()).toBeVisible({ timeout: 15000 });
    expect(await productCards.count()).toBeGreaterThan(0);
  });

  test('searching for "controller" should return product results', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search by product name or search by image');
    await expect(searchInput).toBeVisible({ timeout: 15000 });

    await searchInput.fill('controller');
    await searchInput.press('Enter');
    await page.waitForLoadState('domcontentloaded');

    const productCards = page.locator(
      '[class*=ProductCard], [class*=MuiCard-root], [class*=product], article'
    );
    await expect(productCards.first()).toBeVisible({ timeout: 15000 });
    expect(await productCards.count()).toBeGreaterThan(0);
  });
});
