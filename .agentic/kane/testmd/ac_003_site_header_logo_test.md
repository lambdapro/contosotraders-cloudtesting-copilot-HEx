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

# AC-003: Site Header Logo and Brand Name

## Open Application
Open {{app_url}} and wait for the page to load completely.

## Verify Logo and Brand
Look at the top of the page in the header area. Verify that the Contoso Traders logo
or brand name is visible. The logo may be an image or text. Confirm it is displayed
prominently in the header navigation bar. Fail if no logo or brand name can be found.
