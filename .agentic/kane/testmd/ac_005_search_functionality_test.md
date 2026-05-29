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

# AC-005: Search Functionality

## Open Application
Open {{app_url}} and wait for the full page to load.

## Perform Product Search
Find the search box in the header or navigation area. Click on it and type the word
"laptop" into the search box. Submit the search by pressing Enter or clicking the search
button. Wait for the search results page to load. Verify that the results page shows
at least one product matching the search query. Fail if the search box is not found or
if no results appear after searching.
