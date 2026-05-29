---
mode: testing
headless: true
max_steps: 20
timeout: 120
code_export: true
code_language: python
project: "01J2VAWPNBPA21T0BW44JW026X"
folder: "01KPD0NC5ZXZD9EXB23QCATTG2"
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-002: Home Page Hero Section

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load completely.

## Step 2 — Scroll to the top
Ensure the page is scrolled to the top so the hero/banner area is fully visible.

## Step 3 — Locate the hero section
Look for the main hero or featured section below the navigation header.
This may be a product carousel, a large promotional image, a banner with products,
or a grid of featured product cards.

## Step 4 — Verify content is present
Confirm that the hero section contains at least one product image, product name, or
promotional text. The section should not be empty or blank.

## Step 5 — Mark test result
If a hero section with product or promotional content is visible, this test PASSES.
If the hero area is blank or completely missing, this test FAILS.
