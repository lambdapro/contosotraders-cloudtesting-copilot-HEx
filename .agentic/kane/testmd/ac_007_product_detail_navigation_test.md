---
mode: testing
headless: true
max_steps: 25
timeout: 120
code_export: true
code_language: python
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-007: Product Detail Page Navigation

## Open Application
Open {{app_url}} and wait for the full page to load.

## Navigate to Products
Click on a product category in the navigation to open the products listing page.
Wait for product cards to appear.

## Click a Product
Click on the first product card to navigate to its detail page. Wait for the product
detail page to fully load.

## Verify Navigation Succeeded
Confirm that the URL has changed to a product detail page URL. Verify the page shows
the product detail view with more information than the listing card. Fail if clicking
the product card does not navigate to a detail page.
