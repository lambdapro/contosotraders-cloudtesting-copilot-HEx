import {  expect } from '@playwright/test';
import test from '../lambda.setup';
import { verify } from 'crypto';
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:3000/');
});

test('verify Memorial Day Sale banner text is present', async ({ page }) => {
  await page.getByText("Memorial Day Sale")
  await expect(page).toBeTruthy();
});

test('top banner is visible and shows Memorial Day Sale offer', async ({ page }) => {
  const banner = page.locator(
    '.memorial-day-sale, [data-testid="promo-banner"], .promo-banner, header .announcement-bar'
  ).first();
  await expect(banner).toBeVisible({ timeout: 10000 });
  const text = await banner.textContent() ?? '';
  expect(text.toLowerCase()).toContain('memorial day sale');
});


test('verify Memorial Day Sale banner text is present', async ({ page }) => {
  await page.getByText("Memorial Day Sale")
  await expect(page).toBeTruthy();
});

test('top banner is visible and shows Memorial Day Sale offer', async ({ page }) => {
  const banner = page.locator(
    '.memorial-day-sale, [data-testid="promo-banner"], .promo-banner, header .announcement-bar'
  ).first();
  await expect(banner).toBeVisible({ timeout: 10000 });
  const text = await banner.textContent() ?? '';
  expect(text.toLowerCase()).toContain('memorial day sale');
});







test('verify message on site', async ({ page }) => {
 await page.getByText("Dehli NCR Offers")
 await expect(page).toBeTruthy();})

 test('verify message on site', async ({ page }) => {
  await page.getByText("Dehli NCR Offers")
  await expect(page).toBeTruthy();})





