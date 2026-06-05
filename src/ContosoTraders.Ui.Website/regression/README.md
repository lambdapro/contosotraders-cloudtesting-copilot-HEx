# Kane AI — exported regression suite

This folder holds the **Python `testmu` scripts exported by the Kane AI CLI**
(`--code-export`) during Stage 3 of the Agentic STLC pipeline. Files are named
`<requirement>.py`.

- **Generated, not hand-written.** `ci/export_kane_regression.py` copies each Kane
  session's `code-export/test.py` here after Stage 1. Do not edit by hand.
- **Run model.** Each script is a `testmu` test (`testmu.run`) that launches a local
  Playwright browser and calls Kane's AI vision API for assertions. It is executed
  with `python <file>` — **not** `npx playwright test` (kane-cli only emits Python;
  `--code-language javascript` is a no-op as of 0.4.0).
- **Consumed by HyperExecute.** `ci/build_he_matrix.py` writes these into the
  `hyperexecute.yaml` matrix (`files: [...]`) and HE runs `python $files` per task,
  with `pip3 install -r requirement.txt` + `playwright install` in `pre`.

This README keeps the folder present in the repo even before the first export.
