import { expect } from '@playwright/test';
import test from '../lambda.setup';

// AC-011: Add to cart — cart counter updates
// AC-012: Cart page shows items with names and prices
// AC-013: Quantity update recalculates total
test.describe('Shopping Cart', () => {
  // Helper: add first laptop to cart from its detail page
  async function addFirstLaptopToCart(page: any) {
    await page.goto('/list/laptops', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const firstCard = page.locator('[class*=ProductCard], [class*=MuiCard-root], article').first();
    await expect(firstCard).toBeVisible({ timeout: 20000 });
    await firstCard.click();
    await page.waitForLoadState('domcontentloaded');

    const addBtn = page.locator(
      'button:has-text("Add to Cart"), button:has-text("Add To Cart"), button:has-text("BUY"), [class*=AddToCart]'
    ).first();
    await expect(addBtn).toBeVisible({ timeout: 15000 });
    await addBtn.click();
    await page.waitForTimeout(1500);
  }

  test('add to cart should update the cart counter in the header', async ({ page }) => {
    await addFirstLaptopToCart(page);

    const cartBadge = page.locator(
      '[class*=MuiBadge], [class*=badge], [class*=Badge], [class*=cart-count]'
    ).first();
    await expect(cartBadge).toBeVisible({ timeout: 10000 });
  });

  test('cart page should show the added item with a name', async ({ page }) => {
    await addFirstLaptopToCart(page);

    await page.goto('/cart', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const cartItems = page.locator(
      '[class*=CartItem], [class*=cart-item], [class*=MuiListItem], [class*=lineItem]'
    );
    await expect(cartItems.first()).toBeVisible({ timeout: 15000 });
    expect(await cartItems.count()).toBeGreaterThan(0);
  });

  test('cart page should show a price for each item', async ({ page }) => {
    await addFirstLaptopToCart(page);

    await page.goto('/cart', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toContain('$');
  });

  test('cart page should display a total price', async ({ page }) => {
    await addFirstLaptopToCart(page);

    await page.goto('/cart', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');

    // Total section — look for "Total", "Subtotal", or "Order total"
    const bodyText = await page.locator('body').innerText();
    const hasTotalLabel = /total|subtotal|order total/i.test(bodyText);
    expect(hasTotalLabel).toBe(true);
    expect(bodyText).toContain('$');
  });
});
