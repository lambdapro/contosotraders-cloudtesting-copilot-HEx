import {  expect } from '@playwright/test';
import test from '../lambda.setup';
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:3000/');
});

test.describe('Header Message Validation', () => {
  test('verify Title of Page', async ({ page }) => {
    await page.getByTitle('Contoso Traders')
    await expect(page).toHaveTitle('Contoso Traders');
  })
  test.describe('Header Message Validation', () => {
    test('verify Title of Page', async ({ page }) => {
      await page.getByTitle('Contoso Traders')
      await expect(page).toHaveTitle('Contoso Traders');
    })

});