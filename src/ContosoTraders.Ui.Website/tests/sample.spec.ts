import {  expect } from '@playwright/test';
import test from '../lambda.setup';
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:3000/');
});

test.describe('Cart Validation', () => {
  test('verify cart icon on the Page', async ({ page }) => {
    await page.locator("(//img[@alt='iconimage'])[2]").click()
    await expect(page).toHaveURL('http://localhost:3000/cart');
  })

  test.describe('Cart Validation', () => {
    test('verify cart icon on the Page', async ({ page }) => {
      await page.locator("(//img[@alt='iconimage'])[2]").click()
      await expect(page).toHaveURL('http://localhost:3000/cart');
    })

});
