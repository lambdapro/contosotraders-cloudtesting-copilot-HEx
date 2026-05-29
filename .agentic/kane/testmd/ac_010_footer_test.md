---
mode: testing
headless: true
max_steps: 15
timeout: 60
code_export: true
code_language: python
project: "01J2VAWPNBPA21T0BW44JW026X"
folder: "01KPD0NC5ZXZD9EXB23QCATTG2"
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-010: Footer Visibility

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Scroll to the bottom
Scroll down to the very bottom of the page.

## Step 3 — Locate the footer
Look for a footer section at the bottom of the page. It may contain:
- Copyright text (e.g., "© 2024 Contoso Traders")
- Navigation links
- Social media links
- Contact information or address

## Step 4 — Verify footer has content
Confirm the footer is visible and contains at least one text element.
The footer should not be invisible or completely empty.

## Step 5 — Mark test result
If a footer with text content is visible at the bottom of the page, this PASSES.
If no footer is found or the footer area is blank, this FAILS.
