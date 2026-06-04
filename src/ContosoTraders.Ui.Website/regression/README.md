# Kane AI — exported regression suite

This folder holds the Playwright regression specs **exported by the Kane AI CLI**
(`--code-export --code-language javascript`) during Stage 3 of the Agentic STLC
pipeline. Files are named `<requirement>.test.js`.

- **Generated, not hand-written.** `ci/export_kane_regression.py` copies each Kane
  session's code-export here after Stage 1. Do not edit by hand.
- **Consumed by HyperExecute.** `hyperexecute.yaml` runs them via the matrix:
  `test_files: ["regression/*.test.js"]` →
  `cd src/ContosoTraders.Ui.Website && npx playwright test $test_files`.

This `.gitkeep`/README keeps the folder present in the repo even before the first
export.
