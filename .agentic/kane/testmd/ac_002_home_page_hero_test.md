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

# AC-002: Home Page Hero Section

## Open Application
Open {{app_url}} and wait for the full page to load.

## Verify Hero Section
Scroll to the top of the page. Look for a hero section, banner carousel, or featured
products area below the main navigation. Verify that at least one product image or
promotional banner is visible. Confirm the section contains either product cards or
a large promotional image. Fail if the hero area is blank or empty.
