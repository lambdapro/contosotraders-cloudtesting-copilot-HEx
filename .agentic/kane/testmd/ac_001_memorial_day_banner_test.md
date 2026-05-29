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

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to fully load.
Confirm the browser title is "Contoso Traders" and the header navigation is visible.

## Step 2 — Locate the promotional banner area
Scroll to the very top of the page.
Look for any announcement bar, promotional strip, or banner element positioned
above or inside the main navigation header.

## Step 3 — Verify Memorial Day Sale text
Check the banner or announcement area for text that includes the phrase "Memorial Day Sale".
The text may appear in any case (upper, lower, mixed).
If the banner is found and contains "Memorial Day Sale", this step passes.
If no such banner exists anywhere on the page after a full page load, this step fails.
Describe exactly what the header area shows instead.

## Step 4 — Mark test result
If the Memorial Day Sale banner was found and readable, mark this test as PASSED.
If the banner was absent, mark this test as FAILED with a note describing what
the top of the page currently shows.
