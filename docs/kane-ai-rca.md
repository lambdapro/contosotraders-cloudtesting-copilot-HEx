# Kane AI RCA — AI-Powered Root Cause Analysis for fixing

`ci/fetch_kane_rca.py` pulls AI-generated Root Cause Analysis from the LambdaTest
**AI-Powered RCA API** and writes a Claude/Copilot-consumable fix report. The goal:
turn a failed Kane (or HyperExecute) run into concrete `steps_to_fix` an agent can
act on — without anyone reading raw session logs.

## API embedded

```
GET https://api.lambdatest.com/insights/api/v3/public/rca      (US)
GET https://eu-api.lambdatest.com/insights/api/v3/public/rca   (EU, set LT_REGION=eu)
Authorization: Basic base64(LT_USERNAME:LT_ACCESS_KEY)
Params: test_ids | job_ids | task_ids | stage_ids  (comma-separated; ≥1 required), page, limit
Doc: https://www.testmuai.com/support/api-doc/analytics/root-cause-analysis/get-ai-powered-root-cause-analysis-for-test-failures/
```

Response `data[].rca_detail` carries the actionable fields:
`failure_summary`, `analysis[]`, **`steps_to_fix[{issue, module, suggested_fix}]`**,
`error_timeline[]`, `root_cause_category`, `stack_trace`.

## Usage

```bash
# Kane flow — reads requirements/analyzed_requirements.json, queries RCA per session
LT_USERNAME=... LT_ACCESS_KEY=... python ci/fetch_kane_rca.py
KANE_RCA_SCOPE=all python ci/fetch_kane_rca.py        # include passed sessions too

# Ad-hoc — embed the full API surface (works for HyperExecute too)
python ci/fetch_kane_rca.py --job-ids  <heJobId>
python ci/fetch_kane_rca.py --test-ids <id1>,<id2> --task-ids <taskId>
```

Outputs:
- `reports/kane_rca.json` — machine-readable (per requirement + raw `rca_detail`)
- `reports/kane_rca.md`  — **Fix Intelligence**: failure summary, analysis, a
  `steps_to_fix` table (module / issue / suggested fix), error timeline, stack
  trace, and the Kane steps. Feed this to Claude/Copilot to drive the fix.

In CI it runs in the **RCA - Failure Intelligence** job and is uploaded in the
`rca-intelligence` artifact.

## Data availability (important)

The RCA API only returns records once LambdaTest has **generated RCA for an
executed test failure** (real assertion/app/runtime errors). It returns
`{"data": [], "status": "success"}` when:

- a session passed (no failure to analyze), or
- a failure happened **before the test executed** (e.g. the `chromium.connect()`
  tunnel-binding failures documented in
  [agentic-stlc-hyperexecute-limitation.md](agentic-stlc-hyperexecute-limitation.md)
  produce no RCA), or
- RCA is still generating (it is asynchronous — retry after the run settles), or
- the identifier isn't an Automation `test_id` the API keys on. Kane exposes a
  TestManager test-case link and a session UUID; when a Kane run also emits an
  `automation.lambdatest.com/test?testID=…` link, that testID is used (preferred).

So `steps_to_fix` appears for genuine in-test failures; connect/tunnel-level
failures must be fixed from the stage logs (see the limitation doc).
