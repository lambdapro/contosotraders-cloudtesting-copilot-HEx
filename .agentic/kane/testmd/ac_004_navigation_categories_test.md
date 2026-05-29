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

## Open Application
Open {{app_url}} and wait for the full page to load. Confirm the header navigation is visible.

## Verify Category Navigation
Look at the top navigation bar for product category links or menu items. Verify that at
least one of the following categories is visible: Controllers, Laptops, Headphones, or
any other product category name. Click on one category link and verify the page navigates
to a products listing page for that category. Fail if no category links are visible.
