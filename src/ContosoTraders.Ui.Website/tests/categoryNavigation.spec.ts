import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-003: Laptops category shows product grid
// AC-004: Controllers category shows products
// AC-005: Monitors category shows products
// AC-006: New Arrivals page shows products
test.describe('Category Navigation', () => {
  test('Laptops category should display a product grid with prices', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const cards = page.locator('[class*=ProductCard], [class*=MuiCard-root], article');
    await expect(cards.first()).toBeVisible({ timeout: 20000 });
    expect(await cards.count()).toBeGreaterThan(0);

    // At least one price must be visible
    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toContain('$');
  });

  test('Controllers category should display products', async ({ page }) => {
    await page.goto('/list/controllers', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const cards = page.locator('[class*=ProductCard], [class*=MuiCard-root], article');
    await expect(cards.first()).toBeVisible({ timeout: 20000 });
    expect(await cards.count()).toBeGreaterThan(0);
  });

  test('Monitors category should display products with prices', async ({ page }) => {
    await page.goto('/list/monitors', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const cards = page.locator('[class*=ProductCard], [class*=MuiCard-root], article');
    await expect(cards.first()).toBeVisible({ timeout: 20000 });
    expect(await cards.count()).toBeGreaterThan(0);

    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toContain('$');
  });

  test('New Arrivals page should display products', async ({ page }) => {
    await page.goto('/new-arrivals', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const content = page.locator(
      '[class*=ProductCard], [class*=MuiCard-root], article, img'
    ).first();
    await expect(content).toBeVisible({ timeout: 20000 });
  });

  test('All Products page should display a product listing', async ({ page }) => {
    await page.goto('/list/all-products', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const cards = page.locator('[class*=ProductCard], [class*=MuiCard-root], article');
    await expect(cards.first()).toBeVisible({ timeout: 20000 });
    expect(await cards.count()).toBeGreaterThan(0);
  });

  test('clicking category nav link should navigate to category page', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    // Click Laptops from nav
    const laptopsLink = page.locator('a[href*="laptops"], button:has-text("Laptops")').first();
    await expect(laptopsLink).toBeVisible({ timeout: 15000 });
    await laptopsLink.click();
    await page.waitForLoadState('domcontentloaded');

    await expect(page).toHaveURL(/laptops/);
    const cards = page.locator('[class*=ProductCard], [class*=MuiCard-root], article');
    await expect(cards.first()).toBeVisible({ timeout: 15000 });
  });
});
