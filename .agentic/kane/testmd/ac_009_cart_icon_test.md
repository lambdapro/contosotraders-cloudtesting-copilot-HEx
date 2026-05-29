---
mode: testing
headless: true
max_steps: 15
timeout: 60
code_export: true
code_language: python
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-009: Cart Icon in Header

## Open Application
Open {{app_url}} and wait for the full page to load.

## Verify Cart Icon
Look at the header navigation area for a shopping cart icon. The cart icon may be
a bag icon, cart icon, or similar shopping symbol. Verify the cart icon is visible
in the header. Click on the cart icon to confirm it is interactive and responds to
the click (e.g., opens a cart sidebar or navigates to a cart page). Fail if the cart
icon is not visible or not clickable.
