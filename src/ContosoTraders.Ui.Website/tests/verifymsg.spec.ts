import {  expect } from '@playwright/test';
import test from '../lambda.setup';
import { verify } from 'crypto';
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:3000/');
});



//   test('verify message on site', async ({ page }) => {
//     await page.getByText("Memorial Sale Week is Live !")
//     await expect(page).toBeTruthy();
//   })


  test('verify message on site', async ({ page }) => {
    await page.getByText("14 March Offers")
    await expect(page).toBeTruthy();
  })

