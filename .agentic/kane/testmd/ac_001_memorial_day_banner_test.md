---
mode: testing
headless: true
max_steps: 30
timeout: 120
code_export: true
code_language: python
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-001: Memorial Day Sale Banner

## Open Application
Open {{app_url}} and wait for the full page to load. Confirm the main navigation header is visible.

## Verify Memorial Day Banner
Look near the top of the page — above or inside the header navigation — for a promotional
banner, announcement bar, or highlighted strip. Verify the banner text contains the words
"Memorial Day Sale". If no such banner is visible after the page loads, fail this step
and describe exactly what the header area currently shows instead.
