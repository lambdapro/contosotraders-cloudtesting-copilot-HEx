---
mode: testing
headless: true
max_steps: 25
timeout: 120
code_export: true
code_language: python
project: "01J2VAWPNBPA21T0BW44JW026X"
folder: "01KPD0NC5ZXZD9EXB23QCATTG2"
variables:
  app_url:
    value: "http://localhost:3000"
---

# AC-005: Search Functionality

## Step 1 — Open the application
Navigate to {{app_url}} and wait for the page to load.

## Step 2 — Locate the search box
Find the search input field in the header. It should have a placeholder text
"Search by product name or search by image" or similar.

## Step 3 — Type a search query
Click on the search input field and type the word "laptop".

## Step 4 — Submit the search
Press the Enter key or click the search button to submit the query.

## Step 5 — Wait for results
Wait for the page to load or refresh with search results.

## Step 6 — Verify results appeared
Check that the page now shows product cards or a results list related to "laptop".
At least one product result should be visible.

## Step 7 — Mark test result
If search results appeared after typing and submitting, this test PASSES.
If the search box is missing or no results appeared, this test FAILS.
