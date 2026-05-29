---
mode: testing
headless: true
max_steps: 20
timeout: 120
code_export: true
code_language: python
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-006: Products Listing Page

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Navigate to a category
Click on one of the product category links in the navigation (e.g., Controllers,
Laptops, or Headphones). Wait for the products listing page to load.

## Step 3 — Verify product cards are visible
Check that the products listing page shows multiple product cards arranged in a
grid or list. Each product card should display at least:
- A product image
- A product name
- A price

## Step 4 — Count product cards
Verify at least 2 product cards are visible on the page.

## Step 5 — Mark test result
If multiple product cards with images, names, and prices are visible, this PASSES.
If the listing is empty or product cards are missing key information, this FAILS.
