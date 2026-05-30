import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-009: Product detail page shows name, price, description
// AC-010: Product detail shows image and Add to Cart button
test.describe('Product Detail Page', () => {
  // Navigate to a product via the Laptops category listing
  test('clicking a product card should open the detail page', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await expect(firstCard).toBeVisible({ timeout: 20000 });
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    // URL should contain /product/detail/
    await expect(page).toHaveURL(/\/product\/detail\//);
  });

  test('product detail page should show product name', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    const productName = page.locator('h1, h2, [class*=productName], [class*=ProductName]').first();
    await expect(productName).toBeVisible({ timeout: 15000 });
    const nameText = await productName.innerText();
    expect(nameText.trim().length).toBeGreaterThan(0);
  });

  test('product detail page should show a price', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toContain('$');
  });

  test('product detail page should show a product image', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    const productImage = page.locator('img').first();
    await expect(productImage).toBeVisible({ timeout: 15000 });
  });

  test('product detail page should have an Add to Cart button', async ({ page }) => {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    const addToCartBtn = page.locator(
      'button:has-text("Add to Cart"), button:has-text("Add To Cart"), button:has-text("BUY"), [class*=AddToCart], [class*=addToCart]'
    ).first();
    await expect(addToCartBtn).toBeVisible({ timeout: 15000 });
  });
});
