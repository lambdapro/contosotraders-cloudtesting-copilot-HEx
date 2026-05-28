---
mode: testing
headless: true
max_steps: 30
timeout: 120
code_export: true
code_language: typescript
variables:
  app_url:
    value: "http://localhost:3000"
---


# The application top banner should display a Memorial Day Sale banner

## Open Application
Open {{app_url}} and wait for the full page to load. Confirm the main navigation is visible.

## Verify Banner
Look near the top of the page — above or inside the header navigation — for a promotional banner, announcement bar, or highlighted strip. Verify the banner text contains the expected promotional message. If no banner is visible after the page loads, fail this step and describe what the header area currently shows.
