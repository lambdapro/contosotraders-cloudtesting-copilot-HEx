---
mode: testing
headless: true
max_steps: 15
timeout: 60
code_export: true
code_language: python
project: "01J2VAWPNBPA21T0BW44JW026X"
folder: "01KPD0NC5ZXZD9EXB23QCATTG2"
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-009: Cart Icon in Header

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Locate the cart icon
Look in the header navigation area for a shopping cart icon, bag icon, or
cart symbol. It may show a count badge (e.g., "0" or empty).

## Step 3 — Verify cart icon is visible
Confirm the cart icon is present and visible in the header.

## Step 4 — Click the cart icon
Click on the cart icon and wait for a response. It should either:
- Open a cart sidebar or drawer
- Navigate to a cart page
- Show a mini-cart dropdown

## Step 5 — Verify cart opened
Confirm the click triggered a visible response (sidebar, page, or dropdown).

## Step 6 — Mark test result
If the cart icon is visible and clicking it triggers a cart view, this PASSES.
If the cart icon is missing or not clickable, this FAILS.
