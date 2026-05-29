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

# AC-008: Product Detail Page Content

## Open Application
Open {{app_url}} and navigate to a product category page by clicking a category link.
Click on the first product in the listing to open the product detail page.
Wait for the detail page to load fully.

## Verify Product Name
Confirm the product detail page displays a clear product name in a heading or prominent
text element.

## Verify Product Price
Confirm a price is displayed on the product detail page, formatted as a monetary value
(e.g., $299.99).

## Verify Add to Cart Button
Look for an "Add to Cart" button or similar call-to-action button. Verify the button
is visible and enabled. Fail if the product name, price, or add-to-cart button is missing.
