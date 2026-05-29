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

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Inspect the header
Look at the top-left area of the page inside the main navigation bar.

## Step 3 — Verify the Contoso Traders logo or brand name
Confirm that the Contoso Traders logo (an image) or the text "Contoso Traders"
is displayed in the header. The logo should be clearly visible and not broken.

## Step 4 — Mark test result
If the Contoso Traders logo or brand name is visible in the header, this test PASSES.
If no logo or brand name can be found in the header area, this test FAILS.
