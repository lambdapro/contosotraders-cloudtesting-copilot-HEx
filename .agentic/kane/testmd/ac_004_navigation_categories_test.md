---
mode: testing
headless: true
max_steps: 20
timeout: 60
code_export: true
code_language: python
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-004: Navigation Product Categories

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load. Confirm the header is visible.

## Step 2 — Find the navigation menu
Look at the top navigation bar for category links or menu items.

## Step 3 — Verify product categories are listed
Check for navigation links to product categories. At least one of the following
category names should appear: Controllers, Laptops, Headphones, Monitors, or
any other product category name.

## Step 4 — Click a category
Click on one category link and wait for the page to navigate to that category's
product listing.

## Step 5 — Verify navigation succeeded
Confirm the URL changed and a products listing page loaded successfully.

## Step 6 — Mark test result
If at least one product category is visible in the navigation and clicking it
loads a products page, this test PASSES. If no categories are found, this FAILS.
