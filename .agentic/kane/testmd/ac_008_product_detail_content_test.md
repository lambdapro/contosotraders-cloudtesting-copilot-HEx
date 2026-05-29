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

# AC-008: Product Detail Page Content

## Step 1 — Open the application and navigate to a product
Navigate to {{app_url}}, click a product category, then click a product card
to open the product detail page. Wait for it to load fully.

## Step 2 — Verify product name
Find a heading (h1 or h2) that shows the product name.
Confirm the name is not empty and is clearly readable.

## Step 3 — Verify product price
Find the price display on the page. It should show a monetary value such as
$299.99 or similar formatted price.

## Step 4 — Verify Add to Cart button
Find an "Add to Cart" button or similar call-to-action button.
Confirm the button is visible and appears enabled (not grayed out).

## Step 5 — Mark test result
If product name, price, and an Add to Cart button are all present and visible,
this test PASSES. If any of these three elements is missing, this test FAILS.
