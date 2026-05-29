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

# AC-010: Footer Visibility

## Open Application
Open {{app_url}} and wait for the full page to load.

## Verify Footer
Scroll to the bottom of the page. Verify that a footer section is visible containing
site information such as copyright text, links, or brand information. Confirm at least
one text element is present in the footer area. Fail if no footer is visible or the
footer area is completely empty.
