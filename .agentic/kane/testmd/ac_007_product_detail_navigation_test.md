---
mode: testing
headless: true
max_steps: 25
timeout: 120
code_export: true
code_language: python
project: "01J2VAWPNBPA21T0BW44JW026X"
folder: "01KPD0NC5ZXZD9EXB23QCATTG2"
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-007: Product Detail Page Navigation

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Go to product listing
Click a product category in the navigation to open the products listing page.
Wait for product cards to appear.

## Step 3 — Note the current URL
Record the current URL of the products listing page.

## Step 4 — Click a product card
Click on the first product card in the listing. Wait for the page to load.

## Step 5 — Verify navigation to detail page
Confirm that the URL has changed from the listing page URL to a new URL.
The new page should show more detailed information about that specific product.

## Step 6 — Mark test result
If clicking the product card navigated to a different page with product details,
this test PASSES. If the URL did not change or the click had no effect, this FAILS.
