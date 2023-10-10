import {  expect } from '@playwright/test';
import test from '../lambda.setup';
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:3000/');
});



//   test('verify message on site', async ({ page }) => {
//     await page.getByText("Memorial Sale Week is Live !")
//     await expect(page).toBeTruthy();
//   })

test.describe('msg Validation', () => {
  test('verify message on site', async ({ page }) => {
    await page.getByText("Sales for Weekend")
    await expect(page).toBeTruthy();
  })

});