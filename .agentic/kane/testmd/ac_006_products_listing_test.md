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

## Open Application
Open {{app_url}} and wait for the full page to load.

## Navigate to Products
Click on a product category link in the navigation (such as Controllers, Laptops, or
Headphones). Wait for the products listing page to load.

## Verify Product Cards
Confirm that the page displays multiple product cards. Each product card should show
at least a product image, product name, and price. Verify at least 2 product cards
are visible on the page. Fail if the product listing is empty or cards are missing
key information.
